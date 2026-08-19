#!/usr/bin/env python3
"""Unit tests for bin/lantern-bridge. Standard library only, no network.

The module under test has no .py suffix, because it is a program Herdr runs by
name. SourceFileLoader is how you import one of those.
"""
from __future__ import annotations

import hashlib
import hmac
import importlib.util
import json
import queue
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
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


def telegram_update(update_id, chat_id, text=None, key="message", extra=None):
    message = {"chat": {"id": chat_id}}
    if text is not None:
        message["text"] = text
    if extra:
        message.update(extra)
    return {"update_id": update_id, key: message}


class TelegramParseTest(unittest.TestCase):
    def setUp(self):
        self.inbox = queue.Queue()
        cfg = base_config(TELEGRAM_BOT_TOKEN="tok", TELEGRAM_ALLOWED_CHATS="42, 43")
        self.adapter = lb.TelegramAdapter(cfg, self.inbox)

    def parse(self, updates):
        return self.adapter.parse_updates({"ok": True, "result": updates})

    def test_text_message_from_an_allowed_chat_is_accepted(self):
        accepted, offset = self.parse([telegram_update(7, 42, " hello there ")])
        self.assertEqual(accepted, [("42", "hello there")])
        self.assertEqual(offset, 8)

    def test_edited_message_is_ignored(self):
        accepted, offset = self.parse(
            [telegram_update(7, 42, "hello", key="edited_message")]
        )
        self.assertEqual(accepted, [])
        self.assertEqual(offset, 8)

    def test_non_text_message_is_ignored(self):
        accepted, _ = self.parse([telegram_update(7, 42, None, extra={"photo": []})])
        self.assertEqual(accepted, [])

    def test_empty_text_is_ignored(self):
        self.assertEqual(self.parse([telegram_update(7, 42, "   ")])[0], [])

    def test_disallowed_chat_is_dropped_but_still_acknowledged(self):
        accepted, offset = self.parse([telegram_update(9, 99, "let me in")])
        self.assertEqual(accepted, [])
        # The cursor still advances, or a stranger's message replays forever.
        self.assertEqual(offset, 10)

    def test_offset_advances_past_the_highest_update(self):
        accepted, offset = self.parse(
            [
                telegram_update(4, 42, "one"),
                telegram_update(9, 43, "two"),
                telegram_update(6, 42, "three"),
            ]
        )
        self.assertEqual([c for c, _ in accepted], ["42", "43", "42"])
        self.assertEqual(offset, 10)

    def test_poll_url_carries_the_cursor_and_the_long_poll(self):
        self.adapter.offset = 11
        url = self.adapter.updates_url()
        self.assertIn("offset=11", url)
        self.assertIn("timeout=%d" % lb.TELEGRAM_POLL, url)
        self.assertIn("/bottok/getUpdates", url)

    def test_long_poll_socket_timeout_outlives_the_poll(self):
        self.assertGreater(lb.LONG_POLL_TIMEOUT, lb.TELEGRAM_POLL)

    def test_garbage_payload_does_not_raise(self):
        self.assertEqual(self.adapter.parse_updates({}), ([], 0))
        self.assertEqual(self.adapter.parse_updates({"result": [None, 5]}), ([], 0))


class TelegramSendTest(unittest.TestCase):
    def setUp(self):
        cfg = base_config(TELEGRAM_BOT_TOKEN="tok", TELEGRAM_ALLOWED_CHATS="42")
        self.adapter = lb.TelegramAdapter(cfg, queue.Queue())

    def test_payload_shape(self):
        sends = self.adapter.send_payloads(42, "hi")
        self.assertEqual(len(sends), 1)
        url, payload = sends[0]
        self.assertEqual(url, "https://api.telegram.org/bottok/sendMessage")
        self.assertEqual(payload, {"chat_id": "42", "text": "hi"})

    def test_no_parse_mode(self):
        _url, payload = self.adapter.send_payloads(42, "a_b_c")[0]
        self.assertNotIn("parse_mode", payload)

    def test_long_reply_is_split_on_line_boundaries(self):
        line = "x" * 200
        text = "\n".join([line] * 60)
        sends = self.adapter.send_payloads(42, text)
        self.assertGreater(len(sends), 1)
        for _url, payload in sends:
            self.assertLessEqual(len(payload["text"]), lb.TELEGRAM_LIMIT)
            self.assertTrue(payload["text"].startswith("x"))

    def test_offer_puts_a_named_tuple_on_the_queue(self):
        inbox = queue.Queue()
        cfg = base_config(TELEGRAM_BOT_TOKEN="tok", TELEGRAM_ALLOWED_CHATS="42")
        lb.TelegramAdapter(cfg, inbox).offer(42, "hi")
        self.assertEqual(inbox.get_nowait(), ("telegram", "42", "hi"))


