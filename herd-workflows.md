## Herd workflows

Match these invoke phrases before the general seat routes. Repository names,
run names, models, and efforts are slots. An invoke authorizes the stated
workflow for its named targets. It does not authorize unrelated runs.

| Invoke phrase | Action and stop point |
| --- | --- |
| `sweep <repos> with <model>` | Seat one audit agent per named repo. Find high ROI issues with file and line evidence. Check for duplicates before filing issues. Stop after the issue report. No Elves until the user names a run. |
| `issue harvest <repos>` | Read open issues with `gh -R <owner/repo> issue list --state open --json number,title,body,labels,url`. Page through all open issues. Group them into 1-3 landable Elves runs per repo. Give scope, issue URLs, dependencies, and acceptance for each run. Lantern brings the menu. The user picks. No writes, staging, or execution. |
| `stage <run> on <repo> with <model>` | Route staging to one supported Elves driver. Have it create a plan PR if needed, an implementation draft PR, a dedicated worktree, and the run records. Bind prewalk, execute, and independent review to the named routes. Stop when launch ready. |
| `landable loop <run> on <repo> with <model>, merge when clean` | One kickoff authorizes the named driver to audit, stage Elves, execute, review, fix, re-review, update docs + changelog + version, merge when clean, publish the GitHub version, check deploy, pull main, and report closable. Lantern monitors the whole loop. |
| `parallel pack <runs and repos> with <model>, merge when clean` | Run the same loop for each selected run across repos. Start all independent runs. Sequence dependencies. Keep one live driver per Elves run. Interrupt the user only for NEEDS YOU. |
| `cutoff resume <run>` | Recover the exact session, kind, model, effort, worktree, and phase. Restart a login picker without a keypress. Keep competing drivers dead. Continue the existing run. |
| `close bar` | List only tabs with merged work, current main, and a passed deploy check or a stated deployment block. Show tab names and evidence. The user names what to close. |

`landable loop` and `parallel pack` without `merge when clean` use the same
loop through a landable PR. Existing explicit merge authority for the named
run still applies. Never infer merge authority from a sweep, a menu choice,
a stage request, a green check, or another run. A stage request does not
execute. An execute or full loop request continues after launch readiness
without a second user prompt.

### Seat and driver ownership

Before any mutation, read `herdr workspace list`, `herdr tab list`, and
`herdr agent list`. Resolve real cwd paths. Read `herdr agent get <target>`,
`herdr agent read <target> --lines 80`, and
`herdr pane process-info --pane <pane_id>` for any candidate driver.
Reuse one workspace per cwd. A dedicated worktree has its own cwd.

Load the installed Elves skill for the selected driver. Supported drivers
are Codex, Claude Code, Grok Build, and OMP. Cursor and Pi can audit or review;
they are not Elves main drivers. If a named harness cannot drive Elves, report
NEEDS YOU and ask for a supported driver. Do not change it in silence.

When the kind is omitted, Astra or gpt-6 astra selects Codex. Fable selects
Claude Code unless the user names Cursor or another harness. An explicit
harness always wins and must have that model in its own catalog.

Resolve and preflight the driver and every named phase model before seating.
Use `bin/model-route` and `bin/model-preflight` for covered kinds. For OMP,
use `omp` help and its live catalog. Never infer OMP flags from another CLI.
Keep explicit phase choices. If the user names one model, use that model
for all requested phases. A different effort or guide/execution model needs
a recorded route choice. Do not silently replace Fable with Opus or Astra
with Sol. A substitute is a proposal, never a phase binding.

Use these actual Herdr routes with `HERDR_HELPER_OK=1` for mutations:

- No workspace: `herdr workspace create --cwd <dir> --label <repo> --no-focus`.
- Existing workspace, new seat: `herdr tab create --workspace <workspace_id>
  --cwd <dir> --label <run> --no-focus`.
- New worktree: `herdr worktree create --cwd <repo> --branch <branch>
  --base <ref> --path <worktree> --label <run> --no-focus`. Prefer the
  driver's Elves staging helper when it must register the worktree.
- Existing worktree without a workspace: `herdr worktree open --cwd <repo>
  --path <worktree> --label <run> --no-focus`.
- Start at a verified shell: `herdr agent start <slug> --kind <kind>
  --pane <pane_id> -- <verified model and permission args>`.
- Send the bounded kickoff only to an idle or done, interactive ready seat:
  `herdr agent prompt <target> "<kickoff>" --wait`.
- Label the seat: `herdr tab rename <tab_id> "<run> · <kind>"`.

Read IDs from command results. Do not guess them. Do not prompt a working
chat, including a driver parked while its worker runs. Observe it instead.
A fresh audit or driver request permits a new tab when the existing chat
is busy with other work. It never permits a second driver for the same run.
Before a retry after failed start, check the pane again. A timeout does not
prove that the process died.

