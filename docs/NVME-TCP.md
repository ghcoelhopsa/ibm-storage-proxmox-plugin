# NVMe over TCP

Status: **code ready, not validated on hardware.** The current lab is Fibre Channel
(V7000 Gen1, no NVMe). The FC/iSCSI path was not changed.

## What changes compared to FC/iSCSI

| Topic | FC / iSCSI | NVMe over TCP |
|---|---|---|
| Multipath | dm-multipath (`multipathd`, `/etc/multipath.conf`) | **native** kernel multipath (`nvme_core.multipath=Y`), no dm-multipath |
| Disk on the node | `/dev/disk/by-id/dm-uuid-mpath-3<UID>` | `/dev/disk/by-id/nvme-<wwid>` (`eui.<NGUID>` or `uuid.<UUID>`) |
| Node identity | WWPN / IQN | NQN (`/etc/nvme/hostnqn`) |
| Host on the array | `mkhost -fcwwpn` / `-iscsiname` | `mkhost -protocol tcpnvme -nqn <NQN>` (done on the storage) |
| Seeing a new volume | SCSI rescan + `multipath -a` | controller rescan (`rescan_controller`); it usually arrives by itself |
| Expand | rescan of the paths + `multipathd resize` | controller rescan |
| Remove | `multipath -f` + delete paths | nothing local: the namespace goes away with the mapping |
| Load balancing | `path_selector` in multipath.conf | subsystem `iopolicy` (udev rule: round-robin) |

## Configuration

Plugin: `sw-transport nvme-tcp` and `sw-portals <IP[:port],...>` (default port 4420). In the panel,
the registration wizard has the **Transport** field. In **Proxmox nodes › Prepare node**, the
**NVMe over TCP** step installs `nvme-cli`, loads `nvme-tcp`, generates the NQN if it is missing,
writes the rule `/etc/udev/rules.d/71-nvme-ibm-iopolicy.rules` and enables `nvmf-autoconnect`. The
NQN is shown on the node card, to be used when creating the host on the storage.

When a volume is activated, the plugin connects to the ports
(`nvme connect-all -t tcp -a <ip> -s <port>`) if there is no active IBM controller yet, asks for a
rescan and looks for the namespace.

## How the plugin finds the namespace

With an NVMe host, the array exposes the volume identifier in NGUID format. Because the exact
relation to `vdisk_UID` is not confirmed, the plugin compares **all** the UID-like fields of
`lsvdisk` (`vdisk_UID`, `volume_uuid`, `uuid`, `nguid`) with `wwid`, `nguid` and `uuid` of each
namespace in `/sys/block/nvme*n*`, ignoring punctuation. If it does not find a match, the error
lists what the array reported and what the node sees, so it can be adjusted in minutes on the
first test.

## To confirm on hardware

1. Which `lsvdisk` field corresponds to the NGUID/UUID seen on the node.
2. `nvme connect-all` on the data ports (or whether the discovery port 8009 is needed).
3. Model advertised by the subsystem (the udev rule and the plugin look for `IBM`, `2145` or `FlashSystem`).
4. Online expansion and volume removal with the VM running.
5. **LVM filter**: the panel's current filter covers only dm-multipath devices (`36005076...`).
   An equivalent filter for NVMe is still missing; until then the host can see the LVM inside the VMs.
6. **Snapshot backup (CBT)** and the `pve-ibm-storage` utility still look for the disk by the
   multipath path; they do not work with NVMe/TCP.
7. Per-LUN statistics in the panel (collected from the dm devices) do not cover NVMe namespaces.
8. Live migration: the volume must be mapped to the NVMe host of both nodes.
