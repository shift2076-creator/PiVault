"""Load private, installer-owned integration configuration. No secrets reach forms."""
import json
from pathlib import Path
from urllib.parse import urlsplit
from django.conf import settings

PUBLIC_URLS = {'pivault': 'https://192.168.4.90:8443/users/sign-in'}


def configure():
    if not settings.PIVAULT_NATIVE_CONFIG:
        return
    config = json.loads(Path(settings.PIVAULT_NATIVE_CONFIG).read_text())
    from .services import ADAPTERS
    from .vaultwarden import VaultwardenAdapter
    from .jellyfin import JellyfinAdapter
    from .homebox import HomeboxAdapter
    from .navidrome import NavidromeAdapter
    for key, value in config.get('public_urls', {}).items():
        parsed = urlsplit(value)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('Native client links require an HTTPS address.')
        PUBLIC_URLS[key] = value
    vault = config.get('vaultwarden')
    if vault:
        ADAPTERS['passwords'] = VaultwardenAdapter(vault['origin'], vault['admin_token'], PUBLIC_URLS['passwords'])
    music = config.get('navidrome')
    if music:
        ADAPTERS['music'] = NavidromeAdapter(music['origin'], music['admin_username'],
            music['admin_password'], library_ids=music.get('library_ids'))
    video = config.get('jellyfin')
    if video:
        ADAPTERS['videos'] = JellyfinAdapter(video['origin'], video['api_key'])
    inventory = config.get('homebox')
    if inventory:
        ADAPTERS['inventory'] = HomeboxAdapter(inventory['origin'],
            allow_isolated=inventory.get('allow_isolated', False), shared_token=inventory.get('shared_token', ''),
            shared_group_id=inventory.get('shared_group_id', ''))


def credential_plan(user):
    from .services import SERVICES, ADAPTERS
    result = {'pivault': {'name': 'PiVault', 'username': user.email, 'uri': PUBLIC_URLS['pivault']}}
    for key, label in SERVICES:
        if user.service_access.get(key, 'none') == 'none':
            continue
        adapter = ADAPTERS.get(key)
        method = getattr(adapter, 'credential_username', None)
        if not method:
            continue
        username = method(user)
        if username is None:
            continue
        if key not in PUBLIC_URLS:
            from django.core.exceptions import ValidationError
            raise ValidationError(label + ' needs its HTTPS client address configured.')
        result[key] = {'name': 'PiVault ' + label, 'username': username, 'uri': PUBLIC_URLS[key]}
    return result
