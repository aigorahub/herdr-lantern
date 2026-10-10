#!/usr/bin/env python3
"""Show plan limits for accounts with an open agent tab on this Herdr server."""
from __future__ import annotations

import argparse
import hashlib
import math
import os
import platform
import shutil
import signal
import tarfile
import tempfile
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import field_status as field
import usage_runtime as runtime
import usage_sources as sources
from terminal_readout import Readout

NAMES = {"codex": "OpenAI", "claude": "Claude", "grok": "Grok", "agy": "Google", "gemini": "Google", "fugu": "Fugu"}
CONFIG = {"version": 1, "hooks": {"enabled": False, "events": []}, "providers": [
    {"id": provider, "enabled": True, "cookieSource": "off", "source": source}
    for provider, source in (("claude", "cli"), ("grok", "oauth"))]}


def eligible(tabs: list[dict], agents: list[dict]) -> dict[str, list[dict]]:
    opened = {tab.get("tab_id") for tab in tabs if tab.get("tab_id")}
    result = {}
    for agent in agents:
        if agent.get("tab_id") in opened and agent.get("pane_id") and agent.get("agent"):
            result.setdefault(str(agent["agent"]), []).append(agent)
    return result


def verified_scope(binary: str, provider: str, agent: dict) -> dict:
    pane = agent["pane_id"]
    before = field.control(binary, ["pane", "process-info", "--pane", pane], 2).get("process_info", {})
    candidates = [p for p in before.get("foreground_processes", []) if isinstance(p, dict)
                  and isinstance(p.get("pid"), int) and p.get("pid") != before.get("shell_pid")
                  and isinstance(p.get("argv"), list) and p["argv"]
                  and provider in Path(str(p["argv"][0])).name.lower()]
    if len(candidates) != 1:
        raise sources.SourceError("Account unknown")
    process = candidates[0]
    hint = sources.route_hint(provider, process["argv"])
    try:
        return verified_process_scope(binary, provider, pane, process)
    except sources.SourceError as exc:
        if hint == "fugu":
            exc.provider = "fugu"
        raise
    except (OSError, RuntimeError, ValueError, TypeError, KeyError):
        raise sources.SourceError("Account unknown", provider=hint) from None


def verified_process_scope(binary: str, provider: str, pane: str, process: dict) -> dict:
    pid = process["pid"]
    start = runtime.process_start(pid)
    if not start:
        raise sources.SourceError("Account unknown")
    env = runtime.process_environment(pid)
    process = dict(process, executable=runtime.process_executable(pid))
    scope = sources.scope_for(provider, process, env)
    del env
    after = field.control(binary, ["pane", "process-info", "--pane", pane], 2).get("process_info", {})
    current = next((p for p in after.get("foreground_processes", []) if p.get("pid") == pid), {})
    if (current.get("argv") != process["argv"] or runtime.process_start(pid) != start
            or runtime.process_executable(pid) != process["executable"]
            or sources.current_stamp(scope) != scope["stamp"]):
        raise sources.SourceError("Account unknown")
    return scope


