# Lantern 0.19.1: the manager keeps all agents moving

## Requirement (John, 2026-11-22)

"We don't want agents stopping for permissions. The manager should grant
permissions so the work keeps moving." And: "The manager must keep all
agents moving."

This reverses the 0.19.0 decision that a seat stops and reports any tool,
command, file or network permission prompt at startup. Review against this
requirement, not against the 0.19.0 text.

## Gaps in 0.19.0

- The desktop path (`bin/desktop.py` chat(), about line 422) builds the
  prompt from `desktop-prompt.md`, `guidance.md` and optional
  `kata-monitor.md`. None of them has a permission procedure. The full
  procedure exists only in `herd-workflows.md` ("Permission handling during
  monitoring"), which only `launch.sh` reads.
- That procedure applies to "selected runs" (Elves runs) only
  (`herd-workflows.md` about 569, `launch.sh` about 356, `prompt.md` about
  103). A named team or saved workflow has no defined scope.
- A team lead that Lantern sets up gets the roster and `guidance.md` only.
  It gets no duty to keep its seats moving and no limits.
- The startup rule tells the lead to stop on any permission prompt
  (`desktop-prompt.md` about 44 and 65, `launch.sh` about 382 and 402,
  `prompt.md` about 501 and 522).
- "The file grants no ... permission ... authority" can be read as "never
  grant a permission prompt".

## Goal

Every manager (the Lantern lead and every team lead it sets up) has a duty
to keep each agent it manages moving, on both prompt paths. Routine
in-scope permission prompts are granted by the manager, not reported to
the user.

## Acceptance

1. Duty. The manager watches the seats it manages. When a seat is blocked
   on a permission prompt, waiting on a question the manager can answer,
   stalled, or idle with assigned work left, the manager acts so the seat
   moves again. Healthy working seats are not interrupted.
2. Scope. For an Elves run, scope is the accepted run scope (unchanged).
   For a seat in a named team or saved workflow, scope is the task and role
   the manager assigned to that seat. At startup, before a task exists,
   scope is: start the seat in its configured state in the workspace that
   Lantern opened for it. A read-only reviewer that asks to write files, or
   any seat that asks to reach an outside service the task does not need,
   is out of scope.
3. Grant procedure. One general procedure for both paths: read the exact
   prompt, compare it with scope, select the narrowest visible option
   (prefer allow once), verify the screen again before the key, then
   confirm that the same prompt cleared and the seat resumed. No repeated
   keys to an unchanged screen. Desktop seats use `hc keys`. Legacy seats
   use `HERDR_HELPER_OK=1 herdr agent send-keys`. Shared text must not
   hard-code only one of them.
4. Startup. A permission prompt during startup is handled by the same
   procedure with the startup scope. `startup: "ask"` still means report
   and send no keys.
5. Escalate only these to the user: login, account choice, plan, quota or
   payment; an action outside scope; merge, deployment, publication or
   financial actions without authority from the current request; a raise
   of the permission mode (yolo, force, always-approve, bypassPermissions,
   dangerously skip permissions) or permanent broad access; a dialog
   outside the agent pane; a screen the manager cannot classify.
6. Non-overridable. The duty, scope, procedure and limits live in shipped
   text that the user `guidance.md` cannot override. Both prompt paths
   include it. Lantern gives it to each team lead together with the
   managerial guidance and roster.
7. Intent line. The shipped `guidance.md` gets one plain line of intent:
   the manager keeps every agent moving and grants routine in-scope
   permissions.
8. Wording. "Grants no permission authority" is reworded so that it plainly
   means no permission-mode raise or broad grant. Routine in-scope grants
   are part of managing.
9. Contradicting text is changed, not only added to, in `desktop-prompt.md`,
   the `launch.sh` appendix, `prompt.md`, `herd-workflows.md`, `README.md`
   (about 421), `docs/teams.html` and `howto.html` if they state the old
   behavior.
10. Tests. Update the smoke assertions to the new contract. Add a check
    that both prompt paths include the shared text, because a missing
    include fails without an error. The `launch.sh` appendix heredoc must
    contain no ASCII apostrophe.
11. Release prep (Opus does this after review): `CHANGELOG.md` 0.19.1 and
    the version in every place `tests/smoke.sh` checks.

## Constraints

- Do not open a second Herdr app copy.
- No change to model routing, launch argv, or permission tiers.
- Prompt text stays short and general. The leads are frontier models.
