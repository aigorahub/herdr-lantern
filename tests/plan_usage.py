"""Plan Usage protocol, identity, collection, locks, and startup tests."""
from __future__ import annotations

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
from unittest.mock import patch

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

    def test_standalone_modern_and_legacy_profiles_use_the_same_settings(self):
        modern = self.root / "fugu.config.toml"
        modern.write_text('model_provider = "sakana"\n' + self.provider)
        (self.root / "config.toml").unlink()
        self.assertEqual(sources.configured_fugu_scope(self.env)["provider"], "fugu")
        modern.unlink()
        (self.root / "config.toml").write_text(self.provider + '\n[profiles.fugu]\nmodel_provider = "sakana"\n')
        self.assertEqual(sources.configured_fugu_scope(self.env)["provider"], "fugu")

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

    def test_key_rotation_changes_binding_and_missing_key_hides_old_values(self):
        env = {"HOME": str(self.home)}
        store = self.store('SAKANA_API_KEY=first-key\n')
        first = sources.configured_fugu_scope(env)
        store.write_text('SAKANA_API_KEY=second-key\n')
        second = sources.configured_fugu_scope(env)
        self.assertNotEqual(first["key"], second["key"])
        self.assertNotEqual(first["stamp"], sources.current_stamp(first))
        store.write_text('OTHER_KEY=hidden\n')
        with self.assertRaisesRegex(sources.SourceError, "Sign in required"):
            sources.configured_fugu_scope(env)

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

    def test_configured_plan_rejects_other_endpoints_and_malformed_profiles(self):
        for provider in (self.provider.replace("api.sakana.ai", "other.invalid"),
                         self.provider.replace('"https://api.sakana.ai/v1"', '123'),
                         self.provider.replace('"SAKANA_API_KEY"', '"INVALID-KEY"'), "[broken"):
            with self.subTest(provider=provider):
                (self.root / "config.toml").write_text(provider)
                with self.assertRaises(sources.SourceError) as failure:
                    sources.configured_fugu_scope(self.env)
                self.assertEqual(failure.exception.provider, "fugu")


class UsageTests(unittest.TestCase):
    def setUp(self):
        # Collector fixtures do not use the machine's installed Fugu account.
        configured = patch.object(sources, "configured_fugu_scope", return_value=None)
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
            with self.assertRaisesRegex(sources.SourceError, "Sign in required") as missing:
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
                self.assertEqual(rows, {"agy": "Account unknown", "claude": "Dependency unavailable", "grok": "Dependency unavailable"})
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

    def test_partial_unreadable_tabs_do_not_warn_when_plan_is_measured(self):
        row = dict(measured(), state="Measured")
        for state in ("Account unknown", "Limits unavailable", "Dependency unavailable", "Reading usage"):
            with self.subTest(state=state):
                notice = {"provider": "codex", "state": state}
                plain = usage.render({"rows": [row]}, now=1800000030, width=61)
                self.assertEqual(usage.render({"rows": [row, notice]}, now=1800000030, width=61), plain)
                self.assertIn(state, usage.render({"rows": [notice]}, now=1800000030, width=61))
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
        import pty
        import select
        master, slave = pty.openpty()
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

    def test_server_status_timeout_has_safe_error(self):
        failure = subprocess.TimeoutExpired(["herdr", "private-command-text"], 2,
                                             output="private-output", stderr="private-stderr")
        with patch.object(runtime.subprocess, "run", side_effect=failure):
            with self.assertRaisesRegex(RuntimeError, "^Herdr server status timed out$") as caught:
                runtime.server_identity("herdr", 2)
        self.assertTrue(caught.exception.__suppress_context__)

    def test_watch_recovers_after_initial_server_status_timeout(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            endpoint = root / "socket"
            endpoint.touch()
            response = subprocess.CompletedProcess([], 0, json.dumps({
                "server": {"running": True, "socket": str(endpoint)}}), "")
            failure = subprocess.TimeoutExpired(["herdr", "private-command-text"], 2,
                                                 output="private-output", stderr="private-stderr")
            stop = threading.Event()
            with patch.object(runtime.subprocess, "run", side_effect=[failure, response, response]), \
                    patch.object(runtime, "shared_dir", return_value=root / "shared"), \
                    patch.object(field, "snapshot", return_value=([], [], [])), \
                    patch.object(usage, "Readout") as readout:
                screen = readout.return_value
                def wait(_interval):
                    if screen.draw.call_count == 2:
                        stop.set()
                screen.wait.side_effect = wait
                usage.run(root / "state", "herdr", True, stop, 0.1)
            self.assertEqual(screen.draw.call_count, 2)
            failed, recovered = [call.args[0] for call in screen.draw.call_args_list]
            self.assertIn("Herdr server status timed out", failed)
            self.assertIn("No open service plans.", recovered)
            self.assertNotIn("timed out", recovered)
            for private in ("private-command-text", "private-output", "private-stderr", "Traceback"):
                self.assertNotIn(private, failed + recovered)
            screen.__exit__.assert_called_once()
            self.assertTrue((root / "shared/snapshot.json").exists())

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
            with self.assertRaisesRegex(sources.SourceError, "Dependency unavailable"):
                sources.resolve_codexbar(Path("state"), time.monotonic() + 1, threading.Event())

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
        import io
        import tarfile
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / "herd"
            env = {"HERDR_PLUGIN_STATE_DIR": temporary}
            with patch.dict(os.environ, env), patch.object(usage.platform, "system", return_value="Darwin"), \
                    patch.object(usage.platform, "machine", return_value="arm64"), \
                    patch.object(usage.urllib.request, "urlopen", return_value=io.BytesIO(b"bad")):
                with self.assertRaisesRegex(RuntimeError, "digest mismatch"):
                    usage.install(state)
            output = io.BytesIO()
            with tarfile.open(fileobj=output, mode="w:gz") as archive:
                entry = tarfile.TarInfo("../escape")
                entry.size = 1
                archive.addfile(entry, io.BytesIO(b"x"))
            data = output.getvalue()
            with patch.dict(os.environ, env), patch.object(usage.platform, "system", return_value="Darwin"), \
                    patch.object(usage.platform, "machine", return_value="arm64"), \
                    patch.dict(usage.ASSETS, {"macos-arm64": hashlib.sha256(data).hexdigest()}), \
                    patch.object(usage.urllib.request, "urlopen", return_value=io.BytesIO(data)):
                with self.assertRaisesRegex(RuntimeError, "archive path"):
                    usage.install(state)
            self.assertFalse((Path(temporary) / "tools/codexbar/escape").exists())

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
        return views.setup(self.state, ROOT, str(ROOT / "bin/herdr"), "w9", "w9:p1", str(self.root), view=view)

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
        self.save(data)
        self.assertEqual(self.setup_views()["errors"], [])
        self.assert_layout()
        data = self.data()
        self.assertEqual(len(data["tabs"]), 1)
        self.assertEqual(sum(c[:2] == ["pane", "move"] for c in data["calls"]), 2)
        self.assertFalse(any(c[:2] in (["pane", "run"], ["pane", "close"], ["pane", "split"]) for c in data["calls"]))

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
