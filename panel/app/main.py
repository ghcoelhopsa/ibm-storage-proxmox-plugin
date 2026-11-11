"""IBM Storage Plugin Proxmox - web panel (API + static UI).

Login with Proxmox credentials. Proxmox data and actions go through the
Proxmox API with the user's own ticket; array data comes over SSH with the
credential the panel keeps for each storage.
"""
import base64
import hashlib
import datetime
import json
import os
import re
import secrets
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import backup
import dr
import signal
import store
import sv8
import tlsconf
from sv import Array, ArrayError, capabilities, enable_mux, scan_host_keys
from nodes import LVM_FILTER_SCRIPT, MULTIPATH_CONF, NVME_IOPOLICY_RULE, NodeError, NodeSSH, sh
from pve import PVE, PVEError

VERSION = "0.3.2"
HERE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(HERE, "..", "static")
ASSETS = os.path.join(HERE, "..", "assets")

CFG = {}


class PVEProxy:
    """Proxmox client that can be swapped at runtime (setup screen)."""
    _real = None

    def __getattr__(self, name):
        if self._real is None:
            raise PVEError(503, "the panel has not been set up yet")
        return getattr(self._real, name)


pve = PVEProxy()


def configure(cfg):
    CFG.clear()
    CFG.update(cfg or {})
    PVEProxy._real = PVE(cfg["pve_host"], int(cfg.get("pve_port", 8006)), cfg["pve_fingerprint"]) if cfg else None


configure(store.config())
nodessh = NodeSSH(store.NODE_KEY, store.NODE_KNOWN_HOSTS)
enable_mux(os.path.join(store.DATA_DIR, "cm"))

STAGE = "beta"
PRODUCT = "IBM Storage Plugin Proxmox"
app = FastAPI(title=PRODUCT, version=VERSION, docs_url=None, redoc_url=None)

SESSIONS = {}          # sid -> {"pve": {...}, "seen": ts, "privs": set}
WIZARDS = {}           # wid -> state of an "add storage" wizard
CACHE = {}             # key -> (ts, value)
SESSION_IDLE = 2 * 3600
SESSION_MAX = 12 * 3600
LOGIN_FAILS = {}          # ip -> [timestamps]
NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,62}$")
STOREID_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{1,30}$")
PREFIX_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,15}$")
ADDR_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,253}$")
USER_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.@-]{0,63}$")
SNAP_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,39}$")
WWPN_RE = re.compile(r"^[0-9A-Fa-f]{16}$")

STATS_KEEP = ("cpu_pc", "fc_mb", "fc_io", "iscsi_mb", "vdisk_mb", "vdisk_io", "vdisk_ms",
              "vdisk_r_mb", "vdisk_w_mb", "vdisk_r_io", "vdisk_w_io", "vdisk_r_ms", "vdisk_w_ms",
              "mdisk_mb", "mdisk_io", "mdisk_ms", "write_cache_pc", "total_cache_pc", "power_w", "temp_c")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def err(status, msg):
    raise HTTPException(status_code=status, detail=msg)


CACHE_LOCK = threading.Lock()
KEY_LOCKS = {}
REFRESHING = set()
CACHE_GEN = [0]


def cached(key, ttl, fn):
    """Cache with background refresh: recently expired data is returned right away while
    a thread fetches the new value; it only waits when there is nothing (or it is too old)."""
    now = time.time()
    hit = CACHE.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    if hit and now - hit[0] < min(max(ttl * 10, 120), 600):
        with CACHE_LOCK:
            start = key not in REFRESHING
            if start:
                REFRESHING.add(key)
        if start:
            gen = CACHE_GEN[0]

            def bg():
                try:
                    val = fn()
                    if gen == CACHE_GEN[0]:          # nothing was changed/invalidated while fetching
                        CACHE[key] = (time.time(), val)
                except Exception:
                    pass
                finally:
                    REFRESHING.discard(key)
            threading.Thread(target=bg, daemon=True).start()
        return hit[1]
    with CACHE_LOCK:
        lock = KEY_LOCKS.setdefault(key, threading.Lock())
    with lock:                                        # concurrent requests wait for a single fetch
        hit = CACHE.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
        val = fn()
        CACHE[key] = (time.time(), val)
        return val


def drop_cache(prefix=""):
    CACHE_GEN[0] += 1
    for k in list(CACHE):
        if k.startswith(prefix):
            CACHE.pop(k, None)


def purge_sessions():
    now = time.time()
    for k, v in list(SESSIONS.items()):
        if now - v["seen"] > SESSION_IDLE or now - v["created"] > SESSION_MAX:
            SESSIONS.pop(k, None)
    for k, w in list(WIZARDS.items()):          # abandoned wizards: delete the generated key
        if now - w["created"] > 3600:
            WIZARDS.pop(k, None)
            for f in (w.get("key"), (w.get("key") or "") + ".pub", w.get("kh")):
                if f and os.path.exists(f):
                    os.unlink(f)


def session(request: Request):
    sid = request.cookies.get("swsid")
    s = SESSIONS.get(sid or "")
    now = time.time()
    if not s or now - s["seen"] > SESSION_IDLE or now - s["created"] > SESSION_MAX:
        SESSIONS.pop(sid or "", None)
        err(401, "sign in")
    if request.method != "GET" and request.headers.get("x-requested-with") != "swpanel":
        err(403, "request refused")
    if now - s["privs_at"] > 300:               # a permission removed in Proxmox takes effect here within 5 min
        try:
            s["privs"] = pve.privileges(s["pve"], "/")
            s["privs_at"] = now
        except PVEError:
            SESSIONS.pop(sid, None)
            err(401, "session expired")
        if "Sys.Audit" not in s["privs"]:
            SESSIONS.pop(sid, None)
            err(401, "access revoked")
    s["seen"] = now
    return s


def admin(s=Depends(session)):
    if not {"Sys.Modify"} <= s["privs"]:
        err(403, "this action requires the Sys.Modify privilege on / in Proxmox")
    return s


def pcall(s, method, endpoint, **p):
    try:
        return pve.call(s["pve"], method, endpoint, **p)
    except PVEError as e:
        err(e.status if e.status in (400, 401, 403, 404, 500) else 502, f"Proxmox: {e}")


PLUGIN_TYPES = ("storwize", "flashsystem")


def plugin_type(product_name):
    """Proxmox storage type according to the array family: IBM FlashSystem or IBM Storwize (and SVC)."""
    return "flashsystem" if "flashsystem" in (product_name or "").lower() else "storwize"


def storwize_storages(s):
    st = [x for x in (pcall(s, "GET", "/storage") or []) if x.get("type") in PLUGIN_TYPES]
    update_meta(st)
    return st


def storage_cfg(s, storeid):
    for x in storwize_storages(s):
        if x["storage"] == storeid:
            return x
    err(404, f"storage '{storeid}' not found")


def array_for(scfg):
    sid = scfg["storage"]
    if not store.has_array_key(sid):
        raise ArrayError("the panel has no credential for this storage yet")
    return Array(scfg["sw-address"], scfg["sw-user"], store.array_key(sid), store.ARRAY_KNOWN_HOSTS,
                 int(scfg.get("sw-port", 22)))


def to_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def b64(s):
    return base64.b64encode(s.encode()).decode()


def opt_any(fn, default):
    try:
        return fn()
    except Exception:
        return default


def array_snapshot(scfg, ttl=30, force=False):
    """Everything the UI shows about one array, cached."""
    sid = scfg["storage"]

    def load():
        a = array_for(scfg)
        sysd = a.show("lssystem")
        cl = sysd.get("code_level", "")
        caps = capabilities(cl)
        def opt(fn, default):
            try:
                return fn()
            except ArrayError:
                return default

        def clock_now():
            raw = a._ssh("svqueryclock").strip()            # e.g. Tue Nov 10 20:47:46 -03 2026
            fixed = re.sub(r" ([+-]\d{2})( \d{4})$", r" \g<1>00\2", raw)
            t_arr = datetime.datetime.strptime(fixed, "%a %b %d %H:%M:%S %z %Y").timestamp()
            return {"array": raw, "offset_s": round(t_arr - time.time(), 1)}

        jobs = {
            "pools": lambda: a.ls("lsmdiskgrp", [("bytes", None)]),
            "vdisks": lambda: a.ls("lsvdisk", [("bytes", None)]),
            "fcmaps": lambda: a.ls("lsfcmap"),
            "hosts": lambda: a.ls("lshost"),
            "hostmaps": lambda: a.ls("lshostvdiskmap"),
            "hclusters": lambda: a.ls("lshostcluster") if caps["host_cluster"] else [],
            "stats": lambda: {r["stat_name"]: r.get("stat_current") for r in a.ls("lssystemstats")},
            "clock": lambda: opt_any(clock_now, {}),
            "throttles": lambda: opt(lambda: a.ls("lsthrottle"), None),          # 7.7+: volume, pool and host QoS
            "rcrel": lambda: opt(lambda: a.ls("lsrcrelationship"), []),
            "ev": lambda: opt(lambda: a.ls("lseventlog", [("alert", "yes"), ("fixed", "no"), ("order", "severity")]), []),
            "enclosures": lambda: opt(lambda: a.ls("lsenclosure"), []),
            "nodes": lambda: opt(lambda: a.ls("lsnodecanister"), None) or a.ls("lsnode"),
        }
        with ThreadPoolExecutor(max_workers=6) as ex:        # independent queries, in parallel
            futs = {k: ex.submit(f) for k, f in jobs.items()}
            R = {k: f.result() for k, f in futs.items()}
        pools, vdisks, fcmaps, hosts, hostmaps, hclusters = (R[k] for k in ("pools", "vdisks", "fcmaps", "hosts", "hostmaps", "hclusters"))
        stats, throttles, rcrel, ev, enclosures, nodes = (R[k] for k in ("stats", "throttles", "rcrel", "ev", "enclosures", "nodes"))
        clock = {"ntp": sysd.get("cluster_ntp_IP_address") or "", "array": None, "offset_s": None}
        clock.update(R["clock"])
        # only real alerts (status 'alert'); 'message' entries are informational
        events = [e for e in ev if e.get("status") == "alert"]
        try:
            enclosures = a.ls("lsenclosure")
        except ArrayError:
            enclosures = []
        try:
            nodes = a.ls("lsnodecanister")
        except ArrayError:
            nodes = a.ls("lsnode")
        return {
            "storage": sid, "ok": True, "fetched": int(time.time()),
            "system": {k: sysd.get(k) for k in (
                "name", "id", "product_name", "code_level", "location", "console_IP", "total_mdisk_capacity",
                "total_used_capacity", "total_free_space", "total_vdisk_capacity", "total_overallocation",
                "compression_active", "statistics_status", "statistics_frequency", "time_zone", "cluster_ntp_IP_address")},
            "capabilities": caps, "pools": pools, "vdisks": vdisks, "fcmaps": fcmaps,
            "hosts": hosts, "hostmaps": hostmaps, "hostclusters": hclusters, "stats": stats,
            "rcrelationships": rcrel, "events": events, "enclosures": enclosures, "nodes": nodes,
            "throttles": throttles or [], "throttle_supported": throttles is not None, "clock": clock,
        }

    try:
        if force:                                    # used by the background warm-up
            gen = CACHE_GEN[0]
            val = load()
            if gen == CACHE_GEN[0]:
                CACHE[f"array:{sid}"] = (time.time(), val)
            return val
        return cached(f"array:{sid}", ttl, load)
    except (ArrayError, subprocess.TimeoutExpired) as e:
        return {"storage": sid, "ok": False, "error": str(e)}


# ---------------------------------------------------------------------------
# auth
# ---------------------------------------------------------------------------

@app.get("/api/info")
def info():
    return {"name": PRODUCT, "version": VERSION, "stage": STAGE, "configured": bool(CFG)}


# ---------------------------------------------------------------------------
# Initial setup: Proxmox connection
#   - new panel: requires the installation code (shown on the container console)
#   - panel already set up: requires an administrator session
# ---------------------------------------------------------------------------

def setup_auth(request: Request, body):
    if request.headers.get("x-requested-with") != "swpanel":
        err(403, "request refused")
    if CFG:
        return admin(session(request))
    ip, now = request.client.host if request.client else "?", time.time()
    fails = [t for t in LOGIN_FAILS.get(ip, []) if now - t < 300]
    LOGIN_FAILS[ip] = fails
    if len(fails) >= 8:
        err(429, "too many attempts - wait 5 minutes")
    code = store.setup_code()
    given = str(body.get("code", "")).strip().upper().replace(" ", "")
    if not code or not secrets.compare_digest(given, code.replace("-", "")) and not secrets.compare_digest(given, code):
        fails.append(now)
        err(403, "invalid installation code")
    return None


def setup_target(body):
    host = str(body.get("host", "")).strip()
    try:
        port = int(body.get("port") or 8006)
    except (TypeError, ValueError):
        err(400, "invalid port")
    if not ADDR_RE.match(host) or not 1 <= port <= 65535:
        err(400, "invalid address or port")
    return host, port


def probe_pve(host, port):
    from pve import fetch_fingerprint
    try:
        fp = fetch_fingerprint(host, port)
        realms = PVE(host, port, fp).realms()
    except Exception as e:
        err(502, f"could not reach the Proxmox API at {host}:{port} ({e.__class__.__name__})")
    if not isinstance(realms, list) or not any(r.get("realm") == "pam" for r in realms):
        err(502, f"{host}:{port} answered, but it does not look like a Proxmox VE")
    return fp


@app.post("/api/setup/probe")
async def setup_probe(request: Request):
    b = await request.json()
    setup_auth(request, b)
    host, port = setup_target(b)
    return {"host": host, "port": port, "fingerprint": probe_pve(host, port)}


