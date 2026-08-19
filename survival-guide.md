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

- **Planned batches remaining:** 8 (Batches 2-9)
- **Stop allowed right now:** no
- **Why:** Batches 1 and 10 are closed; eight remain.
- **Next required action:** Start Batch 2. Add `bin/herdr.cmd` so the mutation
  gate holds for native Windows callers.

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

**Status:** In progress

**Active batch:** Batch 2: Mutation gate on Windows

**What was just finished:** Batch 10. `.gitattributes` pins the interpreted
files to LF, the working tree carries no CR bytes, and the change is endings
only.

**Single next action:** Start Batch 2.

---

## Active Compute

No active paid or long-running compute.

---

## Next Exact Batch

**Batch:** B2: Mutation gate on Windows

**Scope:**
- Add `bin/herdr.cmd`, forwarding argv to the POSIX wrapper through `sh.exe`
  and exiting with the wrapper's exit code.
- Resolve `sh.exe` from PATH first, then `C:\Program Files\Git\bin\sh.exe`.
  When neither exists, fail loudly. Never fall through to the real herdr.
- Add smoke coverage for the blocked path, the inspect path, and the
  Git Bash resolution order. Skip the Windows-only cases elsewhere.

**Acceptance criteria:**
- [ ] B2-A1: `bin/herdr.cmd` forwards its arguments to the POSIX wrapper through `sh.exe` and exits with the wrapper's exit code.
- [ ] B2-A2: A mutating command through `bin/herdr.cmd` without `HERDR_HELPER_OK` exits non-zero and prints the `HERDR_HELPER_OK` hint.
- [ ] B2-A3: An inspect command through `bin/herdr.cmd` reaches the real herdr.
- [ ] B2-A4: With both files present, Git Bash `command -v herdr` still selects the extensionless wrapper.
- [ ] B2-A5: `bin/herdr.cmd` never falls through to the real herdr when `sh.exe` is missing.

**Risk:** Highest in the run. The gate is a security control and the Windows
failure mode is silent. A `.cmd` that mangles arguments or swallows the exit
code looks like success. Test the block, the pass, and the missing-shell path
separately.

**Rollback authority:** host-created `b2` rollback ref before the batch.

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
