#!/bin/bash
# Removes from this node the multipath maps / SCSI devices of IBM volumes that no
# longer exist on any registered array (e.g. a volume deleted from ANOTHER node -
# free_image only cleans the node it ran on). Asks for confirmation for each map.
#   ./scripts/cleanup-stale-paths.sh [--yes]
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "run as root" >&2; exit 1; }

YES="${1:-}"
mapfile -t STALE < <(pve-ibm-storage --json stale-paths | perl -MJSON -0e \
    'for (@{decode_json(<STDIN>)}) { print "$_->{map} $_->{paths}\n" if $_->{state} eq "STALE" }')

if [ ${#STALE[@]} -eq 0 ]; then
    echo "no stale maps."
else
    for line in "${STALE[@]}"; do
        read -r map paths <<<"$line"
        echo "stale map: $map (paths: ${paths:-none})"
        if [ "$YES" != "--yes" ]; then
            read -r -p "  remove? [y/N] " a; [ "$a" = "y" ] || continue
        fi
        multipath -f "$map" || { echo "  could not remove $map (in use?)"; continue; }
        for sd in ${paths//,/ }; do
            [ -e "/sys/block/$sd/device/delete" ] && echo 1 > "/sys/block/$sd/device/delete"
        done
        multipath -w "$map" >/dev/null 2>&1 || true
        echo "  removed."
    done
fi

# SCSI LUNs with no map at all (dead paths) - optional, with rescan-scsi-bus from sg3-utils
if command -v rescan-scsi-bus.sh >/dev/null; then
    echo
    echo "To remove orphan SCSI LUNs (not mapped on the storage) run, with care:"
    echo "  rescan-scsi-bus.sh -r      # -r removes devices that are gone from the storage"
fi
