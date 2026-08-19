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
