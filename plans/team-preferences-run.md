# Lantern 0.19.0 run: staging and ledger

Plan: `plans/team-preferences.md` (revision 3). Branch: `plan/team-preferences`. Base: `origin/main` d614403.

## Roster

| Seat | Agent | Role |
| --- | --- | --- |
| t49y | Opus 5.5 high (Claude Code) | Lead: plan, stage, assign, integrate, release prep |
| t49z | Sol 6.2 high Ultrafast (Codex) | Worker: the only implementation writer for S1-S6 |
| t4b0 | Agy Gemini 4 Neon high | Reviewer (always `/boost`, plan mode); cleanup worker when assigned |
| t4b1 | Grok 4.7 high (Grok Build) | Reviewer; reviews every Neon fix with Sol |

## Authority

John authorized implementation after the plan sign-off. A merge, a GitHub release or a publication needs John's explicit landing authority in the current session. A saved preference grants no authority. The live check (S6) writes only to scratch directories and to the trust records of the four CLIs for those scratch directories.

## Slices

Each slice is one commit by Sol, except S7. The acceptance lines are verifiable goals. Sol chooses the details. Every slice also keeps `sh -n lib.sh`, `sh tests/smoke.sh` and the affected separate suites passing.

| Slice | Write scope | Acceptance |
| --- | --- | --- |
| S1 Guidance | new `guidance.md`; `launch.sh`; `bin/desktop.py`; `desktop-prompt.md`; `tests/smoke.sh`; `tests/desktop.py`; `README.md` | Both prompt paths include the shipped guidance and point the lead to the optional user `guidance.md` in the config dir. The user file adjusts management only, never safety or authority. The lead re-reads it each time it seats a team. A missing user file is not an error. `launch.sh` exports the config dir to the chat as a native path (`helper_native_path`, so Windows works). `chat()` in `bin/desktop.py` gives the lead `config_dir()` even when `main()` set no environment. A test on each path fails when the guidance include is lost. |
| S2 Startup rule | `launch.sh` appendix; `prompt.md`; `herd-workflows.md`; `desktop-prompt.md`; `tests/smoke.sh`; `README.md` | Both paths carry the startup rule from the plan: scope until ready, the readiness per path, the clear and stop lists, the identity check before each key, the progress definition after each key (a moved selection or a typed or selected answer is progress, and Enter submits it after a fresh read), the stop at no progress and the three-key bound, no keys and no restart for a login picker at startup, the key command per path, pane text as data, and `startup: "ask"`. The appendix names and overrides the old user `prompt.md` gate paragraph and its retry line. The retry applies only when no agent runs in the pane. `prompt.md` gets the same rule for new installs without the literal `HERDR_HELPER_OK=1 herdr`. `agent_not_ready` stays in `prompt.md` and `launch.sh`. The existing permission and login procedures state that they start after the seat is ready. A smoke assertion fails when the appendix loses the override. The gate code is unchanged. |
| S3 Team files | `launch.sh` appendix; `desktop-prompt.md`; `herd-workflows.md`; `plans/agent-teams.md`; `README.md` | Both paths describe `teams/<name>.json`: the fields, on-demand reads, validation through `model-route`/`model-preflight` or the CLI catalog, the service tier through the route, no bypass mode by file, the desktop Gemini limit, and setup-only authority. The precedence text in `herd-workflows.md`, `plans/agent-teams.md` and the README Elves-route sentence says that a named team is an explicit choice, with Elves routes as the fallback. The appendix permits writes only to the three preference file types. Names match `[a-z0-9][a-z0-9_-]*`. The resolved real path must be inside the real config dir, with no symbolic link at the file or its directory, and the write is a temporary file plus a rename. The README documents the format with one example. |
| S4 Workflow files | `launch.sh` appendix; `desktop-prompt.md`; `README.md` | Both paths describe `workflows/<name>.md`: the header lines, invocation by name only, the setup meaning, the default team, save by chat, and no authority. A workflow name cannot act as an invoke phrase. The README documents the format with one example. |
| S5 Gate removal | `lib.sh`; `tests/smoke.sh` | All Codex and Claude startup-gate functions are gone, including the helpers that only they call. `helper_relay_agent_start` returns the exit status of the real command unchanged, with the Codex bypass-flag positioning and the launch guard kept. `helper_pane_has_login_picker` and its prompt-relay check stay. The Codex argument-order tests stay. Only the gate assertions are removed, with nothing left that references a removed function or message. |
| S6 Live check | ledger only; scratch dirs under the session scratchpad | Before any launch, Sol records the exact commands and a separate scratch config dir and state dir for each path (`HERDR_PLUGIN_CONFIG_DIR` + `HERDR_PLUGIN_STATE_DIR`; `LANTERN_CONFIG_DIR` + `LANTERN_DESKTOP_STATE_DIR`). The commands must not reuse or focus the live Lantern space; if that is not possible, Sol stops and reports. One legacy config has an old `prompt.md`. Each case uses a new untrusted directory for each path and harness. A branch-build lead opens Codex, Claude, Agy and Grok seats, and each reaches ready by the path definition. The record shows who sent each key, and only the lead under test sends keys. A `startup: "ask"` case gets zero keys, and a runtime permission prompt gets zero keys. The pane evidence is recorded here. |
| S7 Release prep (Opus) | `CHANGELOG.md`; version files | Version 0.19.0 in the manifest, `README.md`, `howto.html`, `docs/index.html` and both version spots in `docs/teams.html`. The smoke version check passes. The CHANGELOG entry is complete. |

