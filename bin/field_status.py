#!/usr/bin/env python3
"""Compact, persistent, read-only Herdr field view for Lantern."""
from __future__ import annotations

import argparse
import calendar
import hashlib
import uuid
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from terminal_readout import Readout

CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
ANSI = {"yellow": "\x1b[33m", "green": "\x1b[32m", "blue": "\x1b[34m", "red": "\x1b[31m", "purple": "\x1b[35m"}
RESET = "\x1b[0m"
ORDER = {"working": 0, "blocked": 1, "error": 1, "done": 2, "idle": 3, "unknown": 4}
# An agent in one of these states has stopped its turn and awaits review.
STOPPED = ("done", "idle", "blocked", "error")


def clean(value: object, limit: int = 90) -> str:
    text = CONTROL.sub(" ", str(value or ""))
    text = " ".join(text.split())
    return text[: limit - 1] + "…" if len(text) > limit else text


def eastern(now: datetime) -> datetime:
    try:
        return now.astimezone(ZoneInfo("America/New_York"))
    except ZoneInfoNotFoundError:
        # Windows Python may lack the optional tzdata package. US Eastern DST
        # starts at 07:00 UTC on the second Sunday in March, ends at 06:00 UTC
        # on the first Sunday in November.
        year = now.year
        march = 8 + (6 - calendar.weekday(year, 3, 8)) % 7
        november = 1 + (6 - calendar.weekday(year, 11, 1)) % 7
        start = datetime(year, 3, march, 7, tzinfo=timezone.utc)
        end = datetime(year, 11, november, 6, tzinfo=timezone.utc)
        offset = -4 if start <= now.astimezone(timezone.utc) < end else -5
        return now.astimezone(timezone(timedelta(hours=offset), "ET"))


def read_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return default
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"invalid field status state: {path}")
    return data


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError(f"refusing symlinked field status state: {path}")
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def resolve_herdr(explicit: str) -> str:
    for candidate in (explicit, os.environ.get("HERDR_REAL", ""), os.environ.get("HERDR_BIN_PATH", ""), shutil.which("herdr") or ""):
        if candidate and (Path(candidate).is_file() or shutil.which(candidate)):
            return candidate
    raise RuntimeError("Herdr binary unavailable")


def herdr_command(binary: str, args: list[str]) -> list[str]:
    """Run Lantern's extensionless shell wrapper through Git sh on Windows."""
    if os.name == "nt" and Path(binary).suffix == "":
        shell = shutil.which("sh")
        if not shell:
            raise RuntimeError("Git sh unavailable for Lantern's Herdr wrapper")
        return [shell, binary, *args]
    return [binary, *args]


def herdr_environment(binary: str) -> dict[str, str]:
    env = os.environ.copy()
    if os.name == "nt" and Path(binary).suffix == "":
        # Git Bash exports this as C:/... to native Python. Passing that form
        # back to sh makes bin/herdr mistake itself for the real binary.
        env.pop("HERDR_BIN_PATH", None)
    return env


def herdr_list(binary: str, item: str, timeout: float) -> list[dict]:
    try:
        proc = subprocess.run(herdr_command(binary, [item, "list"]), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout, check=False,
                              env=herdr_environment(binary))
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Herdr {item} list unavailable: {exc}") from exc
    if proc.returncode:
        raise RuntimeError(f"Herdr {item} list failed ({proc.returncode})")
    try:
        result = json.loads(proc.stdout)["result"][item + "s"]
    except (ValueError, KeyError, TypeError) as exc:
        raise RuntimeError(f"Herdr {item} list returned invalid JSON") from exc
    if not isinstance(result, list):
        raise RuntimeError(f"Herdr {item} list returned invalid rows")
    return [row for row in result if isinstance(row, dict)]


def snapshot(binary: str, timeout: float) -> tuple[list[dict], list[dict], list[dict]]:
    # Fail closed: an unavailable source must never turn a live tab into a
    # fictitious closed agent or overwrite a valid prior snapshot.
    import desktop
    if desktop.reads(binary):
        view = desktop.read_world(timeout).view
        return view["tabs"], view["agents"], view["workspaces"]
    return (herdr_list(binary, "tab", timeout), herdr_list(binary, "agent", timeout),
            herdr_list(binary, "workspace", timeout))


