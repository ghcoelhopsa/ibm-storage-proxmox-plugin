#!/bin/bash
# Installs/updates the "IBM Storage Plugin Proxmox" panel in a Debian 13 LXC container.
# Run on the Proxmox node (root):  panel/deploy.sh <ctid> [--import-storage <storeid>]
#   --import-storage: copies the existing plugin key to the panel (migration of
#                     storages registered before the panel). Can be repeated.
set -euo pipefail

CT="${1:?usage: $0 <ctid> [--import-storage <storeid>]...}"; shift
IMPORT=(); AUTOCFG=0
while [ $# -gt 0 ]; do
    case "$1" in --import-storage) IMPORT+=("$2"); shift 2;; --auto-config) AUTOCFG=1; shift;; *) echo "unknown option: $1" >&2; exit 1;; esac
done

REPO="$(cd "$(dirname "$0")/.." && pwd)"
P="$REPO/panel"
VENDOR="${VENDOR:-$P/vendor-cache}"
CARBON_VER=1.118.0; PLEX_SANS=1.1.0; PLEX_MONO=2.5.0

pct status "$CT" | grep -q running || { echo "CT $CT is not running" >&2; exit 1; }

# --- Carbon and IBM Plex assets (downloaded once on the node, served locally) ---
if [ ! -f "$VENDOR/carbon.min.css" ]; then
    mkdir -p "$VENDOR/fonts"; tmp=$(mktemp -d)
    curl -fsSL "https://registry.npmjs.org/@carbon/styles/-/styles-$CARBON_VER.tgz" | tar xz -C "$tmp"
    # drop the @font-face rules that point to the IBM CDN (we use the local fonts)
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

# --- panel package ---
stage=$(mktemp -d); trap 'rm -rf "$stage"' EXIT
mkdir -p "$stage/opt/swpanel/assets" "$stage/opt/swpanel/static/vendor"
cp -r "$P/app" "$stage/opt/swpanel/"
cp "$P/setup-issue.sh" "$stage/opt/swpanel/"
cp -r "$P/static/." "$stage/opt/swpanel/static/"
cp -r "$VENDOR/." "$stage/opt/swpanel/static/vendor/"
cp "$REPO/src/PVE/Storage/Custom/StorwizePlugin.pm" "$REPO/src/PVE/Storage/Custom/FlashSystemPlugin.pm" "$REPO/bin/pve-ibm-storage" "$stage/opt/swpanel/assets/"
cp "$REPO/cbt/cbt_backup.py" "$REPO/cbt/cbt_job.py" "$REPO/cbt/cbt_sched.py" "$REPO/cbt/sw-snap.pl" "$stage/opt/swpanel/assets/"
find "$stage" -name __pycache__ -prune -exec rm -rf {} +
tar -C "$stage" -czf "$stage/panel.tgz" opt
pct push "$CT" "$stage/panel.tgz" /tmp/swpanel.tgz

NODE_IP=$(ip -4 -br addr show vmbr0 | awk '{print $3}' | cut -d/ -f1)
FP=$(openssl x509 -in /etc/pve/local/pve-ssl.pem -noout -fingerprint -sha256 | cut -d= -f2)
[ -f /etc/pve/local/pveproxy-ssl.pem ] && FP=$(openssl x509 -in /etc/pve/local/pveproxy-ssl.pem -noout -fingerprint -sha256 | cut -d= -f2)

pct exec "$CT" -- bash -s "$NODE_IP" "$FP" "$AUTOCFG" <<'EOS'
set -euo pipefail
NODE_IP="$1"; FP="$2"; AUTOCFG="$3"
export DEBIAN_FRONTEND=noninteractive
PKGS="python3-fastapi python3-uvicorn python3-requests python3-paramiko python3-cryptography openssh-client openssl certbot python3-certbot-dns-cloudflare"
dpkg -s $PKGS >/dev/null 2>&1 || { apt-get update -qq; apt-get install -y -qq $PKGS >/dev/null; }
systemctl disable --now certbot.timer >/dev/null 2>&1 || true      # renewal is done by the panel, without root
id swpanel >/dev/null 2>&1 || useradd --system --home /var/lib/swpanel --shell /usr/sbin/nologin swpanel
rm -rf /opt/swpanel.new && mkdir -p /opt/swpanel.new && tar -xzf /tmp/swpanel.tgz -C /opt/swpanel.new && rm /tmp/swpanel.tgz
rm -rf /opt/swpanel.old; [ -d /opt/swpanel ] && mv /opt/swpanel /opt/swpanel.old; mv /opt/swpanel.new/opt/swpanel /opt/swpanel; rm -rf /opt/swpanel.new /opt/swpanel.old
install -d -m 0750 -o root -g swpanel /etc/swpanel
install -d -m 0700 -o swpanel -g swpanel /var/lib/swpanel /var/lib/swpanel/keys
if [ "$AUTOCFG" = 1 ] && [ ! -f /etc/swpanel/config.json ] && [ ! -f /var/lib/swpanel/config.json ]; then
    printf '{"pve_host": "%s", "pve_port": 8006, "pve_fingerprint": "%s"}\n' "$NODE_IP" "$FP" > /etc/swpanel/config.json
