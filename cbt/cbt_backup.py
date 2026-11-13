#!/usr/bin/python3
"""VM backup from an IBM Storage Virtualize snapshot + QEMU CBT, written to PBS
in the same format as the Proxmox VM backup (restore from the GUI / qmrestore).

  cbt_backup.py <vmid> <pbs-storage> [--full]

Cycle, without pausing the VM:
  1. prepare the FlashCopy (consistency group + prestart/flush); not needed with volume groups
  2. new bitmap  ->  [fs-freeze]  ->  trigger the FlashCopy, or addsnapshot of the guest volume
     group (sw-snapmode=volumegroup, 8.5+/9.x)  ->  [fs-thaw]  ->  disable the old bitmap
  3. map the snapshots on the proxy (this node; with volume groups, a thinclone of the snapshot
     mapped to this node only) and read:
       full:        every 4 MiB chunk (zeroed chunks are not sent)
       incremental: only the chunks the old bitmap marked
  4. send to PBS with libproxmox_backup_qemu (incremental reuses the previous index)
  5. delete the array snapshot and the old bitmap; save the state

The bitmap state lives in /etc/pve (shared by the cluster): after a live migration the next
backup runs on the other node and stays incremental (QEMU carries the bitmap with the VM).
Upload speed follows the Proxmox backup bandwidth limit (vzdump.conf, datacenter, storage) or
--bwlimit <KiB/s>: on a 1 GbE network a backup at full speed competes with corosync.
"""
import concurrent.futures as cf
import atexit
import re
import threading
import ctypes
import json
import os
import socket
import subprocess
import sys
import time

import nbd

HERE = os.path.dirname(os.path.abspath(__file__))
HELPER = os.path.join(HERE, "sw-snap.pl")
STATE_DIR = "/var/lib/ibm-storage-cbt"               # before 0.3.4: state per node
SHARED_DIR = "/etc/pve/priv/ibm-storage-cbt"          # bitmap + PBS library state, seen by every node
CHUNK = 4 * 1024 * 1024
WORKERS = 4
ZERO = bytes(CHUNK)


# ------------------------------------------------------------------ QMP / QGA
def qmp(vmid, cmd, **args):
    """One connection per command: the QMP socket takes a single client and Proxmox needs it."""
    s = socket.socket(socket.AF_UNIX)
    s.settimeout(60)
    s.connect(f"/var/run/qemu-server/{vmid}.qmp")
    f = s.makefile("rw")
    try:
        json.loads(f.readline())
        for c, a in (("qmp_capabilities", None), (cmd, args)):
            f.write(json.dumps({"execute": c, "arguments": a} if a else {"execute": c}) + "\n")
            f.flush()
            while True:
                m = json.loads(f.readline())
                if "event" in m:
                    continue
                if "error" in m:
                    raise RuntimeError(f"QMP {c}: {m['error'].get('desc')}")
                break
        return m.get("return")
    finally:
        s.close()


def agent(vmid, cmd):
    p = subprocess.run(["qm", "guest", "cmd", str(vmid), cmd], capture_output=True, text=True, timeout=60)
    return p.returncode == 0


def sh(*a):
    p = subprocess.run(a, capture_output=True, text=True)
    if p.returncode:
        raise RuntimeError(f"{' '.join(map(str, a))}: {(p.stderr or p.stdout).strip()}")
    return p.stdout.strip()


# ------------------------------------------------------------------ libproxmox_backup_qemu
lib = ctypes.CDLL("libproxmox_backup_qemu.so.0")
E = ctypes.POINTER(ctypes.c_char_p)
lib.proxmox_backup_new_ns.restype = ctypes.c_void_p
lib.proxmox_backup_new_ns.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint64, ctypes.c_uint64,
                                      ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p,
                                      ctypes.c_bool, ctypes.c_bool, ctypes.c_char_p, E]
