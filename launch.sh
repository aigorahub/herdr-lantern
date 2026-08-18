#!/bin/sh
# Pane entrypoint: build the agent command line from the user's config and
# exec it inside the Herdr popup. Herdr injects HERDR_PLUGIN_CONFIG_DIR,
# HERDR_PLUGIN_ROOT, HERDR_BIN_PATH, and the socket env, so the agent that
# starts here can drive Herdr directly.
set -u

die() {
    printf '\nsession-helper: %s\n' "$1" >&2
    printf 'Fix the config, then reopen the helper. Press Enter to close.\n' >&2
    # Keep the popup open so the message is readable instead of flashing away.
    read -r _ignored
    exit 1
}

plugin_root=${HERDR_PLUGIN_ROOT:-$(dirname "$0")}
config_dir=${HERDR_PLUGIN_CONFIG_DIR:-}
[ -n "$config_dir" ] || die "HERDR_PLUGIN_CONFIG_DIR is not set; run this through Herdr"

conf="$config_dir/helper.conf"
if [ ! -f "$conf" ]; then
    cp "$plugin_root/helper.conf.example" "$conf" ||
        die "could not seed config at $conf"
fi

prompt_file="$config_dir/prompt.md"
if [ ! -f "$prompt_file" ]; then
    cp "$plugin_root/prompt.md" "$prompt_file" ||
        die "could not seed prompt at $prompt_file"
fi

HELPER_AGENT=""
HELPER_MODEL=""
HELPER_EFFORT=""
HELPER_CWD=""
# shellcheck disable=SC1090
. "$conf"

[ -n "$HELPER_AGENT" ] || die "set HELPER_AGENT in $conf (codex, claude, grok, or devin)"

case "$HELPER_AGENT" in
codex | claude | grok | devin) ;;
*) die "unsupported HELPER_AGENT '$HELPER_AGENT' in $conf (use codex, claude, grok, or devin)" ;;
esac

command -v "$HELPER_AGENT" >/dev/null 2>&1 ||
    die "agent program not found on PATH: $HELPER_AGENT"

cwd=${HELPER_CWD:-$HOME}
case "$cwd" in
"~") cwd=$HOME ;;
"~/"*) cwd="$HOME/${cwd#\~/}" ;;
esac
[ -d "$cwd" ] || die "helper working directory does not exist: $cwd"

prompt=$(cat "$prompt_file")

set -- "$HELPER_AGENT"
if [ -n "$HELPER_MODEL" ]; then
    set -- "$@" --model "$HELPER_MODEL"
fi
if [ -n "$HELPER_EFFORT" ]; then
    case "$HELPER_AGENT" in
    codex) set -- "$@" --config "model_reasoning_effort=\"$HELPER_EFFORT\"" ;;
    claude) set -- "$@" --effort "$HELPER_EFFORT" ;;
    grok) set -- "$@" --reasoning-effort "$HELPER_EFFORT" ;;
    esac
fi
if [ "$HELPER_AGENT" = "grok" ]; then
    set -- "$@" --no-subagents
fi
if [ "$HELPER_AGENT" = "devin" ]; then
    # create/focus/start must run without a TUI approval prompt
    set -- "$@" --permission-mode dangerous
fi
if [ -n "$prompt" ]; then
    if [ "$HELPER_AGENT" = "devin" ]; then
        set -- "$@" -- "$prompt"
    else
        set -- "$@" "$prompt"
    fi
fi

cd "$cwd" || die "could not change to $cwd"
exec "$@"
