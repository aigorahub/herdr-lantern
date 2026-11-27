"""Show local memory and disk use in the desktop Lantern tab."""
from __future__ import annotations

import argparse
from collections import Counter, deque
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from typing import NamedTuple

import desktop
from field_status import clean
from lantern_views import record_watcher
from terminal_readout import Readout

WORK_DRIVE = Path("/Volumes/Aigora Work")
HISTORY_SECONDS = 60 * 60
HISTORY_INTERVAL = 5 * 60
SCRIPT_SUFFIXES = {".js", ".cjs", ".mjs", ".jsx", ".ts", ".mts", ".cts", ".py", ".pyw"}


class Process(NamedTuple):
    parent: int
    kib: int
    name: str
    started: str


class Consumer(NamedTuple):
    pid: int
    started: str
    kib: int
    label: str
    grouped: bool

    @property
    def key(self) -> tuple[int, str, bool]:
        return self.pid, self.started, self.grouped


class Change(NamedTuple):
    kib: int
    seconds: float


class MemoryHistory:
    def __init__(self):
        self.samples: dict[tuple[int, str, bool], deque[tuple[float, int]]] = {}
        self.observed_at: float | None = None

    def changes(self, now: float, consumers: list[Consumer]) -> dict[tuple[int, str, bool], Change]:
        if not consumers:
            return {}
        if self.observed_at is not None and now < self.observed_at:
            self.samples.clear()
        self.observed_at = now
        current = {}
        changes = {}
        for row in consumers:
            key = row.key
            samples = self.samples.get(key)
            if samples is None:
                samples = deque()
            while samples and now - samples[0][0] > HISTORY_SECONDS:
                samples.popleft()
            if not samples or now - samples[-1][0] >= HISTORY_INTERVAL:
                samples.append((now, row.kib))
            start, baseline = samples[0]
            seconds = now - start
            if seconds >= 60:
                changes[key] = Change(row.kib - baseline, seconds)
            current[key] = samples
        self.samples = current
        return changes


def output(*args: str) -> str:
    return subprocess.run(args, check=True, capture_output=True, encoding="utf-8",
                          errors="replace", timeout=3, env=dict(os.environ, LC_ALL="C", TZ="UTC")).stdout


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


def swap_used() -> str:
    try:
        if sys.platform == "darwin":
            value = output("/usr/sbin/sysctl", "-n", "vm.swapusage")
            match = re.search(r"\bused\s*=\s*(\d+(?:\.\d+)?)([KMG])\b", value)
            if match:
                return human_size(int(float(match[1]) * 1024 ** ("KMG".index(match[2]) + 1)))
        if sys.platform.startswith("linux"):
            info = Path("/proc/meminfo").read_text(encoding="ascii")
            swap = dict(re.findall(r"^(SwapTotal|SwapFree):\s+(\d+) kB$", info, re.MULTILINE))
            used_kib = max(0, int(swap["SwapTotal"]) - int(swap["SwapFree"]))
            return human_size(used_kib * 1024)
    except (KeyError, OSError, ValueError, subprocess.SubprocessError):
        pass
    return "unavailable"


def parse_processes(data: str) -> dict[int, Process]:
    processes = {}
    for line in data.splitlines():
        fields = line.split(maxsplit=8)
        if len(fields) != 9:
            continue
        try:
            pid, parent, kib = map(int, fields[:3])
        except ValueError:
            continue
        if pid > 0 and parent >= 0 and kib >= 0:
            processes[pid] = Process(parent, kib, clean(Path(fields[8]).name), " ".join(fields[3:8]))
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
            label = f"{clean(name, 24)} ({clean(terminal['address'], 24)})"
            where = clean((terminal.get("labels") or {}).get("where"), 60)
            roots[pid] = f"{label} · {where}" if where else label
    return roots


def ranked_consumers(processes: dict[int, Process], roots: dict[int, str]) -> list[Consumer]:
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
            singles.append(Consumer(pid, process.started, process.kib, process.name, False))
    groups = [Consumer(pid, processes[pid].started, kib, roots[pid], True) for pid, kib in totals.items()]
    return sorted([*groups, *singles], key=lambda row: (-row.kib, row.label, row.pid))


def is_runtime(name: str) -> bool:
    return name in {"node", "nodejs", "bun", "deno"} or bool(re.fullmatch(r"python(?:\d+(?:\.\d+)?)?", name))


def process_folders(pids: list[int]) -> dict[int, Path]:
    if not pids:
        return {}
    folders = {}
    if sys.platform == "darwin":
        command = ["/usr/sbin/lsof", "-nP", "-a", "-p", ",".join(map(str, pids)), "-d", "cwd", "-Fpn"]
        try:
            result = subprocess.run(command, capture_output=True, encoding="utf-8", errors="replace", timeout=2)
        except (OSError, subprocess.SubprocessError):
            return folders
        pid = None
        for line in result.stdout.splitlines():
            if line.startswith("p") and line[1:].isascii() and line[1:].isdigit():
                pid = int(line[1:])
            elif pid in pids and line.startswith("n/"):
                folders[pid] = Path(line[1:])
    elif sys.platform.startswith("linux"):
        for pid in pids:
            try:
                folders[pid] = Path("/proc", str(pid), "cwd").readlink()
            except OSError:
                continue
    return folders


