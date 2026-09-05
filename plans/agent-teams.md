# Plan: Agent teams and reliable coordination

Status: implementation authorized and staged on isolated branches.
Date: 2026-09-05.

## Outcome

One request can assign several model families to one task or to many repos.
Lantern starts the teams, tracks dependencies, receives reports, and keeps work
moving. An Elves driver owns each development run. Helpers investigate, propose,
implement assigned parts, or critique proposals. A separate reviewer checks the
final changes before the driver can land them.

Use two linked Elves runs for implementation, one in Lantern and one in Elves.
Keep one shared acceptance plan and one driver per run. Stage the transport and
team contracts before launching writers. Open draft PRs at the first useful
push. Implement in dependency order. This plan does not start or authorize a
merge of those future PRs.

## Existing code and rules

- Lantern 0.11.0 stores pack records under `LANTERN_HERD_STATE_DIR`.
  `launch.sh` creates the private directory and injects `herd-workflows.md`.
  The workflow requires recurring checks, exact owner identity, scoped
  permissions, early PRs, and verified completion.
- `bin/herdr` and `lib.sh:helper_relay_agent_prompt` check target readiness.
  They do not provide durable queued delivery. A readiness read followed by
  a prompt is not an atomic reservation of a chat turn.
- Installed Herdr 0.8.2 exposes `events.subscribe`, `events.wait`, agent state
  events, output matching, and native session references. Its schema has no
  exact `agent.send` method. Event subscription does not itself wake a model.
- Elves 2.36.1 already has Cobbler role routing, independent proposals, critique,
  synthesis, saved model preferences, and worker lifecycle contracts.
  Reuse `references/council-workflow.md` and the existing onboarding helpers.
  `cobbler_runtime/dispatch.py` already launches independent read only lanes
  concurrently and collects structured reports. Extend that dispatch for the
  critique round before adding another proposal runner.
- Elves `parallel_lanes.py` validates work partitions and has an in-memory
  `LaneSupervisor`. Process spawning belongs to its caller. This is not yet
  a persistent team scheduler. `references/parallelves.md` still describes
  runtime supervision as future work. Align that document with tested code.
  A local trial also found that a registered `pending` lane returns
  `all_terminal: true` while `ok_to_integrate: false`. Fix and test terminal
  classification before using this helper to stop team monitoring.

Implementation must inspect current source and installed capabilities again.
These observations are version specific. Do not copy old model defaults or
treat a documented adapter as proof that its current transport works.

## User experience

Keep existing Ship behavior. Add natural requests that describe the work:

> Brainstorm ways to simplify onboarding. Have three models compare approaches.

> Investigate slow checkout. Give the driver database and frontend helpers.

> Ship saved carts in storefront. Use helpers where useful.

> Have Claude and Codex propose solutions independently, then compare them.

Brainstorming and investigation stop with findings. They do not imply edits,
PRs, or merge. Ship retains its current merge meaning and earlier stop options.
Explicit team size, model choices, constraints, and stop points win over defaults.

Report the lead, assigned helpers, expected result, and material route failures.
Normal status answers show progress, decisions, and blockers. Do not require the
user to learn internal message names or supply run IDs.

## Team patterns

### Driver and helpers

Use this as the normal team structure. The driver assigns each helper a bounded
question or output. Read only helpers can inspect the same source tree. A helper
cannot change the plan, acquire a writer role, or spawn more helpers by itself.
The driver can approve a request for another helper within the accepted budget.

Helpers can exchange evidence and interface questions through the message
service. The driver receives decisions and blockers, rather than every remark.
Record the sender and task on every message. A peer request is not a user command.

### Brainstorm and compare

Give each proposer the same brief, constraints, sources, and evaluation criteria.
Keep first proposals separate until all return or their recorded deadlines pass.
Then share the proposals for one critique round. The lead produces a recommendation
with evidence, tradeoffs, and unresolved disagreement. Do not decide by vote or
model reputation. A missing proposal remains missing in the report.

Default to a lead and two proposers when the user requests several models without
a count. Use different families when available under saved choices. Expand only
within the recorded team limit. Another round needs a concrete unresolved question.

### Parallel implementation

