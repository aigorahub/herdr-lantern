#!/bin/sh
# Action entrypoint: light the lantern in its own workspace.
#
# First open: create a workspace labelled "🔥 lantern" with --cwd $HOME and
# seat the lantern chat there as a tab named "home", then drop the empty
# shell tab the new workspace came with. Later opens: focus the chat that
# is already running, or seat a new one in that same workspace. Herdr
# appends a new workspace at the end of the sidebar; there is no
# pin-to-top, so drag it where you want it.
#
# Bind a key to aigora.lantern.open to reach this from anywhere.
set -eu

plugin_root=${HERDR_PLUGIN_ROOT:-$(CDPATH= cd -- "$(dirname "$0")" && pwd)}
# shellcheck disable=SC1091
. "$plugin_root/lib.sh"
# Herdr on Windows reports this as \\?\C:\path. See helper_posix_path.
plugin_root=$(helper_posix_path "$plugin_root")

# Sidebar naming. The workspace carries the lantern; the tab is the chat.
# pane_title must match [[panes]].title in herdr-plugin.toml, because
# open.sh uses it to recognise a live lantern chat.
workspace_label='🔥 lantern'
tab_label=home
pane_title=Lantern

die() {
    printf 'lantern: %s\n' "$1" >&2
    exit 1
}

herdr=$(helper_resolve_real_herdr "$plugin_root/bin") ||
    die "could not find the herdr binary"
[ -n "${HOME:-}" ] || die "HOME is not set"

state_dir=${HERDR_PLUGIN_STATE_DIR:-${HERDR_PLUGIN_CONFIG_DIR:-$plugin_root}/state}
mkdir -p "$state_dir" || die "could not create $state_dir"
workspace_file=$state_dir/workspace.id
pane_file=$state_dir/pane.id
lock_dir=$state_dir/open.lock

# One open at a time. Two fast key presses must not make two workspaces.
if ! mkdir "$lock_dir" 2>/dev/null; then
    [ -d "$lock_dir" ] || die "could not lock $state_dir"
    if [ -n "$(find "$lock_dir" -prune -mmin +2 2>/dev/null)" ]; then
        # Left behind by a kill or a crash.
        rmdir "$lock_dir" 2>/dev/null || true
        mkdir "$lock_dir" 2>/dev/null || die "could not lock $state_dir"
    else
        printf 'lantern: another open is in flight\n' >&2
        exit 0
    fi
fi
trap 'rmdir "$lock_dir" 2>/dev/null || true' EXIT
trap 'rmdir "$lock_dir" 2>/dev/null || true; exit 1' INT TERM HUP

# 1. A chat that is still running is the answer, wherever it sits now.
#    It names its own workspace, so moving or renaming the tab is safe.
pane=
if [ -f "$pane_file" ]; then
    pane=$(cat "$pane_file") || pane=
fi
workspace=
if [ -n "$pane" ]; then
    workspace=$(helper_lantern_pane_workspace "$herdr" "$pane" "$pane_title") ||
        workspace=
fi
if [ -n "$workspace" ]; then
    "$herdr" workspace focus "$workspace" >/dev/null 2>&1 || true
    if "$herdr" plugin pane focus "$pane"; then
        printf '%s\n' "$workspace" >"$workspace_file" || true
        exit 0
    fi
    # The chat quit between the check and the focus. Seat a new one, and
    # keep the id so step 5 does not offer the same dead pane again.
    workspace=
fi

# 2. No live chat. The remembered workspace counts only while it still
#    carries the lantern label: Herdr reuses ids after a restart, and a
#    stale id would seat the chat in somebody else's workspace.
workspace=
if [ -f "$workspace_file" ]; then
    remembered=$(cat "$workspace_file") || remembered=
    if [ -n "$remembered" ]; then
        label=$(helper_workspace_label "$herdr" "$remembered") || label=
        if [ "$label" = "$workspace_label" ]; then
            workspace=$remembered
        fi
    fi
fi

# 3. Then the label itself.
if [ -z "$workspace" ]; then
    workspace=$(helper_workspace_id_by_label "$herdr" "$workspace_label") ||
        workspace=
fi

# 4. Still nothing: make the lantern its own workspace, at home.
root_pane=
if [ -z "$workspace" ]; then
    # herdr is a native binary. On Windows it wants C:\Users\name, not the
    # /c/Users/name that Git Bash hands out as $HOME.
    created=$("$herdr" workspace create --cwd "$(helper_native_path "$HOME")" \
        --label "$workspace_label" --no-focus) ||
        die "could not create the lantern workspace"
    workspace=$(printf '%s' "$created" | helper_json_value workspace_id)
    [ -n "$workspace" ] || die "could not read the new workspace id"
    root_pane=$(printf '%s' "$created" | helper_json_value pane_id)
