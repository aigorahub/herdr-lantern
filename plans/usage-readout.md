# Lantern plan usage readout

Open the Lantern chat, Field Status, and Plan Usage when Lantern opens. Keep the two readouts separate. Show each account and plan once in Plan Usage when it has an open agent tab anywhere in the current Herdr server. Include idle tabs. Give each view its own tab in the Lantern workspace. Keep each tab as one pane.

This is an implementation plan. Provider access on this machine must pass the source checks below before the display can claim measured usage. Source checks are the first implementation step. A provider failure produces an explicit status in the working display. It must not prevent the other providers or the chat from starting.

## Existing readouts

`bin/field_status.py` joins Herdr tabs, agents, and workspaces by their IDs. It shows Important, Needs You, In Motion, Done, and Keep. Its watcher checks the field every five seconds. It stores rows and notes in private Lantern state. The current `pane` command opens a pane beside Lantern Home.

The field renderer hides idle tabs unless the user pins them in Keep. Usage selection must use the full tab and agent lists before this filter. A hidden idle tab still keeps its plan in the usage display.

`bin/goals-floor` supplies a text snapshot of agent goals and recent output at startup. It is not a persistent usage pane. `bin/model-preflight.py` parses Claude usage before Lantern starts an agent. It does not monitor all plans.

## Display and tab behavior

Keep the existing task sections in Field Status. Add a separate Plan Usage display. Route "Field Status" and "what's going on" to Field Status. Route "usage", "plan limits", and "remaining allowance" to Plan Usage.

For each account and plan, show a short account label, the number of open agent tabs, each known allowance, its reset time, and the age of the last successful update. Use remaining percent where the source reports a percent. Show credits or balance separately when the source supplies them. Do not convert tokens or costs into a remaining allowance.

Show separate allowance rows inside a plan when the provider has separate model groups or time windows. Antigravity Gemini and Claude/GPT limits belong to the Antigravity account. They must not appear under a native Claude or OpenAI account.

Use plain text and align short fields. Wrap at narrow widths. Color must not be the only state signal. Convert a valid used percentage to `100 - usedPercent` and label it remaining. Reject nonfinite values and percentages outside 0-100. Format known reset timestamps with `eastern()`, including its Windows fallback, and label them ET. Keep unparsed reset descriptions unchanged without adding ET. Show the next action when no agent tabs are open.

Open or reuse tabs labeled Field Status and Plan Usage in the Lantern workspace. Create each with the appropriate label. For example:

The setup helper receives the resolved Lantern workspace explicitly. It passes that ID to `herdr tab create --workspace <resolved-workspace> --cwd <chat-cwd> --label <view-name> --no-focus`. It must not use the action caller's workspace.

Use the returned root pane ID to start the watcher. Do not split the caller's tab. Preserve user focus. Keep the existing `pane` command as a compatible entry point. `field_status.py pane` requests only Field Status. `plan_usage.py pane` requests only Plan Usage. Automatic startup requests both.

The shared setup helper is `bin/lantern_views.py`. Its interface is `--state-dir <private-herd-dir> --workspace <id> --home-pane <id> --cwd <path> [--herdr <wrapper>] [--timeout <seconds>] setup [--view field|usage|both]`. The default view is both. The interactive `pane` wrappers can obtain these values from Lantern's environment. The open action must pass them explicitly.

Add tab create and pane run to the helper's precise mutation allowlist. Set `HERDR_HELPER_OK=1` only for these setup operations. Keep the general wrapper gate unchanged. Use the returned tab label and ID, so setup does not need tab rename.

Store each tab and root pane ID in private state. Validate the recorded IDs and foreground process before reuse. Restart a stopped watcher only in its available shell. Never send input to an agent or another foreground process. A previous Field Status side pane can remain open. Do not close or move it during migration. Report its presence when the startup check creates the new readout tab.

