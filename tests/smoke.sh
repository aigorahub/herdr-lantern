#!/bin/sh
# Fast checks: syntax, config parse, herdr wrapper gate.
set -eu
root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
cd "$root"

fail() {
    printf 'FAIL: %s\n' "$1" >&2
    exit 1
}

for f in launch.sh open.sh lib.sh bin/herdr bin/model-route bin/model-preflight hsh tests/smoke.sh; do
    sh -n "$f" || fail "sh -n $f"
done

# shellcheck disable=SC1091
. "$root/lib.sh"

tmp=$(mktemp)
err=$(mktemp)
fake=
fake_prompt=
fake_start=
fake_ws=
fake_cmd=
fake_py=
fake_agents=
argv_dir=
open_dir=
elves_tmp=
update_dir=
model_dir=
trap 'rm -f "$tmp" "$err"; [ -n "$fake" ] && rm -rf "$fake"; [ -n "$fake_prompt" ] && rm -rf "$fake_prompt"; [ -n "$fake_start" ] && rm -rf "$fake_start"; [ -n "$fake_ws" ] && rm -rf "$fake_ws"; [ -n "$fake_cmd" ] && rm -rf "$fake_cmd"; [ -n "$fake_py" ] && rm -rf "$fake_py"; [ -n "$fake_agents" ] && rm -rf "$fake_agents"; [ -n "$argv_dir" ] && rm -rf "$argv_dir"; [ -n "$open_dir" ] && rm -rf "$open_dir"; [ -n "$elves_tmp" ] && rm -rf "$elves_tmp"; [ -n "$update_dir" ] && rm -rf "$update_dir"; [ -n "$model_dir" ] && rm -rf "$model_dir"' EXIT

printf '%s\n' 'HELPER_AGENT="devin"' 'HELPER_SPAWN_KIND="claude"' >"$tmp"
HELPER_AGENT=""
HELPER_SPAWN_KIND=""
helper_parse_conf "$tmp" || fail "parse good conf"
[ "$HELPER_AGENT" = devin ] || fail "parsed HELPER_AGENT"
[ "$HELPER_SPAWN_KIND" = claude ] || fail "parsed HELPER_SPAWN_KIND"

printf '%s\n' 'HELPER_AGENT="devin; rm -rf /"' >"$tmp"
if helper_parse_conf "$tmp" 2>/dev/null; then
    fail "accepted unsafe value"
fi

printf '%s\n' 'EVIL=1' >"$tmp"
if helper_parse_conf "$tmp" 2>/dev/null; then
    fail "accepted unknown key"
fi

[ "$(helper_expand_tilde '~')" = "$HOME" ] || fail "expand ~"
[ "$(helper_normalize_root '~/')" = "$HOME" ] || fail "normalize ~/"
[ "$(helper_normalize_root "$HOME/")" = "$HOME" ] || fail "normalize HOME/"

# helper_native_path is what herdr gets. Identity without cygpath, a
# drive-letter path with it.
native_home=$(helper_native_path "$HOME")
if command -v cygpath >/dev/null 2>&1; then
    case $native_home in
    [A-Za-z]:\\*) ;;
    *) fail "helper_native_path should produce a Windows path (got $native_home)" ;;
    esac
else
    [ "$native_home" = "$HOME" ] ||
        fail "helper_native_path should be identity here (got $native_home)"
