#!/bin/sh
# Evening handoff ordering and easy-command routing, with no live Herdr calls.
set -eu
root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)

fail() {
    printf 'FAIL: %s\n' "$1" >&2
    exit 1
}

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
fake=$tmp/herdr
log=$tmp/herdr.log
closed=$tmp/pane.closed
fake_codex=$tmp/codex
codex_log=$tmp/codex.log

cat >"$fake" <<'EOF'
#!/bin/sh
printf '<%s>\n' "$*" >>"$FAKE_HERDR_LOG"
case "${1:-} ${2:-}" in
"pane get")
    [ ! -e "$FAKE_PANE_CLOSED" ] || exit 1
    printf '%s\n' '{"result":{"pane":{"label":"Lantern","pane_id":"w9:p9","workspace_id":"w9"}}}'
    ;;
"pane process-info")
    if [ "${FAKE_HELPER_KIND:-codex}" = claude ]; then
        printf '%s\n' '{"result":{"process_info":{"pane_id":"w9:p9","foreground_processes":[{"name":"claude.exe","pid":2147483646}]}}}'
    else
        printf '%s\n' '{"result":{"process_info":{"pane_id":"w9:p9","foreground_processes":[{"name":"codex.exe","argv0":"C:\\\\Codex\\\\codex.exe","pid":2147483646}]}}}'
    fi
    ;;
"agent prompt")
    if [ -n "${FAKE_NATIVE_WRITER:-}" ]; then
        python "$FAKE_NATIVE_WRITER" "$4" "$FAKE_NATIVE_EXPECTED" || exit $?
    elif [ "${FAKE_ID_ONLY:-}" = 1 ]; then
        printf 'handoff-id: %s\n' "$LANTERN_EVENING_ID" >"$LANTERN_EVENING_HANDOFF"
    elif [ "${FAKE_SKIP_HANDOFF:-}" != 1 ]; then
        {
            printf 'handoff-id: %s\n' "$LANTERN_EVENING_ID"
            printf '%s\n' \
                'utc: 2026-09-25T00:00:00Z' \
                'active: preserved w2' \
                'closed-temporary: w3' \
                'failed-gates: none' \
                'durable-results: none' \
                'dependencies: none' \
                'next: reconcile morning'
        } >"$LANTERN_EVENING_HANDOFF"
    fi
    ;;
"pane close")
    [ -s "$LANTERN_EVENING_HANDOFF" ] || exit 9
    grep -qF "handoff-id: $LANTERN_EVENING_ID" "$LANTERN_EVENING_HANDOFF" || exit 9
    : >"$FAKE_PANE_CLOSED"
    ;;
esac
EOF
chmod +x "$fake"
case $(uname -s 2>/dev/null || printf unknown) in
MINGW* | MSYS* | CYGWIN*)
    fake_codex=$tmp/codex.cmd
    cat >"$fake_codex" <<'EOF'
@echo off
echo [%*]>>"%FAKE_CODEX_LOG%"
if "%FAKE_CODEX_FAIL%"=="1" exit /b 1
exit /b 0
EOF
    ;;
*)
    cat >"$fake_codex" <<'EOF'
#!/bin/sh
printf '[%s]\n' "$*" >>"$FAKE_CODEX_LOG"
[ "${FAKE_CODEX_FAIL:-}" != 1 ]
EOF
    chmod +x "$fake_codex"
    ;;
esac

state=$tmp/state
mkdir -p "$state"
printf '%s\n' w9 >"$state/workspace.id"
printf '%s\n' w9:p9 >"$state/pane.id"
native_writer=
native_expected=
case $(uname -s 2>/dev/null || printf unknown) in
MINGW* | MSYS* | CYGWIN*)
    native_writer=$root/tests/fixtures/native_evening_handoff_writer.py
    native_expected=$(cygpath -w "$state/herd/evening-handoff.md")
    ;;
esac
if command -v python3 >/dev/null 2>&1; then
    host_python=python3
else
    host_python=python
fi
CODEX_SESSION_ID=01999999-9999-7999-8999-999999999999 \
    $host_python "$root/bin/lantern_session.py" capture \
    --path "$state/herd/lantern-codex-session.json" --pane w9:p9 --workspace w9
FAKE_HERDR_LOG=$log FAKE_PANE_CLOSED=$closed FAKE_CODEX_LOG=$codex_log \
    FAKE_NATIVE_WRITER=$native_writer FAKE_NATIVE_EXPECTED=$native_expected \
    HERDR_PLUGIN_ROOT=$root HERDR_PLUGIN_STATE_DIR=$state HERDR_BIN_PATH=$fake \
    CODEX_BIN_PATH=$fake_codex sh "$root/evening.sh" >"$tmp/evening.out" ||
    fail "successful evening action"
