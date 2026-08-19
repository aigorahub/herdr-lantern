# Plan: Lantern on Windows

**Branch:** `feat/windows-support`
**Run mode:** finite
**Landing outcome:** landable PR. Do not merge.

## Intent

Make the Lantern Herdr plugin run on Windows through Git Bash. Herdr itself
already ships for Windows. The plugin excludes it: the manifest declares only
Linux and macOS, and the shell code carries POSIX and macOS assumptions.

Messaging surfaces (Telegram, Slack, WhatsApp) are a later phase. They are out
of scope here.

## Why Git Bash and not a PowerShell port

Herdr requires action ids to be unique inside a plugin. The user's key binding
calls `aigora.lantern.open`, so there is one `open` action and one command for
all three platforms. A parallel Windows-only action is not possible. Git Bash
keeps one code path.

Measured on the target machine: `sh.exe` from Git for Windows runs
`tests/smoke.sh` with the plain Windows PATH and passes every case except one.
The MSYS runtime supplies its own coreutils. No PATH scaffolding is needed for
`sed`, `grep`, `tr`, `find`, or `mktemp`.

## Established facts

These were measured, not assumed. Do not re-derive them.

- `herdr.exe` 0.8.0-preview.2026-08-18 is installed. `herdr plugin` works on
  Windows.
- Lantern is installed from GitHub at commit `99e34fa` and enabled.
  `herdr plugin action list` reports `"platforms":["linux","macos"]`.
- Git for Windows is installed. `C:\Program Files\Git\bin\sh.exe` exists.
  `sh` is not on PATH. The user approved adding `C:\Program Files\Git\bin`.
- `bash` on PATH is the WSL launcher stub in `WindowsApps`, not Git Bash.
  Never invoke bare `bash`.
- `python3` on PATH is a zero-byte Microsoft Store alias. `command -v python3`
  succeeds and running it opens the Store.
- The only helper CLI present is `claude` at
  `C:\Users\Megan\.local\bin\claude.exe`.
- `tests/smoke.sh:245` fails on Windows because `chmod 500` cannot make a
  directory unwritable there.

## Affected surfaces

`herdr-plugin.toml`, `lib.sh`, `launch.sh`, `open.sh`, `bin/herdr.cmd` (new),
`tests/smoke.sh`, `README.md`, `CHANGELOG.md`.

## Non-negotiables

- No behaviour change on macOS or Linux. Every Windows branch is a no-op
  elsewhere.
- POSIX sh only in the shell files. They run under `sh`, not bash.
- Do not weaken or delete a test to get green.
- Do not merge to `main`.
- No AI attribution in commits.

## Risk

The highest risk is B2. The mutation gate is a security control, and the
Windows failure mode is silent: a native process resolving `herdr` on PATH
skips the extensionless wrapper and reaches the real binary. A wrong fix looks
like success.

## Batches

### Batch 1: Manifest platform gate

Declare Windows in the manifest without disturbing the ids the tests and
`open.sh` agree on.

**Acceptance criteria**

- [ ] B1-A1: `herdr-plugin.toml` declares platforms linux, macos, and windows.
- [ ] B1-A2: The action id stays `open`, the pane id stays `helper`, the pane title stays `Lantern`, and the placement stays `tab`.
- [ ] B1-A3: `herdr plugin action list` reports windows in the action platforms after relink.

### Batch 2: Mutation gate on Windows

`bin/herdr` has no file extension. A native Windows process resolving `herdr`
on PATH uses PATHEXT, skips that file, and finds the real `herdr.exe` further
down. The confirmation gate is bypassed with no message.

**Acceptance criteria**

- [ ] B2-A1: `bin/herdr.cmd` forwards its arguments to the POSIX wrapper through `sh.exe` and exits with the wrapper's exit code.
- [ ] B2-A2: A mutating command through `bin/herdr.cmd` without `HERDR_HELPER_OK` exits non-zero and prints the `HERDR_HELPER_OK` hint.
- [ ] B2-A3: An inspect command through `bin/herdr.cmd` reaches the real herdr.
- [ ] B2-A4: With both files present, Git Bash `command -v herdr` still selects the extensionless wrapper.
- [ ] B2-A5: `bin/herdr.cmd` never falls through to the real herdr when `sh.exe` is missing.

### Batch 3: Python detection

`launch.sh:137` gates the field snapshot on `command -v python3`, which finds
the Store alias on Windows.

