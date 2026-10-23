import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'jobs'))
import maintenance
import native
import configuration


class NativeConfigurationTests(unittest.TestCase):
    def plan(self, *, env=None, native_config=None, vault_settings=None):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        data = root / 'vault-data'; data.mkdir()
        if vault_settings is not None:
            (data / 'config.json').write_text(json.dumps(vault_settings))
        if native_config is not None:
            (root / 'users').mkdir()
            (root / 'users/native.json').write_text(json.dumps(native_config))
        document = {'services': {'vaultwarden': {'image': 'existing-vault',
                    'environment': {'DOMAIN': 'https://192.168.4.90', 'SIGNUPS_ALLOWED': 'false'}}}}
        container = {'Config': {'Env': ['DOMAIN=https://192.168.4.90', *(env or [])],
                     'Labels': {'com.docker.compose.service': 'vaultwarden'}}}
        return native.vault_configuration(document, container, root, data)

    def test_new_private_admin_connection_keeps_existing_registration_and_image(self):
        plan = self.plan()
        service = plan['document']['services']['vaultwarden']
        self.assertEqual(service['image'], 'existing-vault')
        self.assertEqual(service['environment']['SIGNUPS_ALLOWED'], 'false')
        self.assertTrue(plan['needs_restart']); self.assertTrue(plan['protect_admin'])
        saved = next(json.loads(v) for k, v in plan['files'].items() if k.name == 'native.json')
        self.assertEqual(saved['vaultwarden']['origin'], 'http://vaultwarden:80')
        self.assertGreater(len(saved['vaultwarden']['admin_token']), 50)

    def test_existing_raw_admin_is_preserved_without_restart_or_public_route_change(self):
        plan = self.plan(env=['ADMIN_TOKEN=existing-fixture-admin'])
        saved = next(json.loads(v) for k, v in plan['files'].items() if k.name == 'native.json')
        self.assertEqual(saved['vaultwarden']['admin_token'], 'existing-fixture-admin')
        self.assertFalse(plan['needs_restart']); self.assertFalse(plan['protect_admin'])

    def test_existing_hashed_token_is_not_replaced(self):
        with self.assertRaisesRegex(ValueError, 'will not reset'):
            self.plan(env=['ADMIN_TOKEN=$argon2id$fixture'])

    def test_smtp_vault_is_not_silently_reconfigured(self):
        with self.assertRaisesRegex(ValueError, 'SMTP-disabled'):
            self.plan(env=['SMTP_HOST=mail.example.com'])

    def test_existing_native_json_is_retained_on_reinstall(self):
        plan = self.plan(env=['ADMIN_TOKEN=$argon2id$fixture'], native_config={
            'vaultwarden': {'admin_token': 'retained-raw-fixture', 'private_admin': True},
            'public_urls': {'videos': 'https://video.example.com'}, 'jellyfin': {'api_key': 'retained'}})
        saved = next(json.loads(v) for k, v in plan['files'].items() if k.name == 'native.json')
        self.assertEqual(saved['jellyfin']['api_key'], 'retained')
        self.assertEqual(saved['public_urls']['videos'], 'https://video.example.com')
        self.assertTrue(plan['protect_admin'])

    def test_private_admin_block_is_idempotent(self):
        source = 'https://192.168.4.90 {\n tls internal\n reverse_proxy vaultwarden:80\n}\n'
        first = configuration.private_vault_admin(source, '192.168.4.90')
        self.assertEqual(first, configuration.private_vault_admin(first, '192.168.4.90'))
        self.assertIn('path /admin /admin/*', first)
        self.assertIn('reverse_proxy vaultwarden:80', first)


class MaintenanceTests(unittest.TestCase):
    def execute(self, cinema, run):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / 'maintenance.json'
            config.write_text(json.dumps({'cinema_enabled': cinema}))
            publish = Mock()
            result = maintenance.main(run=run, publish=publish, config_path=config)
        return result, publish

    def test_cinema_still_runs_if_client_checks_fail(self):
        run = Mock(side_effect=[SimpleNamespace(returncode=1), SimpleNamespace(returncode=0)])
        result, publish = self.execute(True, run)
        self.assertEqual(result, 1)
        self.assertEqual(run.call_count, 2)
        self.assertIn('pivault-cinema.service', run.call_args.args[0])
        publish.assert_called_once_with('client downloads')

    def test_previously_disabled_cinema_is_not_started(self):
        run = Mock(return_value=SimpleNamespace(returncode=0))
        result, _ = self.execute(False, run)
        self.assertEqual(result, 0); self.assertEqual(run.call_count, 1)
        self.assertNotIn('systemctl', run.call_args.args[0])

    def test_cinema_failure_is_sent_to_existing_notification_outbox(self):
        run = Mock(side_effect=[SimpleNamespace(returncode=0), SimpleNamespace(returncode=1)])
        _, publish = self.execute(True, run)
        publish.assert_called_once_with('cinema')

    def test_one_two_am_eastern_timer_has_no_unrequested_catchup(self):
        units = native.maintenance_units(Path('/srv/pi-server/users/maintenance.py'))
        self.assertEqual(len(units), 2)
        timer = units['pivault-maintenance.timer']
        self.assertIn('OnCalendar=*-*-* 02:00:00 America/New_York', timer)
        self.assertIn('Persistent=false', timer)
        self.assertNotIn('apt', ''.join(units.values()))
