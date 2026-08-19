#!/usr/bin/env python3
"""Unit tests for bin/lantern-bridge. Standard library only, no network.

The module under test has no .py suffix, because it is a program Herdr runs by
name. SourceFileLoader is how you import one of those.
"""
from __future__ import annotations

import hashlib
import hmac
import importlib.util
import io
import json
import os
import queue
import socket
import sys
import tempfile
import threading
import time
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

    def test_dot_ids_cannot_name_a_relative_directory(self):
        # The allowlists gate every id that reaches this, but they are typed
        # by hand, and "." or ".." would resolve out of the conversation dir.
        base = self.state / "bridge" / "telegram"
        for chat_id in ("..", ".", "", "..."):
            wd = lb.workdir_for(self.state, "telegram", chat_id)
            self.assertNotIn("..", wd.parts)
            self.assertNotIn(".", wd.parts)
            resolved = Path(str(wd))
            self.assertTrue(
                str(resolved).startswith(str(base)),
                "%r escaped to %s" % (chat_id, resolved),
            )
            self.assertEqual(wd.parent.name, "unknown")

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


def telegram_update(
    update_id,
    chat_id,
    text=None,
    key="message",
    extra=None,
    chat_type="private",
    sender_id=None,
):
    """One Bot API update, shaped the way Telegram actually sends them.

    Every real message carries `chat.type` and `message.from`, and in a
    private chat the chat id and the sender's user id are the same number.
    """
    message = {
        "chat": {"id": chat_id, "type": chat_type},
        "from": {"id": chat_id if sender_id is None else sender_id},
    }
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

    def test_a_group_does_not_authorize_its_members(self):
        # The reason this matters: an accepted message runs the helper CLI
        # with Bash on this machine. Putting a group id on the allowlist would
        # hand that to everyone who can post in the group.
        cfg = base_config(
            TELEGRAM_BOT_TOKEN="tok", TELEGRAM_ALLOWED_CHATS="-1001234567890"
        )
        adapter = lb.TelegramAdapter(cfg, queue.Queue())
        accepted, offset = adapter.parse_updates(
            {
                "result": [
                    telegram_update(
                        3,
                        -1001234567890,
                        "run something",
                        chat_type="supergroup",
                        sender_id=99999999,
                    )
                ]
            }
        )
        self.assertEqual(accepted, [])
        self.assertEqual(offset, 4)

    def test_a_group_message_from_an_allowlisted_person_is_accepted(self):
        cfg = base_config(TELEGRAM_BOT_TOKEN="tok", TELEGRAM_ALLOWED_CHATS="42")
        adapter = lb.TelegramAdapter(cfg, queue.Queue())
        accepted, _offset = adapter.parse_updates(
            {
                "result": [
                    telegram_update(
                        3,
                        -1001234567890,
                        "hello",
                        chat_type="supergroup",
                        sender_id=42,
                    )
                ]
            }
        )
        self.assertEqual(accepted, [("-1001234567890", "hello")])

    def test_a_private_chat_from_an_allowlisted_person_is_accepted(self):
        accepted, _offset = self.parse([telegram_update(3, 42, "hello")])
        self.assertEqual(accepted, [("42", "hello")])

    def test_a_non_private_chat_whose_id_is_listed_is_still_dropped(self):
        # Same id, and the only difference is that it names a room rather than
        # a person.
        accepted, _offset = self.parse(
            [telegram_update(3, 42, "hello", chat_type="group", sender_id=77)]
        )
        self.assertEqual(accepted, [])

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

    def test_missing_verify_token_refuses(self):
        # compare_digest("", "") is True, so an empty verify token would let
        # any GET pass the webhook challenge.
        cfg = whatsapp_config(WHATSAPP_VERIFY_TOKEN="")
        with self.assertRaises(lb.ConfigError) as caught:
            lb.WhatsAppAdapter(cfg, queue.Queue())
        self.assertIn("WHATSAPP_VERIFY_TOKEN", str(caught.exception))

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

    def test_a_non_ascii_digest_is_false_not_an_exception(self):
        # http.client decodes headers as latin-1, so a byte like this reaches
        # the comparison as a non-ASCII str. hmac.compare_digest raises
        # TypeError on one, and an exception out of do_POST is a traceback in
        # the bridge pane instead of a 401.
        raw = b'{"a":1}'
        self.assertFalse(self.adapter.verify_signature(raw, "sha256=" + "\u00ff" * 64))
        self.assertFalse(self.adapter.verify_signature(raw, "sha256=" + "z" * 64))
        self.assertFalse(self.adapter.verify_signature(raw, "sha256=deadbeef"))

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


