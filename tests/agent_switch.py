"""Provider switch tests at the external CLI boundary."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'bin'))
import agent_switch as switch
from usage_runtime import FileLock


class ProviderSwitch(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='lantern-switch-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / 'state'
        self.log = self.root / 'commands.jsonl'
        self.fixture = self.root / 'fixture.json'
        self.fixture.write_text(json.dumps({'status': 'blocked', 'session': 'session-1', 'available': True}))
        self.context = self.root / 'task.md'
        self.context.write_text('Complete issue 60. Preserve the source transcript.')
        (self.root / 'bin').mkdir()
        fake = self.root / 'boundary.py'
        fake.write_text('''import json, os, sys
from pathlib import Path
fixture = json.loads(Path(os.environ['SWITCH_FIXTURE']).read_text())
args = sys.argv[1:]
with open(os.environ['SWITCH_LOG'], 'a') as stream:
    stream.write(json.dumps(args) + '\\n')
if args == ['tab', 'list']:
    value = {'tabs': [{'tab_id': 't1', 'workspace_id': 'w1'}]}
elif args == ['agent', 'list']:
    value = {'agents': [{'pane_id': 'p1', 'tab_id': 't1', 'name': 'old', 'agent': 'claude', 'agent_session': fixture['session'], 'agent_status': fixture['status']}]}
elif args == ['workspace', 'list']:
    value = {'workspaces': [{'workspace_id': 'w1', 'cwd': '/repo with spaces'}]}
elif args[:2] == ['tab', 'create']:
    value = {'root_pane': {'pane_id': 'p2'}}
else:
    value = {}
print(json.dumps({'result': value}))
''')
        if os.name == 'nt':
            self.herdr = self.root / 'herdr.cmd'
            self.herdr.write_text(f'@"{sys.executable}" "{fake}" %*\r\n')
        else:
            self.herdr = self.root / 'herdr'
            self.herdr.write_text(f'#!{sys.executable}\nexec(compile(open({str(fake)!r}).read(), {str(fake)!r}, "exec"))\n')
            self.herdr.chmod(0o700)
        (self.root / 'bin' / 'model-route.py').write_text('''import json, os, sys
from pathlib import Path
fixture = json.loads(Path(os.environ['SWITCH_FIXTURE']).read_text())
model = fixture.get('model', 'test-model')
print(json.dumps({'kind': sys.argv[1], 'model': model, 'effort': 'high', 'argv': ['-m', model]}))
''')
        (self.root / 'bin' / 'model-preflight.py').write_text('''import json, os, sys
from pathlib import Path
fixture = json.loads(Path(os.environ['SWITCH_FIXTURE']).read_text())
payload = {'available': fixture['available'], 'reason': 'Provider limit'}
if 'quota' in fixture:
    payload['quota'] = fixture['quota']
print(json.dumps(payload))
sys.exit(0 if fixture['available'] else 3)
''')
        self.addCleanup(patch.stopall)
        patch.object(switch, 'ROOT', self.root).start()
        patch.dict(os.environ, {'SWITCH_FIXTURE': str(self.fixture), 'SWITCH_LOG': str(self.log)}).start()

    def run_cli(self, *args):
        from contextlib import redirect_stdout, redirect_stderr
        from io import StringIO
        stdout, stderr = StringIO(), StringIO()
        with patch.object(sys, 'argv', ['agent-switch', '--state-dir', str(self.state), '--herdr', str(self.herdr), *args]), redirect_stdout(stdout), redirect_stderr(stderr):
            code = switch.main()
        output = stdout.getvalue() if code == 0 else stderr.getvalue()
        return code, json.loads(output)

    def configure_offer(self):
        self.assertEqual(self.run_cli('configure', 'codex', '--kind', 'codex', '--model', 'test-model')[0], 0)
        code, offer = self.run_cli('offer', '--pane', 'p1', '--reason', 'Claude session limit reached')
        self.assertEqual(code, 0, offer)
        return offer

    def start(self, offer):
        return self.run_cli('start', '--pane', 'p1', '--token', offer['token'], '--route', 'codex', '--context-file', str(self.context))

    def commands(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def change(self, **values):
        fixture = json.loads(self.fixture.read_text())
        fixture.update(values)
        self.fixture.write_text(json.dumps(fixture))

    def test_offer_reports_pane_limit_and_unknown_quota_without_starting(self):
        offer = self.configure_offer()
        self.assertEqual(offer['source']['pane_id'], 'p1')
        self.assertEqual(offer['limit_reason'], 'Claude session limit reached')
        self.assertEqual(offer['alternatives'][0]['quota'], 'unknown')
        self.assertFalse(any(command[:2] == ['agent', 'start'] for command in self.commands()))

    def test_selected_route_gets_task_in_same_workspace_and_source_is_untouched(self):
        offer = self.configure_offer()
        code, value = self.start(offer)
        self.assertEqual(code, 0, value)
        commands = self.commands()
        create = next(row for row in commands if row[:2] == ['tab', 'create'])
        self.assertEqual(create[create.index('--workspace') + 1], 'w1')
        self.assertEqual(create[create.index('--cwd') + 1], '/repo with spaces')
        start = next(row for row in commands if row[:2] == ['agent', 'start'])
        self.assertEqual(start[start.index('--pane') + 1], 'p2')
        self.assertIn('test-model', start)
        prompt = next(row for row in commands if row[:2] == ['agent', 'prompt'])
        self.assertIn(self.context.read_text(), prompt[3])
        self.assertFalse(any('close' in row or 'stop' in row for row in commands))
        self.assertNotEqual(self.start(offer)[0], 0)

    def test_unavailable_claude_route_does_not_invent_a_quota_measurement(self):
        self.assertEqual(self.run_cli('configure', 'claude', '--kind', 'claude', '--model', 'test-model')[0], 0)
        self.change(available=False)
        code, offer = self.run_cli('offer', '--pane', 'p1', '--reason', 'Original limit')
        self.assertEqual(code, 0, offer)
        self.assertFalse(offer['alternatives'][0]['available'])
        self.assertEqual(offer['alternatives'][0]['quota'], 'unknown')
        self.change(quota='unavailable')
        code, offer = self.run_cli('offer', '--pane', 'p1', '--reason', 'Original limit')
        self.assertEqual(code, 0, offer)
        self.assertEqual(offer['alternatives'][0]['quota'], 'unavailable')

    def test_catalog_change_requires_a_new_explicit_model_selection(self):
        offer = self.configure_offer()
        self.change(model='different-model')
        code, value = self.start(offer)
        self.assertNotEqual(code, 0, value)
        self.assertIn('Resolved route changed', value['error'])
        self.assertFalse(any(row[:2] == ['tab', 'create'] for row in self.commands()))

    def test_concurrent_start_does_not_create_another_tab(self):
        offer = self.configure_offer()
        lock = FileLock(self.state / 'switch.lock')
        self.assertTrue(lock.acquire())
        try:
            code, value = self.start(offer)
            self.assertNotEqual(code, 0, value)
            self.assertIn('active', value['error'])
        finally:
            lock.close()
        self.assertFalse(any(row[:2] == ['tab', 'create'] for row in self.commands()))
        self.assertEqual(self.start(offer)[0], 0)

    def test_limit_session_change_and_working_source_fail_before_tab_creation(self):
        for changed in ({'session': 'replacement'}, {'status': 'working'}, {'available': False}):
            with self.subTest(changed=changed):
                self.change(session='session-1', status='blocked', available=True)
                offer = self.configure_offer()
                self.change(**changed)
                code, value = self.start(offer)
                self.assertNotEqual(code, 0, value)
        self.assertFalse(any(row[:2] == ['tab', 'create'] for row in self.commands()))


if __name__ == '__main__':
    unittest.main()
