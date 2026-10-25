"""Enable launcher search for the apps that need no new credentials. Run on pivault as root.

Adds Offline Wiki always, Videos when native.json has a 'jellyfin' entry and Music when it
has a 'navidrome' entry; existing 'search' entries are kept. Books, Documents, Inventory
and Pantry need read-only logins created in each app first (see app/docs/SEARCH.md).

native.json is backed up, rewritten in place (it is a single-file bind mount) and the
portal restarted. Each enabled app is then searched once inside the portal; apps that
fail are removed again, and an unhealthy portal restores the backup. No credential or
upstream output is printed.
"""
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

NATIVE = Path('/srv/pi-server/config/users/native.json')
BACKUPS = Path('/srv/pi-server/backups/users')
SERVICE = 'pivault-users'
HOST = 'http://192.168.4.90'
# 'origin' is the private Docker address; 'link' is what browsers open (docs/SEARCH.md).
DEFAULTS = {
    'wiki': (None, {'origin': 'http://kiwix:8080', 'link': HOST + ':8084'}),
    'videos': ('jellyfin', {'link': HOST + ':8096'}),
    'music': ('navidrome', {'link': HOST + ':4533/music'}),
}
PROBE = '''from portal import search
for key, connector in sorted(search.CONNECTORS.items()):
    try:
        connector.search('a')
        print('PIVAULT_SEARCH', key, 'ok')
    except Exception:
        print('PIVAULT_SEARCH', key, 'fail')
'''


def plan(native):
    """Return (new native config, keys added). Never touches existing search entries."""
    result = json.loads(json.dumps(native))
    search = result.setdefault('search', {})
    if not isinstance(search, dict):
        raise ValueError("native.json 'search' must be an object.")
    added = []
    for key, (requires, entry) in DEFAULTS.items():
        if key in search or (requires and not result.get(requires)):
            continue
        search[key] = dict(entry)
        added.append(key)
    return result, added


def without(native, keys):
    result = json.loads(json.dumps(native))
    for key in keys:
        result.get('search', {}).pop(key, None)
    if not result.get('search'):
        result.pop('search', None)
    return result


def parse_probe(output):
    status = {}
    for line in output.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[0] == 'PIVAULT_SEARCH':
            status[parts[1]] = parts[2] == 'ok'
    return status


def write_in_place(content):
    # Same inode, owner and mode: Docker keeps seeing the bind-mounted file.
    with NATIVE.open('r+b') as handle:
        handle.write(content)
        handle.truncate()
        handle.flush()
        os.fsync(handle.fileno())


def docker(*args, timeout=120):
    return subprocess.run(['docker', *args], text=True, capture_output=True, timeout=timeout)


def restart_healthy():
    if docker('restart', SERVICE).returncode:
        return False
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        value = docker('inspect', '--format', '{{.State.Status}} {{.State.Health.Status}}', SERVICE)
        state = value.stdout.split()
        if state[-1:] == ['healthy']:
            return True
        if state[:1] in (['exited'], ['dead']):
            return False
        time.sleep(2)
    return False


def probe():
    value = docker('exec', SERVICE, 'python', 'manage.py', 'shell', '-c', PROBE, timeout=90)
    return parse_probe(value.stdout) if value.returncode == 0 else None


def main():
    if os.geteuid() != 0:
        raise SystemExit('Run with sudo on pivault.')
    if NATIVE.is_symlink() or not NATIVE.is_file():
        raise SystemExit('Expected a regular file at ' + str(NATIVE) + '. Nothing changed.')
    original = NATIVE.read_bytes()
    current = json.loads(original)
    candidate, added = plan(current)
    for key, (requires, _) in DEFAULTS.items():
        if key not in candidate['search']:
            print('Skipped ' + key + ": native.json has no '" + requires + "' entry.")
    if not added:
        print('Nothing to add; search entries: ' + ', '.join(sorted(candidate['search'])))
        return
    backup = BACKUPS / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-search-' + secrets.token_hex(3))
    backup.mkdir(mode=0o700, parents=True)
    shutil.copy2(NATIVE, backup / 'native.json')
    os.chmod(backup / 'native.json', 0o600)
    print('Backup: ' + str(backup / 'native.json'))

    def restore(reason):
        write_in_place(original)
        healthy = restart_healthy()
        raise SystemExit(reason + ' native.json restored' + ('.' if healthy else '; the portal is still unhealthy.'))

    write_in_place((json.dumps(candidate, indent=2) + '\n').encode())
    if not restart_healthy():
        restore('The portal did not become healthy with search enabled.')
    status = probe()
    if status is None:
        restore('The search check could not run inside the portal.')
    failed = [key for key in added if not status.get(key)]
    if failed:
        print('Removing apps that did not answer a test search: ' + ', '.join(failed))
        candidate = without(candidate, failed)
        write_in_place((json.dumps(candidate, indent=2) + '\n').encode())
        if not restart_healthy():
            restore('The portal did not become healthy after removing failed apps.')
    working = sorted(key for key in candidate.get('search', {}) if status.get(key))
    print('Search enabled for: ' + (', '.join(working) or 'none'))


if __name__ == '__main__':
    sys.exit(main())
