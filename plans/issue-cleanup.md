# Lantern issue cleanup

Revision 2, 2026-11-28 (Neon and Carbon agreed). Base: `origin/main` 4ac9846. Branch: `chore/issue-cleanup`.

Goal: close every open issue. Fix what we might one day want. Reply and close what is cruft, already done, or not possible to act on.
John decided: no goal budgets (#83). Avoid strict checks and arbitrary limits. Use the fewest tests that catch real failures. A fix test fails on 4ac9846 because of the defect.

## Verdicts

| Issue | Verdict | Reason |
| --- | --- | --- |
| #83 goal budgets | Reply, close | John declined goal budgets. Main has no goal-budget code or docs. |
| #90 hc event feed | Reply, close | PR #85 removed the main cost. The event feed has no foreground or cwd changes, and each readout needs sources outside hc. |
| #91 outcome revert | Fix (S2) | Legacy Herdr marks a viewed done agent idle (`state_label`, `mark_active_tab_seen`) with no new sequence. Bridged panes take that status, so a Complete outcome reverts. Native core keeps `done`. |
| #92.1 legacy tools | Fix `onboard` (S5); reply for the rest | `bin/onboard` fails in a desktop session (needs `HERDR_PLUGIN_*` and `herdr`). `hsh`, `evening.sh`, `lantern_session.py`, `team_observer.py` and `agent_switch.py start` stay legacy-only; desktop has native equivalents. |
| #92.2 state carry-over | Reply | Notes, pins and mailbox rows are keyed to legacy pane IDs. Shared config is already common. |
| #92.3 docs | Fix (S5) | Stale out-of-tab sub-agent wording in `howto.html` and `docs/index.html`; `lantern-app` usage omits `install` and `system`; docs for S4 and S5. |
| #92.4 tests | Fix (S3) | No end-to-end test runs a readout through `bin/lantern-app-read`. `ensure_views` tests already exist. |
| #92.5 Windows desktop | Fix as experimental (S4) | Herdr desktop has Windows code paths. Lantern desktop has no `.cmd` launchers, uses `os.execv`, `sh` launch argv and POSIX hook quoting. CI has no Windows Herdr desktop, so docs call Windows desktop experimental. |
| #92.6 app kinds | Fix (S3) | Herdr.app maps `agent` and `cursor-agent` to Cursor and `oh-my-pi` to omp. `APP_AGENT_NAMES` does not. |
| #93 wrapper PID | Fix (S1) | `native_process` keeps the shell PID with the agent argv and misses the `-lic` / `-li` login wrapper that Herdr.app uses. |

## Slices

Sol (t4kv) writes S1 to S5, one or more commits each. Every slice keeps `sh tests/smoke.sh` and the affected suites passing.

| Slice | Write scope | Acceptance |
| --- | --- | --- |
| S1 #93 | `bin/desktop.py`, `tests/desktop.py`, `tests/plan_usage.py` | `native_process` and `original_launch` accept the `-ic`/`-i` and `-lic`/`-li` wrappers. For a wrapper, the foreground becomes the one live direct child whose argv matches the wrapped argv, with that child's PID. No unique child keeps the shell process, so Plan Usage still reports Account unknown. The executable and two-read checks stay as strict as now. Tests: a real wrapped child resolves to the child PID (fails on main), and Plan Usage binds the account for a wrapped terminal (fails on main). |
| S2 #91 | `bin/field_status.py`, `tests/field_status.py` | `outcome_token` treats `done` and `idle` as the same state. The row keeps its real `raw_status`, so reap and outcome rules do not change. A done agent that becomes idle with the same sequences keeps Complete (fails on main). New work or a new sequence still invalidates it. |
| S3 #92.6, #92.4 | `bin/desktop.py`, `bin/field_status.py`, `tests/desktop.py` | `APP_AGENT_NAMES` maps `agent` and `cursor-agent` to `cursor`, and `oh-my-pi` to `omp` (test fails on main). `AGENT_PROGRAMS` in `bin/field_status.py` has `omp`, so reap matches an omp pane. One end-to-end test runs `field_status.py --herdr bin/lantern-app-read refresh` and `lantern-app-read pane process-info` over stub `hc` and `herdr-app` programs. Its stub has a `cursor-agent` app and a `-lic` wrapped terminal, so it fails on main. |
| S4 #92.5 | `bin/desktop.py`, `bin/lantern-app.cmd`, `bin/lantern-app-read.cmd`, `.gitattributes`, `tests/desktop.py`, `tests/smoke.sh` | On Windows: `.cmd` launchers exist; `READER` uses the `.cmd` reader; `install` also writes `lantern-app.cmd`; launches use `[sys.executable, bin/desktop.py, ...]` instead of `sh`; `chat` and the readout views wait on a child process instead of `os.execv`; Claude hook commands use Windows quoting. `chat` wraps a `.cmd` or `.bat` helper with `win_cmd.cmd_argv`, and `active_helper` strips `.cmd` and `.bat` as it does `.exe`, so `open_home` reuses a live chat instead of opening a second one. A Windows readout keeps a PID and launch argv that `record_start`, `launched` and `original_launch` recognize (for example, the watcher runs in-process), so an old stock readout is still found and replaced on upgrade. POSIX behavior is unchanged. Unit tests with `os.name == "nt"` fail on main. The Windows CI smoke job runs both `.cmd` files. |
| S5 #92.1, #92.3 | `bin/onboard`, `desktop-prompt.md`, `bin/desktop.py` (usage text), `README.md`, `howto.html`, `docs/index.html`, `tests/smoke.sh` | `onboard show`, `apply` and `doctor` work with `LANTERN_CONFIG_DIR` and `LANTERN_DESKTOP_STATE_DIR` and with `hc` and `herdr-app` instead of `herdr` (smoke test fails on main). `desktop-prompt.md` points to `onboard` for spawn defaults. Docs fix the sub-agent wording, name Windows desktop as experimental, and describe desktop `onboard`. `lantern-app` usage names every command. |
| S6 release (lead) | `CHANGELOG.md`, version files | Version 0.20.0 on every pinned surface. CHANGELOG entry. |

## Review and landing

Full review of the cumulative diff by Opus (t4kt), Neon (t4kw) and Carbon (t4kx). Sol fixes. Each fix gets a delta review by the reviewer whose finding it answers. Full CI on the PR after reviewers are green, including the Windows smoke job. John authorized the merge for this run: regular merge commit, then the v0.20.0 release, the plugin reinstall and the live checkout pull. Then post the replies and close all five issues.
