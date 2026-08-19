#!/bin/sh
# Action entrypoint: light the lantern in its own workspace.
#
# First open: create a workspace labelled "🏮 lantern" with --cwd $HOME and
# seat the lantern chat there as a tab named "field", then drop the empty
# shell tab the new workspace came with. Later opens: focus that same
# workspace and the chat already in it. Herdr appends a new workspace at the
# end of the sidebar; there is no pin-to-top, so drag it where you want it.
#
# Bind a key to aigora.lantern.open to reach this from anywhere.
set -eu

plugin_root=${HERDR_PLUGIN_ROOT:-$(CDPATH= cd -- "$(dirname "$0")" && pwd)}
# shellcheck disable=SC1091
. "$plugin_root/lib.sh"

# Sidebar naming. The workspace carries the lantern; the tab is the chat.
# pane_title must match [[panes]].title in herdr-plugin.toml, because
# open.sh uses it to recognise a live lantern chat. legacy_label keeps
# workspaces labelled by hand, or by an earlier version, in use.
workspace_label='🏮 lantern'
legacy_label=lantern
tab_label=field
pane_title=Lantern

die() {
    printf 'lantern: %s\n' "$1" >&2
    exit 1
}

herdr=$(helper_resolve_real_herdr "$plugin_root/bin") ||
    die "could not find the herdr binary"

state_dir=${HERDR_PLUGIN_STATE_DIR:-${HERDR_PLUGIN_CONFIG_DIR:-$plugin_root}/state}
mkdir -p "$state_dir" || die "could not create $state_dir"
workspace_file=$state_dir/workspace.id
pane_file=$state_dir/pane.id

# 1. Find the lantern workspace: remembered id first, then the label.
workspace=
if [ -f "$workspace_file" ]; then
    workspace=$(cat "$workspace_file") || workspace=
    helper_workspace_exists "$herdr" "$workspace" || workspace=
fi
if [ -z "$workspace" ]; then
    workspace=$(helper_workspace_id_by_label "$herdr" "$workspace_label") ||
        workspace=
fi
if [ -z "$workspace" ]; then
    workspace=$(helper_workspace_id_by_label "$herdr" "$legacy_label") ||
        workspace=
fi

# 2. No lantern workspace yet: make one at home.
root_pane=
if [ -z "$workspace" ]; then
    created=$("$herdr" workspace create --cwd "$HOME" \
        --label "$workspace_label" --no-focus) ||
        die "could not create the $workspace_label workspace"
    workspace=$(printf '%s' "$created" | helper_json_value workspace_id)
    [ -n "$workspace" ] || die "could not read the new workspace id"
    root_pane=$(printf '%s' "$created" | helper_json_value pane_id)
fi
printf '%s\n' "$workspace" >"$workspace_file" || true

# 3. A live lantern chat in that workspace is the one to focus.
pane=
if [ -f "$pane_file" ]; then
    pane=$(cat "$pane_file") || pane=
    helper_pane_is_lantern "$herdr" "$pane" "$workspace" "$pane_title" ||
        pane=
fi
if [ -n "$pane" ]; then
    "$herdr" workspace focus "$workspace" >/dev/null 2>&1 || true
    exec "$herdr" plugin pane focus "$pane"
fi

# 4. Seat a new lantern chat as a tab in that workspace.
opened=$("$herdr" plugin pane open \
    --plugin aigora.lantern \
    --entrypoint helper \
    --placement tab \
    --workspace "$workspace" \
    --focus) || die "could not open the lantern chat in $workspace"

pane=$(printf '%s' "$opened" | helper_json_value pane_id)
tab=$(printf '%s' "$opened" | helper_json_value tab_id)
if [ -n "$pane" ]; then
    printf '%s\n' "$pane" >"$pane_file" || true
fi
if [ -n "$tab" ]; then
    "$herdr" tab rename "$tab" "$tab_label" >/dev/null 2>&1 || true
fi

# 5. A fresh workspace opens with an empty shell tab. The chat replaces it.
if [ -n "$root_pane" ]; then
    "$herdr" pane close "$root_pane" >/dev/null 2>&1 || true
fi

"$herdr" workspace focus "$workspace" >/dev/null 2>&1 || true
printf '%s\n' "$opened"
