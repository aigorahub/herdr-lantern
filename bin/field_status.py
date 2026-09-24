#!/usr/bin/env python3
"""Compact, persistent, read-only Herdr field view for Lantern."""
from __future__ import annotations

import argparse
import calendar
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

RETENTION = timedelta(minutes=15)
CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
ANSI = {"yellow": "\x1b[33m", "green": "\x1b[32m", "blue": "\x1b[34m", "red": "\x1b[31m"}
RESET = "\x1b[0m"
ORDER = {"working": 0, "blocked": 1, "done": 2, "idle": 3, "unknown": 4}


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


def herdr_list(binary: str, item: str, timeout: float) -> list[dict]:
    try:
        proc = subprocess.run([binary, item, "list"], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout, check=False)
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
    return (herdr_list(binary, "tab", timeout), herdr_list(binary, "agent", timeout),
            herdr_list(binary, "workspace", timeout))


def control(binary: str, args: list[str], timeout: float) -> dict:
    """Use Lantern's gated Herdr wrapper for layout changes."""
    command = [binary, *args]
    if os.name == "nt" and Path(binary).suffix == "":
        shell = shutil.which("sh")
        if not shell:
            raise RuntimeError("Git sh unavailable for Lantern's Herdr gate")
        command = [shell, binary, *args]
    env = os.environ.copy()
    if args[:2] in (["pane", "split"], ["pane", "rename"], ["pane", "run"]):
        env["HERDR_HELPER_OK"] = "1"
    try:
        proc = subprocess.run(command, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout,
                              env=env, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Herdr {' '.join(args[:2])} unavailable: {exc}") from exc
    if proc.returncode:
        raise RuntimeError(f"Herdr {' '.join(args[:2])} failed ({proc.returncode}): {clean(proc.stderr, 180)}")
    try:
        result = json.loads(proc.stdout)["result"]
    except (ValueError, KeyError, TypeError) as exc:
        raise RuntimeError(f"Herdr {' '.join(args[:2])} returned invalid JSON") from exc
    if not isinstance(result, dict):
        raise RuntimeError(f"Herdr {' '.join(args[:2])} returned invalid result")
    return result


def shell_quote(path: Path) -> str:
    value = path.as_posix()
    # The pane command runs in sh on every platform, including Windows.
    return "'" + value.replace("'", "'\"'\"'") + "'"


def start_watcher(binary: str, pane_id: str, plugin_root: Path, state_dir: Path, timeout: float) -> None:
    command = f"sh {shell_quote(plugin_root / 'bin' / 'field-status')} --state-dir {shell_quote(state_dir)} watch"
    control(binary, ["pane", "run", pane_id, command], timeout)


def open_pane(state_dir: Path, plugin_root: Path, binary: str, timeout: float) -> tuple[str, bool]:
    """Open one Field Status pane beside the caller, preserving Lantern Home."""
    if os.environ.get("HERDR_ENV") != "1":
        raise RuntimeError("Field Status pane requires a Herdr managed Lantern pane")
    home = os.environ.get("HERDR_PANE_ID", "")
    tab = os.environ.get("HERDR_TAB_ID", "")
    workspace = os.environ.get("HERDR_WORKSPACE_ID", "")
    if not home or not tab or not workspace:
        raise RuntimeError("Lantern home pane identity unavailable")
    if os.environ.get("LANTERN_HOME_PANE_ID") != home:
        raise RuntimeError("Field Status must be opened from Lantern Home")
    path = state_dir / "field-status-pane.json"
    panes = control(binary, ["pane", "list", "--workspace", workspace], timeout).get("panes")
    if not isinstance(panes, list):
        raise RuntimeError("Herdr pane list returned invalid rows")
    current = next((pane for pane in panes if pane.get("pane_id") == home), None)
    if not current or current.get("tab_id") != tab:
        raise RuntimeError("Lantern home pane identity changed")
    if current.get("agent") is None:
        raise RuntimeError("Field Status must be opened from Lantern Home")
    record = read_json(path, {})
    saved = record.get("pane_id") if record.get("home_pane_id") == home else None
    if saved and any(pane.get("pane_id") == saved and pane.get("tab_id") == tab
                     and not pane.get("agent") for pane in panes):
        info = control(binary, ["pane", "process-info", "--pane", saved], timeout).get("process_info", {})
        processes = info.get("foreground_processes") if isinstance(info, dict) else None
        if not isinstance(processes, list):
            raise RuntimeError("Field Status pane process state unavailable")
        if any("field_status.py" in str(proc.get("cmdline", "")) and "watch" in str(proc.get("cmdline", ""))
               for proc in processes if isinstance(proc, dict)):
            return saved, False
        shell_pid = info.get("shell_pid")
        if any(proc.get("pid") != shell_pid for proc in processes if isinstance(proc, dict)):
            raise RuntimeError("Field Status pane is occupied by another process")
        start_watcher(binary, saved, plugin_root, state_dir, timeout)
        return saved, False
    # Recovery after state loss: the pane label is visible in pane list on
    # supported Herdr versions. It is never an agent pane.
    existing = next((pane for pane in panes if pane.get("tab_id") == tab
                     and pane.get("label") == "Field Status" and not pane.get("agent")), None)
    if existing:
        pane_id = existing["pane_id"]
        write_json(path, {"schema": 1, "home_pane_id": home, "pane_id": pane_id, "tab_id": tab})
        start_watcher(binary, pane_id, plugin_root, state_dir, timeout)
        return pane_id, False
    split = control(binary, ["pane", "split", "--pane", home, "--direction", "right",
                             "--ratio", "0.30", "--cwd", str(Path.cwd()), "--no-focus"], timeout)
    pane_id = (split.get("pane") or {}).get("pane_id")
    if not pane_id or pane_id == home:
        raise RuntimeError("Herdr split returned no new Field Status pane")
    write_json(path, {"schema": 1, "home_pane_id": home, "pane_id": pane_id, "tab_id": tab})
    control(binary, ["pane", "rename", pane_id, "Field Status"], timeout)
    start_watcher(binary, pane_id, plugin_root, state_dir, timeout)
    return pane_id, True


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
                "sort_index": index * 1000 + occupant_index,
            })
    return rows


