#!/usr/bin/env python3
"""Unit tests for bin/lantern-bridge. Standard library only, no network.

The module under test has no .py suffix, because it is a program Herdr runs by
name. SourceFileLoader is how you import one of those.
"""
from __future__ import annotations

import importlib.util
import tempfile
import unittest
from importlib.machinery import SourceFileLoader
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BRIDGE_PATH = ROOT / "bin" / "lantern-bridge"


def load_bridge():
    loader = SourceFileLoader("lantern_bridge", str(BRIDGE_PATH))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


lb = load_bridge()


def base_config(**overrides):
    cfg = {key: "" for key in lb.CONF_KEYS}
    cfg.update(lb.DEFAULTS)
    cfg.update(overrides)
    return cfg


class ConfParseTest(unittest.TestCase):
    def test_reads_key_value_lines(self):
        cfg = lb.parse_conf('BRIDGE_HELPER="codex"\nBRIDGE_MODEL=gpt-x\n')
        self.assertEqual(cfg["BRIDGE_HELPER"], "codex")
        self.assertEqual(cfg["BRIDGE_MODEL"], "gpt-x")

    def test_skips_blanks_and_comments(self):
        cfg = lb.parse_conf("\n# a comment\n\nBRIDGE_CWD='~/code'\n")
        self.assertEqual(cfg, {"BRIDGE_CWD": "~/code"})

    def test_rejects_unknown_key(self):
        with self.assertRaises(lb.ConfigError):
            lb.parse_conf("EVIL=1\n")

    def test_rejects_shell_metacharacters(self):
        for value in ("a; rm -rf /", "$(id)", "a`id`", "a|b", "a&b", "a>b", "a{b}"):
            with self.assertRaises(lb.ConfigError):
                lb.parse_conf('BRIDGE_MODEL="%s"\n' % value)

    def test_rejects_line_without_equals(self):
        with self.assertRaises(lb.ConfigError):
            lb.parse_conf("BRIDGE_MODEL\n")

    def test_never_sources_the_file(self):
        # A line that would be devastating as shell is just a parse error.
        with self.assertRaises(lb.ConfigError):
            lb.parse_conf("rm -rf /tmp/nothing\n")


class LoadConfigTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.conf = Path(self.dir.name) / "bridge.conf"

    def test_env_fills_an_empty_line(self):
        self.conf.write_text('TELEGRAM_BOT_TOKEN=""\n', encoding="utf-8")
        cfg = lb.load_config(self.conf, {"TELEGRAM_BOT_TOKEN": "from-env"})
        self.assertEqual(cfg["TELEGRAM_BOT_TOKEN"], "from-env")

    def test_conf_wins_over_env(self):
        self.conf.write_text('TELEGRAM_BOT_TOKEN="from-file"\n', encoding="utf-8")
        cfg = lb.load_config(self.conf, {"TELEGRAM_BOT_TOKEN": "from-env"})
        self.assertEqual(cfg["TELEGRAM_BOT_TOKEN"], "from-file")

    def test_defaults_apply_last(self):
        self.conf.write_text("", encoding="utf-8")
        cfg = lb.load_config(self.conf, {})
        self.assertEqual(cfg["BRIDGE_CWD"], "~")
        self.assertEqual(cfg["BRIDGE_SPAWN_KIND"], "claude")
        self.assertEqual(cfg["WHATSAPP_WEBHOOK_HOST"], "127.0.0.1")
        self.assertEqual(cfg["WHATSAPP_WEBHOOK_PORT"], "8787")

    def test_missing_file_is_env_only(self):
        cfg = lb.load_config(Path(self.dir.name) / "absent.conf", {"BRIDGE_MODEL": "m"})
        self.assertEqual(cfg["BRIDGE_MODEL"], "m")

    def test_example_file_parses_and_matches_the_key_list(self):
        text = (ROOT / "bridge.conf.example").read_text(encoding="utf-8")
        parsed = lb.parse_conf(text)
        self.assertEqual(sorted(parsed), sorted(lb.CONF_KEYS))