**Acceptance criteria**

- [ ] B3-A1: `lib.sh` has `helper_detect_python`, which rejects a zero-byte candidate and validates each candidate by running it.
- [ ] B3-A2: `launch.sh` uses the detected interpreter for `bin/goals-floor` and `bin/elves-floor`.
- [ ] B3-A3: A missing interpreter skips the snapshot and never kills the pane.

### Batch 4: Path form handed to herdr

`open.sh:100` sends `--cwd "$HOME"`. Under Git Bash that is `/c/Users/Megan`
and native `herdr.exe` wants `C:\Users\Megan`.

**Acceptance criteria**

- [ ] B4-A1: `lib.sh` has `helper_native_path`, which converts with `cygpath -w` when cygpath exists and is identity otherwise.
- [ ] B4-A2: `open.sh` passes the native form to `herdr workspace create --cwd`.
- [ ] B4-A3: `helper_normalize_root` keeps POSIX form, because `launch.sh:95` tests it with `[ -d ]`.

### Batch 5: Real herdr resolution with a Windows HERDR_BIN_PATH

`HERDR_BIN_PATH` arrives as `C:\Users\...\herdr.exe`. `lib.sh:252-286` file
tests it and compares it against a POSIX plugin path.

**Acceptance criteria**

- [ ] B5-A1: `helper_resolve_real_herdr` accepts a Windows-style `HERDR_BIN_PATH` and returns a path that runs.
- [ ] B5-A2: It never returns the plugin's own wrapper.

### Batch 6: PATH extension

**Acceptance criteria**

- [ ] B6-A1: `helper_extend_user_path` leaves macOS and Linux behaviour unchanged and adds no entry that could resolve bare `bash` to the WSL stub.

### Batch 7: Smoke test portability

**Acceptance criteria**

- [ ] B7-A1: The unwritable-state-dir case detects a platform that ignores the permission bits and prints a SKIP line instead of failing.
- [ ] B7-A2: That case still runs and passes on Linux and macOS.
- [ ] B7-A3: The suite uses the detected Python instead of a bare `python3`.
- [ ] B7-A4: The full suite passes under Git Bash on Windows with the plain Windows PATH.

### Batch 8: Docs and version

**Acceptance criteria**

- [ ] B8-A1: `README.md` documents the Windows requirements: Herdr on Windows, Git for Windows, `C:\Program Files\Git\bin` on PATH, one helper CLI on PATH.
- [ ] B8-A2: `README.md` gives a Windows equivalent for the `hsh` symlink step.
- [ ] B8-A3: `CHANGELOG.md` has a 0.4.0 entry covering Windows support and the gate fix.
- [ ] B8-A4: `herdr-plugin.toml` and the README version line both read 0.4.0.

### Batch 9: End to end verification on the Windows machine

The user approved unlinking the GitHub install and linking the local checkout.

**Acceptance criteria**

- [ ] B9-A1: The linked plugin creates a workspace labelled `🔥 lantern` with cwd `C:\Users\Megan` and a tab named `home`.
- [ ] B9-A2: `claude.exe` starts in the plugin state workdir and the prompt files are written.
- [ ] B9-A3: A second open focuses the existing chat instead of seating a second one.
- [ ] B9-A4: The mutation gate blocks a mutating herdr command from Git Bash and from cmd.

## Master acceptance

- [ ] M-A1: Lantern opens and runs on Windows through Git Bash, with no change to macOS or Linux behaviour.
- [ ] M-A2: The herdr mutation gate cannot be bypassed on Windows by a native process resolving `herdr` on PATH.
- [ ] M-A3: `tests/smoke.sh` passes on Windows under Git Bash and stays valid for Linux and macOS.
- [ ] M-A4: `README.md` and `CHANGELOG.md` describe the Windows requirements and the release.

## Focused tests

`sh tests/smoke.sh` is the only gate this repo has. There is no lint,
typecheck, or build step. Windows runs use
`& 'C:\Program Files\Git\bin\sh.exe' tests/smoke.sh` from PowerShell, so the
suite sees the plain Windows PATH that Herdr's launcher will give it.

## Review focus

The gate in B2. Every Windows branch being a no-op on macOS and Linux. No
weakened test in B7.

## Out of scope

Telegram, Slack, and WhatsApp relay. A PowerShell-native port. Testing Cursor
agent, Devin, Codex, or Grok, whose binaries are absent from this machine.
