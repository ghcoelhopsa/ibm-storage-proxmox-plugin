"""Backups from storage snapshots: activity overview, mounted snapshots and
schedules (systemd timers created on each Proxmox node)."""
import json
import re
import secrets

SCHED_DIR = "/etc/ibm-storage-cbt/schedules"
CBT_DIR = "/usr/local/lib/ibm-storage-cbt"
ID_RE = re.compile(r"^[a-z0-9]{6,12}$")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
SNAP_RE = re.compile(r"^cbt\d{9,11}$")

# runs on the nodes (read only): active units, latest jobs, progress, schedules and multipath disks
NODE_SCRIPT = r"""python3 - <<'PYEOF'
import glob, json, os, subprocess, time
def run(c):
    try:
        return subprocess.run(c, shell=True, capture_output=True, text=True, timeout=20).stdout
    except Exception:
        return ""
units = [l.split()[0] for l in run("systemctl list-units 'ibm-storage-cbt-*' --no-legend --plain --state=active,activating").splitlines() if l.strip()]
logs = []
for f in sorted(glob.glob("/var/lib/ibm-storage-cbt/logs/[0-9]*-[0-9]*.json"), key=os.path.getmtime, reverse=True)[:25]:
    vmid, ts = os.path.basename(f)[:-5].split("-")
    try:
        body = open(f).read()[-6000:]
    except OSError:
        body = ""
    logs.append({"vmid": int(vmid), "id": int(ts), "running": ("ibm-storage-cbt-%s-%s.service" % (vmid, ts)) in units,
                 "body": body, "mtime": int(os.path.getmtime(f))})
prog = {}
# compatibility: progress files written by older node tools use Portuguese keys and phase names
OLD_KEYS = {"fase": "phase", "feito": "done", "inicio": "started", "modo": "mode"}
OLD_PHASES = {"mapeando o snapshot": "mapping the snapshot", "lendo e enviando": "reading and sending", "finalizando": "finishing"}
for f in glob.glob("/run/ibm-storage-cbt-*.progress"):
    try:
        p = json.load(open(f))
        p = {OLD_KEYS.get(k, k): v for k, v in p.items()}
        p["phase"] = OLD_PHASES.get(p.get("phase"), p.get("phase"))
        prog[str(p["vmid"])] = p
    except Exception:
        pass
scheds = []
for f in sorted(glob.glob("/etc/ibm-storage-cbt/schedules/*.json")):
    try:
        c = json.load(open(f))
    except Exception:
        continue
    props = dict(l.split("=", 1) for l in run("systemctl show ibm-storage-cbt-sched-%s.timer --timestamp=unix -p NextElapseUSecRealtime -p LastTriggerUSec -p ActiveState" % c["id"]).splitlines() if "=" in l)
    num = lambda v: int(v[1:]) if v and v.startswith("@") and v[1:].isdigit() else None
    c["next"] = num(props.get("NextElapseUSecRealtime")); c["last_trigger"] = num(props.get("LastTriggerUSec"))
    c["timer_active"] = props.get("ActiveState") == "active"
    c["running"] = ("ibm-storage-cbt-sched-%s.service" % c["id"]) in units
    try:
        c["last"] = json.load(open("/var/lib/ibm-storage-cbt/logs/sched-%s.last.json" % c["id"]))
    except Exception:
        c["last"] = None
    scheds.append(c)
print(json.dumps({"units": units, "logs": logs, "progress": prog, "schedules": scheds,
                  "mpaths": [os.path.basename(x)[len("dm-uuid-mpath-3"):] for x in glob.glob("/dev/disk/by-id/dm-uuid-mpath-3*")],
                  "installed": os.path.exists("/usr/local/lib/ibm-storage-cbt/cbt_job.py"), "now": int(time.time())}))
PYEOF"""


def new_id():
    return secrets.token_hex(4)


