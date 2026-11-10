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
from typing import NamedTuple

import field_status as field
import usage_runtime as runtime

ROOT = Path(__file__).resolve().parents[1]
READER = ROOT / "bin" / "lantern-app-read"
READ_SECONDS = 5


def tool(name: str) -> str:
    # The same directories bin/lantern-app puts first, since a readout or a
    # script can start without them on PATH.
    extra = [os.path.expanduser(folder) for folder in ("~/.grok/bin", "~/.local/bin", "~/bin")]
    path = os.pathsep.join([*extra, "/opt/homebrew/bin", "/usr/local/bin", os.environ.get("PATH", "")])
    return shutil.which(name, path=path) or name


APP = tool(os.environ.get("LANTERN_APP_BIN", "herdr-app"))
HC = tool(os.environ.get("LANTERN_HC_BIN", "hc"))


class World(NamedTuple):
    """One read of the desktop: this core's identity, the readout rows, and core's terminals."""
    server: dict
    view: dict
    terminals: dict


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


def core_calls(calls: list[tuple[str, dict]], timeout: float = 10) -> list[dict]:
    """Send several requests over one core connection and return their results in order."""
    requests = [{"jsonrpc": "2.0", "id": index, "method": method, "params": params}
                for index, (method, params) in enumerate(calls, 1)]
    proc = subprocess.run([HC, "connect"], input="".join(json.dumps(r) + "\n" for r in requests),
                          capture_output=True, text=True, timeout=timeout, check=False)
    if proc.returncode:
        raise RuntimeError(field.clean(proc.stderr, 300))
    responses = {}
    for line in proc.stdout.splitlines():
        if line.strip():
            response = json.loads(line)
            responses[response.get("id")] = response
    results = []
    for index in range(1, len(calls) + 1):
        response = responses[index]
        if "error" in response:
            raise CoreError(response["error"])
        results.append(response["result"])
    return results


def core(method: str, **params) -> dict:
    return core_calls([(method, params)])[0]


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


def reads(binary: str) -> bool:
    """Whether a readout's Herdr command is this checkout's desktop reader."""
    try:
        return Path(binary).resolve() == READER.resolve()
    except (OSError, RuntimeError):
        return False


def core_start(status: dict) -> str:
    # process_start passes the pid to ctypes, whose ArgumentError would escape every
    # read-error handler, so a malformed pid stops here.
    if type(status["pid"]) is not int:
        raise RuntimeError("Herdr core identity unavailable")
    started = runtime.process_start(status["pid"])
    if not started:
        raise RuntimeError("Cannot identify this core's lifetime")
    return started


def core_identity(status: dict) -> dict:
    return runtime.core_identity(status["core"]["id"], core_start(status))


def boundary_errors(action):
    """Report a slow or malformed desktop reply as the readouts' ordinary RuntimeError."""
    try:
        return action()
    except subprocess.TimeoutExpired:
        raise RuntimeError("Herdr desktop read timed out") from None
    except (KeyError, IndexError, TypeError, ValueError, AttributeError):
        raise RuntimeError("Herdr desktop returned an unexpected reply") from None


def server_identity(timeout: float) -> dict:
    return boundary_errors(lambda: core_identity(core_calls([("server.status", {})], timeout)[0]))


