#!/usr/bin/env python3
"""Check or redeem saved resets for a Codex seat in a managed Lantern run."""
from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

import desktop
import field_status as field
import plan_usage
import usage_runtime as runtime
import usage_sources as sources

CONSUME = "account/rateLimitResetCredit/consume"
CONFIRMED = {"reset", "alreadyRedeemed"}
OUTCOMES = CONFIRMED | {"noCredit", "nothingToReset"}
WAITING = {"pending", "unresolved"}
WINDOWS = ("primary", "secondary")
IDLE = {"idle", "done", "waiting", "error"}


class Refusal(RuntimeError):
    pass


def state_root() -> Path:
    xdg = Path(os.environ.get("XDG_STATE_HOME", ""))
    root = xdg if xdg.is_absolute() else Path.home() / ".local" / "state"
    return runtime.private_dir(root / "herdr/plugins/aigora.lantern/resets")


def read_record(path: Path, default: dict) -> dict:
    if path.is_symlink():
        raise Refusal("Reset state is a symlink")
    return field.read_json(path, default)


def save_record(path: Path, value: dict) -> None:
    # The reservation must survive a process or system failure after the RPC.
    folder = runtime.private_dir(path.parent)
    if path.is_symlink():
        raise Refusal("Reset state is a symlink")
    fd, tmp = tempfile.mkstemp(prefix=".reset-", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(value, output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(tmp, path)
        directory = os.open(folder, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(tmp).unlink(missing_ok=True)


@contextmanager
def lock(path: Path):
    lease = runtime.FileLock(path)
    try:
        end = time.monotonic() + 5
        while not lease.acquire():
            if time.monotonic() >= end:
                raise Refusal("A reset check is active. Read its result before retrying")
            time.sleep(0.05)
        yield
    finally:
        lease.close()


def terminal(world: desktop.World, address: str) -> tuple[dict, dict]:
    rows = [row for row in world.view["panes"] if row["terminal_id"] == address]
    if len(rows) != 1 or rows[0].get("legacy_binary") or not world.server.get("lifetime"):
        raise Refusal("Seat is not a live native Herdr terminal")
    return rows[0], world.terminals[address]


def owns(term: dict) -> bool:
    leader = (term.get("foreground") or {}).get("pid")
    parents = runtime._parent_table()
    pid = os.getppid()
    while pid > 1:
        if pid == leader:
            return True
        pid = parents.get(pid, 0)
    return False


def seat_identity(term: dict) -> list:
    pid = (term.get("foreground") or {}).get("pid")
    start = runtime.process_start(pid) if isinstance(pid, int) else None
    if not start:
        raise Refusal("The seat process is not known")
    return [term["created_ms"], pid, start]


def run_path(root: Path, world: desktop.World, space: str) -> Path:
    return root / "runs" / (runtime.digest([world.server, space]) + ".json")


def team_grant(path: Path) -> dict:
    team = read_record(path, {})
    reset = team.get("usage_resets", {})
    if not isinstance(reset, dict):
        raise Refusal("Invalid team reset setting")
    cap = reset.get("max_per_run", 1)
    horizon = sources.number(reset.get("wait_horizon", 20))
    threshold = sources.number(team.get("switch_below", 1))
    if (type(cap) is not int or cap < 0 or horizon is None or horizon < 0
            or threshold is None or not 0 <= threshold <= 100):
        raise Refusal("Invalid team reset setting")
    return {"cap": cap, "horizon": horizon * 60, "threshold": threshold}


def register(args, world: desktop.World, root: Path) -> dict:
    # Only the Lantern home agent can create the grant context. Team leads
    # can spend within it but cannot register a new context to clear a cap.
    home = read_record(desktop.state_dir() / "home.json", {})
    term = world.terminals.get(home.get("terminal"), {})
    if (term.get("state") != "live" or term.get("labels", {}).get("lantern.desktop") != "home"
            or home.get("instance") != desktop.instance_id(desktop.state_dir()) or not owns(term)):
        raise Refusal("Only the Lantern home can register a team")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_. -]*", args.team):
        raise Refusal("Invalid team name")
    team = desktop.config_dir().resolve() / "teams" / (args.team + ".json")
    if not team.is_file():
        raise Refusal("Saved team file is missing")
    grant = team_grant(team)
    if not grant["cap"]:
        return {"outcome": "disabled"}
    addresses = [args.lead, *args.seat]
    if len(set(addresses)) != len(addresses):
        raise Refusal("Duplicate team seat")
    seats = {}
    for address in addresses:
        row, current = terminal(world, address)
        if row["workspace_id"] != args.space:
            raise Refusal("Team seats must be in the same Herdr space")
        seats[address] = seat_identity(current)
    path = run_path(root, world, args.space)
    with lock(path.with_suffix(".lock")):
        run = read_record(path, {})
        if run and run.get("team") != str(team):
            raise Refusal("A different team is already registered in this space")
        record = {"server": world.server, "space": args.space, "team": str(team), "lead": args.lead,
                  "seats": seats, "account": run.get("account")}
        save_record(path, record)
    return {"outcome": "registered", "space": args.space}


def assignment(world: desktop.World, path: Path, seat: str) -> tuple[dict, dict, dict]:
    run = read_record(path, {})
    if not run or run["server"] != world.server:
        raise Refusal("No active Lantern reset context")
    grant = team_grant(Path(run["team"]))
    for address in (run["lead"], seat):
        row, term = terminal(world, address)
        if row["workspace_id"] != run["space"] or seat_identity(term) != run["seats"].get(address):
            raise Refusal("The registered assignment changed")
    return run, grant, world.terminals[run["lead"]]


def seat_scope(world: desktop.World, seat: str) -> tuple[dict, dict]:
    row, term = terminal(world, seat)
    if row.get("agent") != "codex":
        raise Refusal("Seat is not Codex")
    pane = row["pane_id"]
    checked = plan_usage.inspect_process("codex", desktop.process_info(world, pane, 2))
    if checked["scope"]["provider"] != "codex":
        raise Refusal("Seat is not on native ChatGPT")
    return checked, term


def scope_current(world: desktop.World, seat: str, checked: dict) -> desktop.World:
    latest = desktop.read_world()
    before, _ = terminal(world, seat)
    after, _ = terminal(latest, seat)
    if world.server != latest.server or before["workspace_id"] != after["workspace_id"]:
        raise Refusal("Seat moved during the reset check")
    plan_usage.confirm_on(latest, world, before["pane_id"], checked)
    return latest


def rollout_path(pid: int, root: Path) -> Path:
    # The process must hold this log open. Searching by repo or by the newest
    # file can pick a different Codex seat in the same workspace.
    proc_fds = Path(f"/proc/{pid}/fd")
    if sys.platform.startswith("linux"):
        paths = set()
        for fd in proc_fds.iterdir():
            try:
                paths.add(os.readlink(fd))
            except FileNotFoundError:
                continue
    elif sys.platform == "darwin":
        output = subprocess.check_output(["/usr/sbin/lsof", "-a", "-p", str(pid), "-Fn"],
                                         timeout=3, text=True, stderr=subprocess.DEVNULL)
        paths = {line[1:] for line in output.splitlines() if line.startswith("n")}
    else:
        raise Refusal("This platform cannot verify the Codex session")
    sessions = (root / "sessions").resolve()
    candidates = [Path(p) for p in paths if Path(p).is_absolute() and sessions in Path(p).parents
                  and re.fullmatch(r"rollout-.*\.jsonl", Path(p).name)]
    if len(candidates) != 1 or candidates[0].resolve() != candidates[0]:
        raise Refusal("The Codex session is not uniquely known")
    return candidates[0]


def turn_evidence(path: Path) -> dict:
    # Only the latest complete turn counts. The log can contain very large
    # tool results, so refuse if the bounded tail misses that turn.
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise Refusal("Invalid Codex session log")
        offset = max(0, info.st_size - 16 * 1024 * 1024)
        stream.seek(offset)
        data = stream.read(16 * 1024 * 1024)
    if not data.endswith(b"\n"):
        raise Refusal("Codex is still writing its turn log")
    lines = data.splitlines()[bool(offset):]
    turn = {}
    active = {}
    for line in lines:
        event = json.loads(line)
        body = event.get("payload")
        if not isinstance(body, dict):
            continue
        kind = body.get("type")
        if event.get("type") == "event_msg":
            if kind == "task_started":
                turn = {"id": body.get("turn_id"), "ended": False}
            elif kind == "thread_settings_applied" and isinstance(body.get("thread_settings"), dict):
                active = body["thread_settings"]
            elif turn.get("id") and body.get("turn_id") == turn["id"]:
                if kind == "task_complete":
                    error = body.get("error")
                    info = error.get("codex_error_info", error.get("codexErrorInfo")) if isinstance(error, dict) else None
                    turn.update(ended=True, error=error is not None,
                                spent=info in ("usage_limit_exceeded", "usageLimitExceeded"))
                elif kind == "turn_aborted":
                    turn.update(ended=True, error=True, spent=False)
        elif event.get("type") == "turn_context" and turn.get("id") and body.get("turn_id") == turn["id"]:
            turn["model"] = body.get("model")
            active = {"model": turn["model"], "model_provider_id": active.get("model_provider_id") or "openai"}
    return dict(turn, active=active)


def pushed_head(worktree: Path) -> str:
    def git(*argv: str) -> str:
        return subprocess.check_output(["git", "-c", "core.fsmonitor=false", "-C", str(worktree), *argv],
                                       timeout=3, text=True,
                                       stderr=subprocess.DEVNULL).strip()

    try:
        if Path(git("rev-parse", "--show-toplevel")).resolve() != worktree:
            raise Refusal("Use the root of the assigned Git worktree")
        git("merge-base", "--is-ancestor", "HEAD", "@{upstream}")
        if not git("status", "--porcelain"):
            return git("rev-parse", "HEAD")
    except (ValueError, subprocess.SubprocessError):
        pass
    raise Refusal("The seat does not have a clean, pushed worktree")


def evidence(checked: dict, term: dict, stage: str, boundary: dict) -> dict:
    if term.get("status", {}).get("state") not in IDLE:
        raise Refusal("The Codex seat is working or its state is unknown")
    pid = checked["process"]["pid"]
    root = Path(checked["scope"]["env"]["CODEX_HOME"])
    path = rollout_path(pid, root)
    turn = turn_evidence(path)
    active = turn["active"]
    model = active.get("model")
    if not turn.get("ended") or not model or active.get("model_provider_id") != "openai":
        raise Refusal("The latest Codex turn and model are not verified")
    if stage == "spent" and (not turn.get("spent") or turn.get("model") != model):
        raise Refusal("The latest Codex turn has no verified usage limit error")
    if stage == "low":
        if turn.get("error") or not boundary.get("reason") or not boundary.get("worktree"):
            raise Refusal("A finished healthy turn and the next packet reason are required")
        commit = pushed_head(Path(boundary["worktree"]))
        if boundary.get("commit", commit) != commit:
            raise Refusal("The pushed handoff commit changed")
        boundary["commit"] = commit
    return {"session": path.stem, "turn": turn["id"], "model": model}


def active_pool(limits: dict, model: str) -> tuple[str, dict]:
    pools = limits.get("rateLimitsByLimitId") or {}
    if not isinstance(pools, dict):
        raise Refusal("Codex pool data is invalid")
    top = limits.get("rateLimits")
    pools = dict(pools)
    if isinstance(top, dict) and top.get("limitId") in (None, "codex"):
        pools.setdefault("codex", top)
    matches = [(key, pool) for key, pool in pools.items()
               if isinstance(pool, dict) and pool.get("normalModelSlug") == model]
    if len(matches) == 1:
        return matches[0]
    default = pools.get("codex")
    if not matches and isinstance(default, dict) and not default.get("normalModelSlug"):
        return "codex", default
    raise Refusal("The active Codex model pool is unknown")


def windows(pool: dict) -> list[dict]:
    return [window for slot in WINDOWS if (window := sources.window(pool.get(slot), slot, slot))]


def saved_window(intent: dict, pool: dict) -> dict | None:
    slot = intent["window"]
    return sources.window(pool.get(slot), slot, slot)


def trigger(limits: dict, pool: dict, stage: str) -> str:
    reached = pool.get("rateLimitReachedType")
    ordinary = limits.get("ordinaryUsageAllowed")
    if pool.get("spendControlReached") or (reached is not None and reached != "rate_limit_reached"):
        return "non_resettable"
    if stage == "low":
        if ordinary is True and reached is None:
            return "eligible"
    elif ordinary is False or reached == "rate_limit_reached":
        return "eligible"
    return "cleared" if ordinary is True else "unknown"


def limiting_window(pool: dict, stage: str, threshold: float, now: float) -> dict | None:
    candidates = [item for item in windows(pool)
                  if (item["remaining"] < threshold if stage == "low" else item["remaining"] <= 1)]
    if not candidates:
        return None
    if any(item["minutes"] not in (300, 10080) or not item["reset_at"] or item["reset_at"] <= now
           for item in candidates):
        raise Refusal("The limiting window or its refill time is unknown")
    return max(candidates, key=lambda item: item["minutes"])


def summary(reading: dict, limits: dict) -> dict:
    return {"saved_resets": reading.get("resets"), "ordinary_usage_allowed": limits.get("ordinaryUsageAllowed")}


def recovered(intent: dict, limits: dict, pool: dict) -> bool:
    if limits.get("ordinaryUsageAllowed") is not True:
        return False
    if intent["stage"] == "spent":
        return trigger(limits, pool, "spent") == "cleared"
    if intent["status"] == "nothingToReset":
        return True
    window = saved_window(intent, pool)
    return window is not None and window["remaining"] > intent["remaining"]


def record_recovery(intent: dict, pool: dict) -> None:
    window = saved_window(intent, pool)
    intent.update(recovery_confirmed=True, remaining_after=window["remaining"] if window else None)


def response(intent: dict, reading: dict, limits: dict, pool: dict) -> dict:
    return dict(summary(reading, limits), outcome=intent["status"], stage=intent["stage"],
                window=intent["window"], pool=intent["pool"],
                redeemed_credit=None, reset_used=intent["status"] in CONFIRMED,
                reservation_kept=intent["status"] != "noCredit",
                resume=recovered(intent, limits, pool))


def preflight(seat: str, stage: str, boundary: dict, world: desktop.World, checked: dict,
              path: Path, account: str, expected: dict, *, retry: bool = False) -> tuple[desktop.World, dict]:
    latest = scope_current(world, seat, checked)
    run, grant, lead = assignment(latest, path, seat)
    if run["account"] != account or (not retry and not owns(lead)):
        raise Refusal("The registered lead or Codex account changed")
    if evidence(checked, latest.terminals[seat], stage, boundary) != expected:
        raise Refusal("The Codex turn changed")
    return latest, grant


def send_reset(request, path: Path, ledger: dict, intent: dict, reading: dict, limits: dict,
               pool: dict, scope: dict, recheck) -> dict:
    try:
        if not plan_usage.stamp_matches(scope):
            raise sources.SourceError("Codex login changed")
        outcome = request(CONSUME, {"idempotencyKey": intent["key"]}).get("outcome")
        if outcome not in OUTCOMES:
            raise sources.SourceError("Unknown reset response")
    except (sources.SourceError, OSError):
        if intent["retries"]:
            intent["status"] = "unresolved"
        save_record(path, ledger)
        return dict(response(intent, reading, limits, pool), resume=False)
    intent["status"] = outcome
    intent["result_at"] = time.time()
    save_record(path, ledger)
    try:
        reading_after, limits_after = sources.codex_limits(request)
        recheck()
        if reading_after["account"] != reading["account"]:
            raise Refusal("Codex account changed after the request")
        pool_id, pool_after = active_pool(limits_after, intent["evidence"]["model"])
        if pool_id != intent["pool"]:
            raise Refusal("Codex model pool changed after the request")
    except (sources.SourceError, Refusal, OSError, RuntimeError, ValueError, subprocess.SubprocessError):
        return dict(response(intent, {}, {}, {}), resume=False)
    intent["balance_after"] = reading_after.get("resets")
    if outcome in CONFIRMED and recovered(intent, limits_after, pool_after):
        record_recovery(intent, pool_after)
    save_record(path, ledger)
    return response(intent, reading_after, limits_after, pool_after)


def reserved_slots(ledger: dict, run: str) -> int:
    return sum(entry["run"] == run and entry["status"] != "noCredit" for entry in ledger["intents"])


def attempted_this_episode(ledger: dict, pool: str, window: dict, stage: str, expected: dict,
                           threshold: float) -> bool:
    for entry in reversed(ledger["intents"]):
        if (entry["pool"] != pool or entry["window"] != window["id"]
                or entry["resets_at"] != window["reset_at"] or entry["status"] in ("void", "expired")):
            continue
        if entry["status"] in CONFIRMED | {"restored"}:
            if stage == "low":
                # A confirmed debit is enough to block a low attempt until
                # the account proves recovery and a new drop.
                remaining_after = sources.number(entry.get("remaining_after"))
                return not (entry.get("recovery_confirmed") and remaining_after is not None
                            and window["remaining"] < threshold <= remaining_after)
            if entry.get("recovery_confirmed"):
                return entry["stage"] == stage and entry["evidence"] == expected
        if entry["stage"] == stage:
            return True
    return False


def settle_pending(world: desktop.World, original: Path, path: Path, ledger: dict, intent: dict,
                   reading: dict, limits: dict) -> dict:
    def close(outcome: str, pool: dict) -> dict:
        intent["status"] = outcome
        intent["result_at"] = time.time()
        if outcome == "restored":
            record_recovery(intent, pool)
        save_record(path, ledger)
        return dict(response(intent, reading, limits, pool), resume=outcome == "restored")

    if time.time() >= intent["resets_at"]:
        return close("expired", {})
    run = read_record(original, {})
    if run and run["server"]["key"] != world.server["key"]:
        # This core cannot verify an assignment on another server.
        return dict(summary(reading, limits), outcome="pending", resume=False)
    if not run:
        return close("void", {})
    try:
        grant = team_grant(Path(run["team"]))
    except (Refusal, ValueError):
        return close("void", {})
    if not grant["cap"] or reserved_slots(ledger, original.name) > grant["cap"]:
        return close("void", {})
    seat, stage, boundary, expected = (intent[key] for key in ("seat", "stage", "boundary", "evidence"))
    account = reading["account"]
    try:
        original_run, _, _ = assignment(world, original, seat)
        if original_run["account"] != account:
            return close("void", {})
        checked, _ = seat_scope(world, seat)
        latest = scope_current(world, seat, checked)
        scope = checked["scope"]
        with sources.codex_session(scope, time.monotonic() + 24, threading.Event()) as request:
            fresh, snapshot = sources.codex_limits(request)
            if fresh["account"] != account:
                return close("void", {})
            reading, limits = fresh, snapshot
            try:
                pool_id, pool = active_pool(limits, expected["model"])
            except Refusal:
                return close("unresolved", {})
            item = saved_window(intent, pool)
            if pool_id != intent["pool"] or not item or item["reset_at"] is None:
                return close("unresolved", {})
            if recovered(intent, limits, pool):
                return close("restored", pool)
            if item["reset_at"] != intent["resets_at"] or time.time() >= intent["resets_at"]:
                return close("expired", pool)
            state = trigger(limits, pool, stage)
            if state != "eligible":
                return close("unresolved" if state == "unknown" else "void", pool)
            latest, grant = preflight(seat, stage, boundary, latest, checked, original, account,
                                      expected, retry=True)
            now = time.time()
            if now >= intent["resets_at"]:
                return close("expired", pool)
            current = limiting_window(pool, stage, grant["threshold"], now)
            if (reserved_slots(ledger, original.name) > grant["cap"] or not current
                    or current["id"] != intent["window"] or current["reset_at"] - now <= grant["horizon"]
                    or (current["minutes"] == 300 and not boundary.get("no_fallback"))):
                return close("void", pool)
            if intent["retries"] >= 1:
                return close("unresolved", pool)

            # The caller was authorized before taking the account lock.
            # Only the original assignment and existing key can be replayed.
            intent["retries"] += 1
            save_record(path, ledger)
            return send_reset(request, path, ledger, intent, reading, limits, pool, scope,
                              lambda: scope_current(latest, seat, checked))
    except Refusal:
        return close("void", {})
    except (OSError, RuntimeError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
        if intent["status"] != "pending":
            raise
        return close("unresolved", {})


def reconcile(world: desktop.World, root: Path, run_file: Path, target: str, cap: int,
              reading: dict, limits: dict, path: Path, ledger: dict) -> dict | None:
    intent = next((entry for entry in ledger["intents"] if entry["status"] in WAITING), None)
    if intent is None:
        return None
    if intent["status"] == "unresolved":
        return dict(summary(reading, limits), outcome="unresolved", resume=False)
    original = root / "runs" / intent["run"]
    if not cap and original != run_file:
        return dict(summary(reading, limits), outcome="disabled", resume=False)
    with lock(original.with_suffix(".lock")):
        result = settle_pending(world, original, path, ledger, intent, reading, limits)
    if result["outcome"] != "pending" and (original != run_file or intent["seat"] != target):
        result.update(resume=False, settled_seat=intent["seat"])
    return result


def consume(request, world: desktop.World, checked: dict, args, root: Path, run_file: Path,
            before: dict, boundary: dict) -> dict:
    account = before["account"]
    path = root / "accounts" / (account + ".json")
    with lock(path.with_suffix(".lock")):
        world = scope_current(world, args.seat, checked)
        reading, limits = sources.codex_limits(request)
        if reading["account"] != account:
            raise Refusal("Codex account changed before the request")
        with lock(run_file.with_suffix(".lock")):
            run, grant, lead = assignment(world, run_file, args.seat)
            if not owns(lead):
                raise Refusal("Only this team's lead can spend a reset")
            if run["account"] not in (None, account):
                raise Refusal("This run is pinned to a different Codex account")
            if run["account"] is None:
                if not grant["cap"]:
                    return dict(summary(reading, limits), outcome="disabled", resume=False)
                run["account"] = account
                save_record(run_file, run)
        ledger = read_record(path, {"intents": []})
        result = reconcile(world, root, run_file, args.seat, grant["cap"], reading, limits, path, ledger)
        if result is not None:
            return result
        if not grant["cap"]:
            return dict(summary(reading, limits), outcome="disabled", resume=False)
        expected = evidence(checked, world.terminals[args.seat], args.stage, boundary)
        pool_id, pool = active_pool(limits, expected["model"])
        state = trigger(limits, pool, args.stage)
        prior = [entry for entry in ledger["intents"] if entry["seat"] == args.seat
                 and entry["run"] == run_file.name and entry["pool"] == pool_id
                 and entry["stage"] == args.stage and entry["evidence"] == expected]
        last = prior[-1] if prior else None
        resume = last is None or recovered(last, limits, pool)
        if last and last["status"] in CONFIRMED and resume and not last.get("recovery_confirmed"):
            record_recovery(last, pool)
            last["balance_after"] = reading.get("resets")
            save_record(path, ledger)
        reserved = reserved_slots(ledger, run_file.name)
        base = dict(summary(reading, limits), stage=args.stage, pool=pool_id, resume=False,
                    reserved=reserved, max_per_run=grant["cap"])
        if state != "eligible":
            return dict(base, outcome=state, resume=state == "cleared" and resume)
        now = time.time()
        item = limiting_window(pool, args.stage, grant["threshold"], now)
        if item is None:
            return dict(base, outcome="window_not_limiting",
                        resume=resume and args.stage == "low" and limits.get("ordinaryUsageAllowed") is True)
        base.update(window=item["id"], refill_at=item["reset_at"], remaining=item["remaining"])
        if item["reset_at"] - now <= grant["horizon"]:
            return dict(base, outcome="wait")
        if item["minutes"] == 300 and not boundary.get("no_fallback"):
            return dict(base, outcome="fallback_first")
        if attempted_this_episode(ledger, pool_id, item, args.stage, expected, grant["threshold"]):
            return dict(base, outcome="already_attempted", resume=last is not None and resume)
        if reserved >= grant["cap"]:
            return dict(base, outcome="cap_reached")
        resets = reading.get("resets")
        if not resets or not resets["count"]:
            return dict(base, outcome="balance_unknown" if not resets else "no_credit")
        latest, live_grant = preflight(args.seat, args.stage, boundary, world, checked, run_file, account, expected)
        if live_grant != grant or item["reset_at"] - time.time() <= live_grant["horizon"]:
            return dict(base, outcome="check_again")
        intent = {"key": str(uuid.uuid4()), "account": account, "pool": pool_id, "window": item["id"],
                  "resets_at": item["reset_at"], "remaining": item["remaining"], "stage": args.stage,
                  "run": run_file.name, "seat": args.seat, "evidence": expected,
                  "boundary": boundary, "status": "pending", "retries": 0, "created_at": time.time()}
        ledger["intents"].append(intent)
        save_record(path, ledger)
        return send_reset(request, path, ledger, intent, reading, limits, pool, checked["scope"],
                          lambda: scope_current(latest, args.seat, checked))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="command", required=True)
    setup = subs.add_parser("register", help="Register a saved team from the Lantern home")
    setup.add_argument("--team", required=True)
    setup.add_argument("--space", required=True)
    setup.add_argument("--lead", required=True)
    setup.add_argument("--seat", action="append", default=[])
    subs.add_parser("status", help="Read current usage and saved reset balance").add_argument("--seat", required=True)
    spend = subs.add_parser("consume", help="Apply the saved team's reset rule")
    spend.add_argument("--seat", required=True)
    spend.add_argument("--stage", choices=("low", "spent"), required=True)
    spend.add_argument("--reason", help="For low, why the ready packet will exhaust quota before refill")
    spend.add_argument("--worktree", type=Path, help="For low, the root of the worker's assigned Git worktree")
    spend.add_argument("--no-qualified-fallback", action="store_true",
                       help="Lead verified that no saved cross-provider fallback qualifies")
    args = parser.parse_args(argv)
    if args.command == "consume" and args.stage == "low" and args.worktree is None:
        spend.error("--worktree is required with --stage low")
    try:
        world = desktop.read_world()
        if args.command == "register":
            result = register(args, world, state_root())
        else:
            checked, term = seat_scope(world, args.seat)
            world = scope_current(world, args.seat, checked)
            if args.command == "consume":
                root = state_root()
                row, term = terminal(world, args.seat)
                path = run_path(root, world, row["workspace_id"])
                _, _, lead = assignment(world, path, args.seat)
                if not owns(lead):
                    raise Refusal("Only this team's lead can spend a reset")
                reason = args.reason or ""
                if len(reason) > 400 or not all(ch.isprintable() for ch in reason):
                    raise Refusal("The next packet reason must fit on one line")
                boundary = {"reason": reason.strip(), "no_fallback": args.no_qualified_fallback}
                if args.stage == "low":
                    boundary["worktree"] = str(args.worktree.expanduser().resolve())
            with sources.codex_session(checked["scope"], time.monotonic() + 24, threading.Event()) as request:
                reading, limits = sources.codex_limits(request)
                if args.command == "status":
                    scope_current(world, args.seat, checked)
                    result = dict(summary(reading, limits), outcome="status", windows=reading["windows"])
                else:
                    result = consume(request, world, checked, args, root, path, reading, boundary)
        print(json.dumps(result, sort_keys=True))
        return 0
    except Refusal as exc:
        print(f"codex-resets: {exc}", file=sys.stderr)
    except (sources.SourceError, OSError, RuntimeError, ValueError, TypeError, KeyError, subprocess.SubprocessError):
        print("codex-resets: Could not verify reset state or Codex scope", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
