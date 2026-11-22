# Lantern 0.19.0 run: staging and ledger

Plan: `plans/team-preferences.md`. Branch: `plan/team-preferences`. Base: `main` 3769eb9.

## Roster

| Seat | Agent | Role |
| --- | --- | --- |
| t49y | Opus 5.5 high (Claude Code) | Lead: plan, stage, assign, integrate, release prep |
| t49z | Sol 6.2 high Ultrafast (Codex) | Worker: implements S1-S6 |
| t4b0 | Agy Gemini 4 Neon high | Reviewer (always `/boost`, plan mode); cleanup worker when assigned |
| t4b1 | Grok 4.7 high (Grok Build) | Reviewer; reviews every Neon fix |

## Authority

This run is authorized for the planning review only. Implementation starts when John says go. A merge, a GitHub release or a publication needs John's explicit landing authority in the current session. A saved preference grants no authority.

## Slices

Each slice is one commit by Sol. Each acceptance line is a verifiable goal. Sol chooses the details.

| Slice | Write scope | Acceptance |
| --- | --- | --- |
| S1 Guidance | new `guidance.md`; `launch.sh` read and include; `bin/desktop.py` chat prompt; `desktop-prompt.md`; `tests/smoke.sh`; `README.md` | Both prompt paths carry the default guidance and the optional user file `guidance.md` from the config dir, and the user text wins on a conflict. A missing user file is not an error. The smoke check fails if either path loses the include. The appendix heredoc has no ASCII apostrophe. |
| S2 Startup rule | `launch.sh` appendix gate and retry paragraphs; `prompt.md:487-497`; `desktop-prompt.md` near `:26`; `tests/smoke.sh:3203-3206`; `README.md:413-415` | One harness-neutral rule as in the plan, with clear and stop lists, the mechanics, the lessons from the old gates, and `startup: "ask"`. The appendix names and overrides the old user `prompt.md` gate paragraph. `HERDR_HELPER_OK=1` is allowed for startup blockers on the seat just started. A smoke assertion checks the override. The gate code is unchanged. |
| S3 Team files | `launch.sh` appendix; `desktop-prompt.md`; `herd-workflows.md:210-212`; `plans/agent-teams.md:236-238`; `README.md` | Both prompt paths name `teams/<name>.json` in the config dir, the fields, on-demand reads, validation through `model-route`/`model-preflight` or the CLI catalog, save-by-chat, and setup-only authority. The precedence text says that a named team is an explicit choice, with Elves routes as the fallback. The README documents the format with one example. |
| S4 Workflow files | `launch.sh` appendix; `desktop-prompt.md`; `README.md` | Both prompt paths name `workflows/<name>.md`, the header lines, the load order after the built-ins, the collision rule, the setup meaning, save-by-chat, and no landing authority. The README documents the format with one example. |
| S5 Live check | ledger only, plus scratch dirs under the session scratchpad | The exact launch commands for the branch build on both paths, with scratch config dirs, are recorded first. Codex, Claude, Agy and Grok seats each reach ready in an untrusted directory with no human keys. No key goes to a runtime permission prompt. The stale `prompt.md` case is covered. The pane evidence is recorded here. |
| S6 Gate removal | `lib.sh`; `tests/smoke.sh` | The gate helpers and the gate tail of `helper_relay_agent_start` are gone. `helper_pane_has_login_picker`, the Codex bypass flag position and the launch guard remain. The removed helpers' tests are gone. `sh tests/smoke.sh` passes. |
| S7 Release prep (Opus) | `CHANGELOG.md`; version files | The 0.19.0 version is set in the manifest, `README.md`, `howto.html`, `docs/index.html` and `docs/teams.html`, and the smoke version check passes. The CHANGELOG entry is complete. |

## Integration

Sol owns the branch. Neon cleanup fixes go in a separate worktree based on the slice commit under review. Opus integrates them at the next slice boundary, and Sol rebases before the next slice commit. Grok reviews each Neon fix. No agent reviews its own fix.

## Ledger

Each row records one assignment or reply. A verdict names its commit. When the branch moves past a verdict's commit, that verdict and its open blockers are marked stale until they are re-reviewed.

| Turn | From | To | Scope | Commit | Result | State |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | t49y | t49z, t4b0 (/boost), t4b1 | R1 plan review of both documents | 3dea2c1 | sent | open |
| 1 | John | t49y | "great, and follow our principles (like sol as worker)": implementation may follow R1 sign-off; Sol is the only implementation writer | 3dea2c1 | recorded | in force |
| 2 | t49z | t49y | R1 plan review | 3dea2c1 | BLOCKING: 7 blocking, 1 advisory | open |
| 2 | t4b0 | t49y | R1 plan review (/boost) | 3dea2c1 | BLOCKING: 6 blocking, 3 advisory | open |
| 2 | t4b1 | t49y | R1 plan review | 3dea2c1 | BLOCKING: 4 blocking, 6 advisory | open |
