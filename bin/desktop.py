"""Run Lantern in Herdr's desktop app without changing the legacy plugin."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import time

import field_status as field
import usage_runtime as runtime

ROOT = Path(__file__).resolve().parents[1]
APP = os.environ.get("LANTERN_APP_BIN", "herdr-app")
HC = os.environ.get("LANTERN_HC_BIN", "hc")


def state_dir() -> Path:
    return runtime.private_dir(Path(os.environ.get(
        "LANTERN_DESKTOP_STATE_DIR", str(Path.home() / ".local/state/herdr/plugins/aigora.lantern/desktop"))))


def config_dir() -> Path:
    return Path(os.environ.get("LANTERN_CONFIG_DIR", str(Path.home() / ".config/herdr/plugins/config/aigora.lantern")))


def run(argv: list[str], timeout: float = 15) -> str:
    result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode:
        raise RuntimeError(field.clean(result.stderr or result.stdout or f"{argv[0]} failed", 400))
    return result.stdout


def core(method: str, **params) -> dict:
    request = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    proc = subprocess.run([HC, "connect"], input=json.dumps(request) + "\n", capture_output=True,
                          text=True, timeout=10, check=False)
    if proc.returncode:
        raise RuntimeError(field.clean(proc.stderr, 300))
    response = json.loads(proc.stdout)
    if "error" in response:
        raise RuntimeError(str(response["error"]))
    return response["result"]


def app(command: str, **args) -> dict:
    result = json.loads(run([APP, "do", command, json.dumps(args)]))
    if "error" in result:
        raise RuntimeError(str(result["error"]))
    return result


def bridge(terminal: dict) -> tuple[str, str] | None:
    argv = (terminal.get("foreground") or {}).get("argv") or []
    if (len(argv) >= 4 and Path(argv[0]).name in {"herdr", "herdr.exe"}
            and argv[1:3] == ["terminal", "attach"] and Path(argv[0]).is_absolute()):
        return argv[0], argv[3]
    return None


def read_world() -> tuple[dict, dict[str, dict], dict[str, dict]]:
    inventory = json.loads(run([APP, "ls"]))
    terminals = {t["address"]: t for t in core("terminal.list")["terminals"]}
    sources = {}
    for binary in {b[0] for t in terminals.values() if (b := bridge(t))}:
        data = json.loads(run([binary, "api", "snapshot"], 5))["result"]["snapshot"]
        sources[binary] = {p["terminal_id"]: p for p in data["panes"]}
    return inventory, terminals, sources


def snapshot(inventory: dict, terminals: dict[str, dict], sources: dict[str, dict]) -> dict:
    result = {"workspaces": [], "tabs": [], "agents": [], "panes": []}
    for space in inventory["spaces"]:
        if space.get("machine"):
            raise RuntimeError("Desktop readouts do not yet support remote spaces")
        label = "Lantern" if space["name"].lower() in {"lantern", "🔥 lantern"} else space["name"]
        result["workspaces"].append({"workspace_id": space["id"], "label": label})
        for tab in space["tabs"]:
            result["tabs"].append({"tab_id": tab["id"], "workspace_id": space["id"], "label": tab["name"]})
            for pane in tab["terminals"]:
                address = pane["terminal"]
                terminal = terminals.get(address)
                if not terminal or terminal.get("state") != "live":
                    raise RuntimeError(f"Terminal {address} changed during the read; retry")
                status = terminal.get("status") or {}
                if not isinstance(status, dict):
                    raise RuntimeError("Unsupported core status format")
                row = {"pane_id": pane["pane"], "terminal_id": address, "tab_id": tab["id"],
                       "workspace_id": space["id"], "agent_status": status.get("state") or "unknown",
                       "cwd": terminal["cwd"], "name": terminal.get("name"),
                       "state_change_seq": terminal.get("status_ms")}
                connection = bridge(terminal)
                if connection:
                    binary, target = connection
                    original = sources[binary].get(target)
                    if not original:
                        raise RuntimeError(f"Legacy terminal {target} changed during the read; retry")
                    row.update({k: original[k] for k in ("agent", "agent_status", "agent_session", "cwd",
                                                        "name", "state_change_seq") if k in original})
                    row.update(legacy_binary=binary, legacy_pane_id=original["pane_id"])
                elif status.get("app"):
                    row["agent"] = {"claude-code": "claude", "antigravity-cli": "agy",
                                    "antigravity": "agy"}.get(status["app"], status["app"])
                if row.get("agent"):
                    result["agents"].append(row)
                result["panes"].append(row)
    return result


def read_model(args: list[str]) -> None:
    if args == ["status", "--json"]:
        info = json.loads(run([HC, "server", "status"]))
        started = runtime.process_start(info["pid"])
        if not started:
            raise RuntimeError("Cannot identify this core's lifetime")
        print(json.dumps({"server": {"running": True, "transport": "herdr-core",
                                     "id": info["core"]["id"], "started_at": started}}))
        return
    inventory, terminals, sources = read_world()
    world = snapshot(inventory, terminals, sources)
    if len(args) == 2 and args[1] == "list" and args[0] in {"workspace", "tab", "agent"}:
        key = args[0] + "s"
        print(json.dumps({"result": {key: world[key]}}))
    elif len(args) == 4 and args[:3] == ["pane", "process-info", "--pane"]:
        row = next((r for r in world["panes"] if r["pane_id"] == args[3]), None)
        if not row:
            raise RuntimeError("Pane no longer exists")
        if row.get("legacy_binary"):
            print(run([row["legacy_binary"], "pane", "process-info", "--pane", row["legacy_pane_id"]]), end="")
        else:
            terminal = terminals[row["terminal_id"]]
            fg = terminal.get("foreground")
            print(json.dumps({"result": {"process_info": {"shell_pid": terminal["pid"],
                             "foreground_processes": [fg] if fg else []}}}))
    else:
        raise RuntimeError("This adapter is read only; use herdr-app and hc for native actions")


def read_config(path: Path) -> dict[str, str]:
    config = {}
    if path.exists():
        for line in path.read_text().splitlines():
            match = re.fullmatch(r'\s*(HELPER_[A-Z_]+)\s*=\s*(.*?)\s*', line)
            if match:
                value = match[2]
                if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
                    value = value[1:-1]
                config[match[1]] = value
    return config


def helper_argv(config: dict[str, str], prompt: str) -> list[str]:
    kind = config.get("HELPER_AGENT") or next((name for name in ("agent", "devin", "claude", "codex", "grok", "pi") if shutil.which(name)), "")
    kind = "agent" if kind == "cursor" else kind
    if kind not in {"claude", "codex", "grok", "pi", "agent", "devin"}:
        raise RuntimeError(f"Unsupported helper: {kind}")
    binary = shutil.which(kind)
    if not binary:
        raise RuntimeError(f"Helper command is not installed: {kind}")
    args = [binary]
    model, effort = config.get("HELPER_MODEL", ""), config.get("HELPER_EFFORT", "")
    if kind == "pi" and config.get("HELPER_PROVIDER"):
        args += ["--provider", config["HELPER_PROVIDER"]]
    if model and kind != "devin":
        args += ["--model", model]
    if effort:
        flag = {"claude": "--effort", "grok": "--reasoning-effort", "pi": "--thinking"}.get(kind)
        if flag:
            args += [flag, effort]
        elif kind == "codex":
            args += ["--config", f'model_reasoning_effort="{effort}"']
    permission = config.get("HELPER_PERMISSION", "smart")
    if permission not in {"auto", "accept-edits", "smart", "dangerous"}:
        raise RuntimeError("Invalid HELPER_PERMISSION")
    if permission == "dangerous" and kind != "pi":
        raise RuntimeError("Desktop Lantern does not enable dangerous permission modes")
    if kind in {"claude", "grok"}:
        args += ["--permission-mode", "acceptEdits" if permission == "accept-edits" else "auto"]
    elif kind == "codex":
        args += ["--no-daemon", "--sandbox", "workspace-write"]
        args += ["--ask-for-approval", "on-request"] if permission == "accept-edits" else ["--approve-for-me"]
    elif kind == "agent":
        if permission == "auto":
            raise RuntimeError("Cursor desktop helper requires smart or accept-edits permission")
        args += ["--auto-review", "--trust"]
    elif kind == "devin":
        args += ["--permission-mode", permission]
    if kind == "grok":
        args += ["--no-subagents"]
    args += shlex.split(config.get("HELPER_EXTRA_ARGS", ""))
    if kind == "claude":
        args += ["--append-system-prompt", prompt, "Read the native Lantern instructions and report which work needs attention. Do not change any project."]
    else:
        args += [prompt + "\nRead these instructions and report the field. Do not change any project."]
    return args


def chat() -> None:
    config = read_config(config_dir() / "helper.conf")
    state = state_dir()
    work = runtime.private_dir(state / "workdir")
    prompt = (ROOT / "desktop-prompt.md").read_text()
    prompt += "\nSaved settings: " + json.dumps({k: v for k, v in config.items() if k != "HELPER_EXTRA_ARGS"})
    prompt += f"\nModel routing: {ROOT / 'bin/model-route'}\nPreflight: {ROOT / 'bin/model-preflight'}\n"
    for filename in ("CLAUDE.md", "AGENTS.md", "GEMINI.md"):
        (work / filename).write_text(prompt)
    args = helper_argv(config, prompt)
    for name in list(os.environ):
        if name.startswith("HERDR_") and name != "HERDR_APP_HOME":
            os.environ.pop(name, None)
    os.environ.update(LANTERN_DESKTOP_COMMAND=str(ROOT / "bin/lantern-app"),
                      LANTERN_HERD_STATE_DIR=str(state), LANTERN_TEAM_STATE_DIR=str(state / "teams"),
                      LANTERN_TEAM_MAILBOX=str(ROOT / "bin/team_mailbox.py"))
    os.chdir(work)
    os.execv(args[0], args)


def locate(inventory: dict, address: str) -> tuple[dict, dict, dict] | None:
    for space in inventory["spaces"]:
        for tab in space["tabs"]:
            for pane in tab["terminals"]:
                if pane["terminal"] == address:
                    return space, tab, pane
    return None


def active_helper(terminal: dict, config: dict) -> bool:
    fg = terminal.get("foreground") or {}
    expected = Path(helper_argv(config, "")[0]).name.removesuffix(".exe")
    actual = Path(fg.get("name", "")).name.removesuffix(".exe")
    if actual == expected:
        return True
    argv = fg.get("argv") or []
    if actual == "node" and any(Path(arg).stem == expected for arg in argv):
        return True
    return str(Path(__file__).resolve()) in argv and "chat" in argv


def starting(terminal: dict) -> bool:
    return time.time() * 1000 - terminal.get("created_ms", 0) < 30000


def ensure_views(record: dict, state: Path) -> None:
    terminals = {t["address"]: t for t in core("terminal.list")["terminals"]}
    inventory = json.loads(run([APP, "ls"]))
    location = locate(inventory, record["terminal"])
    if not location:
        raise RuntimeError("The saved Lantern terminal has no app pane")
    space, tab, pane = location
    record.update(space=space["id"], tab=tab["id"], pane=pane["pane"])
    field.write_json(state / "home.json", record)
    field.write_json(state / "views.json", {"home_pane_id": pane["pane"]})
    changed = not record.get("layout_ready")
    for role in ("field", "usage"):
        saved = record.get(role) or {}
        target = terminals.get(saved.get("terminal"))
        if target and target.get("state") == "live":
            current = locate(inventory, target["address"])
            if not current or current[1]["id"] != tab["id"]:
                raise RuntimeError("A saved Lantern readout moved; leave its current location unchanged")
            from lantern_views import matches_watcher
            fg = target.get("foreground") or {}
            if matches_watcher(fg, ROOT, state, role):
                continue
            argv = fg.get("argv") or []
            if str(Path(__file__).resolve()) in argv and role in argv or starting(target):
                continue
            if fg.get("pid") != target.get("pid") or fg.get("name") not in {"sh", "bash", "zsh", "fish", "pwsh", "powershell.exe"}:
                raise RuntimeError("A Lantern readout pane now runs another program; preserve it")
            # Parking keeps the stopped readout's shell and history, including
            # any input the person typed after quitting the readout.
            app("terminal.park", terminal=target["address"])
            end = time.monotonic() + 3
            while locate(json.loads(run([APP, "ls"])), target["address"]):
                if time.monotonic() >= end:
                    raise RuntimeError("Readout parking is still pending; retry open")
                time.sleep(0.05)
            record.setdefault("parked_readouts", []).append(target["address"])
        anchor = record.get("field", {}).get("pane") if role == "usage" else pane["pane"]
        saved = app("pane.split", pane=anchor, direction="down" if role == "usage" else "right",
                    argv=[sys.executable, str(Path(__file__).resolve()), role, str(state)])
        record[role] = saved
        field.write_json(state / "home.json", record)
        changed = True
    if changed:
        doc = core("doc.get", name="herdr-app")
        for s in doc["value"]["spaces"]:
            for t in s["tabs"]:
                if t["id"] != tab["id"]:
                    continue
                def leaves(node):
                    return [node] if node["type"] == "block" else [leaf for child in node.get("children", []) for leaf in leaves(child)]
                blocks = leaves(t["root"])
                allowed = {record["terminal"], record["field"]["terminal"], record["usage"]["terminal"]}
                if {node.get("content", {}).get("terminal") for node in blocks} != allowed:
                    raise RuntimeError("Lantern tab has extra panes; keep their layout unchanged")
                nodes = {node["content"]["terminal"]: node for node in blocks}
                chat_node = nodes[record["terminal"]]
                field_node, usage_node = nodes[record["field"]["terminal"]], nodes[record["usage"]["terminal"]]
                chat_node["size"] = {"weight": 1}
                field_node["size"], usage_node["size"] = {"weight": 2}, {"weight": 1}
                t["root"] = {"type": "row", "id": t["root"]["id"], "children": [chat_node,
                    {"type": "column", "id": record["tab"] + "-lantern-readouts", "size": {"weight": 1},
                     "children": [field_node, usage_node]}]}
        core("doc.set", name="herdr-app", value=doc["value"], version=doc["version"])
        record["layout_ready"] = True
        field.write_json(state / "home.json", record)
    app("space.move", id=record["space"], index=1)
    app("pane.focus", pane=record["pane"])


def open_home() -> dict:
    state = state_dir()
    lock = runtime.FileLock(state / "open.lock")
    if not lock.acquire():
        raise RuntimeError("Lantern is already opening")
    try:
        config = read_config(config_dir() / "helper.conf")
        helper_argv(config, "")
        instance = runtime.digest([str(state.resolve()), str(config_dir().resolve())])
        inventory = json.loads(run([APP, "ls"]))
        terminals = core("terminal.list")["terminals"]
        record = field.read_json(state / "home.json", {})
        if record.get("instance") not in (None, instance):
            raise RuntimeError("This state directory belongs to another configuration; select a separate desktop state directory")
        home = next((t for t in terminals if t["state"] == "live" and
                     (t["address"] == record.get("terminal") or
                      t.get("labels", {}).get("lantern.instance") == instance and
                      t.get("labels", {}).get("lantern.desktop") == "home")), None)
        if home and (active_helper(home, config) or starting(home)):
            if record.get("terminal") != home["address"]:
                record = {"terminal": home["address"]}
            record["instance"] = instance
            ensure_views(record, state)
            return {"terminal": home["address"], "reused": True}
        model = config.get("HELPER_MODEL")
        kind = config.get("HELPER_AGENT")
        if model and kind in {"claude", "codex", "grok", "cursor"}:
            run([sys.executable, str(ROOT / "bin/model-preflight.py"), kind, model,
                 config.get("HELPER_EFFORT", "")], 90)
        argv = [sys.executable, str(Path(__file__).resolve()), "chat", str(state), str(config_dir())]
        space = next((s for s in inventory["spaces"] if s["name"].lower() in {"lantern", "🔥 lantern"}
                      and not s.get("machine")), None)
        if space:
            made = app("tab.new", space=space["id"], name="Lantern", folder=str(state), argv=argv)
        else:
            made = app("space.new", name="lantern", tab="Lantern", folder=str(state), argv=argv)
        record = dict(made, instance=instance)
        field.write_json(state / "home.json", record)
        run([HC, "label", made["terminal"], "lantern.desktop=home", "lantern.instance=" + instance])
        ensure_views(record, state)
        return dict(made, native=True)
    finally:
        lock.close()


def main() -> int:
    try:
        command, *args = sys.argv[1:] or ["open"]
        if command == "read-model":
            read_model(args)
        elif command == "install":
            target = Path.home() / ".local/bin/lantern-app"
            marker = "# Lantern desktop launcher"
            if target.is_symlink() or target.exists() and marker not in target.read_text():
                raise RuntimeError(f"Refusing to replace an unrelated command: {target}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("#!/bin/sh\n" + marker + "\nexec sh " + shlex.quote(str(ROOT / "bin/lantern-app")) + ' "$@"\n')
            target.chmod(0o700)
            print(target)
        elif command == "snapshot":
            print(json.dumps(snapshot(*read_world())))
        elif command == "open":
            print(json.dumps(open_home()))
        elif command == "chat":
            if args:
                os.environ["LANTERN_DESKTOP_STATE_DIR"] = args[0]
                os.environ["LANTERN_CONFIG_DIR"] = args[1]
            chat()
        elif command in {"field", "usage"}:
            if args:
                os.environ["LANTERN_DESKTOP_STATE_DIR"] = args[0]
            record = field.read_json(state_dir() / "home.json", {})
            if record.get("pane"):
                os.environ["HERDR_PANE_ID"] = record["pane"]
            script = "field_status.py" if command == "field" else "plan_usage.py"
            os.execv(sys.executable, [sys.executable, str(ROOT / "bin" / script),
                       "--state-dir", str(state_dir()), "--herdr", str(ROOT / "bin/lantern-app-read"), "watch"])
        else:
            raise RuntimeError("Usage: lantern-app [open|snapshot|field|usage]")
        return 0
    except (RuntimeError, OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        print("lantern-app: " + field.clean(exc, 400), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
