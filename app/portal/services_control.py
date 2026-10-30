"""Launcher status and Start, Stop and Restart for the PiVault containers.

The portal never talks to the Docker socket. It talks to a Docker socket proxy on the private
network, and the proxy allows only container inspection and start, stop and restart. This module
adds the second check: only the containers listed here can be named, so a request cannot reach any
other container through the proxy.
"""
import json
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings

# Launcher tile key -> container name. Contacts is a host service, not a container, and is not listed.
CONTAINERS = {
    'books': 'calibre-web',
    'games': 'pivault-arcade',
    'music': 'navidrome',
    'videos': 'jellyfin',
    'documents': 'pivault-paperless',
    'offline-wiki': 'pivault-kiwix',
    'inventory': 'pivault-homebox',
    'pantry': 'pivault-grocy',
    'photos': 'lychee',
}
ACTIONS = ('start', 'stop', 'restart')
TIMEOUT = 4
MAX_RESPONSE = 1024 * 1024


class ProxyError(Exception):
    """The proxy is unreachable or refused the request. Carries no upstream detail."""


def _request(method, path):
    base = getattr(settings, 'PIVAULT_DOCKER_PROXY', '')
    if not base:
        raise ProxyError('Docker proxy is not configured.')
    request = Request(base + path, method=method)
    try:
        with urlopen(request, timeout=TIMEOUT) as response:
            return response.status, response.read(MAX_RESPONSE)
    except HTTPError as error:
        return error.code, b''
    except (URLError, OSError) as error:
        raise ProxyError('Docker proxy is unavailable.') from error


def state(key):
    """Return running, attention, stopped or unknown for one launcher tile."""
    try:
        status, body = _request('GET', f'/containers/{CONTAINERS[key]}/json')
        if status == 404:
            return 'stopped'
        if status != 200:
            return 'unknown'
        info = json.loads(body)['State']
    except (ProxyError, ValueError, KeyError):
        return 'unknown'
    if info.get('Status') == 'running':
        health = (info.get('Health') or {}).get('Status')
        return 'attention' if health == 'unhealthy' else 'running'
    if info.get('Status') == 'restarting':
        return 'attention'
    return 'stopped'


def statuses():
    keys = list(CONTAINERS)
    with ThreadPoolExecutor(max_workers=len(keys)) as pool:
        return dict(zip(keys, pool.map(state, keys)))


def act(key, action):
    """Start, stop or restart one listed container. Raises ValueError for anything else."""
    if key not in CONTAINERS or action not in ACTIONS:
        raise ValueError('Unknown service or action.')
    status, _ = _request('POST', f'/containers/{CONTAINERS[key]}/{action}')
    if status not in (204, 304):
        raise ProxyError(f'Docker did not {action} the container.')