def peer_hung_up(sock, timeout=0.05) -> bool:
    """True once the server has closed this connection.

    A close with bytes still unread arrives as a reset rather than an
    end-of-file on both platforms this runs on, so either answer counts.
    """
    sock.settimeout(timeout)
    try:
        return sock.recv(1) == b""
    except (socket.timeout, TimeoutError):
        return False
    except OSError:
        return True


def read_head(sock, timeout=10) -> bytes:
    """Everything up to the end of the response headers, or the close."""
    sock.settimeout(timeout)
    answer = b""
    while b"\r\n\r\n" not in answer:
        try:
            chunk = sock.recv(4096)
        except (socket.timeout, TimeoutError, OSError):
            break
        if not chunk:
            break
        answer += chunk
    return answer


class WebhookHardeningTest(unittest.TestCase):
    """What the webhook has to survive from a caller with no credential.

    Every case here reaches the handler before the signature is checked, and
    the README asks people to publish this port through a tunnel.
    """

    STALLED = (
        b"POST / HTTP/1.1\r\nHost: 127.0.0.1\r\n"
        b"Content-Type: application/json\r\n"
        b"Content-Length: 1048576\r\n\r\nA"
    )

    def start(self):
        self.inbox = queue.Queue()
        cfg = whatsapp_config(
            WHATSAPP_WEBHOOK_HOST="127.0.0.1", WHATSAPP_WEBHOOK_PORT="0"
        )
        adapter = lb.WhatsAppAdapter(cfg, self.inbox)
        server = adapter.start_server()
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def stop():
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()

        self.addCleanup(stop)
        return server, server.server_address[1]

    def connect(self, port, request: bytes):
        sock = socket.create_connection(("127.0.0.1", port), timeout=10)
        self.addCleanup(sock.close)
        sock.sendall(request)
        return sock

    def test_a_stalled_connection_is_dropped_on_the_socket_timeout(self):
        # Inherited from StreamRequestHandler this is None, and None is what
        # let one stalled socket hold a thread for as long as it liked.
        self.assertIsNotNone(lb.WebhookHandler.timeout)
        self.assertGreater(lb.WebhookHandler.timeout, 0)
        # Shortened from here on only so the suite does not sit out the real
        # one; what is under test is that the attribute is applied at all.
        original = lb.WebhookHandler.timeout
        lb.WebhookHandler.timeout = 0.5
        self.addCleanup(setattr, lb.WebhookHandler, "timeout", original)
        _server, port = self.start()
        sock = self.connect(port, self.STALLED)
        # One byte of a megabyte body and then nothing. No credential was
        # offered, so the socket timeout is the only thing that ends this.
        sock.settimeout(10)
        self.assertEqual(sock.recv(4096), b"")

    def test_stalled_connections_cannot_outgrow_the_cap(self):
        server, port = self.start()
        cap = server.max_connections
        before = threading.active_count()
        socks = [self.connect(port, self.STALLED) for _ in range(cap * 5)]
        deadline = time.time() + 15
        dropped = 0
        while time.time() < deadline:
            dropped = sum(1 for sock in socks if peer_hung_up(sock))
            if dropped >= len(socks) - cap:
                break
        self.assertGreaterEqual(
            dropped,
            len(socks) - cap,
            "connections past the cap should be dropped, not parked",
        )
        # The slack is for the accept thread and whatever else the interpreter
        # is running; what must not happen is one thread per socket.
        self.assertLessEqual(threading.active_count() - before, cap + 2)

    def test_a_non_ascii_signature_is_401_and_not_a_traceback(self):
        _server, port = self.start()
        body = json.dumps(whatsapp_body()).encode("utf-8")
        request = (
            b"POST / HTTP/1.1\r\nHost: 127.0.0.1\r\n"
            b"Content-Type: application/json\r\n"
            b"X-Hub-Signature-256: sha256=" + b"\xff" * 64 + b"\r\n"
            b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n\r\n" + body
        )
        # socketserver's default handle_error prints the whole stack, absolute
        # source paths included, to the pane the docs call safe to paste.
        captured = io.StringIO()
        original = sys.stderr
        sys.stderr = captured
        try:
            answer = read_head(self.connect(port, request))
        finally:
            sys.stderr = original
        self.assertIn(b"401", answer.split(b"\r\n", 1)[0])
        self.assertNotIn("Traceback", captured.getvalue())
        self.assertNotIn(str(ROOT), captured.getvalue())
        self.assertTrue(self.inbox.empty())

    def test_an_oversize_body_is_413_and_closes_the_connection(self):
        _server, port = self.start()
        request = (
            b"POST / HTTP/1.1\r\nHost: 127.0.0.1\r\n"
            b"Content-Length: %d\r\n\r\n" % (lb.MAX_WEBHOOK_BODY + 1)
        )
        sock = self.connect(port, request)
        answer = read_head(sock)
        self.assertIn(b"413", answer.split(b"\r\n", 1)[0])
        self.assertIn(b"connection: close", answer.lower())
        # The declared body was never drained. On a kept-alive connection the
        # bytes still to come would be parsed as the next request.
        deadline = time.time() + 5
        while time.time() < deadline:
            if peer_hung_up(sock, timeout=0.2):
                break
        else:
            self.fail("the 413 left the connection open")


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


