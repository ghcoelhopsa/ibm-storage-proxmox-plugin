#!/bin/bash
# Collects information from the node (and optionally from the storage, read-only
# commands) for troubleshooting. Writes collect-<host>-<date>.txt (ignored by git).
#   ./scripts/collect-info.sh [storeid]
set -uo pipefail
ST="${1:-}"
if [ -n "$ST" ] && [[ ! "$ST" =~ ^[A-Za-z][A-Za-z0-9_.-]*$ ]]; then echo "invalid storage id: $ST" >&2; exit 1; fi
OUT="collect-$(hostname)-$(date +%Y%m%d-%H%M%S).txt"

# fixed command lines only; the storage id is checked above and passed as data, never as code
run() { echo; echo "### $*"; bash -c "$1" 2>&1; }
{
    run pveversion -v
    run "perl -MPVE::Storage -e 'print PVE::Storage::APIVER, q{ }, PVE::Storage::APIAGE, qq{\n}'"
    run "ls -l /usr/share/perl5/PVE/Storage/Custom/"
    run "grep -A15 '^storwize:' /etc/pve/storage.cfg"
    run "pvesm status"
    run "multipath -ll"
    run "cat /etc/multipath.conf"
    run "for h in /sys/class/fc_host/host*; do echo \$h \$(cat \$h/port_name) \$(cat \$h/port_state) \$(cat \$h/speed); done"
    run "iscsiadm -m session"
    run "lsblk -o NAME,SIZE,TYPE,VENDOR,MODEL,WWN"
    run "systemctl status multipathd --no-pager | head -5"
    run "pve-ibm-storage list"
    run "pve-ibm-storage stale-paths"
    if [ -n "$ST" ]; then
        echo; echo "### pve-ibm-storage info $ST"; pve-ibm-storage info "$ST" 2>&1
        echo; echo "### pve-ibm-storage inventory $ST"; pve-ibm-storage inventory "$ST" 2>&1
        echo; echo "### array (read-only listings)"
        ST="$ST" perl -MPVE::Storage -MPVE::Storage::Custom::StorwizePlugin -e '
            my $st = $ENV{ST}; my $s = PVE::Storage::config()->{ids}->{$st} or die "no storage $st\n";
            my $P = "PVE::Storage::Custom::StorwizePlugin";
            for my $c (qw(lssystem lsmdiskgrp lshost lshostcluster lsvdisk lsfcmap lslicense)) {
                print "-- $c\n"; my $r = eval { $P->can("sw_list")->($s, $st, $c, []) }; print $@ if $@;
                print join(":", map { $_ // "" } %$_), "\n" for @{ $r // [] };
            }' 2>&1
    fi
    run "journalctl -u pvedaemon -u pvestatd --since '-1h' --no-pager | grep -i storwize | tail -50"
} > "$OUT"
echo "written: $OUT  (review before sharing - it contains IPs and WWPNs)"
