#!/usr/bin/env python3
"""Capture and safely delete the exact Codex session used by Lantern."""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
from win_cmd import cmd_argv


UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def fail(message: str) -> "NoReturn":
    print(f"lantern session: {message}", file=sys.stderr)
    raise SystemExit(1)


def valid_identity(value: str, name: str) -> str:
    value = value.strip()
    if not value or any(ch in value for ch in "\r\n\t"):
        fail(f"invalid {name}")
    return value


def load_record(path: Path, pane: str, workspace: str) -> dict[str, str]:
    if path.is_symlink():
        fail("refusing a symlinked identity receipt")
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        fail(f"could not read the private identity receipt: {exc}")
    if not isinstance(record, dict):
        fail("identity receipt is not an object")
    if record.get("pane_id") != pane or record.get("workspace_id") != workspace:
        fail("identity receipt does not match the exact Lantern pane/workspace")
    session_id = record.get("codex_session_id")
    if not isinstance(session_id, str) or not UUID_RE.fullmatch(session_id):
        fail("identity receipt has no valid Codex session UUID")
    return record


def capture(args: argparse.Namespace) -> None:
    session_id = os.environ.get("CODEX_SESSION_ID", "")
    if not UUID_RE.fullmatch(session_id):
        fail("CODEX_SESSION_ID is missing or is not a UUID")
    pane = valid_identity(args.pane, "pane id")
    workspace = valid_identity(args.workspace, "workspace id")
    path = Path(args.path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        fail("refusing to replace a symlinked identity receipt")
    try:
        path.parent.chmod(0o700)
    except OSError:
        pass
    record = {
        "codex_session_id": session_id,
        "pane_id": pane,
        "workspace_id": workspace,
    }
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        if os.name != "nt":
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(record, handle, separators=(",", ":"), sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        try:
            path.chmod(0o600)
        except OSError:
            pass
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def show(args: argparse.Namespace) -> None:
    record = load_record(Path(args.path), args.pane, args.workspace)
    print(record["codex_session_id"])


def foreground_processes(pane: str) -> list[dict]:
    try:
        payload = json.load(sys.stdin)
        info = payload["result"]["process_info"]
        if info["pane_id"] != pane:
            fail("process metadata belongs to a different pane")
        processes = info["foreground_processes"]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        fail("could not parse exact pane process metadata")
    if not isinstance(processes, list) or not all(isinstance(item, dict) for item in processes):
        fail("invalid foreground process metadata")
    return processes


def process_name(process: dict) -> str:
    name = str(process.get("name", "")).lower()
    return name[:-4] if name.endswith(".exe") else name


def kind_from_json(args: argparse.Namespace) -> None:
    names = [process_name(item) for item in foreground_processes(args.pane)]
    known = [name for name in names if name in {"codex", "claude", "grok", "agent", "devin", "pi"}]
    if len(known) != 1:
        fail("expected exactly one recognized foreground helper process")
    print("codex" if known[0] == "codex" else "other")


def pid_from_json(args: argparse.Namespace) -> None:
    processes = foreground_processes(args.pane)
    matches = []
    for process in processes:
        name = str(process.get("name", "")).lower()
        argv0 = Path(str(process.get("argv0", ""))).name.lower()
        if name in {"codex", "codex.exe"} or argv0 in {"codex", "codex.exe"}:
            pid = process.get("pid")
            if isinstance(pid, int) and pid > 0:
                matches.append(pid)
    if len(matches) != 1:
        fail("expected exactly one foreground Codex process in the Lantern pane")
    print(matches[0])


def process_state(pid: int) -> str:
    """Return alive, exited, or unknown without signalling the process."""
    if os.name == "nt":
        process_query_limited_information = 0x1000
        still_active = 259
        handle = ctypes.windll.kernel32.OpenProcess(  # type: ignore[attr-defined]
            process_query_limited_information, False, pid
        )
        if not handle:
            error = ctypes.windll.kernel32.GetLastError()  # type: ignore[attr-defined]
            return "exited" if error == 87 else "unknown"
        try:
            code = ctypes.c_ulong()
            if not ctypes.windll.kernel32.GetExitCodeProcess(  # type: ignore[attr-defined]
                handle, ctypes.byref(code)
            ):
                return "unknown"
            return "alive" if code.value == still_active else "exited"
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)  # type: ignore[attr-defined]
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return "exited"
    except PermissionError:
        return "unknown"
    return "alive"


def delete(args: argparse.Namespace) -> None:
    record = load_record(Path(args.path), args.pane, args.workspace)
    session_id = record["codex_session_id"]
    deadline = time.monotonic() + args.timeout
    while True:
        state = process_state(args.pid)
        if state == "exited":
            break
        if state == "unknown":
            fail("could not prove the Lantern Codex process exited; session retained")
        if time.monotonic() >= deadline:
            fail("Lantern Codex process is still running; session retained")
        time.sleep(0.25)
    executable = shutil.which(args.codex) or args.codex
    command = [executable, "delete", session_id, "--force"]
    if os.name == "nt" and Path(executable).suffix.lower() in {".cmd", ".bat"}:
        command = cmd_argv(command, os.environ.get("COMSPEC", "cmd.exe"))
    try:
        completed = subprocess.run(
            command, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
    except OSError as exc:
        fail(f"could not invoke supported Codex deletion; session retained: {exc}")
    if completed.returncode != 0:
        fail("supported exact-session deletion failed; session retained")
    print(session_id)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    commands = root.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--path", required=True)
    common.add_argument("--pane", required=True)
    common.add_argument("--workspace", required=True)

    capture_parser = commands.add_parser("capture", parents=[common])
    capture_parser.set_defaults(func=capture)
    show_parser = commands.add_parser("show", parents=[common])
    show_parser.set_defaults(func=show)
    pid_parser = commands.add_parser("pid-from-json")
    pid_parser.add_argument("--pane", required=True)
    pid_parser.set_defaults(func=pid_from_json)
    kind_parser = commands.add_parser("kind-from-json")
    kind_parser.add_argument("--pane", required=True)
    kind_parser.set_defaults(func=kind_from_json)
    delete_parser = commands.add_parser("delete", parents=[common])
    delete_parser.add_argument("--pid", required=True, type=int)
    delete_parser.add_argument("--codex", default="codex")
    delete_parser.add_argument("--timeout", type=float, default=30.0)
    delete_parser.set_defaults(func=delete)
    return root


def main() -> None:
    args = parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
