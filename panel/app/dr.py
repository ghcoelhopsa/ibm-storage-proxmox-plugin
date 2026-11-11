"""DR: inventory of the VMs (configuration) for the recovery site, and recreation
of a VM from the copy replicated by the array (policy-based replication, 8.5.2+).

The production panel keeps the configuration of the VMs that have a disk on a
`storwize` storage and hands it to the DR site panel (GET /api/dr/export, with a token).
The DR panel pulls this inventory periodically; if production goes down, it uses
the last copy to recreate the VMs on top of the replicated volumes.

NOT yet validated on 8.x hardware - see docs/FS8X-SAFEGUARDED-DR.md.
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import time

import requests

import store
from pve import _PinnedAdapter, fetch_fingerprint

INV_FILE = os.path.join(store.DATA_DIR, "dr-inventory.json")
PEER_FILE = os.path.join(store.DATA_DIR, "dr-peer.json")
PEER_INV_FILE = os.path.join(store.DATA_DIR, "dr-peer-inventory.json")
TOKEN_FILE = os.path.join(store.DATA_DIR, "dr-export-token.sha256")
URL_RE = re.compile(r"^https://([A-Za-z0-9][A-Za-z0-9_.-]{0,253})(?::(\d{1,5}))?/?$")
DISK_RE = re.compile(r"^(scsi|virtio|sata|ide|efidisk|tpmstate)\d+$")
NET_RE = re.compile(r"^net\d+$")
SECRET_KEYS = {"cipassword"}
DROP_RE = re.compile(r"^(digest|lock|parent|meta|vmstate|pending|snaptime|runningmachine|runningcpu|unused\d+)$")

# runs on the Proxmox nodes (read only)
EXPORT_SCRIPT = r"""echo '#@STORAGE'; cat /etc/pve/storage.cfg 2>/dev/null
for f in /etc/pve/nodes/*/qemu-server/*.conf; do [ -f "$f" ] || continue; echo "#@FILE $f"; cat "$f"; done"""


def _read(path, default=None):
    try:
        return json.load(open(path))
    except (OSError, ValueError):
        return default


def _write(path, data, mode=0o600):
    tmp = path + ".tmp"
    with open(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode), "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def parse_conf(text):
    """Current section of a qemu-server .conf (stops at the first snapshot section)."""
    cfg = {}
    for line in text.splitlines():
        if line.startswith("["):
            break
        if line.startswith("#") or ":" not in line:
            continue
        k, v = line.split(":", 1)
        if k.strip() in SECRET_KEYS:             # never leaves the site (e.g. cloud-init password)
            continue
        cfg[k.strip()] = v.strip()
    return cfg


def parse_storage_cfg(text):
    out, cur = {}, None
    for line in text.splitlines():
        m = re.match(r"^(\S+):\s*(\S+)\s*$", line)
        if m:
            cur = {"type": m.group(1)}
            out[m.group(2)] = cur
        elif cur is not None and line.strip():
            p = line.strip().split(None, 1)
            cur[p[0]] = p[1] if len(p) > 1 else "1"
    return {k: v for k, v in out.items() if v["type"] in ("storwize", "flashsystem")}


def disks_of(cfg):
    out = []
    for k, v in cfg.items():
        if not DISK_RE.match(k) or "media=cdrom" in v:
            continue
        vol = v.split(",", 1)[0]
        if ":" in vol:
            st, name = vol.split(":", 1)
            out.append({"key": k, "storage": st, "volname": name})
    return out


def build_inventory(output):
    """EXPORT_SCRIPT output -> inventory of the VMs with a disk on a storwize storage."""
    parts = re.split(r"^#@(STORAGE|FILE [^\n]*)\n", output, flags=re.M)
    storages, vms = {}, []
    for i in range(1, len(parts) - 1, 2):
        head, body = parts[i], parts[i + 1]
        if head == "STORAGE":
            storages = parse_storage_cfg(body)
            continue
        m = re.match(r"^FILE /etc/pve/nodes/([^/]+)/qemu-server/(\d+)\.conf$", head)
        if not m:
            continue
        cfg = parse_conf(body)
        disks = disks_of(cfg)
        if not any(d["storage"] in storages for d in disks):
            continue
        vms.append({"vmid": int(m.group(2)), "node": m.group(1), "name": cfg.get("name", ""), "config": cfg,
                    "disks": disks, "template": cfg.get("template") == "1"})
    return {"version": 1, "generated": int(time.time()),
            "storages": {k: {"address": v.get("sw-address", ""), "prefix": v.get("sw-prefix", "pve"), "pool": v.get("sw-pool", "")}
                         for k, v in storages.items()},
            "vms": sorted(vms, key=lambda v: v["vmid"])}


def save_inventory(inv):
    _write(INV_FILE, inv)


def local_inventory():
    return _read(INV_FILE)


# --- export token (production) -----------------------------------------------
def new_token():
    tok = secrets.token_urlsafe(32)
    with open(os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        f.write(hashlib.sha256(tok.encode()).hexdigest())
    return tok


def has_token():
    return os.path.exists(TOKEN_FILE)


def check_token(tok):
    try:
        want = open(TOKEN_FILE).read().strip()
    except OSError:
        return False
    return bool(tok) and hmac.compare_digest(want, hashlib.sha256(tok.encode()).hexdigest())


# --- peer (the DR site pulls from production) --------------------------------
def peer():
    return _read(PEER_FILE)


def peer_target(url):
    m = URL_RE.match(url or "")
    if not m:
        raise ValueError("invalid address (use https://host[:port])")
    return m.group(1), int(m.group(2) or 443)


def probe_peer(url):
    host, port = peer_target(url)
    return fetch_fingerprint(host, port)


def save_peer(url, token, fingerprint):
    host, port = peer_target(url)
    if not re.match(r"^[0-9A-Fa-f:]{64,95}$", fingerprint or ""):
        raise ValueError("invalid fingerprint")
    _write(PEER_FILE, {"url": f"https://{host}:{port}", "token": token, "fingerprint": fingerprint})


def remove_peer():
    for f in (PEER_FILE, PEER_INV_FILE):
        try:
            os.remove(f)
        except OSError:
            pass


def pull_peer():
    """Downloads the inventory from the production panel (certificate pinned by fingerprint)."""
    p = peer()
    if not p:
        return None
    state = _read(PEER_INV_FILE, {}) or {}
    try:
        hdr = {"authorization": "Bearer " + p["token"]}
        try:                                     # valid public certificate (e.g. Let's Encrypt, which changes on every renewal)
            r = requests.get(p["url"] + "/api/dr/export", headers=hdr, timeout=20)
        except requests.exceptions.SSLError:     # self-signed: the fingerprint checked at registration applies
            s = requests.Session()
            s.verify = False
            s.mount("https://", _PinnedAdapter(p["fingerprint"]))
            r = s.get(p["url"] + "/api/dr/export", headers=hdr, timeout=20)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}")
        inv = r.json()
        if inv.get("version") != 1 or not isinstance(inv.get("vms"), list):
            raise RuntimeError("unexpected response")
        state = {"inventory": inv, "fetched": int(time.time()), "error": ""}
    except Exception as e:                       # keep the last good copy: it is the one that counts in a disaster
        state["error"] = str(e)[:300]
        state["tried"] = int(time.time())
    _write(PEER_INV_FILE, state)
    return state


def peer_state():
    return _read(PEER_INV_FILE, {}) or {}


# --- recreating the VM at the DR site ----------------------------------------
def recover_params(vm, storage_map, test=False):
    """Parameters of POST /nodes/{node}/qemu to recreate the VM on top of the replicated volumes.

    storage_map: {production storage: local storage}. Disks on storages outside the
    map (e.g. local-lvm) were not replicated and are left out (returned in `skipped`)."""
    params, skipped = {}, []
    for k, v in vm["config"].items():
        if DROP_RE.match(k) or k in SECRET_KEYS:
            continue
        if DISK_RE.match(k):
            if "media=cdrom" in v:
                params[k] = "none,media=cdrom"
                continue
            vol, _, rest = v.partition(",")
            st, _, name = vol.partition(":")
            if st not in storage_map:
                skipped.append(f"{k} ({vol})")
                continue
            params[k] = f"{storage_map[st]}:{name}" + ("," + rest if rest else "")
            continue
        if NET_RE.match(k) and test:
            v = re.sub(r",?link_down=\d", "", v) + ",link_down=1"
        params[k] = v
    if test:
        params["name"] = (params.get("name") or f"vm{vm['vmid']}")[:50] + "-drtest"
        params["onboot"] = "0"
    for k in ("boot",):                          # boot order only with devices that exist
        if k in params:
            m = re.match(r"^order=(.*)$", params[k])
            if m:
                keep = [d for d in m.group(1).split(";") if d in params]
                params[k] = "order=" + ";".join(keep) if keep else None
                if params[k] is None:
                    del params[k]
    return params, skipped