@app.post("/api/setup/save")
async def setup_save(request: Request):
    b = await request.json()
    setup_auth(request, b)
    host, port = setup_target(b)
    fp = probe_pve(host, port)
    if fp.replace(":", "").lower() != str(b.get("fingerprint", "")).replace(":", "").lower():
        err(409, "the certificate changed since it was checked - fetch it again")
    cfg = {"pve_host": host, "pve_port": port, "pve_fingerprint": fp}
    store.save_config(cfg)
    configure(cfg)
    SESSIONS.clear()                 # tickets from the previous Proxmox are no longer valid
    drop_cache()
    return {"ok": True}


@app.get("/api/realms")
def realms():
    try:
        return [{"realm": r["realm"], "comment": r.get("comment", "")} for r in pve.realms()]
    except Exception as e:
        err(502, f"Proxmox unreachable: {e}")


@app.post("/api/login")
async def login(request: Request, response: Response):
    if request.headers.get("x-requested-with") != "swpanel":
        err(403, "request refused")
    purge_sessions()
    ip, now = request.client.host if request.client else "?", time.time()
    fails = [t for t in LOGIN_FAILS.get(ip, []) if now - t < 300]
    LOGIN_FAILS[ip] = fails
    if len(fails) >= 8:
        err(429, "too many attempts - wait 5 minutes")
    body = await request.json()
    user, realm = str(body.get("username", ""))[:128], str(body.get("realm", "pam"))[:64]
    if not re.match(r"^[A-Za-z0-9_.@-]+$", user) or not re.match(r"^[A-Za-z0-9_.-]+$", realm):
        err(400, "invalid user or realm")
    try:
        p = pve.login(user, str(body.get("password", "")), realm)
    except PVEError as e:
        fails.append(now)
        err(401, str(e))
    except Exception as e:
        err(502, f"Proxmox unreachable: {e}")
    LOGIN_FAILS.pop(ip, None)
    s = {"pve": p, "seen": now, "created": now, "privs_at": now}
    s["privs"] = pve.privileges(p, "/")
    if "Sys.Audit" not in s["privs"]:
        err(403, "the user needs the Sys.Audit privilege on / (for example, the PVEAuditor role)")
    sid = secrets.token_urlsafe(32)
    SESSIONS[sid] = s
    response.set_cookie("swsid", sid, httponly=True, secure=True, samesite="strict", max_age=SESSION_MAX, path="/")
    return {"user": p["user"], "admin": "Sys.Modify" in s["privs"]}


@app.post("/api/logout")
def logout(request: Request, response: Response):
    SESSIONS.pop(request.cookies.get("swsid", ""), None)
    response.delete_cookie("swsid")
    return {}


def dr_enabled():
    """The DR page only shows up when an array has policy-based replication or a peer is configured."""
    if dr.peer() or dr.has_token():
        return True
    return any(isinstance(v[1], dict) and (v[1].get("capabilities") or {}).get("pbr")
               for k, v in list(CACHE.items()) if k.startswith("array:"))


@app.get("/api/me")
def me(s=Depends(session)):
    return {"user": s["pve"]["user"], "admin": "Sys.Modify" in s["privs"], "version": VERSION, "stage": STAGE,
            "pve_host": CFG.get("pve_host"), "dr": dr_enabled()}


# ---------------------------------------------------------------------------
# overview / storages
# ---------------------------------------------------------------------------

def storage_status(s, scfg):
    nodes = pcall(s, "GET", "/nodes") or []
    st = []
    for n in nodes:
        if n.get("status") != "online":
            continue
        try:
            x = pve.get(s["pve"], f"/nodes/{n['node']}/storage/{scfg['storage']}/status")
            st.append({"node": n["node"], "active": x.get("active"), "total": x.get("total"),
                       "used": x.get("used"), "avail": x.get("avail")})
        except PVEError as e:
            st.append({"node": n["node"], "active": 0, "error": str(e)})
    return st


@app.get("/api/storages")
def storages(s=Depends(session)):
    remember_nodes(s)
    out = []
    for c in storwize_storages(s):
        snap = array_snapshot(c)
        item = {"config": {k: v for k, v in c.items() if k != "digest"},
                "panel_credential": store.has_array_key(c["storage"]),
                "status": storage_status(s, c), "array_ok": snap.get("ok"), "error": snap.get("error")}
        if snap.get("ok"):
            pool = next((p for p in snap["pools"] if p["name"] == c.get("sw-pool")), {})
            pfx = c.get("sw-prefix", "pve") + "-vm-"
            mv = [v for v in snap["vdisks"] if v["name"].startswith(pfx) and "_s_" not in v["name"]]
            item["health"] = {
                "canisters": [sum(1 for n in snap["nodes"] if n.get("status") == "online"), len(snap["nodes"])],
                "hosts": [sum(1 for h in snap["hosts"] if h.get("status") == "online"), len(snap["hosts"])],
                "volumes": [sum(1 for v in mv if v.get("status") == "online"), len(mv)],
                "all_volumes": [sum(1 for v in snap["vdisks"] if v.get("status") == "online"), len(snap["vdisks"])],
                "fcmaps": [sum(1 for f in snap["fcmaps"] if f.get("status") in ("copying", "idle_or_copied")), len(snap["fcmaps"])],
                "enclosures": [sum(1 for e in snap["enclosures"] if e.get("status") == "online"), len(snap["enclosures"])],
                "alerts": len(snap["events"]),
            }
            item["fetched"] = snap.get("fetched")
            item.update(system=snap["system"], capabilities=snap["capabilities"], pool=pool,
                        volumes=sum(1 for v in snap["vdisks"] if v["name"].startswith(pfx) and "_s_" not in v["name"]),
                        snapshots=sum(1 for v in snap["vdisks"] if v["name"].startswith(pfx) and "_s_" in v["name"]),
                        alerts=len(snap["events"]), stats=snap["stats"])
        out.append(item)
    return out


@app.get("/api/storages/{storeid}")
def storage_detail(storeid: str, s=Depends(session)):
    c = storage_cfg(s, storeid)
    snap = array_snapshot(c)
    if snap.get("ok"):
        snap["pool_limit"] = vdisk_throttle(snap, c.get("sw-pool"), "mdiskgrp")
    return {"config": c, "panel_credential": store.has_array_key(storeid),
            "status": storage_status(s, c), "array": snap}


@app.delete("/api/storages/{storeid}")
def storage_remove(storeid: str, s=Depends(admin)):
    storage_cfg(s, storeid)
    inv = inventory(s)
    used = [d for d in inv["disks"] if d["storage"] == storeid]
    if used:
        err(409, f"the storage still has {len(used)} disk(s) in VMs/CTs - move or delete them first")
    pcall(s, "DELETE", f"/storage/{storeid}")
    store.remove_array_key(storeid)
    drop_cache()
    return {"removed": storeid}


@app.post("/api/refresh")
def refresh(s=Depends(session)):
    drop_cache("array:")
    drop_cache(f"inv:{s['pve']['user']}")
    return {}


VOL_STATS = ("r_iops", "w_iops", "r_mbs", "w_mbs", "r_ms", "w_ms", "util")


def latest_stats(key, max_age=300):
    rows = store.load_stats(key, list(VOL_STATS), int(time.time()) - max_age)
    out = {k: v[-1][1] for k, v in rows.items() if v}
    return out or None


@app.get("/api/vms/{vmid}/disks/{disk}/perf")
def disk_perf(vmid: int, disk: str, hours: int = 6, s=Depends(session)):
    inv = inventory_api(s)
    d = next((x for x in inv["disks"] if x["vmid"] == vmid and x["key"] == disk), None)
    if not d:
        err(404, f"disk {disk} of VM {vmid} is not on a Storwize storage")
    hours = max(1, min(hours, 168))
    since = int(time.time()) - hours * 3600
    c = inventory(s)["storages"][d["storage"]]
    return {"disk": d, "hours": hours,
            "host": store.load_stats(f"lun:{(d.get('uid') or '').upper()}", list(VOL_STATS), since),
            "array": store.load_stats(f"vdisk:{c['sw-address']}:{d['vdisk']}", list(VOL_STATS), since)}


@app.get("/api/perf/{storeid}")
def perf(storeid: str, hours: int = 6, s=Depends(session)):
    storage_cfg(s, storeid)
    hours = max(1, min(hours, 168))
    return store.load_stats(storeid, list(STATS_KEEP), int(time.time()) - hours * 3600)


# ---------------------------------------------------------------------------
# inventory / reconciliation
# ---------------------------------------------------------------------------

DISK_KEY_RE = re.compile(r"^(scsi|sata|ide|virtio|efidisk|tpmstate|unused|rootfs|mp)\d*$")


def inventory(s):
    def load():
        sts = {c["storage"]: c for c in storwize_storages(s)}
        guests = pcall(s, "GET", "/cluster/resources", type="vm") or []
        disks, vms = [], []
        for g in guests:
            if g.get("template"):
                continue
            kind = "qemu" if g["type"] == "qemu" else "lxc"
            try:
                conf = pve.get(s["pve"], f"/nodes/{g['node']}/{kind}/{g['vmid']}/config")
                snaps = pve.get(s["pve"], f"/nodes/{g['node']}/{kind}/{g['vmid']}/snapshot") or []
            except PVEError:
                continue
            mine = []
            for k, v in conf.items():
                if not DISK_KEY_RE.match(k) or not isinstance(v, str):
                    continue
                volid = v.split(",")[0]
                st, _, volname = volid.partition(":")
                if st not in sts:
                    continue
                size = next((p.split("=", 1)[1] for p in v.split(",")[1:] if p.startswith("size=")), "")
                d = {"vmid": g["vmid"], "name": g.get("name"), "node": g["node"], "type": kind,
                     "status": g.get("status"), "key": k, "volid": volid, "storage": st,
                     "volname": volname, "vdisk": f"{sts[st].get('sw-prefix', 'pve')}-{volname}", "size": size,
                     "qemu_limits": {o.split("=", 1)[0]: o.split("=", 1)[1] for o in v.split(",")[1:]
                                     if o.split("=", 1)[0] in QEMU_LIMIT_KEYS and "=" in o}}
                disks.append(d)
                mine.append(d)
            if mine:
                vms.append({"vmid": g["vmid"], "name": g.get("name"), "node": g["node"], "type": kind,
                            "status": g.get("status"), "agent": conf.get("agent", ""),
                            "snapshots": [x for x in snaps if x.get("name") != "current"],
                            "disks": [d["key"] for d in mine], "uptime": g.get("uptime"),
                            "maxmem": g.get("maxmem"), "maxcpu": g.get("maxcpu")})
        return {"disks": disks, "vms": vms, "storages": sts}

    return cached(f"inv:{s['pve']['user']}", 20, load)


@app.get("/api/inventory")
def inventory_api(s=Depends(session)):
    inv = inventory(s)
    arrays = {sid: array_snapshot(c) for sid, c in inv["storages"].items()}
    for d in inv["disks"]:
        a = arrays.get(d["storage"], {})
        v = next((x for x in a.get("vdisks", []) if x["name"] == d["vdisk"]), None)
        if v:
            d.update(uid=v.get("vdisk_UID"), capacity=to_int(v.get("capacity")), vstatus=v.get("status"),
                     pool=v.get("mdisk_grp_name"), fc_count=to_int(v.get("fc_map_count")))
        d["flashcopies"] = [m for m in a.get("fcmaps", []) if m.get("source_vdisk_name") == d["vdisk"]]
        d["array_limit"] = vdisk_throttle(a, d["vdisk"])
        d["perf_now"] = latest_stats(f"lun:{(d.get('uid') or '').upper()}") if d.get("uid") else None
        d["throttle_supported"] = a.get("throttle_supported", False)
    return {"vms": inv["vms"], "disks": inv["disks"]}


