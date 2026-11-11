"""IBM Storage Virtualize CLI over SSH (same command layer as the PVE plugin)."""
import os
import re
import subprocess

ARG_RE = re.compile(r"^[A-Za-z0-9_.:=*%/-]+$")
NOTFOUND_RE = re.compile(r"CMMVC5753E|CMMVC5804E|does not exist", re.I)


class ArrayError(Exception):
    pass


MUX_DIR = None


def enable_mux(path):
    """Turns on SSH connection reuse (ControlMaster), using `path` for the sockets."""
    global MUX_DIR
    try:
        os.makedirs(path, mode=0o700, exist_ok=True)
        MUX_DIR = path
    except OSError:
        MUX_DIR = None


def _q(v):
    v = str(v)
    if not ARG_RE.match(v):
        raise ArrayError(f"argument refused: {v!r}")
    return f"'{v}'"


def parse_table(text):
    lines = [l for l in text.splitlines() if l]
    if not lines:
        return []
    hdr = lines[0].split(":")
    rows = []
    for l in lines[1:]:
        v = l.split(":")
        if len(v) > len(hdr):
            v = v[:len(hdr) - 1] + [":".join(v[len(hdr) - 1:])]
        rows.append(dict(zip(hdr, v)))
    return rows


def parse_detail(text):
    d = {}
    for l in text.splitlines():
        if ":" in l:
            k, v = l.split(":", 1)
            d.setdefault(k, v)
    return d


class Array:
    def __init__(self, address, user, keyfile, known_hosts, port=22):
        self.address, self.user, self.keyfile = address, user, keyfile
        self.known_hosts, self.port = known_hosts, port

    def _ssh(self, remote, timeout=60):
        cmd = ["ssh", "-i", self.keyfile, "-p", str(self.port),
               "-o", f"UserKnownHostsFile={self.known_hosts}",
               "-o", "StrictHostKeyChecking=yes", "-o", "BatchMode=yes",
               "-o", "ConnectTimeout=10", "-o", "PubkeyAcceptedAlgorithms=+ssh-rsa",
               "-o", "LogLevel=ERROR"]
        if MUX_DIR:          # one SSH connection per array, reused: no new handshake on every command
            cmd += ["-o", "ControlMaster=auto", "-o", f"ControlPath={MUX_DIR}/%C", "-o", "ControlPersist=120"]
        cmd += ["--", f"{self.user}@{self.address}", remote]
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if p.returncode != 0 or re.search(r"CMMVC\d+E", p.stderr):
            raise ArrayError((p.stderr or p.stdout).strip() or f"ssh rc={p.returncode}")
        return p.stdout

    def fetch(self, remote, local, timeout=120):
        """Copies a file from the array (e.g. /dumps/iostats/...) with scp in the legacy protocol."""
        if not re.match(r"^/dumps/[A-Za-z0-9_./-]+$", remote):
            raise ArrayError("invalid remote path")
        cmd = ["scp", "-O", "-q", "-i", self.keyfile, "-P", str(self.port),
               "-o", f"UserKnownHostsFile={self.known_hosts}", "-o", "StrictHostKeyChecking=yes",
               "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "PubkeyAcceptedAlgorithms=+ssh-rsa",
               f"{self.user}@{self.address}:{remote}", local]
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if p.returncode != 0:
            # 9.1+: native SCP was discontinued (the array serves scp over SFTP); try without -O
            p = subprocess.run([c for c in cmd if c != "-O"], capture_output=True, text=True, timeout=timeout)
        if p.returncode != 0:
            raise ArrayError((p.stderr or p.stdout).strip() or f"scp rc={p.returncode}")

    def run(self, cmd, opts=None, obj=None, detail=False):
        if not ARG_RE.match(cmd):
            raise ArrayError("invalid command")
        ls = cmd.startswith("ls")
        words = ["svcinfo" if ls else "svctask", cmd]
        if ls:
            words += ["-delim", "':'"]
        for k, v in (opts or []):
            words.append("-" + _q(k).strip("'"))
            if v is not None:
                words.append(_q(v))
        if obj is not None:
            words.append(_q(obj))
        out = self._ssh(" ".join(words))
        if ls:
            return parse_detail(out) if detail else parse_table(out)
        m = re.search(r"id \[(\d+)\]", out)
        return {"message": out.strip(), "id": m.group(1) if m else None}

    def raw(self, cmd, obj=None):
        """Raw 'key:value' lines (for detail views with repeated keys, e.g. lshost WWPN)."""
        if not ARG_RE.match(cmd):
            raise ArrayError("invalid command")
        words = ["svcinfo", cmd, "-delim", "':'"] + ([_q(obj)] if obj is not None else [])
        return [tuple(l.split(":", 1)) for l in self._ssh(" ".join(words)).splitlines() if ":" in l]

    def ls(self, cmd, opts=None, obj=None):
        return self.run(cmd, opts, obj)

    def show(self, cmd, obj=None, opts=None):
        try:
            return self.run(cmd, opts, obj, detail=True)
        except ArrayError as e:
            if NOTFOUND_RE.search(str(e)):
                return None
            raise


def scan_host_keys(address, port=22):
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$", address):
        raise ArrayError("invalid address")
    p = subprocess.run(["ssh-keyscan", "-T", "10", "-p", str(int(port)), "--", address],
                       capture_output=True, text=True, timeout=30)
    lines = [l for l in p.stdout.splitlines() if l and not l.startswith("#")]
    if not lines:
        raise ArrayError(f"could not read the SSH key of {address}")
    fps = []
    for l in lines:
        q = subprocess.run(["ssh-keygen", "-lf", "-"], input=l, capture_output=True, text=True)
        fps.append(q.stdout.strip())
    return lines, fps


def code_ge(code_level, want):
    a = [int(x) for x in re.findall(r"\d+", code_level or "0")]
    for i, b in enumerate(int(x) for x in want.split(".")):
        x = a[i] if i < len(a) else 0
        if x != b:
            return x > b
    return True


def capabilities(code_level):
    return {
        "flashcopy": True,
        "child_pool": code_ge(code_level, "7.4.0"),
        "host_cluster": code_ge(code_level, "7.7.1"),
        "rest_api": code_ge(code_level, "8.1.3"),
        "volume_group": code_ge(code_level, "8.3.0"),
        "vg_snapshot": code_ge(code_level, "8.5.1"),
        "partition": code_ge(code_level, "8.6.1"),
        "safeguarded": code_ge(code_level, "8.5.2"),
        "pbr": code_ge(code_level, "8.5.2"),
        "recovery_test": code_ge(code_level, "8.6.2"),
    }
