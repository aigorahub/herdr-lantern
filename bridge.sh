#!/bin/sh
# Pane entrypoint for the Lantern Bridge. It builds the same environment the
# lantern pane gets -- extended PATH, the inspect-by-default herdr wrapper as
# HERDR_BIN_PATH, a seeded config and prompt -- and then hands off to the
# Python daemon in bin/lantern-bridge.
#
# It has to run two ways. As a Herdr pane, Herdr injects the plugin env. From a
# terminal, nothing is injected, so the config dir is asked of herdr itself.
set -u

die() {
    printf '\nlantern bridge: %s\n' "$1" >&2
    # A pane closes with the process, so an error would flash past. Hold only
    # when a person is actually looking; tests and daemons run without a tty.
    if [ -t 0 ]; then
        printf 'Fix the config, then reopen the bridge. Press Enter to close.\n' >&2
        read -r _ignored
    fi
    exit 1
}

plugin_root=${HERDR_PLUGIN_ROOT:-$(CDPATH= cd -- "$(dirname "$0")" && pwd)}
# shellcheck disable=SC1091
. "$plugin_root/lib.sh"
# Herdr on Windows reports this as \\?\C:\path, which breaks as soon as a child
# path is appended. Same normalisation launch.sh does, same reason.
plugin_root=$(helper_posix_path "$plugin_root")

# Before anything looks for herdr or a helper CLI: an action started from the
# Herdr UI inherits a thin PATH.
helper_extend_user_path

# Must match [[panes]].title for the bridge pane in herdr-plugin.toml: --open
# uses it to recognise a bridge that is already running.
bridge_pane_title='Lantern Bridge'

# The `bridge` action seats the `bridge` pane. An action runs in a process of
# its own, so it cannot become the daemon; it asks Herdr to open the pane, and
# the pane runs this file again without --open. The real herdr is called
# directly here, the way open.sh does: the wrapper's gate is for the helper
# CLI, not for the plugin's own entrypoints.
if [ "${1:-}" = "--open" ]; then
    real_herdr=${HERDR_BIN_PATH:-}
    case $real_herdr in
    '' | "$plugin_root"/bin/herdr)
        real_herdr=$(helper_resolve_real_herdr "$plugin_root/bin") ||
            die "could not find the real herdr binary"
        ;;
    esac

    # Herdr does not deduplicate plugin panes, and a second bridge is not a
    # cosmetic duplicate: two long polls on one bot token fight over the same
    # cursor, two Slack pollers answer every message twice, and the second
    # webhook cannot bind its port. The daemon holds a lock of its own; this is
    # the cheap half, so a second press focuses instead of racing for it.
    open_state_dir=${HERDR_PLUGIN_STATE_DIR:-${HERDR_PLUGIN_CONFIG_DIR:-$plugin_root}/state}
    bridge_pane_file=$open_state_dir/bridge/pane.id
    if [ -f "$bridge_pane_file" ]; then
        bridge_pane=$(cat "$bridge_pane_file") || bridge_pane=
        if [ -n "$bridge_pane" ]; then
            # The title check is what makes a remembered id safe: Herdr reuses
            # pane ids after a restart, and focusing a stale one would pull up
            # somebody else's pane.
            bridge_ws=$(helper_lantern_pane_workspace \
                "$real_herdr" "$bridge_pane" "$bridge_pane_title") || bridge_ws=
            if [ -n "$bridge_ws" ]; then
                "$real_herdr" workspace focus "$bridge_ws" >/dev/null 2>&1 || true
                if "$real_herdr" plugin pane focus "$bridge_pane"; then
                    exit 0
                fi
                # It quit between the check and the focus. Seat a new one.
            fi
        fi
    fi

    opened=$("$real_herdr" plugin pane open \
        --plugin aigora.lantern --entrypoint bridge --placement tab) ||
        die "could not open the bridge pane"
    bridge_pane=$(printf '%s' "$opened" | helper_json_value pane_id)
    if [ -n "$bridge_pane" ] && mkdir -p "$open_state_dir/bridge" 2>/dev/null; then
        printf '%s\n' "$bridge_pane" >"$bridge_pane_file" || true
    fi
    printf '%s\n' "$opened"
    exit 0
fi

config_dir=${HERDR_PLUGIN_CONFIG_DIR:-}
if [ -z "$config_dir" ]; then
    # Ask before the plugin's own bin goes on PATH, so this reaches the real
    # herdr and not the wrapper.
    config_dir=$(herdr plugin config-dir aigora.lantern 2>/dev/null) || config_dir=
fi
[ -n "$config_dir" ] ||
    die "could not find the plugin config dir; run this through Herdr, or install the plugin"
mkdir -p "$config_dir" || die "could not create $config_dir"

helper_prepend_path "$plugin_root/bin"

if [ -n "${HERDR_BIN_PATH:-}" ] && [ -x "$HERDR_BIN_PATH" ]; then
    case $HERDR_BIN_PATH in
    "$plugin_root"/bin/herdr) ;;
    *)
        HERDR_REAL=$HERDR_BIN_PATH
        export HERDR_REAL
        ;;
    esac
fi
export HERDR_BIN_PATH="$plugin_root/bin/herdr"
export HERDR_PLUGIN_ROOT="$plugin_root"

conf="$config_dir/bridge.conf"
if [ ! -f "$conf" ]; then
    # This file is the documented home of five secrets. A plain cp lands it
    # 0644 -- readable by every other account on the machine -- so create it
    # under a tight umask and pin the mode afterwards, in case a cp on some
    # platform copies the example's bits instead. Only the file just created
    # is touched; an existing one keeps whatever mode its owner chose.
    (umask 077 && cp "$plugin_root/bridge.conf.example" "$conf") ||
        die "could not seed config at $conf"
    chmod 600 "$conf" || die "could not restrict permissions on $conf"
fi

# The bridge answers with the same lantern prompt the pane uses, so it reads
# the same copied file. launch.sh seeds it too; whichever runs first wins and
# neither overwrites.
prompt_file="$config_dir/prompt.md"
if [ ! -f "$prompt_file" ]; then
    cp "$plugin_root/prompt.md" "$prompt_file" ||
        die "could not seed prompt at $prompt_file"
fi

state_dir=${HERDR_PLUGIN_STATE_DIR:-$config_dir/state}
mkdir -p "$state_dir/bridge" || die "could not create $state_dir/bridge"

bridge_python=$(helper_detect_python) ||
    die "no working Python 3 on PATH; the bridge needs one"

# The wrapper finds the real binary itself. Do not leak HERDR_REAL to the
# helper the daemon starts: that is the gate-free path.
unset HERDR_REAL
# $bridge_python is split on purpose: the Windows launcher is `py -3`.
# shellcheck disable=SC2086
exec $bridge_python "$plugin_root/bin/lantern-bridge" \
    --conf "$conf" \
    --example "$plugin_root/bridge.conf.example" \
    --prompt "$prompt_file" \
    --state "$state_dir" \
    "$@"