def read_world(seconds: float = READ_SECONDS) -> World:
    """Read the app's layout, core's terminals, and bridged legacy panes within one deadline."""
    end = time.monotonic() + seconds

    def left() -> float:
        remaining = end - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired("desktop read", seconds)
        return remaining

    def read() -> World:
        inventory = json.loads(run([APP, "ls"], left()))
        status, listing = core_calls([("server.status", {}), ("terminal.list", {})], left())
        terminals = {t["address"]: t for t in listing["terminals"]}
        # The live list leaves out finished terminals, which pile up. Ask only about the
        # ones a pane still shows; one core does not know stays missing and fails the read.
        missing = [pane["terminal"] for space in inventory["spaces"] if not space.get("machine")
                   for tab in space["tabs"] for pane in tab["terminals"] if pane["terminal"] not in terminals]
        if missing:
            try:
                found = core_calls([("terminal.get", {"terminal": address}) for address in missing], left())
            except CoreError as exc:
                if exc.kind != "not_found":
                    raise
                found = []
            terminals.update((terminal["address"], terminal) for terminal in found)
        sources = {}
        for binary in {b[0] for t in terminals.values() if (b := bridge(t))}:
            data = json.loads(run([binary, "api", "snapshot"], min(5, left())))["result"]["snapshot"]
            recognized = {agent["terminal_id"]: agent for agent in data["agents"]}
            sources[binary] = {}
            for pane in data["panes"]:
                pane = dict(pane)
                pane.pop("agent", None)
                pane.update(recognized.get(pane["terminal_id"], {}))
                sources[binary][pane["terminal_id"]] = pane
        return World(core_identity(status), snapshot(inventory, terminals, sources), terminals)
    return boundary_errors(read)


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
                if terminal and terminal.get("state") == "finished":
                    continue  # Its program ended, so the pane holds no agent until it is closed.
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
                                                        "name", "state_change_seq", "completion_seq") if k in original})
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


def pane_route(world: World, pane: str) -> tuple:
    """The terminal, and any bridged legacy pane, behind an app pane."""
    row = next((r for r in world.view["panes"] if r["pane_id"] == pane), None)
    if not row:
        raise RuntimeError("Pane no longer exists")
    return row["terminal_id"], row.get("legacy_binary"), row.get("legacy_pane_id")


def process_info(world: World, pane: str, timeout: float = 15) -> dict:
    terminal_id, binary, legacy_pane = pane_route(world, pane)
    if binary:
        return field.control(binary, ["pane", "process-info", "--pane", legacy_pane], timeout).get("process_info", {})
    terminal = world.terminals[terminal_id]
    fg = native_process(terminal)
    return {"shell_pid": terminal["pid"], "foreground_processes": [fg] if fg else []}


def read_model(args: list[str]) -> None:
    if args == ["status", "--json"]:
        status = core("server.status")
        print(json.dumps({"server": {"running": True, "transport": "herdr-core",
                                     "id": status["core"]["id"], "started_at": core_start(status)}}))
        return
    world = read_world()
    if len(args) == 2 and args[1] == "list" and args[0] in {"workspace", "tab", "agent"}:
        key = args[0] + "s"
        print(json.dumps({"result": {key: world.view[key]}}))
    elif len(args) == 4 and args[:3] == ["pane", "process-info", "--pane"]:
        print(json.dumps({"result": {"process_info": process_info(world, args[3])}}))
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


def install_claude_hooks(work: Path) -> None:
    folder = runtime.private_dir(work / ".claude")
    path = folder / "settings.local.json"
    settings = json.loads(path.read_text()) if path.exists() else {}
    hooks = settings.setdefault("hooks", {})
    command = shlex.join([sys.executable, str(ROOT / "bin/agent_status.py")])
    events = {"SessionStart": "idle", "UserPromptSubmit": "working",
              "PreToolUse": "working", "Notification": "blocked", "Stop": "done"}
    for event, state in events.items():
        entries = hooks.setdefault(event, [])
        report = {"type": "command", "command": command + " " + state}
        if not any(report in entry.get("hooks", []) for entry in entries):
            entries.append({"matcher": "permission_prompt|elicitation_dialog" if event == "Notification" else "",
                            "hooks": [report]})
    field.write_json(path, settings)


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
    if helper_command(config)[0] == "claude":
        install_claude_hooks(work)
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
    expected = os.path.normcase(Path(binary or helper_command(config)[1]).name).removesuffix(".exe")
    actual = os.path.normcase(Path(fg.get("name", "")).name).removesuffix(".exe")
    if actual == expected:
        return True
    argv = fg.get("argv") or []
    if actual == "node" and any(os.path.normcase(Path(arg).stem) == expected for arg in argv):
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
            print(json.dumps(read_world().view))
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
