# Lantern 0.19.0: managerial guidance, team files, workflow files, general startup rule

## Context

John wants four user-configurable improvements in one PR from `plan/team-preferences` (base `main` 3769eb9).

1. **Managerial guidance.** Default guidance for any agent that manages other agents, which the user can adjust. Lantern passes it to every team lead it sets up.
2. **Team files.** Each user saves teams that map roles to harness, model, effort and service tier. John's roster is his preference, not a product default.
3. **Workflow files.** Each user saves named working principles with aliases, so "set up the elves workflow" works.
4. **Startup blockers.** One general rule lets the lead clear initial blockers (workspace trust, first-run confirms) for any harness. At 3769eb9 only the legacy `lib.sh` wrapper has gates, for Codex and Claude only (`lib.sh:1131-1134`). The desktop path (`herdr-app do tab.new`, `desktop-prompt.md:21-28`) has none, so Agy and Grok seats stop at startup.

Design principle (John): Lantern leads and team leads are frontier models. Give them goals and boundaries, not scripts. Use code only where exactness matters: model IDs, argv, and the authority boundary.

## Facts that shape the design

- `launch.sh:60-64` copies `prompt.md` to the config dir once and never refreshes it. User copies keep the old gate text at `prompt.md:487-497` ("Do not send y or Enter yourself"). New rules must go in files read on every launch (the `launch.sh` appendix `:214-562`, or files read like `herd-workflows.md` at `:195`). They must override the old paragraph by name.
- The desktop app is a second prompt path: `bin/desktop.py:359-371` builds from `desktop-prompt.md` and uses `LANTERN_CONFIG_DIR` (default `~/.config/herdr/plugins/config/aigora.lantern`). Every rule goes to both paths.
- `herd-workflows.md:210-211` says "Reuse Elves saved role routes ... Do not create a second helper route list." The same position is in `plans/agent-teams.md:236-238` and `README.md:612-614`. Proposed resolution: a team that the user names is an explicit user choice, and explicit choices already outrank Elves routes (`herd-workflows.md:211-212`). Elves routes stay the fallback when the user names no team. This reverses written product text, so John decides.
- `model-route` covers codex, claude, cursor, grok, fugu and gemini. Agy has no route. `desktop-prompt.md:42-47` already tells the lead to check the CLI help and the live catalog for other harnesses, and to pin the Agy model and effort.
- `tests/smoke.sh:16-23` fails on an ASCII apostrophe in the appendix heredoc. Rule text must avoid apostrophes ("what is new", not "what's new"). Do not paste user prose into the heredoc.
- `send-keys` is blocked unless rerun with `HERDR_HELPER_OK=1` (`launch.sh:277-279`). The new rule must permit it for startup blockers on the seat the lead just started.

## Design

### Managerial guidance

- Shipped default: a new `guidance.md` in the plugin root. Both prompt paths read it on every launch, like `herd-workflows.md`, so it never goes stale.
- User adjustments: `$config_dir/guidance.md`, optional. It adds to the default or overrides it. On a conflict, the user text wins.
- The defaults (prose, short):
  - The manager plans the work and divides it, to get all of it done correctly as fast as the available resources allow, inside the other rules.
  - Give each agent verifiable goals, acceptance checks and constraints, then let it work out the details. Do not over-prompt or micromanage capable models.
  - Image and video models can see reference images. Give them the reference and do not describe it in words.
  - Route each task to the seat that does it best. Send multimodal analysis (video, audio, visual inspection) to the strongest multimodal seat on the team.
- When Lantern sets up a team, it gives the team lead the merged guidance with the roster.
- "Remember that as manager guidance" means the lead adds a line to the user file.
- John's own line (after merge, with his approval): "Send video, audio and visual inspection to Agy."

### Team files: `$config_dir/teams/<name>.json`

Small JSON, because the values must be exact: `name`, `aliases`, `agents[]` with `role` (free text, such as "reviewer; cleanup worker when assigned"), `harness`, `model`, `effort`, optional `service_tier`, `mode`, `label`, `notes`. An optional `startup` field is `"auto"` by default, or `"ask"` to report blockers instead of clearing them. Free-text `coordination` notes are allowed.

