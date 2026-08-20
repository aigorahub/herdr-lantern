# Trying the Lantern Bridge on Slack

Slack is the easiest of the three channels to trial, and the right one to do
first. It needs no public URL and no tunnel: the bridge polls Slack's servers
rather than listening for them, so nothing on your machine has to be reachable
from outside. It also means the bridge does not know or care whether you typed
from the desktop app or the phone. Both work, with no extra setup, and you can
start a conversation on a laptop and carry it on from a phone.

Budget about ten minutes. Steps 1 to 3 happen in a browser and in Slack;
steps 4 to 6 happen in a terminal.

## Before you start

- Herdr 0.7.5+ running, with this plugin installed or linked.
- `claude` or `codex` on `PATH`. Those are the only two bridge helpers: they
  are the two with a real one-shot mode that also resumes a conversation.
- Python 3 on `PATH`.
- Permission to add an app to your Slack workspace. In a workspace that
  restricts app installs, an admin has to approve it, so ask before you start
  rather than halfway through.

Check the first two now:

```bash
command -v claude codex herdr python3
```

## 1. Create the Slack app

Go to [api.slack.com/apps](https://api.slack.com/apps) → **Create New App** →
**From scratch**. Name it something the channel will recognise (`Lantern` is
fine) and pick your workspace.

In **OAuth & Permissions**, scroll to **Bot Token Scopes** and add exactly
four:

| Scope | Why |
| --- | --- |
| `channels:history` | read the messages in the channel it watches |
| `chat:write` | post its answers back |
| `im:history` | read your DM with the bot, where a conversation continues |
| `im:write` | open that DM |

Add nothing else. The bridge uses no other Slack API. If you added scopes
after installing the app, reinstall it from this same page; scope changes do
not take effect until you do.

Then turn the DM on. Scopes alone do not do it: a Slack app cannot be sent a
direct message until its Messages tab is enabled, and Slack shows either no
message box at all or "Sending messages to this app has been turned off".

Go to **App Home** in the left sidebar, find **Show Tabs**, switch on
**Messages Tab**, and tick **Allow users to send Slash commands and messages
from the messages tab**. This one is a setting rather than a scope, so it
takes effect at once with no reinstall.

If you plan to watch a **private** channel, use `groups:history` instead of
`channels:history`. Everything else is the same.

## 2. Install it and copy the token

Still in **OAuth & Permissions**, click **Install to Workspace** and approve.
Copy the **Bot User OAuth Token**. It starts with `xoxb-`.

That token can read and post in any channel the bot joins. Treat it the way
you would treat a password: do not paste it into a chat window, a ticket, or a
commit. Step 4 keeps it out of files entirely.

## 3. Invite the bot to a channel

Pick a quiet channel for the trial, or make a new one. Everyone in the channel
will see the lantern's answers, so a dedicated channel is the polite choice on
a busy team.

In that channel, send:

```
/invite @Lantern
```

The bridge only ever watches this one channel. It cannot see any other part of
the workspace.

## 4. Find the two ids

`SLACK_CHANNEL` is the channel's id and `SLACK_ALLOWED_USERS` is your own
member id. Both are visible in the Slack apps:

- **Channel id** (`C…`): open the channel, click its name, and scroll to the
  bottom of the **About** tab. Or copy the channel link — the id is the last
  path segment.
- **Your member id** (`U…`): click your avatar → **Profile** → the **⋮** menu
  → **Copy member ID**.

The allowlist is not optional and it is not a warning. A channel with a
credential and an empty allowlist refuses to start, because a bridge that
answers anyone who finds the bot is a bridge that runs commands for anyone who
finds the bot.

**What an allowlisted member gets is a shell on this machine.** The headless
helper runs as the account that started the bridge. Claude is started with
`--allowed-tools "Bash,Read,Glob,Grep,LS"` and no sandbox flag. Codex is
started as `codex exec` with no extra permission flags, so it uses that
CLI's own defaults. `Bash` (when the helper has it) is an ordinary shell:
it can read, write, and delete whatever that account can, and reach the
network. The `bin/herdr` wrapper described further down gates
mutating **`herdr` subcommands** and nothing else — it is not a sandbox, and it
does not stand between a message and the rest of your files.

So `SLACK_ALLOWED_USERS` is the whole security boundary. Every id on it is the
same trust as a seat at your keyboard. For a trial, put your own id there and
nobody else's. The other two channels work the same way: WhatsApp allowlists
sender numbers, and a Telegram chat id is a person's private chat — a group id
is not accepted, because it would authorize every member of the group.

## 5. Configure

Open the config:

```bash
$EDITOR "$(herdr plugin config-dir aigora.lantern)/bridge.conf"
```

Set the channel and the allowlist. Leave `SLACK_BOT_TOKEN` empty:

```sh
BRIDGE_HELPER="claude"
SLACK_BOT_TOKEN=""
SLACK_CHANNEL="C0123456789"
SLACK_ALLOWED_USERS="U0123456789"
```

Every key falls back to an environment variable of the same name when the line
here is empty. That is deliberate, and it is why the token line stays blank:
export the token in your shell instead, and it never reaches disk.

```bash
export SLACK_BOT_TOKEN="xoxb-…"
```

If you would rather write it into the file, that is fine too: `bridge.conf` is
created `0600` on first start, because five of its keys are secrets. Values
from the file and values from the environment are checked the same way —
neither may hold a shell metacharacter, and the refusal names which side it
came from.

Confirm the config without touching the network. `--check` prints what the
bridge resolved, redacts every secret to a byte count, and exits non-zero if
anything is missing:

```bash
sh bridge.sh --check
```

You want `channels: slack` and a `helper:` line naming a CLI that is on
`PATH`.

## 6. Run it

```bash
sh bridge.sh
```

That process is the daemon. Leave it running; the window is the log. Then post
a message in the channel, and an answer should arrive within a few seconds —
the first one is slower, because the helper is starting cold.

Only one bridge may run per state directory. A second one is refused by name
rather than left to fight the first over the same messages, and the pane
action focuses a running bridge instead of seating another.

## What to expect

**It only sees messages sent after it starts.** The cursor begins at the
moment the daemon comes up, so nothing in the channel's history is read. Start
the daemon, then send your test message.

**In a channel it answers only when you mention it.** Type `@Lantern` at the
channel's top level and it replies; say anything else in that channel and it
stays quiet. A mention inside one of its own reply threads reaches nobody, for
the same reason the thread footer gives: Slack's history API does not return
thread replies, so the bridge cannot see them. In its DM you
need no mention, because the DM is the conversation. If the bridge cannot work
out its own user id, it says so in the log and answers every allowlisted
message in the channel until it can, on the grounds that a silent bot is
harder to diagnose than a chatty one.

**It answers you and ignores everyone else.** Messages from anyone outside
`SLACK_ALLOWED_USERS` are dropped, as are the bot's own messages and Slack's
join/leave notices.

**Channel answers land in a thread; the conversation lives in your DM.** A
message in the channel is answered in its own thread, so the channel stays
readable. Do not reply inside the thread: Slack's history API does not return
thread replies, so the bridge cannot see them, and the threaded answer says
so. Open the bot's DM instead and carry on there; it is the same conversation,
because the session follows you rather than the room. The DM is also simply
the better place to talk to it, phone included.

**No terminal, if you want that.** Once the tokens are in `bridge.conf`
rather than exported, set `BRIDGE_AUTOSTART="1"` in the same file and the
Herdr server seats the bridge pane itself on startup.

**Mutating Herdr still needs your confirmation.** Inspect commands run
normally, but anything that creates, starts, focuses, closes, or prompts is
blocked until you confirm the exact target — and over Slack that confirmation
is simply your next message. This is the same gate the lantern pane uses; the
bridge does not widen it.

**Replies are plain and short.** The helper is told it is answering a person
who is probably on a phone.

**Each conversation is its own workdir** under the plugin state directory,
seeded with the lantern prompt. The helper does not read your lantern pane and
cannot see any other conversation.

## When it does not work

| Symptom | Cause |
| --- | --- |
| `no channels configured` | `SLACK_BOT_TOKEN` reached neither the file nor the environment. `export` it in the *same* shell that runs `bridge.sh`. |
| `SLACK_ALLOWED_USERS is empty` | The allowlist is required. Put your `U…` id in it. |
| Daemon starts, nothing answers | The bot is not in the channel (`/invite`), or `SLACK_CHANNEL` is the wrong id, or you are messaging from an account that is not on the allowlist. |
| `not_in_channel` in the log | Same: invite the bot. |
| `missing_scope` in the log | A scope was added after installing. Re-install the app from **OAuth & Permissions**; scope changes need it. |
| `a lantern bridge is already running` | One is. The message names the lock file. Stop that daemon first. |
| `no bridge helper on PATH` | Neither `claude` nor `codex` is installed, or `BRIDGE_HELPER` names something else. |
| You cannot type a DM to the bot | The Messages tab is off. **App Home** → **Show Tabs** → **Messages Tab**, plus the checkbox under it. Not a scope, so no reinstall. |
| The DM never answers, but the channel does | The `im:history` and `im:write` scopes were added after the app was installed. Reinstall it. The bridge log names the missing scope. |

The daemon logs one line per message with the channel, the sender, and a byte
count. It never logs message text or any token, and an unexpected error inside
the webhook is one line naming the exception rather than a stack trace with
your filesystem paths in it. A log is safe to paste when you ask for help —
but read it before you do.

One thing the log does not cover: while a turn is running, the message text is
an argument to the helper process, so any other account on this machine can
read it with `ps auxww`. Tokens and secrets are never passed that way. The
bridge is built for a machine you are the only user of.