@app.get("/api/reconcile")
def reconcile(s=Depends(session)):
    inv = inventory(s)
    issues = []

    def add(sev, storage, kind, obj, msg):
        issues.append({"severity": sev, "storage": storage, "kind": kind, "object": obj, "message": msg})

    for sid, c in inv["storages"].items():
        a = array_snapshot(c)
        if not a.get("ok"):
            add("error", sid, "access", sid, a.get("error", "array unreachable"))
            continue
        pfx = c.get("sw-prefix", "pve") + "-"
        vd = {v["name"]: v for v in a["vdisks"]}
        used = {d["vdisk"] for d in inv["disks"] if d["storage"] == sid}
        hosts_cfg = set(filter(None, (c.get("sw-hosts") or "").split(",")))
        hc = c.get("sw-hostcluster")
        for name, v in vd.items():
            if not name.startswith(pfx + "vm-"):
                continue
            if "_s_" in name:
                continue
            if name not in used:
                add("warning", sid, "orphan volume", name,
                    "Proxmox volume with no owning VM/CT (left over from a deleted VM or detached disk?)")
            if v.get("status") != "online":
                add("error", sid, "volume", name, f"status {v.get('status')}")
            maps = [m for m in a["hostmaps"] if m.get("vdisk_name") == name]
            if hc:
                if not any(m.get("host_cluster_name") == hc for m in maps):
                    add("error", sid, "mapping", name, f"not mapped to host cluster {hc}")
            else:
                missing = hosts_cfg - {m.get("name") for m in maps}
                if missing:
                    add("error", sid, "mapping", name, f"not mapped to: {', '.join(sorted(missing))}")
        for d in (x for x in inv["disks"] if x["storage"] == sid):
            if d["vdisk"] not in vd:
                add("error", sid, "missing volume", d["volid"],
                    f"VM {d['vmid']} ({d['key']}) points to a volume that does not exist on the array")
        # snapshots: Proxmox <-> FlashCopy
        for vm in inv["vms"]:
            vmdisks = [d for d in inv["disks"] if d["vmid"] == vm["vmid"] and d["storage"] == sid
                       and not d["key"].startswith("unused")]
            for snapname in (x["name"] for x in vm["snapshots"]):
                for d in vmdisks:
                    if f"{d['vdisk']}_s_{snapname}" not in vd:
                        add("warning", sid, "snapshot", f"{vm['vmid']}/{snapname}",
                            f"Proxmox snapshot with no FlashCopy on disk {d['key']}")
        pve_snaps = {(d["vdisk"], x["name"]) for vm in inv["vms"] for x in vm["snapshots"]
                     for d in inv["disks"] if d["vmid"] == vm["vmid"]}
        for m in a["fcmaps"]:
            t = m.get("target_vdisk_name", "")
            if t.startswith(pfx) and "_s_" in t:
                src, snap = t.split("_s_", 1)
                if (src, snap) not in pve_snaps:
                    add("warning", sid, "orphan FlashCopy", t, "FlashCopy with no matching snapshot in Proxmox")
            if m.get("status") not in ("copying", "idle_or_copied"):
                add("warning", sid, "FlashCopy", m.get("name"), f"map in state '{m.get('status')}'")
        ck = a.get("clock") or {}
        if not ck.get("ntp"):
            add("warning", sid, "clock", a["system"].get("name"), "array has no NTP server - logs and statistics may be off")
        if ck.get("offset_s") is not None and abs(ck["offset_s"]) > 60:
            add("warning", sid, "clock", a["system"].get("name"), f"array clock {ck['offset_s']:+.0f} s relative to the panel")
        for p in a["pools"]:
            if p["name"] == c.get("sw-pool"):
                cap, free = to_int(p.get("capacity")), to_int(p.get("free_capacity"))
                if cap and (cap - free) / cap > 0.8:
                    add("warning", sid, "capacity", p["name"], f"pool {100 * (cap - free) // cap}% used")
        groups = {}
        for e in a["events"]:
            key = (e.get("error_code") or "", e.get("event_id") or "", e.get("description", ""))
            groups.setdefault(key, []).append(e.get("object_name") or e.get("object_id") or "")
        for (code, event_id, desc), objs in groups.items():
            sev = "error" if code else "warning"     # error_code = needs a fix procedure
            add(sev, sid, "array alert", code or event_id,
                f"{desc} ({len(objs)}x: {', '.join(sorted(set(objs))[:5])}) - mark as fixed in the array GUI once handled")
    return issues


# ---------------------------------------------------------------------------
# VM actions (through the Proxmox API, with the user's permissions)
# ---------------------------------------------------------------------------

def guest_path(s, vmid):
    for g in pcall(s, "GET", "/cluster/resources", type="vm") or []:
        if g["vmid"] == vmid:
            return g["node"], ("qemu" if g["type"] == "qemu" else "lxc")
    err(404, f"VM {vmid} not found")


def run_task(s, node, upid):
    status, log = pve.wait_task(s["pve"], node, upid)
    drop_cache()
    if status != "OK":
        err(500, (log[-1] if log else status) or "failed")
    return {"status": status, "log": log[-15:]}


@app.post("/api/vms/{vmid}/snapshot")
async def vm_snapshot(vmid: int, request: Request, s=Depends(session)):
    b = await request.json()
    name = b.get("name", "")
    if not SNAP_RE.match(name):
        err(400, "invalid snapshot name (starts with a letter, letters/digits/_/-, up to 40)")
    node, kind = guest_path(s, vmid)
    p = {"snapname": name}
    if b.get("description"):
        p["description"] = str(b["description"])[:500]
    upid = pcall(s, "POST", f"/nodes/{node}/{kind}/{vmid}/snapshot", **p)
    return run_task(s, node, upid)


@app.delete("/api/vms/{vmid}/snapshot/{name}")
def vm_snapshot_delete(vmid: int, name: str, s=Depends(session)):
    if not SNAP_RE.match(name):
        err(400, "invalid snapshot name")
    node, kind = guest_path(s, vmid)
    upid = pcall(s, "DELETE", f"/nodes/{node}/{kind}/{vmid}/snapshot/{name}")
    return run_task(s, node, upid)


@app.post("/api/vms/{vmid}/snapshot/{name}/rollback")
async def vm_rollback(vmid: int, name: str, request: Request, s=Depends(session)):
    if not SNAP_RE.match(name):
        err(400, "invalid snapshot name")
    try:
        b = await request.json()
    except Exception:
        b = {}
    node, kind = guest_path(s, vmid)
    # Proxmox stops the VM to roll back and only starts it again when asked
    p = {"start": 1} if b.get("start") else {}
    upid = await run_in_threadpool(pcall, s, "POST", f"/nodes/{node}/{kind}/{vmid}/snapshot/{name}/rollback", **p)
    res = await run_in_threadpool(run_task, s, node, upid)
    g = await run_in_threadpool(guest_status, s, vmid)
    res["vm_status"] = g.get("status") if g else None
    return res


@app.post("/api/vms/{vmid}/resize")
async def vm_resize(vmid: int, request: Request, s=Depends(session)):
    b = await request.json()
    disk, size = b.get("disk", ""), str(b.get("size", ""))
    if not DISK_KEY_RE.match(disk) or not re.match(r"^\+?\d+(\.\d+)?[KMGT]?$", size):
        err(400, "invalid disk or size (e.g. +10G)")
    node, kind = guest_path(s, vmid)
    if kind == "qemu":
        upid = pcall(s, "PUT", f"/nodes/{node}/qemu/{vmid}/resize", disk=disk, size=size)
    else:
        upid = pcall(s, "PUT", f"/nodes/{node}/lxc/{vmid}/resize", disk=disk, size=size)
    if isinstance(upid, str) and upid.startswith("UPID:"):
        return run_task(s, node, upid)
    drop_cache()
    return {"status": "OK"}


# ---------------------------------------------------------------------------
# I/O limits (IOPS / bandwidth)
#   - array:   QoS of the IBM array (mkthrottle on the volume or on the pool)
#   - proxmox: QEMU throttling of the VM disk (iops / mbps options)
# ---------------------------------------------------------------------------

QEMU_LIMIT_KEYS = ("iops", "iops_rd", "iops_wr", "mbps", "mbps_rd", "mbps_wr",
                   "iops_max", "iops_rd_max", "iops_wr_max", "mbps_max", "mbps_rd_max", "mbps_wr_max")


def vdisk_throttle(a, obj, ttype="vdisk"):
    for t in a.get("throttles") or []:
        if t.get("throttle_type") == ttype and t.get("object_name") == obj:
            return {"id": t.get("throttle_id"), "name": t.get("throttle_name"),
                    "iops": to_int(t.get("IOPs_limit")) or None, "mbps": to_int(t.get("bandwidth_limit_MB")) or None}
    return None


def limit_value(v, maximum):
    if v in (None, "", 0, "0"):
        return None
    try:
        n = int(v)
    except (TypeError, ValueError):
        err(400, f"invalid limit value: {v!r}")
    if n < 1 or n > maximum:
        err(400, f"limit out of range (1 to {maximum})")
    return n


def set_array_throttle(scfg, ttype, obj, iops, mbps):
    """Replace the throttle of a volume/pool: rmthrottle + mkthrottle (or only rm)."""
    a = array_for(scfg)
    rows = a.ls("lsthrottle")
    for t in rows:
        if t.get("throttle_type") == ttype and t.get("object_name") == obj:
            a.run("rmthrottle", obj=t["throttle_id"])
    if iops or mbps:
        opts = [("type", ttype), (ttype, obj)]
        if iops:
            opts.append(("iops", iops))
        if mbps:
            opts.append(("bandwidth", mbps))
        a.run("mkthrottle", opts)
    drop_cache(f"array:{scfg['storage']}")


@app.post("/api/vms/{vmid}/disks/{disk}/limits")
async def disk_limits(vmid: int, disk: str, request: Request, s=Depends(session)):
    b = await request.json()
    if not DISK_KEY_RE.match(disk):
        err(400, "invalid disk")
    level = b.get("level")
    iops, mbps = limit_value(b.get("iops"), 10_000_000), limit_value(b.get("mbps"), 100_000)
    node, kind = guest_path(s, vmid)
    conf = pcall(s, "GET", f"/nodes/{node}/{kind}/{vmid}/config")
    val = conf.get(disk)
    if not isinstance(val, str):
        err(404, f"disk {disk} does not exist in VM {vmid}")
    volid = val.split(",")[0]
    st, _, volname = volid.partition(":")
    scfg = storage_cfg(s, st)
    if level == "array":
        if "Sys.Modify" not in s["privs"]:
            err(403, "a limit on the array requires the Sys.Modify privilege on /")
        try:
            set_array_throttle(scfg, "vdisk", f"{scfg.get('sw-prefix', 'pve')}-{volname}", iops, mbps)
        except ArrayError as e:
            err(502, f"array: {e}")
        return {"ok": True, "level": "array", "iops": iops, "mbps": mbps}
    if level == "proxmox":
        if kind != "qemu":
            err(400, "QEMU limits apply to VMs only (for a CT use the limit on the array)")
        parts = [p for p in val.split(",")[1:] if p.split("=", 1)[0] not in QEMU_LIMIT_KEYS]
        if iops:
            parts.append(f"iops={iops}")
        if mbps:
            parts.append(f"mbps={mbps}")
        pcall(s, "PUT", f"/nodes/{node}/qemu/{vmid}/config", **{disk: ",".join([volid] + parts)})
        drop_cache()
        return {"ok": True, "level": "proxmox", "iops": iops, "mbps": mbps}
    err(400, "level must be 'array' or 'proxmox'")


@app.post("/api/storages/{storeid}/limits")
async def pool_limits(storeid: str, request: Request, s=Depends(admin)):
    b = await request.json()
    scfg = storage_cfg(s, storeid)
    iops, mbps = limit_value(b.get("iops"), 10_000_000), limit_value(b.get("mbps"), 100_000)
    try:
        set_array_throttle(scfg, "mdiskgrp", scfg["sw-pool"], iops, mbps)
    except ArrayError as e:
        err(502, f"array: {e}")
    return {"ok": True, "iops": iops, "mbps": mbps}


# ---------------------------------------------------------------------------
# Hosts of an existing storage (array host objects + who receives the volumes)
# ---------------------------------------------------------------------------

IQN_RE = re.compile(r"^(iqn\.\d{4}-\d{2}\.[A-Za-z0-9.:-]+|eui\.[0-9A-Fa-f]{16})$")


def managed_vdisks(a, scfg):
    pfx = scfg.get("sw-prefix", "pve") + "-vm-"
    return [v["name"] for v in a.ls("lsvdisk") if v["name"].startswith(pfx) and "_s_" not in v["name"]]


def pve_nodes_ports(s):
    out = []
    for n in pcall(s, "GET", "/cluster/status") or []:
        if n.get("type") != "node":
            continue
        info = {"node": n["name"], "ip": n.get("ip"), "online": n.get("online"), "wwpns": None, "iqn": None}
        try:
            st = nodessh.status(n.get("ip"))
            info["wwpns"] = [f["wwpn"] for f in st["fc"]]
            info["iqn"] = st["iqn"] or None
        except Exception:
            pass
        out.append(info)
    return out


@app.get("/api/storages/{storeid}/hosts")
def hosts_info(storeid: str, s=Depends(session)):
    c = storage_cfg(s, storeid)
    snap = array_snapshot(c)                 # hosts, mappings and volumes already come from the cached array read
    if not snap.get("ok"):
        err(502, f"array: {snap.get('error')}")
    hosts, hcs, maps, caps = snap["hosts"], snap["hostclusters"], snap["hostmaps"], snap["capabilities"]
    pfx = c.get("sw-prefix", "pve") + "-vm-"
    vols = [v["name"] for v in snap["vdisks"] if v["name"].startswith(pfx) and "_s_" not in v["name"]]

    def load_ports():                        # only the port detail needs one query per host
        a = array_for(c)

        def one(h):
            return h["name"], [(k, v.upper() if k == "WWPN" else v) for k, v in a.raw("lshost", h["name"])
                               if k in ("WWPN", "iscsi_name", "nqn", "state")]

        def candidates():
            try:
                return [(r.get("HBA_WWPN") or r.get("id") or "").upper() for r in a.ls("lshbaportcandidate")]
            except ArrayError:
                return []
        with ThreadPoolExecutor(max_workers=6) as ex:
            fc = ex.submit(candidates)
            return dict(ex.map(one, hosts)), fc.result()
    try:
        ports, cand = cached(f"array:{storeid}:hostports", 30, load_ports)
    except (ArrayError, subprocess.TimeoutExpired) as e:
        err(502, f"array: {e}")
    hosts_cfg = [h for h in (c.get("sw-hosts") or "").split(",") if h]
    out = []
    for h in hosts:
        p = ports.get(h["name"], [])
        wwpns, iqns, cur = [], [], None
        for k, v in p:
            if k == "WWPN":
                cur = {"wwpn": v, "state": None}
                wwpns.append(cur)
            elif k == "state" and cur is not None and "state" in cur and cur["state"] is None:
                cur["state"] = v
            elif k in ("iscsi_name", "nqn"):
                iqns.append(v)
        mapped = {m["vdisk_name"] for m in maps if m.get("name") == h["name"]}
        out.append({**h, "wwpns": wwpns, "iqns": iqns,
                    "attached": (h["name"] in hosts_cfg) or bool(c.get("sw-hostcluster") and h.get("host_cluster_name") == c["sw-hostcluster"]),
                    "volumes_total": len(vols), "volumes_mapped": len([v for v in vols if v in mapped])})
    return {"mode": "hostcluster" if c.get("sw-hostcluster") else "hosts", "hostcluster": c.get("sw-hostcluster"),
            "hosts": out, "hostclusters": hcs, "candidates": [x for x in cand if x], "host_cluster_supported": caps["host_cluster"],
            "pve_nodes": cached("nodeports", 60, lambda: pve_nodes_ports(s))}


