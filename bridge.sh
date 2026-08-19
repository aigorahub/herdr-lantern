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

config_dir=${HERDR_PLUGIN_CONFIG_DIR:-}
if [ -z "$config_dir" ]; then
    # Ask before the plugin's own bin goes on PATH, so this reaches the real
    # herdr and not the wrapper.
    config_dir=$(herdr plugin config-dir aigora.lantern 2>/dev/null) || config_dir=
fi
[ -n "$config_dir" ] ||
    die "could not find the plugin config dir; run this through Herdr, or install the plugin"
mkdir -p "$config_dir" || die "could not create $config_dir"

helper_extend_user_path
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
    cp "$plugin_root/bridge.conf.example" "$conf" ||
        die "could not seed config at $conf"
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
