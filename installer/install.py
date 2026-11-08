"""Install the current PiVault Users portal on the existing PiVault host.

Installs enrollment, personal vaults and approval-based local client downloads.
Other native tool integrations and all-service SSO remain separate work.
"""
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone

try:
    import yaml
except ImportError:
    raise SystemExit('The Pi needs its existing python3-yaml package. Nothing was installed.')

from configuration import (PORTAL_SITE, SERVICE, compose_configuration, homepage_js,
                           apps_caddyfile, portal_caddyfile, private_vault_admin, verify_routes)
from native import vault_configuration, maintenance_units

ROOT = Path('/srv/pi-server')
CONFIG = ROOT / 'config'
COMPOSE = Path('/home/snx/pi-services/docker-compose.yml')
CADDY = 'pivault-https'
LABEL = 'users-installer'
SOURCE = Path(__file__).resolve().parents[1]  # Repository (payload) root.
MODEL = Path('/proc/device-tree/model')
SYSTEMD_DIR = Path('/etc/systemd/system')


class InstallError(Exception):
    pass


def say(message):
    print(message, flush=True)


# Full output of this installation, written to one log file on the Pi. Opened by main().
STAGE_LOG = None
LOG_DIR = Path('/home/snx/Downloads')
# Background colours for the stage bar. ANSI codes reach the terminal through the SSH relay.
COLOURS = {'green': '42', 'yellow': '43', 'red': '41'}
WARNING = re.compile(r'\bWARNING\b')
ERROR = re.compile(r'\b(ERROR|CRITICAL|FAILED|FAIL)\b|Traceback \(most recent call last\)')
BLOCK_START = re.compile(r'^(ERROR|FAIL): |Traceback \(most recent call last\)')