def slack_message(ts, user="U1", text="hello", **extra):
    message = {"type": "message", "ts": ts, "user": user, "text": text}
    message.update(extra)
    return message


class SlackParseTest(unittest.TestCase):
    def setUp(self):
        cfg = base_config(
            SLACK_BOT_TOKEN="xoxb-secret",
            SLACK_CHANNEL="C123",
            SLACK_ALLOWED_USERS="U1,U2",
        )
        self.inbox = queue.Queue()
        self.adapter = lb.SlackAdapter(cfg, self.inbox, now=1000.0)

    def parse(self, messages):
        # Slack returns history newest first.
        return self.adapter.parse_history({"ok": True, "messages": list(reversed(messages))})

    def test_allowed_user_is_accepted(self):
        accepted, oldest = self.parse([slack_message("1001.000100", "U1", " hi ")])
        self.assertEqual(accepted, [("C123", "hi")])
        self.assertEqual(oldest, "1001.000100")

    def test_own_bot_message_is_skipped(self):
        accepted, oldest = self.parse(
            [slack_message("1001.000100", "U1", "my own reply", bot_id="B9")]
        )
        self.assertEqual(accepted, [])
        self.assertEqual(oldest, "1001.000100")

    def test_subtype_is_skipped(self):
        accepted, _ = self.parse(
            [slack_message("1001.000100", "U1", "joined", subtype="channel_join")]
        )
        self.assertEqual(accepted, [])

    def test_disallowed_user_is_skipped(self):
        accepted, oldest = self.parse([slack_message("1002.000100", "U9", "let me in")])
        self.assertEqual(accepted, [])
        self.assertEqual(oldest, "1002.000100")

    def test_cursor_advances_to_the_newest_ts(self):
        accepted, oldest = self.parse(
            [
                slack_message("1001.000100", "U1", "one"),
                slack_message("1002.000100", "U9", "dropped"),
                slack_message("1003.000100", "U2", "two"),
            ]
        )
        # Oldest first, whichever order Slack listed them in, and the cursor
        # clears the dropped message too.
        self.assertEqual([t for _c, t in accepted], ["one", "two"])
        self.assertEqual(oldest, "1003.000100")

    def test_a_replayed_message_is_not_answered_twice(self):
        message = slack_message("1001.000100", "U1", "hi")
        self.assertEqual(len(self.parse([message])[0]), 1)
        self.adapter.oldest = "1000.000000"
        self.assertEqual(self.parse([message])[0], [])

    def test_messages_before_startup_never_arrive(self):
        # The cursor is sent to Slack, so old history is not returned at all;
        # this pins the cursor's initial value, which is what does that.
        self.assertEqual(self.adapter.oldest, "%.6f" % 1000.0)
        self.assertIn("oldest=1000.000000", self.adapter.history_url())
        self.assertIn("channel=C123", self.adapter.history_url())

    def test_seen_set_stays_bounded(self):
        for i in range(700):
            self.adapter.remember("2000.%06d" % i)
        self.assertLessEqual(len(self.adapter.seen), 512)

    def test_garbage_payload_does_not_raise(self):
        self.assertEqual(self.adapter.parse_history({}), ([], self.adapter.oldest))
        self.assertEqual(
            self.adapter.parse_history({"messages": [None, {"ts": "nope"}]}),
            ([], self.adapter.oldest),
        )


class SlackSendTest(unittest.TestCase):
    def setUp(self):
        cfg = base_config(
            SLACK_BOT_TOKEN="xoxb-secret",
            SLACK_CHANNEL="C123",
            SLACK_ALLOWED_USERS="U1",
        )
        self.adapter = lb.SlackAdapter(cfg, queue.Queue(), now=1000.0)

    def test_payload_shape(self):
        sends = self.adapter.send_payloads("C123", "hi")
        self.assertEqual(
            sends, [("https://slack.com/api/chat.postMessage", {"channel": "C123", "text": "hi"})]
        )

    def test_token_travels_in_the_header_not_the_url(self):
        self.assertEqual(self.adapter.headers(), {"Authorization": "Bearer xoxb-secret"})
        self.assertNotIn("xoxb-secret", self.adapter.history_url())

    def test_long_reply_is_split_at_the_slack_limit(self):
        text = "\n".join(["y" * 200] * 60)
        sends = self.adapter.send_payloads("C123", text)
        self.assertGreater(len(sends), 1)
        for _url, payload in sends:
            self.assertLessEqual(len(payload["text"]), lb.SLACK_LIMIT)


WHATSAPP_SECRET = "app-secret-value"


