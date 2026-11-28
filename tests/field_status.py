"""Field Status lifecycle, persistence, and display regression tests."""
from __future__ import annotations

import importlib.util
import io
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import textwrap
import threading
import unittest
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from itertools import count
from pathlib import Path
from unittest.mock import patch

BIN = Path(__file__).resolve().parents[1] / "bin"
sys.path.insert(0, str(BIN))
spec = importlib.util.spec_from_file_location("field_status", BIN / "field_status.py")
status = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = status
spec.loader.exec_module(status)

NOW = datetime(2026, 9, 24, 16, 0, tzinfo=timezone.utc)


def field(agent_status="working", include_done=True):
    tabs = [
        {"tab_id": "home", "workspace_id": "w1", "label": "Lantern Home", "agent_status": "working"},
        {"tab_id": "notes", "workspace_id": "w2", "label": "notes · codex", "agent_status": "idle"},
    ]
    agents = [
        {"tab_id": "home", "pane_id": "w1:p1", "agent": "codex", "name": "lantern", "agent_status": "working"},
        {"tab_id": "notes", "pane_id": "w2:p1", "agent": "codex", "name": "notes", "agent_status": "idle"},
    ]
    if include_done:
        tabs.append({"tab_id": "review", "workspace_id": "w3", "label": "Review", "agent_status": agent_status})
        agents.append({"tab_id": "review", "pane_id": "w3:p1", "agent": "codex", "name": "Sol Reviewer", "terminal_id": "term3", "agent_session": "session3", "state_change_seq": 1, "completion_seq": 1, "agent_status": agent_status})
    workspaces = [
        {"workspace_id": "w1", "label": "🔥 lantern"},
        {"workspace_id": "w2", "label": "Notes"},
        {"workspace_id": "w3", "label": "Plugin Update"},
    ]
    return tabs, agents, workspaces


def done_rows(n):
    return [{"raw_status": "done"} for _ in range(n)]


