"""Prepare the vault connection without overwriting native account credentials."""
import copy
import json
from pathlib import Path
import secrets
from urllib.parse import urlsplit

from configuration import VAULT_SITE


def environment(container):
    return dict(item.split('=', 1) for item in container.get('Config', {}).get('Env', []) if '=' in item)


def vault_configuration(document, container, config_root, mounted_data):
    labels = container.get('Config', {}).get('Labels', {}) or {}
    service = labels.get('com.docker.compose.service')
    if service not in document.get('services', {}):
        raise ValueError('Vaultwarden is not part of the expected Compose project.')
    env = environment(container)
    # Vaultwarden's public address is its own HTTPS port; a different DOMAIN is applied by recreating it.
    public_url = VAULT_SITE
    domain_changed = env.get('DOMAIN', '').rstrip('/') != public_url
    parsed = urlsplit(public_url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path:
        raise ValueError('Vaultwarden needs its existing HTTPS origin configured before enrollment is enabled.')
    native_path = config_root / 'users/native.json'
    native = json.loads(native_path.read_text()) if native_path.is_file() else {}
    data_path = mounted_data / 'config.json'
    vault_settings = json.loads(data_path.read_text()) if data_path.is_file() else {}
    if vault_settings.get('smtp_host') or env.get('SMTP_HOST'):
        raise ValueError('This offline enrollment flow requires the existing SMTP-disabled vault configuration.')
    existing = vault_settings.get('admin_token') or env.get('ADMIN_TOKEN', '')
    raw = native.get('vaultwarden', {}).get('admin_token', '')
    if existing and not existing.startswith('$argon2'):
        raw = existing
    if existing.startswith('$argon2') and not raw:
        raise ValueError('The existing vault Admin token is hashed. Its matching private token is required; the installer will not reset it.')
    if not raw:
        raw = secrets.token_urlsafe(48)
    changes = {}
    if 'admin_token' in vault_settings and not vault_settings['admin_token']:
        vault_settings['admin_token'] = raw
        changes[data_path] = json.dumps(vault_settings, indent=2) + '\n'
    # Preserve all existing native settings, including registration restrictions.
    port = env.get('ROCKET_PORT', '80')
    if not port.isdigit() or not 0 < int(port) < 65536:
        raise ValueError('The vault internal port is invalid.')
    origin = 'http://' + service + ':' + port
    native.setdefault('public_urls', {})['passwords'] = public_url
    protect = not bool(existing) or native.get('vaultwarden', {}).get('private_admin', False)
    native['vaultwarden'] = {'origin': origin, 'admin_token': raw, 'private_admin': protect}
    changes[native_path] = json.dumps(native, indent=2) + '\n'
    env_path = config_root / 'users/vaultwarden.env'
    if any(c in raw for c in '\r\n'):
        raise ValueError('The existing private vault token is not supported by an environment file.')
    result = copy.deepcopy(document)
    target = result['services'][service]
    configured = target.setdefault('environment', {})
    if isinstance(configured, list):
        configured = [value for value in configured if not value.startswith('DOMAIN=')] + ['DOMAIN=' + public_url]
        target['environment'] = configured
    else:
        configured['DOMAIN'] = public_url
    if not existing:
        changes[env_path] = 'ADMIN_TOKEN=' + raw + '\n'
        files = target.get('env_file', [])
        if isinstance(files, str):
            files = [files]
        target['env_file'] = [item for item in files if item != str(env_path)] + [str(env_path)]
        configured = target.get('environment', {})
        if isinstance(configured, dict) and not configured.get('ADMIN_TOKEN'):
            configured.pop('ADMIN_TOKEN', None)
        elif isinstance(configured, list):
            target['environment'] = [value for value in configured if not value.startswith('ADMIN_TOKEN=')]
    return {'document': result, 'files': changes, 'service': service,
            'host': parsed.hostname, 'public_url': public_url, 'container': container,
            'needs_restart': not bool(existing) or data_path in changes or domain_changed,
            'protect_admin': protect}


def maintenance_units(runtime_path):
    return {
        'pivault-maintenance.service': '''[Unit]
Description=PiVault consolidated nightly maintenance
Wants=network-online.target
After=network-online.target docker.service
Requires=docker.service

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 ''' + str(runtime_path) + '''
TimeoutStartSec=infinity
''',
        'pivault-maintenance.timer': '''[Unit]
Description=PiVault maintenance daily at 02:00 America/New_York

[Timer]
OnCalendar=*-*-* 02:00:00 America/New_York
AccuracySec=1s
Persistent=false
Unit=pivault-maintenance.service

[Install]
WantedBy=timers.target
'''}
