#!/bin/bash
# Root side of the panel self-update. Started by swpanel-update.path when the panel leaves
# /var/lib/swpanel/update/request (after downloading and checking the package).
#
# 1. checks the signature again with the public key in /opt/swpanel (root-owned: the panel user
#    cannot replace it), and the version inside the package
# 2. installs the Debian packages the new version lists, if any are missing
# 3. moves /opt/swpanel to /opt/swpanel.old and puts the new code in place, with its systemd units
# 4. restarts the panel and waits up to 60 s for it to answer with the new version
# 5. on any failure puts the previous code back and restarts it
#
# Configuration and data (/etc/swpanel, /var/lib/swpanel) are never touched.
set -u
U=/var/lib/swpanel/update
REQ=$U/request
LOG=$U/update.log
CUR=/opt/swpanel
NEW=/opt/swpanel.new
OLD=/opt/swpanel.old

[ -f "$REQ" ] || exit 0
exec >>"$LOG" 2>&1
echo "=== $(date -Is) update requested"

status() {   # state message [version]
    python3 -I - "$1" "$2" "${3:-${VER:-}}" <<'PY'
import json, os, sys, time
st, msg, ver = sys.argv[1:4]
p = "/var/lib/swpanel/update/status.json"
json.dump({"state": st, "message": msg, "version": ver, "time": int(time.time())}, open(p + ".tmp", "w"))
os.replace(p + ".tmp", p)
PY
    chown swpanel:swpanel "$U/status.json" 2>/dev/null
    echo "$(date -Is) $1: $2"
}

VER=$(python3 -I -c 'import json,sys,re; v=json.load(open(sys.argv[1])).get("version",""); print(v if re.match(r"^\d+\.\d+\.\d+$", v) else "")' "$REQ" 2>/dev/null)
rm -f "$REQ"
[ -n "$VER" ] || { status failed "invalid request"; exit 1; }
PKG=$U/swpanel-$VER.tgz
SIG=$PKG.sig
status installing "checking the package"

python3 -I - "$PKG" "$SIG" "$CUR/app/release-key.pem" <<'PY' || { status failed "the package signature is not valid; nothing was changed"; exit 1; }
import sys
from cryptography.hazmat.primitives.serialization import load_pem_public_key
pkg, sig, key = sys.argv[1:4]
load_pem_public_key(open(key, "rb").read()).verify(open(sig, "rb").read(), open(pkg, "rb").read())
PY

rm -rf "$NEW" && mkdir -p "$NEW" && tar --no-same-owner --no-same-permissions -xzf "$PKG" -C "$NEW" \
    || { rm -rf "$NEW"; status failed "could not unpack the package; nothing was changed"; exit 1; }
INNER=$(cat "$NEW/opt/swpanel/VERSION" 2>/dev/null)
[ "$INNER" = "$VER" ] || { rm -rf "$NEW"; status failed "the package carries version '$INNER', not $VER; nothing was changed"; exit 1; }
chmod -R u=rwX,go=rX "$NEW/opt/swpanel"

if [ -s "$NEW/opt/swpanel/packages.txt" ]; then
    PK=$(tr -s ' \n' ' ' < "$NEW/opt/swpanel/packages.txt")
    if ! dpkg -s $PK >/dev/null 2>&1; then
        status installing "installing Debian packages: $PK"
        DEBIAN_FRONTEND=noninteractive apt-get -qq update && DEBIAN_FRONTEND=noninteractive apt-get -qq install -y $PK \
            || { rm -rf "$NEW"; status failed "could not install $PK; nothing was changed"; exit 1; }
    fi
fi

status installing "replacing the code"
PREV=$(cat "$CUR/VERSION" 2>/dev/null || echo previous)
rm -rf "$OLD"
mv "$CUR" "$OLD" && mv "$NEW/opt/swpanel" "$CUR" && rm -rf "$NEW" \
    || { [ -d "$CUR" ] || mv "$OLD" "$CUR"; status failed "could not replace the code; the previous version stays"; exit 1; }

units() {   # install the systemd units shipped with the code in $1
    local changed=0 f
    for f in "$1"/systemd/*.service "$1"/systemd/*.path; do
        [ -f "$f" ] || continue
        cmp -s "$f" "/etc/systemd/system/$(basename "$f")" || { install -m 644 "$f" /etc/systemd/system/; changed=1; }
    done
    [ $changed = 1 ] && systemctl daemon-reload
    return 0
}
units "$CUR"

answers() {   # waits until the panel answers with version $1
    python3 -I - "$1" <<'PY'
import json, ssl, sys, time, urllib.request
want, ctx = sys.argv[1], ssl._create_unverified_context()
end = time.time() + 60
while time.time() < end:
    try:
        v = json.load(urllib.request.urlopen("https://127.0.0.1/api/info", context=ctx, timeout=5)).get("version")
        if v == want:
            sys.exit(0)
    except Exception:
        pass
    time.sleep(2)
sys.exit(1)
PY
}

status installing "restarting the panel"
systemctl restart swpanel
if answers "$VER"; then
    rm -f "$PKG" "$SIG"
    status done "version $VER installed (previous version $PREV kept in $OLD)"
    exit 0
fi

echo "$(date -Is) the new version did not answer; rolling back"
journalctl -u swpanel -n 30 --no-pager
rm -rf /opt/swpanel.failed
mv "$CUR" /opt/swpanel.failed && mv "$OLD" "$CUR"
units "$CUR"
systemctl restart swpanel
answers "$PREV" || true
status failed "version $VER did not start; version $PREV was put back (details in $LOG)"
exit 1
