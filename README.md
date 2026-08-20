# Lantern, by Elves

![Lantern, illuminating your herd](assets/lantern-banner.jpeg)

**v0.5.1** — a [Herdr](https://herdr.dev) plugin (`aigora.lantern`).

From the team that brought you [Elves](https://github.com/aigorahub/elves).

Herdr manages the herd. The herd is in the field. Lantern illuminates
the field: who needs you, what they are working toward, jump to a pane,
start a new agent. The sidebar already marks working, blocked, done, or
idle. This plugin does not replace Herdr or wrap the agent CLIs.

It opens as a chat tab in its own Herdr workspace and starts the helper CLI
you already use (Cursor `agent`, Devin, Claude Code, Codex, or Grok). That
CLI drives `herdr`.

Requires Herdr **0.7.5+** on macOS, Linux, or Windows. Windows needs Git for
Windows as well; see [Windows](#windows).

## Install the plugin

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

## Pick your helper CLI

The lantern chat runs **one** CLI. That is independent of
`HELPER_SPAWN_KIND`, which is only the default `--kind` when the helper starts
an agent for your work (usually `claude`).

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
HELPER_SPAWN_KIND="claude"   # default --kind for `herdr agent start`
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
working toward?” for that readout.

Lantern works great with Elves. Without Elves it is still the Herdr
plugin: workspaces, panes, agents. If `.elves-session.json` files exist,
it also snapshots those runs (`bin/elves-floor`). Ask “how’s the night
shift?” It does not cobble or land.

Mutating `herdr` commands (create, start, focus, close, …) go through
`bin/herdr`. After you confirm a path or target, the helper reruns with
`HERDR_HELPER_OK=1`.

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
directory and seats the chat there as a tab named **home**. It does not
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


## Use the lantern from Telegram, WhatsApp, or Slack

> Thinking of running this for a team? Read
> [why Aigora stopped](plans/slack-app-discontinued.md) first. The short
> version: an allowlisted sender gets a shell on the machine running the
> bridge, which is a fair trade for one person on their own machine and not
> one for a team, and chat access to a herd needs that machine awake.

The **Lantern Bridge** is a second pane that answers chat messages with the
same lantern. Open it with `herdr plugin action invoke aigora.lantern.bridge`,
or run `sh bridge.sh` from a checkout. Leave it running; it is a daemon.

One bridge runs at a time. Invoking the action again focuses the pane that is
already open, and a second daemon on the same state directory refuses to start
and names the lock file — two pollers on one token would answer every message
twice.

It does not read the lantern chat. Every conversation gets its own workdir
under the plugin state directory, seeded with the same `prompt.md` plus a
remote appendix, and a headless helper runs there. Mutating `herdr` is still
blocked until you confirm, and the confirmation is a chat message.

The bridge helper is `claude` or `codex` only. Those are the two with a
headless mode that also resumes a conversation.

```bash
$EDITOR "$(herdr plugin config-dir aigora.lantern)/bridge.conf"
```

Every key falls back to an environment variable of the same name when the line
is empty, so tokens can stay out of the file.

| Key | What it is |
| --- | --- |
| `BRIDGE_HELPER` | `claude` or `codex`; empty = first of those on `PATH` |
| `BRIDGE_MODEL` | optional `--model` |
| `BRIDGE_EFFORT` | `claude --effort`, `codex model_reasoning_effort` |
| `BRIDGE_CWD` | search root mentioned to the helper (default `~`) |
| `BRIDGE_SPAWN_KIND` | default `--kind` for `herdr agent start` |
| `BRIDGE_EXTRA_ARGS` | extra unquoted helper CLI tokens; permission and sandbox flags are refused |
| `TELEGRAM_BOT_TOKEN` | token from @BotFather |
| `TELEGRAM_ALLOWED_CHATS` | comma-separated numeric ids of **people**: each one a private chat, or a user id |
| `SLACK_BOT_TOKEN` | `xoxb-` token |
| `SLACK_CHANNEL` | the one channel id the bridge watches |
| `SLACK_ALLOWED_USERS` | comma-separated member ids (`U…`) |
| `WHATSAPP_ACCESS_TOKEN` | Cloud API access token |
| `WHATSAPP_PHONE_NUMBER_ID` | Cloud API phone number id |
| `WHATSAPP_VERIFY_TOKEN` | string you also type into Meta's webhook form |
| `WHATSAPP_APP_SECRET` | app secret; required, it signs every webhook |
| `WHATSAPP_ALLOWED_NUMBERS` | comma-separated E.164 senders |
| `WHATSAPP_WEBHOOK_HOST` | bind host, default `127.0.0.1` |
| `WHATSAPP_WEBHOOK_PORT` | bind port, default `8787` |

**A channel turns on when its credential is set, and refuses to start until
its allowlist names who may talk to it.** With no channels configured at all
the bridge exits and points at `bridge.conf.example`. Check a config without
touching the network:

```bash
sh bridge.sh --check
```

**Telegram.** Talk to [@BotFather](https://t.me/BotFather), `/newbot`, keep the
token. Message your bot once, then read your chat id out of
`https://api.telegram.org/bot<TOKEN>/getUpdates`. Put that id in
`TELEGRAM_ALLOWED_CHATS`. The bridge long-polls, so nothing has to be
reachable from outside.

The id you took from your own private chat is your user id, and that is what
the allowlist is for: **a chat id here is a person, not a room.** A group or
supergroup id would name a room, and the bridge will not answer one — putting
it here would authorize every member of the group, present and future, to run
the commands described under [What a sender gets](#what-a-sender-gets). To use
the bridge in a group, allowlist the user ids of the people who may drive it;
their messages are answered there and everyone else's are dropped.

**Slack.** Create an app at api.slack.com, add the bot scopes
`channels:history` and `chat:write`, install it to the workspace, and invite
the bot to the channel (`/invite @yourbot`). `SLACK_CHANNEL` is that channel's
id (`C…`), and `SLACK_ALLOWED_USERS` holds your member id (`U…`). The bridge
polls that one channel and ignores everything it did not hear from an
allowlisted human. Step by step, including where to find both ids and what to
do when it does not answer: [docs/slack-trial.md](docs/slack-trial.md).

**WhatsApp.** Meta Cloud API only. Create a Meta app with the WhatsApp
product, note the phone number id, the access token, and the app secret. The
bridge binds localhost, and Meta needs a public HTTPS URL, so put a tunnel in
front of it:

```bash
cloudflared tunnel --url http://127.0.0.1:8787
```

Give Meta that URL as the callback, with `WHATSAPP_VERIFY_TOKEN` as the verify
token, and subscribe to `messages`. Every POST is checked against
`X-Hub-Signature-256` before it is parsed; a body that does not verify gets a
401 and is never read as a message. That is why `WHATSAPP_APP_SECRET` is
required rather than optional: without it the tunnel is an open door.

Text messages only, both directions. Replies are split at each provider's
limit.

### What a sender gets

**An allowlisted sender gets a shell on this machine.** The headless helper
runs as the user who started the bridge. Claude is started with
`--allowed-tools "Bash,Read,Glob,Grep,LS"` and no sandbox flag. Codex is
started as `codex exec` with no extra permission flags, so it uses that
CLI's own defaults. `Bash` (when the helper has it) is an ordinary shell:
it can read, write, and delete anything that account can, and reach the
network. The `bin/herdr` wrapper gates mutating **`herdr`
subcommands** — create, start, focus, close, prompt, `send-*` — and nothing
else. It is not a sandbox, and it does not stand between a message and the
rest of your files.

So the allowlists are the whole security boundary. An id on one is the same
trust as handing that person your keyboard. Treat every one of them that way:

- Put only your own ids on the allowlists to start with.
- A Telegram chat id is a person's private chat. A group id is not accepted;
  see the Telegram notes above.
- Slack allowlists member ids, WhatsApp allowlists sender numbers. Neither
  channel is authorized by the room.
- `BRIDGE_EXTRA_ARGS` is appended after the bridge's own flags and a later
  flag wins, so the tokens that would switch the tool list or the sandbox off
  are refused by name. `sh bridge.sh --check` prints a loud line whenever any
  extra args are set at all.

Two things the daemon does not protect against, said plainly:

- Message text is never logged, but while a turn runs it is an argument to the
  helper process, so any other account on this machine can read it out of
  `ps auxww` for up to fifteen minutes. Tokens and secrets are never argv and
  never logged.
- `bridge.conf` is created `0600`, because five of its keys are secrets. If
  you loosened that mode, or you keep the secrets in your environment on a
  shared machine, they are readable by whoever can read that.

The bridge is built for a machine you are the only user of.

## Windows

Herdr ships for Windows, and so does this plugin. It runs the same POSIX shell
files there, through Git Bash. There is no separate Windows code path.

You need:

- Herdr for Windows.
- [Git for Windows](https://git-scm.com/download/win).
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
helper CLI it fronts has whatever tools you gave it, as you. For the bridge,
where the person at the other end is not necessarily sitting here, read
[What a sender gets](#what-a-sender-gets) before you fill in an allowlist.
