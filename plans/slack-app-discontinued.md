# Why Aigora stopped building the Lantern Slack app

**Status: discontinued for Aigora on 2026-08-20.** The laptop bridge on `main`
still works and is still supported. The v2 router design is not wrong; it is
conditional, and the condition does not hold for us. This file says which
condition, so nobody has to re-derive it.

![The Lantern Slack app icon](../assets/lantern-slack-icon.png)

## What we were trying to build

Chat access to your herd. Message Lantern from Slack, WhatsApp, or Telegram,
and ask what your agents are doing, or tell one of them to do something. For a
whole team, at any hour, from a phone.

We shipped the laptop version of it: v0.5.0 added the bridge, v0.5.1 closed a
mutate-gate hole in it, and we ran a real Slack trial with DMs, channel
threads, and a relay into another agent's pane.

## The finding that decided it

**A herd is panes on a machine.** Lantern illuminates what your agents are
doing, and that exists only where the agents run. So chat access to a herd
requires that machine to be running. There is no arrangement that avoids it.

Hosting the bridge somewhere else does not help. Lift `bin/lantern-bridge`
onto Railway or Fly and it starts a helper in an empty container with nobody's
panes in it. You have moved the dependency rather than removed it.

So an always-available team service needs an always-available machine, and
there are only three ways to get one:

**Keep laptops awake.** Fine for a desktop, useless for a laptop that travels.

**A box per person.** Works, and Herdr supports it directly: run the server on
the box and attach from the laptop with `herdr --remote`. It costs a machine
per person and moves everyone's repositories onto it.

**One shared box.** We had a Mac mini available. One macOS account per person
with one Herdr server each would even have preserved isolation.

**SOC 2 closed all three.** Machines have to be allowed to sleep, which rules
out the first. The shared box puts several people's work and credentials on
one machine whose ownership is ambiguous, which is among the first things an
auditor pushes on. A box per person was rejected on cost and provisioning
before compliance was raised.

## The second finding, which matters more to anyone reading this

**The bridge's security model is right for one person and untenable for a
team, and that is not a bug.** The README has said so from the beginning: an
allowlisted sender gets a shell on the machine running the bridge, as the user
running it. The `bin/herdr` wrapper gates mutating `herdr` subcommands and
nothing else. It is not a sandbox and was never presented as one.

For one person on their own machine that is a reasonable trade: you are
allowlisting yourself. For a team it means an allowlisted Slack member can run
commands on a colleague's laptop, as that colleague, with no per-action audit
trail. That does not survive an audit, and it should not.

The v2 design (see
[`bridge-v2-router-connector.md`](bridge-v2-router-connector.md)) fixes the
sharing half of that with per-person enrollment, so a message only ever
reaches the machine its sender enrolled. It does not fix the sleeping half,
and it does not add an audit trail.

## What we concluded

For a team that needs always-available chat access under compliance
constraints, the answer is not a Herdr plugin at all. It is a hosted service
that acts on your repositories, with identity from your existing SSO, scoped
repository credentials rather than a person's own token, and one audit log.

That is a different product, and it cannot show you your herd, because your
herd is on laptops that are allowed to sleep. It can answer questions about
your code and change it, which is most of what a phone is useful for.

If you go that way, compare buying against building first. A service you
operate that reads and writes your repositories enters your own audit scope,
which is ongoing work: change management, access reviews, log retention, and
vendor review of the model provider. Several hosted coding agents already
publish SOC 2 reports, which moves most of that to vendor management.

## Practical findings from the trial

These cost us time and are not written down anywhere else.

**Slack will not deliver DMs to an app until its Messages tab is on.** App
Home, Show Tabs, Messages Tab, plus the checkbox under it. It is a setting
rather than a scope, so it needs no reinstall, and nothing reaches the bridge
to log when it is off. Scope changes (`im:history`, `im:write`) do need a
reinstall.

**The bridge answers every allowlisted message in the watched channel**, not
only `@Lantern` mentions. In a channel with people in it, that is noisy.
Mention-only in channels with full conversation in DMs is the shape you
probably want, and it is not implemented.

**Latency has three parts,** and only one is the model: the poll interval
(three seconds, multiplied by the number of watched conversations), a cold
helper start on every single turn, and, if the lantern relays to another
agent, that agent's whole turn on top. Our recipe test came back in a bit over
a minute, of which 37 seconds was the target agent.

**The Slack cursor lives in memory.** Stop and start the bridge and everything
sent while it was down is skipped, with no log line. Sleeping is fine, because
the process is frozen with its cursor intact; restarting is not.

**Sleep behaves differently on each channel.** Slack recovers on wake, because
the cursor asks for everything newer. Telegram recovers properly, because
updates sit on Telegram's servers until an offset acknowledges them. WhatsApp
mostly loses them, because Meta retries for a limited window, and may deliver
twice if you wake inside it.

**Telling the helper to be brief will flatten what it relays.** We asked for
short replies and got a numbered recipe compressed into paragraphs with the
numbering run inline. Brevity is a rule about conversation; content with a
shape needs a separate rule saying to keep it.

## How to proceed, depending on who you are

**One person, one machine.** Use it as it stands. `main` at v0.5.1 works.
[PR #12](https://github.com/aigorahub/herdr-lantern/pull/12) adds channel
threading, DM conversations, autostart, and the fixes from a pre-merge review;
land it if you want them.

**A team whose machines can stay awake.** The v2 router and connector design
is the path, and it is sound. Answer the four product questions in it first;
they are product calls, not engineering ones. Budget about $6 a month for the
router.

**A team under compliance constraints like ours.** Do not build this. The herd
view is not obtainable from a service, and a laptop-bound agent with a chat
door will not pass an audit. Buy or build a hosted agent against your
repositories instead, and keep Lantern for the local herd view it is good at.

## Where things are

| Thing | Where |
| --- | --- |
| Laptop bridge | `bin/lantern-bridge`, `bridge.sh`, README |
| Slack setup walkthrough | `docs/slack-trial.md` |
| v2 design, conditional | `plans/bridge-v2-router-connector.md` |
| v2 session record | `plans/bridge-v2-handoff.md` |
| Open bridge defects | issues #8, #9, #10 |
| Slack app icon | `assets/lantern-slack-icon.png` |
