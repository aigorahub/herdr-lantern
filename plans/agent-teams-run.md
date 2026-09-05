# Plan: Lantern agent teams implementation

Implement the accepted design in agent-teams.md with the optional callback interface in team-protocol-v1.md. Elves owns execution, contributor review gates, and writer integration. Lantern owns message transport, observation, team instructions, and pack monitoring. Risk is high for identity and concurrency. Do not change merge authority or model pins.

## Batches

### Batch 1 [B1]: Durable reports

Owned surfaces: bin/team-mailbox, bin/team_mailbox.py, tests/teams.py.
Build on Python standard library and current private Lantern state.
Review focus: wrong recipients, duplicate effects, concurrent writers, stale claims.

**Acceptance criteria:**

- [x] B1-A1: Durable scoped messages survive retries and process failure without duplicate delivery or authority changes.
- [x] B1-A2: Receipt expiry and exact actor identity require explicit reconciliation before retry.

### Batch 2 [B2]: Observation and coordination

Owned surfaces: observer module, launch integration, team workflow contract.
Build on Herdr subscriptions and existing active monitor. Elves supplies checkpoint consumption and team scheduling.
Review focus: busy chats, spoofed events, unsupported transports, premature completion.

**Acceptance criteria:**

- [x] B2-A1: Bounded Herdr observation uses an explicit read only method allowlist and reports fallback without prompting chats.
- [x] B2-A2: Natural requests start driver and helper teams through the linked Elves contract with bounded capacity and independent final review.

### Batch 3 [B3]: Verification and release preparation

Owned surfaces: tests, README, guides, changelog and version surfaces.
Build on smoke CI and local live trials. Review the complete branch independently.

**Acceptance criteria:**

- [x] B3-A1: Cross-repo callback and team scenarios pass with real CLI processes and captured evidence.
- [x] B3-A2: Documentation and version agree with tested behavior and all required checks pass.

## Master Acceptance

- [x] M-A1: The PR has a clean independent review at the final commit and remains unmerged.

## Verification evidence

Lantern cumulative product review is clean at 222730a. Test review is clean
at 6701e16. Final contract and documentation review is clean at 067aca1.
GitHub Actions run 33994233798 passes on Linux, macOS, and Windows.
Thirty team tests pass, with the platform-specific Windows test skipped locally.
The 100 task concurrent report test verifies unique claims and blocked report priority.

The real CLI integration at Lantern 067aca1 and Elves f97c0d1 verifies helper
setup, scoped packet generation, reporting, checkpoint consumption, and receipt
completion. Its transport identities are fixtures; it makes no model calls.
Live Herdr observation also passed. Reports arrive at safe checkpoints and do
not wake model chats. Elves Windows execution requires WSL2.

The implementation remains in PR 37, linked to Elves PR 275. Merge is not authorized.