class Collector:
    def __init__(self, state: Path, shared: Path, binary: str, server: dict, owner_fd: int | None = None):
        self.state, self.shared, self.binary, self.server = state, shared, binary, server
        self.owner_fd = owner_fd
        runtime.private_dir(state)
        field.write_json(state / "usage-provider-config.json", CONFIG)
        cached = field.read_json(shared / "snapshot.json", {})
        self.cache = cached.get("bindings", {})
        same_lifetime = cached.get("server") == server and bool(server.get("lifetime"))
        self.prior_membership = cached.get("membership", []) if same_lifetime else []
        self.prior_rows = []
        if self.prior_membership:
            by_provider = {}
            for member in self.prior_membership:
                by_provider.setdefault(member["provider"], set()).update(member["tabs"])
            self.prior_rows = [{"provider": provider, "tabs": len(tabs),
                                "state": "Account unknown" if provider in sources.SUPPORTED or provider == "agy" else "Limits unavailable"}
                               for provider, tabs in by_provider.items()]
        elif same_lifetime:
            # Migrate an earlier snapshot without measured values.
            self.prior_rows = [{"provider": row["provider"], "tabs": row["tabs"], "state": "Account unknown"}
                               for row in cached.get("rows", [])]
        # Cached quota stays hidden until the current binding is verified.
        self.bindings = {}
        self.notices = {}
        self.scan_error = False
        self.validated = False
        self.cancel = threading.Event()
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="usage")
        self.pending = {}
        field.write_json(shared / "owner.json", {"pid": os.getpid(), "start": runtime.process_start(os.getpid()),
                                                  "server": server, "observed_at": time.time()})

    def scan(self) -> None:
        try:
            current = runtime.server_identity(self.binary, 2)
            if current != self.server:
                self.bindings, self.notices, self.cache = {}, {}, {}
                self.prior_rows, self.prior_membership = [], []
                self.validated = False
                if current["key"] != self.server["key"]:
                    raise RuntimeError("Herdr endpoint changed")
                self.server = current
            tabs, agents, _ = field.snapshot(self.binary, 2)
        except (RuntimeError, OSError, ValueError):
            self.scan_error = True
            return
        bindings, notices = {}, {}
        for provider, rows in eligible(tabs, agents).items():
            # This branch must precede every process and dependency inspection.
            if provider not in sources.SUPPORTED:
                state = "Account unknown" if provider == "agy" else "Limits unavailable"
                notices[(provider, state)] = {row["tab_id"] for row in rows}
                continue
            if os.name == "nt" and provider in ("claude", "grok"):
                notices[(provider, "Dependency unavailable")] = {row["tab_id"] for row in rows}
                continue
            for agent in rows:
                try:
                    scope = verified_scope(self.binary, provider, agent)
                except sources.SourceError as exc:
                    notices.setdefault((exc.provider or provider, exc.state), set()).add(agent["tab_id"])
                    continue
                except (OSError, RuntimeError, ValueError, TypeError, KeyError):
                    notices.setdefault((provider, "Account unknown"), set()).add(agent["tab_id"])
                    continue
                entry = bindings.setdefault(scope["key"], {"scope": scope, "tabs": set()})
                entry["tabs"].add(agent["tab_id"])
        self.bindings, self.notices = bindings, notices
        self.cache = {key: self.cache[key] for key in bindings if key in self.cache}
        self.validated, self.scan_error = True, False
        self.prior_rows, self.prior_membership = [], []

    def drain(self) -> None:
        for key, item in list(self.pending.items()):
            future, generation = item
            if not future.done():
                continue
            del self.pending[key]
            current = self.bindings.get(key)
            if not current or generation != self.generation(current):
                continue
            old = self.cache.get(key, {})
            try:
                result = future.result()
                if sources.current_stamp(current["scope"]) != current["scope"]["stamp"]:
                    raise sources.SourceError("Account unknown")
                self.cache[key] = {"result": result, "state": "Measured", "failures": 0,
                                   "next_at": time.time() + 60}
            except (sources.SourceError, OSError, RuntimeError, ValueError) as exc:
                state = exc.state if isinstance(exc, sources.SourceError) else "Last update failed"
                failures = old.get("failures", 0) + 1
                delay = min(300, 120 * 2 ** min(failures - 1, 2))
                if isinstance(exc, sources.SourceError):
                    delay = max(delay, exc.retry_after)
                self.cache[key] = {"state": state, "failures": failures, "next_at": time.time() + delay}
                # Identity failures hide an old account. Other failures retain age.
                if state not in ("Account unknown", "Sign in required") and old.get("result"):
                    self.cache[key]["result"] = old["result"]

    def generation(self, entry: dict) -> str:
        return runtime.digest([self.server, entry["scope"]["key"], sorted(entry["tabs"])])

    def schedule(self, force: bool = False) -> None:
        if not self.validated or self.scan_error:
            return
        busy = {self.cache.get(key, {}).get("result", {}).get("account") or key for key in self.pending}
        provider_busy = {}
        for key in self.pending:
            provider = self.bindings.get(key, {}).get("scope", {}).get("provider")
            provider_busy.setdefault(provider, set()).add(self.cache.get(key, {}).get("result", {}).get("account"))
        for key, entry in self.bindings.items():
            cached = self.cache.get(key, {})
            identity = cached.get("result", {}).get("account")
            account = identity or key
            provider = entry["scope"]["provider"]
            if key in self.pending or account in busy or len(self.pending) >= 2:
                continue
            # An unresolved scope can belong to a known account. Resolve it
            # alone at this provider before allowing concurrent requests.
            active = provider_busy.get(provider, set())
            if active and (identity is None or None in active):
                continue
            if not force and cached.get("next_at", 0) > time.time():
                continue
            # Scopes which resolve to one account share the latest measurement.
            peers = [value for other, value in self.cache.items() if other != key
                     and value.get("result", {}).get("account") == account]
            latest = max(peers, key=lambda value: value.get("next_at", 0), default={})
            if not force and latest.get("next_at", 0) > time.time():
                self.cache[key] = dict(latest)
                continue
            self.pending[key] = (self.pool.submit(sources.fetch, dict(entry["scope"], owner_fd=self.owner_fd), self.state, self.cancel),
                                 self.generation(entry))
            busy.add(account)
            provider_busy.setdefault(provider, set()).add(identity)

    def write(self) -> dict:
        grouped = {}
        for key, entry in self.bindings.items():
            cached = self.cache.get(key, {})
            result = cached.get("result")
            provider = entry["scope"]["provider"]
            if not result:
                state = cached.get("state", "Reading usage")
                group = grouped.setdefault((provider, state), {"provider": provider, "state": state, "tabs": set()})
            else:
                group_key = (provider, result["account"])
                group = grouped.setdefault(group_key, dict(result, state=cached.get("state", "Measured"), tabs=set()))
                if result["observed_at"] > group["observed_at"]:
                    group.update(result, state=cached.get("state", "Measured"))
            group["tabs"].update(entry["tabs"])
        for (provider, state), tabs in self.notices.items():
            group = grouped.setdefault((provider, state), {"provider": provider, "state": state, "tabs": set()})
            group["tabs"].update(tabs)
        rows = [dict(row, tabs=len(row["tabs"])) for row in grouped.values()]
        if not self.validated:
            rows = self.prior_rows
        rows.sort(key=lambda row: (row["provider"], row.get("account", ""), row["state"]))
        membership = ([{"provider": entry["scope"]["provider"], "tabs": sorted(entry["tabs"])}
                       for entry in self.bindings.values()]
                      + [{"provider": provider, "tabs": sorted(tabs)} for (provider, _), tabs in self.notices.items()])
        if not self.validated:
            membership = self.prior_membership
        snapshot = {"schema": 1, "server": self.server, "observed_at": time.time(), "scan_failed": self.scan_error,
                    "rows": rows, "bindings": self.cache, "membership": membership}
        field.write_json(self.shared / "snapshot.json", snapshot)
        return snapshot

    def close(self) -> None:
        self.cancel.set()
        self.pool.shutdown(wait=True, cancel_futures=True)