Reuse Elves lane partitioning and worker permissions. Build shared interfaces in
a driver owned foundation step. Assign each writer its own worktree, branch, and
owned paths. Independent lanes can run together. Dependent work waits for its
prerequisites. Helpers discuss interface changes before either changes the contract.

Only the driver integrates lane results. If paths or dependencies overlap,
repartition or continue in sequence. Do not treat file ownership in a prompt as
an enforced sandbox. Validate the actual diff before integration.

Competing implementations use separate scratch worktrees. Compare them against
the same tests and criteria. Choose one result. Do not combine them automatically.

### Independent landing review

Record authors and substantive design contributors. Select a reviewer outside
that set. Prefer a different model family. If none is available, use a separate
qualified agent of the same family and record that choice. Preserve exact user
route choices and the existing substitute policy.

The reviewer reads the relevant code, docs, tests, and cumulative diff at the
recorded commit. Its first assessment precedes discussion with authors. The
driver resolves findings, fixes, and obtains re-review. Keep Agy plan mode and
`/boost` requirements when Agy is selected. Bot feedback starts early and does
not replace the final independent review.

## Ownership and implementation boundaries

| Component | Owner | Responsibility |
| --- | --- | --- |
| Repo pack and team allocation | Lantern | Accepted request, queued repos, capacity, user reports |
| Durable message transport | Lantern helper process | Store messages, receipts, subscriptions, bounded delivery |
| Development task and helpers | Elves driver | Plan, roles, worktrees, integration, canonical run records |
| Read only discussion | Cobbler protocol | Proposals, critique, evidence, synthesis |
| Worker execution | Existing qualified adapters | Exact model, session, worktree, permissions, continuation |
| Landing | Elves driver | Review, fixes, docs, version, checks, authorized merge, release |

Lantern does not become a second repo driver. The transport process makes no
product decisions and has no merge authority. Elves must still work without
Lantern or Herdr. Use a versioned optional callback interface with local adapter
support. Do not embed a second copy of the transport implementation in Elves.
Standalone Elves retains its existing council and worker behavior. The first
release's new persistent peer mailbox requires Lantern. State that dependency
in the user guide and capability output.

## Message and state contract

Use a small versioned envelope. Include message ID, pack/run/task ID, parent task,
sender and receiver identities, message kind, correlation ID, timestamp, sequence,
expiry, and structured evidence references. Bind identities to the Herdr server,
pane occupant, native session, kind, and actual model. A pane ID alone is not
enough. Reject identity drift before delivery or state changes.

Initial message kinds cover assignment, receipt, progress, question, answer,
decision, PR opened, review requested, review result, blocked, completion report,
and cancellation. Separate transport state from task state. A receipt proves
storage or consumption only. It does not prove that the task passed acceptance.

Use one private local SQLite store through Python's standard library for message
state and delivery claims. Keep large evidence in bounded local artifacts and
reference it from messages. Use transactions and unique message IDs. Configure
bounded lock waits. Do not put the database in a product repo or on a network share.
Test the design on macOS, Linux, and Windows before making it the default.
SQLite owns messages and delivery claims only. Lantern's existing pack JSON
remains the task graph and observed progress record, with its single writer and
atomic updates. Elves records remain authoritative for run acceptance and landing.
Messages can request a state check but cannot directly change verified progress.
Test state retention through plugin update and transport schema migration.

The sender persists its report before exit. Delivery can repeat after failure;
consumers must deduplicate by message ID. Track queued, claimed, consumed, expired,
and unresolved delivery states. Do not promise exactly once external actions.
Before repeating a PR action, permission response, launch, or integration, inspect
the current external result. An ambiguous delivery outcome requires reconciliation.
Use the message ID, current pane output, agent state, and exact session evidence
to reconcile. A prompt timeout does not prove that the receiver missed the message.

Maintain a task graph with one owner per task. Distinguish queued, running,
waiting on dependency, waiting on peer, blocked, reported complete, verified
complete, failed, and cancelled. Detect dependency cycles and peer wait deadlocks.
Do not allow a parked parent to appear available for unrelated work.

Senders can report facts and ask for help. They cannot grant permissions, change
the merge policy, substitute models, or mark acceptance verified. Reject messages
outside the sender's registered scope. Use private paths and scoped endpoint
access. Do not claim same-user local processes have stronger isolation than the
host provides. Keep secrets and full chat transcripts out of message storage.

