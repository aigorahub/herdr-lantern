"""Desktop protocol and configuration boundaries. No live user processes."""
from __future__ import annotations

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


class DesktopTests(unittest.TestCase):
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
                    (config / "helper.conf").write_text(
                        f'HELPER_KATA_PROJECT="{project}"\n' if project else "")
                    desktop.chat()
                    prompt = execute.call_args.args[1][1]
                    settings = prompt.split("\nSaved settings: ", 1)[1].split("\n", 1)[0]
                    self.assertNotIn("HELPER_KATA_PROJECT", json.loads(settings))
                    if project:
                        wake = json.loads(prompt.split("Kata project and wake target: ", 1)[1].splitlines()[0])
                        self.assertEqual(wake["project"], project)
                        target = wake["target"]
                        self.assertRegex(target, r"^lantern-[0-9a-f]{16}$")
                        self.assertIn("herdr-kata.agent", prompt)
                    else:
                        expected = (desktop.ROOT / "desktop-prompt.md").read_text()
                        expected += "\nSaved settings: " + json.dumps({
                            k: v for k, v in desktop.read_config(config / "helper.conf").items()
                            if k not in {"HELPER_EXTRA_ARGS", "HELPER_KATA_PROJECT"}})
                        expected += f"\nModel routing: {desktop.ROOT / 'bin/model-route'}\nPreflight: {desktop.ROOT / 'bin/model-preflight'}\n"
                        self.assertEqual(prompt, expected)
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
            for labels in ({"herdr-kata.agent": "lantern-123"}, {}):
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
                expected = [desktop.HC, "label", "t1", "lantern.desktop=", "lantern.instance=", "lantern.helper="]
                if labels:
                    expected.append("herdr-kata.agent=")
                self.assertEqual(clearing, expected)

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


if __name__ == "__main__":
    unittest.main()
