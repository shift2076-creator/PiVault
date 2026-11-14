"""One local systemd run for approved client copies, release checks and cinema."""
import hashlib
import json
import os
from pathlib import Path
import pwd
import subprocess
import tempfile
from datetime import datetime, timezone

CONFIG = Path('/srv/pi-server/config/users/maintenance.json')


def publish_failure(step):
    # Standalone systemd job: it cannot import portal code, so it repeats the atomic inbox write from
    # jobs/cinema/cinema-notifications.py and app/portal/notifications.py. Keep the three in step.
    owner = pwd.getpwnam('snx')
    root = Path('/srv/pi-server/config/notifications/inbox')
    root.mkdir(mode=0o750, parents=True, exist_ok=True)
    key = datetime.now(timezone.utc).date().isoformat() + ':' + step
    identity = hashlib.sha256(('maintenance:' + key).encode()).hexdigest()
    value = {'version': 1, 'id': identity, 'source': 'maintenance', 'kind': 'service.notice',
        'level': 'warning', 'title': 'Nightly ' + step + ' needs review',
        'message': 'The scheduled job did not finish successfully. Other maintenance steps were still attempted.',
        'details': {}, 'created_at': datetime.now(timezone.utc).isoformat()}
    fd, temporary = tempfile.mkstemp(prefix='.maintenance-', dir=root)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream); stream.flush(); os.fsync(stream.fileno())
        os.chown(temporary, owner.pw_uid, owner.pw_gid)
        os.chmod(temporary, 0o640)
        os.replace(temporary, root / (identity + '.json'))
    finally:
        Path(temporary).unlink(missing_ok=True)


def offline_mode(run=subprocess.run):
    """True only when the portal confirms the Admin turned offline mode on."""
    try:
        result = run(['docker', 'exec', 'pivault-users', 'python', 'manage.py', 'offline_mode', '--status'],
                     timeout=120, check=False, capture_output=True, text=True)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and result.stdout.strip() == 'on'


def main(run=subprocess.run, publish=publish_failure, config_path=CONFIG, offline=offline_mode):
    config = json.loads(config_path.read_text())
    if offline():
        # Every nightly job needs the internet; offline mode pauses them silently.
        print('Offline mode is on: nightly internet jobs are paused.')
        return 0
    jobs = [('client downloads', ['docker', 'exec', 'pivault-users', 'python', 'manage.py', 'maintain_clients'], 3600)]
    if config.get('cinema_enabled'):
        jobs.append(('cinema', ['systemctl', 'start', '--wait', 'pivault-cinema.service'], None))
    failed = False
    for name, command, timeout in jobs:
        try:
            result = run(command, timeout=timeout, check=False)
            ok = result.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            ok = False
        if not ok:
            failed = True
            publish(name)
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
