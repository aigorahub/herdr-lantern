"""Plan Usage protocol, identity, collection, locks, and startup tests."""
from __future__ import annotations

import contextlib
import io
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import Future
from datetime import timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
import field_status as field
import lantern_views as views
import plan_usage as usage
import usage_runtime as runtime
import usage_sources as sources
from terminal_readout import Readout, page, fit


def limits(account="account-one"):
    pool = {"limitId": "codex", "planType": "pro", "primary": {"usedPercent": 21, "windowDurationMins": 300, "resetsAt": 1800000000},
            "secondary": {"usedPercent": 90, "windowDurationMins": 10080}, "credits": {"hasCredits": True, "balance": "12"}}
    return {"accountId": account, "rateLimits": pool, "rateLimitsByLimitId": {"codex": pool,
        "review": {"primary": {"usedPercent": 5, "windowDurationMins": 10080}}}}


def measured(account="account-one"):
    return sources.parse_codex({"type": "chatgpt", "planType": "pro"}, limits(account), 1800000000)


try:
    import tomllib
except ImportError:
    tomllib = None
# Codex and Fugu settings are TOML, which Python reads from 3.11 on.
# OldPythonTests covers what older Python shows instead.
needs_tomllib = unittest.skipIf(tomllib is None, "Codex settings need Python 3.11")


class ConfiguredFuguTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.root = self.home / ".codex"
        self.root.mkdir()
        self.env = {"HOME": str(self.home), "SAKANA_API_KEY": "private-key"}
        self.provider = ('[model_providers.sakana]\nbase_url = "https://api.sakana.ai/v1"\n'
                         'env_key = "SAKANA_API_KEY"\n')
        (self.root / "config.toml").write_text(self.provider)
        (self.root / "fugu.config.toml").write_text('model_provider = "sakana"\n')

    def store(self, text):
        path = self.root / ".env"
        path.write_text(text)
        path.chmod(0o600)
        return path

    @needs_tomllib
    def test_configured_profile_needs_no_open_agent_or_executable(self):
        with patch.object(runtime, "capture") as command:
            scope = sources.configured_fugu_scope(self.env)
        command.assert_not_called()
        self.assertEqual(scope["provider"], "fugu")
        self.assertEqual(scope["api_key"], "private-key")
        self.assertNotIn("executable", scope)
        self.assertNotIn("private-key", json.dumps(scope["env"]))
        process = {"argv": ["codex", "-p", "fugu"], "executable": str(self.home / "codex")}
        Path(process["executable"]).touch(); Path(process["executable"]).chmod(0o700)
        opened = sources.scope_for("codex", process, self.env)
        self.assertEqual(scope["key"], opened["key"])

    @needs_tomllib
    def test_standalone_modern_and_legacy_profiles_use_the_same_settings(self):
        modern = self.root / "fugu.config.toml"
        modern.write_text('model_provider = "sakana"\n' + self.provider)
        (self.root / "config.toml").unlink()
        self.assertEqual(sources.configured_fugu_scope(self.env)["provider"], "fugu")
        modern.unlink()
        (self.root / "config.toml").write_text(self.provider + '\n[profiles.fugu]\nmodel_provider = "sakana"\n')
        self.assertEqual(sources.configured_fugu_scope(self.env)["provider"], "fugu")

    @needs_tomllib
    def test_home_selector_is_exact_and_invalid_selectors_do_not_read_default(self):
        self.assertIsNone(sources.configured_fugu_scope({}))
        self.assertIsNone(sources.configured_fugu_scope(dict(self.env, CODEX_HOME="relative")))
        self.assertIsNone(sources.configured_fugu_scope(dict(self.env, CODEX_HOME="")))
        self.assertIsNone(sources.configured_fugu_scope(dict(self.env, CODEX_HOME=str(self.home / "other"))))
        self.assertEqual(sources.configured_fugu_scope(dict(self.env, CODEX_HOME=str(self.root)))["provider"], "fugu")
        if os.name == "nt":
            self.assertEqual(sources.configured_fugu_scope({"USERPROFILE": str(self.home),
                "SAKANA_API_KEY": "private-key"})["provider"], "fugu")

    def test_no_installed_profile_does_not_add_a_service(self):
        (self.root / "fugu.config.toml").unlink()
        for text in (self.provider, "", "[broken", 'model_providers = "wrong"'):
            with self.subTest(text=text):
                (self.root / "config.toml").write_text(text)
                self.assertIsNone(sources.configured_fugu_scope(self.env))
        (self.root / "config.toml").unlink()
        self.assertIsNone(sources.configured_fugu_scope(self.env))

    @needs_tomllib
    def test_key_store_reads_only_selected_key_without_shell_execution(self):
        marker = self.home / "should-not-exist"
        store = self.store(f'OTHER_KEY=$(touch {marker})\nexport SAKANA_API_KEY="stored-key" # saved\n')
        scope = sources.configured_fugu_scope({"HOME": str(self.home)})
        self.assertEqual(scope["api_key"], "stored-key")
        self.assertIn(str(store), scope["files"])
        self.assertFalse(marker.exists())
        scope = sources.configured_fugu_scope(self.env)
        self.assertEqual(scope["api_key"], "private-key")
        self.assertNotIn(str(store), scope["files"])

    @needs_tomllib
    def test_key_rotation_changes_binding_and_missing_key_hides_old_values(self):
        env = {"HOME": str(self.home)}
        store = self.store('SAKANA_API_KEY=first-key\n')
        first = sources.configured_fugu_scope(env)
        store.write_text('SAKANA_API_KEY=second-key\n')
        second = sources.configured_fugu_scope(env)
        self.assertNotEqual(first["key"], second["key"])
        self.assertNotEqual(first["stamp"], sources.current_stamp(first))
        store.write_text('OTHER_KEY=hidden\n')
        with self.assertRaisesRegex(sources.SourceError, "^Sakana API key missing$"):
            sources.configured_fugu_scope(env)

    @needs_tomllib
    def test_changed_store_or_profile_during_read_is_not_bound(self):
        env = {"HOME": str(self.home)}
        store = self.store('SAKANA_API_KEY=first-key\n')
        def change_key(*args):
            store.write_text('SAKANA_API_KEY=second-key\n')
            return "first-key"
        with patch.object(sources, "stored_fugu_key", side_effect=change_key), \
                self.assertRaisesRegex(sources.SourceError, "Account unknown"):
            sources.configured_fugu_scope(env)
        original = sources.codex_settings
        def change_profile(*args, **kwargs):
            result = original(*args, **kwargs)
            (self.root / "fugu.config.toml").write_text('model_provider = "other"\n')
            return result
        with patch.object(sources, "codex_settings", side_effect=change_profile), \
                self.assertRaisesRegex(sources.SourceError, "Account unknown"):
            sources.configured_fugu_scope(self.env)

    @needs_tomllib
    def test_invalid_store_is_not_sourced_or_sent(self):
        env = {"HOME": str(self.home)}
        for text in ('SAKANA_API_KEY="unterminated\n', 'SAKANA_API_KEY=one two\n', 'x' * 65537):
            with self.subTest(text=text[:30]):
                self.store(text)
                with self.assertRaisesRegex(sources.SourceError, "Account unknown"):
                    sources.configured_fugu_scope(env)
        store = self.store('SAKANA_API_KEY=stored-key\n')
        if os.name != "nt":
            store.chmod(0o644)
            with self.assertRaisesRegex(sources.SourceError, "Account unknown"):
                sources.configured_fugu_scope(env)
            store.chmod(0o600)
            store.rename(self.root / "actual.env")
            store.symlink_to(self.root / "actual.env")
            with self.assertRaisesRegex(sources.SourceError, "Account unknown"):
                sources.configured_fugu_scope(env)
            store.unlink()
            os.mkfifo(store)
            with self.assertRaisesRegex(sources.SourceError, "Account unknown"):
                sources.configured_fugu_scope(env)

    @needs_tomllib
    def test_configured_plan_rejects_other_endpoints_and_malformed_profiles(self):
        for provider in (self.provider.replace("api.sakana.ai", "other.invalid"),
                         self.provider.replace('"https://api.sakana.ai/v1"', '123'),
                         self.provider.replace('"SAKANA_API_KEY"', '"INVALID-KEY"'), "[broken"):
            with self.subTest(provider=provider):
                (self.root / "config.toml").write_text(provider)
                with self.assertRaises(sources.SourceError) as failure:
                    sources.configured_fugu_scope(self.env)
                self.assertEqual(failure.exception.provider, "fugu")


# The fixture installs codex as a symbolic link found on a Unix PATH.
@unittest.skipIf(os.name == "nt", "needs Unix symbolic links and PATH lookup")
class ConfiguredCodexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.root = self.home / ".codex"
        self.root.mkdir()
        (self.root / "auth.json").write_text("{}")
        (self.root / "config.toml").write_text("")
        # The installer puts a codex link on PATH that points at the real program.
        release = self.home / "releases" / "1"
        release.mkdir(parents=True)
        self.program = release / "codex"
        self.program.touch(); self.program.chmod(0o700)
        self.bin = self.home / "bin"
        self.bin.mkdir()
        (self.bin / "codex").symlink_to(self.program)
        self.env = {"HOME": str(self.home), "PATH": str(self.bin), "OPENAI_API_KEY": "private-key"}

    @needs_tomllib
    def test_signed_in_login_is_bound_like_an_open_tab_on_it(self):
        scope = sources.configured_codex_scope(self.env)
        self.assertEqual(scope["provider"], "codex")
        self.assertEqual(scope["executable"], os.path.realpath(self.program))
        # An open tab on this login has the same key, so it shares the row and the read.
        process = {"argv": [str(self.program)], "executable": os.path.realpath(self.program)}
        self.assertEqual(scope["key"], sources.scope_for("codex", process, {"HOME": str(self.home)})["key"])
        # An inherited API key doesn't hide the saved login and never reaches the reader.
        self.assertNotIn("private-key", json.dumps(scope))

    @needs_tomllib
    def test_nothing_is_shown_without_a_signed_in_openai_login(self):
        for change in ("no sign-in", "no codex", "relative path", "relative home", "other provider",
                       "fugu default", "not codex"):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as other:
                env = dict(self.env)
                if change == "no sign-in":
                    env["CODEX_HOME"] = other
                elif change == "no codex":
                    env["PATH"] = other
                elif change == "relative path":
                    env["PATH"] = "bin"
                elif change == "relative home":
                    env["CODEX_HOME"] = ".codex"
                elif change in ("other provider", "fugu default"):
                    config = Path(other) / "config.toml"
                    config.write_text('model_provider = "local"\n[model_providers.local]\nbase_url = "http://localhost"\n'
                                      if change == "other provider" else
                                      'model_provider = "sakana"\n[model_providers.sakana]\n'
                                      'base_url = "https://api.sakana.ai/v1"\nenv_key = "SAKANA_API_KEY"\n')
                    (Path(other) / "auth.json").write_text("{}")
                    env["CODEX_HOME"] = other
                else:
                    (self.bin / "codex").unlink()
                    shim = Path(other) / "codex-shim"
                    shim.touch(); shim.chmod(0o700)
                    (self.bin / "codex").symlink_to(shim)
                self.assertIsNone(sources.configured_codex_scope(env))
        self.assertIsNone(sources.configured_codex_scope({"PATH": str(self.bin)}))

    @needs_tomllib
    def test_unreadable_settings_are_reported_not_hidden(self):
        (self.root / "config.toml").write_text("not = [toml")
        with self.assertRaisesRegex(sources.SourceError, "^Account unknown$"):
            sources.configured_codex_scope(self.env)


class OldPythonTests(unittest.TestCase):
    """Python 3.10 and older have no tomllib, so Codex settings can't be read."""

    def test_codex_and_fugu_say_they_need_python_3_11(self):
        with tempfile.TemporaryDirectory() as temporary, patch.dict(sys.modules, {"tomllib": None}):
            home = Path(temporary)
            root = home / ".codex"
            root.mkdir()
            (root / "config.toml").write_text("")
            exe = home / "codex"
            exe.touch(); exe.chmod(0o700)
            with self.assertRaisesRegex(sources.SourceError, "^Needs Python 3.11 or newer$"):
                sources.scope_for("codex", {"argv": ["codex"], "executable": str(exe)}, {"HOME": str(home)})
            # Codex's own settings can't show a Fugu profile, so there is no Fugu row.
            self.assertIsNone(sources.configured_fugu_scope({"HOME": str(home)}))
            (root / "fugu.config.toml").write_text('model_provider = "sakana"\n')
            with self.assertRaisesRegex(sources.SourceError, "^Needs Python 3.11 or newer$") as caught:
                sources.configured_fugu_scope({"HOME": str(home)})
            self.assertEqual(caught.exception.provider, "fugu")


