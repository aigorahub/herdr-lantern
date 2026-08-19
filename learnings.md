# Learnings

Durable lessons from work on this repository. Reusable beyond one run.

---

## Git Bash on Windows

**`sh.exe` brings its own coreutils.** A plugin command launched straight from
Windows as `["sh", "open.sh"]` still gets `sed`, `grep`, `tr`, `find`, and
`mktemp` from the MSYS runtime, even though `C:\Program Files\Git\usr\bin` is
not on the Windows PATH. Measured with `tests/smoke.sh`, which is heavy in all
five. Do not add PATH scaffolding for them.

**Bare `bash` is a trap on Windows.** `bash` on PATH is usually
`%LOCALAPPDATA%\Microsoft\WindowsApps\bash.exe`, the WSL launcher. Invoking it
starts Linux, not Git Bash. Name `sh` (which is not shimmed by WindowsApps) or
an absolute Git Bash path.

**`python3` on PATH is usually a lie.** Windows ships a zero-byte Microsoft
Store alias at `%LOCALAPPDATA%\Microsoft\WindowsApps\python3.exe`. It satisfies
`command -v python3` and then opens the Store instead of running Python. Detect
an interpreter by running it, not by finding it. A zero-byte size is a cheap
first filter.

**Extensionless wrappers do not intercept native callers.** A PATH-shadowing
wrapper script with no file extension is invisible to a native Windows process,
because PATHEXT resolution skips it and finds the real `.exe` further down the
PATH. Any security gate built on PATH interception needs a `.cmd` sibling on
Windows, or it fails open and silently.

---

## Herdr plugins

**Item ids must be unique inside a plugin.** Action ids, pane ids, and link
handler ids can each appear once, whatever their `platforms` lists say. So a
plugin cannot ship a POSIX action and a Windows action under one id, and any
key binding that names an action id pins that id to a single command. Per-item
`platforms` can only narrow where an entry runs, not select between entries.

**Herdr installs a plugin whose `platforms` exclude the host.** The install and
the enable both succeed, and `herdr plugin action list` reports the declared
platforms. The exclusion shows up at invoke time, not install time. Read the
`platforms` field in `action list` rather than trusting that an installed
plugin is a usable one.

---

## Elves staging

**The plan parser wants canonical headings.** Batch sections must be
`### Batch N: Name` (`^###?\s+Batch\s+\[?([0-9]+)\]?`), and each needs an
explicit `**Acceptance criteria**` label before the `- [ ] B#-A#: text` rows.
`### B1: Name` parses as no batch at all and fails staging with
`plan_batch_required` plus one `session_batch_missing_in_plan` per batch.
