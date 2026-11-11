"""Storage Virtualize 8.5.2+ : volume group snapshots (incl. Safeguarded) and
policy-based replication (PBR), used by the "Protection" and "DR" screens.

WARNING: written from the documentation (Redbooks REDP-5704, REDP-5737,
SG24-8569 and the official ibm.storage_virtualize Ansible modules); NOT yet
validated on 8.x hardware. See docs/FS8X-SAFEGUARDED-DR.md.
"""
import re

from sv import ArrayError, code_ge

VG_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,62}$")
SNAPNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,39}$")
REPL_ACTIONS = {
    "failover": ("mode", "independent"),         # runs on the system that holds the recovery copy
    "restart": ("mode", "production"),           # runs on the system that becomes production
    "test-start": ("startrecoverytest", None),   # 8.6.2+, on the recovery system
    "test-stop": ("stoprecoverytest", None),
}


def caps8(code_level):
    return {
        "vg_snapshot": code_ge(code_level, "8.5.1"),
        "safeguarded": code_ge(code_level, "8.5.2"),
        "pbr": code_ge(code_level, "8.5.2"),
        "recovery_test": code_ge(code_level, "8.6.2"),
        "partition": code_ge(code_level, "8.6.1"),
        # 9.1.0+: inside a partition, the recovery copy stays online and read only
        "readonly_recovery": code_ge(code_level, "9.1.0"),
    }


def _ls(a, cmd, opts=None, obj=None):
    try:
        return a.ls(cmd, opts, obj)
    except ArrayError:
        return []


def _yes(v):
    return str(v or "").lower() in ("yes", "1", "true", "on")


def guest_of(prefix, vg):
    m = re.match(rf"^{re.escape(prefix)}-vm-(\d+)$", vg or "")
    return int(m.group(1)) if m else None


def replication_detail(a, vg, system):
    """Replication state of a volume group, already telling which side is the local one."""
    d = a.show("lsvolumegroupreplication", vg)
    if not d or not d.get("replication_policy_name"):
        return None
    local = "1"
    for n in ("1", "2"):
        if d.get(f"location{n}_system_name") == system.get("name") or d.get(f"location{n}_system_id") == system.get("id"):
            local = n
    remote = "2" if local == "1" else "1"
    g = lambda n, k: d.get(f"location{n}_{k}") or ""
    return {
        "policy": d.get("replication_policy_name"),
        "local_mode": g(local, "replication_mode"), "local_status": g(local, "status"),
        "remote_system": g(remote, "system_name"), "remote_mode": g(remote, "replication_mode"),
        "remote_status": g(remote, "status"),
        "within_rpo": g("1", "within_rpo") or g("2", "within_rpo"),
        "recovery_point": g("1", "running_recovery_point") or g("2", "running_recovery_point"),
        "link": d.get("link1_status") or "",
        "recovery_test": _yes(d.get("recovery_test_active") or d.get("recovery_test")),
        "raw": d,
    }


def partitions(a):
    """Published storage partitions (8.6.1+ / 9.x). No filter on the array: on 9.1.0/9.1.1, filtering
    lsvdisk/lshost by partition outside the partition IP can bring down the config node."""
    out = []
    for p in _ls(a, "lspartition"):
        out.append({"name": p.get("name"), "draft": _yes(p.get("draft")),
                    "replication_policy": p.get("replication_policy_name") or "", "ha_status": p.get("ha_status") or "",
                    "link_status": p.get("link_status") or "", "active_management": p.get("active_management_system_name") or "",
                    "location1": p.get("location1_system_name") or "", "location2": p.get("location2_system_name") or "",
                    "hosts": p.get("host_count"), "volume_groups": p.get("volume_group_count")})
    return out


