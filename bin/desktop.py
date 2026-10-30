"""Run Lantern in Herdr's desktop app without changing the legacy plugin."""
from __future__ import annotations

import json
import os
from pathlib import Path
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


class CoreError(RuntimeError):
    def __init__(self, error: dict):
        self.kind = error.get("data", {}).get("kind")
        super().__init__(str(error))


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
        raise CoreError(response["error"])
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
        recognized = {agent["terminal_id"]: agent for agent in data["agents"]}
        sources[binary] = {}
        for pane in data["panes"]:
            pane = dict(pane)
            pane.pop("agent", None)
            pane.update(recognized.get(pane["terminal_id"], {}))
            sources[binary][pane["terminal_id"]] = pane
    return inventory, terminals, sources


def snapshot(inventory: dict, terminals: dict[str, dict], sources: dict[str, dict]) -> dict:
    result = {"workspaces": [], "tabs": [], "agents": [], "panes": []}
    for space in inventory["spaces"]:
        if space.get("machine"):
            continue
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
                    row["agent"] = {"claude-code": "claude", "grok-build": "grok", "antigravity-cli": "agy",
                                    "antigravity": "agy"}.get(status["app"], status["app"])
                if row.get("agent"):
                    result["agents"].append(row)
                result["panes"].append(row)
    return result


def native_process(terminal: dict) -> dict:
    fg = dict(terminal.get("foreground") or {})
    if not fg:
        return fg
    argv = fg.get("argv") or []
    # Accept the app's fixed exec wrapper without parsing arbitrary shell code.
    if (len(argv) >= 5 and argv[1:3] == ["-ic", '\"$@\"; exec "$0" -i']
            and argv[0] == argv[3] and Path(argv[0]).name in {"sh", "bash", "zsh"}):
        fg["argv"] = argv[4:]
    cwd = fg.get("cwd") or terminal.get("cwd")
    if not isinstance(cwd, str) or not Path(cwd).is_absolute():
        raise RuntimeError("Native process working directory unavailable")
    fg["cwd"] = cwd
    return fg


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
            fg = native_process(terminal)
            print(json.dumps({"result": {"process_info": {"shell_pid": terminal["pid"],
                             "foreground_processes": [fg] if fg else []}}}))
    else:
        raise RuntimeError("This adapter is read only; use herdr-app and hc for native actions")


