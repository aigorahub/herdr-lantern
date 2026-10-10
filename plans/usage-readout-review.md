# Lantern usage readout plan review

## Implementation review

Grok and Agy reviewed the implementation on `feat/plan-usage-readout`.
Agy used `/boost` for the full review and the final code changes. Both returned
READY after the fixes below. The approved plan hash stayed unchanged.
Agy confirmed that all Boost workers and review tasks had finished.

Grok found three faults in the first code review. A failed first scan removed
saved tab counts. A busy setup caller did not read the owner's completed
record. Field Status had a shorter startup deadline than Plan Usage. The fixes
retain the previous same lifetime membership without unverified quota, read
a matching completed setup record, and give Field Status a separate 15 second
setup deadline. Regression tests cover each case.

The final review checked the provider child lease, lock inheritance, macOS
process markers, and Windows states. A lease receives a duration and uses its
own clock. It keeps the Unix collector lock until child cleanup ends after
owner death. Native providers do not inherit that descriptor. macOS uses
microsecond process start markers from `libproc`. Windows Claude and Grok show
Dependency unavailable. Antigravity stays Account unknown without probes.

Grok returned READY after the four focused tests passed. Agy returned READY
after all 36 Plan Usage tests passed. The full smoke check and the 32 team
transport tests also passed. Platform specific tests were skipped where the
local host could not run them. Remote CI results are not claimed here.

The live checks and platform limits are in
[usage-readout-verification.md](usage-readout-verification.md).

## Revision history

Revision 2 had SHA256 `b37160e693de7647b529199265d3f9b589f306ee9a0cca8ca82e4c4656f8cb00`. Grok and Agy returned NOT READY. Both confirmed the new setup interface, wrapper gate, lock order, and Codex handshake. Agy used `/boost` and confirmed that all workers finished.

Revision 3 has SHA256 `256b26c507742184eb36a25e8b551c0f432ece030c8f8dbb2a4ff6fab861af3e`. It corrects the source table from the live checks. It adds Claude account probes, precise dependency installation, invalid identity handling, unsupported provider rules, ownership recovery, lock byte handling, and remaining percentage conversion. Agy returned READY after all Boost workers finished. Grok requested explicit Agy membership and a clear order for recovery before label conflicts.

Revision 4 had SHA256 `4f303169d6b31796ce917bf5caec1da7818d54e4de86a66e342d34f123534037`. Agy returned READY. Grok requested an explicit exception from all generic provider probes for Agy.

Revision 5 has SHA256 `b75484bdc772d40f39f3fe8921d267f9cb36e36d5ee45895d5286a930e53fbbc`. Grok and Agy both returned READY. Agy confirmed Boost and worker completion. Both verified the exact hash. This is the implementation contract.

The initial Claude parser recommendation below is superseded. The monitor will use the verified CodexBar CLI usage panel and separate `claude auth status --json` probes. It will not call the model preflight print command.

Antigravity quota lacks a verified billing identity on this machine. Revision 3 makes Account unknown the explicit initial release state. It rejects installation IDs as account identity. Its quota parser can retain the four supported groups in fixtures without attaching them to an unverified account.

Grok proposed restarting a same label idle shell after ownership loss. That would authorize input from a label alone. Revision 3 instead recovers only a matching running watcher. An unowned label conflict stays untouched and does not create another tab.

The kernel lock controls collector ownership. PID and start metadata aid diagnostics. They do not override a held kernel lock. Windows locks an existing byte. The owner holds one descriptor until its work ends.

The text below records the initial review.

Grok and Agy reviewed [the usage readout plan](usage-readout.md). Both returned changes before implementation. The three separate tabs remain the intended layout. The plan needs a more precise setup interface, account binding contract, and collector lifecycle.

The reviewed base commit is `1fd3169e292c02f8c8207004ecbd60f5cb134776`. The reviewed plan SHA256 is `7045323835fdd3bdfcd9880381067c632417cafdc0c07118b843f417e911d14c`.

Grok reviewed through `lantern-repo-grok`. Agy reviewed through `lantern-repo-agy` with `/boost`. Agy started an investigator, an initial worker, and a second worker to check the findings. The final report confirmed Boost activation, worker completion, and context coverage. Both reviewers verified the plan hash.

## Required plan changes

### Allow tab creation through the command wrapper