def control(binary: str, args: list[str], timeout: float) -> dict:
    """Use Lantern's gated Herdr wrapper for layout changes."""
    command = herdr_command(binary, args)
    env = herdr_environment(binary)
    if args[:2] in (["pane", "run"], ["pane", "split"], ["pane", "move"],
                    ["pane", "rename"], ["pane", "send-keys"], ["tab", "create"]):
        env["HERDR_HELPER_OK"] = "1"
    try:
        proc = subprocess.run(command, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout,
                              env=env, check=False)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Herdr {' '.join(args[:2])} timed out after {timeout:g}s") from None
    except OSError as exc:
        raise RuntimeError(f"Herdr {' '.join(args[:2])} unavailable: {exc}") from exc
    if proc.returncode:
        raise RuntimeError(f"Herdr {' '.join(args[:2])} failed ({proc.returncode}): {clean(proc.stderr, 180)}")
    if args[:2] in (["pane", "run"], ["pane", "send-keys"]) and not proc.stdout.strip():
        # The installed CLI returns no body after a successful submission or
        # key press. Callers still confirm the pane's processes afterward.
        return {}
    try:
        result = json.loads(proc.stdout)["result"]
    except (ValueError, KeyError, TypeError) as exc:
        raise RuntimeError(f"Herdr {' '.join(args[:2])} returned invalid JSON") from exc
    if not isinstance(result, dict):
        raise RuntimeError(f"Herdr {' '.join(args[:2])} returned invalid result")
    return result


def shell_quote(path: Path) -> str:
    value = path.as_posix()
    return "'" + value.replace("'", "'\"'\"'") + "'"


def powershell_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def watcher_command(plugin_root: Path, state_dir: Path, *, shell: str, python: str) -> str:
    """Build the text pane run will type into the new pane's own shell."""
    if shell == "sh":
        script = plugin_root / "bin" / "field-status"
        return f"sh {shell_quote(script)} --state-dir {shell_quote(state_dir)} watch"
    if shell == "powershell":
        # Windows panes start in PowerShell. POSIX quotes never reach sh.
        # Keep backslashes on every host so the typed command does not depend
        # on which OS built the string.
        script = str(plugin_root / "bin" / "field_status.py").replace("/", "\\")
        state = str(state_dir).replace("/", "\\")
        return (
            f"& {powershell_quote(python)} {powershell_quote(script)} "
            f"--state-dir {powershell_quote(state)} watch"
        )
    raise RuntimeError(f"unsupported Field Status pane shell: {shell}")


def pane_shell() -> str:
    return "powershell" if os.name == "nt" else "sh"


def start_watcher(binary: str, pane_id: str, plugin_root: Path, state_dir: Path, timeout: float) -> None:
    command = watcher_command(
        plugin_root, state_dir, shell=pane_shell(), python=sys.executable
    )
    control(binary, ["pane", "run", pane_id, command], timeout)


def watcher_occupancy(info: object) -> str:
    """Return running or idle. An unrelated foreground process is an error."""
    if not isinstance(info, dict):
        raise RuntimeError("Field Status pane process state unavailable")
    processes = info.get("foreground_processes")
    if not isinstance(processes, list):
        raise RuntimeError("Field Status pane process state unavailable")
    if any(
        isinstance(proc, dict)
        and "field_status.py" in str(proc.get("cmdline", ""))
        and "watch" in str(proc.get("cmdline", ""))
        for proc in processes
    ):
        return "running"
    shell_pid = info.get("shell_pid")
    if any(isinstance(proc, dict) and proc.get("pid") != shell_pid for proc in processes):
        raise RuntimeError("Field Status pane is occupied by another process")
    return "idle"


def open_pane(state_dir: Path, plugin_root: Path, binary: str, timeout: float) -> tuple[str, bool]:
    """Open or reuse the dedicated Field Status tab."""
    from lantern_views import open_view
    return open_view(state_dir, plugin_root, binary, timeout, "field")