For a new tab, wait for process info to show a nonzero shell PID and no unrelated foreground process. Also require Herdr to report shell readiness if that capability is available. Bound readiness checks by the setup deadline. Build the watcher command with the existing POSIX or PowerShell quoting helpers. Confirm the expected watcher process after submission. Record a failed or uncertain submission. Do not submit it again until process inspection proves an available shell.

Use stored ownership records, server lifetime, tab ID, pane ID, and the expected watcher command to identify a view. A label alone must not authorize input. A missing ownership record can recover a running matching watcher. It must not adopt an ordinary shell merely because its tab has the same label.

Use new `views.json` records. Ignore the old `field-status-pane.json` as setup ownership. Never recover a watcher in the chat tab or a tab with several panes. Recovery requires the resolved Lantern workspace, exact readout label, one root pane, and a running watcher with this plugin path and state directory in its arguments. A valid current lifetime record can restart its idle shell. First recover a running watcher that passes all these checks. Leave every other unowned tab with the readout label untouched and report a label conflict. Do not create a second tab with that label. This protects an unrelated shell after state loss. The legacy chat side pane stays separate and does not prevent creation of the new tab.

## Automatic startup

Start both readouts through launcher code. Do not depend on the chat model to open them in its first turn. Make this the default for fresh Lantern sessions and for reopening an existing Lantern chat.

`launch.sh` must start the view setup after it has private state and Herdr context. Show both readouts before provider network requests finish. Plan Usage can show Checking usage until data arrives. Start the chat without waiting for provider access.

`open.sh` currently returns early when it finds and focuses a live chat. Add the same view setup to both reuse paths. Recreate a missing readout tab or restart its stopped watcher when the user opens Lantern again. Repeated opens must not create extra chats, readout tabs, or collectors.

Use one setup helper with explicit Lantern workspace, chat pane, and state directory inputs. Validate the plugin chat identity and current server identity. A fresh launcher may run before Herdr recognizes the chat agent, so setup must not require an idle agent state or send a prompt to establish one. Use a lock to coordinate `launch.sh` and `open.sh` setup calls.

Use a separate `view-setup.lock` in private herd state. Never acquire `open.lock` from the view helper. The lock order is open action, then view setup. `launch.sh` acquires only view setup. Set a total setup deadline of 15 seconds. Catch helper failure explicitly in both shell scripts so `set -eu` cannot end chat startup. Record setup errors in private state and report a short message without raw command output. A busy lock must leave work to its current owner, then inspect the records before reporting completion.

Limit each view attempt to seven seconds within that deadline. A failed first view must leave time to attempt the second. Do not report successful setup merely because the tab was created. Confirm the watcher or report its exact startup state.

The collector runs in the Plan Usage watcher, not in the launcher shell. `launch.sh` starts the watchers through their own tab shells before it executes the chat. No provider network request runs in the setup helper.

Create readout tabs with `--no-focus`. Leave focus on the chat. The three views will be open in the same workspace, with one pane in each tab, as the workspace rule requires. Do not change this development tab's layout.

If one readout fails to start, keep the chat and the other readout available. Record the failure in private startup state and report its concrete cause. Retry that readout on the next open. A missing usage provider must show an unavailable row inside Plan Usage, rather than prevent the pane from opening.

## Plan selection and account identity

Read the full Herdr field every five seconds. Join agents to open tabs. Include working, idle, done, blocked, and unknown agent states. Exclude shell tabs and the readout itself. Remove a plan after a successful scan confirms that its last agent tab has closed or no longer contains an agent. A failed scan must not remove plans.

Map each agent to its billing provider and account. Group by provider and a stable account ID. Read the current plan name from that account's usage response. Several Codex model names can use one account and plan. Model names and tab titles must not establish account identity.

Use the account, profile, and home directory associated with the agent process or its verified launch record. Add account binding to Lantern's launch records where needed. For tabs started outside Lantern, inspect supported process metadata and provider account reports. Never query the ambient default account and assign its limits to every tab without proof.

