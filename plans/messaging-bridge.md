# Plan: Lantern Bridge — Telegram, WhatsApp, and Slack

## Mission

Let a person use their lantern from a phone or a chat app. A new bridge daemon
(`bin/lantern-bridge`) receives messages from Telegram, WhatsApp, and Slack,
relays each one to the same helper CLI the lantern pane runs (headless, with
the same prompt and the same `bin/herdr` mutate gate), and sends the helper's
reply back to the channel. Done means: with a `bridge.conf` filled in, a
message sent from any of the three apps produces a lantern answer in that app,
and the existing pane-based lantern is unchanged.

Also in this run: fix the one outstanding repo defect — `tests/smoke.sh` argv
cases fail on machines that have a real `grok` or `agent` in
`/opt/homebrew/bin` or `/usr/local/bin`, because `launch.sh` prepends those
dirs ahead of the test's stub dir.

## Scope

### In Scope
- `tests/smoke.sh` isolation fix (stub dir must beat machine bin dirs).
- New `bin/lantern-bridge` (Python 3, stdlib only — same constraint as
  `bin/goals-floor` and `bin/elves-floor`).
- New `bridge.sh` entrypoint (POSIX sh, mirrors `launch.sh` env handling).
- New `bridge.conf.example`, seeded to the plugin config dir on first start.
- Telegram adapter (Bot API `getUpdates` long-poll + `sendMessage`).
- Slack adapter (Web API `conversations.history` poll + `chat.postMessage`).
- WhatsApp adapter (Cloud API webhook receiver + `/messages` send).
- Plugin wiring: a `bridge` pane and a `bridge` action in
  `herdr-plugin.toml`; version bump to 0.5.0.
- Tests wired into `tests/smoke.sh` (plus a Python unittest file the smoke
  suite invokes when a working Python 3 exists).
- Docs: README section, CHANGELOG entry, one-line mentions in `howto.html`
  and `docs/index.html`.

### Out of Scope
- No change to the interactive lantern pane behaviour (`launch.sh`,
  `open.sh`, `prompt.md` semantics stay as they are; the bridge gets its own
  prompt appendix).
- No third-party Python packages, no npm, no compiled code.
- No Slack Socket Mode (needs websockets; stdlib cannot), no Slack Events
  API server.
- No Twilio; WhatsApp is the Meta Cloud API only.
- No media handling (text messages only, both directions).
- No multi-tenant auth beyond per-channel sender allowlists.
- No changes to `bin/goals-floor` / `bin/elves-floor`.

## Design decisions (pinned; do not re-litigate)

**How the bridge talks to the lantern.** Not by scraping the pane. Each
conversation gets its own workdir under the plugin state dir
(`state/bridge/<channel>/<chat-id>/workdir`), seeded with the same lantern
prompt plus a remote appendix, and the helper CLI runs headless there:

- `claude`: `claude -p <text> --output-format text`, plus `--continue` when
  the workdir has a prior session, plus `--model` / `--effort` when set.
  Allow the tools the lantern needs:
  `--allowed-tools "Bash,Read,Glob,Grep,LS"`.
- `codex`: `codex exec <text> --skip-git-repo-check` first turn,
  `codex exec resume --last <text> --skip-git-repo-check` after.

Only `claude` and `codex` are supported bridge helpers (they have real
headless modes). `BRIDGE_HELPER` empty picks the first of those on PATH. Any
other value dies with a clear message naming the two supported values.

**The mutate gate survives.** The bridge prepends `$plugin_root/bin` to PATH
and exports `HERDR_BIN_PATH` exactly like `launch.sh`, so the headless helper
hits the same wrapper: inspect passes, mutation is blocked until the helper
reruns with `HERDR_HELPER_OK=1` after the user confirms — the confirmation
now happens as a chat message in the channel. The remote appendix explains
this to the helper.

