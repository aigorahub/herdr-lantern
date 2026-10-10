# Split readouts and service table

This follow on change replaces the three tab layout from version 0.17.0.
The development tab stays as one pane. Lantern Home uses three panes in one
product tab. Chat fills the left half. Plan Usage fills the top right.
Field Status fills the bottom right. The right column starts with a 60/40
height split. Later opens preserve existing sizes and chat focus.

Setup moves only exact readout watchers or recorded idle readout shells.
It preserves each watcher during a move. Unrelated panes and agents receive
no input. Saved ownership, server identity, and shell checks still apply.
A lost split response must not cause another split on retry. A lost move
response can recover the exact running watcher. A failed setup does not stop
chat startup.

Plan Usage shows one logical row per service. Each row contains all verified
allowance windows, reset times, credits, and data age. Multiple verified
accounts keep separate values within that row. Unknown scopes stay visible.
Collection still checks provider and account identity. Display grouping does
not combine balances or assign measured values to unknown accounts.

The table has aligned Service, Left, and Reset columns. Narrow panes use a
combined allowance and reset column. Compact allowance bars help compare
short limit labels. Text shows all percentages and error states. Terminal
watch output uses warning colors. NO_COLOR disables color. Snapshot output
stays plain. The display omits agents and tab counts.

Tests cover startup, reuse, stopped watchers, closed readouts, migration,
uncertain responses, identity checks, account distinctions, unknown scopes,
color output, and narrow widths. The full smoke and team suites run before
review. Independent reviewers inspect the final commit. The follow on PR
stays open until the user approves a merge.
