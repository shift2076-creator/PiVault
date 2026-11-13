"""Pure configuration transformations used by the installer and its tests."""
import copy
import fnmatch
import re

BEGIN = '# BEGIN PIVAULT USERS INSTALLER'
END = '# END PIVAULT USERS INSTALLER'
JS_BEGIN = '/* BEGIN PIVAULT USERS INSTALLER */'
JS_END = '/* END PIVAULT USERS INSTALLER */'
# The PiVault launcher and portal are the root address; Vaultwarden has its own HTTPS port.
PORTAL_HOST = '192.168.4.90'
PORTAL_PORT = 443
PORTAL_SITE = f'https://{PORTAL_HOST}'
# The launcher used to live on 8443. That address now only redirects to the root.
OLD_LAUNCHER_PORT = 8443
OLD_LAUNCHER_SITE = f'https://{PORTAL_HOST}:{OLD_LAUNCHER_PORT}'
VAULT_PORT = 8448
VAULT_SITE = f'https://{PORTAL_HOST}:{VAULT_PORT}'
SERVICE = 'pivault-users'
# Radicale's config folder on the Pi. The portal writes its htpasswd login file here.
RADICALE_CONFIG_FOLDER = '/home/snx/radicale/config'
MUSIC_PRIVATE_FOLDER = '/srv/disk3/music-private'

PORTAL = '''
    # BEGIN PIVAULT USERS INSTALLER
    @pivault_users_installer path /join /join/* /users /users/* /notifications /notifications/* /certificate /certificate/* /devices /devices/* /services /services/* /internal /internal/* /favicon.ico /apple-touch-icon.png /manifest.webmanifest
    handle @pivault_users_installer {
        @pivault_users_internal path /internal /internal/*
        respond @pivault_users_internal 404
        reverse_proxy pivault-users:8000 {
            header_up X-Forwarded-Proto https
            header_up X-PiVault-Client-IP {remote_host}
        }
    }
    # END PIVAULT USERS INSTALLER
'''
# Launcher sign-in gate. Portal routes (including /services) are excluded so the portal answers them itself.
LAUNCHER_BEGIN = '# BEGIN PIVAULT LAUNCHER AUTH'
LAUNCHER_END = '# END PIVAULT LAUNCHER AUTH'
LAUNCHER_AUTH = '''
    # BEGIN PIVAULT LAUNCHER AUTH
    @pivault_launcher_auth {
        not path /join /join/* /users /users/* /notifications /notifications/* /devices /devices/* /services /services/* /internal /internal/* /music /music/* /favicon.ico /apple-touch-icon.png /manifest.webmanifest
    }
    header @pivault_launcher_auth Cache-Control "no-store, private"
    forward_auth @pivault_launcher_auth pivault-users:8000 {
        uri /internal/gate
        header_up Host 192.168.4.90
        header_up X-Forwarded-Proto https
        header_up X-PiVault-Client-IP {remote_host}
        @pivault_launcher_login status 401
        handle_response @pivault_launcher_login {
            redir * /users/sign-in 303
        }
    }
    # END PIVAULT LAUNCHER AUTH
'''


# HTTPS sites for the apps that were plain HTTP on the LAN. Each keeps its port number.
APPS_BEGIN = '# BEGIN PIVAULT APP HTTPS SITES'
APPS_END = '# END PIVAULT APP HTTPS SITES'
APP_SITES = [
    (5232, '    reverse_proxy radicale:5232\n'),
    (8000, '    reverse_proxy pivault-arcade:8000\n'),
    (8083, '    reverse_proxy calibre-web:8083\n'),
    (8084, '    reverse_proxy pivault-kiwix:8080\n'),
    (8085, '    reverse_proxy pivault-paperless:8000\n'),
    (8096, '    reverse_proxy jellyfin:8096\n'),
    (8446, '    request_body {\n        max_size 2048MB\n    }\n'
           '    reverse_proxy lychee:8000 {\n        header_up X-Forwarded-Proto https\n    }\n'),
    (8447, '    reverse_proxy lychee:8000\n'),
]
APP_PORTS = {port for port, _ in APP_SITES}


def replace_managed(text, begin, end, replacement=''):
    if begin not in text and end not in text:
        return text
    if text.count(begin) != 1 or text.count(end) != 1:
        raise ValueError('The existing managed block is incomplete or duplicated.')
    start = text.index(begin)
    finish = text.index(end, start) + len(end)
    line_start = text.rfind('\n', 0, start) + 1
    if not text[line_start:start].strip():
        start = max(0, line_start - 1)
    line_end = text.find('\n', finish)
    if line_end != -1 and not text[finish:line_end].strip():
        finish = line_end + 1
    return text[:start] + replacement + text[finish:]