def rows_for(tabs: list[dict], agents: list[dict], workspaces: list[dict]) -> list[dict]:
    by_tab: dict[str, list[dict]] = {}
    for agent in agents:
        if agent.get("tab_id"):
            by_tab.setdefault(str(agent["tab_id"]), []).append(agent)
    by_workspace = {str(workspace.get("workspace_id")): workspace for workspace in workspaces}
    rows = []
    for index, tab in enumerate(tabs):
        tab_id = clean(tab.get("tab_id"))
        if not tab_id:
            continue
        workspace = by_workspace.get(str(tab.get("workspace_id")), {})
        occupants = by_tab.get(tab_id) or [{}]
        for occupant_index, agent in enumerate(occupants):
            raw_status = clean(agent.get("agent_status") or tab.get("agent_status") or "unknown").lower()
            if raw_status not in ORDER:
                raw_status = "unknown"
            if not agent:
                raw_status = "idle"  # A shell is kept, never called done.
            agent_name = agent.get("name") or agent.get("agent") or "shell"
            if len(occupants) > 1 and not agent.get("name"):
                agent_name = f"{agent_name} · {agent.get('pane_id') or occupant_index + 1}"
            rows.append({
                "tab_id": tab_id,
                "pane_id": clean(agent.get("pane_id")),
                "terminal_id": clean(agent.get("terminal_id")),
                "workspace": clean(workspace.get("label") or tab.get("workspace_id") or "?"),
                "tab": clean(tab.get("label") or tab_id),
                "agent": clean(agent_name),
                "raw_status": raw_status,
                "state_change_seq": agent.get("state_change_seq"),
                "completion_seq": agent.get("completion_seq"),
                "agent_session": agent.get("agent_session"),
                "has_agent": bool(agent),
                "sort_index": index * 1000 + occupant_index,
            })
    return rows


def reconcile(live: list[dict], previous: dict, now: datetime) -> list[dict]:
    old = {row.get("pane_id"): row for row in previous.get("rows", [])}
    for row in live:
        prior = old.get(row.get("pane_id"), {})
        same_session = all(row.get(key) == prior.get(key) for key in
                           ("tab_id", "terminal_id", "agent_session", "agent"))
        epoch = prior.get("work_epoch", 0) if same_session else 0
        if not same_session or (row["raw_status"] == "working" and prior.get("raw_status") != "working"):
            epoch += 1
        row["work_epoch"] = epoch
        row["session_epoch"] = prior.get("session_epoch") if same_session else None
        if not row["session_epoch"]:
            row["session_epoch"] = uuid.uuid4().hex
    return live


def acceptance_state(state_dir: Path) -> dict:
    data = read_json(state_dir / "field-status-outcomes.json", {"tasks": {}, "outcomes": {}})
    if not isinstance(data.get("tasks"), dict) or not isinstance(data.get("outcomes"), dict):
        raise ValueError("invalid field status outcomes")
    return data


def outcome_token(row: dict, task: dict) -> str:
    # Bind to both the provider turn and the observed work cycle. New task
    # registrations invalidate an outcome even when the provider stays idle.
    identity = {key: row.get(key) for key in ("tab_id", "pane_id", "terminal_id", "agent_session",
                "agent", "state_change_seq", "completion_seq", "work_epoch", "session_epoch", "raw_status")}
    identity["task"] = task
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


def apply_outcomes(rows: list[dict], data: dict) -> None:
    for row in rows:
        task = data["tasks"].get(row.get("pane_id"), {})
        row["task"] = task.get("id")
        row["outcome_token"] = outcome_token(row, task)
        row["outcome"] = None
        outcome = data["outcomes"].get(row.get("pane_id"), {})
        if outcome.get("token") == row["outcome_token"]:
            row["outcome"] = outcome


def pending_rows(rows: list[dict], home_pane_id: str = "") -> list[dict]:
    return [{"pane_id": row["pane_id"], "tab_id": row["tab_id"], "task": row.get("task"),
             "token": row["outcome_token"], "state": row["raw_status"]} for row in rows
            if section_for(row, {}, home_pane_id) == "VERIFICATION PENDING"]


def find_agent(rows: list[dict], pane: str) -> dict:
    matches = [row for row in rows if row.get("pane_id") == pane and row.get("has_agent")]
    if len(matches) != 1:
        raise ValueError(f"live agent {pane!r} unavailable")
    return matches[0]


def record_outcome(row: dict, data: dict, task_id: str, token: str,
                   status: str, evidence: str, summary: str) -> None:
    task = data["tasks"].get(row["pane_id"], {})
    if task.get("id") != task_id or not task_id:
        raise ValueError("task is not registered for this pane")
    if row["raw_status"] not in STOPPED:
        raise ValueError("agent has not stopped")
    if status == "complete" and row["raw_status"] in ("blocked", "error"):
        raise ValueError(f"an agent stopped as {row['raw_status']} cannot be complete")
    if not (row.get("terminal_id") or row.get("agent_session")) or all(
            row.get(key) is None for key in ("completion_seq", "state_change_seq")):
        raise ValueError("source cannot identify the stopped turn; keep verification pending")
    if token != outcome_token(row, task):
        raise ValueError("stale outcome token; read pending again")
    if not evidence.strip() or not summary.strip():
        raise ValueError("outcome requires evidence and summary")
    data["outcomes"][row["pane_id"]] = {"token": token, "task": task_id, "status": status,
                                        "evidence": evidence.strip(), "summary": clean(summary, 180)}