def cached_snapshot(shared: Path, server: dict) -> dict:
    value = field.read_json(shared / "snapshot.json", {})
    if value.get("server") != server:
        return {"rows": [], "scan_failed": True}
    return value


def render(snapshot: dict, now: float | None = None, width: int | None = None,
           color: bool = False) -> str:
    """Show session and weekly percentages left, one row per service."""
    import textwrap
    now = time.time() if now is None else now
    width = max(20, width or shutil.get_terminal_size((88, 24)).columns)
    color = color and "NO_COLOR" not in os.environ and os.environ.get("TERM") != "dumb"

    def style(text: str, code: str) -> str:
        return f"\033[{code}m{text}\033[0m" if color else text

    lines = []
    if snapshot.get("scan_failed"):
        lines += [style(line, "33") for line in textwrap.wrap("Herdr scan failed. Plan data is not current.", width)]
    services = {}
    for row in snapshot.get("rows", []):
        name = NAMES.get(row["provider"], field.clean(row["provider"]))
        services.setdefault(name, []).append(row)
    if not services:
        return "\n".join(lines + ["No open service plans."]) + "\n"
    full_labels = width >= 32
    gap = "  " if full_labels else " "
    widths = [7, 12, 9] if full_labels else [7, width - 14, 5]
    heading = ["Service", "Session left", "Week left"] if full_labels else ["Service", "Sess", "Week"]
    if not full_labels:
        lines.append("Percent left.")
    lines.append(style(gap.join(t.ljust(w) for t, w in zip(heading, widths)).rstrip(), "1"))
    lines.append(style(gap.join("-" * w for w in widths), "2"))
    notes = []

    def add(cells: list[str], codes: list[str] | None = None):
        parts = [textwrap.wrap(field.clean(cell, 500), width=w) or [""] for cell, w in zip(cells, widths)]
        for index in range(max(map(len, parts))):
            values = [part[index] if index < len(part) else "" for part in parts]
            lines.append(gap.join(style(value.ljust(w) if i < len(widths) - 1 else value, (codes or [""] * len(widths))[i])
                                    if (codes or [""] * len(widths))[i] else (value.ljust(w) if i < len(widths) - 1 else value)
                                    for i, (value, w) in enumerate(zip(values, widths))).rstrip())

    def period(value: dict) -> str | None:
        minutes = value.get("minutes")
        label = str(value.get("label", "")).lower()
        if minutes == 10080 or "week" in label:
            return "week"
        if (isinstance(minutes, (int, float)) and 0 < minutes < 1440
                or any(term in label for term in ("session", "5 hours", "5h"))
                or minutes is None and str(value.get("id", "")).endswith("primary")):
            return "session"
        return None

    def allowance(value: dict | None, stale: bool) -> tuple[str, str]:
        if value is None:
            return "--", "2"
        remaining = value["remaining"]
        code = "1;31" if remaining <= 10 else "1;33" if remaining <= 25 else "1"
        return f"{remaining:g}%" + ("*" if stale else ""), code

    order = {name: i for i, name in enumerate(("Claude", "OpenAI", "Grok", "Google", "Fugu"))}
    for name in sorted(services, key=lambda key: (order.get(key, 99), key)):
        accounts = sorted(services[name], key=lambda row: not bool(row.get("windows")))
        selected = {}
        stale = False
        notices = set()
        for row in accounts:
            if not row.get("windows"):
                notices.add(field.clean(row.get("state", "Limits unavailable")))
                continue
            age = max(0, now - row["observed_at"])
            row_stale = age >= 120 or snapshot.get("scan_failed") or row.get("state") != "Measured"
            for label in ("session", "week"):
                candidates = [value for value in row["windows"] if period(value) == label]
                for value in candidates:
                    previous = selected.get(label)
                    if previous is None or value["remaining"] < previous[0]["remaining"]:
                        selected[label] = (value, row_stale)
                    elif value["remaining"] == previous[0]["remaining"]:
                        selected[label] = (previous[0], previous[1] or row_stale)
        values, codes = [name], ["1"]
        for label in ("session", "week"):
            value, outdated = selected.get(label, (None, False))
            if value and value.get("reset_at") and value["reset_at"] <= now:
                outdated = True
            stale = stale or outdated
            left, code = allowance(value, outdated)
            values.append(left)
            codes.append(code)
        add(values, codes)
        if stale:
            notes.append(f"{name}: * Waiting for fresh limits.")
        if not selected:
            notes.append(f"{name}: {', '.join(sorted(notices)) or 'Limits unavailable'}.")
        elif notices:
            notes.append(f"{name}: some limits are unavailable.")
    if notes:
        lines.append("")
        for note in dict.fromkeys(notes):
            lines.extend(style(line, "33") for line in textwrap.wrap(note, width))
    return "\n".join(lines).rstrip() + "\n"