fi
# helper_posix_path exists because Herdr on Windows reports the plugin root as
# an extended-length path. The shell reads \\?\C:\path, but appending a child
# gives a form Windows rejects, and the Python snapshot then fails silently.
if command -v cygpath >/dev/null 2>&1; then
    posix_root=$(helper_posix_path '\\?\C:\Claude\herdr-lantern')
    case $posix_root in
    /*) ;;
    *) fail "helper_posix_path should return a POSIX path (got $posix_root)" ;;
    esac
    case $posix_root in
    *'\\?\'*) fail "helper_posix_path left the extended-length prefix" ;;
    esac
else
    [ "$(helper_posix_path "$HOME")" = "$HOME" ] ||
        fail "helper_posix_path should be identity here"
fi
grep -q 'plugin_root=$(helper_posix_path "$plugin_root")' "$root/launch.sh" ||
    fail "launch.sh should normalise the plugin root"
grep -q 'plugin_root=$(helper_posix_path "$plugin_root")' "$root/open.sh" ||
    fail "open.sh should normalise the plugin root"

# launch.sh tests the search root with [ -d ], so that one must stay POSIX.
[ "$(helper_normalize_root "$HOME")" = "$HOME" ] ||
    fail "helper_normalize_root must not convert the path form"
grep -q 'helper_native_path "$HOME"' "$root/open.sh" ||
    fail "open.sh should hand herdr the native path form"

# helper_resolve_real_herdr has to cope with the Windows form of
# HERDR_BIN_PATH, which arrives with backslashes and a drive letter.
resolve_dir=$(mktemp -d)
printf '%s\n' '#!/bin/sh' 'exit 0' >"$resolve_dir/herdr"
chmod +x "$resolve_dir/herdr"
if command -v cygpath >/dev/null 2>&1; then
    win_herdr=$(cygpath -w "$resolve_dir/herdr")
    got=$(HERDR_REAL= HERDR_BIN_PATH="$win_herdr" \
        helper_resolve_real_herdr "$root/bin") ||
        fail "resolve should accept a Windows HERDR_BIN_PATH"
    [ -x "$got" ] || fail "resolved herdr is not runnable (got $got)"
fi
# The plugin's own wrapper must never be returned as the real binary.
got=$(HERDR_REAL= HERDR_BIN_PATH="$root/bin/herdr" \
    helper_resolve_real_herdr "$root/bin") || got=
case $got in
"$root/bin/herdr")
    fail "resolve returned the plugin wrapper as the real herdr"
    ;;
esac

# The wrapper has to win `command -v herdr` even when the plugin's bin
# directory is already on the inherited PATH. It used to keep that inherited
# position while helper_extend_user_path put /usr/local/bin and
# /opt/homebrew/bin in front of it, so a bare `herdr` reached the real binary
# and the mutate gate never ran. prompt.md permits a bare `herdr`.
front_got=$(
    PATH="$resolve_dir:$root/bin:/usr/bin:/bin"
    export PATH
    helper_extend_user_path
    helper_force_front_path "$root/bin"
    command -v herdr
) || front_got=
[ "$front_got" = "$root/bin/herdr" ] ||
    fail "the wrapper must resolve first even when its dir was already on PATH (got $front_got)"
# And the real binary must still be reachable, or the wrapper has nothing to
# exec: helper_resolve_real_herdr strips this directory back out, so every
# copy of it has to be gone.
front_real=$(
    PATH="$resolve_dir:$root/bin:/usr/bin:/bin"
    export PATH
    helper_extend_user_path
    helper_force_front_path "$root/bin"
    HERDR_REAL= HERDR_BIN_PATH="$root/bin/herdr" helper_resolve_real_herdr "$root/bin"
) || front_real=
case $front_real in
'' | "$root/bin/herdr")
    fail "resolve should still find a real herdr behind the wrapper (got $front_real)"
    ;;
esac
grep -q 'helper_force_front_path "$plugin_root/bin"' "$root/launch.sh" ||
    fail "launch.sh should force the wrapper to the front of PATH"
grep -q 'helper_relay_agent_start' "$root/bin/herdr" ||
    fail "bin/herdr should relay agent start for the Codex startup gate"
rm -rf "$resolve_dir"

# The plugin must never invoke bare `bash`. On Windows the bash on PATH is the
# WSL launcher in WindowsApps, so that call would start Linux. Herdr runs these
# files with `sh`, and nothing here needs more than that.
if grep -vE '^[[:space:]]*#' "$root/launch.sh" "$root/open.sh" \
    "$root/lib.sh" "$root/bin/herdr" |
    grep -qE '(^|[^A-Za-z_/-])bash([^A-Za-z_]|$)'; then
    fail "plugin shell code must not invoke bare bash"
fi
if grep -q 'WindowsApps' "$root/lib.sh"; then
    fail "lib.sh must not put WindowsApps on PATH"
fi
grep -q 'CLAUDE.md' "$root/launch.sh" || fail "launch writes CLAUDE.md"

# prompt.md is the other half of the gate: bin/herdr blocks the command, and
# this file is what makes the lantern ask first. A ready-to-run prefixed line
# next to an instruction that never mentions confirming is how an agent ends
# up typing into other people's panes with nobody's permission.
if grep -q 'HERDR_HELPER_OK=1 herdr' "$root/prompt.md"; then
    fail "prompt.md should not hand out a prefixed herdr command to paste"
fi
for gated_verb in prompt send-keys start focus close remove; do
    grep -qF "$gated_verb" "$root/prompt.md" ||
        fail "prompt.md does not name $gated_verb as gated"
    grep -qF "$gated_verb" "$root/launch.sh" ||
        fail "the launch.sh appendix does not name $gated_verb as gated"
done

for route_file in prompt.md launch.sh; do
    grep -qF 'Would you like me to open the tab?' "$root/$route_file" ||
        fail "$route_file does not use the open-tab confirmation"
    if grep -qiF 'Would you like me to walk you there?' "$root/$route_file"; then
        fail "$route_file still uses the old walk confirmation"
    fi
done
grep -qF '"walk me there"' "$root/prompt.md" ||
    fail "prompt.md no longer accepts the old input alias"

# Seated agents start without ordinary approval stops. The per-kind flags
# have to appear everywhere the lantern is told how to start an agent —
# the prompt and the pane appendix. Codex is unattended
# (--dangerously-bypass-approvals-and-sandbox). Claude, Grok, and Cursor
# stay smart-auto. bypassPermissions, --yolo, and --always-approve stay
# named as never passed unless the user asks. (--force is forbidden there
# too, but launch.sh passes it legitimately for the helper CLI's own
# dangerous tier, so a grep for it proves nothing.)
for seat_file in prompt.md launch.sh; do
    for seat_args in '--permission-mode auto' '--auto-review --trust' \
        '--dangerously-bypass-approvals-and-sandbox'; do
        grep -qF -- "$seat_args" "$root/$seat_file" ||
            fail "$seat_file does not pass $seat_args on agent start"
    done
    if grep -qF -- '-s workspace-write' "$root/$seat_file"; then
        fail "$seat_file still seats Codex in the workspace-write sandbox"
    fi
    grep -qF -- '-a never -s danger-full-access' "$root/$seat_file" ||
        fail "$seat_file does not warn that -a never is not enough for Codex"
    for reckless in bypassPermissions --yolo --always-approve; do
        grep -qF -- "$reckless" "$root/$seat_file" ||
            fail "$seat_file does not forbid $reckless for seated agents"
    done
done

# Spoken model choices are separate family, effort, and fast values. The
# resolver must read each installed CLI catalog. It must not make one guessed
# slug from the whole phrase.
model_dir=$(mktemp -d)
cat >"$model_dir/codex" <<'EOF'
#!/bin/sh
[ "$1 $2" = "debug models" ] || exit 2
cat <<'JSON'
{"models":[{"slug":"gpt-5.6-sol","display_name":"GPT-5.6-Sol","visibility":"list","supported_reasoning_levels":[{"effort":"low"},{"effort":"medium"},{"effort":"high"},{"effort":"xhigh"},{"effort":"max"}],"service_tiers":[{"id":"priority","name":"Fast"}],"additional_speed_tiers":["fast"]},{"slug":"gpt-5.6-terra","display_name":"GPT-5.6-Terra","visibility":"list","supported_reasoning_levels":[{"effort":"high"}],"service_tiers":[],"additional_speed_tiers":[]},{"slug":"gpt-5.6-luna","display_name":"GPT-5.6-Luna","visibility":"list","supported_reasoning_levels":[{"effort":"high"}],"service_tiers":[],"additional_speed_tiers":["fast"]}]}
JSON
EOF
cat >"$model_dir/agent" <<'EOF'
#!/bin/sh
[ "$1" = "--list-models" ] || exit 2
cat <<'MODELS'
Available models
auto - Auto (default)
gpt-5.6-sol-high-fast - GPT-5.6 Sol High Fast
cursor-grok-4.6-high-fast - Cursor Grok 4.6 Fast
claude-opus-5-high-fast - Claude Opus 5 1M Fast
claude-opus-5-high - Claude Opus 5 1M
MODELS
EOF
cat >"$model_dir/grok" <<'EOF'
#!/bin/sh
[ "$1" = "models" ] || exit 2
cat <<'MODELS'
Default model: grok-4.6
Available models:
  * grok-4.6 (default)
  - grok-4.5
MODELS
EOF
cat >"$model_dir/claude" <<'EOF'
#!/bin/sh
case "$*" in
"/usage -p --output-format json")
    case ${CLAUDE_USAGE_MODE-} in
    global)
        printf '%s\n' '{"result":"You have hit your weekly limit - resets Aug 23 at 4:59am"}'
        ;;
    session-no-reset)
        printf '%s\n' '{"result":"Current session: 0% used\nCurrent week (all models): 82% used · resets Aug 23 at 5am\nCurrent week (Fable): 100% used · resets Aug 23 at 5am"}'
        ;;
    session-only-no-reset)
        printf '%s\n' '{"result":"Current session: 0% used"}'
        ;;
    session-exhausted-no-reset)
        printf '%s\n' '{"result":"Current session: 100% used"}'
        ;;
    *)
        printf '%s\n' '{"result":"Current session: 4% used - resets Aug 22 at 1:19pm\nCurrent week (all models): 82% used - resets Aug 23 at 4:59am\nCurrent week (Fable): 100% used - resets Aug 23 at 4:59am"}'
        ;;
    esac
    ;;
"--help")
    cat <<'HELP'
  --effort <level>  Effort level for the current session (low, medium, high, xhigh, max)
  --model <model>   Use alias 'fable', 'opus', or 'sonnet', or full name 'claude-fable-5'.
  --permission-mode <mode>
HELP
    ;;
*) exit 2 ;;
esac
EOF
cat >"$model_dir/codex.cmd" <<'EOF'
@echo off
if not "%1 %2"=="debug models" exit /b 2
echo {"models":[{"slug":"gpt-5.6-sol","display_name":"GPT-5.6-Sol","visibility":"list","supported_reasoning_levels":[{"effort":"low"},{"effort":"medium"},{"effort":"high"},{"effort":"xhigh"},{"effort":"max"}],"service_tiers":[{"id":"priority","name":"Fast"}],"additional_speed_tiers":["fast"]},{"slug":"gpt-5.6-terra","display_name":"GPT-5.6-Terra","visibility":"list","supported_reasoning_levels":[{"effort":"high"}],"service_tiers":[],"additional_speed_tiers":[]},{"slug":"gpt-5.6-luna","display_name":"GPT-5.6-Luna","visibility":"list","supported_reasoning_levels":[{"effort":"high"}],"service_tiers":[],"additional_speed_tiers":["fast"]}]}
EOF
cat >"$model_dir/agent.cmd" <<'EOF'
@echo off
if not "%1"=="--list-models" exit /b 2
echo Available models
echo auto - Auto (default)
echo gpt-5.6-sol-high-fast - GPT-5.6 Sol High Fast
echo cursor-grok-4.6-high-fast - Cursor Grok 4.6 Fast
echo claude-opus-5-high-fast - Claude Opus 5 1M Fast
echo claude-opus-5-high - Claude Opus 5 1M
EOF
cat >"$model_dir/grok.cmd" <<'EOF'
@echo off
if not "%1"=="models" exit /b 2
echo Default model: grok-4.6
echo Available models:
echo   * grok-4.6 (default)
echo   - grok-4.5
EOF
cat >"$model_dir/claude.cmd" <<'EOF'
@echo off
if "%1"=="--help" (
  echo   --effort ^<level^>  Effort level for the current session ^(low, medium, high, xhigh, max^)
  echo   --model ^<model^>   Use alias 'fable', 'opus', or 'sonnet', or full name 'claude-fable-5'.
  echo   --permission-mode ^<mode^>
  exit /b 0
)
if not "%1 %2 %3 %4"=="/usage -p --output-format json" exit /b 2
if "%CLAUDE_USAGE_MODE%"=="global" (
  echo {"result":"You have hit your weekly limit - resets Aug 23 at 4:59am"}
  exit /b 0
)
if "%CLAUDE_USAGE_MODE%"=="session-no-reset" (
  echo {"result":"Current session: 0%% used\nCurrent week (all models): 82%% used - resets Aug 23 at 5am\nCurrent week (Fable): 100%% used - resets Aug 23 at 5am"}
  exit /b 0
)
if "%CLAUDE_USAGE_MODE%"=="session-only-no-reset" (
  echo {"result":"Current session: 0%% used"}
  exit /b 0
)
if "%CLAUDE_USAGE_MODE%"=="session-exhausted-no-reset" (
  echo {"result":"Current session: 100%% used"}
  exit /b 0
)
echo {"result":"Current session: 4%% used - resets Aug 22 at 1:19pm\nCurrent week (all models): 82%% used - resets Aug 23 at 4:59am\nCurrent week (Fable): 100%% used - resets Aug 23 at 4:59am"}
EOF
chmod +x "$model_dir/codex" "$model_dir/agent" "$model_dir/grok" "$model_dir/claude"
helper_detect_python >/dev/null || fail "model route needs Python 3"
model_path=$model_dir:$PATH
run_model_route() {
    PATH="$model_path" "$root/bin/model-route" "$@"
}
run_model_preflight() {
    PATH="$model_path" "$root/bin/model-preflight" "$@"
}
codex_route=$(run_model_route codex \
    "5.6 sol high fast") || fail "Codex spoken model route"
printf '%s\n' "$codex_route" | grep -qF '"argv":["-m","gpt-5.6-sol","-c","model_reasoning_effort=\"high\"","-c","service_tier=\"priority\""]' ||
    fail "5.6 sol high fast did not become separate Codex argv"
cursor_route=$(run_model_route cursor \
    "5.6 sol high fast") || fail "Cursor spoken model route"
printf '%s\n' "$cursor_route" | grep -qF '"argv":["--model","gpt-5.6-sol-high-fast"]' ||
    fail "Cursor route did not use the listed model ID"
cursor_default=$(run_model_route cursor \
    default) || fail "Cursor live default route"
printf '%s\n' "$cursor_default" | grep -qF '"model":"gpt-5.6-sol-high-fast"' ||
    fail "Cursor default did not prefer live Sol 5.6 high fast"
printf '%s\n' "$cursor_default" | grep -qF 'cursor-grok' &&
    fail "Cursor default used a Grok model"
grok_route=$(run_model_route cursor \
    "cursor grok 4.6 high fast") || fail "Cursor Grok spoken model route"
printf '%s\n' "$grok_route" | grep -qF '"argv":["--model","cursor-grok-4.6-high-fast"]' ||
    fail "Cursor Grok route did not use the listed model ID"
opus_route=$(run_model_route cursor \
    "opus 5 high fast") || fail "Cursor Opus spoken model route"
printf '%s\n' "$opus_route" | grep -qF '"argv":["--model","claude-opus-5-high-fast"]' ||
    fail "Cursor Opus route did not use the listed model ID"
grok_default=$(run_model_route grok \
    default) || fail "Grok live default route"
printf '%s\n' "$grok_default" | grep -qF '"argv":["-m","grok-4.6","--reasoning-effort","high"]' ||
    fail "Grok default did not use live Grok 4.6 at high effort"
if fable_check=$(run_model_preflight claude fable high); then
    fail "Claude preflight accepted an exhausted Fable bucket"
else
    preflight_status=$?
fi
[ "$preflight_status" -eq 3 ] || fail "exhausted Fable should return unavailable"
printf '%s\n' "$fable_check" | grep -qF '"reason":"Current week (Fable) is 100% used; resets Aug 23 at 4:59am"' ||
    fail "Claude preflight did not report the exhausted bucket and reset"
printf '%s\n' "$fable_check" | grep -qF '"substitute":{"kind":"claude","model":"opus","effort":"xhigh"' ||
    fail "Claude preflight did not propose Opus xhigh"
if run_model_preflight claude fabel high >/dev/null 2>&1; then
    fail "Claude preflight accepted an unverified model"
fi
if run_model_preflight claude fable ultra >/dev/null 2>&1; then
    fail "Claude preflight accepted an unverified effort"
fi
CLAUDE_USAGE_MODE=session-no-reset
export CLAUDE_USAGE_MODE
opus_ok=$(run_model_preflight claude opus high) ||
    fail "Claude preflight rejected Opus when session has no reset time"
printf '%s\n' "$opus_ok" | grep -qF '"available":true' ||
    fail "Claude preflight did not treat a session without reset as available"
if fable_no_reset=$(run_model_preflight claude fable high); then
    fail "Claude preflight accepted exhausted Fable without a session reset"
else
    preflight_status=$?
fi
[ "$preflight_status" -eq 3 ] || fail "exhausted Fable without session reset should return unavailable"
printf '%s\n' "$fable_no_reset" | grep -qF '"reason":"Current week (Fable) is 100% used; resets Aug 23 at 5am"' ||
    fail "Claude preflight dropped the Fable reset when session had none"
printf '%s\n' "$fable_no_reset" | grep -qF '"substitute":{"kind":"claude","model":"opus","effort":"xhigh"' ||
    fail "Claude preflight did not keep the Opus substitute"
CLAUDE_USAGE_MODE=session-only-no-reset
opus_session_only=$(run_model_preflight claude opus high) ||
    fail "Claude preflight required an all-models bucket"
printf '%s\n' "$opus_session_only" | grep -qF '"available":true' ||
    fail "Claude preflight did not accept a session-only usage line"
CLAUDE_USAGE_MODE=session-exhausted-no-reset
if session_exhausted=$(run_model_preflight claude opus high); then
    fail "Claude preflight accepted a 100% session with no reset time"
else
    preflight_status=$?
fi
[ "$preflight_status" -eq 3 ] || fail "100% session without reset should return unavailable"
printf '%s\n' "$session_exhausted" | grep -qF '"reason":"Current session is 100% used"' ||
    fail "Claude preflight required a reset time in the exhausted reason"
printf '%s\n' "$session_exhausted" | grep -qF '"substitute":{"kind":"cursor","model":"gpt-5.6-sol-high-fast"' ||
    fail "exhausted session did not keep the substitute list"
unset CLAUDE_USAGE_MODE
CLAUDE_USAGE_MODE=global
export CLAUDE_USAGE_MODE
if global_check=$(run_model_preflight claude fable high); then
    fail "Claude preflight accepted a global limit"
else
    preflight_status=$?
fi
unset CLAUDE_USAGE_MODE
[ "$preflight_status" -eq 3 ] || fail "global Claude limit should return unavailable"
printf '%s\n' "$global_check" | grep -qF '"reason":"Limit: weekly is 100% used; resets Aug 23 at 4:59am"' ||
    fail "Claude preflight did not report the generic limit and reset"
printf '%s\n' "$global_check" | grep -qF '"substitute":{"kind":"cursor","model":"gpt-5.6-sol-high-fast"' ||
    fail "global Claude limit proposed another Claude model"
run_model_preflight cursor gpt-5.6-sol-high-fast high >/dev/null ||
    fail "Cursor preflight rejected a listed model"
if cursor_miss=$(run_model_preflight cursor cursor-grok-4.7-high-fast high); then
    fail "Cursor preflight accepted a missing catalog model"
else
    preflight_status=$?
fi
[ "$preflight_status" -eq 3 ] || fail "Cursor catalog miss should return unavailable"
printf '%s\n' "$cursor_miss" | grep -qF '"available":false' ||
    fail "Cursor catalog miss did not fail closed"
printf '%s\n' "$cursor_miss" | grep -qF '"substitute":{"kind":"cursor","model":"cursor-grok-4.6-high-fast"' ||
    fail "Cursor Grok miss did not propose the next live Cursor Grok model"
if grok_miss=$(run_model_preflight grok grok-4.7 high); then
    fail "Grok preflight accepted a missing catalog model"
else
    preflight_status=$?
fi
[ "$preflight_status" -eq 3 ] || fail "Grok catalog miss should return unavailable"
printf '%s\n' "$grok_miss" | grep -qF '"substitute":{"kind":"grok","model":"grok-4.5","effort":"high"' ||
    fail "Grok Build miss did not propose live Grok 4.5 high"
if run_model_route codex \
    "5.6 terra high fast" >/dev/null 2>&1; then
    fail "model route accepted fast for a model without fast service"
fi
if run_model_route codex \
    "5.6 luna high fast" >/dev/null 2>&1; then
    fail "model route accepted Fast without one live service tier ID"
fi
if run_model_route cursor \
    "retired mystery max" >/dev/null 2>&1; then
    fail "model route invented a model for an unknown phrase"
fi
rm -rf "$model_dir"
model_dir=

for route_file in prompt.md launch.sh; do
    grep -qiF 'open a review' "$root/$route_file" ||
        fail "$route_file does not name the Codex review route"
    grep -qF '5.6 sol high fast' "$root/$route_file" ||
        fail "$route_file does not name the split Sol default"
    grep -qE 'service_tier=\\?"priority\\?"' "$root/$route_file" ||
        fail "$route_file does not pass the Codex fast setting"
    grep -qF 'review PR' "$root/$route_file" ||
        fail "$route_file does not name the pull request review route"
done
grep -qF 'gh -R <owner/repo> pr view' "$root/prompt.md" ||
    fail "prompt.md does not resolve a named pull request with gh"
grep -qF 'start <slug> --kind codex' "$root/prompt.md" ||
    fail "prompt.md does not seat Codex for a named pull request review"
for review_kind in Cursor Grok; do
    for route_file in prompt.md launch.sh; do
        grep -qF "$review_kind review" "$root/$route_file" ||
            fail "$route_file does not name the $review_kind pull request review route"
    done
done
grep -qF '"Cursor" means `--kind cursor`' "$root/prompt.md" ||
    fail "prompt.md does not route Cursor to the Cursor kind"
grep -qF '"open battle paddle with Grok" | Seat with `--kind cursor`' "$root/prompt.md" ||
    fail "prompt.md does not route bare Grok through Cursor"
grep -qF '"open battle paddle with Grok Build", "open with SuperGrok" | Seat with `--kind grok`' "$root/prompt.md" ||
    fail "prompt.md does not route Grok Build to the Grok CLI"
grep -qF '"open battle paddle in Cursor with Grok"' "$root/prompt.md" ||
    fail "prompt.md does not name the explicit Cursor Grok route"
grep -qF '"Grok review on XYZ", "have Cursor Grok review that PR" | Use the named pull request route with Cursor plan mode' "$root/prompt.md" ||
    fail "prompt.md does not route bare Grok review through Cursor"
grep -qF '"Grok Build review on XYZ", "have SuperGrok review that PR" | Use the named pull request route with Grok Build single-turn mode' "$root/prompt.md" ||
    fail "prompt.md does not route Grok Build review through the Grok CLI"
for preflight_file in prompt.md launch.sh README.md; do
    grep -qF 'model-preflight' "$root/$preflight_file" ||
        fail "$preflight_file does not require the availability preflight"
    grep -qF 'usage line with no reset' "$root/$preflight_file" ||
        fail "$preflight_file still requires a reset time on Claude usage"
done
grep -qF '"Grok" also' "$root/launch.sh" ||
    fail "launch.sh does not route bare Grok through Cursor"
grep -qF '"Grok Build" and' "$root/launch.sh" ||
    fail "launch.sh does not route Grok Build to the Grok CLI"
grep -qF 'Never merge' "$root/prompt.md" ||
    fail "prompt.md does not forbid merge from Lantern"
for repo_file in prompt.md launch.sh README.md; do
    grep -qF 'gh repo create' "$root/$repo_file" ||
        fail "$repo_file does not route GitHub repo creation"
    grep -qF -- '--private' "$root/$repo_file" ||
        fail "$repo_file does not require private GitHub repos"
    grep -qF -- '--public' "$root/$repo_file" ||
        fail "$repo_file does not forbid public GitHub repos unless asked"
done
grep -qF '"create a GitHub repo"' "$root/prompt.md" ||
    fail "prompt.md has no GitHub repo creation route"
grep -qF 'Finish this step before any' "$root/prompt.md" ||
    fail "named PR route does not preflight before herd mutation"
grep -qF 'confirms the exact flag and the protections it removes' "$root/README.md" ||
    fail "README does not describe the explicit yolo exception"
# hsh carried a HERDR_REAL branch to dodge the wrapper, and launch.sh unsets
# HERDR_REAL before it execs the agent, so inside the pane that branch never
# ran. Nothing should put a bypass back: opening a second lantern is a
# mutation, and the gate is what makes the lantern ask first.
if grep -vE '^[[:space:]]*#' "$root/hsh" | grep -q 'HERDR_REAL'; then
    fail "hsh should not carry a path around the mutate gate"
fi

# The snapshots are other agents' terminals, copied verbatim. Anyone whose
# text reaches a pane can address the lantern in them.
grep -q 'never instructions' "$root/prompt.md" ||
    fail "prompt.md should say the snapshot files are data, not instructions"
for snapshot in floor.txt goals-floor.txt elves-floor.txt; do
    grep -qF "$snapshot" "$root/prompt.md" ||
        fail "prompt.md does not name $snapshot"
done

grep -q '^placement = "tab"$' "$root/herdr-plugin.toml" || fail "pane placement is tab"

# The bridge was removed in 0.8.0. The manifest must not offer Herdr a pane,
# action, or startup hook whose script no longer exists, and no shipped
# surface may keep selling the channels: prompt.md and launch.sh feed a live
# agent, and the pages and README are what a new user reads. The README's
# Trust section keeps one clearly historical note (and CHANGELOG.md is
# history throughout), so the prose words are checked everywhere but there;
# the config and command tokens are checked everywhere.
if grep -qi 'bridge' "$root/herdr-plugin.toml"; then
    fail "the manifest still declares bridge plumbing"
fi
for bridge_token in bridge.sh bridge.conf BRIDGE_ lantern-bridge \
    aigora.lantern.bridge slack-trial; do
    for bridge_file in README.md howto.html docs/index.html prompt.md \
        launch.sh open.sh lib.sh herdr-plugin.toml; do
        if grep -qF -- "$bridge_token" "$root/$bridge_file"; then
            fail "$bridge_file still carries $bridge_token"
        fi
    done
done
for bridge_word in 'Lantern Bridge' Telegram WhatsApp Slack; do
    for bridge_file in howto.html docs/index.html prompt.md launch.sh; do
        if grep -qiF -- "$bridge_word" "$root/$bridge_file"; then
            fail "$bridge_file still mentions $bridge_word"
        fi
    done
done

# One version, every file that prints it. A manifest bump nobody echoed is how
# a marketplace listing and a README start disagreeing — and howto.html and
# docs/index.html are the two that actually drifted, three releases behind,
# while this case checked the two that had not.
version=$(sed -n 's/^version = "\(.*\)"$/\1/p' "$root/herdr-plugin.toml")
[ -n "$version" ] || fail "no version in herdr-plugin.toml"
grep -qF "**v$version**" "$root/README.md" || fail "README does not say v$version"
grep -qF "## [$version]" "$root/CHANGELOG.md" || fail "CHANGELOG has no $version entry"
for version_page in howto.html docs/index.html; do
    grep -qF "v$version" "$root/$version_page" ||
        fail "$version_page does not say v$version"
    # And nothing older left behind next to it.
    if grep -oE 'v[0-9]+\.[0-9]+\.[0-9]+' "$root/$version_page" |
        grep -qvF "v$version"; then
        fail "$version_page still carries a version that is not v$version"
    fi
done

if grep -qE '^(width|height) =' "$root/herdr-plugin.toml"; then
    fail "pane still sizes a popup"
fi

# Real herdr 0.7.5 shapes, trimmed of fields these helpers ignore.
created_json='{"id":"cli:workspace:create","result":{"root_pane":{"agent_status":"unknown","cwd":"/Users/j","pane_id":"w9:p1","scroll":{"viewport_rows":32},"tab_id":"w9:t1","workspace_id":"w9"},"tab":{"label":"1","tab_id":"w9:t1","workspace_id":"w9"},"type":"workspace_created","workspace":{"active_tab_id":"w9:t1","label":"🔥 lantern","workspace_id":"w9"}}}'
opened_json='{"id":"cli:plugin","result":{"plugin_pane":{"entrypoint":"helper","pane":{"agent_status":"unknown","cwd":"/state/workdir","label":"Lantern","pane_id":"w9:p2","scroll":{"viewport_rows":32},"tab_id":"w9:t2","terminal_id":"term_x","workspace_id":"w9"},"plugin_id":"aigora.lantern"},"type":"plugin_pane_opened"}}'

ws=$(printf '%s' "$created_json" | helper_json_value workspace_id)
[ "$ws" = w9 ] || fail "json workspace_id ($ws)"
pane=$(printf '%s' "$created_json" | helper_json_value pane_id)
[ "$pane" = "w9:p1" ] || fail "json root pane_id ($pane)"
pane=$(printf '%s' "$opened_json" | helper_json_value pane_id)
[ "$pane" = "w9:p2" ] || fail "json opened pane_id ($pane)"
tab=$(printf '%s' "$opened_json" | helper_json_value tab_id)
[ "$tab" = "w9:t2" ] || fail "json opened tab_id ($tab)"
missing=$(printf '%s' '{"result":{}}' | helper_json_value pane_id)
[ -z "$missing" ] || fail "json missing key should be empty"

# The chat is found by pane title, so open.sh and the manifest must agree.
grep -q 'pane_title=Lantern' "$root/open.sh" || fail "open.sh pane title"
grep -q '^title = "Lantern"$' "$root/herdr-plugin.toml" || fail "manifest pane title"
grep -q "workspace_label='🔥 lantern'" "$root/open.sh" || fail "open.sh lantern label"

fake_ws=$(mktemp -d)
cat >"$fake_ws/herdr" <<'EOF'
#!/bin/sh
if [ "$1" = workspace ] && [ "$2" = list ]; then
    printf '%s\n' '{"result":{"workspaces":[{"label":"love-spark","workspace_id":"w1"},{"label":"lantern","workspace_id":"w5"},{"label":"🔥 lantern","workspace_id":"w7"}]}}'
    exit 0
fi
if [ "$1" = workspace ] && [ "$2" = get ]; then
    [ "$3" = w7 ] || exit 1
    printf '%s\n' '{"result":{"type":"workspace_info","workspace":{"active_tab_id":"w7:t2","label":"🔥 lantern","workspace_id":"w7"}}}'
    exit 0
fi
if [ "$1" = pane ] && [ "$2" = get ]; then
    [ "$3" = "w7:p2" ] || exit 1
    printf '%s\n' '{"result":{"pane":{"agent_status":"idle","label":"Lantern","pane_id":"w7:p2","scroll":{"viewport_rows":32},"tab_id":"w7:t2","workspace_id":"w7"},"type":"pane_info"}}'
    exit 0
fi
exit 1
EOF
chmod +x "$fake_ws/herdr"
found=$(helper_workspace_id_by_label "$fake_ws/herdr" '🔥 lantern')
[ "$found" = w7 ] || fail "workspace by lantern label ($found)"
[ -z "$(helper_workspace_id_by_label "$fake_ws/herdr" nope)" ] || fail "unknown label"
[ "$(helper_workspace_label "$fake_ws/herdr" w7)" = '🔥 lantern' ] ||
    fail "workspace label"
if helper_workspace_label "$fake_ws/herdr" w8 >/dev/null 2>&1; then
    fail "stale workspace id"
fi
if helper_workspace_label "$fake_ws/herdr" "" >/dev/null 2>&1; then
    fail "empty workspace id"
fi
[ "$(helper_lantern_pane_workspace "$fake_ws/herdr" w7:p2 Lantern)" = w7 ] ||
    fail "lantern pane workspace"
if helper_lantern_pane_workspace "$fake_ws/herdr" w7:p2 Probe >/dev/null 2>&1; then
    fail "pane with another title"
fi
if helper_lantern_pane_workspace "$fake_ws/herdr" w7:p9 Lantern >/dev/null 2>&1; then
    fail "missing pane"
fi
if helper_lantern_pane_workspace "$fake_ws/herdr" "" Lantern >/dev/null 2>&1; then
    fail "empty pane id"
fi

# open.sh end to end against a stub herdr that records every call.
open_dir=$(mktemp -d)
cat >"$open_dir/herdr" <<'EOF'
#!/bin/sh
printf '%s\n' "$*" >>"$STUB_LOG"
case "$1 $2" in
"workspace list")
    printf '{"result":{"workspaces":[{"label":"repo","workspace_id":"w1"}%s]}}\n' "$STUB_WS_EXTRA"
    ;;
"workspace get")
    [ "$3" = "$STUB_WS" ] || exit 1
    printf '{"result":{"workspace":{"label":"%s","workspace_id":"%s"}}}\n' \
        "$STUB_WS_LABEL" "$3"
    ;;
"pane get")
    [ "$3" = "$STUB_PANE" ] || exit 1
    printf '{"result":{"pane":{"label":"%s","pane_id":"%s","scroll":{"viewport_rows":32},"tab_id":"%s:t2","workspace_id":"%s"}}}\n' \
        "$STUB_PANE_LABEL" "$3" "${3%%:*}" "${3%%:*}"
    ;;
"pane list")
    printf '{"result":{"panes":[{"agent_status":"idle","cwd":"/repo","pane_id":"w1:p1","scroll":{"viewport_rows":32},"tab_id":"w1:t1","workspace_id":"w1"}%s]}}\n' \
        "$STUB_PANE_EXTRA"
    ;;
"workspace create")
    printf '{"result":{"root_pane":{"pane_id":"w9:p1","scroll":{"viewport_rows":32},"tab_id":"w9:t1","workspace_id":"w9"},"workspace":{"label":"new","workspace_id":"w9"}}}\n'
    ;;
"plugin pane")
    if [ "$3" = focus ]; then
        [ "${STUB_FOCUS_FAIL:-}" = 1 ] && exit 1
    fi
    if [ "$3" = open ]; then
        [ "${STUB_OPEN_FAIL:-}" = 1 ] && exit 1
        printf '{"result":{"plugin_pane":{"pane":{"label":"Lantern","pane_id":"%s:p2","scroll":{"viewport_rows":32},"tab_id":"%s:t2","workspace_id":"%s"}}}}\n' \
            "$STUB_OPEN_WS" "$STUB_OPEN_WS" "$STUB_OPEN_WS"
    fi
    ;;
esac
exit 0
EOF
chmod +x "$open_dir/herdr"
STUB_LOG=$open_dir/calls.txt
export STUB_LOG STUB_WS_EXTRA STUB_WS STUB_WS_LABEL STUB_PANE STUB_PANE_LABEL
export STUB_OPEN_WS STUB_OPEN_FAIL STUB_PANE_EXTRA STUB_FOCUS_FAIL
STUB_FOCUS_FAIL=
STUB_WS_EXTRA=
STUB_WS=
STUB_WS_LABEL=
STUB_PANE=
STUB_PANE_LABEL=Lantern
STUB_PANE_EXTRA=
STUB_OPEN_WS=w9
STUB_OPEN_FAIL=

open_state=$open_dir/state
reset_open() {
    rm -rf "$open_state"
    mkdir -p "$open_state"
    : >"$STUB_LOG"
}
run_open() {
    # OPEN_CONF_DIR is empty for most cases: open.sh treats a missing config
    # directory as "no conf" and the tab label stays plain home.
    HERDR_REAL="$open_dir/herdr" \
        HERDR_PLUGIN_STATE_DIR="$open_state" \
        HERDR_PLUGIN_CONFIG_DIR="${OPEN_CONF_DIR:-}" \
        HERDR_PLUGIN_ROOT="$root" \
        sh "$root/open.sh" >/dev/null 2>&1
}
logged() { grep -qF -e "$1" "$STUB_LOG"; }

# First open: create the lantern workspace, seat the chat, drop the shell.
reset_open
run_open || fail "first open"
logged 'workspace create --cwd' || fail "first open should create a workspace"
# The exact form matters. herdr is native, so on Windows this has to be
# C:\Users\name and not the /c/Users/name that Git Bash reports as $HOME.
logged "workspace create --cwd $(helper_native_path "$HOME")" ||
    fail "first open should pass the native path form to herdr"
logged '--label 🔥 lantern' || fail "first open should use the lantern label"
logged 'plugin pane open --plugin aigora.lantern --entrypoint helper --placement tab --workspace w9' ||
    fail "first open should seat a tab in the new workspace"
logged 'tab rename w9:t2 home' || fail "first open should name the tab"
logged 'pane close w9:p1' || fail "first open should close the empty shell"
[ "$(cat "$open_state/workspace.id")" = w9 ] || fail "first open workspace state"
[ "$(cat "$open_state/pane.id")" = w9:p2 ] || fail "first open pane state"

# A live chat is focused, not opened again, wherever it sits now.
reset_open
printf 'w7\n' >"$open_state/workspace.id"
printf 'w7:p2\n' >"$open_state/pane.id"
STUB_PANE=w7:p2
run_open || fail "reopen with a live chat"
logged 'plugin pane focus w7:p2' || fail "reopen should focus the live chat"
if logged 'plugin pane open'; then fail "reopen should not seat a second chat"; fi
if logged 'workspace create'; then fail "reopen should not create a workspace"; fi
STUB_PANE=

# A remembered id that lost the label is somebody else's workspace now.
reset_open
printf 'w1\n' >"$open_state/workspace.id"
STUB_WS=w1
STUB_WS_LABEL=repo
STUB_OPEN_WS=w9
run_open || fail "stale workspace id"
if logged 'plugin pane open --plugin aigora.lantern --entrypoint helper --placement tab --workspace w1'; then
    fail "a relabelled workspace must not host the chat"
fi
logged 'workspace create --cwd' || fail "stale id should fall through to create"
STUB_WS=
STUB_WS_LABEL=

# An existing lantern workspace is reused, and no shell tab is closed.
reset_open
STUB_WS_EXTRA=',{"label":"🔥 lantern","workspace_id":"w7"}'
STUB_OPEN_WS=w7
run_open || fail "reuse by label"
if logged 'workspace create'; then fail "label match must not create a workspace"; fi
logged '--placement tab --workspace w7' || fail "label match should seat in w7"
if logged 'pane close'; then fail "label match must not close a pane"; fi
STUB_WS_EXTRA=
STUB_OPEN_WS=w9

# A chat that dies between the check and the focus is replaced, not fatal.
reset_open
printf 'w7\n' >"$open_state/workspace.id"
printf 'w7:p2\n' >"$open_state/pane.id"
STUB_PANE=w7:p2
STUB_FOCUS_FAIL=1
run_open || fail "a refused focus should not end the open"
logged 'workspace create --cwd' || fail "a refused focus should seat a new chat"
STUB_FOCUS_FAIL=
STUB_PANE=

# A state directory that cannot be locked is an error, not silence.
# Windows ignores the permission bits on a directory, so chmod cannot set the
# precondition there. Probe for that instead of failing on it, and keep the
# assertion wherever the bits are real.
reset_open
chmod 500 "$open_state"
if mkdir "$open_state/probe" 2>/dev/null; then
    rmdir "$open_state/probe"
    printf 'SKIP: platform ignores directory permission bits (unlockable state dir)\n'
elif run_open; then
    chmod 700 "$open_state"
    fail "an unlockable state dir should exit nonzero"
fi
chmod 700 "$open_state"

# A workspace labelled by hand is not the lantern workspace.
reset_open
STUB_WS_EXTRA=',{"label":"lantern","workspace_id":"w5"}'
run_open || fail "plain label"
if logged '--workspace w5'; then fail "a plain lantern label must not be adopted"; fi
logged 'workspace create --cwd' || fail "plain label should fall through to create"
STUB_WS_EXTRA=

# A chat already running in that workspace is focused, not duplicated.
reset_open
STUB_WS_EXTRA=',{"label":"🔥 lantern","workspace_id":"w7"}'
STUB_PANE_EXTRA=',{"agent_status":"idle","cwd":"/state","label":"Lantern","pane_id":"w7:p2","scroll":{"viewport_rows":32},"tab_id":"w7:t2","workspace_id":"w7"}'
STUB_PANE=w7:p2
run_open || fail "duplicate guard"
logged 'plugin pane focus w7:p2' || fail "an existing chat should be focused"
if logged 'plugin pane open'; then fail "must not seat a second chat in w7"; fi
[ "$(cat "$open_state/pane.id")" = w7:p2 ] || fail "duplicate guard pane state"
STUB_WS_EXTRA=
STUB_PANE_EXTRA=
STUB_PANE=

# Follow the workspace Herdr reports, and drop the one this run made.
reset_open
STUB_OPEN_WS=w8
run_open || fail "adopt the reported workspace"
logged 'workspace close w9' || fail "the unused new workspace should be closed"
if logged 'pane close w9:p1'; then fail "must not close a pane in a dropped workspace"; fi
[ "$(cat "$open_state/workspace.id")" = w8 ] || fail "adopted workspace state"
STUB_OPEN_WS=w9

# A chat that never starts must not leave an empty workspace behind.
reset_open
printf 'w1\n' >"$open_state/workspace.id"
STUB_WS=w1
STUB_WS_LABEL=repo
STUB_OPEN_FAIL=1
if run_open; then fail "failed open should exit nonzero"; fi
logged 'workspace close w9' || fail "failed open should drop the new workspace"
if [ -f "$open_state/workspace.id" ]; then
    fail "failed open should forget the workspace"
fi
STUB_OPEN_FAIL=
STUB_WS=
STUB_WS_LABEL=

# A second open while one is in flight does nothing.
reset_open
mkdir "$open_state/open.lock"
run_open || fail "locked open should exit 0"
if [ -s "$STUB_LOG" ]; then
    fail "locked open should call nothing"
fi
rmdir "$open_state/open.lock"

# The chat tab names what it runs. With a readable helper.conf the seat
# renames the tab to carry the CLI and model; every case above ran with no
# config directory and asserted the plain home label.
reset_open
mkdir -p "$open_dir/conf"
printf '%s\n' 'HELPER_AGENT="claude"' 'HELPER_MODEL="opus"' \
    'HELPER_EFFORT="high"' >"$open_dir/conf/helper.conf"
OPEN_CONF_DIR=$open_dir/conf
export OPEN_CONF_DIR
run_open || fail "open with a helper.conf should succeed"
logged 'tab rename w9:t2 home · claude · opus · high' ||
    fail "the chat tab should say which CLI and model it runs"

# A conf the parser refuses must not break the open, and must not label the
# tab with a guess.
reset_open
printf '%s\n' 'EVIL=1' >"$open_dir/conf/helper.conf"
run_open || fail "open with a bad conf should still succeed"
logged 'tab rename w9:t2 home' || fail "a bad conf should leave the tab home"
if grep -qF 'tab rename w9:t2 home ·' "$STUB_LOG"; then
    fail "a bad conf must not invent a chat identity"
fi

# An agent launch.sh would refuse is never labelled: the pane will only
# show launch.sh's error, and a sidebar advertising a CLI that never ran
# is the confusion the label exists to prevent.
reset_open
printf '%s\n' 'HELPER_AGENT="gemini"' >"$open_dir/conf/helper.conf"
run_open || fail "open with an unsupported agent should still succeed"
logged 'tab rename w9:t2 home' ||
    fail "an unsupported agent should leave the tab home"
if grep -qF 'tab rename w9:t2 home ·' "$STUB_LOG"; then
    fail "an agent launch.sh refuses must not be labelled"
fi

# The very first open runs before launch.sh has seeded helper.conf. The
# label falls back to the same PATH detection launch.sh will use, so the
# tab still says what the chat will run. The stub sits in $HOME/.grok/bin
# because helper_extend_user_path prepends that last, ahead of any real
# Homebrew CLI — and detection prefers the name `agent` first either way,
# so the label is the same wherever the name resolves.
reset_open
rm -f "$open_dir/conf/helper.conf"
first_home=$open_dir/first-home
mkdir -p "$first_home/.grok/bin"
printf '%s\n' '#!/bin/sh' 'exit 0' >"$first_home/.grok/bin/agent"
chmod +x "$first_home/.grok/bin/agent"
env HOME="$first_home" PATH="/usr/bin:/bin" \
    HERDR_REAL="$open_dir/herdr" \
    HERDR_PLUGIN_STATE_DIR="$open_state" \
    HERDR_PLUGIN_CONFIG_DIR="$open_dir/conf" \
    HERDR_PLUGIN_ROOT="$root" \
    sh "$root/open.sh" >/dev/null 2>&1 ||
    fail "a first open without a seeded conf should succeed"
logged "tab rename w9:t2 home · cursor agent · $(helper_cursor_default_model)" ||
    fail "a first open should label from the same detection launch.sh uses"
OPEN_CONF_DIR=

# helper_detect_agent picks the first of agent, devin, claude, codex, grok on
# PATH. This used to assert that one of them was installed on the machine
# running the suite, which is a fact about the machine and not about the code:
# it can never hold on a CI runner or in a bare container. Test the order
# against a PATH the test controls, which also covers preference, something the
# old assertion never did.
fake_agents=$(mktemp -d)
for stub_agent in claude codex; do
    printf '%s\n' '#!/bin/sh' 'exit 0' >"$fake_agents/$stub_agent"
    chmod +x "$fake_agents/$stub_agent"
done
picked=$(PATH="$fake_agents"; export PATH; helper_detect_agent) ||
    fail "detect should find a stub on a controlled PATH"
[ "$picked" = claude ] || fail "detect should prefer claude over codex (got $picked)"

printf '%s\n' '#!/bin/sh' 'exit 0' >"$fake_agents/agent"
chmod +x "$fake_agents/agent"
picked=$(PATH="$fake_agents"; export PATH; helper_detect_agent) ||
    fail "detect should still find a stub"
[ "$picked" = agent ] || fail "detect should prefer agent first (got $picked)"

empty_agents=$(mktemp -d)
if picked=$(PATH="$empty_agents"; export PATH; helper_detect_agent); then
    fail "detect should fail when nothing is on PATH (got $picked)"
fi
rm -rf "$fake_agents" "$empty_agents"
fake_agents=

# The real machine is reported, never asserted. launch.sh is where a missing
# CLI becomes an error the user can read.
if detected=$(helper_detect_agent); then
    printf 'note: helper CLI on this machine: %s\n' "$detected"
else
    printf 'note: no helper CLI on PATH here\n'
fi

export HERDR_REAL=/bin/echo
export HERDR_HELPER_OK=
out=$(sh "$root/bin/herdr" agent list) || fail "inspect should pass"
printf '%s\n' "$out" | grep -q 'agent list' || fail "inspect did not exec real herdr"
for inspect_route in 'tab list' 'pane list' 'session list' 'integration status'; do
    # Word splitting is intentional. These fixed test values are command argv.
    # shellcheck disable=SC2086
    out=$(sh "$root/bin/herdr" $inspect_route) ||
        fail "$inspect_route should pass the read only gate"
    printf '%s\n' "$out" | grep -qF "$inspect_route" ||
        fail "$inspect_route did not exec real herdr"
done

if sh "$root/bin/herdr" pane run w1:p1 codex review --base main 2>"$err"; then
    fail "pane run without confirmation should fail"
fi
grep -q HERDR_HELPER_OK "$err" || fail "pane run blocked hint missing"

if sh "$root/bin/herdr" workspace create --cwd /tmp --label x 2>"$err"; then
    fail "mutate without OK should fail"
fi
grep -q HERDR_HELPER_OK "$err" || fail "blocked hint missing"

export HERDR_HELPER_OK=1
out=$(sh "$root/bin/herdr" workspace create --cwd /tmp --label x) ||
    fail "mutate with OK should pass"
printf '%s\n' "$out" | grep -q 'workspace create' || fail "OK mutate did not exec real herdr"

fake_prompt=$(mktemp -d)
# A Cursor pane that keeps the text in its follow-up field: the prompt with
# --wait times out with the message showing in the pane, and only Enter
# submits it. FAKE_PANE is that pane's screen, so `agent read` can answer
# with what is actually on it.
cat >"$fake_prompt/herdr" <<'EOF'
#!/bin/sh
case "$1 $2" in
"agent prompt")
    if [ "$3" = gone ]; then
        printf 'no such agent: %s\n' "$3" >&2
        exit 3
    fi
    if [ "$5" = --wait ]; then
        printf '> %s\n' "$4" >>"$FAKE_PANE"
        exit 1
    fi
    printf 'agent prompt %s\n' "$3"
    exit 0
    ;;
"agent read")
    cat "$FAKE_PANE" 2>/dev/null
    exit 0
    ;;
"agent send-keys")
    printf 'agent send-keys %s %s\n' "$3" "$4"
    [ -n "${FAKE_PANE_DEAF:-}" ] || printf 'submitted\n' >>"$FAKE_PANE"
    exit 0
    ;;
"agent wait")
    printf 'agent wait %s\n' "$3"
    exit 0
    ;;
esac
printf '%s\n' "$*"
exit 0
EOF
chmod +x "$fake_prompt/herdr"
export HERDR_REAL="$fake_prompt/herdr"
export HERDR_HELPER_OK=1
export FAKE_PANE="$fake_prompt/pane.txt"
: >"$FAKE_PANE"
out=$(sh "$root/bin/herdr" agent prompt w1:p1 "hello there") ||
    fail "prompt relay should succeed after Enter fallback"
printf '%s\n' "$out" | grep -q 'agent send-keys w1:p1 Enter' ||
    fail "prompt relay did not send Enter fallback"
printf '%s\n' "$out" | grep -q 'agent wait w1:p1' ||
    fail "prompt relay did not wait after Enter"

# Enter is for the stall and nothing else. A prompt that failed for its own
# reason — unknown target, herdr not running, a flag herdr rejected — leaves
# nothing in the pane, and the relay used to press Enter anyway and then
# report the message as sent on the strength of a wait that returns at once.
: >"$FAKE_PANE"
if out=$(sh "$root/bin/herdr" agent prompt gone "unrelated failure" 2>"$err"); then
    printf '%s\n' "$out" >&2
    fail "a prompt failure that is not a stall must not be reported as sent"
fi
if printf '%s\n' "$out" | grep -q 'agent send-keys'; then
    fail "a prompt failure that is not a stall must not press Enter"
fi

# The stall path, with a pane that swallows the Enter. `agent wait` answers
# straight away because the pane is idle already, so the wait is not evidence
# of anything and the relay must not call this sent.
: >"$FAKE_PANE"
export FAKE_PANE_DEAF=1
if out=$(sh "$root/bin/herdr" agent prompt w1:p1 "hello there" 2>"$err"); then
    printf '%s\n' "$out" >&2
    fail "an Enter that submitted nothing must not be reported as sent"
fi
printf '%s\n' "$out" | grep -q 'agent send-keys w1:p1 Enter' ||
    fail "the stall case should still press Enter"
unset FAKE_PANE_DEAF
unset FAKE_PANE
unset HERDR_REAL
rm -rf "$fake_prompt"
fake_prompt=

# Codex first-run gates. Herdr returns agent_not_ready as soon as the
# seat is blocked during startup. The wrapper reads that named pane and
# sends only the key that gate wants.
[ "$(helper_codex_startup_key "$(printf '%s\n' \
    '> You are in /tmp/demo' \
    'Do you trust the contents of this directory?' \
    '› 1. Yes, continue')")" = Enter ] ||
    fail "trust dialog should send Enter"
[ "$(helper_codex_startup_key "$(printf '%s\n' \
    'Do you trust the contents of this' \
    'directory?')")" = Enter ] ||
    fail "a wrapped trust question should still send Enter"
[ "$(helper_codex_startup_key 'Start a new chat? [y/n]')" = y ] ||
    fail "[y/n] new-chat confirm should send y"
[ "$(helper_codex_startup_key 'Resume this session? yes (y)')" = y ] ||
    fail "yes (y) new-chat confirm should send y"
[ "$(helper_codex_startup_key "$(printf '%s\n' \
    'Do you trust the contents of this directory?' \
    'Yes, continue' \
    'Start a new chat? [y/n]')")" = Enter ] ||
    fail "trust should win the first key when both gates are visible"
if helper_codex_startup_key 'Would you like to run setup.sh? [y/n]' >/dev/null; then
    fail "a permission [y/n] must not look like a new-chat confirm"
fi
if helper_codex_startup_key 'Approve MCP server startup? yes (y)' >/dev/null; then
    fail "a permission yes (y) must not look like a new-chat confirm"
fi
helper_seat_is_ready '{"interactive_ready":true,"agent_status":"idle"}' ||
    fail "idle plus interactive_ready should be ready"
helper_seat_is_ready '{"interactive_ready":true,"agent_status":"done"}' ||
    fail "done plus interactive_ready should be ready"
if helper_seat_is_ready '{"interactive_ready":true,"agent_status":"blocked"}'; then
    fail "blocked plus interactive_ready must not count as seated"
fi
if helper_codex_seat_ok '{"agent":"claude","pane_id":"w1:p1"}' w1:p1; then
    fail "a Claude occupant must not count as the Codex seat"
fi
helper_codex_seat_ok '{"agent":"codex","pane_id":"w1:p1"}' w1:p1 ||
    fail "a Codex occupant in the start pane should count"
helper_seat_ok '{"agent":"claude","pane_id":"w1:p1"}' w1:p1 claude ||
    fail "a Claude occupant in the start pane should count"
if helper_seat_ok '{"agent":"codex","pane_id":"w1:p1"}' w1:p1 claude; then
    fail "a Codex occupant must not count as the Claude seat"
fi
if helper_seat_ok '{"agent":"claude","pane_id":"w2:p9"}' w1:p1 claude; then
    fail "a Claude seat in another pane must not count"
fi

# Claude first-run folder trust. One screen, one Enter, nothing else.
helper_claude_pane_has_trust "$(printf '%s\n' \
    'Accessing workspace' \
    '/tmp/demo' \
    '› 1. Yes, I trust this folder' \
    '  2. No, choose another folder' \
    'Enter to confirm')" ||
    fail "the Claude folder trust screen should be a gate"
helper_claude_pane_has_trust "$(printf '%s\n' \
    'Yes, I' \
    'trust this folder')" ||
    fail "a wrapped Claude trust option should still be a gate"
if helper_claude_pane_has_trust "$(printf '%s\n' \
    '> You are in /tmp/demo' \
    'Do you trust the contents of this directory?' \
    '› 1. Yes, continue')"; then
    fail "the Codex trust dialog must not be the Claude gate"
fi
if helper_claude_pane_has_trust 'press enter to confirm or esc to cancel'; then
    fail "a later permission prompt must not look like the Claude gate"
fi
if helper_claude_pane_has_trust 'Do you want to make this edit to lib.sh?'; then
    fail "a later edit permission prompt must not look like the Claude gate"
fi
if helper_claude_pane_has_trust 'Start a new chat? [y/n]'; then
    fail "a new-chat confirm must not look like the Claude gate"
fi
if helper_claude_pane_has_trust 'claude is starting...'; then
    fail "unrelated pane text must not look like the Claude gate"
fi
if helper_codex_startup_key 'press enter to confirm or esc to cancel' >/dev/null; then
    fail "a later confirm prompt must not look like a first-run gate"
fi
if helper_codex_startup_key 'codex is starting...' >/dev/null; then
    fail "unrelated pane text must not look like a first-run gate"
fi
[ "$(helper_argv_option_value --kind agent start reviewer --kind codex --pane w1:p1)" = codex ] ||
    fail "--kind value"
[ "$(helper_argv_option_value --pane agent start reviewer --kind=codex --pane=w1:p1)" = w1:p1 ] ||
    fail "--pane= value"
if helper_argv_option_value --kind agent start reviewer --pane w1:p1 -- --kind codex >/dev/null; then
    fail "a --kind after -- must not be read as the seat kind"
fi

fake_start=$(mktemp -d)
cat >"$fake_start/herdr" <<'EOF'
#!/bin/sh
case "$1 $2" in
"agent start")
    printf '<%s>\n' "$@" >"${FAKE_START_LOG:-/dev/null}"
    if [ "${FAKE_START_OK:-}" = 1 ]; then
        printf 'agent start %s\n' "$3"
        exit 0
    fi
    if [ "${FAKE_START_ERR:-agent_not_ready}" != agent_not_ready ]; then
        printf '{"error":{"code":"%s","message":"start failed: %s"}}\n' \
            "$FAKE_START_ERR" "$3" >&2
        exit 1
    fi
    printf '{"error":{"code":"agent_not_ready","message":"%s is blocked during startup and is not ready for prompts"}}\n' \
        "$3" >&2
    exit 1
    ;;
"agent get")
    ready=false
    status=blocked
    [ -f "${FAKE_READY_FILE:-}" ] && ready=true && status=idle
    [ -n "${FAKE_GET_STATUS:-}" ] && status=$FAKE_GET_STATUS
    printf '{"result":{"agent":{"pane_id":"%s","agent":"%s","interactive_ready":%s,"agent_status":"%s"}}}\n' \
        "${FAKE_AGENT_PANE:-w1:p1}" "${FAKE_GET_AGENT:-codex}" "$ready" "$status"
    exit 0
    ;;
"agent read")
    n=0
    [ -f "${FAKE_READ_N:-}" ] && n=$(cat "$FAKE_READ_N")
    n=$((n + 1))
    [ -n "${FAKE_READ_N:-}" ] && printf '%s\n' "$n" >"$FAKE_READ_N"
    if [ -n "${FAKE_PANE_AFTER:-}" ] && [ -f "$FAKE_PANE_AFTER" ] &&
        [ "$n" -ge "${FAKE_SWAP_AT:-999}" ]; then
        cat "$FAKE_PANE_AFTER"
        exit 0
    fi
    if [ "$n" -eq 1 ] && [ -n "${FAKE_PANE_DELAY:-}" ] && [ -f "$FAKE_PANE_DELAY" ]; then
        cat "$FAKE_PANE_DELAY"
        exit 0
    fi
    cat "$FAKE_PANE" 2>/dev/null
    exit 0
    ;;
"agent send-keys")
    printf 'agent send-keys %s %s\n' "$3" "$4"
    if [ -n "${FAKE_PANE_NEXT:-}" ] && [ -f "$FAKE_PANE_NEXT" ]; then
        cat "$FAKE_PANE_NEXT" >"$FAKE_PANE"
        rm -f "$FAKE_PANE_NEXT"
        exit 0
    fi
    if [ -n "${FAKE_HOLD_FILE:-}" ] && [ -f "$FAKE_HOLD_FILE" ]; then
        rm -f "$FAKE_HOLD_FILE"
        exit 0
    fi
    [ -n "${FAKE_START_DEAF:-}" ] || : >"${FAKE_READY_FILE:-/dev/null}"
    exit 0
    ;;
"agent wait")
    printf '%s\n' "$*"
    case "$*" in
    *"--timeout 120000"*) exit 0 ;;
    esac
    [ -f "${FAKE_READY_FILE:-}" ] && exit 0
    exit 1
    ;;
esac
printf '%s\n' "$*"
exit 0
EOF
chmod +x "$fake_start/herdr"
export HERDR_REAL="$fake_start/herdr"
export HERDR_HELPER_OK=1
export FAKE_PANE="$fake_start/pane.txt"
export FAKE_PANE_NEXT="$fake_start/pane.next"
export FAKE_PANE_DELAY="$fake_start/pane.delay"
export FAKE_PANE_AFTER="$fake_start/pane.after"
export FAKE_SWAP_AT=999
export FAKE_GET_AGENT=codex
unset FAKE_GET_STATUS
export FAKE_READ_N="$fake_start/read.n"
export FAKE_HOLD_FILE="$fake_start/hold"
export FAKE_READY_FILE="$fake_start/ready"
export FAKE_AGENT_PANE=w1:p1
export FAKE_START_ERR=agent_not_ready
export FAKE_START_LOG="$fake_start/start.log"
unset FAKE_START_OK
unset FAKE_START_DEAF
: >"$FAKE_PANE"
rm -f "$FAKE_READY_FILE" "$FAKE_PANE_NEXT" "$FAKE_PANE_DELAY" "$FAKE_READ_N" "$FAKE_HOLD_FILE"

printf '%s\n' '> You are in /tmp/demo' \
    'Do you trust the contents of this directory?' \
    '› 1. Yes, continue' >"$FAKE_PANE"
out=$(sh "$root/bin/herdr" agent start reviewer --kind codex --pane w1:p1 2>/dev/null) ||
    fail "Codex trust gate should recover after Enter"
printf '%s\n' "$out" | grep -q 'agent send-keys reviewer Enter' ||
    fail "Codex trust gate did not send Enter to the named seat"
printf '%s\n' "$out" | grep -q 'agent wait reviewer --until idle' ||
    fail "Codex trust gate did not wait until idle"

rm -f "$FAKE_READY_FILE"
printf '%s\n' 'Start a new chat? [y/n]' >"$FAKE_PANE"
out=$(sh "$root/bin/herdr" agent start reviewer --kind codex --pane w1:p1 2>/dev/null) ||
    fail "Codex new-chat [y/n] should recover after y"
printf '%s\n' "$out" | grep -q 'agent send-keys reviewer y' ||
    fail "Codex new-chat confirm did not send y to the named seat"

rm -f "$FAKE_READY_FILE"
printf '%s\n' 'Resume this session? yes (y)' >"$FAKE_PANE"
out=$(sh "$root/bin/herdr" agent start reviewer --kind codex --pane w1:p1 2>/dev/null) ||
    fail "Codex yes (y) confirm should recover after y"
printf '%s\n' "$out" | grep -q 'agent send-keys reviewer y' ||
    fail "Codex yes (y) confirm did not send y"

# Trust then a new-chat confirm: one key is not enough.
rm -f "$FAKE_READY_FILE" "$FAKE_READ_N"
printf '%s\n' '> You are in /tmp/demo' \
    'Do you trust the contents of this directory?' \
    '› 1. Yes, continue' >"$FAKE_PANE"
printf '%s\n' 'Start a new chat? [y/n]' >"$FAKE_PANE_NEXT"
out=$(sh "$root/bin/herdr" agent start reviewer --kind codex --pane w1:p1 2>/dev/null) ||
    fail "trust then new-chat should recover after both keys"
printf '%s\n' "$out" | grep -q 'agent send-keys reviewer Enter' ||
    fail "sequential gates did not send Enter for trust"
printf '%s\n' "$out" | grep -q 'agent send-keys reviewer y' ||
    fail "sequential gates did not send y for new-chat"

# Leftover trust text in the pane must not send a second Enter instead of y.
rm -f "$FAKE_READY_FILE" "$FAKE_READ_N" "$FAKE_HOLD_FILE"
printf '%s\n' '> You are in /tmp/demo' \
    'Do you trust the contents of this directory?' \
    '› 1. Yes, continue' >"$FAKE_PANE"
printf '%s\n' '> You are in /tmp/demo' \
    'Do you trust the contents of this directory?' \
    '› 1. Yes, continue' \
    'Start a new chat? [y/n]' >"$FAKE_PANE_NEXT"
out=$(sh "$root/bin/herdr" agent start reviewer --kind codex --pane w1:p1 2>/dev/null) ||
    fail "leftover trust text should still send y for new-chat"
printf '%s\n' "$out" | grep -q 'agent send-keys reviewer y' ||
    fail "leftover trust text ate the new-chat y"
[ "$(printf '%s\n' "$out" | grep -c 'agent send-keys reviewer Enter')" -eq 1 ] ||
    fail "leftover trust text sent Enter more than once"

# After trust, a blank transition, then the new-chat confirm.
rm -f "$FAKE_READY_FILE" "$FAKE_READ_N" "$FAKE_HOLD_FILE"
printf '%s\n' '> You are in /tmp/demo' \
    'Do you trust the contents of this directory?' \
    '› 1. Yes, continue' >"$FAKE_PANE"
printf '%s\n' 'codex is starting...' >"$FAKE_PANE_NEXT"
printf '%s\n' 'Start a new chat? [y/n]' >"$FAKE_PANE_AFTER"
export FAKE_SWAP_AT=3
out=$(sh "$root/bin/herdr" agent start reviewer --kind codex --pane w1:p1 2>/dev/null) ||
    fail "a delayed second gate should still recover"
printf '%s\n' "$out" | grep -q 'agent send-keys reviewer Enter' ||
    fail "delayed second gate did not send Enter for trust"
printf '%s\n' "$out" | grep -q 'agent send-keys reviewer y' ||
    fail "delayed second gate did not send y after the blank"
export FAKE_SWAP_AT=999
rm -f "$FAKE_PANE_AFTER" "$FAKE_PANE_NEXT"

# A live Claude occupant in that pane must not receive keys.
export FAKE_GET_AGENT=claude
rm -f "$FAKE_READY_FILE" "$FAKE_READ_N"
printf '%s\n' '> You are in /tmp/demo' \
    'Do you trust the contents of this directory?' \
    '› 1. Yes, continue' >"$FAKE_PANE"
if out=$(sh "$root/bin/herdr" agent start reviewer --kind codex --pane w1:p1 2>"$err"); then
    printf '%s\n' "$out" >&2
    fail "a Claude occupant must not be reported as a Codex seat"
fi
if printf '%s\n' "$out" | grep -q 'agent send-keys'; then
    fail "a Claude occupant must not receive Codex startup keys"
fi
export FAKE_GET_AGENT=codex

# Trust, then a [y/n] that still needs Enter.
rm -f "$FAKE_READY_FILE" "$FAKE_READ_N"
printf '%s\n' '> You are in /tmp/demo' \
    'Do you trust the contents of this directory?' \
    '› 1. Yes, continue' >"$FAKE_PANE"
printf '%s\n' 'Start a new chat? [y/n]' >"$FAKE_PANE_NEXT"
: >"$FAKE_HOLD_FILE"
out=$(sh "$root/bin/herdr" agent start reviewer --kind codex --pane w1:p1 2>/dev/null) ||
    fail "trust then stuck [y/n] should recover after Enter"
printf '%s\n' "$out" | grep -q 'agent send-keys reviewer Enter' ||
    fail "trust then stuck [y/n] did not send Enter"
printf '%s\n' "$out" | grep -q 'agent send-keys reviewer y' ||
    fail "trust then stuck [y/n] did not send y"
# Enter, y, Enter — the last Enter is the one that submits [y/n].
[ "$(printf '%s\n' "$out" | grep -c 'agent send-keys reviewer Enter')" -ge 2 ] ||
    fail "trust then stuck [y/n] should send Enter twice"

# A [y/n] that does not submit on y still needs Enter.
rm -f "$FAKE_READY_FILE" "$FAKE_READ_N" "$FAKE_PANE_NEXT"
: >"$FAKE_HOLD_FILE"
printf '%s\n' 'Start a new chat? [y/n]' >"$FAKE_PANE"
out=$(sh "$root/bin/herdr" agent start reviewer --kind codex --pane w1:p1 2>/dev/null) ||
    fail "a [y/n] that stayed up should recover after Enter"
printf '%s\n' "$out" | grep -q 'agent send-keys reviewer y' ||
    fail "stuck [y/n] did not send y first"
printf '%s\n' "$out" | grep -q 'agent send-keys reviewer Enter' ||
    fail "stuck [y/n] did not send Enter after y"

# The dialog may still be painting when Herdr first reports blocked.
rm -f "$FAKE_READY_FILE" "$FAKE_READ_N" "$FAKE_HOLD_FILE"
printf '%s\n' 'codex is starting...' >"$FAKE_PANE_DELAY"
printf '%s\n' '> You are in /tmp/demo' \
    'Do you trust the contents of this directory?' \
    '› 1. Yes, continue' >"$FAKE_PANE"
out=$(sh "$root/bin/herdr" agent start reviewer --kind codex --pane w1:p1 2>/dev/null) ||
    fail "a delayed trust dialog should still recover"
printf '%s\n' "$out" | grep -q 'agent send-keys reviewer Enter' ||
    fail "a delayed trust dialog did not send Enter"
rm -f "$FAKE_PANE_DELAY"

# A successful start must not send keys. Codex argv is logged one token
# per line so a glued string cannot fake the unattended-flag position.
export FAKE_START_OK=1
codex_start_head='<agent>
<start>
<reviewer>
<--kind>
<codex>
<--pane>
<w1:p1>
<-->
<--dangerously-bypass-approvals-and-sandbox>'
check_start_log() {
    printf '%s\n' "$1" >"$fake_start/want"
    if ! diff -u "$fake_start/want" "$FAKE_START_LOG"; then
        fail "$2"
    fi
}
rm -f "$FAKE_READY_FILE" "$FAKE_START_LOG"
out=$(sh "$root/bin/herdr" agent start reviewer --kind codex --pane w1:p1) ||
    fail "a successful Codex start should pass through"
if printf '%s\n' "$out" | grep -q 'agent send-keys'; then
    fail "a successful start must not send keys"
fi
check_start_log "$codex_start_head" \
    "a Codex start that omitted the unattended flag must grow -- and the flag"
rm -f "$FAKE_START_LOG"
out=$(sh "$root/bin/herdr" agent start reviewer --kind codex --pane w1:p1 \
    --) ||
    fail "a Codex start with a trailing -- should pass through"
check_start_log "$codex_start_head" \
    "a trailing -- must still receive the unattended flag after --"
rm -f "$FAKE_START_LOG"
out=$(sh "$root/bin/herdr" agent start reviewer --kind codex --pane w1:p1 \
    -- --dangerously-bypass-approvals-and-sandbox) ||
    fail "a Codex start that already has the unattended flag should pass through"
check_start_log "$codex_start_head" \
    "a Codex start must not duplicate the unattended flag"
rm -f "$FAKE_START_LOG"
out=$(sh "$root/bin/herdr" agent start reviewer --kind claude --pane w1:p1) ||
    fail "a successful Claude start should pass through"
if grep -qF -- '--dangerously-bypass-approvals-and-sandbox' "$FAKE_START_LOG"; then
    fail "a Claude start must not receive the Codex unattended flag"
fi
if printf '%s\n' "$out" | grep -q 'agent send-keys'; then
    fail "a successful Claude start must not send keys"
fi
rm -f "$FAKE_START_LOG"
out=$(sh "$root/bin/herdr" agent start reviewer --kind codex --pane w1:p1 \
    -- resume --last) ||
    fail "a Codex resume start should pass through"
check_start_log "$codex_start_head
<resume>
<--last>" \
    "a Codex resume must get the unattended flag before the subcommand"
rm -f "$FAKE_START_LOG"
out=$(sh "$root/bin/herdr" agent start reviewer --kind codex --pane w1:p1 \
    -- resume --last --dangerously-bypass-approvals-and-sandbox) ||
    fail "a Codex resume with a trailing unattended flag should pass through"
check_start_log "$codex_start_head
<resume>
<--last>" \
    "a misplaced unattended flag after resume must move to after --"
rm -f "$FAKE_START_LOG"
out=$(sh "$root/bin/herdr" agent start reviewer --kind codex --pane w1:p1 \
    -- fork --last) ||
    fail "a Codex fork start should pass through"
check_start_log "$codex_start_head
<fork>
<--last>" \
    "a Codex fork must get the unattended flag before the subcommand"
rm -f "$FAKE_START_LOG"
out=$(sh "$root/bin/herdr" agent start reviewer --kind codex --pane w1:p1 \
    -- -m gpt-5.6-sol review --base main) ||
    fail "a Codex review start should pass through"
check_start_log "$codex_start_head
<-m>
<gpt-5.6-sol>
<review>
<--base>
<main>" \
    "a Codex review must get the unattended flag before the subcommand"
unset FAKE_START_OK

# The Codex dialog on a Claude seat is somebody else's gate.
rm -f "$FAKE_READY_FILE"
printf '%s\n' '> You are in /tmp/demo' \
    'Do you trust the contents of this directory?' \
    '› 1. Yes, continue' >"$FAKE_PANE"
if out=$(sh "$root/bin/herdr" agent start reviewer --kind claude --pane w1:p1 2>"$err"); then
    printf '%s\n' "$out" >&2
    fail "a Claude start on the Codex dialog must not be reported as seated"
fi
if printf '%s\n' "$out" | grep -q 'agent send-keys'; then
    fail "a Claude start on the Codex dialog must not send keys into the pane"
fi

# Unrelated start failure, even with a trust dialog on screen.
export FAKE_START_ERR=pane_not_found
rm -f "$FAKE_READY_FILE"
if out=$(sh "$root/bin/herdr" agent start reviewer --kind codex --pane w1:p1 2>"$err"); then
    printf '%s\n' "$out" >&2
    fail "an unrelated start failure must not be reported as seated"
fi
if printf '%s\n' "$out" | grep -q 'agent send-keys'; then
    fail "an unrelated start failure must not send keys"
fi
export FAKE_START_ERR=agent_not_ready

# The named agent is sitting in another pane. Do not type into it.
export FAKE_AGENT_PANE=w2:p9
rm -f "$FAKE_READY_FILE"
if out=$(sh "$root/bin/herdr" agent start reviewer --kind codex --pane w1:p1 2>"$err"); then
    printf '%s\n' "$out" >&2
    fail "a pane mismatch must not be reported as seated"
fi
if printf '%s\n' "$out" | grep -q 'agent send-keys'; then
    fail "a pane mismatch must not send keys into another agent's pane"
fi
export FAKE_AGENT_PANE=w1:p1

# A later permission prompt during startup is not a first-run gate.
rm -f "$FAKE_READY_FILE"
printf '%s\n' 'Allow command?' 'press enter to confirm or esc to cancel' >"$FAKE_PANE"
if out=$(sh "$root/bin/herdr" agent start reviewer --kind codex --pane w1:p1 2>"$err"); then
    printf '%s\n' "$out" >&2
    fail "a later permission prompt must not be auto-dismissed"
fi
if printf '%s\n' "$out" | grep -q 'agent send-keys'; then
    fail "a later permission prompt must not receive keys"
fi

# Kind taken from agent args after -- must not trigger the gate.
rm -f "$FAKE_READY_FILE"
printf '%s\n' 'Start a new chat? [y/n]' >"$FAKE_PANE"
if out=$(sh "$root/bin/herdr" agent start reviewer --kind claude --pane w1:p1 -- --kind codex 2>"$err"); then
    printf '%s\n' "$out" >&2
    fail "a --kind after -- must not trigger the Codex startup gate"
fi
if printf '%s\n' "$out" | grep -q 'agent send-keys'; then
    fail "a --kind after -- must not send keys"
fi

# Enter that never makes the seat interactive_ready is a failed start.
export FAKE_START_DEAF=1
rm -f "$FAKE_READY_FILE"
printf '%s\n' '> You are in /tmp/demo' \
    'Do you trust the contents of this directory?' \
    '› 1. Yes, continue' >"$FAKE_PANE"
if out=$(sh "$root/bin/herdr" agent start reviewer --kind codex --pane w1:p1 2>"$err"); then
    printf '%s\n' "$out" >&2
    fail "a gate that did not become ready must not be reported as seated"
fi
printf '%s\n' "$out" | grep -q 'agent send-keys reviewer Enter' ||
    fail "the unreadied trust gate should still press Enter"
grep -q 'did not become ready' "$err" ||
    fail "an unreadied Codex seat should say why"
unset FAKE_START_DEAF

# Claude first-run folder trust, same shape as Codex: only that screen,
# only the named start pane, one Enter, then wait for a ready seat.
claude_trust_pane() {
    printf '%s\n' 'Accessing workspace' \
        '/tmp/demo' \
        '› 1. Yes, I trust this folder' \
        '  2. No, choose another folder' \
        'Enter to confirm' >"$FAKE_PANE"
}
export FAKE_GET_AGENT=claude
rm -f "$FAKE_READY_FILE" "$FAKE_READ_N"
claude_trust_pane
out=$(sh "$root/bin/herdr" agent start reviewer --kind claude --pane w1:p1 2>/dev/null) ||
    fail "Claude folder trust gate should recover after Enter"
printf '%s\n' "$out" | grep -q 'agent send-keys reviewer Enter' ||
    fail "Claude folder trust gate did not send Enter to the named seat"
printf '%s\n' "$out" | grep -q 'agent wait reviewer --until idle' ||
    fail "Claude folder trust gate did not wait until idle"
[ "$(printf '%s\n' "$out" | grep -c 'agent send-keys')" -eq 1 ] ||
    fail "Claude folder trust gate should send exactly one key"

# The dialog may still be painting when Herdr first reports blocked.
rm -f "$FAKE_READY_FILE" "$FAKE_READ_N"
printf '%s\n' 'claude is starting...' >"$FAKE_PANE_DELAY"
claude_trust_pane
out=$(sh "$root/bin/herdr" agent start reviewer --kind claude --pane w1:p1 2>/dev/null) ||
    fail "a delayed Claude trust screen should still recover"
printf '%s\n' "$out" | grep -q 'agent send-keys reviewer Enter' ||
    fail "a delayed Claude trust screen did not send Enter"
rm -f "$FAKE_PANE_DELAY"

# A later Claude permission prompt during startup is not a first-run gate.
rm -f "$FAKE_READY_FILE" "$FAKE_READ_N"
printf '%s\n' 'Allow command?' 'press enter to confirm or esc to cancel' >"$FAKE_PANE"
if out=$(sh "$root/bin/herdr" agent start reviewer --kind claude --pane w1:p1 2>"$err"); then
    printf '%s\n' "$out" >&2
    fail "a later Claude permission prompt must not be auto-dismissed"
fi
if printf '%s\n' "$out" | grep -q 'agent send-keys'; then
    fail "a later Claude permission prompt must not receive keys"
fi

# A new-chat confirm is a Codex gate. Claude seats leave it alone.
rm -f "$FAKE_READY_FILE" "$FAKE_READ_N"
printf '%s\n' 'Start a new chat? [y/n]' >"$FAKE_PANE"
if out=$(sh "$root/bin/herdr" agent start reviewer --kind claude --pane w1:p1 2>"$err"); then
    printf '%s\n' "$out" >&2
    fail "a [y/n] on a Claude seat must not be reported as seated"
fi
if printf '%s\n' "$out" | grep -q 'agent send-keys'; then
    fail "a Claude seat must not answer a new-chat confirm"
fi

# A Codex occupant on the Claude trust screen is another agent's pane.
export FAKE_GET_AGENT=codex
rm -f "$FAKE_READY_FILE" "$FAKE_READ_N"
claude_trust_pane
if out=$(sh "$root/bin/herdr" agent start reviewer --kind claude --pane w1:p1 2>"$err"); then
    printf '%s\n' "$out" >&2
    fail "a Codex occupant must not be reported as a Claude seat"
fi
if printf '%s\n' "$out" | grep -q 'agent send-keys'; then
    fail "a Codex occupant must not receive Claude startup keys"
fi
export FAKE_GET_AGENT=claude

# The named agent is sitting in another pane. Do not type into it.
export FAKE_AGENT_PANE=w2:p9
rm -f "$FAKE_READY_FILE" "$FAKE_READ_N"
claude_trust_pane
if out=$(sh "$root/bin/herdr" agent start reviewer --kind claude --pane w1:p1 2>"$err"); then
    printf '%s\n' "$out" >&2
    fail "a Claude pane mismatch must not be reported as seated"
fi
if printf '%s\n' "$out" | grep -q 'agent send-keys'; then
    fail "a Claude pane mismatch must not send keys into another agent's pane"
fi
export FAKE_AGENT_PANE=w1:p1

# Unrelated start failure, even with the folder trust screen on show.
export FAKE_START_ERR=pane_not_found
rm -f "$FAKE_READY_FILE" "$FAKE_READ_N"
claude_trust_pane
if out=$(sh "$root/bin/herdr" agent start reviewer --kind claude --pane w1:p1 2>"$err"); then
    printf '%s\n' "$out" >&2
    fail "an unrelated Claude start failure must not be reported as seated"
fi
if printf '%s\n' "$out" | grep -q 'agent send-keys'; then
    fail "an unrelated Claude start failure must not send keys"
fi
export FAKE_START_ERR=agent_not_ready

# Enter that never makes the seat interactive_ready is a failed start.
export FAKE_START_DEAF=1
rm -f "$FAKE_READY_FILE" "$FAKE_READ_N"
claude_trust_pane
if out=$(sh "$root/bin/herdr" agent start reviewer --kind claude --pane w1:p1 2>"$err"); then
    printf '%s\n' "$out" >&2
    fail "a Claude gate that did not become ready must not be reported as seated"
fi
printf '%s\n' "$out" | grep -q 'agent send-keys reviewer Enter' ||
    fail "the unreadied Claude trust gate should still press Enter"
grep -q 'did not become ready' "$err" ||
    fail "an unreadied Claude seat should say why"
unset FAKE_START_DEAF
export FAKE_GET_AGENT=codex
rm -f "$FAKE_READY_FILE" "$FAKE_READ_N"

unset FAKE_PANE
unset FAKE_PANE_NEXT
unset FAKE_PANE_DELAY
unset FAKE_PANE_AFTER
unset FAKE_SWAP_AT
unset FAKE_READ_N
unset FAKE_HOLD_FILE
unset FAKE_READY_FILE
unset FAKE_AGENT_PANE
unset FAKE_GET_AGENT
unset FAKE_GET_STATUS
unset FAKE_START_ERR
unset FAKE_START_OK
unset HERDR_REAL
rm -rf "$fake_start"
fake_start=

# Windows: bin/herdr has no file extension, so a native caller resolving
# `herdr` on PATH skips it under PATHEXT and reaches the real herdr.exe. The
# .cmd sibling is what that caller finds instead. Nothing here runs elsewhere.
#
# `cmd.exe //c` is not a typo. MSYS rewrites a lone `/c` argument into the C:
# drive path before cmd.exe sees it, and cmd.exe then starts interactively.
# The doubled slash survives the rewrite as a single one.
if [ -f "$root/bin/herdr.cmd" ] &&
    command -v cygpath >/dev/null 2>&1 &&
    command -v cmd.exe >/dev/null 2>&1; then
    win_cmd=$(cygpath -w "$root/bin/herdr.cmd")

    fake_cmd=$(mktemp -d)
    cat >"$fake_cmd/herdr" <<'EOF'
#!/bin/sh
printf 'real herdr %s\n' "$*"
exit 7
EOF
    chmod +x "$fake_cmd/herdr"
    export HERDR_REAL="$fake_cmd/herdr"

    # A mutating verb is blocked, and the block reaches the caller as a
    # non-zero status with the hint on stderr.
    export HERDR_HELPER_OK=
    if cmd.exe //c "$win_cmd" workspace create --cwd C:\\Temp --label x \
        >/dev/null 2>"$err"; then
        fail "herdr.cmd mutate without OK should fail"
    fi
    grep -q HERDR_HELPER_OK "$err" || fail "herdr.cmd blocked hint missing"
    if grep -q 'real herdr' "$err"; then
        fail "herdr.cmd blocked path reached the real herdr"
    fi

    # An inspect verb passes through, and the wrapper's exit code survives the
    # trip through cmd.exe. The stub exits 7 so a swallowed code is visible.
    cmd_status=0
    cmd.exe //c "$win_cmd" agent list >"$tmp" 2>/dev/null || cmd_status=$?
    [ "$cmd_status" = 7 ] ||
        fail "herdr.cmd should return the wrapper exit code (got $cmd_status)"
    grep -q 'real herdr agent list' "$tmp" ||
        fail "herdr.cmd inspect did not reach the real herdr"

    # What survives cmd.exe, measured rather than assumed. A lone percent and
    # an ampersand inside a quoted argument both arrive intact. A %NAME% that
    # names a defined variable does not, and no shim can stop that: cmd.exe
    # expands it while parsing its own command line, before the batch file
    # runs. Agents under Lantern call the POSIX wrapper through Git Bash,
    # which is unaffected.
    cmd.exe //c "$win_cmd" agent list "50% off & more" >"$tmp" 2>/dev/null || true
    grep -q '50% off & more' "$tmp" ||
        fail "herdr.cmd mangled a percent or ampersand in a quoted argument"

    # With no sh.exe anywhere, it refuses. It must never fall through to the
    # real herdr, because that is the gate-free path this file exists to close.
    # The scrubbed environment goes in its own batch file. Passing it as one
    # long `cmd.exe //c "set ... && ..."` string does not survive: MSYS escapes
    # the embedded quotes to \" on the way to a native program, and cmd.exe
    # reports the command as unrecognised.
    nowhere=$win_cmd.nowhere
    no_sh_cmd=$fake_cmd/no-sh.cmd
    cat >"$no_sh_cmd" <<EOF
@echo off
set "PATH=C:\\Windows\\System32"
set "ProgramFiles=$nowhere"
set "ProgramFiles(x86)=$nowhere"
set "LOCALAPPDATA=$nowhere"
call "$win_cmd" workspace create --cwd C:\\Temp --label x
exit /b %ERRORLEVEL%
EOF
    if cmd.exe //c "$(cygpath -w "$no_sh_cmd")" >"$tmp" 2>"$err"; then
        fail "herdr.cmd without sh.exe should fail"
    fi
    grep -q 'refusing to run herdr' "$err" ||
        fail "herdr.cmd without sh.exe should say why"
    if grep -q 'real herdr' "$tmp" "$err"; then
        fail "herdr.cmd without sh.exe must not reach the real herdr"
    fi

    # Git Bash still matches the exact name, so the POSIX wrapper stays in
    # front of the agent's own calls.
    resolved=$(PATH="$root/bin:$PATH" command -v herdr)
    [ "$resolved" = "$root/bin/herdr" ] ||
        fail "Git Bash should resolve herdr to the POSIX wrapper (got $resolved)"

    unset HERDR_REAL
    export HERDR_HELPER_OK=
    rm -rf "$fake_cmd"
    fake_cmd=
    printf 'ok: windows herdr.cmd gate\n'
fi

smoke_python=$(helper_detect_python) || fail "no working python 3 on this machine"
# shellcheck disable=SC2086
$smoke_python -c 'import sys; raise SystemExit(0 if sys.version_info[0] == 3 else 1)' ||
    fail "helper_detect_python returned something that is not python 3"

# Windows ships a zero-byte Microsoft Store alias named python3. It satisfies
# `command -v` and then opens the Store. Detection must refuse it, either by
# choosing another interpreter or by failing outright.
#
# The stub needs the .exe suffix on Windows. MSYS decides that a file is
# executable from its extension or its first bytes, so an extensionless empty
# file is not executable there and would never shadow anything. The real Store
# alias is python3.exe, which is exactly why it fools `command -v`.
fake_py=$(mktemp -d)
if command -v cygpath >/dev/null 2>&1; then
    stub_python=$fake_py/python3.exe
else
    stub_python=$fake_py/python3
fi
: >"$stub_python"
chmod +x "$stub_python"
stub_pick=$(PATH="$fake_py:$PATH"; export PATH; helper_detect_python) || stub_pick=
if [ "$stub_pick" = python3 ]; then
    fail "helper_detect_python accepted a zero-byte python3 stub"
fi
rm -rf "$fake_py"
fake_py=

# The Windows launcher branch. `py` is not a plain file on PATH the way an
# interpreter is, so it gets its own check, and it is the only fallback left
# when a Store alias shadows python3 and no python exists.
if command -v py >/dev/null 2>&1; then
    launcher_dir=$(dirname "$(command -v py)")
    picked=$(PATH="$launcher_dir"; export PATH; helper_detect_python) || picked=
    [ "$picked" = "py -3" ] ||
        fail "detection should fall back to the py launcher (got $picked)"
    # shellcheck disable=SC2086
    $picked -c 'import sys; raise SystemExit(0 if sys.version_info[0] == 3 else 1)' ||
        fail "the py launcher fallback returned something that is not python 3"
fi

# shellcheck disable=SC2086
$smoke_python "$root/bin/elves-floor" --root /tmp >/dev/null || fail "elves-floor"

# An explicit --root has to be the whole search. The scan used to append
# ~/aigora on top of whatever the caller asked for, so a scoped run reported
# sessions from outside its root and walked the user's tree every time.
#
# The bait sits under this run's own HOME, because ~/aigora is precisely what
# the old append reached for. An empty root alone proves nothing: on a machine
# with no ~/aigora — every CI runner — the old scan also answered
# elves_detected 0, and the regression this case exists for went unguarded in
# the one place it would show up first. USERPROFILE is set as well; that is
# what Path.home() reads on Windows.
elves_tmp=$(mktemp -d)
mkdir -p "$elves_tmp/home/aigora/outside" "$elves_tmp/scan"
printf '%s' '{"status":"executing","batches":[{"id":"B1","status":"open"}]}' \
    >"$elves_tmp/home/aigora/outside/.elves-session.json"
# shellcheck disable=SC2086
HOME="$elves_tmp/home" USERPROFILE="$elves_tmp/home" \
    $smoke_python "$root/bin/elves-floor" --root "$elves_tmp/scan" >"$tmp" 2>&1 ||
    fail "elves-floor --root should still run"
if grep -q 'outside' "$tmp"; then
    cat "$tmp" >&2
    fail "elves-floor --root reported a session from outside its root"
fi
grep -q 'elves_detected 0' "$tmp" ||
    fail "elves-floor --root should scan only the roots it was given"
rm -rf "$elves_tmp/home"

# One session file with one byte that is not UTF-8 used to end the whole
# scan: load_session caught OSError and JSONDecodeError, and read_text raises
# UnicodeDecodeError before either of those can happen. A file nobody can read
# is skipped, and every other run on the floor still gets reported.
mkdir -p "$elves_tmp/broken" "$elves_tmp/live"
printf '{"status":"exec\377uting","batches":[]}' \
    >"$elves_tmp/broken/.elves-session.json"
printf '%s' '{"status":"executing","batches":[{"id":"B1","status":"open"}]}' \
    >"$elves_tmp/live/.elves-session.json"
# shellcheck disable=SC2086
$smoke_python "$root/bin/elves-floor" --root "$elves_tmp" >"$tmp" 2>&1 ||
    fail "one unreadable session file should not end the elves scan"
grep -q 'elves_detected 1' "$tmp" ||
    fail "the readable session should still be reported"
grep -q -- '- live —' "$tmp" ||
    fail "elves-floor should still list the run it could read"
rm -rf "$elves_tmp/broken" "$elves_tmp/live"

# A session file sitting exactly at the depth limit counts. Only the descent
# past that directory stops.
mkdir -p "$elves_tmp/a/b"
printf '%s' '{"status":"executing","batches":[{"id":"B1","status":"open"}]}' \
    >"$elves_tmp/a/b/.elves-session.json"
# shellcheck disable=SC2086
$smoke_python "$root/bin/elves-floor" --root "$elves_tmp" --max-depth 2 |
    grep -q 'elves_detected 1' ||
    fail "elves-floor should read a session at exactly --max-depth"
rm -rf "$elves_tmp"
elves_tmp=

# shellcheck disable=SC2086
$smoke_python -m py_compile "$root/bin/elves-floor" "$root/bin/goals-floor" ||
    fail "py_compile"

grep -q 'helper_detect_python' "$root/launch.sh" ||
    fail "launch.sh should use the detected interpreter"
if grep -qE '^[^#]*\bpython3 "\$plugin_root' "$root/launch.sh"; then
    fail "launch.sh still calls python3 directly"
fi

fake=$(mktemp -d)
printf '%s\n' \
    '{"result":{"agents":[{"pane_id":"w1:p1","agent":"claude","agent_status":"done","cwd":"/tmp/demo","terminal_title_stripped":"Fix login"}]}}' \
    >"$fake/list.json"
# Non-ASCII on purpose. goals-floor reads real pane text, which carries box
# drawing, arrows, and emoji, and it must not decode that with a code page.
printf '%s\n' \
    '※ recap: Goal: fix login. Next: your go-ahead to merge #12.' \
    '◎ /goal active (2h)' \
    >"$fake/read.txt"

# goals-floor runs the herdr binary itself, so on Windows the stub has to be a
# native batch file: Python there cannot execute a script by its shebang. Both
# stubs just print the fixtures above, so neither has to quote JSON or Unicode.
if command -v cygpath >/dev/null 2>&1 && command -v cmd.exe >/dev/null 2>&1; then
    fake_herdr=$fake/herdr.cmd
    {
        printf '@echo off\r\n'
        printf 'if "%%2"=="list" (\r\n'
        printf '  type "%s"\r\n' "$(cygpath -w "$fake/list.json")"
        printf '  exit /b 0\r\n'
        printf ')\r\n'
        printf 'if "%%2"=="read" (\r\n'
        printf '  type "%s"\r\n' "$(cygpath -w "$fake/read.txt")"
        printf '  exit /b 0\r\n'
        printf ')\r\n'
        printf 'exit /b 1\r\n'
    } >"$fake_herdr"
    fake_herdr_arg=$(cygpath -w "$fake_herdr")
else
    fake_herdr=$fake/herdr
    cat >"$fake_herdr" <<EOF
#!/bin/sh
if [ "\$1" = agent ] && [ "\$2" = list ]; then
    cat "$fake/list.json"
    exit 0
fi
if [ "\$1" = agent ] && [ "\$2" = read ]; then
    cat "$fake/read.txt"
    exit 0
fi
exit 1
EOF
    chmod +x "$fake_herdr"
    fake_herdr_arg=$fake_herdr
fi
# shellcheck disable=SC2086
goals=$($smoke_python "$root/bin/goals-floor" --herdr "$fake_herdr_arg") || fail "goals-floor"
printf '%s\n' "$goals" | grep -q 'herd_detected 1' || fail "goals-floor herd_detected"
printf '%s\n' "$goals" | grep -q 'NEEDS YOU' || fail "goals-floor needs you"
printf '%s\n' "$goals" | grep -q '/goal 2h' || fail "goals-floor goal age"

# launch.sh builds a different command line for each helper CLI, and until now
# nothing checked any of them. Only `claude` exists on the machine this was
# ported on, so Cursor agent, Devin, Codex, and Grok had no coverage at all.
#
# This runs launch.sh for real against stub binaries. HOME points at an empty
# directory so helper_extend_user_path cannot find a genuine CLI, and
# HERDR_BIN_PATH points at a stub so no live herdr is touched.
#
# The stubs live in $HOME/.local/bin and that directory is deliberately left
# out of the PATH handed to launch.sh. helper_extend_user_path is what puts it
# there, and it prepends /usr/local/bin and /opt/homebrew/bin first. Handing
# the stub dir in through PATH is worse than useless: helper_prepend_path skips
# a directory that is already on PATH, so the stub dir keeps its inherited
# position and the Homebrew dirs land in front of it. A machine with a real
# grok or agent then runs that instead of the stub, and the ARGV line never
# appears. Let the function place the dir, and only $HOME/.grok/bin outranks
# it — absent under this throwaway home.
argv_dir=$(mktemp -d)
argv_home=$argv_dir/home
argv_bin=$argv_home/.local/bin
mkdir -p "$argv_bin" "$argv_dir/config" "$argv_dir/state"
for stub_bin in agent devin claude codex grok herdr; do
    printf '%s\n' '#!/bin/sh' 'printf "ARGV:"' 'for a in "$@"; do printf " [%s]" "$a"; done' \
        'printf "\n"' 'exit 0' >"$argv_bin/$stub_bin"
    chmod +x "$argv_bin/$stub_bin"
done

run_launch() {
    # $1 is the helper.conf body. Prints the stub's ARGV line. The update
    # URL points at a file that does not exist so no launch here waits on
    # the network; the fetch fails at once and the snapshot says so.
    printf '%s\n' "$1" >"$argv_dir/config/helper.conf"
    rm -rf "$argv_dir/state"
    mkdir -p "$argv_dir/state"
    env -i \
        HOME="$argv_home" \
        PATH="/usr/bin:/bin" \
        LANTERN_UPDATE_URL="file:///nonexistent-lantern-update" \
        HERDR_PLUGIN_ROOT="$root" \
        HERDR_PLUGIN_CONFIG_DIR="$argv_dir/config" \
        HERDR_PLUGIN_STATE_DIR="$argv_dir/state" \
        HERDR_BIN_PATH="$argv_bin/herdr" \
        sh "$root/launch.sh" </dev/null 2>/dev/null |
        grep '^ARGV:' | tail -1
}

argv_is() {
    # $1 label, $2 conf body, $3 expected leading arguments
    _argv_got=$(run_launch "$2")
    case $_argv_got in
    "ARGV:$3"*) ;;
    *)
        printf 'want prefix: ARGV:%s\n' "$3" >&2
        printf 'got:         %s\n' "$_argv_got" >&2
        fail "launch.sh argv for $1"
        ;;
    esac
}

argv_is "claude" 'HELPER_AGENT="claude"
HELPER_MODEL="opus"
HELPER_EFFORT="high"
HELPER_CWD="~"' ' [--model] [opus] [--effort] [high]'

argv_is "codex" 'HELPER_AGENT="codex"
HELPER_MODEL="gpt-x"
HELPER_EFFORT="high"
HELPER_CWD="~"' ' [--model] [gpt-x] [--config] [model_reasoning_effort="high"] [--dangerously-bypass-approvals-and-sandbox]'

argv_is "grok" 'HELPER_AGENT="grok"
HELPER_MODEL="grok-x"
HELPER_EFFORT="high"
HELPER_CWD="~"' ' [--model] [grok-x] [--reasoning-effort] [high] [--no-subagents]'

# Devin rejects --model on the free tier, so launch.sh drops it on purpose.
argv_is "devin" 'HELPER_AGENT="devin"
HELPER_MODEL="ignored"
HELPER_PERMISSION="smart"
HELPER_CWD="~"' ' [--permission-mode] [smart]'

# Cursor agent: an empty model means the documented default, and smart maps to
# --auto-review. The binary is called agent, whichever name the config used.
argv_is "cursor agent" 'HELPER_AGENT="agent"
HELPER_MODEL=""
HELPER_PERMISSION="smart"
HELPER_CWD="~"' ' [--model] [cursor-grok-4.6-high-fast] [--trust] [--sandbox] [disabled] [--auto-review]'

argv_is "cursor alias" 'HELPER_AGENT="cursor"
HELPER_MODEL="m"
HELPER_PERMISSION="dangerous"
HELPER_CWD="~"' ' [--model] [m] [--trust] [--sandbox] [disabled] [--force]'

argv_is "extra args" 'HELPER_AGENT="claude"
HELPER_MODEL=""
HELPER_EXTRA_ARGS="--verbose --foo"
HELPER_CWD="~"' ' [--verbose] [--foo]'

printf 'ok: launch.sh argv for every helper CLI\n'

# A snapshot that cannot be refreshed must not be left behind. prompt.md has
# the lantern read these files at light-up and lead with who needs the user,
# so last run's field would be reported as this one.
#
# The stubs go in $HOME/.grok/bin because helper_extend_user_path prepends
# that last, which puts it ahead of the real /opt/homebrew/bin. Handing them
# in through PATH would not work: this machine has a genuine python3 in one of
# the directories that function puts in front. Non-zero on purpose — that is
# the one answer that is neither a Microsoft Store alias nor a working python.
nopy_dir=$argv_home/.grok/bin
mkdir -p "$nopy_dir"
for stub_bin in python3 python py; do
    printf '%s\n' '#!/bin/sh' 'exit 1' >"$nopy_dir/$stub_bin"
    chmod +x "$nopy_dir/$stub_bin"
done
rm -rf "$argv_dir/state"
mkdir -p "$argv_dir/state/workdir"
stale_workdir=$argv_dir/state/workdir
printf '%s\n' 'NEEDS YOU' '- stale-agent — waiting on you since the last run' \
    >"$stale_workdir/goals-floor.txt"
printf '%s\n' 'elves_detected 1' 'IN PROGRESS' '- stale-run — 2/5 open B3' \
    >"$stale_workdir/elves-floor.txt"
printf '%s\n' 'HELPER_AGENT="claude"' 'HELPER_CWD="~"' \
    >"$argv_dir/config/helper.conf"
env -i \
    HOME="$argv_home" \
    PATH="/usr/bin:/bin" \
    LANTERN_UPDATE_URL="file:///nonexistent-lantern-update" \
    HERDR_PLUGIN_ROOT="$root" \
    HERDR_PLUGIN_CONFIG_DIR="$argv_dir/config" \
    HERDR_PLUGIN_STATE_DIR="$argv_dir/state" \
    HERDR_BIN_PATH="$argv_bin/herdr" \
    sh "$root/launch.sh" </dev/null >/dev/null 2>&1 ||
    fail "launch.sh should still start with no interpreter on PATH"
for stale_file in goals-floor elves-floor; do
    if grep -q 'stale-' "$stale_workdir/$stale_file.txt"; then
        fail "launch.sh left last run's $stale_file.txt in place"
    fi
    grep -q 'snapshot unavailable' "$stale_workdir/$stale_file.txt" ||
        fail "$stale_file.txt should say why it could not be refreshed"
done
# The version check writes its file on the same rule: a fetch that failed is
# said out loud rather than last run's answer surviving in place.
grep -q 'update check unavailable' "$stale_workdir/update.txt" ||
    fail "launch.sh should write update.txt even when the check fails"
# And the rendered appendix names what this chat runs, so the light-up line
# can say which CLI and model is answering.
grep -q 'This chat runs claude' "$stale_workdir/CLAUDE.md" ||
    fail "the rendered appendix should name what the chat runs"
printf 'ok: launch.sh replaces a snapshot it cannot refresh\n'

rm -rf "$argv_dir"
argv_dir=

# The update offer starts from one honest line in update.txt, and these are
# its rules: versions compare numerically per field, a dev checkout ahead of
# main is never offered a downgrade, a linked checkout is told apart from a
# GitHub install (there is no `herdr plugin update`, and reinstalling over a
# link would orphan it), and a fetch that failed says so.
helper_version_gt 0.7.0 0.6.9 || fail "0.7.0 should be newer than 0.6.9"
helper_version_gt 0.10.0 0.9.9 ||
    fail "0.10.0 should be newer than 0.9.9 (numeric, not lexicographic)"
helper_version_gt 1.0.0 0.99.99 || fail "1.0.0 should be newer than 0.99.99"
if helper_version_gt 0.6.0 0.6.0; then
    fail "equal versions are not an update"
fi
if helper_version_gt 0.6.0 0.7.0; then
    fail "an older version is not an update"
fi
if helper_version_gt main 0.6.0; then
    fail "a non-numeric version is never an update"
fi
# Short and long versions are refused whole, before any field compares.
# "0.9" used to beat "0.8.0" on its second field, with the missing third
# never looked at — a manifest mid-edit read as an update.
if helper_version_gt 0.9 0.8.0; then
    fail "a two-field version is never an update"
fi
if helper_version_gt 9 0.8.0; then
    fail "a one-field version is never an update"
fi
if helper_version_gt 1.2.3.4 1.2.3; then
    fail "a four-field version is never an update"
fi
if helper_version_gt 0.9.0 0.8; then
    fail "a two-field installed version never reads as behind"
fi

update_dir=$(mktemp -d)
# The root sits where Herdr keeps GitHub installs, because that path is the
# install-kind signal: Herdr clones GitHub installs, so a .git proves
# nothing, and the real install on a real machine carries one.
mkdir -p "$update_dir/plugins/github/aigora.lantern-abc123" "$update_dir/bin"
update_root=$update_dir/plugins/github/aigora.lantern-abc123
printf '%s\n' 'version = "0.7.0"' >"$update_root/herdr-plugin.toml"
# A curl that answers from a fixture, or fails the way a dead network does.
cat >"$update_dir/bin/curl" <<'EOF'
#!/bin/sh
[ -n "${FAKE_MANIFEST:-}" ] || exit 22
cat "$FAKE_MANIFEST"
EOF
chmod +x "$update_dir/bin/curl"
export FAKE_MANIFEST=

run_update_snapshot() {
    # $1 is the plugin root to classify and check.
    (
        PATH="$update_dir/bin:$PATH"
        export PATH
        helper_update_snapshot "$1" "$update_dir/update.txt"
    )
    cat "$update_dir/update.txt"
}

printf '%s\n' 'version = "0.8.0"' >"$update_dir/manifest.toml"
FAKE_MANIFEST=$update_dir/manifest.toml
update_out=$(run_update_snapshot "$update_root")
[ "$update_out" = 'update available: v0.8.0 is published, this is v0.7.0 (GitHub install)' ] ||
    fail "update snapshot for a newer publish (got $update_out)"

# The confirmed real-world layout: Herdr's GitHub install carries a .git.
# It must still be named a GitHub install, or the one refresh path that
# works for it is never offered.
mkdir -p "$update_root/.git"
update_out=$(run_update_snapshot "$update_root")
case $update_out in
*'(GitHub install)') ;;
*) fail "a cloned GitHub install must not be called a checkout (got $update_out)" ;;
esac
rm -rf "$update_root/.git"

# A root outside Herdr's managed directory is somebody's checkout, with or
# without a .git: offering plugin install over a linked plugin would
# conflict with the link, so the cautious answer is the only safe one.
mkdir -p "$update_dir/checkout"
cp "$update_root/herdr-plugin.toml" "$update_dir/checkout/"
update_out=$(run_update_snapshot "$update_dir/checkout")
case $update_out in
*'(linked checkout)') ;;
*) fail "a root outside plugins/github is a linked checkout (got $update_out)" ;;
esac

printf '%s\n' 'version = "0.7.0"' >"$update_dir/manifest.toml"
update_out=$(run_update_snapshot "$update_root")
[ "$update_out" = 'up to date: v0.7.0 (GitHub install)' ] ||
    fail "update snapshot for the same version (got $update_out)"

printf '%s\n' 'version = "0.6.0"' >"$update_dir/manifest.toml"
update_out=$(run_update_snapshot "$update_root")
[ "$update_out" = 'up to date: v0.7.0 (GitHub install)' ] ||
    fail "an older publish is not an update (got $update_out)"

FAKE_MANIFEST=
update_out=$(run_update_snapshot "$update_root")
case $update_out in
'update check unavailable:'*) ;;
*) fail "a failed fetch should say unavailable (got $update_out)" ;;
esac
unset FAKE_MANIFEST
rm -rf "$update_dir"
update_dir=

# The wiring: launch.sh writes the snapshot at light-up, and prompt.md turns
# it into an offer that asks — naming the install command that refreshes a
# GitHub install and the linked checkout it must never reinstall over.
grep -q 'helper_update_snapshot "\$plugin_root" "\$workdir/update.txt"' \
    "$root/launch.sh" ||
    fail "launch.sh should write the update snapshot at light-up"
for update_word in 'update.txt' 'plugin install aigorahub/herdr-lantern' \
    'linked checkout'; do
    grep -qF -- "$update_word" "$root/prompt.md" ||
        fail "prompt.md update offer does not mention $update_word"
    grep -qF -- "$update_word" "$root/launch.sh" ||
        fail "the launch.sh appendix update note does not mention $update_word"
done
printf 'ok: update snapshot and offer wiring\n'

# The chat says what it runs, in the tab and in its first line. The identity
# mirrors launch.sh rather than echoing the conf: Cursor agent's empty model
# is the documented default, Devin's model belongs to Devin's own config,
# and effort only reaches the CLIs launch.sh passes it to.
ident=$(helper_chat_identity claude opus high)
[ "$ident" = 'claude · opus · high' ] || fail "identity claude (got $ident)"
ident=$(helper_chat_identity claude '' '')
[ "$ident" = claude ] || fail "identity bare claude (got $ident)"
ident=$(helper_chat_identity agent '' '')
[ "$ident" = "cursor agent · $(helper_cursor_default_model)" ] ||
    fail "identity cursor default model (got $ident)"
ident=$(helper_chat_identity cursor m high)
[ "$ident" = 'cursor agent · m' ] ||
    fail "identity cursor should ignore effort (got $ident)"
ident=$(helper_chat_identity devin opus '')
[ "$ident" = devin ] || fail "identity devin claims no model (got $ident)"
ident=$(helper_chat_identity codex gpt-x high)
[ "$ident" = 'codex · gpt-x · high' ] || fail "identity codex (got $ident)"

# A --model in the extra args lands after the built flags and the later
# flag wins, so it is the model the identity must name.
[ "$(helper_effective_model opus '--verbose --model sonnet-x')" = sonnet-x ] ||
    fail "effective model should follow a --model in the extra args"
[ "$(helper_effective_model opus '--model=sonnet-x --verbose')" = sonnet-x ] ||
    fail "effective model should follow a --model= in the extra args"
[ "$(helper_effective_model opus '--verbose --foo')" = opus ] ||
    fail "extra args without --model keep the conf model"
[ -z "$(helper_effective_model '' '')" ] ||
    fail "no model anywhere is no model"
grep -q 'helper_effective_model' "$root/launch.sh" ||
    fail "launch.sh identity should use the effective model"
grep -q 'helper_effective_model' "$root/open.sh" ||
    fail "open.sh tab label should use the effective model"
grep -q 'helper_agent_takes_effort' "$root/launch.sh" ||
    fail "launch.sh effort flags should share the lib.sh membership"
grep -qF 'This chat runs $chat_identity' "$root/launch.sh" ||
    fail "the launch.sh appendix should name what the chat runs"
grep -qF 'agent_not_ready' "$root/prompt.md" ||
    fail "prompt.md does not describe the Codex startup gate"
grep -qF 'agent_not_ready' "$root/launch.sh" ||
    fail "the launch.sh appendix does not describe the Codex startup gate"
grep -q 'helper_cursor_default_model' "$root/launch.sh" ||
    fail "launch.sh should take the cursor default model from lib.sh"
# And what is supposed to be happening where: after a confirmed seat, each
# instruction surface has the lantern say slug, kind, model-only-if-chosen,
# and the task.
for seat_file in prompt.md launch.sh; do
    grep -qF 'what is running where' "$root/$seat_file" ||
        fail "$seat_file does not have the lantern announce a seat"
done
grep -qF 'tab rename' "$root/prompt.md" ||
    fail "prompt.md should rename a seated agent's tab"
printf 'ok: the chat and its seats say what they run\n'

# Field status is the whole field. Light-up and "what's going on" used to
# name only the panes that needed the user or were moving, so a quiet tab
# was invisible in a chat that claims to light the field. Every instruction
# surface must now name every open tab, and both prompt.md and the rendered
# appendix carry the same rule, because a lantern seated on Cursor or Codex
# reads the appendix copy and never the file in this repo.
grep -qF 'Field status: name every tab' "$root/prompt.md" ||
    fail "prompt.md has no field status section"
for field_file in prompt.md launch.sh; do
    for field_word in 'herdr tab list' 'herdr agent list' \
        'herdr workspace list' 'workspace label' 'tab label'; do
        grep -qF -- "$field_word" "$root/$field_file" ||
            fail "$field_file field status does not name $field_word"
    done
    # The sidebar name is the point: elves-run, chrome, and a second lantern
    # tab in the same workspace are what the user reads in Herdr, and a
    # per-workspace roll-up would drop the second one.
    for field_example in 'elves-run' 'chrome' 'lantern · 2'; do
        grep -qF -- "$field_example" "$root/$field_file" ||
            fail "$field_file field status does not keep the sidebar name $field_example"
    done
    grep -qF 'Two tabs in one' "$root/$field_file" ||
        fail "$field_file does not name both tabs in one workspace"
    grep -qiE 'quiet and idle tabs stay|a quiet tab still gets its line' \
        "$root/$field_file" ||
        fail "$field_file drops quiet tabs from the field status"
done
# Who needs the user still comes first, and the answer stays a lamp.
grep -qF 'lead with who needs the user' "$root/launch.sh" ||
    fail "the launch.sh appendix should still lead with who needs the user"
grep -qF 'Then name every open tab' "$root/prompt.md" ||
    fail "the prompt.md light-up should name every open tab after who needs you"
grep -qF 'Keep answers short' "$root/prompt.md" ||
    fail "prompt.md should still keep answers short"
for field_file in prompt.md launch.sh; do
    grep -qF 'Sort the tab list by state' "$root/$field_file" ||
        fail "$field_file field status does not sort working tabs above idle"
    grep -qF 'working, then blocked, then done, then idle' "$root/$field_file" ||
        fail "$field_file field status does not name the working-to-idle order"
    grep -qF 'sorts with idle' "$root/$field_file" ||
        fail "$field_file field status does not sort shell tabs with idle"
    grep -qF 'keep the order from' "$root/$field_file" ||
        fail "$field_file field status does not keep herdr tab list order within a state"
    grep -qF 'Do not add group headings' "$root/$field_file" ||
        fail "$field_file field status adds group headings"
done
printf 'ok: field status names every open tab\n'

printf 'ok\n'
