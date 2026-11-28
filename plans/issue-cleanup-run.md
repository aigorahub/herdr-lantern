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
- S2 #91: Complete survives the legacy done to idle transition with unchanged sequences. On 4ac9846 the CLI refresh loses the recorded outcome summary after the view transition. The test checks raw idle status and rejects a new completion sequence. Smoke, Field Status, and Python 3.9 compilation passed.
- S3 #92.4 and #92.6: Field Status and the read-model launcher consume the desktop protocol over stub app and core programs. On 4ac9846 the Cursor and omp alias assertions, omp foreground match, refresh agent identity, and login wrapper child PID assertions fail. Smoke, Desktop, Field Status, and Python 3.9 compilation passed.
- S4 #92.5: Added the Windows batch launchers, native Python child launches, cmd quoting, and in-process watcher identity for replacement. On 4ac9846 the install file, hook quoting, helper exit code and reuse, watcher receipt, and stale watcher replacement assertions fail. Local smoke, Desktop, and Python 3.9 compilation passed. Windows CI now runs both batch launchers against a stub core. Live Windows Herdr desktop remains unverified.
- S5 #92.1 and #92.3: Desktop onboarding reads its config and state paths and checks hc and herdr-app. On 4ac9846 the desktop apply smoke fails because it requires HERDR_PLUGIN_CONFIG_DIR, and launcher usage omits install and system. Updated desktop spawn instructions and corrected the Windows and parked sub-agent docs. Smoke, Desktop, Teams, Agent Switch, and Python 3.9 compilation passed.
