# Handoff: Lantern on Slack, cloud path

**For the next person or agent.** Written 2026-08-20 after the conversation
that landed PR #13. Design lives in
[`plans/bridge-v2-router-connector.md`](bridge-v2-router-connector.md). This
file is the session record, the decisions, and the next steps. Do not
re-derive the product from Slack history or from PR #12.

Repo: `aigorahub/herdr-lantern`. Latest release on `main` is **v0.5.1**.

---

## What the conversation was about

The laptop Slack bridge is at a stopping point. The team does not want the
Lantern Slack app hosted on one person's computer.

A first reading of that stop was "Claude is unreliable, so we cannot host
this." That is the wrong diagnosis. Claude was only the helper the current
bridge spawns because it has a headless one-shot mode. Swapping Grok or
Cursor into the same daemon still glues the bot to one machine.

The real shape:

- The **Slack app** can live in the cloud, for the whole Aigora workspace.
- **Lantern itself** still runs on the person who messaged, because that is
  where their Herdr and their herd are.
- A DM does not start a random coding agent. It opens **Lantern** (the
  plugin, its prompt / skill) on **that person's** computer.

Lifting `bin/lantern-bridge` onto Railway as-is is not the product. That
would run a helper in an empty container with nobody's panes.

---

## Product, in one paragraph

Someone DMs the Lantern Slack app (or `@Lantern` in a channel). The hosted
app looks up that Slack user and talks to **their** machine. If Herdr is up
and the Lantern plugin is installed and enrolled, the connector opens (or
focuses) the Lantern session there — the same thing `hsh` / `prefix+H`
opens locally — and the Slack text is the first turn. Lantern then does
what Lantern does: illuminate the field, walk to a pane, start an agent
when they ask.

If they do not have Herdr, Slack tells them they need it, with directions
(`https://herdr.dev`, 0.7.5+, macOS / Linux / Windows). If they have Herdr
but not Lantern, Slack tells them:

```
herdr plugin install aigorahub/herdr-lantern
```

then to open it once locally and enroll. Do not try another person's
machine. Do not run the work in the cloud. Laptop asleep is not "you don't
have Herdr": the message waits on the router (stale-queue threshold still
open).

---

## What landed

| Item | Where |
| --- | --- |
| v0.5.0 messaging bridge (Telegram / Slack / WhatsApp, laptop daemon) | `main`, PR #7 |
| v0.5.1 mutate-gate PATH hole on the bridge | `main`, PR #11, release v0.5.1 |
| Bridge v2 design (router + connector) | `main`, PR #13, `plans/bridge-v2-router-connector.md` |
| DM opens Lantern on the sender's machine; missing Herdr / plugin get install directions | same PR, commit `4caf6b6` |

PR #13 is a merge commit (`8f38f82`), not a squash.

---

## What is still open, and what not to do

**Do not land [PR #12](https://github.com/aigorahub/herdr-lantern/pull/12)
(`feat/slack-threads-dm`) unless someone still wants the laptop trial.** It
is local-Slack polish (threads, DMs, autostart). CI is green and a
pre-merge review was folded in, but it is the architecture we are leaving:
one machine, one daemon. Remaining caveats if anyone does land it:
`[[startup]]` vs `min_herdr_version = 0.7.5` is unverified; live
`conversations.open` shape is unverified; Slack sessions reset on upgrade
(channel key → member key); needs `im:history` / `im:write` and the
Messages tab.

Open issues from the v0.5 audit, still on `main`:

- [#8](https://github.com/aigorahub/herdr-lantern/issues/8) Slack drops the
  middle of a burst — **retired by v2** (Events API, not paged history).
- [#9](https://github.com/aigorahub/herdr-lantern/issues/9) one slow
  conversation blocks every channel — **retired by v2** (one lantern turn
  per person).
- [#10](https://github.com/aigorahub/herdr-lantern/issues/10) WhatsApp
  answers a redelivered webhook twice — **still needs a seen-id set, on
  the router**.

Do not implement v2 code until the four product questions in the design
are answered. They are repeated below.

---

## Architecture to implement, when unblocked

Two processes, one repo.

**Router (cloud, Railway, ~$6/month, 512MB).** Owns the Slack app (and
later WhatsApp / Telegram), tokens, the map from Slack user → person, and
the queue. No agent, no repository, no model API key. Production Slack
delivery is HTTP Events API to a public HTTPS URL, not the current
`conversations.history` poller and not Socket Mode.

**Connector (this plugin, in each person's Herdr).** Dials out over HTTPS
long poll (~25s, `urllib` only). Opens or focuses the Lantern pane on that
machine and relays the message. Enrollment binds "this Slack account ↔
this machine." Anyone in the Aigora workspace may talk to the bot; they
can only ever reach their own computer.

`run_helper` (headless `claude` / `codex`) is deleted in v2. The cloud app
never chooses the helper; `helper.conf` on that machine does.

---

## Open questions — settle these before any code

Copied from the design. These are product calls, not engineering ones.

1. **Permission model.** A Slack DM is driving Lantern on their own
   machine from a phone. Options: enrollment is the whole boundary; start
   the lantern pane in a constrained directory / worktree; or confirm
   writes in chat.
2. **Channel replies.** A thread is visible to everyone, and agents quote
   paths and file contents. DM-full vs channel-redacted?
3. **Thread ownership.** Alice starts a thread, Bob replies. Whose Herdr?
4. **Stale queue.** Above what age does a queued message need "run this?"
   before it executes, so a prompt that sat for hours does not start
   editing code?

---

## Next steps, in order

1. **Get answers to the four questions above.** Until then, do not start
   the router or the connector.
2. **Leave PR #12 open or close it.** Closing is cleaner if the laptop
   trial is done. Do not merge it as a stepping stone to v2; the poller
   and the headless helper do not survive.
3. **When unblocked, implement v2 in this repo** (one repo, not two):
   router first (Slack Events API + identity map + queue + "you need
   Herdr / you need Lantern" replies), then the connector that opens the
   Lantern plugin session, then enrollment.
4. **Carry #10 to the router** (WhatsApp seen-id) when that channel moves.
   Do not spend a PR fixing #8 or #9 on the laptop daemon.
5. **Do not spawn Claude (or any helper) in the cloud.** The hosted
   process is a receptionist.

Install directions the Slack replies must eventually send are already
pinned in the design under "What a DM does."

---

## Pointers

| Thing | Where |
| --- | --- |
| v2 design | `plans/bridge-v2-router-connector.md` |
| Current laptop bridge | `bin/lantern-bridge`, `bridge.sh`, `docs/slack-trial.md` |
| Lantern prompt / skill | `prompt.md` (plugin id `aigora.lantern`) |
| How people install Lantern today | README, `herdr plugin install aigorahub/herdr-lantern` |
| Laptop Slack PR, do not land | https://github.com/aigorahub/herdr-lantern/pull/12 |
| Durable Windows / plugin lessons | `learnings.md` |