def validate(b, pbs_ids):
    """Checks and normalises a schedule coming from the UI. Raises ValueError with the message."""
    sid = b.get("id") or new_id()
    if not ID_RE.match(sid):
        raise ValueError("invalid identifier")
    name = re.sub(r"[^\w .,()/-]", "", str(b.get("name", "")), flags=re.UNICODE).strip()[:60]
    if not name:
        raise ValueError("give the schedule a name")
    try:
        vmids = sorted({int(v) for v in b.get("vmids") or []})
    except (TypeError, ValueError):
        raise ValueError("invalid VM list")
    if not vmids or any(v < 100 or v > 999999999 for v in vmids):
        raise ValueError("choose at least one VM")
    if b.get("pbs") not in pbs_ids:
        raise ValueError("choose a valid PBS storage")
    if not TIME_RE.match(str(b.get("time", ""))):
        raise ValueError("invalid time (HH:MM)")
    days = [d for d in DAYS if d in (b.get("days") or [])]
    return {"id": sid, "name": name, "vmids": vmids, "pbs": b["pbs"], "time": b["time"], "days": days,
            "fallback": bool(b.get("fallback", True)), "protected": bool(b.get("protected", False)), "enabled": bool(b.get("enabled", True))}


def on_calendar(c):
    return f"{','.join(c['days']) + ' ' if c['days'] else ''}*-*-* {c['time']}:00"


def unit_files(c):
    desc = re.sub(r"[^A-Za-z0-9 ._-]", "", c["name"]) or c["id"]
    svc = (f"[Unit]\nDescription=Backup from storage snapshot - {desc}\n\n[Service]\nType=oneshot\n"
           f"ExecStart=/usr/bin/python3 {CBT_DIR}/cbt_sched.py {c['id']}\n")
    tim = (f"[Unit]\nDescription=Schedule for the backup from storage snapshot - {desc}\n\n[Timer]\n"
           f"OnCalendar={on_calendar(c)}\nPersistent=true\n\n[Install]\nWantedBy=timers.target\n")
    return svc, tim


def install_cmd(c):
    u = f"ibm-storage-cbt-sched-{c['id']}"
    return (f"systemd-analyze calendar '{on_calendar(c)}' >/dev/null && systemctl daemon-reload && "
            + (f"systemctl enable --now {u}.timer" if c["enabled"] else f"systemctl disable --now {u}.timer 2>/dev/null; true"))


def remove_cmd(sid):
    u = f"ibm-storage-cbt-sched-{sid}"
    return (f"systemctl disable --now {u}.timer 2>/dev/null; rm -f /etc/systemd/system/{u}.timer /etc/systemd/system/{u}.service "
            f"{SCHED_DIR}/{sid}.json; systemctl daemon-reload")


# Compatibility: reports written by older node tools use Portuguese keys. They are
# renamed to the current names so old history entries still display.
OLD_REPORT_KEYS = {"modo": "mode", "motivo": "reason", "erro": "error", "discos": "disks",
                   "lido_MiB": "read_MiB", "enviado_MiB": "sent_MiB", "reaproveitado_MiB": "reused_MiB",
                   "MiB_s_lido": "read_MiB_s"}
OLD_TIMING_KEYS = {"preparo_flashcopy": "flashcopy_prepare", "congelamento_fs": "fs_freeze",
                   "disparo_snapshot": "snapshot_trigger", "leitura_e_envio": "read_and_send"}
OLD_DISK_KEYS = {"tamanho_GiB": "size_GiB", "blocos_lidos": "chunks_read", "lido_MiB": "read_MiB"}


def _rename(d, table):
    return {table.get(k, k): v for k, v in d.items()} if isinstance(d, dict) else d


def normalize_report(rep):
    """Renames the old Portuguese keys of a backup report (top level, `t` and per disk)."""
    if not isinstance(rep, dict):
        return rep
    rep = _rename(rep, OLD_REPORT_KEYS)
    if "t" in rep:
        rep["t"] = _rename(rep["t"], OLD_TIMING_KEYS)
    if isinstance(rep.get("disks"), dict):
        rep["disks"] = {k: _rename(v, OLD_DISK_KEYS) for k, v in rep["disks"].items()}
    return rep


def parse_log(item):
    """Result of a job taken from the end of the log file (JSON printed by cbt_job.py)."""
    out = {"vmid": item["vmid"], "id": item["id"], "running": item["running"], "finished": item.get("mtime")}
    body = item.get("body") or ""
    try:
        out["result"] = normalize_report(json.loads(body[body.index("{"):body.rindex("}") + 1]))
    except ValueError:
        if body.strip():
            out["log"] = body.strip()[-800:]
    return out