## Event handling and safe wake-up

The transport owns one subscription connection and a local event cache. Subscribe
before taking the initial snapshot, buffer events during it, then reconcile.
On disconnect, obtain a fresh snapshot and reconcile durable state before acting.
Do not assume event replay or stable IDs across a server restart.
The raw socket client permits only its explicit observation methods, including
snapshot and subscription. It must reject prompt, key, create, start, and close
methods before any request reaches the socket. Test this restriction with request
capture. Keep any authorized delivery or permission action in the existing
controlled adapter path. Do not bypass `bin/herdr` through a generic socket sender.

Coalesce output noise and repeated state changes. Prioritize blocked permissions,
questions, completion reports, and failures. Use events to select what needs a
read. Keep recurring checks for missed reports, missing external checks, and dead
agents. Preserve the current Agy child permission service requirement.

Use a host adapter to deliver queued messages at a proven safe boundary. Prefer
native inbox or hook support when the installed host proves that capability.
Otherwise consume messages at explicit driver checkpoints or through the existing
active monitor. A shell process that prints a reminder is not a model wake method.
Name the consumption points in Elves: after staging, at supported batch or packet
boundaries, before review dispatch, and before final readiness and landing.
Preserve the one-packet prewalk transition. A parked full-run consumes only through
its qualified supervisor or wake contract. Do not invent an extra mid-run packet.

Lantern's own task instructions target the recorded driver. Helper assignments
and helper delivery belong to that driver or its qualified worker supervisor.
The mailbox can carry attributed peer messages directly to helper inboxes, within
the driver's recorded scope. Transporting a peer message does not let Lantern
issue a second set of instructions to a helper.

Do not solve delivery by typing into working, blocked, unknown, or parked panes.
A stable idle observation plus a process lock still cannot exclude human input
or another tool between the check and submission. Qualify each delivery adapter
against that race. If it cannot enforce the boundary, keep the message queued
and use checkpoint consumption. State this limit instead of advertising immediate
delivery. A native Herdr queued-send extension is a possible later improvement,
not a dependency assumed to exist.

Keep one recorded transport owner per Lantern session with a process lifetime
lock and generation identity. A replacement must prove the prior owner is dead.
Messages from an older generation remain evidence but cannot trigger fresh actions.
Recover the same agent session and route. Never resume by latest-session lookup.

Stop owned monitoring when all tasks have verified terminal outcomes and no
pending delivery, review, integration, or external check can advance. Preserve
unresolved failures in the report. User blocks follow the current pause rule.
Do not close tabs, stop unrelated processes, or close Lantern home.

## Routing and resource limits

Extend existing Elves preferences for lead, proposer, investigator, implementer,
critic, and reviewer roles. Lantern uses that resolved policy instead of creating
a competing set of defaults. Preserve explicit run choices and repo vetoes.

Cache capability checks by CLI version, route, relevant configuration, and expiry.
Refresh on a changed binary, expired record, launch failure, or explicit request.
Catalog membership and actual launch qualification are separate evidence.
Check current readiness before each launch without repeating full discovery.
Do not invent model IDs or silently replace a pinned model.

Record limits for active agents across the pack, helpers per task, proposal rounds,
wall time, and retries. Start with no recursive helper creation. Queue excess work
fairly across repos. Check observed usage when the host provides it. Do not promise
an exact currency cap for subscription routes that do not expose reliable cost.
Quota failure stops the affected route and invokes the existing substitute rules.
Permission handling remains limited to the accepted task. Keep Codex unattended
flags. Do not add other yolo flags.

## Implementation batches and PR boundaries