# ---------------------------------------------------------------------------
# Fibre Channel health: array ports <-> node ports, light levels and errors
# ---------------------------------------------------------------------------

FC_HOST_COUNTERS = ("link_failure_count", "loss_of_sync_count", "loss_of_signal_count", "invalid_crc_count",
                    "invalid_tx_word_count", "prim_seq_protocol_err_count", "error_frames")
FC_ARRAY_COUNTERS = {"lf": "link failures", "lsy": "sync losses", "lsi": "signal losses",
                     "pspe": "protocol errors", "itw": "invalid words", "icrc": "CRC errors"}


def remember_nodes(s):
    try:
        nodes = {n["name"]: n.get("ip") for n in pcall(s, "GET", "/cluster/status") or [] if n.get("type") == "node"}
        path = os.path.join(store.DATA_DIR, "nodes.json")
        old = json.load(open(path)) if os.path.exists(path) else {}
        if old != nodes:
            json.dump(nodes, open(path, "w"))
    except Exception:
        pass


def node_fc(s):
    """FC ports of each managed node, with SFP data and how much the counters rose in 24 h."""
    remember_nodes(s)
    out = []
    for n in pcall(s, "GET", "/cluster/status") or []:
        if n.get("type") != "node":
            continue
        try:
            st = nodessh.status(n.get("ip"))
        except Exception as e:
            out.append({"node": n["name"], "error": str(e), "ports": []})
            continue
        ports = []
        for f in st["fc"]:
            cnt = {k: f["counters"].get(k, 0) for k in FC_HOST_COUNTERS}
            pfx = f"node:{n['name']}:{f['wwpn']}"
            store.save_counters(pfx, cnt)
            ports.append({**f, "deltas": store.counter_deltas(pfx, cnt)})
        out.append({"node": n["name"], "ip": n.get("ip"), "ports": ports})
    return out


def array_fc(scfg):
    """FC ports of the array (lsportfc) + error counters from the Nn_stats files."""
    def load():
        a = array_for(scfg)
        ports = a.ls("lsportfc")
        for p in ports:
            if p.get("status") == "active":
                d = a.show("lsportfc", obj=p["id"]) or {}
                p["switch_WWPN"] = (d.get("switch_WWPN") or "").upper()
                p["fabric_WWN"] = (d.get("fabric_WWN") or "").upper()
        counters, note = {}, None
        try:
            dumps = [r.get("filename", "") for r in a.ls("lsdumps", [("prefix", "/dumps/iostats")])]
            try:
                nodes = a.ls("lsnodecanister")
            except ArrayError:
                nodes = a.ls("lsnode")          # SAN Volume Controller
            def nn_files(nid):
                return sorted((f for f in dumps if f.startswith("Nn_stats_") and f.split("_")[2].endswith(f"-{nid}")),
                              key=lambda f: f.split("_", 3)[3])

            def fresh(name):
                try:
                    t = time.mktime(time.strptime(name.split("_", 3)[3], "%y%m%d_%H%M%S"))
                except ValueError:
                    return False
                return abs(time.time() - t) < 3 * 3600
            for nd in nodes:
                mine = [f for f in nn_files(nd["id"]) if fresh(f)]
                if not mine and nd.get("config_node") != "yes":
                    try:     # bring the other canister's files to the configuration node
                        a.run("cpdumps", [("prefix", f"/dumps/iostats/Nn_stats_*-{nd['id']}_*")], obj=nd["name"])
                        dumps = [r.get("filename", "") for r in a.ls("lsdumps", [("prefix", "/dumps/iostats")])]
                        mine = [f for f in nn_files(nd["id"]) if fresh(f)]
                    except ArrayError:
                        pass
                if not mine:
                    continue
                fd, tmp = tempfile.mkstemp(prefix="nnstats-", dir=store.DATA_DIR)
                os.close(fd)
                try:
                    a.fetch(f"/dumps/iostats/{mine[-1]}", tmp)
                    xml = open(tmp).read()
                finally:
                    os.unlink(tmp)
                for mt in re.finditer(r"<port\s([^>]*)>", xml):
                    at = dict(re.findall(r'(\w+)="([^"]*)"', mt.group(1)))
                    if at.get("type") != "FC":
                        continue
                    w = at.get("wwpn", "").replace("0x", "").upper()
                    counters[w] = {k: int(at.get(k, 0) or 0) for k in FC_ARRAY_COUNTERS}
        except ArrayError as e:
            note = f"counters unavailable: {e}"
        for p in ports:
            c = counters.get(p.get("WWPN", "").upper())
            if c is not None:
                pfx = f"array:{scfg['sw-address']}:{p['WWPN']}"
                store.save_counters(pfx, c)
                p["counters"] = c
                p["deltas"] = store.counter_deltas(pfx, c)
        return {"ports": ports, "note": note, "fetched": int(time.time())}

    return cached(f"fc:{scfg['sw-address']}", 300, load)


@app.get("/api/storages/{storeid}/fc")
def storage_fc(storeid: str, s=Depends(session)):
    c = storage_cfg(s, storeid)
    try:
        arr = array_fc(c)
    except ArrayError as e:
        err(502, f"array: {e}")
    nodes = cached("nodefc", 30, lambda: node_fc(s))        # SSH to the nodes: kept for 30 s and refreshed in the background
    by_wwpn = {p["wwpn"]: (n["node"], p) for n in nodes for p in n["ports"]}
    for p in arr["ports"]:
        peer = by_wwpn.get(p.get("switch_WWPN", ""))
        if peer:                       # direct attach: the "switch" is the node's own HBA
            p["peer"] = {"node": peer[0], "host": peer[1]["host"], "wwpn": peer[1]["wwpn"], "direct": True}
        elif p.get("switch_WWPN"):
            p["peer"] = {"switch": p["switch_WWPN"], "direct": False}
    return {"array": arr, "nodes": nodes, "counter_labels": {**FC_ARRAY_COUNTERS}}


# ---------------------------------------------------------------------------
# Backup through an array snapshot (QEMU CBT + FlashCopy) -> PBS
# ---------------------------------------------------------------------------

CBT_DIR = "/usr/local/lib/ibm-storage-cbt"
CBT_LOG = "/var/lib/ibm-storage-cbt/logs"


@app.get("/api/pbs-storages")
def pbs_storages(s=Depends(session)):
    return [{"storage": x["storage"], "datastore": x.get("datastore"), "server": x.get("server")}
            for x in (pcall(s, "GET", "/storage") or []) if x.get("type") == "pbs"]


@app.post("/api/vms/{vmid}/cbt-backup")
async def cbt_backup(vmid: int, request: Request, s=Depends(admin)):
    b = await request.json()
    pbs = b.get("pbs", "")
    if pbs not in {x["storage"] for x in pbs_storages(s)}:
        err(400, "choose a valid PBS storage")
    node, kind = guest_path(s, vmid)
    if kind != "qemu":
        err(400, "backup through an array snapshot is for VMs only")
    ip = node_ip(s, node)
    ts = int(time.time())
    unit = f"ibm-storage-cbt-{vmid}-{ts}"
    full = (" --full" if b.get("full") else "") + ("" if b.get("fallback", True) else " --no-fallback") + (" --protected" if b.get("protected") else "")
    cmd = (f"test -x {CBT_DIR}/cbt_job.py || {{ echo 'tool not installed on this node - use Prepare node'; exit 3; }}; "
           f"mkdir -p {CBT_LOG} && systemd-run --unit {unit} --collect --property=Type=exec "
           f"/bin/sh -c '/usr/bin/python3 {CBT_DIR}/cbt_job.py {vmid} {pbs}{full} > {CBT_LOG}/{vmid}-{ts}.json 2>&1'")
    rc, o, e = nodessh.run(ip, cmd, 60)
    if rc != 0:
        err(500, (o + e).strip() or f"rc={rc}")
    return {"started": True, "unit": unit, "node": node, "id": ts}


@app.get("/api/vms/{vmid}/cbt-backups")
def cbt_history(vmid: int, s=Depends(session)):
    node, _ = guest_path(s, vmid)
    try:
        ip = node_ip(s, node)
        script = (f"cd {CBT_LOG} 2>/dev/null || exit 0; for f in $(ls -1t {vmid}-*.json 2>/dev/null | head -10); do "
                  f"ts=${{f#{vmid}-}}; ts=${{ts%.json}}; st=$(systemctl is-active ibm-storage-cbt-{vmid}-$ts 2>/dev/null); "
                  "echo \"@@@ $ts $st\"; tail -c 6000 $f; echo; done")
        rc, o, e = nodessh.run(ip, script, 30)
    except Exception as ex:
        return {"installed": False, "error": str(ex), "items": []}
    items = []
    for blk in o.split("@@@ ")[1:]:
        head, _, body = blk.partition("\n")
        ts, _, state = head.partition(" ")
        item = {"id": int(ts), "running": state.strip() in ("active", "activating")}
        try:
            j = body[body.index("{"):body.rindex("}") + 1]
            item["result"] = json.loads(j)
        except ValueError:
            item["log"] = body.strip()[-1500:]
        items.append(item)
    return {"installed": True, "items": items}


# ---------------------------------------------------------------------------
# Wizard: add a storage (everything from the panel)
# ---------------------------------------------------------------------------

def wizard(wid):
    w = WIZARDS.get(wid)
    if not w or time.time() - w["created"] > 3600:
        err(404, "wizard expired - start again")
    return w


def wizard_array(w):
    if not w.get("address") or not w.get("hostkeys"):
        err(400, "confirm the array address and key first")
    fd, kh = tempfile.mkstemp(prefix="kh-", dir=store.DATA_DIR)
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(w["hostkeys"]) + "\n")
    w["kh"] = kh
    return Array(w["address"], w["user"], w["key"], kh)


@app.post("/api/wizard/start")
async def wiz_start(request: Request, s=Depends(admin)):
    b = await request.json()
    address, user = b.get("address", "").strip(), b.get("user", "pveplugin").strip()
    if not ADDR_RE.match(address) or not USER_RE.match(user):
        err(400, "invalid address or user")
    try:
        lines, fps = scan_host_keys(address)
    except ArrayError as e:
        err(502, str(e))
    wid = secrets.token_urlsafe(12)
    key = os.path.join(store.KEY_DIR, f"wizard-{wid}")
    subprocess.run(["ssh-keygen", "-q", "-t", "rsa", "-b", "4096", "-m", "PEM", "-N", "",
                    "-C", f"pveplugin@storwize-panel", "-f", key], check=True)
    WIZARDS[wid] = {"created": time.time(), "address": address, "user": user, "key": key,
                    "hostkeys_offered": lines, "hostkeys": None}
    return {"wid": wid, "pubkey": open(key + ".pub").read().strip(), "fingerprints": fps}


@app.post("/api/wizard/{wid}/trust")
def wiz_trust(wid: str, s=Depends(admin)):
    w = wizard(wid)
    w["hostkeys"] = w["hostkeys_offered"]
    return {"ok": True}


@app.post("/api/wizard/{wid}/test")
def wiz_test(wid: str, s=Depends(admin)):
    w = wizard(wid)
    a = wizard_array(w)
    try:
        sysd = a.show("lssystem")
        caps = capabilities(sysd.get("code_level"))
        pools = a.ls("lsmdiskgrp", [("bytes", None)])
        hosts = a.ls("lshost")
        hostdetail = {}
        for h in hosts:
            hostdetail[h["name"]] = [v.upper() if k == "WWPN" else v
                                     for k, v in a.raw("lshost", h["name"]) if k in ("WWPN", "iscsi_name")]
        hcs = a.ls("lshostcluster") if caps["host_cluster"] else []
        try:
            cand = [r.get("HBA_WWPN") or r.get("id") for r in a.ls("lshbaportcandidate")]
        except ArrayError:
            cand = []
        lic = a.show("lslicense") or {}
        parts = [p for p in sv8.partitions(a) if not p["draft"]] if caps.get("partition") else []
    except ArrayError as e:
        err(502, f"array: {e}")
    w["system"] = sysd
    # existing storwize storages on the same array -> prefix suggestions
    used_prefixes = [c.get("sw-prefix", "pve") for c in storwize_storages(s)
                     if c.get("sw-address", "").lower() == w["address"].lower()]
    # Proxmox nodes and their WWPNs (when the panel already manages them)
    pnodes = []
    for n in pcall(s, "GET", "/cluster/status") or []:
        if n.get("type") != "node":
            continue
        info = {"node": n["name"], "ip": n.get("ip"), "online": n.get("online")}
        try:
            st = nodessh.status(n.get("ip"))
            info["wwpns"] = [f["wwpn"] for f in st["fc"]]
            info["iqn"] = st["iqn"]
        except Exception:
            info["wwpns"] = None
        pnodes.append(info)
    return {"system": {k: sysd.get(k) for k in ("name", "product_name", "code_level", "id")},
            "capabilities": caps, "pools": pools, "hosts": hosts, "host_ports": hostdetail,
            "hostclusters": hcs, "candidates": cand, "license": lic,
            "used_prefixes": used_prefixes, "pve_nodes": pnodes, "partitions": parts,
            "plugin_type": plugin_type(sysd.get("product_name"))}


@app.post("/api/wizard/{wid}/childpool")
async def wiz_childpool(wid: str, request: Request, s=Depends(admin)):
    b = await request.json()
    w = wizard(wid)
    name, parent, size = b.get("name", ""), b.get("parent", ""), to_int(b.get("size_gb"))
    if not NAME_RE.match(name) or not NAME_RE.match(parent) or size <= 0:
        err(400, "invalid name, parent pool or size")
    try:
        r = wizard_array(w).run("mkmdiskgrp", [("name", name), ("parentmdiskgrp", parent),
                                               ("size", size), ("unit", "gb")])
    except ArrayError as e:
        err(502, f"array: {e}")
    return r