def whatsapp_config(**overrides):
    cfg = base_config(
        WHATSAPP_ACCESS_TOKEN="access-token-value",
        WHATSAPP_PHONE_NUMBER_ID="PN1",
        WHATSAPP_VERIFY_TOKEN="verify-token-value",
        WHATSAPP_APP_SECRET=WHATSAPP_SECRET,
        WHATSAPP_ALLOWED_NUMBERS="+1 555 123 4567, 447700900000",
    )
    cfg.update(overrides)
    return cfg


def whatsapp_body(sender="15551234567", text="hello", kind="text"):
    message = {"from": sender, "id": "wamid.1", "type": kind}
    if kind == "text":
        message["text"] = {"body": text}
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": "E1", "changes": [{"value": {"messages": [message]}, "field": "messages"}]}],
    }


def sign(raw: bytes, secret=WHATSAPP_SECRET):
    return "sha256=" + hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()


class WhatsAppConfigTest(unittest.TestCase):
    def test_missing_app_secret_refuses(self):
        cfg = whatsapp_config(WHATSAPP_APP_SECRET="")
        with self.assertRaises(lb.ConfigError) as caught:
            lb.WhatsAppAdapter(cfg, queue.Queue())
        self.assertIn("WHATSAPP_APP_SECRET", str(caught.exception))

    def test_missing_allowlist_refuses(self):
        cfg = whatsapp_config(WHATSAPP_ALLOWED_NUMBERS="")
        with self.assertRaises(lb.ConfigError) as caught:
            lb.WhatsAppAdapter(cfg, queue.Queue())
        self.assertIn("WHATSAPP_ALLOWED_NUMBERS", str(caught.exception))

    def test_allowlist_ignores_formatting(self):
        adapter = lb.WhatsAppAdapter(whatsapp_config(), queue.Queue())
        self.assertEqual(adapter.allowed, {"15551234567", "447700900000"})


class WhatsAppSignatureTest(unittest.TestCase):
    def setUp(self):
        self.adapter = lb.WhatsAppAdapter(whatsapp_config(), queue.Queue())

    def test_valid_signature(self):
        raw = b'{"a":1}'
        self.assertTrue(self.adapter.verify_signature(raw, sign(raw)))

    def test_wrong_secret(self):
        raw = b'{"a":1}'
        self.assertFalse(self.adapter.verify_signature(raw, sign(raw, "other")))

    def test_body_tampered_after_signing(self):
        signature = sign(b'{"a":1}')
        self.assertFalse(self.adapter.verify_signature(b'{"a":2}', signature))

    def test_missing_or_malformed_header(self):
        raw = b'{"a":1}'
        for header in ("", "sha1=deadbeef", "sha256=", "deadbeef", "sha256"):
            self.assertFalse(self.adapter.verify_signature(raw, header), header)

    def test_uppercase_digest_is_accepted(self):
        raw = b'{"a":1}'
        self.assertTrue(self.adapter.verify_signature(raw, sign(raw).upper().replace("SHA256", "sha256")))

    def test_verify_challenge(self):
        params = {
            "hub.mode": ["subscribe"],
            "hub.verify_token": ["verify-token-value"],
            "hub.challenge": ["1234"],
        }
        self.assertEqual(self.adapter.verify_challenge(params), "1234")
        bad = dict(params, **{"hub.verify_token": ["wrong"]})
        self.assertIsNone(self.adapter.verify_challenge(bad))
        self.assertIsNone(self.adapter.verify_challenge({}))
        self.assertIsNone(
            self.adapter.verify_challenge(dict(params, **{"hub.mode": ["unsubscribe"]}))
        )


class WhatsAppExtractTest(unittest.TestCase):
    def setUp(self):
        self.adapter = lb.WhatsAppAdapter(whatsapp_config(), queue.Queue())

    def test_text_message(self):
        self.assertEqual(
            self.adapter.accepted_from(whatsapp_body(text=" hi ")),
            [("15551234567", "hi")],
        )

    def test_non_text_message_is_ignored(self):
        self.assertEqual(self.adapter.extract_messages(whatsapp_body(kind="image")), [])

    def test_status_only_body_is_ignored(self):
        body = {"entry": [{"changes": [{"value": {"statuses": [{"status": "read"}]}}]}]}
        self.assertEqual(self.adapter.extract_messages(body), [])

    def test_disallowed_number_is_dropped(self):
        body = whatsapp_body(sender="19998887777")
        self.assertEqual(len(self.adapter.extract_messages(body)), 1)
        self.assertEqual(self.adapter.accepted_from(body), [])

    def test_garbage_body_does_not_raise(self):
        for body in ({}, {"entry": None}, {"entry": [1, {"changes": [2]}]}):
            self.assertEqual(self.adapter.extract_messages(body), [])


