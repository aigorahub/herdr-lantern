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
trap 'rm -f "$tmp" "$err"; [ -n "$fake" ] && rm -rf "$fake"; [ -n "$fake_prompt" ] && rm -rf "$fake_prompt"' EXIT

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
