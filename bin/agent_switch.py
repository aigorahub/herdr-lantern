#!/usr/bin/env python3
"""Offer explicit provider changes without stopping the source agent."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid

import field_status as field
import usage_runtime as runtime

ROOT = Path(__file__).resolve().parents[1]
KINDS = ("claude", "codex", "cursor", "grok")
PERMISSIONS = {"claude": ["--permission-mode", "auto"],
               "codex": ["--dangerously-bypass-approvals-and-sandbox"],
               "cursor": ["--auto-review", "--trust"],
               "grok": ["--permission-mode", "auto"]}


def helper(name: str, *args: str) -> tuple[int, dict]:
    proc = subprocess.run([sys.executable, str(ROOT / "bin" / (name + ".py")), *args],
                          capture_output=True, text=True, timeout=40)
    if proc.returncode not in (0, 3):
        raise ValueError(proc.stderr.strip() or f"{name} failed")
    value = json.loads(proc.stdout)
    if not isinstance(value, dict):
        raise ValueError(f"{name} returned invalid data")
    return proc.returncode, value


def resolve(route: dict) -> tuple[dict, dict]:
    _, resolved = helper("model-route", route["kind"], route["phrase"])
    _, availability = helper("model-preflight", resolved["kind"], resolved["model"],
                             *([resolved["effort"]] if resolved.get("effort") else []))
    return resolved, availability


def target(binary: str, pane: str) -> tuple[dict, dict, dict]:
    tabs, agents, workspaces = field.snapshot(binary, 5)
    matches = [row for row in agents if row.get("pane_id") == pane]
    if len(matches) != 1:
        raise ValueError("Source pane must identify one live agent")
    agent = matches[0]
    tab = next((row for row in tabs if row.get("tab_id") == agent.get("tab_id")), None)
    workspace = next((row for row in workspaces if tab and row.get("workspace_id") == tab.get("workspace_id")), None)
    if not tab or not workspace:
        raise ValueError("Source workspace is unavailable")
    if agent.get("agent_status") not in field.STOPPED:
        raise ValueError("Source agent must be stopped before a provider switch")
    return agent, tab, workspace


def identity(agent: dict, tab: dict) -> dict:
    return {key: agent.get(key) for key in ("pane_id", "tab_id", "name", "agent", "agent_session", "terminal_id", "completion_seq", "state_change_seq")} | {"workspace_id": tab["workspace_id"]}


def command(binary: str, args: list[str], *, mutate: bool = False) -> dict:
    env = field.herdr_environment(binary)
    if mutate:
        env["HERDR_HELPER_OK"] = "1"
    proc = subprocess.run(field.herdr_command(binary, args), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=90, env=env)
    if proc.returncode:
        raise ValueError(f"Herdr {' '.join(args[:2])} failed: {proc.stderr.strip()}")
    value = json.loads(proc.stdout) if proc.stdout.strip() else {"result": {}}
    result = value.get("result")
    if not isinstance(result, dict):
        raise ValueError("Herdr returned invalid data")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", default=os.environ.get("LANTERN_HERD_STATE_DIR"))
    parser.add_argument("--herdr", default=str(ROOT / "bin" / "herdr"))
    commands = parser.add_subparsers(dest="action", required=True)
    configure = commands.add_parser("configure", help="Save an explicitly selected alternative route")
    configure.add_argument("name")
    configure.add_argument("--kind", choices=KINDS, required=True)
    configure.add_argument("--model", required=True, help="Model phrase for model-route")
    offer = commands.add_parser("offer", help="Record a reported limit and inspect configured alternatives")
    offer.add_argument("--pane", required=True)
    offer.add_argument("--reason", required=True)
    start = commands.add_parser("start", help="Start the route the user explicitly selected")
    start.add_argument("--pane", required=True)
    start.add_argument("--token", required=True)
    start.add_argument("--route", required=True)
    start.add_argument("--context-file", type=Path, required=True)
    args = parser.parse_args()
    lock = None
    try:
        if not args.state_dir:
            raise ValueError("--state-dir or LANTERN_HERD_STATE_DIR is required")
        state = Path(args.state_dir)
        lock = runtime.FileLock(state / "switch.lock")
        if not lock.acquire():
            raise ValueError("Another provider switch command is active; retry after it completes")
        config_path = state / "switch-routes.json"
        config = field.read_json(config_path, {"routes": {}})
        routes = config["routes"]
        if args.action == "configure":
            if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", args.name):
                raise ValueError("Route name must contain letters, digits, underscores or hyphens")
            route = {"kind": args.kind, "phrase": args.model}
            resolved, availability = resolve(route)
            if not availability.get("available"):
                raise ValueError(availability.get("reason", "Route is unavailable"))
            routes[args.name] = route
            field.write_json(config_path, config)
            print(json.dumps({"route": args.name, "model": resolved["model"]}))
            return 0
        binary = field.resolve_herdr(args.herdr)
        agent, tab, workspace = target(binary, args.pane)
        source = identity(agent, tab)
        offer_path = state / ("switch-" + hashlib.sha256(args.pane.encode()).hexdigest() + ".json")
        if args.action == "offer":
            alternatives = []
            for name, route in routes.items():
                entry = {"route": name, **route, "quota": "unknown"}
                try:
                    resolved, availability = resolve(route)
                    entry.update(model=resolved["model"], available=availability["available"],
                                 resolved=resolved)
                    entry["quota"] = availability.get("quota", "unknown")
                    if availability.get("reason"):
                        entry["reason"] = availability["reason"]
                except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
                    entry.update(available=False, reason=str(exc))
                alternatives.append(entry)
            value = {"source": source, "limit_reason": args.reason, "token": uuid.uuid4().hex,
                     "routes": routes, "alternatives": alternatives}
            field.write_json(offer_path, value)
            print(json.dumps(value))
            return 0
        offered = field.read_json(offer_path, {})
        if offered.get("token") != args.token or offered.get("source") != source:
            raise ValueError("Offer is stale; inspect the source limit again")
        if offered.get("started"):
            raise ValueError("This offer already started a replacement agent")
        route = routes.get(args.route)
        if not route or offered.get("routes", {}).get(args.route) != route:
            raise ValueError("Choose an unchanged configured route from this offer")
        context = args.context_file.read_bytes().decode("utf-8")
        if not context.strip() or len(context) > 48000:
            raise ValueError("Task context must contain 1-48000 characters")
        resolved, availability = resolve(route)
        if not availability.get("available"):
            raise ValueError(availability.get("reason", "Selected route is unavailable"))
        selected = next((item for item in offered.get("alternatives", [])
                         if item.get("route") == args.route), {})
        if selected.get("resolved") != resolved:
            raise ValueError("Resolved route changed; inspect a new offer before selecting a model")
        cwd = workspace.get("cwd") or tab.get("cwd")
        if not isinstance(cwd, str) or not cwd:
            raise ValueError("Source workspace has no working directory")
        # The source may have resumed while the route catalog was loading.
        latest, latest_tab, _ = target(binary, args.pane)
        if identity(latest, latest_tab) != source:
            raise ValueError("Source session changed; inspect the source limit again")
        slug = "continue-" + uuid.uuid4().hex[:10]
        context_path = (state / (slug + "-context.json")).resolve()
        field.write_json(context_path, {"context": context, "source": source})
        created = command(binary, ["tab", "create", "--workspace", tab["workspace_id"],
                                  "--cwd", cwd, "--label", slug, "--no-focus"], mutate=True)
        pane = created["root_pane"]["pane_id"]
        offered.update(started=True, replacement_pane=pane, replacement_route=args.route,
                       context_file=str(context_path))
        field.write_json(offer_path, offered)
        command(binary, ["agent", "start", slug, "--kind", resolved["kind"], "--pane", pane,
                         "--", *resolved["argv"], *PERMISSIONS[resolved["kind"]]], mutate=True)
        # cmd.exe truncates multiline arguments and expands percent signs.
        # Keep task text in a private file and send only a safe path token.
        encoded_path = base64.b64encode(str(context_path).encode("utf-8")).decode("ascii")
        prompt = ("Continue the task in this existing workspace. Read its AGENTS.md first. "
                  "The prior agent remains available in pane " + args.pane + ". "
                  "Do not stop, close, or send messages to that agent. "
                  "Decode this base64 UTF-8 absolute path and read its JSON context field "
                  "for the complete task and progress. Context file path (base64 UTF-8): " + encoded_path)
        command(binary, ["agent", "prompt", slug, prompt, "--wait"], mutate=True)
        print(json.dumps({"source_pane": args.pane, "replacement_pane": pane,
                          "route": args.route, "model": resolved["model"],
                          "original_preserved": True}))
        return 0
    except (ValueError, OSError, KeyError, TypeError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2
    finally:
        if lock is not None:
            lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
