#!/usr/bin/python3
"""Schedule of the backup from storage snapshot: runs the VMs of a schedule that are on this
node, `parallel` of them at a time (default 1). Each job also takes one of the node's backup slots
(cbt_job.py), so the node-wide limit holds across schedules and manual backups. Called by a
systemd timer (ibm-storage-cbt-sched-<id>.timer), created by the panel.

  cbt_sched.py <id>      reads /etc/ibm-storage-cbt/schedules/<id>.json

A schedule picks VMs by id ("vmids") and/or by Proxmox resource pool ("pools"). Pool members are read
at every run, so VMs added to or removed from the pool (a cloud portal does that) need no change here.
"""
import concurrent.futures as cf
import json
import os
import threading
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SCHED_DIR = "/etc/ibm-storage-cbt/schedules"
LOG_DIR = "/var/lib/ibm-storage-cbt/logs"


def active(pattern):
    o = subprocess.run(["systemctl", "list-units", pattern, "--no-legend", "--plain", "--state=active,activating"],
                       capture_output=True, text=True).stdout
    return [l.split()[0] for l in o.splitlines() if l.strip()]


def pool_vmids(pools):
    """QEMU VMs that are members of the pools right now (missing pools are skipped)."""
    out = set()
    for pool in pools:
        p = subprocess.run(["pvesh", "get", "/pools", "--poolid", pool, "--output-format", "json"],
                           capture_output=True, text=True)
        if p.returncode != 0:
            print(f"pool {pool}: {p.stderr.strip()}", file=sys.stderr)
            continue
        for g in json.loads(p.stdout or "[]"):
            out.update(int(m["vmid"]) for m in g.get("members") or [] if m.get("type") == "qemu" and not m.get("template"))
    return out


def main():
    sid = sys.argv[1]
    cfg = json.load(open(f"{SCHED_DIR}/{sid}.json"))
    os.makedirs(LOG_DIR, exist_ok=True)
    flags = ("" if cfg.get("fallback", True) else " --no-fallback") + (" --protected" if cfg.get("protected") else "")
    wanted = {int(v) for v in cfg.get("vmids") or []} | pool_vmids(cfg.get("pools") or [])
    here = sorted(v for v in wanted if os.path.exists(f"/etc/pve/qemu-server/{v}.conf"))
    # VMs on other nodes (or removed) are left to the timer there
    ts_lock, last_ts = threading.Lock(), [0]

    def one(vmid):
        if active(f"ibm-storage-cbt-{vmid}-*"):
            return {"vmid": vmid, "ok": False, "error": "a backup of this VM was already running"}
        with ts_lock:                                   # a distinct ts per VM (unit and log names)
            ts = max(int(time.time()), last_ts[0] + 1)
            last_ts[0] = ts
        log = f"{LOG_DIR}/{vmid}-{ts}.json"
        # same shape as the manual backups from the panel: the unit shows up as a running activity
        p = subprocess.run(["systemd-run", "--unit", f"ibm-storage-cbt-{vmid}-{ts}", "--collect", "--wait", "--quiet",
                            "--property=Type=exec", "/bin/sh", "-c",
                            f"/usr/bin/python3 {HERE}/cbt_job.py {vmid} {cfg['pbs']}{flags} > {log} 2>&1"])
        return {"vmid": vmid, "ok": p.returncode == 0, "log": os.path.basename(log)}

    par = min(max(int(cfg.get("parallel", 1) or 1), 1), 16)
    with cf.ThreadPoolExecutor(par) as ex:
        out = list(ex.map(one, here))
    json.dump({"id": sid, "finished": int(time.time()), "results": out}, open(f"{LOG_DIR}/sched-{sid}.last.json", "w"))
    return 0 if all(r["ok"] for r in out) else 1


if __name__ == "__main__":
    sys.exit(main())
