"""Android device registry: Admin pages, and a token-authenticated API for the USB sync helper.

The helper runs on the computer the phone is plugged into. It signs in with a short-lived token that
is issued to an Admin session and embedded in the helper script. The token is a signed value, so the
portal stores nothing secret. ADB keys never reach the portal.
"""
from functools import wraps
import hashlib
import hmac
import json
from pathlib import Path
import re
import ssl

from django.conf import settings
from django.core import signing
from django.db import transaction
from django.http import FileResponse, Http404, HttpResponse, HttpResponseBadRequest, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from .client_packages import CLIENTS, download_filename, release_path
from .models import ClientPackage, ClientRelease, Device, DeviceApp, User

# Android package ids for the apps a phone can receive. Firefox is a browser add-on, not an Android app.
# The list is the only gate: an app missing from it cannot be synced or toggled from the portal.
DEVICE_PACKAGES = {'android': 'com.x8bit.bitwarden', 'fdroid': 'org.fdroid.fdroid', 'adaway': 'org.adaway',
                   'davx5': 'at.bitfire.davdroid', 'jellyfin': 'org.jellyfin.mobile', 'organicmaps': 'app.organicmaps',
                   'vibrdrome': 'com.vibrdrome.app'}
# Names and one-line descriptions shown on the device page, keyed like DEVICE_PACKAGES.
DEVICE_DESCRIPTIONS = {
    'android': ('Bitwarden', 'Password manager for your Vaultwarden server'),
    'fdroid': ('F-Droid', 'Open-source app store for the local repo'),
    'adaway': ('AdAway', 'Blocks ads and trackers system-wide'),
    'davx5': ('DAVx⁵', 'Syncs contacts and calendars with your server'),
    'jellyfin': ('Jellyfin', 'Client for your Jellyfin media server'),
    'organicmaps': ('Organic Maps', 'Offline maps and navigation'),
    'vibrdrome': ('Vibrdrome', 'Client for your Navidrome music server'),
}
# The device page may call only the sync service on the sync computer (device_sync/pivault_android_sync.py serve).
DEVICE_PAGE_CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
                   "script-src 'self'; connect-src 'self' http://127.0.0.1:8765; frame-ancestors 'none'; "
                   "form-action 'self'; base-uri 'none'")
TOKEN_SALT = 'pivault.devices.helper'
TOKEN_SECONDS = 2 * 60 * 60
HELPER = Path(__file__).resolve().parent / 'device_sync' / 'pivault_android_sync.py'
ANDROID_ID = re.compile(r'[0-9a-f]{8,64}')
APP_STATES = {'enabled', 'disabled', 'missing', 'error', 'unknown'}


def _identity(android_id):
    # Keyed, so the stored value cannot be matched to a phone without the portal's secret key.
    return hmac.new(settings.SECRET_KEY.encode(), ('pivault-device:' + android_id).encode(), hashlib.sha256).hexdigest()


def _issue_token(user):
    return signing.dumps({'uid': user.pk}, salt=TOKEN_SALT, compress=True)


def _bearer_admin(request):
    header = request.headers.get('Authorization', '')
    if not header.startswith('Bearer '):
        return None
    try:
        claims = signing.loads(header[len('Bearer '):], salt=TOKEN_SALT, max_age=TOKEN_SECONDS)
    except signing.BadSignature:
        return None
    return User.objects.filter(pk=claims.get('uid'), is_active=True, role='admin').first()


def device_api(view):
    """Token only, never a session. A browser cannot attach the Authorization header across sites, so CSRF is not needed."""
    @csrf_exempt
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if _bearer_admin(request) is None:
            return JsonResponse({'error': 'Device sync token required.'}, status=401)
        return view(request, *args, **kwargs)
    return wrapped