| Batch | Repo | Change | Exit proof |
| --- | --- | --- | --- |
| B0 | Both | Freeze protocol, role ownership, host capability matrix, and fixtures from live trials | Commands and observed identities recorded; unsupported wakes marked |
| B1 | Lantern | Add durable mailbox helper, transactional claims, identity checks, and consumer CLI | Concurrent send, duplicate, crash, stale owner, and migration tests pass |
| B2 | Lantern | Add subscriptions, reconciliation, host delivery adapters, and recovery monitor integration | Busy agent receives no prompt; restart and missed event tests pass |
| B3 | Elves | Add optional callbacks, team context, saved roles, contributor ledger, reviewer exclusion, and driver/helper lifecycle | Standalone behavior works; contributors cannot review; exact sessions and permissions hold |
| B4 | Both | Connect brainstorming, peer questions, and bounded helper scheduling to natural requests | Two families complete proposals, critique, and a combined report |
| B5 | Elves | Integrate persistent lane state and driver owned integration with the existing gates | Separate writers land in one integration PR; overlapping changes block |
| B6 | Both | Complete docs, compatibility, independent review, fixes, version, and release proof | Cumulative review and CI pass; authorized landing retains all existing gates |

Use a first PR pair for reliable callbacks, reports, and read only helper teams
(B0-B4). Use a second PR pair for automatic writer teams (B5-B6 as applicable).
This permits a useful first release without claiming that writer supervision is
complete. Each pair needs its own docs, version, review, and release checks.
Lantern and Elves can implement separate parts in parallel after B0 fixes the
interface. Integrate only after compatibility tests pass.

New Lantern surfaces should include a small `bin/` command and a focused Python
runtime module. Extend `launch.sh`, `lib.sh`, `prompt.md`, `herd-workflows.md`,
`helper.conf.example` only where necessary, and the current test entry points.
Update `README.md`, `docs/index.html`, `howto.html`, and `CHANGELOG.md`.
Change routing and preflight helpers only for the new roles or qualification data.

Elves changes belong in the existing Cobbler runtime, session acceptance schema,
preferences and onboarding, callback adapter, and lane lifecycle. Update `SKILL.md`,
`AGENTS.md`, canonical references, guide, changelog, install bundle, and version
surfaces. Reconcile outdated runtime claims instead of adding another guide fork.
The driver records contributor host, model, exact session, and role in the session
schema. Review routing and final readiness both enforce reviewer exclusion.
Worker reports cannot remove contributors or rewrite this driver owned evidence.

Do not increase Lantern's Herdr minimum version based on a documentation guess.
Probe required methods and test the current supported floor. If a mandatory
primitive is absent, retain the supported fallback or document a tested minimum.

## Acceptance tests

1. Two model families receive the same task in separate sessions. Each produces
   an independent first proposal. The lead obtains critique and records evidence
   and dissent in its final recommendation.
2. A helper asks a peer a question. The peer receives its sender and task context.
   The answer reaches the requester. Lantern can trace the exchange without
   receiving a model turn for every message.
3. Many reports arrive while Lantern works. They remain stored. Safe consumption
   processes each once. Retry or restart cannot create duplicate task launches.
4. A driver is parked on a worker. Reports are collected without prompting that
   driver. The authorized checkpoint or terminal event starts reconciliation.
5. Sender, consumer, transport, Lantern, and Herdr restart at each critical write
   boundary. No report disappears after a storage receipt. No stale owner acts.
   Ambiguous external effects are inspected before retry.
6. A forged sender, stale pane occupant, expired message, out of scope request,
   or peer instruction to merge cannot expand authority.
7. Two writers use separate worktrees. Overlap, unexpected branch movement,
   unmet dependencies, and duplicated integration each block the unsafe action.
8. A contributor cannot serve as the independent landing reviewer. The reviewer
   checks the final commit and relevant docs. New changes invalidate affected proof.
9. Capability failures, quota loss, and unavailable named routes preserve the
   original identity and expose the real limit. Agy requires its qualified Boost
   procedure. No silent provider or model substitution occurs.
10. At least 100 simulated tasks respect configured concurrency and queue limits.
    Blocked permissions and terminal reports are not starved by output events.
    Done panes without acceptance and queued tasks cannot stop the monitor.
11. Mixed plugin and skill versions negotiate the callback contract. Standalone
    Elves and existing single-driver Ship runs retain their behavior.
12. Local macOS trials and Linux/Windows CI cover process ownership, state paths,
    locking, disconnects, and supported transport fallbacks. Do not label a route
    verified on platforms that have only mock evidence.

## Planning trial evidence

These are local observations from 2026-09-05. They do not prove that the proposed
team runtime exists. Helper sessions ran as CLI subprocesses. No Herdr helper
pane or automatic message return path was qualified by these trials.