Both reviewers found this gap. [Field Status control](../bin/field_status.py#L121) sets `HERDR_HELPER_OK` only for pane split, pane rename, and pane run. [The wrapper](../bin/herdr#L22) blocks other mutations without that flag. Tab creation through the reused control function would fail.

Add an explicit task to allow tab creation for the view setup helper. Allow tab rename only if setup uses it. Keep pane split out of that path. Add a test through the actual wrapper, rather than only a mocked control function. A setup failure must not prevent the chat from starting.

### Define the setup interface and startup sequence

The plan already requires explicit workspace, chat pane, and state directory inputs. Specify the helper file, command, and arguments. Specify whether each `pane` entry point opens one requested view or ensures both views. Automatic startup must ensure both.

Pass the resolved Lantern workspace from [open.sh](../open.sh#L59). Do not use the action caller's workspace from the example command. Cover both paths that focus an existing chat. Use a separate view setup lock from `open.lock`. Define the lock order so `open.sh` and `launch.sh` cannot wait on each other.

Wait for each new root pane to have an available shell before sending its watcher command. Reuse the Windows command construction in [watcher_command](../bin/field_status.py#L155). Record setup failures and continue chat startup. Keep the legacy side pane separate from the new tab records.

### Specify account binding for each provider

Both reviewers found that the current account binding section is incomplete. Herdr process metadata supplies arguments and process IDs. It does not supply the process environment. A usage response also may lack account identity.

Define which provider report proves account identity, and how that identity is tied to the open agent process. For Claude, evaluate `claude auth status --json` separately from the usage report. For Antigravity, require an account source that can detect an account change. Keep unresolved bindings explicit.

Do not assign every tab to the current ambient account merely because no profile flag appears. Environment settings can select a different configuration without an argument. A verified launch record can establish the configuration used by a Lantern agent. Tabs opened elsewhere need a supported binding method or an Account unknown notice.

Select the billing provider from the agent kind and verified configuration. A Claude model through Antigravity uses the Antigravity plan. A Fugu profile must not inherit the native OpenAI row. Other providers can show Unavailable until their own adapter passes its source checks.

### Define collector ownership and recovery

Both reviewers found that the plan leaves collector ownership open. Decide which process runs it. A workable model lets each Plan Usage watcher try to own the collector lock. Other watchers read snapshots. If the owner exits, another watcher can take over after it verifies that the prior process ended.

Define behavior when the last usage view closes. Collection can stop while no view consumes it. Reopening a view must start collection again. Give a plain snapshot command a bounded collection path.

Specify a locking method that works on Linux, macOS, and Windows. Use process identity and start time during recovery. Age alone must not permit a second collector to take the lock.

Define server scope with the actual Herdr session endpoint. A socket path can identify a session, but the same path can exist after a server restart. Keep a server lifetime marker for live tab and pane records. Validate prior cache data before using it after restart.

### Specify provider transport and measurement rules

Agy found that the Codex adapter needs initialization before it can read account limits. Define the initialization exchange, response matching by request ID, notification handling, request deadlines, and process cleanup. See the [OpenAI app server documentation](https://learn.chatgpt.com/docs/app-server).

Extract Claude text parsing from the live preflight command. The collector must handle parse failures without terminating the watcher. Convert used percent to remaining percent explicitly. Keep raw reset text when it cannot be parsed. Do not label an unknown time as ET.

Select a verified Grok account usage source through CodexBar. Exclude browser cookie import and session token fallback. Specify the supported dependency version and platform behavior. A missing dependency or unsupported platform must affect only the Grok row.

Reuse the ET fallback from [eastern](../bin/field_status.py#L32) for systems without time zone data. Resolve actual provider executable paths. A shell alias is not a subprocess executable.

### Update the tests with the layout change

Both reviewers found missing test update tasks. [Field Status tests](../tests/field_status.py#L152) assert pane splitting. [The startup stub](../tests/smoke.sh#L950) does not return new tab records. Update these tests with the implementation.

Add checks for setup from an action context, the mutation gate, shell readiness, both chat reuse paths, lock recovery, collector takeover, Codex notifications, and Windows time zone fallback.

Count distinct eligible tab IDs for the displayed tab count. Keep account selection at the agent level because a tab elsewhere can contain several agents. Several agents in one tab must not inflate that plan's tab count.

## Claims that do not establish defects

Grok stated that shell `exec` kills background jobs. The cited launcher code does not establish that claim. Do not use it as a confirmed failure. Collector ownership still needs an explicit lifecycle.

Agy described `fcntl.flock` as a chosen implementation. The plan contains no `fcntl` requirement. Portable locking is a design requirement, not a confirmed failure in existing usage code.

Agy proposed Antigravity `installation_id` as a billing account ID. The review did not establish that it identifies a billing account or changes when the user switches accounts. Do not accept that binding without provider evidence.

Agy proposed treating the absence of alternate profile arguments as proof of the default account. Arguments do not prove the absence of environment overrides. Preserve the existing requirement to verify account binding.

Some findings describe intended code changes already present in the plan. The current side pane layout and both early return paths are examples. They need implementation and regression tests. Their current behavior does not itself contradict a plan to change them.

## Review coverage and limits

Both reviewers read the plan, Field Status code, model preflight code, launcher, open action, command wrapper, library helpers, plugin manifest, prompt instructions, Field Status tests, and startup smoke tests. Agy also reported coverage of the review workflow and Windows support notes. Grok inspected current Herdr process metadata and tab scope. Grok reported 19 open tabs across seven workspaces at review time.

The reports are plan reviews. They do not establish measured usage for all four accounts on this machine. Provider identity and allowance access remain implementation gates. The plan file retains the reviewed hash so the findings remain tied to the exact version reviewed.