def tokens(text):
    # Caddy placeholders are single tokens, not block braces. Comments and
    # quoted strings cannot open or close a site block.
    pattern = r'#[^\n]*|"(?:\\.|[^"\\])*"|`[^`]*`|[^\s]+'
    for match in re.finditer(pattern, text):
        value = match.group()
        if not value.startswith('#'):
            yield value, match.start(), match.end()


VAULT_ROUTE = 'reverse_proxy vaultwarden:80'
LAUNCHER_ROUTE = 'reverse_proxy homepage:3000'
ROOT_NAMES = {PORTAL_SITE, f'{PORTAL_HOST}:{PORTAL_PORT}'}
OLD_LAUNCHER_NAMES = {OLD_LAUNCHER_SITE, f'{PORTAL_HOST}:{OLD_LAUNCHER_PORT}'}
VAULT_NAMES = {VAULT_SITE, f'{PORTAL_HOST}:{VAULT_PORT}'}


def site_blocks(text):
    # Top-level Caddy site blocks, each with its address names and character positions.
    depth = 0
    header = []
    header_start = None
    opened = None
    blocks = []
    for value, start, finish in tokens(text):
        if value == '{':
            if depth == 0:
                names = [x.strip(',').strip('"`') for x in header]
                opened = (names, header_start, finish)
                header = []
            depth += 1
        elif value == '}':
            depth -= 1
            if depth < 0:
                raise ValueError('Caddyfile has unbalanced blocks.')
            if depth == 0:
                names, begin, body = opened
                blocks.append({'names': names, 'start': begin, 'body': text[body:start], 'end': finish})
                header = []
        elif depth == 0:
            if not header:
                header_start = start
            header.append(value)
    if depth:
        raise ValueError('Caddyfile has unbalanced blocks.')
    return blocks


def move_vault_to_own_port(text):
    # Live layout: Vaultwarden on the root address, the launcher on 8443.
    # Target layout: the launcher on the root address, Vaultwarden on VAULT_PORT,
    # and 8443 redirects to the root so old bookmarks still work.
    blocks = site_blocks(text)
    if any(set(block['names']) & VAULT_NAMES for block in blocks):
        return text
    roots = [block for block in blocks if set(block['names']) & ROOT_NAMES]
    launchers = [block for block in blocks if set(block['names']) & OLD_LAUNCHER_NAMES]
    if (len(roots) != 1 or len(launchers) != 1 or VAULT_ROUTE not in roots[0]['body']
            or LAUNCHER_ROUTE not in launchers[0]['body']):
        raise ValueError('Expected Vaultwarden on ' + PORTAL_SITE + ' and the launcher on ' + OLD_LAUNCHER_SITE + '.')
    root, launcher = roots[0], launchers[0]
    redirect = ('%s {\n    tls internal\n    redir %s{uri} 302\n}\n' % (OLD_LAUNCHER_SITE, PORTAL_SITE))
    vault = VAULT_SITE + ' {' + root['body'] + '}\n'
    edits = [
        (root['start'], root['end'], PORTAL_SITE + ' {' + launcher['body'] + '}'),
        (launcher['start'], launcher['end'], redirect + vault),
    ]
    for start, finish, replacement in sorted(edits, reverse=True):
        text = text[:start] + replacement + text[finish:]
    return text


def portal_caddyfile(text):
    clean = replace_managed(text, BEGIN, END)
    clean = replace_managed(clean, LAUNCHER_BEGIN, LAUNCHER_END)
    clean = move_vault_to_own_port(clean)
    if '@pivault_users_installer' in clean:
        raise ValueError('The portal matcher already exists outside its managed block.')
    depth = 0
    header = []
    matches = []
    for value, start, finish in tokens(clean):
        if value == '{':
            if depth == 0:
                names = [x.strip(',').strip('"`') for x in header]
                target = any(x in (f'{PORTAL_HOST}:{PORTAL_PORT}', PORTAL_SITE) for x in names)
                if target:
                    matches.append(finish)
                header = []
            depth += 1
        elif value == '}':
            depth -= 1
            if depth < 0:
                raise ValueError('Caddyfile has unbalanced blocks.')
            if depth == 0:
                header = []
        elif depth == 0:
            header.append(value)
    if depth or len(matches) != 1:
        raise ValueError('Expected one explicit ' + PORTAL_SITE + ' site block in the active Caddyfile.')
    position = matches[0]
    return clean[:position] + PORTAL + LAUNCHER_AUTH + clean[position:]


