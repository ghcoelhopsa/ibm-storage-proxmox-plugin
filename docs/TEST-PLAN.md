# Test plan

Run in a dedicated pool, with the test VMID 9999. `scripts/smoke-test.sh <storeid>` automates
the tests marked with (auto).

| # | Test | How | Expected | Result |
|---|---|---|---|---|
| T01 (auto) | status | `pvesm status` | pool capacity, active | Passed 2026-11-10 |
| T02 (auto) | alloc/free | `pvesm alloc` / `pvesm free` | volume created and mapped / removed | Passed 2026-11-10 |
| T03 (auto) | activation | `activate_volumes` on each node | `/dev/disk/by-id/dm-uuid-mpath-3<uid>` appears | Passed (1 node) |
| T04 | disk from the GUI | VM → Hardware → Add Disk | volume `pve-vm-N-disk-M` on the array | Passed: `qm create --scsi0 v7k:1` |
| T05 | VM boot | VM with a disk on the storage | boots | Passed: VM 100 (Ubuntu 24.04) |
| T06 | live migration | `qm migrate --online` | no error, device activated on the target | Pending: needs a 2nd node |
| T07 (auto) | snapshot with the VM running + agent | `qm snapshot` | fs-freeze, FlashCopy `copying` | Passed: VM running (no agent) |
| T08 (auto) | rollback | `qm rollback` | snapshot data | Passed: data checked (md5) |
| T09 (auto) | delete the middle snapshot of 3 | `qm delsnapshot` | the other 2 remain valid | Passed |
| T10 (auto) | resize without and with a snapshot | `qm resize` | ok / "FlashCopy" error | Passed: online (+1G with the VM running) / refused with a snapshot |
| T11 (auto) | destroy a VM with snapshots | `qm destroy` | snapshots and volume removed | Passed: `qm destroy --purge`, 0 volumes/maps left |
| T12 | backup and restore through PBS | vzdump / qmrestore to this storage | ok | Pending |
| T13 | HA failover | power off a node | VM starts on the other node | Pending: needs a 2nd node |
| T14 | concurrent alloc on 2 nodes | 2× `pvesm alloc` at the same time | distinct names (cluster lock) | Pending: needs a 2nd node |
| T15 | storage unreachable | block the management interface | status inactive, without hanging pvestatd | Pending |
| T16 | REST (8.x) | `sw-api rest` | the same tests as above | Pending: no 8.x array |
| T17 | two storages on the same array | two `pvesm add` with the same prefix | the second is refused | Passed: refused |
| T18 | snapshot of a VM with 2 disks (consistency group) | `qm snapshot` (VM running and stopped) | 1 group `pve-vm-N_s_<snap>` with the 2 maps | Passed (7.8.1.6) |
| T19 | rollback and delete the middle snapshot with a group | `qm rollback` / `qm delsnapshot` | the 2 disks go back; the other groups stay | Passed (7.8.1.6) |
| T20 | QoS per volume and per pool | panel › I/O limits / Pool QoS | `lsthrottle` shows and removes | Passed (7.8.1.6) |
| T21 | QEMU limit (iops/mbps) | panel › I/O limits › Proxmox | option written to the disk | Passed (live effect not measured) |
| T22 | remove a storage in use | `pvesm remove` / GUI | refused, lists the VMs | Passed |
| T23 | volume group snapshot (8.5.1+) | `sw-snapmode volumegroup` | addsnapshot / restore / rmsnapshot | Pending: needs an 8.x array |
| T24 | manual Safeguarded snapshot (8.5.2+) | panel › storage › Protection › Take snapshot | `lsvolumegroupsnapshot` shows `safeguarded yes` and the expiration; deleting is refused (CMMVC8851E) | Pending: needs an 8.x array |
| T25 | Safeguarded snapshot policy | panel › Protection › Snapshot policy | automatic snapshots appear with the "policy" tag | Pending: needs an 8.x array |
| T26 | volume group snapshot restore | panel › Protection › Restore (VM stopped) | disks go back to the snapshot state; VM starts | Pending: needs an 8.x array |
| T27 | policy-based replication | panel › Protection › Replication policy | copy created on the DR array; state "Production", within the RPO | Pending: needs 2 arrays on 8.5.2+ |
| T28 | DR inventory between panels | DR page: token on production, peer on DR | production VMs listed in the DR panel | Pending: needs 2 sites (tested only with the simulator) |
| T29 | DR test (recovery test, 8.6.2+) | DR page › DR test / End test | VM `-drtest` starts with the network disconnected; on ending, the VM is gone and replication continues | Pending: needs 2 arrays on 8.6.2+ |
| T30 | failover and return | DR page › Failover; then Protection › Make production | VM recreated on DR over the copy; replication resumed in the reverse direction | Pending: needs 2 arrays on 8.5.2+ |
| T31 | protection of the recovery copy | on DR, delete a VM whose volume is in recovery mode | plugin refuses the `rmvdisk` | Pending: needs 2 arrays on 8.5.2+ |
| T32 | storage inside a partition (8.6.1+/9.x) | wizard › Storage partition | `sw-partition` saved; a new disk is created in the VM's volume group inside the partition and is mapped | Pending: needs a 9.x array |
| T33 | host created in the partition | panel › Hosts › create | `lshost` shows `partition_name` | Pending: needs a 9.x array |
| T34 | delete a VM in volume group mode | VM destroy | volumes and the volume group are gone (`rmvolumegroup`) | Pending: needs an 8.x/9.x array |
| T35 | statistics collection on 9.1 (SCP over SFTP) | Performance page | per-volume charts keep arriving | Pending: needs a 9.1 array |
| T36 | read-only recovery copy (9.1, partition) | DR node sees the disk before failover | multipath lists the volume; writes only after failover/test | Pending: needs 2 arrays on 9.1 |
| T37 | NVMe over TCP: new disk, expand, delete | storage with `sw-transport nvme-tcp` | namespace appears in `/dev/disk/by-id/nvme-*`, grows online and is gone on delete | Pending: needs an array with NVMe/TCP |
| T38 | NVMe over TCP: loss of one path | take down a data port | I/O continues over the other paths (`nvme list-subsys`) | Pending: needs an array with NVMe/TCP |