fi

# 5. A workspace we did not just make can already hold a chat, if the
#    remembered pane id was lost. Herdr would open a second one.
if [ -z "$root_pane" ]; then
    running=$(helper_lantern_pane_in_workspace "$herdr" "$workspace" "$pane_title") ||
        running=
    if [ -n "$running" ] && [ "$running" != "$pane" ]; then
        printf '%s\n' "$workspace" >"$workspace_file" || true
        printf '%s\n' "$running" >"$pane_file" || true
        "$herdr" workspace focus "$workspace" >/dev/null 2>&1 || true
        if "$herdr" plugin pane focus "$running"; then
            exit 0
        fi
        # It went away too. Seat a new one below.
    fi
fi

# 6. Seat the chat as a tab in that workspace.
if ! opened=$("$herdr" plugin pane open \
    --plugin aigora.lantern \
    --entrypoint helper \
    --placement tab \
    --workspace "$workspace" \
    --focus); then
    # Leave no empty workspace behind when the chat never started.
    if [ -n "$root_pane" ]; then
        "$herdr" workspace close "$workspace" >/dev/null 2>&1 || true
        rm -f "$workspace_file"
    fi
    die "could not open the lantern chat in $workspace"
fi

pane=$(printf '%s' "$opened" | helper_json_value pane_id)
tab=$(printf '%s' "$opened" | helper_json_value tab_id)
seated=$(printf '%s' "$opened" | helper_json_value workspace_id)

# Herdr honours --workspace. If that ever changes, follow the chat and
# drop the workspace this run created, rather than closing a pane in it.
if [ -n "$seated" ] && [ "$seated" != "$workspace" ]; then
    if [ -n "$root_pane" ]; then
        "$herdr" workspace close "$workspace" >/dev/null 2>&1 || true
        root_pane=
    fi
    workspace=$seated
fi

printf '%s\n' "$workspace" >"$workspace_file" || true
if [ -n "$pane" ]; then
    printf '%s\n' "$pane" >"$pane_file" || true
fi

# The tab also names what the chat runs — "home · claude · opus" — so the
# sidebar answers "which CLI and model is this" without opening the tab.
# Computed here, at seat time, when it can be honest: the conf is read the
# way launch.sh reads it, a missing conf (the very first open runs before
# launch.sh has seeded one) falls back to the same PATH detection
# launch.sh will use, an agent launch.sh would refuse is never labelled,
# and a --model in the extra args wins the way it wins on the argv. A conf
# the parser refuses leaves the tab plain home rather than guessing. This
# sits after herdr was resolved, so extending PATH for detection cannot
# change which herdr this open drives. A focused, already-running chat
# keeps the label it was seated with.
if [ -n "${HERDR_PLUGIN_CONFIG_DIR:-}" ]; then
    HELPER_AGENT=
    HELPER_MODEL=
    HELPER_EFFORT=
    HELPER_CWD=
    HELPER_SPAWN_KIND=
    HELPER_PERMISSION=
    HELPER_EXTRA_ARGS=
    conf_ok=1
    if [ -f "$HERDR_PLUGIN_CONFIG_DIR/helper.conf" ]; then
        helper_parse_conf "$HERDR_PLUGIN_CONFIG_DIR/helper.conf" 2>/dev/null ||
            conf_ok=
    fi
    if [ -n "$conf_ok" ] && [ -z "$HELPER_AGENT" ]; then
        helper_extend_user_path
        HELPER_AGENT=$(helper_detect_agent) || HELPER_AGENT=
    fi
    if [ -n "$conf_ok" ]; then
        case $HELPER_AGENT in
        codex | claude | grok | devin | agent | cursor)
            tab_label="home · $(helper_chat_identity "$HELPER_AGENT" \
                "$(helper_effective_model "$HELPER_MODEL" "$HELPER_EXTRA_ARGS")" \
                "$HELPER_EFFORT")"
            ;;
        esac
    fi
fi

if [ -n "$tab" ]; then
    "$herdr" tab rename "$tab" "$tab_label" >/dev/null 2>&1 || true
fi

# 7. A fresh workspace opens with an empty shell tab. The chat replaces it.
if [ -n "$root_pane" ]; then
    "$herdr" pane close "$root_pane" >/dev/null 2>&1 || true
fi

"$herdr" workspace focus "$workspace" >/dev/null 2>&1 || true
printf '%s\n' "$opened"