### Commands checked

`bin/model-route claude 'fable 5.1 high'` resolved `claude-fable-5-1` at `high`.
`bin/model-preflight claude claude-fable-5-1 high` returned available.
The planning session completed with model usage recorded as `claude-fable-5-1`.

The command shape used was:

```sh
claude -p --model claude-fable-5-1 --effort high \
  --permission-mode plan --tools Read,Glob,Grep \
  --allowedTools Read,Glob,Grep --strict-mcp-config \
  --session-id <new-uuid> --add-dir <elves-skill-root> \
  --add-dir <herdr-source-root> --output-format json < <brief-file>
```

Use `--resume <exact-uuid>` in place of `--session-id` for a later turn.
Do not use `--continue`, which selects by recency. The initial session read
the source and returned its design. No permission denials appeared in its result.
An installed prose rewrite hook timed out after 45 seconds. The CLI still returned
the original answer with a successful exit. Preserve the raw answer and record
postprocessing failures separately from task failure.

`bin/model-route grok 'grok 4.6 xhigh'` resolved `grok-4.6` at `xhigh`.
`bin/model-preflight grok grok-4.6 xhigh` returned available. `grok models`
listed `grok-4.6`. The result recorded backend usage as `grok-4.6-build`.
Keep the requested CLI route and observed backend identity as separate fields.
Do not add the backend label as a new selectable slug without catalog evidence.

The first Grok trial used `--prompt-file` and a three-turn limit. Grok offloaded
the long prompt to a local file, read sources, and reached that limit without a
final answer. It returned exit 1, `stopReason: cancelled`, and `max turns reached`.
This was incomplete work. Also, `--tools ''` did not remove its read tools.
Never copy empty-tool-list semantics from another CLI.

The recovery command used the same session and model:

```sh
grok --resume <exact-uuid> --model grok-4.6 --reasoning-effort xhigh \
  --permission-mode plan --tools read_file,grep --no-subagents \
  --disable-web-search --max-turns 8 --output-format json \
  --single '<bounded continuation request>'
```

Recovery completed with exit 0 and `stopReason: end_turn`. The returned session
ID matched the first attempt. Backend usage remained `grok-4.6-build` and the
recorded effort remained `xhigh`. The final design was present in the result.

The actual first-run command and both session IDs are in the local trial logs.
Do not publish private chat IDs in the user guide. This test shows why launch
qualification must check tools, completion status, and result evidence, rather
than accept a successful preflight as task completion.

`herdr agent list` returned JSON directly. Both `herdr agent list --json` and
`herdr --json agent list` failed. `herdr api schema --json` worked. CLI flags
must follow the installed command grammar.

A local socket request to `HERDR_SOCKET_PATH` with the current observed pane ID
and this shape returned `subscription_started`:

```json
{
  "id": "planning-subscription-trial",
  "method": "events.subscribe",
  "params": {
    "subscriptions": [
      {"type": "pane.agent_status_changed", "pane_id": "<observed-pane-id>"}
    ]
  }
}
```

The client then closed the subscription. This proves subscription acceptance,
not event delivery, replay, or model wake-up. Those remain B0-B2 exit tests.

### Independent proposals and critique

Claude and Grok produced separate proposals from the source evidence. Claude then
resumed the same session and read this plan for a critique. Its result kept the
same session ID and `claude-fable-5-1` model, with a successful exit.

Accepted findings: reuse council dispatch, keep one driver, name checkpoint
consumption points, separate transport and task state, limit the raw socket
client, and enforce contributor exclusion in the first release. The plan now
includes each item and its acceptance evidence.

Rejected suggestions: a mandatory unused model family for review, a mailbox tied
to a disposable worktree, and treating idle-check-plus-prompt as safe delivery.
The user permits a fresh reviewer of the same family. The persistent store must
survive worktree cleanup. Prompt delivery still needs a qualified safe boundary.
Claude accepted these corrections in its critique. Grok's recommendation to keep
events advisory matches the fallback until a real wake adapter passes its tests.

The local lane trial returned `pending`, `all_terminal: true`, and
`ok_to_integrate: false` for a newly registered lane. This is a concrete B5 fix,
not evidence that integration currently accepts unfinished work.