grep -qF '<agent prompt w9:p9 ' "$log" || fail "evening did not ask Lantern for the audit"
grep -qF '<pane close w9:p9>' "$log" || fail "evening did not close exact home pane"
grep -qF '<pane process-info --pane w9:p9>' "$log" ||
    fail "evening did not capture exact Codex process"
prompt_line=$(grep -nF '<agent prompt w9:p9 ' "$log" | cut -d: -f1)
close_line=$(grep -nF '<pane close w9:p9>' "$log" | cut -d: -f1)
[ "$prompt_line" -lt "$close_line" ] || fail "home closed before handoff prompt completed"
grep -qF '[delete 01999999-9999-7999-8999-999999999999 --force]' "$codex_log" ||
    fail "exact saved Codex session was not deleted"
if grep -qE '<(session|server) (stop|delete)' "$log"; then
    fail "evening stopped or deleted the Herdr session/server"
fi
grep -qF 'exact saved Codex session was deleted' "$tmp/evening.out" ||
    fail "evening did not report exact-session cleanup"

state_fail=$tmp/state-fail
mkdir -p "$state_fail"
printf '%s\n' w9 >"$state_fail/workspace.id"
printf '%s\n' w9:p9 >"$state_fail/pane.id"
before=$(grep -cF '<pane close w9:p9>' "$log")
rm -f "$closed"
if FAKE_SKIP_HANDOFF=1 FAKE_HERDR_LOG=$log FAKE_PANE_CLOSED=$closed \
    HERDR_PLUGIN_ROOT=$root \
    HERDR_PLUGIN_STATE_DIR=$state_fail HERDR_BIN_PATH=$fake \
    sh "$root/evening.sh" >/dev/null 2>&1; then
    fail "evening succeeded without a durable handoff"
fi
after=$(grep -cF '<pane close w9:p9>' "$log")
[ "$before" = "$after" ] || fail "evening closed home after handoff failure"

# An ID-only file is not a handoff the morning session can reconcile.
state_thin=$tmp/state-thin
mkdir -p "$state_thin"
printf '%s\n' w9 >"$state_thin/workspace.id"
printf '%s\n' w9:p9 >"$state_thin/pane.id"
before=$(grep -cF '<pane close w9:p9>' "$log")
rm -f "$closed"
if FAKE_ID_ONLY=1 FAKE_HERDR_LOG=$log FAKE_PANE_CLOSED=$closed \
    HERDR_PLUGIN_ROOT=$root HERDR_PLUGIN_STATE_DIR=$state_thin \
    HERDR_BIN_PATH=$fake sh "$root/evening.sh" >/dev/null 2>"$tmp/thin.err"; then
    fail "evening succeeded with an ID-only handoff"
fi
grep -qF 'handoff is missing utc:' "$tmp/thin.err" ||
    fail "ID-only handoff did not name the missing field"
after=$(grep -cF '<pane close w9:p9>' "$log")
[ "$before" = "$after" ] || fail "evening closed home after a thin handoff"

# A deletion failure is reported after pane close; no fuzzy name or direct
# session-file removal is attempted.
state_delete_fail=$tmp/state-delete-fail
mkdir -p "$state_delete_fail"
printf '%s\n' w9 >"$state_delete_fail/workspace.id"
printf '%s\n' w9:p9 >"$state_delete_fail/pane.id"
CODEX_SESSION_ID=01999999-9999-7999-8999-999999999998 \
    $host_python "$root/bin/lantern_session.py" capture \
    --path "$state_delete_fail/herd/lantern-codex-session.json" \
    --pane w9:p9 --workspace w9
rm -f "$closed"
if FAKE_CODEX_FAIL=1 FAKE_HERDR_LOG=$log FAKE_PANE_CLOSED=$closed \
    FAKE_CODEX_LOG=$codex_log HERDR_PLUGIN_ROOT=$root \
    HERDR_PLUGIN_STATE_DIR=$state_delete_fail HERDR_BIN_PATH=$fake \
    CODEX_BIN_PATH=$fake_codex sh "$root/evening.sh" \
    >"$tmp/delete-fail.out" 2>"$tmp/delete-fail.err"; then
    fail "evening claimed success after exact-session deletion failed"
fi
[ -e "$closed" ] || fail "deletion failure happened before exact pane close"
grep -qF 'saved session retained' "$tmp/delete-fail.err" ||
    fail "deletion failure did not plainly report retained history"

