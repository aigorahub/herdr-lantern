# herdr-session-helper

A [Herdr](https://herdr.dev) plugin that opens a chat popup with a helper
agent for your session. Describe a repository in natural language and it finds
the directory and spawns an agent there; ask about your open agents and it
tells you who is blocked, done, or still working, and jumps you to them.

The helper is not a new chat UI. It launches the agent CLI you already use
(Devin, Codex, Claude Code, or Grok) in a 90% session popup, primed with a
prompt that teaches it to drive the Herdr CLI.

## Install

```bash
herdr plugin install aigorahub/herdr-session-helper
```

Or link a local checkout while developing:

```bash
herdr plugin link /path/to/herdr-session-helper
```

## Configure

```bash
$EDITOR "$(herdr plugin config-dir aigora.session-helper)/helper.conf"
```

```sh
HELPER_AGENT="devin"         # devin, claude, codex, grok; empty = first on PATH
HELPER_MODEL=""              # optional --model; leave empty for devin
HELPER_EFFORT=""             # unused for devin
HELPER_CWD="~"               # search root mentioned to the helper
HELPER_SPAWN_KIND="claude"   # default --kind for herdr agent start
HELPER_PERMISSION="smart"    # devin: auto | accept-edits | smart | dangerous
HELPER_EXTRA_ARGS=""         # extra unquoted CLI tokens
```

`launch.sh` parses `KEY=value` only; it does not source the file as shell.

The helper prompt is seeded to `prompt.md` in the same config directory. Edit
that copy to change what the helper does; it is never overwritten. Devin also
gets the same text as a `.windsurf/rules` file in the plugin state workdir.

Mutating `herdr` commands (create, start, focus, close, …) are wrapped. After
the user confirms a path or target, the helper must rerun with
`HERDR_HELPER_OK=1`.

## Use

```bash
herdr plugin action invoke aigora.session-helper.open
```

Or bind a key. `prefix+h` is already “focus pane left”:

```toml
[[keys.command]]
key = "prefix+H"
type = "plugin_action"
command = "aigora.session-helper.open"
description = "session helper"
```

The popup closes when the agent exits (or with your agent's normal quit key).

## Requirements

- Herdr 0.7.5 or newer, macOS or Linux
- The configured agent CLI on `PATH` (Herdr panes should see `~/.local/bin`)

## Tests

```bash
sh tests/smoke.sh
```

## Trust

Like every Herdr plugin, this runs as your user with your environment. Read
`launch.sh`, `lib.sh`, and `bin/herdr` before installing. Devin
`HELPER_PERMISSION=dangerous` skips Devin's own approval UI; the herdr wrapper
still requires `HERDR_HELPER_OK=1` for mutating commands.
