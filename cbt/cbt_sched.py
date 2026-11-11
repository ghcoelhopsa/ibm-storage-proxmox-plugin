#!/usr/bin/python3
"""Schedule of the backup from storage snapshot: runs, one at a time, the VMs of a
schedule that are on this node. Called by a systemd timer
(ibm-storage-cbt-sched-<id>.timer), created by the panel.

  cbt_sched.py <id>      reads /etc/ibm-storage-cbt/schedules/<id>.json
"""
import json
import os
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


def main():
    sid = sys.argv[1]
    cfg = json.load(open(f"{SCHED_DIR}/{sid}.json"))
    os.makedirs(LOG_DIR, exist_ok=True)
    out = []
    for vmid in cfg["vmids"]:
        vmid = int(vmid)
        if not os.path.exists(f"/etc/pve/qemu-server/{vmid}.conf"):
            continue                                    # VM on another node (or removed): the timer there handles it
        if active(f"ibm-storage-cbt-{vmid}-*"):
            out.append({"vmid": vmid, "ok": False, "error": "a backup of this VM was already running"})
            continue
        ts = int(time.time())
        flags = ("" if cfg.get("fallback", True) else " --no-fallback") + (" --protected" if cfg.get("protected") else "")
        log = f"{LOG_DIR}/{vmid}-{ts}.json"
        # same shape as the manual backups from the panel: the unit shows up as a running activity
        p = subprocess.run(["systemd-run", "--unit", f"ibm-storage-cbt-{vmid}-{ts}", "--collect", "--wait", "--quiet",
                            "--property=Type=exec", "/bin/sh", "-c",
                            f"/usr/bin/python3 {HERE}/cbt_job.py {vmid} {cfg['pbs']}{flags} > {log} 2>&1"])
        out.append({"vmid": vmid, "ok": p.returncode == 0, "log": os.path.basename(log)})
        time.sleep(1)                                   # makes sure each VM gets a different ts
    json.dump({"id": sid, "finished": int(time.time()), "results": out}, open(f"{LOG_DIR}/sched-{sid}.last.json", "w"))
    return 0 if all(r["ok"] for r in out) else 1


if __name__ == "__main__":
    sys.exit(main())