def protection(a, prefix, system, partition=None):
    """Volume groups of the VMs on this storage, with snapshots, policies and replication."""
    c = caps8(system.get("code_level", ""))
    out = {"capabilities": c, "groups": [], "snapshot_policies": [], "replication_policies": [], "partnerships": [], "partition": None}
    if partition and c["partition"]:
        out["partition"] = next((p for p in partitions(a) if p["name"] == partition), {"name": partition, "missing": True})
    if not c["vg_snapshot"]:
        return out
    snaps = {}
    for s in _ls(a, "lsvolumegroupsnapshot"):
        snaps.setdefault(s.get("volume_group_name", ""), []).append({
            "id": s.get("id"), "name": s.get("name") or s.get("snapshot_name"),
            "time": s.get("time") or s.get("timestamp") or "", "expires": s.get("expiration_time") or "",
            "safeguarded": _yes(s.get("safeguarded")), "state": s.get("state") or "",
            "auto": _yes(s.get("auto_snapshot")), "matches_group": s.get("matches_group") or "",
        })
    sched = {}
    for r in _ls(a, "lssnapshotschedule"):
        sched.setdefault(r.get("policy_name", ""), []).append(
            {"unit": r.get("backup_unit"), "interval": r.get("backup_interval"), "retention_days": r.get("retention_days")})
    out["snapshot_policies"] = [{"name": p.get("name"), "schedules": sched.get(p.get("name"), [])} for p in _ls(a, "lssnapshotpolicy")]
    vgpol = {r.get("volume_group_name"): r for r in _ls(a, "lsvolumegroupsnapshotpolicy")}
    if c["pbr"]:
        out["replication_policies"] = [{"name": p.get("name"), "topology": p.get("topology"), "rpo_alert": p.get("rpo_alert"),
                                        "location1": p.get("location1_system_name"), "location2": p.get("location2_system_name")}
                                       for p in _ls(a, "lsreplicationpolicy")]
        out["partnerships"] = [{"name": p.get("name"), "location": p.get("location"), "state": p.get("partnership"), "type": p.get("type")}
                               for p in _ls(a, "lspartnership") if p.get("location") != "local"]
    for g in _ls(a, "lsvolumegroup"):
        name = g.get("name", "")
        vmid = guest_of(prefix, name)
        if vmid is None:
            continue
        pol = vgpol.get(name, {})
        row = {
            "name": name, "vmid": vmid, "volumes": g.get("volume_count"), "partition": g.get("partition_name") or "",
            "snapshot_policy": g.get("snapshot_policy_name") or pol.get("snapshot_policy_name") or "",
            "policy_safeguarded": _yes(pol.get("snapshot_policy_safeguarded") or g.get("snapshot_policy_safeguarded")),
            "policy_suspended": _yes(pol.get("snapshot_policy_suspended")),
            "snapshots": sorted(snaps.get(name, []), key=lambda s: s["time"], reverse=True),
            "replication": None,
        }
        if c["pbr"] and g.get("replication_policy_name"):
            try:
                row["replication"] = replication_detail(a, name, system)
            except ArrayError as e:
                row["replication"] = {"error": str(e)}
        out["groups"].append(row)
    out["groups"].sort(key=lambda r: r["vmid"])
    return out


def add_snapshot(a, vg, name, safeguarded=False, days=None):
    opts = [("volumegroup", vg), ("name", name)]
    if safeguarded:
        opts += [("safeguarded", None), ("retentiondays", int(days))]
    elif days:
        opts += [("retentiondays", int(days))]
    return a.run("addsnapshot", opts)


def restore_snapshot(a, vg, snap):
    return a.run("restorefromsnapshot", [("snapshot", snap), ("volumegroup", vg)])


def remove_snapshot(a, vg, snap):
    return a.run("rmsnapshot", [("snapshot", snap), ("volumegroup", vg)])


def set_snapshot_policy(a, vg, policy, safeguarded=False):
    if not policy:
        return a.run("chvolumegroup", [("nosnapshotpolicy", None)], vg)
    return a.run("chvolumegroup", [("snapshotpolicy", policy)] + ([("safeguarded", None)] if safeguarded else []), vg)


def set_replication_policy(a, vg, policy):
    return a.run("chvolumegroup", [("replicationpolicy", policy)] if policy else [("noreplicationpolicy", None)], vg)


def replication_action(a, vg, action):
    k, v = REPL_ACTIONS[action]
    return a.run("chvolumegroupreplication", [(k, v)], vg)
