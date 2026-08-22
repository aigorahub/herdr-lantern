#!/bin/sh
# Pane entrypoint: build the agent command line from the user's config and
# exec it in the lantern pane. open.sh seats that pane as the "home" tab
# in the workspace labelled "🔥 lantern". Herdr injects HERDR_PLUGIN_CONFIG_DIR,
# HERDR_PLUGIN_ROOT, HERDR_BIN_PATH, and the socket env, so the agent that
# starts here can drive Herdr directly.
set -u

die() {
    printf '\nlantern, by elves: %s\n' "$1" >&2
    printf 'Fix the config, then reopen the lantern. Press Enter to close.\n' >&2
    read -r _ignored
    exit 1
}

snapshot_unavailable() {
    # $1 snapshot file, $2 why. The workdir survives between runs and
    # prompt.md has the lantern read these files at light-up, so a snapshot
    # left unrefreshed is last run's field reported as this one: who needs
    # you, who is blocked, all of it hours old and stated as fact. An honest
    # line is the only thing worth leaving here.
    printf 'snapshot unavailable: %s\n' "$2" >"$1" 2>/dev/null || true
}

plugin_root=${HERDR_PLUGIN_ROOT:-$(CDPATH= cd -- "$(dirname "$0")" && pwd)}
# shellcheck disable=SC1091
. "$plugin_root/lib.sh"
# Herdr on Windows reports this as \\?\C:\path. The shell copes; the Python
# snapshot scripts do not, once a child path is appended. Normalise it before
# anything builds a path from it.
plugin_root=$(helper_posix_path "$plugin_root")

config_dir=${HERDR_PLUGIN_CONFIG_DIR:-}
[ -n "$config_dir" ] || die "HERDR_PLUGIN_CONFIG_DIR is not set; run this through Herdr"

helper_extend_user_path
# After the user directories, and forced to the front: the wrapper is the
# mutate gate, and a plugin bin that arrived on the inherited PATH would
# otherwise keep its old position behind a real herdr.
helper_force_front_path "$plugin_root/bin"

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

# The identity follows what actually runs: a --model in HELPER_EXTRA_ARGS
# lands after the built flags and the later flag wins, so it is the model
# the label and the light-up line must name.
chat_identity=$(helper_chat_identity "$HELPER_AGENT" \
    "$(helper_effective_model "$HELPER_MODEL" "$HELPER_EXTRA_ARGS")" \
    "$HELPER_EFFORT")

state_dir=${HERDR_PLUGIN_STATE_DIR:-$config_dir/state}
workdir=$state_dir/workdir
mkdir -p "$workdir/.windsurf/rules" || die "could not create helper workdir"

search_root=$(helper_normalize_root "${HELPER_CWD:-~}")
[ -d "$search_root" ] || die "helper search directory does not exist: $search_root"

