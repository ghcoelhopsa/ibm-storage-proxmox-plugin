# Storage Virtualize 8.5.2+ and 9.1: Safeguarded snapshots, DR and storage partitions

Status: volume group snapshots, Safeguarded snapshots and snapshot policies validated on a
FlashSystem 7600, firmware 9.1.2, with a two-node Proxmox cluster (2026-11-13). Policy-based
replication and DR need a second array and are the next step; section 6 is the runbook.

## 1. Sources

| Source | Used for |
|---|---|
| Redpaper REDP-5737, *Data Resiliency Designs: A Deep Dive into IBM Storage Safeguarded Snapshots* | `addsnapshot -safeguarded -retentiondays`, restore, clone, roles (Security Administrator), CMMVC8851E |
| Redpaper REDP-5704, *Policy-Based Replication with IBM Storage FlashSystem...* (8.5.2) | partnership, `mkreplicationpolicy`, pool linking, `lsvolumegroupreplication`, `chvolumegroupreplication -mode` |
| Redbook SG24-8569, *Ensuring Business Continuity: ... Policy-Based Replication and Policy-Based HA* (8.7) | recovery test (`-startrecoverytest` / `-stoprecoverytest`, 8.6.2+), REST |
| Official Ansible collection `ibm.storage_virtualize` (modules `ibm_sv_manage_snapshot`, `ibm_svc_manage_volumegroup`, `ibm_sv_manage_snapshotpolicy`, `ibm_sv_manage_replication_policy`, `ibm_sv_switch_replication_direction`) | exact command and parameter names, minimum versions |

The command reference on ibm.com/docs could not be downloaded by script (HTTP 403); the syntax
below comes from the sources above. Confirm it in the documentation of the installed version before
validating.

## 2. Minimum versions

| Feature | Firmware | Where the code decides |
|---|---|---|
| Volume group | 8.3.0 | `sv.capabilities` |
| Volume group snapshot (`addsnapshot`) and snapshot policy | 8.5.1 | `sv8.caps8` |
| Safeguarded snapshot through `addsnapshot` | 8.5.2 | `sv8.caps8` |
| Policy-based replication (asynchronous) | 8.5.2 (Ansible mentions 8.5.2.1) | `sv8.caps8` |
| Recovery test (DR test without stopping replication) | 8.6.2 | `sv8.caps8` |

The **Protection** tab only appears with 8.5.1+; the **DR** page only appears when some storage has
policy-based replication, or when a token/peer has already been configured.

## 3. Commands used

Snapshots (per volume group; one volume group per VM, `<prefix>-vm-<vmid>`):

    lsvolumegroup
    lsvolumegroupsnapshot [-filtervalue volume_group_name=<vg>]
    lssnapshotpolicy / lssnapshotschedule / lsvolumegroupsnapshotpolicy
    addsnapshot -volumegroup <vg> -name <name> [-safeguarded -retentiondays <n>]
    restorefromsnapshot -snapshot <name> -volumegroup <vg>
    rmsnapshot -snapshot <name> -volumegroup <vg>
    chvolumegroup -snapshotpolicy <policy> [-safeguarded] <vg>
    chvolumegroup -nosnapshotpolicy <vg>

Policy-based replication:

    lspartnership / lsreplicationpolicy
    lsvolumegroupreplication <vg>
    chvolumegroup -replicationpolicy <policy> <vg>        (-noreplicationpolicy removes it)
    chvolumegroupreplication -mode independent <vg>       (failover; runs on the recovery system)
    chvolumegroupreplication -mode production <vg>        (resumes replication; runs on the system that becomes production)
    chvolumegroupreplication -startrecoverytest <vg>      (8.6.2+, on the recovery system)
    chvolumegroupreplication -stoprecoverytest <vg>

The panel **does not create** the partnership, the pool link, the replication policy or the snapshot
policy: that is done once in the array GUI (or CLI: `mkippartnership`/`mkfcpartnership`,
`chmdiskgrp -replicationpoollinkuid`, `mkreplicationpolicy -topology 2-site-async-dr ...`,
`mksnapshotpolicy -backupunit ... -backupinterval ... -retentiondays ...`). The panel only assigns
existing policies to the VMs' volume groups.

## 4. How it works