def notes(state_dir: Path) -> dict:
    data = read_json(state_dir / "field-status-notes.json", {"important": {}, "needs_you": {}, "keep": {}, "done": {}})
    for key in ("important", "needs_you", "review_gates", "keep", "done"):
        if key not in data:
            continue
        if not isinstance(data.get(key), dict):
            raise ValueError(f"invalid {key} notes")
    # Existing review-gate notes remain visible under Important. The legacy
    # note command remains an alias while running Lantern sessions update.
    # The next note write persists the migration.
    return {"important": {**data.get("review_gates", {}), **data.get("important", {})},
            "needs_you": data.get("needs_you", {}), "keep": data.get("keep", {}),
            "done": data.get("done", {})}


def color(text: str, shade: str, enabled: bool) -> str:
    return f"{ANSI[shade]}{text}{RESET}" if enabled else text


def fit(text: str, width: int) -> str:
    return text if len(text) <= width else text[: max(0, width - 1)] + "…"


def display_name(row: dict) -> str:
    workspace = clean(row.get("workspace"))
    if workspace.casefold() == "🔥 lantern":
        return "Lantern"
    if workspace and workspace != "?" and not re.fullmatch(r"w\d+", workspace):
        return workspace
    tab = clean(row.get("tab"))
    return tab or clean(row.get("agent")) or "Unnamed session"


def useful_tab_detail(tab: str, name: str) -> bool:
    normalized = tab.casefold()
    return bool(tab and normalized != name.casefold()
                and not tab.isdigit()
                and not normalized.startswith("home · codex")
                and not normalized.startswith(name.casefold() + " · codex"))


def row_identity(row: dict) -> dict:
    return {key: row.get(key) for key in ("tab_id", "pane_id", "terminal_id", "agent", "state_change_seq")}


def is_lantern_home(row: dict, home_pane_id: str) -> bool:
    if display_name(row) != "Lantern":
        return False
    if home_pane_id and row.get("pane_id") == home_pane_id:
        return True
    tab = str(row.get("tab", "")).casefold()
    return tab.startswith("home") or "lantern home" in tab


def section_for(row: dict, note_data: dict, home_pane_id: str) -> str | None:
    if is_lantern_home(row, home_pane_id):
        return "KEEP"
    if display_name(row) == "Lantern":
        return None
    if row.get("raw_status") == "working":
        return "IN MOTION"
    if row.get("has_agent") and row.get("raw_status") in STOPPED:
        outcome = row.get("outcome") or {}
        if outcome.get("status") == "complete":
            return "DONE"
        if outcome:
            return "NOT COMPLETE"
        return "VERIFICATION PENDING"
    keep = note_data.get("keep", {})
    if row.get("pane_id") in keep or row.get("tab_id") in keep:
        return "KEEP"
    return None


def done_summary(row: dict, note_data: dict) -> str:
    outcome = row.get("outcome") or {}
    summary = clean(outcome.get("summary"), 180)
    if summary:
        return summary
    return "Stopped with an error" if row.get("raw_status") == "error" else "Outcome not yet verified"


def append_note_section(lines: list[str], title: str, actions: dict, shade: str,
                        width: int, colors: bool) -> None:
    lines.append(color(title, shade, colors))
    if not actions:
        lines.append("• None")
    for action in actions.values():
        lines.extend(textwrap.wrap("• " + clean(action, 180), width=width,
                                   subsequent_indent="  ", break_long_words=False))
    lines.append("")