@app.post("/api/wizard/{wid}/host")
async def wiz_host(wid: str, request: Request, s=Depends(admin)):
    b = await request.json()
    w = wizard(wid)
    name, wwpns = b.get("name", ""), [x.upper() for x in b.get("wwpns", [])]
    if not NAME_RE.match(name) or not wwpns or not all(WWPN_RE.match(x) for x in wwpns):
        err(400, "invalid name or WWPN")
    try:
        r = wizard_array(w).run("mkhost", [("name", name), ("fcwwpn", ":".join(wwpns)), ("type", "generic")])
    except ArrayError as e:
        err(502, f"array: {e}")
    return r


@app.post("/api/wizard/{wid}/hostcluster")
async def wiz_hostcluster(wid: str, request: Request, s=Depends(admin)):
    b = await request.json()
    w = wizard(wid)
    name, members = b.get("name", ""), b.get("members", [])
    if not NAME_RE.match(name) or not all(NAME_RE.match(m) for m in members):
        err(400, "invalid name")
    a = wizard_array(w)
    try:
        if not a.show("lshostcluster", obj=name):
            a.run("mkhostcluster", [("name", name)])
        for m in members:
            a.run("addhostclustermember", [("host", m)], obj=name)
    except ArrayError as e:
        err(502, f"array: {e}")
    return {"ok": True}


@app.post("/api/wizard/{wid}/register")
async def wiz_register(wid: str, request: Request, s=Depends(admin)):
    b = await request.json()
    w = wizard(wid)
    storeid, pool = b.get("storage", ""), b.get("pool", "")
    prefix = b.get("prefix") or "pve"
    if not STOREID_RE.match(storeid) or not NAME_RE.match(pool) or not PREFIX_RE.match(prefix):
        err(400, "invalid storage ID, pool or prefix")
    p = {"type": plugin_type((w.get("system") or {}).get("product_name")), "storage": storeid, "sw-address": w["address"], "sw-user": w["user"],
         "sw-api": "ssh", "sw-pool": pool, "sw-prefix": prefix, "content": b.get("content") or "images",
         "shared": 1, "sw-thin": 1 if b.get("thin", True) else 0,
         "sw-sshprivkey": base64.b64encode(open(w["key"], "rb").read()).decode(),
         "sw-sshhostkey": b64("\n".join(w["hostkeys"]))}
    if b.get("hostcluster"):
        if not NAME_RE.match(b["hostcluster"]):
            err(400, "invalid host cluster")
        p["sw-hostcluster"] = b["hostcluster"]
    else:
        hosts = [h for h in b.get("hosts", []) if NAME_RE.match(h)]
        if not hosts:
            err(400, "choose the hosts or the host cluster")
        p["sw-hosts"] = ",".join(hosts)
    if b.get("snappool"):
        p["sw-snappool"] = b["snappool"]
    p["sw-consistency"] = 1 if b.get("consistency", True) else 0
    if b.get("transport") == "nvme-tcp":
        portals = str(b.get("portals", "")).replace(" ", "")
        if not re.match(r"^[0-9A-Fa-f:.\[\]]+(,[0-9A-Fa-f:.\[\]]+)*$", portals):
            err(400, "enter the IPs of the array NVMe/TCP ports (IP or IP:port, comma-separated)")
        p["sw-transport"], p["sw-portals"] = "nvme-tcp", portals
    if b.get("partition"):
        if not NAME_RE.match(b["partition"]):
            err(400, "invalid storage partition")
        p["sw-partition"] = b["partition"]
        b["snapmode"] = "volumegroup"            # inside a partition every volume lives in a volume group
    if b.get("snapmode") == "volumegroup":
        p["sw-snapmode"] = "volumegroup"
    if b.get("nodes"):
        p["nodes"] = ",".join(b["nodes"])
    pcall(s, "POST", "/storage", **p)
    store.install_array_key(storeid, w["key"])
    store.add_known_hosts(w["address"], w["hostkeys"])
    if w.get("kh") and os.path.exists(w["kh"]):
        os.unlink(w["kh"])
    WIZARDS.pop(wid, None)
    drop_cache()
    return {"storage": storeid}


@app.delete("/api/wizard/{wid}")
def wiz_cancel(wid: str, s=Depends(admin)):
    w = WIZARDS.pop(wid, None)
    if w:
        for f in (w["key"], w["key"] + ".pub", w.get("kh")):
            if f and os.path.exists(f):
                os.unlink(f)
    return {}


# --- reuse the plugin key (panel reinstalled) ---------------------------

@app.post("/api/storages/{storeid}/credential/import")
def cred_import(storeid: str, s=Depends(admin)):
    import_plugin_key(s, storage_cfg(s, storeid))
    return {"ok": True}


def import_missing_keys(s):
    """Proxmox keeps the array key of each storage; a new or reinstalled panel takes it from there
    as soon as it can reach a node, so the array key does not have to change."""
    done = []
    for c in storwize_storages(s):
        if store.has_array_key(c["storage"]):
            continue
        try:
            import_plugin_key(s, c)
            done.append(c["storage"])
        except Exception:
            pass
    return done


def import_plugin_key(s, c):
    storeid = c["storage"]
    if not STOREID_RE.match(storeid) or not ADDR_RE.match(c.get("sw-address", "")):
        err(400, "invalid storage")
    if c.get("sw-sshkey"):
        err(400, "this storage uses its own key path (sw-sshkey) - generate a new credential")
    out = None
    for n in pcall(s, "GET", "/cluster/status") or []:
        if n.get("type") != "node" or not n.get("online"):
            continue
        try:
            rc, o, e = nodessh.run(n.get("ip"), f"cat /etc/pve/priv/storwize/{storeid}_id_rsa && echo @@KH@@ && "
                                                f"grep -F -- '{c['sw-address']} ' /etc/pve/priv/storwize/known_hosts", 30)
        except Exception:
            continue
        if rc == 0 and "@@KH@@" in o:
            out = o
            break
    if not out:
        err(409, "could not read the plugin key - authorize a node under Proxmox nodes first")
    key, _, kh = out.partition("@@KH@@")
    lines = [l for l in kh.strip().splitlines() if l.split(" ")[0] == c["sw-address"]]
    if "PRIVATE KEY" not in key or not lines:
        err(500, "array key or host key not found in Proxmox")
    fd, tmp = tempfile.mkstemp(prefix="import-", dir=store.KEY_DIR)
    with os.fdopen(fd, "w") as f:
        f.write(key.strip() + "\n")
    os.chmod(tmp, 0o600)
    fd, khf = tempfile.mkstemp(prefix="kh-", dir=store.DATA_DIR)
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(lines) + "\n")
    try:
        Array(c["sw-address"], c["sw-user"], tmp, khf).show("lssystem")
    except ArrayError as e:
        os.unlink(tmp)
        err(502, f"the plugin key did not open the array: {e}")
    finally:
        os.unlink(khf)
    store.install_array_key(storeid, tmp)
    store.add_known_hosts(c["sw-address"], lines)
    drop_cache()


# --- give the panel a credential for an existing storage ---------------

def pending_key(storeid):
    if not STOREID_RE.match(storeid):
        err(400, "invalid storage")
    return os.path.join(store.KEY_DIR, f"rekey-{storeid}")


@app.post("/api/storages/{storeid}/credential")
def cred_new(storeid: str, s=Depends(admin)):
    c = storage_cfg(s, storeid)
    key = pending_key(storeid)
    # a key generated in the last day and not applied yet is offered again: it may already be
    # on the array, and a second one would lock out whoever installed the first
    if not (os.path.exists(key) and os.path.exists(key + ".pub") and time.time() - os.path.getmtime(key) < 86400):
        for f in (key, key + ".pub"):
            if os.path.exists(f):
                os.unlink(f)
        subprocess.run(["ssh-keygen", "-q", "-t", "rsa", "-b", "4096", "-m", "PEM", "-N", "",
                        "-C", "pveplugin@ibm-storage-panel", "-f", key], check=True)
    lines, fps = scan_host_keys(c["sw-address"])
    WIZARDS[f"rekey-{storeid}"] = {"created": time.time(), "key": key, "hostkeys": lines}
    return {"pubkey": open(key + ".pub").read().strip(), "fingerprints": fps, "user": c["sw-user"]}


@app.post("/api/storages/{storeid}/credential/apply")
def cred_apply(storeid: str, s=Depends(admin)):
    c = storage_cfg(s, storeid)
    w = WIZARDS.get(f"rekey-{storeid}")
    if not w:
        # the panel restarted between the two steps: the key is still on disk
        key = pending_key(storeid)
        if not os.path.exists(key):
            err(404, "no pending key - generate the credential again")
        w = {"key": key, "hostkeys": scan_host_keys(c["sw-address"])[0]}
    fd, kh = tempfile.mkstemp(prefix="kh-", dir=store.DATA_DIR)
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(w["hostkeys"]) + "\n")
    try:
        Array(c["sw-address"], c["sw-user"], w["key"], kh).show("lssystem")
    except ArrayError as e:
        err(502, f"the new key does not work on the array yet: {e}")
    finally:
        os.unlink(kh)
    pcall(s, "PUT", f"/storage/{storeid}",
          **{"sw-sshprivkey": base64.b64encode(open(w["key"], "rb").read()).decode(),
             "sw-sshhostkey": b64("\n".join(w["hostkeys"]))})
    store.install_array_key(storeid, w["key"])
    store.add_known_hosts(c["sw-address"], w["hostkeys"])
    WIZARDS.pop(f"rekey-{storeid}", None)
    drop_cache()
    return {"ok": True}


# ---------------------------------------------------------------------------
# Proxmox nodes: preparation without touching config files by hand
# ---------------------------------------------------------------------------

def node_ip(s, node):
    for n in pcall(s, "GET", "/cluster/status") or []:
        if n.get("type") == "node" and n.get("name") == node:
            return n.get("ip")
    err(404, f"node {node} not found")


@app.get("/api/nodes")
def nodes(s=Depends(session)):
    remember_nodes(s)
    out = []
    plugin_ver = plugin_version()
    for n in pcall(s, "GET", "/cluster/status") or []:
        if n.get("type") != "node":
            continue
        item = {"node": n["name"], "ip": n.get("ip"), "online": n.get("online"), "managed": False}
        try:
            st = nodessh.status(n.get("ip"))
            item.update(managed=True, **st)
            item["plugin_current"] = plugin_ver
            item["multipath_ok"] = st["multipath_conf"].strip() == MULTIPATH_CONF.strip() or st["multipath_has_2145"]
            item["multipath_diff"] = nodessh.multipath_diff(st["multipath_conf"] + "\n")
        except Exception as e:
            item["error"] = str(e)
        out.append(item)
    return out


@app.get("/api/nodes/{node}/hostkey")
def node_hostkey(node: str, s=Depends(admin)):
    try:
        return NodeSSH.probe_hostkey(node_ip(s, node))
    except Exception as e:
        err(502, f"could not connect to {node}: {e}")


@app.post("/api/nodes/{node}/trust")
async def node_trust(node: str, request: Request, s=Depends(admin)):
    b = await request.json()
    ip = node_ip(s, node)
    hk = NodeSSH.probe_hostkey(ip)
    if hk["fingerprint"] != b.get("fingerprint"):
        err(409, "the node key changed since it was confirmed - start over")
    try:
        nodessh.trust(ip, b.get("password", ""), hk)
    except NodeError as e:
        err(401, str(e))
    return {"ok": True, "imported": import_missing_keys(s)}


def plugin_version():
    try:
        src = open(os.path.join(ASSETS, "StorwizePlugin.pm")).read()
        return re.search(r"our \$VERSION = '([^']+)'", src).group(1)
    except Exception:
        return None


