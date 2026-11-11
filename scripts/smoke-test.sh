#!/bin/bash
# End-to-end test of the plugin on a storage, using VMID 9999 (which must not exist).
# CREATES and DELETES volumes and FlashCopy maps on the array - run only on a test/dedicated pool.
#   ./scripts/smoke-test.sh <storeid> [--yes]
set -uo pipefail

ST="${1:?usage: $0 <storeid> [--yes]}"
VMID=9999
VOL="vm-$VMID-disk-0"
VOLID="$ST:$VOL"
PASS=0; FAIL=0
TMPD=$(mktemp -d); trap 'rm -rf "$TMPD"' EXIT

[ -e "/etc/pve/qemu-server/$VMID.conf" ] || [ -e "/etc/pve/lxc/$VMID.conf" ] && {
    echo "VMID $VMID exists - aborting" >&2; exit 1; }

if [ "${2:-}" != "--yes" ]; then
    echo "This test creates and deletes vm-$VMID-* volumes and FlashCopy snapshots on '$ST'."
    read -r -p "Continue? [y/N] " a; [ "$a" = "y" ] || exit 1
fi

ok()   { echo "  [OK]   $*"; PASS=$((PASS+1)); }
bad()  { echo "  [FAIL] $*"; FAIL=$((FAIL+1)); }
step() { echo; echo "== $*"; }
# -T: pvedaemon (GUI/API) runs in taint mode - we test the same way
pv()   { perl -T -MPVE::Storage -e "\$ENV{PATH}='/usr/sbin:/usr/bin:/sbin:/bin'; delete @ENV{qw(IFS CDPATH ENV BASH_ENV)}; my \$c = PVE::Storage::config(); $1" 2>&1; }

step "T01 status"
pvesm status --storage "$ST" && ok "status" || bad "status"

step "T02 alloc 1G"
pvesm alloc "$ST" $VMID "$VOL" 1G && ok "alloc" || { bad "alloc"; exit 1; }
pvesm list "$ST" --vmid $VMID | grep -q "$VOL" && ok "shows up in list" || bad "list"

step "T03 activate + path"
pv "PVE::Storage::activate_volumes(\$c, ['$VOLID']); print scalar(PVE::Storage::path(\$c, '$VOLID')), qq{\n};" | tee /dev/stderr | tail -1 > $TMPD/dev
DEV=$(tail -1 $TMPD/dev); [ -b "$DEV" ] && ok "device $DEV" || { bad "device did not show up: $DEV"; }
SZ=$(blockdev --getsize64 "$DEV" 2>/dev/null); [ "$SZ" = "1073741824" ] && ok "size 1 GiB" || bad "size $SZ"

step "I/O"
head -c 8M /dev/urandom > $TMPD/a; head -c 8M /dev/urandom > $TMPD/b
dd if=$TMPD/a of="$DEV" bs=1M oflag=direct status=none && ok "write A"
SUM_A=$(md5sum < $TMPD/a | cut -c1-32)
R=$(dd if="$DEV" bs=1M count=8 iflag=direct status=none | md5sum | cut -c1-32)
[ "$R" = "$SUM_A" ] && ok "read matches" || bad "read does not match"

step "T07 snapshot s1 (data A)"
pv "PVE::Storage::volume_snapshot(\$c, '$VOLID', 's1');" && ok "snapshot s1" || bad "snapshot s1"
dd if=$TMPD/b of="$DEV" bs=1M oflag=direct status=none && ok "overwrite with B"

step "T08 rollback s1"
pv "PVE::Storage::volume_rollback_is_possible(\$c, '$VOLID', 's1'); PVE::Storage::volume_snapshot_rollback(\$c, '$VOLID', 's1');" \
    && ok "rollback" || bad "rollback"
R=$(dd if="$DEV" bs=1M count=8 iflag=direct status=none | md5sum | cut -c1-32)
[ "$R" = "$SUM_A" ] && ok "data is back to A" || bad "data after rollback does not match"

step "T09 three snapshots, delete the middle one"
pv "PVE::Storage::volume_snapshot(\$c, '$VOLID', 's2'); PVE::Storage::volume_snapshot(\$c, '$VOLID', 's3');" \
    && ok "s2 and s3" || bad "s2/s3"
pv "PVE::Storage::volume_snapshot_delete(\$c, '$VOLID', 's2');" && ok "deleted s2 (middle)" || bad "delete s2"

step "T10 resize with snapshot (error expected)"
OUT=$(pv "PVE::Storage::volume_resize(\$c, '$VOLID', 2*1024*1024*1024, 0);"); echo "  -> $OUT"
if echo "$OUT" | grep -q "FlashCopy"; then
    ok "resize refused with snapshot"
else
    bad "resize should have been refused"
fi

step "delete s1 and s3"
pv "PVE::Storage::volume_snapshot_delete(\$c, '$VOLID', 's1'); PVE::Storage::volume_snapshot_delete(\$c, '$VOLID', 's3');" \
    && ok "snapshots deleted" || bad "delete snapshots"

step "T10 resize without snapshot"
pv "PVE::Storage::volume_resize(\$c, '$VOLID', 2*1024*1024*1024, 0);" && ok "resize" || bad "resize"
SZ=$(blockdev --getsize64 "$DEV" 2>/dev/null); [ "$SZ" = "2147483648" ] && ok "device is 2 GiB" || bad "device is $SZ"

step "T11 free with a pending snapshot"
pv "PVE::Storage::volume_snapshot(\$c, '$VOLID', 'end');" && ok "snapshot 'end'"
pvesm free "$VOLID" && ok "free (removes snapshot + volume)" || bad "free"
[ -e "$DEV" ] && bad "local device still exists" || ok "local device removed"
pvesm list "$ST" --vmid $VMID | grep -q "$VOL" && bad "volume still listed" || ok "volume is gone"

echo; echo "== result: $PASS ok, $FAIL failure(s)"
[ $FAIL -eq 0 ]
