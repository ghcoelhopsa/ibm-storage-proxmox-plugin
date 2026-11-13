# Changelog

## 0.3.7-beta - 2026-11-13
- Panel update: the progress window no longer stays on "waiting for the installer". The panel
  restarts to install the update and the sessions do not survive it; the window now follows the
  update through the public status of the panel and, when the new version answers, reloads the
  Updates page (sign in again if asked). A failed update is shown the same way.
- The progress window runs the code of the version being replaced, so the fix applies to updates
  from 0.3.7 on. Updating 0.3.6 to 0.3.7 can still leave the window waiting: close it and sign in.

## 0.3.6-beta - 2026-11-13
- First boot of the appliance: the container console shows the panel address and the installation
  code on the login screen as soon as the panel writes them, without logging in. Before, the login
  screen was drawn before the code existed and showed it only after pressing Enter.
- When the panel is configured, the login screen goes back to the standard one at once.
- README: how to read the code from the node with `pct exec <ctid> -- cat /var/lib/swpanel/setup-code`.

Security (audit of 2026-11-13)
- REST: the pinned certificate fingerprint is now enforced. Before, the TLS library ignored the pin
  because verification was off, and any certificate was accepted.
- REST without a pin requires a certificate valid under the system CAs; the array password never
  goes over an unverified connection. A storage that moves from SSH to REST (password added later)
  gets its certificate pinned at that moment, as when it is added.
- Appliance image signed with the release key; the README checks the signature before `pct restore`.
- Carbon and IBM Plex tarballs pinned by sha512 and extracted only after the check, rejecting
  absolute or `..` paths.
- Panel: sign-out requires the CSRF header; failed sign-ins limited per address and per account;
  the realm list is cached and counts against the sign-in limit; expired sessions and old failed
  sign-ins are pruned every 20 seconds.
- Scripts: storage ids validated and values passed as data in `panel/deploy.sh` and
  `scripts/collect-info.sh` (no `eval`); the plugin validates the storage id in the password path;
  the panel quotes every argument of the snapshot backup helper.
- README: threat model and the array role (Administrator, never Security Administrator).
- SECURITY.md with private reporting and the release key fingerprint; CI with shellcheck, bandit,
  Perl, Python and JavaScript syntax and a secret pattern scan; Dependabot for the workflow actions.
- Lab addresses in the documentation replaced by documentation ranges (RFC 5737).

## 0.3.5-beta - 2026-11-13
- Panel update from the panel: the new **Updates** page lists the newer releases with their notes
  and installs the chosen one with one click. Only the panel code is replaced; the container, its
  configuration, the array keys and the history stay.
- Every update package is signed (Ed25519). The panel and the installer both check the signature
  with the public key shipped in the panel; an unsigned or changed package is refused.
- The installer keeps the previous version and puts it back by itself when the new one does not
  answer within 60 seconds.
- A release without notes is not offered for installation. `scripts/release.sh` builds and signs
  the package and takes the release notes from this file; it stops when the version has no section.
- The panel checks for updates once a day and shows a badge on the menu when one is available.
- After the panel, each node shows the new plugin version on the Proxmox nodes page, installed with
  Update node as before.
- `panel/deploy.sh` and the in-panel update install the same package
  (`scripts/build-panel-package.sh`).

## 0.3.4-beta - 2026-11-13
Validated on an IBM FlashSystem 7600 (firmware 9.1.2) with a two-node Proxmox VE 9.2 cluster.
Measured numbers: docs/PERFORMANCE.md.

Snapshots and recovery (volume group mode, 8.5+/9.x)
- New VM from a volume group snapshot, as a thinclone (immediate, shares data with the snapshot) or
  a clone (immediate, the array copies the data in the background). Panel: Protection tab and the
  VMs page.
- Full clone from a snapshot in Proxmox (`qm clone --snapname`): the snapshot is read through a
  temporary thinclone, removed with the snapshot or after a day.
- Snapshot policies in the panel: create (frequency, retention, start time, Safeguarded), assign
  to several VMs at once, delete. The next policy snapshot of each VM is shown.
- The VMs page lists the array snapshots of each VM, including the ones taken by a policy and the
  Safeguarded ones, with restore over the VM and new VM from the snapshot.
- Anomaly status of a snapshot (9.x threat detection) is shown when the array reports one.
- Snapshot names up to 63 characters (policy snapshots are named `snapshot-<UUID>`).
- Errors of the Security Administrator role (CMMVC8851E, CMMVC8853E) are explained in the panel.

Disks
- Trash also in volume group mode: the disk leaves the VM's volume group and goes to the trash;
  restoring it puts it back in the group.
- Thin volumes are created without the per-volume space alert at 80 % of their size; the pool alert
  is the one that matters with autoexpand.
- Temporary thinclones are never listed as disks of a VM.

Backup from the array snapshot
- Volume group mode: `addsnapshot` of the VM's group and a thinclone mapped only to the node that
  runs the backup. Works with host clusters.
- The VM is locked (`lock: backup`) during the backup, as with vzdump.
- Backup state in `/etc/pve`: incremental on any node after a live migration.
- Follows the Proxmox backup bandwidth limit (vzdump.conf, datacenter, storage) or `--bwlimit`;
  only data actually sent to PBS counts.
- Bitmaps left by an interrupted run are merged into the next one.

Panel
- Latency in microseconds in every chart, with the unit on the axis.
- FC optical levels of Emulex HBAs (`lpfc_xcvr_data`), besides QLogic.
- Direct-attach links of 8.x/9.x arrays are matched to the node HBA by the fabric name.
- Policy list of a volume group read correctly on 9.1 (`name` column).


## 0.3.3-beta - 2026-11-11
- Trash: a disk removed in Proxmox (including the disks of a destroyed VM) is unmapped and renamed
  `<prefix>-trash<time>-<volume>` instead of deleted, and kept for `sw-trash-days` (default 7).
  The panel has a Trash tab with restore and delete; the CLI has `trash`, `trash-restore`,
  `trash-delete` and `trash-purge`.
- Fix: the first opening of an unconfigured panel bounced between the sign-in and the initial setup
  pages, which could leave the page without its styles.
- Fix: the menu and pages could show without their layout when a stylesheet did not arrive. The
  panel now checks that both stylesheets applied before drawing and fetches a missing one again.
  File versions in the page address come from the file content, not its date, so a browser never
  reuses a copy from another panel at the same address.
- Fix: the "protected" option of a backup schedule was not saved (its checkbox shared an id with the
  PBS selector).
- Fix (plugin): replacing the array key through the Proxmox API failed with "sw-address not set" on
  Proxmox VE 9, which hands the update hook only the changed properties. The plugin now uses the
  full update hook.
- Fix (plugin): a new private key is written together with its public key file; ssh offered the
  old public key from the file next to it and the array refused the new key.
- Fix (plugin): the shared SSH connection to the array is tied to the key file, so a replaced key
  opens a new connection instead of reusing one the array no longer accepts.
- A new or reinstalled panel takes the array key of each storage from Proxmox when a node is
  authorized or the storage is opened, so the key on the array does not change. Generating a new
  key shows a warning: the plugin cannot reach the array until the key is applied.
- Fix: a panel credential generated but not applied yet survives a restart of the panel and is
  offered again instead of being replaced.
- Plugin and panel share one version number: 0.3.3. The appliance image carries the same number.

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
