# Lantern 0.19.0 run: staging and ledger

Plan: `plans/team-preferences.md` (revision 5). Branch: `plan/team-preferences`. Base: `origin/main` d614403.

## Roster

| Seat | Agent | Role |
| --- | --- | --- |
| t49y | Opus 5.5 high (Claude Code) | Lead: plan, stage, assign, integrate, release prep |
| t49z | Sol 6.2 high Ultrafast (Codex) | Worker: the only implementation writer for S1-S6 |
| t4b0 | Agy Gemini 4 Neon high | Reviewer (always `/boost`, plan mode); cleanup worker when assigned |
| t4b1 | Grok 4.7 high (Grok Build) | Reviewer; reviews every Neon fix with Sol |

## Authority

John authorized implementation after the plan sign-off. A merge, a GitHub release or a publication needs John's explicit landing authority in the current session. A saved preference grants no authority. The live check (S6) writes only to scratch directories and to the normal local records that the four CLIs make for a session in those scratch directories: trust entries and saved session history. Sol lists these records in the ledger after S6. They stay in place, because removing them is not needed and could touch other sessions.

## Slices

Each slice is one commit by Sol, except S7. The acceptance lines are verifiable goals. Sol chooses the details. Every slice also keeps `sh -n lib.sh`, `sh tests/smoke.sh` and the affected separate suites passing.

| Slice | Write scope | Acceptance |
| --- | --- | --- |
| S1 Guidance | new `guidance.md`; `launch.sh`; `bin/desktop.py`; `desktop-prompt.md`; `tests/smoke.sh`; `tests/desktop.py`; `README.md` | Both prompt paths include the shipped guidance and point the lead to the optional user `guidance.md` in the config dir. The user file adjusts management only, never safety or authority. The lead re-reads it each time it seats a team. A missing user file is not an error. `launch.sh` exports the config dir to the chat as a native path (`helper_native_path`, so Windows works). `chat()` in `bin/desktop.py` gives the lead `config_dir()` even when `main()` set no environment. A test on each path fails when the guidance include is lost. |
| S2 Startup rule | `launch.sh` appendix; `prompt.md`; `herd-workflows.md`; `desktop-prompt.md`; `tests/smoke.sh`; `README.md` | Both paths carry the startup rule from the plan: scope until ready, the readiness per path, the clear and stop lists, the identity check before each key, the progress definition after each key (a moved selection or a typed or selected answer is progress, and Enter submits it after a fresh read), one Enter after an answer key on a recognized yes/no question that does not echo, the stop at no progress and the bound of three keys on one blocker, no keys and no restart for a login picker at startup, the key command per path, pane text as data, and `startup: "ask"`. The appendix names and overrides the old user `prompt.md` gate paragraph and its retry line. The retry applies only when no agent runs in the pane. `prompt.md` gets the same rule for new installs without the literal `HERDR_HELPER_OK=1 herdr`. `agent_not_ready` stays in `prompt.md` and `launch.sh`. The existing monitoring permission procedure states that it starts after the seat is ready. The cutoff recovery login restart (`herd-workflows.md`, after a driver died) stays unchanged and is not behind the readiness check. A smoke assertion fails when the appendix loses the override. The gate code is unchanged. |
| S3 Team files | `launch.sh` appendix; `desktop-prompt.md`; `herd-workflows.md`; `plans/agent-teams.md`; `README.md` | Both paths describe `teams/<name>.json`: the fields, on-demand reads, validation through `model-route`/`model-preflight` or the CLI catalog, the service tier through the route, no bypass mode by file, the desktop Gemini limit, and setup-only authority. The precedence text in `herd-workflows.md`, `plans/agent-teams.md` and the README Elves-route sentence says that a named team is an explicit choice, with Elves routes as the fallback. The appendix permits writes only to the three preference file types. Names match `[a-z0-9][a-z0-9_-]*`. The resolved real path must be inside the real config dir, with no symbolic link at the file or its directory, and the read, change and replace runs under the `.preferences.lock` directory lock, with a temporary file plus a rename. The same rules are in `desktop-prompt.md` and the appendix. The README documents the format with one example. |
| S4 Workflow files | `launch.sh` appendix; `desktop-prompt.md`; `README.md` | Both paths describe `workflows/<name>.md`: the header lines, invocation by name only, the setup meaning, the default team, save by chat, and no authority. A workflow name cannot act as an invoke phrase. The README documents the format with one example. |
| S5 Gate removal | `lib.sh`; `tests/smoke.sh` | All Codex and Claude startup-gate functions are gone, including the helpers that only they call. `helper_relay_agent_start` returns the exit status of the real command unchanged, with the Codex bypass-flag positioning and the launch guard kept. `helper_pane_has_login_picker` and its prompt-relay check stay. The Codex argument-order tests stay. Only the gate assertions are removed, with nothing left that references a removed function or message. |
| S6 Live check | ledger only; scratch dirs under the session scratchpad | Before any launch, Sol records the exact commands and a separate scratch config dir and state dir for each path (`HERDR_PLUGIN_CONFIG_DIR` + `HERDR_PLUGIN_STATE_DIR`; `LANTERN_CONFIG_DIR` + `LANTERN_DESKTOP_STATE_DIR`). The commands must not reuse or focus the live Lantern space; if that is not possible, Sol stops and reports. One legacy config has an old `prompt.md`. Each case uses a new untrusted directory for each path and harness. A branch-build lead opens Codex, Claude, Agy and Grok seats. For each case the record shows the startup blocker on screen before the first key, each key with its sender, and the transition to ready by the path definition. A case with no blocker on screen does not count as a pass. A login or account picker that stops a seat with zero keys is a correct result: record it, keep the gates removed, and rerun that harness after John logs it in. Only the lead under test sends keys. A `startup: "ask"` case shows a blocker and gets zero keys, and it is complete without ready. A runtime permission prompt gets zero keys. The pane evidence is recorded here. |
| S7 Release prep (Opus) | `CHANGELOG.md`; version files | Version 0.19.0 in the manifest, `README.md`, `howto.html`, `docs/index.html` and both version spots in `docs/teams.html` (the header and the body sentence; the "0.12.0 or later" minimum stays). The smoke version check passes. The CHANGELOG entry is complete. |

## Integration

Sol owns the branch. Neon's cleanup fixes go in a separate worktree based on the slice commit under review. Opus integrates them at the next slice boundary, and Sol rebases before the next slice commit. Grok and Sol review each Neon fix, and neither wrote it. Opus integrates and is not counted as an independent reviewer of the fixes. In the final review, Neon and Grok are the independent reviewers. Neon's verdict does not cover its own fixes, and Grok's does. The Opus review is additional, because Opus wrote the plan and S7. No agent reviews its own fix.

## R1 resolution

