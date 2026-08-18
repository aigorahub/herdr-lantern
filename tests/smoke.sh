#!/bin/sh
# Fast checks: syntax, config parse, herdr wrapper gate.
set -eu
root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
cd "$root"

fail() {
    printf 'FAIL: %s\n' "$1" >&2
    exit 1
}

for f in launch.sh open.sh lib.sh bin/herdr tests/smoke.sh; do
    sh -n "$f" || fail "sh -n $f"
done

# shellcheck disable=SC1091
. "$root/lib.sh"

tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT

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

detected=$(helper_detect_agent) || fail "no helper agent on PATH"
case $detected in
devin | claude | codex | grok) ;;
*) fail "detect returned $detected" ;;
esac

export HERDR_REAL=/bin/echo
export HERDR_HELPER_OK=
out=$(sh "$root/bin/herdr" agent list) || fail "inspect should pass"
printf '%s\n' "$out" | grep -q 'agent list' || fail "inspect did not exec real herdr"

if sh "$root/bin/herdr" workspace create --cwd /tmp --label x 2>/tmp/helper-wrap.err; then
    fail "mutate without OK should fail"
fi
grep -q HERDR_HELPER_OK /tmp/helper-wrap.err || fail "blocked hint missing"

export HERDR_HELPER_OK=1
out=$(sh "$root/bin/herdr" workspace create --cwd /tmp --label x) ||
    fail "mutate with OK should pass"
printf '%s\n' "$out" | grep -q 'workspace create' || fail "OK mutate did not exec real herdr"

printf 'ok\n'