lib.proxmox_backup_connect.argtypes = [ctypes.c_void_p, E]
lib.proxmox_backup_check_incremental.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint64]
lib.proxmox_backup_register_image.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint64, ctypes.c_bool, E]
lib.proxmox_backup_write_data.argtypes = [ctypes.c_void_p, ctypes.c_uint8, ctypes.c_char_p, ctypes.c_uint64, ctypes.c_uint64, E]
lib.proxmox_backup_close_image.argtypes = [ctypes.c_void_p, ctypes.c_uint8, E]
lib.proxmox_backup_add_config.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint64, E]
lib.proxmox_backup_finish.argtypes = [ctypes.c_void_p, E]
lib.proxmox_backup_abort.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
lib.proxmox_backup_disconnect.argtypes = [ctypes.c_void_p]
lib.proxmox_export_state.restype = ctypes.POINTER(ctypes.c_uint8)
lib.proxmox_export_state.argtypes = [ctypes.POINTER(ctypes.c_size_t)]
lib.proxmox_import_state.argtypes = [ctypes.POINTER(ctypes.c_uint8), ctypes.c_size_t]
lib.proxmox_free_state_buf.argtypes = [ctypes.POINTER(ctypes.c_uint8)]


def chk(rc, err, what):
    if rc < 0:
        msg = err.value.decode() if err.value else "error"
        raise RuntimeError(f"PBS {what}: {msg}")
    return rc


def pbs_conf(storeid):
    cfg, cur = {}, None
    for l in open("/etc/pve/storage.cfg"):
        if l.startswith(("pbs:", "storwize:", "dir:", "lvmthin:", "lvm:", "nfs:", "cifs:", "zfspool:", "rbd:", "cephfs:", "iscsi:", "btrfs:", "esxi:")):
            t, _, n = l.strip().partition(": ")
            cur = n if t == "pbs" else None
            if cur:
                cfg[cur] = {}
        elif cur and l.strip():
            k, _, v = l.strip().partition(" ")
            cfg[cur][k] = v
    c = cfg[storeid]
    c["password"] = open(f"/etc/pve/priv/storage/{storeid}.pw").read().strip()
    c["repo"] = f"{c['username']}@{c['server']}:{c['datastore']}"
    return c


# ------------------------------------------------------------------ disks / bitmap / NBD
def guest_disks(vmid, array_store):
    nodes = qmp(vmid, "query-named-block-nodes")
    out = {}
    for line in sh("qm", "config", str(vmid), "--current").splitlines():
        key, _, val = line.partition(": ")
        if not key[:4] in ("scsi", "virt", "sata") or not val.startswith(array_store + ":") or "media=cdrom" in val:
            continue
        volname = val.split(",")[0].split(":", 1)[1]
        path = sh("pvesm", "path", f"{array_store}:{volname}")
        raw = [n for n in nodes if n["drv"] == "raw" and path in n.get("file", "")]
        bms = {b["name"] for b in raw[0].get("dirty-bitmaps", [])} if raw else set()
        out[key] = {"volname": volname, "node": raw[0]["node-name"], "size": int(sh("blockdev", "--getsize64", path)),
                    "bitmaps": bms}
    return out


