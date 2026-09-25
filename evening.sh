#!/bin/sh
# Ask the live Lantern to audit the field, persist a handoff, and only then
# close its own pane. The Herdr server and every preserved workspace stay up.
set -eu

die() {
    printf 'lantern evening: %s\n' "$1" >&2
    exit 1
}

plugin_root=${HERDR_PLUGIN_ROOT:-$(CDPATH= cd -- "$(dirname "$0")" && pwd)}
# shellcheck disable=SC1091
. "$plugin_root/lib.sh"
plugin_root=$(helper_posix_path "$plugin_root")

state_dir=${HERDR_PLUGIN_STATE_DIR:-}
[ -n "$state_dir" ] || die "HERDR_PLUGIN_STATE_DIR is not set; invoke this as a plugin action"
state_dir=$(helper_posix_path "$state_dir")
herdr=${HERDR_BIN_PATH:-herdr}
workspace_file=$state_dir/workspace.id
pane_file=$state_dir/pane.id
[ -s "$workspace_file" ] || die "no remembered Lantern home workspace"
[ -s "$pane_file" ] || die "no remembered Lantern home pane"
workspace=$(sed -n '1p' "$workspace_file")
pane=$(sed -n '1p' "$pane_file")
live_workspace=$(helper_lantern_pane_workspace "$herdr" "$pane" Lantern) ||
    die "the remembered Lantern pane is not live"
[ "$live_workspace" = "$workspace" ] ||
    die "the remembered pane no longer belongs to the Lantern home workspace"

handoff_dir=$state_dir/herd
handoff=$handoff_dir/evening-handoff.md
handoff_native=$(helper_native_path "$handoff")
session_receipt=$handoff_dir/lantern-codex-session.json
session_helper=$plugin_root/bin/lantern_session.py
(umask 077; mkdir -p "$handoff_dir") || die "could not create private handoff directory"
[ ! -L "$handoff" ] || die "refusing to replace a symlinked handoff"
handoff_id="$(date -u +%Y%m%dT%H%M%SZ)-$$"
export LANTERN_EVENING_HANDOFF=$handoff
export LANTERN_EVENING_ID=$handoff_id

request=$(cat <<EOF
Run the authorized evening shutdown workflow now.

1. Inventory Herdr workspaces, tabs, agents, current task/monitor records, and
   dependency or handoff relationships. A workspace is temporary only when it
   is explicitly labelled tmp:/temp:/temporary: or the durable task record
   explicitly marks it temporary. Ambiguous workspaces are not temporary.
2. Preserve every working, blocked, unresolved, ambiguous, or depended-on
   workspace. For each completed temporary workspace, require settled agents,
   committed clean edit results or durably saved non-edit results, passed
   required task/repository/dependency/integration checks, and no active child,
   handoff, monitor, or downstream consumer. Recheck identity and state, then
   close only exact tabs/workspaces that pass every gate. Do not remove a
   worktree. Do not close the Lantern home pane or workspace.
3. After the audit and eligible closes, atomically write a compact Markdown
   handoff to this exact path: $handoff_native
   Its first line must be exactly: handoff-id: $handoff_id
   Also write one line for each of these labels. A line may say none.
   utc:
   active:
   closed-temporary:
   failed-gates:
   durable-results:
   dependencies:
   next:
   Never include credentials, tokens, auth/config contents, or copied
   secrets. Keep it concise.
4. Read the file back, verify that exact handoff ID and those labels,
   then report completion. Do not exit or close your own pane; the outer
   evening action does that only after it independently verifies the handoff.
EOF
)

"$herdr" agent prompt "$pane" "$request" --wait --timeout 600000 ||
    die "Lantern did not complete the evening audit; home remains open"
[ -s "$handoff" ] || die "handoff was not written; home remains open"
first_line=$(sed -n '1p' "$handoff")
[ "$first_line" = "handoff-id: $handoff_id" ] ||
    die "handoff verification failed; home remains open"
for handoff_label in utc: active: closed-temporary: failed-gates: \
    durable-results: dependencies: next:; do
    grep -q "^${handoff_label}" "$handoff" ||
        die "handoff is missing ${handoff_label}; home remains open"
done

# Capture cleanup evidence while the exact pane is still live. Failure is not
# permission to guess: the pane may exit, but its saved Codex session remains.
helper_python=$(helper_detect_python) || helper_python=
cleanup_ready=1
cleanup_reason=
session_id=
codex_pid=
if [ -z "$helper_python" ]; then
    cleanup_ready=0
    cleanup_reason="no Python 3 interpreter; saved Codex session retained"
else
    process_json=$("$herdr" pane process-info --pane "$pane" 2>/dev/null) || process_json=
    if [ -z "$process_json" ] || ! helper_kind=$(printf '%s\n' "$process_json" | \
        $helper_python "$session_helper" kind-from-json --pane "$pane"); then
        cleanup_ready=0
        cleanup_reason="exact Lantern helper process could not be proved; saved session retained if Codex"
    elif [ "$helper_kind" = other ]; then
        cleanup_ready=skip
    elif ! session_id=$($helper_python "$session_helper" show \
        --path "$session_receipt" --pane "$pane" --workspace "$workspace"); then
        cleanup_ready=0
        cleanup_reason="private Codex session identity could not be proved; saved session retained"
    elif ! codex_pid=$(printf '%s\n' "$process_json" | $helper_python \
        "$session_helper" pid-from-json --pane "$pane"); then
        cleanup_ready=0
        cleanup_reason="exact Lantern Codex process could not be proved; saved session retained"
    fi
fi

# The current user command authorized evening shutdown. This is deliberately
# last: pane close may also remove the empty home tab/workspace. It never stops
# the Herdr server, so other workspaces and their processes survive UI exit.
"$herdr" pane close "$pane" ||
    die "handoff is safe at $handoff, but the Lantern home pane did not close"
printf 'evening handoff saved: %s\n' "$handoff"

# Prove the exact pane is gone before even considering Codex history deletion.
pane_exited=0
attempt=0
while [ "$attempt" -lt 120 ]; do
    if ! "$herdr" pane get "$pane" >/dev/null 2>&1; then
        pane_exited=1
        break
    fi
    attempt=$((attempt + 1))
    sleep 0.25
done
if [ "$pane_exited" != 1 ]; then
    cleanup_ready=0
    cleanup_reason="Lantern pane exit could not be proved; saved Codex session retained"
fi

if [ "$cleanup_ready" = 1 ]; then
    if deleted_id=$($helper_python "$session_helper" delete \
        --path "$session_receipt" --pane "$pane" --workspace "$workspace" \
        --pid "$codex_pid" --codex "${CODEX_BIN_PATH:-codex}" --timeout 30); then
        [ "$deleted_id" = "$session_id" ] ||
            die "Codex deletion returned an unexpected identity; cleanup is unverified"
        printf 'Codex desktop history removed for exact exited session: %s\n' "$session_id"
    else
        cleanup_ready=0
        cleanup_reason="supported exact-session Codex deletion failed; saved session retained or cleanup is unverified"
    fi
fi

if [ "$cleanup_ready" != 1 ]; then
    if [ "$cleanup_ready" = skip ]; then
        printf 'Lantern home exited; no Codex history cleanup was needed.\n'
        printf 'Close the Herdr window normally; do not stop the server.\n'
        exit 0
    fi
    printf 'WARNING: %s\n' "$cleanup_reason" >&2
    printf 'Lantern home exited, but Codex history cleanup was not completed.\n' >&2
    exit 1
fi
printf 'Lantern home exited and its exact saved Codex session was deleted.\n'
printf 'Close the Herdr window normally; do not stop the server.\n'