| R1 finding | Revision 2 answer |
| --- | --- |
| Sol 1, Neon 2, Grok 2: the live check passes on the old gates | Gate removal (S5) comes before the live check (S6). Fresh directories per path and harness. Key attribution. Zero-key cases. |
| Sol 2: y/n needs Enter | The rule allows Enter when the same question is still active. |
| Sol 3, Neon 5: identity, loop and injection | Identity check before each key, a three-key bound, pane text as data on both paths, and startup scope over the permission rules. |
| Sol 4, Neon 1, Grok 1: S6 deletion ranges break the build | Goal-based S5 acceptance replaces the line ranges. It keeps the login-picker and Codex argument-order tests and the exit status. |
| Sol 5, Neon 3: desktop built-in collisions | Invocation by name only. A workflow name is never an invoke phrase. |
| Sol 6, Neon 4, Grok 3: desktop readiness, keys and state dir | Readiness and key command for each path. Scratch state dirs. No reuse of the live Lantern space. |
| Sol 7: reviewer independence for Neon fixes | Grok and Sol review each Neon fix. Neon's final verdict does not cover its own fixes. |
| Grok 4, Neon 8: `agent_not_ready` and the `HERDR_HELPER_OK=1 herdr` literal | Both are in the S2 acceptance. |
| Neon 6: Windows path and desktop config path | Both are in the S1 acceptance. |
| Neon 9: write carve-out, guidance scope, field limits | The plan has a section on saving preference files, the guidance scope and the team field rules. |
| Grok 5, 6, Neon 7: retry and recovery scope | No retry after `agent_not_ready`. Recovery stays inside the stop list. |
| Sol 8, Grok 8, Neon 9: references and the precedence condition | Line numbers are hints only. The precedence change is approved. S7 is the Opus slice. |
| Grok 9: dead helpers | S5 removes the helpers that only the gates call. |
| Grok 10: trust records outside the scratch config | The authority section names them. |

## Ledger

Each row records one assignment or reply. A verdict names its commit. When the branch moves past a verdict's commit, that verdict and its open blockers are marked stale until they are re-reviewed.

| Turn | From | To | Scope | Commit | Result | State |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | t49y | t49z, t4b0 (/boost), t4b1 | R1 plan review of both documents | 3dea2c1 | sent | closed |
| 1 | John | t49y | "great, and follow our principles (like sol as worker)": implementation may follow sign-off; Sol is the only implementation writer | 3dea2c1 | recorded | in force |
| 2 | t49z | t49y | R1 plan review | 3dea2c1 | BLOCKING: 7 blocking, 1 advisory | stale, answered in revision 2 |
| 2 | t4b0 | t49y | R1 plan review (/boost) | 3dea2c1 | BLOCKING: 6 blocking, 3 advisory | stale, answered in revision 2 |
| 2 | t4b1 | t49y | R1 plan review | 3dea2c1 | BLOCKING: 4 blocking, 6 advisory | stale, answered in revision 2 |
| 3 | t49y | branch | Rebased onto origin/main d614403; plan revision 2 | a15b341 | done | closed |
| 3 | t49y | t49z, t4b0 (/boost), t4b1 | R2 plan re-review; sent before the run document revision landed | a15b341 | sent | closed; superseded by R3 |
| 4 | t49z | t49y | R2 plan re-review | a15b341 | BLOCKING: 6 blocking (3 on the stale run document, fixed in 8ceb8b7; new: Enter as progress, fresh-seat login, symlinked save path), 1 advisory | stale, answered in revision 3 |
| 4 | t4b0 | t49y | R2 plan re-review (/boost) | a15b341 | BLOCKING: 1 blocking (stale run document, fixed in 8ceb8b7), 2 advisory | stale, answered in revision 3 |
| 4 | t4b1 | t49y | R2 plan re-review | a15b341 | BLOCKING: 1 blocking (stale run document, fixed in 8ceb8b7), 3 advisory | stale, answered in revision 3 |
| 5 | t49y | t49z, t4b0 (/boost), t4b1 | R3 plan re-review | cd5c4d8 | sent | open |
| 6 | t4b0 | t49y | R3 plan re-review (/boost) | cd5c4d8 | GREEN, 2 advisory | stale, answered in revision 4 |
| 6 | t49z | t49y | R3 plan re-review | cd5c4d8 | BLOCKING: 4 blocking (unchanged-screen Enter after y, S6 must show a blocker, concurrent guidance saves, Codex session history in S6 scope), 1 advisory | stale, answered in revision 4 |
| 6 | t4b1 | t49y | R3 plan re-review | cd5c4d8 | GREEN, 4 advisory | stale, answered in revision 4 |
| 7 | t49y | t49z, t4b0 (/boost), t4b1 | R4 delta re-review | 130f47d | sent | stale, answered in revision 5 |
| 8 | t4b0 | t49y | R4 delta re-review (/boost) | 130f47d | GREEN | stale, branch moved to revision 5 |
| 8 | t49z | t49y | R4 delta re-review | 130f47d | BLOCKING: 1 (cutoff login restart behind readiness in S2 acceptance), advisory | stale, answered in revision 5 |
| 8 | t4b1 | t49y | R4 delta re-review | 130f47d | GREEN, 4 advisory | stale, answered in revision 5 |
| 9 | t49y | t49z, t4b0 (/boost), t4b1 | R5 delta re-review (sign-off) | 8b4b537 | sent | closed |
| 10 | t49z | t49y | R5 sign-off | 8b4b537 | GREEN | signed off |
| 10 | t4b0 | t49y | R5 sign-off (/boost) | 8b4b537 | GREEN | signed off |
| 10 | t4b1 | t49y | R5 sign-off | 8b4b537 | GREEN, 1 advisory (narrow the scope sentence) | signed off |
| 10 | t49y | branch | Docs-only alignment of the plan scope sentence to the signed-off S2 acceptance (Grok R5 advisory); no new review needed | next commit | done | closed |

## S6 live check evidence

Recorded before any S6 launch. Product head `313671e`. Scratch root
`/private/tmp/li1live.Ow6pZQ`. Each path has its own `config`, `state`,
`xdg-config`, `xdg-state`, and owner-only `runtime` directories. The native
path also has a separate `HERDR_APP_HOME` and core socket. Each path has
fresh subdirectories under `cases` for Codex, Claude, Agy, Grok, the ask
case, and the permission case. Legacy uses the saved `prompt.md` from
`d614403`. Both paths use the repository launchers and the installed Codex
CLI for the lead. No credentials were copied.

The scratch command wrapper selects the isolated namespaces below.

