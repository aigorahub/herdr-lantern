"""Persistent Herdr fixture for tab setup through Lantern's real gate."""
import json
import os
import shlex
import sys
from pathlib import Path

path = Path(os.environ["VIEWS_FIXTURE"])
data = json.loads(path.read_text())
args = sys.argv[1:]
data.setdefault("calls", []).append(args)
result = {}


def flag(name, default=None):
    return args[args.index(name) + 1] if name in args else default


if args[0] == "status":
    path.write_text(json.dumps(data))
    print(json.dumps({"server": {"running": True, "socket": str(path.parent / "socket"), "pid": 123, "started_at": "fixture"}}))
    sys.exit(0)
if args[:2] == ["workspace", "list"]:
    result = {"workspaces": [{"workspace_id": "w9", "label": "🔥 lantern"}] if data.get("workspace") else []}
elif args[:2] == ["workspace", "get"]:
    result = {"workspace": {"workspace_id": "w9", "label": "🔥 lantern"}}
elif args[:2] == ["workspace", "create"]:
    data["workspace"] = True
    result = {"workspace": {"workspace_id": "w9"}, "root_pane": {"pane_id": "w9:p0"}}
elif args[:2] == ["plugin", "pane"] and args[2] == "open":
    data["workspace"] = True
    data["panes"]["w9:p1"] = {"pane_id": "w9:p1", "tab_id": "w9:t1", "workspace_id": "w9", "label": "Lantern", "agent": "codex"}
    data["tabs"]["w9:t1"] = {"tab_id": "w9:t1", "workspace_id": "w9", "label": "home", "pane_count": 1}
    result = {"plugin_pane": {"pane": data["panes"]["w9:p1"]}}
elif args[:2] == ["tab", "list"]:
    result = {"tabs": list(data["tabs"].values())}
elif args[:2] == ["agent", "list"]:
    result = {"agents": []}
elif args[:2] == ["pane", "list"]:
    result = {"panes": list(data["panes"].values())}
elif args[:2] == ["pane", "get"]:
    result = {"pane": data["panes"].get(args[2], {})}
elif args[:2] == ["pane", "process-info"]:
    pane = flag("--pane")
    processes = data.get("processes", {}).get(pane, [])
    result = {"process_info": {"shell_pid": 100, "foreground_processes": processes, "interactive_ready": True}}
elif args[:2] == ["tab", "create"]:
    number = len(data["tabs"]) + 1
    tab, pane = f"w9:t{number}", f"w9:p{number}"
    data["tabs"][tab] = {"tab_id": tab, "workspace_id": flag("--workspace"), "label": flag("--label"), "pane_count": 1}
    data["panes"][pane] = {"pane_id": pane, "tab_id": tab, "workspace_id": flag("--workspace")}
    result = {"tab": data["tabs"][tab], "root_pane": data["panes"][pane]}
elif args[:2] == ["pane", "run"]:
    pane, command = args[2:4]
    view = "field_status.py" if "field_status.py" in command else "plan_usage.py"
    # Simulate the foreground process which Herdr reports after submission.
    data.setdefault("processes", {})[pane] = [{"pid": 200, "argv": [sys.executable,
        str(Path(os.environ["VIEWS_ROOT"]) / "bin" / view), "--state-dir", os.environ["VIEWS_STATE"], "watch"]}]
elif args[:2] == ["tab", "rename"]:
    data["tabs"][args[2]]["label"] = args[3]
elif args[:2] == ["pane", "close"]:
    data["panes"].pop(args[2], None)
elif args[:2] not in (["workspace", "focus"], ["plugin", "pane"]):
    sys.exit(1)
path.write_text(json.dumps(data))
print(json.dumps({"result": result}, separators=(",", ":")))
