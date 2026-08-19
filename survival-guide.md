# READ THIS FILE FIRST AFTER ANY COMPACTION OR RESTART

If this file contradicts what you think you remember, trust this file.

Read order after compaction: this file (Stop Gate and Run Control first) ->
`.elves-session.json` -> `learnings.md` -> `plans/windows-support.md` ->
`execution-log.md`.

---

## Mission

Make the Lantern Herdr plugin run on Windows through Git Bash. Herdr already
ships for Windows; the plugin excludes it in the manifest and carries POSIX and
macOS assumptions in the shell code. No behaviour change on macOS or Linux.
Messaging surfaces (Telegram, Slack, WhatsApp) are a later phase and are out of
scope.

---

## Run Control

- **Run mode:** finite
- **Stop policy:** stop after Batch 9, the final readiness review, and the PR
- **User intent:** "Set up a plan and use /elves to help you make edits." Windows
  first, agreed in session. Git Bash route, agreed in session.
- **Checkpoint due by:** none
- **Checkpoint semantics:** none
- **May continue after checkpoint:** yes
- **Actual stop conditions:** Batches 1-9 complete, readiness clean, PR open.
- **Workspace ownership:** owned branch `feat/windows-support` in the main
  checkout at `C:\Claude\herdr-lantern`. No other agent shares it.
- **Branch tip at start (collision tripwire):** `99e34fa17a2204a2227d21aafc9eb9ef548973ec`
- **Merge policy:** user-merges. Never merge. The user said "Do not merge to
  main; open a PR and stop."
- **Final-response policy:** allowed once the Stop Gate says yes
- **Coordination mode:** direct execution. This is mechanical porting work
  against a plan the user already approved.
- **Batch completion rule:** update execution log -> update survival guide ->
  commit -> push -> re-read this file.
- **Progress visibility rule:** commit subjects use
  `[feat/windows-support · Batch N/9 · Contract|Implement|Validate|Review|Close] <concrete outcome>`.
  No vague subjects.
- **Worker packet:** n/a — host-native
- **Handoff validation:** n/a — host-native
- **E2E mode:** direct
- **Work driver:** host-native
- **Implementation lane:** fast
- **Delegation scope:** none
- **Git mode:** host_only
- **Driver monitor mode:** interactive
- **Driver review policy:** final independent review only
- **Risk posture:** standard
- **Trust mode:** trusted
- **Landing outcome:** landable_pr
- **Driver merge authorized:** no
- **Worker merge authority:** false
- **Staging acceptance validation:** PASS
  (`acceptance_contract.py validate` exit 0, 2026-08-19)
- **Re-drive budget:** n/a — host-native
- **Continuation harness:** host-native
- **Re-read rule:** re-read this file immediately after every commit and push.

---

## Cobbler Session State

- **Cobbler default:** on
- **Activated by:** Elves invocation
- **Scope:** current Elves run
- **Behavior:** direct execution for the mechanical batches; Cobbler lenses for
  the B2 gate design and the final review if either turns non-trivial
- **Persistence:** survival guide and `.elves-session.json`

---

## Session Budget

- **Started:** 2026-08-19
- **User returns:** present in session (interactive)
- **Checkpoint expectation:** a landable PR with Batches 1-9 proven
- **Time budget:** unlimited within this session
- **Batches remaining:** 9 of 9

---

## Stop Gate

- **Planned batches remaining:** 9
- **Stop allowed right now:** no
- **Why:** no batch has started.
- **Next required action:** Start Batch 1. Declare windows in
  `herdr-plugin.toml` without touching the action id, pane id, pane title, or
  placement.

---

## Non-Negotiables

- No behaviour change on macOS or Linux. Every Windows branch is a no-op
  elsewhere.
- POSIX sh only in the shell files. They run under `sh`, not bash.
- Never weaken, skip, or delete a test to obtain green. `tests/smoke.sh:245`
  gets a platform detection, not a deletion.
- Never merge to `main`. Open a PR and stop.
- No AI attribution in commits: no `Co-Authored-By` naming a model, no
  "Generated with" line, no robot emoji.
- Never run `git reset --hard`, `git checkout .`, `git clean -fd`,
  `git push --force`, or `git rebase` on this branch.
- Never invoke bare `bash` in plugin code. On this machine it resolves to the
  WSL launcher stub, not Git Bash.

---

## Launch Readiness

- [x] Plan cleaned and saved to `plans/windows-support.md`
- [x] Survival guide written from the plan
- [x] Learnings file initialized
- [x] Execution log initialized with the batch breakdown and preflight notes
- [x] Branch `feat/windows-support` created off `main` at `99e34fa`
- [x] Branch and checkout ownership confirmed
- [ ] PR opened (opened after the staging commit)
- [x] Preflight run: origin reachable, push allowed, `gh` authenticated
- [x] Run mode and non-negotiables recorded
- [x] Stop Gate initialized with `Stop allowed right now: no`

---

## Current Phase

**Status:** Launch-ready

**Active batch:** Batch 1: Manifest platform gate

**What was just finished:** Staging. Plan, session, survival guide, learnings,
and execution log written. `acceptance_contract.py validate` passed.

**Single next action:** Start Batch 1.

---

## Active Compute

No active paid or long-running compute.

---

## Next Exact Batch

**Batch:** B1: Manifest platform gate

**Scope:**
- Add `windows` to `platforms` in `herdr-plugin.toml`.
- Leave the action id, pane id, pane title, and placement untouched.
- Confirm with `herdr plugin action list` after the Batch 9 relink.

**Acceptance criteria:**
- [ ] B1-A1: `herdr-plugin.toml` declares platforms linux, macos, and windows.
- [ ] B1-A2: The action id stays `open`, the pane id stays `helper`, the pane title stays `Lantern`, and the placement stays `tab`.
- [ ] B1-A3: `herdr plugin action list` reports windows in the action platforms after relink.

**Risk:** Low. The ids are asserted by `tests/smoke.sh:49,70,71`, so a slip is
caught by the gate.

**Rollback authority:** host-created `b1` rollback ref before the batch.

---

## Tool Configuration

```yaml
lint:        # none in this repo
typecheck:   # none in this repo
build:       # none in this repo
test: sh tests/smoke.sh
review: github-pr-comments
notification: pr-comment
```

Windows runs of the gate use
`& 'C:\Program Files\Git\bin\sh.exe' tests/smoke.sh` from PowerShell, so the
suite sees the plain Windows PATH that Herdr's launcher gives a plugin command.

---

## Plan and Log Paths

- **Plan:** `plans/windows-support.md`
- **Learnings:** `learnings.md`
- **Execution log:** `execution-log.md`
- **Branch:** `feat/windows-support`
- **PR number:** (fill in after the PR is created)

---

## Elves Report

- **Generate Elves Report:** yes
- **Default path:** `/tmp/elves-report-herdr-lantern-2026-08-19.html`
- **Commit report:** no

---

# READ THIS FILE FIRST AFTER ANY COMPACTION OR RESTART
