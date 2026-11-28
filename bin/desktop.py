"""Run Lantern in Herdr's desktop app without changing the legacy plugin."""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import runpy
import shlex
import shutil
import subprocess
import sys
import time
from typing import Callable, NamedTuple

import field_status as field
from lantern_views import DESKTOP_VIEWS, current_watcher, matches_watcher
import usage_runtime as runtime
from win_cmd import cmd_argv, cmd_command_line

ROOT = Path(__file__).resolve().parents[1]


def launcher(name: str) -> Path:
    return ROOT / "bin" / (name + ".cmd" if os.name == "nt" else name)


READER = launcher("lantern-app-read")
READ_SECONDS = 5
APP_AGENT_NAMES = {
    "claude-code": "claude",
    "grok-build": "grok",
    "antigravity-cli": "agy",
    "antigravity": "agy",
    "agent": "cursor",
    "cursor-agent": "cursor",
    "oh-my-pi": "omp",
}


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


def instance_id(state: Path) -> str:
    return runtime.digest([str(state.resolve()), str(config_dir().resolve())])


def desktop_launch(*args: str) -> list[str]:
    if os.name == "nt":
        return [sys.executable, str(ROOT / "bin" / "desktop.py"), *args]
    return ["sh", str(launcher("lantern-app")), *args]


def batch_command(path: str) -> bool:
    return os.name == "nt" and Path(path).suffix.lower() in {".cmd", ".bat"}


def native_command(argv: list[str]) -> list[str] | str:
    if batch_command(argv[0]):
        *prefix, command = cmd_argv(argv, os.environ.get("COMSPEC", "cmd.exe"))
        # cmd parses the text after /c itself. CRT argument escaping would
        # turn the command's quotes into literal backslashes.
        return subprocess.list2cmdline(prefix) + ' "' + command + '"'
    return argv


