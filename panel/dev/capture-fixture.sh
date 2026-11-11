#!/bin/bash
# Captures real responses (GET only) from the Proxmox API of this node for the devserver.
#   panel/dev/capture-fixture.sh output.json
set -euo pipefail
python3 -I - "${1:?usage: $0 output.json}" <<'PY'
import json, subprocess, sys
def g(p, **kw):
    args = ["pvesh", "get", p, "--output-format", "json"] + sum([[f"--{k}", str(v)] for k, v in kw.items()], [])
    return json.loads(subprocess.run(args, capture_output=True, text=True).stdout or "null")
fx = {"/storage": g("/storage"), "/cluster/resources": g("/cluster/resources", type="vm"),
      "/nodes": g("/nodes"), "/cluster/status": g("/cluster/status")}
for vm in fx["/cluster/resources"]:
    k = "qemu" if vm["type"] == "qemu" else "lxc"
    for sub in ("config", "snapshot"):
        p = f"/nodes/{vm['node']}/{k}/{vm['vmid']}/{sub}"; fx[p] = g(p)
for n in fx["/nodes"]:
    for st in fx["/storage"]:
        p = f"/nodes/{n['node']}/storage/{st['storage']}/status"; fx[p] = g(p)
json.dump(fx, open(sys.argv[1], "w"), indent=1)
print(f"{len(fx)} routes written to {sys.argv[1]}")
PY
