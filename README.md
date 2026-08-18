# herdr-session-helper

A [Herdr](https://herdr.dev) plugin that opens a chat popup with a helper
agent for your session. Describe a repository in natural language and it finds
the directory and spawns an agent there; ask about your open agents and it
tells you who is blocked, done, or still working, and jumps you to them.

The helper is not a new chat UI. It launches the agent CLI you already use
(Codex, Claude Code, Grok, or Devin) in a 90% session popup, primed with a prompt
that teaches it to drive the Herdr CLI: `herdr workspace create`,
`herdr agent start`, `herdr agent list`, `herdr agent focus`, and friends.

## Install

```bash
herdr plugin install aigorahub/herdr-session-helper
```

Or link a local checkout while developing:

```bash
herdr plugin link /path/to/herdr-session-helper
```

## Configure

Set the agent that powers the helper:

```bash
$EDITOR "$(herdr plugin config-dir aigora.session-helper)/helper.conf"
```

```sh
HELPER_AGENT="devin"  # required: devin, claude, codex, or grok
HELPER_MODEL=""         # optional --model; leave empty for devin
HELPER_EFFORT=""        # optional; unused for devin
HELPER_CWD="~"          # working directory for the helper
```

The helper prompt is seeded to `prompt.md` in the same config directory. Edit
it to change what the helper does; your copy is never overwritten.

## Use

Open it from the command line:

```bash
herdr plugin action invoke aigora.session-helper.open
```

Or bind a key in your Herdr config. `prefix+h` is already “focus pane left”, so use another chord:

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
- The configured agent CLI (`devin`, `codex`, `claude`, or `grok`) on your PATH

## Trust

Like every Herdr plugin, this runs as your user with your environment. It is
two small POSIX shell scripts; read them before installing.
