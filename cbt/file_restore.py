#!/usr/bin/python3
"""File restore from a snapshot of the array, through the Proxmox restore VM.

The snapshot (any snapshot of the guest's volume group, including Safeguarded ones) is never
touched: the array gives a thinclone of it, mapped to this node only. The thinclone is opened by
the same small VM Proxmox uses for single file restore from PBS (package
proxmox-backup-restore-image): its own kernel mounts the guest file systems read-only, so a
damaged or hostile file system is never parsed by the node's kernel. Read-only on three levels:
the block devices are set read-only on the node (blockdev --setro), QEMU opens them read-only,
and the restore VM mounts without replaying journals (ext4 noload, xfs norecovery).

The panel talks to the restore VM through this script (vsock, port 807, ticket in the initramfs):
  file_restore.py start   <storeid> <vmid> <snap>    start a session, prints JSON
  file_restore.py list    <id> <base64 path>         directory listing (JSON of the restore VM)
  file_restore.py extract <id> <base64 path> <fmt>   file or folder on stdout (plain, zip, tar.zst)
  file_restore.py status  <id>                       restore VM uptime and idle timeout
  file_restore.py stop    <id>                       stop the VM; the thinclone is removed
                                                     (after the downloads in progress end)
  file_restore.py ls                                 sessions on this node

The restore VM stops by itself after 10 minutes without requests. A watcher process removes the
thinclone as soon as the VM is gone, and stops the VM after MAX_AGE in any case.
"""
import base64
import fcntl
import http.client
import json
import os
import re
import secrets
import signal
import socket
import subprocess
import sys
import time
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
RUN = "/run/ibm-storage-files"
IMG = "/usr/lib/x86_64-linux-gnu/proxmox-backup/file-restore"
INITRD_CACHE = "/var/cache/proxmox-backup/file-restore-initramfs.img"
PORT = 807
MAX_AGE = 4 * 3600
ID_RE = re.compile(r"^[0-9a-f]{12}$")
FORMATS = {"plain": "format=plain", "zip": "format=zip", "tar.zst": "format=tar&zstd=1"}


class Fail(Exception):
    pass


def sess_path(sid):
    if not ID_RE.match(sid or ""):
        raise Fail("invalid session id")
    return os.path.join(RUN, sid + ".json")


def load(sid):
    try:
        return json.load(open(sess_path(sid)))
    except OSError:
        raise Fail("the file restore session ended (idle for 10 minutes, or stopped)")


def save(s):
    p = sess_path(s["id"])
    with open(p + ".tmp", "w") as f:
        json.dump(s, f)
    os.chmod(p + ".tmp", 0o600)
    os.replace(p + ".tmp", p)


def alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:   # a recycled pid is not our VM
        return b"pbs-restore-vm" in open(f"/proc/{pid}/cmdline", "rb").read() or b"ibm-file-restore" in open(f"/proc/{pid}/cmdline", "rb").read()
    except OSError:
        return False


# ---- initramfs with the ticket -----------------------------------------------------------
def cpio_entry(name, data, mode=0o100644):
    """One entry of a cpio "newc" archive (what the kernel unpacks into the initramfs)."""
    n = name.encode() + b"\0"
    hdr = b"070701" + b"".join(b"%08X" % v for v in (
        0, mode, 0, 0, 1, int(time.time()), len(data), 0, 0, 0, 0, len(n), 0))
    out = hdr + n
    out += b"\0" * (-len(out) % 4) + data
    return out + b"\0" * (-len(out) % 4)


def cpio(entries):
    a = b"".join(cpio_entry(*e) for e in entries) + cpio_entry("TRAILER!!!", b"", 0)
    return a + b"\0" * (-len(a) % 512)


def initramfs(path, ticket):
    """Base image + restore daemon + ticket. The kernel unpacks concatenated archives in order."""
    with open(path, "wb") as out:
        os.fchmod(out.fileno(), 0o600)
        if os.path.exists(INITRD_CACHE):          # built by proxmox-file-restore: base + daemon
            out.write(open(INITRD_CACHE, "rb").read())
        else:
            out.write(open(os.path.join(IMG, "initramfs.img"), "rb").read())
            out.write(cpio([("proxmox-restore-daemon", open(os.path.join(IMG, "proxmox-restore-daemon"), "rb").read(), 0o100755)]))
        out.write(cpio([("ticket", ticket.encode())]))


