"""Field Status lifecycle, persistence, and display regression tests."""
from __future__ import annotations

import importlib.util
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

BIN = Path(__file__).resolve().parents[1] / "bin"
spec = importlib.util.spec_from_file_location("field_status", BIN / "field_status.py")
status = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = status
spec.loader.exec_module(status)

NOW = datetime(2026, 9, 24, 16, 0, tzinfo=timezone.utc)


def field(agent_status="working", include_done=True):
    tabs = [
        {"tab_id": "home", "workspace_id": "w1", "label": "Lantern Home", "agent_status": "working"},
        {"tab_id": "daily", "workspace_id": "w2", "label": "daily-tasks · codex", "agent_status": "idle"},
    ]
    agents = [
        {"tab_id": "home", "pane_id": "w1:p1", "agent": "codex", "name": "lantern", "agent_status": "working"},
        {"tab_id": "daily", "pane_id": "w2:p1", "agent": "codex", "name": "daily-tasks", "agent_status": "idle"},
    ]
    if include_done:
        tabs.append({"tab_id": "review", "workspace_id": "w3", "label": "Review", "agent_status": agent_status})
        agents.append({"tab_id": "review", "pane_id": "w3:p1", "agent": "codex", "name": "Sol Reviewer", "agent_status": agent_status})
    workspaces = [
        {"workspace_id": "w1", "label": "🔥 lantern"},
        {"workspace_id": "w2", "label": "Daily-Tasks"},
        {"workspace_id": "w3", "label": "Plugin Update"},
    ]
    return tabs, agents, workspaces