class UsageTests(unittest.TestCase):
    def setUp(self):
        # Collector fixtures do not use the machine's installed Fugu or Codex account.
        for name in ("configured_fugu_scope", "configured_codex_scope"):
            configured = patch.object(sources, name, return_value=None)
            configured.start()
            self.addCleanup(configured.stop)

    def test_configured_fugu_stays_visible_after_agent_exit_and_deduplicates_open_tab(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); server = {"key": "server", "lifetime": "one"}
            collector = usage.Collector(root / "state", root / "shared", "herdr", server)
            scope = {"key": "fugu-key", "provider": "fugu", "stamp": "stamp"}
            result = sources.parse_fugu({"window_seconds": 18000,
                "window_usage": {"usage_percent": 20},
                "weekly_usage": {"usage_percent": 35, "reset_at": 1800000600}}, "private-key", 1800000000)
            try:
                with patch.object(runtime, "server_identity", return_value=server), \
                        patch.object(field, "snapshot", return_value=([{"tab_id": "a"}], [
                            {"agent": "codex", "tab_id": "a", "pane_id": "p", "agent_status": "done"}], [])), \
                        patch.object(usage, "verified_scope", return_value=scope), \
                        patch.object(sources, "configured_fugu_scope", return_value=scope):
                    collector.scan()
                    self.assertEqual(len(collector.bindings), 1)
                    collector.cache["fugu-key"] = {"result": result, "state": "Measured"}
                    with patch.object(field, "snapshot", return_value=([], [], [])):
                        collector.scan()
                    snapshot = collector.write()
                self.assertEqual(len(snapshot["rows"]), 1)
                self.assertEqual(snapshot["rows"][0]["tabs"], 0)
                output = usage.render(snapshot, now=1800000030, width=61, zone=timezone.utc)
                self.assertIn("Fugu", output)
                self.assertIn("80%", output)
                self.assertIn("65%", output)
                self.assertNotIn("--", output.splitlines()[2])
                self.assertNotIn("private-key", (root / "shared/snapshot.json").read_text())
                with patch.object(runtime, "server_identity", return_value=server), \
                        patch.object(field, "snapshot", return_value=([], [], [])):
                    collector.scan()
                self.assertEqual(collector.write()["rows"], [])
                self.assertEqual(collector.cache, {})
            finally:
                collector.close()

    def test_configured_codex_and_an_open_codex_tab_on_its_login_make_one_row(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); server = {"key": "server", "lifetime": "one"}
            collector = usage.Collector(root / "state", root / "shared", "herdr", server)
            scope = {"key": "codex-key", "provider": "codex", "stamp": "stamp"}
            tab = ([{"tab_id": "a"}], [{"agent": "codex", "tab_id": "a", "pane_id": "p", "agent_status": "done"}], [])
            try:
                with patch.object(runtime, "server_identity", return_value=server), \
                        patch.object(field, "snapshot", return_value=tab), \
                        patch.object(usage, "verified_scope", return_value=scope), \
                        patch.object(sources, "configured_codex_scope", return_value=scope), \
                        patch.object(sources, "fetch") as fetch:
                    collector.scan()
                    self.assertEqual(len(collector.bindings), 1)
                    collector.schedule()
                    self.assertEqual(fetch.call_count, 1)
                    # A tab scoped by another path to the same account still shares one row.
                    collector.bindings["tab-key"] = {"scope": dict(scope, key="tab-key"), "tabs": {"a"}}
                    for key in ("codex-key", "tab-key"):
                        collector.cache[key] = {"result": dict(measured(), observed_at=1800000000), "state": "Measured"}
                    rows = collector.write()["rows"]
                self.assertEqual([row["provider"] for row in rows], ["codex"])
                table = usage.render({"rows": rows}, now=1800000030, width=88, zone=timezone.utc).split("\n\n")[0]
                self.assertEqual(sum(line.startswith("OpenAI") for line in table.splitlines()), 1)
                # The configured login stays after the tab closes.
                with patch.object(runtime, "server_identity", return_value=server), \
                        patch.object(field, "snapshot", return_value=([], [], [])), \
                        patch.object(sources, "configured_codex_scope", return_value=scope):
                    collector.scan()
                self.assertEqual([(row["provider"], row["tabs"]) for row in collector.write()["rows"]], [("codex", 0)])
            finally:
                collector.close()

    def test_configured_fugu_failure_and_rotation_hide_prior_values(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); server = {"key": "server", "lifetime": "one"}
            collector = usage.Collector(root / "state", root / "shared", "herdr", server)
            try:
                with patch.object(runtime, "server_identity", return_value=server), \
                        patch.object(field, "snapshot", return_value=([], [], [])), \
                        patch.object(sources, "configured_fugu_scope", return_value={"key": "first", "provider": "fugu"}):
                    collector.scan()
                    collector.cache["first"] = {"result": {"account": "old-account", "windows": []}, "state": "Measured"}
                    with patch.object(sources, "configured_fugu_scope", return_value={"key": "second", "provider": "fugu"}):
                        collector.scan()
                    self.assertEqual(collector.cache, {})
                    self.assertEqual(collector.write()["rows"], [{"provider": "fugu", "state": "Reading usage", "tabs": 0}])
                    with patch.object(sources, "configured_fugu_scope", side_effect=sources.SourceError("Sign in required")):
                        collector.scan()
                    self.assertEqual(collector.write()["rows"], [{"provider": "fugu", "state": "Sign in required", "tabs": 0}])
            finally:
                collector.close()

    def test_agy_structured_quota_uses_remaining_fraction_without_model_turns(self):
        payload = {"status": "SUCCESS", "num_turns": 0, "command": {"name": "usage", "data": {"groups": [
            {"name": "Gemini Models", "buckets": [
                {"id": "gemini-weekly", "window": "weekly", "remaining_fraction": .9064},
                {"id": "gemini-5h", "window": "5h", "remaining_fraction": .5847}]},
            {"name": "Claude and GPT models", "buckets": [
                {"id": "other-weekly", "window": "weekly", "remaining_fraction": 1}]}]}}}
        scope = {"env": {"HOME": "/profile"}}
        row = sources.parse_agy(payload, scope, 1800000000)
        self.assertEqual([v["remaining"] for v in row["windows"]], [90.6, 58.5, 100])
        output = usage.render({"rows": [dict(row, state="Measured")]}, now=1800000030, width=61)
        self.assertIn("Google", output)
        self.assertIn("58.5%", output)
        self.assertIn("90.6%", output)
        self.assertNotIn("Gemini Models", output)
        for turns in (1, None, False):
            with self.assertRaises(sources.SourceError):
                sources.parse_agy(dict(payload, num_turns=turns), scope, 0)
        bad = dict(payload, command={"name": "usage", "data": {"groups": [{"buckets": [
            {"window": "weekly", "remaining_fraction": "NaN"},
            {"window": "5h", "remaining_fraction": 2}]}]}})
        with self.assertRaises(sources.SourceError):
            sources.parse_agy(bad, scope, 0)

    def test_grok_saved_login_ignores_inherited_api_key_and_accepts_oauth_proxy(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            (home / ".grok").mkdir()
            (home / ".grok/auth.json").write_text("{}")
            process = {"argv": ["grok", "--api-key", "hidden"], "executable": str(home / "grok-1.0")}
            scope = sources.scope_for("grok", process, {"HOME": str(home), "XAI_API_KEY": "hidden", "XAI_BASE_URL": "https://other.invalid"})
            self.assertNotIn("hidden", json.dumps(scope))
            self.assertNotIn("XAI_API_KEY", scope["env"])
            row = {"provider": "grok", "source": "grok-cli-proxy", "usage": {
                "identity": {"accountEmail": "test@example.invalid"},
                "primary": {"usedPercent": 11, "windowMinutes": 10080}}}
            parsed = sources.parse_codexbar(row, "grok", 0)
            self.assertEqual(parsed["windows"][0]["remaining"], 89)
            with self.assertRaises(sources.SourceError):
                sources.parse_codexbar(dict(row, source="manual-cookie"), "grok", 0)

    def test_agy_probe_is_scoped_and_rejects_login_change(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            exe = home / "agy"
            exe.touch(); exe.chmod(0o700)
            token = home / ".gemini/antigravity-cli/antigravity-oauth-token"
            token.parent.mkdir(parents=True); token.write_text("first-login")
            scope = sources.scope_for("agy", {"argv": ["agy"], "executable": str(exe)},
                                      {"HOME": str(home), "GOOGLE_API_KEY": "hidden"})
            self.assertNotIn("hidden", json.dumps(scope))
            payload = {"status": "SUCCESS", "num_turns": 0, "command": {"name": "usage", "data": {
                "groups": [{"buckets": [{"window": "5h", "remaining_fraction": .7}]}]}}}
            with patch.object(runtime, "capture", return_value=(0, json.dumps(payload).encode())) as probe:
                self.assertEqual(sources.fetch(scope, home / "state", threading.Event())["windows"][0]["remaining"], 70)
                self.assertEqual(probe.call_args.args[0], [str(exe), "-p", "/usage", "--output-format", "json"])
                self.assertEqual(probe.call_args.kwargs["cwd"], str(home))
            def change_login(*args, **kwargs):
                token.write_text("another-login")
                return 0, json.dumps(payload).encode()
            with patch.object(runtime, "capture", side_effect=change_login):
                with self.assertRaisesRegex(sources.SourceError, "Account unknown"):
                    sources.fetch(scope, home / "state", threading.Event())

    def test_probe_cleanup_timeout_is_a_failed_read(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            exe = home / "agy"
            exe.touch(); exe.chmod(0o700)
            token = home / ".gemini/antigravity-cli/antigravity-oauth-token"
            token.parent.mkdir(parents=True); token.write_text("login")
            scope = sources.scope_for("agy", {"argv": ["agy"], "executable": str(exe)}, {"HOME": str(home)})
            # runtime.stop_process can time out while reaping a probe on a busy machine.
            with patch.object(runtime, "capture", side_effect=subprocess.TimeoutExpired(["agy"], 2)):
                with self.assertRaisesRegex(sources.SourceError, "Last update failed"):
                    sources.fetch(scope, home / "state", threading.Event())

    @needs_tomllib
    def test_fugu_modern_and_legacy_profiles_bind_the_configured_key(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary); root = home / ".codex"; root.mkdir()
            exe = home / "codex"; exe.touch(); exe.chmod(0o700)
            provider = '[model_providers.sakana]\nbase_url = "https://api.sakana.ai/v1"\nenv_key = "SAKANA_API_KEY"\n'
            (root / "config.toml").write_text(provider)
            modern = root / "fugu.config.toml"; modern.write_text('model_provider = "sakana"\n')
            process = {"argv": ["codex", "-p", "fugu"], "executable": str(exe)}
            env = {"HOME": str(home), "SAKANA_API_KEY": "private-key", "OPENAI_API_KEY": "other-key"}
            scope = sources.scope_for("codex", process, env)
            self.assertEqual(scope["provider"], "fugu")
            self.assertEqual(scope["api_key"], "private-key")
            self.assertIn(str(modern), scope["files"])
            modern.unlink()
            (root / "config.toml").write_text(provider + '\n[profiles.fugu]\nmodel_provider = "sakana"\n')
            legacy = sources.scope_for("codex", process, env)
            self.assertEqual(legacy["provider"], "fugu")
            self.assertNotEqual(scope["key"], legacy["key"])
            with self.assertRaisesRegex(sources.SourceError, "^Sakana API key missing$") as missing:
                sources.scope_for("codex", process, {"HOME": str(home)})
            self.assertEqual(missing.exception.provider, "fugu")
            (root / "config.toml").write_text(provider.replace("api.sakana.ai", "other.invalid") +
                                             '\n[profiles.fugu]\nmodel_provider = "sakana"\n')
            with self.assertRaises(sources.SourceError):
                sources.scope_for("codex", process, env)

    def test_failed_fugu_profiles_keep_the_service_route(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary); root = home / ".codex"; root.mkdir()
            exe = home / "codex"; exe.touch(); exe.chmod(0o700)
            process = {"argv": ["codex", "-p", "fugu"], "executable": str(exe)}
            config = root / "config.toml"
            for text in (None, "[broken", "", 'model_providers = "wrong"',
                         '[profiles.fugu]\nmodel_provider = "sakana"\n[model_providers.sakana]\nbase_url = 123'):
                with self.subTest(config=text):
                    if text is None:
                        config.unlink(missing_ok=True)
                    else:
                        config.write_text(text)
                    with self.assertRaises(sources.SourceError) as failure:
                        sources.scope_for("codex", process, {"HOME": str(home)})
                    self.assertEqual(failure.exception.provider, "fugu")
            config.write_text("")
            (root / "fugu.config.toml").write_text("[broken")
            with self.assertRaises(sources.SourceError) as failure:
                sources.scope_for("codex", process, {"HOME": str(home)})
            self.assertEqual(failure.exception.provider, "fugu")
            self.assertEqual(sources.route_hint("codex", ["codex", "--profile=fugu-work"]), "fugu")
            self.assertEqual(sources.route_hint("codex", ["codex-fugu"]), "fugu")
            self.assertEqual(sources.route_hint("codex", ["codex"]), "codex")

    def test_fugu_process_inspection_failure_stays_out_of_openai_row(self):
        for argv, provider in ((["codex", "-p", "fugu"], "fugu"), (["codex"], "codex")):
            with self.subTest(argv=argv), \
                    patch.object(field, "control", return_value={"process_info": {
                        "shell_pid": 1, "foreground_processes": [{"pid": 2, "argv": argv}]}}), \
                    patch.object(runtime, "process_start", return_value="started"), \
                    patch.object(runtime, "process_environment", side_effect=RuntimeError("unavailable")):
                with self.assertRaises(sources.SourceError) as failure:
                    usage.verified_scope("herdr", "codex", {"pane_id": "p"})
                self.assertEqual(failure.exception.provider, provider)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); server = {"key": "server", "lifetime": "one"}
            collector = usage.Collector(root / "state", root / "shared", "herdr", server)
            try:
                with patch.object(runtime, "server_identity", return_value=server), \
                        patch.object(field, "snapshot", return_value=([{"tab_id": "a"}], [
                            {"agent": "codex", "tab_id": "a", "pane_id": "p"}], [])), \
                        patch.object(usage, "verified_scope", side_effect=sources.SourceError("Account unknown", provider="fugu")):
                    collector.scan()
                self.assertEqual(collector.write()["rows"], [{"provider": "fugu", "state": "Account unknown", "tabs": 1}])
            finally:
                collector.close()

    def test_fugu_usage_percent_is_used_not_remaining(self):
        payload = {"plan": "max", "window_seconds": 18000,
                   "window_usage": {"usage_percent": 6.0848578}, "weekly_usage": {"usage_percent": 55.0257938}}
        row = sources.parse_fugu(payload, "private-key", 1800000000)
        self.assertEqual([v["remaining"] for v in row["windows"]], [93.9, 45])
        self.assertEqual([v["minutes"] for v in row["windows"]], [300, 10080])
        self.assertNotIn("private-key", json.dumps(row))
        self.assertNotIn("plan", row)
        for bad in ({}, {"weekly_usage": {"usage_percent": "NaN"}}, {"window_usage": {"usage_percent": -1}}):
            with self.assertRaises(sources.SourceError):
                sources.parse_fugu(bad, "private-key", 0)

    def test_codex_preserves_pools_without_default_duplicate(self):
        row = measured()
        self.assertEqual([v["remaining"] for v in row["windows"]], [79, 10, 95])
        self.assertEqual(row["credits"][0]["balance"], 12)
        self.assertNotIn("account-one", json.dumps(row))
        for identity in (None, "", " "):
            with self.assertRaisesRegex(sources.SourceError, "Account unknown"):
                sources.parse_codex({"type": "chatgpt"}, limits(identity), 0)
        with self.assertRaises(sources.SourceError):
            sources.parse_codex({"type": "apiKey"}, limits(), 0)

    def test_missing_and_invalid_windows_are_not_full(self):
        for used in (None, -1, 101, "NaN", "inf", True):
            self.assertIsNone(sources.window({"usedPercent": used}, "Weekly", "a"))
        self.assertEqual(sources.window({"usedPercent": 100}, "Weekly", "a")["remaining"], 0)

    def test_codexbar_identity_sources_and_four_antigravity_windows(self):
        row = {"provider": "claude", "source": "claude", "usage": {
            "identity": {"accountEmail": "test@example.invalid"}, "primary": {"usedPercent": 10}}}
        auth = {"loggedIn": True, "email": "test@example.invalid", "orgId": "org", "subscriptionType": "max"}
        self.assertEqual(sources.parse_codexbar([row], "claude", 0, auth)["plan"], "max")
        for mutation in ({"source": "web"}, {"usage": {"primary": {"usedPercent": 10}}}):
            with self.assertRaises(sources.SourceError):
                sources.parse_codexbar(dict(row, **mutation), "claude", 0, auth)
        with self.assertRaises(sources.SourceError):
            sources.parse_codexbar(row, "claude", 0, dict(auth, email="other@example.invalid"))
        grok = dict(row, provider="grok", source="grok-web")
        self.assertEqual(sources.parse_codexbar(grok, "grok", 0)["source"], "grok-web")
        agy = dict(row, provider="antigravity", source="cli", usage=dict(row["usage"], extraRateWindows=[
            {"id": str(i), "title": f"Pool {i}", "window": {"usedPercent": i}} for i in range(4)]))
        self.assertEqual(len(sources.parse_codexbar(agy, "antigravity", 0)["windows"]), 4)

    def test_auth_failure_differs_from_missing_account_identity(self):
        scope = {"executable": "claude", "env": {}}
        for data, state in (({"loggedIn": False}, "Sign in required"),
                            ({"loggedIn": True, "email": ""}, "Account unknown")):
            with patch.object(runtime, "capture", return_value=(0, json.dumps(data).encode())):
                with self.assertRaisesRegex(sources.SourceError, state):
                    sources.auth_status(scope, time.monotonic() + 1, threading.Event())

    def test_scope_rejects_ambient_account_and_overrides(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            exe = home / "codex"
            exe.touch()
            exe.chmod(0o700)
            process = {"argv": ["codex", "-m", "gpt-6-astra"], "executable": str(exe)}
            for env in ({}, {"HOME": "relative"}, {"HOME": str(home), "OPENAI_API_KEY": "secret"}):
                with self.assertRaisesRegex(sources.SourceError, "Account unknown"):
                    sources.scope_for("codex", process, env)
            scope = sources.scope_for("codex", process, {"HOME": str(home), "UNRELATED_SECRET": "secret"})
            self.assertNotIn("secret", json.dumps(scope))
            with self.assertRaisesRegex(sources.SourceError, "Limits unavailable"):
                sources.scope_for("codex", dict(process, argv=["codex", "-p", "fugu"]), {"HOME": str(home)})

    @needs_tomllib
    def test_codex_settings_for_another_provider_are_not_openai(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            exe = home / "codex"
            exe.touch()
            exe.chmod(0o700)
            process = {"argv": ["codex", "-m", "gpt-6-astra"], "executable": str(exe)}
            (home / ".codex").mkdir()
            (home / ".codex/config.toml").write_text('model_provider = "fugu"')
            with self.assertRaisesRegex(sources.SourceError, "Limits unavailable"):
                sources.scope_for("codex", process, {"HOME": str(home)})

    def test_membership_includes_idle_tabs_in_other_workspaces(self):
        agents = [{"agent": "codex", "tab_id": "a", "pane_id": "1", "agent_status": "idle"},
                  {"agent": "codex", "tab_id": "a", "pane_id": "2", "agent_status": "done"},
                  {"agent": "grok", "tab_id": "b", "pane_id": "3"},
                  {"agent": "claude", "tab_id": "closed", "pane_id": "4"}]
        self.assertEqual(set(usage.eligible([{"tab_id": "a"}, {"tab_id": "b"}], agents)), {"codex", "grok"})

    def test_collector_agy_binds_profile_and_deduplicates_tabs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            server = {"key": "server", "lifetime": "one"}
            collector = usage.Collector(root / "state", root / "shared", "herdr", server)
            tabs = [{"tab_id": "a"}, {"tab_id": "b"}]
            agents = [{"agent": "agy", "tab_id": "a", "pane_id": "1"},
                      {"agent": "agy", "tab_id": "a", "pane_id": "2"},
                      {"agent": "agy", "tab_id": "b", "pane_id": "3"}]
            try:
                with patch.object(runtime, "server_identity", return_value=server), patch.object(field, "snapshot", return_value=(tabs, agents, [])), \
                        patch.object(usage, "verified_scope", return_value={"key": "agy-profile", "provider": "agy"}):
                    collector.scan()
                self.assertEqual(collector.write()["rows"], [{"provider": "agy", "state": "Reading usage", "tabs": 2}])
                with patch.object(runtime, "server_identity", return_value=server), patch.object(field, "snapshot", side_effect=RuntimeError()):
                    collector.scan()
                self.assertEqual(collector.write()["rows"][0]["tabs"], 2)
                with patch.object(runtime, "server_identity", return_value=dict(server, lifetime="two")), patch.object(field, "snapshot", side_effect=RuntimeError()):
                    collector.scan()
                self.assertEqual(collector.write()["rows"], [])
            finally:
                collector.close()

    def test_windows_dependency_state_keeps_agy_identity_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            server = {"key": "server", "lifetime": "one"}
            collector = usage.Collector(root / "state", root / "shared", "herdr", server)
            tabs = [{"tab_id": provider} for provider in ("claude", "grok", "agy")]
            agents = [{"agent": tab["tab_id"], "tab_id": tab["tab_id"], "pane_id": "p"} for tab in tabs]
            try:
                with patch.object(usage.os, "name", "nt"), patch.object(runtime, "server_identity", return_value=server), \
                        patch.object(field, "snapshot", return_value=(tabs, agents, [])), \
                        patch.object(usage, "verified_scope", side_effect=sources.SourceError("Account unknown")):
                    collector.scan()
                rows = {row["provider"]: row["state"] for row in collector.write()["rows"]}
                self.assertEqual(rows, {"agy": "Account unknown", "claude": "CodexBar has no Windows build", "grok": "CodexBar has no Windows build"})
            finally:
                collector.close()

    def test_accounts_group_tabs_and_late_response_is_discarded(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            collector = usage.Collector(root / "state", root / "shared", "herdr", {"key": "s", "lifetime": "l"})
            collector.validated = True
            try:
                for key, tabs, account in (("a", {"1", "2"}, "one"), ("b", {"2", "3"}, "one"), ("c", {"4"}, "two")):
                    collector.bindings[key] = {"scope": {"key": key, "provider": "codex", "stamp": "stamp"}, "tabs": tabs}
                    collector.cache[key] = {"result": measured(account), "state": "Measured"}
                rows = collector.write()["rows"]
                self.assertEqual(sorted(row["tabs"] for row in rows), [1, 3])
                future = Future()
                generation = collector.generation(collector.bindings["a"])
                collector.pending["a"] = future, generation
                collector.bindings["a"]["tabs"] = {"new"}
                future.set_result(measured("wrong"))
                collector.drain()
                self.assertEqual(collector.cache["a"]["result"]["account"], measured("one")["account"])
                collector.pending["gone"] = future, generation
                collector.drain()
                self.assertNotIn("gone", collector.cache)
            finally:
                collector.close()

    def test_failures_keep_age_and_honor_retry(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            collector = usage.Collector(root / "state", root / "shared", "herdr", {"key": "s"})
            try:
                entry = {"scope": {"key": "a", "provider": "codex", "stamp": "stamp"}, "tabs": {"tab"}}
                collector.bindings["a"] = entry
                collector.cache["a"] = {"result": measured(), "failures": 0}
                for delay in (120, 240, 300):
                    future = Future()
                    future.set_exception(sources.SourceError("Last update failed"))
                    collector.pending["a"] = future, collector.generation(entry)
                    with patch.object(usage.time, "time", return_value=100):
                        collector.drain()
                    self.assertEqual(collector.cache["a"]["next_at"], 100 + delay)
                    self.assertEqual(collector.cache["a"]["result"]["observed_at"], 1800000000)
                future = Future()
                future.set_exception(sources.SourceError("Last update failed", 900))
                collector.pending["a"] = future, collector.generation(entry)
                with patch.object(usage.time, "time", return_value=100):
                    collector.drain()
                self.assertEqual(collector.cache["a"]["next_at"], 1000)
            finally:
                collector.close()

    def test_stale_and_expired_resets_do_not_change_usage(self):
        row = dict(measured(), state="Last update failed", tabs=2)
        row["windows"][1]["reset_text"] = "next Tuesday"
        output = usage.render({"rows": [row]}, now=1800000300, width=28)
        self.assertIn("Waiting for fresh", output)
        self.assertIn("79%*", output)
        self.assertNotIn("next Tuesday", output)
        self.assertNotIn("Reset", output)
        self.assertTrue(all(len(line) <= 28 for line in output.splitlines()))
        with patch.object(field, "ZoneInfo", side_effect=field.ZoneInfoNotFoundError()):
            self.assertIn("79%*", usage.render({"rows": [row]}, now=1800000300))

    def test_table_groups_services_and_keeps_accounts_and_unknowns(self):
        first = dict(measured("one"), state="Measured", tabs=18)
        second = dict(measured("two"), state="Measured", tabs=3)
        snapshot = {"rows": [first, second, {"provider": "codex", "tabs": 7, "state": "Account unknown"},
                             {"provider": "agy", "tabs": 2, "state": "Account unknown"}]}
        output = usage.render(snapshot, now=1800000030, width=88)
        self.assertEqual(sum(line.startswith("OpenAI ") for line in output.splitlines()), 1)
        self.assertEqual(sum(line.startswith("Google ") for line in output.splitlines()), 1)
        self.assertNotIn(first["account"][:6], output)
        self.assertNotIn(second["account"][:6], output)
        self.assertEqual(output.count("79%"), 1)
        self.assertEqual(output.count("Account unknown"), 1)
        self.assertNotIn("agent", output)
        self.assertNotIn("tabs", output)
        for width in (20, 28, 32, 38, 39, 43, 44, 49, 50, 61, 88):
            plain = usage.render(snapshot, now=1800000030, width=width)
            self.assertTrue(all(len(line) <= width for line in plain.splitlines()), (width, plain))

    def test_percentage_table_omits_details_and_uses_lowest_allowance(self):
        row = dict(measured(), state="Measured", tabs=4, plan="promax")
        row["windows"] += [{"id": "other-week", "label": "Model identifier", "minutes": 10080,
                            "remaining": 2, "reset_at": 1800003600}]
        output = usage.render({"rows": [row]}, now=1800000030, width=61)
        self.assertIn("Session left", output)
        self.assertIn("Week left", output)
        self.assertIn("79%", output)
        self.assertIn("2%", output)
        self.assertNotIn("10%", output)
        for hidden in ("promax", "credits", "Updated", "Native", "Model identifier", row["account"][:6], "█", "░"):
            self.assertNotIn(hidden, output)
        self.assertIn("Week resets", output)

    def test_weekly_reset_follows_lowest_percentage_across_logins(self):
        now = 1704067200  # Monday, January 1, 2024, 00:00 UTC.
        rows = [dict(measured("one"), state="Measured", observed_at=now),
                dict(measured("two"), state="Measured", observed_at=now)]
        rows[0]["windows"] = [
            {"id": "session", "label": "Session", "minutes": 300, "remaining": 5, "reset_at": now + 3600},
            {"id": "week", "label": "Weekly", "minutes": 10080, "remaining": 10, "reset_at": now + 86400}]
        rows[1]["windows"] = [{"id": "week", "label": "Weekly", "minutes": 10080,
                               "remaining": 5, "reset_at": 1704626100}]
        output = usage.render({"rows": rows}, now=now, width=61,
                              zone=timezone(timedelta(hours=-5), "EST"))
        self.assertEqual(sum(line.startswith("OpenAI ") for line in output.splitlines()), 1)
        self.assertIn("Sun 06:15 EST", output)
        self.assertNotIn("Mon 19:00", output)
        self.assertNotIn("Sun 20:00", output)

    def test_weekly_reset_handles_ties_missing_and_expired_times(self):
        now = 1704067200
        for resets, expected in (((1704626100, 1704626100), "Sun 11:15 UTC"),
                                 ((1704626100, 1704539700), "Varies"),
                                 ((1704626100, None), "--"),
                                 ((None, None), "--"),
                                 ((1704626100, now - 1), "--")):
            with self.subTest(resets=resets):
                row = dict(measured(), state="Measured", observed_at=now)
                row["windows"] = [{"id": str(index), "label": "Weekly", "minutes": 10080,
                                    "remaining": 10, "reset_at": reset} for index, reset in enumerate(resets)]
                output = usage.render({"rows": [row]}, now=now, width=61, zone=timezone.utc)
                self.assertTrue(output.splitlines()[2].endswith(expected), output)
                if now - 1 in resets:
                    self.assertIn("10%*", output)
                    self.assertIn("Waiting for fresh limits", output)
                row["windows"].reverse()
                self.assertEqual(usage.render({"rows": [row]}, now=now, width=61, zone=timezone.utc), output)

    def test_claude_total_reset_wins_tied_model_windows(self):
        now = 1704067200
        auth = {"loggedIn": True, "email": "test@example.invalid", "orgId": "org"}
        payload = {"provider": "claude", "source": "claude", "rateWindowLabels": {
            "primary": "Session", "secondary": "Weekly", "tertiary": "Model only"}, "usage": {
            "identity": {"accountEmail": auth["email"]},
            "primary": {"usedPercent": 0, "windowMinutes": 300, "resetsAt": now + 18000},
            "secondary": {"usedPercent": 0, "windowMinutes": 10080, "resetsAt": "2024-01-07T11:15:00Z"},
            "tertiary": {"usedPercent": 0, "windowMinutes": 10080, "resetsAt": now + 86400},
            "extraRateWindows": [{"id": "claude-weekly-scoped-fable", "title": "Fable only", "window": {
                "usedPercent": 0, "windowMinutes": 10080, "resetsAt": now + 172800}}]}}
        row = dict(sources.parse_codexbar(payload, "claude", now, auth), state="Measured")
        self.assertEqual([w["scope"] for w in row["windows"]], ["service", "service", "model", "model"])
        output = usage.render({"rows": [row]}, now=now, width=61, zone=timezone.utc)
        self.assertIn("Sun 11:15 UTC", output)
        self.assertNotIn("Varies", output)
        self.assertEqual(output.count("100%"), 2)
        row["windows"].reverse()
        self.assertEqual(usage.render({"rows": [row]}, now=now, width=61, zone=timezone.utc), output)
        for window in row["windows"]:
            window.pop("scope")  # Upgrade an older shared snapshot without a fresh probe.
        self.assertEqual(usage.render({"rows": [row]}, now=now, width=61, zone=timezone.utc), output)
        row["windows"][0]["remaining"] = 20
        output = usage.render({"rows": [row]}, now=now, width=61, zone=timezone.utc)
        self.assertIn("20%", output)
        self.assertIn("Wed 00:00 UTC", output)
        self.assertNotIn("Sun 11:15 UTC", output)
        row["windows"][0]["remaining"] = 100
        other = dict(row, windows=[dict(w, reset_at=now + 86400) for w in row["windows"]])
        self.assertIn("Varies", usage.render({"rows": [row, other]}, now=now, width=61, zone=timezone.utc))
        # A model limit from a different login must keep its own reset in the tie.
        row["windows"] = [dict(row["windows"][0], remaining=80)]
        other["windows"] = [dict(w, remaining=80 if w["id"] == "secondary" else 90)
                            for w in other["windows"]]
        output = usage.render({"rows": [row, other]}, now=now, width=61, zone=timezone.utc)
        self.assertIn("80%", output)
        self.assertIn("Varies", output)

    def test_codex_reset_credits_give_the_count_and_the_oldest_expiry(self):
        def credit(expires, status="available"):
            return {"id": "private-id", "grantedAt": 1, "resetType": "codexRateLimits", "status": status, "expiresAt": expires}
        for summary, expected in (
                ({"availableCount": 2, "credits": [credit(1800500000), credit(1800100000), credit(1, "redeemed")]},
                 {"count": 2, "expiry_known": True, "expires_at": 1800100000}),
                # Only the count came back, or the list was capped: the oldest isn't guessed.
                ({"availableCount": 2, "credits": None}, {"count": 2, "expiry_known": False, "expires_at": None}),
                ({"availableCount": 2, "credits": [credit(1800100000)]}, {"count": 2, "expiry_known": False, "expires_at": None}),
                ({"availableCount": 1, "credits": [credit(None)]}, {"count": 1, "expiry_known": True, "expires_at": None}),
                ({"availableCount": 0, "credits": []}, {"count": 0, "expiry_known": True, "expires_at": None}),
                # An expiry that can't be read is unknown, never "no expiry".
                ({"availableCount": 1, "credits": [credit("soon")]}, {"count": 1, "expiry_known": False, "expires_at": None}),
                ({"availableCount": 1, "credits": [credit(0)]}, {"count": 1, "expiry_known": False, "expires_at": None}),
                ({"availableCount": 1, "credits": [credit("2027-01-15T08:00:00Z")]},
                 {"count": 1, "expiry_known": True, "expires_at": 1800000000.0}),
                (None, None), ({"availableCount": True}, None), ({"availableCount": -1}, None)):
            with self.subTest(summary=summary):
                self.assertEqual(sources.parse_reset_credits(summary), expected)
        data = dict(limits(), rateLimitResetCredits={"availableCount": 1, "credits": [credit(1800100000)]})
        result = sources.parse_codex({"type": "chatgpt"}, data, 1800000000)
        self.assertEqual(result["resets"], {"count": 1, "expiry_known": True, "expires_at": 1800100000})
        self.assertNotIn("private-id", json.dumps(result))
        self.assertNotIn("resets", measured())

    def test_reset_columns_list_every_service_and_say_unknown_when_not_reported(self):
        now = 1800000000
        def codex(count, expires=now + 6 * 86400, known=True, account="one"):
            return dict(measured(account), state="Measured", observed_at=now,
                        resets={"count": count, "expiry_known": known, "expires_at": expires})
        claude = {"provider": "claude", "state": "Measured", "observed_at": now, "account": "c",
                  "windows": [{"id": "secondary", "minutes": 10080, "remaining": 55, "reset_at": now + 3600, "scope": "service"}]}
        heading = lambda output: output.splitlines()[0]
        # Claude doesn't report saved resets, so it says unknown instead of looking like none.
        for rows in ([claude], [claude, codex(0)], [claude, codex(2)]):
            output = usage.render({"rows": rows}, now=now, width=88, zone=timezone.utc)
            self.assertTrue(heading(output).endswith("Week resets        Resets  Expires"), output)
            self.assertRegex(next(line for line in output.splitlines() if line.startswith("Claude")), r"\sunknown  unknown$")
        lines = usage.render({"rows": [claude, codex(2)]}, now=now, width=88, zone=timezone.utc).splitlines()
        self.assertRegex(next(line for line in lines if line.startswith("OpenAI")), r"\s2  Jan 21$")
        for rows, cells in (([codex(0)], r"\s0  --$"), ([codex(1, None)], r"\s1  No expiry$"),
                            ([codex(2, None, False)], r"\s2  unknown$"),
                            ([codex(1, now + 9 * 86400), codex(2, now + 6 * 86400, account="two")], r"\s3  Jan 21$"),
                            ([codex(1, now + 9 * 86400), codex(1, None, False, account="two")], r"\s2  unknown$"),
                            # A date already past, or a login that didn't report resets, leaves it unknown.
                            ([codex(2, now - 60)], r"\s2  unknown$"),
                            ([codex(1, now + 9 * 86400), dict(measured("two"), state="Measured", observed_at=now)], r"\s1  unknown$"),
                            ([codex(1, now + 9 * 86400), {"provider": "codex", "state": "Reading usage", "tabs": 1}], r"\s1  unknown$"),
                            # A Codex read without a reset report is unknown too, not zero.
                            ([dict(measured(), state="Measured", observed_at=now)], r"\sunknown  unknown$")):
            with self.subTest(rows=len(rows), cells=cells):
                table = usage.render({"rows": rows}, now=now, width=88, zone=timezone.utc).splitlines()
                self.assertRegex(next(line for line in table if line.startswith("OpenAI")), cells)
        # Too narrow for the two columns: the table stays as it is, with one line under it.
        narrow = usage.render({"rows": [claude, codex(2)]}, now=now, width=69, zone=timezone.utc)
        self.assertNotIn("Resets", heading(narrow))
        self.assertNotIn(usage.UNKNOWN, narrow)
        self.assertIn("OpenAI: 2 resets. The oldest expires Jan 21.", narrow)
        self.assertIn("OpenAI: 1 reset. It doesn't expire.", usage.render({"rows": [codex(1, None)]}, now=now, width=40))
        self.assertIn("OpenAI: 2 resets.\n", usage.render({"rows": [codex(2, None, False)]}, now=now, width=40))
        # The note is information, so it isn't drawn in the warning color.
        self.assertIn("\nOpenAI: 2 resets. The oldest", usage.render({"rows": [codex(2)]}, now=now, width=60, color=True, zone=timezone.utc))
        for width in range(20, 121):
            for rows in ([claude], [claude, codex(2)]):
                output = usage.render({"rows": rows}, now=now, width=width, zone=timezone.utc)
                # The reset columns leave the rest of the layout as it was.
                self.assertEqual("Percent left." in output, width < 32, (width, output))
                self.assertEqual("Resets" in heading(output), width >= 70, (width, output))
                for line in output.splitlines():
                    self.assertLessEqual(len(line), width, (width, line))

    def test_hand_entered_resets_fill_unreported_services_until_their_day_ends(self):
        now = 1800000000  # Jan 15 2027, 08:00 UTC
        claude = {"provider": "claude", "state": "Measured", "observed_at": now, "account": "c",
                  "windows": [{"id": "secondary", "minutes": 10080, "remaining": 55, "reset_at": now + 3600, "scope": "service"}]}
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "hand_resets.json"
            path.write_text(json.dumps({"claude": {"count": 1, "expires": "2027-01-20"},
                                        "codex": {"count": 2, "expires": "2027-01-20"},
                                        "grok": {"count": True, "expires": "2027-01-20"},
                                        "fugu": {"count": 0, "expires": "2027-01-20"},
                                        "agy": {"count": 1, "expires": "2027-13-01"}, "gemini": [1]}))
            hand = usage.hand_resets(path)
            self.assertEqual(sorted(hand), ["claude", "codex"])
            for text in ("{not json", "[]"):
                path.write_text(text)
                self.assertEqual(usage.hand_resets(path), {})
            self.assertEqual(usage.hand_resets(Path(temporary) / "missing.json"), {})
        live = dict(measured(), state="Measured", observed_at=now,
                    resets={"count": 0, "expiry_known": True, "expires_at": None})
        render = lambda moment, width=88: usage.render({"rows": [claude, live]}, now=moment, width=width,
                                                       zone=timezone.utc, hand=hand)
        output = render(now)
        self.assertRegex(next(line for line in output.splitlines() if line.startswith("Claude")), r"\s1  Jan 20$")
        self.assertIn("\nClaude resets: entered by hand.", output)
        # A live report replaces the hand entry.
        self.assertRegex(next(line for line in output.splitlines() if line.startswith("OpenAI")), r"\s0  --$")
        self.assertNotIn("OpenAI resets", output)
        narrow = render(now, 60)
        self.assertIn("Claude: 1 reset. It expires Jan 20.\nClaude resets: entered by hand.", narrow)
        # The entry lasts to the end of its expiry day, then the service is unknown again.
        last = 1800000000 + 5 * 86400 + 16 * 3600 - 1  # Jan 20 23:59:59 UTC
        self.assertIn("entered by hand", render(last))
        after = render(last + 1)
        self.assertRegex(next(line for line in after.splitlines() if line.startswith("Claude")), r"\sunknown  unknown$")
        self.assertNotIn("entered by hand", after)
        for width in range(20, 121):
            for line in render(now, width).splitlines():
                self.assertLessEqual(len(line), width, (width, line))

    def test_stale_resets_on_a_higher_allowance_login_are_not_reported_as_current(self):
        now = 1800000000
        fresh = dict(measured("fresh"), state="Measured", observed_at=now)
        for window in fresh["windows"]:
            window["reset_at"] = now + 3600
        stale = dict(measured("stale"), state="Last update failed", observed_at=now - 121,
                     resets={"count": 2, "expiry_known": True, "expires_at": now + 6 * 86400})
        for window in stale["windows"]:
            window["remaining"] = 99
        for width in (60, 88):
            output = usage.render({"rows": [fresh, stale]}, now=now, width=width, zone=timezone.utc)
            with self.subTest(width=width):
                self.assertIn("Waiting for fresh limits", output)
                self.assertNotIn("2 resets", output)
                self.assertNotIn("Jan 21", output)

    def test_a_newer_reading_without_resets_drops_the_older_resets(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            collector = usage.Collector(root / "state", root / "shared", "herdr", {"key": "server", "lifetime": "one"})
            try:
                collector.validated = True
                older = dict(measured(), observed_at=1800000000, resets={"count": 2, "expiry_known": True, "expires_at": 1800100000})
                newer = dict(measured(), observed_at=1800000060)
                for key, result in (("a", older), ("b", newer)):
                    collector.bindings[key] = {"scope": {"key": key, "provider": "codex"}, "tabs": {key}}
                    collector.cache[key] = {"result": result, "state": "Measured"}
                rows = collector.write()["rows"]
                self.assertEqual(len(rows), 1)
                self.assertNotIn("resets", rows[0])
            finally:
                collector.close()

    def test_partial_unreadable_tabs_do_not_warn_when_plan_is_measured(self):
        row = dict(measured(), state="Measured")
        for state in ("Account unknown", "Limits unavailable", "Reading usage", *sources.DEPENDENCY_STATES):
            with self.subTest(state=state):
                notice = {"provider": "codex", "state": state}
                plain = usage.render({"rows": [row]}, now=1800000030, width=61)
                self.assertEqual(usage.render({"rows": [row, notice]}, now=1800000030, width=61), plain)
                self.assertIn(state, " ".join(usage.render({"rows": [notice]}, now=1800000030, width=61).split()))
        for state in ("Last update failed", "Sign in required"):
            notice = {"provider": "codex", "state": state}
            self.assertIn(f"OpenAI: {state}.", usage.render({"rows": [row, notice]}, now=1800000030, width=61))

    def test_percentages_align_right_and_resets_are_not_dimmed(self):
        now = 1704067200
        row = dict(measured(), state="Measured", observed_at=now)
        row["windows"] = [{"id": "week", "label": "Weekly", "minutes": 10080,
                            "remaining": 87.3, "reset_at": now + 3600}]
        other = dict(row, provider="grok", windows=[dict(row["windows"][0], remaining=9)])
        plain = usage.render({"rows": [row, other]}, now=now, width=61, zone=timezone.utc)
        lines = plain.splitlines()
        self.assertEqual(lines[2].index("87.3%") + 5, lines[3].index("9%") + 2)
        with patch.dict(os.environ, {"TERM": "xterm"}):
            os.environ.pop("NO_COLOR", None)
            colored = usage.render({"rows": [row]}, now=now, width=61, color=True, zone=timezone.utc)
            self.assertIn("Mon 01:00 UTC", colored)
            self.assertNotIn("\033[2mMon", colored)

    def test_weekly_reset_timezones_and_narrow_layout(self):
        now = 1704067200
        row = dict(measured(), state="Measured", observed_at=now)
        row["windows"] = [{"id": "week", "label": "Weekly", "minutes": 10080,
                            "remaining": 40, "reset_at": now + 3600}]
        for zone, expected in ((timezone.utc, "Mon 01:00 UTC"),
                               (timezone(timedelta(hours=-5), "EST"), "Sun 20:00 EST"),
                               (timezone(timedelta(hours=5, minutes=30)), "Mon 06:30 +0530")):
            with self.subTest(zone=zone):
                for width in (39, 49, 50, 61):
                    output = usage.render({"rows": [row]}, now=now, width=width, zone=zone)
                    self.assertIn(expected, output)
                    self.assertTrue(all(len(line) <= width for line in output.splitlines()), output)
        output = usage.render({"rows": [row]}, now=now, width=38, zone=timezone.utc)
        self.assertNotIn("Mon 01:00", output)
        self.assertIn("Widen pane for weekly reset times.", output)

    def test_missing_period_and_unreadable_login_do_not_make_percentages(self):
        row = dict(measured(), state="Measured", tabs=4)
        row["windows"] = [row["windows"][1]]
        output = usage.render({"rows": [row, {"provider": "grok", "state": "Account unknown", "tabs": 3}]},
                              now=1800000030, width=61)
        self.assertEqual(next(line.split()[1] for line in output.splitlines() if line.startswith("OpenAI ")), "--")
        self.assertEqual(next(line.split()[1] for line in output.splitlines() if line.startswith("Grok ")), "--")
        self.assertNotIn("0%", output.replace("10%", ""))
        self.assertIn("Grok: Account unknown.", output)

    def test_table_color_preserves_plain_text_and_no_color(self):
        import re
        snapshot = {"rows": [dict(measured(), state="Measured", tabs=4)]}
        plain = usage.render(snapshot, now=1800000030, width=61)
        with patch.dict(os.environ, {"TERM": "xterm"}):
            env = dict(os.environ)
            env.pop("NO_COLOR", None)
            with patch.dict(os.environ, env, clear=True):
                styled = usage.render(snapshot, now=1800000030, width=61, color=True)
                self.assertIn("\033[1;31m", styled)
                self.assertEqual(re.sub(r"\x1b\[[0-9;]*m", "", styled), plain)
            with patch.dict(os.environ, {"NO_COLOR": ""}):
                self.assertEqual(usage.render(snapshot, now=1800000030, width=61, color=True), plain)

    def test_readout_viewport_scrolls_and_keeps_top_visible(self):
        output = "\n".join(f"Line {i}" for i in range(30)) + "\n"
        visible, offset = page(output, 0, 40, 10)
        self.assertEqual(visible.splitlines()[0], "Line 0")
        self.assertEqual(len(visible.splitlines()), 10)
        self.assertIn("Up/Down", visible)
        screen = Readout()
        screen.output = output
        screen.size = os.terminal_size((40, 10))
        screen.key("\033[6~")
        self.assertEqual(screen.offset, 8)
        screen.key("G")
        visible, screen.offset = page(output, screen.offset, 40, 10)
        self.assertIn("Line 29", visible)
        screen.key("g")
        self.assertEqual(screen.offset, 0)
        screen.feed("jj\033OB")
        self.assertEqual(screen.offset, 3)
        screen.key("q")
        self.assertTrue(screen.stop.is_set())
        self.assertEqual(page("Short\n", 20, 40, 10), ("Short", 0))
        self.assertEqual(len(page(output, 0, 20, 1)[0].splitlines()), 1)
        self.assertEqual(fit("你好abcdef", 6), "你好ab")
        self.assertEqual(fit("\033[31mabcdef\033[0m", 3), "\033[31mabc\033[0m")
        self.assertEqual(page("abcdef\n", 0, 3, 10), ("abc", 0))

    @unittest.skipIf(os.name == "nt", "requires a Unix pseudoterminal")
    def test_readout_live_terminal_scroll_and_close_restore_terminal(self):
        import fcntl
        import pty
        import select
        import struct
        import termios
        master, slave = pty.openpty()
        # A real pane has a size. Python before 3.11 keeps a 0x0 size instead of the default.
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
        code = r'''import sys
sys.path.insert(0, sys.argv[1])
from terminal_readout import Readout
with Readout() as r:
    r.draw("\n".join(str(i) for i in range(100)))
    while not r.stop.is_set():
        r.wait(.1)
'''
        proc = subprocess.Popen([sys.executable, "-c", code, str(ROOT / "bin")], stdin=slave, stdout=slave, stderr=slave)
        output = b""
        try:
            deadline = time.monotonic() + 5
            while b"Up/Down" not in output and time.monotonic() < deadline:
                if select.select([master], [], [], .1)[0]:
                    output += os.read(master, 8192)
            self.assertIn(b"Up/Down", output)
            os.write(master, b"G")
            deadline = time.monotonic() + 5
            while b"99" not in output and time.monotonic() < deadline:
                if select.select([master], [], [], .1)[0]:
                    output += os.read(master, 8192)
            self.assertIn(b"99", output)
            os.write(master, b"q")
            proc.wait(timeout=5)
            while select.select([master], [], [], .1)[0]:
                try:
                    chunk = os.read(master, 8192)
                    if not chunk:
                        break
                    output += chunk
                except OSError:
                    break
            self.assertEqual(proc.returncode, 0, output)
            self.assertIn(b"99", output)
            self.assertIn(b"\x1b[?25h\x1b[?1049l", output)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            os.close(master)
            os.close(slave)

    def test_kernel_lock_survives_file_age_and_releases_on_exit(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "lock"
            first, second = runtime.FileLock(path), runtime.FileLock(path)
            try:
                self.assertTrue(first.acquire())
                os.utime(path, (1, 1))
                self.assertFalse(second.acquire())
                first.close()
                self.assertTrue(second.acquire())
            finally:
                first.close()
                second.close()
            code = "import sys,time;sys.path.insert(0,sys.argv[1]);from usage_runtime import FileLock;l=FileLock(__import__('pathlib').Path(sys.argv[2]));assert l.acquire();print('locked',flush=True);time.sleep(30)"
            child = subprocess.Popen([sys.executable, "-c", code, str(ROOT / "bin"), str(path)], stdout=subprocess.PIPE, text=True)
            try:
                self.assertEqual(child.stdout.readline().strip(), "locked")
                child.terminate()
                child.wait(timeout=5)
                third = runtime.FileLock(path)
                self.assertTrue(third.acquire())
                third.close()
            finally:
                if child.poll() is None:
                    child.kill()
                child.wait()
                child.stdout.close()

    def test_socket_replacement_changes_lifetime(self):
        if os.name == "nt":
            self.skipTest("Unix socket lifetime")
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "socket"
            path.touch()
            response = subprocess.CompletedProcess([], 0, json.dumps({"server": {"running": True, "socket": str(path)}}), "")
            with patch.object(runtime.subprocess, "run", return_value=response):
                before = runtime.server_identity("herdr", 1)
                new = path.with_name("new")
                new.touch()
                os.replace(new, path)
                after = runtime.server_identity("herdr", 1)
            self.assertEqual(before["key"], after["key"])
            self.assertNotEqual(before["lifetime"], after["lifetime"])

    def test_watcher_reports_slow_herdr_status_and_recovers(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "socket").touch()
            response = subprocess.CompletedProcess([], 0, json.dumps({"server": {"running": True, "socket": str(root / "socket")}}), "")
            calls, draws = [], []

            def status(command, **kwargs):
                calls.append(command)
                if len(calls) == 1:
                    raise subprocess.TimeoutExpired(command, kwargs["timeout"])
                return response

            class Screen:
                def __init__(self, stop):
                    self.stop = stop

                def __enter__(self):
                    return self

                def __exit__(self, *_):
                    pass

                def draw(self, output):
                    draws.append(output)

                def wait(self, _):
                    if len(draws) == 2:
                        self.stop.set()

            with patch.dict(os.environ, {"XDG_STATE_HOME": str(root / "xdg")}), \
                    patch.object(runtime.subprocess, "run", side_effect=status), \
                    patch.object(field, "snapshot", return_value=([], [], [])), \
                    patch.object(usage, "Readout", Screen):
                usage.run(root / "state", "herdr", True, threading.Event(), 5)
            self.assertEqual(draws[0], "Plan Usage\nHerdr server status timed out\n")
            self.assertIn("No open service plans.", draws[1])

    def test_server_status_timeout_marks_collected_limits_stale_then_recovers(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            endpoint = root / "socket"
            endpoint.touch()
            response = subprocess.CompletedProcess([], 0, json.dumps({
                "server": {"running": True, "socket": str(endpoint)}}), "")
            with patch.object(runtime.subprocess, "run", return_value=response):
                server = runtime.server_identity("herdr", 2)
            collector = usage.Collector(root / "state", root / "shared", "herdr", server)
            collector.validated = True
            collector.bindings["a"] = {"scope": {"key": "a", "provider": "codex"}, "tabs": {"tab"}}
            collector.cache["a"] = {"state": "Measured", "result": measured()}
            try:
                failure = subprocess.TimeoutExpired(["herdr", "private-command-text"], 2)
                with patch.object(runtime.subprocess, "run", side_effect=failure):
                    collector.scan()
                collector.schedule()
                self.assertEqual(collector.pending, {})
                stale = collector.write()
                self.assertTrue(stale["scan_failed"])
                self.assertIn("Herdr scan failed.", usage.render(stale, now=1800000030))
                self.assertIn("79%*", usage.render(stale, now=1800000030))
                with patch.object(runtime.subprocess, "run", return_value=response), \
                        patch.object(field, "snapshot", return_value=([], [], [])):
                    collector.scan()
                recovered = collector.write()
                self.assertFalse(recovered["scan_failed"])
                self.assertEqual(recovered["rows"], [])
                self.assertEqual(collector.cache, {})
            finally:
                collector.close()


    def test_refresh_server_status_timeout_returns_clean_failure(self):
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(sys, "argv", ["plan-usage", "--state-dir", temporary, "refresh"]), \
                patch.object(usage.signal, "signal"), \
                patch.object(runtime.subprocess, "run", side_effect=subprocess.TimeoutExpired(
                    ["herdr", "private-command-text"], 2, output="private-output", stderr="private-stderr")), \
                patch.object(sys, "stderr", new_callable=io.StringIO) as errors, \
                patch.object(sys, "stdout", new_callable=io.StringIO) as output:
            result = usage.main()
        self.assertEqual(result, 1)
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(errors.getvalue(), "plan-usage: Herdr server status timed out\n")


    def test_codex_stdio_handshake_notifications_and_timeout(self):
        with tempfile.TemporaryDirectory() as temporary:
            script = Path(temporary) / "server.py"
            script.write_text('''import json,sys,time
for line in sys.stdin:
 d=json.loads(line)
 if d["method"]=="initialized":continue
 print(json.dumps({"method":"account/rateLimits/updated","params":{}}),flush=True)
 if d["method"]=="initialize":r={}
 elif d["method"]=="account/read":
  assert d["params"]["refreshToken"] is False
  r={"account":{"type":"chatgpt","planType":"pro"}}
 elif d["method"]=="account/rateLimits/read":r={"accountId":"fixture","rateLimits":{"primary":{"usedPercent":40}}}
 else:raise AssertionError(d)
 print(json.dumps({"id":d["id"],"result":r}),flush=True)
''')
            spawn = runtime.spawn
            scope = {"executable": "unused", "env": os.environ.copy()}
            with patch.object(runtime, "spawn", side_effect=lambda *_: spawn([sys.executable, str(script)], scope["env"])):
                self.assertEqual(sources.codex_read(scope, time.monotonic() + 5, threading.Event())["windows"][0]["remaining"], 60)
            with patch.object(runtime, "spawn", side_effect=lambda *_: spawn([sys.executable, "-c", "import time;time.sleep(30)"], scope["env"])):
                start = time.monotonic()
                with self.assertRaises(sources.SourceError):
                    sources.codex_read(scope, time.monotonic() + .15, threading.Event())
                self.assertLess(time.monotonic() - start, 4)

    def test_output_is_bounded_during_capture(self):
        with self.assertRaisesRegex(RuntimeError, "too large"):
            runtime.capture([sys.executable, "-c", "import sys;sys.stdout.write('x'*100000);sys.stdout.flush()"], os.environ.copy(), time.monotonic() + 5, limit=1024)

    def test_explicit_invalid_codexbar_never_falls_back(self):
        with patch.dict(os.environ, {"LANTERN_CODEXBAR_BIN": "/absent/tool"}), patch.object(sources.shutil, "which", side_effect=AssertionError("no fallback")):
            # Windows has no CodexBar build, so it says that before it looks at the setting.
            expected = sources.CODEXBAR_WINDOWS if os.name == "nt" else sources.CODEXBAR_SETTING
            with patch.object(sources, "install", side_effect=AssertionError("no install")), \
                    self.assertRaisesRegex(sources.SourceError, f"^{expected}$"):
                sources.resolve_codexbar(Path("state"), time.monotonic() + 1, threading.Event())

    @unittest.skipIf(os.name == "nt", "CodexBar has no Windows build")
    def test_missing_codexbar_is_installed_once_per_watcher(self):
        import http.client
        missing = "^CodexBar 0.74.0 not found. Ask Lantern to install it$"

        def resolve(state, cancel=None):
            return sources.resolve_codexbar(state, time.monotonic() + 5, cancel or threading.Event())

        def fake_install(state, cancel=None):
            tool = sources.tools_dir(state) / "CodexBarCLI"
            tool.parent.mkdir(parents=True)
            tool.write_text("#!/bin/sh\necho 'CodexBar 0.74.0'\n")
            tool.chmod(0o700)
            return tool

        @contextlib.contextmanager
        def machine(temporary):
            with patch.dict(os.environ, {"HERDR_PLUGIN_STATE_DIR": temporary}), \
                    patch.object(sources, "codexbar_asset", return_value="macos-arm64"), \
                    patch.object(sources.shutil, "which", return_value=None):
                os.environ.pop("LANTERN_CODEXBAR_BIN", None)
                yield

        # Installed once, then found without another install.
        with tempfile.TemporaryDirectory() as temporary:
            with machine(temporary), patch.object(sources, "install", side_effect=fake_install) as install:
                state = Path(temporary) / "herd"
                tool = str(sources.tools_dir(state) / "CodexBarCLI")
                self.assertEqual([resolve(state), resolve(state)], [tool, tool])
                install.assert_called_once()
        # Any failed install, including a garbled HTTP reply, leaves the hint and is not retried.
        for failure in (OSError("offline"), http.client.BadStatusLine("garbage")):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as temporary:
                with machine(temporary), patch.object(sources, "install", side_effect=failure) as install:
                    for _ in range(2):
                        with self.assertRaisesRegex(sources.SourceError, missing):
                            resolve(Path(temporary) / "herd")
                    install.assert_called_once()
        # A watcher that stops mid-download leaves the try for the next one. A read
        # whose watcher has already stopped starts no download.
        with tempfile.TemporaryDirectory() as temporary:
            stopped = threading.Event()

            def stopped_once(state, cancel=None):
                if not stopped.is_set():
                    stopped.set()
                    raise RuntimeError("CodexBar install was stopped")
                return fake_install(state)

            with machine(temporary), patch.object(sources, "install", side_effect=stopped_once) as install:
                state = Path(temporary) / "herd"
                for _ in range(2):
                    with self.assertRaisesRegex(sources.SourceError, missing):
                        resolve(state, stopped)
                self.assertEqual(install.call_count, 1)
                self.assertEqual(resolve(state), str(sources.tools_dir(state) / "CodexBarCLI"))
                self.assertEqual(install.call_count, 2)
        # A second row waits for the install in progress and then uses it.
        with tempfile.TemporaryDirectory() as temporary:
            started, release, found = threading.Event(), threading.Event(), []

            def slow_install(state, cancel=None):
                started.set()
                release.wait(5)
                return fake_install(state)
            with machine(temporary), patch.object(sources, "install", side_effect=slow_install) as install:
                state = Path(temporary) / "herd"
                rows = [threading.Thread(target=lambda: found.append(resolve(state))) for _ in range(2)]
                rows[0].start()
                started.wait(5)
                rows[1].start()
                time.sleep(.2)
                release.set()
                for row in rows:
                    row.join(5)
                self.assertEqual(found, [str(sources.tools_dir(state) / "CodexBarCLI")] * 2)
                install.assert_called_once()
        # No release for this system: every read says so, and nothing is downloaded.
        with tempfile.TemporaryDirectory() as temporary, \
                patch.dict(os.environ, {"HERDR_PLUGIN_STATE_DIR": temporary}), \
                patch.object(sources.platform, "system", return_value="FreeBSD"), \
                patch.object(sources.shutil, "which", return_value=None), \
                patch.object(sources.urllib.request, "urlopen", side_effect=AssertionError("no download")):
            os.environ.pop("LANTERN_CODEXBAR_BIN", None)
            for _ in range(2):
                with self.assertRaisesRegex(sources.SourceError, "^CodexBar has no build for this system$"):
                    resolve(Path(temporary) / "herd")

    def test_dependency_notes_say_what_is_missing_and_what_to_do(self):
        rows = [{"provider": "claude", "state": sources.CODEXBAR_MISSING},
                {"provider": "fugu", "state": sources.FUGU_KEY_MISSING},
                {"provider": "codex", "state": sources.NEEDS_PYTHON}]
        output = " ".join(usage.render({"rows": rows}, now=1800000030, width=61).split())
        for note in ("Claude: CodexBar 0.74.0 not found. Ask Lantern to install it.",
                     "Fugu: Sakana API key missing.", "OpenAI: Needs Python 3.11 or newer."):
            self.assertIn(note, output)

    def test_unknown_scopes_at_one_provider_do_not_overlap(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            collector = usage.Collector(root / "state", root / "shared", "herdr", {"key": "s"})
            collector.validated = True
            for key, provider in (("a", "claude"), ("b", "claude"), ("c", "codex")):
                collector.bindings[key] = {"scope": {"key": key, "provider": provider}, "tabs": {key}}
            try:
                with patch.object(collector.pool, "submit", side_effect=lambda *_: Future()):
                    collector.schedule()
                self.assertEqual(set(collector.pending), {"a", "c"})
            finally:
                collector.pending.clear()
                collector.close()

    def test_claude_keeps_absent_config_selector_absent(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            exe = home / "claude.exe"
            exe.touch()
            exe.chmod(0o700)
            process = {"argv": ["claude"], "executable": str(exe), "cwd": temporary}
            scope = sources.scope_for("claude", process, {"HOME": temporary})
            self.assertNotIn("CLAUDE_CONFIG_DIR", scope["env"])
            default_key = scope["key"]
            explicit = sources.scope_for("claude", process, {"HOME": temporary, "CLAUDE_CONFIG_DIR": str(home / ".claude")})
            self.assertNotEqual(default_key, explicit["key"])
            custom = str(home / "custom")
            scope = sources.scope_for("claude", process, {"HOME": temporary, "CLAUDE_CONFIG_DIR": custom})
            self.assertEqual(scope["env"]["CLAUDE_CONFIG_DIR"], custom)

    def test_claude_accepts_native_versioned_binary_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            def executable(relative):
                path = home / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.touch(); path.chmod(0o700)
                return str(path)
            for relative in (".local/share/claude/versions/2.1.292", "bin/claude"):
                with self.subTest(executable=relative):
                    scope = sources.scope_for("claude", {"argv": ["claude"], "executable": executable(relative)},
                                              {"HOME": temporary})
                    self.assertEqual(scope["executable"], str(home / relative))
            for provider, relative in (("claude", "tools/versions/1.2.3"),
                                       ("claude", "claude-tools/versions/1.2.3"),
                                       ("claude", ".local/share/claude/versions/latest"),
                                       ("claude", ".local/share/claude/versions/2.1"),
                                       ("claude", ".local/share/claude/builds/2.1.292"),
                                       ("codex", ".local/share/claude/versions/2.1.292")):
                with self.subTest(provider=provider, executable=relative), \
                        self.assertRaisesRegex(sources.SourceError, "Account unknown"):
                    sources.scope_for(provider, {"argv": [provider], "executable": executable(relative)},
                                      {"HOME": temporary})

    def test_native_claude_tab_is_selected_and_verified(self):
        with tempfile.TemporaryDirectory() as temporary:
            exe = Path(temporary) / ".local/share/claude/versions/2.1.292"
            exe.parent.mkdir(parents=True)
            exe.touch(); exe.chmod(0o700)
            info = {"process_info": {"shell_pid": 1, "foreground_processes": [
                {"pid": 2, "argv": ["claude", "--resume"], "cwd": temporary}]}}
            with patch.object(field, "control", return_value=info), \
                    patch.object(runtime, "process_start", return_value="started"), \
                    patch.object(runtime, "process_environment", return_value={"HOME": temporary}), \
                    patch.object(runtime, "process_executable", return_value=str(exe)):
                scope = usage.verified_scope("herdr", "claude", {"pane_id": "p"})
            self.assertEqual((scope["provider"], scope["executable"]), ("claude", str(exe)))

    @unittest.skipIf(os.name == "nt" or not shutil.which("cc"), "needs a C compiler on macOS or Linux")
    def test_program_path_survives_its_file_being_deleted(self):
        import select
        with tempfile.TemporaryDirectory() as temporary:
            source, program = Path(temporary) / "wait.c", Path(temporary) / "wait"
            source.write_text('#include <unistd.h>\nint main(void) { write(1, "R", 1); sleep(30); return 0; }\n')
            subprocess.run(["cc", "-o", str(program), str(source)], check=True)
            child = subprocess.Popen([str(program)], stdout=subprocess.PIPE)
            try:
                # proc_pidpath can name the image before dyld maps it. Wait
                # for main before reproducing an updater deleting that image.
                self.assertTrue(select.select([child.stdout], [], [], 5)[0], "Test program did not start")
                self.assertEqual(child.stdout.read(1), b"R")
                expected = os.path.realpath(program)
                self.assertEqual(runtime.process_executable(child.pid), expected)
                # Like Claude's updater removing a version that a tab still runs.
                program.unlink()
                self.assertEqual(runtime.process_executable(child.pid), expected)
                # Both systems report the old path again once a new file is there.
                program.write_text("replacement")
                self.assertEqual(runtime.process_executable(child.pid), expected)
            finally:
                child.kill()
                child.wait()
                child.stdout.close()

    def test_claude_tab_on_a_deleted_version_reads_through_the_current_version(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary).resolve()
            versions = home / ".local/share/claude/versions"
            elsewhere = home / "other/claude/versions"
            for folder in (versions, elsewhere, home / ".local/bin"):
                folder.mkdir(parents=True)
            for path in (versions / "2.1.301", versions / "helper", elsewhere / "2.1.302"):
                path.touch(); path.chmod(0o700)
            launcher = home / ".local/bin/claude"
            deleted = str(versions / "2.1.300")
            launcher.symlink_to(versions / "2.1.301")
            scope = sources.scope_for("claude", {"argv": ["claude"], "executable": deleted}, {"HOME": str(home)})
            self.assertEqual(scope["executable"], str(versions / "2.1.301"))
            for target in (elsewhere / "2.1.302", versions / "helper", None):
                launcher.unlink()
                if target:
                    launcher.symlink_to(target)
                with self.subTest(launcher=target), self.assertRaisesRegex(sources.SourceError, "Account unknown"):
                    sources.scope_for("claude", {"argv": ["claude"], "executable": deleted}, {"HOME": str(home)})
            launcher.symlink_to(versions / "2.1.301")
            for provider, executable in (("claude", str(versions / "latest")), ("claude", str(home / "gone/claude")),
                                         ("codex", deleted)):
                with self.subTest(provider=provider, executable=executable), \
                        self.assertRaisesRegex(sources.SourceError, "Account unknown"):
                    sources.scope_for(provider, {"argv": [provider], "executable": executable}, {"HOME": str(home)})

    def test_agent_started_in_place_of_the_pane_shell_is_read(self):
        with tempfile.TemporaryDirectory() as temporary:
            exe = Path(temporary) / "claude"
            exe.touch(); exe.chmod(0o700)
            for processes, expected in (([{"pid": 2, "argv": ["claude"]}], 2),
                                        ([{"pid": 2, "argv": ["claude"]}, {"pid": 3, "argv": ["claude", "-p"]}], 3),
                                        ([{"pid": 2, "argv": ["-zsh"]}], None)):
                info = {"process_info": {"shell_pid": 2, "foreground_processes": [dict(p, cwd=temporary) for p in processes]}}
                read = []
                with self.subTest(processes=processes), patch.object(field, "control", return_value=info), \
                        patch.object(runtime, "process_start", return_value="started"), \
                        patch.object(runtime, "process_environment", return_value={"HOME": temporary}), \
                        patch.object(runtime, "process_executable", side_effect=lambda pid: read.append(pid) or str(exe)):
                    if expected is None:
                        with self.assertRaisesRegex(sources.SourceError, "Account unknown"):
                            usage.verified_scope("herdr", "claude", {"pane_id": "p"})
                    else:
                        self.assertEqual(usage.verified_scope("herdr", "claude", {"pane_id": "p"})["provider"], "claude")
                        self.assertEqual(set(read), {expected})

    def test_tls_context_falls_back_when_python_has_no_certificates(self):
        with tempfile.TemporaryDirectory() as temporary:
            bundle = Path(temporary) / "cert.pem"
            bundle.write_text("system roots")
            missing = str(Path(temporary) / "missing.pem")
            certifi = Mock(where=Mock(return_value="certifi.pem"))
            def created(cafile=None):
                return Mock(cafile=cafile, cert_store_stats=Mock(return_value={"x509_ca": 0 if cafile is None else 1}))
            clean = {k: v for k, v in os.environ.items() if k not in ("SSL_CERT_FILE", "SSL_CERT_DIR")}
            for system, env, module, expected in (((missing, str(bundle)), {}, certifi, str(bundle)),
                                                  ((missing,), {}, certifi, "certifi.pem"),
                                                  ((missing,), {}, None, None),
                                                  ((str(bundle),), {"SSL_CERT_FILE": "chosen.pem"}, None, None)):
                with self.subTest(system=system, env=env, certifi=bool(module)), \
                        patch.object(runtime.ssl, "create_default_context", side_effect=created), \
                        patch.object(runtime, "SYSTEM_CA_FILES", system), \
                        patch.dict(os.environ, dict(clean, **env), clear=True), \
                        patch.dict(sys.modules, {"certifi": module}):
                    self.assertEqual(runtime.tls_context().cafile, expected)
            with patch.object(runtime.ssl, "create_default_context",
                              return_value=Mock(cafile=None, cert_store_stats=Mock(return_value={"x509_ca": 3}))):
                self.assertIsNone(runtime.tls_context().cafile)

    def test_codexbar_download_and_fugu_read_verify_with_the_shared_context(self):
        import ssl
        import urllib.error
        context = ssl.create_default_context()
        with tempfile.TemporaryDirectory() as temporary, patch.object(runtime, "tls_context", return_value=context):
            with patch.dict(os.environ, {"HERDR_PLUGIN_STATE_DIR": temporary}), \
                    patch.object(sources.platform, "system", return_value="Darwin"), \
                    patch.object(sources.platform, "machine", return_value="arm64"), \
                    patch.object(sources.urllib.request, "urlopen", return_value=io.BytesIO(b"bad")) as opened:
                with self.assertRaisesRegex(RuntimeError, "digest mismatch"):
                    sources.install(Path(temporary) / "herd")
            self.assertIs(opened.call_args.kwargs["context"], context)
            opener = Mock()
            opener.open.side_effect = urllib.error.URLError("offline")
            with patch.object(sources.urllib.request, "HTTPSHandler") as handler, \
                    patch.object(sources.urllib.request, "build_opener", return_value=opener):
                with self.assertRaisesRegex(sources.SourceError, "Last update failed"):
                    sources.fugu_read({"api_key": "private-key"}, time.monotonic() + 5, threading.Event())
            handler.assert_called_once_with(context=context)

    def test_watchers_record_the_code_they_run(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            views.record_watcher(state, "usage")
            self.assertEqual(field.read_json(state / "usage-watcher.json", {}),
                             {"pid": os.getpid(), "code": views.code_stamp(ROOT)})
            with patch.object(views, "record_watcher") as recorded, patch.object(usage, "run"), \
                    patch.object(sys, "argv", ["plan-usage", "--state-dir", temporary, "watch"]):
                self.assertEqual(usage.main(), 0)
            recorded.assert_called_once_with(state, "usage")
            screen = MagicMock()
            screen.__enter__.side_effect = KeyboardInterrupt
            with patch.object(views, "record_watcher") as recorded, patch.object(field, "Readout", return_value=screen), \
                    patch.object(field, "resolve_herdr", return_value="herdr"), \
                    patch.object(sys, "argv", ["field-status", "--state-dir", temporary, "watch"]):
                self.assertEqual(field.main(), 0)
            recorded.assert_called_once_with(state, "field")
            code = state / "bin" / "plan_usage.py"
            code.parent.mkdir()
            code.write_text("old")
            before = views.code_stamp(state)
            code.write_text("new")
            self.assertNotEqual(views.code_stamp(state), before)

    def test_claude_ui_counters_do_not_change_auth_binding(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / ".claude.json"
            scope = {"provider": "claude", "files": [str(path)]}
            path.write_text(json.dumps({"oauthAccount": {"accountUuid": "one"}, "numStartups": 1}))
            before = sources.current_stamp(scope)
            path.write_text(json.dumps({"oauthAccount": {"accountUuid": "one"}, "numStartups": 2}))
            self.assertEqual(sources.current_stamp(scope), before)
            path.write_text(json.dumps({"oauthAccount": {"accountUuid": "two"}, "numStartups": 2}))
            self.assertNotEqual(sources.current_stamp(scope), before)

    @unittest.skipIf(os.name == "nt", "requires Unix PTY process groups")
    def test_stop_reaps_helper_in_separate_process_group(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "pid"
            code = "import os,signal,subprocess,sys,time; child=subprocess.Popen([sys.executable,'-c','import os,signal,time;os.setsid();signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(30)']);open(sys.argv[1],'w').write(str(child.pid));time.sleep(30)"
            proc = runtime.spawn([sys.executable, "-c", code, str(path)], os.environ.copy())
            try:
                deadline = time.monotonic() + 5
                while not path.exists() and time.monotonic() < deadline:
                    time.sleep(.05)
                self.assertTrue(path.exists())
                pid = int(path.read_text())
                time.sleep(.5)
                runtime.stop_process(proc)
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    state = subprocess.run(["/bin/ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True).stdout.strip()
                    if not state or state.startswith("Z"):
                        break
                    time.sleep(.05)
                self.assertTrue(not state or state.startswith("Z"), state)
                self.assertIsNotNone(proc.returncode)
            finally:
                runtime.stop_process(proc)
                proc.stdin.close()
                proc.stdout.close()

    @unittest.skipIf(os.name == "nt", "requires Unix provider lease")
    def test_provider_lease_stops_native_child_after_parent_is_killed(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "pid"
            code = "import os,sys,time;sys.path.insert(0,sys.argv[1]);import usage_runtime as r;child=r.spawn([sys.executable,'-c',\"import os,sys,time;open(sys.argv[1],'w').write(str(os.getpid()));time.sleep(30)\",sys.argv[2]],os.environ.copy());time.sleep(30)"
            parent = subprocess.Popen([sys.executable, "-c", code, str(ROOT / "bin"), str(path)])
            try:
                deadline = time.monotonic() + 5
                while not path.exists() and time.monotonic() < deadline:
                    time.sleep(.05)
                self.assertTrue(path.exists())
                pid = int(path.read_text())
                parent.kill()
                parent.wait(timeout=5)
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    state = subprocess.run(["/bin/ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True).stdout.strip()
                    if not state or state.startswith("Z"):
                        break
                    time.sleep(.05)
                self.assertTrue(not state or state.startswith("Z"), state)
            finally:
                if parent.poll() is None:
                    parent.kill()
                parent.wait()

    @unittest.skipIf(os.name == "nt", "requires Unix provider lease")
    def test_provider_lease_duration_expires_while_parent_stays_alive(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "pid"
            code = "import os,sys,time;open(sys.argv[1],'w').write(str(os.getpid()));time.sleep(30)"
            started = time.monotonic()
            lease = subprocess.Popen([sys.executable, str(ROOT / "bin/usage_child.py"), str(os.getpid()),
                                      runtime.process_start(os.getpid()), ".3", sys.executable, "-c", code, str(path)],
                                     start_new_session=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                lease.wait(timeout=5)
                self.assertEqual(lease.returncode, 1)
                self.assertLess(time.monotonic() - started, 4)
                self.assertTrue(path.exists())
                state = subprocess.run(["/bin/ps", "-p", path.read_text(), "-o", "stat="], capture_output=True, text=True).stdout.strip()
                self.assertTrue(not state or state.startswith("Z"), state)
            finally:
                if lease.poll() is None:
                    runtime.stop_process(lease)
                lease.wait()
                lease.stdout.close()
                lease.stderr.close()

    @unittest.skipIf(os.name == "nt", "requires Unix inherited flock")
    def test_provider_lease_keeps_lock_until_cleanup_after_owner_death(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pid_file, lock_file = root / "pid", root / "lock"
            native = "import os,signal,sys,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);open(sys.argv[1],'w').write(str(os.getpid()));time.sleep(30)"
            code = "import os,sys,time;from pathlib import Path;sys.path.insert(0,sys.argv[1]);import usage_runtime as r;lock=r.FileLock(Path(sys.argv[2]));assert lock.acquire();env=dict(os.environ,LANTERN_USAGE_LOCK_FD=str(lock.fd));child=r.spawn([sys.executable,'-c',sys.argv[4],sys.argv[3]],env);time.sleep(30)"
            parent = subprocess.Popen([sys.executable, "-c", code, str(ROOT / "bin"), str(lock_file), str(pid_file), native])
            contender = None
            try:
                deadline = time.monotonic() + 5
                while not pid_file.exists() and time.monotonic() < deadline:
                    time.sleep(.05)
                self.assertTrue(pid_file.exists())
                contender = runtime.FileLock(lock_file)
                self.assertFalse(contender.acquire())
                parent.kill()
                parent.wait(timeout=5)
                self.assertFalse(contender.acquire(), "lease must hold ownership during cleanup")
                deadline = time.monotonic() + 6
                while not contender.acquire() and time.monotonic() < deadline:
                    time.sleep(.05)
                self.assertTrue(contender.held)
                state = subprocess.run(["/bin/ps", "-p", pid_file.read_text(), "-o", "stat="], capture_output=True, text=True).stdout.strip()
                self.assertTrue(not state or state.startswith("Z"), state)
            finally:
                if parent.poll() is None:
                    parent.kill()
                parent.wait()
                if contender:
                    contender.close()

    def test_installer_rejects_bad_digest_and_archive_escape(self):
        import hashlib
        import tarfile
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / "herd"
            env = {"HERDR_PLUGIN_STATE_DIR": temporary}
            with patch.dict(os.environ, env), patch.object(sources.platform, "system", return_value="Darwin"), \
                    patch.object(sources.platform, "machine", return_value="arm64"), \
                    patch.object(sources.urllib.request, "urlopen", return_value=io.BytesIO(b"bad")):
                with self.assertRaisesRegex(RuntimeError, "digest mismatch"):
                    sources.install(state)
            output = io.BytesIO()
            with tarfile.open(fileobj=output, mode="w:gz") as archive:
                entry = tarfile.TarInfo("../escape")
                entry.size = 1
                archive.addfile(entry, io.BytesIO(b"x"))
            data = output.getvalue()
            with patch.dict(os.environ, env), patch.object(sources.platform, "system", return_value="Darwin"), \
                    patch.object(sources.platform, "machine", return_value="arm64"), \
                    patch.dict(sources.ASSETS, {"macos-arm64": hashlib.sha256(data).hexdigest()}), \
                    patch.object(sources.urllib.request, "urlopen", return_value=io.BytesIO(data)):
                with self.assertRaisesRegex(RuntimeError, "archive path"):
                    sources.install(state)
            self.assertFalse((Path(temporary) / "tools/codexbar/escape").exists())

    def test_installer_stops_when_the_watcher_stops(self):
        with tempfile.TemporaryDirectory() as temporary:
            stop = threading.Event()
            stop.set()
            with patch.dict(os.environ, {"HERDR_PLUGIN_STATE_DIR": temporary}), \
                    patch.object(sources.platform, "system", return_value="Darwin"), \
                    patch.object(sources.platform, "machine", return_value="arm64"), \
                    patch.object(sources.urllib.request, "urlopen", return_value=io.BytesIO(b"x" * 10)):
                with self.assertRaisesRegex(RuntimeError, "install was stopped"):
                    sources.install(Path(temporary) / "herd", stop)
            self.assertFalse((Path(temporary) / "tools/codexbar/0.74.0").exists())

    def test_first_failed_scan_retains_same_lifetime_membership_without_quota(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            server = {"key": "server", "lifetime": "one"}
            original = usage.Collector(root / "state", root / "shared", "herdr", server)
            original.validated = True
            original.bindings["a"] = {"scope": {"provider": "codex"}, "tabs": {"1", "2"}}
            original.cache["a"] = {"state": "Measured", "result": measured()}
            original.write()
            original.close()
            follower = usage.Collector(root / "state", root / "shared", "herdr", server)
            try:
                with patch.object(runtime, "server_identity", return_value=server), patch.object(field, "snapshot", side_effect=RuntimeError()):
                    follower.scan()
                rows = follower.write()["rows"]
                self.assertEqual(rows, [{"provider": "codex", "tabs": 2, "state": "Account unknown"}])
                self.assertNotIn("windows", rows[0])
                with patch.object(runtime, "server_identity", return_value=dict(server, lifetime="two")), patch.object(field, "snapshot", side_effect=RuntimeError()):
                    follower.scan()
                self.assertEqual(follower.write()["rows"], [])
            finally:
                follower.close()

    def test_field_tab_cli_has_full_setup_deadline(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(sys, "argv", ["field-status", "--state-dir", temporary, "pane"]), \
                patch.object(field, "open_pane", return_value=("pane", False)) as opened:
            self.assertEqual(field.main(), 0)
            self.assertEqual(opened.call_args.args[-1], 15)


class ViewTests(unittest.TestCase):
    maxDiff = None
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.state = self.root / "plugin-state/herd"
        self.fixture = self.root / "fixture.json"
        (self.root / "socket").touch()
        self.fixture.write_text(json.dumps({"workspace": True,
            "tabs": {"w9:t1": {"tab_id": "w9:t1", "workspace_id": "w9", "label": "home", "pane_count": 1}},
            "panes": {"w9:p1": {"pane_id": "w9:p1", "tab_id": "w9:t1", "workspace_id": "w9", "label": "Lantern", "agent": "codex"}}}))
        self.fake = self.root / "herdr"
        self.fake.write_bytes(b'#!/bin/sh\nexec "$VIEWS_PYTHON" "$VIEWS_BACKEND" "$@"\n')
        self.fake.chmod(0o700)
        self.env = {"HERDR_ENV": "1", "HERDR_REAL": str(self.fake), "VIEWS_PYTHON": sys.executable,
                    "VIEWS_BACKEND": str(ROOT / "tests/fixtures/views_herdr.py"), "VIEWS_FIXTURE": str(self.fixture),
                    "VIEWS_ROOT": str(ROOT), "VIEWS_STATE": str(self.state), "HERDR_WORKSPACE_ID": "wrong",
                    "HERDR_PANE_ID": "wrong:p1"}
        self.environment = patch.dict(os.environ, self.env)
        self.environment.start()

    def tearDown(self):
        self.environment.stop()
        self.temp.cleanup()

    def setup_views(self, view="both"):
        return views.setup(self.state, ROOT, str(ROOT / "bin/herdr"), "w9", "w9:p1", str(self.root),
                           view=view, timeout=45 if os.name == "nt" else 15)

    def data(self):
        return json.loads(self.fixture.read_text())

    def save(self, data):
        self.fixture.write_text(json.dumps(data))

    def assert_layout(self):
        data = self.data()
        record = field.read_json(self.state / "views.json", {})
        usage = data["rects"][record["views"]["usage"]["pane_id"]]
        activity = data["rects"][record["views"]["field"]["pane_id"]]
        chat = data["rects"]["w9:p1"]
        self.assertEqual(usage["x"], activity["x"])
        self.assertEqual(usage["x"], chat["x"] + chat["width"])
        self.assertLess(activity["y"], usage["y"])
        self.assertGreater(activity["height"], usage["height"])
        self.assertAlmostEqual(activity["height"] / chat["height"], 2 / 3, delta=1 / chat["height"])
        self.assertEqual(chat["height"], usage["height"] + activity["height"])
        self.assertTrue(all(entry["tab_id"] == "w9:t1" for entry in record["views"].values()))
        self.assertEqual(data.get("focused_pane", "w9:p1"), "w9:p1")
        self.assertFalse(any(c[:2] == ["pane", "swap"] for c in data["calls"]))
        self.assertTrue(all("--no-focus" in c for c in data["calls"] if c[:2] == ["pane", "move"]))

    def test_closed_readout_restores_in_right_column(self):
        for kind in ("usage", "field"):
            self.assertEqual(self.setup_views()["errors"], [])
            entry = field.read_json(self.state / "views.json", {})["views"][kind]
            with patch.dict(os.environ, {"HERDR_HELPER_OK": "1"}):
                field.control(str(ROOT / "bin/herdr"), ["pane", "close", entry["pane_id"]], 2)
            self.assertEqual(self.setup_views()["errors"], [])
            self.assert_layout()

    def test_unowned_home_shell_prevents_duplicate_split_after_lost_response(self):
        original = field.control
        def uncertain(binary, args, timeout):
            result = original(binary, args, timeout)
            if args[:2] == ["pane", "split"]:
                raise RuntimeError("response lost")
            return result
        with patch.object(field, "control", side_effect=uncertain):
            self.assertTrue(self.setup_views("usage")["errors"])
        self.assertIn("unowned", self.setup_views()["errors"][0])
        self.assertEqual(sum(c[:2] == ["pane", "split"] for c in self.data()["calls"]), 1)
        self.assertFalse(any(c[:2] == ["pane", "run"] for c in self.data()["calls"]))

    def test_existing_pane_sizes_stay_unchanged_on_reopen(self):
        self.assertEqual(self.setup_views()["errors"], [])
        data = self.data()
        entries = field.read_json(self.state / "views.json", {})["views"]
        usage = data["rects"][entries["usage"]["pane_id"]]
        activity = data["rects"][entries["field"]["pane_id"]]
        activity["height"], usage["y"], usage["height"] = 18, 18, 22
        self.save(data)
        self.assertEqual(self.setup_views()["errors"], [])
        self.assertEqual(self.data()["rects"], data["rects"])

    def test_old_usage_above_field_layout_moves_without_restart(self):
        self.assertEqual(self.setup_views()["errors"], [])
        data = self.data()
        entries = field.read_json(self.state / "views.json", {})["views"]
        usage = data["rects"][entries["usage"]["pane_id"]]
        activity = data["rects"][entries["field"]["pane_id"]]
        usage.update(y=0, height=24)
        activity.update(y=24, height=16)
        self.save(data)
        processes = data["processes"]
        runs = sum(c[:2] == ["pane", "run"] for c in data["calls"])
        splits = sum(c[:2] == ["pane", "split"] for c in data["calls"])
        self.assertEqual(self.setup_views()["errors"], [])
        self.assert_layout()
        after = self.data()
        self.assertEqual(after["processes"], processes)
        self.assertEqual(sum(c[:2] == ["pane", "run"] for c in after["calls"]), runs)
        self.assertEqual(sum(c[:2] == ["pane", "split"] for c in after["calls"]), splits)
        self.assertEqual(sum(c[:2] == ["pane", "move"] for c in after["calls"]), 2)

    def test_new_and_saved_shells_wait_before_layout_repair_and_start(self):
        data = self.data()
        data["starting_reads"] = 2
        self.save(data)
        self.assertEqual(self.setup_views("usage")["errors"], [])
        self.assertEqual(self.setup_views()["errors"], [])
        self.assert_layout()
        data = self.data()
        pane = field.read_json(self.state / "views.json", {})["views"]["field"]["pane_id"]
        data["processes"][pane] = []
        data["starting"][pane] = 2
        self.save(data)
        self.assertEqual(self.setup_views("field")["errors"], [])
        self.assertEqual(self.data()["starting"][pane], 0)

    def test_explicit_long_setup_budget_allows_slow_control_process(self):
        data = self.data()
        data["delay_once"] = {"command": ["pane", "process-info"], "seconds": 2.1}
        self.save(data)
        result = views.setup(self.state, ROOT, str(ROOT / "bin/herdr"), "w9", "w9:p1",
                             str(self.root), timeout=45)
        self.assertEqual(result["errors"], [])
        self.assert_layout()

    def test_lost_layout_move_response_recovers_without_new_panes(self):
        for lost_stage in (True, False):
            self.assertEqual(self.setup_views("usage")["errors"], [])
            entry = field.read_json(self.state / "views.json", {})["views"]["usage"]
            data = self.data()
            data["processes"][entry["pane_id"]] = []
            self.save(data)
            original = field.control
            lost = []
            def uncertain(binary, args, timeout):
                result = original(binary, args, timeout)
                if args[:2] == ["pane", "move"] and ("--new-tab" in args) == lost_stage and not lost:
                    lost.append(True)
                    raise RuntimeError("response lost")
                return result
            # Recreate Field Status so repair must move usage through a temporary tab.
            old_field = field.read_json(self.state / "views.json", {}).get("views", {}).get("field")
            if old_field:
                with patch.dict(os.environ, {"HERDR_HELPER_OK": "1"}):
                    field.control(str(ROOT / "bin/herdr"), ["pane", "close", old_field["pane_id"]], 2)
            with patch.object(field, "control", side_effect=uncertain):
                self.assertTrue(self.setup_views()["errors"])
            self.assertTrue(lost)
            self.assertEqual(self.setup_views()["errors"], [])
            self.assert_layout()
            self.assertEqual(len(self.data()["panes"]), 3)
            self.assertEqual(len(self.data()["tabs"]), 1)

    def test_usage_first_then_field_uses_upper_right(self):
        self.assertEqual(self.setup_views("usage")["errors"], [])
        self.assertEqual(self.setup_views()["errors"], [])
        self.assert_layout()
        self.assertEqual(sum(c[:2] == ["pane", "split"] for c in self.data()["calls"]), 2)

    def test_exact_watchers_in_old_tabs_move_without_restart(self):
        data = self.data()
        for number, kind in enumerate(("field", "usage"), 2):
            tab, pane = f"w9:t{number}", f"w9:p{number}"
            data["tabs"][tab] = {"tab_id": tab, "workspace_id": "w9", "label": views.VIEWS[kind][0], "pane_count": 1}
            data["panes"][pane] = {"pane_id": pane, "tab_id": tab, "workspace_id": "w9"}
            data.setdefault("processes", {})[pane] = [{"pid": 200 + number, "argv": [sys.executable,
                str(ROOT / "bin" / views.VIEWS[kind][1]), "--state-dir", str(self.state), "watch"]}]
            field.write_json(runtime.private_dir(self.state) / f"{kind}-watcher.json",
                             {"pid": 200 + number, "code": views.code_stamp(ROOT)})
        self.save(data)
        self.assertEqual(self.setup_views()["errors"], [])
        self.assert_layout()
        data = self.data()
        self.assertEqual(len(data["tabs"]), 1)
        self.assertEqual(sum(c[:2] == ["pane", "move"] for c in data["calls"]), 2)
        self.assertFalse(any(c[:2] in (["pane", "run"], ["pane", "close"], ["pane", "split"]) for c in data["calls"]))

    def test_outdated_watcher_is_replaced_in_its_pane_after_update(self):
        self.assertEqual(self.setup_views()["errors"], [])
        entry = field.read_json(self.state / "views.json", {})["views"]["usage"]
        record = self.state / "usage-watcher.json"
        for older in ({"pid": 200, "code": "older release"}, None):
            with self.subTest(record=older):
                if older:
                    field.write_json(record, older)
                else:
                    record.unlink()  # Releases before watcher records wrote none.
                start = len(self.data()["calls"])
                self.assertEqual(self.setup_views("usage")["errors"], [])
                calls = self.data()["calls"][start:]
                runs = [c for c in calls if c[:2] == ["pane", "run"]]
                self.assertEqual(runs, [["pane", "run", entry["pane_id"], entry["command"]]])
                self.assertLess(calls.index(["pane", "send-keys", entry["pane_id"], "ctrl+c"]), calls.index(runs[0]))
                self.assertFalse(any(c[:2] in (["pane", "split"], ["pane", "close"]) for c in calls))
                self.assertEqual(field.read_json(record, {})["code"], views.code_stamp(ROOT))
        start = len(self.data()["calls"])
        self.assertEqual(self.setup_views()["errors"], [])
        self.assertFalse(any(c[:2] in (["pane", "send-keys"], ["pane", "run"]) for c in self.data()["calls"][start:]))
        self.assert_layout()

    def test_outdated_watcher_that_keeps_running_gets_no_input(self):
        self.assertEqual(self.setup_views()["errors"], [])
        pane = field.read_json(self.state / "views.json", {})["views"]["usage"]["pane_id"]
        field.write_json(self.state / "usage-watcher.json", {"pid": 200, "code": "older release"})
        data = self.data()
        data["ignores_interrupt"] = [pane]
        self.save(data)
        runs = sum(c[:2] == ["pane", "run"] for c in data["calls"])
        self.assertIn("did not stop", self.setup_views("usage")["errors"][0])
        self.assertEqual(sum(c[:2] == ["pane", "run"] for c in self.data()["calls"]), runs)

    def test_command_that_replaces_outdated_watcher_gets_no_interrupt(self):
        self.assertEqual(self.setup_views()["errors"], [])
        pane = field.read_json(self.state / "views.json", {})["views"]["usage"]["pane_id"]
        field.write_json(self.state / "usage-watcher.json", {"pid": 200, "code": "older release"})
        runs = sum(c[:2] == ["pane", "run"] for c in self.data()["calls"])
        original = field.control
        def takeover(binary, args, timeout):
            result = original(binary, args, timeout)
            if args == ["pane", "get", pane]:
                # The watcher exits and another command starts during the identity checks.
                data = self.data()
                data["processes"][pane] = [{"pid": 999, "argv": ["sleep", "300"]}]
                self.save(data)
            return result
        with patch.object(field, "control", side_effect=takeover):
            self.assertIn("identity changed", self.setup_views("usage")["errors"][0])
        calls = self.data()["calls"]
        self.assertFalse(any(c[:2] == ["pane", "send-keys"] for c in calls))
        self.assertEqual(sum(c[:2] == ["pane", "run"] for c in calls), runs)

    def test_outdated_watcher_with_a_short_lived_child_is_still_replaced(self):
        self.assertEqual(self.setup_views()["errors"], [])
        entry = field.read_json(self.state / "views.json", {})["views"]["usage"]
        field.write_json(self.state / "usage-watcher.json", {"pid": 200, "code": "older release"})
        original = field.control
        added = []
        def scan_child(binary, args, timeout):
            result = original(binary, args, timeout)
            if args == ["pane", "get", entry["pane_id"]] and not added:
                # The watcher's own Herdr scan appears beside it in the foreground.
                added.append(True)
                data = self.data()
                data["processes"][entry["pane_id"]].append(
                    {"pid": 999, "argv": ["herdr", "pane", "process-info", "--pane", "other"]})
                self.save(data)
            return result
        start = len(self.data()["calls"])
        with patch.object(field, "control", side_effect=scan_child):
            self.assertEqual(self.setup_views("usage")["errors"], [])
        calls = self.data()["calls"][start:]
        self.assertIn(["pane", "send-keys", entry["pane_id"], "ctrl+c"], calls)
        self.assertEqual([c for c in calls if c[:2] == ["pane", "run"]],
                         [["pane", "run", entry["pane_id"], entry["command"]]])

    def test_setup_splits_home_once_and_real_wrapper_stays_gated(self):
        result = self.setup_views()
        self.assertEqual(result["errors"], [])
        self.assertEqual(self.setup_views()["errors"], [])
        calls = self.data()["calls"]
        self.assertEqual(sum(c[:2] == ["pane", "split"] for c in calls), 2)
        self.assertEqual(len(self.data()["tabs"]), 1)
        self.assert_layout()
        self.assertEqual(sum(c[:2] == ["pane", "run"] for c in calls), 2)
        self.assertTrue(all("--no-focus" in c for c in calls if c[:2] == ["pane", "split"]))
        self.assertFalse(any(c[:2] in (["tab", "create"], ["pane", "close"]) for c in calls))
        env = dict(os.environ, HERDR_HELPER_OK="")
        denied = subprocess.run(field.herdr_command(str(ROOT / "bin/herdr"), ["tab", "create"]), env=env, capture_output=True, text=True)
        self.assertEqual(denied.returncode, 2)

    def test_owned_stopped_view_restarts_but_occupied_view_gets_no_input(self):
        self.setup_views()
        data = self.data()
        pid = field.read_json(self.state / "views.json", {})["views"]["field"]["pane_id"]
        data["processes"][pid] = []
        self.save(data)
        self.assertEqual(self.setup_views("field")["errors"], [])
        data = self.data()
        data["processes"][pid] = [{"pid": 3, "argv": ["editor"]}]
        runs = sum(c[:2] == ["pane", "run"] for c in data["calls"])
        self.save(data)
        self.assertIn("occupied", self.setup_views("field")["errors"][0])
        self.assertEqual(sum(c[:2] == ["pane", "run"] for c in self.data()["calls"]), runs)

    def test_unowned_label_and_old_split_do_not_receive_input(self):
        data = self.data()
        data["tabs"]["w9:t2"] = {"tab_id": "w9:t2", "workspace_id": "w9", "label": "Field Status", "pane_count": 1}
        data["panes"]["w9:p2"] = {"tab_id": "w9:t2", "workspace_id": "w9", "pane_id": "w9:p2"}
        self.save(data)
        field.write_json(self.state / "field-status-pane.json", {"home_pane_id": "w9:p1", "pane_id": "w9:p2"})
        self.assertIn("conflict", self.setup_views("field")["errors"][0])
        self.assertFalse(any(c[:2] == ["pane", "run"] for c in self.data()["calls"]))

    def test_running_exact_watcher_can_recover_ownership(self):
        self.setup_views()
        (self.state / "views.json").unlink()
        self.assertEqual(self.setup_views()["errors"], [])
        self.assertEqual(sum(c[:2] == ["pane", "split"] for c in self.data()["calls"]), 2)
        self.assert_layout()

    def test_uncertain_submission_rechecks_running_process_before_retry(self):
        original = field.control
        def uncertain(binary, args, timeout):
            value = original(binary, args, timeout)
            if args[:2] == ["pane", "run"]:
                raise RuntimeError("response lost")
            return value
        with patch.object(field, "control", side_effect=uncertain):
            self.assertTrue(self.setup_views("field")["errors"])
        self.assertEqual(self.setup_views("field")["errors"], [])
        self.assertEqual(sum(c[:2] == ["pane", "run"] for c in self.data()["calls"]), 1)

    def test_setup_lock_and_invalid_home_prevent_mutations(self):
        lock = runtime.FileLock(self.state / "view-setup.lock")
        self.assertTrue(lock.acquire())
        try:
            result = views.setup(self.state, ROOT, str(ROOT / "bin/herdr"), "w9", "w9:p1", str(self.root), timeout=1.5)
            self.assertTrue(result["busy"])
        finally:
            lock.close()
        with self.assertRaisesRegex(RuntimeError, "identity changed"):
            views.setup(self.state, ROOT, str(ROOT / "bin/herdr"), "wrong", "w9:p1", str(self.root))
        self.assertFalse(any(c[:2] == ["tab", "create"] for c in self.data()["calls"]))

    def test_busy_setup_reads_completed_owner_record(self):
        self.assertEqual(self.setup_views()["errors"], [])
        lock = runtime.FileLock(self.state / "view-setup.lock")
        self.assertTrue(lock.acquire())
        try:
            result = views.setup(self.state, ROOT, str(ROOT / "bin/herdr"), "w9", "w9:p1", str(self.root), timeout=1.5)
            self.assertEqual(result["errors"], [])
            self.assertTrue(result["setup_complete"])
        finally:
            lock.close()

    def test_windows_commands_quote_native_paths(self):
        command = views.watcher_command(Path("C:/Program Files/O'Brien"), Path("C:/Users/O'Brien/state"), "usage", "powershell")
        self.assertTrue(command.startswith("& '"))
        self.assertIn("O''Brien", command)
        self.assertIn("bin\\plan_usage.py", command)
        self.assertNotIn("sh ", command)

    def test_open_fresh_and_both_reuse_paths_restore_readouts(self):
        sh = shutil.which("sh")
        env = dict(os.environ, HERDR_PLUGIN_ROOT=str(ROOT), HERDR_PLUGIN_STATE_DIR=str(self.state.parent), HERDR_PLUGIN_CONFIG_DIR="")
        data = self.data()
        data.update(workspace=False, tabs={}, panes={})
        self.save(data)
        for mode in ("fresh", "remembered", "lost-record"):
            if mode != "fresh":
                data = self.data()
                data["tabs"] = {k: v for k, v in data["tabs"].items() if k == "w9:t1"}
                data["panes"] = {k: v for k, v in data["panes"].items() if k == "w9:p1"}
                data["processes"] = {}
                data["rects"] = {}
                data["tabs"]["w9:t1"]["pane_count"] = 1
                self.save(data)
            if mode == "lost-record":
                (self.state.parent / "pane.id").unlink()
            completed = subprocess.run([sh, str(ROOT / "open.sh")], env=env, capture_output=True, text=True, timeout=20)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            record = field.read_json(self.state / "views.json", {})
            self.assertEqual(record.get("errors"), [], (mode, completed.stderr))
            self.assertEqual(set(record["views"]), {"field", "usage"})
            self.assertEqual(len(self.data()["tabs"]), 1)
            self.assert_layout()

    def test_direct_launch_opens_readouts_before_chat(self):
        fake_bin = self.root / "bin"
        fake_bin.mkdir()
        agent = fake_bin / "codex"
        agent.write_bytes(b'#!/bin/sh\nprintf "fixture chat started\\n"\n')
        agent.chmod(0o700)
        conf = self.root / "config"
        conf.mkdir()
        # Shell fixtures need LF endings on Windows as well as Unix.
        (conf / "helper.conf").write_bytes(b'HELPER_AGENT=codex\nHELPER_MODEL=\nHELPER_PERMISSION=smart\n')
        env = dict(os.environ, HERDR_PLUGIN_ROOT=str(ROOT), HERDR_PLUGIN_STATE_DIR=str(self.state.parent),
                   HERDR_PLUGIN_CONFIG_DIR=str(conf), HERDR_WORKSPACE_ID="w9", HERDR_PANE_ID="w9:p1",
                   HERDR_BIN_PATH=str(self.fake),
                   PATH=os.pathsep.join([str(fake_bin), os.environ["PATH"]]))
        completed = subprocess.run([shutil.which("sh"), str(ROOT / "launch.sh")], env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("fixture chat started", completed.stdout)
        self.assertEqual(field.read_json(self.state / "views.json", {}).get("errors"), [], completed.stderr)
        self.assertEqual(len(self.data()["tabs"]), 1)
        self.assert_layout()


if __name__ == "__main__":
    unittest.main()