class DaemonLockTest(unittest.TestCase):
    """Two daemons on one config break every channel, so only one may start."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.state = Path(self.dir.name)

    def take(self):
        fd = lb.acquire_daemon_lock(self.state)
        if fd is None:
            self.skipTest("no advisory file locking on this platform")
        self.addCleanup(self.release, fd)
        return fd

    def release(self, fd):
        try:
            os.close(fd)
        except OSError:
            pass

    def test_a_second_daemon_is_refused_and_named_the_lock(self):
        self.take()
        with self.assertRaises(lb.ConfigError) as caught:
            lb.acquire_daemon_lock(self.state)
        message = str(caught.exception)
        self.assertIn(str(lb.daemon_lock_path(self.state)), message)
        self.assertIn("already running", message)

    def test_a_lock_file_left_behind_does_not_block_the_next_start(self):
        # What a killed daemon leaves: the file is still there, the lock is
        # not. Bare O_EXCL would refuse to start ever again.
        fd = self.take()
        self.release(fd)
        self.assertTrue(lb.daemon_lock_path(self.state).is_file())
        second = lb.acquire_daemon_lock(self.state)
        self.assertIsNotNone(second)
        self.release(second)

    def test_the_file_carries_the_pid_for_a_human(self):
        self.take()
        body = lb.daemon_lock_path(self.state).read_text(encoding="utf-8")
        self.assertEqual(body.strip(), str(os.getpid()))


class DispatchLoopTest(unittest.TestCase):
    """The adapters are daemon threads. An exception on the dispatch thread
    would end the process and take every channel with it."""

    def test_a_failed_turn_is_reported_and_the_loop_continues(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        # serve() parks its lock fd for the life of the process, which is
        # right for a daemon and wrong for a test: Windows will not delete a
        # file that still has an open handle. Cleanups run last-registered
        # first, so this one releases the lock before tmp.cleanup above.
        self.addCleanup(lb.release_daemon_locks)
        state = Path(tmp.name)
        sent = []
        started = threading.Event()
        answered = threading.Event()
        captured = []

        class Fake(lb.Adapter):
            name = "telegram"
            limit = 100

            def __init__(self, cfg, inbox):
                super().__init__(cfg, inbox)
                captured.append(self)

            def run(self):
                started.set()
                threading.Event().wait()

            def send(self, chat_id, text):
                sent.append((chat_id, text))
                if len(sent) >= 2:
                    answered.set()

        seeds = []

        def flaky_seed(*args, **kwargs):
            seeds.append(1)
            if len(seeds) == 1:
                raise OSError("no space left on device")
            return True

        original = (lb.ADAPTERS.get("telegram"), lb.seed_workdir, lb.run_helper, lb.log)

        def restore():
            lb.ADAPTERS["telegram"] = original[0]
            lb.seed_workdir, lb.run_helper, lb.log = original[1], original[2], original[3]

        self.addCleanup(restore)
        lb.ADAPTERS["telegram"] = Fake
        lb.seed_workdir = flaky_seed
        lb.run_helper = lambda *args, **kwargs: "the answer"
        lb.log = lambda message: None

        cfg = base_config(
            BRIDGE_HELPER="claude",
            TELEGRAM_BOT_TOKEN="token-value",
            TELEGRAM_ALLOWED_CHATS="1",
        )
        worker = threading.Thread(
            target=lb.serve, args=(cfg, "claude", "PROMPT", str(state)), daemon=True
        )
        worker.start()
        self.assertTrue(started.wait(10), "the adapter thread never started")
        inbox = captured[0].inbox
        inbox.put(("telegram", "1", "first"))
        inbox.put(("telegram", "1", "second"))
        self.assertTrue(answered.wait(10), "the loop died on the first message")
        self.assertTrue(worker.is_alive())
        # The first turn failed before its reply, so the user hears about it.
        self.assertIn("error", sent[0][1].lower())
        self.assertEqual(sent[1], ("1", "the answer"))


class HomeDirTest(unittest.TestCase):
    """Path.home() reads USERPROFILE on Windows and HOME elsewhere, and raises
    rather than returning None when its own variable is missing. A shell that
    exports only HOME is ordinary on macOS and fatal on Windows."""

    def swap(self, obj, name, value):
        original = getattr(obj, name)
        self.addCleanup(setattr, obj, name, original)
        setattr(obj, name, value)

    def set_env(self, **values):
        original = dict(os.environ)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(original)))
        os.environ.clear()
        os.environ.update(values)

    def raise_home(self):
        def boom():
            raise RuntimeError("Could not determine home directory.")

        self.swap(lb.Path, "home", staticmethod(boom))

    def test_env_home_answers_when_path_home_raises(self):
        self.raise_home()
        self.set_env(HOME=os.sep + "from-env")
        self.assertEqual(lb.home_dir(), Path(os.sep + "from-env"))

    def test_userprofile_answers_when_home_is_absent(self):
        self.raise_home()
        self.set_env(USERPROFILE=os.sep + "from-win")
        self.assertEqual(lb.home_dir(), Path(os.sep + "from-win"))

    def test_nothing_at_all_is_none_not_an_exception(self):
        self.raise_home()
        self.set_env()
        self.assertIsNone(lb.home_dir())

    def test_a_homeless_environment_does_not_end_the_daemon(self):
        # serve() expands the search root before it starts a single adapter,
        # and BRIDGE_CWD defaults to "~", so a raise here is a startup crash
        # rather than a bad hint.
        self.swap(lb, "home_dir", lambda: None)
        self.swap(lb, "log", lambda message: None)
        self.assertEqual(lb.expand_root("~"), "~")
        self.assertEqual(lb.expand_root("~/code"), "~/code")
        self.assertEqual(lb.expand_root("/explicit"), "/explicit")


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