- The lead reads the directory when a request names a team or alias, so a file saved mid-session works.
- When the lead opens a team, it checks each seat with `model-route` and `model-preflight` where a route exists. For other harnesses (Agy) it uses the installed CLI help and live catalog. An unavailable model is reported, not substituted.
- "Save this as my X team" means the lead writes the file. No new CLI.
- Opening a team is setup only. The file cannot grant merge, deploy, publication, permission or financial authority.

### Workflow files: `$config_dir/workflows/<name>.md`

Markdown prose. The first lines give the name, `Aliases:` and an optional `Default team:`. The body is working principles. No phase engine.

- User workflows load after the built-in `herd-workflows.md`. A user alias that collides with a built-in phrase is reported once, and the built-in wins.
- "Set up the X workflow [with the Y team]" means: open the team (named, else the default team, else ask once), give every seat the full roster and roles, give the lead the guidance and the principles, and wait for a task.
- A workflow file or alias grants no landing authority. Built-in Ship differs, because there the request states the authority. A user workflow run lands only when the current request says so.
- "Save this as my X workflow" means the lead writes the file.

### Startup blockers: one general rule

Goal: get each seat that the lead just opened to `interactive_ready` and idle or done. At each blocker, the lead selects the least-privilege option that lets the seat start in its configured state.

- Clear: trust for the workspace that Lantern opened for that seat, first-run confirms, new chat, welcome screens, notes about what is new, tips, theme, declining telemetry, skipping an update for now.
- Stop and report: login, account choice, plan, quota or payment; any tool, command, file or network permission prompt; any permission-mode increase not named in the current request; trust for another directory; installs or updates; any screen it cannot classify.
- Mechanics: read the exact pane before each key, send keys only to that seat, confirm the screen changed after each key, and stop when a key changes nothing.
- Lessons from the removed gates, in neutral words: the preselected option can be the exit option, so move to the trust option first. Several gates can appear in sequence. A y/n prompt can still need Enter.
- The lead decides how to recover a seat that Herdr reports as not ready.
- When the team file sets `startup: "ask"`, report instead of clearing.

## Files

- New `guidance.md`. `launch.sh` reads it next to `:195`, reads the user file if present, and adds both near `:560`. `bin/desktop.py:359-371` does the same.
- `launch.sh`: replace the gate paragraph `:300-309`. State that the new rule overrides the old user `prompt.md` gate paragraph. Allow `HERDR_HELPER_OK=1` for startup blockers. Align the retry rule `:514-515`. Add the team and workflow paths and rules (path variables only).
- `desktop-prompt.md`: the startup rule near `:26`, and the team, workflow and guidance rules near `:59-63`, with `LANTERN_CONFIG_DIR` paths.
- `prompt.md:487-497`: the new rule, for new installs.
- `herd-workflows.md:210-212`, `plans/agent-teams.md:236-238`, `README.md:612-614`: the precedence wording, if John agrees.
- `README.md`: new sections for guidance, team files and workflow files, with formats and examples. Replace the gate text `:413-415`.
- `lib.sh` (last code slice): remove `:785-981` except `helper_pane_has_login_picker` (`:711`, used by the prompt relay), and the gate tail of `helper_relay_agent_start` from about `:1131`. Keep the Codex bypass flag position and the launch guard. Update the comment `:1051-1060`.
- `tests/smoke.sh`: the guidance file must exist, and both prompt paths must include it, because a missing include fails silently. Extend `:3203-3206` with one assertion: the appendix overrides the old user `prompt.md` gate paragraph, the same silent-failure class. In the last code slice, delete the tests of the removed helpers (`:1458-1626`, `:1722-2237`). Keep `:1432-1452`.
- `CHANGELOG.md` and version 0.19.0 in `herdr-plugin.toml:3`, `README.md:5`, `howto.html:185`, `docs/index.html:265` and `docs/teams.html` (`smoke.sh:883-902` enforces that they match).

