# Changelog

All notable changes to Lantern, by Elves are documented here.

## [0.7.0] - 2026-08-21

### Added

- The lantern checks for a newer published version at light-up and offers
  the update. `launch.sh` writes one line to `update.txt` in the workdir —
  update available, up to date, or why it could not tell — from one fetch
  of the published manifest with a few seconds' budget, so an offline
  machine loses a few seconds and nothing else. The offer is a question,
  never a silent upgrade: there is no `herdr plugin update`, a GitHub
  install refreshes with `herdr plugin install aigorahub/herdr-lantern`,
  and that command sits behind the mutate gate like every other. A linked
  checkout is told it is behind and never reinstalled over — reinstalling
  would orphan the link, and the checkout may hold work in progress.
  Versions compare numerically per field, so 0.10.0 beats 0.9.9 and a dev
  checkout ahead of `main` is not offered a downgrade.

### Changed

- Agents the lantern seats start in the smart-auto permission tier rather
  than each kind's bare default. `agent start` passes the kind's own flags
  after `--`: Claude Code and Grok `--permission-mode auto`, Cursor `agent`
  `--auto-review --trust`, Codex `-a never -s workspace-write`; a kind
  without a listed tier gets no extra args. The flags that skip approvals
  altogether — bypassPermissions, `--yolo`, `--force`, `--always-approve`,
  `--dangerously-bypass-approvals-and-sandbox` — are named as never passed.
  The rule lives in `prompt.md`, the pane appendix, and the bridge
  appendix, whose version marker is bumped so existing conversations
  re-seed.
- The bridge answers a Slack channel only when the message mentions it. A
  channel has other people in it, and answering everything an allowlisted
  person says there is noise; a DM is still the whole conversation and needs
  no mention. The bot's own user id comes from `auth.test`, which needs no
  extra scope. Until that answers, the channel behaves as it did before and
  the log says why, because a bot that has gone silent is harder to diagnose
  than one that is too eager.
- Conversation workdirs are re-seeded when the appendix changes. The helper's
  instruction files were written once and never again, so a rule corrected in
  a later build reached only conversations created after it: the formatting
  note below would have gone nowhere near the conversations already getting
  formatting wrong. The files now carry a version marker, and a file whose
  marker is missing or older is rewritten. An edit that keeps the marker is
  kept, and prompt.md in the config directory is never touched.
- The helper is told each channel's formatting dialect. Ordinary Markdown is
  wrong on all three and differently so: Slack reads `*one asterisk*`,
  Telegram is sent with no parse mode at all, and WhatsApp uses single
  markers. An agent writing `**bold**` was putting literal asterisks in front
  of the reader.

## [0.6.0] - 2026-08-20

### Fixed

These came out of a pre-merge review of the Slack work below, and every one of
them was introduced by it.

- The dedupe set lost a whole conversation at a time. It changed from holding
  timestamps to holding `(conversation, timestamp)` pairs, but the eviction
  still sorted the pairs, which sorts by conversation id first. Channel ids
  start with `C` and DM ids with `D`, so a busy DM evicted every channel entry
  before touching any DM entry, newest included. That newest entry is the
  cursor boundary the set exists to protect, so the next poll could answer the
  same channel message twice. Eviction now sorts on the timestamp.
- Splitting a long reply ate the shape it was sent with. The chunk boundary
  did `rstrip()` and `lstrip("\n ")`, so a blank line between steps vanished
  and an indented continuation line arrived flush left. Exactly one separator
  character is now removed, which is what the new formatting instruction
  promises the helper.
- A DM that opened but could not be read took the channel down with it, once
  per poll, forever. That is the shape of `im:write` granted without
  `im:history`. The failure raised out of the poll, and the retry loop logged
  only the exception class, discarding the error slug that names the cause.
  Such a DM is now dropped with the slug and the missing scope named, and the
  channel keeps working.
- A network blip while the daemon started turned DMs off for the life of the
  process and reported it as a missing scope. Opening is now retried on a slow
  timer until at least one DM opens.
- The threaded footer told people to DM the bot based on whether anyone had a
  DM open, not whether they did. Someone whose own DM failed to open was sent
  somewhere nothing is read. The footer now follows the person it is answering.
- The autostart hook read `BRIDGE_AUTOSTART` anchored at column 0 and matched
  only lowercase, while the daemon's parser strips the line first. An indented
  key was therefore valid config that the hook could not see, and the symptom
  was a key that looks set, no bridge, and nothing logged. The hook now matches
  the daemon, accepts any case, and says so when the value is set to something
  it does not understand.

