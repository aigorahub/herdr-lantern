"""Model routing tests. Catalog fixtures never launch a model session."""

import importlib.util
import io
import json
import os
import subprocess
import tempfile
from contextlib import redirect_stdout
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

BIN = Path(__file__).resolve().parents[1] / "bin"
sys.path.insert(0, str(BIN))


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, BIN / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


route = load("model_route", "model-route.py")
preflight = load("model_preflight", "model-preflight.py")


def codex_catalog(fast=False):
    return json.dumps({"models": [
        {"slug": "gpt-6-astra", "display_name": "GPT-6-Astra", "visibility": "list",
         "default_reasoning_level": "medium",
         "supported_reasoning_levels": [{"effort": level} for level in
                                        ("low", "medium", "high", "xhigh", "max", "ultra")],
         "service_tiers": [{"id": "live-fast", "name": "Fast"}] if fast else [],
         "additional_speed_tiers": ["fast"] if fast else []},
        {"slug": "gpt-5.5", "display_name": "GPT-5.5", "visibility": "list",
         "default_reasoning_level": "medium",
         "supported_reasoning_levels": [{"effort": "medium"}]},
    ]})


CLAUDE_HELP = """
  --effort <level>  Effort level (low, medium, high, xhigh, max)
  --model <model>   Use 'fable', 'opus', or 'sonnet', or 'claude-fable-5-1'.
  -n, --name <name> A name such as 'not-a-model'
"""
CLAUDE_CATALOG = (Path(__file__).parent / "fixtures/claude-models.json").read_text()


def claude_catalog_with_badged_opus():
    # Matches the live initialization catalog: Opus's value/resolvedModel now
    # carry a terminal-style "[1m]" context badge and there is no bare
    # "opus"/"claude-opus-5" row at all.
    catalog = json.loads(CLAUDE_CATALOG)
    for row in catalog["response"]["response"]["models"]:
        if row["displayName"] == "Opus":
            row["value"] = "opus[1m]"
            row["resolvedModel"] = "claude-opus-5[1m]"
            row["displayName"] = "Opus (1M context)"
    return json.dumps(catalog)


def refuse_usage(command):
    if any("/usage" in part for part in command):
        raise AssertionError(f"model check must never read usage: {command}")


def claude_read(command, *, input_text=None, **_):
    refuse_usage(command)
    if command == ["claude", "--help"]:
        return CLAUDE_HELP.replace("claude-fable-5-1", "claude-fable-5")
    return CLAUDE_CATALOG


CURSOR = """Available models
claude-fable-5-high - Claude Fable 5 1M
claude-fable-5-1-high - Claude Fable 5.1 1M
claude-fable-5-1-thinking-high - Claude Fable 5.1 1M Thinking
claude-fable-5-1-max - Claude Fable 5.1 1M Max
"""