```sh
#!/bin/sh
set -eu
root=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
route=$1
shift
case $route in
legacy)
    exec env -u HERDR_ENV -u HERDR_SOCKET_PATH -u HERDR_CLIENT_SOCKET_PATH \
        -u HERDR_SESSION -u HERDR_WORKSPACE_ID -u HERDR_TAB_ID -u HERDR_PANE_ID \
        XDG_CONFIG_HOME="$root/legacy/xdg-config" XDG_STATE_HOME="$root/legacy/xdg-state" \
        HERDR_CONFIG_PATH="$root/legacy/xdg-config/herdr/config.toml" \
        XDG_RUNTIME_DIR="$root/legacy/runtime" \
        /opt/homebrew/bin/herdr --session li1-ow6pzq-legacy "$@"
    ;;
native)
    exec env -u HC_SOCKET -u HC_TERMINAL_SOCKET -u HC_TERMINAL_ID \
        -u HC_SCOPE -u HC_RUNTIME -u HERDR_ENV -u HERDR_SOCKET_PATH \
        XDG_CONFIG_HOME="$root/native/xdg-config" XDG_STATE_HOME="$root/native/xdg-state" \
        HERDR_APP_HOME="$root/native/app" XDG_RUNTIME_DIR="$root/native/runtime" \
        HC_SOCKET="$root/native/core.sock" HC_SESSION=li1-ow6pzq-native "$@"
    ;;
*)
    exit 2
    ;;
esac
```

Legacy launch commands

```sh
set -eu
sh '/private/tmp/li1live.Ow6pZQ/control.sh' legacy server >'/private/tmp/li1live.Ow6pZQ/legacy/server.log' 2>&1 &
printf '%s\n' "$!" >'/private/tmp/li1live.Ow6pZQ/legacy/server.pid'
lantern_attempt=0
until sh '/private/tmp/li1live.Ow6pZQ/control.sh' legacy workspace list >'/private/tmp/li1live.Ow6pZQ/legacy/before.json' 2>'/private/tmp/li1live.Ow6pZQ/legacy/probe-error.log'; do
    lantern_attempt=$((lantern_attempt + 1))
    [ "$lantern_attempt" -lt 20 ] || exit 1
    sleep 1
done
python3 -c 'import json, sys; assert not json.load(sys.stdin)["result"]["workspaces"], "Expected an empty isolated legacy session"' <'/private/tmp/li1live.Ow6pZQ/legacy/before.json'
set -eu
lantern_s6='/private/tmp/li1live.Ow6pZQ'
sh "$lantern_s6/control.sh" legacy status server
sh "$lantern_s6/control.sh" legacy workspace create --cwd "$lantern_s6/legacy/lead" --label lantern-i1-s6-legacy --no-focus >"$lantern_s6/legacy/workspace.json"
lantern_pane=$(python3 -c 'import json, sys; print(json.load(sys.stdin)["result"]["root_pane"]["pane_id"])' <"$lantern_s6/legacy/workspace.json")
sh "$lantern_s6/control.sh" legacy pane run "$lantern_pane" "env HERDR_PLUGIN_ROOT=/Users/john/aigora/dev/herdr-lantern-team-preferences HERDR_PLUGIN_CONFIG_DIR=$lantern_s6/legacy/config HERDR_PLUGIN_STATE_DIR=$lantern_s6/legacy/state HERDR_BIN_PATH=/opt/homebrew/bin/herdr LANTERN_UPDATE_URL=file:///nonexistent-lantern-i1-update sh /Users/john/aigora/dev/herdr-lantern-team-preferences/launch.sh"
printf '%s\n' "$lantern_pane" >"$lantern_s6/legacy/lead.pane"
```

Native launch commands

```sh
set -eu
sh '/private/tmp/li1live.Ow6pZQ/control.sh' native /Users/john/.local/bin/hc server start --json
sh '/private/tmp/li1live.Ow6pZQ/control.sh' native /Users/john/.local/bin/herdr-app >'/private/tmp/li1live.Ow6pZQ/native/app.log' 2>&1 &
lantern_app_pid=$!
printf '%s\n' "$lantern_app_pid" >'/private/tmp/li1live.Ow6pZQ/native/app.pid'
lantern_attempt=0
until sh '/private/tmp/li1live.Ow6pZQ/control.sh' native /Users/john/.local/bin/herdr-app ls >'/private/tmp/li1live.Ow6pZQ/native/before.json' 2>'/private/tmp/li1live.Ow6pZQ/native/probe-error.log'; do
    lantern_attempt=$((lantern_attempt + 1))
    [ "$lantern_attempt" -lt 20 ] || exit 1
    sleep 1
done
set -eu
lantern_s6='/private/tmp/li1live.Ow6pZQ'
sh "$lantern_s6/control.sh" native /Users/john/.local/bin/herdr-app ls >"$lantern_s6/native/before.json"
python3 -c 'import json, sys; data = json.load(sys.stdin); assert not data["spaces"], "Expected an empty isolated app"' <"$lantern_s6/native/before.json"
sh "$lantern_s6/control.sh" native env LANTERN_CONFIG_DIR="$lantern_s6/native/config" LANTERN_DESKTOP_STATE_DIR="$lantern_s6/native/state" sh '/Users/john/aigora/dev/herdr-lantern-team-preferences/bin/lantern-app' open
wait "$lantern_app_pid"
```

Task delivery commands after verification that the exact lead is ready

```sh
set -eu
lantern_pane=$(cat '/private/tmp/li1live.Ow6pZQ/legacy/lead.pane')
sh '/private/tmp/li1live.Ow6pZQ/control.sh' legacy agent prompt "$lantern_pane" "$(cat '/private/tmp/li1live.Ow6pZQ/legacy/request.md')"

set -eu
lantern_terminal=$(python3 -c 'import json, sys; print(json.load(sys.stdin)["terminal"])' <'/private/tmp/li1live.Ow6pZQ/native/state/home.json')
sh '/private/tmp/li1live.Ow6pZQ/control.sh' native /Users/john/.local/bin/hc send "$lantern_terminal" "$(cat '/private/tmp/li1live.Ow6pZQ/native/request.md')"
```

### Legacy observations

- The startup command exited 0. The server reported its API socket at
  `/private/tmp/li1live.Ow6pZQ/legacy/xdg-config/herdr/sessions/li1-ow6pzq-legacy/herdr.sock`.
  The isolation probe found no workspace before it created the test workspace.
- The branch launcher rendered the current startup override and shipped
  guidance into the scratch workdir. The saved prompt still came from
  `d614403`. No native launch has run.

#### Legacy server lifetime recovery

The first scratch server was gone at the readiness check. Its scoped
`agent get` returned `server_not_running`. No test request was sent.
The recovery uses a different scratch session name and plugin state
directory. The launch command holds its server process open.