### Added

- Slack DMs. The bridge now opens and polls each allowlisted member's DM with
  the bot, besides the one watched channel. Needs the `im:history` and
  `im:write` scopes and an app reinstall; without them the bridge logs which
  scope is missing and runs channel-only, as before. It also needs the app's
  Messages tab switched on under App Home, which is a setting rather than a
  scope: until it is, Slack refuses to let anyone send the app a DM at all.
  Both the README and the Slack trial guide say so, and the trial guide's
  troubleshooting table names the symptom.
- `BRIDGE_AUTOSTART`. Set it to `1` in `bridge.conf` and a `[[startup]]` hook
  has the Herdr server seat the bridge pane itself, so no terminal stays open
  for the bridge. The hook reads the config file only, exits quietly when the
  key is off or the file does not exist, and never seeds anything.

### Changed

- Slack replies follow the message and the session follows the person. A
  channel message is answered in its own thread, so the channel stays
  readable; a DM is answered in the DM; and both share one conversation per
  allowlisted member, so a question asked in the channel continues in the DM
  without starting over. Sessions used to be keyed by the channel, so an
  existing conversation starts fresh once on upgrade.
- Threaded answers carry one line saying the bridge cannot read the thread.
  Slack's history API does not return thread replies, so a reply typed into
  the thread reaches nobody; the line says where to continue instead.
- Slack polling is round-robin, one conversation per pass, so watching the
  DMs costs the same request rate as watching the channel alone.
- The remote appendix separates brevity from formatting. A conversational
  answer is two or three short sentences, but anything with a shape, a list,
  steps, a recipe, or output relayed from another agent, is passed through
  with its own line breaks and numbering. The first version of this said only
  "keep replies short" with "no headings, no tables", and a relayed recipe
  came back as paragraphs with the numbering run inline, losing the shape the
  user had asked to see. Length is explicitly not a reason to condense: line
  breaks reach the chat app unchanged and long replies are split at one.

## [0.5.1] - 2026-08-19

### Fixed

- `bridge.sh` used `helper_prepend_path` for the mutate-gate wrapper after
  `helper_extend_user_path` had already put Homebrew in front. That is the
  same PATH hole `launch.sh` closed in 0.5.0: on a machine whose PATH already
  carried the plugin's `bin`, a bare `herdr` from an allowlisted chat sender
  reached the real binary and nobody was asked. The bridge now force-fronts
  the wrapper the same way the lantern pane does.
- The remote appendix still handed the helper a ready-to-run
  `HERDR_HELPER_OK=1 herdr …` line. `prompt.md` had that pattern removed in
  0.5.0 so an agent would not paste the prefix without asking; the bridge
  reintroduced it for the chat path. It now describes the prefix the same way
  `launch.sh` does, without a command to copy.
- Exported channel tokens reached the headless helper. The README tells
  people to keep secrets in the environment, `subprocess.run` inherited
  that environment, and the helper has Bash, so a prompt-injected turn
  could `printenv` the WhatsApp app secret that gates the public webhook.
  The child now gets a copy of the environment with those keys removed.
  File-sourced secrets remain readable to the same account.

### Changed

- The published pages (`docs/index.html`, `howto.html`) now walk through
  opening the bridge, filling `bridge.conf`, `--check`, and the Slack-first
  trial, instead of one paragraph that pointed at the README. Version strings
  on those pages, the README, and the manifest are 0.5.1.
- "What a sender gets" named Claude's `--allowed-tools` list as if Codex got
  it too. Codex is started as `codex exec` with no extra permission flags.

## [0.5.0] - 2026-08-19

### Added

- The Lantern Bridge: use the lantern from Telegram, WhatsApp, or Slack. A new
  `bridge` pane and a `bridge` action run `bridge.sh`, which sets up the same
  environment the lantern pane gets and starts `bin/lantern-bridge`. Each
  conversation gets its own workdir seeded with `prompt.md` plus a remote
  appendix, and a headless `claude` or `codex` answers there. Nothing scrapes
  the lantern pane.
- The mutate gate reaches the chat apps. The bridge exports the same
  `HERDR_BIN_PATH` wrapper, so create, start, focus, close, and prompt stay
  blocked until you confirm — and the confirmation is a message in the
  channel, because there is no terminal at the other end.
