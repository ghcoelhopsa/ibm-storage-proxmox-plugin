#!/bin/bash
# Builds the panel package: the /opt/swpanel tree used by panel/deploy.sh and by the in-panel
# update (release asset swpanel-<version>.tgz).
#   scripts/build-panel-package.sh <output.tgz>
# Downloads the Carbon and IBM Plex assets once into panel/vendor-cache (or $VENDOR).
set -euo pipefail

OUTF="${1:?usage: $0 <output.tgz>}"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
P="$REPO/panel"
VENDOR="${VENDOR:-$P/vendor-cache}"
CARBON_VER=1.118.0; PLEX_SANS=1.1.0; PLEX_MONO=2.5.0
VERSION=$(sed -n 's/^VERSION = "\([0-9.]*\)"$/\1/p' "$P/app/main.py")
[ -n "$VERSION" ] || { echo "VERSION not found in panel/app/main.py" >&2; exit 1; }

if [ ! -f "$VENDOR/carbon.min.css" ]; then
    mkdir -p "$VENDOR/fonts"; tmp=$(mktemp -d)
    curl -fsSL "https://registry.npmjs.org/@carbon/styles/-/styles-$CARBON_VER.tgz" | tar xz -C "$tmp"
    # drop the @font-face rules that point to the IBM CDN (the panel serves the fonts itself)
    python3 -I -c 'import re,sys; s=open(sys.argv[1]).read(); open(sys.argv[2],"w").write(re.sub(r"@font-face\{[^}]*\}","",s))' \
        "$tmp/package/css/styles.min.css" "$VENDOR/carbon.min.css"
    mkdir -p "$tmp/s" "$tmp/m"
    curl -fsSL "https://registry.npmjs.org/@ibm/plex-sans/-/plex-sans-$PLEX_SANS.tgz" | tar xz -C "$tmp/s"
    curl -fsSL "https://registry.npmjs.org/@ibm/plex-mono/-/plex-mono-$PLEX_MONO.tgz" | tar xz -C "$tmp/m"
    for w in Light Regular SemiBold; do cp "$tmp/s/package/fonts/complete/woff2/IBMPlexSans-$w.woff2" "$VENDOR/fonts/"; done
    cp "$tmp/m/package/fonts/complete/woff2/IBMPlexMono-Regular.woff2" "$VENDOR/fonts/"
    cp "$tmp/package/LICENSE" "$VENDOR/LICENSE-carbon" 2>/dev/null || true
    rm -rf "$tmp"
fi

stage=$(mktemp -d); trap 'rm -rf "$stage"' EXIT
O="$stage/opt/swpanel"
mkdir -p "$O/assets" "$O/static/vendor" "$O/systemd"
cp -r "$P/app" "$O/"
cp "$P/setup-issue.sh" "$P/update.sh" "$O/"
cp "$P/systemd/"* "$O/systemd/"
cp -r "$P/static/." "$O/static/"
cp -r "$VENDOR/." "$O/static/vendor/"
cp "$REPO/src/PVE/Storage/Custom/StorwizePlugin.pm" "$REPO/src/PVE/Storage/Custom/FlashSystemPlugin.pm" "$REPO/bin/pve-ibm-storage" "$O/assets/"
cp "$REPO/cbt/cbt_backup.py" "$REPO/cbt/cbt_job.py" "$REPO/cbt/cbt_sched.py" "$REPO/cbt/sw-snap.pl" "$O/assets/"
cp "$REPO/CHANGELOG.md" "$O/"
echo "$VERSION" > "$O/VERSION"
# Debian packages the panel needs (the updater installs the missing ones)
echo "python3-fastapi python3-uvicorn python3-requests python3-paramiko python3-cryptography openssh-client openssl certbot python3-certbot-dns-cloudflare" > "$O/packages.txt"
chmod 755 "$O/update.sh" "$O/setup-issue.sh"
find "$stage" -name __pycache__ -prune -exec rm -rf {} +
find "$stage" -name '*.0.3.3' -delete
tar -C "$stage" --owner=0 --group=0 --sort=name -czf "$OUTF" opt
echo "$OUTF: panel $VERSION, $(du -h "$OUTF" | cut -f1)"