Bind repo, run_id, worktree, branch, PR, driver pane, driver session ID,
kind, exact model, effort, and phase routes to the driver's run records.
Lantern reads these records. It does not edit `.elves-session.json`, plans,
survival guides, execution logs, or product files. Independent reviewers
are separate sessions. They return findings and have no driver authority.

### Stage and prewalk

The driver owns the plan, survival guide, learnings, execution log, acceptance
contract, worker packet, implementation draft PR, and registered worktree.
A separate plan PR is needed only when the repo or task needs plan review.
Use a feature branch. Record the named run and merge policy in Run Control.

Prewalk is one worker trajectory: guide route, bounded TODO, first meaningful
edit, private checkpoint, then exact session and same worktree resume on the
bound execute route with only `Continue.`. Send the worker packet once.
The Elves supervisor performs that transition. Lantern does not type it into
a working pane. Follow the installed Elves qualification rules. Required
prewalk stops if qualification fails. Never call a new chat with copied notes
prewalk. Never use a cold substitute after a task edit.

### Loop and independent monitoring

The driver audits the selected scope, stages, executes, and gets independent
review of the cumulative diff at the exact head. It fixes blocking findings
and gets re-review of the changes and unresolved findings. Docs, changelog,
and the repo's existing version scheme must be current before final checks.
Versioned repos get a version bump. Unversioned repos do not get a new scheme.

The driver reads PR comments and required checks. It removes draft state
only when ready. It merges only with explicit authority for this run and
clean evidence at the same head. Elves uses a regular merge commit. Lantern
never runs `gh pr merge` or `land-pr` and never edits product repositories.

After merge, the driver publishes the matching GitHub tag/release when the
repo uses that release process. Reuse release automation and existing tags.
Do not duplicate a release or add an unreviewed version commit on main.
The driver checks the deployment for the merged commit, pulls current main
with a fast forward in the intended checkout, and reports the commit and
result. A failed or unavailable deploy check is a named block, never a pass.

Lantern monitors independently with `herdr agent get/read/explain`,
`herdr agent wait <target> --until idle --until done --until blocked
--timeout 60000`, the run records, `gh pr view`, `gh pr checks`, and the
repo's read only deployment status command. Use bounded waits. Read the
existing Elves follow evidence. Do not run a competing worker supervisor.
After a quiet timeout, inspect process and progress evidence. Do not prompt
the chat for status. Silence, idle, and done are not proof of completion.

For a large pack, show one line per selected run: repo, run, phase, driver,
PR, and next gate. Track dependencies per run. Continue healthy runs when
another run blocks. Multi repo packs are separate Elves runs. Parallel
batches inside one repo belong to its driver and Elves lane checks, with
separate worktrees and disjoint owned surfaces. Do not launch overlapping
writers to increase pack width.

Routine in-scope permission prompts are handled below without interrupting
the user. NEEDS YOU means a real unresolved question, quota death, or dirty review
that the driver cannot resolve under the accepted scope. Include the exact
run, evidence, and one needed decision. Routine review fixes stay with the
driver. No timed status prompts to the user or to a working chat.

### Permission handling during monitoring

The user authorizes Lantern to grant routine permissions needed by selected
runs. A blocked permission prompt is a monitoring event. It is not a working
chat prompt and does not need a second user confirmation when it is within
the accepted run scope.

1. Read `herdr agent get <target>`, `herdr agent read <target> --lines 80`,
   and pane process info. Require a blocked state and the recorded run,
   session, kind, and pane. Read the exact action and its target.
2. Compare that action with the run's accepted scope and current phase.
   Permit required repo reads, worktree edits, tests, builds, feature branch
   commits and pushes, and the named run's authorized PR and deploy actions.
   Merge approval requires recorded merge authority and clean evidence at
   the exact head. It belongs to the Elves driver, never a worker or reviewer.
3. Select the visible permission option with the narrowest scope that allows
   that action. Prefer allow once. Use
   `HERDR_HELPER_OK=1 herdr agent send-keys <target> <verified-key>` only
   after checking the current screen again. Do not guess that Enter or y
   means approve. Use only keys shown by that UI.
4. Wait with `herdr agent wait <target> --until working --until idle
   --until done --until blocked --timeout 60000`. Read the pane again.
   Confirm that the same permission cleared and the intended process resumed.
   Do not send repeated keys to an unchanged screen.

A login picker, model picker, real question, quota error, unknown target,
or action outside the accepted scope is not a routine permission. Report
NEEDS YOU when the driver cannot resolve it. Do not grant permanent broad
access, change credential sources, or enable yolo, force, always approve,
bypassPermissions, or dangerously skip permissions without the user's
explicit yolo choice. Keep the required Codex unattended flag.