class AllowlistTest(unittest.TestCase):
    def test_splits_and_trims(self):
        self.assertEqual(lb.split_list(" 1, 22 ,,333 "), ["1", "22", "333"])

    def test_empty_is_empty(self):
        self.assertEqual(lb.split_list(""), [])

    def test_every_enabled_channel_needs_an_allowlist(self):
        for cred, extra in (
            ("TELEGRAM_BOT_TOKEN", {}),
            ("SLACK_BOT_TOKEN", {"SLACK_CHANNEL": "C1"}),
            (
                "WHATSAPP_ACCESS_TOKEN",
                {
                    "WHATSAPP_PHONE_NUMBER_ID": "1",
                    "WHATSAPP_VERIFY_TOKEN": "v",
                    "WHATSAPP_APP_SECRET": "s",
                },
            ),
        ):
            cfg = base_config(**{cred: "x"}, **extra)
            problems = lb.validate(cfg, None, None)
            self.assertTrue(
                any("ALLOWED" in p for p in problems),
                "%s should demand an allowlist, got %r" % (cred, problems),
            )

    def test_no_channel_names_both_files(self):
        problems = lb.validate(base_config(), "/c/bridge.conf", "/r/bridge.conf.example")
        self.assertEqual(len(problems), 1)
        self.assertIn("/c/bridge.conf", problems[0])
        self.assertIn("/r/bridge.conf.example", problems[0])

    def test_a_full_telegram_config_is_clean(self):
        cfg = base_config(TELEGRAM_BOT_TOKEN="t", TELEGRAM_ALLOWED_CHATS="42")
        self.assertEqual(lb.validate(cfg, None, None), [])

    def test_whatsapp_without_app_secret_refuses(self):
        cfg = base_config(
            WHATSAPP_ACCESS_TOKEN="t",
            WHATSAPP_PHONE_NUMBER_ID="1",
            WHATSAPP_VERIFY_TOKEN="v",
            WHATSAPP_ALLOWED_NUMBERS="15551234567",
        )
        problems = lb.validate(cfg, None, None)
        self.assertTrue(any("WHATSAPP_APP_SECRET" in p for p in problems))

    def test_bad_spawn_kind_refuses(self):
        cfg = base_config(
            TELEGRAM_BOT_TOKEN="t",
            TELEGRAM_ALLOWED_CHATS="42",
            BRIDGE_SPAWN_KIND="Claude Code",
        )
        self.assertTrue(any("BRIDGE_SPAWN_KIND" in p for p in lb.validate(cfg, None, None)))


class HelperResolutionTest(unittest.TestCase):
    def test_explicit_helper_wins(self):
        cfg = base_config(BRIDGE_HELPER="codex")
        self.assertEqual(lb.resolve_helper(cfg, which=lambda _n: None), "codex")

    def test_detects_first_supported_on_path(self):
        cfg = base_config()
        self.assertEqual(
            lb.resolve_helper(cfg, which=lambda n: "/x/" + n if n == "codex" else None),
            "codex",
        )
        self.assertEqual(lb.resolve_helper(cfg, which=lambda n: "/x/" + n), "claude")

    def test_unsupported_helper_names_the_two(self):
        cfg = base_config(BRIDGE_HELPER="grok")
        with self.assertRaises(lb.ConfigError) as caught:
            lb.resolve_helper(cfg, which=lambda n: "/x/" + n)
        self.assertIn("claude", str(caught.exception))
        self.assertIn("codex", str(caught.exception))

    def test_nothing_on_path_fails(self):
        with self.assertRaises(lb.ConfigError):
            lb.resolve_helper(base_config(), which=lambda _n: None)


class ArgvTest(unittest.TestCase):
    def test_claude_first_turn(self):
        cfg = base_config(BRIDGE_HELPER="claude")
        argv = lb.build_argv(cfg, "hi", False)
        self.assertEqual(
            argv,
            [
                "claude",
                "-p",
                "hi",
                "--output-format",
                "text",
                "--allowed-tools",
                lb.CLAUDE_TOOLS,
            ],
        )

    def test_claude_resume_and_options(self):
        cfg = base_config(
            BRIDGE_HELPER="claude", BRIDGE_MODEL="opus", BRIDGE_EFFORT="high"
        )
        argv = lb.build_argv(cfg, "hi", True)
        self.assertIn("--continue", argv)
        self.assertEqual(argv[argv.index("--model") + 1], "opus")
        self.assertEqual(argv[argv.index("--effort") + 1], "high")

    def test_codex_first_turn_and_resume(self):
        cfg = base_config(BRIDGE_HELPER="codex")
        self.assertEqual(
            lb.build_argv(cfg, "hi", False),
            ["codex", "exec", "hi", "--skip-git-repo-check"],
        )
        self.assertEqual(
            lb.build_argv(cfg, "hi", True),
            ["codex", "exec", "resume", "--last", "hi", "--skip-git-repo-check"],
        )

    def test_codex_effort_uses_config_key(self):
        cfg = base_config(BRIDGE_HELPER="codex", BRIDGE_EFFORT="high")
        argv = lb.build_argv(cfg, "hi", False)
        self.assertIn('model_reasoning_effort="high"', argv)

    def test_extra_args_are_appended_as_separate_tokens(self):
        cfg = base_config(BRIDGE_HELPER="claude", BRIDGE_EXTRA_ARGS="--verbose --foo")
        argv = lb.build_argv(cfg, "hi", False)
        self.assertEqual(argv[-2:], ["--verbose", "--foo"])

    def test_message_text_is_one_argument(self):
        # The text arrives from a stranger. It must stay a single argv slot.
        cfg = base_config(BRIDGE_HELPER="claude")
        argv = lb.build_argv(cfg, "a b; rm -rf /", False)
        self.assertIn("a b; rm -rf /", argv)

    def test_unsupported_helper_raises(self):
        with self.assertRaises(lb.ConfigError):
            lb.build_argv(base_config(BRIDGE_HELPER="grok"), "hi", False)