def remove_site_blocks(text, ports):
    targets = {f'https://{PORTAL_HOST}:{port}' for port in ports}
    depth = 0
    header = []
    header_start = None
    block_start = None
    removals = []
    for value, start, finish in tokens(text):
        if value == '{':
            if depth == 0:
                names = [x.strip(',').strip('"`') for x in header]
                block_start = header_start if any(name in targets for name in names) else None
                header = []
            depth += 1
        elif value == '}':
            depth -= 1
            if depth == 0:
                if block_start is not None:
                    removals.append((block_start, finish))
                    block_start = None
                header = []
        elif depth == 0:
            if not header:
                header_start = start
            header.append(value)
    for start, finish in reversed(removals):
        text = text[:start] + text[finish:]
    return text


def apps_caddyfile(text):
    clean = replace_managed(text, APPS_BEGIN, APPS_END)
    clean = remove_site_blocks(clean, APP_PORTS)
    sites = []
    for port, body in APP_SITES:
        sites.append('https://%s:%d {\n    tls internal\n    encode zstd gzip\n%s}\n' % (PORTAL_HOST, port, body))
    block = APPS_BEGIN + '\n' + '\n'.join(sites) + APPS_END
    return clean.rstrip('\n') + '\n\n' + block + '\n'


def private_vault_admin(text, site=VAULT_SITE):
    begin = '# BEGIN PIVAULT PRIVATE VAULT ADMIN'
    end = '# END PIVAULT PRIVATE VAULT ADMIN'
    clean = replace_managed(text, begin, end)
    depth = 0; header = []; positions = []
    for value, start, finish in tokens(clean):
        if value == '{':
            if depth == 0:
                names = [x.strip(',').strip('"`') for x in header]
                if any(name in (site, site[len('https://'):]) for name in names):
                    positions.append(finish)
                header = []
            depth += 1
        elif value == '}':
            depth -= 1
            if depth == 0: header = []
        elif depth == 0:
            header.append(value)
    if len(positions) != 1:
        raise ValueError('Expected one explicit vault HTTPS site before enabling private enrollment.')
    block = '\n    ' + begin + '\n    @pivault_private_vault_admin path /admin /admin/*\n    respond @pivault_private_vault_admin 404\n    ' + end + '\n'
    position = positions[0]
    return clean[:position] + block + clean[position:]


def homepage_js(original, addon):
    clean = replace_managed(original, JS_BEGIN, JS_END).rstrip('\n')
    return clean + '\n\n' + JS_BEGIN + '\n' + addon.rstrip() + '\n' + JS_END + '\n'


def compose_configuration(original, *, image, uid, gid, network_names, radicale_config=False, music_private=False):
    result = copy.deepcopy(original)
    services = result.setdefault('services', {})
    existing = services.get(SERVICE)
    if existing and existing.get('labels', {}).get('org.pivault.managed') != 'users-installer':
        raise ValueError('An unmanaged pivault-users Compose service already exists.')
    volumes = [
        '/srv/pi-server/config/users/data:/data',
        '/srv/pi-server/config/notifications:/notifications',
        '/srv/pi-server/config/users/native.json:/native.json:ro',
    ]
    if radicale_config:
        # Radicale's login file is written by the portal, so its folder is mounted read-write.
        volumes.append(RADICALE_CONFIG_FOLDER + ':/radicale-config')
    if music_private:
        # Per-user Music folders are created by the portal, so the private root is mounted read-write.
        volumes.append(MUSIC_PRIVATE_FOLDER + ':/music-private')
    service = {
        'image': image, 'container_name': SERVICE, 'restart': 'unless-stopped',
        'user': f'{uid}:{gid}', 'read_only': True,
        'cap_drop': ['ALL'], 'security_opt': ['no-new-privileges:true'],
        'tmpfs': ['/tmp:rw,noexec,nosuid,size=64m'],
        'mem_limit': '512m',
        'env_file': ['/srv/pi-server/config/users/users.env'],
        'environment': {'PIVAULT_NATIVE_CONFIG': '/native.json'},
        'volumes': volumes,
        'labels': {'org.pivault.managed': 'users-installer'},
    }
    if network_names:
        service['networks'] = list(network_names)
    services[SERVICE] = service
    return result


