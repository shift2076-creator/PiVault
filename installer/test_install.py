import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest
from types import SimpleNamespace
from unittest.mock import patch

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
import configuration as config
import install
import build
import native


CADDYFILE = '''{
    admin localhost:2019
}
# The launcher and Vaultwarden have separate existing TLS routes (live layout).
https://192.168.4.90:8443 {
    tls internal
    reverse_proxy homepage:3000
}

https://192.168.4.90 {
    tls internal
    reverse_proxy vaultwarden:80
}
'''


def native_config(portal=False):
    homepage = {'handler': 'reverse_proxy', 'upstreams': [{'dial': 'homepage:3000'}]}
    routes = [{'handle': [homepage]}]
    if portal:
        routes.insert(0, {'match': [{'path': ['/join', '/join/*', '/users', '/users/*',
            '/notifications', '/notifications/*', '/internal', '/internal/*']}],
            'handle': [{'handler': 'subroute', 'routes': [
                {'match': [{'path': ['/internal', '/internal/*']}], 'handle': [
                    {'handler': 'static_response', 'status_code': 404}]},
                {'handle': [{'handler': 'reverse_proxy', 'upstreams': [{'dial': 'pivault-users:8000'}],
                    'headers': {'request': {'set': {'X-Forwarded-Proto': ['https'],
                        'X-Pivault-Client-Ip': ['{http.request.remote.host}']}}}}]},
            ]}]})
    return {'apps': {'tls': {'automation': {'policies': [{'issuers': [{'module': 'internal'}]}]}},
        'http': {'servers': {
            'srv0': {'listen': [':443'], 'routes': [
                {'match': [{'host': ['192.168.4.90']}], 'handle': [
                    {'handler': 'reverse_proxy', 'upstreams': [{'dial': 'vaultwarden:80'}]}]}]},
            'srv1': {'listen': [':8443'], 'routes': [
                {'match': [{'host': ['192.168.4.90']}], 'handle': [{'handler': 'subroute', 'routes': routes}]}]},
        }}}}


