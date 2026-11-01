#!/usr/bin/env python3
"""PiVault Android sync: install approved apps on one phone over USB, and enable or disable them.

Run this on the computer the phone is plugged into. It needs adb (Android platform-tools) on PATH and
uses only the Python standard library. PiVault generated this copy with a short-lived sign-in token;
do not share it. Pass --cafile with the PiVault root certificate if Python does not trust the portal.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = '__PIVAULT_BASE__'
TOKEN = '__PIVAULT_TOKEN__'
ANDROID_ID = re.compile(r'[0-9a-f]{8,64}')
DEVICE_LINE = re.compile(r'^(\S+)\s+device$', re.M)
UNAUTHORIZED_LINE = re.compile(r'^(\S+)\s+unauthorized$', re.M)
VERSION_NAME = re.compile(r'versionName=(\S+)')
NEW_STATE = re.compile(r'new state: (\S+)')
# The PiVault page on this computer's browser calls this local service. It accepts requests only from BASE.
LOCAL_PORT = 8765


def parse_devices(output):
    """Return the serials ready for use and the serials still waiting for the USB debugging prompt."""
    return DEVICE_LINE.findall(output), UNAUTHORIZED_LINE.findall(output)


def parse_android_id(output):
    value = output.strip()
    return value if ANDROID_ID.fullmatch(value) else None


def parse_version_name(dumpsys_output):
    match = VERSION_NAME.search(dumpsys_output)
    return match.group(1) if match else None


def parse_pm_state(output):
    match = NEW_STATE.search(output)
    return match.group(1) if match else None


def adb(binary, serial, *args, timeout=120):
    command = [binary, '-s', serial, *args] if serial else [binary, *args]
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    return result.returncode, result.stdout + result.stderr


def installed_version(binary, serial, package):
    """Return the installed versionName, '' if installed without one, or None if the package is missing."""
    _, listing = adb(binary, serial, 'shell', 'pm', 'path', package)
    if 'package:' not in listing:
        return None
    _, dump = adb(binary, serial, 'shell', 'dumpsys', 'package', package)
    return parse_version_name(dump) or ''


def set_enabled(binary, serial, package, enabled):
    if enabled:
        _, output = adb(binary, serial, 'shell', 'pm', 'enable', '--user', '0', package)
        wanted = 'enabled'
    else:
        _, output = adb(binary, serial, 'shell', 'pm', 'disable-user', '--user', '0', package)
        wanted = 'disabled-user'
    if parse_pm_state(output) == wanted:
        return 'enabled' if enabled else 'disabled'
    return 'error'


def download_verified(base, token, context, entry, directory, client):
    """Stream an approved APK to a temporary file and refuse it unless its size and SHA-256 match the manifest."""
    digest = hashlib.sha256()
    written = 0
    path = Path(directory) / (client + '.apk')
    request = urllib.request.Request(base + entry['url'], headers={'Authorization': 'Bearer ' + token})
    with urllib.request.urlopen(request, context=context, timeout=120) as response, path.open('wb') as output:
        for chunk in iter(lambda: response.read(1024 * 1024), b''):
            written += len(chunk)
            if written > entry['size']:
                raise ValueError('The download is larger than the approved size.')
            digest.update(chunk)
            output.write(chunk)
    if written != entry['size'] or digest.hexdigest() != entry['sha256']:
        raise ValueError('The download does not match the approved checksum.')
    return path


def api_call(base, token, context, path, payload=None):
    data = None if payload is None else json.dumps(payload).encode()
    headers = {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}
    request = urllib.request.Request(base + path, data=data, headers=headers,
                                     method='GET' if payload is None else 'POST')
    with urllib.request.urlopen(request, context=context, timeout=60) as response:
        return json.loads(response.read() or b'{}')


def sync_app(binary, serial, base, token, context, client, app, approved, directory):
    package = app['package']
    entry = approved.get(client)
    try:
        installed = installed_version(binary, serial, package)
        if entry and installed != entry['version']:
            apk = download_verified(base, token, context, entry, directory, client)
            # -d lets the install go to an older approved version, which is how an app is reverted.
            code, output = adb(binary, serial, 'install', '-r', '-d', str(apk), timeout=900)
            if 'Success' not in output:
                print('  %s: install failed: %s' % (client, output.strip()))
                return {'state': 'error', 'version': installed or ''}
            installed = entry['version']
        if installed is None:
            return {'state': 'missing', 'version': ''}
        state = set_enabled(binary, serial, package, app['enabled'])
        return {'state': state, 'version': installed}
    except (OSError, subprocess.TimeoutExpired, ValueError, urllib.error.URLError) as error:
        print('  %s: %s' % (client, error))
        return {'state': 'error', 'version': ''}


class SyncError(Exception):
    """A problem the person can fix on the phone or the computer. The message is shown as is."""


def sync_phone(context, only=None, log=print):
    """Sync the one connected phone. Pass a set of app names in only to sync just those apps."""
    binary = shutil.which('adb')
    if not binary:
        raise SyncError('adb was not found on PATH. Install Android platform-tools on this computer.')
    ready, unauthorized = parse_devices(adb(binary, None, 'devices')[1])
    if unauthorized:
        raise SyncError('Accept the USB debugging prompt on the phone, then run this again.')
    if len(ready) != 1:
        raise SyncError('Connect exactly one phone. Found %d.' % len(ready))
    serial = ready[0]
    android_id = parse_android_id(adb(binary, serial, 'shell', 'settings', 'get', 'secure', 'android_id')[1])
    if not android_id:
        raise SyncError('Could not read the Android ID from the phone.')
    model = adb(binary, serial, 'shell', 'getprop', 'ro.product.model')[1].strip()
    release = adb(binary, serial, 'shell', 'getprop', 'ro.build.version.release')[1].strip()
    try:
        device = api_call(BASE, TOKEN, context, '/devices/api/identify',
                          {'android_id': android_id, 'model': model, 'android_release': release})
        manifest = api_call(BASE, TOKEN, context, '/devices/api/manifest')
    except urllib.error.HTTPError as error:
        raise SyncError('The portal refused the request (HTTP %d). If it says 401, download the script again.' % error.code)
    approved = manifest.get('apps', {})
    log('Syncing %s' % device['name'])
    results = {}
    with tempfile.TemporaryDirectory() as directory:
        for client, app in sorted(device['apps'].items()):
            if only is not None and client not in only:
                continue
            results[client] = sync_app(binary, serial, BASE, TOKEN, context, client, app, approved, directory)
            log('  %s: %s %s' % (client, results[client]['state'], results[client]['version']))
    api_call(BASE, TOKEN, context, '/devices/api/%s/report' % device['id'], {'apps': results})
    return device['name'], results


class LocalHandler(BaseHTTPRequestHandler):
    context = None
    lock = threading.Lock()

    def _json(self, status, body, origin_ok):
        data = json.dumps(body).encode()
        self.send_response(status)
        if origin_ok:
            self.send_header('Access-Control-Allow-Origin', BASE)
            self.send_header('Vary', 'Origin')
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        allowed = self.headers.get('Origin') == BASE
        self.send_response(204 if allowed else 403)
        if allowed:
            self.send_header('Access-Control-Allow-Origin', BASE)
            self.send_header('Access-Control-Allow-Methods', 'GET, POST')
            self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.send_header('Content-Length', '0')
        self.end_headers()

    def do_GET(self):
        if self.path != '/status':
            return self._json(404, {'error': 'Not found.'}, False)
        self._json(200, {'service': 'pivault-android-sync'}, self.headers.get('Origin') == BASE)

    def do_POST(self):
        if self.headers.get('Origin') != BASE:
            return self._json(403, {'error': 'Only the PiVault page may call this service.'}, False)
        if self.path != '/sync':
            return self._json(404, {'error': 'Not found.'}, True)
        length = int(self.headers.get('Content-Length') or 0)
        try:
            body = json.loads(self.rfile.read(min(length, 4096)) or b'{}')
        except ValueError:
            return self._json(400, {'error': 'Invalid request.'}, True)
        only = body.get('apps') if isinstance(body, dict) else None
        if only is not None and (not isinstance(only, list) or not all(isinstance(app, str) for app in only)):
            return self._json(400, {'error': 'Invalid app list.'}, True)
        if not self.lock.acquire(blocking=False):
            return self._json(409, {'error': 'A sync is already running.'}, True)
        try:
            name, results = sync_phone(self.context, only=set(only) if only is not None else None,
                                       log=lambda line: None)
        except SyncError as error:
            return self._json(409, {'error': str(error)}, True)
        finally:
            self.lock.release()
        self._json(200, {'device': name, 'results': results}, True)

    def log_message(self, format, *args):
        pass


def serve(context):
    LocalHandler.context = context
    server = ThreadingHTTPServer(('127.0.0.1', LOCAL_PORT), LocalHandler)
    print('PiVault sync is running on 127.0.0.1:%d. Keep this window open while you use the PiVault page.' % LOCAL_PORT)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description='Sync approved PiVault apps to one USB-connected phone.')
    parser.add_argument('--cafile', help='PEM file with the PiVault root certificate')
    parser.add_argument('command', nargs='?', choices=['serve'],
                        help='serve: keep a local service running for the PiVault page')
    args = parser.parse_args(argv)
    context = ssl.create_default_context(cafile=args.cafile)
    if args.command == 'serve':
        return serve(context)
    try:
        sync_phone(context)
    except SyncError as error:
        print(error)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