def reconcile(live: list[dict], previous: dict, now: datetime) -> list[dict]:
    prior = [row for row in previous.get("rows", []) if isinstance(row, dict)]
    seen = {(row["tab_id"], row.get("pane_id"), row.get("terminal_id"), row.get("agent")) for row in live}
    result = list(live)
    for row in prior:
        identity = (row.get("tab_id"), row.get("pane_id"), row.get("terminal_id"), row.get("agent"))
        if identity in seen or row.get("raw_status") != "done" or row.get("agent") == "shell":
            continue
        closed_at = row.get("closed_at") or now.isoformat()
        try:
            age = now - datetime.fromisoformat(closed_at)
        except (ValueError, TypeError):
            continue
        if timedelta(0) <= age < RETENTION:
            result.append({**row, "closed_at": closed_at})
    return result


def notes(state_dir: Path) -> dict:
    data = read_json(state_dir / "field-status-notes.json", {"needs_you": {}, "review_gates": {}})
    for key in ("needs_you", "review_gates"):
        if not isinstance(data.get(key), dict):
            raise ValueError(f"invalid {key} notes")
    return data


def color(text: str, shade: str, enabled: bool) -> str:
    return f"{ANSI[shade]}{text}{RESET}" if enabled else text


def fit(text: str, width: int) -> str:
    return text if len(text) <= width else text[: max(0, width - 1)] + "…"


