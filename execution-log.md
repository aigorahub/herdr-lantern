# Execution log: Lantern on Windows

Branch `feat/windows-support`. Plan `plans/windows-support.md`.

---

## 2026-08-19 — Staging

**Preflight**

| Check | Result |
| --- | --- |
| `git remote get-url origin` | `https://github.com/aigorahub/herdr-lantern.git` |
| `git push --dry-run origin feat/windows-support` | `* [new branch]` — push allowed |
| `gh auth status` | logged in as MasonHsu02, active |
| Branch | `feat/windows-support` created off `main` at `99e34fa` |
| Working tree | clean at staging |

**Environment survey on the target machine**

| Fact | Value |
| --- | --- |
| herdr | `C:\Users\Megan\AppData\Local\Programs\Herdr\bin\herdr.exe`, 0.8.0-preview.2026-08-18 |
| `herdr plugin` on Windows | present: install, link, config-dir, action, pane, log |
| Lantern install | github:aigorahub/herdr-lantern@99e34fa, enabled |
| Action platforms today | `["linux","macos"]` |
| Git for Windows | `C:\Program Files\Git\bin\sh.exe` present; `sh` NOT on PATH |
| `bash` on PATH | `...\WindowsApps\bash.exe` — the WSL launcher, not Git Bash |
| `python3` on PATH | `...\WindowsApps\python3.exe`, 0 bytes — a Store alias stub |
| Real Python | `...\Programs\Python\Python313\python.exe` |
| Helper CLIs | only `claude` at `C:\Users\Megan\.local\bin\claude.exe` |

**Baseline gate**

`& 'C:\Program Files\Git\bin\sh.exe' tests/smoke.sh` from PowerShell, with the
plain Windows PATH: one failure, everything else passes.

```
FAIL: an unlockable state dir should exit nonzero
exit=1
```

That is `tests/smoke.sh:245`. `chmod 500` cannot make a directory unwritable on
Windows, so the lock in `open.sh` succeeds and the case's precondition never
holds. It is a test-portability defect, not a product defect.

The run confirms the load-bearing assumption for the whole port: `sh.exe`
launched straight from Windows still resolves the MSYS coreutils (`sed`,
`grep`, `tr`, `find`, `mktemp`). No PATH scaffolding is needed for them.

**Decisions made**

1. Git Bash route, not a PowerShell port. Herdr requires unique action ids
   inside a plugin, so `aigora.lantern.open` cannot have a Windows twin. One
   command serves all three platforms and `sh.exe` must be on PATH. The user
   approved adding `C:\Program Files\Git\bin`.
2. Host-native work driver. The batches are small and mechanical, and the
   verification needs the live Herdr on this machine.
3. Landable PR only. The user said do not merge.

**Staging acceptance**

```
python "$ELVES_SKILL_ROOT/scripts/acceptance_contract.py" validate --repo-root . --session .elves-session.json
Elves acceptance staging check OK
```

First run failed with `plan_batch_required`: the plan used `### B1: ...`
headings. The parser requires `### Batch N: ...` plus an explicit
`**Acceptance criteria**` label. Fixed in the plan.

**Plan location**

The plan started at `docs/plans/windows-support.md` and moved to
`plans/windows-support.md`. `.github/workflows/pages.yml` uploads the whole
`docs/` directory to GitHub Pages on every push to `main`, so anything under
`docs/` becomes public. Run documents do not belong on the product site.

**Next required action:** Batch 1.

---

## 2026-08-19 — Batch 1: Manifest platform gate

**Rollback ref**

`refs/elves/rollback/lantern-windows-2026-08-/lantern-windows-/b1-469b51eb10bd`
at `a9097c5`, pushed to origin. The helper truncates the run id and session id
in the ref name; the head SHA is exact.

**Change**

`herdr-plugin.toml:6`: `platforms = ["linux", "macos"]` becomes
`platforms = ["linux", "macos", "windows"]`. Nothing else in the manifest
moved.

**Plugin swap on this machine**

`herdr pane list` showed one pane, this session's own. No lantern pane and no
lantern workspace existed, so the swap disturbed nothing.

```
herdr plugin uninstall aigora.lantern   -> Uninstalled aigora.lantern.
herdr plugin link C:\Claude\herdr-lantern -> plugin_linked
herdr plugin list -> aigora.lantern enabled [local:\\?\C:\Claude\herdr-lantern]
```

The previous state was `github:aigorahub/herdr-lantern@99e34fa`. Restore with
`herdr plugin unlink aigora.lantern` then
`herdr plugin install aigorahub/herdr-lantern`. Recorded in
`.elves-session.json` under `plugin_install_state`.

**Acceptance**

| Id | Evidence |
| --- | --- |
| B1-A1 | `herdr-plugin.toml:6` declares the three platforms. |
| B1-A2 | `plugin link` returned `actions[0].id=open`, `panes[0].id=helper`, `panes[0].title=Lantern`, `panes[0].placement=tab`. `tests/smoke.sh:49,70,71` assert placement and title and passed. |
| B1-A3 | `herdr plugin action list` returned `"platforms":["linux","macos","windows"]`. |