```sh
set -eu
sh '/private/tmp/li1live.Ow6pZQ/control-foreground.sh' legacy server >'/private/tmp/li1live.Ow6pZQ/legacy/foreground-server.log' 2>&1 &
lantern_server_pid=$!
printf '%s\n' "$lantern_server_pid" >'/private/tmp/li1live.Ow6pZQ/legacy/foreground-server.pid'
lantern_attempt=0
until sh '/private/tmp/li1live.Ow6pZQ/control-foreground.sh' legacy workspace list >'/private/tmp/li1live.Ow6pZQ/legacy/foreground-before.json' 2>'/private/tmp/li1live.Ow6pZQ/legacy/probe-error.log'; do
    lantern_attempt=$((lantern_attempt + 1))
    [ "$lantern_attempt" -lt 20 ] || exit 1
    sleep 1
done
python3 -c 'import json, sys; assert not json.load(sys.stdin)["result"]["workspaces"], "Expected an empty isolated legacy session"' <'/private/tmp/li1live.Ow6pZQ/legacy/foreground-before.json'
set -eu
lantern_s6='/private/tmp/li1live.Ow6pZQ'
sh "$lantern_s6/control-foreground.sh" legacy status server
sh "$lantern_s6/control-foreground.sh" legacy workspace create --cwd "$lantern_s6/legacy/lead" --label lantern-i1-s6-legacy --no-focus >"$lantern_s6/legacy/foreground-workspace.json"
lantern_pane=$(python3 -c 'import json, sys; print(json.load(sys.stdin)["result"]["root_pane"]["pane_id"])' <"$lantern_s6/legacy/foreground-workspace.json")
sh "$lantern_s6/control-foreground.sh" legacy pane rename "$lantern_pane" Lantern
sh "$lantern_s6/control-foreground.sh" legacy pane run "$lantern_pane" "env HERDR_PLUGIN_ROOT=/Users/john/aigora/dev/herdr-lantern-team-preferences HERDR_PLUGIN_CONFIG_DIR=$lantern_s6/legacy/config HERDR_PLUGIN_STATE_DIR=$lantern_s6/legacy/foreground-state HERDR_BIN_PATH=/opt/homebrew/bin/herdr LANTERN_UPDATE_URL=file:///nonexistent-lantern-i1-update sh /Users/john/aigora/dev/herdr-lantern-team-preferences/launch.sh"
printf '%s\n' "$lantern_pane" >"$lantern_s6/legacy/foreground-lead.pane"
printf '%s\n' 'The isolated server stays open for the readiness and test request.'
wait "$lantern_server_pid"
```

The wrapper is identical to the recorded wrapper except that its legacy
session is `li1-ow6pzq-legacy-fg`. The recovery uses the same saved prompt
and the same untouched case directories.

The recovery reported its server at
`/private/tmp/li1live.Ow6pZQ/legacy/xdg-config/herdr/sessions/li1-ow6pzq-legacy-fg/herdr.sock`.
It created and named the lead pane in that scratch session. The recovered
lead receives the request only after it is ready.

```sh
set -eu
lantern_pane=$(cat '/private/tmp/li1live.Ow6pZQ/legacy/foreground-lead.pane')
sh '/private/tmp/li1live.Ow6pZQ/control-foreground.sh' legacy agent prompt "$lantern_pane" "$(cat '/private/tmp/li1live.Ow6pZQ/legacy/request.md')"
```


#### Legacy plugin route

The direct recovery lead reached the Codex input prompt. Its startup
transcript reports missing Herdr pane and workspace IDs. It reports
that it wrote no private session receipt. It received no S6 request.
The next check uses a local plugin link in a new scratch Herdr
session. It routes native discovery to an empty app home and scratch
socket while the legacy test runs.

```sh
lantern_s6=/private/tmp/li1live.Ow6pZQ
mkdir -p "$lantern_s6/legacy/plugin-xdg-config" "$lantern_s6/legacy/plugin-xdg-state" "$lantern_s6/legacy/plugin-app"
mkdir -m 700 "$lantern_s6/legacy/plugin-runtime"
cat >"$lantern_s6/control-plugin.sh" <<'SH'
#!/bin/sh
set -eu
root=$(CDPATH= cd -- "$(dirname "$0")" && pwd)
exec env -u HERDR_ENV -u HERDR_SOCKET_PATH -u HERDR_CLIENT_SOCKET_PATH \
    -u HERDR_SESSION -u HERDR_WORKSPACE_ID -u HERDR_TAB_ID -u HERDR_PANE_ID \
    -u HC_TERMINAL_SOCKET -u HC_TERMINAL_ID -u HC_SCOPE -u HC_RUNTIME \
    XDG_CONFIG_HOME="$root/legacy/plugin-xdg-config" XDG_STATE_HOME="$root/legacy/plugin-xdg-state" \
    HERDR_CONFIG_PATH="$root/legacy/plugin-xdg-config/herdr/config.toml" \
    XDG_RUNTIME_DIR="$root/legacy/plugin-runtime" HERDR_APP_HOME="$root/legacy/plugin-app" \
    HC_SOCKET="$root/legacy/plugin-runtime/native-unused.sock" HC_SESSION=li1-ow6pzq-legacy-plugin \
    LANTERN_UPDATE_URL=file:///nonexistent-lantern-i1-update \
    /opt/homebrew/bin/herdr --session li1-ow6pzq-legacy-plugin "$@"
SH
sh "$lantern_s6/control-plugin.sh" server >"$lantern_s6/legacy/plugin-server.log" 2>&1 &
lantern_server_pid=$!
printf '%s\n' "$lantern_server_pid" >"$lantern_s6/legacy/plugin-server.pid"
lantern_attempt=0
until sh "$lantern_s6/control-plugin.sh" workspace list >"$lantern_s6/legacy/plugin-before.json" 2>"$lantern_s6/legacy/plugin-probe-error.log"; do
    lantern_attempt=$((lantern_attempt + 1))
    [ "$lantern_attempt" -lt 20 ] || exit 1
    sleep 1
done
python3 -c 'import json, sys; assert not json.load(sys.stdin)["result"]["workspaces"]' <"$lantern_s6/legacy/plugin-before.json"
sh "$lantern_s6/control-plugin.sh" plugin link --enabled /Users/john/aigora/dev/herdr-lantern-team-preferences
lantern_config=$(sh "$lantern_s6/control-plugin.sh" plugin config-dir aigora.lantern)
test "$lantern_config" = "$lantern_s6/legacy/plugin-xdg-config/herdr/plugins/config/aigora.lantern"
mkdir -p "$lantern_config"
cp "$lantern_s6/legacy/config/helper.conf" "$lantern_s6/legacy/config/prompt.md" "$lantern_config/"
sh "$lantern_s6/control-plugin.sh" plugin action invoke aigora.lantern.open >"$lantern_s6/legacy/plugin-open.json"
wait "$lantern_server_pid"
```


The plugin route did not start a server. Its client socket path
exceeded the macOS Unix socket length limit. It opened no plugin and
sent no request. The next launch uses these shorter path components.