@app.post("/api/nodes/{node}/prepare")
async def node_prepare(node: str, request: Request, s=Depends(admin)):
    b = await request.json()
    ip = node_ip(s, node)
    log = []

    def run(title, cmd, timeout=900):
        rc, o, e = nodessh.run(ip, cmd, timeout)
        log.append({"step": title, "rc": rc, "out": (o + e)[-4000:]})
        if rc != 0:
            raise NodeError(f"{title} failed (rc={rc})")

    try:
        if b.get("packages"):
            run("packages", "export DEBIAN_FRONTEND=noninteractive; (apt-get update -q || true) && "
                            "apt-get install -y -q multipath-tools sg3-utils")
        if b.get("multipath"):
            ts = time.strftime("%Y%m%d-%H%M%S")
            run("backup multipath.conf", f"[ -f /etc/multipath.conf ] && cp -a /etc/multipath.conf "
                                         f"/etc/multipath.conf.bak.{ts} || true")
            nodessh.put(ip, "/etc/multipath.conf", MULTIPATH_CONF)
            run("multipathd", "systemctl enable --now multipathd && multipathd reconfigure && sleep 2 && "
                              "multipath -ll | grep -c 'IBM,2145' || true")
        if b.get("lvmfilter"):
            run("LVM filter", LVM_FILTER_SCRIPT)
        if b.get("nvme"):                        # NVMe over TCP: native kernel multipath, no dm-multipath
            run("NVMe/TCP: package", "export DEBIAN_FRONTEND=noninteractive; dpkg -s nvme-cli >/dev/null 2>&1 || "
                                     "{ (apt-get update -q || true) && apt-get install -y -q nvme-cli; }")
            run("NVMe/TCP: module and NQN", "echo nvme-tcp > /etc/modules-load.d/nvme-tcp.conf && modprobe nvme-tcp && "
                "mkdir -p /etc/nvme && { [ -s /etc/nvme/hostnqn ] || nvme gen-hostnqn > /etc/nvme/hostnqn; } && "
                "{ [ -s /etc/nvme/hostid ] || cat /proc/sys/kernel/random/uuid > /etc/nvme/hostid; } && cat /etc/nvme/hostnqn && "
                "echo native multipath: $(cat /sys/module/nvme_core/parameters/multipath)")
            nodessh.put(ip, "/etc/udev/rules.d/71-nvme-ibm-iopolicy.rules", NVME_IOPOLICY_RULE)
            run("NVMe/TCP: path policy", "udevadm control --reload && udevadm trigger --subsystem-match=nvme-subsystem; "
                                         "systemctl enable nvmf-autoconnect.service 2>/dev/null || true")
        if b.get("cbt"):
            run("snapshot backup: dependencies", "export DEBIAN_FRONTEND=noninteractive; "
                "dpkg -s python3-libnbd >/dev/null 2>&1 || { (apt-get update -q || true) && apt-get install -y -q python3-libnbd; }")
            # releases before 0.3.2 kept these under "storwize-cbt"; carry the backup state over
            run("snapshot backup: directories",
                "for d in /usr/local/lib /var/lib /etc; do if [ -d $d/storwize-cbt ] && [ ! -e $d/ibm-storage-cbt ]; "
                "then mv $d/storwize-cbt $d/ibm-storage-cbt; fi; done; "
                f"mkdir -p {CBT_DIR} {CBT_LOG} /var/lib/ibm-storage-cbt && chmod 700 /var/lib/ibm-storage-cbt")
            for f in ("cbt_backup.py", "cbt_job.py", "cbt_sched.py", "sw-snap.pl"):
                nodessh.put(ip, f"{CBT_DIR}/{f}", open(os.path.join(ASSETS, f)).read(), 0o755)
            nodessh.put(ip, f"{CBT_DIR}/VERSION", f"{VERSION}\n")
            run("snapshot backup: validation", f"python3 -c 'import nbd' && perl -c {CBT_DIR}/sw-snap.pl 2>&1 && "
                f"python3 -m py_compile {CBT_DIR}/cbt_backup.py && echo ok")
        if b.get("plugin"):
            run("directory", "mkdir -p /usr/share/perl5/PVE/Storage/Custom")
            nodessh.put(ip, "/usr/share/perl5/PVE/Storage/Custom/StorwizePlugin.pm",
                        open(os.path.join(ASSETS, "StorwizePlugin.pm")).read())
            nodessh.put(ip, "/usr/share/perl5/PVE/Storage/Custom/FlashSystemPlugin.pm",
                        open(os.path.join(ASSETS, "FlashSystemPlugin.pm")).read())
            nodessh.put(ip, "/usr/local/sbin/pve-ibm-storage", open(os.path.join(ASSETS, "pve-ibm-storage")).read(), 0o755)
            run("old command name", "rm -f /usr/local/sbin/pve-storwize")
            run("plugin validation", "perl -MPVE::Storage -e 'PVE::Storage::Plugin->lookup(\"storwize\") "
                                     "&& PVE::Storage::Plugin->lookup(\"flashsystem\") or die; print \"plugin $PVE::Storage::Custom::StorwizePlugin::VERSION ok\\n\"'")
            run("services", "mkdir -p /etc/pve/priv/storwize; chmod 700 /etc/pve/priv/storwize 2>/dev/null; "
                            "systemctl try-restart pvedaemon pvestatd pvescheduler; "
                            "(sleep 3; systemctl try-restart pveproxy) >/dev/null 2>&1 &")
    except NodeError as e:
        return JSONResponse({"ok": False, "error": str(e), "log": log}, status_code=500)
    return {"ok": True, "log": log}


# ---------------------------------------------------------------------------
# collector: performance history
# ---------------------------------------------------------------------------

LUN_SCRIPT = r"""python3 - <<'PYEOF'
import glob, json, os, time
out = {"t": time.time(), "luns": {}}
for dm in glob.glob("/sys/block/dm-*"):
    try:
        u = open(dm + "/dm/uuid").read().strip()
    except OSError:
        continue
    if not u.startswith("mpath-36005076"):
        continue
    f = open(dm + "/stat").read().split()
    out["luns"][u[len("mpath-3"):].upper()] = [int(x) for x in f[:11]]
print(json.dumps(out))
PYEOF
"""
LUN_PREV = {}


def collect_luns():
    """IOPS, MB/s and latency per multipath LUN, as seen by the node (what the VM experiences)."""
    path = os.path.join(store.DATA_DIR, "nodes.json")
    if not os.path.exists(path):
        return
    for node, ip in json.load(open(path)).items():
        try:
            rc, o, e = nodessh.run(ip, LUN_SCRIPT, 30)
            cur = json.loads(o)
        except Exception:
            continue
        prev = LUN_PREV.get(node)
        LUN_PREV[node] = cur
        if not prev:
            continue
        dt = cur["t"] - prev["t"]
        if dt <= 0:
            continue
        for uid, v in cur["luns"].items():
            p = prev["luns"].get(uid)
            if not p:
                continue
            d = [a - b for a, b in zip(v, p)]
            if any(x < 0 for x in d):           # counter reset (device recreated)
                continue
            rio, rsec, rtk, wio, wsec, wtk, iotk = d[0], d[2], d[3], d[4], d[6], d[7], d[9]
            store.save_stats(f"lun:{uid}", {
                "r_iops": round(rio / dt, 1), "w_iops": round(wio / dt, 1),
                "r_mbs": round(rsec * 512 / dt / 2 ** 20, 2), "w_mbs": round(wsec * 512 / dt / 2 ** 20, 2),
                "r_ms": round(rtk / rio, 2) if rio else 0, "w_ms": round(wtk / wio, 2) if wio else 0,
                "util": round(min(100.0, iotk / dt / 10), 1)}, ts=cur["t"])


VD_DONE_FILE = os.path.join(store.DATA_DIR, "vdisk_intervals.json")
try:
    VD_DONE = json.load(open(VD_DONE_FILE))     # survives restarts: the same interval is not saved twice
except (OSError, ValueError):
    VD_DONE = {}


def parse_nv(xml):
    ts = re.search(r'timestamp="([^"]+)"', xml).group(1)
    t = time.mktime(time.strptime(ts, "%Y-%m-%d %H:%M:%S"))
    vols = {}
    for mt in re.finditer(r"<vdsk\s([^>]*)>", xml, re.S):
        a = dict(re.findall(r'(\w+)="([^"]*)"', mt.group(1)))
        if a.get("id"):
            vols[a["id"]] = {k: int(a.get(k, 0) or 0) for k in ("ro", "wo", "rb", "wb", "rl", "wl")}
    return t, vols


def collect_vdisks(address, meta):
    """Per volume, on the array. Each canister records the I/O of the volumes it serves, so the total
    is the sum of the canisters - but only for the SAME interval (files with the same timestamp).
    cpdumps (copying the other canister's files) runs in the background on the array: if the
    interval has not arrived from every canister yet, wait for the next check."""
    a = Array(address, meta["user"], meta["key"], store.ARRAY_KNOWN_HOSTS)
    try:
        nodes = a.ls("lsnodecanister")
    except ArrayError:
        nodes = a.ls("lsnode")
    for nd in nodes:
        if nd.get("config_node") != "yes":
            try:
                a.run("cpdumps", [("prefix", f"/dumps/iostats/Nv_stats_*-{nd['id']}_*")], obj=nd["name"])
            except ArrayError:
                pass
    files = [r.get("filename", "") for r in a.ls("lsdumps", [("prefix", "/dumps/iostats")])]

    def stamp(f):                # Nv_stats_<panel>-<node>_YYMMDD_HHMMSS -> YYMMDD_HHMMSS
        return f.split("_", 3)[3] if f.count("_") >= 3 else ""

    def fresh(st):
        try:
            return abs(time.time() - time.mktime(time.strptime(st, "%y%m%d_%H%M%S"))) < 3 * 3600
        except ValueError:
            return False

    per_node = {}
    for nd in nodes:
        mine = {stamp(f): f for f in files if f.startswith("Nv_stats_") and f.split("_")[2].endswith(f"-{nd['id']}")}
        mine = {s: f for s, f in mine.items() if fresh(s)}
        if mine:
            per_node[nd["id"]] = mine
    if not per_node:
        return
    common = sorted(set.intersection(*(set(v) for v in per_node.values())))
    if len(common) < 2:
        return                                   # some canister does not have the interval yet
    s0, s1 = common[-2], common[-1]
    if VD_DONE.get(address) == s1:
        return
    sums, dt = {}, None
    for nid, mine in per_node.items():
        parsed = []
        for st in (s0, s1):
            fd, tmp = tempfile.mkstemp(prefix="nvstats-", dir=store.DATA_DIR)
            os.close(fd)
            try:
                a.fetch(f"/dumps/iostats/{mine[st]}", tmp)
                parsed.append(parse_nv(open(tmp).read()))
            finally:
                os.unlink(tmp)
        (t0, v0), (t1, v1) = parsed
        dt = t1 - t0
        if not 120 <= dt <= 3600:
            VD_DONE[address] = s1                # abnormal interval (e.g. clock adjusted): discard
            return
        for name, c in v1.items():
            p = v0.get(name)
            if not p:
                continue
            acc = sums.setdefault(name, {k: 0 for k in c})
            for k in c:
                acc[k] += max(0, c[k] - p[k])
    VD_DONE[address] = s1
    try:
        json.dump(VD_DONE, open(VD_DONE_FILE, "w"))
    except OSError:
        pass
    now = int(time.time())                       # node time: the array clock may be off
    for name, s in sums.items():
        store.save_stats(f"vdisk:{address}:{name}", {
            "r_iops": round(s["ro"] / dt, 2), "w_iops": round(s["wo"] / dt, 2),
            "r_mbs": round(s["rb"] * 512 / dt / 2 ** 20, 3), "w_mbs": round(s["wb"] * 512 / dt / 2 ** 20, 3),
            "r_ms": round(s["rl"] / s["ro"], 2) if s["ro"] else 0, "w_ms": round(s["wl"] / s["wo"], 2) if s["wo"] else 0},
            ts=now)


def collector():
    tick = 0
    while True:
        try:
            collect_luns()
        except Exception:
            pass
        if tick % 2 == 0:                       # array: every 2 min (the array writes every 5; a repeated interval is ignored)
            seen = set()
            for k, meta in list(CACHE.items()):
                if not k.startswith("meta:") or not meta or meta.get("address") in seen:
                    continue
                sid = k[5:]
                if not store.has_array_key(sid):
                    continue
                seen.add(meta["address"])
                try:
                    collect_vdisks(meta["address"], {"user": meta["user"], "key": store.array_key(sid)})
                except Exception:
                    pass
        if tick % 1440 == 5:                    # once a day: Let's Encrypt renewal
            try:
                if tlsconf.renew_if_due():
                    restart_soon()
            except Exception:
                pass
        if tick % 10 == 0:                      # DR: VM inventory and copy from the peer site
            try:
                collect_dr()
            except Exception:
                pass
        tick += 1
        try:
            for f in os.listdir(store.KEY_DIR):
                m = re.match(r"^([A-Za-z][A-Za-z0-9_-]+)_id_rsa$", f)
                if not m:
                    continue
                sid = m.group(1)
                meta = CACHE.get(f"meta:{sid}")
                if not meta:
                    continue
                try:
                    a = Array(meta["address"], meta["user"], store.array_key(sid), store.ARRAY_KNOWN_HOSTS)
                    st = {r["stat_name"]: r.get("stat_current") for r in a.ls("lssystemstats")}
                    vals = {k: float(v) for k, v in st.items() if k in STATS_KEEP and v not in (None, "")}
                    store.save_stats(sid, vals)
                except Exception:
                    pass
        except Exception:
            pass
        time.sleep(60)


def remember_meta():
    """The collector runs without a user session: keep array address/user of
    each storage seen by a logged-in user (also persisted for restarts)."""
    import json
    path = os.path.join(store.DATA_DIR, "arrays.json")
    try:
        for sid, m in json.load(open(path)).items():
            CACHE[f"meta:{sid}"] = m
    except Exception:
        pass
    return path


META_FILE = remember_meta()


def update_meta(storages):
    import json
    changed = False
    for c in storages:
        m = {"address": c.get("sw-address"), "user": c.get("sw-user"), "port": int(c.get("sw-port", 22))}
        if CACHE.get(f"meta:{c['storage']}") != m:
            CACHE[f"meta:{c['storage']}"] = m
            changed = True
    if changed:
        data = {k[5:]: v for k, v in CACHE.items() if k.startswith("meta:")}
        with open(META_FILE, "w") as f:
            json.dump(data, f)



# ---------------------------------------------------------------------------
# 8.5.2+: volume group snapshots (Safeguarded) and policy-based replication
# (not validated on 8.x hardware - docs/FS8X-SAFEGUARDED-DR.md)
# ---------------------------------------------------------------------------
def protection_data(scfg, ttl=30):
    def load():
        a = array_for(scfg)
        sysd = a.show("lssystem")
        return sv8.protection(a, scfg.get("sw-prefix", "pve"), sysd, scfg.get("sw-partition"))
    return cached(f"array:{scfg['storage']}:protection", ttl, load)


def vg_checked(scfg, vg):
    if not sv8.VG_RE.match(vg) or sv8.guest_of(scfg.get("sw-prefix", "pve"), vg) is None:
        err(400, "volume group outside the scope of this storage")
    return sv8.guest_of(scfg.get("sw-prefix", "pve"), vg)