class WhatsAppSendTest(unittest.TestCase):
    def setUp(self):
        self.adapter = lb.WhatsAppAdapter(whatsapp_config(), queue.Queue())

    def test_payload_shape(self):
        sends = self.adapter.send_payloads("15551234567", "hi")
        url, payload = sends[0]
        self.assertEqual(
            url, "https://graph.facebook.com/%s/PN1/messages" % lb.WHATSAPP_API_VERSION
        )
        self.assertEqual(payload["messaging_product"], "whatsapp")
        self.assertEqual(payload["to"], "15551234567")
        self.assertEqual(payload["type"], "text")
        self.assertEqual(payload["text"]["body"], "hi")

    def test_split_at_the_whatsapp_limit(self):
        text = "\n".join(["z" * 200] * 60)
        sends = self.adapter.send_payloads("15551234567", text)
        self.assertGreater(len(sends), 1)
        for _url, payload in sends:
            self.assertLessEqual(len(payload["text"]["body"]), lb.WHATSAPP_LIMIT)

    def test_access_token_is_not_in_the_url(self):
        url, _payload = self.adapter.send_payloads("15551234567", "hi")[0]
        self.assertNotIn("access-token-value", url)


class WhatsAppWebhookTest(unittest.TestCase):
    """The signature check against a real socket, on an ephemeral port."""

    def setUp(self):
        self.inbox = queue.Queue()
        cfg = whatsapp_config(WHATSAPP_WEBHOOK_HOST="127.0.0.1", WHATSAPP_WEBHOOK_PORT="0")
        self.adapter = lb.WhatsAppAdapter(cfg, self.inbox)
        self.server = self.adapter.start_server()
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()

    def url(self, path="/"):
        return "http://127.0.0.1:%d%s" % (self.port, path)

    def post(self, raw: bytes, signature):
        headers = {"Content-Type": "application/json"}
        if signature is not None:
            headers["X-Hub-Signature-256"] = signature
        request = urllib.request.Request(self.url(), data=raw, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            with exc:
                return exc.code, exc.read()

    def test_good_signature_enqueues_the_message(self):
        raw = json.dumps(whatsapp_body(text="light the field")).encode("utf-8")
        status, _body = self.post(raw, sign(raw))
        self.assertEqual(status, 200)
        self.assertEqual(
            self.inbox.get(timeout=5), ("whatsapp", "15551234567", "light the field")
        )

    def test_bad_signature_is_401_and_enqueues_nothing(self):
        raw = json.dumps(whatsapp_body()).encode("utf-8")
        status, _body = self.post(raw, sign(raw, "wrong-secret"))
        self.assertEqual(status, 401)
        self.assertTrue(self.inbox.empty())

    def test_missing_signature_is_401_and_enqueues_nothing(self):
        raw = json.dumps(whatsapp_body()).encode("utf-8")
        status, _body = self.post(raw, None)
        self.assertEqual(status, 401)
        self.assertTrue(self.inbox.empty())

    def test_disallowed_sender_is_accepted_but_never_queued(self):
        raw = json.dumps(whatsapp_body(sender="19998887777")).encode("utf-8")
        status, _body = self.post(raw, sign(raw))
        self.assertEqual(status, 200)
        self.assertTrue(self.inbox.empty())

    def test_signed_garbage_is_400(self):
        raw = b"not json"
        status, _body = self.post(raw, sign(raw))
        self.assertEqual(status, 400)
        self.assertTrue(self.inbox.empty())

    def test_get_verification_echoes_the_challenge(self):
        query = urllib.parse.urlencode(
            {
                "hub.mode": "subscribe",
                "hub.verify_token": "verify-token-value",
                "hub.challenge": "42424",
            }
        )
        with urllib.request.urlopen(self.url("/?" + query), timeout=10) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.read(), b"42424")

    def test_get_verification_mismatch_is_403(self):
        query = urllib.parse.urlencode(
            {
                "hub.mode": "subscribe",
                "hub.verify_token": "wrong",
                "hub.challenge": "42424",
            }
        )
        try:
            with urllib.request.urlopen(self.url("/?" + query), timeout=10) as response:
                self.fail("expected 403, got %d" % response.status)
        except urllib.error.HTTPError as exc:
            with exc:
                self.assertEqual(exc.code, 403)
                self.assertNotIn(b"42424", exc.read())


class AdapterLoopTest(unittest.TestCase):
    def test_a_network_error_backs_off_and_continues(self):
        calls = []

        class Flaky(lb.Adapter):
            name = "flaky"

            def poll_once(self):
                calls.append(1)
                if len(calls) == 1:
                    raise OSError("connection reset")
                raise KeyboardInterrupt

        slept = []
        real_sleep = lb.time.sleep
        lb.time.sleep = lambda s: slept.append(s)
        try:
            with self.assertRaises(KeyboardInterrupt):
                Flaky(base_config(), queue.Queue()).run()
        finally:
            lb.time.sleep = real_sleep
        self.assertEqual(len(calls), 2)
        self.assertEqual(slept, [lb.BACKOFF])


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
