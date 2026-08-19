#!/bin/sh
# Fast checks: syntax, config parse, herdr wrapper gate.
set -eu
root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
cd "$root"

fail() {
    printf 'FAIL: %s\n' "$1" >&2
    exit 1
}

for f in launch.sh open.sh lib.sh bin/herdr hsh tests/smoke.sh; do
    sh -n "$f" || fail "sh -n $f"
done

# shellcheck disable=SC1091
. "$root/lib.sh"

tmp=$(mktemp)
err=$(mktemp)
fake=
fake_prompt=
fake_ws=
fake_cmd=
fake_py=
fake_agents=
open_dir=
trap 'rm -f "$tmp" "$err"; [ -n "$fake" ] && rm -rf "$fake"; [ -n "$fake_prompt" ] && rm -rf "$fake_prompt"; [ -n "$fake_ws" ] && rm -rf "$fake_ws"; [ -n "$fake_cmd" ] && rm -rf "$fake_cmd"; [ -n "$fake_py" ] && rm -rf "$fake_py"; [ -n "$fake_agents" ] && rm -rf "$fake_agents"; [ -n "$open_dir" ] && rm -rf "$open_dir"' EXIT

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
rm -rf "$resolve_dir"

# The plugin must never invoke bare `bash`. On Windows the bash on PATH is the
# WSL launcher in WindowsApps, so that call would start Linux. Herdr runs these
# files with `sh`, and nothing here needs more than that.
if grep -vE '^[[:space:]]*#' "$root/launch.sh" "$root/open.sh" "$root/lib.sh" "$root/bin/herdr" |
    grep -qE '(^|[^A-Za-z_/-])bash([^A-Za-z_]|$)'; then
    fail "plugin shell code must not invoke bare bash"
fi
if grep -q 'WindowsApps' "$root/lib.sh"; then
    fail "lib.sh must not put WindowsApps on PATH"
fi
grep -q 'CLAUDE.md' "$root/launch.sh" || fail "launch writes CLAUDE.md"

grep -q '^placement = "tab"$' "$root/herdr-plugin.toml" || fail "pane placement is tab"
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
    HERDR_REAL="$open_dir/herdr" \
        HERDR_PLUGIN_STATE_DIR="$open_state" \
        HERDR_PLUGIN_ROOT="$root" \
        sh "$root/open.sh" >/dev/null 2>&1
}
logged() { grep -qF -e "$1" "$STUB_LOG"; }

# First open: create the lantern workspace, seat the chat, drop the shell.
reset_open
run_open || fail "first open"
logged 'workspace create --cwd' || fail "first open should create a workspace"
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

if sh "$root/bin/herdr" workspace create --cwd /tmp --label x 2>"$err"; then
    fail "mutate without OK should fail"
fi
grep -q HERDR_HELPER_OK "$err" || fail "blocked hint missing"

export HERDR_HELPER_OK=1
out=$(sh "$root/bin/herdr" workspace create --cwd /tmp --label x) ||
    fail "mutate with OK should pass"
printf '%s\n' "$out" | grep -q 'workspace create' || fail "OK mutate did not exec real herdr"

fake_prompt=$(mktemp -d)
cat >"$fake_prompt/herdr" <<'EOF'
#!/bin/sh
case "$1 $2" in
"agent prompt")
    if [ "$5" = --wait ]; then
        exit 1
    fi
    printf 'agent prompt %s\n' "$3"
    exit 0
    ;;
"agent send-keys")
    printf 'agent send-keys %s %s\n' "$3" "$4"
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
out=$(sh "$root/bin/herdr" agent prompt w1:p1 "hello there") ||
    fail "prompt relay should succeed after Enter fallback"
printf '%s\n' "$out" | grep -q 'agent send-keys w1:p1 Enter' ||
    fail "prompt relay did not send Enter fallback"
printf '%s\n' "$out" | grep -q 'agent wait w1:p1' ||
    fail "prompt relay did not wait after Enter"
unset HERDR_REAL
fake_prompt=

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
fake_py=$(mktemp -d)
: >"$fake_py/python3"
chmod +x "$fake_py/python3"
stub_pick=$(PATH="$fake_py:$PATH"; export PATH; helper_detect_python) || stub_pick=
if [ "$stub_pick" = python3 ]; then
    fail "helper_detect_python accepted a zero-byte python3 stub"
fi
rm -rf "$fake_py"
fake_py=

# shellcheck disable=SC2086
$smoke_python "$root/bin/elves-floor" --root /tmp >/dev/null || fail "elves-floor"
# shellcheck disable=SC2086
$smoke_python -m py_compile "$root/bin/elves-floor" "$root/bin/goals-floor" || fail "py_compile"

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

printf 'ok\n'