**Config.** `bridge.conf` in the plugin config dir, `KEY=value` lines parsed
with the same restrictions as `helper.conf` (never sourced; unknown keys
fail; quotes stripped; values containing `` $ ` ; | & < > ( ) { } `` fail).
Every key falls back to an environment variable of the same name when the
conf line is empty or absent. Keys:

```
BRIDGE_HELPER=              # claude | codex; empty = first on PATH
BRIDGE_MODEL=               # optional --model
BRIDGE_EFFORT=              # optional effort (claude --effort; codex config)
BRIDGE_CWD=~                # search root mentioned to the helper
BRIDGE_SPAWN_KIND=claude    # default --kind for herdr agent start
BRIDGE_EXTRA_ARGS=          # extra unquoted helper CLI tokens

TELEGRAM_BOT_TOKEN=
TELEGRAM_ALLOWED_CHATS=     # comma-separated numeric chat ids

SLACK_BOT_TOKEN=            # xoxb- token
SLACK_CHANNEL=              # one channel id the bridge watches
SLACK_ALLOWED_USERS=        # comma-separated member ids (U…)

WHATSAPP_ACCESS_TOKEN=
WHATSAPP_PHONE_NUMBER_ID=
WHATSAPP_VERIFY_TOKEN=      # webhook GET verification
WHATSAPP_APP_SECRET=        # required; X-Hub-Signature-256 check
WHATSAPP_ALLOWED_NUMBERS=   # comma-separated E.164, digits only ok
WHATSAPP_WEBHOOK_HOST=127.0.0.1
WHATSAPP_WEBHOOK_PORT=8787
```

**A channel is enabled by its credentials and armed by its allowlist.** A
channel with credentials but an empty allowlist refuses to start, with a
message saying which key to set. No channels configured at all: die with a
pointer to the example file. Tokens are never printed; log lines show
channel, chat id, and byte counts, not message bodies or secrets.

**Adapters.**
- Telegram: `getUpdates` long-poll (`timeout=50`, offset cursor). Replies via
  `sendMessage` with plain text (no parse_mode), split at 4096 chars.
- Slack: poll `conversations.history` for `SLACK_CHANNEL` every 3 seconds
  with an `oldest` cursor initialised to now; ignore messages with a
  `bot_id` or with `subtype`; ignore senders not in the allowlist. Reply via
  `chat.postMessage`, split at 4000 chars.
- WhatsApp: `http.server` on `WHATSAPP_WEBHOOK_HOST:PORT`. GET echoes
  `hub.challenge` when `hub.verify_token` matches. POST verifies
  `X-Hub-Signature-256` as HMAC-SHA256 of the raw body with
  `WHATSAPP_APP_SECRET` (reject 401 otherwise — the check is mandatory, not
  optional), extracts text messages, replies via
  `POST /v20.0/<phone_number_id>/messages`, split at 4096 chars. The README
  documents that Meta needs a public HTTPS URL and shows a `cloudflared`
  one-liner; the bridge itself binds localhost.

**Concurrency.** One thread per adapter pushes `(channel, chat_id, text,
reply)` onto a single `queue.Queue`; the main thread pops and runs the helper
synchronously. Global serialization is fine for v1. `urllib.request` with
explicit timeouts everywhere; adapter loops catch, log one line, back off 5s,
and continue — a network blip never kills the daemon.

**Entry.** `bridge.sh` mirrors `launch.sh`: resolve plugin root, source
`lib.sh`, normalise paths, extend PATH, install the wrapper as
`HERDR_BIN_PATH`, seed `bridge.conf` and `prompt.md`, then exec the detected
Python on `bin/lantern-bridge`. It must run both as a Herdr pane (env
injected) and from a terminal (fall back to
`herdr plugin config-dir aigora.lantern`). New manifest entries:
`[[panes]] id = "bridge", title = "Lantern Bridge", placement = "tab",
command = ["sh", "bridge.sh"]` and `[[actions]] id = "bridge"` opening it.

## Batches

### Batch 1: Smoke test isolation

The argv harness's stubs must beat machine CLIs. Put the stub bin dir at
`$argv_home/.local/bin` (launch.sh prepends `$HOME/.local/bin` after the
Homebrew dirs, so it wins), point `HERDR_BIN_PATH` there, and keep everything
else as is.

**Acceptance criteria**
- [ ] B1-A1: `sh tests/smoke.sh` passes on a machine that has a real `grok`
  and `agent` in `/opt/homebrew/bin` (this machine).
- [ ] B1-A2: No behaviour change to `launch.sh`/`lib.sh` in this batch —
  test-only diff.

### Batch 2: Bridge core

`bin/lantern-bridge` skeleton: conf parse (+ env fallback), helper
detection/argv build for claude and codex, per-conversation workdir seeding
(prompt + remote appendix + AGENTS.md/CLAUDE.md), reply splitting, redacting
log helper, `--check` mode that validates config and prints a redacted
summary plus the helper argv for a dummy message without any network. Plus
`bridge.sh` and `bridge.conf.example`. Plus `tests/bridge_test.py` (stdlib
unittest, loads the extensionless script via importlib) covering conf parse
rejects/accepts, env fallback, allowlist parsing, argv build for both
helpers, message splitting, and workdir seeding; smoke.sh runs it when a
working Python 3 exists and also `py_compile`s `bin/lantern-bridge` and
`sh -n`s `bridge.sh`.

**Acceptance criteria**
- [ ] B2-A1: `sh bridge.sh` with no channels configured exits nonzero with a
  message naming `bridge.conf` and the example file.
- [ ] B2-A2: `bin/lantern-bridge --check` with a Telegram token + allowlist
  set prints a redacted config summary (no token bytes) and a claude/codex
  argv, exit 0.
- [ ] B2-A3: Unit tests cover conf parsing (unsafe value rejected, unknown
  key rejected, env fallback), argv for both helpers, and splitting; all
  green via `sh tests/smoke.sh`.
- [ ] B2-A4: Workdir seeding writes the lantern prompt + remote appendix to
  `AGENTS.md` and `CLAUDE.md` in the conversation workdir, exactly once.

### Batch 3: Telegram adapter

Long-poll loop, offset persistence in memory, allowlist filter, reply with
splitting, 5s backoff on errors. Unit tests for update parsing (message,
edited message ignored, non-text ignored, disallowed chat dropped) and URL
construction using canned JSON — no network in tests.

**Acceptance criteria**
- [ ] B3-A1: Parsing tests green for text message, non-text, disallowed
  sender, and offset advance.
- [ ] B3-A2: Outbound send builds the right URL and payload; messages over
  4096 chars split on line boundaries where possible.

### Batch 4: Slack adapter

Poll loop with `oldest` cursor, skip `bot_id`/`subtype` messages, allowlist
filter, `chat.postMessage` replies. Unit tests with canned
`conversations.history` JSON: own-bot skip, subtype skip, disallowed user
skip, cursor advance, payload build.

**Acceptance criteria**
- [ ] B4-A1: Parsing tests green for the four skip/accept cases and cursor
  advance.
- [ ] B4-A2: Replies are split at 4000 chars and posted to `SLACK_CHANNEL`.

### Batch 5: WhatsApp adapter

Webhook handler (GET verification, POST with mandatory HMAC check), text
message extraction, send via Cloud API. Unit tests: GET verify success and
mismatch, POST with a valid signature parses a text message, POST with a bad
signature is rejected and nothing reaches the queue, POST from a disallowed
number is dropped, send payload build.

**Acceptance criteria**
- [ ] B5-A1: A POST whose `X-Hub-Signature-256` does not verify returns 401
  and enqueues nothing (unit-tested with a live `http.server` on an
  ephemeral port).
- [ ] B5-A2: Starting the WhatsApp channel without `WHATSAPP_APP_SECRET` or
  without an allowlist refuses with a clear message.
- [ ] B5-A3: Extraction/verification/send-payload tests green.

### Batch 6: Plugin wiring and docs

Manifest pane + action, version 0.5.0 in `herdr-plugin.toml` and README
header, README "Use the lantern from Telegram, WhatsApp, or Slack" section
(setup per channel: BotFather, Slack app scopes `channels:history`,
`chat:write` + invite the bot, Meta app + webhook + cloudflared note),
CHANGELOG 0.5.0 entry, one-paragraph mentions in `howto.html` and
`docs/index.html`.

**Acceptance criteria**
- [ ] B6-A1: `herdr-plugin.toml` declares the bridge pane and action;
  version is 0.5.0 in the manifest, README, and CHANGELOG.
- [ ] B6-A2: README documents all config keys shown in
  `bridge.conf.example`, and the two files agree.
- [ ] B6-A3: `sh tests/smoke.sh` fully green.

### Batch 7: Repo review fixes

Five findings from the repo review, each verified against the code. Scope is
these fixes only: `bin/elves-floor` (explicit `--root` must be the whole
search; a session file at exactly `--max-depth` must be read; drop the
vestigial `kind\t` prefix `row()` added and the consumer immediately split
off), `bin/goals-floor` (delete the dead `blob` assignment in
`extract_signals`), and the two released pages `howto.html` and
`docs/index.html`, still at v0.3.0 and still claiming macOS or Linux only
after 0.4.0 shipped Windows. Two regression tests join `tests/smoke.sh` beside
the existing `elves-floor` call.

**Acceptance criteria**
- [x] B7-A1: `elves-floor --root <dir>` scans only the roots it was given, and
  `tests/smoke.sh` asserts `elves_detected 0` for a fresh empty root.
- [x] B7-A2: A session file in a directory at exactly `--max-depth` is
  reported, and `tests/smoke.sh` asserts `elves_detected 1` for one at depth 2
  under `--max-depth 2`.
- [x] B7-A3: The dead `blob` assignment and the `kind\t` row prefix are gone;
  `py_compile` of both floor scripts is green.
- [x] B7-A4: `howto.html` and `docs/index.html` read v0.5.0 and describe the
  platforms as macOS, Linux, or Windows, with the Git for Windows note.

### Batch 8: Bridge hardening

Four findings from a line-by-line review of the bridge. The one that matters:
nothing stopped a second bridge daemon, and two daemons on one config break
every channel — two `getUpdates` long polls on one bot token give 409 and fight
over the offset cursor, two Slack pollers both answer, and the second WhatsApp
adapter cannot bind its port and retries forever. The daemon now takes an
advisory lock (`flock`, `msvcrt.locking` on Windows, a logged line and no guard
where neither exists) at `<state_dir>/bridge/daemon.lock` before any adapter
thread starts, and `bridge.sh --open` remembers its pane id and focuses a live
bridge instead of seating a second one. Also: the dispatch loop catches, so a
disk-full in `seed_workdir` cannot kill the daemon threads with it; a chat id
that is only dots becomes `unknown`; and an empty `WHATSAPP_VERIFY_TOKEN` is
refused in the constructor, because `compare_digest("", "")` is a match.

**Acceptance criteria**
- [x] B8-A1: A second daemon on one state dir is refused with the lock path in
  the message, a lock file left by a killed daemon does not block the next
  start, and a second `bridge.sh --open` focuses the running pane instead of
  opening another.
- [x] B8-A2: An exception from `seed_workdir` or `run_helper` is logged with
  the channel, the chat id, and the exception class, the user is told in
  channel, and the loop answers the next message.
- [x] B8-A3: Chat ids `..`, `.`, and `""` all resolve inside the state
  directory.
- [x] B8-A4: `WhatsAppAdapter` refuses an empty `WHATSAPP_VERIFY_TOKEN`.

## Master Acceptance

- [ ] M-A1: Full smoke suite green locally (macOS with real Homebrew CLIs
  present) — i.e. the Batch 1 defect is gone and nothing regressed.
- [ ] M-A2: The bridge never logs or echoes a token, secret, or message
  body at default verbosity.
- [ ] M-A3: Every enabled channel requires a non-empty sender allowlist to
  start.
- [ ] M-A4: The interactive lantern (launch.sh/open.sh) is byte-identical
  except where Batch 1 touches tests.
- [ ] M-A5: No new runtime dependencies beyond POSIX sh and Python 3 stdlib.

## Risks and cautions

- Headless CLI flags drift between versions. Keep argv construction in one
  function per helper, unit-tested, with `BRIDGE_EXTRA_ARGS` as the escape
  hatch.
- Slack polling can double-deliver on cursor edge; dedupe on message `ts`
  per channel.
- WhatsApp signature check must read the raw request body bytes before any
  parse; compare with `hmac.compare_digest`.
- The conf parser must never source the file; reuse the exact metacharacter
  reject set from `lib.sh`.
- Do not weaken or skip existing smoke cases to get green.

## Review focus

Security of the webhook path (signature before parse, 401 on mismatch,
localhost bind), allowlist enforcement on every inbound path, secret
redaction, and the smoke-test isolation fix not masking real regressions.
