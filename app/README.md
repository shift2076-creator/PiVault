# PiVault Users — vault enrollment and approved client downloads

Download `PiVault-Users-Install.sh` to Greenie and run:

```sh
bash ~/Downloads/PiVault-Users-Install.sh
```

The Bash file contains the source and uses one SSH connection to `snx@192.168.4.90`. It asks for the PiVault account password at most once and reuses that answer for SSH and sudo. A rejected password stops the run. Existing SSH host trust, Python 3 and SSH are required on Greenie. No Docker or sudo runs locally on Greenie.

## This update

- Add User offers Password Manager as **Per user** or **No access**. Shared vault access is rejected by the backend.
- Selecting Password Manager creates a new personal Vaultwarden account with the joiner's email and **the same password chosen for PiVault**. Browser WebCrypto generates and encrypts the native vault keys and login entries.
- The personal vault's PiVault folder contains the PiVault login, vault login, and credentials actually created for other selected, configured native adapters. Their correct usernames and HTTPS addresses are included. Entries use host matching for autofill.
- After joining, the user sees Open my vault, the self-hosted server address, a copy button and QR code, and Android, iPhone/iPad, Chrome, Firefox, Safari and Edge installation choices.
- Android APK and Firefox XPI copies can be hosted locally. Their first download and every replacement require explicit Admin approval in the existing notification inbox. Until an initial release is approved and the nightly job runs, its local install link remains unavailable.
- Add User includes a local-client table with installed download version, latest discovered version, check time, status and a review link.
- Approve update queues the exact version and SHA-256 digest for the next maintenance run. Skip this version resolves the notification. Approval does not immediately download or publish a package.
- Nightly maintenance runs at **2:00 AM America/New_York**. It applies approved local downloads, checks official release metadata, and then invokes the existing cinema service when video scheduling was previously enabled. A disabled cinema schedule stays disabled. Cinema's script, queue, quota, storage cap and download policies are preserved.

**PiVault never silently publishes an unapproved local client release and never installs an update on a device.** Firefox's signed package can retain Mozilla's update behavior: the completion page tells the user to turn automatic updates Off for that extension. Store-installed Chrome, Edge, Safari and iOS clients follow their browser/device update policies; PiVault approval does not override those policies.

Other native tool integrations, service-wide access enforcement and all-service SSO remain unfinished. Unsupported service modes stay omitted. This update activates Vaultwarden enrollment; it does not invent logins for tools that have no configured account adapter. Jellyfin and Homebox adapters can be explicitly configured in the protected native configuration, but their live Pi integration has not been verified here. Existing native apps retain their current direct-access behavior.

## Installer behavior

The existing Compose project, launcher stylesheet and Caddy certificates are retained. A private `pivault-users` service has no published host port. Managed portal routes and the existing Add User/notification addon are updated without replacing unrelated launcher JavaScript.

The installer connects to the existing Vaultwarden service over the private Docker network. If no Admin token exists, it creates a private token, adds its protected environment file, and recreates only that service using the same existing image. It rejects a changed image tag rather than applying an unreviewed image upgrade. Newly enabled vault administration is blocked at the existing public Caddy vault route; native API access remains private. Existing owner-managed Admin access is preserved. The installer never resets existing user or master passwords and never enables public vault signups.

The offline enrollment flow requires the existing SMTP-disabled vault. An unrecoverable pre-existing hashed Admin token or incompatible configuration stops installation rather than resetting it. Native integration secrets stay in root-managed files outside the application image.

Before changing live routes, the installer builds the image, runs its backend tests without network access, validates candidate Compose configuration, and verifies routing through the Pi's own Caddy parser. It backs up configuration and the stopped Users database. A failed startup or verified HTTPS probe triggers rollback. TLS verification uses the existing Caddy CA.

It installs one consolidated systemd timer and coordinator. An enabled old cinema timer moves into this coordinator to avoid duplicate scheduling; an already disabled cinema timer is not enabled. Initial official-release discovery is metadata-only. There are no automatic OS, container-image or device-app upgrades.

The first successful installation prints a 30-minute first-Admin invitation for `snx` when no Users identity exists. Reinstallation preserves existing accounts. A first-Admin identity without Password Manager selected does not take over the owner's pre-existing vault.

## Account and notification behavior

Admin assigns name, role and service access. Four-character case-insensitive invitation codes expire after 30 minutes, are single-use and stored as keyed digests. Join and Sign in use HTTPS, CSRF protection, secure server sessions, persistent rate limits and Django password hashing.

The vault adapter refuses an existing same-email vault. Failed enrollment leaves the PiVault invitation unused and removes only native accounts newly created by that attempt. Ambiguous creation or failed cleanup creates an Admin notification. Password changes and later vault synchronization are not part of this revision.

The existing notification bell, per-admin unread state, shared decision history and cinema filesize-review bridge are retained. See `NOTIFICATIONS.md` and `VAULT.md` for the API and encryption details.

## Validation

81 backend tests, 34 installer/maintenance tests, and 8 existing checker tests pass locally. Checks include enrollment, same-password encrypted bundle validation, native account conflict protection and rollback, client approval permissions/CSRF, checksum rejection, no unapproved publication, old-approval downgrade prevention, single-password SSH/sudo, large archive transfer, preservation/rollback and cinema enabled/disabled states.

The official npm Bitwarden CLI **2026.10.0** successfully logs into a temporary HTTPS fixture and decrypts a login produced by `portal/vault-crypto.js`. This checks interoperability with independent client code, not just our own decryptor. Caddy 2.10's actual parser verifies the candidate routes, existing TLS configuration and private Admin block. Python, Bash and JavaScript syntax and Django migration consistency are checked.

Docker is unavailable in the development environment. A live ARM Docker installation, browser visual pass and actual PiVault account enrollment have not been run here. The installer performs image tests and live route checks when run on the Pi. Native API tests use fixtures; the CLI compatibility check is a real official-client decrypt against a local fixture, not a live Vaultwarden instance.

## Developer commands

Use Python 3.12+, install `requirements.txt`, and set a development-only `PIVAULT_SECRET_KEY`. Run the first three commands from `app/` and the rest from the repository root:

```sh
python manage.py test portal
python manage.py check
python manage.py makemigrations --check --dry-run
python -m unittest discover -s installer -p 'test*.py'
python -m unittest discover -s tools/check -p 'test*.py'
python tools/vault_compat.py /absolute/path/to/official/bitwarden/cli/build/bw.js
python installer/build.py /absolute/path/PiVault-Users-Install.sh
```

The compatibility check also requires Node and OpenSSL. Its temporary certificate is explicitly trusted only by the isolated test CLI; TLS verification stays enabled. Dummy fixture credentials are test-only. Never use the development secret or fixture passwords on PiVault.
