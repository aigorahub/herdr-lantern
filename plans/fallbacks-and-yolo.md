# Lantern 0.19.3: cross-provider fallbacks and saved yolo

## Requirement (John, 2026-11-22)

1. When a worker agent gets low on usage, the manager may switch that work
   to a comparable agent with more usage left. The problem is the weekly
   limit, which is shared by every model of one provider account, so the
   switch must cross providers.
2. John always wants Codex and Agy seats opened in yolo mode.

Both reverse 0.19 text: "Report an unavailable route without starting a
substitute", "Start a different provider only after the user selects it",
"A saved mode cannot enable a bypass, yolo, or always-approve", and the
manager-duty escalation of permission-mode increases. Review against this
requirement.

## Acceptance

### Fallbacks

1. A team seat can have an optional ordered `fallbacks` list. Each entry
   has exact harness, model and effort, and optional service_tier and mode.
   A team can set `switch_below` (percent of the weekly or 5-hour window
   left, default 10).
2. Trigger: the seat's provider usage, from the Plan Usage data Lantern
   already collects, is below `switch_below`, or the seat is stopped by a
   usage limit. Unknown usage never triggers a switch.
3. Choice: the first listed fallback whose usage pool differs from the
   exhausted one and has more than `switch_below` left. Entries in the same
   pool are skipped (Codex/OpenAI, Claude/Anthropic, Agy/Google,
   Grok/xAI). Verify the fallback with model-route and model-preflight, or
   the installed CLI catalog where no route exists. If none qualifies,
   report to the user and do not switch.
4. Timing: switch at a clean boundary (after a commit, or with the seat
   idle or done). Never interrupt a healthy working turn unless the seat is
   already stopped by its limit.
5. Handoff: start the fallback as a new seat, give it the full roster and
   roles, and a compact handoff (repo, worktree, branch, commit, task,
   acceptance, open items, run ledger when one exists). Keep the old
   session and transcript. Reuse the existing provider-switch handoff
   wording where it fits.
6. Independence: a fallback session that writes a change never reviews
   that change. The manager moves review to another seat if needed.
7. Lead seat: a switch of the lead seat needs the user told first, because
   the lead holds the plan, ledger and review state, and may be the user's
   own chat.
8. Report each switch in one line: from, to, usage numbers, handoff commit.
9. Without `fallbacks`, the 0.19 behavior stays: report and do not
   substitute.

### Saved yolo

10. A team seat can set `"permissions": "yolo"`. Lantern maps it to the
    harness flag: Codex `--dangerously-bypass-approvals-and-sandbox`, Agy
    `--dangerously-skip-permissions`, Claude
    `--dangerously-skip-permissions`, Grok its always-approve mode. Verify
    each flag against the installed CLI help before use.
11. The saved setting is the user's explicit yolo choice. Lantern does not
    ask again. Without the field, the current permission tiers stay.
12. A fallback inherits the `permissions` value of the seat it replaces and
    never raises it.
13. Yolo does not change authority: merge, deployment, publication and
    financial actions still need the current request.

### Both

14. Both prompt paths (desktop-prompt.md with bin/desktop.py, and the
    launch.sh appendix with prompt.md and herd-workflows.md where they
    state the old rule) and manager-duty.md carry the new rules. Remove the
    contradicting 0.19 sentences; do not only add.
15. README documents both fields with an example team file.
16. Tests: extend the existing smoke checks so both prompt paths include
    the new rules. The launch.sh appendix heredoc has no ASCII apostrophe.
17. No version bump (Opus does release prep).

## Constraints

- Prompt text stays short and general. The leads are frontier models.
- No change to model-route internals unless a check above needs it.
- Do not open a second Herdr app copy.