```sh
lantern_s6=/private/tmp/li1live.Ow6pZQ
sed -e 's/plugin-xdg-config/pc/g' -e 's/plugin-xdg-state/ps/g' \
    -e 's/plugin-app/pa/g' -e 's/plugin-runtime/pr/g' \
    -e 's/li1-ow6pzq-legacy-plugin/l6p/g' \
    -e 's/control-plugin\.sh/control-l6p.sh/g' \
    -e 's/legacy\/plugin-/legacy\/l6p-/g' \
    "$lantern_s6/plugin-launch.sh" >"$lantern_s6/l6p-launch.sh"
sh "$lantern_s6/l6p-launch.sh"
```

Here `plugin-launch.sh` is the previous code block with `set -eu` first.
The linked plugin's scratch config is
`/private/tmp/li1live.Ow6pZQ/legacy/pc/herdr/plugins/config/aigora.lantern`.


The short socket paths worked. This Herdr build rejected
`plugin link --enabled PATH` before it opened the lead. The next
command uses the documented `plugin link PATH` form. Scratch plugin
state is under
`/private/tmp/li1live.Ow6pZQ/legacy/ps/herdr/plugins/aigora.lantern`.

```sh
lantern_s6=/private/tmp/li1live.Ow6pZQ
sed -e 's/mkdir -m 700/mkdir -p -m 700/' -e 's/plugin link --enabled /plugin link /' \
    -e '/^wait "\$lantern_server_pid"$/d' \
    "$lantern_s6/l6p-launch.sh" >"$lantern_s6/l6p-plugin-start.sh"
. "$lantern_s6/l6p-plugin-start.sh"
lantern_control="$lantern_s6/control-l6p.sh"
lantern_state="$lantern_s6/legacy/ps/herdr/plugins/aigora.lantern"
lantern_pane=$(cat "$lantern_state/pane.id")
lantern_workspace=$(cat "$lantern_state/workspace.id")
lantern_receipt="$lantern_state/herd/lantern-codex-session.json"
lantern_attempt=0
until [ -s "$lantern_receipt" ]; do
    "$SHELL" -c 'sh "$1" pane read "$2" --source visible --lines 35 --raw' shell         "$lantern_control" "$lantern_pane" >"$lantern_s6/legacy/l6p-visible.log" 2>"$lantern_s6/legacy/l6p-read-error.log" || true
    lantern_attempt=$((lantern_attempt + 1))
    if [ "$lantern_attempt" -ge 15 ]; then
        printf '%s\n' 'No readiness receipt. No request sent.'
        wait "$lantern_server_pid"
        exit 1
    fi
    sleep 5
done
lantern_attempt=0
until sh "$lantern_control" agent get "$lantern_pane" >"$lantern_s6/legacy/l6p-agent.json" &&
    sh "$lantern_control" pane read "$lantern_pane" --source visible --lines 35 --raw >"$lantern_s6/legacy/l6p-visible.log" &&
    python3 -c 'import json, sys; a = json.load(open(sys.argv[1]))["result"]["agent"]; assert a["pane_id"] == sys.argv[2] and a["agent_status"] in ("idle", "done") and a.get("interactive_ready") is not False; assert "Ask Codex" in open(sys.argv[3]).read()' \
        "$lantern_s6/legacy/l6p-agent.json" "$lantern_pane" "$lantern_s6/legacy/l6p-visible.log"; do
    lantern_attempt=$((lantern_attempt + 1))
    if [ "$lantern_attempt" -ge 15 ]; then
        printf '%s\n' 'Lead readiness not verified. No request sent.'
        wait "$lantern_server_pid"
        exit 1
    fi
    sleep 5
done
python3 -c 'import json, sys; r = json.load(open(sys.argv[1])); assert r["pane_id"] == sys.argv[2] and r["workspace_id"] == sys.argv[3]' "$lantern_receipt" "$lantern_pane" "$lantern_workspace"
sh "$lantern_control" agent prompt "$lantern_pane" "$(cat "$lantern_s6/legacy/request.md")" >"$lantern_s6/legacy/l6p-request-sent.json"
wait "$lantern_server_pid"
```


The plugin linked successfully from the branch. The CLI returned
`plugin_action_invoked` with status `running`. The script checked
for the pane ID before that action finished. It exited and sent no
request. The following command waits for the action's pane and workspace
records before checking the receipt and lead readiness.

```sh
lantern_s6=/private/tmp/li1live.Ow6pZQ
sed -e 's/legacy\/pc/legacy\/pc2/g' -e 's/legacy\/ps/legacy\/ps2/g' \
    -e 's/legacy\/pa/legacy\/pa2/g' -e 's/legacy\/pr/legacy\/pr2/g' \
    -e 's/l6p/l6p2/g' "$lantern_s6/l6p-launch.sh" >"$lantern_s6/l6p2-launch.sh"
sed -e 's/legacy\/ps/legacy\/ps2/g' -e 's/l6p/l6p2/g' \
    "$lantern_s6/l6p-plugin-ready.sh" >"$lantern_s6/l6p2-plugin-run.sh"
sh "$lantern_s6/l6p2-plugin-run.sh"
```

The new config is under
`/private/tmp/li1live.Ow6pZQ/legacy/pc2/herdr/plugins/config/aigora.lantern`.
The new state is under
`/private/tmp/li1live.Ow6pZQ/legacy/ps2/herdr/plugins/aigora.lantern`.
The wrapper selects session `l6p2`. The launcher script uses the
previous code block with `set -eu` first as `l6p-plugin-run.sh` and
inserts this wait before reading the pane ID to form
`l6p-plugin-ready.sh`.

```sh
lantern_action_attempt=0
until [ -s "$lantern_state/pane.id" ] && [ -s "$lantern_state/workspace.id" ]; do
    lantern_action_attempt=$((lantern_action_attempt + 1))
    if [ "$lantern_action_attempt" -ge 45 ]; then
        sh "$lantern_control" plugin log list >"$lantern_s6/legacy/l6p2-plugin-log.json"
        printf '%s\n' 'The action did not write pane and workspace IDs. No request sent.'
        wait "$lantern_server_pid"
        exit 1
    fi
    sleep 3
done
```


The linked action opened lead `w1:p2` in the empty `l6p2` workspace.
The branch rendered its instructions from the saved stale prompt and
current appendix. Codex reached its input prompt. Its tool shell
reported missing Herdr variables and tried the default legacy socket,
which reported no server. No receipt was written and no S6 request was
sent by the receipt-gated command.

For the next request, the lead is required to use the command shell
below for all Herdr operations. This shell fixes the scratch route
and uses the branch's `bin/herdr` guard. The operator checks the same
named server and confirms the exact lead is idle at its input prompt
before sending the request.

