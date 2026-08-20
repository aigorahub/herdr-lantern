# Plan: Lantern Bridge v2, a router and a connector

> **Conditional, and not the path Aigora took.** This design assumes each
> person's machine is reachable while they are messaging. Aigora stopped there
> because SOC 2 requires machines to be able to sleep, which no laptop-bound
> design survives. The design itself still holds for a team whose machines can
> stay awake. Read
> [`slack-app-discontinued.md`](slack-app-discontinued.md) before building
> from this.

**Status: designed; cloud path chosen; not started.** Build is blocked on the
four open questions below. Session record and next steps:
[`plans/bridge-v2-handoff.md`](bridge-v2-handoff.md).

## Why the current bridge cannot become a team tool

Two faults, and only one is about hosting.

**It is single-machine.** The daemon sees exactly one Herdr session: the one on
the machine it runs on. Hosting it on somebody's laptop makes it that person's
tool and makes everyone else depend on their laptop. Moving the same daemon to
a cloud host makes it see nothing at all, because a herd only exists where the
panes are.

**It answers by spawning its own agent.** `BRIDGE_HELPER` is `claude` or
`codex`, and every conversation starts a fresh headless one. Herdr detects 22
agent kinds and the team uses several of them. A plugin whose job is to
illuminate the herd should not be adding another agent to it.

Fixing the second makes the first tractable.

## Decisions taken

| Question | Answer |
| --- | --- |
| Whose herd does a message reach? | The sender's own. |
| What opens on their machine? | Lantern. The Slack app is a remote control for the Lantern plugin (its prompt / skill), not a generic coding agent. |
| What is it for? | Full conversation with Lantern, not only status. |
| Where does the router run? | Railway. Router alone, 512MB, roughly $6/month at the published per-second rates. |
| Machine offline? | Queue. |
| Missing Herdr or missing Lantern? | Tell them, with install directions. Do not try another machine and do not run the work in the cloud. |
| One repo or two? | One. |
| Slack shape | DM: full conversation, no mention. Channel: answer only an `@Lantern` mention, reply in the thread. |
| Who may use it? | Everyone in the Aigora workspace. |

## Architecture

**The router**, hosted, runs no agent and holds no repository. It owns the
Slack app, the WhatsApp webhook, and the Telegram bot; the chat credentials;
the map from chat identity to person; and the queue. It needs no model API key.
The stable public URL permanently retires the WhatsApp tunnel.

**The connector** is this plugin, running in each person's Herdr. It dials out
to the router and presents that person's herd. It is the only thing that
touches their code, their credentials, or their agents. Nobody depends on
anybody else's computer; you depend on your own machine for your own herd,
which is what a herd is.

**Transport is HTTPS long polling**, roughly 25-second holds, `urllib` only.
No WebSocket dependency, no virtualenv, no inbound port, and it survives
corporate proxies. The plugin stays readable in an afternoon.

**Enrollment replaces the allowlist.** Today `SLACK_ALLOWED_USERS` is a trust
list maintained by hand. In v2 you prove a machine is yours by running an
enrollment command on it, and the router binds your chat identity to that
connector. A workspace member who has not enrolled reaches nothing and is told
how to enroll. That is what makes opening it to the whole workspace safe: the
door is wide, but each person can only ever reach their own machine.

## What a DM does

Someone DMs the Lantern Slack app (or `@Lantern` in a channel). The hosted app
looks up **that Slack user** and talks to **their** machine.

**If they are enrolled and Herdr is up with Lantern installed,** the connector
opens (or focuses) the Lantern session in their Herdr — the plugin chat with
its prompt/skill, the same thing `hsh` / `prefix+H` opens locally. The Slack
message is the first turn of that conversation. Lantern then does what Lantern
does: illuminate the field, walk to a pane, start an agent when they ask. It
does not start a random Grok or Claude in their stead.

**If they do not have Herdr,** the Slack app does not open anything on anyone
else's computer. It replies that they need Herdr, and how:

```
Herdr 0.7.5+ on macOS, Linux, or Windows.
https://herdr.dev
```

**If they have Herdr but not Lantern** (the plugin is missing, unlinked, or
disabled), it replies that they need the Lantern plugin, and how:

```
herdr plugin install aigorahub/herdr-lantern
```

Then open it once locally (`hsh`, or `herdr plugin action invoke aigora.lantern.open`)
and enroll so Slack knows this machine is theirs.

**If Herdr is installed but the laptop is asleep or the connector is not
dialled in,** that is not "you don't have Herdr." The message waits on the
router (see stale-queue, still open). A setup miss gets directions; an offline
machine gets a queue.

## Answering: open Lantern, do not pick a coding agent

The Slack app opens **Lantern** in the sender's Herdr. That is the plugin
session (`aigora.lantern`), with its prompt/skill, not `herdr agent start`
of a random `--kind`. Whatever helper they already chose in `helper.conf`
(Grok, Cursor, Codex, Claude, …) is what Lantern runs as. The cloud app
never chooses Claude, never ships a model key, and never starts a coding
agent on their behalf. If they later ask Lantern to seat one, Lantern does
that the way it does locally.

A conversation is that lantern pane. Messages go in with
`herdr agent prompt --wait`; replies come back with
`herdr agent read --source recent`.

Headless `claude -p` / `codex exec` are the wrong foundation: they silently
re-pick Claude and they are not Lantern.

Three consequences worth the change:

- **Turn boundaries come from `agent_status`** (working, blocked, idle, done)
  rather than from parsing a redrawing TUI. What remains is extracting the new
  text since the prompt.
- **Conversations run in parallel**, one lantern turn per person, instead of
  today's single global worker.
- **Continuity is Lantern's own**, in whatever form that helper keeps it.

## What carries over from 0.5.x

The adapters, allowlists, message splitting, HMAC verification, and dedupe are
transport code with no local dependency. They move to the router. Polling is
replaced by the Slack Events API and webhooks. `run_helper` is deleted.

Two of the three open issues are retired rather than fixed:

- **#9**, one slow conversation blocking every channel: gone, panes are
  per-conversation.
- **#8**, Slack dropping the middle of a burst: gone, events are pushed rather
  than paged out of history.
- **#10**, WhatsApp answering a redelivery twice: still needs a seen-id set,
  now at the router.

## Open questions, to settle before any code

1. **The permission model.** Full conversation means an enrolled person gets a
   full agent with a shell on their own machine, driven from a phone. The
   `bin/herdr` gate covers mutating `herdr` subcommands and nothing else, and
   the 22 agents have 22 permission models. Options: rely on each agent's own
   defaults and treat enrollment as the whole boundary; start remote panes in a
   constrained directory or a git worktree so the blast radius is one repo; or
   gate writes behind a chat confirmation.
2. **Channel replies.** A threaded answer is visible to everyone in the
   channel, and agent output quotes file contents and paths. Should a channel
   reply be shortened or redacted compared to a DM reply?
3. **Thread ownership.** Alice starts a thread, Bob replies in it. Whose herd?
4. **Stale queue threshold.** Queued work that arrives hours late and starts
   editing code is the failure mode. Above what age does a queued message need
   a confirmation reply before it runs?

## Not in this plan

The hosting itself. Cost and platform are settled. The build waits on the
four questions above; see the handoff for order of work.
