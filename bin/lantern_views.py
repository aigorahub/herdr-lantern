#!/usr/bin/env python3
"""Open Lantern readouts beside chat without changing focus."""
from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import time
from pathlib import Path

import field_status as field
from usage_runtime import FileLock, private_dir, server_identity

VIEWS = {"field": ("Field Status", "field_status.py"), "usage": ("Plan Usage", "plan_usage.py")}
RIGHT_SPLIT_RATIO = "0.6666666667"
LAYOUT = "field-above-usage"


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


def code_stamp(root: Path) -> str:
    """Fingerprint the readout code on disk."""
    digest = hashlib.sha256()
    for path in sorted((root / "bin").glob("*.py")):
        digest.update(path.name.encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def record_watcher(state: Path, view: str) -> None:
    """Record the code a starting watcher runs, so setup can replace it after an update."""
    field.write_json(private_dir(state) / f"{view}-watcher.json",
                     {"pid": os.getpid(), "code": code_stamp(Path(__file__).resolve().parent.parent)})


def watcher_processes(info: dict, root: Path, state: Path, view: str) -> list:
    """Identify a pane's watcher by PID and arguments. Its short-lived children do not count."""
    return [(proc.get("pid"), proc.get("argv")) for proc in info.get("foreground_processes") or []
            if isinstance(proc, dict) and matches_watcher(proc, root, state, view)]


def current_watcher(info: dict, root: Path, state: Path, view: str) -> bool:
    """A running watcher is current only when it recorded the code now on disk."""
    record = field.read_json(state / f"{view}-watcher.json", {})
    return (record.get("code") == code_stamp(root)
            and any(isinstance(proc, dict) and proc.get("pid") == record.get("pid")
                    and matches_watcher(proc, root, state, view)
                    for proc in info.get("foreground_processes") or []))


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
    deadline = time.monotonic() + timeout

    def call(args: list[str], end: float = deadline) -> dict:
        remaining = end - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("Readout setup timed out")
        return field.control(binary, args, min(2 if timeout <= 15 else 5, remaining))

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
                if (completed.get("setup_complete") and completed.get("layout") == LAYOUT and completed.get("server") == server
                        and completed.get("home_pane_id") == home and completed.get("workspace_id") == workspace
                        and not completed.get("errors") and all(
                            completed.get("views", {}).get(kind, {}).get("status") == "running"
                            and completed["views"][kind].get("tab_id") == home_tab
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
        record = {"schema": 2, "layout": LAYOUT, "home_pane_id": home, "workspace_id": workspace,
                  "server": server, "views": saved, "errors": [], "setup_complete": False}
        field.write_json(path, record)
        def check_context(end: float) -> None:
            if server_identity(binary, min(2, max(0.1, end - time.monotonic()))) != server:
                raise RuntimeError("Herdr server identity changed")
            current_chat = call(["pane", "get", home], end).get("pane", {})
            if (current_chat.get("label") != "Lantern" or current_chat.get("workspace_id") != workspace
                    or current_chat.get("tab_id") != home_tab):
                raise RuntimeError("Lantern chat identity changed")

        def find_target(kind: str, end: float):
            label, _ = VIEWS[kind]
            tabs = call(["tab", "list", "--workspace", workspace], end).get("tabs")
            panes = call(["pane", "list", "--workspace", workspace], end).get("panes")
            if not isinstance(tabs, list) or not isinstance(panes, list):
                raise RuntimeError("Readout layout unavailable")
            current = saved.get(kind, {})
            candidates = []
            for pane in panes:
                tab = next((t for t in tabs if t.get("tab_id") == pane.get("tab_id")), {})
                if (pane.get("workspace_id") != workspace or pane.get("pane_id") == home
                        or tab.get("workspace_id") != workspace):
                    continue
                dedicated = tab.get("label") == label and tab.get("pane_count") == 1
                if (pane.get("label") == label or dedicated
                        or pane.get("pane_id") == current.get("pane_id")):
                    candidates.append(pane)
            for pane in candidates:
                pid = pane.get("pane_id")
                if not pid or pane.get("agent"):
                    continue
                info = call(["pane", "process-info", "--pane", pid], end).get("process_info", {})
                status = occupancy(info, root, state, kind)
                if status == "running":
                    return pane, status
                if (current.get("pane_id") == pid and (current.get("tab_id") == pane.get("tab_id")
                         or current.get("layout_phase") == "moving" and pane.get("label") == label)
                        and current.get("command") == watcher_command(root, state, kind)):
                    settle = min(end, time.monotonic() + 2)
                    while status not in ("idle", "running") and time.monotonic() < settle:
                        time.sleep(0.1)
                        live = call(["pane", "get", pid], end).get("pane", {})
                        if live.get("agent") or live.get("tab_id") != pane.get("tab_id"):
                            raise RuntimeError("Recorded readout identity changed")
                        info = call(["pane", "process-info", "--pane", pid], end).get("process_info", {})
                        status = occupancy(info, root, state, kind)
                    if status not in ("idle", "running"):
                        raise RuntimeError("Recorded readout is occupied or not ready")
                    return pane, status
            # Recover a running watcher after a lost move/rename response.
            for pane in panes:
                if (pane.get("tab_id") != home_tab or pane.get("pane_id") == home
                        or pane.get("agent") or pane in candidates):
                    continue
                info = call(["pane", "process-info", "--pane", pane["pane_id"]], end).get("process_info", {})
                if occupancy(info, root, state, kind) == "running":
                    return pane, "running"
            if candidates:
                raise RuntimeError("Unowned readout label conflict")
            return None, None

        def place(kind: str, pane: dict | None, end: float) -> dict:
            # Only exact watchers or saved idle shells can move into the home tab.
            # Keep existing ratios when both readouts are already in place.
            other = "field" if kind == "usage" else "usage"
            sibling, _ = find_target(other, end)
            anchor = sibling["pane_id"] if sibling and sibling.get("tab_id") == home_tab else home
            direction = "down" if anchor != home else "right"
            if not pane or pane.get("tab_id") != home_tab:
                check_context(end)
                layout = call(["pane", "layout", "--pane", home], end).get("layout", {})
                allowed = {home, anchor}
                if any(p.get("pane_id") not in allowed for p in layout.get("panes", [])):
                    raise RuntimeError("Home tab contains an unowned pane")
                if pane:
                    live = call(["pane", "get", pane["pane_id"]], end).get("pane", {})
                    info = call(["pane", "process-info", "--pane", pane["pane_id"]], end).get("process_info", {})
                    if (live.get("agent") or live.get("workspace_id") != workspace
                            or live.get("tab_id") != pane.get("tab_id")
                            or occupancy(info, root, state, kind) not in ("idle", "running")):
                        raise RuntimeError("Readout identity changed before move")
                    response = call(["pane", "move", pane["pane_id"], "--tab", home_tab,
                                     "--target-pane", anchor, "--split", direction,
                                     "--ratio", RIGHT_SPLIT_RATIO if direction == "down" else "0.5", "--no-focus"], end)
                    pane = response.get("move_result", {}).get("pane", {})
                else:
                    pane = call(["pane", "split", anchor, "--direction", direction,
                                 "--ratio", RIGHT_SPLIT_RATIO if direction == "down" else "0.5", "--cwd", cwd, "--no-focus"], end).get("pane", {})
                if (not pane.get("pane_id") or pane.get("pane_id") == home
                        or pane.get("tab_id") != home_tab or pane.get("workspace_id") != workspace):
                    raise RuntimeError("Herdr returned no readout pane in the home tab")
                # Save ownership before a label or shell command can fail.
                record["views"][kind] = {"tab_id": home_tab, "pane_id": pane["pane_id"],
                    "command": watcher_command(root, state, kind), "status": "starting"}
                field.write_json(path, record)
                call(["pane", "rename", pane["pane_id"], VIEWS[kind][0]], end)
            if sibling and sibling.get("tab_id") == home_tab:
                layout = call(["pane", "layout", "--pane", home], end).get("layout", {})
                rects = {p["pane_id"]: p.get("rect", {}) for p in layout.get("panes", [])}
                usage = rects.get(pane["pane_id"] if kind == "usage" else sibling["pane_id"], {})
                activity = rects.get(pane["pane_id"] if kind == "field" else sibling["pane_id"], {})
                chat_rect = rects.get(home, {})
                if (not usage or not activity or not chat_rect
                        or usage.get("x") != activity.get("x")
                        or usage["x"] <= chat_rect["x"]):
                    raise RuntimeError("Readout panes are outside the right column")
                if activity["y"] > usage["y"]:
                    check_context(end)
                    for target, target_kind in ((pane, kind), (sibling, other)):
                        settle = min(end, time.monotonic() + 2)
                        while True:
                            live = call(["pane", "get", target["pane_id"]], end).get("pane", {})
                            info = call(["pane", "process-info", "--pane", target["pane_id"]], end).get("process_info", {})
                            status = occupancy(info, root, state, target_kind)
                            if (live.get("agent") or live.get("tab_id") != home_tab
                                    or live.get("workspace_id") != workspace):
                                raise RuntimeError("Readout identity changed before layout repair")
                            if status == "running" or (status == "idle" and shell_ready(binary,
                                    target["pane_id"], info, min(2, max(.01, end - time.monotonic())))):
                                break
                            if time.monotonic() >= settle:
                                raise RuntimeError("Readout shell is occupied or not ready for layout repair")
                            time.sleep(.1)
                    usage_pane = pane if kind == "usage" else sibling
                    field_pane = pane if kind == "field" else sibling
                    # Herdr swap focuses its source. Move usage under Field Status
                    # with no focus instead. Detaching usage expands Field Status.
                    record["views"]["usage"] = dict(record["views"].get("usage", {}),
                        pane_id=usage_pane["pane_id"], tab_id=home_tab, layout_phase="moving",
                        command=watcher_command(root, state, "usage"))
                    field.write_json(path, record)
                    # A move to the current tab is a no-op. Move through an owned
                    # temporary tab, then return below Field Status. Both moves keep focus.
                    staged = call(["pane", "move", usage_pane["pane_id"], "--new-tab",
                                   "--workspace", workspace, "--label", VIEWS["usage"][0],
                                   "--no-focus"], end).get("move_result", {}).get("pane", {})
                    if (not staged.get("pane_id") or staged.get("workspace_id") != workspace
                            or not staged.get("tab_id") or staged.get("tab_id") == home_tab):
                        raise RuntimeError("Herdr returned no temporary readout tab")
                    record["views"]["usage"].update(pane_id=staged["pane_id"], tab_id=staged["tab_id"])
                    field.write_json(path, record)
                    check_context(end)
                    live = call(["pane", "get", staged["pane_id"]], end).get("pane", {})
                    info = call(["pane", "process-info", "--pane", staged["pane_id"]], end).get("process_info", {})
                    if (live.get("agent") or live.get("tab_id") != staged["tab_id"]
                            or live.get("workspace_id") != workspace
                            or occupancy(info, root, state, "usage") not in ("idle", "running")):
                        raise RuntimeError("Readout identity changed during layout repair")
                    moved = call(["pane", "move", staged["pane_id"], "--tab", home_tab,
                                  "--target-pane", field_pane["pane_id"], "--split", "down",
                                  "--ratio", RIGHT_SPLIT_RATIO, "--no-focus"], end).get("move_result", {}).get("pane", {})
                    if (not moved.get("pane_id") or moved.get("tab_id") != home_tab
                            or moved.get("workspace_id") != workspace):
                        raise RuntimeError("Herdr returned no readout pane after layout repair")
                    record["views"]["usage"] = dict(record["views"].get("usage", {}),
                        pane_id=moved["pane_id"], tab_id=home_tab,
                        command=watcher_command(root, state, "usage"))
                    record["views"]["usage"].pop("layout_phase", None)
                    field.write_json(path, record)
                    if kind == "usage":
                        pane = moved
            return pane

        def stop_outdated(kind: str, pane: dict, end: float) -> str:
            # A reinstall leaves the old watcher running its old code. Stop it
            # like a person would, then start the new one in the same shell.
            pid = pane["pane_id"]
            info = call(["pane", "process-info", "--pane", pid], end).get("process_info", {})
            if current_watcher(info, root, state, kind):
                return "running"
            watcher = watcher_processes(info, root, state, kind)
            check_context(end)
            live = call(["pane", "get", pid], end).get("pane", {})
            # Recheck immediately before the key press. Never interrupt a
            # command that replaced the watcher during the checks above.
            latest = call(["pane", "process-info", "--pane", pid], end).get("process_info", {})
            if (live.get("agent") or live.get("tab_id") != pane.get("tab_id") or not watcher
                    or watcher_processes(latest, root, state, kind) != watcher):
                raise RuntimeError("Readout identity changed before restart")
            call(["pane", "send-keys", pid, "ctrl+c"], end)
            settle = min(end, time.monotonic() + 3)
            while occupancy(info, root, state, kind) != "idle":
                if time.monotonic() >= settle:
                    raise RuntimeError("Outdated readout did not stop")
                time.sleep(0.1)
                info = call(["pane", "process-info", "--pane", pid], end).get("process_info", {})
            return "idle"

        for kind in requested:
            end = min(deadline, time.monotonic() + max(7, timeout / 2))
            label, _ = VIEWS[kind]
            try:
                pane, status = find_target(kind, end)
                if status == "running":
                    status = stop_outdated(kind, pane, end)
                created = pane is None
                pane = place(kind, pane, end)
                tab = {"tab_id": home_tab}
                pid = pane["pane_id"]
                status = status or "starting"
                entry = {"tab_id": tab["tab_id"], "pane_id": pid, "created": created,
                         "status": status, "command": watcher_command(root, state, kind)}
                record["views"][kind] = entry
                field.write_json(path, record)  # Own the pane before submission.
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
