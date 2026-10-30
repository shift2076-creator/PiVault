"""Launcher status and Admin start, stop and restart for the PiVault apps.

The portal reaches Docker only through the socket proxy on the Compose network, and it sends
only these requests: read one container's state, and start, stop or restart one listed container.
Container names come from CONTAINERS below, never from the request.
"""
import json
import os
import urllib.error
import urllib.request

from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.views.decorators.http import require_GET, require_POST

from portal.notification_views import admin_only

PROXY = os.environ.get('PIVAULT_DOCKER_PROXY', 'http://pivault-docker-proxy:2375')
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
VERBS = ('start', 'stop', 'restart')
TIMEOUT_SECONDS = 10


def _proxy(method, path):
    request = urllib.request.Request(PROXY + path, method=method)
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return response.status, response.read()


def _state(name):
    try:
        _, body = _proxy('GET', f'/containers/{name}/json')
    except OSError:
        return 'unknown'
    state = json.loads(body).get('State', {})
    if state.get('Status') == 'running':
        return 'attention' if state.get('Health', {}).get('Status') == 'unhealthy' else 'running'
    if state.get('Status') in ('exited', 'created', 'dead'):
        return 'stopped'
    return 'unknown'


@require_GET
def status(request):
    if not request.user.is_authenticated or not request.user.is_active:
        return JsonResponse({'error': 'Sign in required.'}, status=401)
    can_control = request.user.role == 'admin'
    data = {'services': {key: _state(name) for key, name in CONTAINERS.items()},
            'can_control': can_control}
    if can_control:
        data['csrf_token'] = get_token(request)
    return JsonResponse(data)


@require_POST
@admin_only
def control(request, key, verb):
    name = CONTAINERS.get(key)
    if name is None or verb not in VERBS:
        return JsonResponse({'error': 'Unknown service or action.'}, status=404)
    try:
        _proxy('POST', f'/containers/{name}/{verb}')
    except urllib.error.HTTPError as error:
        # 304 means the container is already in the requested state.
        if error.code != 304:
            return JsonResponse({'error': 'The container could not be changed.'}, status=502)
    except OSError:
        return JsonResponse({'error': 'The container service is unavailable.'}, status=502)
    return JsonResponse({'ok': True})
