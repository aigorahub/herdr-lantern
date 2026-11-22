"""Desktop protocol and configuration boundaries. No live user processes."""
from __future__ import annotations

from contextlib import contextmanager
from itertools import count
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))
import desktop
import lantern_views


def terminal_missing():
    return desktop.CoreError({"data": {"kind": "not_found"}})


class ReadoutHome:
    def __init__(self, state):
        self.state = state
        self.record = {"terminal": "t1", "instance": "instance", "layout_ready": True}
        self.panes = {"p1": "t1"}
        self.terminals = {}
        self.starts = {}
        self.live_argv = {}
        self.get_replies = {}
        self.actions = []
        self.kill_state = "finished"
        self.kill_error = None
        self.exit_process = True
        self.code = lantern_views.code_stamp(desktop.ROOT)
        for index, (role, (_, script)) in enumerate(lantern_views.VIEWS.items(), 2):
            pane, address = f"p{index}", f"t{index}"
            root, pid = 100 + index, 200 + index
            created = int(time.time() * 1000) - 60000
            argv = ["/bin/sh", "-ic", '"$@"; exec "$0" -i', "/bin/sh", "sh",
                    str(desktop.ROOT / "bin" / "lantern-app"), role, str(state)]
            self.panes[pane] = address
            self.record[role] = {"pane": pane, "terminal": address}
            self.terminals[address] = {"address": address, "state": "live", "pid": root,
                                       "created_ms": created, "argv": argv, "cwd": str(state),
                                       "foreground": {"pid": pid, "argv": [sys.executable,
                                           str(desktop.ROOT / "bin" / script), "--state-dir", str(state),
                                           "--herdr", str(desktop.READER), "watch"]}}
            self.live_argv[root] = argv
            self.starts[pid] = str(pid)
            receipt = state / f"{role}-start.json"
            receipt.write_text(json.dumps({"terminal": address, "pid": pid, "start": self.starts[pid]}))
            os.utime(receipt, (created / 1000, created / 1000))
            (state / f"{role}-watcher.json").write_text(json.dumps({"pid": pid, "code": self.code}))
        (state / "home.json").write_text(json.dumps(self.record))

    def stale(self, role):
        address = self.record[role]["terminal"]
        target = self.terminals[address]
        (self.state / f"{role}-watcher.json").write_text(
            json.dumps({"pid": target["foreground"]["pid"], "code": "old"}))
        return target

    def run(self, argv, timeout=15):
        if argv == [desktop.APP, "ls"]:
            return json.dumps({"spaces": [{"id": "s1", "tabs": [{"id": "tab1", "terminals": [
                {"pane": pane, "terminal": terminal} for pane, terminal in self.panes.items()]}]}]})
        if argv[:2] == [desktop.HC, "label"]:
            self.actions.append(("label", argv[2:]))
            return ""
        raise AssertionError(argv)

    def core(self, calls, timeout=10):
        (method, params), = calls
        if method == "terminal.list":
            return [{"terminals": [t for t in self.terminals.values() if t["state"] == "live"]}]
        if method == "terminal.get":
            address = params["terminal"]
            replies = self.get_replies.get(address)
            result = replies.pop(0) if replies else self.terminals.get(address, terminal_missing())
            if isinstance(result, BaseException):
                raise result
            return [result]
        if method == "terminal.kill":
            self.actions.append((method, params))
            target = self.terminals[params["terminal"]]
            target["state"] = self.kill_state
            if self.exit_process:
                self.starts.pop(target["foreground"]["pid"])
            if self.kill_error:
                raise self.kill_error
            return [target]
        if method == "doc.get":
            root = {"id": "root", "type": "row", "children": [
                {"type": "block", "content": {"terminal": address}} for address in self.panes.values()]}
            return [{"value": {"spaces": [{"tabs": [{"id": "tab1", "root": root}]}]}, "version": 1}]
        if method == "doc.set":
            return [{}]
        raise AssertionError(method)

    def app(self, command, **params):
        self.actions.append((command, params))
        if command == "pane.close":
            del self.panes[params["pane"]]
        elif command == "pane.split":
            created = {"pane": "p4", "terminal": "t4"}
            self.panes[created["pane"]] = created["terminal"]
            return created
        elif command not in {"space.move", "pane.focus"}:
            raise AssertionError(command)
        return {}

    def mutations(self):
        return [(method, params) for method, params in self.actions
                if method in {"terminal.kill", "pane.close", "pane.split"}]


