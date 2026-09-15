#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "bin" / "lantern_session.py"
SPEC = importlib.util.spec_from_file_location("lantern_session", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class LanternSessionTests(unittest.TestCase):
    def run_script(self, *args: str, env: dict[str, str] | None = None, stdin: str = ""):
        merged = os.environ.copy()
        if env:
            merged.update(env)
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            input=stdin,
            text=True,
            capture_output=True,
            env=merged,
            check=False,
        )

    def test_capture_stores_only_exact_identifiers(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "private" / "session.json"
            session_id = "01999999-9999-7999-8999-999999999999"
            result = self.run_script(
                "capture", "--path", str(path), "--pane", "w1:p1",
                "--workspace", "w1", env={"CODEX_SESSION_ID": session_id},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8")),
                {"codex_session_id": session_id, "pane_id": "w1:p1", "workspace_id": "w1"},
            )

    def test_show_rejects_mismatched_pane(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "session.json"
            path.write_text(
                '{"codex_session_id":"01999999-9999-7999-8999-999999999999",'
                '"pane_id":"w1:p1","workspace_id":"w1"}\n', encoding="utf-8"
            )
            result = self.run_script(
                "show", "--path", str(path), "--pane", "w1:p2", "--workspace", "w1"
            )
            self.assertNotEqual(result.returncode, 0)

    def test_pid_parser_requires_one_exact_codex_process(self):
        payload = json.dumps({
            "result": {"process_info": {"pane_id": "w1:p1", "foreground_processes": [
                {"name": "codex.exe", "argv0": "C:/Codex/codex.exe", "pid": 12345}
            ]}}
        })
        result = self.run_script("pid-from-json", "--pane", "w1:p1", stdin=payload)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "12345")

    def test_delete_refuses_current_running_process(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "session.json"
            path.write_text(
                '{"codex_session_id":"01999999-9999-7999-8999-999999999999",'
                '"pane_id":"w1:p1","workspace_id":"w1"}\n', encoding="utf-8"
            )
            result = self.run_script(
                "delete", "--path", str(path), "--pane", "w1:p1", "--workspace", "w1",
                "--pid", str(os.getpid()), "--timeout", "0", "--codex", "does-not-exist",
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("still running", result.stderr)


if __name__ == "__main__":
    unittest.main()
