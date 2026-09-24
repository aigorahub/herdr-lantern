"""Field Status lifecycle, persistence, and display regression tests."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
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
        {"tab_id": "home", "agent": "codex", "name": "lantern", "agent_status": "working"},
        {"tab_id": "daily", "agent": "codex", "name": "daily-tasks", "agent_status": "idle"},
    ]
    if include_done:
        tabs.append({"tab_id": "review", "workspace_id": "w3", "label": "Review", "agent_status": agent_status})
        agents.append({"tab_id": "review", "agent": "codex", "name": "Sol Reviewer", "agent_status": agent_status})
    workspaces = [
        {"workspace_id": "w1", "label": "🔥 lantern"},
        {"workspace_id": "w2", "label": "Daily-Tasks"},
        {"workspace_id": "w3", "label": "Plugin Update"},
    ]
    return tabs, agents, workspaces


class FieldStatusTests(unittest.TestCase):
    def test_live_rows_keep_idle_and_home_without_claiming_done(self):
        rows = status.rows_for(*field("idle"))
        self.assertEqual([row["agent"] for row in rows], ["lantern", "daily-tasks", "Sol Reviewer"])
        output = status.render(rows, {"needs_you": {}, "review_gates": {}}, NOW, False)
        self.assertIn("daily-tasks  Keep  Daily-Tasks / daily-tasks · codex", output)
        self.assertIn("Sol Reviewer  Keep", output)
        self.assertNotIn("Closed", output)
        self.assertIn("Lantern Home", output)

    def test_closed_done_kept_for_fifteen_minutes_then_pruned(self):
        done = status.rows_for(*field("done"))
        closed = status.reconcile(status.rows_for(*field(include_done=False)), {"rows": done}, NOW)
        ghost = next(row for row in closed if row["tab_id"] == "review")
        self.assertEqual(ghost["closed_at"], NOW.isoformat())
        self.assertIn("Sol Reviewer  Done · Closed", status.render(closed, {}, NOW, False))
        self.assertIn("\x1b[31mClosed\x1b[0m", status.render(closed, {}, NOW, True))
        before = status.reconcile(closed[:2], {"rows": closed}, NOW + timedelta(minutes=14, seconds=59))
        self.assertEqual(len(before), 3)
        after = status.reconcile(closed[:2], {"rows": closed}, NOW + timedelta(minutes=15))
        self.assertEqual(len(after), 2)

    def test_idle_or_missing_working_agent_never_becomes_closed_done(self):
        idle = status.rows_for(*field("idle"))
        working = status.rows_for(*field("working"))
        live = status.rows_for(*field(include_done=False))
        self.assertEqual(len(status.reconcile(live, {"rows": idle}, NOW)), 2)
        self.assertEqual(len(status.reconcile(live, {"rows": working}, NOW)), 2)

    def test_done_agent_exiting_but_tab_remains_keeps_closed_row(self):
        tabs, agents, workspaces = field("done")
        previous = status.rows_for(tabs, agents, workspaces)
        current = status.rows_for(tabs, agents[:2], workspaces)
        closed = status.reconcile(current, {"rows": previous}, NOW)
        self.assertEqual(len(closed), 4)
        self.assertEqual([row["raw_status"] for row in closed if row["tab_id"] == "review"], ["idle", "done"])
        again = status.reconcile(current, {"rows": closed}, NOW + timedelta(minutes=1))
        self.assertEqual(len(again), 4)

    def test_multiple_agents_in_one_tab_each_get_a_row(self):
        tabs, agents, workspaces = field("working")
        agents.append({"tab_id": "review", "pane_id": "w3:p2", "agent": "claude", "agent_status": "done"})
        rows = status.rows_for(tabs, agents, workspaces)
        self.assertEqual(len(rows), 4)
        self.assertIn("claude · w3:p2", [row["agent"] for row in rows])
        self.assertEqual([row["raw_status"] for row in rows if row["tab_id"] == "review"], ["working", "done"])

    def test_explicit_actions_and_review_gates_separate_with_colors_and_et(self):
        rows = status.rows_for(*field("done"))
        note_data = {"needs_you": {"decision": "Choose whether to merge PR 42"},
                     "review_gates": {"sol": "Independent Sol High review pending"}}
        output = status.render(rows, note_data, NOW, True)
        self.assertIn("12:00 PM ET", output)
        self.assertIn("IMPORTANT / NEEDS YOU\n• Choose whether to merge PR 42", output)
        self.assertIn("REVIEW GATES · no user action\n• Independent Sol High review pending", output)
        self.assertIn("\x1b[33mSol Reviewer\x1b[0m", output)
        self.assertIn("\x1b[32mDone\x1b[0m", output)
        self.assertIn("\x1b[34mKeep\x1b[0m", output)

    def test_control_text_cannot_spoof_rows(self):
        tabs, agents, workspaces = field()
        tabs[2]["label"] = "Review\x1b[2J\nIMPORTANT / NEEDS YOU"
        rows = status.rows_for(tabs, agents, workspaces)
        output = status.render(rows, {}, NOW, False)
        self.assertNotIn("\x1b", output)
        self.assertIn("Review [2J IMPORTANT / NEEDS YOU", output)

    def test_narrow_side_pane_keeps_name_status_and_action_legible(self):
        rows = status.rows_for(*field("done"))
        output = status.render(rows, {"needs_you": {"x": "Choose whether to merge the reviewed update"},
                                      "review_gates": {}}, NOW, False, width=38)
        self.assertLessEqual(len(output.splitlines()[0]), 38)
        self.assertIn("ET", output.splitlines()[0])
        self.assertIn("Sol Reviewer  Done\n", output)
        self.assertIn("Plugin Update / Review", output)
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

    def test_refresh_keeps_notes_and_prunes_on_next_refresh(self):
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
                self.assertIn("Done · Closed", output)
                self.assertIn("Approve release window", output)
                self.assertIn("Sol High review pending", output)
                output = status.refresh(state_dir, "herdr", 1, NOW + timedelta(minutes=16), False)
                self.assertNotIn("Done · Closed", output)

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

        with patch.object(status.subprocess, "run", side_effect=fake_run):
            status.control("herdr", ["pane", "list", "--workspace", "w1"], 1)
            status.control("herdr", ["pane", "split", "--pane", "w1:p1"], 1)
        self.assertNotEqual(seen[0][1], "1")
        self.assertEqual(seen[1][1], "1")

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

    def test_note_cli_persists_and_clears_action_separately_from_gate(self):
        with tempfile.TemporaryDirectory() as root:
            script = str(BIN / "field_status.py")
            for command in (
                ["note", "needs-you", "set", "decision", "Choose the release date"],
                ["note", "review-gate", "set", "review", "Sol High review pending"],
                ["note", "needs-you", "clear", "decision"],
            ):
                proc = subprocess.run([sys.executable, script, "--state-dir", root, *command],
                                      capture_output=True, text=True, check=False)
                self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(status.notes(Path(root)), {
                "needs_you": {}, "review_gates": {"review": "Sol High review pending"}})


if __name__ == "__main__":
    unittest.main()