def admin_page(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('/users/sign-in')
        if request.user.role != 'admin':
            return HttpResponseForbidden()
        return view(request, *args, **kwargs)
    return wrapped


def _json_body(request):
    try:
        data = json.loads(request.body or b'{}')
    except (ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _text(value, limit):
    return value.strip()[:limit] if isinstance(value, str) else ''


def _device_json(device):
    apps = {app.client: {'enabled': app.enabled, 'package': DEVICE_PACKAGES[app.client]}
            for app in device.apps.all() if app.client in DEVICE_PACKAGES}
    return {'id': str(device.id), 'name': device.name, 'apps': apps}


@device_api
@require_POST
def identify(request):
    body = _json_body(request)
    android_id = body.get('android_id') if body else None
    if not isinstance(android_id, str) or not ANDROID_ID.fullmatch(android_id):
        return JsonResponse({'error': 'A valid Android ID is required.'}, status=400)
    model = _text(body.get('model'), 80)
    release = _text(body.get('android_release'), 16)
    now = timezone.now()
    with transaction.atomic():
        device, created = Device.objects.get_or_create(identity=_identity(android_id), defaults={
            'name': model or 'Android device', 'model': model, 'android_release': release, 'last_seen': now})
        if not created:
            device.model = model or device.model
            device.android_release = release or device.android_release
            device.last_seen = now
            device.save(update_fields=['model', 'android_release', 'last_seen'])
        for client in DEVICE_PACKAGES:
            DeviceApp.objects.get_or_create(device=device, client=client)
    return JsonResponse(_device_json(device))


@device_api
@require_POST
def report(request, device_id):
    device = get_object_or_404(Device, pk=device_id)
    body = _json_body(request)
    apps = body.get('apps') if body else None
    if not isinstance(apps, dict) or any(client not in DEVICE_PACKAGES for client in apps):
        return JsonResponse({'error': 'Unknown app in report.'}, status=400)
    if any(not isinstance(value, dict) or value.get('state') not in APP_STATES for value in apps.values()):
        return JsonResponse({'error': 'Invalid app state in report.'}, status=400)
    now = timezone.now()
    with transaction.atomic():
        for client, value in apps.items():
            DeviceApp.objects.filter(device=device, client=client).update(
                observed_state=value['state'], observed_version=_text(value.get('version'), 64), observed_at=now)
        device.last_seen = now
        device.save(update_fields=['last_seen'])
    return JsonResponse({'ok': True})


@device_api
@require_GET
def manifest(request):
    apps = {}
    for package in ClientPackage.objects.select_related('current').filter(client__in=DEVICE_PACKAGES, current__isnull=False):
        release = package.current
        if release_path(release).is_file():
            apps[package.client] = {'version': release.version, 'sha256': release.sha256, 'size': release.size,
                                    'url': '/devices/api/apk/' + package.client}
    return JsonResponse({'apps': apps})


@device_api
@require_GET
def apk(request, client):
    if client not in DEVICE_PACKAGES:
        raise Http404()
    package = ClientPackage.objects.select_related('current').filter(client=client).first()
    if not package or not package.current:
        raise Http404('No approved local download is available.')
    path = release_path(package.current)
    if not path.is_file() or path.is_symlink():
        raise Http404('The approved local download is unavailable.')
    return FileResponse(path.open('rb'), as_attachment=True,
                        filename=download_filename(client, package.current.version), content_type=CLIENTS[client][2])


def _version_choices():
    # Approved releases whose local copy is a regular file, newest first. set_version accepts only these.
    choices = {}
    for release in ClientRelease.objects.filter(notification__resolution='approve').order_by('-discovered_at'):
        path = release_path(release)
        if path.is_file() and not path.is_symlink():
            choices.setdefault(release.client, []).append(release)
    return choices


@admin_page
@require_GET
def overview(request):
    rows = []
    versions = _version_choices()
    pinned = {package.client: package.current_id for package in ClientPackage.objects.all()}
    for device in Device.objects.prefetch_related('apps').order_by('name', 'first_seen'):
        apps = {app.client: app for app in device.apps.all()}
        rows.append({'device': device, 'apps': [
            {'client': client, 'label': CLIENTS[client][0], 'name': DEVICE_DESCRIPTIONS[client][0],
             'description': DEVICE_DESCRIPTIONS[client][1], 'app': apps.get(client),
             'enabled': apps[client].enabled if client in apps else True,
             'versions': versions.get(client, []), 'pinned': pinned.get(client)}
            for client in DEVICE_PACKAGES]})
    response = render(request, 'devices.html', {'rows': rows})
    response['Content-Security-Policy'] = DEVICE_PAGE_CSP
    return response


@admin_page
@require_POST
def rename(request, device_id):
    device = get_object_or_404(Device, pk=device_id)
    name = request.POST.get('name', '').strip()
    if not 1 <= len(name) <= 80:
        return HttpResponseBadRequest('The name must be 1 to 80 characters.')
    device.name = name
    device.save(update_fields=['name'])
    return redirect('/devices')


@admin_page
@require_POST
def set_app(request, device_id, client):
    if client not in DEVICE_PACKAGES:
        raise Http404()
    app = get_object_or_404(DeviceApp, device_id=device_id, client=client)
    value = request.POST.get('enabled')
    if value not in ('on', 'off'):
        return HttpResponseBadRequest('Choose on or off.')
    app.enabled = value == 'on'
    app.save(update_fields=['enabled'])
    return redirect('/devices')


@admin_page
@require_POST
def set_version(request, client):
    # The chosen release becomes the version every phone syncs. Choosing an older approved release is how an app is reverted.
    if client not in DEVICE_PACKAGES:
        raise Http404()
    release = get_object_or_404(ClientRelease.objects.filter(notification__resolution='approve'),
                                pk=request.POST.get('release', ''), client=client)
    path = release_path(release)
    if not path.is_file() or path.is_symlink():
        return HttpResponseBadRequest('That release has no local copy.')
    with transaction.atomic():
        package, _ = ClientPackage.objects.get_or_create(client=client)
        if package.current_id != release.id:
            package.previous = package.current
            package.current = release
            package.save(update_fields=['previous', 'current'])
            # Phones keep reporting the old version until their next sync, so their state is no longer current.
            DeviceApp.objects.filter(client=client).update(observed_state='unknown')
    return redirect('/devices')


@admin_page
@require_GET
def helper_script(request):
    # Placeholders are replaced here; the source file itself keeps them, so it cannot be run with a stale token.
    source = HELPER.read_text(encoding='utf-8')
    script = (source.replace('__PIVAULT_BASE__', settings.PUBLIC_ORIGIN)
                    .replace('__PIVAULT_TOKEN__', _issue_token(request.user)))
    return HttpResponse(script, content_type='text/x-python; charset=utf-8',
                        headers={'Content-Disposition': 'attachment; filename="pivault-android-sync.py"'})


def root_certificate():
    # Only the public root certificate is copied into the data directory by the installer, never the CA key.
    path = Path(settings.DATA_DIR) / 'caddy-root.crt'
    return None if not path.is_file() or path.is_symlink() else path


def root_fingerprint(path):
    # SHA-256 of the DER certificate, as colon-separated uppercase pairs: the form browsers and OS viewers display.
    # Returns '' when the file is not a readable PEM certificate, so the page can omit the line.
    try:
        der = ssl.PEM_cert_to_DER_cert(Path(path).read_text(encoding='ascii'))
    except (OSError, ValueError, UnicodeDecodeError):
        return ''
    digest = hashlib.sha256(der).hexdigest().upper()
    return ':'.join(digest[i:i + 2] for i in range(0, len(digest), 2))


@admin_page
@require_GET
def ca_certificate(request):
    path = root_certificate()
    if path is None:
        raise Http404('The PiVault root certificate has not been installed yet.')
    return FileResponse(path.open('rb'), as_attachment=True, filename='pivault-root.crt',
                        content_type='application/x-x509-ca-cert')
