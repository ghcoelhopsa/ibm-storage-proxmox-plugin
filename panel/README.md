# IBM Storage Plugin Proxmox (panel), beta version

Web platform, in the IBM Carbon look, to register and operate IBM Storage Virtualize storages
(Storwize, FlashSystem, SVC) used by Proxmox VE. It runs in its own LXC container and
**does not modify any Proxmox file**: everything goes through the Proxmox API, with the login and
permissions of Proxmox itself.

```
Browser ──HTTPS──► CT "storwize-panel" (FastAPI + Carbon)
                     ├─► Proxmox API (:8006, user's ticket, pinned certificate)
                     ├─► arrays over SSH (one key per storage)
                     └─► Proxmox nodes over SSH (only for preparation: packages, multipath, plugin)
```

## What you can do

| Screen | Function |
|---|---|
| Overview | Storages, capacity, VMs with a disk on the array, errors and warnings |
| Storages › Add | Wizard: generates the SSH key, checks the array fingerprint, picks or creates the child pool, creates hosts from the logged-in WWPNs and the host cluster, and registers the storage in Proxmox |
| Storage | Summary (system, canisters, pool, firmware features), volumes, FlashCopy, hosts, array alerts, configuration; renew credential; remove |
| VMs and volumes | Each disk with its volume and UID; take, restore and delete snapshots; expand disk |
| Reconciliation | Orphan volumes, missing volumes, missing mappings, snapshots without FlashCopy and the opposite, pool above 80%, array alerts |
| Performance | 7-day history of `lssystemstats` (MB/s, IOPS, latency, CPU, FC, cache) |
| Proxmox nodes | Status of each node (packages, multipathd, multipath.conf, plugin, FC ports); authorize the panel (root password once); prepare or update the node |

## Permissions

- **Login:** Proxmox user and realm, with the `Sys.Audit` privilege on `/` (for example, the
  PVEAuditor role).
- **Storage registration and node preparation:** require `Sys.Modify` on `/`.
- **Snapshots and resize:** go through the Proxmox API with the user's ticket. The user's
  permissions on each VM apply.

## Installation

On the Proxmox node (root), with a Debian 13 CT:

```bash
panel/deploy.sh <ctid>                          # installs or updates
panel/deploy.sh <ctid> --import-storage v7k     # imports the key of a storage registered before the panel
```

The script downloads Carbon (`@carbon/styles`) and the IBM Plex fonts once, packages everything,
installs it in the CT and creates the `swpanel` service (HTTPS on port 443, unprivileged user). The
Proxmox certificate is pinned in `/etc/swpanel/config.json`.

## Where the data lives (in the CT)

| Path | Content |
|---|---|
| `/etc/swpanel/config.json` | address and fingerprint of the Proxmox API |
| `/etc/swpanel/tls.*` | panel certificate (self-signed; replace it with your own if you want) |
| `/var/lib/swpanel/keys/` | SSH key of each storage and the panel's key for the nodes |
| `/var/lib/swpanel/perf.db` | performance history (SQLite, 7 days) |

Back up the CT (vzdump) to keep the keys.

## Development

| Tool | Use |
|---|---|
| `panel/dev/capture-fixture.sh output.json` | Captures real responses (GET only) from the node's Proxmox API |
| `panel/dev/devserver.py fixture.json 8444` | Real panel with the Proxmox API replaced by the fixture and **without login**. Listens only on 127.0.0.1; never leave it running |
| `panel/dev/shot.js url output.png 1440x900 'js;;js'` | Screenshot with interactions (clicks, modals) through the Chrome DevTools Protocol (`node --experimental-websocket`, `chromium`) |

The interface uses the IBM Carbon v11 CSS classes (`@carbon/styles`) and the official icons
(`@carbon/icons`, in `static/icons.js`), both Apache-2.0. The light theme (g10) is the default and
the dark one (g100) is optional, with the choice saved in the browser.

## Domain and certificate

In **Infrastructure › Domain and certificate** the administrator replaces the self-signed
certificate with:

- **Let's Encrypt**: enter the domain. Validation over HTTP (the domain points to the panel and
  port 80 reaches it from the internet) or over DNS on Cloudflare (token with Zone › DNS › Edit;
  works for a panel that is only on the internal network). The panel renews by itself 30 days
  before expiry and restarts.
- **Own certificate**: paste the certificate with the chain and the private key without a
  passphrase (PEM).

Files are in `/var/lib/swpanel/tls` and `/var/lib/swpanel/acme`. If the active certificate fails to
load, the panel starts with the self-signed one from `/etc/swpanel`. The system `certbot.timer`
stays disabled: renewal is done by the panel, which runs without root.
