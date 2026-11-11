"""Storage Virtualize 8.7 / 9.1 simulator (FAKE8_LEVEL) for the development server.

Wraps the real array (read only) and answers, from an in-memory state, the
volume group, snapshot (Safeguarded) and policy-based replication commands.
It is meant for exercising screens and endpoints without 8.x hardware. Field
names follow the documentation; the output of a real system may differ.
"""
import os
import re
import time

from sv import ArrayError

STATE = {"groups": None, "snaps": [], "seq": 100}
POLICIES = [{"id": "0", "name": "daily-7d"}, {"id": "1", "name": "4h-safeguarded-14d"}]
SCHEDULES = [{"policy_name": "daily-7d", "backup_unit": "day", "backup_interval": "1", "retention_days": "7"},
             {"policy_name": "4h-safeguarded-14d", "backup_unit": "hour", "backup_interval": "4", "retention_days": "14"}]
REPL_POLICIES = [{"id": "0", "name": "async-dr-15min", "topology": "2-site-async-dr", "rpo_alert": "900",
                  "location1_system_name": "LOCAL", "location2_system_name": "FS5300-DR"}]


def ts(off=0):
    return time.strftime("%y%m%d%H%M%S", time.localtime(time.time() + off))


class Fake8:
    def __init__(self, real, prefix="pve", role="production"):
        self.real, self.prefix, self.role = real, prefix, role
        self.sys = None

    def __getattr__(self, name):
        return getattr(self.real, name)

    def system(self):
        if not self.sys:
            self.sys = dict(self.real.show("lssystem"))
            self.sys["code_level"] = os.environ.get("FAKE8_LEVEL", "9.1.2.3") + " (simulated build)"
            self.sys["product_name"] = "IBM FlashSystem 5300 (simulated)"
        return self.sys

    def groups(self):
        if STATE["groups"] is None:
            g = {}
            for v in self.real.ls("lsvdisk"):
                m = re.match(rf"^({re.escape(self.prefix)}-vm-\d+)-disk-\d+$", v["name"])
                if m:
                    g.setdefault(m.group(1), {"count": 0, "snapshot_policy": "", "safeguarded": False, "repl": "", "mode": "", "test": False})["count"] += 1
            names = sorted(g)
            if names:
                g[names[0]].update(snapshot_policy="4h-safeguarded-14d", safeguarded=True, repl="async-dr-15min", mode="production")
                STATE["snaps"] += [
                    {"vg": names[0], "name": "snapshot0", "time": ts(-8 * 3600), "exp": ts(14 * 86400 - 8 * 3600), "safe": True, "auto": True},
                    {"vg": names[0], "name": "snapshot1", "time": ts(-4 * 3600), "exp": ts(14 * 86400 - 4 * 3600), "safe": True, "auto": True},
                    {"vg": names[0], "name": "before-update", "time": ts(-3600), "exp": "", "safe": False, "auto": False}]
            # a volume group that only exists as a recovery copy (VM from another site)
            g[f"{self.prefix}-vm-9100"] = {"count": 2, "snapshot_policy": "", "safeguarded": False, "repl": "async-dr-15min", "mode": "recovery", "test": False}
            STATE["groups"] = g
        return STATE["groups"]

    def _vg(self, name):
        g = self.groups().get(name)
        if not g:
            raise ArrayError("CMMVC5753E The specified object does not exist or is not a suitable candidate.")
        return g

    # ---- queries ----
    def show(self, cmd, obj=None, opts=None):
        if cmd == "lssystem":
            return self.system()
        if cmd == "lsvolumegroupreplication":
            g = self.groups().get(obj)
            if not g:
                return None
            if not g["repl"]:
                return {"name": obj, "replication_policy_name": ""}
            me, other = self.system()["name"], "FS5300-DR"
            prod = g["mode"] == "production"
            return {"name": obj, "replication_policy_name": g["repl"],
                    "location1_system_name": me if prod else other, "location1_replication_mode": "production" if g["mode"] != "independent" else "independent",
                    "location1_status": "healthy", "location1_within_rpo": "",
                    "location2_system_name": other if prod else me, "location2_replication_mode": "recovery" if g["mode"] != "independent" else "independent",
                    "location2_status": "healthy", "location2_within_rpo": "yes", "location2_running_recovery_point": "4",
                    "link1_status": "running", "recovery_test_active": "yes" if g["test"] else "no"} if g["mode"] != "independent" else {
                    "name": obj, "replication_policy_name": g["repl"], "location1_system_name": other, "location1_replication_mode": "production",
                    "location1_status": "disconnected", "location2_system_name": me, "location2_replication_mode": "independent",
                    "location2_status": "healthy", "link1_status": "disconnected"}
        return self.real.show(cmd, obj, opts)

    def ls(self, cmd, opts=None, obj=None):
        if cmd == "lsvolumegroup":
            return [{"id": str(i), "name": n, "volume_count": str(g["count"]), "snapshot_policy_name": g["snapshot_policy"],
                     "replication_policy_name": g["repl"]} for i, (n, g) in enumerate(sorted(self.groups().items()))]
        if cmd == "lsvolumegroupsnapshot":
            self.groups()
            return [{"id": str(i), "name": s["name"], "volume_group_name": s["vg"], "time": s["time"], "expiration_time": s["exp"],
                     "safeguarded": "yes" if s["safe"] else "no", "state": "active", "auto_snapshot": "yes" if s["auto"] else "no",
                     "matches_group": "yes"} for i, s in enumerate(STATE["snaps"])]
        if cmd == "lsvolumegroupsnapshotpolicy":
            return [{"volume_group_name": n, "snapshot_policy_name": g["snapshot_policy"], "snapshot_policy_safeguarded": "yes" if g["safeguarded"] else "no",
                     "snapshot_policy_suspended": "no"} for n, g in self.groups().items() if g["snapshot_policy"]]
        if cmd == "lssnapshotpolicy":
            return POLICIES
        if cmd == "lssnapshotschedule":
            return SCHEDULES
        if cmd == "lspartition":
            return [{"id": "0", "name": "proxmox-prod", "draft": "no", "replication_policy_name": "async-dr-15min", "ha_status": "",
                     "link_status": "synchronized", "active_management_system_name": self.system()["name"],
                     "location1_system_name": self.system()["name"], "location2_system_name": "FS5300-DR",
                     "host_count": "1", "volume_group_count": str(len(self.groups()))},
                    {"id": "1", "name": "draft", "draft": "yes"}]
        if cmd == "lsreplicationpolicy":
            return REPL_POLICIES
        if cmd == "lspartnership":
            return [{"name": self.system()["name"], "location": "local", "partnership": "", "type": ""},
                    {"name": "FS5300-DR", "location": "remote", "partnership": "fully_configured", "type": "ipv4"}]
        return self.real.ls(cmd, opts, obj)

    # ---- commands ----
    def run(self, cmd, opts=None, obj=None, detail=False):
        if cmd.startswith("ls"):
            return self.show(cmd, obj, opts) if detail else self.ls(cmd, opts, obj)
        o = dict(opts or [])
        print("fake8 svctask", cmd, opts, obj, flush=True)
        if cmd == "addsnapshot":
            self._vg(o["volumegroup"])
            if "safeguarded" in o and "retentiondays" not in o:
                raise ArrayError("CMMVC9999E safeguarded requires retentiondays (simulated)")
            STATE["snaps"].append({"vg": o["volumegroup"], "name": o["name"], "time": ts(), "safe": "safeguarded" in o, "auto": False,
                                   "exp": ts(int(o["retentiondays"]) * 86400) if "retentiondays" in o else ""})
        elif cmd in ("rmsnapshot", "restorefromsnapshot"):
            s = next((x for x in STATE["snaps"] if x["vg"] == o["volumegroup"] and x["name"] == o["snapshot"]), None)
            if not s:
                raise ArrayError("CMMVC5753E The specified object does not exist or is not a suitable candidate.")
            if cmd == "rmsnapshot":
                if s["safe"]:
                    raise ArrayError("CMMVC8851E The command failed because the snapshot is Safeguarded and your requested action "
                                     "cannot be completed under your current user role.")
                STATE["snaps"].remove(s)
        elif cmd == "chvolumegroup":
            g = self._vg(obj)
            if "snapshotpolicy" in o:
                g.update(snapshot_policy=o["snapshotpolicy"], safeguarded="safeguarded" in o)
            if "nosnapshotpolicy" in o:
                g.update(snapshot_policy="", safeguarded=False)
            if "replicationpolicy" in o:
                g.update(repl=o["replicationpolicy"], mode="production")
            if "noreplicationpolicy" in o:
                g.update(repl="", mode="")
        elif cmd == "chvolumegroupreplication":
            g = self._vg(obj)
            if o.get("mode") == "independent":
                if g["mode"] != "recovery":
                    raise ArrayError("CMMVC9999E must be run on the system hosting the recovery copy (simulated)")
                g["mode"] = "independent"
            elif o.get("mode") == "production":
                g["mode"] = "production"
            elif "startrecoverytest" in o:
                if g["mode"] != "recovery":
                    raise ArrayError("CMMVC9999E not a recovery copy (simulated)")
                g["test"] = True
            elif "stoprecoverytest" in o:
                g["test"] = False
        else:
            raise ArrayError(f"fake8: write command blocked in the simulator: {cmd}")
        return {"message": "", "id": None}