**Gate**

`& 'C:\Program Files\Git\bin\sh.exe' tests/smoke.sh` still fails only at the
known `tests/smoke.sh:245` case. That is the pre-existing Windows baseline
failure that Batch 7 repairs, not a regression from this batch. Every
assertion before it passed, which is what proves B1-A2.

**Decisions made**

Proved B1-A3 now rather than deferring it to Batch 9. The relink was needed for
Batch 9 anyway, no lantern pane was running, and leaving a criterion open
across eight batches would have made the batch unclosable.

**Next required action:** Batch 10, before the shell edits.

---

## 2026-08-19 — Batch 10: Line endings

**Rollback ref**

`refs/elves/rollback/lantern-windows-2026-08-/lantern-windows-/b10-7c45939ccf49`
at `b9eced7`, pushed.

**Measurement that started this batch**

`core.autocrlf=true` on this machine, and every interpreted file in the working
tree carried a CR before every LF:

| File | CR before | CR after |
| --- | --- | --- |
| launch.sh | 208 | 0 |
| open.sh | 167 | 0 |
| lib.sh | 286 | 0 |
| bin/herdr | 33 | 0 |
| hsh | 9 | 0 |
| tests/smoke.sh | 387 | 0 |
| bin/goals-floor | 262 | 0 |
| bin/elves-floor | 262 | 0 |

**Change**

New `.gitattributes`. `* text=auto eol=lf`, with the interpreted files named
again so the intent survives a later edit, `*.cmd` and `*.bat` at `eol=crlf`
for cmd.exe, and the image assets marked binary.

**The renormalization was endings only**

`git add --renormalize .` staged nothing but `.gitattributes`. The committed
blobs were already LF, because `core.autocrlf=true` converts on the way into
the index. Only the working-tree copies were CRLF.

**Rewriting the working tree**

`git checkout-index -f -a` did not rewrite the files. `git ls-files --eol`
then read `i/lf w/crlf attr/text eol=lf`: Git had the right attributes, knew
the working tree disagreed, and skipped the write because the stat information
matched. `git checkout .` and `git reset --hard` are forbidden by the run's
non-negotiables, so the files were rewritten byte by byte instead, dropping
each CR that preceded an LF. 18 tracked text files changed.

Afterwards `git ls-files --eol` reads `i/lf w/lf attr/text eol=lf`,
`git diff --stat` is empty, and `git status --short` is clean.

**Acceptance**

| Id | Evidence |
| --- | --- |
| B10-A1 | `.gitattributes` written; `git check-attr -a launch.sh` returns `text: set`, `eol: lf`. |
| B10-A2 | Byte scan reports CR=0 for all eight interpreted files. |
| B10-A3 | Gate reports the same single known `tests/smoke.sh:245` failure. No new failure. |
| B10-A4 | Renormalize staged only `.gitattributes`; `git diff --stat` empty and tree clean after the rewrite. |

**Decisions made**

Rewrote the working-tree bytes rather than using the documented
`git rm --cached -r . && git reset --hard` renormalization recipe. That recipe
is forbidden by this run's non-negotiables, and the byte rewrite is narrower:
it touches only tracked text files and cannot discard uncommitted work.

**Next required action:** Batch 2, the mutation gate.

---

## 2026-08-19 — Batches 2, 3, and 7

These three closed together because they unblock each other. The suite stopped
at the Windows failure in `tests/smoke.sh:245`, so no test added after that
line could run until Batch 7 repaired it, and Batch 7 needed the interpreter
detection from Batch 3.

**Rollback ref**

`refs/elves/rollback/lantern-windows-2026-08-/lantern-windows-/b2-9570aa7b4948`
at `148c405`, pushed.

### Batch 2: the mutation gate

New `bin/herdr.cmd`. It resolves `sh.exe` from PATH, then from the usual Git
for Windows locations, forwards to the POSIX wrapper, and returns its exit
code. With no `sh.exe` it exits 127 and says so. It never reaches the real
herdr on its own.

Proved by hand before the tests existed: blocked mutate returned 2 with the
hint, `agent list` returned real JSON from the live binary, the scrubbed
environment returned 127, and Git Bash still resolved `herdr` to `bin/herdr`.

Two MSYS traps cost time here, both now recorded in `learnings.md`:

1. `cmd.exe /c` does not survive Git Bash. MSYS rewrites the lone `/c` into
   the `C:` drive path, and cmd.exe starts interactively instead of running
   the command. The idiom is `cmd.exe //c`.
2. A `cmd.exe //c "set X=1 && ..."` string does not survive either. MSYS
   escapes the embedded quotes to `\"`, and cmd.exe answers
   `'\"C:\...\herdr.cmd\"' is not recognized`. The scrubbed-environment case
   writes its own small batch file instead.

### Batch 3: interpreter detection

`helper_detect_python` in `lib.sh`. It tries `python3`, `python`, then `py -3`,
skips a zero-byte file, and accepts a candidate only after running it and
seeing major version 3. `launch.sh` uses it for both snapshot scripts.