def render(rows: list[dict], note_data: dict, now: datetime, colors: bool = True,
           width: int = 88, home_pane_id: str = "") -> str:
    width = max(38, width)
    local_time = eastern(now)
    stamp = local_time.strftime("%a %b %d, %Y · %I:%M %p ET")
    scope = "LOCAL " if os.environ.get("LANTERN_VIEW_SCOPE") == "local" else ""
    heading = f"{scope}FIELD STATUS  {stamp}"
    if len(heading) > width:
        separator = ": " if scope else " · "
        heading = f"{scope}FIELD STATUS{separator}{local_time.strftime('%b %d %I:%M %p ET')}"
    lines = [heading, ""]
    append_note_section(lines, "IMPORTANT", note_data.get("important", {}), "red", width, colors)
    append_note_section(lines, "NEEDS YOU", note_data.get("needs_you", {}), "purple", width, colors)
    ordered = sorted(rows, key=lambda item: (ORDER.get(item["raw_status"], 4), item["sort_index"]))
    for title, shade, selected in (
        ("IN MOTION", "yellow", [row for row in ordered if section_for(row, note_data, home_pane_id) == "IN MOTION"]),
        ("VERIFICATION PENDING", "yellow", [row for row in ordered if section_for(row, note_data, home_pane_id) == "VERIFICATION PENDING"]),
        ("NOT COMPLETE", "red", [row for row in ordered if section_for(row, note_data, home_pane_id) == "NOT COMPLETE"]),
        ("DONE", "green", [row for row in ordered if section_for(row, note_data, home_pane_id) == "DONE"]),
        ("KEEP", "blue", [row for row in ordered if section_for(row, note_data, home_pane_id) == "KEEP"]),
    ):
        lines.append(color(title, shade, colors))
        if not selected:
            lines.append("• None")
        for row in selected:
            name = display_name(row)
            lines.append(f"• {color(fit(name, width - 2), 'yellow', colors)}")
            tab = clean(row.get("tab"))
            if useful_tab_detail(tab, name):
                lines.append("  " + fit(tab, width - 2))
            if title in ("DONE", "NOT COMPLETE", "VERIFICATION PENDING"):
                lines.extend(textwrap.wrap("  " + done_summary(row, note_data), width=width,
                                           subsequent_indent="  ", break_long_words=False))
        lines.append("")
    return "\n".join(lines) + "\n"


@contextmanager
def state_lock(state_dir: Path):
    from usage_runtime import FileLock
    lock = FileLock(state_dir / "field-status.lock")
    try:
        deadline = time.monotonic() + 5
        while not lock.acquire():
            if time.monotonic() >= deadline:
                raise RuntimeError("Field Status state is busy; retry")
            time.sleep(0.05)
        yield
    finally:
        lock.close()


def refresh(state_dir: Path, binary: str, timeout: float, now: datetime, colors: bool) -> str:
    with state_lock(state_dir):
        return refresh_locked(state_dir, binary, timeout, now, colors)