# ---- talking to the restore VM -----------------------------------------------------------
class VsockHTTP(http.client.HTTPConnection):
    def __init__(self, cid, timeout=60):
        super().__init__("localhost", timeout=timeout)
        self.cid = cid

    def connect(self):
        self.sock = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect((self.cid, PORT))


def call(s, path, timeout=60):
    c = VsockHTTP(s["cid"], timeout)
    c.request("GET", path, headers={"Authorization": s["ticket"]})
    r = c.getresponse()
    if r.status != 200:
        msg = r.read(4000).decode(errors="replace").strip()
        try:
            msg = json.loads(msg).get("message") or msg
        except ValueError:
            pass
        raise Fail(f"restore VM: {msg or r.reason}")
    return r


def q(p):
    return urllib.parse.quote(p, safe="")


# ---- guest disks -------------------------------------------------------------------------
def disk_keys(vmid, storeid):
    """volname -> disk key (scsi0, virtio1 ...) from the VM config, current section first."""
    import glob
    keys = {}
    try:      # the guest may run on another node of the cluster
        text = open((glob.glob(f"/etc/pve/nodes/*/qemu-server/{vmid}.conf") or [""])[0]).read()
    except OSError:
        return keys
    for m in re.finditer(r"^((?:scsi|virtio|sata|ide|efidisk|tpmstate)\d+):\s*([^,\s]+)", text, re.M):
        st, _, vol = m.group(2).partition(":")
        if st == storeid and vol and vol not in keys:
            keys[vol] = m.group(1)
    return keys


def storage_prefix(storeid):
    try:
        text = open("/etc/pve/storage.cfg").read()
    except OSError:
        return "pve"
    m = re.search(rf"^\w+:\s*{re.escape(storeid)}\s*\n((?:[ \t]+.*\n?)*)", text, re.M)
    p = re.search(r"^\s+sw-prefix\s+(\S+)", m.group(1), re.M) if m else None
    return p.group(1) if p else "pve"


def readonly(dev):
    """blockdev --setro on the multipath device and on every path below it."""
    real = os.path.realpath(dev)
    devs = [real]
    name = os.path.basename(real)
    try:
        devs += ["/dev/" + x for x in os.listdir(f"/sys/block/{name}/slaves")]
    except OSError:
        pass
    for d in devs:
        subprocess.run(["blockdev", "--setro", d], check=True)
    return real


# ---- commands ----------------------------------------------------------------------------
def start(storeid, vmid, snap):
    if not re.match(r"^\d+$", vmid):
        raise Fail("invalid VM id")
    if not os.path.exists(os.path.join(IMG, "bzImage")):
        raise Fail("the Proxmox restore VM image is not installed on this node (package proxmox-backup-restore-image)")
    os.makedirs(RUN, mode=0o700, exist_ok=True)
    sid = secrets.token_hex(6)
    s = {"id": sid, "storage": storeid, "vmid": int(vmid), "snap": snap, "started": int(time.time()),
         "group": None, "pid": None, "disks": []}
    save(s)
    try:
        r = subprocess.run(["perl", os.path.join(HERE, "sw-snap.pl"), "fsmap", storeid, vmid, snap],
                           capture_output=True, text=True, timeout=600)
        g = re.search(r"^group (\S+)$", r.stdout, re.M)
        if g:
            s["group"] = g.group(1)
            save(s)
        if r.returncode != 0 or not g:
            raise Fail((r.stderr or r.stdout).strip()[-400:] or "could not map the snapshot")
        prefix = storage_prefix(storeid) + "-"
        keys = disk_keys(vmid, storeid)
        used = set()
        for line in r.stdout.splitlines()[1:]:
            src, dev = line.split()
            vol = src[len(prefix):] if src.startswith(prefix) else src
            serial = ("drive-" + keys[vol]) if vol in keys else vol
            serial = re.sub(r"[^A-Za-z0-9_-]", "-", serial)[:20]
            while serial in used:
                serial = serial[:18] + secrets.token_hex(1)
            used.add(serial)
            s["disks"].append({"volume": vol, "serial": serial, "dev": readonly(dev), "key": keys.get(vol, "")})
        s["disks"].sort(key=lambda d: (d["key"] == "", d["key"], d["volume"]))
        save(s)
        boot(s)
    except BaseException:
        cleanup(s)
        raise
    subprocess.Popen([sys.executable, os.path.abspath(__file__), "watch", sid], stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)
    return public(s)