class FieldStatusTests(unittest.TestCase):
    def test_keep_shows_only_lantern_by_default(self):
        rows = status.rows_for(*field("idle"))
        self.assertEqual([row["agent"] for row in rows], ["lantern", "daily-tasks", "Sol Reviewer"])
        output = status.render(rows, {"important": {}, "needs_you": {}}, NOW, False)
        self.assertIn("KEEP\n• Lantern", output)
        self.assertNotIn("Daily-Tasks", output)
        self.assertNotIn("Plugin Update", output)
        self.assertNotIn("shell", output)
        self.assertNotIn("codex  ", output)
        self.assertNotIn("Closed", output)

    def test_closed_done_disappears_immediately(self):
        done = status.rows_for(*field("done"))
        closed = status.reconcile(status.rows_for(*field(include_done=False)), {"rows": done}, NOW)
        self.assertEqual(len(closed), 2)
        self.assertNotIn("Plugin Update", status.render(closed, {}, NOW, False))

    def test_idle_or_missing_working_agent_never_becomes_closed_done(self):
        idle = status.rows_for(*field("idle"))
        working = status.rows_for(*field("working"))
        live = status.rows_for(*field(include_done=False))
        self.assertEqual(len(status.reconcile(live, {"rows": idle}, NOW)), 2)
        self.assertEqual(len(status.reconcile(live, {"rows": working}, NOW)), 2)

    def test_done_agent_exiting_but_tab_remains_is_not_ghosted(self):
        tabs, agents, workspaces = field("done")
        previous = status.rows_for(tabs, agents, workspaces)
        current = status.rows_for(tabs, agents[:2], workspaces)
        updated = status.reconcile(current, {"rows": previous}, NOW)
        self.assertEqual(len(updated), 3)
        self.assertEqual([row["raw_status"] for row in updated if row["tab_id"] == "review"], ["idle"])
        self.assertNotIn("Plugin Update", status.render(updated, {}, NOW, False))

    def test_multiple_agents_in_one_tab_each_get_a_row(self):
        tabs, agents, workspaces = field("working")
        agents.append({"tab_id": "review", "pane_id": "w3:p2", "agent": "claude", "agent_status": "done"})
        rows = status.rows_for(tabs, agents, workspaces)
        self.assertEqual(len(rows), 4)
        self.assertIn("claude · w3:p2", [row["agent"] for row in rows])
        self.assertEqual([row["raw_status"] for row in rows if row["tab_id"] == "review"], ["working", "done"])

    def test_sections_names_and_colors_and_et(self):
        rows = status.rows_for(*field("done"))
        note_data = {"important": {"sol": "Independent Sol High review pending"},
                     "needs_you": {"decision": "Choose whether to merge PR 42"}}
        output = status.render(rows, note_data, NOW, True)
        self.assertIn("12:00 PM ET", output)
        self.assertIn("\x1b[31mIMPORTANT\x1b[0m\n• Independent Sol High review pending", output)
        self.assertIn("\x1b[35mNEEDS YOU\x1b[0m\n• Choose whether to merge PR 42", output)
        self.assertIn("\x1b[33mLantern\x1b[0m", output)
        self.assertIn("\x1b[32mDONE\x1b[0m", output)
        self.assertIn("\x1b[34mKEEP\x1b[0m", output)
        self.assertNotIn("REVIEW GATES", output)
        self.assertNotIn("Sol Reviewer", output)
        self.assertIn("Outcome not yet verified", output)

    def test_control_text_cannot_spoof_rows(self):
        tabs, agents, workspaces = field()
        tabs[2]["label"] = "Review\x1b[2J\nIMPORTANT"
        rows = status.rows_for(tabs, agents, workspaces)
        output = status.render(rows, {}, NOW, False)
        self.assertNotIn("\x1b", output)
        self.assertIn("Review [2J IMPORTANT", output)

    def test_narrow_side_pane_keeps_name_status_and_action_legible(self):
        rows = status.rows_for(*field("done"))
        output = status.render(rows, {"needs_you": {"x": "Choose whether to merge the reviewed update"},
                                      "important": {}}, NOW, False, width=38)
        self.assertLessEqual(len(output.splitlines()[0]), 38)
        self.assertIn("ET", output.splitlines()[0])
        self.assertIn("DONE\n• Plugin Update\n  Review\n  Outcome not yet verified", output)
        self.assertIn("• Choose whether to merge", output)
        self.assertIn("  update", output)

    def test_unavailable_snapshot_does_not_erase_prior_rows(self):
        with tempfile.TemporaryDirectory() as root:
            state_dir = Path(root)
            data = {"schema": 1, "rows": status.rows_for(*field("done"))}
            path = state_dir / "field-status-rows.json"
            status.write_json(path, data)
            with patch.object(status, "snapshot", side_effect=RuntimeError("offline")):
                with self.assertRaises(RuntimeError):
                    status.refresh(state_dir, "herdr", 1, NOW, False)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), data)

    def test_refresh_keeps_notes_and_removes_closed_agent(self):
        with tempfile.TemporaryDirectory() as root:
            state_dir = Path(root)
            status.write_json(state_dir / "field-status-notes.json", {
                "needs_you": {"x": "Approve release window"},
                "review_gates": {"y": "Sol High review pending"},
            })
            with patch.object(status, "snapshot", return_value=field("done")):
                status.refresh(state_dir, "herdr", 1, NOW, False)
            with patch.object(status, "snapshot", return_value=field(include_done=False)):
                output = status.refresh(state_dir, "herdr", 1, NOW + timedelta(minutes=1), False)
                self.assertNotIn("Plugin Update", output)
                self.assertIn("Approve release window", output)
                self.assertIn("Sol High review pending", output)
                self.assertIn("IMPORTANT\n• Sol High review pending", output)
                self.assertNotIn("REVIEW GATES", output)
                output = status.refresh(state_dir, "herdr", 1, NOW + timedelta(minutes=16), False)
                self.assertNotIn("Plugin Update", output)

    def test_pane_opens_once_and_reuses_without_closing_home(self):
        with tempfile.TemporaryDirectory() as root:
            state_dir = Path(root)
            calls = []
            panes = [{"pane_id": "w1:p1", "tab_id": "w1:t1", "agent": "codex"}]

            def fake_control(_binary, args, _timeout):
                calls.append(args)
                if args[:2] == ["pane", "list"]:
                    return {"panes": panes}
                if args[:2] == ["pane", "split"]:
                    panes.append({"pane_id": "w1:p2", "tab_id": "w1:t1"})
                    return {"pane": {"pane_id": "w1:p2"}}
                if args[:2] == ["pane", "process-info"]:
                    return {"process_info": {"foreground_processes": [
                        {"cmdline": "python field_status.py watch", "pid": 77}], "shell_pid": 1}}
                return {}

            env = {"HERDR_ENV": "1", "HERDR_PANE_ID": "w1:p1", "HERDR_TAB_ID": "w1:t1", "HERDR_WORKSPACE_ID": "w1", "LANTERN_HOME_PANE_ID": "w1:p1"}
            with patch.dict(status.os.environ, env), patch.object(status, "control", side_effect=fake_control):
                self.assertEqual(status.open_pane(state_dir, BIN.parent, "herdr", 1), ("w1:p2", True))
                self.assertEqual(status.open_pane(state_dir, BIN.parent, "herdr", 1), ("w1:p2", False))
            self.assertEqual(sum(args[:2] == ["pane", "split"] for args in calls), 1)
            self.assertEqual(sum(args[:2] == ["pane", "run"] for args in calls), 1)
            self.assertFalse(any("close" in args for args in calls))
            self.assertIn("watch", next(args[-1] for args in calls if args[:2] == ["pane", "run"]))

    def test_pane_mutations_use_existing_herdr_gate(self):
        seen = []

        def fake_run(command, **kwargs):
            seen.append((command, kwargs["env"].get("HERDR_HELPER_OK")))
            return subprocess.CompletedProcess(command, 0, '{"result":{}}', "")

        # smoke.sh may inherit the Herdr gate from an earlier shell fixture.
        with patch.dict(status.os.environ, {"HERDR_HELPER_OK": ""}), \
                patch.object(status.subprocess, "run", side_effect=fake_run):
            status.control("herdr", ["pane", "list", "--workspace", "w1"], 1)
            status.control("herdr", ["pane", "split", "--pane", "w1:p1"], 1)
        self.assertNotEqual(seen[0][1], "1")
        self.assertEqual(seen[1][1], "1")

    def test_watcher_command_quotes_windows_user_path_for_sh(self):
        path = Path("C:/Users/O'Brien/Lantern")
        self.assertEqual(shlex.split(status.shell_quote(path)), [path.as_posix()])

    def test_windows_watcher_command_is_powershell(self):
        command = status.watcher_command(
            Path("C:/Program Files/Lantern"),
            Path("C:/Users/O'Brien/state"),
            shell="powershell",
            python=r"C:\Program Files\Python\python.exe",
        )
        self.assertTrue(command.startswith("& "))
        self.assertIn("'C:\\Program Files\\Python\\python.exe'", command)
        self.assertIn("'C:\\Program Files\\Lantern\\bin\\field_status.py'", command)
        self.assertIn("'C:\\Users\\O''Brien\\state'", command)
        self.assertNotIn("'\"'\"'", command)
        self.assertNotIn(" sh ", command)

    def test_recovered_label_checks_occupancy_before_watcher(self):
        with tempfile.TemporaryDirectory() as root:
            state_dir = Path(root)
            calls = []
            occupied = True

            def fake_control(_binary, args, _timeout):
                calls.append(args)
                if args[:2] == ["pane", "list"]:
                    return {"panes": [
                        {"pane_id": "w1:p1", "tab_id": "w1:t1", "agent": "codex"},
                        {"pane_id": "w1:p2", "tab_id": "w1:t1", "label": "Field Status"},
                    ]}
                if args[:2] == ["pane", "process-info"]:
                    processes = [{"pid": 2, "cmdline": "other job"}] if occupied else []
                    return {"process_info": {"shell_pid": 1, "foreground_processes": processes}}
                return {}

            env = {"HERDR_ENV": "1", "HERDR_PANE_ID": "w1:p1", "HERDR_TAB_ID": "w1:t1",
                   "HERDR_WORKSPACE_ID": "w1", "LANTERN_HOME_PANE_ID": "w1:p1"}
            with patch.dict(status.os.environ, env), patch.object(status, "control", side_effect=fake_control):
                with self.assertRaisesRegex(RuntimeError, "occupied"):
                    status.open_pane(state_dir, BIN.parent, "herdr", 1)
                occupied = False
                self.assertEqual(status.open_pane(state_dir, BIN.parent, "herdr", 1), ("w1:p2", False))
            self.assertEqual(sum(args[:2] == ["pane", "run"] for args in calls), 1)
            self.assertFalse(any(args[:2] == ["pane", "split"] for args in calls))

    @unittest.skipUnless(os.name == "nt", "requires native Windows Python")
    def test_native_windows_refresh_and_watch_use_launcher_wrapper(self):
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            fake_dir = root_path / "fake-bin"
            fake_dir.mkdir()
            fake_herdr = fake_dir / "herdr"
            shutil.copyfile(Path(__file__).parent / "fixtures" / "field_status_herdr", fake_herdr)
            fake_herdr.chmod(0o755)
            env = os.environ.copy()
            env.pop("HERDR_REAL", None)  # launch.sh removes this before entering the pane.
            # Git Bash exports the launch.sh POSIX wrapper path in this native form.
            env["HERDR_BIN_PATH"] = (BIN / "herdr").as_posix()
            env["LANTERN_HERD_STATE_DIR"] = str(root_path / "state")
            env["PATH"] = os.pathsep.join((str(BIN), str(fake_dir), env["PATH"]))
            with patch.dict(os.environ, env, clear=True):
                self.assertEqual(Path(status.resolve_herdr("")).resolve(),
                                 (BIN / "herdr").resolve())
            script = str(BIN / "field_status.py")
            base = [sys.executable, script, "--plain"]
            refreshed = subprocess.run([*base, "refresh"], env=env, capture_output=True,
                                       text=True, timeout=15, check=False)
            self.assertEqual(refreshed.returncode, 0, refreshed.stderr)
            self.assertIn("Fixture Lantern Home", refreshed.stdout)
            self.assertIn("IN MOTION", refreshed.stdout)
            self.assertIn(" ET", refreshed.stdout)
            watcher = subprocess.Popen([*base, "watch", "--interval", "0.1"],
                                       env=env, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, text=True)
            lines = []
            ready = threading.Event()

            def collect() -> None:
                assert watcher.stdout is not None
                for line in watcher.stdout:
                    lines.append(line)
                    if "Fixture Lantern Home" in line:
                        ready.set()

            reader = threading.Thread(target=collect, daemon=True)
            reader.start()
            try:
                self.assertTrue(ready.wait(20), f"watch produced no field frame: {lines}")
                self.assertIsNone(watcher.poll(), "watch exited after its first refresh")
            finally:
                if watcher.poll() is None:
                    watcher.terminate()
                watcher.wait(timeout=5)
                reader.join(timeout=5)
                if watcher.stdout is not None:
                    watcher.stdout.close()
                if watcher.stderr is not None:
                    watcher.stderr.close()
            output = "".join(lines)
            self.assertIn("Fixture Lantern Home", output)
            self.assertIn("IN MOTION", output)
            self.assertNotIn("unavailable", output)

    def test_reused_pane_restarts_idle_watcher_but_not_other_process(self):
        with tempfile.TemporaryDirectory() as root:
            state_dir = Path(root)
            status.write_json(state_dir / "field-status-pane.json", {
                "home_pane_id": "w1:p1", "pane_id": "w1:p2", "tab_id": "w1:t1"})
            calls = []
            occupied = False

            def fake_control(_binary, args, _timeout):
                calls.append(args)
                if args[:2] == ["pane", "list"]:
                    return {"panes": [{"pane_id": "w1:p1", "tab_id": "w1:t1", "agent": "codex"},
                                      {"pane_id": "w1:p2", "tab_id": "w1:t1"}]}
                if args[:2] == ["pane", "process-info"]:
                    return {"process_info": {"shell_pid": 1, "foreground_processes":
                                             [{"pid": 2, "cmdline": "other job"}] if occupied else []}}
                return {}

            env = {"HERDR_ENV": "1", "HERDR_PANE_ID": "w1:p1", "HERDR_TAB_ID": "w1:t1", "HERDR_WORKSPACE_ID": "w1", "LANTERN_HOME_PANE_ID": "w1:p1"}
            with patch.dict(status.os.environ, env), patch.object(status, "control", side_effect=fake_control):
                self.assertEqual(status.open_pane(state_dir, BIN.parent, "herdr", 1), ("w1:p2", False))
                occupied = True
                with self.assertRaisesRegex(RuntimeError, "occupied"):
                    status.open_pane(state_dir, BIN.parent, "herdr", 1)
            self.assertEqual(sum(args[:2] == ["pane", "run"] for args in calls), 1)

    def test_note_cli_persists_and_clears_action_separately_from_important(self):
        with tempfile.TemporaryDirectory() as root:
            script = str(BIN / "field_status.py")
            for command in (
                ["note", "needs-you", "set", "decision", "Choose the release date"],
                ["note", "important", "set", "review", "Sol High review pending"],
                ["note", "needs-you", "clear", "decision"],
            ):
                proc = subprocess.run([sys.executable, script, "--state-dir", root, *command],
                                      capture_output=True, text=True, check=False)
                self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(status.notes(Path(root)), {
                "needs_you": {}, "important": {"review": "Sol High review pending"},
                "keep": {}, "done": {}})

    def test_legacy_review_gate_migrates_into_important_on_note_write(self):
        with tempfile.TemporaryDirectory() as root:
            state_dir = Path(root)
            path = state_dir / "field-status-notes.json"
            status.write_json(path, {"review_gates": {"old": "Review still pending"}, "needs_you": {}})
            self.assertEqual(status.notes(state_dir)["important"], {"old": "Review still pending"})
            proc = subprocess.run([sys.executable, str(BIN / "field_status.py"), "--state-dir", root,
                                   "note", "important", "clear", "old"], capture_output=True, text=True, check=False)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(status.notes(state_dir)["important"], {})
            self.assertNotIn("review_gates", json.loads(path.read_text(encoding="utf-8")))

    def test_legacy_review_gate_command_updates_important(self):
        with tempfile.TemporaryDirectory() as root:
            proc = subprocess.run([sys.executable, str(BIN / "field_status.py"), "--state-dir", root,
                                   "note", "review-gate", "set", "review", "Review pending"],
                                  capture_output=True, text=True, check=False)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(status.notes(Path(root))["important"], {"review": "Review pending"})

    def test_shell_tab_only_appears_when_explicitly_kept(self):
        tabs, agents, workspaces = field(include_done=False)
        tabs.append({"tab_id": "shell-tab", "workspace_id": "w4", "label": "Finance Audit (reviewed)"})
        tabs.append({"tab_id": "numbered", "workspace_id": "w4", "label": "1"})
        workspaces.append({"workspace_id": "w4", "label": "Finance-Tracker Astra Audit"})
        rows = status.rows_for(tabs, agents, workspaces)
        output = status.render(rows, {}, NOW, False)
        self.assertNotIn("Finance-Tracker Astra Audit", output)
        output = status.render(rows, {"keep": {"shell-tab": "Keep this audit"}}, NOW, False)
        self.assertIn("• Finance-Tracker Astra Audit\n  Finance Audit (reviewed)", output)
        self.assertNotIn("  1\n", output)
        self.assertNotIn("shell", output)

    def test_done_summary_requires_matching_agent_identity(self):
        rows = status.rows_for(*field("done"))
        done = next(row for row in rows if row["tab_id"] == "review")
        notes = {"done": {"w3:p1": {"identity": status.row_identity(done),
                                      "summary": "Reviewed the plugin update; tests passed."}}}
        output = status.render(rows, notes, NOW, False)
        self.assertIn("Reviewed the plugin update; tests passed.", output)
        changed = [dict(row) for row in rows]
        changed[-1]["state_change_seq"] = 99
        self.assertIn("Outcome not yet verified", status.render(changed, notes, NOW, False))

    def test_lantern_home_always_keep_even_when_done(self):
        rows = status.rows_for(*field("done"))
        rows[0]["raw_status"] = "done"
        output = status.render(rows, {}, NOW, False, home_pane_id="w1:p1")
        self.assertIn("KEEP\n• Lantern", output)
        self.assertNotIn("DONE\n• Lantern", output)

    def test_keep_and_done_note_commands_validate_live_rows(self):
        with tempfile.TemporaryDirectory() as root:
            state_dir = Path(root)
            status.write_json(state_dir / "field-status-rows.json", {"schema": 1, "rows": status.rows_for(*field("done"))})
            script = str(BIN / "field_status.py")
            for command in (
                ["note", "keep", "set", "w2:p1", "Keep Daily-Tasks"],
                ["note", "done", "set", "w3:p1", "Reviewed plugin update; tests passed"],
            ):
                proc = subprocess.run([sys.executable, script, "--state-dir", root, *command],
                                      capture_output=True, text=True, check=False)
                self.assertEqual(proc.returncode, 0, proc.stderr)
            data = status.notes(state_dir)
            self.assertEqual(data["keep"], {"w2:p1": "Keep Daily-Tasks"})
            self.assertEqual(data["done"]["w3:p1"]["summary"], "Reviewed plugin update; tests passed")
            output = status.render(status.rows_for(*field("done")), data, NOW, False)
            self.assertIn("KEEP\n• Lantern\n  Lantern Home\n• Daily-Tasks", output)
            self.assertIn("Reviewed plugin update; tests passed", output)
            proc = subprocess.run([sys.executable, script, "--state-dir", root,
                                   "note", "done", "set", "w2:p1", "Not done"],
                                  capture_output=True, text=True, check=False)
            self.assertNotEqual(proc.returncode, 0)


if __name__ == "__main__":
    unittest.main()