def array_call(fn, *a):
    try:
        r = fn(*a)
    except (ArrayError, subprocess.TimeoutExpired) as e:
        msg = str(e)
        if "CMMVC8851E" in msg:
            msg = "the snapshot is Safeguarded: only a Security Administrator of the array can remove it before it expires"
        err(502, f"array: {msg}")
    drop_cache("array:")
    return r


def guest_status(s, vmid):
    for g in pcall(s, "GET", "/cluster/resources", type="vm") or []:
        if g["vmid"] == vmid:
            return g
    return None


@app.get("/api/storages/{storeid}/protection")
def storage_protection(storeid: str, s=Depends(session)):
    scfg = storage_cfg(s, storeid)
    try:
        d = protection_data(scfg)
    except (ArrayError, subprocess.TimeoutExpired) as e:
        err(502, f"array: {e}")
    names = {g["vmid"]: g for g in pcall(s, "GET", "/cluster/resources", type="vm") or []}
    for g in d["groups"]:
        vm = names.get(g["vmid"])
        g["vm_name"] = vm.get("name") if vm else None
        g["vm_status"] = vm.get("status") if vm else None
    d["snapmode"] = scfg.get("sw-snapmode", "flashcopy")
    return d


@app.post("/api/storages/{storeid}/vg/{vg}/snapshots")
async def vg_snapshot_add(storeid: str, vg: str, request: Request, s=Depends(admin)):
    scfg = storage_cfg(s, storeid)
    vg_checked(scfg, vg)
    b = await request.json()
    name, safe = str(b.get("name", "")), bool(b.get("safeguarded"))
    if not sv8.SNAPNAME_RE.match(name):
        err(400, "invalid snapshot name (starts with a letter, letters/digits/_/-, up to 40)")
    days = to_int(b.get("days"))
    if safe and not 1 <= days <= 3650:
        err(400, "Safeguarded snapshot retention: from 1 to 3650 days")
    return array_call(sv8.add_snapshot, array_for(scfg), vg, name, safe, days if safe else None)


@app.post("/api/storages/{storeid}/vg/{vg}/snapshots/{snap}/restore")
def vg_snapshot_restore(storeid: str, vg: str, snap: str, s=Depends(admin)):
    scfg = storage_cfg(s, storeid)
    vmid = vg_checked(scfg, vg)
    if not sv8.SNAPNAME_RE.match(snap):
        err(400, "invalid snapshot name")
    g = guest_status(s, vmid)
    if g and g.get("status") != "stopped":
        err(409, f"shut down VM {vmid} before restoring: the disks go back to the snapshot state")
    return array_call(sv8.restore_snapshot, array_for(scfg), vg, snap)


@app.delete("/api/storages/{storeid}/vg/{vg}/snapshots/{snap}")
def vg_snapshot_remove(storeid: str, vg: str, snap: str, s=Depends(admin)):
    scfg = storage_cfg(s, storeid)
    vg_checked(scfg, vg)
    if not sv8.SNAPNAME_RE.match(snap):
        err(400, "invalid snapshot name")
    return array_call(sv8.remove_snapshot, array_for(scfg), vg, snap)


@app.post("/api/storages/{storeid}/vg/{vg}/policy")
async def vg_policy(storeid: str, vg: str, request: Request, s=Depends(admin)):
    scfg = storage_cfg(s, storeid)
    vg_checked(scfg, vg)
    b = await request.json()
    a = array_for(scfg)
    out = {}
    if "snapshot_policy" in b:
        pol = str(b.get("snapshot_policy") or "")
        if pol and not NAME_RE.match(pol):
            err(400, "invalid policy")
        out["snapshot"] = array_call(sv8.set_snapshot_policy, a, vg, pol, bool(b.get("safeguarded")))
    if "replication_policy" in b:
        pol = str(b.get("replication_policy") or "")
        if pol and not NAME_RE.match(pol):
            err(400, "invalid policy")
        out["replication"] = array_call(sv8.set_replication_policy, a, vg, pol)
    return out


@app.post("/api/storages/{storeid}/vg/{vg}/replication")
async def vg_replication(storeid: str, vg: str, request: Request, s=Depends(admin)):
    scfg = storage_cfg(s, storeid)
    vg_checked(scfg, vg)
    action = str((await request.json()).get("action", ""))
    if action not in sv8.REPL_ACTIONS:
        err(400, "invalid action")
    return array_call(sv8.replication_action, array_for(scfg), vg, action)


# --- DR: VM inventory between the two sites -------------------------------
def collect_dr(force=False):
    path = os.path.join(store.DATA_DIR, "nodes.json")
    if (force or dr.has_token()) and os.path.exists(path):      # only when this site exports to a DR site
        for node, ip in json.load(open(path)).items():
            try:
                rc, o, e = nodessh.run(ip, dr.EXPORT_SCRIPT, 30)
            except Exception:
                continue
            if rc == 0 and "#@STORAGE" in o:
                dr.save_inventory(dr.build_inventory(o))
                break                                   # /etc/pve is cluster-wide: one node is enough
    if dr.peer():
        dr.pull_peer()


@app.get("/api/dr/export")
def dr_export(request: Request):
    tok = request.headers.get("authorization", "")
    if not tok.startswith("Bearer ") or not dr.check_token(tok[7:]):
        err(401, "invalid token")
    inv = dr.local_inventory()
    if not inv:
        err(503, "inventory not collected yet (authorize a node under Proxmox nodes)")
    return inv


@app.get("/api/dr")
def dr_status(s=Depends(session)):
    inv, p, st = dr.local_inventory(), dr.peer(), dr.peer_state()
    local = {x["storage"]: x for x in storwize_storages(s)}
    vms = []
    if st.get("inventory"):
        here = {g["vmid"]: g for g in pcall(s, "GET", "/cluster/resources", type="vm") or []}
        by_prefix = {(c.get("sw-prefix") or "pve"): sid for sid, c in local.items()}
        for vm in st["inventory"]["vms"]:
            srcs = sorted({d["storage"] for d in vm["disks"] if d["storage"] in st["inventory"]["storages"]})
            sugg = {x: by_prefix.get(st["inventory"]["storages"][x]["prefix"]) for x in srcs}
            g = here.get(vm["vmid"])
            vms.append({"vmid": vm["vmid"], "name": vm["name"], "node": vm["node"], "template": vm["template"],
                        "disks": vm["disks"], "source_storages": srcs, "suggested": sugg,
                        "local": {"name": g.get("name"), "status": g.get("status")} if g else None})
    return {
        "export": {"token_set": dr.has_token(), "generated": inv.get("generated") if inv else None,
                   "vms": len(inv["vms"]) if inv else 0},
        "peer": {"url": p["url"], "fingerprint": p["fingerprint"], "fetched": st.get("fetched"), "error": st.get("error", ""),
                 "generated": (st.get("inventory") or {}).get("generated")} if p else None,
        "vms": vms, "local_storages": sorted(local),
        "nodes": sorted(n["node"] for n in pcall(s, "GET", "/nodes") or [] if n.get("status") == "online"),
    }


@app.post("/api/dr/token")
def dr_token(s=Depends(admin)):
    try:
        collect_dr(force=True)
    except Exception:
        pass
    return {"token": dr.new_token()}


@app.post("/api/dr/peer/probe")
async def dr_peer_probe(request: Request, s=Depends(admin)):
    try:
        return {"fingerprint": dr.probe_peer(str((await request.json()).get("url", "")))}
    except Exception as e:
        err(400, f"could not read the certificate: {e}")


@app.post("/api/dr/peer")
async def dr_peer_save(request: Request, s=Depends(admin)):
    b = await request.json()
    tok = str(b.get("token", ""))
    if not re.match(r"^[A-Za-z0-9_-]{20,128}$", tok):
        err(400, "invalid token")
    try:
        dr.save_peer(str(b.get("url", "")), tok, str(b.get("fingerprint", "")))
    except ValueError as e:
        err(400, str(e))
    st = dr.pull_peer()
    if st.get("error"):
        err(502, f"the peer panel answered with an error: {st['error']}")
    return {"vms": len(st["inventory"]["vms"])}


@app.delete("/api/dr/peer")
def dr_peer_remove(s=Depends(admin)):
    dr.remove_peer()
    return {}


def dr_vm(vmid):
    inv = dr.peer_state().get("inventory") or {}
    for vm in inv.get("vms", []):
        if vm["vmid"] == vmid:
            return vm, inv
    err(404, f"VM {vmid} is not in the peer site inventory")


def dr_groups(s, vm, storage_map):
    """(local storage config, volume group) for each storage the VM uses."""
    out = []
    for src in sorted({d["storage"] for d in vm["disks"]}):
        if src not in storage_map:
            continue
        scfg = storage_cfg(s, storage_map[src])
        out.append((scfg, f"{scfg.get('sw-prefix', 'pve')}-vm-{vm['vmid']}"))
    if not out:
        err(400, "no disk of this VM is on a storage mapped to this site")
    return out


@app.post("/api/dr/recover")
async def dr_recover(request: Request, s=Depends(admin)):
    """Failover (or DR test) of a VM: releases the replicated copy on the array and recreates the VM here."""
    b = await request.json()
    vmid, test = to_int(b.get("vmid")), bool(b.get("test"))
    vm, inv = dr_vm(vmid)
    smap = {str(k): str(v) for k, v in (b.get("storage_map") or {}).items() if v}
    node = str(b.get("node", ""))
    if not NAME_RE.match(node):
        err(400, "invalid node")
    if guest_status(s, vmid):
        err(409, f"a VM {vmid} already exists on this site; remove it or finish the previous DR test")
    groups = dr_groups(s, vm, smap)
    steps = []
    for scfg, vg in groups:
        a = array_for(scfg)
        try:
            sysd = a.show("lssystem")
            if test and not sv8.caps8(sysd.get("code_level", ""))["recovery_test"]:
                err(400, f"{scfg['storage']}: a DR test without stopping replication requires firmware 8.6.2+")
            r = sv8.replication_detail(a, vg, sysd)
        except (ArrayError, subprocess.TimeoutExpired) as e:
            err(502, f"array {scfg['storage']}: {e}")
        if not r:
            err(409, f"{scfg['storage']}: volume group {vg} does not exist here or has no replication policy")
        if r["local_mode"] == "production":
            err(409, f"{scfg['storage']}: this array is already production for {vg}; nothing to recover")
        if r["local_mode"] == "independent" and not test:
            steps.append(f"{vg}: was already in independent mode")
            continue
        array_call(sv8.replication_action, a, vg, "test-start" if test else "failover")
        steps.append(f"{vg}: {'recovery test started' if test else 'independent mode (failover)'}")
    params, skipped = dr.recover_params(vm, smap, test)
    upid = pcall(s, "POST", f"/nodes/{node}/qemu", vmid=vmid, **params)
    res = run_task(s, node, upid)
    if b.get("start"):
        res["start"] = run_task(s, node, pcall(s, "POST", f"/nodes/{node}/qemu/{vmid}/status/start"))
    return {"steps": steps, "skipped": skipped, "task": res}


@app.post("/api/dr/test-stop")
async def dr_test_stop(request: Request, s=Depends(admin)):
    """Ends the DR test: returns the volumes to recovery mode and only then removes the test VM."""
    b = await request.json()
    vmid = to_int(b.get("vmid"))
    vm, inv = dr_vm(vmid)
    smap = {str(k): str(v) for k, v in (b.get("storage_map") or {}).items() if v}
    g = guest_status(s, vmid)
    if g and g.get("status") != "stopped":
        err(409, f"shut down test VM {vmid} before ending the test")
    if g and not str(g.get("name", "")).endswith("-drtest"):
        err(409, f"VM {vmid} on this site is not a DR test VM (name without -drtest); nothing was done")
    steps = []
    for scfg, vg in dr_groups(s, vm, smap):
        array_call(sv8.replication_action, array_for(scfg), vg, "test-stop")
        steps.append(f"{vg}: recovery test ended")
    if g:
        # with the test ended the volumes are a recovery copy again: the array (and the plugin)
        # refuse to delete them, so removing the VM here only removes the configuration
        st, log = pve.wait_task(s["pve"], g["node"], pcall(s, "DELETE", f"/nodes/{g['node']}/qemu/{vmid}"))
        drop_cache()
        if st != "OK" and not str(st).startswith("WARNINGS"):      # expected warning: disks not deleted
            err(500, (log[-1] if log else st) or "failed to remove the test VM")
        steps.append(f"test VM {vmid} removed (configuration)")
    return {"steps": steps}


# ---------------------------------------------------------------------------
# panel domain and HTTPS certificate
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Backups: running activities, mounted snapshots and schedules
# ---------------------------------------------------------------------------
def cluster_nodes(s):
    return [(n["name"], n.get("ip")) for n in pcall(s, "GET", "/cluster/status") or [] if n.get("type") == "node" and n.get("ip")]


def backup_nodes(s):
    """Backup state on each node (one SSH per node, in parallel)."""
    def one(n):
        name, ip = n
        try:
            rc, o, e = nodessh.run(ip, backup.NODE_SCRIPT, 40)
            return name, json.loads(o)
        except Exception as ex:
            return name, {"error": str(ex)[:200]}
    nodes = cluster_nodes(s)
    with ThreadPoolExecutor(max_workers=6) as ex:
        return dict(ex.map(one, nodes))