class DesktopTests(unittest.TestCase):
    @contextmanager
    def readout_home(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = ReadoutHome(Path(tmp).resolve())
            with patch.object(desktop, "core_calls", side_effect=home.core), \
                    patch.object(desktop, "run", side_effect=home.run), \
                    patch.object(desktop, "app", side_effect=home.app), \
                    patch.object(desktop, "process_argv", side_effect=home.live_argv.get), \
                    patch.object(desktop.runtime, "process_start", side_effect=lambda pid: home.starts.get(pid, "")), \
                    patch.object(desktop.time, "monotonic", side_effect=count(step=0.5)), \
                    patch.object(desktop.time, "sleep"):
                yield home

    def inventory(self):
        return {"spaces": [{"id": "s1", "name": "api", "machine": None,
                            "tabs": [{"id": "tab1", "name": "fix", "terminals": [
                                {"pane": "p1", "terminal": "t1", "app": "codex"},
                                {"pane": "p2", "terminal": "t2", "app": "python"}]}]}]}

    def terminals(self):
        return {"t1": {"state": "live", "cwd": "/api", "status": {"app": "codex", "state": "blocked"},
                       "status_ms": 123},
                "t2": {"state": "live", "cwd": "/api", "status": None}}

    def test_native_blocked_status_is_preserved_and_readout_is_not_an_agent(self):
        result = desktop.snapshot(self.inventory(), self.terminals(), {})
        self.assertEqual(len(result["agents"]), 1)
        self.assertEqual(result["agents"][0]["agent_status"], "blocked")
        self.assertEqual(result["agents"][0]["pane_id"], "p1")
        terminals = self.terminals()
        terminals["t1"]["status"] = {"app": "grok-build", "state": "working"}
        grok = desktop.snapshot(self.inventory(), terminals, {})["agents"][0]
        self.assertEqual((grok["agent"], grok["agent_status"]), ("grok", "working"))

    def test_bridged_legacy_agent_keeps_legacy_action_target_and_app_focus_target(self):
        terminals = self.terminals()
        binary = str(Path.cwd() / "herdr")
        terminals["t1"]["foreground"] = {"argv": [binary, "terminal", "attach", "term_old"]}
        result = desktop.snapshot(self.inventory(), terminals, {binary: {"term_old": {
            "pane_id": "w2:p3", "agent": "claude", "agent_status": "working"}}})
        agent = result["agents"][0]
        self.assertEqual((agent["pane_id"], agent["legacy_pane_id"]), ("p1", "w2:p3"))
        self.assertEqual((agent["agent"], agent["agent_status"]), ("claude", "working"))
        with self.assertRaises(RuntimeError):
            desktop.snapshot(self.inventory(), terminals, {binary: {}})

    def test_terminal_disappearing_during_read_fails_instead_of_removing_rows(self):
        with self.assertRaises(RuntimeError):
            desktop.snapshot(self.inventory(), {}, {})

    def core_replies(self, *responses):
        def run(argv, **kwargs):
            if argv[1:] == ["ls"]:
                return subprocess.CompletedProcess(argv, 0, json.dumps(self.inventory()), "")
            requests = [json.loads(line) for line in kwargs["input"].splitlines()]
            results = {"server.status": {"pid": os.getpid(), "core": {"id": "core-1"}},
                       "terminal.list": {"terminals": [dict(t, address=a) for a, t in self.terminals().items()]}}
            # Answer in reverse order: replies are matched by id, not by position.
            lines = [json.dumps({"id": r["id"], "result": results[r["method"]]}) for r in reversed(requests)]
            return subprocess.CompletedProcess(argv, 0, "\n".join(responses or lines) + "\n", "")
        return run

    def test_world_read_and_status_read_report_the_same_core(self):
        with patch.object(desktop.subprocess, "run", side_effect=self.core_replies()):
            world = desktop.read_world()
            self.assertEqual(world.server, desktop.server_identity(2))
        self.assertEqual([agent["pane_id"] for agent in world.view["agents"]], ["p1"])

    def test_finished_pane_is_left_out_and_an_unknown_terminal_still_fails_the_read(self):
        inventory = self.inventory()
        inventory["spaces"][0]["tabs"].append({"id": "tab2", "name": "ended", "terminals": [
            {"pane": "p3", "terminal": "t3", "app": "codex"}]})
        for known, reply in ((True, {"address": "t3", "state": "finished", "status": None}),
                             (False, {"code": -32000, "data": {"kind": "not_found"}, "message": "no terminal"})):
            def run(argv, **kwargs):
                if argv[1:] == ["ls"]:
                    return subprocess.CompletedProcess(argv, 0, json.dumps(inventory), "")
                lines = []
                for request in map(json.loads, kwargs["input"].splitlines()):
                    if request["method"] == "terminal.get":
                        self.assertEqual(request["params"], {"terminal": "t3"})
                        lines.append({"id": request["id"], "result" if known else "error": reply})
                    elif request["method"] == "server.status":
                        lines.append({"id": request["id"], "result": {"pid": os.getpid(), "core": {"id": "core-1"}}})
                    else:
                        lines.append({"id": request["id"], "result": {"terminals": [
                            dict(t, address=a) for a, t in self.terminals().items()]}})
                return subprocess.CompletedProcess(argv, 0, "".join(json.dumps(line) + "\n" for line in lines), "")
            with self.subTest(known=known), patch.object(desktop.subprocess, "run", side_effect=run):
                if known:
                    world = desktop.read_world()
                    self.assertEqual([agent["pane_id"] for agent in world.view["agents"]], ["p1"])
                    self.assertEqual([tab["tab_id"] for tab in world.view["tabs"]], ["tab1", "tab2"])
                else:
                    with self.assertRaisesRegex(RuntimeError, "t3 changed during the read"):
                        desktop.read_world()

    def test_slow_or_malformed_desktop_reply_is_an_ordinary_read_error(self):
        def slow(argv, **kwargs):
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        for reply, message in ((slow, "timed out"), (self.core_replies("not json"), "unexpected reply"),
                               (self.core_replies('{"id": 1, "result": {}}'), "unexpected reply")):
            with self.subTest(message=message), patch.object(desktop.subprocess, "run", side_effect=reply), \
                    self.assertRaisesRegex(RuntimeError, message):
                desktop.read_world()
        no_pid = self.core_replies('{"id": 1, "result": {"pid": null, "core": {"id": "c"}}}')
        with patch.object(desktop.subprocess, "run", side_effect=no_pid), \
                self.assertRaisesRegex(RuntimeError, "identity unavailable"):
            desktop.server_identity(2)

    def test_desktop_tools_are_found_when_path_lacks_the_user_folders(self):
        with tempfile.TemporaryDirectory() as home:
            folder = Path(home) / ".local" / "bin"
            folder.mkdir(parents=True)
            tool = folder / ("hc.exe" if os.name == "nt" else "hc")
            tool.write_text("")
            tool.chmod(0o700)
            with patch.dict(os.environ, {"HOME": home, "USERPROFILE": home, "PATH": os.defpath}):
                self.assertEqual(Path(desktop.tool("hc")), tool)

    def test_config_is_data_and_never_runs_shell_substitutions(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "must-not-exist"
            path = Path(tmp) / "helper.conf"
            path.write_text(f'HELPER_MODEL="$(touch {marker})"\nHELPER_EFFORT="high"\n')
            with self.assertRaises(RuntimeError):
                desktop.read_config(path)
            self.assertFalse(marker.exists())

    def test_kata_opt_in_adds_only_desktop_monitor_instructions(self):
        with tempfile.TemporaryDirectory() as tmp:
            state, config = Path(tmp) / "state", Path(tmp) / "config"
            config.mkdir()
            with patch.dict(os.environ, {"LANTERN_DESKTOP_STATE_DIR": str(state),
                                         "LANTERN_CONFIG_DIR": str(config)}), \
                    patch.object(desktop, "helper_argv", side_effect=lambda conf, prompt: ["helper", prompt]), \
                    patch.object(desktop, "helper_command", return_value=("codex", "helper")), \
                    patch.object(desktop.os, "chdir"), patch.object(desktop.os, "execv") as execute:
                for project in ("", "example-project"):
                    (config / "helper.conf").write_bytes(
                        f'HELPER_KATA_PROJECT="{project}"\n'.encode() if project else b"")
                    desktop.chat()
                    prompt = execute.call_args.args[1][1]
                    settings = prompt.split("\nSaved settings: ", 1)[1].split("\n", 1)[0]
                    self.assertNotIn("HELPER_KATA_PROJECT", json.loads(settings))
                    monitor = (desktop.ROOT / "kata-monitor.md").read_text()
                    if project:
                        self.assertIn(monitor, prompt)
                        wake = json.loads(prompt.split("Kata project and wake target: ", 1)[1].splitlines()[0])
                        self.assertEqual(wake["project"], project)
                        target = wake["target"]
                        self.assertRegex(target, r"^lantern-[0-9a-f]{16}$")
                    else:
                        self.assertNotIn(monitor, prompt)
                        self.assertNotIn("Kata project and wake target", prompt)
                with patch.object(desktop, "run", return_value=json.dumps({"spaces": []})) as invoke, \
                        patch.object(desktop, "core", return_value={"terminals": []}), \
                        patch.object(desktop, "app", return_value={"terminal": "t1"}), \
                        patch.object(desktop, "ensure_views"):
                    desktop.open_home()
                label = next(arg for call in invoke.call_args_list for arg in call.args[0]
                             if arg.startswith("lantern.instance="))
                self.assertEqual(target, "lantern-" + label.split("=", 1)[1][:16])

    def test_replaced_live_home_releases_kata_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            state, config = Path(tmp) / "state", Path(tmp) / "config"
            state.mkdir()
            config.mkdir()
            (state / "home.json").write_text(json.dumps({"terminal": "t1", "helper_binary": "codex"}))
            def run(argv, **kwargs):
                return json.dumps({"spaces": []}) if argv == [desktop.APP, "ls"] else ""
            for labels in ({"herdr-kata.agent": "lantern-0123456789abcdef"}, {}):
                home = {"address": "t1", "state": "live", "foreground": {"name": "sh"}, "labels": labels}
                (state / "home.json").write_text(json.dumps({"terminal": "t1", "helper_binary": "codex"}))
                with self.subTest(labels=labels), \
                        patch.dict(os.environ, {"LANTERN_DESKTOP_STATE_DIR": str(state),
                                                "LANTERN_CONFIG_DIR": str(config)}), \
                        patch.object(desktop, "run", side_effect=run) as invoke, \
                        patch.object(desktop, "core", return_value={"terminals": [home]}), \
                        patch.object(desktop, "helper_argv", return_value=["codex"]), \
                        patch.object(desktop, "app", return_value={"terminal": "t2"}), \
                        patch.object(desktop, "ensure_views"):
                    desktop.open_home()
                clearing = next(call.args[0] for call in invoke.call_args_list
                                if call.args[0][:3] == [desktop.HC, "label", "t1"])
                if labels:
                    self.assertIn("herdr-kata.agent=", clearing)
                else:
                    self.assertNotIn("herdr-kata.agent=", clearing)

    def test_empty_helper_uses_an_installed_codex_and_retains_native_process_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            command = Path(tmp) / ("codex.exe" if os.name == "nt" else "codex")
            command.write_text("")
            command.chmod(0o700)
            with patch.dict(os.environ, {"PATH": tmp}):
                args = desktop.helper_argv({}, "native instructions")
            self.assertTrue(os.path.samefile(args[0], command))
            self.assertIn("--no-daemon", args)
            self.assertIn("--dangerously-bypass-approvals-and-sandbox", args)

    def test_claude_helper_retains_its_provider_permission_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            command = Path(tmp) / ("claude.exe" if os.name == "nt" else "claude")
            command.write_text("")
            command.chmod(0o700)
            with patch.dict(os.environ, {"PATH": tmp}):
                args = desktop.helper_argv({"HELPER_AGENT": "claude", "HELPER_PERMISSION": "accept-edits"}, "native")
                self.assertNotIn("--permission-mode", args)
                dangerous = desktop.helper_argv({"HELPER_AGENT": "claude", "HELPER_PERMISSION": "dangerous"}, "native")
                self.assertNotIn("bypassPermissions", dangerous)
                self.assertFalse(desktop.active_helper({"foreground": {"name": "zsh", "argv": ["zsh"]}},
                                                      {"HELPER_AGENT": "claude"}))
                self.assertTrue(desktop.active_helper({"foreground": {"name": "claude", "argv": ["claude"]}},
                                                     {"HELPER_AGENT": "claude"}))

    def test_claude_hooks_preserve_local_settings_and_explicit_settings_argument(self):
        with patch.object(desktop, "helper_command", return_value=("claude", "/bin/claude")):
            args = desktop.helper_argv({"HELPER_EXTRA_ARGS": "--settings /tmp/user.json --resume abc"}, "native")
        self.assertEqual(args.count("--settings"), 1)
        self.assertIn("/tmp/user.json", args)
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / ".claude").mkdir()
            path = work / ".claude/settings.local.json"
            original = {"permissions": {"defaultMode": "plan"}, "hooks": {
                "Stop": [{"hooks": [{"type": "command", "command": "echo preserved"}]}]}}
            path.write_text(json.dumps(original))
            desktop.install_claude_hooks(work)
            first = json.loads(path.read_text())
            desktop.install_claude_hooks(work)
            self.assertEqual(json.loads(path.read_text()), first)
            self.assertEqual(first["permissions"], original["permissions"])
            hooks = first["hooks"]
            self.assertEqual(set(hooks), {"SessionStart", "UserPromptSubmit", "PreToolUse", "Notification", "Stop"})
            self.assertEqual(hooks["Notification"][0]["matcher"], "permission_prompt|elicitation_dialog")
            self.assertEqual(hooks["Stop"][0], original["hooks"]["Stop"][0])
            self.assertTrue(hooks["Stop"][1]["hooks"][0]["command"].endswith(" done"))

    @unittest.skipIf(os.name == "nt", "POSIX terminal protocol")
    def test_hook_writes_status_to_agent_tty_when_all_hook_streams_are_pipes(self):
        import pty
        import select
        master, slave = pty.openpty()
        script = Path(desktop.__file__).with_name("agent_status.py")
        # A parent with a tty runs the hook with pipe-only streams, as Claude does.
        code = ("import os,subprocess,sys; os.setsid(); "
                ""
                "p=subprocess.run([sys.executable,sys.argv[1],sys.argv[2]],capture_output=True); "
                "sys.exit(p.returncode or bool(p.stdout) or bool(p.stderr))")
        try:
            for state in ("idle", "working", "blocked", "done"):
                env = dict(os.environ, HC_TERMINAL_ID="test-terminal")
                result = subprocess.run([sys.executable, "-c", code, str(script), state],
                                        stdin=slave, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        env=env, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertTrue(select.select([master], [], [], 2)[0])
                self.assertEqual(os.read(master, 1024),
                                 f"\x1b]7501;state={state}:app=claude-code\x07".encode())
            env.pop("HC_TERMINAL_ID")
            result = subprocess.run([sys.executable, "-c", code, str(script), "done"],
                                    stdin=slave, capture_output=True, env=env, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(select.select([master], [], [], 0.1)[0])
        finally:
            os.close(slave)
            os.close(master)

    def test_cursor_node_wrapper_is_still_the_active_helper(self):
        with tempfile.TemporaryDirectory() as tmp:
            command = Path(tmp) / ("agent.exe" if os.name == "nt" else "agent")
            command.write_text("")
            command.chmod(0o700)
            with patch.dict(os.environ, {"PATH": tmp}):
                self.assertTrue(desktop.active_helper({"foreground": {"name": "node", "argv": [
                    str(command), "--use-system-ca", "/app/index.js"]}}, {"HELPER_AGENT": "agent"}))
                self.assertFalse(desktop.active_helper({"foreground": {"name": "node", "argv": [
                    "node", "/unrelated/server.js"]}}, {"HELPER_AGENT": "agent"}))

    def test_remote_space_does_not_remove_local_agents(self):
        inventory = self.inventory()
        inventory["spaces"].append({"id": "remote/s1", "name": "remote api", "machine": "remote", "tabs": []})
        result = desktop.snapshot(inventory, self.terminals(), {})
        self.assertEqual([agent["pane_id"] for agent in result["agents"]], ["p1"])

    def test_exited_helper_does_not_reuse_the_initial_shell_grace(self):
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait(timeout=5)
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"LANTERN_DESKTOP_STATE_DIR": tmp}):
            Path(tmp, "chat-start.json").write_text(json.dumps({"terminal": "t-ended", "pid": child.pid, "start": "ended"}))
            terminal = {"address": "t-ended", "created_ms": int(time.time() * 1000)}
            self.assertFalse(desktop.starting(terminal))

    def test_native_process_retains_actual_arguments_and_fills_its_working_directory(self):
        cwd = str(Path.cwd())
        fg = {"pid": 3, "argv": ["/bin/zsh", "-ic", '\"$@\"; exec "$0" -i', "/bin/zsh", "grok", "--model", "grok-4.7"]}
        result = desktop.native_process({"foreground": fg, "cwd": cwd})
        self.assertEqual(result["argv"], ["grok", "--model", "grok-4.7"])
        self.assertEqual(result["cwd"], cwd)
        self.assertEqual(fg["argv"][0], "/bin/zsh")

    def test_stale_stock_readout_is_replaced_after_stop_confirmation(self):
        for role, case in (("field", "old stamp"), ("usage", "old stamp"), ("field", "missing stamp"),
                           ("field", "corrupt stamp"), ("field", "kill not found"), ("field", "kill timeout")):
            with self.subTest(role=role, case=case), self.readout_home() as home:
                address = home.stale(role)["address"]
                stamp = home.state / f"{role}-watcher.json"
                if case == "missing stamp":
                    stamp.unlink()
                elif case == "corrupt stamp":
                    stamp.write_text("[]")
                elif case == "kill not found":
                    home.kill_error = terminal_missing()
                elif case == "kill timeout":
                    home.kill_error = subprocess.TimeoutExpired("hc connect", 5)
                pane = home.record[role]["pane"]
                anchor, direction = ("p1", "right") if role == "field" else ("p2", "down")
                notices = desktop.ensure_views(home.record, home.state)
                self.assertEqual(home.mutations(), [
                    ("terminal.kill", {"terminal": address}), ("pane.close", {"pane": pane}),
                    ("pane.split", {"pane": anchor, "direction": direction,
                                    "argv": ["sh", str(desktop.ROOT / "bin" / "lantern-app"), role, str(home.state)]}),
                ])
                self.assertEqual(home.record[role], {"pane": "p4", "terminal": "t4"})
                self.assertIn(("label", ["t4", "lantern.desktop=" + role, "lantern.instance=instance"]), home.actions)
                self.assertEqual(notices, [])

    def test_current_starting_and_custom_watchers_keep_their_pane(self):
        custom = {"extra argv", "relative reader", "no receipt", "late relaunch", "early relaunch", "unreadable argv"}
        for case in ("current", "starting", *sorted(custom)):
            with self.subTest(case=case), self.readout_home() as home:
                target = home.stale("field") if case in custom else home.terminals["t2"]
                argv = target["foreground"]["argv"]
                if case == "starting":
                    target["created_ms"] = int(time.time() * 1000)
                    (home.state / "field-watcher.json").unlink()
                elif case == "extra argv":
                    argv[-1:-1] = ["--interval", "2"]
                elif case == "relative reader":
                    argv[5] = "bin/lantern-app-read"
                elif case == "no receipt":
                    (home.state / "field-start.json").unlink()
                elif case == "late relaunch":
                    os.utime(home.state / "field-start.json", None)
                elif case == "early relaunch":
                    home.live_argv[target["pid"]] = ["/bin/sh", "-i"]
                elif case == "unreadable argv":
                    del home.live_argv[target["pid"]]
                notices = desktop.ensure_views(home.record, home.state)
                self.assertEqual(home.mutations(), [])
                self.assertIn(("label", ["t2", "lantern.desktop=field", "lantern.instance=instance"]), home.actions)
                if case in custom:
                    self.assertIsInstance(notices, list)
                    self.assertEqual(len(notices), 1)
                    self.assertIn("t2", notices[0])
                else:
                    self.assertFalse(notices)

    def test_unconfirmed_stop_preserves_the_pane_across_open_attempts(self):
        error = "Outdated readout did not stop"
        for case in ("stays live", "same process marker", "replacement shell", "removed on retry", "live on retry"):
            with self.subTest(case=case), self.readout_home() as home:
                target = home.stale("field")
                address, pid = target["address"], target["foreground"]["pid"]
                if case == "replacement shell":
                    home.get_replies[address] = [dict(target, foreground={
                        "pid": target["pid"], "argv": ["/bin/sh", "-i"]})]
                    with self.assertRaisesRegex(RuntimeError, "Readout state changed"):
                        desktop.ensure_views(home.record, home.state)
                    self.assertEqual(home.mutations(), [])
                    continue
                if case in {"stays live", "same process marker"}:
                    home.exit_process = False
                    if case == "stays live":
                        home.kill_state = "live"
                    with self.assertRaisesRegex(RuntimeError, error):
                        desktop.ensure_views(home.record, home.state)
                    self.assertEqual(home.mutations(), [("terminal.kill", {"terminal": address})])
                    if case == "stays live":
                        continue
                    with self.assertRaisesRegex(RuntimeError, error):
                        desktop.ensure_views(self.saved_home(home), home.state)
                    self.assertEqual(home.mutations(), [("terminal.kill", {"terminal": address})])
                    del home.starts[pid]
                    home.get_replies[address] = [AssertionError("The saved finished result should suffice")]
                else:
                    home.record["field"]["stopping"] = {"pid": pid, "start": home.starts.pop(pid)}
                    (home.state / "home.json").write_text(json.dumps(home.record))
                    del home.terminals[address]
                    del home.panes["p2"]
                    if case == "live on retry":
                        home.get_replies[address] = [target]
                        with self.assertRaisesRegex(RuntimeError, error):
                            desktop.ensure_views(self.saved_home(home), home.state)
                        self.assertEqual(home.mutations(), [])
                        continue
                desktop.ensure_views(self.saved_home(home), home.state)
                self.assertEqual(home.panes, {"p1": "t1", "p3": "t3", "p4": "t4"})
                self.assertEqual(self.saved_home(home)["field"], {"pane": "p4", "terminal": "t4"})

    def saved_home(self, home):
        return json.loads((home.state / "home.json").read_text())

    def test_unreadable_code_fingerprint_does_not_trigger_a_restart(self):
        with self.readout_home() as home, \
                patch.object(lantern_views, "code_stamp", side_effect=PermissionError("fingerprint unreadable")):
            with self.assertRaises(OSError):
                desktop.ensure_views(home.record, home.state)
            self.assertEqual(home.mutations(), [])

    def test_open_home_returns_notices_for_new_and_reused_homes(self):
        notice = ["Field Status in t2 runs older Lantern code"]
        for reused in (False, True):
            with self.subTest(reused=reused), tempfile.TemporaryDirectory() as tmp:
                state = Path(tmp)
                (state / "home.json").write_text(json.dumps({"terminal": "t1", "helper_binary": "codex"}))
                terminals = [{"address": "t1", "state": "live", "foreground": {"name": "codex"}}] if reused else []
                with patch.dict(os.environ, {"LANTERN_DESKTOP_STATE_DIR": str(state),
                                             "LANTERN_CONFIG_DIR": str(state)}), \
                        patch.object(desktop, "run", return_value='{"spaces": []}'), \
                        patch.object(desktop, "core_calls", return_value=[{"terminals": terminals}]), \
                        patch.object(desktop, "app", return_value={"terminal": "t1"}), \
                        patch.object(desktop, "helper_argv", return_value=["codex"]), \
                        patch.object(desktop, "ensure_views", return_value=notice):
                    result = desktop.open_home()
                self.assertEqual(result["terminal"], "t1")
                self.assertIs(result["reused" if reused else "native"], True)
                self.assertIn("notices", result)
                self.assertEqual(result["notices"], notice)

    @unittest.skipUnless(sys.platform.startswith("linux") or sys.platform == "darwin", "Unix process argv")
    def test_live_process_argv_preserves_spaces_empty_and_non_ascii_arguments(self):
        import select

        argv = [sys.executable, "-c", "import time; print('ready', flush=True); time.sleep(30)", "a b", "", "ü"]
        with subprocess.Popen(argv, stdout=subprocess.PIPE, text=True) as child:
            try:
                self.assertTrue(select.select([child.stdout], [], [], 5)[0], "child did not become ready")
                self.assertEqual(child.stdout.readline(), "ready\n")
                self.assertEqual(desktop.process_argv(child.pid), argv)
            finally:
                child.terminate()
                child.wait(timeout=5)
        self.assertIsNone(desktop.process_argv(child.pid))


if __name__ == "__main__":
    unittest.main()
