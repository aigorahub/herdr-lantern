# Plan: Lantern routing and model availability

## Mission

Teach Lantern to route loose task, repository, agent, and model phrases to the
real Herdr and agent CLI commands on this machine. Resolve models from live
catalogs. Stop before seating when a requested model cannot run. Keep all herd
changes behind the existing confirmation gate.

## Scope

### In scope

- Field, focus, seat, resume, close, worktree, pane, plugin, and integration routes.
- Codex, Cursor, Cursor Grok, Grok Build, and Claude seating defaults.
- Pull request review routes for Codex, Cursor, Cursor Grok, and Grok Build.
- Live model phrase resolution and availability checks.
- Smart-auto permission flags and dangerous bypass exclusions.
- Smoke tests, operator documentation, release notes, and the plugin version.

### Out of scope

- Editing any product repository from Lantern.
- Merging or landing pull requests from Lantern.
- Changing Herdr or any agent CLI.
- Installing dependencies or agent CLIs.

## Batches

### Batch 1 [B1]: Reconcile and release routing

**Tasks:**

- [ ] Verify all routes against the installed Herdr and agent CLI help.
- [ ] Review the existing branch with Fugu and a separate host review.
- [ ] Fix all blocking review findings and add regression tests.
- [ ] Update all operator documentation, release notes, and version surfaces.

**Acceptance criteria:**

- [ ] B1-A1: The phrase `5.6 sol high fast` resolves to the live Codex Sol model, high reasoning, and the live Fast service tier as separate argv values.
- [ ] B1-A2: Bare `Grok` routes to Cursor with a live Cursor Grok model, `Grok Build` routes to the Grok CLI, and plain `Cursor` keeps the live Sol default.
- [ ] B1-A3: Named pull request review requests resolve the repository and pull request, then route to the requested real review surface without editing the product repository.
- [ ] B1-A4: Every supported seat uses smart-auto permissions, and dangerous bypass flags stay forbidden unless the user explicitly requests yolo.
- [ ] B1-A5: Model availability checks fail closed on catalog, command, timeout, or parse failure, and the exhausted Fable case reports its reset and proposes live Opus xhigh.
- [ ] B1-A6: `sh tests/smoke.sh` passes, the pull request checks pass on Linux, macOS, and Windows, and `git diff --check` passes.
- [ ] B1-A7: README, prompt appendix, changelog, and plugin version describe the released behavior and version.

**Docs likely touched:** README, prompt, launch appendix, changelog, and this plan.

**Risk:** standard. The main risk is reporting a model or command that the installed CLI does not accept.

**Caution:** Live catalogs can change. Every route must fail closed instead of creating a model name.

**Affected surfaces:** `prompt.md`, `launch.sh`, `lib.sh`, `bin/model-route`, `bin/model-preflight`, `tests/smoke.sh`, `README.md`, `CHANGELOG.md`, and `herdr-plugin.toml`.

**Constitution impacts:** The helper gate and the ban on product repository edits must stay unchanged.

**Review focus:** Shell and subprocess safety, exact live CLI argv, permission defaults, ambiguous repository and pull request handling, and cross-platform catalog wrappers.

**Focused tests:** `sh tests/smoke.sh` and the GitHub test matrix.

**Depends on:** none.

## Master acceptance

- [ ] M-A1: Lantern gives a short, correct field report and can route every documented herd task without editing a product repository.
- [ ] M-A2: Fugu and host reviews have no unresolved blocking finding at the final product tip.
- [ ] M-A3: The release version and GitHub release describe the tested routing and availability behavior.

## Non-negotiables

- Do not weaken the Herdr helper gate.
- Do not let Lantern edit product repositories, merge, or run land-pr.
- Do not invent model IDs or unsupported CLI commands.
- Do not pass full permission bypass flags unless the user explicitly requests yolo.
- Merge only with a regular merge commit after final readiness.

## Test strategy

- **Primary gate:** `sh tests/smoke.sh`.
- **Static gate:** `git diff --check origin/main...HEAD`.
- **CI gate:** GitHub smoke jobs on Ubuntu, macOS, and Windows.
- **Review gate:** Read-only Fugu review plus an independent host review.

## Notes

- The branch existed before this landing run started. The run reconciles and releases the open pull request.
- The live CLI evidence is from 2026-08-22.