class Models(unittest.TestCase):
    def test_astra_spoken_phrases_and_default(self):
        with patch.object(route, "run_catalog", return_value=codex_catalog()) as read:
            for phrase in ("astra", "gpt-6 astra", "gpt-6-astra", "default"):
                result = route.codex_route(phrase)
                self.assertEqual(result["model"], "gpt-6-astra")
                self.assertEqual(result["effort"], "medium")
                self.assertFalse(result["fast"])
                self.assertEqual(result["service_tier"], "default")
                self.assertEqual(result["argv"], ["-m", "gpt-6-astra", "-c", 'model_reasoning_effort="medium"', "-c", 'service_tier="default"'])
            read.assert_called_with(["codex", "debug", "models"])

    def test_astra_all_efforts(self):
        with patch.object(route, "run_catalog", return_value=codex_catalog()):
            for effort in ("low", "medium", "high", "xhigh", "max", "ultra"):
                self.assertEqual(route.codex_route(f"astra {effort}")["effort"], effort)
            for phrase in ("astra minimal", "astra none", "astra low high"):
                with self.assertRaises(route.RouteError):
                    route.codex_route(phrase)

    def test_gpt6_sol_and_luna_need_a_generation(self):
        catalog = json.dumps({"models": [
            {"slug": "gpt-6-sol", "display_name": "GPT-6-Sol", "visibility": "list",
             "default_reasoning_level": "medium",
             "supported_reasoning_levels": [{"effort": "high"}, {"effort": "medium"}]},
            {"slug": "gpt-6-luna", "display_name": "GPT-6-Luna", "visibility": "list",
             "default_reasoning_level": "medium",
             "supported_reasoning_levels": [{"effort": "medium"}, {"effort": "high"}, {"effort": "max"}]},
            {"slug": "gpt-5.6-sol", "display_name": "GPT-5.6-Sol", "visibility": "list",
             "default_reasoning_level": "low",
             "supported_reasoning_levels": [{"effort": "high"}, {"effort": "low"}]},
            {"slug": "gpt-5.6-luna", "display_name": "GPT-5.6-Luna", "visibility": "list",
             "default_reasoning_level": "medium",
             "supported_reasoning_levels": [{"effort": "high"}]},
        ]})
        with patch.object(route, "run_catalog", return_value=catalog):
            self.assertEqual(route.codex_route("gpt-6 sol high")["model"], "gpt-6-sol")
            self.assertEqual(route.codex_route("6 sol")["effort"], "medium")
            self.assertEqual(route.codex_route("gpt-6 luna")["model"], "gpt-6-luna")
            with self.assertRaisesRegex(route.RouteError, "ultra"):
                route.codex_route("gpt-6 luna ultra")
            for phrase in ("sol", "sol high", "luna"):
                with self.assertRaisesRegex(route.RouteError, "ambiguous"):
                    route.codex_route(phrase)

    def test_cursor_current_openai_and_grok_ids(self):
        catalog = """Available models
grok-4.7-high-fast - Grok 4.7 High Fast
cursor-grok-4.6-high-fast - Cursor Grok 4.6 Fast
gpt-5.3-codex-high-fast - Codex 5.3 High Fast
claude-opus-5-5-high-fast - Claude Opus 5.5 1M High Fast
"""
        with patch.object(route, "run_catalog", return_value=catalog):
            self.assertEqual(route.cursor_route("grok 4.7 high fast")["model"], "grok-4.7-high-fast")
            self.assertEqual(route.cursor_route("codex 5.3 high fast")["model"], "gpt-5.3-codex-high-fast")
            self.assertEqual(route.cursor_route("opus 5.5 high fast")["model"], "claude-opus-5-5-high-fast")
            with self.assertRaises(route.RouteError):
                route.cursor_route("cursor grok 4.7 high fast")

    def test_grok_default_prefers_47_over_build_fast(self):
        catalog = """Available models:
  - grok-4.7
  * grok-4.7-build-fast (default)
  - grok-4.6
"""
        with patch.object(route, "run_catalog", return_value=catalog):
            result = route.grok_route("default")
            self.assertEqual(result["argv"], ["-m", "grok-4.7-build-fast", "--reasoning-effort", "medium"])
            self.assertEqual(route.grok_route("grok 4.7 build fast")["model"], "grok-4.7-build-fast")

    def test_badged_opus_pins_resolved_model(self):
        catalog = json.dumps({"type": "control_response", "response": {
            "subtype": "success", "request_id": "lantern-model-catalog", "response": {"models": [
                {"value": "opus[1m]", "resolvedModel": "claude-opus-5-5[1m]",
                 "displayName": "Opus (1M context)",
                 "supportedEffortLevels": ["low", "medium", "high", "xhigh", "max"]},
                {"value": "claude-fable-5-1[1m]", "resolvedModel": "claude-fable-5-1",
                 "displayName": "Fable",
                 "supportedEffortLevels": ["low", "medium", "high", "xhigh", "max"]},
            ]}}})

        def read(command, *, input_text=None, **_):
            if command == ["claude", "--help"]:
                return CLAUDE_HELP
            return catalog

        with patch.object(route, "run_catalog", side_effect=read):
            result = route.claude_route("opus high")
            self.assertEqual(result["argv"], ["--model", "claude-opus-5-5[1m]", "--effort", "high"])
            self.assertEqual(route.claude_route("fable high")["model"], "claude-fable-5-1")

    def test_fugu_routes_current_catalog(self):
        catalog = json.dumps({"models": [
            {"slug": "fugu-max", "display_name": "Fugu Max", "visibility": "list",
             "supported_reasoning_levels": [{"effort": "high"}, {"effort": "xhigh"}]},
            {"slug": "fugu-ultra-v2.0", "display_name": "Fugu Ultra v2.0", "visibility": "list",
             "supported_reasoning_levels": [{"effort": "high"}, {"effort": "xhigh"}]},
            {"slug": "fugu-ultra", "display_name": "Fugu Ultra", "visibility": "list",
             "supported_reasoning_levels": [{"effort": "high"}, {"effort": "xhigh"}]},
            {"slug": "fugu", "display_name": "Fugu", "visibility": "list",
             "supported_reasoning_levels": [{"effort": "high"}, {"effort": "xhigh"}]},
            {"slug": "fugu-ultra-v1.1", "display_name": "Fugu Ultra v1.1", "visibility": "list",
             "supported_reasoning_levels": [{"effort": "high"}, {"effort": "xhigh"}, {"effort": "max"}]},
            {"slug": "fugu-ultra-v1.0", "display_name": "Fugu Ultra v1.0", "visibility": "list",
             "supported_reasoning_levels": [{"effort": "high"}, {"effort": "xhigh"}]},
        ]})
        with tempfile.TemporaryDirectory() as home:
            path = os.path.join(home, "fugu.json")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(catalog)
            with patch.dict(os.environ, {"CODEX_HOME": home}):
                default = route.fugu_route("default")
                self.assertEqual(default["model"], "fugu")
                self.assertEqual(default["effort"], "high")
                self.assertEqual(route.fugu_route("fugu")["model"], "fugu")
                self.assertEqual(route.fugu_route("fugu deep")["model"], "fugu")
                self.assertEqual(route.fugu_route("fugu deep")["effort"], "xhigh")
                self.assertEqual(route.fugu_route("fugu ultra deep")["effort"], "xhigh")
                with self.assertRaises(route.RouteError):
                    route.fugu_route("fugu deep high")
                self.assertEqual(route.fugu_route("fugu ultra")["model"], "fugu-ultra-v2.0")
                self.assertEqual(route.fugu_route("fugu max")["model"], "fugu-max")
                self.assertEqual(route.fugu_route("ultra max")["model"], "fugu-ultra-v1.1")
                self.assertEqual(route.fugu_route("fugu ultra max")["model"], "fugu-ultra-v1.1")
                self.assertEqual(route.fugu_route("fugu-ultra-v1.1 max")["effort"], "max")
                with self.assertRaises(route.RouteError):
                    route.fugu_route("fugu-ultra-v1.0")

    def test_bare_generation_requires_choice(self):
        with patch.object(route, "run_catalog", return_value=codex_catalog()):
            for phrase in ("gpt-6", "gpt 6 high", "codex gpt-6"):
                with self.assertRaisesRegex(route.RouteError, "ambiguous"):
                    route.codex_route(phrase)
            self.assertEqual(route.codex_route("gpt-5.5")["model"], "gpt-5.5")
            with self.assertRaises(route.RouteError):
                route.codex_route("gpt-7 astra")

    def test_malformed_catalogs_fail_with_route_errors(self):
        for catalog in ('[]', '{}', '{"models":[null]}', '{"models":"invalid"}',
                        '{"models":[{"slug":"gpt-6-astra","visibility":"list","supported_reasoning_levels":null}]}'):
            with patch.object(route, "run_catalog", return_value=catalog):
                with self.assertRaises(route.RouteError):
                    route.codex_route("astra")
            with patch.object(preflight, "run", return_value=catalog):
                with self.assertRaises(preflight.CheckError):
                    self.check("codex", "gpt-6-astra", "high")

    def test_no_astra_fallback(self):
        with patch.object(route, "run_catalog", return_value='{"models":[]}'):
            with self.assertRaises(route.RouteError):
                route.codex_route("default")

    def test_fast_requires_live_tier_and_explicit_request(self):
        with patch.object(route, "run_catalog", return_value=codex_catalog()):
            with self.assertRaisesRegex(route.RouteError, "does not support fast"):
                route.codex_route("astra high fast")
        with patch.object(route, "run_catalog", return_value=codex_catalog(fast=True)):
            self.assertFalse(route.codex_route("astra")["fast"])
            result = route.codex_route("astra high fast")
            self.assertEqual(result["service_tier"], "live-fast")
            self.assertIn('service_tier="live-fast"', result["argv"])

    def test_normal_route_overrides_inherited_priority(self):
        with patch.object(route, "run_catalog", return_value=codex_catalog(fast=True)):
            result = route.codex_route("default")
        self.assertIn('service_tier="default"', result["argv"])
        self.assertNotIn('service_tier="priority"', result["argv"])
        self.assertEqual(result["service_tier"], "default")

    def test_cursor_fable_versions_and_exact_ids(self):
        with patch.object(route, "run_catalog", return_value=CURSOR):
            for phrase, model in (
                ("fable 5 high", "claude-fable-5-high"),
                ("fable 5.1 high", "claude-fable-5-1-high"),
                ("claude-fable-5-1-high", "claude-fable-5-1-high"),
                ("fable 5.1 thinking high", "claude-fable-5-1-thinking-high"),
                ("fable 5.1 max", "claude-fable-5-1-max"),
            ):
                self.assertEqual(route.cursor_route(phrase)["model"], model)
            for phrase in ("astra", "gpt-6 astra", "fable 5.1 fast"):
                with self.assertRaises(route.RouteError):
                    route.cursor_route(phrase)

    def test_cursor_only_accepts_astra_if_listed(self):
        with patch.object(route, "run_catalog", return_value=CURSOR + "gpt-6-astra - GPT-6 Astra\n"):
            self.assertEqual(route.cursor_route("astra")["model"], "gpt-6-astra")

    def test_claude_fable_alias_and_full_version(self):
        with patch.object(route, "run_catalog", side_effect=claude_read):
            for phrase, model in (("fable high", "claude-fable-5-1"),
                                  ("fable 5.1 high", "claude-fable-5-1"),
                                  ("claude-fable-5-1 high", "claude-fable-5-1")):
                self.assertEqual(route.claude_route(phrase)["argv"], ["--model", model, "--effort", "high"])
            for phrase in ("fable 5", "fable ultra", "fable fast", "not-a-model"):
                with self.assertRaises(route.RouteError):
                    route.claude_route(phrase)
        # Help still says 5. The initialization response, not its examples,
        # controls both aliases and full IDs.
        with patch.object(route, "run_catalog", side_effect=claude_read) as read:
            self.assertEqual(route.claude_route("fable")["model"], "claude-fable-5-1")
            request = json.loads(read.call_args.kwargs["input_text"])
            self.assertEqual(request["type"], "control_request")
            self.assertEqual(request["request"], {"subtype": "initialize"})
            self.assertIn("--safe-mode", read.call_args.args[0])
            self.assertIn("--no-session-persistence", read.call_args.args[0])

    def test_claude_unavailable_catalog_never_uses_help_as_allowlist(self):
        for output in ('{}', 'invalid', CLAUDE_CATALOG.replace('lantern-model-catalog', 'wrong-id'),
                       CLAUDE_CATALOG.replace('"success"', '"error"')):
            with patch.object(route, "run_catalog", side_effect=[CLAUDE_HELP, output]):
                with self.assertRaises(route.RouteError):
                    route.claude_route("fable")

    def test_claude_model_specific_efforts(self):
        with patch.object(route, "run_catalog", side_effect=claude_read):
            with self.assertRaises(route.RouteError):
                route.claude_route("sonnet max")

    def test_claude_catalog_normalizes_badged_opus_identity(self):
        badged_catalog = claude_catalog_with_badged_opus()

        def badged_read(command, *, input_text=None, **_):
            if command == ["claude", "--help"]:
                return CLAUDE_HELP
            return badged_catalog

        with patch.object(route, "run_catalog", side_effect=badged_read):
            # The catalog only lists "opus[1m]" / "claude-opus-5[1m]", but the
            # bare phrase and exact bare model id must still resolve to it.
            for phrase in ("opus high", "claude-opus-5 high"):
                result = route.claude_route(phrase)
                self.assertEqual(result["model"], "claude-opus-5[1m]")
                self.assertEqual(result["argv"], ["--model", "claude-opus-5[1m]", "--effort", "high"])

        def badged_run(command, *, input_text=None, **_):
            refuse_usage(command)
            return badged_read(command, input_text=input_text)

        with patch.object(preflight, "run", side_effect=badged_run):
            code, result = self.check("claude", "claude-opus-5", "high")
            self.assertEqual(code, 0)
            self.assertTrue(result["available"])
            code, result = self.check("claude", "opus", "high")
            self.assertEqual(code, 0)
            self.assertTrue(result["available"])


    def check(self, kind, model, effort):
        output = io.StringIO()
        with redirect_stdout(output):
            code = preflight.check(kind, model, effort)
        return code, json.loads(output.getvalue())

    def test_codex_preflight_effort(self):
        with patch.object(preflight, "run", return_value=codex_catalog()):
            self.assertEqual(self.check("codex", "gpt-6-astra", "ultra")[0], 0)
            self.assertEqual(self.check("codex", "gpt-6-astra", "none")[0], 3)
            self.assertEqual(self.check("codex", "gpt-6-invented", "high")[0], 3)

    def test_claude_check_never_reads_usage(self):
        def run(command, *, input_text=None, **_):
            if command == ["agent", "--list-models"]:
                return "gpt-5.6-sol-high-fast - GPT Sol\n"
            return claude_read(command, input_text=input_text)
        with patch.object(preflight, "run", side_effect=run):
            for model in ("fable", "claude-fable-5-1", "opus", "sonnet"):
                code, result = self.check("claude", model, "high")
                self.assertEqual(code, 0)
                self.assertTrue(result["available"])
                self.assertEqual(result["quota"], "unknown")
            code, result = self.check("claude", "fable", "ultra")
            self.assertEqual(code, 3)
            self.assertEqual(result["substitute"]["model"], "opus")
            self.assertEqual(result["substitute"]["effort"], "xhigh")
            self.assertNotIn("quota", result)
            code, result = self.check("claude", "opus", "ultra")
            self.assertEqual(code, 3)
            self.assertEqual(result["substitute"]["model"], "sonnet")
            code, result = self.check("claude", "sonnet", "max")
            self.assertEqual(code, 3)
            self.assertEqual(result["substitute"]["kind"], "cursor")
            self.assertEqual(self.check("claude", "invented", "high")[0], 3)
        self.assertFalse(hasattr(preflight, "parse_claude_usage"))
        self.assertFalse(hasattr(preflight, "claude_cost_usage"))

    def test_claude_login_comes_from_initialization_only(self):
        cases = (
            ("oauth-token", 0),
            ("login-source", 0),
            ("CLAUDE_CODE_OAUTH_TOKEN", 0),
            (None, 0),
            ("", 0),
            ("   ", 0),
            (True, 0),
            ({"status": "logged-out"}, 0),
            ("none", 2),
            (" NONE ", 2),
        )
        for token_source, expected_code in cases:
            with self.subTest(token_source=token_source):
                catalog = json.loads(CLAUDE_CATALOG)
                if token_source is None:
                    catalog["response"]["response"].pop("account", None)
                else:
                    catalog["response"]["response"]["account"] = {"tokenSource": token_source}

                def run(command, *, input_text=None, catalog=catalog, **_):
                    refuse_usage(command)
                    if command == ["claude", "--help"]:
                        return CLAUDE_HELP
                    return json.dumps(catalog)

                with patch.object(preflight, "run", side_effect=run):
                    if expected_code == 2:
                        with self.assertRaisesRegex(preflight.CheckError, "not logged in"):
                            self.check("claude", "opus", "high")
                        continue
                    code, payload = self.check("claude", "opus", "high")
                    self.assertEqual(code, 0)
                    self.assertTrue(payload["available"])
                    self.assertEqual(payload["quota"], "unknown")

    def test_claude_catalog_keeps_optional_account_metadata_optional(self):
        for account in (None, {}, {"tokenSource": None}, {"tokenSource": True}, "invalid"):
            with self.subTest(account=account):
                catalog = json.loads(CLAUDE_CATALOG)
                catalog["response"]["response"]["account"] = account
                def run(command, *, input_text=None, **_):
                    refuse_usage(command)
                    if command == ["claude", "--help"]:
                        return CLAUDE_HELP
                    return json.dumps(catalog)
                self.assertIn("opus", route.claude_model_catalog(run))
                with patch.object(preflight, "run", side_effect=run):
                    self.assertEqual(preflight.claude_capabilities()[1], None)
                    self.assertEqual(self.check("claude", "opus", "high")[0], 0)

    def test_claude_help_uses_regular_file_when_pipe_truncates(self):
        script = "\n".join((
            "import os, stat, sys",
            "from pathlib import Path",
            "here = Path(__file__).resolve().parent",
            "if sys.argv[1:] == ['--help']:",
            "    if os.environ.get('FAKE_CLAUDE_FAIL_HELP') == '1':",
            "        raise SystemExit(2)",
            "    prefix = 'Usage: claude [options]\\n  --effort <level>  Effort level (low, medium, high, xhigh, max)\\n'",
            "    padding = ''.join(",
            "        f'  --option-{index:04d} <value>  Option description text for flag padding\\n'",
            "        for index in range(290)",
            "    )",
            "    suffix = (",
            "        \"  --model <model>   Use 'fable', 'opus', or 'sonnet', or 'claude-fable-5-1'.\\n\"",
            "        \"  -n, --name <name> A name such as 'not-a-model'\\n\"",
            "    )",
            "    full = (prefix + padding + suffix).encode('utf-8')",
            "    once = os.environ.get('FAKE_CLAUDE_TRUNCATE_ONCE')",
            "    if once and not Path(once).exists():",
            "        Path(once).write_text('1', encoding='utf-8')",
            "        data = full[:8190]",
            "    else:",
            "        data = full if stat.S_ISREG(os.fstat(1).st_mode) else full[:8190]",
            "    sys.stdout.buffer.write(data)",
            "    sys.stdout.buffer.flush()",
            "    raise SystemExit(0)",
            "if '--input-format' in sys.argv:",
            "    _ = sys.stdin.readline()",
            "    sys.stdout.write((here / 'claude-models.json').read_text(encoding='utf-8'))",
            "    raise SystemExit(0)",
            "raise SystemExit(2)",
        ))
        with tempfile.TemporaryDirectory() as root:
            bin_dir = Path(root) / "bin"
            tmp_dir = Path(root) / "tmp"
            bin_dir.mkdir()
            tmp_dir.mkdir()
            (bin_dir / "fake_claude.py").write_text(script, encoding="utf-8")
            (bin_dir / "claude-models.json").write_text(CLAUDE_CATALOG, encoding="utf-8")
            if os.name == "nt":
                (bin_dir / "claude.cmd").write_text(
                    '@echo off\r\n"%FAKE_CLAUDE_PYTHON%" "%~dp0fake_claude.py" %*\r\n',
                    encoding="utf-8",
                )
            else:
                posix_shim = bin_dir / "claude"
                posix_shim.write_text(
                    '#!/bin/sh\nexec "$FAKE_CLAUDE_PYTHON" "$(dirname "$0")/fake_claude.py" "$@"\n',
                    encoding="utf-8",
                )
                posix_shim.chmod(0o755)
            env = {
                "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
                "FAKE_CLAUDE_PYTHON": sys.executable,
            }
            with patch.dict(os.environ, env), patch.object(tempfile, "tempdir", str(tmp_dir)):
                piped = subprocess.run(
                    [sys.executable, str(bin_dir / "fake_claude.py"), "--help"],
                    check=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                self.assertEqual(len(piped.stdout), 8190)
                self.assertNotIn(b"--model", piped.stdout)

                routed = route.claude_route("opus high")
                self.assertEqual(routed["model"], "claude-opus-5")
                self.assertEqual(routed["argv"], ["--model", "claude-opus-5", "--effort", "high"])
                self.assertEqual(list(tmp_dir.iterdir()), [])

                code, checked = self.check("claude", "opus", "high")
                self.assertEqual(code, 0)
                self.assertTrue(checked["available"])
                self.assertEqual(list(tmp_dir.iterdir()), [])

                once_marker = Path(root) / "truncated-once"
                with patch.dict(os.environ, {"FAKE_CLAUDE_TRUNCATE_ONCE": str(once_marker)}):
                    retried = route.claude_route("opus high")
                self.assertEqual(retried["model"], "claude-opus-5")
                self.assertTrue(once_marker.exists())
                self.assertEqual(list(tmp_dir.iterdir()), [])

                with patch.dict(os.environ, {"FAKE_CLAUDE_FAIL_HELP": "1"}):
                    with self.assertRaisesRegex(route.RouteError, "catalog unavailable: claude"):
                        route.claude_route("opus high")
                    self.assertEqual(list(tmp_dir.iterdir()), [])
                    with self.assertRaisesRegex(preflight.CheckError, "availability check failed: claude"):
                        self.check("claude", "opus", "high")
                    self.assertEqual(list(tmp_dir.iterdir()), [])

    def test_claude_help_retries_truncated_output(self):
        truncated_model = (
            "Usage: claude [options]\n"
            "  --effort <level>  Effort level (low, medium, high, xhigh, max)\n"
            + "".join(f"  --flag-{index:03d} <v>\n" for index in range(400))
        )
        truncated_effort = (
            "Usage: claude [options]\n"
            "  --model <model>   Use 'fable', 'opus', or 'sonnet', or 'claude-fable-5-1'.\n"
        )

        route_replies = iter([truncated_model, CLAUDE_HELP])
        route_file_flags = []

        def route_read(command, *, input_text=None, stdout_via_file=False):
            route_file_flags.append((command[1], stdout_via_file))
            if command == ["claude", "--help"]:
                return next(route_replies)
            return CLAUDE_CATALOG

        with patch.object(route, "run_catalog", side_effect=route_read):
            result = route.claude_route("opus high")
            self.assertEqual(result["argv"], ["--model", "claude-opus-5", "--effort", "high"])
        self.assertEqual(
            route_file_flags,
            [("--help", True), ("--help", True), ("--safe-mode", False)],
        )

        preflight_replies = iter([truncated_effort, truncated_model, CLAUDE_HELP])

        def preflight_read(command, *, input_text=None, stdout_via_file=False):
            if command == ["claude", "--help"]:
                self.assertTrue(stdout_via_file)
                return next(preflight_replies)
            self.assertFalse(stdout_via_file)
            refuse_usage(command)
            return CLAUDE_CATALOG

        with patch.object(preflight, "run", side_effect=preflight_read):
            code, payload = self.check("claude", "opus", "high")
            self.assertEqual(code, 0)
            self.assertTrue(payload["available"])

        direct_replies = iter([truncated_model, CLAUDE_HELP])

        def plain_run(command, *, input_text=None, stdout_via_file=False):
            if command == ["claude", "--help"]:
                self.assertTrue(stdout_via_file)
                return next(direct_replies)
            return CLAUDE_CATALOG

        catalog = route.claude_model_catalog(plain_run)
        self.assertEqual(catalog["opus"][0], "claude-opus-5")

    def test_claude_help_stops_after_max_truncated_attempts(self):
        truncated_model = (
            "Usage: claude [options]\n"
            "  --effort <level>  Effort level (low, medium, high, xhigh, max)\n"
        )
        truncated_effort = (
            "Usage: claude [options]\n"
            "  --model <model>   Use 'fable', 'opus', or 'sonnet'.\n"
        )
        for label, truncated, expected in (
            ("no_model", truncated_model, "Claude help has no model choices"),
            ("no_effort", truncated_effort, "Claude help has no effort choices"),
        ):
            with self.subTest(case=label):
                attempts = 0

                def always_truncated(command, *, input_text=None, stdout_via_file=False, text=truncated):
                    nonlocal attempts
                    if command == ["claude", "--help"]:
                        attempts += 1
                        return text
                    return CLAUDE_CATALOG

                with patch.object(route, "run_catalog", side_effect=always_truncated):
                    with self.assertRaisesRegex(route.RouteError, expected):
                        route.claude_route("opus high")
                self.assertEqual(attempts, 3)

                with patch.object(preflight, "run", side_effect=always_truncated):
                    with self.assertRaisesRegex(preflight.CheckError, expected):
                        self.check("claude", "opus", "high")
                self.assertEqual(attempts, 6)

    def test_claude_help_does_not_retry_other_parse_errors(self):
        malformed_help = (
            "  --effort <level>  Effort level (none)\n"
            "  --model <model>   Use a model without quoted aliases.\n"
        )
        attempts = 0

        def unparseable_help(command, *, input_text=None, stdout_via_file=False):
            nonlocal attempts
            if command == ["claude", "--help"]:
                attempts += 1
                return malformed_help
            return CLAUDE_CATALOG

        with patch.object(route, "run_catalog", side_effect=unparseable_help):
            with self.assertRaisesRegex(
                route.RouteError, "Claude returned unparseable model or effort choices"
            ):
                route.claude_route("opus high")
        self.assertEqual(attempts, 1)

        with patch.object(preflight, "run", side_effect=unparseable_help):
            with self.assertRaisesRegex(
                preflight.CheckError, "Claude returned unparseable model or effort choices"
            ):
                self.check("claude", "opus", "high")
        self.assertEqual(attempts, 2)

    def test_capture_to_file_ignores_unlink_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(tempfile, "tempdir", tmp), patch.object(
                os, "unlink", side_effect=PermissionError("locked")
            ) as unlink:
                self.assertEqual(
                    route.model_catalog.capture_to_file(lambda handle: handle.write(b"ok")),
                    "ok",
                )
                with self.assertRaises(subprocess.TimeoutExpired):
                    route.model_catalog.capture_to_file(
                        lambda _handle: (_ for _ in ()).throw(
                            subprocess.TimeoutExpired("claude", 30)
                        )
                    )
                self.assertEqual(unlink.call_count, 2)

if __name__ == "__main__":
    unittest.main()