class SplitMessageTest(unittest.TestCase):
    def test_short_text_is_one_chunk(self):
        self.assertEqual(lb.split_message("hello", 100), ["hello"])

    def test_empty_text_is_one_empty_chunk(self):
        self.assertEqual(lb.split_message("", 100), [""])

    def test_every_chunk_respects_the_limit(self):
        text = ("word " * 400).strip()
        chunks = lb.split_message(text, 50)
        self.assertTrue(all(len(c) <= 50 for c in chunks))
        self.assertEqual("".join(c for c in chunks).replace(" ", ""), text.replace(" ", ""))

    def test_prefers_a_line_boundary(self):
        text = "alpha\nbeta\ngamma"
        chunks = lb.split_message(text, 12)
        self.assertEqual(chunks[0], "alpha\nbeta")

    def test_unbreakable_text_is_hard_cut(self):
        text = "x" * 25
        self.assertEqual(lb.split_message(text, 10), ["x" * 10, "x" * 10, "x" * 5])

    def test_channel_limits_are_the_documented_ones(self):
        self.assertEqual(lb.TELEGRAM_LIMIT, 4096)
        self.assertEqual(lb.SLACK_LIMIT, 4000)
        self.assertEqual(lb.WHATSAPP_LIMIT, 4096)


class RedactionTest(unittest.TestCase):
    def test_secrets_never_show_their_bytes(self):
        for key in lb.SECRET_KEYS:
            shown = lb.redact(key, "super-secret-token")
            self.assertNotIn("super-secret-token", shown)
            self.assertIn("18 bytes", shown)

    def test_unset_is_named_not_blank(self):
        self.assertEqual(lb.redact("TELEGRAM_BOT_TOKEN", ""), "(unset)")

    def test_plain_keys_show_their_value(self):
        self.assertEqual(lb.redact("BRIDGE_MODEL", "opus"), "opus")

    def test_every_credential_key_is_a_secret(self):
        for key in lb.CONF_KEYS:
            if key.endswith(("_TOKEN", "_SECRET")):
                self.assertIn(key, lb.SECRET_KEYS, "%s must be redacted" % key)


class WorkdirTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.state = Path(self.dir.name)

    def test_chat_id_cannot_escape_the_state_dir(self):
        wd = lb.workdir_for(self.state, "telegram", "../../etc/passwd")
        self.assertTrue(str(wd).startswith(str(self.state / "bridge" / "telegram")))
        self.assertNotIn("..", wd.parts)

    def test_each_conversation_gets_its_own_dir(self):
        a = lb.workdir_for(self.state, "telegram", 1)
        b = lb.workdir_for(self.state, "telegram", 2)
        c = lb.workdir_for(self.state, "slack", 1)
        self.assertEqual(len({a, b, c}), 3)

    def test_seeding_writes_both_files_once(self):
        wd = lb.workdir_for(self.state, "telegram", 7)
        cfg = base_config(BRIDGE_HELPER="claude")
        appendix = lb.remote_appendix(cfg, "telegram", "/home/j")
        self.assertTrue(lb.seed_workdir(wd, "PROMPT BODY", appendix))
        for name in ("AGENTS.md", "CLAUDE.md"):
            body = (wd / name).read_text(encoding="utf-8")
            self.assertIn("PROMPT BODY", body)
            self.assertIn("HERDR_HELPER_OK=1", body)
            self.assertIn("telegram", body)
            self.assertIn("/home/j", body)
        (wd / "AGENTS.md").write_text("EDITED", encoding="utf-8")
        self.assertFalse(lb.seed_workdir(wd, "PROMPT BODY", appendix))
        self.assertEqual((wd / "AGENTS.md").read_text(encoding="utf-8"), "EDITED")

    def test_session_marker_drives_resume(self):
        wd = lb.workdir_for(self.state, "slack", "C1")
        lb.seed_workdir(wd, "P", "")
        self.assertFalse(lb.has_session(wd))
        lb.mark_session(wd)
        self.assertTrue(lb.has_session(wd))
        # The marker must not land where the helper reads its instructions.
        self.assertFalse((wd / "session.marker").exists())

    def test_appendix_says_there_is_no_terminal(self):
        text = lb.remote_appendix(base_config(), "whatsapp", "/root")
        self.assertIn("HERDR_HELPER_OK=1", text)
        self.assertIn("chat", text)


class ExpandRootTest(unittest.TestCase):
    def test_tilde(self):
        self.assertEqual(lb.expand_root("~"), str(Path.home()))
        self.assertEqual(lb.expand_root("~/code"), str(Path.home() / "code"))

    def test_trailing_slash_dropped(self):
        self.assertEqual(lb.expand_root("/tmp/"), "/tmp")

    def test_empty_is_home(self):
        self.assertEqual(lb.expand_root(""), str(Path.home()))


if __name__ == "__main__":
    unittest.main(verbosity=1)
