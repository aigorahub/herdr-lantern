# Usage readout verification

The checks ran on macOS on October 10, 2026. The branch is
`feat/plan-usage-readout`. The approved plan SHA256 is
`b75484bdc772d40f39f3fe8921d267f9cb36e36d5ee45895d5286a930e53fbbc`.

## Automated checks

The repository smoke check covers shell syntax, launcher and open action
behavior, model routing, headless Codex jobs, Field Status, Plan Usage, and
session cleanup. Team transport has a separate check. The Plan Usage tests
use provider fixtures and a fake Herdr server. The tab tests run through the
actual gated command wrapper.

Final results: the smoke check passed. All 36 Plan Usage tests passed. All 32
team transport tests passed, with one Windows specific test skipped on macOS.
The Field Status suite also skipped its Windows specific test. Python compile
checks and `git diff --check` passed. ShellCheck found no new issue after the
existing warning codes were excluded: SC1007, SC2034, SC2088, SC2295, SC1010,
and SC2020.

The tests cover account grouping, distinct tab counts, unknown identities,
credential overrides, Claude config selector presence, auth metadata changes,
and UI counter changes. They check all Codex limit pools, notifications during
initialization, stale values, past resets, retry delays, and late responses.

The lock tests check owner exit, takeover, and socket replacement. The provider
lease tests check a live parent deadline, abrupt parent death, separate child
process groups, and inherited lock ownership until cleanup ends. Native
providers do not inherit the collector lock descriptor.

Startup tests check fresh open, both chat reuse paths, direct launch, duplicate
prevention, occupied panes, label conflicts, uncertain command submission,
the shared setup lock, and the full Field Status setup deadline. They check
PowerShell path quotes and the ET time zone fallback. Installer tests reject
an incorrect digest and an archive path outside the extraction directory.

The existing CI matrix runs the smoke check on Linux, macOS, and Windows.
These local checks do not establish a result for those remote CI jobs.

## Live checks

The live check used four new tabs in workspace `wB3`. Each tab had one pane.
The setup helper opened Field Status and Plan Usage with `--no-focus`.
A second setup reused both watcher tabs. It sent no second watcher command.

Two usage watchers shared one collector. The follower became the owner after
the first owner's tab closed. The owner PID changed. An idle test agent report
raised the Antigravity tab count from four to five. Closing that test tab
returned the count to four. No Antigravity account or quota probe ran.

The view grouped seven native Codex tabs into one account row. The live Codex
source reported the weekly pool and separate credits. The Claude source
grouped three tabs into one Max account row. It reported session and weekly
allowances. Both sources supplied verified identity. The stored and displayed
account labels contained hashes, not email addresses.

Grok tabs had an API key override in their process environment. The collector
rejected that scope and showed Account unknown. The separate source check had
already verified the pinned OAuth adapter with cookies off. Antigravity stayed
Account unknown because the checked quota source has no billing identity.

All four test tabs were closed after the checks. The development tab kept one
pane. The peer tabs stayed open. The check sent no model prompt and answered
no peer approval dialog.

## Startup fixes from the live checks

A shell PID can exist before its prompt. The setup helper now checks the
prompt before it types a watcher command. Herdr returns empty output after a
successful `pane run`. The command helper accepts that response and confirms
the watcher through process metadata.

An explicit default Claude config path changes its macOS sign in lookup.
The adapter preserves an absent selector as absent. Its binding key includes
selector presence. The Claude usage probe also changes UI counters in
`.claude.json`. The binding uses that file's account metadata. Settings and
credential files retain full identity and change stamps. Separate account
probes check the identity before and after each usage request.

## Platform limits

Windows has no CodexBar release asset. Claude and Grok show Dependency
unavailable there. Native Codex shows Account unknown until a supported
process scope or verified launch receipt can prove its account. Antigravity
shows Account unknown on all platforms. None of these states has a percentage.
