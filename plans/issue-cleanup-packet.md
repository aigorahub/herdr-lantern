# Worker packet: Lantern issue cleanup S1 to S5

To: Sol (t4kv). From: lead t4kt. Date: 2026-11-28.

## Intent

Close every open Lantern issue. Fix the ones we want, as listed in `plans/issue-cleanup.md`. The lead posts the replies for the rest. You implement S1 to S5.

## Why it matters

- #93 and #91 give wrong answers in Plan Usage and Field Status.
- #92.6 makes Cursor and omp desktop panes unmatched.
- #92.5 and #92.1 make desktop Lantern usable on Windows and make `onboard` usable in a desktop session.

## Build on

- `bin/desktop.py`: `native_process`, `original_launch`, `APP_AGENT_NAMES`, `chat`, `install`, `open_home`, `ensure_views`, `install_claude_hooks`.
- `bin/usage_runtime.py`: `child_pids`, `process_start`, and the process identity helpers.
- `bin/field_status.py`: `outcome_token`.
- `bin/win_cmd.py` (`cmd_argv`), and `bin/codex-headless.cmd` with its `.gitattributes` line, as the `.cmd` pattern.
- The existing tests in `tests/desktop.py`, `tests/plan_usage.py`, `tests/field_status.py` and `tests/smoke.sh`. Extend existing tests before you add new ones.

## Owned surfaces

The files named in the S1 to S5 rows of the plan. Plus `plans/issue-cleanup-run.md`, where you add one ledger line for each slice.

## Forbidden surfaces

- `CHANGELOG.md` and all version strings. S6 is the lead's.
- Lantern config under `~/.config/herdr`, and the live checkout `/Users/john/aigora/dev/herdr-lantern`.
- Any other branch. Do not merge, tag, open or edit PRs, or comment on issues.
- Do not weaken or skip a test to get green.

## Acceptance evidence

These items are required for each slice:

1. The plan row's acceptance is met.
2. A test that fails on 4ac9846 because of the defect, for every fix. Name the failing assertion in the ledger line.
3. `sh tests/smoke.sh` passes, and the affected Python suites pass: `python3 tests/desktop.py`, `tests/plan_usage.py`, `tests/field_status.py`, `tests/teams.py` and `tests/agent_switch.py` as they apply.
4. `python3 -m py_compile` passes on Python 3.9 syntax (CI `oldest-python`).
5. A pushed commit. The subject shape is `[chore/issue-cleanup · Slice Sn · Implement|Validate|Close] <concrete outcome>`. The Close commit carries `Confidence: <level>` or `Confidence: <level> — unsure: <items>`.

## Pitfalls

- **S1:**
  - Never relax the executable or two-read checks in Plan Usage or the reset helper.
  - If no live direct child matches the wrapped argv, or more than one matches, keep the shell process. The result must fail closed.
  - Do not parse arbitrary shell code. Accept only the two fixed wrapper shapes.
- **S2:**
  - Normalize only the token input.
  - The row keeps its real `raw_status`, so reap still skips idle rows and outcome recording still rejects blocked and error rows.
- **S4:**
  - Point `LANTERN_DESKTOP_COMMAND` (`bin/desktop.py:443`) to `bin/lantern-app.cmd` on Windows, the same as `READER`.
  - Put every Windows change behind `os.name == "nt"`. POSIX behavior and argv stay byte-for-byte the same.
  - Windows desktop is experimental. Write the docs to say that.
- **S5:**
  - `onboard` keeps its legacy behavior when `HERDR_PLUGIN_*` is set.
  - Update `cmd_plan` (`bin/onboard:75`) together with `cmd_doctor` (`:188`) when you accept `hc` and `herdr-app` without legacy `herdr`.
- No arbitrary limits, and no new config keys.
- Comments explain why, not what.

## Identity and output

- Worktree `/Users/john/aigora/dev/herdr-lantern-issue-cleanup`, branch `chore/issue-cleanup`, HEAD 4ac9846 plus the lead's staging commit.
- Push progress after each slice.
- When all five slices are done, run `hc send t4kt` with a report:
  - each slice with its commits and the tests that fail on 4ac9846;
  - the commands you ran and their results;
  - anything you could not prove, such as Windows behavior beyond CI;
  - the head SHA.
- If you are blocked, send the block to t4kt and stop on that slice.