def open_install_log():
    global STAGE_LOG
    owner = pwd.getpwnam('snx')
    LOG_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chown(LOG_DIR, owner.pw_uid, owner.pw_gid)
    path = LOG_DIR / ('pivault-install-' + datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S') + '.log')
    STAGE_LOG = open(path, 'x', encoding='utf-8')
    os.chmod(path, 0o600)
    os.chown(path, owner.pw_uid, owner.pw_gid)
    say('Full installation output: ' + str(path))


def close_install_log():
    global STAGE_LOG
    if STAGE_LOG is not None:
        STAGE_LOG.close()
        STAGE_LOG = None


def duration(seconds):
    total = int(seconds)
    return f'{total // 60}m {total % 60:02d}s'


def show_timer(title, started, count, done, enabled):
    # One line, rewritten once a second, so a long stage shows that it is still working.
    if not enabled:
        return
    while not done.wait(1.0):
        text = f'{title}: running for {duration(time.monotonic() - started)}, {count[0]} lines'
        sys.stdout.write('\r' + text.ljust(78)[:78])
        sys.stdout.flush()


def run_stage(title, args, *, cwd=None, timeout=180, progress=True):
    """Run one long command as a stage.

    The full output goes to the installation log. The terminal shows a timer while the stage runs, then one
    green, yellow or red bar, then only the warning and error lines. Red means the command failed.
    """
    if STAGE_LOG is not None:
        STAGE_LOG.write(f'== {title} ==\n')
    started = time.monotonic()
    count = [0]
    done = threading.Event()
    timed_out = threading.Event()
    kept = []
    flagged = False
    in_block = False
    try:
        process = subprocess.Popen(args, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding='utf-8', errors='replace', bufsize=1)
    except OSError as error:
        raise InstallError(f'{args[0]} could not complete ({type(error).__name__}).') from None
    watchdog = threading.Timer(timeout, lambda: (timed_out.set(), process.kill()))
    watchdog.start()
    timer = threading.Thread(target=show_timer, args=(title, started, count, done, progress), daemon=True)
    timer.start()
    try:
        for raw in process.stdout:
            line = raw.rstrip('\n')
            count[0] += 1
            if STAGE_LOG is not None:
                STAGE_LOG.write(line + '\n')
            if BLOCK_START.search(line):
                # An error or failed test: keep its traceback, up to the blank line that ends it.
                in_block, flagged = True, True
                kept.append(line)
            elif in_block and line.strip() and not line.startswith('===='):
                kept.append(line)
            else:
                in_block = False
                if WARNING.search(line) or ERROR.search(line):
                    flagged = True
                    kept.append(line)
    finally:
        watchdog.cancel()
        if process.poll() is None:
            process.kill()
        code = process.wait()
        process.stdout.close()
        done.set()
        timer.join()
    failed = timed_out.is_set() or code != 0
    colour = 'red' if failed else 'yellow' if flagged else 'green'
    if progress:
        sys.stdout.write('\r' + ' ' * 78 + '\r')
    if STAGE_LOG is not None:
        STAGE_LOG.write(f'== {title}: {colour}, exit {code} ==\n')
    # ANSI: set background colour, erase the line (fills it to the edge), reset.
    say(f'\033[{COLOURS[colour]}m\033[K\033[0m')
    verdict = 'failed' if failed else 'passed, with warnings or logged errors below' if flagged else 'passed'
    say(f'{title}: {verdict} in {duration(time.monotonic() - started)}.')
    for line in kept:
        say(line)
    if timed_out.is_set():
        raise InstallError(f'{args[0]} could not complete (timed out).')
    if code:
        raise InstallError(f'{args[0]} failed during installation (exit {code}).')


def run(args, *, cwd=None, data=None, stage=None, timeout=180):
    if cwd is None and args[:2] == ['docker', 'compose']:
        cwd = COMPOSE.parent
    if stage:
        return run_stage(stage, args, cwd=cwd, timeout=timeout)
    try:
        value = subprocess.run(args, cwd=cwd, input=data, text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise InstallError(f'{args[0]} could not complete ({type(error).__name__}).') from None
    if value.returncode:
        # Do not echo raw app/configuration output; it can contain credentials.
        raise InstallError(f'{args[0]} failed during installation (exit {value.returncode}).')
    return value.stdout or ''


def inspect(name, *, missing=False):
    value = subprocess.run(['docker', 'inspect', name], text=True, capture_output=True)
    if value.returncode:
        if missing and 'No such object' in value.stderr:
            return None
        raise InstallError('Cannot inspect the expected Docker container: ' + name)
    return json.loads(value.stdout)[0]


def container_path(container, destination):
    for mount in container.get('Mounts', []):
        target = Path(mount['Destination'])
        wanted = Path(destination)
        if wanted == target or target in wanted.parents:
            return Path(mount['Source']) / wanted.relative_to(target)
    raise InstallError('The expected mounted configuration is missing: ' + destination)


def write_file(path, content, *, mode=0o600):
    """Write existing bind-mounted files in place so Docker sees the same inode."""
    payload = content.encode() if isinstance(content, str) else content
    if path.is_symlink():
        raise InstallError('Refusing to replace a symbolic-link configuration: ' + str(path))
    if path.exists():
        with path.open('r+b') as handle:
            handle.seek(0)
            handle.write(payload)
            handle.truncate()
            handle.flush()
            os.fsync(handle.fileno())
    else:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
        with os.fdopen(fd, 'wb') as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())


def restore_file(path, snapshot):
    if snapshot is None:
        path.unlink(missing_ok=True)
        return
    content, metadata = snapshot
    write_file(path, content)
    os.chown(path, metadata.st_uid, metadata.st_gid)
    os.chmod(path, stat.S_IMODE(metadata.st_mode))


def snapshot(path):
    if path.is_symlink():
        raise InstallError('Expected a regular configuration file: ' + str(path))
    return (path.read_bytes(), path.stat()) if path.exists() else None


def compose_service(document, container):
    labels = container.get('Config', {}).get('Labels', {}) or {}
    name = labels.get('com.docker.compose.service')
    if name not in document.get('services', {}):
        raise InstallError('Caddy is not managed by the expected pi-services Compose file.')
    paths = labels.get('com.docker.compose.project.config_files', '')
    # Standalone docker-compose records the file as given (often relative to the
    # project folder), so resolve each entry against the recorded working directory.
    folder = Path(labels.get('com.docker.compose.project.working_dir', '') or '/')
    if paths and COMPOSE not in [folder / item.strip() for item in paths.split(',')]:
        raise InstallError('The active Caddy container uses a different Compose file.')
    project = labels.get('com.docker.compose.project')
    if not project:
        raise InstallError('Cannot identify the existing Compose project.')
    return document['services'][name], project


def adapt(candidate):
    # Caddy parses its own syntax. Credentials/TLS data are never printed.
    remote = '/tmp/pivault-users-candidate-' + secrets.token_hex(8) + '.caddy'
    try:
        run(['docker', 'cp', str(candidate), CADDY + ':' + remote])
        value = run(['docker', 'exec', CADDY, 'caddy', 'adapt', '--config', remote,
                     '--adapter', 'caddyfile'])
        result = json.loads(value)
        run(['docker', 'exec', CADDY, 'caddy', 'validate', '--config', remote,
             '--adapter', 'caddyfile'])
        return result
    finally:
        subprocess.run(['docker', 'exec', CADDY, 'rm', '-f', remote], capture_output=True)


def wait_healthy():
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        value = inspect(SERVICE)
        if value['State'].get('Health', {}).get('Status') == 'healthy':
            return
        if value['State'].get('Status') in ('exited', 'dead'):
            break
        time.sleep(2)
    raise InstallError('The Users portal did not become healthy.')


def unit_flag(flag, name):
    return subprocess.run(['systemctl', flag, '--quiet', name], capture_output=True).returncode == 0


def caddy_root_certificate():
    # The public root certificate only. The CA private key stays in Caddy's data directory.
    ca = None
    caddy = inspect(CADDY)
    for mount in caddy.get('Mounts', []):
        if mount['Destination'] == '/data':
            candidate = Path(mount['Source']) / 'caddy/pki/authorities/local/root.crt'
            if candidate.is_file():
                ca = candidate
    if ca is None:
        raise InstallError('Cannot verify the existing Caddy local CA certificate.')
    return ca


def publish_root_certificate(data_dir, uid, gid):
    # The portal serves this copy to Admins for the Android sync helper (/devices/ca.crt).
    target = data_dir / 'caddy-root.crt'
    target.write_bytes(caddy_root_certificate().read_bytes())
    os.chmod(target, 0o600)
    os.chown(target, uid, gid)


def portal_probe():
    # Resolve the actual mounted Caddy CA. Verify TLS; never use -k or replace CA.
    ca = caddy_root_certificate()
    base = ['curl', '--silent', '--show-error', '--fail', '--max-time', '15',
            '--cacert', str(ca), '--noproxy', '*']
    body = run(base + [PORTAL_SITE + '/users/sign-in'])
    # Before the first account exists, the same route shows the join form instead.
    if 'name="password"' not in body or ('Sign in' not in body and 'Create account' not in body):
        raise InstallError('The live HTTPS sign-in route did not return the portal.')
    # The gate must stay private even after a successful installation.
    code = run([x for x in base if x != '--fail'] + ['--output', '/dev/null',
        '--write-out', '%{http_code}', PORTAL_SITE + '/internal/gate']).strip()
    if code != '404':
        raise InstallError('The private gate was not blocked by Caddy.')


def install_portal():
    model = MODEL
    if not model.is_file() or 'Raspberry Pi 5' not in model.read_text(errors='replace'):
        raise InstallError('The target is not the expected Raspberry Pi 5. Nothing was installed.')
    owner = pwd.getpwnam('snx')
    uid, gid = owner.pw_uid, owner.pw_gid
    for command in ('docker', 'curl', 'systemctl', 'systemd-analyze'):
        if not shutil.which(command):
            raise InstallError(command + ' is required on PiVault. Nothing was installed.')
    if not COMPOSE.is_file():
        raise InstallError('The expected pi-services Compose file does not exist.')

    say('Installing PiVault enrollment, per-user vault setup, client downloads and approved-update notifications.')
    say('Other native tool integrations remain available only when their adapters and HTTPS addresses are configured.')
    original_compose = yaml.safe_load(COMPOSE.read_text())
    caddy = inspect(CADDY)
    caddy_service, project = compose_service(original_compose, caddy)
    networks = list(caddy_service.get('networks', []))
    caddyfile = container_path(caddy, '/etc/caddy/Caddyfile')
    # Check the active container actually uses the Caddyfile this installer edits.
    command = caddy.get('Config', {}).get('Cmd', [])
    if '/etc/caddy/Caddyfile' not in command:
        raise InstallError('The active Caddy process is not configured from its mounted Caddyfile.')
    homepage = inspect('pivault-homepage')
    homepage_config = container_path(homepage, '/app/config')
    custom_js = homepage_config / 'custom.js'
    before = {p: snapshot(p) for p in (COMPOSE, caddyfile, custom_js)}
    caddy_candidate = apps_caddyfile(portal_caddyfile(caddyfile.read_text()))
    js_candidate = homepage_js(custom_js.read_text() if custom_js.exists() else '',
                               (SOURCE / 'stack/homepage-addon.js').read_text())
    old_container = inspect(SERVICE, missing=True)
    if old_container and (old_container.get('Config', {}).get('Labels', {}) or {}).get('org.pivault.managed') != LABEL:
        raise InstallError('An unmanaged pivault-users container already exists.')

    vault = inspect('pivault-vaultwarden')
    vault_plan = vault_configuration(original_compose, vault, CONFIG, container_path(vault, '/data'))
    if vault_plan['needs_restart']:
        image_reference = original_compose['services'][vault_plan['service']].get('image')
        if not image_reference or run(['docker', 'image', 'inspect', '--format', '{{.Id}}', image_reference]).strip() != vault.get('Image'):
            raise InstallError('The configured vault image differs from its running image. Review that image change separately before enrollment is installed.')
    if vault_plan['protect_admin']:
        caddy_candidate = private_vault_admin(caddy_candidate, vault_plan['host'])
    runtime_path = ROOT / 'users/maintenance.py'
    maintenance_config = CONFIG / 'users/maintenance.json'
    units = maintenance_units(runtime_path)
    for path in [*vault_plan['files'], runtime_path, maintenance_config,
                 *(SYSTEMD_DIR / name for name in units)]:
        before[path] = snapshot(path)
    timer_before = {name: {'enabled': unit_flag('is-enabled', name), 'active': unit_flag('is-active', name)}
                   for name in ('pivault-cinema.timer', 'pivault-maintenance.timer')}
    old_maintenance = json.loads(maintenance_config.read_text()) if maintenance_config.is_file() else {}
    cinema_enabled = (timer_before['pivault-cinema.timer']['enabled'] or
                      timer_before['pivault-cinema.timer']['active'] or old_maintenance.get('cinema_enabled', False))
    run(['systemd-analyze', 'calendar', '*-*-* 02:00:00 America/New_York'])

    app_source = SOURCE / 'app'
    fingerprint = hashlib.sha256()
    for path in sorted(app_source.rglob('*')):
        if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc':
            fingerprint.update(str(path.relative_to(app_source)).encode() + b'\0' + path.read_bytes())
    image = 'pivault-users:' + fingerprint.hexdigest()[:16]
    # Radicale's folder is mounted only once its portal entry exists in native.json.
    native_path = CONFIG / 'users/native.json'
    native_now = json.loads(native_path.read_text()) if native_path.is_file() else {}
    candidate_document = compose_configuration(vault_plan['document'], image=image,
        uid=uid, gid=gid, network_names=networks, radicale_config='radicale' in native_now)

    with tempfile.TemporaryDirectory(prefix='.pivault-users-', dir=COMPOSE.parent) as work:
        work = Path(work)
        candidate_caddy = work / 'Caddyfile'
        candidate_caddy.write_text(caddy_candidate)
        candidate_compose = work / 'candidate.yml'
        candidate_compose.write_text(yaml.safe_dump(candidate_document, sort_keys=False))
        # Existing relative Compose paths must resolve against the original folder.
        cc = ['docker', 'compose', '--project-directory', str(COMPOSE.parent), '-p', project,
              '-f', str(candidate_compose)]
        live = ['docker', 'compose', '--project-directory', str(COMPOSE.parent), '-p', project,
                '-f', str(COMPOSE)]
        original_json = json.loads(run(['docker', 'exec', CADDY, 'caddy', 'adapt',
            '--config', '/etc/caddy/Caddyfile', '--adapter', 'caddyfile']))
        verify_routes(original_json, adapt(candidate_caddy),
                      vault_host=vault_plan['host'] if vault_plan['protect_admin'] else None)
        say('Building and testing the portal image before changing the live configuration…')
        run(['docker', 'build', '--tag', image, str(app_source)], stage='Portal image build', timeout=1200)
        test_command = ['docker', 'run', '--rm', '--network', 'none', '--read-only',
            '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
            '--user', f'{uid}:{gid}', '--tmpfs', '/tmp:rw,noexec,nosuid,size=128m',
            '-e', 'PIVAULT_SECRET_KEY=installer-test-key-not-a-production-secret',
            '-e', 'PIVAULT_DATA=/tmp/pivault-check', image,
            'python', 'manage.py', 'test', 'portal', '--verbosity', '0']
        run(test_command, stage='Portal tests in the image', timeout=180)

        data_dir = CONFIG / 'users/data'
        env = CONFIG / 'users/users.env'
        notification_root = CONFIG / 'notifications'
        for path in (CONFIG / 'users', data_dir, notification_root):
            if path.is_symlink():
                raise InstallError('Expected a real persistent data directory: ' + str(path))
        state_before = data_dir.exists()
        env_before = snapshot(env)
        backup = ROOT / 'backups/users' / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + secrets.token_hex(3))
        backup.mkdir(mode=0o700, parents=True)
        os.chmod(backup, 0o700)
        for index, (path, value) in enumerate(before.items()):
            if value:
                (backup / (str(index) + '-' + path.name)).write_bytes(value[0])
        if env_before:
            (backup / 'users.env').write_bytes(env_before[0])
        CONFIG.joinpath('users').mkdir(mode=0o700, parents=True, exist_ok=True)
        data_dir.mkdir(mode=0o700, exist_ok=True)
        os.chown(data_dir, uid, gid)
        os.chmod(data_dir, 0o700)
        if not env.exists():
            write_file(env, 'PIVAULT_SECRET_KEY=' + secrets.token_urlsafe(64) + '\n')
        else:
            keys = dict(line.split('=', 1) for line in env.read_text().splitlines()
                        if line and not line.startswith('#') and '=' in line)
            if len(keys.get('PIVAULT_SECRET_KEY', '')) < 50:
                raise InstallError('The existing Users secret is missing or too short. It was not replaced.')
        os.chmod(env, 0o600)
        for path, value in vault_plan['files'].items():
            if path.parent == CONFIG / 'users':
                write_file(path, value)
                os.chmod(path, 0o640 if path.name == 'native.json' else 0o600)
                if path.name == 'native.json':
                    os.chown(path, 0, gid)
        if not notification_root.exists():
            notification_root.mkdir(mode=0o750)
            os.chown(notification_root, uid, gid)
        # This verifies producer-folder permissions as the actual runtime identity.
        permission_check = ['docker', 'run', '--rm', '--read-only', '--network', 'none',
            '--user', f'{uid}:{gid}', '-v', str(notification_root) + ':/notifications',
            '-e', 'PIVAULT_SECRET_KEY=installer-permission-check', image, 'python', '-c',
            'import os,tempfile; from pathlib import Path; p=Path("/notifications"); '
            'f=tempfile.NamedTemporaryFile(dir=p); f.close(); '
            'folders=[q for q in (p/"inbox",p/"decisions") if q.exists()]; '
            'assert all(os.access(q,os.R_OK|os.W_OK|os.X_OK) for q in folders); '
            'assert all(os.access(q,os.R_OK) for q in (p/"inbox").glob("*.json") '
            'if q.is_file() and not q.is_symlink())']
        run(permission_check)
        run(cc + ['config', '--quiet'])

        started = False
        old_stopped = False
        data_snapshot = False
        vault_changed = False
        timers_changed = False
        try:
            if old_container and old_container.get('State', {}).get('Running'):
                run(['docker', 'stop', SERVICE])
                old_stopped = True
            if state_before:
                shutil.copytree(data_dir, backup / 'users-data')
                data_snapshot = True
            say('Starting the portal on the private Docker network…')
            write_file(COMPOSE, yaml.safe_dump(candidate_document, sort_keys=False))
            started = True
            for path, value in vault_plan['files'].items():
                if path.parent != CONFIG / 'users':
                    write_file(path, value)
            if vault_plan['needs_restart']:
                vault_changed = True
                run(live + ['up', '-d', '--no-deps', vault_plan['service']], stage='Vault service')
            run(live + ['run', '--rm', '--no-deps', SERVICE, 'python', 'manage.py',
                        'migrate', '--noinput'], stage='Portal database migration')
            run(live + ['up', '-d', '--no-deps', SERVICE], stage='Portal start')
            wait_healthy()
            # Prove the private native administration credential works before exposing enrollment.
            for attempt in range(12):
                try:
                    run(['docker', 'exec', SERVICE, 'python', 'manage.py', 'shell', '-c',
                        'from portal.services import ADAPTERS; a=ADAPTERS["passwords"]; '
                        'a.client.admin(a.admin_token); print("Private vault enrollment connection verified.")'])
                    break
                except InstallError:
                    if attempt == 11:
                        raise
                    time.sleep(2)
            write_file(caddyfile, caddy_candidate)
            run(['docker', 'exec', CADDY, 'caddy', 'reload', '--config',
                 '/etc/caddy/Caddyfile', '--adapter', 'caddyfile'])
            portal_probe()
            publish_root_certificate(data_dir, uid, gid)
            write_file(custom_js, js_candidate, mode=0o644)
            runtime_path.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
            write_file(runtime_path, (SOURCE / 'jobs/maintenance.py').read_text(), mode=0o700)
            write_file(maintenance_config, json.dumps({'cinema_enabled': bool(cinema_enabled)}, indent=2) + '\n')
            for name, contents in units.items():
                write_file(SYSTEMD_DIR / name, contents, mode=0o644)
            timers_changed = True
            run(['systemctl', 'daemon-reload'])
            if timer_before['pivault-cinema.timer']['enabled'] or timer_before['pivault-cinema.timer']['active']:
                run(['systemctl', 'disable', '--now', 'pivault-cinema.timer'])
            run(['systemctl', 'enable', '--now', 'pivault-maintenance.timer'])
            try:
                run(['docker', 'exec', SERVICE, 'python', 'manage.py', 'maintain_clients', '--check-only'], timeout=180)
            except InstallError:
                say('The initial release check needs review in Admin notifications; it will retry at 2:00 AM.')
            # Bootstrap is a local administrator invitation, never a public claim.
            bootstrap = run(['docker', 'exec', SERVICE, 'python', 'manage.py', 'shell', '-c',
                'from portal.models import User; from django.core.management import call_command; '
                'call_command("first_admin", "snx") if not User.objects.exists() '
                'else print("An Admin account already exists. Sign in at ' + PORTAL_SITE + '/users/sign-in")'])
        except BaseException:
            say('Installation did not finish. Restoring the previous live configuration…')
            recovery_errors = []
            if started:
                with contextlib.suppress(Exception):
                    run(['docker', 'rm', '-f', SERVICE])
            if data_snapshot:
                try:
                    shutil.rmtree(data_dir)
                    shutil.copytree(backup / 'users-data', data_dir)
                    for path in [data_dir, *data_dir.rglob('*')]:
                        if not path.is_symlink():
                            os.chown(path, uid, gid)
                except Exception:
                    recovery_errors.append('Users database')
            for path, value in before.items():
                try:
                    restore_file(path, value)
                except Exception:
                    recovery_errors.append(str(path))
            if timers_changed:
                try:
                    run(['systemctl', 'daemon-reload'])
                    for name, state in timer_before.items():
                        run(['systemctl', 'enable' if state['enabled'] else 'disable', name])
                        run(['systemctl', 'start' if state['active'] else 'stop', name])
                except Exception:
                    recovery_errors.append('maintenance timer state')
            if vault_changed:
                try:
                    run(live + ['up', '-d', '--no-deps', vault_plan['service']])
                    if not vault.get('State', {}).get('Running'):
                        run(live + ['stop', vault_plan['service']])
                except Exception:
                    recovery_errors.append('previous Vaultwarden container')
            try:
                run(['docker', 'exec', CADDY, 'caddy', 'reload', '--config',
                     '/etc/caddy/Caddyfile', '--adapter', 'caddyfile'])
            except Exception:
                recovery_errors.append('Caddy reload')
            if old_container and (started or old_stopped):
                try:
                    run(live + ['up', '-d', '--no-deps', SERVICE])
                except Exception:
                    recovery_errors.append('previous Users container')
            if recovery_errors:
                say('Restoration needs review: ' + ', '.join(recovery_errors))
                say('Configuration/database backup: ' + str(backup))
            raise
        say('Installed: per-user vault enrollment, encrypted login saving, client download links and approval notifications.')
        say('Consolidated maintenance: 2:00 AM America/New_York. New client releases require Admin approval.')
        say('Existing video scheduling is ' + ('included in this maintenance run.' if cinema_enabled else 'still disabled, as it was before this installation.'))
        say('Other service-wide SSO and unfinished native adapters are not enabled by this update.')
        say('HTTPS uses the existing Caddy certificates; launcher CSS is unchanged.')
        say(bootstrap.strip())


def main():
    if os.geteuid() != 0:
        raise InstallError('Run this through the PiVault installer, which authenticates sudo once.')
    with open('/run/lock/pivault-users-install.lock', 'w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise InstallError('Another PiVault Users installation is running.') from None
        open_install_log()
        try:
            install_portal()
        finally:
            close_install_log()


if __name__ == '__main__':
    try:
        main()
    except (InstallError, ValueError, KeyError, OSError) as error:
        print('PiVault Users installation stopped: ' + str(error), file=sys.stderr)
        raise SystemExit(1)