## Slices

1. **S1 Guidance.** Default file, user file, both prompt paths, test, README.
2. **S2 Startup rule.** The rule in all three prompt texts, `HERDR_HELPER_OK` permission, override assertion, README. Gate code stays for now.
3. **S3 Team files.** Format, rules, precedence wording, README.
4. **S4 Workflow files.** Format, matching, authority, README.
5. **S5 Live check.** Sol first writes in the ledger the exact command that runs a branch-build Lantern lead on the desktop path and on the legacy `launch.sh` path, with `LANTERN_CONFIG_DIR` / `HERDR_PLUGIN_CONFIG_DIR` set to a scratch config dir. One scratch config has an old `prompt.md`. Then the lead under test opens Codex, Claude, Agy and Grok seats in a new untrusted scratch directory. Pass: each seat reaches ready with no human keys, and no key goes to a runtime permission prompt. Record the pane text in the ledger. This adds trust entries for that scratch directory in each CLI's local config. Login and payment stops are covered by the rule text only. A live test would need a logout.
6. **S6 Gate removal.** Only after S5 passes on all four harnesses.
7. **S7 Release prep (Opus).** CHANGELOG, version 0.19.0, docs.

## Team workflow (John's Elves principles)

Roster: t49y Opus 5.5 high, lead. t49z Sol 6.2 high Ultrafast, worker. t4b0 Agy Gemini 4 Neon high, reviewer and assigned cleanup worker. t4b1 Grok 4.7 high (Grok Build), reviewer. Messages go through `hc send`. Monitoring goes through `hc wait`, `hc get` and `hc read`.

1. Commit this plan as `plans/team-preferences.md`, and a staging document `plans/team-preferences-run.md` (slices, owner, write scope, acceptance, ledger), on `plan/team-preferences`.
2. **Plan review.** Sol, Neon and Grok each review both documents independently at that commit. Every Agy review and re-review starts with `/boost` in plan mode. Opus revises and asks for re-review until all three sign off on the same commit.
3. **Stop.** Opus reports the sign-off to John. Implementation starts only on John's go.
4. **Implementation.** Sol gets each slice as verifiable goals, then works through S1-S6 without routine pauses, with one commit per slice.
5. **Slice review.** Neon and Grok review each slice at its commit, behind Sol.
6. **Cleanup.** Opus assigns blocking fixes to Neon in a separate worktree based on that slice commit. The slices share files (`launch.sh`, `README.md`, `desktop-prompt.md`), so Opus integrates Neon's fixes at the next slice boundary, and Sol rebases before the next slice commit. Grok reviews Neon's fixes, because Neon cannot review its own work.
7. **Final review.** Neon, Grok and Opus review the cumulative diff from `main` and give Sol feedback until all are green on the current commit.
8. **S7 and PR.** Opus updates docs, changelog and version and opens the PR. The full CI runs once.
9. **Stop.** No merge or GitHub release without John's explicit landing authority in the current session.

Ledger rules (from the retrospective): record each assignment and reply with its turn, scope and commit. Each verdict names its commit. A verdict for an older commit is marked stale, together with any open blocker. A slice is done only when its tests ran and its reviews are green on its commit.

After merge, only with John's approval: move `standard-team.json` to `teams/standard.json` with the current roster (Sol 6.2, Neon high, Grok 4.7 high), add John's Agy multimodal line to his `guidance.md`, save his Elves workflow as `workflows/elves.md`, and update the pointer in `~/.claude/CLAUDE.md`.

## Verification

- `sh tests/smoke.sh` and the separate CI suites (`teams`, `agent_switch`, `desktop`, `codex_jobs`, `session_cleanup`) pass after each slice. The full CI runs once on the PR.
- S5 live evidence for all four harnesses on both launch paths, including the stale `prompt.md` case.
- Manual: save guidance, a team and a workflow by chat. Then "set up the elves workflow with the standard team" opens four seats with the right models and roles, the lead gets the guidance, and the team waits for a task.