def dirty_chunks(vmid, node, bitmap, name, sock, size):
    """Indexes of the 4 MiB chunks touched by the bitmap (via NBD qemu:dirty-bitmap)."""
    qmp(vmid, "block-export-add", type="nbd", id=f"cbt-{name}", **{"node-name": node}, name=name,
        writable=False, bitmaps=[bitmap])
    try:
        h = nbd.NBD()
        h.set_export_name(name)
        ctx = f"qemu:dirty-bitmap:{bitmap}"
        h.add_meta_context(ctx)
        h.connect_unix(sock)
        chunks, pos = set(), 0

        def cb(mc, offset, entries, err):
            nonlocal pos
            if mc != ctx:
                return 0
            o = offset
            for i in range(0, len(entries), 2):
                ln, fl = entries[i], entries[i + 1]
                if fl & 1:
                    chunks.update(range(o // CHUNK, (o + ln - 1) // CHUNK + 1))
                o += ln
            pos = o
            return 0

        while pos < size:
            h.block_status(min(size - pos, 1 << 30), pos, cb)
        h.shutdown()
    finally:
        qmp(vmid, "block-export-del", id=f"cbt-{name}")
    return sorted(chunks)


def storage_section(storeid):
    cur, out = None, {}
    for l in open("/etc/pve/storage.cfg"):
        if l and not l[0].isspace() and ":" in l:
            t, _, n = l.strip().partition(": ")
            cur = n.strip() if n.strip() == storeid else None
            if cur:
                out["type"] = t
        elif cur and l.strip():
            k, _, v = l.strip().partition(" ")
            out[k] = v.strip()
    return out


def kib_limit(text, op="backup"):
    """'backup=1000,default=2000' or a plain number -> KiB/s for the operation, None if unset."""
    if not text:
        return None
    text = text.strip()
    if text.isdigit():
        return int(text) or None
    vals = dict(p.split("=", 1) for p in text.split(",") if "=" in p)
    for k in (op, "default"):
        if vals.get(k, "").isdigit() and int(vals[k]) > 0:
            return int(vals[k])
    return None


def bandwidth_limit(pbs_store):
    """Lowest backup limit set in Proxmox (KiB/s), like vzdump does; --bwlimit overrides."""
    for i, a in enumerate(sys.argv):
        if a == "--bwlimit" and i + 1 < len(sys.argv):
            return int(sys.argv[i + 1]) or None
    lims = []
    try:
        for l in open("/etc/vzdump.conf"):
            m = re.match(r"^\s*bwlimit:\s*(\d+)", l)
            if m and int(m.group(1)):
                lims.append(int(m.group(1)))
    except OSError:
        pass
    try:
        for l in open("/etc/pve/datacenter.cfg"):
            if l.startswith("bwlimit:"):
                v = kib_limit(l.split(":", 1)[1])
                if v:
                    lims.append(v)
    except OSError:
        pass
    v = kib_limit(storage_section(pbs_store).get("bwlimit"))
    if v:
        lims.append(v)
    return min(lims) if lims else None


class Throttle:
    """Token bucket shared by the upload threads (bytes per second)."""
    def __init__(self, kib_s):
        self.rate = kib_s * 1024 if kib_s else None
        self.lock = threading.Lock()
        self.t = time.monotonic()
        self.allow = 0.0

    def take(self, n):
        if not self.rate:
            return
        with self.lock:
            now = time.monotonic()
            self.allow = min(self.rate, self.allow + (now - self.t) * self.rate)
            self.t = now
            self.allow -= n
            wait = -self.allow / self.rate if self.allow < 0 else 0
        if wait > 0:
            time.sleep(wait)


def helper(store, *a):
    return sh("perl", HELPER, a[0], store, *map(str, a[1:]))


def vzdump_config(vmid, disks, array_store):
    """Config in vzdump format: no snapshots/pending/special, no unused/lock/parent,
    plus one '#qmdump#map:<disk>:drive-<disk>:<storage>:raw:' line per disk in the backup.
    Without these lines the restore keeps the disks pointing to the original volumes."""
    out = []
    for line in open(f"/etc/pve/qemu-server/{vmid}.conf"):
        if line.startswith("["):
            break
        if line.startswith(("#qmdump#", "#vzdump#", "lock:", "parent:")) or line.split(":")[0].startswith("unused"):
            continue
        out.append(line)
    for k in disks:
        out.append(f"#qmdump#map:{k}:drive-{k}:{array_store}:raw:\n")
    return "".join(out)


def check_supported(vmid, array_store):
    """Refuse disks outside the array: they are not in the backup and the restore would
    leave them pointing to the original volume."""
    bad = []
    for line in sh("qm", "config", str(vmid), "--current").splitlines():
        key, _, val = line.partition(": ")
        if key.rstrip("0123456789") in ("scsi", "virtio", "sata", "ide", "efidisk", "tpmstate") and "media=cdrom" not in val \
                and not val.startswith(array_store + ":") and val.split(",")[0] != "none":
            bad.append(f"{key}={val.split(',')[0]}")
    if bad:
        raise SystemExit(f"VM {vmid} has disks outside storage {array_store}: {', '.join(bad)} - use the regular backup")


# ------------------------------------------------------------------ backup
def main():
    vmid, pbs_store = int(sys.argv[1]), sys.argv[2]
    if not os.path.exists(f"/var/run/qemu-server/{vmid}.qmp"):
        raise SystemExit(f"VM {vmid} is stopped (the snapshot backup needs it running)")
    force_full = "--full" in sys.argv
    os.makedirs(STATE_DIR, mode=0o700, exist_ok=True)
    os.makedirs(SHARED_DIR, exist_ok=True)
    stf = f"{SHARED_DIR}/{vmid}-{pbs_store}.json"
    # The PBS library state (~100 bytes: checksum of the last index of each disk) must travel
    # with the bitmap: without it the library refuses the incremental and reads the whole disk.
    lsf = f"{SHARED_DIR}/{vmid}-{pbs_store}.libstate"
    for new, old in ((stf, f"{STATE_DIR}/{vmid}-{pbs_store}.json"), (lsf, f"{STATE_DIR}/{vmid}-{pbs_store}.libstate")):
        if not os.path.exists(new) and os.path.exists(old):             # before 0.3.4 the state was per node
            open(new, "wb").write(open(old, "rb").read())
    state = json.load(open(stf)) if os.path.exists(stf) else {}
    array_store = state.get("array_store") or [l.split(": ")[1].split(":")[0] for l in sh("qm", "config", str(vmid)).splitlines()
                                               if l.startswith("scsi0: ")][0]
    rep = {"vmid": vmid, "pbs": pbs_store, "t": {}}
    T0 = time.time()

    check_supported(vmid, array_store)
    vgmode = storage_section(array_store).get("sw-snapmode") == "volumegroup"
    disks = guest_disks(vmid, array_store)
    old_bm = state.get("bitmap")
    have_bm = bool(old_bm) and all(old_bm in d["bitmaps"] for d in disks.values())
    mode = "incremental" if have_bm and os.path.exists(lsf) and not force_full else "full"
    limit = bandwidth_limit(pbs_store)
    throttle = Throttle(limit)
    rep["bwlimit_KiB_s"] = limit
    rep["snapshot_engine"] = "volumegroup" if vgmode else "flashcopy"
    rep["reason"] = "--full requested" if force_full else ("no bitmap (first backup or VM restarted)" if not have_bm else "bitmap ok")
    new_bm = f"swcbt-{int(time.time())}"
    snap = f"cbt{int(time.time())}"
    vols = [d["volname"] for d in disks.values()]
    # progress for the panel (running activities)
    pfile = f"/run/ibm-storage-cbt-{vmid}.progress"
    prog = {"vmid": int(vmid), "pbs": pbs_store, "snap": snap, "mode": mode, "started": int(time.time()), "phase": "snapshot", "total": 0, "done": 0}

    def progress(**kw):
        prog.update(kw)
        try:
            json.dump(prog, open(pfile + ".tmp", "w"))
            os.replace(pfile + ".tmp", pfile)
        except OSError:
            pass
    atexit.register(lambda: os.path.exists(pfile) and os.unlink(pfile))
    progress()

    # 1-2. array snapshot with an overlapping bitmap swap (without pausing the VM)
    t = time.time()
    if not vgmode:
        helper(array_store, "prepare", vmid, snap, *vols)
        rep["t"]["flashcopy_prepare"] = round(time.time() - t, 2)
    qmp(vmid, "transaction", actions=[{"type": "block-dirty-bitmap-add",
        "data": {"node": d["node"], "name": new_bm, "granularity": 65536}} for d in disks.values()])
    use_agent = "agent: 1" in sh("qm", "config", str(vmid)) or "agent: enabled=1" in sh("qm", "config", str(vmid))
    frozen = use_agent and agent(vmid, "fsfreeze-freeze")
    t = time.time()
    try:
        helper(array_store, "vgsnap" if vgmode else "trigger", vmid, snap)
    finally:
        if frozen:
            agent(vmid, "fsfreeze-thaw")
    rep["t"]["fs_freeze" if frozen else "snapshot_trigger"] = round(time.time() - t, 3)
    rep["fs_freeze"] = frozen
    if have_bm:
        qmp(vmid, "transaction", actions=[{"type": "block-dirty-bitmap-disable",
            "data": {"node": d["node"], "name": old_bm}} for d in disks.values()])
    # Bitmaps left by a run that was killed (no chance to merge them) hold writes the old bitmap
    # missed: fold them into it. The new bitmap already records, so nothing falls in between.
    for d in disks.values():
        for b in sorted(x for x in d["bitmaps"] if x.startswith("swcbt-") and x not in (old_bm, new_bm)):
            if have_bm:
                qmp(vmid, "block-dirty-bitmap-merge", node=d["node"], target=old_bm, bitmaps=[b])
            qmp(vmid, "block-dirty-bitmap-remove", node=d["node"], name=b)

    sock = f"/run/ibm-storage-cbt-{vmid}.sock"
    handle = None
    devs = {}
    try:
        # 3. which chunks to read
        if mode == "incremental":
            if os.path.exists(sock):
                os.unlink(sock)
            qmp(vmid, "nbd-server-start", addr={"type": "unix", "data": {"path": sock}})
            try:
                for k, d in disks.items():
                    d["chunks"] = dirty_chunks(vmid, d["node"], old_bm, k, sock, d["size"])
            finally:
                qmp(vmid, "nbd-server-stop")
        progress(phase="mapping the snapshot")
        if vgmode:
            devmap = dict(l.split(" ", 1) for l in helper(array_store, "vgmap", vmid, snap, *vols).splitlines() if " " in l)
            for k, d in disks.items():
                d["dev"] = devmap[d["volname"]]
        else:
            for k, d in disks.items():
                d["dev"] = helper(array_store, "map", d["volname"], snap)
        progress(phase="reading and sending", total=sum((len(d["chunks"]) * CHUNK if d.get("chunks") is not None else d["size"]) for d in disks.values()))
        last_p = [0.0]

        # 4. PBS
        c = pbs_conf(pbs_store)
        if os.path.exists(lsf) and mode == "incremental":
            buf = open(lsf, "rb").read()
            arr = (ctypes.c_uint8 * len(buf)).from_buffer_copy(buf)
            lib.proxmox_import_state(arr, len(buf))
        err = ctypes.c_char_p()
        btime = int(time.time())
        handle = lib.proxmox_backup_new_ns(c["repo"].encode(), (c.get("namespace") or "").encode() or None, str(vmid).encode(),
                                           btime, CHUNK, c["password"].encode(), None, None, None, True, False,
                                           c["fingerprint"].encode(), ctypes.byref(err))
        if not handle:
            raise RuntimeError(f"PBS: {err.value.decode() if err.value else 'failed to create handle'}")
        chk(lib.proxmox_backup_connect(handle, ctypes.byref(err)), err, "connect")

        t = time.time()
        stats = {"read": 0, "sent": 0, "reused": 0, "zero": 0}
        for k, d in disks.items():
            name = f"drive-{k}".encode()
            inc = mode == "incremental" and lib.proxmox_backup_check_incremental(handle, name, d["size"]) == 1
            d["inc"] = inc
            dev_id = chk(lib.proxmox_backup_register_image(handle, name, d["size"], inc, ctypes.byref(err)), err, "register")
            n_chunks = (d["size"] + CHUNK - 1) // CHUNK
            todo = d.get("chunks") if inc else range(n_chunks)
            fd = os.open(d["dev"], os.O_RDONLY)

            def work(i, fd=fd, dev_id=dev_id, size=d["size"]):
                off = i * CHUNK
                ln = min(CHUNK, size - off)
                data = os.pread(fd, ln, off)
                e = ctypes.c_char_p()
                if ln == CHUNK and data == ZERO:
                    r = lib.proxmox_backup_write_data(handle, dev_id, None, off, ln, ctypes.byref(e))
                    return ln, -1 if r < 0 else 0, "zero", e
                r = lib.proxmox_backup_write_data(handle, dev_id, data, off, ln, ctypes.byref(e))
                if r > 0:
                    throttle.take(ln)        # only what went to the PBS; known chunks do not travel
                return ln, r, "data", e

            with cf.ThreadPoolExecutor(WORKERS) as ex:
                for ln, r, kind, e in ex.map(work, todo):
                    if r < 0:
                        raise RuntimeError(f"PBS write: {e.value.decode() if e.value else '?'}")
                    stats["read"] += ln
                    if time.time() - last_p[0] > 2:
                        last_p[0] = time.time()
                        progress(done=stats["read"])
                    if kind == "zero":
                        stats["zero"] += ln
                    elif r == 0:
                        stats["reused"] += ln
                    else:
                        stats["sent"] += ln
            os.close(fd)
            chk(lib.proxmox_backup_close_image(handle, dev_id, ctypes.byref(err)), err, "close")
            devs[k] = {"size_GiB": round(d["size"] / 2 ** 30, 2), "incremental": inc,
                       "chunks_read": len(todo), "read_MiB": round(len(todo) * CHUNK / 2 ** 20, 1)}
        rep["t"]["read_and_send"] = round(time.time() - t, 2)
        progress(phase="finishing", done=stats["read"])

        conf = vzdump_config(vmid, disks, array_store)
        chk(lib.proxmox_backup_add_config(handle, b"qemu-server.conf", conf.encode(), len(conf.encode()), ctypes.byref(err)), err, "config")
        chk(lib.proxmox_backup_finish(handle, ctypes.byref(err)), err, "finish")
        sz = ctypes.c_size_t()
        p = lib.proxmox_export_state(ctypes.byref(sz))
        open(lsf, "wb").write(ctypes.string_at(p, sz.value))
        lib.proxmox_free_state_buf(p)
        lib.proxmox_backup_disconnect(handle)
        handle = None

        # 5. success: the new bitmap becomes the current one
        if have_bm:
            for d in disks.values():
                qmp(vmid, "block-dirty-bitmap-remove", node=d["node"], name=old_bm)
        json.dump({"bitmap": new_bm, "array_store": array_store, "last": btime}, open(stf, "w"))
        rep.update(mode=mode, backup=f"{pbs_store}:backup/vm/{vmid}/" + time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(btime)),
                   disks=devs, **{k + "_MiB": round(v / 2 ** 20, 1) for k, v in stats.items()})
        secs = rep["t"]["read_and_send"]
        rep["read_MiB_s"] = round(stats["read"] / 2 ** 20 / secs, 1) if secs else None
    except Exception:
        if handle:
            lib.proxmox_backup_abort(handle, b"CBT backup failed")
            lib.proxmox_backup_disconnect(handle)
        # failed: the next backup must cover old + new -> merge into the new bitmap
        if have_bm:
            for d in disks.values():
                try:
                    qmp(vmid, "block-dirty-bitmap-merge", node=d["node"], target=new_bm, bitmaps=[old_bm])
                    qmp(vmid, "block-dirty-bitmap-remove", node=d["node"], name=old_bm)
                except Exception:
                    pass
        json.dump({"bitmap": new_bm, "array_store": array_store, "last": state.get("last")}, open(stf, "w"))
        raise
    finally:
        if vgmode:
            try:
                helper(array_store, "vgclean", vmid, snap)
            except Exception as e:
                print("warning cleanup:", e, file=sys.stderr)
        else:
            for d in disks.values():
                try:
                    helper(array_store, "unmap", d["volname"], snap)
                except Exception as e:
                    print("warning unmap:", e, file=sys.stderr)
            for d in disks.values():
                try:
                    helper(array_store, "delete", d["volname"], snap)
                except Exception as e:
                    print("warning delete snapshot:", e, file=sys.stderr)
    rep["t"]["total"] = round(time.time() - T0, 2)
    print(json.dumps(rep, indent=1, ensure_ascii=False))


def locked(vmid, fn):
    """Same lock as vzdump ('lock: backup'): Proxmox refuses migration, snapshots and config
    changes while the backup runs. A migration in the middle would move the VM config away
    and the backup would fail."""
    p = subprocess.run(["qm", "set", str(vmid), "--lock", "backup"], capture_output=True, text=True)
    if p.returncode:
        raise SystemExit(f"VM {vmid} could not be locked for the backup: {(p.stderr or p.stdout).strip()}")
    try:
        return fn()
    finally:
        subprocess.run(["qm", "unlock", str(vmid)], capture_output=True)


if __name__ == "__main__":
    locked(sys.argv[1], main)
