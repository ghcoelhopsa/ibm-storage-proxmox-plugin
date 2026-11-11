# Array preparation (Storwize V7000 / Storage Virtualize)

The commands below **change the array**. Review each one before running it.

## 1. Dedicated pool (child pool, 7.4+)

```
svcinfo lsmdiskgrp -delim :
svctask mkmdiskgrp -name PVE_Pool -parentmdiskgrp pool0 -size 4 -unit tb
```

The plugin only sees the `sw-pool`. A separate pool also protects the volumes that do not belong to
Proxmox.

## 2. Hosts

Register one host per node, with the WWPNs for FC or the IQN for iSCSI:

```
# on the node: cat /sys/class/fc_host/host*/port_name   or   cat /etc/iscsi/initiatorname.iscsi
svctask mkhost -name pve01 -fcwwpn 2100000000000001:2100000000000002 -type generic
svctask mkhost -name pve02 -iscsiname iqn.1993-08.org.debian:01:xxxx
```

With several nodes, use a host cluster (7.7.1+). Each volume then gets a single mapping for all
the nodes:

```
svctask mkhostcluster -name pve-cluster
svctask addhostclustermember -host pve01 pve-cluster
svctask addhostclustermember -host pve02 pve-cluster
```

In Proxmox, set `--sw-hostcluster pve-cluster`. Without a host cluster, set
`--sw-hosts pve01,pve02`.

## 3. Plugin user

The user must be in the Administrator group, because it creates and deletes volumes, mappings and
FlashCopy.

```
# generate the key on the node: scripts/setup-ssh-key.sh <storeid>
scp <storeid>_id_rsa.pub superuser@<ip>:/tmp/
svctask mkuser -name pveplugin -usergrp Administrator -keyfile /tmp/<storeid>_id_rsa.pub
# existing user:
svctask chuser -keyfile /tmp/<storeid>_id_rsa.pub pveplugin
```

On 7.8.1 use an RSA key, because the firmware does not accept ed25519. For the REST backend (8.x),
the same user needs a password.

## 4. FC zoning

Use single-initiator zoning: each HBA port of the node in a zone with the array ports. There are
two fabrics, with one HBA in each.

## 5. FlashCopy license

```
svcinfo lslicense
```

On the V7000 Gen1, `license_flash 0` is usually normal, because FlashCopy is part of the base
license. Confirm by taking a test snapshot with `scripts/smoke-test.sh`.

## Validations done on the V7000 7.8.1.6

| Item | Result |
|---|---|
| Single-quoted arguments in the restricted shell | Passed |
| `-filtervalue 'name=pve-*'` with wildcard | Passed |
| `mkvdisk -size <bytes> -unit b -rsize 2% -autoexpand` | Passed |
| `mkmdiskgrp -parentmdiskgrp` (child pool) | Passed |
| `mkvdiskhostmap` | Passed |
| FlashCopy: snapshot, rollback `-restore`, delete in the middle of the cascade | Passed (base license OK) |
| `expandvdisksize -unit b` | Passed (blocked by the plugin when there is FlashCopy) |
| `rmvdiskhostmap`, `rmfcmap -force`, `rmvdisk` | Passed |
| `mkvolumehostclustermap` | not tested (single-node environment) |
