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
open_dir=
trap 'rm -f "$tmp" "$err"; [ -n "$fake" ] && rm -rf "$fake"; [ -n "$fake_prompt" ] && rm -rf "$fake_prompt"; [ -n "$fake_ws" ] && rm -rf "$fake_ws"; [ -n "$open_dir" ] && rm -rf "$open_dir"' EXIT

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
grep -q 'CLAUDE.md' "$root/launch.sh" || fail "launch writes CLAUDE.md"

grep -q '^placement = "tab"$' "$root/herdr-plugin.toml" || fail "pane placement is tab"
if grep -qE '^(width|height) =' "$root/herdr-plugin.toml"; then
    fail "pane still sizes a popup"
fi

# Real herdr 0.7.5 shapes, trimmed of fields these helpers ignore.
created_json='{"id":"cli:workspace:create","result":{"root_pane":{"agent_status":"unknown","cwd":"/Users/j","pane_id":"w9:p1","scroll":{"viewport_rows":32},"tab_id":"w9:t1","workspace_id":"w9"},"tab":{"label":"1","tab_id":"w9:t1","workspace_id":"w9"},"type":"workspace_created","workspace":{"active_tab_id":"w9:t1","label":"🪔 lantern","workspace_id":"w9"}}}'
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
grep -q "workspace_label='🪔 lantern'" "$root/open.sh" || fail "open.sh lantern label"

fake_ws=$(mktemp -d)
cat >"$fake_ws/herdr" <<'EOF'
#!/bin/sh
if [ "$1" = workspace ] && [ "$2" = list ]; then
    printf '%s\n' '{"result":{"workspaces":[{"label":"love-spark","workspace_id":"w1"},{"label":"lantern","workspace_id":"w5"},{"label":"🪔 lantern","workspace_id":"w7"}]}}'
    exit 0
fi
if [ "$1" = workspace ] && [ "$2" = get ]; then
    [ "$3" = w7 ] || exit 1
    printf '%s\n' '{"result":{"type":"workspace_info","workspace":{"active_tab_id":"w7:t2","label":"🪔 lantern","workspace_id":"w7"}}}'
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
found=$(helper_workspace_id_by_label "$fake_ws/herdr" '🪔 lantern')
[ "$found" = w7 ] || fail "workspace by lantern label ($found)"
[ -z "$(helper_workspace_id_by_label "$fake_ws/herdr" nope)" ] || fail "unknown label"
[ "$(helper_workspace_label "$fake_ws/herdr" w7)" = '🪔 lantern' ] ||
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
"workspace create")
    printf '{"result":{"root_pane":{"pane_id":"w9:p1","scroll":{"viewport_rows":32},"tab_id":"w9:t1","workspace_id":"w9"},"workspace":{"label":"new","workspace_id":"w9"}}}\n'
    ;;
"plugin pane")
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
export STUB_OPEN_WS STUB_OPEN_FAIL
STUB_WS_EXTRA=
STUB_WS=
STUB_WS_LABEL=
STUB_PANE=
STUB_PANE_LABEL=Lantern
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
logged '--label 🪔 lantern' || fail "first open should use the lantern label"
logged 'plugin pane open --plugin aigora.lantern --entrypoint helper --placement tab --workspace w9' ||
    fail "first open should seat a tab in the new workspace"
logged 'tab rename w9:t2 field' || fail "first open should name the tab"
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
STUB_WS_EXTRA=',{"label":"🪔 lantern","workspace_id":"w7"}'
STUB_OPEN_WS=w7
run_open || fail "reuse by label"
if logged 'workspace create'; then fail "label match must not create a workspace"; fi
logged '--placement tab --workspace w7' || fail "label match should seat in w7"
if logged 'pane close'; then fail "label match must not close a pane"; fi
STUB_WS_EXTRA=
STUB_OPEN_WS=w9

# A chat that never starts must not leave an empty workspace behind.
reset_open
STUB_OPEN_FAIL=1
if run_open; then fail "failed open should exit nonzero"; fi
logged 'workspace close w9' || fail "failed open should drop the new workspace"
if [ -f "$open_state/workspace.id" ]; then
    fail "failed open should not remember a workspace"
fi
STUB_OPEN_FAIL=

# A second open while one is in flight does nothing.
reset_open
mkdir "$open_state/open.lock"
run_open || fail "locked open should exit 0"
if [ -s "$STUB_LOG" ]; then
    fail "locked open should call nothing"
fi
rmdir "$open_state/open.lock"

detected=$(helper_detect_agent) || fail "no helper agent on PATH"
case $detected in
devin | agent | claude | codex | grok) ;;
*) fail "detect returned $detected" ;;
esac

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

python3 "$root/bin/elves-floor" --root /tmp >/dev/null || fail "elves-floor"
python3 -m py_compile "$root/bin/elves-floor" "$root/bin/goals-floor" || fail "py_compile"

fake=$(mktemp -d)
cat >"$fake/herdr" <<'EOF'
#!/bin/sh
if [ "$1" = agent ] && [ "$2" = list ]; then
    cat <<'JSON'
{"result":{"agents":[{"pane_id":"w1:p1","agent":"claude","agent_status":"done","cwd":"/tmp/demo","terminal_title_stripped":"Fix login"}]}}
JSON
    exit 0
fi
if [ "$1" = agent ] && [ "$2" = read ]; then
    printf '%s\n' '※ recap: Goal: fix login. Next: your go-ahead to merge #12.'
    printf '%s\n' '◎ /goal active (2h)'
    exit 0
fi
exit 1
EOF
chmod +x "$fake/herdr"
goals=$(python3 "$root/bin/goals-floor" --herdr "$fake/herdr") || fail "goals-floor"
printf '%s\n' "$goals" | grep -q 'herd_detected 1' || fail "goals-floor herd_detected"
printf '%s\n' "$goals" | grep -q 'NEEDS YOU' || fail "goals-floor needs you"
printf '%s\n' "$goals" | grep -q '/goal 2h' || fail "goals-floor goal age"

printf 'ok\n'
