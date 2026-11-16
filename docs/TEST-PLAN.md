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
| T06 | live migration | `qm migrate --online` | no error, device activated on the target | Passed (9.1.2, 2 nodes): 69–110 ms downtime under load |
| T07 (auto) | snapshot with the VM running + agent | `qm snapshot` | fs-freeze, FlashCopy `copying` | Passed: VM running (no agent) |
| T08 (auto) | rollback | `qm rollback` | snapshot data | Passed: data checked (md5) |
| T09 (auto) | delete the middle snapshot of 3 | `qm delsnapshot` | the other 2 remain valid | Passed |
| T10 (auto) | resize without and with a snapshot | `qm resize` | ok / "FlashCopy" error | Passed: online (+1G with the VM running) / refused with a snapshot |
| T11 (auto) | destroy a VM with snapshots | `qm destroy` | snapshots and volume removed | Passed: `qm destroy --purge`, 0 volumes/maps left |
| T12 | backup and restore through PBS | vzdump / qmrestore to this storage | ok | Passed (9.1.2, PBS 4.2) |
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
| T23 | volume group snapshot (8.5.1+) | `sw-snapmode volumegroup` | addsnapshot / restore / rmsnapshot | Passed (9.1.2): data checked (md5) |
| T24 | manual Safeguarded snapshot (8.5.2+) | panel › storage › Protection › Take snapshot | `lsvolumegroupsnapshot` shows `safeguarded yes` and the expiration; deleting is refused (CMMVC8851E) | Pending: needs an 8.x array |
| T25 | Safeguarded snapshot policy | panel › Protection › Snapshot policy | automatic snapshots appear with the "policy" tag | Passed (9.1.2): Safeguarded, deletion refused (CMMVC8851E) |
| T26 | volume group snapshot restore | panel › Protection › Restore (VM stopped) | disks go back to the snapshot state; VM starts | Passed (9.1.2), also from a Safeguarded snapshot |
| T27 | policy-based replication | panel › Protection › Replication policy | copy created on the DR array; state "Production", within the RPO | Pending: needs 2 arrays on 8.5.2+ |
| T28 | DR inventory between panels | DR page: token on production, peer on DR | production VMs listed in the DR panel | Pending: needs 2 sites (tested only with the simulator) |
| T29 | DR test (recovery test, 8.6.2+) | DR page › DR test / End test | VM `-drtest` starts with the network disconnected; on ending, the VM is gone and replication continues | Pending: needs 2 arrays on 8.6.2+ |
| T30 | failover and return | DR page › Failover; then Protection › Make production | VM recreated on DR over the copy; replication resumed in the reverse direction | Pending: needs 2 arrays on 8.5.2+ |
| T31 | protection of the recovery copy | on DR, delete a VM whose volume is in recovery mode | plugin refuses the `rmvdisk` | Pending: needs 2 arrays on 8.5.2+ |
| T32 | storage inside a partition (8.6.1+/9.x) | wizard › Storage partition | `sw-partition` saved; a new disk is created in the VM's volume group inside the partition and is mapped | Pending: needs a 9.x array |
| T33 | host created in the partition | panel › Hosts › create | `lshost` shows `partition_name` | Pending: needs a 9.x array |
| T34 | delete a VM in volume group mode | VM destroy | volumes and the volume group are gone (`rmvolumegroup`) | Passed (9.1.2): volumes to the trash, group removed |
| T35 | statistics collection on 9.1 (SCP over SFTP) | Performance page | per-volume charts keep arriving | Passed (9.1.2) |
| T36 | read-only recovery copy (9.1, partition) | DR node sees the disk before failover | multipath lists the volume; writes only after failover/test | Pending: needs 2 arrays on 9.1 |
| T37 | NVMe over TCP: new disk, expand, delete | storage with `sw-transport nvme-tcp` | namespace appears in `/dev/disk/by-id/nvme-*`, grows online and is gone on delete | Pending: needs an array with NVMe/TCP |
| T38 | NVMe over TCP: loss of one path | take down a data port | I/O continues over the other paths (`nvme list-subsys`) | Pending: needs an array with NVMe/TCP |
| T39 | new VM from a volume group snapshot | panel › Protection or VMs › New VM (thinclone / clone) | VM boots with the snapshot data; new MAC, UUID, cloud-init | Passed (9.1.2) |
| T40 | full clone from a snapshot | `qm clone --snapname` with the VM running | clone identical to the snapshot; temporary thinclone gone with the snapshot | Passed (9.1.2) |
| T41 | trash in volume group mode | remove a disk; restore from the trash | disk leaves the group, goes to the trash, returns to the group | Passed (9.1.2) |
| T42 | snapshot backup in volume group mode | `cbt_backup.py`, full then incremental on the other node | incremental reads only the changed blocks | Passed (9.1.2): 140 GiB full, 2 GiB incremental |
| T43 | migration during a snapshot backup | `qm migrate` while the backup runs | refused (`lock: backup`), backup completes | Passed (9.1.2) |
| T44 | backup bandwidth limit | `--bwlimit 30720` | upload at the limit | Passed: 30.4 MiB/s |
| T45 | FC path loss | take the active path offline under fio | failover under 1 s, no I/O error, return to the preferred path | Passed (9.1.2) |
| T46 | concurrency | backups, snapshots, migrations, clone, move and resize at the same time, fio with verification | every job ends, no data error | Passed (9.1.2), see docs/PERFORMANCE.md |
| T47 | snapshot policy from the panel | create with Safeguarded and assign to VMs; delete | policy on the array, snapshots on schedule | Passed (9.1.2) |
| T48 | panel update from the panel | Updates page › Update to (0.3.5 → 0.3.6, test release list) | code replaced in 3 s, configuration kept, previous version in `/opt/swpanel.old` | Passed |
| T49 | changed package | one byte changed, original signature | refused by the panel and by the root installer; nothing changed | Passed |
| T50 | new version that does not start | signed package that fails at start | after 60 s the previous version is back and answering | Passed |
| T51 | release without notes | release with package, empty notes | listed, not offered; `scripts/release.sh` refuses a version without a CHANGELOG section | Passed |
| T52 | appliance first boot | restore the image, open the console without pressing a key | address and code on the login screen | Passed (tty0 and tty1) |
| T53 | REST certificate pin | `rest_ua` with the right pin, a wrong pin and no pin, against the FS7600 | right: connects; wrong and none: refused (self-signed certificate) | Passed |
| T54 | panel CSRF and sign-in limits | POST /api/logout without the header; 22 wrong passwords | 403; 429 from the 9th attempt | Passed |
| T55 | build with pinned assets | build with the right sha512 and with a changed one | same assets as before; changed hash stops the build | Passed |
| T56 | update progress without a session | update a 0.3.6 container to 0.3.7 and poll /api/info without signing in | new version and update state visible during the restart | Passed |
| T57 | simultaneous backup limit | limit 1, three manual backups at once | one at a time, the others queued; queue time in the report | Passed |
| T58 | schedule with VMs at a time | limit 2, schedule of 3 VMs with 3 at a time | two run together, the third waits; all incremental | Passed |
| T59 | backup settings from the panel | Backups › Settings | written to every node (0600); out-of-range values refused | Passed |
| T60 | bottleneck analysis | full backup of 140 GiB already in the PBS (servidor02, 10 GbE) and of 20 GiB with the PBS as a local VM | proxy 82 % (hashing; 80 MiB new) / source 71 %; network empty for the local PBS | Passed |
| T61 | latency control: start | start limit below the array latency | backup queued with the reason; released when the limit goes back up | Passed |
| T62 | latency control: slow down | slow-down limit below the measured latency during a full backup | readers 4 → 2, wait recorded in the report | Passed |
| T63 | file restore from a Safeguarded snapshot | `file_restore.py start` on a policy snapshot of VM 9998; list, download a file and a folder | session in 5 s; ext4 tree listed; file and .zip match | Passed (9.1.2) |
| T64 | file restore: VM on the other node, two disks | snapshot of VM 9999 (running on servidor02) opened on servidor01 | both disks listed (drive-scsi0, drive-scsi1); disk without a file system shown empty | Passed |
| T65 | file restore: read-only | `blockdev --getro` on the thinclone device during a session | 1 on the multipath device and its paths; QEMU drive read-only | Passed |
| T66 | file restore: cleanup | close the session; kill the restore VM; leave it idle 10 min | thinclone group removed from the array each time | Passed |
| T67 | file restore: large download through the panel | 1 GiB and 3 GiB streamed over the panel SSH channel | complete and identical (sha256); no stop at 512 MiB | Passed |
| T68 | file restore: download in progress when the window closes | stop during a 285 MiB zip download | download completes; session and thinclone removed 5 s after | Passed |
| T69 | file restore: Windows NTFS and XFS guests | Restore files on a Windows VM and an XFS VM snapshot | trees listed, file downloaded | Pending: no such VMs in the lab |
| T70 | delete a disk mapped on two nodes | disk with a partition table (kpartx maps) active on both nodes; delete from one | map, partition maps and SCSI disks gone on both nodes; no failed paths | Passed |
| T71 | delete a disk open on the other node | hold the device open on node 2, delete from node 1 | delete stops before the unmap with "still in use on node(s)"; works once released | Passed |
| T72 | orphan cleanup | maps left by 0.4.0 deletes (15 + 9, some with partition maps) | listed by `stale-paths`, removed by the sweep; none in use touched; 0 failed paths after | Passed |
| T73 | new volume gets the SCSI LUN id of an orphan map | volume A active on node 1, unmapped and deleted on the array only; volume B created (got LUN 66, same as A) and activated | orphan map of A removed after 8 s, B active in 12 s | Passed |
| T74 | file changed on a node | append a line to StorwizePlugin.pm on node 2 | Proxmox log warning on node 2; `verify` shows "modified" on node 2 and "differs" on both, exit 1; all ok after restoring | Passed |
| T75 | panel shows changed files | node status with one plugin file changed on node 2 | page warning (servidor02 ≠ servidor01), node card "changed on the node", Prepare node opens with the plugin option checked | Passed |
| T76 | schedule by pool | pool virtcloud-backup with 2 VMs on node 2 | form lists the pool with its VM count; each node resolves the members and keeps only its own VMs; missing pool skipped with a message | Passed |