ASSETS = {
    "macos-arm64": "9aab1b9229f5d1d77de21b99aa887a60a47921bfd9b66e4b941a0e8a9d6e38e8",
    "macos-x86_64": "4d239b7412683f1eb529d55b851fe922bb462527850b767d6c9b89ba14be3fdb",
    "linux-aarch64": "46dc3b259b1df48b3fd6f337c754dd60dbece6772d7840cdc9514ab4977a0007",
    "linux-x86_64": "fac8b2c474e4e37a2b067224437cb182693411ebf639441cd0facbb57bf086b1",
    "linux-musl-aarch64": "9dab39f1197d9ccc501d1f678234cf15f94e8b84aa7349ba87103a5866dd793b",
    "linux-musl-x86_64": "e7b0bdc6d6931a4e6fd5ce4037e1125a073ec6cf38cd72f1b3bd8aeecfde51f5",
}


def install(state: Path) -> Path:
    system, machine = platform.system(), platform.machine().lower()
    arch = {"arm64": "aarch64", "aarch64": "aarch64", "amd64": "x86_64", "x86_64": "x86_64"}.get(machine)
    if system == "Darwin" and arch:
        asset = "macos-" + ("arm64" if arch == "aarch64" else arch)
    elif system == "Linux" and arch:
        musl = bool(list(Path("/lib").glob("ld-musl-*.so.1"))) or platform.libc_ver()[0] == "musl"
        asset = ("linux-musl-" if musl else "linux-") + arch
    else:
        raise sources.SourceError("Dependency unavailable")
    target = sources.tools_dir(state)
    runtime.private_dir(target.parent)
    lock = runtime.FileLock(target.parent / "install.lock")
    try:
        if not lock.acquire():
            raise RuntimeError("CodexBar install is in progress")
        if target.exists():
            if target.is_symlink():
                raise RuntimeError("CodexBar install path is a symlink")
            tool = target / "CodexBarCLI"
            code, output = runtime.capture([str(tool), "--version"], {"PATH": os.environ.get("PATH", "")},
                                           time.monotonic() + 3)
            if code == 0 and b"CodexBar 0.74.0" in output:
                return tool
            raise RuntimeError("Private CodexBar install has the wrong version")
        with tempfile.TemporaryDirectory(prefix=".install-", dir=target.parent) as temporary:
            root = Path(temporary)
            archive = root / "release.tar.gz"
            url = f"https://github.com/steipete/CodexBar/releases/download/v{sources.VERSION}/CodexBarCLI-v{sources.VERSION}-{asset}.tar.gz"
            digest = hashlib.sha256()
            with urllib.request.urlopen(url, timeout=30) as response, archive.open("wb") as stream:
                total = 0
                while chunk := response.read(65536):
                    total += len(chunk)
                    if total > 100 * 1024 * 1024:
                        raise RuntimeError("CodexBar release is too large")
                    digest.update(chunk)
                    stream.write(chunk)
            if digest.hexdigest() != ASSETS[asset]:
                raise RuntimeError("CodexBar release digest mismatch")
            unpacked = runtime.private_dir(root / "files")
            with tarfile.open(archive) as tar:
                total = 0
                for member in tar.getmembers():
                    path = unpacked / member.name
                    if not path.resolve().is_relative_to(unpacked.resolve()):
                        raise RuntimeError("CodexBar archive path is invalid")
                    if member.isdir():
                        path.mkdir(parents=True, exist_ok=True)
                    elif member.isfile():
                        total += member.size
                        if total > 200 * 1024 * 1024:
                            raise RuntimeError("CodexBar archive is too large")
                        path.parent.mkdir(parents=True, exist_ok=True)
                        with tar.extractfile(member) as source, path.open("wb") as output:
                            shutil.copyfileobj(source, output)
                        path.chmod(0o700 if member.mode & 0o111 else 0o600)
                    elif not member.issym() and not member.islnk():
                        raise RuntimeError("CodexBar archive entry is invalid")
            cli = unpacked / "CodexBarCLI"
            code, output = runtime.capture([str(cli), "--version"], {"PATH": os.environ.get("PATH", "")}, time.monotonic() + 3)
            if code or b"CodexBar 0.74.0" not in output:
                raise RuntimeError("CodexBar release version mismatch")
            os.replace(unpacked, target)
            return target / "CodexBarCLI"
    finally:
        lock.close()