class FieldStatusTests(unittest.TestCase):
    def test_many_open_finished_agents_warn_at_default_limit(self):
        with patch.dict(os.environ):
            os.environ.pop("LANTERN_LOAD_WARN", None)
            os.environ.pop("LANTERN_DONE_WARN", None)
            warning = status.machine_warning(done_rows(25), load=0.0, cores=12)
            self.assertIsNotNone(warning)
            self.assertIn("25", warning)
            self.assertIsNotNone(status.machine_warning(done_rows(20), load=0.0, cores=12))
            self.assertIsNone(status.machine_warning(done_rows(19), load=0.0, cores=12))

    def test_load_above_twice_the_cores_warns(self):
        with patch.dict(os.environ):
            os.environ.pop("LANTERN_LOAD_WARN", None)
            os.environ.pop("LANTERN_DONE_WARN", None)
            warning = status.machine_warning([], load=30.0, cores=12)
            self.assertIsNotNone(warning)
            self.assertIn("2.5", warning)
            self.assertNotIn("finished", warning)
            self.assertIsNone(status.machine_warning([], load=24.0, cores=12))

    def test_zero_env_values_turn_each_check_off(self):
        rows = done_rows(25)
        with patch.dict(os.environ, {"LANTERN_LOAD_WARN": "0", "LANTERN_DONE_WARN": "0"}):
            self.assertIsNone(status.machine_warning(rows, load=30.0, cores=12))

            os.environ.pop("LANTERN_LOAD_WARN")
            load_warning = status.machine_warning(rows, load=30.0, cores=12)
            self.assertIsNotNone(load_warning)
            self.assertIn("2.5", load_warning)
            self.assertNotIn("finished", load_warning)

            os.environ["LANTERN_LOAD_WARN"] = "0"
            os.environ.pop("LANTERN_DONE_WARN")
            done_warning = status.machine_warning(rows, load=30.0, cores=12)
            self.assertIsNotNone(done_warning)
            self.assertIn("25", done_warning)
            self.assertNotIn("per core", done_warning)

            os.environ.update(LANTERN_LOAD_WARN="abc", LANTERN_DONE_WARN="-1")
            warning = status.machine_warning(rows, load=30.0, cores=12)
            self.assertIsNotNone(warning)
            self.assertIn("2.5", warning)
            self.assertIn("25", warning)

    def test_missing_getloadavg_does_not_raise(self):
        with patch.dict(os.environ):
            os.environ.pop("LANTERN_LOAD_WARN", None)
            os.environ.pop("LANTERN_DONE_WARN", None)
            getloadavg = getattr(status.os, "getloadavg", None)
            try:
                if getloadavg is not None:
                    delattr(status.os, "getloadavg")
                self.assertIsNone(status.machine_warning([]))
                self.assertIsNotNone(status.machine_warning(done_rows(25)))
            finally:
                if getloadavg is not None:
                    status.os.getloadavg = getloadavg

            with patch.object(status.os, "getloadavg", side_effect=OSError, create=True):
                self.assertIsNone(status.machine_warning([]))

    def test_busy_warning_renders_under_heading(self):
        with tempfile.TemporaryDirectory() as root, patch.object(status, "snapshot", return_value=field("done")), \
                patch.dict(os.environ, {"LANTERN_DONE_WARN": "1", "LANTERN_LOAD_WARN": "0", "COLUMNS": "200"}):
            state_dir = Path(root)
            lines = status.refresh(state_dir, "herdr", 1, NOW, False).splitlines()
            self.assertIn("FIELD STATUS", lines[0])
            self.assertTrue(lines[1].startswith("Machine busy"))
            self.assertIn("free memory", lines[1])
            self.assertEqual(lines[2], "")

            os.environ["LANTERN_DONE_WARN"] = "0"
            lines = status.refresh(state_dir, "herdr", 1, NOW, False).splitlines()
            self.assertEqual(lines[1], "")

            os.environ.update(LANTERN_DONE_WARN="1", COLUMNS="25")
            lines = status.refresh(state_dir, "herdr", 1, NOW, False).splitlines()
            self.assertTrue(lines[1].startswith(("Machine busy", "Busy")))
            self.assertLessEqual(len(lines[1]), 25)
            self.assertIn("1 done", lines[1])

        rows = status.rows_for(*field("done"))
        for width in (38, 60, 88):
            with self.subTest(width=width):
                warning = status.machine_warning(done_rows(34), load=34.8, cores=12,
                                                 load_factor=2.0, done_limit=20.0, width=width)
                lines = status.render(rows, {}, NOW, False, width=width, warning=warning).splitlines()
                self.assertTrue(lines[1].startswith(("Machine busy", "Busy")))
                self.assertLessEqual(len(lines[1]), width)
                self.assertIn("2.9", lines[1])
                self.assertIn("34", lines[1])
                self.assertEqual(lines[2], "")
                if width >= 60:
                    self.assertIn("Close verified helpers", lines[1])
                if width == 88:
                    self.assertIn("free memory", lines[1])

    def test_keep_shows_only_lantern_by_default(self):
        rows = status.rows_for(*field("idle"))
        self.assertEqual([row["agent"] for row in rows], ["lantern", "notes", "Sol Reviewer"])
        output = status.render(rows, {"important": {}, "needs_you": {}}, NOW, False)
        self.assertIn("KEEP\n• Lantern", output)
        self.assertIn("VERIFICATION PENDING\n• Notes", output)
        self.assertIn("Plugin Update", output)
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
        self.assertIn("VERIFICATION PENDING\n• Plugin Update\n  Review\n  Outcome not yet verified", output)
        self.assertIn("• Choose whether to merge", output)
        self.assertIn("  update", output)
        with patch.dict(os.environ, {"LANTERN_VIEW_SCOPE": "local"}):
            local = status.render(rows, {}, NOW, False, width=38).splitlines()[0]
        self.assertTrue(local.startswith("LOCAL FIELD STATUS"))
        self.assertLessEqual(len(local), 38)
        self.assertIn("ET", local)

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

    def test_desktop_refresh_reads_the_desktop_once(self):
        import desktop
        tabs, agents, workspaces = field("done")
        world = desktop.World({}, {"tabs": tabs, "agents": agents, "workspaces": workspaces, "panes": agents}, {})
        with tempfile.TemporaryDirectory() as root, patch.object(desktop, "read_world", return_value=world) as read:
            output = status.refresh(Path(root), str(desktop.READER), 5, NOW, False)
        read.assert_called_once_with(5)
        self.assertIn("Plugin Update", output)

    def test_desktop_sub_agent_outside_tabs_stays_out_of_field_status(self):
        import desktop

        inventory = {"spaces": [{"id": "w1", "name": "Local", "tabs": [
            {"id": "tb1", "name": "Parent", "terminals": [{"pane": "p1", "terminal": "t1"}]},
        ]}]}
        cwd = str(Path(__file__).resolve().parent)
        terminals = {
            "t1": {"state": "live", "cwd": cwd, "status": {"app": "claude-code"}},
            "t2": {"state": "live", "cwd": cwd, "status": {"app": "codex"}, "parent": "t1"},
        }
        view = desktop.snapshot(inventory, terminals, {})
        world = desktop.World({}, view, terminals)
        with patch.object(desktop, "read_world", return_value=world):
            rows = status.rows_for(*status.snapshot(str(desktop.READER), 2))
        self.assertEqual([(row["terminal_id"], row["agent"]) for row in rows], [("t1", "claude")])

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

    def test_pane_mutations_use_existing_herdr_gate(self):
        seen = []

        def fake_run(command, **kwargs):
            seen.append((command, kwargs["env"].get("HERDR_HELPER_OK")))
            return subprocess.CompletedProcess(command, 0, '{"result":{}}', "")

        # smoke.sh may inherit the Herdr gate from an earlier shell fixture.
        with patch.dict(status.os.environ, {"HERDR_HELPER_OK": ""}), \
                patch.object(status.subprocess, "run", side_effect=fake_run):
            status.control("herdr", ["pane", "list", "--workspace", "w1"], 1)
            status.control("herdr", ["tab", "create", "--workspace", "w1"], 1)
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
        tabs.append({"tab_id": "shell-tab", "workspace_id": "w4", "label": "Billing Audit (reviewed)"})
        tabs.append({"tab_id": "numbered", "workspace_id": "w4", "label": "1"})
        workspaces.append({"workspace_id": "w4", "label": "Billing API Audit"})
        rows = status.rows_for(tabs, agents, workspaces)
        output = status.render(rows, {}, NOW, False)
        self.assertNotIn("Billing API Audit", output)
        output = status.render(rows, {"keep": {"shell-tab": "Keep this audit"}}, NOW, False)
        self.assertIn("• Billing API Audit\n  Billing Audit (reviewed)", output)
        self.assertNotIn("  1\n", output)
        self.assertNotIn("shell", output)

    def test_legacy_done_note_cannot_accept_an_unreviewed_turn(self):
        rows = status.rows_for(*field("done"))
        row = rows[-1]
        notes = {"done": {"w3:p1": {"identity": status.row_identity(row), "summary": "Old success"}}}
        self.assertNotIn("Old success", status.render(rows, notes, NOW, False))
        self.assertEqual(status.section_for(row, notes, ""), "VERIFICATION PENDING")

    def test_outcome_requires_current_turn_and_task_generation(self):
        rows = status.reconcile(status.rows_for(*field("done")), {}, NOW)
        row = rows[-1]
        data = {"tasks": {"w3:p1": {"id": "issue75", "generation": "one"}}, "outcomes": {}}
        status.apply_outcomes(rows, data)
        token = row["outcome_token"]
        status.record_outcome(row, data, "issue75", token, "complete", "commit abc, checks passed", "Fixed")
        status.apply_outcomes(rows, data)
        self.assertEqual(status.section_for(row, {}, ""), "DONE")
        for field_name, new_value in (("completion_seq", 2), ("state_change_seq", 3),
                                      ("agent_session", "new"), ("terminal_id", "new"),
                                      ("work_epoch", 9), ("session_epoch", "new")):
            changed = dict(row, **{field_name: new_value})
            status.apply_outcomes([changed], data)
            self.assertEqual(status.section_for(changed, {}, ""), "VERIFICATION PENDING")
            with self.assertRaisesRegex(ValueError, "stale"):
                status.record_outcome(changed, data, "issue75", token, "complete", "proof", "Fixed")
        data["tasks"]["w3:p1"]["generation"] = "two"
        status.apply_outcomes(rows, data)
        self.assertEqual(status.section_for(row, {}, ""), "VERIFICATION PENDING")

    def test_agent_stopped_with_an_error_waits_for_review_and_cannot_be_complete(self):
        rows = status.reconcile(status.rows_for(*field("error")), {}, NOW)
        row = rows[-1]
        data = {"tasks": {"w3:p1": {"id": "task", "generation": "one"}}, "outcomes": {}}
        status.apply_outcomes(rows, data)
        self.assertEqual(status.section_for(row, {}, ""), "VERIFICATION PENDING")
        self.assertIn("w3:p1", [pending["pane_id"] for pending in status.pending_rows(rows)])
        self.assertIn("Plugin Update\n  Review\n  Stopped with an error", status.render(rows, {}, NOW, False))
        with self.assertRaisesRegex(ValueError, "cannot be complete"):
            status.record_outcome(row, data, "task", row["outcome_token"], "complete", "proof", "Fixed")
        for result in ("blocked", "incomplete"):
            status.record_outcome(row, data, "task", row["outcome_token"], result, "error output read", "Stopped early")
            self.assertEqual(data["outcomes"]["w3:p1"]["status"], result)

    def test_fallback_sequence_and_incomplete_outcome(self):
        row = status.reconcile(status.rows_for(*field("done")), {}, NOW)[-1]
        row["agent_session"] = None
        row["completion_seq"] = None
        data = {"tasks": {"w3:p1": {"id": "task", "generation": "one"}}, "outcomes": {}}
        token = status.outcome_token(row, data["tasks"]["w3:p1"])
        status.record_outcome(row, data, "task", token, "incomplete", "review found missing output", "Missing output")
        status.apply_outcomes([row], data)
        self.assertEqual(status.section_for(row, {}, ""), "NOT COMPLETE")
        row["state_change_seq"] = None
        with self.assertRaisesRegex(ValueError, "cannot identify"):
            status.record_outcome(row, data, "task", token, "complete", "proof", "Fixed")

    def test_cli_pending_and_live_outcome_reject_stale_turn(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            source = root / "source.json"
            live = dict(zip(("tabs", "agents", "workspaces"), field("done")))
            source.write_text(json.dumps(live), encoding="utf-8")
            reader = root / "reader.py"
            reader.write_text("import json,sys\nfrom pathlib import Path\nk=sys.argv[2]+'s'\nprint(json.dumps({'result': {k:json.loads(Path(sys.argv[1]).read_text())[k]}}))\n")
            fake = root / "herdr"
            fake.write_text("#!/bin/sh\nexec " + " ".join(shlex.quote(x) for x in
                            (sys.executable, str(reader), str(source))) + ' "$1"\n')
            fake.chmod(0o755)
            base = [sys.executable, str(BIN / "field_status.py"), "--state-dir", str(root / "state"),
                    "--herdr", str(fake), "--plain"]
            def run(*args):
                return subprocess.run([*base, *args], capture_output=True, text=True, encoding="utf-8", timeout=10)
            self.assertEqual(run("task", "w3:p1", "task75").returncode, 0)
            pending = run("pending")
            self.assertEqual(pending.returncode, 3, pending.stderr)
            item = next(x for x in json.loads(pending.stdout) if x["pane_id"] == "w3:p1")
            args = ["outcome", "w3:p1", "--task", "task75", "--token", item["token"],
                    "--status", "complete", "--evidence", "commit abc; tests passed", "--summary", "Fixed output"]
            accepted = run(*args)
            self.assertEqual(accepted.returncode, 0, accepted.stderr)
            self.assertIn("DONE\n• Plugin Update\n  Review\n  Fixed output", run("refresh").stdout)
            live["agents"][-1]["completion_seq"] = 2
            source.write_text(json.dumps(live), encoding="utf-8")
            rejected = run(*args)
            self.assertEqual(rejected.returncode, 1)
            self.assertIn("stale outcome token", rejected.stderr)
            self.assertNotIn("Fixed output", run("refresh").stdout)

    def test_work_cycle_and_removed_session_invalidate_old_outcomes(self):
        stopped = status.reconcile(status.rows_for(*field("done")), {}, NOW)
        working = status.reconcile(status.rows_for(*field("working")), {"rows": stopped}, NOW)
        again = status.reconcile(status.rows_for(*field("done")), {"rows": working}, NOW)
        self.assertNotEqual(status.outcome_token(stopped[-1], {}), status.outcome_token(again[-1], {}))
        replacement = status.reconcile(status.rows_for(*field("done")), {"rows": []}, NOW)
        self.assertNotEqual(status.outcome_token(stopped[-1], {}), status.outcome_token(replacement[-1], {}))

    def test_lantern_home_always_keep_even_when_done(self):
        rows = status.rows_for(*field("done"))
        rows[0]["raw_status"] = "done"
        output = status.render(rows, {}, NOW, False, home_pane_id="w1:p1")
        self.assertIn("KEEP\n• Lantern", output)
        self.assertNotIn("DONE\n• Lantern", output)

    def test_keep_note_and_rejection_of_legacy_done_write(self):
        with tempfile.TemporaryDirectory() as root:
            state_dir = Path(root)
            status.write_json(state_dir / "field-status-rows.json", {"schema": 1, "rows": status.rows_for(*field("done"))})
            script = str(BIN / "field_status.py")
            for command in (
                ["note", "keep", "set", "w2:p1", "Keep Notes"],
            ):
                proc = subprocess.run([sys.executable, script, "--state-dir", root, *command],
                                      capture_output=True, text=True, check=False)
                self.assertEqual(proc.returncode, 0, proc.stderr)
            data = status.notes(state_dir)
            self.assertEqual(data["keep"], {"w2:p1": "Keep Notes"})
            self.assertEqual(data["done"], {})
            output = status.render(status.rows_for(*field("done")), data, NOW, False)
            self.assertIn("KEEP\n• Lantern\n  Lantern Home", output)
            self.assertIn("VERIFICATION PENDING", output)
            proc = subprocess.run([sys.executable, script, "--state-dir", root,
                                   "note", "done", "set", "w2:p1", "Not done"],
                                  capture_output=True, text=True, check=False)
            self.assertNotEqual(proc.returncode, 0)


class ReapTests(unittest.TestCase):
    eligible = ("w1:p1", "done", "complete")
    protected = (
        ("w1:p2", "done", None, "no Complete outcome"),
        ("w1:p3", "done", "incomplete", "outcome incomplete"),
        ("w1:p4", "working", None, "working"),
        ("w1:p5", "blocked", None, "blocked"),
        ("w1:p6", "error", None, "error"),
        ("w1:p7", "idle", "complete", "idle"),
        ("w1:p8", "done", "complete", "pinned keep"),
        ("w1:p9", "done", "complete", "Lantern home"),
    )

    def reviewed(self, run, state, pane, result):
        for args in (("task", pane, pane), ("refresh",)):
            reply = run(*args)
            self.assertIn(reply.returncode, (0, 3), reply.stderr)
        rows = json.loads((state / "field-status-rows.json").read_text(encoding="utf-8"))["rows"]
        token = next(row["outcome_token"] for row in rows if row["pane_id"] == pane)
        reply = run("outcome", pane, "--task", pane, "--token", token, "--status", result,
                    "--evidence", "Reviewed the commit and checks", "--summary", "Verified result")
        self.assertEqual(reply.returncode, 0, reply.stderr)

    def legacy(self, *cases):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        state = root / "state"
        state.mkdir()
        source = root / "source.json"
        helper = root / "fake_herdr.py"
        log = root / "closed"
        reads = root / "agent-reads"
        log.touch()
        reads.write_text("0", encoding="utf-8")
        helper.write_text(textwrap.dedent("""\
            import json
            import os
            import sys
            from pathlib import Path

            root = Path(__file__).parent
            data = json.loads((root / "source.json").read_text(encoding="utf-8"))
            args = sys.argv[1:]
            if args in (["tab", "list"], ["agent", "list"], ["workspace", "list"]):
                key = args[0] + "s"
                if key == "agents":
                    counter = root / "agent-reads"
                    count = int(counter.read_text(encoding="utf-8"))
                    counter.write_text(str(count + 1), encoding="utf-8")
                    if count and data.get("changed"):
                        data["agents"][0]["completion_seq"] += 1
                result = {key: data[key]}
            elif args[:3] == ["pane", "process-info", "--pane"] and len(args) == 4:
                pane = args[3]
                result = {"process_info": {"pane_id": pane, "shell_pid": 10,
                          "foreground_processes": data["foreground"][pane]}}
            elif args[:2] == ["pane", "close"] and len(args) == 3:
                if os.environ.get("HERDR_HELPER_OK") != "1":
                    sys.exit("missing wrapper approval")
                with (root / "closed").open("a", encoding="utf-8") as closed:
                    print(args[2], file=closed)
                if data.get("empty_close"):
                    sys.exit(0)
                result = {}
            else:
                sys.exit("unexpected Fake Herdr arguments: " + repr(args))
            print(json.dumps({"result": result}))
            """), encoding="utf-8")
        fake = root / "herdr"
        command = " ".join(shlex.quote(path.as_posix()) for path in (Path(sys.executable), helper))
        fake.write_bytes(f'#!/bin/sh\nexec {command} "$@"\n'.encode("utf-8"))
        fake.chmod(0o755)
        base = [sys.executable, str(BIN / "field_status.py"), "--state-dir", str(state),
                "--herdr", str(fake), "--plain"]
        env = os.environ.copy()
        env.pop("HERDR_HELPER_OK", None)

        def run(*args):
            return subprocess.run([*base, *args], capture_output=True, text=True, encoding="utf-8",
                                  timeout=15, env=env)

        data = {"tabs": [], "agents": [], "workspaces": [{"workspace_id": "w1", "label": "Local"}],
                "foreground": {}}
        for pane, lifecycle, _ in cases:
            tab = pane.replace(":p", ":t")
            data["tabs"].append({"tab_id": tab, "workspace_id": "w1", "label": f"Helper {pane}"})
            data["agents"].append({"tab_id": tab, "pane_id": pane, "terminal_id": f"term-{pane}",
                                   "agent": "codex", "name": "Sol Reviewer", "agent_status": lifecycle,
                                   "state_change_seq": 1, "completion_seq": 1})
            data["foreground"][pane] = [{"pid": 11, "name": "codex", "argv": ["codex"]}]

        def update():
            source.write_text(json.dumps(data), encoding="utf-8")
            reads.write_text("0", encoding="utf-8")

        update()
        status.write_json(state / "views.json", {"home_pane_id": "w1:p9"})
        for pane, _, outcome in cases:
            if outcome:
                self.reviewed(run, state, pane, outcome)
        if any(pane == "w1:p8" for pane, _, _ in cases):
            self.assertEqual(run("note", "keep", "set", "w1:p8", "Retain this helper").returncode, 0)
        return run, data, update, log

    def assert_protected(self, output):
        lines = output.splitlines()
        for pane, _, _, reason in self.protected:
            with self.subTest(protected=pane):
                self.assertTrue(any(line.startswith(f"skipped {pane} ") and line.endswith(f": {reason}")
                                    for line in lines), output)

    def test_reap_dry_run_closes_nothing(self):
        run, _, _, closed = self.legacy(self.eligible, *(case[:3] for case in self.protected))
        reply = run("reap")
        self.assertEqual(reply.returncode, 0, reply.stderr)
        self.assertIn("finished agents open: 5", reply.stdout)
        offered = [line.split()[2] for line in reply.stdout.splitlines() if line.startswith("would close ")]
        self.assertEqual(offered, ["w1:p1"])
        self.assert_protected(reply.stdout)
        self.assertEqual(closed.read_text(encoding="utf-8"), "")

    def test_reap_yes_closes_only_done_complete_agents(self):
        run, data, update, closed = self.legacy(self.eligible, *(case[:3] for case in self.protected))
        data["empty_close"] = True
        data["foreground"]["w1:p1"].append({"pid": 10, "argv": ["-zsh"]})
        update()
        reply = run("reap", "--yes")
        self.assertEqual(reply.returncode, 0, reply.stderr)
        self.assertIn("closed w1:p1 ", reply.stdout)
        self.assert_protected(reply.stdout)
        self.assertEqual(closed.read_text(encoding="utf-8").splitlines(), ["w1:p1"])

    def test_reap_yes_with_pane_closes_only_the_named_helper(self):
        run, _, _, closed = self.legacy(self.eligible, ("w1:p2", "done", "complete"),
                                         ("w1:p3", "done", None))
        reply = run("reap", "--pane", "w1:p1", "--pane", "w1:p3", "--pane", "missing", "--yes")
        self.assertEqual(reply.returncode, 0, reply.stderr)
        self.assertIn("would close w1:p2 ", reply.stdout)
        self.assertIn("skipped w1:p3: no Complete outcome", reply.stdout)
        self.assertIn("skipped missing: not found", reply.stdout)
        self.assertEqual(closed.read_text(encoding="utf-8").splitlines(), ["w1:p1"])

    def test_reap_yes_keeps_a_done_helper_whose_pane_runs_another_program(self):
        run, data, update, closed = self.legacy(self.eligible)
        for pid, name, argv in ((11, "vim", ["vim", "notes.md"]), (11, "codex-notes", ["codex-notes"]),
                                (10, "sh", ["sh"]), (11, "node", ["node", "/repo/codex.js"])):
            with self.subTest(program=name):
                data["foreground"]["w1:p1"] = [{"pid": pid, "name": name, "argv": argv}]
                update()
                reply = run("reap", "--yes")
                self.assertEqual(reply.returncode, 0, reply.stderr)
                self.assertIn("foreground is not the agent", reply.stdout)
                self.assertEqual(closed.read_text(encoding="utf-8"), "")

    def test_reap_yes_skips_a_pane_that_changed_since_listing(self):
        run, data, update, closed = self.legacy(self.eligible)
        data["changed"] = True
        update()
        reply = run("reap", "--yes")
        self.assertEqual(reply.returncode, 0, reply.stderr)
        self.assertIn("skipped w1:p1 Sol Reviewer: changed since listing", reply.stdout)
        self.assertEqual(closed.read_text(encoding="utf-8"), "")

    def desktop_reap(self, scenario, command):
        import desktop

        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp)
            cwd = str(state.resolve())
            panes = {"target": "t1", "parent": "t2", "bridge": "t3", "home": "t4", "other-home": "t5"}
            if scenario in {"missing-spaces", "null-spaces", "missing-tab", "null-close"}:
                panes["next"] = "t7"
            terminals = {}
            for pid, address in enumerate(panes.values(), 100):
                terminals[address] = {"address": address, "pid": pid, "cwd": cwd, "state": "live",
                                      "status": {"app": "codex", "state": "done"}, "status_ms": 1,
                                      "foreground": {"pid": pid, "name": "codex", "argv": ["codex"]}}
            terminals["t3"]["foreground"]["argv"] = [str(state.resolve() / "herdr"), "terminal", "attach", "legacy"]
            terminals["t5"]["labels"] = {"lantern.desktop": "home"}
            terminals["t6"] = {"address": "t6", "state": "live", "parent": "t2"}
            if scenario == "wrapper":
                terminals["t1"]["foreground"].update(
                    name="zsh", argv=["/bin/zsh", "-ic", '"$@"; exec "$0" -i', "/bin/zsh", "codex", "--no-daemon"])
            if scenario == "cursor":
                terminals["t1"]["status"]["app"] = "cursor"
                terminals["t1"]["foreground"].update(
                    name="node", argv=[str(state / "agent"), "--use-system-ca", "/app/index.js"])
            sources = {str(state.resolve() / "herdr"): {
                "legacy": {"pane_id": "legacy-pane", "agent": "codex", "agent_status": "done"}}}
            status.write_json(state / "home.json", {"terminal": "t4"})

            def inventory():
                tabs = [{"id": pane, "name": "Lantern" if pane == "other-home" else pane,
                         "terminals": [{"pane": pane, "terminal": address}]} for pane, address in panes.items()]
                return {"spaces": [{"id": "space1", "name": "Local", "machine": None, "tabs": tabs}]}

            closes = []
            inventory_reads = 0

            def external(argv, timeout=15):
                nonlocal inventory_reads
                if argv == [desktop.APP, "ls"]:
                    layout = inventory()
                    inventory_reads += 1
                    if inventory_reads == 1:
                        if scenario == "missing-spaces":
                            return "{}"
                        if scenario == "null-spaces":
                            return '{"spaces": null}'
                        if scenario == "missing-tab":
                            del layout["spaces"][0]["tabs"][0]["id"]
                        if scenario == "moved":
                            layout["spaces"][0]["tabs"][0]["id"] = "moved-tab"
                    return json.dumps(layout)
                if argv[:3] == [desktop.APP, "do", "pane.close"]:
                    pane = json.loads(argv[3])["pane"]
                    closes.append(pane)
                    if scenario != "pending":
                        del panes[pane]
                    return "null" if scenario == "null-close" and pane == "target" else "{}"
                raise AssertionError(argv)

            def snapshot(_timeout):
                return desktop.World({}, desktop.snapshot(inventory(), terminals, sources), terminals)

            def run(*args):
                argv = [str(BIN / "field_status.py"), "--state-dir", str(state), "--herdr", str(desktop.READER),
                        "--plain", *args]
                stdout, stderr = io.StringIO(), io.StringIO()
                with patch.object(sys, "argv", argv), redirect_stdout(stdout), redirect_stderr(stderr):
                    try:
                        result = status.main()
                    except SystemExit as exc:
                        result = exc.code
                return subprocess.CompletedProcess(argv, result, stdout.getvalue(), stderr.getvalue())

            with patch.object(desktop, "reads", return_value=True), patch.object(desktop, "read_world", snapshot), \
                    patch.object(desktop, "run", external), ExitStack() as processes:
                if scenario == "wrapper":
                    parent = terminals["t1"]["foreground"]["pid"]
                    processes.enter_context(patch.object(
                        desktop.runtime, "child_pids", side_effect=lambda pid: {200} if pid == parent else set()))
                    processes.enter_context(patch.object(
                        desktop.runtime, "process_start", side_effect=lambda pid: "live-child" if pid == 200 else ""))
                    processes.enter_context(patch.object(
                        desktop, "process_argv", side_effect=lambda pid: ["codex", "--no-daemon"] if pid == 200 else None))
                for pane in panes:
                    self.reviewed(run, state, pane, "complete")
                if scenario == "pending":
                    with patch.object(status.time, "monotonic", side_effect=count(step=0.25)), \
                            patch.object(status.time, "sleep"):
                        reply = run(*command)
                else:
                    reply = run(*command)
            return reply, closes

    def test_reap_desktop_closes_with_pane_close_and_protects_home_children_and_bridges(self):
        cases = (
            ("direct", 0, ["target"], "closed target "),
            ("wrapper", 0, ["target"], "closed target "),
            ("cursor", 0, ["target"], "closed target "),
            ("moved", 0, [], "changed since listing"),
            ("missing-spaces", 1, ["next"], "failed target "),
            ("null-spaces", 1, ["next"], "failed target "),
            ("missing-tab", 1, ["next"], "failed target "),
            ("null-close", 1, ["target", "next"], "failed target "),
            ("pending", 1, ["target"], "close still pending"),
        )
        for scenario, code, expected_closes, phrase in cases:
            with self.subTest(scenario=scenario):
                reply, closes = self.desktop_reap(scenario, ("reap", "--yes"))
                self.assertEqual(reply.returncode, code, reply.stderr)
                self.assertEqual(closes, expected_closes)
                self.assertIn(phrase, reply.stdout)
                for pane, reason in (("parent", "running child t6"), ("bridge", "bridged legacy pane"),
                                     ("home", "Lantern home"), ("other-home", "Lantern home")):
                    self.assertTrue(any(line.startswith(f"skipped {pane} ") and line.endswith(f": {reason}")
                                        for line in reply.stdout.splitlines()), reply.stdout)
                if "next" in expected_closes:
                    self.assertIn("closed next ", reply.stdout)


if __name__ == "__main__":
    unittest.main()
