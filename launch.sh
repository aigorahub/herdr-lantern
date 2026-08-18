#!/bin/sh
# Pane entrypoint: build the agent command line from the user's config and
# exec it inside the Herdr popup. Herdr injects HERDR_PLUGIN_CONFIG_DIR,
# HERDR_PLUGIN_ROOT, HERDR_BIN_PATH, and the socket env, so the agent that
# starts here can drive Herdr directly.
set -u

die() {
    printf '\nsession-helper: %s\n' "$1" >&2
    printf 'Fix the config, then reopen the helper. Press Enter to close.\n' >&2
    read -r _ignored
    exit 1
}

plugin_root=${HERDR_PLUGIN_ROOT:-$(CDPATH= cd -- "$(dirname "$0")" && pwd)}
# shellcheck disable=SC1091
. "$plugin_root/lib.sh"

config_dir=${HERDR_PLUGIN_CONFIG_DIR:-}
[ -n "$config_dir" ] || die "HERDR_PLUGIN_CONFIG_DIR is not set; run this through Herdr"

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
HELPER_SPAWN_KIND=""
HELPER_PERMISSION=""
HELPER_EXTRA_ARGS=""
helper_parse_conf "$conf" || die "could not parse $conf"

if [ -z "$HELPER_AGENT" ]; then
    HELPER_AGENT=$(helper_detect_agent) ||
        die "set HELPER_AGENT in $conf (devin, claude, codex, or grok)"
    printf 'session-helper: HELPER_AGENT empty; using %s from PATH\n' "$HELPER_AGENT" >&2
fi

case $HELPER_AGENT in
codex | claude | grok | devin) ;;
*) die "unsupported HELPER_AGENT '$HELPER_AGENT' in $conf (use devin, claude, codex, or grok)" ;;
esac

command -v "$HELPER_AGENT" >/dev/null 2>&1 ||
    die "agent program not found on PATH: $HELPER_AGENT"

HELPER_SPAWN_KIND=${HELPER_SPAWN_KIND:-claude}
case $HELPER_SPAWN_KIND in
*[!a-z0-9_-]* | '')
    die "HELPER_SPAWN_KIND must match [a-z][a-z0-9_-]* (got '$HELPER_SPAWN_KIND')"
    ;;
esac

HELPER_PERMISSION=${HELPER_PERMISSION:-smart}
case $HELPER_PERMISSION in
auto | accept-edits | smart | dangerous) ;;
*) die "HELPER_PERMISSION must be auto, accept-edits, smart, or dangerous" ;;
esac

state_dir=${HERDR_PLUGIN_STATE_DIR:-$config_dir/state}
workdir=$state_dir/workdir
mkdir -p "$workdir/.windsurf/rules" || die "could not create helper workdir"

search_root=$(helper_expand_tilde "${HELPER_CWD:-~}")
[ -d "$search_root" ] || die "helper search directory does not exist: $search_root"

prompt=$(cat "$prompt_file")
appendix=$(
    cat <<EOF

Runtime (injected by launch.sh; do not ignore):

- Prefer \$HERDR_BIN_PATH when calling Herdr. A wrapper is first on PATH.
  Inspect commands (list/read/get) run as usual.
  Mutating commands (create, start, focus, close, remove, prompt, send-*)
  are blocked until the user confirms the exact path or target. Then rerun:
    HERDR_HELPER_OK=1 herdr <same command>
  Never call /opt/homebrew/bin/herdr or another absolute herdr path.
- Default --kind for agent start is $HELPER_SPAWN_KIND unless the user names one.
- After workspace create, if agent start fails, wait two seconds and retry once
  (the new pane may still be coming up to a shell prompt).
- Search from $search_root plus the usual project roots. You are not a coding
  agent; do not edit files in this helper workdir.
EOF
)
full_prompt=$prompt$appendix

printf '%s\n' "$full_prompt" >"$workdir/.windsurf/rules/session-helper.md" ||
    die "could not write devin rule file"

set -- "$HELPER_AGENT"
if [ -n "$HELPER_MODEL" ]; then
    set -- "$@" --model "$HELPER_MODEL"
fi
if [ -n "$HELPER_EFFORT" ]; then
    case $HELPER_AGENT in
    codex) set -- "$@" --config "model_reasoning_effort=\"$HELPER_EFFORT\"" ;;
    claude) set -- "$@" --effort "$HELPER_EFFORT" ;;
    grok) set -- "$@" --reasoning-effort "$HELPER_EFFORT" ;;
    esac
fi
if [ "$HELPER_AGENT" = "grok" ]; then
    set -- "$@" --no-subagents
fi
if [ "$HELPER_AGENT" = "devin" ]; then
    set -- "$@" --permission-mode "$HELPER_PERMISSION"
fi
if [ -n "$HELPER_EXTRA_ARGS" ]; then
    for _helper_extra in $HELPER_EXTRA_ARGS; do
        set -- "$@" "$_helper_extra"
    done
fi
if [ "$HELPER_AGENT" = "devin" ]; then
    set -- "$@" -- "Run herdr agent list, greet with a one-line summary of open agents, and ask what to do."
elif [ -n "$full_prompt" ]; then
    set -- "$@" "$full_prompt"
fi

cd "$workdir" || die "could not change to $workdir"
exec "$@"