def read_config(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    keys = ["HELPER_AGENT", "HELPER_MODEL", "HELPER_PROVIDER", "HELPER_EFFORT", "HELPER_CWD",
            "HELPER_SPAWN_KIND", "HELPER_SPAWN_MODEL", "HELPER_SPAWN_EFFORT", "HELPER_PERMISSION", "HELPER_EXTRA_ARGS"]
    # The legacy parser is the configuration contract for both backends.
    script = '. "$1"; helper_parse_conf "$2" || exit 1; printf "%s\\0" ' + " ".join('"$' + key + '"' for key in keys)
    values = run(["sh", "-c", script, "lantern-config", str(ROOT / "lib.sh"), str(path)]).split("\0")
    return dict(zip(keys, values))


def helper_command(config: dict[str, str]) -> tuple[str, str]:
    kind = config.get("HELPER_AGENT") or next((name for name in ("agent", "devin", "claude", "codex", "grok", "pi") if shutil.which(name)), "")
    kind = "agent" if kind == "cursor" else kind
    if kind not in {"claude", "codex", "grok", "pi", "agent", "devin"}:
        raise RuntimeError(f"Unsupported helper: {kind}")
    binary = shutil.which(kind)
    if not binary:
        raise RuntimeError(f"Helper command is not installed: {kind}")
    return kind, binary


def helper_argv(config: dict[str, str], prompt: str) -> list[str]:
    kind, binary = helper_command(config)
    args = [binary]
    model, effort = config.get("HELPER_MODEL", ""), config.get("HELPER_EFFORT", "")
    if kind == "agent" and not model:
        model = run(["sh", "-c", '. "$1"; helper_cursor_default_model', "lantern-default", str(ROOT / "lib.sh")]).strip()
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
    permission = config.get("HELPER_PERMISSION") or "smart"
    if permission not in {"auto", "accept-edits", "smart", "dangerous"}:
        raise RuntimeError("Invalid HELPER_PERMISSION")
    # Keep the legacy helper's policy. Claude and Grok use their own saved
    # settings; Codex retains the established orchestration launch flags.
    if kind == "codex":
        args += ["--no-daemon", "--dangerously-bypass-approvals-and-sandbox"]
    elif kind == "agent":
        args += ["--trust", "--sandbox", "disabled"]
        if permission in {"smart", "accept-edits"}:
            args += ["--auto-review"]
        elif permission == "dangerous":
            args += ["--force"]
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
    record_start(state_dir(), "chat")
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
                      LANTERN_HERD_STATE_DIR=str(state), LANTERN_TEAM_STATE_DIR=str(state),
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


def wait_location(address: str) -> tuple[dict, dict, dict]:
    end = time.monotonic() + 3
    while True:
        current = locate(json.loads(run([APP, "ls"])), address)
        if current:
            return current
        if time.monotonic() >= end:
            raise RuntimeError("Readout adoption is still pending; retry open")
        time.sleep(0.05)


def active_helper(terminal: dict, config: dict, binary: str | None = None) -> bool:
    fg = terminal.get("foreground") or {}
    expected = Path(binary or helper_command(config)[1]).name.removesuffix(".exe")
    actual = Path(fg.get("name", "")).name.removesuffix(".exe")
    if actual == expected:
        return True
    argv = fg.get("argv") or []
    if actual == "node" and any(Path(arg).stem == expected for arg in argv):
        return True
    return str(Path(__file__).resolve()) in argv and "chat" in argv


def record_start(state: Path, role: str) -> None:
    field.write_json(state / (role + "-start.json"), {"terminal": os.environ.get("HC_TERMINAL_ID"),
                     "pid": os.getpid(), "start": runtime.process_start(os.getpid())})


def starting(terminal: dict, state: Path | None = None, role: str = "chat") -> bool:
    record = field.read_json((state or state_dir()) / (role + "-start.json"), {})
    if record.get("terminal") == terminal["address"]:
        pid = record.get("pid")
        return bool(isinstance(pid, int) and record.get("start") and
                    runtime.process_start(pid) == record["start"])
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
        if not target:
            recovered = [t for t in terminals.values() if t.get("labels", {}).get("lantern.instance") == record["instance"]
                         and t.get("labels", {}).get("lantern.desktop") == role
                         and t.get("labels", {}).get("herdr.tab") == tab["id"] and t.get("state") == "live"]
            if len(recovered) > 1:
                raise RuntimeError("Multiple readouts have the same ownership label; preserve them")
            if recovered:
                target = recovered[0]
                where = locate(inventory, target["address"])
                if where:
                    saved = record[role] = {"pane": where[2]["pane"], "terminal": target["address"]}
                    field.write_json(state / "home.json", record)
        if target and target.get("state") == "live":
            current = locate(inventory, target["address"])
            if not current:
                run([HC, "label", target["address"], "herdr.space=" + space["id"], "herdr.tab=" + tab["id"],
                     "lantern.desktop=" + role, "lantern.instance=" + record["instance"]])
                current = wait_location(target["address"])
                record[role] = {"pane": current[2]["pane"], "terminal": target["address"]}
                field.write_json(state / "home.json", record)
                changed = True
            if current[1]["id"] != tab["id"]:
                raise RuntimeError("A saved Lantern readout moved; leave its current location unchanged")
            from lantern_views import matches_watcher
            fg = native_process(target)
            if matches_watcher(fg, ROOT, state, role):
                run([HC, "label", target["address"], "lantern.desktop=" + role, "lantern.instance=" + record["instance"]])
                continue
            argv = fg.get("argv") or []
            if str(Path(__file__).resolve()) in argv and role in argv or starting(target, state, role):
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
            run([HC, "label", target["address"], "lantern.desktop=", "lantern.instance=", "herdr.space=", "herdr.tab="])
            record.setdefault("parked_readouts", []).append(target["address"])
        elif saved.get("terminal"):
            mounted = locate(inventory, saved["terminal"])
            if mounted:
                if mounted[1]["id"] != tab["id"]:
                    raise RuntimeError("A saved Lantern readout moved; preserve its pane")
                try:
                    ended = core("terminal.get", terminal=saved["terminal"])
                except CoreError as exc:
                    if exc.kind != "not_found":
                        raise
                    ended = {"state": "finished"}
                if ended.get("state") != "finished":
                    raise RuntimeError("Readout state changed during repair; retry open")
                app("pane.close", pane=mounted[2]["pane"])
        anchor = record.get("field", {}).get("pane") if role == "usage" else pane["pane"]
        saved = app("pane.split", pane=anchor, direction="down" if role == "usage" else "right",
                    argv=["sh", str(ROOT / "bin/lantern-app"), role, str(state)])
        record[role] = saved
        field.write_json(state / "home.json", record)
        run([HC, "label", saved["terminal"], "lantern.desktop=" + role, "lantern.instance=" + record["instance"]])
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
        instance = runtime.digest([str(state.resolve()), str(config_dir().resolve())])
        inventory = json.loads(run([APP, "ls"]))
        terminals = core("terminal.list")["terminals"]
        record = field.read_json(state / "home.json", {})
        if record.get("instance") not in (None, instance):
            raise RuntimeError("This state directory belongs to another configuration; select a separate desktop state directory")
        home = next((t for t in terminals if t["state"] == "live" and t["address"] == record.get("terminal")), None)
        if not home:
            candidates = [t for t in terminals if t["state"] == "live" and
                          t.get("labels", {}).get("lantern.instance") == instance and
                          t.get("labels", {}).get("lantern.desktop") == "home"]
            if len(candidates) > 1:
                raise RuntimeError("Multiple Lantern homes match this instance; preserve them")
            home = candidates[0] if candidates else None
        binary = record.get("helper_binary") or (home or {}).get("labels", {}).get("lantern.helper")
        if home and (active_helper(home, config, binary) or starting(home, state)):
            if record.get("terminal") != home["address"]:
                record = {"terminal": home["address"]}
            record["instance"] = instance
            record["helper_binary"] = binary or (home.get("foreground") or {}).get("argv", [""])[0]
            run([HC, "label", home["address"], "lantern.instance=" + instance,
                 "lantern.helper=" + record["helper_binary"]])
            ensure_views(record, state)
            return {"terminal": home["address"], "reused": True}
        helper_binary = helper_argv(config, "")[0]
        if home:
            run([HC, "label", home["address"], "lantern.desktop=", "lantern.instance=", "lantern.helper="])
        argv = ["sh", str(ROOT / "bin/lantern-app"), "chat", str(state), str(config_dir())]
        space = next((s for s in inventory["spaces"] if s["name"].lower() in {"lantern", "🔥 lantern"}
                      and not s.get("machine")), None)
        if space:
            made = app("tab.new", space=space["id"], name="Lantern", folder=str(state), argv=argv)
        else:
            made = app("space.new", name="lantern", tab="Lantern", folder=str(state), argv=argv)
        record = dict(made, instance=instance, helper_binary=helper_binary)
        field.write_json(state / "home.json", record)
        run([HC, "label", made["terminal"], "lantern.desktop=home", "lantern.instance=" + instance,
             "lantern.helper=" + helper_binary])
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
            record_start(state_dir(), command)
            os.environ["LANTERN_VIEW_SCOPE"] = "local"
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
