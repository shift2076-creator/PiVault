# PiVault

An offline services hub on a Raspberry Pi 5: media, knowledge, organization and password management behind one HTTPS portal.

## Layout

| Folder | What it is | Where it runs |
|---|---|---|
| `app/` | The PiVault web app: sign-in, joining, invitations, password vault, notifications (Django) | Docker container `pivault-users` on pivault |
| `stack/` | The container stack around the app: reference `docker-compose.yml`, Homepage add-on, PHP setting, launcher stylesheet and favicons (`stack/homepage/README.md`) | Docker Compose on pivault |
| `jobs/` | Scheduled scripts: `maintenance.py` (nightly 02:00) and `cinema/` (video downloads) | systemd on pivault |
| `installer/` | Builds the one-file installer and installs `app/` onto pivault | Built on the deploy machine, runs on pivault |
| `tools/` | Read-only integration check and Bitwarden compatibility check | Any machine with a checkout |

## Entry points

- `app/manage.py`: Django commands (`migrate`, `test portal`, `first_admin`).
- `app/settings.py`: all portal settings.
- `app/urls.py`: every portal route.

Inside `app/portal/`: views and logic at the top; `integrations/` (Vaultwarden, Jellyfin, Navidrome), `static/portal/` (browser scripts), `templates/`, `tests/`, `management/commands/` and `migrations/`. Background docs are in `app/docs/`.
- `installer/build.py`: produces `PiVault-Users-Install.sh`.
- `installer/install.py`: what that installer runs on pivault.
- `jobs/maintenance.py`: installed to `/srv/pi-server/users/maintenance.py`.
- `jobs/cinema/PiVault-Cinema.sh`: installed to `/home/snx/pi-services/cinema/`.

## Live paths on pivault

The repository is not deployed as a folder. The installer copies what it needs:

- Portal image: built from `app/`, run as container `pivault-users`.
- Compose file: `/home/snx/pi-services/docker-compose.yml`. The `stack/` copy is a reference snapshot.
- Configuration and data: `/srv/pi-server/config/` (root-owned).

## Tests

From `app/`, with a development-only secret:

```sh
PIVAULT_SECRET_KEY=dev-only-test-key PIVAULT_DATA=/tmp/pv-data python manage.py test portal
```

From the repository root:

```sh
python -m unittest discover -s installer -p 'test*.py'
python -m unittest discover -s tools/check -p 'test*.py'
```