def refresh_locked(state_dir: Path, binary: str, timeout: float, now: datetime, colors: bool) -> str:
    tabs, agents, workspaces = snapshot(binary, timeout)
    path = state_dir / "field-status-rows.json"
    previous = read_json(path, {"rows": []})
    live = rows_for(tabs, agents, workspaces)
    rows = reconcile(live, previous, now)
    apply_outcomes(rows, acceptance_state(state_dir))
    note_data = notes(state_dir)
    pane_record = read_json(state_dir / "views.json", {})
    home_pane_id = clean(pane_record.get("home_pane_id"))
    if previous.get("rows") != rows:
        write_json(path, {"schema": 1, "rows": rows})
    return render(rows, note_data, now, colors, shutil.get_terminal_size((88, 24)).columns, home_pane_id)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", default=os.environ.get("LANTERN_HERD_STATE_DIR", ""))
    parser.add_argument("--herdr", default="")
    parser.add_argument("--timeout", type=float, default=5)
    parser.add_argument("--plain", action="store_true", help="omit ANSI colors")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("refresh", help="refresh and print compact field view")
    sub.add_parser("pending", help="list stopped agents without a current outcome; exit 3 when any")
    task = sub.add_parser("task", help="register new work and invalidate the previous outcome")
    task.add_argument("pane")
    task.add_argument("task_id")
    outcome = sub.add_parser("outcome", help="record a review against a live stopped turn")
    outcome.add_argument("pane")
    outcome.add_argument("--task", required=True)
    outcome.add_argument("--token", required=True)
    outcome.add_argument("--status", choices=("complete", "incomplete", "blocked", "cancelled", "superseded"), required=True)
    outcome.add_argument("--evidence", required=True)
    outcome.add_argument("--summary", required=True)
    pane = sub.add_parser("pane", help="open or reuse the Field Status pane")
    pane.add_argument("--setup-timeout", type=float, default=15)
    watch = sub.add_parser("watch", help="redraw only when live field or notes change")
    watch.add_argument("--interval", type=float, default=5)
    note = sub.add_parser("note", help="set or clear an important item, user action, keep pin, or done summary")
    note.add_argument("kind", choices=("important", "needs-you", "review-gate", "keep", "done"))
    note.add_argument("operation", choices=("set", "clear"))
    note.add_argument("id", help="stable monitor or task ID")
    note.add_argument("text", nargs="?", help="exact note or verified task summary; required for set")
    args = parser.parse_args()
    if not args.state_dir:
        parser.error("--state-dir or LANTERN_HERD_STATE_DIR is required")
    state_dir = Path(args.state_dir)
    try:
        if args.command == "note":
            if args.kind == "done" and args.operation == "set":
                raise ValueError("note done cannot accept a task; use task, pending, and outcome with evidence")
            if args.operation == "set" and not clean(args.text):
                parser.error("note set requires text")
            data = notes(state_dir)
            group = "needs_you" if args.kind == "needs-you" else (
                args.kind if args.kind in ("keep", "done") else "important")
            key = clean(args.id)
            if args.operation == "set":
                if group in ("keep", "done"):
                    rows = read_json(state_dir / "field-status-rows.json", {"rows": []}).get("rows", [])
                    matches = [row for row in rows if isinstance(row, dict) and
                               (row.get("pane_id") == key or (group == "keep" and row.get("tab_id") == key))]
                    if len(matches) != 1:
                        raise ValueError(f"field status row {key!r} unavailable; refresh first")
                    if group == "done":
                        if matches[0].get("raw_status") != "done":
                            raise ValueError("done summary requires a completed agent")
                        data[group][key] = {"identity": row_identity(matches[0]), "summary": clean(args.text, 180)}
                    else:
                        data[group][key] = clean(args.text, 180)
                else:
                    data[group][key] = clean(args.text, 180)
            else:
                data[group].pop(key, None)
            write_json(state_dir / "field-status-notes.json", data)
            return 0
        if args.command == "pane":
            wrapper = Path(__file__).resolve().with_name("herdr")
            binary = args.herdr or str(wrapper)
            if not Path(binary).is_file():
                raise RuntimeError("Lantern's gated Herdr command unavailable")
            pane_id, created = open_pane(state_dir, Path(__file__).resolve().parent.parent, binary, args.setup_timeout)
            print(f"Field Status pane {pane_id} {'opened' if created else 'reused'}")
            return 0
        binary = resolve_herdr(args.herdr)
        if args.command in ("task", "outcome", "pending"):
            with state_lock(state_dir):
                refresh_locked(state_dir, binary, args.timeout, datetime.now(timezone.utc), False)
                rows = read_json(state_dir / "field-status-rows.json", {"rows": []})["rows"]
                data = acceptance_state(state_dir)
                if args.command == "pending":
                    home = clean(read_json(state_dir / "views.json", {}).get("home_pane_id"))
                    pending = pending_rows(rows, home)
                    print(json.dumps(pending, sort_keys=True))
                    return 3 if pending else 0
                row = find_agent(rows, args.pane)
                if args.command == "task":
                    if not args.task_id.strip():
                        raise ValueError("task ID must not be empty")
                    data["tasks"][args.pane] = {"id": args.task_id, "generation": uuid.uuid4().hex}
                    data["outcomes"].pop(args.pane, None)
                else:
                    record_outcome(row, data, args.task, args.token, args.status, args.evidence, args.summary)
                write_json(state_dir / "field-status-outcomes.json", data)
                return 0
        if args.command == "watch":
            if args.interval <= 0:
                parser.error("--interval must be positive")
            from lantern_views import record_watcher
            record_watcher(state_dir, "field")
            old_body = None
            with Readout() as screen:
                while not screen.stop.is_set():
                    now = datetime.now(timezone.utc)
                    try:
                        output = refresh(state_dir, binary, args.timeout, now, not args.plain and "NO_COLOR" not in os.environ)
                    except (RuntimeError, ValueError, OSError) as exc:
                        output = f"FIELD STATUS unavailable: {exc}\n"
                    body = output.split("\n", 1)[1] if "\n" in output else output
                    signature = (body, eastern(now).strftime("%Y%m%d%H%M"))
                    if signature != old_body:
                        screen.draw(output)
                        old_body = signature
                    screen.wait(args.interval)
        else:
            print(refresh(state_dir, binary, args.timeout, datetime.now(timezone.utc), not args.plain), end="")
            rows = read_json(state_dir / "field-status-rows.json", {"rows": []})["rows"]
            home = clean(read_json(state_dir / "views.json", {}).get("home_pane_id"))
            if pending_rows(rows, home):
                return 3
        return 0
    except KeyboardInterrupt:
        return 0
    except (RuntimeError, ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"field-status: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