- `bridge.conf`, seeded from `bridge.conf.example` on first start and parsed
  the same way `helper.conf` is: `KEY=value` lines only, never sourced,
  unknown keys and shell metacharacters refused. Every key falls back to an
  environment variable of the same name, so tokens can stay out of the file.
- `sh bridge.sh --check` validates a config and prints a summary with every
  token redacted, plus the helper command line it would run. No network, no
  helper process.
- A sender allowlist per channel, and it is mandatory. A channel with
  credentials and an empty allowlist refuses to start and names the key to
  fill in. With no channels configured at all the bridge exits and points at
  the example file.
- `tests/bridge_test.py`, run by `tests/smoke.sh` wherever a working Python 3
  exists. It covers config parsing, allowlists, argv for both helpers, reply
  splitting, workdir seeding, the Telegram and Slack message filters, and the
  WhatsApp webhook driven against a real socket on an ephemeral port.

### Security

- Every WhatsApp webhook POST is verified as HMAC-SHA256 of the raw request
  body against `WHATSAPP_APP_SECRET`, with `hmac.compare_digest`, before
  anything parses it. A signature that is missing, malformed, or wrong is a
  401 and nothing reaches the queue. The app secret is required, not optional.
  The webhook binds localhost; a tunnel is what gives Meta a public URL.
- The webhook's default request logger is off. The line it writes carries
  `hub.verify_token`.
- No token or secret is logged or echoed, and no log line carries message
  text: a line is a channel, a chat id, and byte counts. Message text is not
  logged, but it is an argument to the helper process while the turn runs, so
  any other account on this machine can read it out of `ps auxww` for up to
  `HELPER_TIMEOUT` (900s). That is accepted rather than fixed: the bridge is
  built for a single-user host, where the same account could read the
  conversation workdir anyway. On a shared machine, treat every message as
  visible to everyone logged in.
- **An allowlisted sender gets a shell.** The helper runs with
  `--allowed-tools "Bash,Read,Glob,Grep,LS"` and no sandbox, as the user who
  started the bridge. The `bin/herdr` gate covers mutating `herdr` subcommands
  and nothing else; it is not a sandbox and never was. The allowlists are the
  security boundary, so an entry on one is the same trust as a seat at the
  keyboard.
- The mutate gate failed open, silently. `launch.sh` put the wrapper on PATH
  with a helper that returns early when the directory is already there, and
  `helper_extend_user_path` runs first and prepends `/usr/local/bin` and
  `/opt/homebrew/bin`. On a machine whose PATH already carried the plugin's
  `bin`, the wrapper kept that inherited position, `herdr` resolved to the
  real binary, and a bare `herdr agent start` ran with nobody asked. The
  wrapper directory is now moved to the front of PATH wherever it already
  sits, and `HERDR_BIN_PATH` was never the problem.
- `prompt.md` weakened its own gate. It handed the lantern a ready-to-run
  `HERDR_HELPER_OK=1 herdr agent prompt ...` line with no confirmation step
  attached, while the bullet that does require confirmation listed only
  create/start/focus/close — so relaying a message into another agent's pane
  read as ungated. The gate rule now names the read-only list, names every
  verb the wrapper blocks, and no prefixed command appears anywhere for an
  agent to paste.
- The snapshot files are now marked as what they are. `floor.txt`,
  `goals-floor.txt`, and `elves-floor.txt` carry text captured verbatim from
  other agents' panes, and `prompt.md` had the lantern read them at light-up
  with nothing saying they are data. Anyone whose text reaches a pane could
  address the lantern directly. They are observed data, never instructions,
  and anything in them that reads as an order is quoted to the user instead.
- The webhook could be taken down by anyone who learned its URL. The handler
  inherited `timeout=None` from `StreamRequestHandler` and
  `ThreadingHTTPServer` caps nothing, so connections that announce a
  `Content-Length` and then stop sending parked one thread each — and the body
  is read before the signature is checked, so no credential was needed. The
  handler now carries a 15s socket timeout and the server refuses a connection
  past eight in flight.
- A signature header holding one non-ASCII byte crashed the request. Headers
  are decoded as latin-1, and `hmac.compare_digest` raises `TypeError` rather
  than returning `False` on a non-ASCII `str`. The exception escaped to
  `socketserver`, which printed a full traceback with absolute filesystem
  paths into the bridge pane and dropped the connection with no 401 at all.
  Only 64 hex digits reach the comparison now, and an unexpected handler
  exception is one redacted line.