fi
[ -f /etc/swpanel/config.json ] && { chgrp swpanel /etc/swpanel/config.json; chmod 640 /etc/swpanel/config.json; }
if [ ! -f /etc/swpanel/tls.key ]; then
    ip=$(hostname -I | awk '{print $1}')
    openssl req -x509 -newkey rsa:3072 -nodes -days 3650 -subj "/CN=$(hostname)" \
        -addext "subjectAltName=DNS:$(hostname),IP:$ip" -keyout /etc/swpanel/tls.key -out /etc/swpanel/tls.crt 2>/dev/null
fi
chgrp swpanel /etc/swpanel/tls.key /etc/swpanel/tls.crt; chmod 640 /etc/swpanel/tls.key
cat > /etc/systemd/system/swpanel.service <<'UNIT'
[Unit]
Description=IBM Storage Plugin Proxmox - web panel
After=network-online.target
Wants=network-online.target

[Service]
User=swpanel
Group=swpanel
WorkingDirectory=/opt/swpanel/app
ExecStartPre=+/opt/swpanel/setup-issue.sh
ExecStart=/usr/bin/python3 /opt/swpanel/app/run.py
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=/var/lib/swpanel
PrivateTmp=yes
Restart=always
RestartSec=1

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable -q swpanel
systemctl restart swpanel
EOS

# --- migration of existing storages (plugin key -> panel) ---
for st in "${IMPORT[@]}"; do
    k="/etc/pve/priv/storwize/${st}_id_rsa"
    [ -f "$k" ] || { echo "key $k does not exist" >&2; continue; }
    pct push "$CT" "$k" "/var/lib/swpanel/keys/${st}_id_rsa" --perms 600
    pct exec "$CT" -- chown swpanel:swpanel "/var/lib/swpanel/keys/${st}_id_rsa"
    addr=$(perl -MPVE::Storage -e "print PVE::Storage::config()->{ids}->{'$st'}->{'sw-address'}")
    grep "^$addr " /etc/pve/priv/storwize/known_hosts > /tmp/.kh.$$ || true
    pct push "$CT" /tmp/.kh.$$ /tmp/kh.import; rm -f /tmp/.kh.$$
    pct exec "$CT" -- bash -c "f=/var/lib/swpanel/array_known_hosts; touch \$f; grep -v '^$addr ' \$f > \$f.n || true; cat /tmp/kh.import >> \$f.n; mv \$f.n \$f; rm /tmp/kh.import; chown swpanel:swpanel \$f; chmod 600 \$f"
    user=$(perl -MPVE::Storage -e "print PVE::Storage::config()->{ids}->{'$st'}->{'sw-user'}")
    pct exec "$CT" -- python3 -c "import json,os; f='/var/lib/swpanel/arrays.json'; d=json.load(open(f)) if os.path.exists(f) else {}; d['$st']={'address':'$addr','user':'$user'}; json.dump(d,open(f,'w'))"
    pct exec "$CT" -- chown swpanel:swpanel /var/lib/swpanel/arrays.json
    echo "storage '$st' imported into the panel"
done

sleep 2
CTIP=$(pct exec "$CT" -- hostname -I | awk '{print $1}')
CODE=$(pct exec "$CT" -- cat /var/lib/swpanel/setup-code 2>/dev/null || true)
[ -n "$CODE" ] && echo "installation code (first setup): $CODE"
pct exec "$CT" -- systemctl is-active swpanel >/dev/null && echo "panel is up: https://$CTIP/" || { pct exec "$CT" -- journalctl -u swpanel -n 30 --no-pager; exit 1; }
