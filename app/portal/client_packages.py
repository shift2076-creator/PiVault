"""Discover official releases; publish a local copy only after explicit approval."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
import zipfile
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from packaging.version import Version, InvalidVersion
from .models import ClientPackage, ClientRelease, Notification, SiteSettings
from .notifications import notify

CLIENTS = {'android': ('Android app', '.apk', 'application/vnd.android.package-archive'),
           'firefox': ('Firefox extension', '.xpi', 'application/x-xpinstall')}
# Prefix for the file name a vault user downloads, e.g. Bitwarden-2026.10.0.apk.
DOWNLOAD_NAMES = {'android': 'Bitwarden', 'firefox': 'Bitwarden'}
FIREFOX_ID = '{446900e4-71c2-419f-a6a7-df9c091e268b}'
MAX_SIZE = 256 * 1024 * 1024


def cache_root():
    return Path(settings.PIVAULT_CLIENT_ROOT)


def safe_url(url, *, download=False):
    p = urlsplit(url)
    if p.scheme != 'https' or p.username or p.password or p.port not in (None, 443) or p.fragment:
        raise ValidationError('Invalid official client URL.')
    hosts = ('api.github.com', 'github.com', 'release-assets.githubusercontent.com',
        'objects.githubusercontent.com', 'github-releases.githubusercontent.com',
        'addons.mozilla.org', 'addons.cdn.mozilla.net', 'addons.mozilla.net', 'f-droid.org')
    if p.hostname not in hosts:
        raise ValidationError('Unexpected official client download host.')
    return url


class OfficialRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, url):
        safe_url(url, download=True)
        return super().redirect_request(request, fp, code, message, headers, url)


def http_open(url):
    safe_url(url)
    return build_opener(ProxyHandler({}), OfficialRedirects()).open(Request(url, headers={
        'User-Agent': 'PiVault-client-check/1', 'Accept-Encoding': 'identity',
        'Accept': 'application/json,application/octet-stream;q=0.9'}), timeout=30)


def read_json(url, limit=2 * 1024 * 1024):
    with http_open(url) as response:
        data = response.read(limit + 1)
        if len(data) > limit:
            raise ValidationError('Official release metadata was too large.')
        return json.loads(data)


def _bitwarden_artifact_rule(url):
    p = urlsplit(url)
    if p.hostname != 'github.com' or not p.path.startswith('/bitwarden/android/releases/download/') or not p.path.endswith('/com.x8bit.bitwarden.apk'):
        raise ValidationError('Unexpected Android release artifact.')


def _firefox_artifact_rule(url):
    p = urlsplit(url)
    if p.hostname != 'addons.mozilla.org' or not p.path.startswith('/firefox/downloads/file/') or not p.path.endswith('.xpi'):
        raise ValidationError('Unexpected Firefox release artifact.')


# Each client in CLIENTS needs one entry in ARTIFACT_RULES, DISCOVERERS and ARCHIVE_CHECKS.
def _fdroid_artifact_rule(package_id, url):
    p = urlsplit(url)
    name = p.path.rsplit('/', 1)[-1]
    if p.hostname != 'f-droid.org' or not p.path.startswith('/repo/') or not name.startswith(package_id + '_') or not name.endswith('.apk'):
        raise ValidationError('Unexpected F-Droid release artifact.')


ARTIFACT_RULES = {'android': _bitwarden_artifact_rule, 'firefox': _firefox_artifact_rule}


def validate_release(client, data):
    if client not in CLIENTS or set(data) != {'version', 'sha256', 'size', 'url', 'release_url'}:
        raise ValidationError('Invalid client release metadata.')
    try:
        version = Version(data['version'])
    except (InvalidVersion, TypeError):
        raise ValidationError('Invalid client version.') from None
    if version.is_prerelease or version.is_devrelease or len(data['version']) > 64:
        raise ValidationError('Only stable client releases can be offered.')
    if not re.fullmatch('[a-f0-9]{64}', data['sha256']):
        raise ValidationError('The official release has no valid SHA-256 digest.')
    if type(data['size']) is not int or not 0 < data['size'] <= MAX_SIZE:
        raise ValidationError('The official client package exceeds the allowed size.')
    safe_url(data['url']); safe_url(data['release_url'])
    ARTIFACT_RULES[client](data['url'])
    return data


def _discover_firefox():
    value = read_json('https://addons.mozilla.org/api/v5/addons/addon/bitwarden-password-manager/')
    if value.get('guid') != FIREFOX_ID:
        raise ValidationError('The Firefox add-on identity did not match Bitwarden.')
    current = value['current_version']; file = current['file']
    if file.get('status') != 'public':
        raise ValidationError('The Firefox package is not a published release.')
    return {'version': current['version'], 'sha256': file['hash'].removeprefix('sha256:'),
        'size': file['size'], 'url': file['url'],
        'release_url': 'https://addons.mozilla.org/en-US/firefox/addon/bitwarden-password-manager/'}


def _discover_bitwarden_android():
    releases = read_json('https://api.github.com/repos/bitwarden/android/releases?per_page=20')
    release = next(r for r in releases if not r.get('draft') and not r.get('prerelease') and r['tag_name'].endswith('-bwpm'))
    asset = next(a for a in release['assets'] if a['name'] == 'com.x8bit.bitwarden.apk')
    digest = asset.get('digest', '')
    if not digest.startswith('sha256:'):
        raise ValidationError('The Android release has no upstream SHA-256 digest.')
    return {'version': release['tag_name'].removeprefix('v').removesuffix('-bwpm'),
        'sha256': digest[7:], 'size': asset['size'], 'url': asset['browser_download_url'],
        'release_url': release['html_url']}


FDROID_INDEX_URL = 'https://f-droid.org/repo/index-v2.json'
FDROID_INDEX_LIMIT = 128 * 1024 * 1024
_INDEX_CACHE = {}


def fdroid_index():
    # One index read serves every F-Droid client in a maintenance run; maintain() clears the cache.
    if 'index' not in _INDEX_CACHE:
        _INDEX_CACHE['index'] = read_json(FDROID_INDEX_URL, limit=FDROID_INDEX_LIMIT)
    return _INDEX_CACHE['index']


def _discover_fdroid(package_id):
    package = fdroid_index().get('packages', {}).get(package_id)
    if not package:
        raise ValidationError('The F-Droid index does not list ' + package_id + '.')
    versions = sorted(package.get('versions', {}).values(),
        key=lambda value: value.get('manifest', {}).get('versionCode', 0), reverse=True)
    for version in versions:
        try:
            name = version['manifest']['versionName']; parsed = Version(name)
        except (InvalidVersion, TypeError, KeyError):
            continue
        if parsed.is_prerelease or parsed.is_devrelease:
            continue
        file = version['file']
        return {'version': name, 'sha256': file['sha256'], 'size': file['size'],
            'url': 'https://f-droid.org/repo/' + file['name'].lstrip('/'),
            'release_url': 'https://f-droid.org/packages/' + package_id + '/'}
    raise ValidationError('The F-Droid index has no stable release of ' + package_id + '.')


DISCOVERERS = {'android': _discover_bitwarden_android, 'firefox': _discover_firefox}


def discover(client):
    return validate_release(client, DISCOVERERS[client]())


def record_release(client, data):
    validate_release(client, data)
    identity = hashlib.sha256(json.dumps({'client': client, **data}, sort_keys=True).encode()).hexdigest()
    with transaction.atomic():
        package, _ = ClientPackage.objects.get_or_create(client=client)
        if package.current and Version(data['version']) < Version(package.current.version):
            package.checked_at = timezone.now(); package.last_error = ''
            package.save(update_fields=['checked_at', 'last_error'])
            return
        release, _ = ClientRelease.objects.get_or_create(id=identity, defaults={'client': client, **data})
        if package.current_id != release.id and release.notification_id is None:
            item = notify('clients', identity, CLIENTS[client][0] + ' update available',
                'Review this release before PiVault replaces its locally hosted download.',
                kind='clients.update', level='info', details={'release_id': identity, 'client': client,
                    'version': data['version'], 'current_version': package.current.version if package.current else '',
                    'sha256': data['sha256'], 'size': data['size'], 'release_url': data['release_url']})
            release.notification = item; release.save(update_fields=['notification'])
        package.latest = release; package.checked_at = timezone.now(); package.last_error = ''
        package.save(update_fields=['latest', 'checked_at', 'last_error'])


def release_path(release):
    if release.client not in CLIENTS or not re.fullmatch('[a-f0-9]{64}', release.sha256):
        raise ValidationError('Invalid cached release identity.')
    return cache_root() / (release.client + '-' + release.sha256 + CLIENTS[release.client][1])


def download_filename(client, version):
    return DOWNLOAD_NAMES[client] + '-' + version + CLIENTS[client][1]


def _is_cache_blob(name):
    return any(re.fullmatch(re.escape(client) + '-[a-f0-9]{64}' + re.escape(extension), name)
               for client, (_, extension, _) in CLIENTS.items())


def _check_firefox_archive(archive, release):
    if archive.getinfo('manifest.json').file_size > 1024 * 1024:
        raise ValidationError('Invalid Firefox manifest.')
    manifest = json.loads(archive.read('manifest.json'))
    gecko = manifest.get('browser_specific_settings', manifest.get('applications', {})).get('gecko', {})
    if gecko.get('id') != FIREFOX_ID or manifest.get('version') != release.version:
        raise ValidationError('The Firefox package identity or version did not match.')
    names = [name.lower() for name in archive.namelist()]
    if not any(name.startswith('meta-inf/') and name.endswith(('.rsa', '.sig')) for name in names):
        raise ValidationError('The Firefox package has no retained signing data.')


def _check_android_archive(archive, release):
    if 'AndroidManifest.xml' not in archive.namelist():
        raise ValidationError('The Android package is not an APK.')


ARCHIVE_CHECKS = {'android': _check_android_archive, 'firefox': _check_firefox_archive}


def verify_package(path, release):
    if path.stat().st_size != release.size:
        raise ValidationError('Client package size does not match the approved release.')
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    if digest.hexdigest() != release.sha256:
        raise ValidationError('Client package checksum does not match the approved release.')
    with zipfile.ZipFile(path) as archive:
        ARCHIVE_CHECKS[release.client](archive, release)


def apply_release(release):
    # Never called by a web view: only the scheduled management command uses it.
    item = release.notification
    if not item or item.kind != 'clients.update' or item.resolution != 'approve' or item.status != 'open':
        return False
    package = ClientPackage.objects.filter(client=release.client).select_related('current').first()
    if package and package.current and Version(package.current.version) > Version(release.version):
        item.status = 'resolved'; item.resolved_at = timezone.now()
        item.save(update_fields=['status', 'resolved_at'])
        return False
    data = {key: getattr(release, key) for key in ('version', 'sha256', 'size', 'url', 'release_url')}
    validate_release(release.client, data)
    destination = release_path(release)
    cache_root().mkdir(mode=0o750, parents=True, exist_ok=True)
    if shutil.disk_usage(cache_root()).free < release.size * 2 + 32 * 1024 * 1024:
        raise ValidationError('Insufficient space for a verified client download.')
    fd, temporary = tempfile.mkstemp(prefix='.client-', dir=cache_root())
    try:
        with os.fdopen(fd, 'wb') as output, http_open(release.url) as response:
            written = 0
            while chunk := response.read(1024 * 1024):
                written += len(chunk)
                if written > release.size:
                    raise ValidationError('Client download exceeded the approved size.')
                output.write(chunk)
            output.flush(); os.fsync(output.fileno())
        verify_package(Path(temporary), release)
        os.chmod(temporary, 0o444)
        os.replace(temporary, destination)
        with transaction.atomic():
            item = Notification.objects.select_for_update().get(pk=item.pk)
            if item.resolution != 'approve' or item.status != 'open':
                return False
            package, _ = ClientPackage.objects.get_or_create(client=release.client)
            package.previous = package.current; package.current = release
            package.save(update_fields=['previous', 'current'])
            item.status = 'resolved'; item.resolved_at = timezone.now()
            item.save(update_fields=['status', 'resolved_at'])
        # Keep the active and previous approved copy; remove only this cache's old blobs.
        keep = {str(release_path(value)) for p in ClientPackage.objects.select_related('current', 'previous')
                for value in (p.current, p.previous) if value}
        for path in cache_root().iterdir():
            if _is_cache_blob(path.name) and str(path) not in keep:
                path.unlink(missing_ok=True)
        return True
    finally:
        Path(temporary).unlink(missing_ok=True)


def maintain(check_only=False):
    if SiteSettings.is_offline():
        # Every step needs the internet; offline mode pauses them without raising notices.
        return []
    cache_root().mkdir(mode=0o750, parents=True, exist_ok=True)
    _INDEX_CACHE.clear()
    with (cache_root() / '.maintenance.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        errors = []
        approved = ClientRelease.objects.none() if check_only else ClientRelease.objects.filter(notification__kind='clients.update',
                notification__status='open', notification__resolution='approve').order_by('discovered_at')
        for release in approved:
            try:
                apply_release(release)
            except Exception:
                errors.append(release.client)
                notify('clients', 'apply-' + release.id, CLIENTS[release.client][0] + ' update failed',
                    'The approved download could not be verified or published. The previous local copy is retained; maintenance will retry.',
                    level='error')
        for client in CLIENTS:
            try:
                record_release(client, discover(client))
            except Exception:
                errors.append(client)
                package, _ = ClientPackage.objects.get_or_create(client=client)
                package.last_error = 'The official release could not be checked. The previous copy is retained.'
                package.save(update_fields=['last_error'])
                notify('clients', 'check-' + client + '-' + timezone.now().date().isoformat(),
                    CLIENTS[client][0] + ' release check failed', package.last_error, level='warning')
        return sorted(set(errors))


def client_rows():
    packages = {p.client: p for p in ClientPackage.objects.select_related('current', 'latest__notification')}
    rows = []
    for client, (label, _, _) in CLIENTS.items():
        p = packages.get(client)
        item = p.latest.notification if p and p.latest else None
        current = p.current if p else None
        available = bool(current and release_path(current).is_file())
        pending = bool(p and p.latest and p.latest_id != p.current_id)
        status = 'Up to date' if available and not pending else 'Not downloaded'
        if pending:
            status = 'Approved · queued for 2:00 AM' if item and item.resolution == 'approve' else (
                'Skipped this version' if item and item.resolution == 'skip' else 'Approval needed')
        if p and p.last_error:
            status = 'Check failed · local copy retained' if available else 'Check failed'
        rows.append({'key': client, 'label': label, 'current': current.version if current else '—',
            'latest': p.latest.version if p and p.latest else '—', 'checked_at': p.checked_at if p else None,
            'error': p.last_error if p else '', 'status': status, 'available': available,
            'review_url': '/notifications/' + item.id if item and pending else ''})
    return rows
