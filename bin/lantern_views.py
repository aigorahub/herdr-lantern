#!/usr/bin/env python3
"""Open Lantern readouts in separate tabs without changing chat focus."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

import field_status as field
from usage_runtime import FileLock, private_dir, server_identity

VIEWS = {"field": ("Field Status", "field_status.py"), "usage": ("Plan Usage", "plan_usage.py")}


def watcher_command(root: Path, state: Path, view: str, shell: str | None = None) -> str:
    shell = shell or field.pane_shell()
    script = root / "bin" / VIEWS[view][1]
    if shell == "powershell":
        return (f"& {field.powershell_quote(sys.executable)} "
                f"{field.powershell_quote(str(script).replace('/', chr(92)))} "
                f"--state-dir {field.powershell_quote(str(state).replace('/', chr(92)))} watch")
    if shell != "sh":
        raise RuntimeError("Unsupported readout shell")
    return (f"{field.shell_quote(Path(sys.executable))} {field.shell_quote(script)} "
            f"--state-dir {field.shell_quote(state)} watch")


def matches_watcher(proc: dict, root: Path, state: Path, view: str) -> bool:
    argv = proc.get("argv")
    if not isinstance(argv, list) or "watch" not in argv or "--state-dir" not in argv:
        return False
    try:
        script = root / "bin" / VIEWS[view][1]
        scope = argv[argv.index("--state-dir") + 1]
        return (any(os.path.normcase(str(Path(str(arg)).resolve())) == os.path.normcase(str(script.resolve()))
                    for arg in argv[1:])
                and os.path.normcase(str(Path(scope).resolve())) == os.path.normcase(str(state.resolve())))
    except (IndexError, TypeError, ValueError):
        return False


def occupancy(info: dict, root: Path, state: Path, view: str) -> str:
    processes = info.get("foreground_processes")
    shell = info.get("shell_pid")
    if not isinstance(processes, list):
        return "unknown"
    if any(matches_watcher(proc, root, state, view) for proc in processes if isinstance(proc, dict)):
        return "running"
    if any(proc.get("pid") != shell for proc in processes if isinstance(proc, dict)):
        return "occupied"
    if isinstance(shell, int) and shell > 0 and info.get("interactive_ready") is not False:
        return "idle"
    return "starting"


def shell_ready(binary: str, pane: str, info: dict, timeout: float) -> bool:
    if info.get("interactive_ready") is True:
        return True
    # A shell PID exists during login scripts. Do not type before its prompt.
    response = subprocess.run(field.herdr_command(binary, ["pane", "read", pane,
                              "--source", "visible", "--lines", "4"]),
                              env=field.herdr_environment(binary), capture_output=True,
                              text=True, timeout=timeout, check=False)
    if response.returncode:
        return False
    lines = response.stdout.rstrip().splitlines()
    return bool(lines and lines[-1].rstrip().endswith(("%", "$", "#", ">", "❯", "➜", "λ")))


def setup(state: Path, root: Path, binary: str, workspace: str, home: str,
          cwd: str, timeout: float = 15, view: str = "both") -> dict:
    if os.environ.get("HERDR_ENV") != "1":
        raise RuntimeError("Readout setup requires Herdr")
    if not workspace or not home or not Path(cwd).is_absolute():
        raise RuntimeError("Lantern setup context unavailable")
    private_dir(state)
    deadline = time.monotonic() + min(timeout, 15)

    def call(args: list[str], end: float = deadline) -> dict:
        remaining = end - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("Readout setup timed out")
        return field.control(binary, args, min(2, remaining))

    chat = call(["pane", "get", home]).get("pane", {})
    if chat.get("workspace_id") != workspace or chat.get("label") != "Lantern":
        raise RuntimeError("Lantern chat identity changed")
    home_tab = chat.get("tab_id")
    server = server_identity(binary, min(2, max(0.1, deadline - time.monotonic())))
    lock = FileLock(state / "view-setup.lock")
    requested = list(VIEWS) if view == "both" else [view]
    try:
        while not lock.acquire():
            if time.monotonic() + 0.1 >= deadline:
                completed = field.read_json(state / "views.json", {})
                if (completed.get("setup_complete") and completed.get("server") == server
                        and completed.get("home_pane_id") == home and completed.get("workspace_id") == workspace
                        and not completed.get("errors") and all(
                            completed.get("views", {}).get(kind, {}).get("status") == "running"
                            and completed["views"][kind].get("command") == watcher_command(root, state, kind)
                            for kind in requested)):
                    return completed
                return {"busy": True, "views": {}, "errors": ["Readout setup is in progress"]}
            time.sleep(0.1)
        path = state / "views.json"
        record = field.read_json(path, {})
        owned = (record.get("home_pane_id") == home and record.get("server") == server
                 and bool(server["lifetime"]))
        saved = record.get("views", {}) if owned else {}
        record = {"schema": 1, "home_pane_id": home, "workspace_id": workspace,
                  "server": server, "views": saved, "errors": [], "setup_complete": False}
        field.write_json(path, record)
        panes = []

        def check_context(end: float) -> None:
            if server_identity(binary, min(2, max(0.1, end - time.monotonic()))) != server:
                raise RuntimeError("Herdr server identity changed")
            current_chat = call(["pane", "get", home], end).get("pane", {})
            if current_chat.get("label") != "Lantern" or current_chat.get("workspace_id") != workspace:
                raise RuntimeError("Lantern chat identity changed")

        for kind in requested:
            end = min(deadline, time.monotonic() + 7)
            label, _ = VIEWS[kind]
            try:
                tabs = call(["tab", "list", "--workspace", workspace], end).get("tabs")
                panes = call(["pane", "list", "--workspace", workspace], end).get("panes")
                if not isinstance(tabs, list) or not isinstance(panes, list):
                    raise RuntimeError("Readout layout unavailable")
                candidates = []
                for tab in tabs:
                    if tab.get("workspace_id") != workspace or tab.get("label") != label:
                        continue
                    roots = [p for p in panes if p.get("tab_id") == tab.get("tab_id")]
                    if tab.get("pane_count") == 1 and len(roots) == 1 and tab.get("tab_id") != home_tab:
                        candidates.append((tab, roots[0]))
                target = None
                current = saved.get(kind, {})
                for tab, pane in candidates:
                    pid = pane.get("pane_id")
                    if not pid or pid == home or pane.get("agent"):
                        continue
                    info = call(["pane", "process-info", "--pane", pid], end).get("process_info", {})
                    status = occupancy(info, root, state, kind)
                    if status == "running":
                        target = (tab, pane, False, status)
                        break
                    if (current.get("pane_id") == pid and current.get("tab_id") == tab.get("tab_id")
                            and current.get("command") == watcher_command(root, state, kind)):
                        if status != "idle":
                            raise RuntimeError("Recorded readout is occupied or not ready")
                        target = (tab, pane, False, status)
                        break
                if target is None:
                    if any(t.get("label") == label for t in tabs):
                        raise RuntimeError("Unowned readout label conflict")
                    check_context(end)
                    created = call(["tab", "create", "--workspace", workspace, "--cwd", cwd,
                                    "--label", label, "--no-focus"], end)
                    tab, pane = created.get("tab", {}), created.get("root_pane", {})
                    if (not tab.get("tab_id") or not pane.get("pane_id") or pane.get("pane_id") == home
                            or tab.get("tab_id") == home_tab or tab.get("workspace_id") != workspace):
                        raise RuntimeError("Herdr returned no new readout root pane")
                    target = (tab, pane, True, "starting")
                tab, pane, created, status = target
                pid = pane["pane_id"]
                entry = {"tab_id": tab["tab_id"], "pane_id": pid, "created": created,
                         "status": status, "command": watcher_command(root, state, kind)}
                record["views"][kind] = entry
                field.write_json(path, record)  # Own the new tab before submission.
                ready = status == "running"
                while not ready:
                    info = call(["pane", "process-info", "--pane", pid], end).get("process_info", {})
                    status = occupancy(info, root, state, kind)
                    if status in ("occupied", "unknown"):
                        if created and time.monotonic() < end:
                            time.sleep(0.1)
                            continue
                        raise RuntimeError("Readout shell is occupied or not ready")
                    remaining = end - time.monotonic()
                    if remaining <= 0:
                        raise RuntimeError("Readout shell readiness timed out")
                    ready = status == "running" or (status == "idle" and shell_ready(binary, pid, info, min(2, remaining)))
                    if not ready:
                        time.sleep(0.1)
                if status == "idle":
                    # Recheck immediately before typing. Never type into an agent.
                    check_context(end)
                    live = call(["pane", "get", pid], end).get("pane", {})
                    info = call(["pane", "process-info", "--pane", pid], end).get("process_info", {})
                    if (live.get("agent") or live.get("tab_id") != tab["tab_id"]
                            or live.get("workspace_id") != workspace
                            or occupancy(info, root, state, kind) != "idle"
                            or not shell_ready(binary, pid, info, min(2, max(0.01, end - time.monotonic())))):
                        raise RuntimeError("Readout shell is occupied or not ready")
                    entry["status"] = "submission pending"
                    field.write_json(path, record)
                    call(["pane", "run", pid, entry["command"]], end)
                    status = "starting"
                    while status != "running":
                        info = call(["pane", "process-info", "--pane", pid], end).get("process_info", {})
                        status = occupancy(info, root, state, kind)
                        if status != "running":
                            time.sleep(0.1)
                if status != "running":
                    raise RuntimeError("Readout watcher not confirmed")
                entry["status"] = "running"
            except (RuntimeError, OSError, ValueError, TypeError, KeyError, subprocess.TimeoutExpired) as exc:
                record["errors"].append(f"{label}: {field.clean(exc, 160)}")
                if kind in record["views"]:
                    record["views"][kind]["status"] = "start not confirmed"
            field.write_json(path, record)
        legacy = [p for p in panes
                  if p.get("tab_id") == home_tab and p.get("label") == "Field Status"]
        if legacy:
            record["notice"] = "Legacy Field Status side pane remains open"
        record["setup_complete"] = True
        field.write_json(path, record)
        return record
    finally:
        lock.close()


def open_view(state: Path, root: Path, binary: str, timeout: float, view: str) -> tuple[str, bool]:
    home = os.environ.get("HERDR_PANE_ID", "")
    if not home or home != os.environ.get("LANTERN_HOME_PANE_ID"):
        raise RuntimeError("Readout must be opened from Lantern Home")
    result = setup(state, root, binary, os.environ.get("HERDR_WORKSPACE_ID", ""),
                   home, str(Path.cwd()), timeout, view)
    if result.get("errors"):
        raise RuntimeError(result["errors"][0])
    entry = result["views"][view]
    return entry["pane_id"], entry.get("created", False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--home-pane", required=True)
    parser.add_argument("--cwd", required=True)
    parser.add_argument("--herdr", default=str(Path(__file__).with_name("herdr")))
    parser.add_argument("--timeout", type=float, default=15)
    sub = parser.add_subparsers(dest="command", required=True)
    setup_parser = sub.add_parser("setup")
    setup_parser.add_argument("--view", choices=["both", *VIEWS], default="both")
    args = parser.parse_args()
    try:
        result = setup(Path(args.state_dir), Path(__file__).resolve().parent.parent,
                       args.herdr, args.workspace, args.home_pane, args.cwd, args.timeout, args.view)
        if result.get("errors"):
            for error in result["errors"]:
                print(f"lantern: {error}", file=sys.stderr)
            return 1
        if result.get("notice"):
            print("lantern: " + result["notice"], file=sys.stderr)
        return 0
    except (RuntimeError, OSError, ValueError) as exc:
        field.write_json(Path(args.state_dir) / "view-setup-error.json",
                         {"error": field.clean(exc), "observed_at": time.time()})
        print(f"lantern: {field.clean(exc)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