def boot(s):
    s["ticket"] = secrets.token_urlsafe(24)
    d = os.path.join(RUN, s["id"])
    os.makedirs(d, mode=0o700, exist_ok=True)
    initramfs(os.path.join(d, "initramfs.img"), s["ticket"])
    args = ["qemu-system-x86_64", "-name", f"ibm-file-restore-{s['id']}",
            "-chardev", f"file,id=log,path={d}/console.log", "-serial", "chardev:log", "-vnc", "none",
            "-enable-kvm", "-kernel", os.path.join(IMG, "bzImage"), "-initrd", os.path.join(d, "initramfs.img"),
            "-append", "quiet panic=1 zfs.zfs_arc_min=33554432 zfs.zfs_arc_max=67108864",
            "-daemonize", "-pidfile", f"{d}/qemu.pid", "-m", "512M",
            "-device", "pci-bridge,id=bridge2,chassis_nr=2"]
    for i, k in enumerate(s["disks"]):
        args += ["-blockdev", f"driver=host_device,filename={k['dev']},read-only=on,node-name=drive{i}",
                 "-device", f"virtio-blk-pci,drive=drive{i},serial={k['serial']},bus=bridge2"]
    for _ in range(10):
        cid = 100000 + secrets.randbelow(1 << 30)
        r = subprocess.run(args + ["-device", f"vhost-vsock-pci,guest-cid={cid},disable-legacy=on"],
                           capture_output=True, text=True, timeout=60)
        if r.returncode == 0:
            break
        if "cid" not in (r.stderr or "").lower():
            raise Fail(f"could not start the restore VM: {r.stderr.strip()[-300:]}")
    else:
        raise Fail("no free vsock id for the restore VM")
    s["cid"], s["pid"] = cid, int(open(f"{d}/qemu.pid").read())
    save(s)
    t = time.time()
    while time.time() - t < 120:
        if not alive(s["pid"]):
            raise Fail("the restore VM stopped while starting: " + tail(f"{d}/console.log"))
        try:
            call(s, "/api2/json/status", 5).read()
            return
        except (OSError, Fail, http.client.HTTPException):
            time.sleep(1)
    raise Fail("the restore VM did not answer in 120 s")


def tail(p, n=300):
    try:
        return open(p, errors="replace").read()[-n:].strip()
    except OSError:
        return ""


def public(s):
    return {"id": s["id"], "storage": s["storage"], "vmid": s["vmid"], "snap": s["snap"], "started": s["started"],
            "disks": [{"volume": d["volume"], "serial": d["serial"], "key": d["key"]} for d in s["disks"]]}


