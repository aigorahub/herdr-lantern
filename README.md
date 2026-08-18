# herdr-session-helper

**v0.2.0** — a [Herdr](https://herdr.dev) plugin (`aigora.session-helper`).

It opens a 90% popup and starts the agent CLI you already use (Devin, Claude
Code, Codex, or Grok). That helper drives `herdr`: find a repo, reuse or spawn
an agent, triage who is blocked. It is not a new chat product.

Requires Herdr **0.7.5+** on macOS or Linux.

## Install the plugin

From the marketplace / GitHub (after this release is on `main`):

```bash
# If you previously used `herdr plugin link` for this repo, unlink first.
herdr plugin unlink aigora.session-helper
herdr plugin install aigorahub/herdr-session-helper
```

Local checkout while developing:

```bash
herdr plugin link /path/to/herdr-session-helper
```

Do not run link and GitHub install at the same time for the same plugin id.

## Pick your helper CLI

The popup runs **one** CLI as the concierge. That is independent of
`HELPER_SPAWN_KIND`, which is only the default `--kind` when the helper starts
an agent for your work (usually `claude`).

Leave `HELPER_AGENT` empty to use the first of `devin`, `claude`, `codex`,
`grok` on `PATH`. Launch prepends `~/.local/bin`, `~/bin`, and Homebrew.

| Helper you want | Install that CLI | `helper.conf` |
| --- | --- | --- |
| Devin | [Devin CLI](https://docs.devin.ai) — typically `~/.local/bin/devin` | `HELPER_AGENT="devin"` · leave `HELPER_MODEL` empty (Free rejects `--model`; Devin uses `~/.config/devin/config.json`) · `HELPER_PERMISSION="smart"` |
| Claude Code | [Claude Code](https://code.claude.com/docs) on `PATH` as `claude` | `HELPER_AGENT="claude"` · optional `HELPER_MODEL` · optional `HELPER_EFFORT` (`--effort`) |
| Codex | [Codex CLI](https://github.com/openai/codex) on `PATH` as `codex` | `HELPER_AGENT="codex"` · optional `HELPER_MODEL` · optional `HELPER_EFFORT` (`model_reasoning_effort`) |
| Grok | Grok CLI on `PATH` as `grok` (often `~/.grok/bin`) | `HELPER_AGENT="grok"` · optional `HELPER_MODEL` · optional `HELPER_EFFORT` (`--reasoning-effort`) |

Each person on the team sets their own file. Nobody shares one model string.

```bash
$EDITOR "$(herdr plugin config-dir aigora.session-helper)/helper.conf"
```

```sh
HELPER_AGENT="devin"         # or claude, codex, grok; empty = first on PATH
HELPER_MODEL=""              # optional --model; leave empty for Devin
HELPER_EFFORT=""             # unused for Devin
HELPER_CWD="~"               # search root mentioned to the helper
HELPER_SPAWN_KIND="claude"   # default --kind for `herdr agent start`
HELPER_PERMISSION="smart"    # Devin only: auto | accept-edits | smart | dangerous
HELPER_EXTRA_ARGS=""         # extra unquoted CLI tokens
```

`launch.sh` parses `KEY=value` only; it does not source the file as shell.

The helper prompt is copied once to `prompt.md` in the same config directory
and is never overwritten. After upgrading the plugin, copy the new
`prompt.md` from the repo over that file if you want the latest rules. Devin
also gets the same text as `.windsurf/rules/session-helper.md` in the plugin
state workdir.

Mutating `herdr` commands (create, start, focus, close, …) go through
`bin/herdr`. After you confirm a path or target, the helper reruns with
`HERDR_HELPER_OK=1`.

Shared walkthrough for the team: [howto.html](howto.html).

## Open it

```bash
hsh
```

That runs `herdr plugin action invoke aigora.session-helper.open`. Put `hsh`
from this repo on your `PATH` (for example `ln -s "$PWD/hsh" ~/bin/hsh`).

Or bind a key. `prefix+h` is already “focus pane left”, so use capital H:

```toml
[[keys.command]]
key = "prefix+H"
type = "plugin_action"
command = "aigora.session-helper.open"
description = "session helper"
```

Then `herdr server reload-config`. No Herdr restart is needed for plugin
script or config edits; reopen the popup.

The popup closes when the agent exits (or with that agent’s quit key).

## Tests

```bash
sh tests/smoke.sh
```

## Trust

This runs as your user with your environment. Read `launch.sh`, `lib.sh`, and
`bin/herdr` before installing. Devin `HELPER_PERMISSION=dangerous` skips
Devin’s own approval UI; the herdr wrapper still requires `HERDR_HELPER_OK=1`
for mutating commands.