If approval belongs to an external host dialog rather than the agent pane,
report the exact pending action and the host. Do not send terminal keys as
an answer to a desktop dialog. A model cannot lift its parent's execution
policy. Do not route around a denied host permission through another tool.

### Cutoff recovery

Read run records, agent get/read, and pane process info first. Herdr
`session list` lists Herdr server sessions, not saved model conversations.
Get the exact model session ID from agent metadata and the run records.
Use the installed CLI's saved session inventory when needed. Never guess
an ID or use `--last`, `--continue`, or a picker for cutoff recovery.

If the recorded driver still runs, monitor it. If it died, verify no competing
driver or worker supervisor owns that run. When the named cutoff recovery
has a proven stale competing driver, terminate only that process by its
verified PID with `kill -TERM <pid>`. Check process info again before restart.
Do not close its tab to stop it. If ownership is unclear, report NEEDS YOU.
There is no `herdr agent kill` or `herdr agent restart` route on this host.

A login or account picker gets no Enter, y, arrows, or pasted credential.
Stop that exact process, wait for its pane to return to a shell, and restart
once with the recorded resume argv. If login still blocks, report NEEDS YOU.
Do not loop restarts. Do not revive competing drivers.

Resume through `herdr agent start` with the same kind and verified model args:
Codex `--dangerously-bypass-approvals-and-sandbox resume <session_id>`, Claude
`--resume <session_id>`, Cursor `--resume <chatId>`, Grok
`--resume <id>`, or OMP `-r <id>`. Keep recorded effort and permission args.
Verify the resumed session ID, worktree, and observed model before continuing.
A changed or unavailable route stops recovery. No silent substitute.

### Close bar evidence

Exclude Lantern home and any tab with working or unmerged work. For each
candidate, verify the PR is MERGED with `gh -R <owner/repo> pr view <number>
--json state,mergeCommit,url`. Check current remote main with
`git -C <repo> ls-remote origin refs/heads/main`. Check the intended local
checkout with `git -C <repo> branch --show-current`,
`git -C <repo> rev-parse HEAD`, and `git -C <repo> status --porcelain`.
Use the repo's actual default branch if it is not main. Require a clean
checkout on that branch at the remote tip. Require deploy evidence for the
merged commit or an explicit deployment block with its reason.

List the exact tab label, PR, main commit, and deploy result or BLOCKED reason.
Do not include an open PR as a blocked close candidate. The user names which
tabs to close. Recheck their evidence and identities before
`herdr tab close <tab_id>`. A workspace close also requires all child tabs to
be named and eligible. Never close Lantern home, its pane, or its workspace.

### Review transport checks

A listed model is not proof that a review can launch. Before a pack starts,
the driver must check the actual review transport from its execution context.
Keep kind, model, auth, local transport, sandbox, and review completion as
separate facts. A successful help or catalog read does not mean a review ran.

Use Claude Code for a named Claude review. Resolve with `model-route claude`
and preflight Claude usage. Seat `--kind claude` with verified model args and
`--permission-mode plan`, then send a findings only review request to the
ready seat. Never replace Claude Code with Opus through OMP or Cursor without
a user route choice. The same model name does not mean the same harness.

Agy uses `agy models` and `agy --help`, not the Cursor catalog. For a named
Gemini review through Agy, verify the exact listed ID and use its plan mode.
The installed route is `agy --model <listed-id> --effort <listed-effort>
--mode plan --print-timeout 15m --print "<review request>"`. Agy needs access
to its local state and localhost transport. Plan mode does not remove an
outer host sandbox. Do not add `--dangerously-skip-permissions` unless the
user names yolo. Agy is an optional reviewer, not an Elves main driver.

Grok Build is its own CLI and catalog. A direct Herdr review seat uses its
verified single turn route. An Elves Grok provider shortcut uses the active
Elves `scripts/run_grok.sh` with its required isolation and timeout intact.
These are different transports. Never remove a required sandbox from the
Elves runner to make it launch. On macOS, a nested `sandbox-exec` failure is
a transport block, not a model miss. OMP also needs its daemon state and any
configured auth broker. Do not silently change its credential source.

If the parent execution context denies a socket, localhost bind, state
write, or sandbox setup, stop that route and report the exact denied
resource. Do not retry with another model or bypass the parent boundary
through Herdr, a browser, or another tool. A pending host approval is not a
running review. Avoid competing approval requests that supersede each
other. Resume the exact driver in the authorized execution context when
that context becomes available. Codex seats must include the unattended
flag on resume as well as first start. It cannot remove an externally
managed execution restriction.

Record a review as complete only after its result names the reviewed commit
and returns findings or a clean result. A launch plan, a started process,
a timeout, or an empty result does not satisfy independent review.