def entry_point(name: str, argv: list[str], folder: Path | None) -> str:
    args = iter(argv[1:])
    for arg in args:
        if arg.split("=", 1)[0] in {"-c", "-e", "--eval", "-p", "--print"}:
            return ""
        if arg in {"-m", "--module"} and name.startswith("python"):
            module = next(args, "")
            return module if re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_.]*", module) else ""
        if arg in {"--require", "-r", "--import", "--loader"}:
            next(args, None)
            continue
        if arg in {"--", "-u", "-B", "-O", "-OO"} or arg.startswith("--") and "=" in arg:
            continue
        if arg.startswith("-"):
            return ""
        path = Path(arg)
        if not path.is_absolute():
            if folder is None:
                return ""
            path = folder / path
        try:
            path = path.resolve(strict=True)
            if not path.is_file():
                return ""
        except (OSError, RuntimeError, ValueError):
            return ""
        for index in range(len(path.parts) - 2, -1, -1):
            if path.parts[index] == "node_modules":
                package = path.parts[index + 1]
                if package.startswith("@") and index + 2 < len(path.parts) - 1:
                    return f"{package}/{path.parts[index + 2]}"
                if package == ".bin" and index + 3 == len(path.parts):
                    return path.name
                if not package.startswith(".") and index + 1 < len(path.parts) - 1:
                    return package
                break
        return path.name if path.suffix in SCRIPT_SUFFIXES else ""
    return ""


def consumer_label(row: Consumer, folders: dict[int, Path]) -> str:
    if row.grouped:
        return row.label
    name = row.label
    folder = folders.get(row.pid)
    if is_runtime(name):
        argv = desktop.process_argv(row.pid) or []
        name = clean(entry_point(name, argv, folder), 36) or name
    project = clean(folder.name, 32) if folder and folder != Path.home() else ""
    if project:
        name = f"{name} · {project}"
    return f"{name} ({row.pid})"


def human_size(size: int) -> str:
    for unit, shift in (("TiB", 40), ("GiB", 30), ("MiB", 20)):
        if size >= 1 << shift:
            return f"{size / (1 << shift):.1f} {unit}"
    return f"{size // 1024} KiB"


def change_label(change: Change | None) -> str:
    if change is None:
        return "—"
    prefix = "+" if change.kib > 0 else "−" if change.kib < 0 else ""
    size = human_size(abs(change.kib) * 1024).replace(" ", "")
    return f"{prefix}{size}/{int(change.seconds // 60)}m"


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


def snapshot(history: MemoryHistory | None = None) -> str:
    lines = [f"RAM pressure  {memory_pressure()}  ·  Swap  {swap_used()} used", *disk_lines()]
    try:
        roots = agent_roots()
    except (RuntimeError, ValueError, KeyError, AttributeError, TypeError, OSError, subprocess.SubprocessError):
        roots = {}
    try:
        processes = parse_processes(output("ps", "-axo", "pid=,ppid=,rss=,lstart=,comm="))
    except (OSError, subprocess.SubprocessError):
        processes = {}
    consumers = ranked_consumers(processes, roots)
    if not consumers:
        lines.append("Process memory unavailable")
        return "\n".join(lines) + "\n"
    changes = history.changes(time.time(), consumers) if history else {}
    header = "Top RAM (RSS)"
    if history:
        header += "  ·  Change / time" if changes else "  ·  Measuring change"
    lines.append(header)
    top = consumers[:5]
    fastest = max((key for key, change in changes.items() if change.kib >= 32 * 1024),
                  key=lambda key: changes[key].kib / changes[key].seconds, default=None)
    rising = next((row for row in consumers[5:] if row.key == fastest), None)
    shown = [*top, rising] if rising else top
    folders = process_folders([row.pid for row in shown if not row.grouped and is_runtime(row.label)])
    for row in top:
        size = human_size(row.kib * 1024)
        delta = f" {change_label(changes.get(row.key)):>13}" if history else ""
        lines.append(f"{size:>9}{delta}  {consumer_label(row, folders)}")
    if rising:
        lines.append(f"Rising  {change_label(changes[rising.key])}  {consumer_label(rising, folders)}")
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
    history = MemoryHistory()
    with Readout() as screen:
        while not screen.stop.is_set():
            sample = snapshot(history)
            if sample != screen.output:
                screen.draw(sample)
            screen.wait(10)


if __name__ == "__main__":
    main()
