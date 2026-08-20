# Plan: Lantern Bridge v2, a router and a connector

**Status: designed, not started.** Hosting is deliberately on the backburner.
This file exists so the design is not re-derived from scratch later.

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
| What is it for? | Full conversation, not only status. |
| Where does the router run? | Railway. Router alone, 512MB, roughly $6/month at the published per-second rates. |
| Machine offline? | Queue. |
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

## Answering, without picking an agent

`herdr agent start <name> --kind <kind> --pane <id>` accepts 22 kinds and
confirms the agent came up and is ready for input. Herdr is already the
abstraction layer, so Lantern does not need to learn how Grok differs from
Codex.

A conversation is a pane. Messages go in with `herdr agent prompt --wait`;
replies come back with `herdr agent read --source recent`.

Headless helpers are the wrong foundation here: only a few of the 22 have a
real one-shot-plus-resume mode, so building on it silently re-picks Claude.

Three consequences worth the change:

- **Turn boundaries come from `agent_status`** (working, blocked, idle, done)
  rather than from parsing a redrawing TUI. What remains is extracting the new
  text since the prompt.
- **Conversations run in parallel**, one turn per pane, instead of today's
  single global worker.
- **Continuity is the agent's own**, in whatever form that agent keeps it.

Bootstrapping is the first prompt rather than a seeded `CLAUDE.md`, because
file conventions differ per agent and a first turn works for all 22.

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

The hosting itself. Cost and platform are settled; the build is deferred.