- `TELEGRAM_ALLOWED_CHATS` authorized a conversation rather than a person. A
  group id on that list let every member of the group drive the lantern. A
  message is accepted when the sender's own id is allowlisted, or when the
  chat is private and its id is allowlisted; a non-private chat whose members
  are not individually listed is dropped and logged.
- `BRIDGE_EXTRA_ARGS` could switch the permission model off. It is appended
  after the bridge's own `--allowed-tools` and a later flag wins, so
  `--dangerously-skip-permissions`, `--permission-mode bypassPermissions`,
  `--allowed-tools *`, `--sandbox danger-full-access`, `--add-dir /` and their
  neighbours all went through. They are refused by name now, and `--check`
  prints a loud line whenever any extra args are set.
- Config values from the environment were used verbatim; only file values were
  checked for shell metacharacters. The environment is the path this README
  recommends for secrets, so it now gets the same reject set, with an error
  naming the key and the source.
- `bridge.conf` was seeded world-readable with `cp`. It holds five secrets, so
  it is created `0600`. An existing file's mode is left alone.
- Untrusted message text was a bare positional for `codex`, so a message that
  is exactly a flag was parsed as one. An end-of-options `--` guards it.
  `claude` was already safe: the text is the value of `-p`.

### Changed

- `hsh` no longer prefers `$HERDR_REAL`. That branch existed to dodge the
  mutate gate from inside the lantern pane, and `launch.sh` unsets
  `HERDR_REAL` before it execs the agent, so it never ran there. Reviving it
  would be a hole in the gate rather than a fix: opening a second lantern is a
  change like any other. Outside the pane `hsh` reaches the real herdr as
  before; inside it, the wrapper asks first.

### Fixed

- A snapshot the lantern could not refresh was left showing the last run's
  field. With no Python 3 on PATH `launch.sh` skipped the refresh entirely,
  and the workdir survives between runs, so `goals-floor.txt` and
  `elves-floor.txt` still said who needed the user and which agents were
  blocked — hours old, and read at light-up as the field right now. A file
  that cannot be refreshed now holds one line saying so and why, and the same
  goes for `floor.txt` when the real herdr cannot be found.
- One non-UTF-8 byte in one `.elves-session.json` ended the whole Elves scan.
  `load_session` caught `OSError` and `JSONDecodeError`, and `read_text`
  raises `UnicodeDecodeError` before either can happen. An unreadable file is
  skipped like any other and the rest of the floor is still reported.
- A relayed message could be reported as sent when it never was. The Enter
  fallback fired after any `agent prompt` failure and the relay then reported
  success on the strength of a wait that returns at once for an idle pane, so
  an unknown target or a rejected flag came back as delivered — and Enter was
  pressed into a session for a failure that had nothing to do with a stalled
  submit. Enter is now only for the stall it was written for, recognised by
  the message still showing in the pane, and a pane that has not moved
  afterwards is a failure rather than a guess.
- A helper that exited non-zero after printing anything still got a session
  marker, so every later turn passed `--continue` for a session that never
  began and the conversation was stuck there. The marker goes down only when
  the run succeeded.
- A reply could be lost whole. `split_message` could emit an empty chunk,
  every provider rejects an empty message, and the failed send took the entire
  reply with it behind one `send failed` line.
- The webhook's 413 answered without draining the body it refused, and on a
  keep-alive connection the undelivered bytes were then parsed as the next
  request. It closes the connection.
- `WHATSAPP_WEBHOOK_PORT` was checked with `isdigit()` alone, so `99999`
  passed validation and failed at bind — leaving that channel dead while the
  daemon reported itself healthy. It has to be 1-65535.
- Nothing stopped a second Lantern Bridge, and a second one breaks every
  channel: two `getUpdates` long polls on one bot token answer 409 and fight
  over the offset cursor, two Slack pollers both reply to every message, and
  the second WhatsApp adapter cannot bind its webhook port and retries in a
  loop. The daemon now takes an advisory lock on
  `<state dir>/bridge/daemon.lock` before any adapter starts and refuses to run
  while another holds it, and the `bridge` action focuses the bridge pane it
  already opened instead of seating a second one. The lock is advisory, so a
  daemon killed with `-9` leaves nothing to clean up by hand.
- One unexpected error while answering a message ended the whole bridge. The
  adapters run as daemon threads, so an exception reaching the dispatch loop
  took every channel down with it, silently. A failed turn is now one log line
  and a note in the channel, and the next message is answered.
- A chat id made only of dots would have named a directory beside the
  conversation state rather than one inside it. The allowlists never let one
  through, but the slug refuses it now as well.
