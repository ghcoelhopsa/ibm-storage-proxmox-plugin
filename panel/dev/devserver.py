"""Development server: the real panel with the Proxmox API replaced by a JSON
fixture (captured with panel/dev/capture-fixture.sh) and authentication
bypassed. Array access is real (read-only screens). NEVER expose it: it binds
to 127.0.0.1 only.

    cd /opt/swpanel/app && python3 /opt/swpanel/dev/devserver.py /path/fixture.json
"""
import json
import os
import sys

import uvicorn

sys.path.insert(0, os.environ.get("SWPANEL_APP", "/opt/swpanel/app"))
import main  # noqa: E402

FX = json.load(open(sys.argv[1]))


def fake_call(sess, method, endpoint, **p):
    if method != "GET":
        raise main.PVEError(400, f"dev: {method} {endpoint} disabled")
    if endpoint == "/access/permissions":
        return {"/": {"Sys.Audit": 1, "Sys.Modify": 1}}
    if endpoint not in FX:
        raise main.PVEError(404, f"dev: no fixture for {endpoint}")
    return FX[endpoint]


main.pve.call = fake_call
main.pve.get = lambda sess, endpoint, **p: fake_call(sess, "GET", endpoint, **p)
SESS = {"pve": {"user": "dev@fixture", "issued": 9e12, "ticket": "x", "csrf": "x"},
        "privs": {"Sys.Audit", "Sys.Modify"}, "seen": 0}
main.app.dependency_overrides[main.session] = lambda: SESS
main.app.dependency_overrides[main.admin] = lambda: SESS

# SWPANEL_FAKE8=1: fakes firmware 8.7 (volume groups, Safeguarded, replication) on top of the real array,
# accepts writes on the fake Proxmox and injects a DR inventory. Use with SWPANEL_DATA pointing to a copy.
import os  # noqa: E402

if os.environ.get("SWPANEL_FAKE8"):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import copy  # noqa: E402
    import time  # noqa: E402

    import fake8  # noqa: E402

    real_array_for = main.array_for
    main.array_for = lambda scfg: fake8.Fake8(real_array_for(scfg), scfg.get("sw-prefix", "pve"))
    if os.environ.get("FAKE8_PARTITION"):       # pretends the storage was registered inside a partition
        real_storages = main.storwize_storages
        main.storwize_storages = lambda sess: [dict(c, **{"sw-partition": os.environ["FAKE8_PARTITION"], "sw-snapmode": "volumegroup"})
                                               for c in real_storages(sess)]
    PVE_LOG = []

    def fake_call8(sess, method, endpoint, **p):
        if method == "GET":
            if endpoint.endswith("/status") and "/tasks/" in endpoint:
                return {"status": "stopped", "exitstatus": "OK"}
            if endpoint.endswith("/log") and "/tasks/" in endpoint:
                return [{"t": "TASK OK"}]
            return fake_call(sess, method, endpoint, **p)
        PVE_LOG.append((method, endpoint, p))
        print("fake pve", method, endpoint, p, flush=True)
        return "UPID:dev:0000:0000:0000:fake:0:dev@fixture:"

    main.pve.call = fake_call8
    main.pve.get = lambda sess, endpoint, **p: fake_call8(sess, "GET", endpoint, **p)
    main.pve.wait_task = lambda sess, node, upid, timeout=900: ("OK", ["TASK OK"])

    def fake_peer_state():
        inv = copy.deepcopy(main.dr.local_inventory() or {"version": 1, "storages": {}, "vms": []})
        for vm in inv["vms"][:1]:                 # the 1st VM becomes "VM 9100 from the other site"
            for d in vm["disks"]:
                d["volname"] = d["volname"].replace(f"vm-{vm['vmid']}-", "vm-9100-")
            vm["config"] = {k: v.replace(f"vm-{vm['vmid']}-", "vm-9100-") for k, v in vm["config"].items()}
            vm.update(vmid=9100, name="erp-production", node="pve-prod01")
        inv["vms"] = inv["vms"][:1]
        return {"inventory": inv, "fetched": int(time.time()) - 240, "error": ""}

    main.dr.peer = lambda: {"url": "https://production-panel:443", "fingerprint": "AA:" * 31 + "AA", "token": "x"}
    main.dr.peer_state = fake_peer_state
    main.dr.pull_peer = fake_peer_state
    main.collect_dr(force=True)

if __name__ == "__main__":
    uvicorn.run(main.app, host="127.0.0.1", port=int(sys.argv[2]) if len(sys.argv) > 2 else 8444, log_level="warning")