Herdr process info supplies the PID, executable arguments, and working directory. It does not supply environment variables. On Linux, read the same user's `/proc/<pid>/environ`. On macOS, read the same user's `KERN_PROCARGS2` data through `sysctl`. Keep only the provider's home and profile selectors in memory. Do not print or store the full environment. Reject API keys, token overrides, proxy endpoints, and unsupported account selectors. Require the process PID and arguments to remain unchanged after inspection. On Windows, use a verified launch receipt if available. Otherwise show Account unknown. Do not infer the default account from missing command flags.

Require a readable environment block with an absolute HOME. An empty, omitted, denied, or malformed block is Account unknown. Never substitute the collector's environment. Resolve the provider command to an executable path. A shell alias is not an executable. A Codex process with profile `fugu`, executable `codex-fugu`, or a nonnative model provider is Limits unavailable. Check both arguments and its scoped config. Kinds `cursor`, `agent`, `devin`, and `pi` are Limits unavailable. The model argument does not select a billing provider.

Herdr kind `agy` belongs to the Antigravity row. Count its distinct open tab IDs there with Account unknown and no measured allowance. Do not select it by model name. This membership rule does not need an executable or account probe because this release does not fetch its quota.

For kind `agy`, do not resolve an executable, inspect its process environment, probe account identity, check a dependency, or fetch quota in this release. This exception takes precedence over the provider query and identity rules below. Its row comes only from the full Herdr tab and agent lists.

Query the provider under the verified home and profile. Require a nonempty verified identity from its usage response or a separate account probe in the same scope. Never hash a missing, null, or empty identity. Hash the identity with the provider and organization to form the stored account key. Use an opaque short account label. Include the auth scope and its configuration file stamp in a binding generation. Recheck that generation before accepting a response. A changed scope invalidates its old measurement. Count distinct tab IDs within each account, including tabs with several panes. Unsupported agent kinds get a separate Limits unavailable row. Do not map them by their model names.

For Claude, run `claude auth status --json` in the same scope before and after the quota request. Require `loggedIn`, a nonempty email, and unchanged email, orgId, and subscriptionType. Require the usage identity email to match. The probe supplies organization and plan identity when the quota report omits them. For Codex, require the native ChatGPT account and a nonempty `accountId` from the limits response. A successful quota fetch without these binding checks is not a measured row.

When an account cannot be resolved, show one Account unknown notice per provider with the affected tab count. Do not merge it with a known account. Do not claim remaining usage for those tabs. Preserve separate rows for two accounts at the same provider, even when both have the same plan name.

## Usage sources

Keep provider fetches behind a small adapter interface. Each fetch adapter must return account identity, plan name when available, allowance windows, reset times, source name, observation time, and a clear result state. The local Antigravity Account unknown row does not call a fetch adapter.

| Provider | Candidate source | Required source check |
| --- | --- | --- |
| OpenAI Codex | App server `account/rateLimits/read` | Verify account binding and all returned limit IDs. Preserve separate pools. |
| Claude | CodexBar `--source cli` | Use the verified Claude executable, its read only CLI usage panel, returned source `claude`, and the account probes above. Do not use the print prompt or browser cookies. |
| Antigravity | Account unknown in the initial release | The CLI quota report has no billing identity. A verified quota parser can be added, but no measured allowance is attached to these tabs until an identity source passes. |
| Grok Build | CodexBar `--source oauth` | Use the signed in Grok CLI credentials and cookie free account billing. Accept `grok-web` only from this isolated OAuth request. Do not use the failing CLI billing RPC. |

The Codex adapter uses stdio app server requests only. Send `initialize`, wait for its matching response, send `initialized`, then request `account/read` and `account/rateLimits/read`. Match response IDs and ignore notifications. Preserve `rateLimitsByLimitId` entries and the default pool without duplication. Terminate and reap the owned child on completion or timeout. Never create a thread or turn.

