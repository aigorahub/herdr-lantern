"""Model routing tests. Catalog fixtures never launch a model session."""

import importlib.util
import io
import json
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
        with patch.object(route, "run_catalog", return_value=CLAUDE_HELP):
            for phrase, model in (("fable high", "fable"),
                                  ("fable 5.1 high", "claude-fable-5-1"),
                                  ("claude-fable-5-1 high", "claude-fable-5-1")):
                self.assertEqual(route.claude_route(phrase)["argv"], ["--model", model, "--effort", "high"])
            for phrase in ("fable 5", "fable ultra", "fable fast", "not-a-model"):
                with self.assertRaises(route.RouteError):
                    route.claude_route(phrase)
        with patch.object(route, "run_catalog", return_value=CLAUDE_HELP.replace("claude-fable-5-1", "claude-fable-5")):
            with self.assertRaises(route.RouteError):
                route.claude_route("claude-fable-5-1")

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

    def test_fable_51_quota_and_substitute(self):
        def run(command):
            if command == ["claude", "--help"]:
                return CLAUDE_HELP
            return json.dumps({"result": "Current session: 0% used\nCurrent week (Fable 5.1): 100% used"})
        with patch.object(preflight, "run", side_effect=run):
            for model in ("fable", "claude-fable-5-1"):
                code, result = self.check("claude", model, "high")
                self.assertEqual(code, 3)
                self.assertIn("Fable 5.1", result["reason"])
                self.assertEqual(result["substitute"]["model"], "opus")
            self.assertEqual(self.check("claude", "opus", "high")[0], 0)

    def test_fable_51_available_and_unreadable_usage(self):
        for usage in ("Current session: 0% used", "unparseable"):
            with patch.object(preflight, "run", side_effect=[CLAUDE_HELP, json.dumps({"result": usage})]):
                if usage == "unparseable":
                    with self.assertRaises(preflight.CheckError):
                        self.check("claude", "claude-fable-5-1", "high")
                else:
                    self.assertEqual(self.check("claude", "claude-fable-5-1", "high")[0], 0)


if __name__ == "__main__":
    unittest.main()
