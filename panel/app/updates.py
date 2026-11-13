"""Panel self-update.

The panel lists the newer releases (with their release notes), downloads the panel package of the
chosen one, checks its Ed25519 signature and leaves a request for the root helper
(/opt/swpanel/update.sh, started by swpanel-update.path). The helper checks the signature again
with the public key installed in /opt/swpanel (root-owned), swaps the code, restarts the panel and
rolls back if the new version does not answer. Configuration and data in /etc/swpanel and
/var/lib/swpanel are not touched.

Release assets: swpanel-<version>.tgz and swpanel-<version>.tgz.sig. A release without notes or
without both assets is listed but cannot be installed.
"""
import json
import os
import re
import tarfile
import time

import requests

import store

RELEASES_URL = "https://api.github.com/repos/ghcoelhopsa/ibm-storage-proxmox-plugin/releases"
HERE = os.path.dirname(os.path.abspath(__file__))
PUBKEY = os.path.join(HERE, "release-key.pem")
CHANGELOG = os.path.join(HERE, "..", "CHANGELOG.md")
UPD_DIR = os.path.join(store.DATA_DIR, "update")
REQUEST = os.path.join(UPD_DIR, "request")
STATUS = os.path.join(UPD_DIR, "status.json")
CHECK = os.path.join(UPD_DIR, "check.json")
# Optional override of the release list (a local mirror, or a test server). Root-owned file,
# so the panel itself cannot point the updates somewhere else.
SOURCE_FILE = os.path.join(store.CONF_DIR, "update-source")
MAX_PKG = 64 * 1024 * 1024


class UpdateError(Exception):
    pass


def vtuple(v):
    m = re.match(r"^v?(\d+)\.(\d+)\.(\d+)", str(v or ""))
    return tuple(int(x) for x in m.groups()) if m else None


def source():
    try:
        s = open(SOURCE_FILE).readline().strip()
        if re.match(r"^https?://\S+$", s):
            return s
    except OSError:
        pass
    return RELEASES_URL


def _get(url, **kw):
    r = requests.get(url, timeout=kw.pop("timeout", 30), headers={"Accept": "application/vnd.github+json",
                     "User-Agent": "ibm-storage-plugin-proxmox-panel"}, **kw)
    r.raise_for_status()
    return r


def check(current):
    """Newer releases, oldest first, each with its notes. Saved for /api/me and the page."""
    os.makedirs(UPD_DIR, mode=0o700, exist_ok=True)
    cur = vtuple(current)
    out = {"checked": int(time.time()), "source": source(), "current": current, "releases": [], "error": ""}
    try:
        data = _get(source()).json()
    except (requests.RequestException, ValueError) as e:
        out["error"] = f"could not read the release list: {e}"
        _save(CHECK, out)
        return out
    for r in data if isinstance(data, list) else []:
        v = vtuple(r.get("tag_name"))
        if r.get("draft") or not v or not cur or v <= cur:
            continue
        ver = ".".join(map(str, v))
        assets = {a.get("name"): a for a in r.get("assets") or []}
        pkg, sig = assets.get(f"swpanel-{ver}.tgz"), assets.get(f"swpanel-{ver}.tgz.sig")
        notes = (r.get("body") or "").strip()
        out["releases"].append({
            "version": ver, "tag": r.get("tag_name"), "name": r.get("name") or r.get("tag_name"),
            "notes": notes, "published": r.get("published_at") or "", "prerelease": bool(r.get("prerelease")),
            "url": r.get("html_url") or "",
            "pkg_url": pkg.get("browser_download_url") if pkg else "", "sig_url": sig.get("browser_download_url") if sig else "",
            "size": int(pkg.get("size") or 0) if pkg else 0,
            "installable": bool(pkg and sig and notes),
            "why": "" if notes else "the release has no notes",
        })
        if notes and not (pkg and sig):
            out["releases"][-1]["why"] = "the release has no panel package"
    out["releases"].sort(key=lambda r: vtuple(r["version"]))
    _save(CHECK, out)
    return out


def cached():
    try:
        return json.load(open(CHECK))
    except (OSError, ValueError):
        return {}


def latest_installable(c):
    rel = [r for r in c.get("releases", []) if r.get("installable")]
    return rel[-1] if rel else None


def verify(pkg_path, sig_path, pubkey=PUBKEY):
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.serialization import load_pem_public_key
    key = load_pem_public_key(open(pubkey, "rb").read())
    try:
        key.verify(open(sig_path, "rb").read(), open(pkg_path, "rb").read())
    except InvalidSignature:
        raise UpdateError("the package signature is not valid: it was not published by this project")


def package_version(pkg_path):
    with tarfile.open(pkg_path, "r:gz") as t:
        f = t.extractfile("opt/swpanel/VERSION")
        return f.read().decode().strip() if f else ""


def download_and_request(rel, user):
    """Download, check, and hand over to the root helper. Raises UpdateError."""
    if not rel.get("installable"):
        raise UpdateError(f"version {rel.get('version')} cannot be installed: {rel.get('why') or 'no package'}")
    st = status()
    if st.get("state") in ("queued", "installing") and time.time() - st.get("time", 0) < 600:
        raise UpdateError("an update is already running")
    os.makedirs(UPD_DIR, mode=0o700, exist_ok=True)
    ver = rel["version"]
    pkg, sig = os.path.join(UPD_DIR, f"swpanel-{ver}.tgz"), os.path.join(UPD_DIR, f"swpanel-{ver}.tgz.sig")
    for url, dst in ((rel["pkg_url"], pkg), (rel["sig_url"], sig)):
        try:
            r = _get(url, stream=True, timeout=120)
            n = 0
            with open(dst + ".part", "wb") as f:
                for chunk in r.iter_content(1 << 16):
                    n += len(chunk)
                    if n > MAX_PKG:
                        raise UpdateError("the package is larger than expected")
                    f.write(chunk)
        except requests.RequestException as e:
            raise UpdateError(f"download failed: {e}")
        os.replace(dst + ".part", dst)
    verify(pkg, sig)
    inner = package_version(pkg)
    if inner != ver:
        raise UpdateError(f"the package carries version {inner or '?'}, not {ver}")
    _save(STATUS, {"state": "queued", "version": ver, "message": "waiting for the installer", "time": int(time.time()),
                   "user": user})
    _save(REQUEST, {"version": ver, "user": user, "time": int(time.time())})
    return {"version": ver}


def status():
    try:
        return json.load(open(STATUS))
    except (OSError, ValueError):
        return {}


def changelog_section(version):
    """Notes of one version from the CHANGELOG shipped with the panel."""
    try:
        text = open(CHANGELOG).read()
    except OSError:
        return ""
    m = re.search(rf"^## {re.escape(version)}(?:-beta)?\b[^\n]*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    return m.group(1).strip() if m else ""


def _save(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
