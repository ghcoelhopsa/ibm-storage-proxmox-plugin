#!/usr/bin/python3
"""Backup job: try the backup from the array snapshot (cbt_backup.py) and, if it
fails, run the regular Proxmox backup (vzdump) to the same PBS.

  cbt_job.py <vmid> <pbs-storage> [--full] [--no-fallback] [--protected]

--protected marks the finished backup as protected on the PBS, so prune jobs and a
plain "remove" leave it alone until someone clears the mark.

Concurrency: at most `max_parallel` jobs run at the same time on this node (manual and scheduled
alike), read from /etc/ibm-storage-cbt/settings.json (default 2). A job over the limit waits for a
free slot; the panel shows it as queued.

Prints a JSON with the result (the panel reads it from the log file).
"""
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SETTINGS = "/etc/ibm-storage-cbt/settings.json"
SLOT_DIR = "/run/lock/ibm-storage-cbt"
DEFAULT_PARALLEL = 2
MAX_PARALLEL = 16


def max_parallel():
    try:
        n = int(json.load(open(SETTINGS)).get("max_parallel", DEFAULT_PARALLEL))
    except (OSError, ValueError, TypeError, AttributeError):
        n = DEFAULT_PARALLEL
    return min(max(n, 1), MAX_PARALLEL)


def take_slot(vmid):
    """Blocks until one of the node's backup slots is free; returns the open lock file (keep it
    open: the slot is released when the process ends, also if it is killed)."""
    import fcntl
    os.makedirs(SLOT_DIR, exist_ok=True)
    pfile = f"/run/ibm-storage-cbt-{vmid}.progress"
    since = int(time.time())
    while True:
        n = max_parallel()                       # re-read: a changed limit applies to waiting jobs
        for i in range(n):
            f = open(f"{SLOT_DIR}/slot-{i}", "w")
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                f.write(f"{vmid}\n")
                f.flush()
                return f
            except BlockingIOError:
                f.close()
        try:
            json.dump({"vmid": int(vmid), "phase": "queued", "started": since, "limit": n, "total": 0, "done": 0},
                      open(pfile + ".tmp", "w"))
            os.replace(pfile + ".tmp", pfile)
        except OSError:
            pass
        time.sleep(5)


def protect(rep):
    """Sets the 'protected' mark on the backup named in the report."""
    node = os.uname().nodename.split(".")[0]
    p = subprocess.run(["pvesh", "set", f"/nodes/{node}/storage/{rep['pbs']}/content/{rep['backup']}", "--protected", "1"],
                       capture_output=True, text=True)
    if p.returncode == 0:
        rep["protected"] = True
    else:
        lines = (p.stderr + p.stdout).strip().splitlines()
        rep["protect_error"] = (lines[-1] if lines else f"rc={p.returncode}")[:300]


def main():
    vmid, pbs = sys.argv[1], sys.argv[2]
    flags = sys.argv[3:]
    tq = time.time()
    slot = take_slot(vmid)  # noqa: F841 - held until the process ends
    t0 = time.time()
    queued = round(t0 - tq, 1)
    cmd = ["/usr/bin/python3", os.path.join(HERE, "cbt_backup.py"), vmid, pbs] + (["--full"] if "--full" in flags else [])
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode == 0:
        if "--protected" in flags:
            try:
                rep = json.loads(p.stdout)
                protect(rep)
                print(json.dumps(dict(rep, queued_s=queued), indent=1, ensure_ascii=False))
                return 0
            except (ValueError, KeyError):
                pass
        try:
            print(json.dumps(dict(json.loads(p.stdout), queued_s=queued), indent=1, ensure_ascii=False))
        except ValueError:
            print(p.stdout)
        return 0
    lines = [l for l in (p.stderr + "\n" + p.stdout).strip().splitlines() if l.strip()]
    reason = lines[-1].strip()[:300] if lines else f"rc={p.returncode}"
    if "--no-fallback" in flags:
        print(json.dumps({"vmid": int(vmid), "pbs": pbs, "error": reason}, ensure_ascii=False))
        return 1

    v = subprocess.run(["vzdump", vmid, "--storage", pbs, "--mode", "snapshot",
                        "--notes-template", "fallback from the array snapshot backup"], capture_output=True, text=True)
    out = v.stdout + v.stderr
    arch = re.search(r"creating Proxmox Backup Server archive '([^']+)'", out)
    tr = re.search(r"transferred ([\d.]+) (MiB|GiB|TiB) in (\d+) seconds \(([\d.]+) MiB/s\)", out)
    rep = {"vmid": int(vmid), "pbs": pbs, "mode": "normal", "fallback": True, "reason": reason,
           "t": {"total": round(time.time() - t0, 2)}}
    if v.returncode != 0 or not arch:
        err = [l for l in out.strip().splitlines() if "ERROR" in l]
        rep["error"] = f"snapshot: {reason} | regular backup: {(err[-1] if err else out.strip().splitlines()[-1] if out.strip() else 'failed')[:300]}"
        print(json.dumps(dict(rep, queued_s=queued), indent=1, ensure_ascii=False))
        return 1
    rep["backup"] = f"{pbs}:backup/{arch.group(1)}"
    if tr:
        mult = {"MiB": 1, "GiB": 1024, "TiB": 1024 * 1024}[tr.group(2)]
        rep["read_MiB"] = round(float(tr.group(1)) * mult, 1)
        rep["read_MiB_s"] = float(tr.group(4))
    if "--protected" in flags:
        protect(rep)
    print(json.dumps(dict(rep, queued_s=queued), indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