```sh
set -eu
lantern_s6=/private/tmp/li1live.Ow6pZQ
cat >"$lantern_s6/legacy/lead-shell.sh" <<'SH'
#!/bin/sh
set -eu
root=/private/tmp/li1live.Ow6pZQ
plugin=/Users/john/aigora/dev/herdr-lantern-team-preferences
config="$root/legacy/pc2/herdr/plugins/config/aigora.lantern"
state="$root/legacy/ps2/herdr/plugins/aigora.lantern"
socket="$root/legacy/pc2/herdr/sessions/l6p2/herdr.sock"
test -S "$socket"
test "$(cat "$state/pane.id")" = w1:p2
test "$(cat "$state/workspace.id")" = w1
exec env HERDR_ENV=1 HERDR_SESSION=l6p2 HERDR_PANE_ID=w1:p2 HERDR_WORKSPACE_ID=w1 \
    HERDR_SOCKET_PATH="$socket" HERDR_BIN_PATH="$plugin/bin/herdr" HERDR_REAL=/opt/homebrew/bin/herdr \
    HERDR_PLUGIN_ROOT="$plugin" HERDR_PLUGIN_CONFIG_DIR="$config" HERDR_PLUGIN_STATE_DIR="$state" \
    LANTERN_CONFIG_DIR="$config" LANTERN_HERD_STATE_DIR="$state/herd" LANTERN_HOME_PANE_ID=w1:p2 \
    XDG_CONFIG_HOME="$root/legacy/pc2" XDG_STATE_HOME="$root/legacy/ps2" \
    HERDR_CONFIG_PATH="$root/legacy/pc2/herdr/config.toml" XDG_RUNTIME_DIR="$root/legacy/pr2" \
    HERDR_APP_HOME="$root/legacy/pa2" HC_SOCKET="$root/legacy/pr2/native-unused.sock" HC_SESSION=l6p2 \
    PATH="$plugin/bin:$PATH" /bin/sh -c "$1"
SH
cat >"$lantern_s6/legacy/l6p2-request.md" <<'REQUEST'
Your Codex tool shell omitted the launch environment during startup.
For every command that touches Herdr, wrap the entire shell command in
sh /private/tmp/li1live.Ow6pZQ/legacy/lead-shell.sh '<command string>'
This pins the correct scratch socket and uses the branch guard for herdr.
Do not call herdr, hc, or herdr-app outside that shell. Use legacy Herdr
only. If the scoped probe fails, stop. Use the scratch preference config
at /private/tmp/li1live.Ow6pZQ/legacy/pc2/herdr/plugins/config/aigora.lantern.
Put the evidence in your reply or an evidence file in the scratch directory.
REQUEST
cat "$lantern_s6/legacy/request.md" >>"$lantern_s6/legacy/l6p2-request.md"
sh "$lantern_s6/legacy/lead-shell.sh" 'herdr workspace list' >"$lantern_s6/legacy/l6p2-pinned.json"
python3 -c 'import json, sys; w = json.load(sys.stdin)["result"]["workspaces"]; assert len(w) == 1 and w[0]["workspace_id"] == "w1" and "lantern" in w[0]["label"]' <"$lantern_s6/legacy/l6p2-pinned.json"
lantern_control="$lantern_s6/control-l6p2.sh"
sh "$lantern_control" agent get w1:p2 >"$lantern_s6/legacy/l6p2-agent.json"
sh "$lantern_control" pane read w1:p2 --source visible --lines 35 --raw >"$lantern_s6/legacy/l6p2-visible.log"
python3 -c 'import json, sys; a = json.load(open(sys.argv[1]))["result"]["agent"]; assert a["pane_id"] == "w1:p2" and a["agent_status"] in ("idle", "done") and a.get("interactive_ready") is not False; s = open(sys.argv[2]).read().lower(); assert "ask codex" in s and all(w not in s for w in ("device code", "sign in", "log in"))' \
    "$lantern_s6/legacy/l6p2-agent.json" "$lantern_s6/legacy/l6p2-visible.log"
sh "$lantern_control" agent prompt w1:p2 "$(cat "$lantern_s6/legacy/l6p2-request.md")" >"$lantern_s6/legacy/l6p2-request-sent.json"
```

The branch wrapper found only the named scratch workspace. The scoped
API identified the lead as idle and its screen showed the Codex input
prompt. The API returned `agent_prompted` for the recorded request to
`w1:p2`. This check is for the test lead. Each test seat must meet the
full readiness rule.

The lead used the scoped shell and wrote the private session receipt
with mode 600. The first test snapshot is in
`/private/tmp/li1live.Ow6pZQ/legacy/l6p2-results`. The API reported
Agy `w2:p1`, Claude Code `w3:p1`, and Grok Build `w4:p1` idle with
`interactive_ready: true`. The visible Claude MCP permission prompt
in `w5:p1` remained open. The Claude ask case in `w6:p1` showed its
folder trust prompt and remained blocked.

The first Codex case did not start. The live catalog found two matches
for the default model phrase. The exact route check for the retry is
`sh bin/model-route codex 'gpt-6.2-sol high fast'`. It returned
`gpt-6.2-sol`, high effort, and service tier `priority`. The lead
will receive that route only after its first request ends.


The first lead report is at
`/private/tmp/li1live.Ow6pZQ/legacy/cases/s6-evidence.md`.
It records Agy `Enter`, Claude Code `Down` then `Enter`, and Grok
Build `y`, all from the scoped lead. Agy and Claude Code had their
folder trust prompts before input and reached their expected input
prompts with `interactive_ready: true`. The Claude ask case received
zero keys. The separate Claude permission case received its trust
keys, then zero keys at the MCP permission prompt. The lead saved
scratch guidance, two teams, and a workflow. The workflow team had
Claude Opus 5.5 high as lead and Agy Gemini 3.8 Flash high in plan
mode as reviewer. Both acknowledged setup. The lead received the
saved guidance line.

Grok Build exposed a legacy readiness defect. `agent start` and
`agent get` returned idle and `interactive_ready: true` while the
screen still showed the directory trust question. The test lead sent
`y` after it checked the screen. The prompt cleared, but the written
rule closes its startup window at those flags. This case does not
pass the signed rule. Legacy readiness needs a live pane read that
can distinguish a visible startup dialog from the actual input prompt.

The native app inventory was empty before the branch opened it. Its
core started on `/private/tmp/li1live.Ow6pZQ/native/core.sock`. The
branch wrote the layout record to `native/state/home.json` for lead
`t1`. The following commands keep both requests on their verified
scratch routes. The legacy retry runs in its original unused Codex
case directory. The native lead gets the exact catalog phrase up front.

