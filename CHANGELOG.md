# Changelog

## 0.3.2-beta release - 2026-11-11
- English is now the source language of the code, comments, messages and documentation; the
  interface defaults to English, with Portuguese and Spanish as translations.
- Project renamed to IBM Storage Plugin Proxmox.
- Backup report keys are in English; reports written by older node tools are still read.
- Appliance image: a ready-made LXC container with the panel, published with the release. The
  first boot creates the certificate and the installation code.
- Static files are served with a version in the address and always in full.
- License: AGPL-3.0-or-later, the same as Proxmox VE.
- Snapshot backups, manual or scheduled, can be marked as protected on the PBS.
- Names no longer carry one product family: the CLI is `pve-ibm-storage` (was `pve-storwize`) and
  the snapshot backup tools, units and directories are `ibm-storage-cbt` (was `storwize-cbt`).
  "Prepare node" moves the backup state and removes the old command; schedules created before
  have to be created again.

- Panel version follows the plugin: 0.3.2. The appliance image carries the same number.
- Fix: the first opening of an unconfigured panel bounced between the sign-in and the initial setup
  pages, which could leave the page without its styles.
- Fix: the menu and pages could show without their layout when a stylesheet did not arrive. The
  panel now checks that both stylesheets applied before drawing and fetches a missing one again.
  File versions in the page address come from the file content, not its date, so a browser never
  reuses a copy from another panel at the same address.
- Fix: the "protected" option of a backup schedule was not saved (its checkbox shared an id with the
  PBS selector).
- A new or reinstalled panel takes the array key of each storage from Proxmox when a node is
  authorized or the storage is opened, so the key on the array does not change. Generating a new
  key shows a warning: the plugin cannot reach the array until the key is applied.
- Fix: a panel credential generated but not applied yet survives a restart of the panel and is
  offered again instead of being replaced.

## 0.3.2 (plugin) - 2026-11-11
- Two storage types in Proxmox with the same code: `storwize` (IBM Storwize and SVC) and `flashsystem`
  (IBM FlashSystem), the latter in `FlashSystemPlugin.pm`. The panel wizard picks one from the model
  the array reports. Existing storages do not change.
- Panel: Backups page (in progress, mounted snapshots, schedules by systemd timer, latest runs),
  option to power the VM back on after the rollback, and compact node card.

## 0.3.1 (plugin) / panel 0.3.0 - 2026-11-11
Preparation for Storage Virtualize 8.5.2+ (current FlashSystem models). **None of this was validated
on 8.x hardware**: the commands come from the IBM Redbooks and Ansible modules and were exercised only
against a simulator (`panel/dev/fake8.py`). Validation runbook in `docs/FS8X-SAFEGUARDED-DR.md`.
- Panel: **Protection** tab on the storage (shown with firmware 8.5.1+): volume group per VM, manual
  and Safeguarded snapshots (immutable, with retention), snapshot policy, restore and replication state.
- Panel: **DR** page: inventory of the VM configuration between two panels (token + pinned
  certificate), DR test (recovery test, 8.6.2+) and failover that recreates the VM over the replicated copy.
- Plugin: `free_image` refuses to delete the recovery copy of a volume replicated by policy.
- Plugin: listing of volume group snapshots fixed (`-filtervalue volume_group_name=`).
- Panel: **Domain and certificate** page: own certificate (PEM) or Let's Encrypt (certbot without
  root; validation over HTTP on port 80 or over DNS on Cloudflare) with automatic renewal 30 days
  before expiry. Port 80 now redirects to HTTPS. Real issuance not tested yet.
- **NVMe over TCP** (experimental, not validated): `sw-transport nvme-tcp` + `sw-portals`, native
  kernel multipath, namespace discovery by NGUID/UUID; node preparation step and NQN in the panel.
  Limitations in `docs/NVME-TCP.md` (LVM filter, snapshot backup and per-LUN statistics).
- Panel: the Hosts tab is now **read only** (creating a host, FC ports and linking are done on the
  storage) and became fast; the array is read in parallel with a reused SSH connection and a cache
  refreshed in the background; option of a self-signed certificate for a domain.
- Version 9.1 reviewed (Redbook 9.1.3 and release notes): `sw-partition` option (storage partition:
  `mkvolumegroup -partition`, `mkvolume -volumegroup`, `mkhost -partition`), partition in the wizard and
  in the Protection tab, statistics collection compatible with the SCP over SFTP of 9.1, and removal of
  the VM's volume group together with the last disk.

## 0.3.0 - 2026-11-10
- Consistent snapshots across the disks of a VM/CT (`sw-consistency`, on by default):
  FlashCopy consistency group per snapshot (`mkfcconsistgrp` + `startfcconsistgrp -prep`),
  validated on the V7000 7.8.1.6 (snapshot with the VM running, rollback, delete the middle snapshot, destroy).
- `sw-snapmode=volumegroup` (8.5.1+): one volume group per VM and `addsnapshot`/`rmsnapshot`/
  `restorefromsnapshot`. **Experimental**: commands as in the IBM white paper, not yet tested on 8.x.
- QoS validated on 7.8.1: IOPS/bandwidth limit per volume and per pool (`mkthrottle`).

## 0.2.1 - 2026-11-10
- Plugin: `on_delete_hook` refuses to remove the storage while any VM/CT (on any node, including
  snapshots, unused and vmstate) references its volumes. Proxmox does not do this check by itself.
- Plugin: accepts SSH key and host key through the API (`sw-sshprivkey`/`sw-sshhostkey`, sensitive).
- Plugin: `free_image` removes the volume's throttle (QoS) before `rmvdisk`.
- Plugin: compatible with taint mode (`pvedaemon -T`), which fixes resize from the GUI.
- Panel: IOPS and bandwidth limits per disk (QoS on the array via `mkthrottle` or QEMU in Proxmox) and per pool.
- Panel: host management per storage (create, add ports, link and unlink, host cluster).
- Panel/nodes: LVM filter so the host does not read the LVM inside the IBM volumes.

## 0.2.0 - 2026-11-10
- Dual backend with a single command layer: SSH/CLI (7.x and 8.x) and REST API (8.x, JWT,
  TLS fingerprint pinned at registration).
- Multiple storages: each `storwize:` entry has its own credentials, cache and prefix, and the plugin
  detects a prefix conflict on the same array.
- Features detected from the firmware (`lssystem`); `sw-volumegroup` available on 8.3+.
- `pve-ibm-storage` CLI: list, info, inventory, orphans and stale-paths, with `--json` output.
- `rename_volume` (`qm disk reassign`), `sparseinit` on thin volumes, `content rootdir`.
- `api()` validated against storage API 14 of PVE 9.2.

## 0.1.0
- Initial specification: SSH plugin for Storwize V7000 7.8.1, FlashCopy snapshots.