def run(state: Path, binary: str, watch: bool, stop: threading.Event, interval: float) -> None:
    lock, collector, shared, server = None, None, None, None
    screen = Readout(stop) if watch else None
    if screen:
        screen.__enter__()
    try:
        while not stop.is_set():
            try:
                current = runtime.server_identity(binary, 2)
                if server != current:
                    if collector:
                        collector.close()
                        collector = None
                    if lock:
                        lock.close()
                    server = current
                    shared = runtime.shared_dir(server)
                    lock = runtime.FileLock(shared / "collector.lock")
                if not collector and lock.acquire():
                    collector = Collector(state, shared, binary, server, owner_fd=lock.fd)
                if collector:
                    collector.scan()
                    collector.drain()
                    if not watch:
                        for value in collector.cache.values():
                            value["next_at"] = 0
                    collector.schedule(force=not watch)
                    if not watch:
                        deadline = time.monotonic() + 102
                        next_scan = time.monotonic() + 5
                        while collector.pending and time.monotonic() < deadline and not stop.is_set():
                            stop.wait(0.1)
                            if time.monotonic() >= next_scan or any(future.done() for future, _ in collector.pending.values()):
                                collector.scan()
                                next_scan = time.monotonic() + 5
                            collector.drain()
                            collector.schedule()
                        # A closure or auth change during the fetch must remove it.
                        collector.scan()
                        collector.drain()
                    snapshot = collector.write()
                else:
                    snapshot = cached_snapshot(shared, server)
                output = render(snapshot, color=watch and os.isatty(1))
            except (RuntimeError, OSError, ValueError) as exc:
                output = "Plan Usage\n" + field.clean(exc, 180) + "\n"
            if screen:
                screen.draw(output)
            else:
                print(output, end="", flush=True)
            if not watch:
                break
            screen.wait(max(0.1, interval))
    finally:
        if screen:
            screen.__exit__()
        if collector:
            collector.close()
        if lock:
            lock.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", default=os.environ.get("LANTERN_HERD_STATE_DIR", ""))
    parser.add_argument("--herdr", default=str(Path(__file__).with_name("herdr")))
    sub = parser.add_subparsers(dest="command", required=True)
    watch = sub.add_parser("watch")
    watch.add_argument("--interval", type=float, default=5)
    for command in ("refresh", "collect", "pane", "install"):
        sub.add_parser(command)
    args = parser.parse_args()
    if args.command == "watch" and (not math.isfinite(args.interval) or args.interval <= 0):
        parser.error("--interval must be positive and finite")
    default = Path(os.environ.get("HERDR_PLUGIN_STATE_DIR", Path.home() / ".local/state/herdr/plugins/aigora.lantern")) / "herd"
    state = runtime.private_dir(Path(args.state_dir) if args.state_dir else default)
    stop = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM, *([signal.SIGHUP] if hasattr(signal, "SIGHUP") else [])):
        signal.signal(signum, lambda *_: stop.set())
    try:
        if args.command == "install":
            print(install(state))
        elif args.command == "pane":
            from lantern_views import open_view
            pane, _ = open_view(state, Path(__file__).resolve().parent.parent, args.herdr, 15, "usage")
            print(pane)
        elif args.command == "refresh":
            server = runtime.server_identity(args.herdr, 2)
            print(render(cached_snapshot(runtime.shared_dir(server), server)), end="")
        else:
            run(state, args.herdr, args.command == "watch", stop, getattr(args, "interval", 5))
        return 0
    except (RuntimeError, OSError, ValueError) as exc:
        print("plan-usage: " + field.clean(exc, 180), file=__import__("sys").stderr)
        return 1


if __name__ == "__main__":
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