def front_door_compose(original, caddy_service):
    # Caddy publishes Vaultwarden's own port, and the homepage accepts the root address.
    result = copy.deepcopy(original)
    services = result.get('services', {})
    caddy = services.get(caddy_service)
    homepage = services.get('homepage')
    if caddy is None or homepage is None:
        raise ValueError('The Caddy or homepage Compose service is missing.')
    published = f'{PORTAL_HOST}:{VAULT_PORT}:{VAULT_PORT}'
    ports = caddy.setdefault('ports', [])
    if not any(str(item).split('/')[0] == published for item in ports):
        ports.append(published)
    environment = homepage.setdefault('environment', {})
    if not isinstance(environment, dict):
        raise ValueError('The homepage Compose environment must be a mapping.')
    environment['HOMEPAGE_ALLOWED_HOSTS'] = f'{PORTAL_HOST}:8086,{PORTAL_HOST}'
    return result


def _matches(match, host, path):
    for name, value in match.items():
        if name == 'host':
            if not any(fnmatch.fnmatchcase(host, item) for item in value):
                return False
        elif name == 'path':
            if not any(fnmatch.fnmatchcase(path, item) for item in value):
                return False
        elif name == 'method':
            if 'GET' not in value:
                return False
        elif name == 'protocol':
            if value != 'https':
                return False
        elif name == 'not':
            if any(_matches(item, host, path) for item in value):
                return False
        else:
            raise ValueError('Cannot verify portal precedence with the existing Caddy matcher: ' + name)
    return True


def terminal_handler(config, host, path, port=443):
    def walk(routes):
        for route in routes:
            if route.get('match') and not any(_matches(item, host, path) for item in route['match']):
                continue
            for handler in route.get('handle', []):
                kind = handler.get('handler')
                if kind == 'subroute':
                    found = walk(handler.get('routes', []))
                    if found:
                        return found
                elif kind in ('reverse_proxy', 'static_response'):
                    return handler
                elif kind not in ('headers', 'vars', 'encode'):
                    raise ValueError('Cannot verify portal routing with the existing Caddy handler: ' + str(kind))
        return None
    for server in config.get('apps', {}).get('http', {}).get('servers', {}).values():
        if any(address.endswith(f':{port}') for address in server.get('listen', [])):
            found = walk(server.get('routes', []))
            if found:
                return found
    return None


def _launcher_route(handler):
    # The launcher gate's Host header moves from the old 8443 address to the root. Nothing else may change.
    if not handler:
        return handler
    value = copy.deepcopy(handler)
    request = value.get('headers', {}).get('request', {}).get('set', {})
    request.pop('Host', None)
    return value


def verify_routes(before, after, vault_host=None):
    for path in ('/join', '/users/add', '/users/sign-in', '/users/session', '/notifications', '/notifications/api',
                 '/certificate', '/devices'):
        value = terminal_handler(after, PORTAL_HOST, path, PORTAL_PORT)
        if not value or value.get('handler') != 'reverse_proxy' or value.get('upstreams') != [{'dial': 'pivault-users:8000'}]:
            raise ValueError('The candidate Caddy routes do not reach the portal before the existing handlers.')
        request_headers = value.get('headers', {}).get('request', {}).get('set', {})
        if request_headers.get('X-Forwarded-Proto') != ['https'] or request_headers.get('X-Pivault-Client-Ip') != ['{http.request.remote.host}']:
            raise ValueError('The candidate proxy does not overwrite the trusted HTTPS/client address headers.')
    for path in ('/internal', '/internal/gate'):
        value = terminal_handler(after, PORTAL_HOST, path, PORTAL_PORT)
        if not value or value.get('handler') != 'static_response' or value.get('status_code') != 404:
            raise ValueError('The private authentication endpoint would be publicly reachable.')
    if (_launcher_route(terminal_handler(before, PORTAL_HOST, '/', OLD_LAUNCHER_PORT))
            != _launcher_route(terminal_handler(after, PORTAL_HOST, '/', PORTAL_PORT))):
        raise ValueError('The candidate configuration changes the existing launcher route.')
    if terminal_handler(before, PORTAL_HOST, '/', PORTAL_PORT) != terminal_handler(after, PORTAL_HOST, '/', VAULT_PORT):
        raise ValueError('The candidate configuration changes the existing Vaultwarden route.')
    if before.get('apps', {}).get('tls') != after.get('apps', {}).get('tls'):
        raise ValueError('The candidate configuration changes TLS settings.')
    if vault_host:
        for path in ('/admin', '/admin/users'):
            value = terminal_handler(after, vault_host, path, VAULT_PORT)
            if not value or value.get('handler') != 'static_response' or value.get('status_code') != 404:
                raise ValueError('Vault administration must remain private to the enrollment backend.')
