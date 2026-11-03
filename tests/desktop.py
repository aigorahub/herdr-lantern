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

    def test_config_is_data_and_never_runs_shell_substitutions(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "must-not-exist"
            path = Path(tmp) / "helper.conf"
            path.write_text(f'HELPER_MODEL="$(touch {marker})"\nHELPER_EFFORT="high"\n')
            with self.assertRaises(RuntimeError):
                desktop.read_config(path)
            self.assertFalse(marker.exists())

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

    def test_claude_launch_registers_status_hooks_without_replacing_extra_settings(self):
        with patch.object(desktop, "helper_command", return_value=("claude", "/bin/claude")):
            args = desktop.helper_argv({"HELPER_EXTRA_ARGS": "--settings /tmp/user.json --resume abc"}, "native")
        settings = [args[i + 1] for i, arg in enumerate(args) if arg == "--settings"]
        self.assertEqual(settings[0], "/tmp/user.json")
        hooks = json.loads(settings[1])["hooks"]
        self.assertEqual(set(hooks), {"SessionStart", "UserPromptSubmit", "PreToolUse", "Notification", "Stop"})
        self.assertEqual(hooks["Notification"][0]["matcher"], "permission_prompt|elicitation_dialog")
        self.assertTrue(hooks["Stop"][0]["hooks"][0]["command"].endswith(" done"))
        self.assertIn("--resume", args)

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
