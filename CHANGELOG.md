# Changelog

All notable changes to Lantern, by Elves are documented here.

## [0.3.0] - 2026-08-19

### Changed

- Lantern is a normal chat in its own workspace, not a lightbox. The pane
  placement is `tab`, and the 90% width and height are gone.
- `open.sh` seats that chat. The first open creates a workspace labelled
  `🔥 lantern` with `--cwd $HOME`, opens the chat there as a tab named
  `field`, and closes the empty shell tab the new workspace comes with.
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
