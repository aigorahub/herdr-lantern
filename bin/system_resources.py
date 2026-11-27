"""Show local memory and disk use in the desktop Lantern tab."""
from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import NamedTuple

import desktop
from field_status import clean
from lantern_views import record_watcher
from terminal_readout import Readout

WORK_DRIVE = Path("/Volumes/Aigora Work")


class Process(NamedTuple):
    parent: int
    kib: int
    name: str


def output(*args: str) -> str:
    return subprocess.run(args, check=True, capture_output=True, encoding="utf-8",
                          errors="replace", timeout=3).stdout


def memory_pressure() -> str:
    try:
        if sys.platform == "darwin":
            level = output("/usr/sbin/sysctl", "-n", "kern.memorystatus_vm_pressure_level").strip()
            return {"1": "normal", "2": "warning", "4": "critical"}.get(level, "unavailable")
        if sys.platform.startswith("linux"):
            data = Path("/proc/pressure/memory").read_text(encoding="ascii")
            match = re.search(r"^some avg10=(\d+\.\d+)\s", data, re.MULTILINE)
            if match:
                return f"{match[1]}% stalled (10s)"
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return "unavailable"


def parse_processes(data: str) -> dict[int, Process]:
    processes = {}
    for line in data.splitlines():
        fields = line.split(maxsplit=3)
        if len(fields) != 4:
            continue
        try:
            pid, parent, kib = map(int, fields[:3])
        except ValueError:
            continue
        if pid > 0 and parent >= 0 and kib >= 0:
            processes[pid] = Process(parent, kib, clean(Path(fields[3]).name))
    return processes


def agent_roots() -> dict[int, str]:
    roots = {}
    for terminal in desktop.core("terminal.list")["terminals"]:
        status = terminal.get("status") or {}
        app = status.get("app")
        pid = terminal.get("pid")
        if (terminal.get("state") == "live" and not terminal.get("machine") and app
                and type(pid) is int and pid > 0 and not desktop.bridge(terminal)):
            name = terminal.get("name") or desktop.APP_AGENT_NAMES.get(app, app)
            roots[pid] = f"{clean(name, 32)} ({clean(terminal['address'], 24)})"
    return roots


def largest_consumers(processes: dict[int, Process], roots: dict[int, str]) -> list[tuple[int, str]]:
    totals = Counter()
    singles = []
    for pid, process in processes.items():
        if not process.kib:
            continue
        owner = pid
        seen = set()
        while owner in processes and owner not in roots and owner not in seen:
            seen.add(owner)
            owner = processes[owner].parent
        if owner in processes and owner in roots:
            totals[owner] += process.kib
        else:
            singles.append((process.kib, f"{process.name} ({pid})"))
    groups = [(kib, roots[pid]) for pid, kib in totals.items()]
    return sorted([*groups, *singles], key=lambda row: (-row[0], row[1]))[:5]


def human_size(size: int) -> str:
    for unit, shift in (("TiB", 40), ("GiB", 30), ("MiB", 20)):
        if size >= 1 << shift:
            return f"{size / (1 << shift):.1f} {unit}"
    return f"{size // 1024} KiB"


def free_space(path: Path) -> str:
    try:
        return f"{human_size(shutil.disk_usage(path).free)} free"
    except OSError:
        return "unavailable"


def disk_lines(work_drive: Path = WORK_DRIVE) -> list[str]:
    lines = [f"System disk  {free_space(Path('/'))}"]
    if work_drive.is_mount():
        value = free_space(work_drive)
        if work_drive.is_mount():
            lines.append(f"Aigora Work  {value}")
    return lines


def snapshot() -> str:
    lines = [f"Memory pressure  {memory_pressure()}", *disk_lines(), "Top memory (RSS)"]
    try:
        roots = agent_roots()
    except (RuntimeError, ValueError, KeyError, AttributeError, TypeError, OSError, subprocess.SubprocessError):
        roots = {}
    try:
        processes = parse_processes(output("ps", "-axo", "pid=,ppid=,rss=,comm="))
    except (OSError, subprocess.SubprocessError):
        processes = {}
    consumers = largest_consumers(processes, roots)
    lines.extend(f"{human_size(kib * 1024):>9}  {name}" for kib, name in consumers)
    if not consumers:
        lines.append("Process memory unavailable")
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("mode", choices=("once", "watch"), nargs="?", default="once")
    args = parser.parse_args()
    if args.mode == "once":
        print(snapshot(), end="")
        return
    if args.state_dir is None:
        parser.error("watch requires --state-dir")
    record_watcher(args.state_dir, "system")
    with Readout() as screen:
        while not screen.stop.is_set():
            sample = snapshot()
            if sample != screen.output:
                screen.draw(sample)
            screen.wait(10)


if __name__ == "__main__":
    main()
