"""Storage latency control and bottleneck analysis for the backup from the array snapshot.

Latency control (like a storage latency control of other backup products): the array volume
latency is read from the array statistics (lssystemstats, through sw-snap.pl). Over
`latency_start_ms` no new backup starts on this node; over `latency_throttle_ms` the running backups
read with fewer threads until the latency is back under 70 % of that value. 0 turns either off.

Bottleneck: how busy each stage of the pipeline was during the copy, in % of the time:
  source   reading the snapshot from the array
  proxy    this node's CPU (hashing and compression in the PBS library)
  network  the link to the PBS (bytes on the bridge port toward the PBS / port speed); empty when
           the PBS is a VM on this node (no physical link in the way)
  target   the PBS (estimate: time waiting on the PBS library not explained by CPU or network)
"""
import json
import os
import re
import subprocess
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS = "/etc/ibm-storage-cbt/settings.json"
DEFAULTS = {"max_parallel": 2, "latency_start_ms": 20, "latency_throttle_ms": 30}


def settings():
    s = dict(DEFAULTS)
    try:
        for k, v in json.load(open(SETTINGS)).items():
            if k in DEFAULTS:
                s[k] = float(v) if "latency" in k else int(v)
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return s


def array_latency(store):
    """Volume latency of the array in ms (max of overall, read and write), or None."""
    try:
        o = subprocess.run(["perl", os.path.join(HERE, "sw-snap.pl"), "latency", store],
                           capture_output=True, text=True, timeout=30).stdout
        return max(float(x) for x in o.split())
    except (subprocess.SubprocessError, ValueError, OSError):
        return None


class LatencyGovernor:
    """Adjusts how many read threads may work at once, from the array latency."""

    def __init__(self, store, workers, limit_ms, period=15):
        self.store, self.max, self.limit, self.period = store, workers, limit_ms, period
        self.allowed = workers
        self.cond = threading.Condition()
        self.active = 0
        self.throttled_s = 0.0
        self.peak_ms = None
        self.samples = []
        self.stop = threading.Event()
        self._t = None

    def start(self):
        if self.limit and self.limit > 0:
            self._t = threading.Thread(target=self._loop, daemon=True)
            self._t.start()
        return self

    def _loop(self):
        while not self.stop.wait(self.period):
            lat = array_latency(self.store)
            if lat is None:
                continue
            self.samples.append(lat)
            self.peak_ms = lat if self.peak_ms is None else max(self.peak_ms, lat)
            with self.cond:
                if lat > self.limit:
                    self.allowed = max(1, self.allowed // 2)
                elif lat < self.limit * 0.7 and self.allowed < self.max:
                    self.allowed += 1
                self.cond.notify_all()

    def enter(self):
        t = None
        with self.cond:
            while self.active >= self.allowed:
                t = t or time.monotonic()
                self.cond.wait(1)
            self.active += 1
        if t:
            self.throttled_s += time.monotonic() - t

    def leave(self):
        with self.cond:
            self.active -= 1
            self.cond.notify()

    def close(self):
        self.stop.set()


def _bridge_port(ip):
    """Port of the bridge through which `ip` is reached, and its speed in Mb/s (None: local VM)."""
    try:
        r = subprocess.run(["ip", "route", "get", ip], capture_output=True, text=True, timeout=5).stdout
        dev = re.search(r"\bdev (\S+)", r).group(1)
        if not os.path.isdir(f"/sys/class/net/{dev}/brif"):
            return dev, _speed(dev)
        subprocess.run(["ping", "-c1", "-W1", ip], capture_output=True, timeout=5)
        mac = re.search(r"lladdr (\S+)", subprocess.run(["ip", "neigh", "show", ip], capture_output=True, text=True).stdout)
        if mac:
            fdb = subprocess.run(["bridge", "fdb", "show", "br", dev], capture_output=True, text=True).stdout
            m = re.search(rf"^{re.escape(mac.group(1))} dev (\S+)", fdb, re.M | re.I)
            if m:
                port = m.group(1)
                if re.match(r"^(tap|veth|fwpr|fwln)", port):
                    return port, None                   # the PBS is a VM/CT on this node
                return port, _speed(port)
        phys = [p for p in os.listdir(f"/sys/class/net/{dev}/brif") if not re.match(r"^(tap|veth|fwpr|fwln)", p)]
        return (phys[0], _speed(phys[0])) if phys else (dev, None)
    except Exception:
        return None, None


def _speed(dev):
    """Link speed in Mb/s (sum of the members for a bond), None when unknown."""
    try:
        v = int(open(f"/sys/class/net/{dev}/speed").read())
        if v > 0:
            return v
    except (OSError, ValueError):
        pass
    try:
        return sum(int(open(f"/sys/class/net/{s}/speed").read())
                   for s in open(f"/sys/class/net/{dev}/bonding/slaves").read().split()) or None
    except (OSError, ValueError):
        return None


def _tx(port):
    try:
        # a tap/veth port "transmits" toward the guest; a physical port toward the wire
        return int(open(f"/sys/class/net/{port}/statistics/tx_bytes").read())
    except (OSError, ValueError, TypeError):
        return None


class Meter:
    """Busy time of each stage of the copy."""

    def __init__(self, pbs_ip, workers):
        self.workers = workers
        self.read_s = self.send_s = self.limit_s = 0.0
        self.lock = threading.Lock()
        self.port, self.speed = _bridge_port(pbs_ip) if pbs_ip else (None, None)
        self.tx0 = _tx(self.port)
        self.cpu0 = sum(os.times()[:2])
        self.t0 = time.monotonic()

    def add(self, read=0.0, send=0.0, limit=0.0):
        with self.lock:
            self.read_s += read
            self.send_s += send
            self.limit_s += limit

    def report(self, governor=None):
        wall = max(time.monotonic() - self.t0, 0.001)
        cpus = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else (os.cpu_count() or 1)
        slots = wall * self.workers
        pct = lambda x: round(min(100.0, 100.0 * x), 1)
        source = pct(self.read_s / slots)
        proxy = pct((sum(os.times()[:2]) - self.cpu0) / (wall * min(self.workers, cpus)))
        network = None
        tx1 = _tx(self.port)
        if self.speed and self.tx0 is not None and tx1 is not None:
            network = pct((tx1 - self.tx0) / wall / (self.speed * 125000))
        send = self.send_s / slots
        target = pct(max(0.0, send - max(proxy, network or 0) / 100.0))
        out = {"source": source, "proxy": proxy, "network": network, "target": target,
               "link": {"port": self.port, "speed_Mbps": self.speed, "local_pbs": bool(self.port) and self.speed is None}}
        throttled = {"bandwidth_limit": pct(self.limit_s / slots)}
        if governor is not None:
            throttled["latency_control"] = pct(governor.throttled_s / slots)
            out["array_latency_peak_ms"] = governor.peak_ms
        out["throttled"] = throttled
        stages = {k: v for k, v in (("source", source), ("proxy", proxy), ("network", network), ("target", target)) if v is not None}
        limits = {k: v for k, v in throttled.items() if v >= 25}
        if limits:
            out["bottleneck"] = max(limits, key=limits.get)
        else:
            out["bottleneck"] = max(stages, key=stages.get) if stages else None
        return out
