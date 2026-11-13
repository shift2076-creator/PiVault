# Personal vault enrollment

## Data flow

The signed five-minute enrollment plan binds the invitation, normalized email, selected tools, correct native usernames and configured HTTPS addresses. The browser derives Bitwarden-compatible keys with WebCrypto, encrypts a PiVault folder and login ciphers, and submits an encrypted bundle with the join request. Django verifies that its master-password hash corresponds to the same PiVault password and that the bundle has the expected entry set.

The server already receives the PiVault join password transiently for its normal login hash and configured native account creation. It does not persist that plaintext password or an export file. The Users database retains the native vault user/item IDs and the normal Django login password hash. The native vault receives encrypted keys, folder name and login fields. Infrastructure Admin credentials never enter the new user's personal vault.

Provisioning other native accounts completes before vault population. Per-person credential usernames come from each adapter; Vaultwarden uses email. Shared logins (Books, Games, Music, Videos) create no per-person account: the vault receives the one shared login copied from the Admin's vault (see Shared logins). HTTPS addresses come from protected server configuration, not browser-supplied form fields. This revision retains the established same-password account handoff for per-person accounts; it does not introduce unique service passwords.

## Native API contract

The implementation targets the inspected Vaultwarden 1.37.4 registration contract. It authenticates to the private Admin API using the native `VW_ADMIN` cookie, refuses an existing same-email account, and explicitly invites a new account. With SMTP disabled, `/identity/accounts/register/send-verification-email` returns a verification token for that invitation. `/identity/accounts/register/finish` accepts the encrypted key material. A password grant verifies account ownership, then the adapter creates the encrypted personal folder and login ciphers.

Failed registration or cipher creation compensates by deleting only this attempt's newly invited native user. Uncertain invitation outcomes never trigger deletion of an account inferred only by email. They require review in the shared Admin inbox.

## Cryptographic format

- PBKDF2-HMAC-SHA256, 600,000 iterations, normalized email salt, 32-byte master key.
- Authentication hash: PBKDF2-HMAC-SHA256 with the master key as password, actual password bytes as salt, one iteration, 32-byte output.
- Stretched master: HKDF-Expand SHA-256 labels `enc` and `mac`, 32 bytes each, with no extra extract step.
- A CSPRNG-generated 64-byte personal vault key is wrapped using the stretched master.
- Type-2 EncStrings use AES-256-CBC with PKCS#7 padding, random 16-byte IV and HMAC-SHA256 over IV plus ciphertext. Encryption and MAC use separate halves of the 64-byte key.
- RSA-OAEP 2048-bit account keys: SPKI public key, personal-key-encrypted PKCS#8 private key.
- Folder and every login name, username, password and URI are encrypted. URI matching is Host.

`tools/vault_compat.py` generates a fresh browser-format bundle in Node, checks the authentication hash independently with Python hashlib, then runs the official Bitwarden CLI against an isolated HTTPS API fixture. The CLI logs in, syncs and decrypts the expected password, username, URI and name. TLS verification remains enabled with an ephemeral local test certificate. No real user credentials are used.

## Shared logins

Books, Games, Music and Videos each use one login, held as an entry in the Admin's own Vaultwarden. Nothing is created per person in these four services. Joining copies the chosen entry into each new person's vault. Documents, Photos and Radicale stay per person, and the Paperless superuser is never shared.

1. Issuing an invitation generates a random 32-byte secret. The server places it only in the QR code's URL fragment (`#k=`) and does not store it.
2. The Admin's browser unlocks the Admin's vault through a relay at `/users/vault-proxy/{prelogin,token,sync}`. The relay serves only Admin sessions, forwards only those three Vaultwarden calls, and sends only the named fields. The master password stays in the browser. The password hash goes to Vaultwarden as its login requires.
3. The Admin picks one entry per service. The browser seals the chosen entries with the vault's AES-256-CBC and HMAC-SHA256 format, under a key derived from the secret. It posts the sealed value to `/users/add/shared`, and the invitation stores only that ciphertext.
4. The new person scans the QR code. The join page reads the secret from the fragment and removes it from the address bar. `/join/plan` returns the sealed copy only when the invitation needs shared logins and one is attached. The browser opens it and writes the shared entries into the new vault under that person's own key.

Refusals: a join without the QR secret is blocked, so the typed code alone cannot open shared logins. A plan is refused until the Admin attaches the copy. A wrong secret stops the join before any account is created.

Trust: anyone who photographs the QR code while the invitation is open can read the shared logins. The invitation expires in 30 minutes.

## Client installation and updates

PiVault stores original Android APK and signed Firefox XPI archives from official upstream releases. Metadata is obtained from Bitwarden's Android GitHub releases and Mozilla's Bitwarden add-on listing. The nightly job downloads the newest stable release automatically, using its exact version, size, URL and upstream SHA-256 identity. It enforces HTTPS and an allowed host list across redirects, size limits, digest verification, APK structure or Firefox manifest identity/version/signing-data presence, and atomic publication. Device/browser installers verify their native signatures; PiVault does not claim its archive inspection independently validates a signing certificate chain.

A failure keeps the previous download available, retries on the next run and raises a notification. The current and previous successful local copies are retained; older releases cannot downgrade a newer published version. Authenticated authorized vault users and Admins can retrieve published copies; arbitrary paths or unverified artifacts are not served.

Local copies start empty. The next 2:00 AM Eastern run downloads and publishes them automatically. Installing on devices remains a manual step. Chrome, Edge, Safari and iOS use official store links. Their automatic-update settings are independent of PiVault. Firefox users must switch automatic updates Off for the locally installed signed extension if they want manual-only device updates.

Devices need PiVault LAN access and trust in the existing Caddy CA. The completion QR contains only the existing vault HTTPS server address, no credential or invitation code. In each Bitwarden client choose Self-hosted and that server address before signing in.

## Primary protocol sources

- Vaultwarden registration/account APIs: https://github.com/dani-garcia/vaultwarden/blob/1.37.4/src/api/core/accounts.rs
- Vaultwarden Admin API: https://github.com/dani-garcia/vaultwarden/blob/1.37.4/src/api/admin.rs
- Vaultwarden identity API: https://github.com/dani-garcia/vaultwarden/blob/1.37.4/src/api/identity.rs
- Bitwarden official clients: https://github.com/bitwarden/clients
- Bitwarden official SDK: https://github.com/bitwarden/sdk-internal
- Official Android releases: https://github.com/bitwarden/android/releases
- Mozilla signed extension: https://addons.mozilla.org/en-US/firefox/addon/bitwarden-password-manager/
