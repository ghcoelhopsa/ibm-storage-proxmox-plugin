#!/bin/bash
# Publishes a release: panel package signed for the in-panel update, release notes from the
# CHANGELOG, optional appliance image.
#   scripts/release.sh <version> [--appliance <image.tar.zst>] [--dry-run]
#
# Refuses to go on when the CHANGELOG has no section for the version: every release carries its
# notes, and the panel shows them before updating.
# Signing key: $RELEASE_KEY (default /root/.config/ibm-storage-release/release-ed25519.pem). Its
# public half is panel/app/release-key.pem.
set -euo pipefail

VER="${1:?usage: $0 <version> [--appliance <image>] [--dry-run]}"; shift
APPL=""; DRY=0
while [ $# -gt 0 ]; do
    case "$1" in --appliance) APPL="$2"; shift 2;; --dry-run) DRY=1; shift;; *) echo "unknown option: $1" >&2; exit 1;; esac
done
REPO="$(cd "$(dirname "$0")/.." && pwd)"
KEY="${RELEASE_KEY:-/root/.config/ibm-storage-release/release-ed25519.pem}"
TAG="v$VER-beta"
OUT="$REPO/dist/$VER"

[[ "$VER" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "version must be N.N.N" >&2; exit 1; }
grep -q "^VERSION = \"$VER\"$" "$REPO/panel/app/main.py" || { echo "panel/app/main.py is not at version $VER" >&2; exit 1; }
grep -q "^our \$VERSION = '$VER';" "$REPO/src/PVE/Storage/Custom/StorwizePlugin.pm" || { echo "the plugin is not at version $VER" >&2; exit 1; }
[ -f "$KEY" ] || { echo "signing key $KEY not found" >&2; exit 1; }

mkdir -p "$OUT"
NOTES="$OUT/release-notes.md"
python3 -I - "$REPO/CHANGELOG.md" "$VER" "$NOTES" <<'PY'
import re, sys
text, ver, out = open(sys.argv[1]).read(), sys.argv[2], sys.argv[3]
m = re.search(rf"^## {re.escape(ver)}(?:-beta)?\b[^\n]*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
body = m.group(1).strip() if m else ""
if len(body) < 40:
    sys.exit(f"CHANGELOG.md has no notes for {ver}: write the '## {ver}-beta' section first")
open(out, "w").write(body + "\n\nFull history: [CHANGELOG.md](https://github.com/ghcoelhopsa/ibm-storage-proxmox-plugin/blob/main/CHANGELOG.md)\n")
PY

PKG="$OUT/swpanel-$VER.tgz"
"$REPO/scripts/build-panel-package.sh" "$PKG"
openssl pkeyutl -sign -inkey "$KEY" -rawin -in "$PKG" -out "$PKG.sig"
openssl pkeyutl -verify -pubin -inkey "$REPO/panel/app/release-key.pem" -rawin -in "$PKG" -sigfile "$PKG.sig" >/dev/null
(cd "$OUT" && sha256sum "swpanel-$VER.tgz" > "swpanel-$VER.tgz.sha256")
ASSETS=("$PKG" "$PKG.sig" "$PKG.sha256")
if [ -n "$APPL" ]; then
    (cd "$(dirname "$APPL")" && sha256sum "$(basename "$APPL")") > "$OUT/$(basename "$APPL").sha256"
    # the image carries the same code plus a whole system: signed with the same key as the package
    openssl pkeyutl -sign -inkey "$KEY" -rawin -in "$APPL" -out "$OUT/$(basename "$APPL").sig"
    openssl pkeyutl -verify -pubin -inkey "$REPO/panel/app/release-key.pem" -rawin -in "$APPL" -sigfile "$OUT/$(basename "$APPL").sig" >/dev/null
    ASSETS+=("$APPL" "$OUT/$(basename "$APPL").sha256" "$OUT/$(basename "$APPL").sig")
fi
echo "release $TAG:"; printf '  %s\n' "${ASSETS[@]}"; echo "  notes: $NOTES"
[ $DRY = 1 ] && { echo "dry run: nothing published"; exit 0; }
gh release create "$TAG" --target main --prerelease --title "$VER beta" --notes-file "$NOTES" "${ASSETS[@]}"