# Other supported Lantern helpers have no Codex receipt or history to delete.
state_claude=$tmp/state-claude
mkdir -p "$state_claude"
printf '%s\n' w9 >"$state_claude/workspace.id"
printf '%s\n' w9:p9 >"$state_claude/pane.id"
before_delete=$(wc -l <"$codex_log")
rm -f "$closed"
FAKE_HELPER_KIND=claude FAKE_HERDR_LOG=$log FAKE_PANE_CLOSED=$closed \
    HERDR_PLUGIN_ROOT=$root HERDR_PLUGIN_STATE_DIR=$state_claude \
    HERDR_BIN_PATH=$fake sh "$root/evening.sh" >"$tmp/claude.out" ||
    fail "non-Codex evening cleanup"
[ "$(wc -l <"$codex_log")" -eq "$before_delete" ] ||
    fail "non-Codex evening tried to delete a Codex session"
grep -qF 'no Codex history cleanup was needed' "$tmp/claude.out" ||
    fail "non-Codex evening did not report its cleanup result"

# A Codex pane with no captured receipt still reports incomplete cleanup.
state_missing=$tmp/state-missing-receipt
mkdir -p "$state_missing"
printf '%s\n' w9 >"$state_missing/workspace.id"
printf '%s\n' w9:p9 >"$state_missing/pane.id"
rm -f "$closed"
if FAKE_HERDR_LOG=$log FAKE_PANE_CLOSED=$closed HERDR_PLUGIN_ROOT=$root \
    HERDR_PLUGIN_STATE_DIR=$state_missing HERDR_BIN_PATH=$fake \
    sh "$root/evening.sh" >"$tmp/missing.out" 2>"$tmp/missing.err"; then
    fail "Codex evening succeeded without its exact session receipt"
fi
grep -qF 'private Codex session identity could not be proved' "$tmp/missing.err" ||
    fail "missing Codex receipt did not report retained history"

# hsh and hsh.cmd expose the same simple verbs. HERDR_ENV avoids attaching a
# nested TUI in this shell-only route test.
fake_path=$tmp/path
mkdir -p "$fake_path"
cp "$fake" "$fake_path/herdr"
FAKE_HERDR_LOG=$log HERDR_ENV=1 PATH="$fake_path:$PATH" sh "$root/hsh" evening
FAKE_HERDR_LOG=$log HERDR_ENV=1 PATH="$fake_path:$PATH" sh "$root/hsh" nightly
FAKE_HERDR_LOG=$log HERDR_ENV=1 PATH="$fake_path:$PATH" sh "$root/hsh" morning
[ "$(grep -cF '<plugin action invoke aigora.lantern.evening>' "$log")" -eq 2 ] ||
    fail "hsh evening/nightly routing"
grep -qF '<plugin action invoke aigora.lantern.open>' "$log" ||
    fail "hsh morning routing"
before_attach=$(grep -cF '<>' "$log" || true)
(
    unset HERDR_ENV
    FAKE_HERDR_LOG=$log PATH="$fake_path:$PATH" sh "$root/hsh" morning
)
after_attach=$(grep -cF '<>' "$log" || true)
[ "$after_attach" -eq $((before_attach + 1)) ] ||
    fail "hsh morning did not attach Herdr outside a managed pane"
grep -qF 'dependency/integration checks' "$log" ||
    fail "evening request does not dependency-audit temporary work"
grep -qF 'explicitly marks it temporary' "$log" ||
    fail "evening request can infer temporary workspace status"
grep -qiF 'if /I "%~1"=="nightly" goto evening' "$root/hsh.cmd" ||
    fail "hsh.cmd nightly routing"
grep -qiF 'if /I "%~1"=="morning" goto morning' "$root/hsh.cmd" ||
    fail "hsh.cmd morning routing"
grep -qF 'if not "%~2"=="" goto usage' "$root/hsh.cmd" ||
    fail "hsh.cmd accepts extra arguments"
grep -qF 'if "%HERDR_ENV%"=="1" exit /b 0' "$root/hsh.cmd" ||
    fail "hsh.cmd morning attaches Herdr inside a managed pane"
grep -qF 'codex_headless.py' "$root/bin/codex-headless.cmd" ||
    fail "Windows headless launcher"
grep -qF 'CODEX_SESSION_ID' "$root/launch.sh" ||
    fail "launch does not capture the exact Codex session identity"
grep -qF 'delete", session_id, "--force"' "$root/bin/lantern_session.py" ||
    fail "cleanup does not use supported exact-session Codex deletion"

printf '%s\n' 'ok: evening handoff gates and morning/nightly commands'
