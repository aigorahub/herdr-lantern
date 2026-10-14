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


def fresh_pane():
    number = max(data.get("next_pane", 1), *(int(p.split(":p")[1]) for p in data["panes"]), 1) + 1
    data["next_pane"] = number
    return f"w9:p{number}"


def rect(pane):
    return data.setdefault("rects", {}).setdefault(pane, {"x": 0, "y": 0, "width": 120, "height": 40})


def split(anchor, pane, direction):
    area = rect(anchor)
    new = dict(area)
    axis, size = ("x", "width") if direction == "right" else ("y", "height")
    share = int(area[size] * float(flag("--ratio", "0.5")))
    new[axis] += share
    new[size] -= share
    area[size] = share
    data["rects"][pane] = new


def detach(pane):
    old = data["panes"][pane]
    area = rect(pane)
    for candidate, item in data["panes"].items():
        if candidate == pane or item["tab_id"] != old["tab_id"]:
            continue
        other = rect(candidate)
        if other["x"] == area["x"] and other["width"] == area["width"]:
            if other["y"] + other["height"] == area["y"] or area["y"] + area["height"] == other["y"]:
                other["y"] = min(other["y"], area["y"])
                other["height"] += area["height"]
                break


def counts():
    for tab in list(data["tabs"]):
        number = sum(p["tab_id"] == tab for p in data["panes"].values())
        if number:
            data["tabs"][tab]["pane_count"] = number
        else:
            del data["tabs"][tab]


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
    starting = data.get("starting", {}).get(pane, 0)
    if starting:
        data["starting"][pane] -= 1
    result = {"process_info": {"shell_pid": 100, "foreground_processes": processes, "interactive_ready": not bool(starting)}}
elif args[:2] == ["tab", "create"]:
    number = len(data["tabs"]) + 1
    tab, pane = f"w9:t{number}", f"w9:p{number}"
    data["tabs"][tab] = {"tab_id": tab, "workspace_id": flag("--workspace"), "label": flag("--label"), "pane_count": 1}
    data["panes"][pane] = {"pane_id": pane, "tab_id": tab, "workspace_id": flag("--workspace")}
    result = {"tab": data["tabs"][tab], "root_pane": data["panes"][pane]}
elif args[:2] == ["pane", "split"]:
    anchor = args[2]
    pane = fresh_pane()
    data["panes"][pane] = dict(data["panes"][anchor], pane_id=pane)
    data["panes"][pane].pop("label", None)
    data["panes"][pane].pop("agent", None)
    data.setdefault("starting", {})[pane] = data.get("starting_reads", 0)
    split(anchor, pane, flag("--direction"))
    counts()
    result = {"pane": data["panes"][pane]}
elif args[:2] == ["pane", "move"]:
    pane = args[2]
    old_tab = data["panes"][pane]["tab_id"]
    if "--new-tab" in args:
        number = max(data.get("next_tab", 1), *(int(t.split(":t")[1]) for t in data["tabs"]), 1) + 1
        data["next_tab"] = number
        tab = f"w9:t{number}"
        detach(pane)
        data["tabs"][tab] = {"tab_id": tab, "workspace_id": "w9", "label": flag("--label"), "pane_count": 1}
        data["panes"][pane]["tab_id"] = tab
        data.setdefault("rects", {})[pane] = {"x": 0, "y": 0, "width": 120, "height": 40}
        changed = True
    elif old_tab == flag("--tab"):
        changed = False  # Native Herdr does not reorder a same-tab move.
    else:
        detach(pane)
        data["panes"][pane]["tab_id"] = flag("--tab")
        split(flag("--target-pane"), pane, flag("--split"))
        changed = True
    counts()
    result = {"move_result": {"pane": data["panes"][pane], "changed": changed}}
elif args[:2] == ["pane", "rename"]:
    data["panes"][args[2]]["label"] = args[3]
elif args[:2] == ["pane", "swap"]:
    source, target = flag("--source-pane"), flag("--target-pane")
    a, b = dict(rect(source)), dict(rect(target))
    data["rects"][source], data["rects"][target] = b, a
    data["focused_pane"] = source
elif args[:2] == ["pane", "layout"]:
    tab = data["panes"][flag("--pane")]["tab_id"]
    result = {"layout": {"focused_pane_id": data.get("focused_pane", "w9:p1"), "panes": [{"pane_id": p, "rect": rect(p)} for p, item in data["panes"].items() if item["tab_id"] == tab]}}
elif args[:2] == ["pane", "run"]:
    pane, command = args[2:4]
    view = "field_status.py" if "field_status.py" in command else "plan_usage.py"
    # Simulate the foreground process which Herdr reports after submission.
    data.setdefault("processes", {})[pane] = [{"pid": 200, "argv": [sys.executable,
        str(Path(os.environ["VIEWS_ROOT"]) / "bin" / view), "--state-dir", os.environ["VIEWS_STATE"], "watch"]}]
    # A real watcher records the code it loaded as it starts.
    sys.path.insert(0, str(Path(os.environ["VIEWS_ROOT"]) / "bin"))
    from lantern_views import code_stamp
    kind = "field" if view == "field_status.py" else "usage"
    (Path(os.environ["VIEWS_STATE"]) / f"{kind}-watcher.json").write_text(
        json.dumps({"pid": 200, "code": code_stamp(Path(os.environ["VIEWS_ROOT"]))}))
elif args[:2] == ["pane", "send-keys"]:
    if args[3:] == ["ctrl+c"] and args[2] not in data.get("ignores_interrupt", []):
        data.setdefault("processes", {})[args[2]] = []
elif args[:2] == ["tab", "rename"]:
    data["tabs"][args[2]]["label"] = args[3]
elif args[:2] == ["pane", "close"]:
    pane = args[2]
    old = data["panes"].pop(pane, {})
    area = rect(pane)
    for candidate, item in data["panes"].items():
        if item["tab_id"] != old.get("tab_id"):
            continue
        other = rect(candidate)
        if other["x"] == area["x"] and other["width"] == area["width"]:
            if other["y"] + other["height"] == area["y"] or area["y"] + area["height"] == other["y"]:
                other["y"] = min(other["y"], area["y"])
                other["height"] += area["height"]
                break
    data.get("processes", {}).pop(pane, None)
    data.get("rects", {}).pop(pane, None)
    counts()
elif args[:2] not in (["workspace", "focus"], ["plugin", "pane"]):
    sys.exit(1)
path.write_text(json.dumps(data))
print(json.dumps({"result": result}, separators=(",", ":")))
