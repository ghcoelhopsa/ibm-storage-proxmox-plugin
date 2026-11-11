#!/usr/bin/python3
"""Backup job: try the backup from the array snapshot (cbt_backup.py) and, if it
fails, run the regular Proxmox backup (vzdump) to the same PBS.

  cbt_job.py <vmid> <pbs-storage> [--full] [--no-fallback] [--protected]

--protected marks the finished backup as protected on the PBS, so prune jobs and a
plain "remove" leave it alone until someone clears the mark.

Prints a JSON with the result (the panel reads it from the log file).
"""
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))


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
    t0 = time.time()
    cmd = ["/usr/bin/python3", os.path.join(HERE, "cbt_backup.py"), vmid, pbs] + (["--full"] if "--full" in flags else [])
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode == 0:
        if "--protected" in flags:
            try:
                rep = json.loads(p.stdout)
                protect(rep)
                print(json.dumps(rep, indent=1, ensure_ascii=False))
                return 0
            except (ValueError, KeyError):
                pass
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
        print(json.dumps(rep, indent=1, ensure_ascii=False))
        return 1
    rep["backup"] = f"{pbs}:backup/{arch.group(1)}"
    if tr:
        mult = {"MiB": 1, "GiB": 1024, "TiB": 1024 * 1024}[tr.group(2)]
        rep["read_MiB"] = round(float(tr.group(1)) * mult, 1)
        rep["read_MiB_s"] = float(tr.group(4))
    if "--protected" in flags:
        protect(rep)
    print(json.dumps(rep, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