```sh
set -eu
lantern_s6=/private/tmp/li1live.Ow6pZQ
lantern_control="$lantern_s6/control-l6p2.sh"
cat >"$lantern_s6/legacy/l6p2-codex-retry.md" <<'REQUEST'
Continue the S6 legacy check for only the missing Codex case. Use the
original empty /private/tmp/li1live.Ow6pZQ/legacy/cases/codex directory.
Use the exact catalog phrase gpt-6.2-sol high fast with model-route.
Use the same scoped lead-shell.sh and branch guard for every Herdr command.
If the seat needs a login, stop and report it without keys, restart, or
login details. If no blocker appears, record it and send no keys. Record
the launch argv, before and after screens, key sender, and ready check.
Append the result to the scratch evidence file. Leave prior cases alone.
REQUEST
sh "$lantern_control" agent get w1:p2 >"$lantern_s6/legacy/l6p2-retry-lead.json"
python3 -c 'import json, sys; a = json.load(sys.stdin)["result"]["agent"]; assert a["pane_id"] == "w1:p2" and a["agent_status"] in ("idle", "done")' <"$lantern_s6/legacy/l6p2-retry-lead.json"
sh "$lantern_control" agent prompt w1:p2 "$(cat "$lantern_s6/legacy/l6p2-codex-retry.md")" >"$lantern_s6/legacy/l6p2-codex-retry-sent.json"

cat >"$lantern_s6/native/lead-shell.sh" <<'SH'
#!/bin/sh
set -eu
root=/private/tmp/li1live.Ow6pZQ
plugin=/Users/john/aigora/dev/herdr-lantern-team-preferences
test -S "$root/native/core.sock"
exec sh "$root/control.sh" native env \
    LANTERN_CONFIG_DIR="$root/native/config" LANTERN_DESKTOP_STATE_DIR="$root/native/state" \
    LANTERN_DESKTOP_COMMAND="$plugin/bin/lantern-app" LANTERN_HERD_STATE_DIR="$root/native/state" \
    PATH="/Users/john/.local/bin:$plugin/bin:$PATH" /bin/sh -c "$1"
SH
cat >"$lantern_s6/native/scoped-request.md" <<'REQUEST'
Use the scratch route explicitly for every Herdr, hc, or Lantern app
command. Wrap the entire command in
sh /private/tmp/li1live.Ow6pZQ/native/lead-shell.sh '<command string>'
The shell pins the scratch app home and core socket. If its probe
fails, stop. Do not use legacy Herdr in this check. The exact Codex
catalog phrase for the test seat is gpt-6.2-sol high fast. Save one
evidence file under /private/tmp/li1live.Ow6pZQ/native/cases. Record
which lead sent each key and the screen before each first key.
REQUEST
cat "$lantern_s6/native/request.md" >>"$lantern_s6/native/scoped-request.md"
lantern_terminal=$(python3 -c 'import json, sys; data = json.load(sys.stdin); assert data["layout_ready"]; print(data["terminal"])' <"$lantern_s6/native/state/home.json")
test "$lantern_terminal" = t1
sh "$lantern_s6/native/lead-shell.sh" 'herdr-app ls' >"$lantern_s6/native/pinned.json"
python3 -c 'import json, sys; app = json.load(open(sys.argv[1])); home = json.load(open(sys.argv[2])); assert len(app["spaces"]) == 1 and app["spaces"][0]["id"] == home["space"]' "$lantern_s6/native/pinned.json" "$lantern_s6/native/state/home.json"
sh "$lantern_s6/native/lead-shell.sh" 'hc get t1 --json' >"$lantern_s6/native/lead-agent.json"
sh "$lantern_s6/native/lead-shell.sh" 'hc read t1' >"$lantern_s6/native/lead-visible.log"
python3 -c 'import json, pathlib, sys; d = json.load(open(sys.argv[1])); a = d.get("terminal", d); assert a["address"] == "t1" and a["state"] == "live" and a["status"]["state"] == "idle"; assert pathlib.Path(a["foreground"]["name"]).name == "codex"; s = open(sys.argv[2]).read().lower(); assert "ask codex" in s and all(w not in s for w in ("device code", "sign in", "log in"))' "$lantern_s6/native/lead-agent.json" "$lantern_s6/native/lead-visible.log"
sh "$lantern_s6/control.sh" native /Users/john/.local/bin/hc send t1 "$(cat "$lantern_s6/native/scoped-request.md")" --if idle --json >"$lantern_s6/native/request-sent.json"
```


The legacy Codex retry request was delivered. The native API reported
`t1` live, with Codex in the foreground and status `done`. Its visible
screen showed the idle input prompt. The previous delivery check only
accepted `idle`, so it sent no test request. The fixed check accepts
`idle` or `done` as the plan specifies. The installed `hc send --if`
option only accepts `idle` and `working`. The send uses the fresh checks
and the normal guard against sending to a blocked terminal.

```sh
set -eu
lantern_s6=/private/tmp/li1live.Ow6pZQ
sh "$lantern_s6/native/lead-shell.sh" 'hc get t1 --json' >"$lantern_s6/native/lead-agent.json"
sh "$lantern_s6/native/lead-shell.sh" 'hc read t1' >"$lantern_s6/native/lead-visible.log"
python3 -c 'import json, pathlib, sys; d = json.load(open(sys.argv[1])); a = d.get("terminal", d); assert a["address"] == "t1" and a["state"] == "live" and a["status"]["state"] in ("idle", "done"); assert pathlib.Path(a["foreground"]["name"]).name == "codex"; s = open(sys.argv[2]).read().lower(); assert "ask codex" in s and all(w not in s for w in ("device code", "sign in", "log in"))' "$lantern_s6/native/lead-agent.json" "$lantern_s6/native/lead-visible.log"
sh "$lantern_s6/control.sh" native /Users/john/.local/bin/hc send t1 "$(cat "$lantern_s6/native/scoped-request.md")" --json >"$lantern_s6/native/request-sent.json"
```

The legacy Codex retry opened `w8:p1` with the exact catalog route.
Its startup update dialog blocked input. The scoped lead verified the
foreground was Codex and sent one `Esc` for the visible skip action.
The exact pane then showed the Codex input prompt with status `done`
and `interactive_ready: true`. It stayed on version 0.170.0. The trace
is in the same legacy evidence file. The Grok readiness defect remains.

Observed local session records from the scratch runs remain in place.

- Codex rollouts under `~/.codex/sessions/2026/11/22` identify the
  scratch leads by their workdir in the session metadata.
- Claude Code added scratch directory trust entries in
  `~/.claude.json`. Its scratch workflow lead wrote a rollout under
  `~/.claude/projects/-private-tmp-li1live-Ow6pZQ-legacy-cases-workflow`.
- The Agy scratch workflow reviewer wrote conversation
  `7fec9cfa-c008-4fee-b838-0a7bf5528091` under
  `~/.gemini/antigravity-cli/conversations`. Its scratch path also
  appears in `~/.gemini/antigravity-cli/settings.json`.
- Grok Build added a scratch trust entry in
  `~/.grok/trusted_folders.toml` and saved a session under
  `~/.grok/sessions/%2Fprivate%2Ftmp%2Fli1live.Ow6pZQ%2Flegacy%2Fcases%2Fgrok`.

The revised native check verified one space in the scratch app, the
recorded live lead `t1`, Codex in its foreground, status `done`, and
the idle input prompt. The normal send returned `started: true`.

Native pane evidence is in
`/private/tmp/li1live.Ow6pZQ/native/cases/s6-native-evidence.md`.

