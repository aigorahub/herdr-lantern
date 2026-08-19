#!/bin/sh
# Pane entrypoint: build the agent command line from the user's config and
# exec it in the lantern pane. open.sh seats that pane as the "field" tab
# in the workspace labelled "🪔 lantern". Herdr injects HERDR_PLUGIN_CONFIG_DIR,
# HERDR_PLUGIN_ROOT, HERDR_BIN_PATH, and the socket env, so the agent that
# starts here can drive Herdr directly.
set -u

die() {
    printf '\nlantern, by elves: %s\n' "$1" >&2
    printf 'Fix the config, then reopen the lantern. Press Enter to close.\n' >&2
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
        die "set HELPER_AGENT in $conf (agent, devin, claude, codex, or grok)"
    printf 'lantern: HELPER_AGENT empty; using %s from PATH\n' "$HELPER_AGENT" >&2
fi

case $HELPER_AGENT in
codex | claude | grok | devin | agent | cursor) ;;
*) die "unsupported HELPER_AGENT '$HELPER_AGENT' in $conf (use agent, devin, claude, codex, or grok)" ;;
esac

helper_bin=$HELPER_AGENT
if [ "$HELPER_AGENT" = "cursor" ]; then
    helper_bin=agent
fi

command -v "$helper_bin" >/dev/null 2>&1 ||
    die "agent program not found on PATH: $helper_bin"

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

search_root=$(helper_normalize_root "${HELPER_CWD:-~}")
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
- \`herdr agent prompt\` through the wrapper adds \`--wait\` and retries
  with Enter when the target pane stalls (Cursor often types into the
  follow-up field without submitting). Read the pane before saying sent.
- Default --kind for agent start is $HELPER_SPAWN_KIND unless the user names one.
- After workspace create, if agent start fails, wait two seconds and retry once
  (the new pane may still be coming up to a shell prompt).
- Search from $search_root plus the usual project roots. You are the lantern,
  not an elf; do not edit files in this workdir.
- You are the "field" tab in the dedicated lantern workspace (labelled
  "🪔 lantern" unless the user renamed it). Seat new agents in their own
  repository workspace, never in this one. Never close this workspace,
  this tab, or your own pane.
- Close this chat by exiting the helper CLI (not Escape). Cursor agent:
  Ctrl+C, or Ctrl+D on an empty prompt. The tab closes with the CLI, and
  the lantern workspace closes with it when nothing else is in there.
EOF
)
full_prompt=$prompt$appendix

real_herdr=${HERDR_REAL:-}
if [ -z "$real_herdr" ] || [ ! -x "$real_herdr" ]; then
    real_herdr=$(helper_resolve_real_herdr "$plugin_root/bin") || real_herdr=
fi
if [ -n "$real_herdr" ]; then
    "$real_herdr" agent list >"$workdir/floor.txt" 2>/dev/null || true
else
    : >"$workdir/floor.txt"
fi
if command -v python3 >/dev/null 2>&1; then
    if [ -n "$real_herdr" ]; then
        python3 "$plugin_root/bin/goals-floor" --herdr "$real_herdr" \
            >"$workdir/goals-floor.txt" 2>/dev/null || true
    fi
    if [ "$search_root" = "$HOME" ]; then
        python3 "$plugin_root/bin/elves-floor" >"$workdir/elves-floor.txt" 2>/dev/null || true
    else
        python3 "$plugin_root/bin/elves-floor" --root "$search_root" \
            >"$workdir/elves-floor.txt" 2>/dev/null || true
    fi
fi

printf '%s\n' "$full_prompt" >"$workdir/AGENTS.md" ||
    die "could not write AGENTS.md"
printf '%s\n' "$full_prompt" >"$workdir/CLAUDE.md" ||
    die "could not write CLAUDE.md"
printf '%s\n' "$full_prompt" >"$workdir/.windsurf/rules/lantern.md" ||
    die "could not write lantern rule file"
mkdir -p "$workdir/.cursor/rules" || die "could not create cursor rules dir"
{
    printf '%s\n' "---" "alwaysApply: true" "description: Lantern" "---" ""
    printf '%s\n' "$full_prompt"
} >"$workdir/.cursor/rules/lantern.mdc" ||
    die "could not write cursor rule file"

if [ "$helper_bin" = "agent" ] && [ -z "$HELPER_MODEL" ]; then
    HELPER_MODEL=cursor-grok-4.6-high-fast
fi

set -- "$helper_bin"
if [ -n "$HELPER_MODEL" ]; then
    if [ "$HELPER_AGENT" = "devin" ]; then
        printf 'lantern: ignoring HELPER_MODEL for devin (use devin config)\n' >&2
    else
        set -- "$@" --model "$HELPER_MODEL"
    fi
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
if [ "$helper_bin" = "agent" ]; then
    set -- "$@" --trust --sandbox disabled
    case $HELPER_PERMISSION in
    smart | accept-edits) set -- "$@" --auto-review ;;
    dangerous) set -- "$@" --force ;;
    esac
fi
if [ -n "$HELPER_EXTRA_ARGS" ]; then
    set -f
    for _helper_extra in $HELPER_EXTRA_ARGS; do
        set -- "$@" "$_helper_extra"
    done
    set +f
fi
# Invisible first turn so the CLI starts work without painting instructions.
set -- "$@" -- "$(printf '\342\200\213')"

cd "$workdir" || die "could not change to $workdir"
# The wrapper finds the real binary itself. Do not leak HERDR_REAL to the agent.
unset HERDR_REAL
exec "$@"