def render(rows: list[dict], note_data: dict, now: datetime, colors: bool = True,
           width: int = 88) -> str:
    width = max(38, width)
    local_time = eastern(now)
    stamp = local_time.strftime("%a %b %d, %Y · %I:%M %p ET")
    heading = f"FIELD STATUS  {stamp}"
    if len(heading) > width:
        heading = f"FIELD STATUS · {local_time.strftime('%b %d %I:%M %p ET')}"
    lines = [heading, ""]
    needs = note_data.get("needs_you", {})
    gates = note_data.get("review_gates", {})
    lines.append("IMPORTANT / NEEDS YOU")
    for action in needs.values():
        lines.extend(textwrap.wrap("• " + clean(action, 180), width=width,
                                   subsequent_indent="  ", break_long_words=False))
    if not needs:
        lines.append("• None")
    lines.append("REVIEW GATES · no user action")
    for gate in gates.values():
        lines.extend(textwrap.wrap("• " + clean(gate, 180), width=width,
                                   subsequent_indent="  ", break_long_words=False))
    if not gates:
        lines.append("• None")
    lines.append("")
    for row in sorted(rows, key=lambda item: (5 if item.get("closed_at") else ORDER.get(item["raw_status"], 4), item["sort_index"])):
        raw = row["raw_status"]
        status, shade = ("In Motion", "yellow") if raw == "working" else (("Done", "green") if raw == "done" else ("Keep", "blue"))
        if row.get("closed_at"):
            plain_status = "Done · Closed"
            status = f"{color('Done', 'green', colors)} · {color('Closed', 'red', colors)}"
        else:
            plain_status = status
            status = color(status, shade, colors)
        agent_name = fit(row["agent"], min(28, width // 2))
        agent = color(agent_name, "yellow", colors)
        location = f"{row['workspace']} / {row['tab']}"
        if len(agent_name) + len(plain_status) + len(location) + 4 <= width:
            lines.append(f"{agent}  {status}  {location}")
        else:
            lines.append(f"{agent}  {status}")
            lines.append("  " + fit(location, width - 2))
    if not rows:
        lines.append("No open tabs")
    return "\n".join(lines) + "\n"


def refresh(state_dir: Path, binary: str, timeout: float, now: datetime, colors: bool) -> str:
    tabs, agents, workspaces = snapshot(binary, timeout)
    path = state_dir / "field-status-rows.json"
    previous = read_json(path, {"rows": []})
    live = rows_for(tabs, agents, workspaces)
    rows = reconcile(live, previous, now)
    note_data = notes(state_dir)
    if previous.get("rows") != rows:
        write_json(path, {"schema": 1, "rows": rows})
    return render(rows, note_data, now, colors, shutil.get_terminal_size((88, 24)).columns)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", default=os.environ.get("LANTERN_HERD_STATE_DIR", ""))
    parser.add_argument("--herdr", default="")
    parser.add_argument("--timeout", type=float, default=5)
    parser.add_argument("--plain", action="store_true", help="omit ANSI colors")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("refresh", help="refresh and print compact field view")
    sub.add_parser("pane", help="open or reuse a right-side Field Status pane")
    watch = sub.add_parser("watch", help="redraw only when live field or notes change")
    watch.add_argument("--interval", type=float, default=5)
    note = sub.add_parser("note", help="set or clear an explicit user action or review gate")
    note.add_argument("kind", choices=("needs-you", "review-gate"))
    note.add_argument("operation", choices=("set", "clear"))
    note.add_argument("id", help="stable monitor or task ID")
    note.add_argument("text", nargs="?", help="exact user action or review gate; required for set")
    args = parser.parse_args()
    if not args.state_dir:
        parser.error("--state-dir or LANTERN_HERD_STATE_DIR is required")
    state_dir = Path(args.state_dir)
    try:
        if args.command == "note":
            if args.operation == "set" and not clean(args.text):
                parser.error("note set requires text")
            data = notes(state_dir)
            group = "needs_you" if args.kind == "needs-you" else "review_gates"
            if args.operation == "set":
                data[group][clean(args.id)] = clean(args.text, 180)
            else:
                data[group].pop(clean(args.id), None)
            write_json(state_dir / "field-status-notes.json", data)
            return 0
        if args.command == "pane":
            wrapper = Path(__file__).resolve().with_name("herdr")
            binary = args.herdr or str(wrapper)
            if not Path(binary).is_file():
                raise RuntimeError("Lantern's gated Herdr command unavailable")
            pane_id, created = open_pane(state_dir, Path(__file__).resolve().parent.parent, binary, args.timeout)
            print(f"Field Status pane {pane_id} {'opened' if created else 'reused'}")
            return 0
        binary = resolve_herdr(args.herdr)
        if args.command == "watch":
            if args.interval <= 0:
                parser.error("--interval must be positive")
            old_body = None
            while True:
                now = datetime.now(timezone.utc)
                try:
                    output = refresh(state_dir, binary, args.timeout, now, not args.plain)
                except (RuntimeError, ValueError, OSError) as exc:
                    output = f"FIELD STATUS unavailable: {exc}\n"
                body = output.split("\n", 1)[1] if "\n" in output else output
                signature = (body, eastern(now).strftime("%Y%m%d%H%M"))
                if signature != old_body:
                    print("\x1b[H\x1b[2J" + output, end="", flush=True)
                    old_body = signature
                time.sleep(args.interval)
        else:
            print(refresh(state_dir, binary, args.timeout, datetime.now(timezone.utc), not args.plain), end="")
        return 0
    except (RuntimeError, ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"field-status: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