class ConfigurationTests(unittest.TestCase):
    def test_existing_certificate_and_vault_route_bytes_are_preserved(self):
        output = config.portal_caddyfile(CADDYFILE)
        self.assertEqual(output[output.index('https://192.168.4.90 {'):],
                         CADDYFILE[CADDYFILE.index('https://192.168.4.90 {'):])
        managed = output.index(config.BEGIN)
        self.assertLess(output.index('https://192.168.4.90:8443 {'), managed)
        self.assertLess(managed, output.index('https://192.168.4.90 {'))
        self.assertEqual(output.count('tls internal'), 2)
        self.assertIn('header_up X-PiVault-Client-IP {remote_host}', output)
        self.assertIn('respond @pivault_users_internal 404', output)

    def test_updates_do_not_duplicate_the_managed_route(self):
        first = config.portal_caddyfile(CADDYFILE)
        output = config.portal_caddyfile(first)
        self.assertEqual(first, output)
        self.assertEqual(output.count(config.BEGIN), 1)
        self.assertEqual(output.count('reverse_proxy pivault-users:8000'), 1)

    def test_braces_in_comments_and_placeholders_are_not_blocks(self):
        original = CADDYFILE.replace('tls internal', '# } fake {\n    tls internal', 1)
        output = config.portal_caddyfile(original)
        self.assertIn('# } fake {', output)
        self.assertIn('reverse_proxy homepage:3000', output)

    def test_ambiguous_or_missing_site_is_rejected(self):
        for original in ('localhost {\n reverse_proxy test:80\n}', CADDYFILE + CADDYFILE,
                         'https://192.168.4.90:8443 {\n reverse_proxy test:80\n',
                         'https://192.168.4.90 {\n reverse_proxy test:80\n}'):
            with self.assertRaises(ValueError):
                config.portal_caddyfile(original)

    def test_incomplete_previous_marker_is_rejected(self):
        with self.assertRaises(ValueError):
            config.portal_caddyfile(CADDYFILE + config.BEGIN)

    def test_javascript_preserves_existing_code_and_updates_once(self):
        original = 'window.existingCinema = true;\n'
        output = config.homepage_js(config.homepage_js(original, 'old();'), 'new();')
        self.assertTrue(output.startswith(original))
        self.assertEqual(output.count(config.JS_BEGIN), 1)
        self.assertNotIn('old();', output)
        self.assertIn('new();', output)

    def test_only_users_service_is_added_and_it_has_no_published_port(self):
        original = {'version': '3.8', 'services': {'video': {'image': 'same', 'ports': ['8096:8096']}},
                    'networks': {'private': {'external': True}}}
        prior = copy.deepcopy(original)
        output = config.compose_configuration(original, image='pivault-users:test', uid=1000,
                                              gid=1000, network_names=['private'])
        self.assertEqual(original, prior)
        self.assertEqual(output['services']['video'], original['services']['video'])
        self.assertNotIn('ports', output['services']['pivault-users'])
        self.assertEqual(output['services']['pivault-users']['networks'], ['private'])
        self.assertTrue(output['services']['pivault-users']['read_only'])

    def test_unmanaged_name_collision_is_rejected(self):
        with self.assertRaises(ValueError):
            config.compose_configuration({'services': {'pivault-users': {'image': 'somebody-elses'}}},
                image='pivault-users:test', uid=1000, gid=1000, network_names=[])

    def test_native_routing_verification_accepts_only_the_private_portal(self):
        config.verify_routes(native_config(), native_config(True))

    def test_public_gate_is_rejected(self):
        bad = native_config(True)
        bad['apps']['http']['servers']['srv1']['routes'][0]['handle'][0]['routes'][0]['handle'][0]['routes'].pop(0)
        with self.assertRaisesRegex(ValueError, 'publicly reachable'):
            config.verify_routes(native_config(), bad)

    def test_preserves_the_launcher_vault_and_tls(self):
        for mutate in ('launcher', 'vault', 'tls'):
            bad = native_config(True)
            servers = bad['apps']['http']['servers']
            if mutate == 'launcher':
                servers['srv1']['routes'][0]['handle'][0]['routes'][-1]['handle'][0]['upstreams'][0]['dial'] = 'different:3000'
            elif mutate == 'vault':
                servers['srv0']['routes'][0]['handle'][0]['upstreams'][0]['dial'] = 'different:80'
            else:
                bad['apps']['tls'] = {}
            with self.assertRaises(ValueError):
                config.verify_routes(native_config(), bad)

    def test_spoofable_protocol_or_client_ip_is_rejected(self):
        bad = native_config(True)
        proxy = bad['apps']['http']['servers']['srv1']['routes'][0]['handle'][0]['routes'][0]['handle'][0]['routes'][-1]['handle'][0]
        proxy['headers']['request']['set']['X-Pivault-Client-Ip'] = ['{http.request.header.X-Forwarded-For}']
        with self.assertRaisesRegex(ValueError, 'trusted HTTPS'):
            config.verify_routes(native_config(), bad)

    def test_bind_mounted_file_keeps_its_inode(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'Caddyfile'
            path.write_text('before')
            inode = path.stat().st_ino
            saved = install.snapshot(path)
            install.write_file(path, 'after')
            self.assertEqual(path.stat().st_ino, inode)
            install.restore_file(path, saved)
            self.assertEqual(path.read_text(), 'before')
            self.assertEqual(path.stat().st_ino, inode)


class ProbeTests(unittest.TestCase):
    def probe(self, body, gate='404'):
        calls = []
        def run(args, **kwargs):
            calls.append(args)
            return gate if '--write-out' in args else body
        with tempfile.TemporaryDirectory() as data:
            Path(data, 'caddy/pki/authorities/local').mkdir(parents=True)
            Path(data, 'caddy/pki/authorities/local/root.crt').write_text('fixture CA')
            caddy = {'Mounts': [{'Destination': '/data', 'Source': data}]}
            with patch.object(install, 'inspect', return_value=caddy), patch.object(install, 'run', side_effect=run):
                install.portal_probe()
        return calls

    def test_probe_uses_the_live_ip_site_with_tls_verification(self):
        calls = self.probe('<input name="password"> Sign in')
        self.assertEqual([c[-1] for c in calls], ['https://192.168.4.90:8443/users/sign-in',
                                                   'https://192.168.4.90:8443/internal/gate'])
        for args in calls:
            self.assertIn('--cacert', args)
            self.assertNotIn('--resolve', args)
            self.assertNotIn('-k', args)

    def test_probe_accepts_the_join_form_before_any_account_exists(self):
        self.probe('<input name="password"> Create account')

    def test_probe_rejects_other_pages_and_a_public_gate(self):
        with self.assertRaises(install.InstallError):
            self.probe('<html>Homepage</html>')
        with self.assertRaises(install.InstallError):
            self.probe('<input name="password"> Sign in', gate='401')


class RelayTests(unittest.TestCase):
    def scenario(self, scenario, payload_size=32):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            fake = folder / 'ssh'
            fake.write_text('#!' + sys.executable + '\n' + textwrap.dedent('''
                import os,sys,time
                scenario=os.environ['TEST_SCENARIO']
                if 'TestPassword-59!' in ' '.join(sys.argv): sys.exit(92)
                if scenario=='host-error':
                    print('Host key verification failed.',flush=True); sys.exit(255)
                if scenario=='key-locked':
                    print("Enter passphrase for key '/key': ",end='',flush=True)
                    time.sleep(5); sys.exit(99)
                def prompt(value):
                    print(value,end='',flush=True)
                    return sys.stdin.readline().rstrip('\\r\\n')
                if scenario in ('both','reject','sudo-reject','split'):
                    if scenario=='split':
                        print("snx@192.168.4.90's pass",end='',flush=True); time.sleep(.02)
                        value=prompt('word: ')
                    else: value=prompt("snx@192.168.4.90's password: ")
                    if value!='TestPassword-59!': sys.exit(93)
                if scenario=='reject':
                    prompt("snx@192.168.4.90's password: "); sys.exit(94)
                if scenario in ('both','sudo-only','sudo-reject','split'):
                    if prompt('__PIVAULT_INSTALL_SUDO__')!='TestPassword-59!': sys.exit(95)
                if scenario=='sudo-reject':
                    prompt('__PIVAULT_INSTALL_SUDO__'); sys.exit(96)
                print('__PIVAULT_INSTALL_PAYLOAD_READY__',flush=True)
                payload=sys.stdin.readline().strip()
                if payload != 'a'*int(os.environ['PAYLOAD_SIZE']): sys.exit(97)
                print('INSTALL_TEST_OK',flush=True)
            '''))
            fake.chmod(0o700)
            driver = folder / 'driver.py'
            driver.write_text(textwrap.dedent('''
                import importlib.util,os
                spec=importlib.util.spec_from_file_location('relay',os.environ['RELAY_SOURCE'])
                relay=importlib.util.module_from_spec(spec); spec.loader.exec_module(relay)
                relay.REMOTE_COMMAND='sudo -S -p __PIVAULT_INSTALL_SUDO__ python3 -c harmless'
                relay.PAYLOAD_B64='a'*int(os.environ['PAYLOAD_SIZE'])
                calls=[]
                def password(prompt): calls.append(prompt); return 'TestPassword-59!'
                relay.getpass.getpass=password
                result=relay.main()
                print('PASSWORD_PROMPTS='+str(len(calls)),flush=True)
                raise SystemExit(result)
            '''))
            result = subprocess.run([sys.executable, str(driver)], capture_output=True, text=True,
                env=dict(os.environ, PATH=str(folder) + os.pathsep + os.environ['PATH'],
                         TEST_SCENARIO=scenario, PAYLOAD_SIZE=str(payload_size), RELAY_SOURCE=str(HERE / 'relay.py')), timeout=15)
        self.assertNotIn('TestPassword-59!', result.stdout + result.stderr)
        return result

    def test_one_password_for_ssh_and_sudo(self):
        value = self.scenario('both')
        self.assertEqual(value.returncode, 0, value.stderr)
        self.assertIn('PASSWORD_PROMPTS=1', value.stdout)

    def test_key_login_only_asks_for_sudo(self):
        value = self.scenario('sudo-only')
        self.assertEqual(value.returncode, 0, value.stderr)
        self.assertIn('PASSWORD_PROMPTS=1', value.stdout)

    def test_cached_authentication_needs_no_password(self):
        value = self.scenario('none')
        self.assertEqual(value.returncode, 0, value.stderr)
        self.assertIn('PASSWORD_PROMPTS=0', value.stdout)

    def test_rejected_ssh_and_sudo_passwords_do_not_reprompt(self):
        for scenario in ('reject', 'sudo-reject'):
            value = self.scenario(scenario)
            self.assertNotEqual(value.returncode, 0)
            self.assertIn('PASSWORD_PROMPTS=1', value.stdout)
            self.assertIn('without another password prompt', value.stderr)

    def test_unknown_host_and_locked_keys_stop_without_prompt(self):
        for scenario in ('host-error', 'key-locked'):
            value = self.scenario(scenario)
            self.assertNotEqual(value.returncode, 0)
            self.assertIn('PASSWORD_PROMPTS=0', value.stdout)

    def test_split_network_prompt_is_handled(self):
        value = self.scenario('split')
        self.assertEqual(value.returncode, 0, value.stderr)
        self.assertIn('PASSWORD_PROMPTS=1', value.stdout)

    def test_large_archive_survives_single_ssh_stdin_without_terminal_truncation(self):
        value = self.scenario('both', payload_size=300000)
        self.assertEqual(value.returncode, 0, value.stderr)
        self.assertIn('INSTALL_TEST_OK', value.stdout)
        self.assertIn('PASSWORD_PROMPTS=1', value.stdout)


class InstallationTransactionTests(unittest.TestCase):
    def exercise(self, fail_probe, cinema_enabled=False):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            root = folder / 'server'
            compose = folder / 'pi-services/docker-compose.yml'
            compose.parent.mkdir()
            original = {'version': '3.8', 'services': {
                'https': {'image': 'existing-caddy'},
                'vaultwarden': {'image': 'existing-vault'},
                'video': {'image': 'existing-video', 'ports': ['8096:8096']},
                'pivault-users': {'image': 'previous-users', 'labels': {'org.pivault.managed': 'users-installer'}},
            }}
            import yaml
            compose.write_text(yaml.safe_dump(original, sort_keys=False))
            config_dir = root / 'config'
            config_dir.joinpath('users/data').mkdir(parents=True)
            database = config_dir / 'users/data/users.sqlite3'
            database.write_bytes(b'ORIGINAL_DATABASE')
            config_dir.joinpath('users/maintenance.json').write_text('{"books_enabled": true}\n')
            env = config_dir / 'users/users.env'
            env.write_text('PIVAULT_SECRET_KEY=' + 'existing-secret-' * 6 + '\n')
            caddyfile = config_dir / 'Caddyfile'
            caddyfile.write_text(CADDYFILE)
            homepage = config_dir / 'homepage'
            homepage.mkdir()
            js = homepage / 'custom.js'
            js.write_text('window.existingCinema=true;\n')
            css = homepage / 'custom.css'
            css.write_text('/* ACCEPTED CSS */')
            source = folder / 'source'
            source.mkdir()
            source.joinpath('app').mkdir()
            source.joinpath('app/Dockerfile').write_text('# image fixture\n')
            source.joinpath('stack').mkdir()
            source.joinpath('stack/homepage-addon.js').write_text('window.usersAddon=true;')
            source.joinpath('jobs').mkdir()
            source.joinpath('jobs/maintenance.py').write_text('# runtime fixture\n')
            vault_data = config_dir / 'vaultwarden'
            vault_data.mkdir()
            systemd_dir = folder / 'systemd'
            systemd_dir.mkdir()
            model = folder / 'model'
            model.write_text('Raspberry Pi 5 Model B')
            snapshots = {p: p.read_bytes() for p in (compose, caddyfile, js, env, database, css)}
            inode = caddyfile.stat().st_ino
            calls = []
            def docker_inspect(name, missing=False):
                if name == install.CADDY:
                    return {'Config': {'Cmd': ['caddy', 'run', '--config', '/etc/caddy/Caddyfile'],
                        'Labels': {'com.docker.compose.service': 'https',
                            'com.docker.compose.project': 'pi-services',
                            'com.docker.compose.project.config_files': str(compose)}},
                        'Mounts': [{'Destination': '/etc/caddy/Caddyfile', 'Source': str(caddyfile)}]}
                if name == 'pivault-homepage':
                    return {'Mounts': [{'Destination': '/app/config', 'Source': str(homepage)}]}
                if name == 'pivault-vaultwarden':
                    return {'Config': {'Env': ['DOMAIN=https://192.168.4.90', 'ADMIN_TOKEN=fixture-token'],
                        'Labels': {'com.docker.compose.service': 'vaultwarden'}},
                        'Mounts': [{'Destination': '/data', 'Source': str(vault_data)}],
                        'State': {'Running': True}}
                return {'Config': {'Labels': {'org.pivault.managed': 'users-installer'}},
                        'State': {'Running': True}}
            def run(args, **kwargs):
                calls.append(args)
                if 'adapt' in args:
                    return json.dumps(native_config())
                if 'migrate' in args:
                    database.write_bytes(b'NEW_DATABASE_FORMAT')
                if 'shell' in args:
                    return 'An Admin account already exists.\n'
                return ''
            output = io.StringIO()
            with contextlib.ExitStack() as stack:
                replacements = {'ROOT': root, 'CONFIG': config_dir, 'COMPOSE': compose,
                    'SOURCE': source, 'MODEL': model, 'SYSTEMD_DIR': systemd_dir}
                for key, value in replacements.items():
                    stack.enter_context(patch.object(install, key, value))
                stack.enter_context(patch.object(install.os, 'geteuid', return_value=0))
                stack.enter_context(patch.object(install.pwd, 'getpwnam', return_value=SimpleNamespace(
                    pw_uid=os.getuid(), pw_gid=os.getgid())))
                stack.enter_context(patch.object(install.shutil, 'which', side_effect=lambda x: '/usr/bin/' + x))
                stack.enter_context(patch.object(install, 'inspect', side_effect=docker_inspect))
                stack.enter_context(patch.object(install, 'run', side_effect=run))
                stack.enter_context(patch.object(install, 'adapt', return_value=native_config(True)))
                stack.enter_context(patch.object(install, 'wait_healthy'))
                stack.enter_context(patch.object(install, 'unit_flag', side_effect=lambda flag, name:
                    cinema_enabled and name == 'pivault-cinema.timer'))
                stack.enter_context(patch.object(install, 'portal_probe',
                    side_effect=install.InstallError('simulated TLS validation failure') if fail_probe else None))
                stack.enter_context(contextlib.redirect_stdout(output))
                if fail_probe:
                    with self.assertRaises(install.InstallError):
                        install.main()
                else:
                    install.main()
            self.assertEqual(caddyfile.stat().st_ino, inode)
            self.assertEqual(css.read_bytes(), snapshots[css])
            self.assertEqual(env.read_bytes(), snapshots[env])
            self.assertNotIn('existing-secret-', output.getvalue())
            if fail_probe:
                for path, content in snapshots.items():
                    self.assertEqual(path.read_bytes(), content, str(path))
                self.assertIn(['docker', 'rm', '-f', 'pivault-users'], calls)
                self.assertTrue(any('up' in c and 'pivault-users' in c for c in calls))
            else:
                result = yaml.safe_load(compose.read_text())
                self.assertEqual(result['services']['video'], original['services']['video'])
                self.assertIn(config.BEGIN, caddyfile.read_text())
                self.assertIn(config.JS_BEGIN, js.read_text())
                self.assertEqual(database.read_bytes(), b'NEW_DATABASE_FORMAT')
                self.assertIn('Other service-wide SSO', output.getvalue())
                saved = json.loads((config_dir / 'users/maintenance.json').read_text())
                self.assertEqual(saved['cinema_enabled'], cinema_enabled)
                self.assertTrue(saved['books_enabled'])
                self.assertEqual(any(c[:3] == ['systemctl', 'disable', '--now'] for c in calls), cinema_enabled)
                self.assertIn('02:00:00 America/New_York', (systemd_dir / 'pivault-maintenance.timer').read_text())

    def test_failed_tls_probe_restores_files_database_and_previous_service(self):
        self.exercise(True)

    def test_success_installs_only_the_portal_preserving_existing_services_and_css(self):
        self.exercise(False)

    def test_enabled_video_timer_moves_to_consolidated_schedule(self):
        self.exercise(False, cinema_enabled=True)


class PackageTests(unittest.TestCase):
    def test_complete_payload_and_embedded_python_compile(self):
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / 'PiVault-Users-Install.sh'
            result = build.build(destination)
            content = destination.read_text()
        self.assertGreater(result['bytes'], 20000)
        code = content.split("<<'PIVAULT_INSTALL_PYTHON'\n", 1)[1].rsplit('\nPIVAULT_INSTALL_PYTHON', 1)[0]
        compile(code, '<installer>', 'exec')
        compile(build.bootstrap(build.package()), '<remote>', 'exec')
        self.assertNotIn("'__REMOTE_COMMAND__'", code)
        self.assertNotIn("'__PAYLOAD_B64__'", code)

    def test_payload_holds_exactly_what_the_pi_installer_reads(self):
        import gzip, tarfile
        with tarfile.open(fileobj=io.BytesIO(gzip.decompress(build.package()))) as archive:
            names = set(archive.getnames())
        for required in ('installer/install.py', 'installer/configuration.py', 'installer/native.py',
                         'app/Dockerfile', 'app/manage.py', 'app/healthcheck.py',
                         'stack/homepage-addon.js', 'jobs/maintenance.py'):
            self.assertIn(required, names)
        self.assertFalse([n for n in names if n.startswith(('jobs/cinema/', 'tools/', 'stack/docker-compose'))])


if __name__ == '__main__':
    unittest.main(verbosity=2)