| Native case | Terminal | Blocker before the first key | Keys from lead `t1` | Final state |
| --- | --- | --- | --- | --- |
| Codex | `t4` | Optional update | Escape | Live, Codex in foreground, idle input prompt |
| Claude Code | `t5` | Folder trust | Down, Enter | Live, Claude Code in foreground, idle input prompt |
| Agy | `t6` | Folder trust | Enter | Live, Agy in foreground, idle input prompt in plan mode |
| Grok Build | `t7` | Folder trust | `y` | Live, Grok Build in foreground, idle input prompt |
| Claude Code with `startup: "ask"` | `t9` | Folder trust | None | Trust prompt still open |
| Claude Code permission check | `t8` | Folder trust, then MCP permission | Down, Enter at folder trust | MCP permission still open with zero keys sent to it |

The native lead saved and read back the guidance, two teams, and the
named workflow under its scratch lock. Workflow lead `t10` was Codex
GPT-6.2-Sol high Fast. Reviewer `t11` was Claude Code Opus 5.5 high.
The lead received the shipped guidance and “Wait for a task before
assigning work.” Both seats acknowledged setup and returned to done
input prompts. This run left the scratch panes open. No login appeared.
Native sessions added local records in the same provider paths above.
Claude Code trust records cover only the accepted scratch folders.

S6 is blocked on the legacy Grok Build case at product commit
`313671e`. The startup window ends at idle or done with
`interactive_ready` in `launch.sh:364-365` and `prompt.md:487-488`.
The legacy Grok trace shows both flags while the trust prompt still
blocks input. The written rule must keep that window open until the
visible input prompt is usable.

### Stop at John's request

The desktop results above came from the second app with its own core
socket. That run proved the four native startup transitions, the
no-key results at folder trust in `t9` and MCP approval in `t8`, and
workflow setup in `t10` and `t11`. The native evidence file contains
the screens before each first key, the keys from `t1`, and the final
screens. It gives no evidence for a desktop test that uses the existing
Herdr app without opening a second copy. John asked to keep the
existing app and its sessions outside the test.

The legacy evidence file records the Codex, Claude Code, and Agy
transitions in `w8:p1`, `w3:p1`, and `w2:p1`. It also records the
ask case in `w6:p1`, the MCP approval in `w5:p1`, and the Grok Build
readiness defect in `w4:p1`. The legacy Grok Build case still needs a
rule correction and a retest. Both evidence files stay in the scratch
directory. No new app launch is in scope.

Cleanup matched the app and legacy servers to their scratch environment
and verified the native core by its dedicated socket. Scoped `hc kill`
returned exit 0 for `t1` through `t11`. The scoped stop commands
returned exit 0 for legacy sessions `li1-ow6pzq-legacy-fg` and `l6p2`
and the native core. The scratch app process, PID `26808`, exited and
its window closed. The final `ps` check found zero live S6 processes.
It confirmed John's Herdr PID `29763` still had its original start time.
The cleanup log is `/private/tmp/li1live.Ow6pZQ/stop-s6-ps.log`.


### I3 legacy Grok Build retest

Recorded before launch. Scratch root `/private/tmp/l3g.rlXGjJ`. The Herdr session is
`l3g`. The legacy config and state are
`/private/tmp/l3g.rlXGjJ/xc/herdr/plugins/config/aigora.lantern` and
`/private/tmp/l3g.rlXGjJ/xs/herdr/plugins/aigora.lantern`. The empty Grok directory is
`/private/tmp/l3g.rlXGjJ/case/grok`. The alternate desktop app home and core socket
stay unused. The legacy socket is
`/private/tmp/l3g.rlXGjJ/xc/herdr/sessions/l3g/herdr.sock`.

The test launcher and its two command wrappers are
`/private/tmp/l3g.rlXGjJ/run.sh`, `/private/tmp/l3g.rlXGjJ/control.sh`, and
`/private/tmp/l3g.rlXGjJ/lead-shell.sh`. The runner starts with this command.

```sh
sh '/private/tmp/l3g.rlXGjJ/run.sh'
```

The runner checks for an empty scratch workspace before it links and
opens Lantern. It uses these commands.

```sh
scratch='/private/tmp/l3g.rlXGjJ'
repo='/Users/john/aigora/dev/herdr-lantern-team-preferences'
sh "$scratch/control.sh" server >"$scratch/server.log" 2>&1 &
server_pid=$!
sh "$scratch/control.sh" plugin link "$repo"
sh "$scratch/control.sh" plugin action invoke aigora.lantern.open
```

The scratch config uses the saved prompt from `d614403` and pins
the Codex test lead to `gpt-6.2-sol` at high effort with `--no-daemon`.
The runner checks the rendered appendix for the fresh pane rule before
it sends `/private/tmp/l3g.rlXGjJ/request.md` to the scratch lead. The lead resolves
and checks only the Grok Build route. It records launch argv, the
foreground process, the screen before any key, each key and its sender,
and the final readiness check. The exit trap stops only the scratch
session and checks its process tree and John's Herdr PID with `ps`.

The headless retest passed. The rendered legacy appendix contained the
fresh pane rule and its override of the saved workspace brief rule.
The scratch lead `w1:p2` opened Grok Build `grok-4.7-build-fast` at
medium effort in pane `w2:p1`. Preflight passed and the start exited 0.
Herdr reported `idle` and `interactive_ready: true`. A fresh read still
showed “Do you trust the contents of this directory?” for the empty
scratch directory. The foreground process was Grok Build 1.0.92.

The lead sent one `y` to `w2:p1` through the branch guard. After the
settle wait, a second pane read showed the empty Grok Build input prompt
with no active dialog. The same foreground process still reported idle
and interactive ready. The full launch, screen, and key trace is in
`/private/tmp/l3g.rlXGjJ/evidence.md`. This closes the legacy readiness
defect. The earlier native pane evidence remains the desktop proof.

The scratch server stop exited 0. The final `ps` check found zero live
retest processes and the same PID and start time for John's Herdr
`29763`. The retest used a background legacy server and opened no
desktop window. The cleanup record is `/private/tmp/l3g.rlXGjJ/cleanup-ps.log`.
Grok Build kept the scratch trust entry in `~/.grok/trusted_folders.toml`
and its session under `~/.grok/sessions/%2Fprivate%2Ftmp%2Fl3g.rlXGjJ%2Fcase%2Fgrok`.

After C1 to C4, shell syntax and full smoke passed at `36e57f4`.
After the readiness fix, shell syntax and the five separate suites
passed. The full smoke run exited 1 with nine Plan Usage timeouts.
The 30 second direct launch timeout also occurred on a clean
`36e57f4` worktree at load 28.41. The other eight errors were in
`pane get` calls on the unchanged readout path. The comparison
worktree was removed. I2 allows the local commit with these results.
The full smoke pass for the final commit remains a PR gate.