prompt=$(cat "$prompt_file")
# macOS /bin/sh is bash 3.2, and its $(...) scanner pairs ASCII quote
# characters even inside this heredoc, so an odd number of ' below breaks
# the parse at the end of the file. Prose apostrophes in the appendix are
# the typographic ’ for that reason.
appendix=$(
    cat <<EOF

Runtime (injected by launch.sh; do not ignore):

- Prefer \$HERDR_BIN_PATH when calling Herdr. A wrapper is first on PATH.
  Read-only commands (--help, status, agent list/read/get/wait/explain,
  workspace list/get, tab list/get,
  pane list/current/get/layout/process-info/neighbor/edges/read,
  worktree list, session list, plugin list/log/logs/config-dir, and
  integration status) run
  as usual. Everything else changes the herd — create, start, focus,
  close, remove, prompt, send-keys, send-text, kill — and is blocked
  until the user confirms the exact path or target in this chat. Only
  then rerun that same command with HERDR_HELPER_OK=1 in front of it.
  Never call /opt/homebrew/bin/herdr or another absolute herdr path.
- \`herdr agent prompt\` through the wrapper adds \`--wait\`. It presses
  Enter only when the target pane stalls with the text still showing
  (Cursor often types into the follow-up field without submitting), and
  it fails when nothing shows the message went in. Read the pane before
  saying sent.
- \`herdr agent start\` through the wrapper, for Codex only, dismisses the
  first-run directory trust dialog with Enter or a new-chat \`[y/n]\` /
  \`yes (y)\` confirm with y when Herdr returns \`agent_not_ready\` /
  blocked during startup on that named pane. If both appear, it dismisses
  them in order. It waits until idle or done and
  \`interactive_ready\`. It does not send keys into any other failure,
  another agent’s pane, or later permission prompts.
- This chat runs $chat_identity. Name that in your light-up line — it is
  how the user tells which CLI and model is answering — and repeat it
  whenever they ask.
- Default --kind for agent start is $HELPER_SPAWN_KIND unless the user names one.
- Seat agents in the smart-auto permission tier. Claude defaults to
  \`--model opus --effort high --permission-mode auto\`. Cursor uses the live
  \`model-route cursor default\` result with \`--auto-review --trust\`. It
  prefers \`gpt-5.6-sol-high-fast\` and excludes Grok and Composer from its
  default fallback. Grok Build uses the live \`model-route grok default\`
  result with \`--permission-mode auto\`. It prefers \`grok-4.6\` at high
  effort, then \`grok-4.5\` at high effort. Codex interactive and review
  default to Sol 5.6, high, fast, after the live catalog confirms
  \`-m gpt-5.6-sol -c model_reasoning_effort=\"high\" -c
  service_tier=\"priority\"\`, plus \`-a never -s danger-full-access\`. Use
  \`\$HERDR_PLUGIN_ROOT/bin/model-route codex "5.6 sol high fast"\` to get
  separate Codex argv. Use the same resolver with cursor or grok for a user
  supplied model phrase. Never invent a model slug. A kind not named here
  gets no extra args. Never pass bypassPermissions, --yolo, --force,
  --always-approve, or --dangerously-bypass-approvals-and-sandbox unless the
  user explicitly asks for yolo in that request. After a confirmed seat, say
  in one line what is running where: the slug, the kind, the live chosen
  model, effort, fast state, and the task, or that it sits at a shell with no
  task yet. Rename the agent’s tab to
  \`<slug> · <kind>\` as part of the seat plan you confirm
  (\`herdr tab rename\`, tab_id from the workspace create JSON), so the
  sidebar says who is in it.
- A yolo request does not select every bypass. Name the one
  provider-specific flag and the protections it removes in the gated seat
  plan. Run it only after the user confirms that exact plan.
- Route loose phrases. "What’s going on" means field status and agent,
  workspace, and tab lists. "Open the tab" means an exact agent, workspace,
  or tab focus. Offer it with, "Would you like me to open the tab?" "Tell
  them X" means an exact gated agent prompt. "Open battle
  paddle with Codex" means resolve the repo, reuse its workspace, and seat
  Codex. "Second tab same way" means create a tab in that same workspace and
  reuse the last verified kind and model settings. Resume uses the real kind
  CLI: Codex \`resume --last\`, Claude \`--continue\`, OMP \`--continue\` or
  \`-r <id>\`, Cursor \`--continue\` or \`--resume <id>\`, Grok
  \`--continue\` or \`--resume <id>\`, Gemini \`--resume latest\`, OpenCode
  \`--continue\`, and Devin \`--continue\`.
- "Cursor" means \`--kind cursor\` with the live Sol default. "Grok" also
  means \`--kind cursor\`, but with a live Cursor Grok model. "Grok Build" and
  "SuperGrok" mean \`--kind grok\` with the live Grok Build default. "In
  Cursor with Grok" uses the same route as bare Grok.
- Route "open a review" and "review this" to the real Codex \`review\`
  command. For "review PR N on repo X", resolve the repo, inspect the pull
  request with \`gh -R <owner/repo> pr view\`, verify the local head matches
  the pull request head, and use \`review --base <base>\`. If it does not
  match, offer a separate gated Herdr worktree at the exact head OID. Do not
  check out the pull request in the product repo. Reuse a Codex agent on the
  matching head with \`herdr agent prompt\`. Otherwise use a gated \`herdr
  agent start <slug> --kind codex\` with the real Codex \`review\` argv. Use
  the Codex default without asking for a model when the user did not name one.
- Route "Cursor review on X" through the same repo and pull request checks.
  Reuse a matching Cursor agent with \`herdr agent prompt\`. Otherwise start
  \`--kind cursor\` with \`--auto-review --trust --mode plan\` and the live
  Cursor default. Cursor has no review subcommand on this machine.
- Route "Grok review on X" through the Cursor review route with a live Cursor
  Grok model. Route "Grok Build review on X" through the same repo and pull
  request checks. Reuse a matching Grok Build agent with \`herdr agent
  prompt\`. Otherwise start \`--kind grok\` with \`--permission-mode auto
  -p\` and the live Grok Build default. Grok Build has no review subcommand.
- After model resolution, run
  \`\$HERDR_PLUGIN_ROOT/bin/model-preflight <kind> <model> [effort]\`. Run it
  before asking the user to confirm a seat. Do not create, start, or prompt
  when it fails. A missing command, timeout, or unparseable check stops the
  route. A usage line with no reset time is still valid. A harness with no
  quota command is not a failed check. If the model is unavailable, report
  its bucket and reset time when known. Name the one live substitute from
  the result and ask the user to confirm it. Never switch models in silence.
- Model routing requires a working Python 3 command. The wrappers try
  \`python3\`, \`python\`, and Windows \`py -3\`. A missing interpreter stops
  the route before any herd change.
- Codex task phrases map to real commands: continue last is \`resume --last\`,
  fork last is \`fork --last\`, apply is \`apply <TASK_ID>\`, diagnostics are
  \`doctor --summary\` and \`login status\`. Lantern never applies a diff
  itself. Interactive chat remains the normal seat.
- Close a workspace, tab, pane, or worktree only when the user names it.
  Split, zoom, or swap panes only when asked. Plugin and integration installs
  are gated. Never merge, run land-pr, edit product repositories, or close the
  Lantern home tab, pane, or workspace.
- update.txt in this workdir is this light-up’s version check. If it says a
  newer version is published, offer the update once, in one line; if it says
  up to date or unavailable, say nothing about it. There is no
  \`herdr plugin update\`: a GitHub install refreshes with
  \`herdr plugin install aigorahub/herdr-lantern\`, which mutates the herd
  and is gated like everything else — ask first, never upgrade silently. A
  linked checkout is never reinstalled over: say it is behind and leave the
  pull to the user. After a confirmed install, tell the user to quit this
  chat and reopen the lantern.
- After workspace create, if agent start fails, wait two seconds and retry once
  (the new pane may still be coming up to a shell prompt).
- Search from $search_root plus the usual project roots. You are the lantern,
  not an elf; do not edit files in this workdir.
- You are the "home" tab in the dedicated lantern workspace (labelled
  "🔥 lantern" unless the user renamed it). Seat new agents in their own
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
    snapshot_unavailable "$workdir/floor.txt" \
        "the real herdr binary was not found from this pane"
fi
helper_python=$(helper_detect_python) || helper_python=
if [ -n "$helper_python" ]; then
    # $helper_python is split on purpose: the Windows launcher is `py -3`.
    if [ -n "$real_herdr" ]; then
        # shellcheck disable=SC2086
        $helper_python "$plugin_root/bin/goals-floor" --herdr "$real_herdr" \
            >"$workdir/goals-floor.txt" 2>/dev/null || true
    else
        snapshot_unavailable "$workdir/goals-floor.txt" \
            "the real herdr binary was not found from this pane"
    fi
    if [ "$search_root" = "$HOME" ]; then
        # shellcheck disable=SC2086
        $helper_python "$plugin_root/bin/elves-floor" \
            >"$workdir/elves-floor.txt" 2>/dev/null || true
    else
        # shellcheck disable=SC2086
        $helper_python "$plugin_root/bin/elves-floor" --root "$search_root" \
            >"$workdir/elves-floor.txt" 2>/dev/null || true
    fi
else
    snapshot_unavailable "$workdir/goals-floor.txt" \
        "no working python 3 interpreter was found on PATH"
    snapshot_unavailable "$workdir/elves-floor.txt" \
        "no working python 3 interpreter was found on PATH"
fi
# The fetch gets a few seconds, and none of them are the user's: an honest
# placeholder goes down synchronously, then the check rewrites the file
# from the background while the agent starts. A light-up that reads before
# the rewrite sees the unavailable line and says nothing about updates,
# which is exactly what a slow network looks like anyway.
printf 'update check unavailable: the check had not finished by light-up\n' \
    >"$workdir/update.txt" 2>/dev/null || true
helper_update_snapshot "$plugin_root" "$workdir/update.txt" &

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
    HELPER_MODEL=$(helper_cursor_default_model)
fi

set -- "$helper_bin"
if [ -n "$HELPER_MODEL" ]; then
    if [ "$HELPER_AGENT" = "devin" ]; then
        printf 'lantern: ignoring HELPER_MODEL for devin (use devin config)\n' >&2
    else
        set -- "$@" --model "$HELPER_MODEL"
    fi
fi
if [ -n "$HELPER_EFFORT" ] && helper_agent_takes_effort "$HELPER_AGENT"; then
    # Membership lives in helper_agent_takes_effort, shared with the chat
    # identity; this case only maps each CLI to its own flag spelling.
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
