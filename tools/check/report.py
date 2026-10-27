"""Read-only PiVault integration inventory. Never export credential values."""
import hashlib
import json
import re
import sqlite3
import subprocess
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from urllib.request import ProxyHandler, Request, build_opener

EXPECTED = {
    'jellyfin', 'navidrome', 'calibre-web', 'pivault-arcade', 'pivault-kiwix',
    'lychee', 'lychee-db',
    'pivault-syncthing', 'pivault-paperless', 'pivault-vaultwarden',
    'pivault-homepage', 'pivault-homebox', 'pivault-grocy', 'pivault-https',
}


def run(args, timeout=15):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return result.returncode, result.stdout
    except (OSError, subprocess.TimeoutExpired):
        return -1, ''


def safe_url(value):
    try:
        url = urlsplit(value)
        host = url.hostname or ''
        if url.port:
            host += ':' + str(url.port)
        return urlunsplit((url.scheme, host, url.path, '', ''))
    except (ValueError, TypeError):
        return '[unreadable URL]'


def links(tree):
    output = []
    if isinstance(tree, list):
        for value in tree:
            output.extend(links(value))
    elif isinstance(tree, dict):
        if 'href' in tree:
            output.append({'id':tree.get('id'), 'href':safe_url(tree['href'])})
        for key, value in tree.items():
            if isinstance(value,(dict,list)):
                found = links(value)
                for entry in found:
                    entry.setdefault('name',str(key))
                output.extend(found)
    return output


def schema(path):
    # Schema only: no user names, emails, hashes, tokens or application content.
    try:
        if not path.is_file():
            return None
        conn = sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=2)
        try:
            names = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
            tables = {}
            for (name,) in names:
                if re.search(r'user|group|role|auth|token|key|setting|config|tenant',name,re.I):
                    quoted = '"'+name.replace('"','""')+'"'
                    tables[name] = [row[1] for row in conn.execute('PRAGMA table_info('+quoted+')')]
            counts = {}
            for name in tables:
                if name.lower() in ('apikeys','api_keys','api_key','api_tokens','authtoken_token'):
                    quoted = '"'+name.replace('"','""')+'"'
                    counts[name] = conn.execute('SELECT COUNT(*) FROM '+quoted).fetchone()[0]
            return {'path':str(path),'auth_table_columns':tables,'api_credential_counts':counts}
        finally:
            conn.close()
    except (OSError,sqlite3.Error):
        return {'path':str(path),'schema_readable':False}


def routes(tree):
    found = []
    if isinstance(tree,list):
        for item in tree:
            found.extend(routes(item))
    elif isinstance(tree,dict):
        if 'match' in tree and isinstance(tree['match'],list):
            for match in tree['match']:
                if isinstance(match,dict):
                    found.append({'match':{k:match[k] for k in ('host','path') if k in match}})
        if tree.get('handler') == 'reverse_proxy':
            found.append({'reverse_proxy':[x.get('dial') for x in tree.get('upstreams',[]) if isinstance(x,dict)]})
        if tree.get('handler') == 'authentication':
            found.append({'authentication':True})
        for value in tree.values():
            if isinstance(value,(dict,list)):
                found.extend(routes(value))
    return found


