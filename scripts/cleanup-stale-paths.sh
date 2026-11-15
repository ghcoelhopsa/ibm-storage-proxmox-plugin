#!/bin/bash
# Removes from this node the multipath maps of IBM volumes that the array no longer maps to it
# (deleted or moved to the trash while this node was offline, or by a plugin older than 0.4.1).
# The plugin does the same by itself every 10 minutes; this is for doing it now.
#   ./scripts/cleanup-stale-paths.sh [--yes]
set -euo pipefail
[ "$(id -u)" -eq 0 ] || { echo "run as root" >&2; exit 1; }

pve-ibm-storage stale-paths
n=$(pve-ibm-storage --json stale-paths | perl -MJSON -0e \
    'print scalar(grep { $_->{state} eq "STALE" } @{decode_json(<STDIN>)})')
if [ "$n" -eq 0 ]; then
    echo "nothing to remove."
    exit 0
fi
if [ "${1:-}" != "--yes" ]; then
    read -r -p "remove the $n map(s) marked STALE? [y/N] " a
    [ "$a" = "y" ] || exit 0
fi
pve-ibm-storage stale-paths --remove