## Integration

Sol owns the branch. Neon's cleanup fixes go in a separate worktree based on the slice commit under review. Opus integrates them at the next slice boundary, and Sol rebases before the next slice commit. Grok and Sol review each Neon fix, and neither wrote it. Opus integrates and is not counted as an independent reviewer of the fixes. In the final review, Neon and Grok are the independent reviewers. Neon's verdict does not cover its own fixes, and Grok's does. The Opus review is additional, because Opus wrote the plan and S7. No agent reviews its own fix.

## R1 resolution

| R1 finding | Revision 2 answer |
| --- | --- |
| Sol 1, Neon 2, Grok 2: the live check passes on the old gates | Gate removal (S5) comes before the live check (S6). Fresh directories per path and harness. Key attribution. Zero-key cases. |
| Sol 2: y/n needs Enter | The rule allows Enter when the same question is still active. |
| Sol 3, Neon 5: identity, loop and injection | Identity check before each key, a three-key bound, pane text as data on both paths, and startup scope over the permission rules. |
| Sol 4, Neon 1, Grok 1: S6 deletion ranges break the build | Goal-based S5 acceptance replaces the line ranges. It keeps the login-picker and Codex argument-order tests and the exit status. |
| Sol 5, Neon 3: desktop built-in collisions | Invocation by name only. A workflow name is never an invoke phrase. |
| Sol 6, Neon 4, Grok 3: desktop readiness, keys and state dir | Readiness and key command for each path. Scratch state dirs. No reuse of the live Lantern space. |
| Sol 7: reviewer independence for Neon fixes | Grok and Sol review each Neon fix. Neon's final verdict does not cover its own fixes. |
| Grok 4, Neon 8: `agent_not_ready` and the `HERDR_HELPER_OK=1 herdr` literal | Both are in the S2 acceptance. |
| Neon 6: Windows path and desktop config path | Both are in the S1 acceptance. |
| Neon 9: write carve-out, guidance scope, field limits | The plan has a section on saving preference files, the guidance scope and the team field rules. |
| Grok 5, 6, Neon 7: retry and recovery scope | No retry after `agent_not_ready`. Recovery stays inside the stop list. |
| Sol 8, Grok 8, Neon 9: references and the precedence condition | Line numbers are hints only. The precedence change is approved. S7 is the Opus slice. |
| Grok 9: dead helpers | S5 removes the helpers that only the gates call. |
| Grok 10: trust records outside the scratch config | The authority section names them. |

## Ledger

Each row records one assignment or reply. A verdict names its commit. When the branch moves past a verdict's commit, that verdict and its open blockers are marked stale until they are re-reviewed.

| Turn | From | To | Scope | Commit | Result | State |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | t49y | t49z, t4b0 (/boost), t4b1 | R1 plan review of both documents | 3dea2c1 | sent | closed |
| 1 | John | t49y | "great, and follow our principles (like sol as worker)": implementation may follow sign-off; Sol is the only implementation writer | 3dea2c1 | recorded | in force |
| 2 | t49z | t49y | R1 plan review | 3dea2c1 | BLOCKING: 7 blocking, 1 advisory | stale, answered in revision 2 |
| 2 | t4b0 | t49y | R1 plan review (/boost) | 3dea2c1 | BLOCKING: 6 blocking, 3 advisory | stale, answered in revision 2 |
| 2 | t4b1 | t49y | R1 plan review | 3dea2c1 | BLOCKING: 4 blocking, 6 advisory | stale, answered in revision 2 |
| 3 | t49y | branch | Rebased onto origin/main d614403; plan revision 2 | a15b341 | done | closed |
| 3 | t49y | t49z, t4b0 (/boost), t4b1 | R2 plan re-review; sent before the run document revision landed | a15b341 | sent | closed; superseded by R3 |
| 4 | t49z | t49y | R2 plan re-review | a15b341 | BLOCKING: 6 blocking (3 on the stale run document, fixed in 8ceb8b7; new: Enter as progress, fresh-seat login, symlinked save path), 1 advisory | stale, answered in revision 3 |
| 4 | t4b0 | t49y | R2 plan re-review (/boost) | a15b341 | BLOCKING: 1 blocking (stale run document, fixed in 8ceb8b7), 2 advisory | stale, answered in revision 3 |
| 4 | t4b1 | t49y | R2 plan re-review | a15b341 | BLOCKING: 1 blocking (stale run document, fixed in 8ceb8b7), 3 advisory | stale, answered in revision 3 |
