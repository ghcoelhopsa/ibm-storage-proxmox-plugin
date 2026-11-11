#!/bin/bash
# Generates the SSH key (RSA 4096 - 7.8.1 does not accept ed25519) of a storage and,
# optionally, records the host key of the array in the shared known_hosts.
#   ./scripts/setup-ssh-key.sh <storeid> [storage-ip]
set -euo pipefail

STOREID="${1:?usage: $0 <storeid> [storage-ip]}"
ADDR="${2:-}"
[[ "$STOREID" =~ ^[A-Za-z][A-Za-z0-9_.-]*$ ]] || { echo "invalid storeid" >&2; exit 1; }

PRIV=/etc/pve/priv/storwize
KEY="$PRIV/${STOREID}_id_rsa"
mkdir -p "$PRIV"; chmod 700 "$PRIV" 2>/dev/null || true

if [ -e "$KEY" ]; then
    echo "key already exists: $KEY (not overwriting)"
else
    ssh-keygen -q -t rsa -b 4096 -m PEM -N "" -C "pveplugin@$(hostname)-$STOREID" -f "$KEY"
    echo "key created: $KEY"
fi
ssh-keygen -lf "$KEY.pub"
echo
echo "---- public key (add it to the storage user) ----------"
cat "$KEY.pub"
echo "---------------------------------------------------------"
echo "GUI: Access > Users > Create User (Administrator group) > SSH Public Key"
echo "CLI: scp ${KEY##*/}.pub superuser@<ip>:/tmp/ && svctask mkuser -name pveplugin -usergrp Administrator -keyfile /tmp/${KEY##*/}.pub"

if [ -n "$ADDR" ]; then
    KH="$PRIV/known_hosts"
    tmp=$(mktemp)
    ssh-keyscan -T 10 "$ADDR" 2>/dev/null | grep -v '^#' > "$tmp" || true
    [ -s "$tmp" ] || { echo "could not read the host key of $ADDR" >&2; rm -f "$tmp"; exit 1; }
    echo; echo "fingerprints of $ADDR (check them on the storage before trusting):"
    ssh-keygen -lf "$tmp"
    read -r -p "Save to $KH? [y/N] " a
    if [ "$a" = "y" ]; then
        touch "$KH"
        ssh-keygen -R "$ADDR" -f "$KH" >/dev/null 2>&1 || true
        cat "$tmp" >> "$KH"; chmod 600 "$KH"
        rm -f "$KH.old"
        echo "saved."
    fi
    rm -f "$tmp"
fi
