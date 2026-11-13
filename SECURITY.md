# Security policy

## Reporting a vulnerability

Report it privately through **GitHub › Security › Report a vulnerability** on this repository
(private vulnerability reporting). Do not open a public issue. Expect an answer within a week.

Include the version (panel: menu footer; plugin: `pveversion -v` shows it under the storage
plugins, or `grep VERSION /usr/share/perl5/PVE/Storage/Custom/StorwizePlugin.pm`), what you did and
what happened. Never send real credentials, keys or array dumps.

## Supported versions

The project is in beta: fixes go into the latest release only. The panel updates itself
(**Updates** page) and then offers the new plugin to each node.

## Release signing

Release assets are signed with the project key (Ed25519):

- public key: [`panel/app/release-key.pem`](panel/app/release-key.pem)
- SHA-256 of the key (DER): `c21923a5b4d96344dae3aba7efb5e388b8f9747c05886b385d4d0f8c81a1a47c`

Check the key you downloaded with
`openssl pkey -pubin -in release-key.pem -outform DER | sha256sum`, then an asset with
`openssl pkeyutl -verify -pubin -inkey release-key.pem -rawin -in <file> -sigfile <file>.sig`.

## Threat model

See [README › Security](README.md#security).
