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
