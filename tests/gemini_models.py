"""Offline Gemini routing tests; fake packages are never executed."""
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'bin'))
import model_catalog

def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'bin' / file)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod

route = load('gemini_route_tests', 'model-route.py')
preflight = load('gemini_preflight_tests', 'model-preflight.py')

def write_bridge(root, payload='{"schema_version":1,"native_thinking_bridge":true}', code=0):
    path = root / ('gemini.cmd' if os.name == 'nt' else 'gemini')
    if os.name == 'nt':
        path.write_text('@echo off\necho ' + payload + '\nexit /b ' + str(code) + '\n')
    else:
        path.write_text("#!/bin/sh\nprintf '%s\\n' '" + payload + "'\nexit " + str(code) + "\n")
        path.chmod(0o755)
    return path


class GeminiModels(unittest.TestCase):
    def setUp(self):
        for module, attribute in ((route, "run_catalog"), (preflight, "run")):
            guard = patch.object(module, attribute, side_effect=AssertionError("unexpected provider command"))
            guard.start()
            self.addCleanup(guard.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / 'bundle').mkdir()
        write_bridge(self.root)
        (self.root / 'package.json').write_text(json.dumps({'name':'@google/gemini-cli', 'version':'0.65.0'}))
        (self.root / 'bundle/core.js').write_text('\n'.join(
            f'var MODEL_{i} = "{m}";' for i, m in enumerate([
                'gemini-3.8-flash', 'gemini-3.1-pro-preview', 'gemini-3.5-flash',
                'gemini-3-flash-preview', 'gemini-3.5-flash-lite', 'gemini-3.1-flash-lite',
                'gemini-2.5-pro', 'gemini-3-pro-preview', 'gemma-4-31b-it',
                'gemma-4-26b-a4b-it', 'gemini-invented-model'])))
        self.env = patch.dict(os.environ, {
            'PATH': str(self.root) + os.pathsep + os.environ['PATH'],
            'LANTERN_GEMINI_PACKAGE_ROOT': str(self.root),
            'LANTERN_GEMINI_ACK_NATIVE_ROUTING': '1',
            'LANTERN_GEMINI_EFFORT_BRIDGE': '',
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_catalog_reports_all_twelve_without_ack_or_entitlement(self):
        os.environ.pop('LANTERN_GEMINI_ACK_NATIVE_ROUTING')
        catalog = route.gemini_route('list')
        self.assertEqual(len(catalog['models']), 12)
        by_id = {row['model']: row for row in catalog['models']}
        self.assertEqual(by_id['gemini-3.8-flash']['policy'], 'candidate')
        self.assertEqual(by_id['gemini-2.5-pro']['policy'], 'blocked')
        self.assertIn('Cloud', by_id['gemini-2.5-pro']['reason'])
        self.assertEqual(by_id['gemma-4-31b-it']['policy'], 'blocked')
        self.assertTrue(all(row['entitlement'] == 'unknown' for row in catalog['models']))

    def test_exact_id_and_honest_static_evidence(self):
        with patch('subprocess.run', side_effect=AssertionError('must not execute CLI')):
            result = route.gemini_route('gemini-3.8-flash')
        self.assertEqual(result['argv'], ['--model', 'gemini-3.8-flash'])
        self.assertEqual(result['requested_model'], 'gemini-3.8-flash')
        self.assertFalse(result['actual_model_verified'])
        self.assertTrue(result['cli_may_remap'])
        self.assertEqual(result['entitlement'], 'unknown')
        self.assertEqual(result['inference'], 'untested')

    def test_no_defaults_aliases_unknown_or_retired(self):
        for name in ('default', 'flash', 'Gemini 3.8 Flash', 'gemini-invented-model',
                     'gemini-2.5-pro', 'gemini-2.5-flash', 'gemini-2.5-flash-lite',
                     'gemini-3-pro-preview', 'gemma-4-31b-it', 'gemma-4-26b-a4b-it'):
            with self.subTest(name=name), self.assertRaises(route.RouteError):
                route.gemini_route(name)

    def test_current_but_absent_from_package_is_blocked(self):
        (self.root / 'bundle/core.js').write_text('var MODEL = "gemini-3.5-flash";')
        with self.assertRaisesRegex(route.RouteError, 'absent'):
            route.gemini_route('gemini-3.8-flash')

    def test_catalog_without_canonical_executable_is_not_launchable(self):
        with patch('shutil.which', return_value=None):
            self.assertEqual(len(route.gemini_route('list')['models']), 12)
            with self.assertRaisesRegex(route.RouteError, 'executable'):
                route.gemini_route('gemini-3.8-flash')

    def test_no_acknowledgement_is_blocked(self):
        os.environ.pop('LANTERN_GEMINI_ACK_NATIVE_ROUTING')
        with self.assertRaisesRegex(route.RouteError, 'ACK_NATIVE_ROUTING'):
            route.gemini_route('gemini-3.8-flash')

    def test_effort_requires_bridge_and_model_support(self):
        with self.assertRaisesRegex(route.RouteError, 'bridge'):
            route.gemini_route('gemini-3.8-flash high')
        os.environ['LANTERN_GEMINI_EFFORT_BRIDGE'] = '1'
        os.environ['PATH'] = str(self.root) + os.pathsep + os.environ['PATH']
        write_bridge(self.root)
        for level in ('low', 'medium', 'high'):
            result = route.gemini_route('gemini-3.8-flash ' + level)
            self.assertEqual(result['command'], 'gemini')
            self.assertEqual(result['argv'], ['--model', 'gemini-3.8-flash', '--thinking-level', level])
            self.assertNotIn('--effort', result['argv'])
        for phrase in ('gemini-3.8-flash minimal', 'gemini-3.8-flash ultra',
                       'gemini-3.8-flash fast', 'gemini-3.8-flash low high'):
            with self.subTest(phrase=phrase), self.assertRaises(route.RouteError):
                route.gemini_route(phrase)

    def test_preflight_does_not_claim_available_or_substitute(self):
        out = io.StringIO()
        with redirect_stdout(out):
            code = preflight.check('gemini', 'gemini-3.8-flash', '')
        self.assertEqual(code, 0)
        result = json.loads(out.getvalue())
        self.assertIsNone(result['available'])
        self.assertTrue(result['launchable'])
        self.assertIsNone(result['substitute'])
        self.assertEqual(result['entitlement'], 'unknown')

    def test_false_capability_does_not_enable_private_option(self):
        os.environ['LANTERN_GEMINI_EFFORT_BRIDGE'] = '1'
        write_bridge(self.root, code=1)
        os.environ['PATH'] = str(self.root) + os.pathsep + os.environ['PATH']
        with self.assertRaisesRegex(route.RouteError, 'capabilit'):
            route.gemini_route('gemini-3.8-flash high')

    def test_capability_payload_must_prove_bridge(self):
        os.environ['LANTERN_GEMINI_EFFORT_BRIDGE'] = '1'
        for payload in ('{}', '[]', '{"schema_version":1,"native_thinking_bridge":false}', 'not json'):
            write_bridge(self.root, payload)
            with self.subTest(payload=payload), self.assertRaisesRegex(route.RouteError, 'capabilit'):
                route.gemini_route('gemini-3.8-flash high')

    def test_all_documented_levels_preserve_requested_model(self):
        os.environ['LANTERN_GEMINI_EFFORT_BRIDGE'] = '1'
        for model, levels in model_catalog.GEMINI_CURRENT.items():
            for level in levels:
                result = route.gemini_route(model + ' ' + level)
                self.assertEqual(result['argv'], ['--model', model, '--thinking-level', level])
                self.assertFalse(result['actual_effort_verified'])

    def test_start_guard_rejects_missing_duplicate_and_retired_models(self):
        for args in ([], ['--', '--model', 'gemini-3.8-flash'], ['--model', 'gemini-3.8-flash', '--', '--thinking-level', 'high'], ['--model', 'gemini-2.5-pro'],
                     ['--model', 'gemini-3.8-flash', '-m', 'gemini-3.5-flash'],
                     ['--model=gemini-3.8-flash', '--effort', 'high'],
                     ['--model', 'gemini-3.8-flash', '-mgemini-2.5-pro']):
            with self.subTest(args=args):
                result = subprocess.run([sys.executable, str(ROOT / 'bin/gemini-start-check.py'), *args], capture_output=True, text=True)
                self.assertEqual(result.returncode, 2)
                self.assertNotIn("can't open file", result.stderr)
        result = subprocess.run([sys.executable, str(ROOT / 'bin/gemini-start-check.py'), '--model', 'gemini-3.8-flash'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('may remap', result.stderr)

    def test_prompt_values_are_not_model_or_effort_flags(self):
        command = [sys.executable, str(ROOT / 'bin/gemini-start-check.py')]
        bad = subprocess.run(command + ['--prompt', '--model=gemini-3.8-flash'], capture_output=True, text=True)
        self.assertEqual(bad.returncode, 2)
        for value in ('--model=gemini-2.5-pro', '--thinking-level=high'):
            ambiguous = subprocess.run(command + ['--model', 'gemini-3.8-flash', '--prompt', value], capture_output=True, text=True)
            self.assertEqual(ambiguous.returncode, 2)
            good = subprocess.run(command + ['--model', 'gemini-3.8-flash', '--prompt=' + value], capture_output=True, text=True)
            self.assertEqual(good.returncode, 0, good.stderr)

    def test_resume_and_worktree_keep_optional_value_semantics(self):
        command = [sys.executable, str(ROOT / 'bin/gemini-start-check.py')]
        for option in ('--resume', '-r', '--worktree', '-w'):
            for args in ([option, '--model', 'gemini-3.8-flash'],
                         ['--model', 'gemini-3.8-flash', option],
                         [option, 'name', '--model', 'gemini-3.8-flash']):
                result = subprocess.run(command + args, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_onboard_requires_explicit_validated_gemini(self):
        conf = self.root / 'conf'
        env = dict(os.environ, HERDR_PLUGIN_ROOT=str(ROOT), HERDR_PLUGIN_CONFIG_DIR=str(conf))
        bad = subprocess.run(['sh', str(ROOT / 'bin/onboard'), 'apply', '--kind', 'gemini'], env=env, capture_output=True, text=True)
        self.assertNotEqual(bad.returncode, 0)
        good = subprocess.run(['sh', str(ROOT / 'bin/onboard'), 'apply', '--kind', 'gemini', '--model', 'gemini-3.8-flash'], env=env, capture_output=True, text=True)
        self.assertEqual(good.returncode, 0, good.stderr)
        self.assertIn('gemini-3.8-flash', (conf / 'helper.conf').read_text())

    def test_herdr_guard_checks_before_target_mutation(self):
        fake = self.root / 'herdr-real'
        marker = self.root / 'called'
        fake.write_text('#!/bin/sh\ntouch "' + str(marker) + '"\n')
        fake.chmod(0o755)
        env = dict(os.environ, HERDR_REAL=str(fake), HERDR_HELPER_OK='1')
        command = ['sh', str(ROOT / 'bin/herdr'), 'agent', 'start', 'test', '--kind', 'gemini', '--pane', 'w1:t1:p1', '--']
        bad = subprocess.run(command + ['--model', 'gemini-2.5-pro'], env=env, capture_output=True, text=True)
        self.assertNotEqual(bad.returncode, 0)
        self.assertFalse(marker.exists(), 'blocked route must not reach Herdr')
        good = subprocess.run(command + ['--model', 'gemini-3.8-flash'], env=env, capture_output=True, text=True)
        self.assertEqual(good.returncode, 0, good.stderr)
        self.assertTrue(marker.exists())

    def test_duplicate_herdr_kinds_cannot_hide_gemini(self):
        fake = self.root / 'herdr-real'
        marker = self.root / 'called'
        fake.write_text('#!/bin/sh\ntouch "' + str(marker) + '"\n')
        fake.chmod(0o755)
        env = dict(os.environ, HERDR_REAL=str(fake), HERDR_HELPER_OK='1')
        command = ['sh', str(ROOT / 'bin/herdr'), 'agent', 'start', 'test', '--kind', 'cursor', '--kind', 'gemini', '--pane', 'w1:t1:p1', '--', '--model', 'gemini-2.5-pro']
        result = subprocess.run(command, env=env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(marker.exists())

    def test_invalid_package_not_catalog(self):
        (self.root / 'package.json').write_text('{"name":"other","version":"0.65.0"}')
        with self.assertRaises(route.RouteError):
            route.gemini_route('gemini-3.8-flash')

if __name__ == '__main__':
    unittest.main()