def main():
    report = {'kind':'PiVault users integration check','configuration_changed':False}
    code,out = run(['docker','ps','-aq'])
    if code or not out.strip():
        raise SystemExit('Could not read Docker containers. No configuration changed.')
    code,out = run(['docker','inspect',*out.split()])
    if code:
        raise SystemExit('Could not inspect Docker containers. No configuration changed.')
    configs = json.loads(out)
    selected = {}
    for item in configs:
        name = item.get('Name','').lstrip('/')
        if name not in EXPECTED:
            continue
        cfg = item.get('Config',{})
        labels = cfg.get('Labels') or {}
        selected[name] = {
            'image':cfg.get('Image'), 'image_id':item.get('Image'),
            'state':item.get('State',{}).get('Status'),
            'version_labels':{k:labels[k] for k in (
                'org.opencontainers.image.version','org.opencontainers.image.revision',
                'org.label-schema.version','build_version',
            ) if k in labels},
            'environment_names':sorted(x.split('=',1)[0] for x in cfg.get('Env',[])),
            'mounts':[{'source':m.get('Source'),'destination':m.get('Destination'),'writable':m.get('RW')}
                      for m in item.get('Mounts',[]) if m.get('Type')=='bind'],
            'ports':item.get('NetworkSettings',{}).get('Ports',{}),
            'network_modes':item.get('HostConfig',{}).get('NetworkMode'),
        }
    report['containers'] = selected

    # Existing Homepage IDs and destinations, excluding widget keys and URL queries.
    home = selected.get('pivault-homepage',{})
    for mount in home.get('mounts',[]):
        if mount['destination']=='/app/config':
            root = Path(mount['source'])
            report['homepage_config'] = str(root)
            report['homepage_files'] = [p.name for p in root.iterdir() if p.is_file() and not p.name.endswith('.bak')]
            report['homepage_hashes'] = {name:hashlib.sha256((root/name).read_bytes()).hexdigest()
                for name in ('custom.css','custom.js','services.yaml') if (root/name).is_file()}
            try:
                import yaml
                report['homepage_links'] = links(yaml.safe_load((root/'services.yaml').read_text()))
            except (ImportError,OSError,ValueError):
                report['homepage_links_readable'] = False

    # Read actual Caddy routes, exporting only route matchers and upstream names.
    code,out = run(['docker','exec','pivault-https','caddy','adapt','--config','/etc/caddy/Caddyfile','--adapter','caddyfile'])
    if not code:
        try:
            report['caddy_routes'] = routes(json.loads(out))
        except ValueError:
            report['caddy_routes_readable'] = False

    version_calls = {
        'navidrome_version':['docker','exec','navidrome','/app/navidrome','--version'],
        'navidrome_user_cli':['docker','exec','navidrome','/app/navidrome','user','--help'],
    }
    for key,args in version_calls.items():
        code,out = run(args)
        # Fixed help/version commands do not print service configuration or users.
        report[key] = {'available':code==0}
        if not code:
            if key.endswith('_version'):
                match=re.search(r'\bv?\d+\.\d+\.\d+(?:[.-][A-Za-z0-9]+)*\b',out)
                report[key]['version']=match.group() if match else 'not detected'
            else:
                report[key]['commands_or_flags']=[value for value in (
                    'create','edit','delete','--username','--email','--user','--password-file','--password',
                ) if value in out]
    try:
        request = Request('http://192.168.4.90:8096/System/Info/Public')
        with build_opener(ProxyHandler({})).open(request,timeout=5) as response:
            info=json.load(response)
        report['jellyfin_public'] = {k:info.get(k) for k in ('Version','StartupWizardCompleted')}
    except Exception:
        report['jellyfin_public'] = {'available':False}

    # Only inspect known configuration database schemas; never dump table values.
    databases = []
    for service in selected.values():
        for mount in service.get('mounts',[]):
            root = Path(mount['source'])
            if not str(root).startswith('/srv/pi-server/config/') or not root.is_dir():
                continue
            for relative in ('app.db','navidrome.db','grocy.db','data/grocy.db','data/app.db',
                             'db.sqlite3','data/db.sqlite3','authentication.db','data/authentication.db',
                             'jellyfin.db','data/jellyfin.db','homebox.db'):
                result = schema(root/relative)
                if result and result not in databases:
                    databases.append(result)
    report['auth_schemas'] = databases
    vault_config=Path('/srv/pi-server/config/vaultwarden/config.json')
    if vault_config.is_file():
        try:
            values=json.loads(vault_config.read_text())
            report['vaultwarden_config_keys']=sorted(values)
            report['vaultwarden_boolean_options']={key:values[key] for key in (
                'signups_allowed','signups_verify','invitations_allowed','sso_enabled','sso_only',
            ) if isinstance(values.get(key),bool)}
        except (OSError,ValueError):
            report['vaultwarden_config_readable']=False
    print(json.dumps(report,indent=2,sort_keys=True))


if __name__=='__main__':
    main()
