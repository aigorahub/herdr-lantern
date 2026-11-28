# Lantern 0.20.0 issue cleanup run: staging and ledger

Plan: `plans/issue-cleanup.md`. Branch: `chore/issue-cleanup`. Worktree: `/Users/john/aigora/dev/herdr-lantern-issue-cleanup`. Base: `origin/main` 4ac9846.

## Run Control

- Mode: finite. Stops when the PR is merged, v0.20.0 is released and all five issues are closed with a reply.
- Authority: John authorized the merge, the version bump and the GitHub release for this run in the current session. Regular merge commit only. No other merge, deploy or publication authority.
- Work driver: Sol (t4kv), native Codex worker, `branch_progress` on `chore/issue-cleanup` only. Worker packet: `plans/issue-cleanup-packet.md`.
- Saved Codex usage resets: off for this run (the team was not registered through Lantern home).

## Roster

| Seat | Agent | Role |
| --- | --- | --- |
| t4kt | Opus 5.5 high (Claude Code) | Lead: plan, stage, assign, review, release prep, landing |
| t4kv | Sol 6.2 high Ultrafast (Codex) | Worker: the only writer for S1 to S5 |
| t4kw | Agy Gemini 4 Neon high | Planner (Hydrogen swarm) and reviewer |
| t4kx | Agy Gemini 4 Carbon high | Planner and reviewer |

## Next action

Sol implements S1 to S5 from the packet.

## Ledger

- 2026-11-28: Neon (Hydrogen swarm) and Carbon planned independently. The lead merged the plans into revision 1 and decided the four disputed items (#91, #92.1, #92.5, #92.6).
- S1 #93: Verified direct child PID and argv for both fixed shell wrappers. On 4ac9846 the real child PID, shell fallback, login wrapper replacement, and Plan Usage child account assertions fail. t4kt extended S1 scope to the Field Status reap fixture only. It now supplies a live child at the OS boundary and retains the close assertion. Smoke, Desktop, Field Status, and Python 3.9 compilation passed.