def backup_overview(s):
    nodes = backup_nodes(s)
    guests = {g["vmid"]: g for g in pcall(s, "GET", "/cluster/resources", type="vm") or []}
    gname = lambda v: (guests.get(v) or {}).get("name") or ""
    running, recent, scheds, mpaths, busy = [], [], {}, set(), set()
    for node, d in nodes.items():
        if d.get("error"):
            continue
        mpaths |= set(d["mpaths"])
        sched_running = any(x.get("running") for x in d["schedules"])
        for item in d["logs"]:
            j = backup.parse_log(item)
            j.update(node=node, name=gname(j["vmid"]))
            if j["running"]:
                busy.add(j["vmid"])
                running.append({"kind": "snapshot", "vmid": j["vmid"], "name": j["name"], "node": node, "start": j["id"],
                                "origin": "schedule" if sched_running else "manual", "progress": d["progress"].get(str(j["vmid"]))})
            else:
                recent.append(j)
        for c in d["schedules"]:
            scheds.setdefault(c["id"], dict(c, nodes=[]))["nodes"].append(node)
            if c.get("running"):
                scheds[c["id"]]["running"] = True
    try:
        for t in pcall(s, "GET", "/cluster/tasks") or []:
            if t.get("type") == "vzdump" and not t.get("endtime"):
                vm = to_int(t.get("id"))
                running.append({"kind": "normal", "vmid": vm or None, "name": gname(vm), "node": t.get("node"),
                                "start": t.get("starttime"), "user": t.get("user"), "origin": "proxmox"})
    except HTTPException:
        pass
    # backup snapshots still on the array (mounted while the backup reads; orphaned if the job died)
    snaps = {}
    for c in storwize_storages(s):
        a = array_snapshot(c)
        if not a.get("ok"):
            continue
        pfx = re.escape(c.get("sw-prefix", "pve"))
        mapped = {m.get("vdisk_name") for m in a["hostmaps"]}
        fc = {m.get("target_vdisk_name"): m for m in a["fcmaps"]}
        for v in a["vdisks"]:
            m = re.match(rf"^{pfx}-(vm-(\d+)-disk-\d+)_s_(cbt\d+)$", v["name"])
            if not m:
                continue
            vm, snap = int(m.group(2)), m.group(3)
            g = snaps.setdefault((c["storage"], vm, snap), {"storage": c["storage"], "vmid": vm, "name": gname(vm), "snap": snap,
                                                           "created": to_int(snap[3:]), "volumes": [], "in_use": vm in busy})
            g["volumes"].append({"volname": m.group(1), "vdisk": v["name"], "mapped": v["name"] in mapped,
                                 "on_node": (v.get("vdisk_UID") or "").lower() in mpaths,
                                 "fc_status": (fc.get(v["name"]) or {}).get("status", "")})
    pve_jobs = []
    try:
        for j in pcall(s, "GET", "/cluster/backup") or []:
            if to_int(j.get("enabled", 1)):
                pve_jobs.append({"id": j.get("id"), "all": bool(to_int(j.get("all"))), "vmids": [to_int(x) for x in str(j.get("vmid") or "").split(",") if x]})
    except HTTPException:
        pass
    return {"running": sorted(running, key=lambda r: r.get("start") or 0), "snapshots": sorted(snaps.values(), key=lambda x: -x["created"]),
            "schedules": sorted(scheds.values(), key=lambda c: c["name"].lower()),
            "recent": sorted(recent, key=lambda r: -r["id"])[:20], "pve_jobs": pve_jobs,
            "nodes": {n: {"error": d.get("error"), "installed": d.get("installed")} for n, d in nodes.items()},
            "vms": sorted(({"vmid": g["vmid"], "name": g.get("name"), "node": g.get("node")} for g in guests.values() if g.get("type") == "qemu" and not g.get("template")),
                          key=lambda g: g["vmid"])}


@app.get("/api/backup")
def backup_get(s=Depends(session)):
    return cached(f"backup:{s['pve']['user']}", 6, lambda: backup_overview(s))


def on_nodes(s, title, fn):
    """Runs fn(ip) on every node; returns [{node, ok, error}]."""
    out = []
    for name, ip in cluster_nodes(s):
        try:
            fn(ip)
            out.append({"node": name, "ok": True})
        except Exception as e:
            out.append({"node": name, "ok": False, "error": str(e)[:300]})
    if not any(r["ok"] for r in out):
        err(502, f"{title}: " + "; ".join(f"{r['node']}: {r.get('error')}" for r in out))
    drop_cache("backup:")
    return out


def node_sh(ip, cmd, timeout=60):
    rc, o, e = nodessh.run(ip, cmd, timeout)
    if rc != 0:
        raise NodeError((o + e).strip()[-400:] or f"rc={rc}")
    return o


@app.post("/api/backup/schedules")
async def backup_schedule_save(request: Request, s=Depends(admin)):
    try:
        c = backup.validate(await request.json(), {x["storage"] for x in pbs_storages(s)})
    except ValueError as e:
        err(400, str(e))
    svc, tim = backup.unit_files(c)
    u = f"ibm-storage-cbt-sched-{c['id']}"

    def apply(ip):
        node_sh(ip, f"test -x {CBT_DIR}/cbt_job.py || {{ echo 'snapshot backup not installed on this node - use Prepare node'; exit 3; }}; "
                    f"mkdir -p {backup.SCHED_DIR} && chmod 700 /etc/ibm-storage-cbt")
        nodessh.put(ip, f"{CBT_DIR}/cbt_sched.py", open(os.path.join(ASSETS, "cbt_sched.py")).read(), 0o755)
        nodessh.put(ip, f"{backup.SCHED_DIR}/{c['id']}.json", json.dumps(c), 0o600)
        nodessh.put(ip, f"/etc/systemd/system/{u}.service", svc)
        nodessh.put(ip, f"/etc/systemd/system/{u}.timer", tim)
        node_sh(ip, backup.install_cmd(c))
    return {"schedule": c, "nodes": on_nodes(s, "could not save the schedule", apply)}


@app.delete("/api/backup/schedules/{sid}")
def backup_schedule_delete(sid: str, s=Depends(admin)):
    if not backup.ID_RE.match(sid):
        err(400, "invalid identifier")
    return {"nodes": on_nodes(s, "could not remove", lambda ip: node_sh(ip, backup.remove_cmd(sid)))}


@app.post("/api/backup/schedules/{sid}/run")
def backup_schedule_run(sid: str, s=Depends(admin)):
    if not backup.ID_RE.match(sid):
        err(400, "invalid identifier")
    return {"nodes": on_nodes(s, "could not start", lambda ip: node_sh(
        ip, f"test -f {backup.SCHED_DIR}/{sid}.json && systemctl start --no-block ibm-storage-cbt-sched-{sid}.service"))}


@app.post("/api/backup/snapshots/cleanup")
async def backup_snapshot_cleanup(request: Request, s=Depends(admin)):
    """Unmaps and deletes a backup snapshot left behind (interrupted job)."""
    b = await request.json()
    storeid, vmid, snap = str(b.get("storage", "")), to_int(b.get("vmid")), str(b.get("snap", ""))
    if not backup.SNAP_RE.match(snap):
        err(400, "invalid snapshot")
    c = storage_cfg(s, storeid)
    ov = backup_overview(s)
    g = next((x for x in ov["snapshots"] if x["storage"] == storeid and x["vmid"] == vmid and x["snap"] == snap), None)
    if not g:
        err(404, "snapshot not found on the array")
    if g["in_use"]:
        err(409, f"a backup of VM {vmid} is in progress using this snapshot")
    nodes = cluster_nodes(s)
    guest = next((x for x in pcall(s, "GET", "/cluster/resources", type="vm") or [] if x["vmid"] == vmid), None)
    ip = next((i for n, i in nodes if guest and n == guest.get("node")), nodes[0][1] if nodes else None)
    if not ip:
        err(502, "no node available")
    log = []
    for v in g["volumes"]:
        for cmd in ("unmap", "delete"):
            rc, o, e = nodessh.run(ip, f"perl {CBT_DIR}/sw-snap.pl {cmd} {c['storage']} {v['volname']} {snap}", 180)
            log.append({"volume": v["volname"], "step": cmd, "rc": rc, "out": (o + e).strip()[-300:]})
    drop_cache()
    bad = [x for x in log if x["rc"] != 0 and x["step"] == "delete"]
    if bad:
        err(500, f"could not delete {bad[0]['volume']}: {bad[0]['out']}")
    return {"log": log}


def restart_soon(delay=2.0):
    """The certificate is read at startup: stop the process and systemd (Restart=always) brings it back up."""
    threading.Timer(delay, lambda: os.kill(os.getpid(), signal.SIGTERM)).start()


@app.get("/api/tls")
def tls_status(s=Depends(session)):
    return tlsconf.status()


def tls_domain(b, required=True):
    d = str(b.get("domain", "")).strip().lower().rstrip(".")
    if not d and not required:
        return ""
    if not tlsconf.DOMAIN_RE.match(d):
        err(400, "invalid domain (e.g. storage.example.com)")
    return d


@app.post("/api/tls/custom")
async def tls_custom(request: Request, s=Depends(admin)):
    b = await request.json()
    cert, key = str(b.get("cert", "")), str(b.get("key", ""))
    if len(cert) > 100_000 or len(key) > 20_000:
        err(400, "file too large")
    try:
        d = tlsconf.install_custom(tls_domain(b, required=False), cert, key)
    except tlsconf.TLSError as e:
        err(400, str(e))
    restart_soon()
    return {"cert": d, "restart": True}


@app.post("/api/tls/letsencrypt")
async def tls_letsencrypt(request: Request, s=Depends(admin)):
    b = await request.json()
    domain = tls_domain(b)
    email = str(b.get("email", "")).strip()
    method = "cloudflare" if b.get("method") == "cloudflare" else "http"
    token = str(b.get("cf_token", "")).strip()
    if email and not tlsconf.EMAIL_RE.match(email):
        err(400, "invalid e-mail")
    if token and not tlsconf.CF_TOKEN_RE.match(token):
        err(400, "invalid Cloudflare token")
    try:
        d = tlsconf.issue_letsencrypt(domain, email, method, token, bool(b.get("staging")))
    except tlsconf.TLSError as e:
        err(502, str(e))
    restart_soon()
    return {"cert": d, "restart": True}


@app.post("/api/tls/selfsigned")
async def tls_selfsigned(request: Request, s=Depends(admin)):
    b = await request.json()
    years = to_int(b.get("years")) or 10
    if years not in (1, 2, 5, 10):
        err(400, "invalid validity")
    try:
        d = tlsconf.issue_selfsigned(tls_domain(b), years)
    except tlsconf.TLSError as e:
        err(400, str(e))
    restart_soon()
    return {"cert": d, "restart": True}


@app.post("/api/tls/reset")
def tls_reset(s=Depends(admin)):
    tlsconf.reset()
    restart_soon()
    return {"restart": True}


def warmer():
    """Keeps each array's data ready while someone is using the panel (and every 5 min when idle),
    so the pages open without waiting for the array."""
    last = 0
    while True:
        try:
            active = any(time.time() - x.get("seen", 0) < 900 for x in list(SESSIONS.values()))
            if active or time.time() - last > 300:
                last = time.time()
                for k, meta in list(CACHE.items()):
                    if not k.startswith("meta:") or not isinstance(meta, dict) or not store.has_array_key(k[5:]):
                        continue
                    hit = CACHE.get(f"array:{k[5:]}")
                    if hit and time.time() - hit[0] < 20:
                        continue
                    scfg = {"storage": k[5:], "sw-address": meta.get("address"), "sw-user": meta.get("user"), "sw-port": meta.get("port", 22)}
                    array_snapshot(scfg, force=True)
                    if active:                       # keep the array FC port data ready as well
                        try:
                            array_fc(scfg)
                        except Exception:
                            pass
        except Exception:
            pass
        time.sleep(20)


threading.Thread(target=warmer, daemon=True).start()
threading.Thread(target=collector, daemon=True).start()


# ---------------------------------------------------------------------------
# static UI
# ---------------------------------------------------------------------------

CSP = ("default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "font-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    static = request.url.path.startswith("/static/")
    if static:
        # small files: always the full response (no partial/resumed download by the browser)
        request.scope["headers"] = [h for h in request.scope["headers"] if h[0] not in (b"range", b"if-range")]
    resp = await call_next(request)
    if static:
        resp.headers["Accept-Ranges"] = "none"
        # with ?v= the content at that address never changes; without ?v= the browser revalidates on each use
        resp.headers["Cache-Control"] = "public, max-age=31536000, immutable" if request.url.query.startswith("v=") else "no-cache"
    h = resp.headers
    h["Content-Security-Policy"] = CSP
    h["X-Content-Type-Options"] = "nosniff"
    h["X-Frame-Options"] = "DENY"
    h["Referrer-Policy"] = "no-referrer"
    h["Strict-Transport-Security"] = "max-age=31536000"
    h["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if request.url.path.startswith("/api/"):
        h["Cache-Control"] = "no-store"
    return resp


app.mount("/static", StaticFiles(directory=STATIC), name="static")


_VERSIONS = {}


def static_version(name):
    """Short hash of the file content. The file date is not enough: every container made from the
    same image has the same dates, and a browser that saw another panel at the same address would
    take its copy as current."""
    p = os.path.join(STATIC, name)
    try:
        st = os.stat(p)
    except OSError:
        return "0"
    key = (name, st.st_mtime_ns, st.st_size)
    if key not in _VERSIONS:
        with open(p, "rb") as f:
            _VERSIONS[key] = hashlib.sha256(f.read()).hexdigest()[:12]
    return _VERSIONS[key]


def index_html():
    """index.html with each file's version in the address (?v=...): after every release the browser
    fetches the new files in full, without trying to reuse or resume old copies."""
    def ver(m):
        return f'{m.group(1)}="/static/{m.group(2)}?v={static_version(m.group(2))}"'
    html = open(os.path.join(STATIC, "index.html"), encoding="utf-8").read()
    return re.sub(r'(href|src)="/static/([^"?]+)"', ver, html)


@app.get("/")
def index():
    return Response(index_html(), media_type="text/html; charset=utf-8", headers={"Cache-Control": "no-cache"})
