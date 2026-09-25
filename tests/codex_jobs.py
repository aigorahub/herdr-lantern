"""Safety and persistence tests for bounded headless Codex jobs."""

import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


BIN = Path(__file__).resolve().parents[1] / "bin"
sys.path.insert(0, str(BIN))


def load():
    spec = importlib.util.spec_from_file_location("codex_headless", BIN / "codex_headless.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


jobs = load()


ROUTE = {
    "kind": "codex",
    "model": "gpt-6-astra",
    "effort": "medium",
    "fast": False,
    "argv": ["-m", "gpt-6-astra", "-c", 'model_reasoning_effort="medium"'],
}


class RouteError(RuntimeError):
    pass


class CheckError(RuntimeError):
    pass


def route_module():
    return types.SimpleNamespace(codex_route=lambda phrase: ROUTE, RouteError=RouteError)


def checker_module(status=0, report='{"available":true}'):
    def check(kind, model, effort):
        print(report)
        return status

    return types.SimpleNamespace(check=check, CheckError=CheckError)


class Runner:
    def __init__(self, returncode=0, write_result=True):
        self.returncode = returncode
        self.write_result = write_result
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        if self.write_result:
            output = Path(command[command.index("-o") + 1])
            output.write_text("saved result\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, self.returncode)


class CodexJobs(unittest.TestCase):
    def run_one(self, mode, runner=None):
        runner = runner or Runner()
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            cwd = root / "repo"
            state = root / "state"
            cwd.mkdir()
            report = jobs.run_job(
                mode=mode,
                cwd=cwd,
                state_dir=state,
                job=f"daily-{mode}",
                model_phrase="default",
                prompt="Do one bounded thing.",
                route_module=route_module(),
                checker_module=checker_module(),
                runner=runner,
                resolver=lambda command: command,
            )
            command, kwargs = runner.calls[0]
            self.assertEqual(Path(report["result"]).read_text(encoding="utf-8"), "saved result\n")
            self.assertFalse(report["session_persisted"])
            self.assertIn("--ephemeral", command)
            self.assertIn("--skip-git-repo-check", command)
            self.assertEqual(command[:2], ["codex", "exec"])
            self.assertNotIn("resume", command)
            self.assertNotIn("fork", command)
            self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", command)
            self.assertNotIn("Do one bounded thing.", command)
            self.assertIn("Do not expose, copy, or persist authentication material", kwargs["input"])
            self.assertIsNone(kwargs.get("env"))
            return command

    def test_windows_batch_shim_escapes_cmd_metacharacters(self):
        from win_cmd import cmd_argv

        argv = cmd_argv(
            [r"C:\Tools\codex.cmd", "exec", "-C", r"C:\Work\R&D\repo", "-o", r"C:\Users\O\R&D Space\out.md"],
            r"C:\Windows\System32\cmd.exe",
        )
        self.assertEqual(argv[:4], [r"C:\Windows\System32\cmd.exe", "/d", "/s", "/c"])
        self.assertIn(r"C:\Work\R^&D\repo", argv[4])
        self.assertIn(r'"C:\Users\O\R&D Space\out.md"', argv[4])
        self.assertNotIn("R&D Space", argv[4].split('"')[0])

    def test_research_is_ephemeral_and_read_only(self):
        command = self.run_one("research")
        self.assertIn("read-only", command)
        self.assertNotIn("--approve-for-me", command)

    def test_update_is_ephemeral_workspace_write_without_yolo(self):
        command = self.run_one("update")
        self.assertIn("--approve-for-me", command)
        self.assertNotIn("danger-full-access", command)

    def test_live_route_passes_current_fast_tier_to_headless_codex(self):
        actual_route = jobs.load_module("headless_route_integration", "model-route.py")
        catalog = json.dumps({"models": [{
            "slug": "gpt-5.6-luna", "display_name": "GPT-5.6 Luna",
            "visibility": "list", "default_reasoning_level": "medium",
            "supported_reasoning_levels": [{"effort": "xhigh"}, {"effort": "medium"}],
            "service_tiers": [{"id": "live-fast", "name": "Fast"}],
            "additional_speed_tiers": ["fast"],
        }]})
        runner = Runner()
        with tempfile.TemporaryDirectory() as root, patch.object(
            actual_route, "run_catalog", return_value=catalog
        ):
            cwd = Path(root) / "repo"
            cwd.mkdir()
            report = jobs.run_job(
                mode="research", cwd=cwd, state_dir=Path(root) / "state",
                job="daily-route", model_phrase="5.6 luna xhigh fast",
                prompt="Read only.", route_module=actual_route,
                checker_module=checker_module(), runner=runner,
                resolver=lambda command: command,
            )
        command = runner.calls[0][0]
        self.assertEqual(report["model"], "gpt-5.6-luna")
        self.assertIn('model_reasoning_effort="xhigh"', command)
        self.assertIn('service_tier="live-fast"', command)

    def test_result_must_be_outside_checkout_and_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as root:
            cwd = Path(root) / "repo"
            cwd.mkdir()
            common = dict(
                mode="research",
                cwd=cwd,
                job="once",
                model_phrase="default",
                prompt="Inspect.",
                route_module=route_module(),
                checker_module=checker_module(),
                runner=Runner(),
                resolver=lambda command: command,
            )
            with self.assertRaisesRegex(jobs.JobError, "outside the product checkout"):
                jobs.run_job(state_dir=cwd / ".state", **common)
            self.assertFalse((cwd / ".state").exists())
            state = Path(root) / "state"
            jobs.run_job(state_dir=state, **common)
            with self.assertRaisesRegex(jobs.JobError, "refusing to overwrite"):
                jobs.run_job(state_dir=state, **common)

    def test_failed_or_empty_run_is_not_reported_complete(self):
        for runner, message in ((Runner(returncode=9), "status 9"),
                                (Runner(write_result=False), "without a final result")):
            with self.subTest(message=message), tempfile.TemporaryDirectory() as root:
                cwd = Path(root) / "repo"
                cwd.mkdir()
                with self.assertRaisesRegex(jobs.JobError, message):
                    jobs.run_job(
                        mode="update",
                        cwd=cwd,
                        state_dir=Path(root) / "state",
                        job="one-shot",
                        model_phrase="default",
                        prompt="Update.",
                        route_module=route_module(),
                        checker_module=checker_module(),
                        runner=runner,
                        resolver=lambda command: command,
                    )

    def test_preflight_failure_stops_before_codex(self):
        runner = Runner()
        with tempfile.TemporaryDirectory() as root:
            cwd = Path(root) / "repo"
            cwd.mkdir()
            with self.assertRaisesRegex(jobs.JobError, "quota exhausted"):
                jobs.run_job(
                    mode="research",
                    cwd=cwd,
                    state_dir=Path(root) / "state",
                    job="research-once",
                    model_phrase="default",
                    prompt="Research.",
                    route_module=route_module(),
                    checker_module=checker_module(
                        status=3,
                        report='{"available":false,"reason":"quota exhausted"}',
                    ),
                    runner=runner,
                    resolver=lambda command: command,
                )
        self.assertEqual(runner.calls, [])

    def test_daily_tasks_profile_pins_luna_cwd_and_fresh_ephemeral_run(self):
        report = {"status": "complete", "session_persisted": False}
        with patch.object(jobs, "run_job", return_value=report) as run:
            stdout = io.StringIO()
            with patch.object(sys, "stdout", stdout), patch.dict(
                jobs.os.environ, {"LANTERN_HERD_STATE_DIR": r"C:\private\lantern"}
            ):
                status = jobs.main([
                    "research",
                    "--profile", "daily-tasks",
                    "--job", "state-2026-09-15",
                    "Read durable context and report current state.",
                ])
        self.assertEqual(status, 0)
        self.assertEqual(run.call_args.kwargs["cwd"], Path(r"C:\Claude\Daily-Tasks"))
        self.assertEqual(run.call_args.kwargs["model_phrase"], "5.6 luna xhigh fast")
        self.assertEqual(run.call_args.kwargs["mode"], "research")
        self.assertFalse(json_from(stdout.getvalue())["session_persisted"])

    def test_daily_tasks_profile_rejects_model_or_cwd_override(self):
        for extra in (("--model", "astra"), ("--cwd", r"C:\Claude\Other")):
            with self.subTest(extra=extra):
                with patch.object(sys, "stderr", io.StringIO()), patch.dict(
                    jobs.os.environ, {"LANTERN_HERD_STATE_DIR": r"C:\private\lantern"}
                ):
                    status = jobs.main([
                        "research", "--profile", "daily-tasks", "--job", "once",
                        *extra, "Read only.",
                    ])
                self.assertEqual(status, 2)


def json_from(value):
    import json
    return json.loads(value)


if __name__ == "__main__":
    unittest.main()