### Safeguarded snapshots
- Prerequisite: storage registered with `sw-snapmode volumegroup` (one volume group per VM).
- A Safeguarded snapshot cannot be deleted or changed before it expires, except by a
  Security Administrator of the array. The plugin user must be Administrator, never Security
  Administrator: that way not even a compromised panel can delete it. The panel translates the
  CMMVC8851E error.
- The VM **does not see** the snapshot: it cannot be mapped. There are two ways to use the content:
  restore in place (`restorefromsnapshot`, implemented, requires the VM to be stopped) or
  clone/thin clone to a new volume group
  (`mkvolumegroup -type thinclone -fromsourcegroup <vg> -snapshot <s>`, **not implemented**: the new
  volumes need to be renamed to the plugin's naming pattern).

### DR
- Replication belongs to the array. On the recovery system the volumes exist, but stay offline to
  the host until the volume group enters `independent` mode (or a recovery test).
- The Proxmox at the DR site needs the **configuration** of the VMs. The production panel stores
  the configuration of the VMs that have a disk on a plugin storage (read over SSH from the nodes;
  `cipassword` is discarded) and serves it at `GET /api/dr/export` with a token. The DR panel pulls
  it every 10 minutes, with the other panel's certificate pinned, and keeps the last good copy.
- **DR test**: `-startrecoverytest`, creates the VM with the same VMID, name `-drtest`, network with
  `link_down=1` and `onboot=0`. On ending: first `-stoprecoverytest` (the volumes go back to being
  the recovery copy), then it removes the VM. In that order, the array and the plugin refuse to
  delete the disks.
- **Failover**: `-mode independent` and creates the VM with the original configuration. Disks on
  storages that are not replicated (e.g. `local-lvm`) are left out and listed.
- **Return**: in the Protection tab of the site that should become production, "Make production and
  resume replication" (`-mode production`). The content on the other side is overwritten. Going back
  to the original site means repeating the process in the reverse direction; there is no single
  failback button.

## 5. Assumptions to confirm on hardware

1. Field names of `lsvolumegroupsnapshot` (`name`, `volume_group_name`, `time`,
   `expiration_time`, `safeguarded`, `auto_snapshot`) and of `lsvolumegroup`
   (`snapshot_policy_name`, `replication_policy_name`). The code tolerates missing fields, but the
   screen may end up with empty columns.
2. `lsvolumegroupsnapshot -filtervalue volume_group_name=` is accepted (used by the plugin).
3. On the recovery system, the volume group and the volumes have the **same name** as in
   production. Failover depends on this (the VM is recreated pointing to `vm-<vmid>-disk-N` on the
   local storage) and requires the same `sw-prefix` at both sites.
4. Field that indicates an active recovery test in `lsvolumegroupreplication` (the code looks for
   `recovery_test_active` / `recovery_test`).
5. `restorefromsnapshot` accepts `-snapshot <name> -volumegroup <vg>` (the form used by the Ansible
   module; the Redpaper shows `-snapshotid`).
6. Proxmox `POST /nodes/{node}/qemu` accepts attaching volumes that already exist on the plugin
   storage (`storage:vm-<vmid>-disk-N`) when the VMID is the same.
7. With volume group snapshots, the plugin can still expand a disk online and delete the VM
   (existing snapshots may block `rmvdisk`).
8. Mapping the recovery volumes to the hosts of the DR site is done by the plugin when the VM
   starts; confirm that mapping in `independent` mode/recovery test works as for an ordinary volume.

## 6. Validation runbook (new storage)

1. Register the storage in the panel with the "volume group" snapshot engine and an Administrator
   user.
2. T23: snapshot/rollback/delete from the Proxmox GUI with a test VM (VMID 9999).
3. T24 to T26: Protection tab: 1-day Safeguarded snapshot, try to delete it (must be refused),
   snapshot policy, restore with the VM stopped.
4. With the second storage: partnership, pool link and replication policy in the array GUI;
   assign the policy to the test VM (T27).
5. Second panel (one LXC per site): token on production, peer on DR (T28).
6. T29: DR test, check the data inside the `-drtest` VM, end it.
7. T30 and T31: failover with the source VM stopped, power it on at DR, resume replication.
8. Compare the real output of the `ls*` commands with `panel/dev/fake8.py` and adjust the field
   names.

Until the items in section 5 are confirmed, use it only with lab VMs.

## 7. Version 9.1 and storage partitions

Additional sources: Redbook SG24-8569 (edition revised for **9.1.3**), release notes 9.1.0, 9.1.2
and 9.1.3 (public.dhe.ibm.com) and the presentation *Storage Virtualize 9.1.0 Technical Update*.

### What changes in 9.1 and affects the plugin

| Change | Version | Handling |
|---|---|---|
| 9.1.0 is the LTS that merges 8.7.1 to 8.7.3 | 9.1.0 | detection by `code_level` already compares 9.x correctly |
| Native SCP discontinued (the array serves `scp` over SFTP) | 9.1.0 | statistics collection tries `scp -O` and retries without `-O` |
| Recovery copy **online and read-only** inside a partition | 9.1.0 | informational only in the Protection tab; failover and DR test do not change |
| Up to 32 partitions per system (4 on the FS5045); host cluster inside a partition | 9.1.0 | `sw-hostcluster` can be used with `sw-partition` (to be validated) |
| Management IP per partition (follows the partition on HA failover and on migration) | 9.1.0 | register the storage by this IP when using PBHA |
| Volumes addressable by UUID; renaming replicated objects | 9.1.0 | no impact (the plugin uses names) |
| `anomaly_sequence_number` field on the snapshot (ransomware detection) | 9.1.0 | **not displayed yet** |
| Snapshot restore to the source group of a thin clone | 9.1.0 | not used |
| vVols not supported | 9.1.1 | no impact |
| Port 8443 between partner systems is no longer required | 9.1.2 | array infrastructure |
| Synchronous PBR (previously asynchronous only) | 9.1.3 | it is just another policy; the panel assigns it by name |
| FS5015/5035 without replication | 8.7.1+ | the tab shows the policy list empty |

Known issue in 9.1.0/9.1.1 (release notes 9.1.2): `lsvdisk`/`lshost` **with a partition filter**,
outside the partition IP, can restart the config node. The code does not use this filter; it
filters in the panel.

### Storage partitions

Array rules: inside a partition every volume is in a volume group, and a host only receives
volumes from its own partition. A partition is mandatory for PBHA and is where 9.1 concentrates HA,
DR and migration between systems.

New plugin option: `sw-partition <name>` (8.6.1+, fixed after registration; requires
`sw-snapmode volumegroup`). With it:

    lspartition <p>                                           (exists and is not a draft)
    mkvolumegroup -name <prefix>-vm-<vmid> -partition <p>     (1st disk of the VM)
    mkvolume -name <vol> -pool <pool> -size <n> -unit b -volumegroup <vg> [-thin -buffersize 2%]
    mkvdiskhostmap ...                                        (only after the volume is in the group)
    mkhost ... -partition <p>                                 (hosts created by the panel)
    rmvolumegroup <vg>                                        (when deleting the last disk of the VM)

In the panel: the registration wizard offers the published partitions; the Protection tab shows the
policy, the link state and the active management system of the partition.

Assumptions to confirm (in addition to those in section 5):

9. `mkvolume` accepts `-thin -buffersize 2%` and `-iogrp` together with `-volumegroup`.
10. `mkvolumegroup -partition` and the `partition_name` field in `lsvolumegroup`.
11. Existing hosts join the partition through `chhost -partition` or through a draft partition; the
    plugin does not move hosts, it only checks/creates them.
12. In a partition with PBHA, the commands need to go to the active management system; hence the
    partition IP. There is no address-switching logic in the plugin.
13. PBHA failover (`chpartition -accessstalecopy`) is not implemented; the panel's failover
    covers DR per volume group (`chvolumegroupreplication`).
14. Expanding a replicated volume (allowed in 9.1.0) with the `expandvdisksize` the plugin uses.

## 8. Out of scope for this version

- Clone/thin clone from a snapshot (recover files without restoring the whole VM).
- Creating the partnership and policies from the panel.
- Legacy replication (Metro/Global Mirror, `mkrcrelationship`) and policy-based HA failover.
- Creating, splitting, merging or migrating partitions from the panel.
- Network mapping (bridge/VLAN) that differs between the sites, and changing the VMID on failover.
- Batch failover of several VMs and boot order.
