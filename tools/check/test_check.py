import base64
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import patch

ROOT=Path(__file__).parent
spec=importlib.util.spec_from_file_location('report',ROOT/'report.py')
report=importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)


class ReportTests(unittest.TestCase):
    def test_never_exports_environment_or_proxy_credentials(self):
        canary='SECRET_VALUE_MUST_NOT_APPEAR'
        configs=[{'Name':'/pivault-homepage','Config':{'Image':'homepage:test',
            'Env':['API_TOKEN='+canary],'Labels':{}},'Mounts':[],
            'NetworkSettings':{'Ports':{}},'HostConfig':{'NetworkMode':'test'}}]
        def run(args,timeout=15):
            if args[:3]==['docker','ps','-aq']:
                return 0,'container-id'
            if args[:2]==['docker','inspect']:
                return 0,json.dumps(configs)
            if 'adapt' in args:
                return 0,json.dumps({'route':{'handler':'reverse_proxy','upstreams':[{'dial':'homepage:3000'}],
                    'headers':{'request':{'set':{'Authorization':[canary]}}}}})
            return 0,'INFO secret='+canary+'\nUsage: navidrome user create --username --email'
        buf=io.StringIO()
        with patch.object(report,'run',side_effect=run),patch.object(report,'build_opener',side_effect=OSError),contextlib.redirect_stdout(buf):
            report.main()
        output=buf.getvalue()
        self.assertNotIn(canary,output)
        self.assertIn('API_TOKEN',output)
        self.assertFalse(json.loads(output)['configuration_changed'])

    def test_database_inventory_is_schema_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'app.db'
            with sqlite3.connect(p) as conn:
                conn.execute('CREATE TABLE users (id INTEGER, email TEXT, password_hash TEXT)')
                conn.execute("INSERT INTO users VALUES (1,'private@example.com','DONT_EXPORT_THIS')")
            output=json.dumps(report.schema(p))
        self.assertIn('password_hash',output)
        self.assertNotIn('DONT_EXPORT_THIS',output)
        self.assertNotIn('private@example.com',output)

    def test_urls_drop_credentials_queries_and_fragments(self):
        self.assertEqual(report.safe_url('https://user:secret@pivault.home.arpa/music?token=secret#secret'),
                         'https://pivault.home.arpa/music')


class RelayTests(unittest.TestCase):
    def scenario(self,scenario):
        with tempfile.TemporaryDirectory() as tmp:
            tmp=Path(tmp)
            fake=tmp/'ssh'
            fake.write_text('#!'+sys.executable+'\n'+textwrap.dedent('''
                import os,sys
                scenario=os.environ['TEST_SCENARIO']
                if 'TestPassword-59!' in ' '.join(sys.argv):
                    sys.exit(92)
                if scenario=='host-error':
                    print('Host key verification failed.',flush=True)
                    sys.exit(255)
                def prompt(text):
                    print(text,end='',flush=True)
                    return sys.stdin.readline().rstrip('\\r\\n')
                if scenario in ('both','reject'):
                    value=prompt("snx@192.168.4.90's password: ")
                    if value!='TestPassword-59!': sys.exit(93)
                if scenario=='reject':
                    prompt("snx@192.168.4.90's password: ")
                    sys.exit(94)
                if scenario in ('both','sudo-only'):
                    if prompt('__PIVAULT_SUDO_CHECK__')!='TestPassword-59!': sys.exit(95)
                print('{"check":"ok"}',flush=True)
            '''))
            fake.chmod(0o700)
            driver=tmp/'driver.py'
            driver.write_text(textwrap.dedent('''
                import importlib.util,os
                spec=importlib.util.spec_from_file_location('relay',os.environ['RELAY_SOURCE'])
                relay=importlib.util.module_from_spec(spec)
                spec.loader.exec_module(relay)
                relay.REPORT_B64='cHJpbnQoIlJFUE9SVCIp'
                calls=[]
                def password(prompt):
                    calls.append(prompt)
                    return 'TestPassword-59!'
                relay.getpass.getpass=password
                status=relay.main()
                print('PASSWORD_PROMPTS='+str(len(calls)),flush=True)
                raise SystemExit(status)
            '''))
            env=dict(os.environ,PATH=str(tmp)+os.pathsep+os.environ['PATH'],TEST_SCENARIO=scenario,
                     RELAY_SOURCE=str(ROOT/'relay.py'))
            result=subprocess.run([sys.executable,str(driver)],env=env,capture_output=True,text=True,timeout=15)
        self.assertNotIn('TestPassword-59!',result.stdout+result.stderr)
        return result

    def test_one_password_for_ssh_and_sudo(self):
        result=self.scenario('both')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('PASSWORD_PROMPTS=1',result.stdout)
        self.assertIn('"check":"ok"',result.stdout)

    def test_existing_ssh_key_needs_only_sudo_password(self):
        result=self.scenario('sudo-only')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('PASSWORD_PROMPTS=1',result.stdout)

    def test_existing_authentication_needs_no_password(self):
        result=self.scenario('none')
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn('PASSWORD_PROMPTS=0',result.stdout)

    def test_rejected_password_stops_without_second_prompt(self):
        result=self.scenario('reject')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('PASSWORD_PROMPTS=1',result.stdout)
        self.assertIn('without another password prompt',result.stderr)

    def test_unknown_host_key_fails_without_password_prompt(self):
        result=self.scenario('host-error')
        self.assertEqual(result.returncode,255)
        self.assertIn('PASSWORD_PROMPTS=0',result.stdout)


if __name__=='__main__':
    unittest.main(verbosity=2)