A second Windows defect turned up while writing the test, and it is a real one
rather than a test artifact: `bin/goals-floor` ran herdr with `text=True` and
no encoding, so it decoded pane output with the locale code page. Real pane
text carries box drawing, arrows, and emoji, and one undecodable byte would
have ended the snapshot with a UnicodeDecodeError on any non-UTF-8 Windows
console. `run_herdr` now passes `encoding="utf-8", errors="replace"`. The plan
gained B3-A4 for it, and the goals-floor fixture now contains `※` and `◎` so
the decoding stays proven. `bin/elves-floor` was already explicit about UTF-8
and needed nothing.

The goals-floor stub also had to change. goals-floor executes the herdr binary
itself, and Python on Windows cannot run a script by its shebang, so the stub
is a batch file there and a shell script elsewhere. Both now print the same
fixtures from disk, which keeps JSON and Unicode out of the quoting.

### Batch 7: suite portability

The `chmod 500` case now probes whether the platform honours directory
permission bits and prints a SKIP line when it does not. The assertion is
unchanged where the bits are real. The suite uses the detected interpreter
instead of a bare `python3`.

**Gate**

```
& 'C:\Program Files\Git\bin\sh.exe' tests/smoke.sh
SKIP: platform ignores directory permission bits (unlockable state dir)
ok: windows herdr.cmd gate
ok
exit=0
```

First green run of the full suite on Windows.

**B7-A2 is not closed**

"That case still runs and passes on Linux and macOS" cannot be executed on this
machine. Rather than close it on reasoning, this batch adds
`.github/workflows/tests.yml`: a smoke-test matrix over ubuntu-latest,
macos-latest, and windows-latest. On the Windows runner `shell: bash` is Git
Bash, the same interpreter Herdr uses there. Batch 7 closes when that run is
green. Batch 7 stays `in_progress` until then.

**Next required action:** push, watch the CI matrix, then Batch 4.

---

## 2026-08-19 — Batch 9: end to end on Windows

This batch found two defects that no unit test would have caught.

**Testing without disturbing the user's session**

The first invoke against the live Herdr failed with `"error":"program not
found"`. Herdr's server had no `sh` on its PATH. `C:\Program Files\Git\bin`
went on the user PATH, but a running server keeps the environment it started
with, and restarting Herdr would have killed the pane this work runs in.

So the end-to-end test ran against a second, headless Herdr server:

```
herdr --session lantern-test server     # started from a process with Git\bin on PATH
herdr --session lantern-test plugin action invoke aigora.lantern.open
```

The user's own session was never restarted.

**What worked first time**

The action succeeded with exit 0. The workspace came up labelled `🔥 lantern`,
the tab was named `home`, the empty shell tab was closed, and the pane read
`lantern: HELPER_AGENT empty; using claude from PATH` followed by Claude Code's
trust prompt for
`C:\Users\Megan\AppData\Local\herdr\plugins\aigora.lantern\workdir`. AGENTS.md,
CLAUDE.md, the Cursor rule, and the Windsurf rule were all written, and
`workspace.id` and `pane.id` were recorded. A second invoke returned
`plugin_pane_focused` for the same pane, not a second chat.

**Defect 1: the field snapshot produced nothing**

`goals-floor.txt` and `elves-floor.txt` were both 0 bytes, while `floor.txt`,
written by the shell, had content. Both Python scripts ran fine by hand, which
made it look like an environment problem.

The cause is that Herdr on Windows reports `HERDR_PLUGIN_ROOT` as an
extended-length path, `\\?\C:\Claude\herdr-lantern`. MSYS reads that happily,
so every shell test passes. Appending a child gives
`\\?\C:\Claude\herdr-lantern/bin/goals-floor`, and Windows rejects a forward
slash after the `\\?\` prefix:

```
python open: NO - OSError [Errno 22] Invalid argument:
  '\\\\?\\C:\\Claude\\herdr-lantern/bin/goals-floor'
```

`launch.sh` sends the snapshot's stderr to `/dev/null`, so the only symptom was
an empty file. `helper_posix_path` now normalises the plugin root in
`launch.sh`, `open.sh`, and `bin/herdr`. MSYS converts an ordinary POSIX path
correctly on the way to a native program; it is only this Windows-specific form
that passes through broken.

After the fix, a recycled pane wrote `goals-floor.txt` at 56 bytes and
`elves-floor.txt` at 155 bytes.

**Defect 2: output encoding, found while chasing defect 1**

A probe reproduced the pre-Batch-3 decoding failure against the live binary:

```
UnicodeDecodeError: 'charmap' codec can't decode byte 0x90 in position 252
```

That confirms B3-A4 was a real bug and not a theoretical one. The same reasoning
applies to the other direction, so both snapshot scripts now force UTF-8 on
stdout as well. `launch.sh` redirects them into files, and a redirected stream
on Windows carries the locale code page.

**Left running**

The `lantern-test` session is still up at this point in the run. Stop it during
final cleanup with `herdr session stop lantern-test`.

**Next required action:** commit, then final readiness.
