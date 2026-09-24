#!/usr/bin/env python3
"""Run a bounded Codex exec job with session persistence disabled."""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Callable, Sequence


BIN = Path(__file__).resolve().parent
JOB_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
DAILY_TASKS_CWD = Path(r"C:\Claude\Daily-Tasks")
DAILY_TASKS_MODEL = "5.6 luna xhigh fast"
PROMPT_PREFIX = """This is a bounded, one-shot Codex job.
Do not expose, copy, or persist authentication material.
If the work needs repeated steering or a durable live session, stop and say so instead of expanding scope.

Task:
"""


class JobError(RuntimeError):
    """A safe headless job could not be started or completed."""


def load_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, BIN / filename)
    if spec is None or spec.loader is None:
        raise JobError(f"could not load {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def result_paths(state_dir: Path, job: str) -> tuple[Path, Path]:
    output_dir = (state_dir / "headless").resolve()
    output_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        output_dir.chmod(0o700)
    except OSError:
        pass
    return output_dir / f"{job}.md", output_dir / f".{job}.partial.md"


def resolve_codex(argv: list[str]) -> list[str]:
    executable = shutil.which(argv[0])
    if executable is None:
        raise JobError("codex command not found")
    command = [executable, *argv[1:]]
    if os.name == "nt" and executable.lower().endswith((".cmd", ".bat")):
        command_line = subprocess.list2cmdline(command)
        return [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c", command_line]
    return command


def build_command(
    mode: str,
    cwd: Path,
    partial: Path,
    route: dict[str, object],
) -> list[str]:
    model_argv = route.get("argv")
    if not isinstance(model_argv, list) or not all(isinstance(item, str) for item in model_argv):
        raise JobError("model route returned invalid argv")
    permission_argv = ["-s", "read-only"] if mode == "research" else ["--approve-for-me"]
    command = [
        "codex",
        "exec",
        "--ephemeral",
        *model_argv,
        *permission_argv,
        "-C",
        str(cwd),
        "-o",
        str(partial),
        "-",
    ]
    forbidden = {"resume", "fork", "--dangerously-bypass-approvals-and-sandbox"}
    if forbidden.intersection(command):
        raise JobError("unsafe or persistent Codex argv in headless route")
    return command


def preflight(route: dict[str, object], checker) -> None:
    model = route.get("model")
    effort = route.get("effort") or ""
    if not isinstance(model, str) or not isinstance(effort, str):
        raise JobError("model route returned invalid identity")
    output = io.StringIO()
    try:
        with redirect_stdout(output):
            status = checker.check("codex", model, effort)
    except checker.CheckError as error:
        raise JobError(str(error)) from error
    if status == 0:
        return
    try:
        report = json.loads(output.getvalue())
        reason = report.get("reason", "model is unavailable")
    except (json.JSONDecodeError, AttributeError):
        reason = "model preflight failed"
    raise JobError(str(reason))


def run_job(
    *,
    mode: str,
    cwd: Path,
    state_dir: Path,
    job: str,
    model_phrase: str,
    prompt: str,
    route_module=None,
    checker_module=None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    resolver: Callable[[list[str]], list[str]] = resolve_codex,
) -> dict[str, object]:
    cwd = cwd.expanduser().resolve()
    state_dir = state_dir.expanduser().resolve()
    if mode not in {"research", "update"}:
        raise JobError("mode must be research or update")
    if not cwd.is_dir():
        raise JobError(f"working directory does not exist: {cwd}")
    if not prompt.strip():
        raise JobError("task prompt is empty")
    if not JOB_RE.fullmatch(job):
        raise JobError("job must match [a-z][a-z0-9_-]{0,63}")

    if inside(state_dir, cwd):
        raise JobError("headless result directory must be outside the product checkout")
    final, partial = result_paths(state_dir, job)
    for path in (final, partial):
        if path.exists():
            raise JobError(f"refusing to overwrite headless result: {path}")

    route_module = route_module or load_module("lantern_model_route", "model-route.py")
    checker_module = checker_module or load_module("lantern_model_preflight", "model-preflight.py")
    try:
        route = route_module.codex_route(model_phrase)
    except route_module.RouteError as error:
        raise JobError(str(error)) from error
    preflight(route, checker_module)
    command = build_command(mode, cwd, partial, route)

    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{job}.", suffix=".md", dir=final.parent
    )
    os.close(fd)
    temporary = Path(temporary_name)
    temporary.unlink()
    command[command.index(str(partial))] = str(temporary)
    try:
        try:
            completed = runner(
                resolver(command),
                input=PROMPT_PREFIX + prompt.strip() + "\n",
                text=True,
                check=False,
            )
        except OSError as error:
            raise JobError(f"could not run Codex: {error}") from error
        if completed.returncode != 0:
            if temporary.exists() and temporary.stat().st_size:
                temporary.replace(partial)
            raise JobError(f"Codex exited with status {completed.returncode}")
        if not temporary.exists() or temporary.stat().st_size == 0:
            raise JobError("Codex completed without a final result")
        try:
            temporary.chmod(0o600)
        except OSError:
            pass
        temporary.replace(final)
    finally:
        if temporary.exists():
            temporary.unlink()

    return {
        "status": "complete",
        "mode": mode,
        "job": job,
        "model": route["model"],
        "effort": route.get("effort"),
        "fast": route.get("fast", False),
        "result": str(final),
        "session_persisted": False,
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Run a bounded Codex job via exec --ephemeral and save its final response privately."
    )
    result.add_argument("mode", choices=("research", "update"))
    result.add_argument("--profile", choices=("daily-tasks",))
    result.add_argument("--cwd", type=Path)
    result.add_argument("--job", required=True)
    result.add_argument("--model", help="spoken Codex model phrase (default: live Codex default)")
    result.add_argument(
        "--state-dir",
        type=Path,
        help="Lantern state root (defaults to LANTERN_HERD_STATE_DIR)",
    )
    result.add_argument("prompt", nargs="?", help="task text; omit to read stdin")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    # The optional prompt may follow flags after the mode positional. Python
    # 3.12 parse_args rejects that order; the CLI has always documented it.
    args = parser().parse_intermixed_args(argv)
    cwd = args.cwd
    model_phrase = args.model or "default"
    if args.profile == "daily-tasks":
        if cwd is not None and cwd.expanduser().resolve() != DAILY_TASKS_CWD.resolve():
            print(f"codex-headless: daily-tasks cwd is fixed at {DAILY_TASKS_CWD}", file=sys.stderr)
            return 2
        if args.model is not None and args.model.strip().lower() != DAILY_TASKS_MODEL:
            print(f"codex-headless: daily-tasks model is fixed at {DAILY_TASKS_MODEL}", file=sys.stderr)
            return 2
        cwd = DAILY_TASKS_CWD
        model_phrase = DAILY_TASKS_MODEL
    if cwd is None:
        print("codex-headless: --cwd is required without --profile daily-tasks", file=sys.stderr)
        return 2
    state_dir = args.state_dir
    if state_dir is None:
        value = os.environ.get("LANTERN_HERD_STATE_DIR", "")
        if not value:
            print("codex-headless: LANTERN_HERD_STATE_DIR is not set", file=sys.stderr)
            return 2
        state_dir = Path(value)
    prompt = args.prompt if args.prompt is not None else sys.stdin.read()
    try:
        report = run_job(
            mode=args.mode,
            cwd=cwd,
            state_dir=state_dir,
            job=args.job,
            model_phrase=model_phrase,
            prompt=prompt,
        )
    except JobError as error:
        print(f"codex-headless: {error}", file=sys.stderr)
        return 1
    print(json.dumps(report, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
