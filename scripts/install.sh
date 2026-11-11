#!/bin/bash
# Installs (or removes) the Storwize/FlashSystem plugin on this Proxmox VE node.
# Run it on EVERY node of the cluster.
#   ./scripts/install.sh            install/update
#   ./scripts/install.sh --uninstall remove the module (does not touch storage.cfg)
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "run as root" >&2; exit 1; }

REPO="$(cd "$(dirname "$0")/.." && pwd)"
DEST=/usr/share/perl5/PVE/Storage/Custom
MOD=StorwizePlugin.pm
PRIV=/etc/pve/priv/storwize

restart_services() {
    for s in pvedaemon pveproxy pvestatd pvescheduler; do
        systemctl try-restart "$s" 2>/dev/null || true
    done
}

if [ "${1:-}" = "--uninstall" ]; then
    if grep -q -E '^(storwize|flashsystem):' /etc/pve/storage.cfg 2>/dev/null; then
        echo "WARNING: there are still 'storwize'/'flashsystem' storages in /etc/pve/storage.cfg." >&2
        echo "         Remove them first (pvesm remove <id>), or PVE will complain about the unknown type." >&2
        read -r -p "Continue anyway? [y/N] " a; [ "$a" = "y" ] || exit 1
    fi
    rm -f "$DEST/$MOD" "$DEST/FlashSystemPlugin.pm" /usr/local/sbin/pve-ibm-storage /usr/local/sbin/pve-storwize
    restart_services
    echo "plugin removed (keys in $PRIV kept)"
    exit 0
fi

command -v pveversion >/dev/null || { echo "this host is not a Proxmox VE node" >&2; exit 1; }
echo "== $(pveversion)"
perl -MPVE::Storage -e 'printf "== storage API %d (age %d)\n", PVE::Storage::APIVER, PVE::Storage::APIAGE'

missing=()
for p in multipath-tools sg3-utils; do
    dpkg -s "$p" >/dev/null 2>&1 || missing+=("$p")
done
if [ ${#missing[@]} -gt 0 ]; then
    echo "ERROR: missing packages: ${missing[*]}  ->  apt install ${missing[*]}" >&2
    exit 1
fi
systemctl is-active -q multipathd || echo "WARNING: multipathd is not active (systemctl enable --now multipathd)"
[ -f /etc/multipath.conf ] || echo "WARNING: /etc/multipath.conf does not exist - see examples/multipath.conf.example"

perl -I"$REPO/src" -c "$REPO/src/PVE/Storage/Custom/$MOD"

install -d -m 0755 "$DEST"
install -m 0644 "$REPO/src/PVE/Storage/Custom/$MOD" "$DEST/$MOD"
install -m 0644 "$REPO/src/PVE/Storage/Custom/FlashSystemPlugin.pm" "$DEST/FlashSystemPlugin.pm"
install -m 0755 "$REPO/bin/pve-ibm-storage" /usr/local/sbin/pve-ibm-storage

if [ -d /etc/pve/priv ]; then
    mkdir -p "$PRIV"
    chmod 700 "$PRIV" 2>/dev/null || true
fi

# check that PVE loads the plugin without errors
out=$(perl -MPVE::Storage -e 'print PVE::Storage::Plugin->lookup("storwize") && PVE::Storage::Plugin->lookup("flashsystem") ? "ok\n" : "fail\n"' 2>&1)
echo "$out" | grep -q '^ok' || { echo "ERROR loading the plugin:"; echo "$out"; exit 1; }
echo "$out" | grep -v '^ok' || true

restart_services
echo "== plugin $(perl -MPVE::Storage -e 'print $PVE::Storage::Custom::StorwizePlugin::VERSION') installed in $DEST/$MOD"
echo "   next step: scripts/setup-ssh-key.sh <storeid>  and  pvesm add storwize <storeid> ..."
