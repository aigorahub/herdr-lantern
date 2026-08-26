# Lantern, by Elves

![Lantern, illuminating your herd](assets/lantern-banner.jpeg)

**v0.9.11** is a [Herdr](https://herdr.dev) plugin (`aigora.lantern`).

From the team that brought you [Elves](https://github.com/aigorahub/elves).

Herdr manages the herd. The herd is in the field. Lantern illuminates
the field: who needs you, what they are working toward, jump to a pane,
start a new agent. The sidebar already marks working, blocked, done, or
idle. This plugin does not replace Herdr or wrap the agent CLIs.

It opens as a chat tab in its own Herdr workspace and starts the helper CLI
you already use (Cursor `agent`, Devin, Claude Code, Codex, or Grok). That
CLI drives `herdr`.

Requires Herdr **0.7.5+** and Python 3 on macOS, Linux, or Windows. Model
routing accepts `python3`, `python`, or the Windows `py -3` launcher. Windows
also needs Git for Windows. See [Windows](#windows).

## Install the plugin

One command after Herdr is installed (installs the plugin and opens it):

```bash
herdr plugin install aigorahub/herdr-lantern && herdr plugin action invoke aigora.lantern.open
```

Or run `install.sh` from a checkout. The first lantern chat asks what to
open when you just name a repo (harness, model, setting). After that,
"open battle-paddle" uses that default.

If you already have a coding agent open, paste the block at the top of
[the guide](https://aigorahub.github.io/herdr-lantern/).

From the marketplace / GitHub (after this release is on `main`):

```bash
# If you previously used `herdr plugin link` for this repo, unlink first.
herdr plugin unlink aigora.lantern
herdr plugin install aigorahub/herdr-lantern
```

Local checkout while developing:

```bash
herdr plugin link /path/to/herdr-lantern
```

Do not run link and GitHub install at the same time for the same plugin id.

### Updates

At light-up the lantern checks whether a newer version is published and, if
one is, offers the update. It asks first and never upgrades itself silently.
There is no `herdr plugin update`; a GitHub install refreshes by running the
install command above again, and from inside the chat that goes through the
same mutate gate as everything else. A linked checkout is never reinstalled
over — the lantern says the checkout is behind and leaves the `git pull` to
you. After an update, quit the chat and reopen the lantern; the note under
[Open it](#open-it) about a leftover tab applies. The check is one
background fetch of the published manifest with a few seconds' budget, so it
never delays the light-up; offline, or before the fetch lands, the lantern
says nothing about updates.

## Pick your helper CLI

The lantern chat runs **one** CLI. That is independent of the spawn
default (`HELPER_SPAWN_KIND`, `HELPER_SPAWN_MODEL`, `HELPER_SPAWN_EFFORT`),
which is what Lantern opens when you name a repo and no harness, model, or
setting.

Leave `HELPER_AGENT` empty to use the first of `agent`, `devin`, `claude`, `codex`,
`grok` on `PATH`. Launch prepends `~/.local/bin`, `~/bin`, and Homebrew.

| Helper you want | Install that CLI | `helper.conf` |
| --- | --- | --- |
| Cursor Ultra | Cursor CLI on `PATH` as `agent` (also `cursor-agent`) | `HELPER_AGENT="agent"` · `HELPER_MODEL="cursor-grok-4.6-high-fast"` (empty also defaults to that) · `HELPER_PERMISSION="smart"` (`--auto-review`) |
| Devin | [Devin CLI](https://docs.devin.ai) — typically `~/.local/bin/devin` | `HELPER_AGENT="devin"` · leave `HELPER_MODEL` empty (Free rejects `--model`; Devin uses `~/.config/devin/config.json`) · `HELPER_PERMISSION="smart"` |
| Claude Code | [Claude Code](https://code.claude.com/docs) on `PATH` as `claude` | `HELPER_AGENT="claude"` · optional `HELPER_MODEL` · optional `HELPER_EFFORT` (`--effort`) |
| Codex | [Codex CLI](https://github.com/openai/codex) on `PATH` as `codex` | `HELPER_AGENT="codex"` · optional `HELPER_MODEL` · optional `HELPER_EFFORT` (`model_reasoning_effort`) |
| Grok | Grok CLI on `PATH` as `grok` (often `~/.grok/bin`) | `HELPER_AGENT="grok"` · optional `HELPER_MODEL` · optional `HELPER_EFFORT` (`--reasoning-effort`) |

Each person on the team sets their own file. Nobody shares one model string.

```bash
$EDITOR "$(herdr plugin config-dir aigora.lantern)/helper.conf"
```

```sh
HELPER_AGENT="agent"         # agent, devin, claude, codex, grok; empty = first on PATH
HELPER_MODEL="cursor-grok-4.6-high-fast"  # optional --model; leave empty for Devin
HELPER_EFFORT=""             # unused for Devin and Cursor agent
HELPER_CWD="~"               # search root mentioned to the helper
HELPER_SPAWN_KIND="claude"   # default --kind when you just name a repo
HELPER_SPAWN_MODEL=""        # spoken phrase or id; empty = that kind's live default
HELPER_SPAWN_EFFORT=""       # optional extra effort when the model id lacks one
HELPER_PERMISSION="smart"    # devin / agent: auto | accept-edits | smart | dangerous
HELPER_EXTRA_ARGS=""         # extra unquoted CLI tokens
```

`launch.sh` parses `KEY=value` only; it does not source the file as shell.

The helper prompt is copied once to `prompt.md` in the same config directory
and is never overwritten. After upgrading the plugin, copy the new
`prompt.md` from the repo over that file if you want the latest rules. Devin
also gets the same text as `.windsurf/rules/lantern.md` in the plugin
state workdir. Cursor `agent` gets `.cursor/rules/lantern.mdc`. Claude
Code gets `CLAUDE.md`.

On light-up it snapshots the field (`bin/goals-floor`): pane titles, Claude
`/goal` / recap lines, and who is waiting on you. Ask “what are they
working toward?” for that readout. “What’s going on” names every open tab
after who needs you, with working and blocked first, then done and idle.

Lantern works great with Elves. Without Elves it is still the Herdr
plugin: workspaces, panes, agents. If `.elves-session.json` files exist,
it also snapshots those runs (`bin/elves-floor`). Ask “how’s the night
shift?” It does not cobble or land.

Mutating `herdr` commands (create, start, focus, close, …) go through
`bin/herdr`, which reruns them with `HERDR_HELPER_OK=1`. Ask for
something and the lantern does it, then tells you what it did. It stops
to ask only when the ask itself is unclear: no target named ("clean
up"), or a name that matches two repositories. Closing, killing, and
removing are not exceptions. It never closes the lantern's own tab.

Agents the lantern seats for you start without stopping for ordinary
approvals. `agent start` passes each kind's own flags after `--`: Claude
Code and Grok get `--permission-mode auto`, Cursor `agent` gets
`--auto-review --trust`, Codex gets
`--dangerously-bypass-approvals-and-sandbox` so a Codex tab does not wait
for command or sandbox confirms. The herdr wrapper puts that Codex flag
immediately after `--`, including when the seat omitted it or placed it
after resume or review. Kinds without a listed tier get no extra flags.
bypassPermissions, `--yolo`, `--force`, and `--always-approve` stay off
unless the user asks for yolo and confirms the exact flag and the protections it removes.

Seat language selects the CLI and model separately. "Cursor" uses `--kind
cursor` with the live Cursor Sol default. Bare "Grok" uses `--kind cursor`
with a live Cursor Grok model. "Grok Build" and "SuperGrok" use `--kind
grok`. Lantern checks the selected model with `bin/model-preflight` before it
seats anything. It stops on a failed check or a model it knows
will not work, and names one live substitute. A usage line with no reset
time is still valid. Missing quota info on a harness that has no usage
command is not a failed check.

"There is a PR on battle-paddle, get a Codex review" is a review route.
Lantern resolves the repository and pull request with Git and `gh`. It checks
the model before it creates a workspace or tab. It reuses a matching Codex,
Cursor, Cursor Grok, or Grok Build agent. Otherwise it seats the requested
kind with its real review or read-only plan command. Lantern never checks out
the pull request or edits the product repository.

GitHub repositories Lantern creates are private. `gh repo create` always
includes `--private`. It does not pass `--public` unless you explicitly ask
for a public repo.

After a seat the lantern renames the agent's tab to
`<slug> · <kind>` and says in one line what is running where: the slug, the
kind, the live model, effort, fast state, and the task the agent was given, or
that it has none yet.

How to use it (GitHub Pages, after this lands on `main`):
[aigorahub.github.io/herdr-lantern](https://aigorahub.github.io/herdr-lantern/).
Team setup notes: [howto.html](howto.html). Changelog: [CHANGELOG.md](CHANGELOG.md).

## Open it

```bash
hsh
```

That runs `herdr plugin action invoke aigora.lantern.open`. Put `hsh`
from this repo on your `PATH` (for example `ln -s "$PWD/hsh" ~/bin/hsh`).
Run it from your own terminal. Inside the lantern's pane `herdr` is the
mutate-gated wrapper, and opening a second lantern is a change like any
other, so there it asks first rather than doing it.

On Windows there is no symlink step. Either run the action directly:

```powershell
herdr plugin action invoke aigora.lantern.open
```

or put a one-line `hsh.cmd` somewhere on your `PATH`:

```bat
@echo off
herdr plugin action invoke aigora.lantern.open
```

Once it is installed, open it in Herdr with **Ctrl+B, then capital H**.
`prefix+h` is already “focus pane left”, so the binding has to be capital H:

```toml
[[keys.command]]
key = "prefix+H"
type = "plugin_action"
command = "aigora.lantern.open"
description = "Open lantern"
```

Then `herdr server reload-config`. No Herdr restart is needed for plugin
script or config edits; reopen the lantern.

Quit the chat before you upgrade, relink, or reinstall the plugin.
`herdr plugin install` and `herdr plugin link` drop Herdr's record of a
running lantern pane, so that tab stops answering to
`herdr plugin pane focus` and the next open seats a fresh chat beside it.
Close the leftover tab with `herdr pane close <pane_id>` if that happens.

## Where it sits

The first open creates a workspace labelled **🔥 lantern** at your home
directory and seats the chat there as a tab named **home** — suffixed with
what the chat runs, `home · claude · opus` say, so the sidebar says which
CLI and model is answering. The chat's first line names the same. It does not
drop a tab into whatever workspace you are in, and it closes the empty
shell tab the new workspace comes with, so the workspace holds the chat
alone. The lantern in the sidebar is how you find it at a glance.

Every later open reuses it. A chat that is still running is focused
wherever it sits, so moving or renaming that tab is safe. If the chat has
exited, Lantern seats a new one in the same workspace and closes nothing
else. It does not open a second lantern workspace, and two fast key
presses cannot race into two.

Lantern remembers both ids under the plugin state directory
(`workspace.id`, `pane.id`) and checks them before it uses them: Herdr
reuses ids after a restart, so a remembered workspace counts only while it
still carries the `🔥 lantern` label, and a remembered pane only while it
is still a lantern chat. Keep that label if you want the workspace reused
after the chat closes. Rename it and the next open makes a fresh one.

A new workspace lands **last** in the sidebar. There is no pin-to-top;
drag it where you want it.

The chat runs in the plugin state workdir. Home is only where the
workspace sits and the search root the helper is told about
(`HELPER_CWD`). Your repositories keep their own workspaces.

The tab closes when the helper CLI exits, and the lantern workspace goes
with it when that chat was the only tab in it. Herdr does not take Escape
until then; Escape stays inside the CLI.

For Cursor `agent`: **Ctrl+C** (twice if a turn is running), or **Ctrl+D** on an
empty prompt.

## Windows

Herdr ships for Windows, and so does this plugin. It runs the same POSIX shell
files there, through Git Bash. There is no separate Windows code path.

You need:

- Herdr for Windows.
- [Git for Windows](https://git-scm.com/download/win).
- Python 3 through `python3`, `python`, or `py -3`. Lantern stops before any
  seat change if none of these commands works.
- `C:\Program Files\Git\bin` on your user `PATH`. That directory holds
  `sh.exe`, and Herdr starts the plugin with `sh open.sh` and `sh launch.sh`.
  The Git installer does not put it there: its default option adds
  `C:\Program Files\Git\cmd`, which holds `git.exe` and no shell. So this step
  is needed even when Git already works in your terminal. Add that one
  directory. Do not add `C:\Program Files\Git\usr\bin`: it would shadow Windows
  tools such as `find.exe` and `sort.exe`, and the plugin does not need it.
- One helper CLI on `PATH`, the same as anywhere else.

After you add that PATH entry, **stop the Herdr server**, not just the window:

```powershell
herdr server stop
```

Herdr keeps a persistent server, and your panes live inside it. Closing and
reopening the app leaves that server running with the environment it started
with, so it still cannot find `sh` and the action fails with
`program not found`. Stopping the server ends every pane in it, so finish what
is running first.

A new PATH entry also has to reach whatever launches Herdr. The Start menu and
the taskbar are Explorer, and Explorer keeps the environment it started with
too. Sign out and back in, or restart Explorer, before you start Herdr again.
Confirm it took in a new terminal before launching:

```powershell
where sh
```

If that prints `C:\Program Files\Git\bin\sh.exe`, Herdr started from there will
find it. If it prints nothing, the new server will fail the same way.

Check the setup:

```powershell
sh --version
herdr plugin action list
```

The action should report `"platforms":["linux","macos","windows"]`.

Two Windows details worth knowing:

- The `bash` on your `PATH` is probably not Git Bash. Windows ships a
  `bash.exe` stub in `WindowsApps` that launches WSL. This plugin never calls
  `bash`, and neither should anything you add to it.
- The `python3` on your `PATH` is probably the zero-byte Microsoft Store
  alias, which opens the Store instead of running Python. Lantern checks an
  interpreter by running it, so the field snapshot works with `python`, `py`,
  or a real `python3`. If none exists you lose the snapshot and nothing else.

Run Herdr for Windows natively. WSL is not the supported path.

## Tests

```bash
sh tests/smoke.sh
```

On Windows, run it through Git Bash:

```powershell
& 'C:\Program Files\Git\bin\sh.exe' tests/smoke.sh
```

GitHub Actions runs the same suite on Linux, macOS, and Windows for every pull
request.

## Trust

This runs as your user with your environment. Read `launch.sh`, `lib.sh`, and
`bin/herdr` before installing. Devin `HELPER_PERMISSION=dangerous` skips
Devin’s own approval UI; the herdr wrapper still requires `HERDR_HELPER_OK=1`
for mutating commands.

That wrapper gates mutating `herdr` subcommands only. It is not a sandbox: the
helper CLI it fronts has whatever tools you gave it, as you.

Earlier releases shipped the Lantern Bridge, which answered Telegram,
WhatsApp, and Slack with the same lantern. It was removed in 0.8.0: an
allowlisted sender got a shell on the machine running it, a fair trade for
one person on their own machine and not one for a team. The reasoning is
recorded in [plans/slack-app-discontinued.md](plans/slack-app-discontinued.md).
