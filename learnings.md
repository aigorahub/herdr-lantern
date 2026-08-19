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

**`cmd.exe /c` does not survive Git Bash.** MSYS rewrites a lone `/c` argument
into the `C:` drive path before cmd.exe sees it, so cmd.exe starts an
interactive shell and the command never runs, with exit status 0. Write
`cmd.exe //c`; the doubled slash arrives as one.

**Do not pass a quoted command string to cmd.exe from Git Bash.** MSYS escapes
embedded double quotes to `\"` on the way to a native program, and cmd.exe
answers `'\"C:\path\thing.cmd\"' is not recognized`. Write the command into a
small batch file and run that file instead.

**Python on Windows cannot execute a script by its shebang.** A test that
hands a program the path of a shell-script stub works everywhere except
Windows, where the stub has to be a batch file. Keep the fixture data in
separate files so both stubs only print it and neither has to quote JSON.

**`text=True` on `subprocess.run` decodes with the locale encoding.** On
Windows that is a code page, so any byte outside it raises UnicodeDecodeError.
Terminal output carries box drawing, arrows, and emoji. Pass
`encoding="utf-8", errors="replace"` whenever the output is terminal text.

**`git checkout-index -f -a` will not rewrite a file whose stat information
matches**, even with `-f`. `git ls-files --eol` showing `i/lf w/crlf` with the
right attributes is the signature. The documented renormalization recipe ends
in `git reset --hard`; where that is not allowed, rewriting the bytes directly
is narrower and safer.

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