def cleanup(s):
    """Stop the VM and remove the thinclone. One at a time per session (stop and the watcher)."""
    os.makedirs(RUN, mode=0o700, exist_ok=True)
    with open(os.path.join(RUN, s["id"] + ".lock"), "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        if not os.path.exists(sess_path(s["id"])):
            return True
        s = json.load(open(sess_path(s["id"])))
        ok = _cleanup(s)
    if ok:
        try:
            os.unlink(os.path.join(RUN, s["id"] + ".lock"))
        except OSError:
            pass
    return ok


def _cleanup(s):
    if s.get("pid") and alive(s["pid"]):
        os.kill(s["pid"], signal.SIGTERM)
        for _ in range(20):
            if not alive(s["pid"]):
                break
            time.sleep(0.5)
        else:
            os.kill(s["pid"], signal.SIGKILL)
    if s.get("group"):
        r = subprocess.run(["perl", os.path.join(HERE, "sw-snap.pl"), "fsclean", s["storage"], s["group"]],
                           capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            print((r.stderr or r.stdout).strip()[-400:], file=sys.stderr)
            return False      # keep the session file: the next 'ls' retries
    d = os.path.join(RUN, s["id"])
    for f in ("initramfs.img", "qemu.pid", "console.log"):
        try:
            os.unlink(os.path.join(d, f))
        except OSError:
            pass
    for p in (d, sess_path(s["id"]), os.path.join(RUN, s["id"] + ".dl")):
        try:
            os.rmdir(p) if p == d else os.unlink(p)
        except OSError:
            pass
    return True


def dl_lock(sid, mode):
    """Downloads hold a shared lock on <id>.dl; stop checks it so a download in progress is not cut
    when the panel window closes."""
    f = open(os.path.join(RUN, sid + ".dl"), "a")
    try:
        fcntl.flock(f, mode)
    except BlockingIOError:
        f.close()
        return None
    return f


def downloading(sid):
    f = dl_lock(sid, fcntl.LOCK_EX | fcntl.LOCK_NB)
    if f is None:
        return True
    f.close()
    return False


def stop(sid):
    try:
        s = load(sid)
    except Fail:
        return {"stopped": True}
    if downloading(sid):          # the watcher stops it when the last download ends
        s["closing"] = True
        save(s)
        return {"stopped": False, "after_downloads": True}
    return {"stopped": cleanup(s)}


def watch(sid):
    s = load(sid)
    while True:
        time.sleep(5)
        try:
            s = load(sid)
        except Fail:
            return
        if not s.get("pid") or not alive(s["pid"]):
            break
        if time.time() - s["started"] > MAX_AGE:
            break
        if s.get("closing") and not downloading(sid):
            break
    for _ in range(5):
        if cleanup(s):
            return
        time.sleep(60)


def sessions():
    out = []
    try:
        names = os.listdir(RUN)
    except OSError:
        return out
    for n in names:
        if not n.endswith(".json"):
            continue
        try:
            s = load(n[:-5])
        except Fail:
            continue
        if s.get("pid") and alive(s["pid"]):
            out.append(public(s))
        elif time.time() - s["started"] > 900:     # watcher gone (node rebooted is handled by /run)
            cleanup(s)
    return out


def b64path(p):
    try:
        raw = base64.b64decode(p, validate=True).decode()
    except ValueError:
        raise Fail("invalid path")
    if not raw.startswith("/") or "\0" in raw:
        raise Fail("invalid path")
    return raw


def main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "start" and len(argv) == 5:
        print(json.dumps(start(*argv[2:5])))
    elif cmd == "list" and len(argv) == 4:
        s = load(argv[2])
        raw = b64path(argv[3])
        data = json.loads(call(s, f"/api2/json/list?path={q(base64.b64encode(raw.encode()).decode())}").read())
        print(json.dumps(data.get("data", [])))
    elif cmd == "extract" and len(argv) == 5:
        s = load(argv[2])
        raw = b64path(argv[3])
        if argv[4] not in FORMATS:
            raise Fail("format: plain, zip or tar.zst")
        lk = dl_lock(s["id"], fcntl.LOCK_SH)
        r = call(s, f"/api2/json/extract?path={q(base64.b64encode(raw.encode()).decode())}&{FORMATS[argv[4]]}", 600)
        out = sys.stdout.buffer
        while True:
            b = r.read(1 << 20)
            if not b:
                break
            out.write(b)
        out.flush()
        lk.close()
    elif cmd == "status" and len(argv) == 3:
        s = load(argv[2])
        st = json.loads(call(s, "/api2/json/status", 10).read()).get("data", {})
        print(json.dumps(dict(public(s), **st)))
    elif cmd == "stop" and len(argv) == 3:
        print(json.dumps(stop(argv[2])))
    elif cmd == "ls":
        print(json.dumps(sessions()))
    elif cmd == "watch" and len(argv) == 3:
        watch(argv[2])
    else:
        print(__doc__, file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    try:
        main(sys.argv)
    except Fail as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