- An empty `WHATSAPP_VERIFY_TOKEN` would have let any webhook GET pass
  verification, because `hmac.compare_digest` of two empty strings is a match.
  The adapter refuses to be constructed without one, the way it already did for
  the app secret and the allowlist.
- `tests/smoke.sh` failed its `launch.sh` argv cases on any machine with a
  real `grok` or `agent` in `/opt/homebrew/bin`. The harness handed its stub
  directory in through `PATH`, but `helper_prepend_path` skips a directory
  that is already on `PATH`, so the stubs kept their inherited position and
  the Homebrew directories landed in front of them. The stubs now sit in
  `$HOME/.local/bin` under the test's throwaway home and let
  `helper_extend_user_path` place the directory itself.
- `bin/elves-floor` ignored the scope of an explicit `--root`. It appended
  `~/aigora` to whatever roots the caller named, so a scoped scan reported
  sessions from outside its root and walked the whole tree on every run. The
  default roots are unchanged; explicit ones are now exact.
- `bin/elves-floor` skipped a `.elves-session.json` sitting in a directory at
  exactly `--max-depth`. The walk stopped before the filename check instead of
  after it, so the last reachable level was read as if it were empty.
- `howto.html` and `docs/index.html` were left at v0.3.0 and still said Herdr
  was needed on macOS or Linux, three releases after 0.4.0 shipped Windows
  support. Both pages now carry the current version and name Windows.

## [0.4.0] - 2026-08-19

### Added

- Windows support. The manifest declares `windows`, and the plugin runs the
  same POSIX shell files there through Git Bash. It needs Git for Windows with
  `C:\Program Files\Git\bin` on `PATH`, because Herdr starts the plugin with
  `sh open.sh` and `sh launch.sh`. There is no separate Windows code path and
  no behaviour change on macOS or Linux.
- `bin/herdr.cmd`, the Windows half of the mutate gate. See Fixed below.
- `.gitattributes`. The interpreted files are pinned to LF and batch files to
  CRLF, so a Windows checkout cannot turn `#!/bin/sh` into a shebang carrying
  a carriage return.
- A GitHub Actions job that runs `tests/smoke.sh` on Linux, macOS, and Windows
  for every pull request.
- README records the step people will otherwise miss: after putting Git on
  `PATH`, stop the Herdr server rather than only the window. The server is
  persistent and keeps the environment it started with, so the action fails
  with `program not found` until it is stopped and started again.

### Fixed

- The herdr mutate gate could be bypassed on Windows, and silently.
  `bin/herdr` has no file extension, so a native Windows process resolving
  `herdr` on `PATH` skipped it under `PATHEXT` and reached the real
  `herdr.exe`. Create, start, focus, close, and prompt then needed no
  confirmation. `bin/herdr.cmd` is what that process finds now. It forwards to
  the same wrapper, returns its exit code, and refuses rather than falling
  through when it can find no `sh.exe`.
- The field snapshot no longer trusts `python3` on `PATH`. Windows ships a
  zero-byte Microsoft Store alias by that name which satisfies a lookup and
  then opens the Store. Lantern now runs a candidate before using it, and
  falls back to `python` and `py -3`.
- `bin/goals-floor` decoded `herdr` output with the locale encoding, which is
  a code page on Windows. Pane text carries box drawing, arrows, and emoji, so
  one byte outside that page ended the snapshot. It now decodes UTF-8 with
  replacement.
- `open.sh` converts the workspace directory to the native form before handing
  it to `herdr`. Git Bash reports `$HOME` as `/c/Users/name`, and the Windows
  binary wants `C:\Users\name`.

### Changed

- `tests/smoke.sh` covers ground it never did. It runs `launch.sh` against stub
  binaries and checks the command line built for every helper CLI, so Cursor
  agent, Devin, Codex, and Grok are no longer untested; it exercises the `py`
  launcher fallback; and it pins what survives `cmd.exe` when a native caller
  goes through `bin/herdr.cmd`.
- `tests/smoke.sh` runs on Windows. The case for an unlockable state directory
  detects a platform that ignores directory permission bits and skips there
  instead of failing. The suite finds a working interpreter rather than
  calling `python3`. Helper CLI detection is now tested against a controlled
  `PATH`, preference order included, rather than asserting that the machine
  running the suite has one installed.

## [0.3.0] - 2026-08-19

### Changed

- Lantern is a normal chat in its own workspace, not a lightbox. The pane
  placement is `tab`, and the 90% width and height are gone.