def run(argv: list[str], timeout: float = 15) -> str:
    result = subprocess.run(native_command(argv), capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode:
        raise RuntimeError(field.clean(result.stderr or result.stdout or f"{argv[0]} failed", 400))
    return result.stdout


def core_calls(calls: list[tuple[str, dict]], timeout: float = 10) -> list[dict]:
    """Send several requests over one core connection and return their results in order."""
    requests = [{"jsonrpc": "2.0", "id": index, "method": method, "params": params}
                for index, (method, params) in enumerate(calls, 1)]
    proc = subprocess.run(native_command([HC, "connect"]), input="".join(json.dumps(r) + "\n" for r in requests),
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
        raise field.HerdrTimeout("Herdr desktop read timed out") from None
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


def sub_agent_parent_tab(address: str, parent: object, terminals: dict[str, dict],
                         pane_tabs: dict[str, tuple[str, str]]) -> tuple[str, str] | None:
    visited = {address}
    while isinstance(parent, str) and parent and "/" not in parent and parent not in visited:
        if parent in pane_tabs:
            return pane_tabs[parent]
        visited.add(parent)
        terminal = terminals.get(parent)
        parent = terminal.get("parent") if isinstance(terminal, dict) else None
    return None


def sub_agent_rows(terminals: dict[str, dict], pane_tabs: dict[str, tuple[str, str]]) -> list[dict]:
    rows = []
    for address, terminal in terminals.items():
        if not isinstance(address, str) or not address or "/" in address or address in pane_tabs:
            continue
        if not isinstance(terminal, dict) or terminal.get("state") != "live":
            continue
        status = terminal.get("status")
        app = status.get("app") if isinstance(status, dict) else None
        if not isinstance(app, str) or not app:
            continue
        try:
            if bridge(terminal):
                continue
        except (AttributeError, KeyError, TypeError, ValueError):
            # Malformed foreground metadata on a parked terminal cannot invalidate pane rows.
            continue
        parent = terminal.get("parent")
        attribution = sub_agent_parent_tab(address, parent, terminals, pane_tabs)
        if attribution is None:
            continue
        tab_id, workspace_id = attribution
        rows.append({
            "pane_id": f"sub-agent:{tab_id}:{address}",
            "terminal_id": address,
            "tab_id": tab_id,
            "workspace_id": workspace_id,
            "agent": APP_AGENT_NAMES.get(app, app),
            "agent_status": status.get("state") or "unknown",
            "cwd": terminal.get("cwd"),
            "name": terminal.get("name"),
            "state_change_seq": terminal.get("status_ms"),
            "sub_agent": True,
            "parent": parent,
        })
    return rows


def snapshot(inventory: dict, terminals: dict[str, dict], sources: dict[str, dict]) -> dict:
    result = {"workspaces": [], "tabs": [], "agents": [], "panes": []}
    pane_tabs = {}
    for space in inventory["spaces"]:
        if space.get("machine"):
            continue
        label = "Lantern" if space["name"].lower() in {"lantern", "🔥 lantern"} else space["name"]
        result["workspaces"].append({"workspace_id": space["id"], "label": label})
        for tab in space["tabs"]:
            result["tabs"].append({"tab_id": tab["id"], "workspace_id": space["id"], "label": tab["name"]})
            for pane in tab["terminals"]:
                address = pane["terminal"]
                pane_tabs[address] = (tab["id"], space["id"])
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
                    row["agent"] = APP_AGENT_NAMES.get(status["app"], status["app"])
                if row.get("agent"):
                    result["agents"].append(row)
                result["panes"].append(row)
    result["sub_agents"] = sub_agent_rows(terminals, pane_tabs)
    return result


def wrapped_argv(argv: object) -> list[str] | None:
    if (isinstance(argv, list) and len(argv) >= 5 and all(isinstance(arg, str) for arg in argv)
            and argv[0] == argv[3] and Path(argv[0]).name in {"sh", "bash", "zsh"}
            and argv[1:3] in (["-ic", '"$@"; exec "$0" -i'], ["-lic", '"$@"; exec "$0" -li'])):
        return argv[4:]
    return None


def wrapped_child(parent: object, argv: list[str]) -> int | None:
    if type(parent) is not int or parent <= 0:
        return None
    try:
        matches = []
        for pid in runtime.child_pids(parent):
            start = runtime.process_start(pid)
            if start and process_argv(pid) == argv and runtime.process_start(pid) == start:
                matches.append(pid)
        if len(matches) == 1:
            return matches[0]
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return None


def native_process(terminal: dict) -> dict:
    fg = dict(terminal.get("foreground") or {})
    if not fg:
        return fg
    argv = wrapped_argv(fg.get("argv"))
    if argv and (pid := wrapped_child(fg.get("pid"), argv)) is not None:
        fg.update(pid=pid, argv=argv, name=Path(argv[0]).name)
    cwd = fg.get("cwd") or terminal.get("cwd")
    if not isinstance(cwd, str) or not Path(cwd).is_absolute():
        raise RuntimeError("Native process working directory unavailable")
    fg["cwd"] = cwd
    return fg


def pane_route(world: World, pane: str) -> tuple:
    """The terminal, and any bridged legacy pane, behind an app pane."""
    for rows in (world.view["panes"], world.view.get("sub_agents", ())):
        for row in rows:
            if row["pane_id"] == pane:
                return row["terminal_id"], row.get("legacy_binary"), row.get("legacy_pane_id")
    raise RuntimeError("Pane no longer exists")


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
            "HELPER_SPAWN_KIND", "HELPER_SPAWN_MODEL", "HELPER_SPAWN_EFFORT", "HELPER_PERMISSION", "HELPER_EXTRA_ARGS",
            "HELPER_KATA_PROJECT"]
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
        separator = " " if batch_command(binary) else "\n"
        args += [prompt + separator + "Read these instructions and report the field. Do not change any project."]
    return args


def install_claude_hooks(work: Path) -> None:
    folder = runtime.private_dir(work / ".claude")
    path = folder / "settings.local.json"
    settings = json.loads(path.read_text()) if path.exists() else {}
    hooks = settings.setdefault("hooks", {})
    argv = [sys.executable, str(ROOT / "bin/agent_status.py")]
    command = cmd_command_line(argv) if os.name == "nt" else shlex.join(argv)
    events = {"SessionStart": "idle", "UserPromptSubmit": "working",
              "PreToolUse": "working", "Notification": "blocked", "Stop": "done"}
    for event, state in events.items():
        entries = hooks.setdefault(event, [])
        report = {"type": "command", "command": command + " " + state}
        if not any(report in entry.get("hooks", []) for entry in entries):
            entries.append({"matcher": "permission_prompt|elicitation_dialog" if event == "Notification" else "",
                            "hooks": [report]})
    field.write_json(path, settings)


def chat() -> int | None:
    record_start(state_dir(), "chat")
    preferences = config_dir().resolve()
    config = read_config(preferences / "helper.conf")
    state = state_dir()
    work = runtime.private_dir(state / "workdir")
    prompt = (ROOT / "desktop-prompt.md").read_text()
    prompt += "\nSaved settings: " + json.dumps({k: v for k, v in config.items() if k not in {"HELPER_EXTRA_ARGS", "HELPER_KATA_PROJECT"}})
    prompt += f"\nModel routing: {ROOT / 'bin/model-route'}\nPreflight: {ROOT / 'bin/model-preflight'}\n"
    prompt += f"\nLantern config directory (LANTERN_CONFIG_DIR): {preferences}\n"
    prompt += f"\nSaved Codex reset helper: {ROOT / 'bin/codex_resets.py'}\n"
    prompt += "\n" + (ROOT / "guidance.md").read_text(encoding="utf-8") + "\n"
    prompt += "\n" + (ROOT / "manager-duty.md").read_text(encoding="utf-8") + "\n"
    if config.get("HELPER_KATA_PROJECT"):
        prompt += "\n" + (ROOT / "kata-monitor.md").read_text()
        prompt += "\nKata project and wake target: " + json.dumps({"project": config["HELPER_KATA_PROJECT"],
                                                                   "target": "lantern-" + instance_id(state)[:16]}) + "\n"
    for filename in ("CLAUDE.md", "AGENTS.md", "GEMINI.md"):
        (work / filename).write_text(prompt)
    kind, binary = helper_command(config)
    # The full instructions live in the workdir files. A batch command
    # line cannot carry the multiline prompt through cmd.exe.
    initial_prompt = "Read the full Lantern instructions in this folder." if batch_command(binary) else prompt
    args = helper_argv(config, initial_prompt)
    if kind == "claude":
        install_claude_hooks(work)
    for name in list(os.environ):
        if name.startswith("HERDR_") and name != "HERDR_APP_HOME":
            os.environ.pop(name, None)
    os.environ.update(LANTERN_CONFIG_DIR=str(preferences),
                      LANTERN_DESKTOP_COMMAND=str(launcher("lantern-app")),
                      LANTERN_HERD_STATE_DIR=str(state), LANTERN_TEAM_STATE_DIR=str(state),
                      LANTERN_TEAM_MAILBOX=str(ROOT / "bin/team_mailbox.py"))
    os.chdir(work)
    if os.name == "nt":
        return subprocess.run(native_command(args), check=False).returncode
    os.execv(args[0], args)
    return None


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
    if os.name == "nt":
        expected = expected.removesuffix(".cmd").removesuffix(".bat")
        actual = actual.removesuffix(".cmd").removesuffix(".bat")
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


def same_path(value, path: Path) -> bool:
    # Resolving a relative argv path here would use Open's cwd, which can differ from the watcher's.
    if not isinstance(value, str) or not Path(value).is_absolute():
        return False
    try:
        return os.path.normcase(str(Path(value).resolve())) == os.path.normcase(str(path.resolve()))
    except (OSError, RuntimeError, ValueError):
        return False


def process_argv(pid) -> list[str] | None:
    """Read current argv on Linux and macOS."""
    if type(pid) is not int or pid <= 0:
        return None
    try:
        if sys.platform.startswith("linux"):
            data = (Path("/proc") / str(pid) / "cmdline").read_bytes()
            if not data.endswith(b"\0"):
                return None
            return [os.fsdecode(arg) for arg in data[:-1].split(b"\0")]
        if sys.platform != "darwin":
            return None
        libc = ctypes.CDLL(None, use_errno=True)
        sysctl = libc.sysctl
        sysctl.argtypes = [ctypes.POINTER(ctypes.c_int), ctypes.c_uint, ctypes.c_void_p,
                           ctypes.POINTER(ctypes.c_size_t), ctypes.c_void_p, ctypes.c_size_t]
        sysctl.restype = ctypes.c_int
        # KERN_PROCARGS2 can under-report its buffer size on macOS.
        argmax_mib = (ctypes.c_int * 2)(1, 8)  # CTL_KERN, KERN_ARGMAX
        argmax = ctypes.c_int()
        size = ctypes.c_size_t(ctypes.sizeof(argmax))
        if (sysctl(argmax_mib, len(argmax_mib), ctypes.byref(argmax), ctypes.byref(size), None, 0)
                or size.value != ctypes.sizeof(argmax) or not 4 < argmax.value <= 2 * 1024 * 1024):
            return None
        mib = (ctypes.c_int * 3)(1, 49, pid)  # CTL_KERN, KERN_PROCARGS2
        buffer = ctypes.create_string_buffer(argmax.value)
        size = ctypes.c_size_t(len(buffer))
        if (sysctl(mib, len(mib), buffer, ctypes.byref(size), None, 0)
                or not 4 < size.value <= len(buffer)):
            return None
        data = buffer.raw[:size.value]
        argc = int.from_bytes(data[:4], sys.byteorder, signed=True)
        if not 0 < argc < len(data):
            return None
        # Darwin places the executable path and NUL padding before argv and the environment after it.
        offset = data.index(b"\0", 4) + 1
        args = data[offset:].lstrip(b"\0").split(b"\0", argc)
        if len(args) == argc + 1:
            return [os.fsdecode(arg) for arg in args[:argc]]
    except (OSError, ValueError, ctypes.ArgumentError):
        pass
    return None


def original_launch(terminal: dict, state: Path, role: str) -> bool:
    """The wrapper execs an interactive shell once its launched command returns."""
    launch = terminal.get("argv")
    if os.name == "nt" and windows_readout_launch(launch, state, role):
        current = (terminal.get("foreground") or {}).get("argv")
        return windows_readout_launch(current, state, role) and same_path(current[0], Path(launch[0]))
    argv = wrapped_argv(launch)
    if not (argv and len(argv) == 4 and Path(argv[0]).name == "sh"
            and same_path(argv[1], ROOT / "bin" / "lantern-app") and argv[2] == role
            and same_path(argv[3], state)):
        return False
    return process_argv(terminal.get("pid")) == launch


def windows_readout_launch(argv: object, state: Path, role: str) -> bool:
    return (isinstance(argv, list) and len(argv) == 4
            and isinstance(argv[0], str) and Path(argv[0]).is_absolute()
            and same_path(argv[1], ROOT / "bin" / "desktop.py")
            and argv[2] == role and same_path(argv[3], state))


def watcher_args(role: str, state: Path, executable: str) -> list[str]:
    script = ROOT / "bin" / DESKTOP_VIEWS[role][1]
    argv = [executable, str(script), "--state-dir", str(state)]
    if role != "system":
        argv.extend(["--herdr", str(READER)])
    return [*argv, "watch"]


def readout_process(terminal: dict, state: Path, role: str) -> dict:
    fg = native_process(terminal)
    if os.name == "nt" and windows_readout_launch(fg.get("argv"), state, role) and original_launch(terminal, state, role):
        fg["argv"] = watcher_args(role, state, fg["argv"][0])
    return fg


def read_state(path: Path) -> dict:
    try:
        return field.read_json(path, {})
    except ValueError:
        return {}


def tolerant(check: Callable[[], bool]) -> bool:
    # A bad receipt is absent. A failed fingerprint read cannot establish that a watcher is stale.
    try:
        return check()
    except ValueError:
        return False


def launched(terminal: dict, fg: dict, state: Path, role: str) -> str:
    pid = fg.get("pid")
    if type(pid) is not int or pid <= 0:
        return ""
    start = runtime.process_start(pid)
    if not start or pid == terminal.get("pid"):
        return start
    path = state / f"{role}-start.json"
    receipt = read_state(path)
    try:
        fresh = abs(path.stat().st_mtime * 1000 - terminal["created_ms"]) < 30000
    except (OSError, KeyError, TypeError):
        fresh = False
    if (fresh and receipt.get("terminal") == terminal.get("address") and receipt.get("pid") == pid
            and receipt.get("start") == start and original_launch(terminal, state, role)):
        return start
    return ""


def stock_readout(terminal: dict, fg: dict, state: Path, role: str, script: str) -> str:
    argv = fg.get("argv")
    if not (isinstance(argv, list) and len(argv) in {5, 7} and argv[2] == "--state-dir"
            and argv[-1] == "watch" and same_path(argv[1], ROOT / "bin" / script)
            and same_path(argv[3], state)):
        return ""
    if role == "system" and len(argv) != 5:
        return ""
    if role != "system" and not (len(argv) == 7 and argv[4] == "--herdr" and same_path(argv[5], READER)):
        return ""
    return launched(terminal, fg, state, role)


def stop_readout(record: dict, state: Path, role: str, address: str, old_pid: int, script: str) -> None:
    end = time.monotonic() + 5

    def call(method: str) -> dict | None:
        remaining = end - time.monotonic()
        if remaining <= 0:
            raise RuntimeError("Outdated readout did not stop; retry open")
        try:
            return core_calls([(method, {"terminal": address})], remaining)[0]
        except subprocess.TimeoutExpired:
            return None
        except CoreError as exc:
            if exc.kind != "not_found":
                raise
            return None

    fresh = call("terminal.get")
    fg = readout_process(fresh, state, role) if fresh and fresh.get("state") == "live" else {}
    start = stock_readout(fresh, fg, state, role, script) if fresh and fg.get("pid") == old_pid else ""
    if not start:
        raise RuntimeError("Readout state changed during repair; retry open")
    # The marker must outlive a timeout so a retry cannot replace a pane whose watcher is still running.
    pending = {"pid": old_pid, "start": start}
    record[role] = dict(record[role], stopping=pending)
    field.write_json(state / "home.json", record)
    observed = call("terminal.kill")
    while True:
        if observed and observed.get("state") == "finished" and not pending.get("finished"):
            pending["finished"] = True
            field.write_json(state / "home.json", record)
        if pending.get("finished") and runtime.process_start(old_pid) != start:
            return
        if time.monotonic() >= end:
            raise RuntimeError("Outdated readout did not stop; retry open")
        time.sleep(0.05)
        observed = call("terminal.get")


def confirm_pending_stop(saved: dict) -> None:
    pending = saved["stopping"]
    if isinstance(pending, dict):
        pid, start = pending.get("pid"), pending.get("start")
        finished = pending.get("finished", False)
        if (type(pid) is int and pid > 0 and isinstance(start, str) and start and type(finished) is bool
                and runtime.process_start(pid) != start):
            if finished:
                return
            try:
                ended = core_calls([("terminal.get", {"terminal": saved["terminal"]})], 5)[0]
                if ended.get("state") == "finished":
                    return
            except subprocess.TimeoutExpired:
                pass
            except CoreError as exc:
                # A previously known terminal on this core can only be removed after it finishes.
                if exc.kind == "not_found":
                    return
                raise
    raise RuntimeError("Outdated readout did not stop; retry open")


def close_readout_pane(address: str, tab_id: str) -> None:
    def mounted(timeout: float = 15):
        return locate(json.loads(run([APP, "ls"], timeout)), address)

    current = mounted()
    if not current:
        return
    if current[1]["id"] != tab_id:
        raise RuntimeError("A saved Lantern readout moved; preserve its pane")
    try:
        app("pane.close", pane=current[2]["pane"])
    except RuntimeError:
        if mounted():
            raise
        return
    end = time.monotonic() + 3
    while (remaining := end - time.monotonic()) > 0:
        try:
            if not mounted(remaining):
                return
        except subprocess.TimeoutExpired:
            break
        time.sleep(0.05)
    raise RuntimeError("Readout pane close is still pending; retry open")


def view_layout(tab_id: str) -> tuple[dict, dict, list[dict]]:
    def leaves(node: dict) -> list[dict]:
        if node["type"] == "block":
            return [node]
        return [leaf for child in node.get("children", []) for leaf in leaves(child)]

    doc = core("doc.get", name="herdr-app")
    for space in doc["value"]["spaces"]:
        for tab in space["tabs"]:
            if tab["id"] == tab_id:
                return doc, tab, leaves(tab["root"])
    raise RuntimeError("The Lantern tab moved during layout setup; retry open")


def ensure_views(record: dict, state: Path) -> list[str]:
    terminals = {t["address"]: t for t in core("terminal.list")["terminals"]}
    inventory = json.loads(run([APP, "ls"]))
    location = locate(inventory, record["terminal"])
    if not location:
        raise RuntimeError("The saved Lantern terminal has no app pane")
    space, tab, pane = location
    record.update(space=space["id"], tab=tab["id"], pane=pane["pane"])
    field.write_json(state / "home.json", record)
    field.write_json(state / "views.json", {"home_pane_id": pane["pane"]})

    def focus_home() -> None:
        app("space.move", id=record["space"], index=1)
        app("pane.focus", pane=record["pane"])

    labeled = [(t, t.get("labels") or {}) for t in terminals.values() if t.get("state") == "live"]
    owned = [(t, labels["lantern.desktop"]) for t, labels in labeled
             if labels.get("lantern.instance") == record["instance"] and labels.get("herdr.tab") == tab["id"]
             and labels.get("lantern.desktop") in DESKTOP_VIEWS]
    known = {record["terminal"], *(t["address"] for t, _ in owned)}
    known.update(record[role]["terminal"] for role in DESKTOP_VIEWS if record.get(role))
    _, _, blocks = view_layout(tab["id"])
    mounted_ids = {node.get("content", {}).get("terminal") for node in blocks}
    if len(mounted_ids) != len(blocks) or not mounted_ids <= known:
        focus_home()
        return ["The Lantern tab has extra panes. Move them before reopening to update the readout layout."]

    changed = not record.get("layout_ready")
    notices = []
    for role, (label, script) in DESKTOP_VIEWS.items():
        saved = record.get(role) or {}
        target = terminals.get(saved.get("terminal"))
        if not target:
            recovered = [t for t, saved_role in owned if saved_role == role]
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
            fg = readout_process(target, state, role)
            if matches_watcher(fg, ROOT, state, role):
                stamp = read_state(state / f"{role}-watcher.json")
                pid = fg.get("pid")
                recorded = type(pid) is int and stamp.get("pid") == pid and bool(stamp.get("code"))
                keep = (tolerant(lambda: current_watcher({"foreground_processes": [fg]}, ROOT, state, role))
                        or (not recorded and time.time() * 1000 - target.get("created_ms", 0) < 30000
                            and tolerant(lambda: starting(target, state, role))))
                if not keep and not stock_readout(target, fg, state, role, script):
                    notices.append(f"{label} in {target['address']} runs older Lantern code with its own "
                                   "command, so Lantern left it running; restart it to update")
                    keep = True
                if keep:
                    run([HC, "label", target["address"], "lantern.desktop=" + role,
                         "lantern.instance=" + record["instance"]])
                    continue
                stop_readout(record, state, role, target["address"], fg["pid"], script)
                close_readout_pane(target["address"], tab["id"])
            else:
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
            if "stopping" in saved:
                confirm_pending_stop(saved)
                close_readout_pane(saved["terminal"], tab["id"])
            elif mounted := locate(inventory, saved["terminal"]):
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
        anchor = pane["pane"] if role == "field" else record["field"]["pane"]
        saved = app("pane.split", pane=anchor, direction="right" if role == "field" else "down",
                    argv=desktop_launch(role, str(state)))
        record[role] = saved
        record["layout_ready"] = False
        field.write_json(state / "home.json", record)
        run([HC, "label", saved["terminal"], "lantern.desktop=" + role, "lantern.instance=" + record["instance"]])
        changed = True
    if changed:
        doc, layout, blocks = view_layout(tab["id"])
        readout_ids = [record[role]["terminal"] for role in DESKTOP_VIEWS]
        allowed = {record["terminal"], *readout_ids}
        nodes = {node.get("content", {}).get("terminal"): node for node in blocks}
        if len(blocks) != len(allowed) or nodes.keys() != allowed:
            raise RuntimeError("Lantern tab has extra panes; keep their layout unchanged")
        chat_node = dict(nodes[record["terminal"]], size={"weight": 1})
        right = [dict(nodes[address], size={"weight": 1}) for address in readout_ids]
        layout["root"] = {"type": "row", "id": layout["root"]["id"], "children": [chat_node,
            {"type": "column", "id": record["tab"] + "-lantern-readouts", "size": {"weight": 1},
             "children": right}]}
        core("doc.set", name="herdr-app", value=doc["value"], version=doc["version"])
        record["layout_ready"] = True
        field.write_json(state / "home.json", record)
    focus_home()
    return notices


def open_home() -> dict:
    state = state_dir()
    lock = runtime.FileLock(state / "open.lock")
    if not lock.acquire():
        raise RuntimeError("Lantern is already opening")
    try:
        config = read_config(config_dir() / "helper.conf")
        instance = instance_id(state)
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
            notices = ensure_views(record, state)
            return {"terminal": home["address"], "reused": True, "notices": notices}
        helper_binary = helper_argv(config, "")[0]
        if home:
            labels = [HC, "label", home["address"], "lantern.desktop=", "lantern.instance=", "lantern.helper="]
            if "herdr-kata.agent" in home.get("labels", {}):
                labels.append("herdr-kata.agent=")
            run(labels)
        argv = desktop_launch("chat", str(state), str(config_dir()))
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
        notices = ensure_views(record, state)
        return dict(made, native=True, notices=notices)
    finally:
        lock.close()


def install_launchers() -> None:
    target = Path.home() / ".local/bin/lantern-app"
    marker = "# Lantern desktop launcher"
    files = {target: ("#!/bin/sh\n" + marker + "\nexec sh " +
                      shlex.quote(str(ROOT / "bin/lantern-app")) + ' "$@"\n').encode("utf-8")}
    if os.name == "nt":
        source = str(launcher("lantern-app")).replace("%", "%%")
        files[target.with_suffix(".cmd")] = (
            f'@echo off\r\nrem {marker}\r\n"{source}" %*\r\n').encode("utf-8")
    for path in files:
        if path.is_symlink() or path.exists() and marker.encode() not in path.read_bytes():
            raise RuntimeError(f"Refusing to replace an unrelated command: {path}")
    target.parent.mkdir(parents=True, exist_ok=True)
    for path, data in files.items():
        path.write_bytes(data)
        path.chmod(0o700)
        print(path)


def watch_readout(argv: list[str]) -> None:
    if os.name == "nt":
        # Keep the process that wrote the start receipt as the watcher.
        original = sys.argv
        try:
            sys.argv = argv[1:]
            runpy.run_path(argv[1], run_name="__main__")
        finally:
            sys.argv = original
    else:
        os.execv(sys.executable, argv)


def main() -> int:
    try:
        command, *args = sys.argv[1:] or ["open"]
        if command == "read-model":
            read_model(args)
        elif command == "install":
            install_launchers()
        elif command == "snapshot":
            print(json.dumps(read_world().view))
        elif command == "open":
            print(json.dumps(open_home()))
        elif command == "chat":
            if args:
                os.environ["LANTERN_DESKTOP_STATE_DIR"] = args[0]
                os.environ["LANTERN_CONFIG_DIR"] = args[1]
            return chat() or 0
        elif command in DESKTOP_VIEWS:
            if args:
                os.environ["LANTERN_DESKTOP_STATE_DIR"] = args[0]
            record_start(state_dir(), command)
            os.environ["LANTERN_VIEW_SCOPE"] = "local"
            record = field.read_json(state_dir() / "home.json", {})
            if record.get("pane"):
                os.environ["HERDR_PANE_ID"] = record["pane"]
            watch_readout(watcher_args(command, state_dir(), sys.executable))
        else:
            raise RuntimeError("Usage: lantern-app [open|snapshot|field|usage]")
        return 0
    except (RuntimeError, OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        print("lantern-app: " + field.clean(exc, 400), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
