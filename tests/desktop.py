"""Desktop protocol and configuration boundaries. No live user processes."""
from __future__ import annotations

from contextlib import contextmanager, redirect_stdout
from itertools import count
from pathlib import Path
from types import SimpleNamespace
import io
import json
import os
import runpy
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bin"))
import desktop
import lantern_views
import system_resources


def terminal_missing():
    return desktop.CoreError({"data": {"kind": "not_found"}})


def desktop_platform(name):
    return patch.object(desktop, "os", SimpleNamespace(**{**vars(os), "name": name}))


@contextmanager
def wrapped_child(flags, interactive):
    shell, sleep = shutil.which("bash"), shutil.which("sleep")
    command = [shell, flags, '"$@"; exec "$0" ' + interactive, shell, sleep, "30"]
    with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, start_new_session=True) as parent:
        try:
            children = []
            deadline = time.monotonic() + 3
            while not children and time.monotonic() < deadline:
                children = [pid for pid in desktop.runtime.child_pids(parent.pid)
                            if desktop.process_argv(pid) == command[4:]]
                if not children:
                    time.sleep(0.02)
            child, = children
            yield command, parent.pid, child
        finally:
            try:
                os.killpg(parent.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


class ReadoutHome:
    def __init__(self, state, roles, platform):
        self.state = state
        self.record = {"terminal": "t1", "instance": "instance", "layout_ready": True}
        self.panes = {"p1": "t1"}
        self.terminals = {}
        self.starts = {}
        self.live_argv = {}
        self.get_replies = {}
        self.actions = []
        self.next_pane = count(5)
        self.kill_state = "finished"
        self.kill_error = None
        self.exit_process = True
        self.code = lantern_views.code_stamp(desktop.ROOT)
        for index, role in enumerate(roles, 2):
            pane, address = f"p{index}", f"t{index}"
            root, pid = 100 + index, 200 + index
            created = int(time.time() * 1000) - 60000
            script = {"field": "field_status.py", "usage": "plan_usage.py", "system": "system_resources.py"}[role]
            watcher = [sys.executable, str(desktop.ROOT / "bin" / script), "--state-dir", str(state)]
            if role != "system":
                watcher.extend(["--herdr", str(desktop.READER)])
            argv = ["/bin/sh", "-ic", '"$@"; exec "$0" -i', "/bin/sh", "sh",
                    str(desktop.ROOT / "bin" / "lantern-app"), role, str(state)]
            if platform == "nt":
                argv = [sys.executable, str(desktop.ROOT / "bin/desktop.py"), role, str(state)]
                foreground = argv
            else:
                foreground = [*watcher, "watch"]
            self.panes[pane] = address
            self.record[role] = {"pane": pane, "terminal": address}
            self.terminals[address] = {"address": address, "state": "live", "pid": root,
                                       "created_ms": created, "argv": argv, "cwd": str(state),
                                       "foreground": {"pid": pid, "argv": foreground}}
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
            self.actions.append((method, params))
            return [{}]
        raise AssertionError(method)

    def app(self, command, **params):
        self.actions.append((command, params))
        if command == "pane.close":
            del self.panes[params["pane"]]
        elif command == "pane.split":
            index = next(self.next_pane)
            created = {"pane": f"p{index}", "terminal": f"t{index}"}
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
    def readout_home(self, roles=("field", "usage", "system"), platform="posix"):
        with tempfile.TemporaryDirectory() as tmp, desktop_platform(platform):
            home = ReadoutHome(Path(tmp).resolve(), roles, platform)
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

    def test_desktop_prompt_includes_manager_rules_with_or_without_kata(self):
        with tempfile.TemporaryDirectory() as tmp:
            home, state = Path(tmp) / "home", Path(tmp) / "state"
            config = home / ".config/herdr/plugins/config/aigora.lantern"
            config.mkdir(parents=True)
            with desktop_platform("posix"), patch.dict(os.environ, {"LANTERN_DESKTOP_STATE_DIR": str(state)}), \
                    patch.object(Path, "home", return_value=home), \
                    patch.object(desktop, "helper_argv", side_effect=lambda conf, prompt: ["helper", prompt]), \
                    patch.object(desktop, "helper_command", return_value=("codex", "helper")), \
                    patch.object(desktop.os, "chdir"), patch.object(desktop.os, "execv") as execute:
                for project in ("", "example-project"):
                    os.environ.pop("LANTERN_CONFIG_DIR", None)
                    (config / "helper.conf").write_bytes(
                        f'HELPER_KATA_PROJECT="{project}"\n'.encode() if project else b"")
                    desktop.chat()
                    prompt = execute.call_args.args[1][1]
                    self.assertIn((desktop.ROOT / "guidance.md").read_text(encoding="utf-8"), prompt)
                    self.assertIn((desktop.ROOT / "manager-duty.md").read_text(encoding="utf-8"), prompt)
                    for team_contract in ('fallbacks', 'switch_below', '"permissions": "yolo"',
                                          'different pool', '--dangerously-skip-permissions',
                                          '--always-approve'):
                        self.assertIn(team_contract, prompt)
                    for peer_command in ('hc ls', 'hc get <terminal-id>', 'hc read <terminal-id>',
                                         'hc send <terminal-id>'):
                        self.assertIn(f'`{peer_command}`', prompt)
                    self.assertEqual(os.environ["LANTERN_CONFIG_DIR"], str(config.resolve()))
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

    def test_windows_install_adds_the_batch_launcher(self):
        with tempfile.TemporaryDirectory() as tmp, desktop_platform("nt"), \
                patch.object(Path, "home", return_value=Path(tmp)), \
                patch.object(sys, "argv", ["lantern-app", "install"]), redirect_stdout(io.StringIO()):
            self.assertEqual(desktop.main(), 0)
            launcher = Path(tmp) / ".local/bin/lantern-app.cmd"
            self.assertTrue(launcher.is_file())
            self.assertIn(b'lantern-app.cmd" %*\r\n', launcher.read_bytes())

    def test_windows_claude_hooks_quote_the_executable_for_cmd(self):
        executable = r"C:\Program Files\Python\python.exe"
        with tempfile.TemporaryDirectory() as tmp, desktop_platform("nt"), \
                patch.object(sys, "executable", executable):
            work = Path(tmp)
            desktop.install_claude_hooks(work)
            hooks = json.loads((work / ".claude/settings.local.json").read_text())["hooks"]
            command = hooks["Stop"][0]["hooks"][0]["command"]
            self.assertTrue(command.startswith('"' + executable + '" '), command)

    def test_windows_chat_waits_for_a_one_line_batch_helper_command(self):
        with tempfile.TemporaryDirectory() as tmp, desktop_platform("nt"):
            state = Path(tmp)
            binary = str(state / "Codex Shell.cmd")
            with patch.dict(os.environ, {"LANTERN_DESKTOP_STATE_DIR": tmp, "LANTERN_CONFIG_DIR": tmp,
                                         "COMSPEC": "fixture-cmd.exe"}), \
                    patch.object(desktop, "helper_command", return_value=("codex", binary)), \
                    patch.object(desktop.os, "chdir"), patch.object(desktop.os, "execv") as replace, \
                    patch.object(desktop.subprocess, "run",
                                 return_value=subprocess.CompletedProcess([], 7)) as run:
                self.assertEqual(desktop.chat(), 7)
                replace.assert_not_called()
                command, = run.call_args.args
                self.assertTrue(command.startswith('fixture-cmd.exe /d /s /c ""' + binary + '" '))
                self.assertNotIn("\n", command)
                self.assertTrue(os.environ["LANTERN_DESKTOP_COMMAND"].endswith("lantern-app.cmd"))
                self.assertIn("manager", (state / "workdir/AGENTS.md").read_text().lower())

    def test_windows_reopen_reuses_batch_helper_names(self):
        for suffix in (".cmd", ".bat"):
            with self.subTest(suffix=suffix), tempfile.TemporaryDirectory() as tmp, desktop_platform("nt"):
                state = Path(tmp)
                binary = str(state / ("codex" + suffix))
                (state / "home.json").write_text(json.dumps({"terminal": "t1", "helper_binary": binary}))
                term = {"address": "t1", "state": "live", "foreground": {"name": "codex.exe"}}
                with patch.dict(os.environ, {"LANTERN_DESKTOP_STATE_DIR": tmp, "LANTERN_CONFIG_DIR": tmp}), \
                        patch.object(desktop, "run", return_value='{"spaces": []}'), \
                        patch.object(desktop, "core", return_value={"terminals": [term]}), \
                        patch.object(desktop, "ensure_views", return_value=[]), \
                        patch.object(desktop, "helper_argv", return_value=[binary]), \
                        patch.object(desktop, "app", return_value={"terminal": "t2"}) as new_home:
                    self.assertTrue(desktop.open_home().get("reused"))
                    new_home.assert_not_called()

    def test_windows_readout_runs_in_the_process_named_in_its_receipt(self):
        with tempfile.TemporaryDirectory() as tmp, desktop_platform("nt"), patch.dict(os.environ), \
                patch.object(sys, "argv", ["lantern-app", "usage", tmp]), \
                patch.object(desktop, "READER", desktop.ROOT / "bin/lantern-app-read.cmd"), \
                patch.object(desktop.os, "execv") as replace:
            entered = []

            def watch(path, *, run_name):
                receipt = desktop.read_state(Path(tmp) / "usage-start.json")
                entered.append((path, run_name, receipt["pid"], sys.argv[-3:]))

            with patch.object(runpy, "run_path", side_effect=watch):
                self.assertEqual(desktop.main(), 0)
            self.assertEqual(entered, [(str(desktop.ROOT / "bin/plan_usage.py"), "__main__", os.getpid(),
                                        ["--herdr", str(desktop.READER), "watch"])])
            replace.assert_not_called()

    def test_windows_reopen_replaces_a_stale_stock_readout(self):
        with self.readout_home(platform="nt") as home:
            stale = home.stale("system")
            self.assertEqual(desktop.ensure_views(home.record, home.state), [])
            self.assertIn(("terminal.kill", {"terminal": stale["address"]}), home.mutations())
            argv, = [args["argv"] for method, args in home.mutations() if method == "pane.split"]
            self.assertEqual(argv, [sys.executable, str(desktop.ROOT / "bin/desktop.py"), "system", str(home.state)])

    @unittest.skipUnless(os.name == "nt", "Native Windows batch launch")
    def test_windows_cmd_launchers_execute(self):
        with tempfile.TemporaryDirectory(prefix="lantern smoke ") as tmp:
            state = Path(tmp)
            hc = state / "hc.cmd"
            reply = json.dumps({"id": 1, "result": {"pid": os.getpid(), "core": {"id": "windows-smoke"}}})
            hc.write_bytes(("@echo off\r\necho " + reply + "\r\n").encode("ascii"))
            env = dict(os.environ, USERPROFILE=str(state), LANTERN_HC_BIN=str(hc))
            comspec = env.get("COMSPEC", "cmd.exe")

            def execute(launcher, *args):
                script = desktop.ROOT / "bin" / launcher
                command = f'"{comspec}" /d /s /c ""{script}" {" ".join(args)}"'
                result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr)
                return result.stdout

            execute("lantern-app.cmd", "install")
            self.assertTrue((state / ".local/bin/lantern-app.cmd").is_file())
            result = json.loads(execute("lantern-app-read.cmd", "status", "--json"))
            self.assertEqual(result["server"]["id"], "windows-smoke")

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
        fg = {"pid": 3, "argv": ["grok", "--model", "grok-4.7"]}
        result = desktop.native_process({"foreground": fg, "cwd": cwd})
        self.assertEqual(result["argv"], ["grok", "--model", "grok-4.7"])
        self.assertEqual(result["cwd"], cwd)
        self.assertNotIn("cwd", fg)

    @unittest.skipUnless(sys.platform in {"darwin", "linux"}, "POSIX process arguments")
    def test_native_wrapper_uses_the_real_direct_child(self):
        for flags, interactive in (("-ic", "-i"), ("-lic", "-li")):
            with self.subTest(flags=flags), wrapped_child(flags, interactive) as (command, parent, child):
                fg = {"pid": parent, "argv": command}
                result = desktop.native_process({"foreground": fg, "cwd": str(Path.cwd())})
                self.assertEqual((result["pid"], result["argv"]), (child, command[4:]))
                self.assertEqual(fg["argv"], command)

    def test_desktop_app_aliases_and_omp_reap_match_the_agent(self):
        cwd = str(Path.cwd())
        for app, alias in (("agent", "cursor"), ("cursor-agent", "cursor"), ("oh-my-pi", "omp"), ("omp", "omp")):
            with self.subTest(app=app):
                terminals = self.terminals()
                terminals["t1"].update(pid=100, cwd=cwd, status={"app": app, "state": "done"},
                                       foreground={"pid": 101, "argv": [app]})
                view = desktop.snapshot(self.inventory(), terminals, {})
                self.assertEqual(view["agents"][0]["agent"], alias)
                world = desktop.World({}, view, terminals)
                field = desktop.field.Field([], view["agents"], "", "", world)
                self.assertTrue(desktop.field.foreground_matches(field, str(desktop.READER), "p1", 2))

    @unittest.skipUnless(sys.platform in {"darwin", "linux"}, "POSIX read-model launcher")
    def test_field_readout_and_reader_use_the_desktop_protocol(self):
        with tempfile.TemporaryDirectory() as tmp, wrapped_child("-lic", "-li") as (command, parent, child):
            root = Path(tmp)
            fixture = root / "world.json"
            stub = root / "stub.py"
            terminal = {"address": "t1", "pid": parent, "state": "live", "cwd": str(root),
                        "status": {"app": "cursor-agent", "state": "working"},
                        "foreground": {"pid": parent, "name": "bash", "argv": command}}
            inventory = {"spaces": [{"id": "s1", "name": "Local", "tabs": [
                {"id": "tab1", "name": "Cursor work", "terminals": [{"pane": "p1", "terminal": "t1"}]}]}]}
            fixture.write_text(json.dumps({"inventory": inventory, "server": {"pid": os.getpid(),
                                           "core": {"id": "fixture"}}, "terminals": [terminal]}))
            stub.write_text("""import json, sys
from pathlib import Path
data = json.loads(Path(sys.argv[1]).read_text())
if sys.argv[2:] == ["app", "ls"]:
    print(json.dumps(data["inventory"]))
else:
    assert sys.argv[2:] == ["core", "connect"]
    for line in sys.stdin:
        request = json.loads(line)
        result = {"server.status": data["server"], "terminal.list": {"terminals": data["terminals"]}}
        print(json.dumps({"id": request["id"], "result": result[request["method"]]}))
""")
            for name, mode in (("herdr-app", "app"), ("hc", "core")):
                launcher = root / name
                launcher.write_text('#!/bin/sh\nexec "$FIXTURE_PY" "$FIXTURE_STUB" "$FIXTURE_WORLD" ' +
                                    mode + ' "$@"\n')
                launcher.chmod(0o700)
            env = dict(os.environ, LANTERN_HC_BIN=str(root / "hc"), LANTERN_APP_BIN=str(root / "herdr-app"),
                       FIXTURE_PY=sys.executable, FIXTURE_STUB=str(stub), FIXTURE_WORLD=str(fixture))
            state = root / "state"
            with self.subTest(command="refresh"):
                reply = subprocess.run([sys.executable, str(desktop.ROOT / "bin/field_status.py"),
                                        "--state-dir", str(state), "--herdr", str(desktop.READER),
                                        "--plain", "refresh"], env=env, capture_output=True, text=True, timeout=10)
                self.assertEqual(reply.returncode, 0, reply.stderr)
                rows = json.loads((state / "field-status-rows.json").read_text())["rows"]
                self.assertEqual([(row["pane_id"], row["agent"]) for row in rows], [("p1", "cursor")])
            with self.subTest(command="process-info"):
                reply = subprocess.run([str(desktop.READER), "pane", "process-info", "--pane", "p1"],
                                       env=env, capture_output=True, text=True, timeout=10)
                self.assertEqual(reply.returncode, 0, reply.stderr)
                process, = json.loads(reply.stdout)["result"]["process_info"]["foreground_processes"]
                self.assertEqual((process["pid"], process["argv"]), (child, command[4:]))

    def test_native_wrapper_keeps_the_shell_when_the_child_is_ambiguous_or_missing(self):
        launch = ["/bin/zsh", "-ic", '"$@"; exec "$0" -i', "/bin/zsh", "codex"]
        foreground = {"pid": 101, "argv": launch, "cwd": str(Path.cwd())}
        for children in (set(), {102, 103}):
            with self.subTest(children=children), patch.object(desktop.runtime, "child_pids", return_value=children), \
                    patch.object(desktop.runtime, "process_start", return_value="live"), \
                    patch.object(desktop, "process_argv", return_value=["codex"]):
                self.assertEqual(desktop.native_process({"foreground": foreground}), foreground)

    def test_stale_stock_readout_launched_by_login_shell_is_replaced(self):
        with self.readout_home() as home:
            stale = home.stale("field")
            stale["argv"][1:3] = ["-lic", '"$@"; exec "$0" -li']
            self.assertEqual(desktop.ensure_views(home.record, home.state), [])
            self.assertIn(("terminal.kill", {"terminal": stale["address"]}), home.mutations())

    def test_stale_stock_readout_is_replaced_after_stop_confirmation(self):
        for role, case in (("field", "old stamp"), ("usage", "old stamp"), ("system", "old stamp"), ("field", "missing stamp"),
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
                self.assertEqual(home.record[role], {"pane": "p5", "terminal": "t5"})
                self.assertIn(("label", ["t5", "lantern.desktop=" + role, "lantern.instance=instance"]), home.actions)
                self.assertEqual(notices, [])

    def test_new_home_and_two_readout_home_get_three_equal_readouts(self):
        for existing in ((), ("field", "usage")):
            with self.subTest(existing=existing), self.readout_home(existing) as home:
                desktop.ensure_views(home.record, home.state)
                launches = [args["argv"][2] for method, args in home.mutations() if method == "pane.split"]
                self.assertEqual(launches, ["system"] if existing else ["field", "system", "usage"])
                self.assertTrue(all(method == "pane.split" for method, _ in home.mutations()))
                params, = [args for method, args in home.actions if method == "doc.set"]
                layout = params["value"]["spaces"][0]["tabs"][0]["root"]
                self.assertEqual(layout["type"], "row")
                chat, right = layout["children"]
                self.assertEqual(chat["content"]["terminal"], "t1")
                self.assertEqual(right["type"], "column")
                self.assertEqual([child["content"]["terminal"] for child in right["children"]],
                                 [home.record[role]["terminal"] for role in ("field", "system", "usage")])
                self.assertEqual([child["size"] for child in right["children"]], [{"weight": 1}] * 3)

    def test_upgrade_with_an_extra_pane_focuses_home_without_changing_layout(self):
        with self.readout_home(("field", "usage")) as home:
            home.panes["p9"] = "t9"
            notices = desktop.ensure_views(home.record, home.state)
            self.assertTrue(any("extra panes" in notice for notice in notices))
            self.assertEqual(home.mutations(), [])
            self.assertFalse(any(method == "doc.set" for method, _ in home.actions))
            self.assertIn(("pane.focus", {"pane": "p1"}), home.actions)

    def test_memory_ranks_nested_agent_groups_and_standalone_processes_once(self):
        start = "Sat Nov 28 00:00:00 2026"
        processes = system_resources.parse_processes(f"""
1 0 100 {start} launchd
20 1 10 {start} /bin/zsh
21 20 501 {start} /usr/bin/codex
22 21 200 {start} /usr/bin/worker
30 21 50 {start} /bin/zsh
31 30 600 {start} subprocess
32 31 100 {start} subprocess
40 1 748 {start} /Applications/Example App
41 1 120 {start} browser
42 1 90 {start} editor
bad process information
""")
        location = "Bookstore / Checkout migration for the new guest payments workflow"
        terminals = [
            {"pid": 20, "address": "t411", "status": {"app": "codex"}, "state": "live",
             "labels": {"where": location}},
            {"pid": 30, "address": "t2", "status": {"app": "codex"}, "state": "live", "name": "review"},
        ]
        with patch.object(desktop, "core", return_value={"terminals": terminals}):
            roots = system_resources.agent_roots()
        ranked = system_resources.ranked_consumers(processes, roots)
        self.assertEqual([(row.kib, system_resources.consumer_label(row, {})) for row in ranked[:5]],
                         [(750, "review (t2)"), (748, "Example App (40)"), (711, location),
                          (120, "browser (41)"), (100, "launchd (1)")])

    def test_node_worker_shows_the_package_and_project_without_exposing_arguments(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "bookstore"
            script = project / "node_modules" / "jest-worker" / "build" / "processChild.js"
            script.parent.mkdir(parents=True)
            script.touch()
            row = system_resources.Consumer(55, "start", 80000, "node", False)
            for arguments, expected in (
                (["--require", "loader.js", str(script), "--token=private"], "jest-worker · bookstore (55)"),
                (["--eval=private", str(script)], "node · bookstore (55)"),
            ):
                with patch.object(desktop, "process_argv", return_value=["node", *arguments]):
                    self.assertEqual(system_resources.consumer_label(row, {55: project}), expected)

    def test_memory_history_ages_out_old_samples_and_ignores_reused_pids(self):
        history = system_resources.MemoryHistory()
        before = [system_resources.Consumer(40, "start-a", 102400, "node", False),
                  system_resources.Consumer(50, "start-b", 80000, "python", False)]
        later = [system_resources.Consumer(40, "start-a", 153600, "node", False),
                 system_resources.Consumer(50, "start-c", 200000, "python", False)]
        self.assertEqual(history.changes(0, before), {})
        self.assertEqual(history.changes(300, later), {later[0].key: system_resources.Change(51200, 300)})
        self.assertEqual(history.changes(3601, later),
                         {row.key: system_resources.Change(0, 3301) for row in later})

    def test_memory_history_tracks_growth_of_a_process_started_after_the_first_sample(self):
        history = system_resources.MemoryHistory()
        other = system_resources.Consumer(20, "start-a", 80000, "python", False)
        created = system_resources.Consumer(50, "start-b", 65536, "node", False)
        grown = system_resources.Consumer(50, "start-b", 262144, "node", False)
        history.changes(0, [other])
        history.changes(120, [other, created])
        changes = history.changes(240, [other, grown])
        self.assertEqual(changes[grown.key], system_resources.Change(196608, 120))

    def test_memory_growth_rate_distinguishes_sixty_from_a_hundred_nineteen_seconds(self):
        history = system_resources.MemoryHistory()
        slow = system_resources.Consumer(7, "start-a", 65536, "node", False)
        fast = system_resources.Consumer(8, "start-b", 65536, "python", False)
        history.changes(1, [slow])
        history.changes(60, [slow, fast])
        changes = history.changes(120, [slow._replace(kib=101376), fast._replace(kib=100352)])
        slow_kib, slow_elapsed = changes[slow.key]
        fast_kib, fast_elapsed = changes[fast.key]
        self.assertLess(slow_kib / slow_elapsed, fast_kib / fast_elapsed)

    def test_disk_readout_omits_an_unmounted_aigora_work_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            placeholder = Path(tmp) / "Aigora Work"
            placeholder.mkdir()
            self.assertFalse(any("Aigora Work" in line for line in system_resources.disk_lines(placeholder)))

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
                self.assertEqual(home.panes, {"p1": "t1", "p3": "t3", "p4": "t4", "p5": "t5"})
                self.assertEqual(self.saved_home(home)["field"], {"pane": "p5", "terminal": "t5"})

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

        argv = ["/bin/sh", "-c", "echo ready; read x", "a b", "", "ü"]
        with subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True) as child:
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