- `open.sh` seats that chat. The first open creates a workspace labelled
  `🔥 lantern` with `--cwd $HOME`, opens the chat there as a tab named
  `home`, and closes the empty shell tab the new workspace comes with.
- Later opens focus a chat that is still running, wherever the tab was
  moved, or seat a new one in the same workspace. Lantern does not open a
  second lantern workspace, and a lock stops two fast opens racing into
  two.
- The workspace and pane ids are remembered under the plugin state
  directory (`workspace.id`, `pane.id`) and checked before use. Herdr
  reuses ids after a restart, so a remembered workspace counts only while
  it still carries the `🔥 lantern` label, and a remembered pane only
  while it is still a lantern chat. Otherwise Lantern looks up the label,
  then creates the workspace.
- A chat that fails to start no longer leaves an empty workspace behind.
- The open path is unchanged: `hsh`, `prefix+H`, or
  `herdr plugin action invoke aigora.lantern.open`.
- A new workspace lands last in the sidebar. Lantern does not pin it to the
  top; drag it where you want it.
- The chat process still runs in the plugin state workdir. Home is only
  where the workspace sits and the `HELPER_CWD` search root.
- Prompt and docs: close the chat by quitting the helper CLI, as before.
  The tab closes with it, and the lantern workspace closes when that chat
  was the only tab. Escape still stays inside the CLI. The helper is told
  not to close its own workspace or seat other agents in it.
- Docs: quit the chat before upgrading, relinking, or reinstalling the
  plugin. Herdr drops its record of a running lantern pane on install and
  link, so that tab stops answering to `herdr plugin pane focus` and the
  next open seats a fresh chat beside it.

## [0.2.1] - 2026-08-19

### Changed

- Cursor `agent` default model is `cursor-grok-4.6-high-fast` (Grok 4.6
  High Fast) when `HELPER_MODEL` is empty.

### Fixed

- `bin/herdr` now relays `agent prompt` with `--wait`. If Herdr reports a
  stalled submit (text typed but Enter ignored — common on idle Cursor
  panes), it sends Enter and waits again. Prompt rules tell the helper to
  read the pane before saying the message was sent.

## [0.2.0] - 2026-08-18

First named release. Plugin id is `aigora.lantern`. GitHub repo is
[aigorahub/herdr-lantern](https://github.com/aigorahub/herdr-lantern).
Requires Herdr 0.7.5+.

### Added

- Field snapshot on light-up (`bin/goals-floor`): pane titles, Claude `/goal`
  and recap lines, buckets NEEDS YOU / IN MOTION / LIVE GOALS / QUIET. No
  invented percent-complete.
- Optional Elves floor (`bin/elves-floor`): if `.elves-session.json` files
  exist under the usual code roots, groups IN PROGRESS / WAITING ON YOU /
  STALE. Home is never walked as a search root. No Elves skill required.
- Helper CLIs: Cursor `agent`, Devin, Claude Code, Codex, Grok. Empty
  `HELPER_AGENT` picks the first of those on `PATH`.
- `bin/herdr` mutate gate: inspect is allowed; create / start / focus / close
  need `HERDR_HELPER_OK=1`.
- `hsh` shortcut and `prefix+H` keybind (`aigora.lantern.open`).
- Product art: cobbler with a lantern over the herd
  (`assets/lantern-banner.jpeg`, GitHub social preview).
- Public guide at `docs/` for GitHub Pages
  (https://aigorahub.github.io/herdr-lantern/).
- Claude helper gets `CLAUDE.md` in the workdir (same text as `AGENTS.md`).

### Changed

- Plugin id `aigora.session-helper` → `aigora.lantern`. Display name
  **Lantern, by Elves**.
- Prompt and docs use Herdr's words: workspace, pane, agent, working /
  blocked / done / idle. The sidebar already has status; Lantern adds
  what they are working toward.
- Cursor `agent` defaults to `composer-2.5-fast`, `--trust --sandbox disabled`.
  `HELPER_PERMISSION=smart` → `--auto-review`.
- Devin: `--permission-mode` from config. Do not pass `--model` (Free rejects it).
- Conf parse is `KEY=value` only. Unknown keys and shell metacharacters fail.
  The file is never sourced.

### Fixed

- Conf character class no longer treats the letter `n` as a metacharacter
  (that broke `HELPER_AGENT="devin"`).

## [0.1.0]

Initial session-helper popup plugin on `main`.