Use CodexBar CLI version 0.74.0. Run `usage --provider <provider> --source <source> --format json` with the explicit source above. Use an isolated private config with cookies off, hooks off, and no saved token accounts. Run one provider per command. Never omit the source or fall back to Auto, Web, session token history, or another account. Accept only measured quota windows with verified identity. The existing Claude preflight parser remains separate because it can exit the launcher and its print command is not the chosen monitor source.

The source checks in [usage-source-checks.md](usage-source-checks.md) measured Codex, Claude CLI, and Grok OAuth on this machine. Claude OAuth returned no credentials. Grok CLI billing returned Method not found. Antigravity CLI and print reports supplied four windows but no billing identity. The existing process local identity request returned HTTP 401. Do not use `installation_id` as an account ID. The initial Antigravity row must say Account unknown with its tab count. This is a source limitation, not a promise to display unverified quota. For future verified Antigravity responses, prefer the four `extraRateWindows` groups once; omit duplicate primary and secondary windows. Offline history is never quota.

Official OpenAI documentation describes the account limit method and its response: [OpenAI Docs](https://learn.chatgpt.com/docs/app-server#6-rate-limits-chatgpt).

CodexBar supplies JSON usage output through its CLI. Its provider notes describe [Antigravity usage reports](https://github.com/steipete/CodexBar/blob/main/docs/antigravity.md) and [Grok account usage](https://github.com/steipete/CodexBar/blob/main/docs/grok.md). See the [CLI contract](https://github.com/steipete/CodexBar/blob/main/docs/cli.md).

The CodexBar CLI was absent from PATH. The macOS arm64 release 0.74.0 was downloaded for source checks and its published SHA256 was verified. Provide an explicit install command that downloads the matching pinned macOS or Linux asset and verifies its published digest before extraction into private Lantern tools. Do not install the desktop app or change the user's CodexBar config. CodexBar has no Windows release asset. On Windows the Claude and Grok adapters show Dependency unavailable. Native Codex remains available when account binding is verified. Missing dependencies affect only their provider rows. The Antigravity row remains Account unknown on every platform and does not need CodexBar.

The install command is `sh bin/plan-usage install`, backed by `plan_usage.py install`. Install into `$HERDR_PLUGIN_STATE_DIR/tools/codexbar/0.74.0`; when only herd state is available, use its parent directory's `tools`. Lookup precedence is an explicit absolute `LANTERN_CODEXBAR_BIN`, this private version path, then PATH. Require version 0.74.0. An invalid explicit path does not fall back. Do not download from a watcher or launcher.

Download base URL: `https://github.com/steipete/CodexBar/releases/download/v0.74.0/`. The helper appends the asset name below. Verify the pinned digest before extraction. Reject archive paths outside the temporary extraction directory. Install only the CLI release files by atomic directory replacement. Unsupported platforms return Dependency unavailable.

| Platform | Asset | SHA256 |
| --- | --- | --- |
| macOS arm64 | `CodexBarCLI-v0.74.0-macos-arm64.tar.gz` | `9aab1b9229f5d1d77de21b99aa887a60a47921bfd9b66e4b941a0e8a9d6e38e8` |
| macOS x86_64 | `CodexBarCLI-v0.74.0-macos-x86_64.tar.gz` | `4d239b7412683f1eb529d55b851fe922bb462527850b767d6c9b89ba14be3fdb` |
| Linux aarch64 (glibc) | `CodexBarCLI-v0.74.0-linux-aarch64.tar.gz` | `46dc3b259b1df48b3fd6f337c754dd60dbece6772d7840cdc9514ab4977a0007` |
| Linux x86_64 (glibc) | `CodexBarCLI-v0.74.0-linux-x86_64.tar.gz` | `fac8b2c474e4e37a2b067224437cb182693411ebf639441cd0facbb57bf086b1` |
| Linux aarch64 (musl) | `CodexBarCLI-v0.74.0-linux-musl-aarch64.tar.gz` | `9dab39f1197d9ccc501d1f678234cf15f94e8b84aa7349ba87103a5866dd793b` |
| Linux x86_64 (musl) | `CodexBarCLI-v0.74.0-linux-musl-x86_64.tar.gz` | `e7b0bdc6d6931a4e6fd5ce4037e1125a073ec6cf38cd72f1b3bd8aeecfde51f5` |

## Collection and stored state

Use one collector per Herdr server and user. Readout tabs consume its stored snapshot. This prevents a second Lantern session from doubling provider requests. Acquire a process lock before starting the collector. Release it on exit and recover it after a confirmed process failure.

Use a shared private usage directory keyed by the stable Herdr server identity. Keep display notes in each Lantern session's existing state directory. Sessions with different provider profiles register their verified account bindings with the shared collector. Do not key shared collection by a workspace or a Lantern Home pane ID.

Resolve the endpoint from `herdr status`. Hash its canonical socket path for the shared cache directory. Use the socket file identity and creation/change stamp as the Unix server lifetime. On Windows, require a server process identity when available; otherwise do not reuse stopped view ownership records across launches. Keep cache identity separate from live ownership identity. Revalidate account bindings before displaying cached values after restart.

Use a held file lock with `fcntl.flock` on Unix and `msvcrt.locking` on Windows. Keep the lock file; the kernel releases its lock when the process exits. Do not steal locks by age or remove a live owner's lock file. Each Plan Usage watcher tries a nonblocking collector lock. Its owner scans and collects in a two worker thread pool while it renders. Other watchers read the atomic shared snapshot. A surviving watcher takes ownership on its next five second tick after owner exit. Stop collection when no watcher holds ownership. Allow queued owned requests to end within their bounded timeout during shutdown. Use the same held lock method for view setup, with its 15 second total deadline.

Hold one lock descriptor until owned work ends. Create at least one byte before locking byte zero on Windows. Record the owner's PID and process start marker for diagnostics. The kernel lock is the ownership authority. Never unlock, delete, or reopen the file to bypass a held lock. Do not copy the two minute `open.lock` recovery rule. Store the server lifetime with cached membership. After a lifetime change, discard old membership before rendering, even if the first field scan fails. Keep old measurements hidden until their binding is revalidated. A `refresh` command reads the current cache and exits. A `collect` command acquires the same lock, scans once, collects under the provider deadline, stores its result, and exits. If the lock is busy, it reads the cache without a second fetch.

Set each provider request deadline to 100 seconds. There is no Antigravity request in this release. Start subprocesses in owned process groups. On timeout, stop and reap their descendants. Use a bounded JSON capture. Do not inherit unrelated credential environment variables into provider commands. The field scan and rendering loop remain responsive while requests run.

Fetch each eligible account every 60 seconds. Fetch a newly discovered account promptly. Limit concurrent provider requests to two. Allow only one request per account at a time. Give each adapter a bounded timeout based on its verified command behavior. Provider work must not block the field renderer.

After failure, retry after 120 seconds, then 240 seconds, then at most every 300 seconds. Honor a provider's longer retry interval. Reset the interval after success. Stop requesting an account after its last agent tab closes. Discard a late response if the tab set or account binding changed during the request.

Store snapshots atomically in private Lantern state outside the repository. Store normalized usage and opaque account IDs only. Do not store credentials or raw command output. Use private file permissions where the platform supports them. Retain the latest successful result through restart, but show its age until a new fetch succeeds.

Mark a value as stale after 120 seconds without a successful update. Retain its timestamp and show Last update failed when applicable. Show Unavailable for unsupported access and Sign in required for an authentication failure. An absent value is not zero usage. An expired reset time does not prove that the allowance has reset.

Do not start a model turn, prompt another agent, read another agent's approval dialog, or change its permissions to collect usage. A collector request must only read provider data. Do not consume reset credits or purchase extra usage.

## Implementation steps

1. Verify provider sources and account binding on the installed CLI versions. Capture sanitized response fixtures. Resolve the Grok dependency. Record which windows each source can measure. Treat a failed source check as an explicit unavailable state.
2. Add `bin/plan_usage.py` for account selection, adapters, collection, locking, and stored snapshots. Keep collection independent of the Claude model preflight policy.
3. Add the Plan Usage renderer and watcher to `bin/plan_usage.py`. Reuse field discovery without its task visibility filter. Add the shared collector lifecycle. Change `bin/field_status.py` to open or reuse its dedicated tab.
4. Add the shared view setup helper. Call it from fresh `launch.sh` startup and both live chat reuse paths in `open.sh`. Update `prompt.md` and `README.md` with automatic startup, separate request routes, dependency setup, account labels, update intervals, and unavailable states. Add release notes when implementation is ready.
5. Run focused tests and a live readout check. Prepare the implementation for review. Merge requires the user's explicit approval in that session.

## Acceptance and verification

Use deterministic provider fixtures and a fake Herdr field for automated tests. Verify these cases:

- Several Astra and Sol tabs on one verified OpenAI account produce one plan row.
- An idle Grok tab in another workspace keeps its plan visible.
- Tabs hidden by Keep still count toward plan selection.
- Two accounts at one provider stay separate. An unresolved account cannot receive another account's usage.
- Antigravity Claude usage remains separate from native Claude usage.
- Separate allowance windows retain their own remaining values and reset times.
- Missing usage, a timeout, an expired reset, or an authentication failure never becomes a false zero or full allowance.
- A failed Herdr scan preserves the prior eligible set. A verified closure removes the last plan row.
- A late response cannot restore a closed account or overwrite a changed account binding.
- Multiple Plan Usage views share one collector. Repeated requests reuse the same display tabs. An unrelated process in a recorded pane prevents input.
- A fresh Lantern open starts chat, Field Status, and Plan Usage without a chat prompt. Each tab has one pane. Focus stays on the chat.
- Both live chat reuse paths in `open.sh` restore missing readouts. Repeated and concurrent opens do not duplicate tabs or collectors.
- Direct plugin launch starts both readouts through `launch.sh`. Slow or unavailable provider access does not delay the chat or Field Status.
- A readout startup failure leaves the chat and other view available. The next open retries only the missing view.
- Opening either readout keeps the caller's tab as one pane and preserves focus.
- Narrow terminal output remains readable. Stored state and error text contain no credentials.
- A missing Windows time zone database uses the existing ET fallback. Unparsed reset prose never gains an ET label.
- The collector uses only approved read operations and does not prompt or interrupt an agent.

Run the existing Field Status tests, launcher tests, and repository smoke checks after code changes. Check Linux, macOS, and Windows behavior through the existing CI coverage. A missing provider dependency must produce a clear state on each platform.

Replace the Field Status split and same tab assertions with new tab lifecycle checks. Update `tests/fixtures/field_status_herdr` and the open action fake in `tests/smoke.sh` for tab list/create, readiness, process inspection, and watcher confirmation. Exercise setup through the actual gated wrapper. Add tests for the Codex initialization exchange, interleaved notifications, per limit IDs, bounded timeouts, missing identity, process environment rejection, distinct tab counts, lock owner exit and takeover, socket replacement, occupied panes, uncertain command submission, and PowerShell command quoting. Run the repository shell checks and all CI test entry points that apply to these changes.

For the live check, use existing signed in accounts and open tabs. Compare each measured allowance with that provider's own usage report. Confirm that the tab count includes idle agents across workspaces. Close only a test tab created for the check and verify row removal. Do not close a peer's tab.

The feature is ready when the shared display passes these checks. A provider that cannot expose account limits must show an explicit unavailable state. Do not present session token totals as proof of remaining plan usage.
