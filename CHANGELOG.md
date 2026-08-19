# Changelog

All notable changes to Lantern, by Elves are documented here.

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
