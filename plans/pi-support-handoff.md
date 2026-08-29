# Handoff — Add Pi CLI Support to Herdr Lantern

**Status:** DRAFT — fork and local checkout are ready; no implementation has started.

## Repository

- Local checkout: `/home/nathan/dev/projects/herdr-lantern`
- Fork: `https://github.com/nathanpt/herdr-lantern`
- Upstream: `https://github.com/aigorahub/herdr-lantern`
- Default branch: `main`
- Current local state: upstream checkout clean; only `plans/pi-support-handoff.md` is intentionally untracked
- Herdr on target machine: `0.8.2`
- Preferred local helper CLI: `pi` (`/home/nathan/.local/bin/pi`)
- Also present on target: `omp` (`/home/nathan/.local/bin/omp`)
- No repository `AGENTS.md` was found.
- No `CONTRIBUTING.md` was found; use the README, plans, source comments, and existing test conventions as project guidance.

## Agent job

Add first-class support for the Pi coding agent CLI to Lantern, the Herdr plugin, so a user can configure Pi as the Lantern chat/helper and use Pi for supported launch, resume, and model/provider flows.

This is an upstream-quality contribution. Keep it narrow, testable, and compatible with all existing helper CLIs. Do not turn this into a Lantern rewrite or a generic CLI abstraction project.

## Why this matters

Lantern is a promising Herdr workflow tool, but its current supported helper list is Cursor/`agent`, Devin, Claude, Codex, and Grok. The target user’s actual coding workflow uses Pi and OMP. Pi support would make Lantern testable and useful without requiring the user to replace the preferred harness.

Pi’s documented CLI includes:

- Interactive launch: `pi`
- Continue latest session: `pi -c` / `pi --continue`
- Resume selection: `pi -r` / `pi --resume`
- Specific session: `pi --session <path|id>`
- Fork: `pi --fork <path|id>`
- Session name: `pi --name <name>`
- Model/provider: `pi --provider <provider> --model <model>`
- Non-interactive print mode: `pi -p`

Pi intentionally does **not** provide built-in MCP, sub-agents, permission popups, plan mode, to-dos, or background bash. Do not claim that Lantern’s existing permission modes map directly onto Pi.

## Verify first

Read these files before editing:

- `README.md`
- `herdr-plugin.toml`
- `launch.sh`
- `lib.sh`
- `bin/herdr`
- `bin/model-route`
- `bin/model-preflight`
- `tests/smoke.sh`
- `plans/lantern-routing-table.md`
- `plans/windows-support.md`

Then map every existing helper-specific branch before adding Pi. Identify:

1. Helper executable detection and PATH handling.
2. `HELPER_AGENT` validation and configuration documentation.
3. Interactive launch argument construction.
4. Model/provider/effort mapping.
5. Continue/resume/fork behavior.
6. Tab naming and chat identity reporting.
7. Permission-mode handling.
8. Model preflight behavior.
9. Existing shell-test fixtures and portability assumptions.

Run the existing smoke suite before changing production code:

```bash
cd /home/nathan/dev/projects/herdr-lantern
sh tests/smoke.sh
```

Record the baseline result in the final handoff. Do not reset or clean unrelated work if any appears; stop and report it.

## MVP scope

Implement only the minimum complete Pi adapter:

1. Recognize `pi` as a supported `HELPER_AGENT`.
2. Detect Pi on PATH using the project’s existing path-resolution conventions.
3. Launch Pi interactively from Lantern’s helper pane.
4. Pass a configured Pi model/provider using Pi’s actual flags.
5. Support Pi continue/resume/session routing where Lantern already has equivalent routes.
6. Preserve Lantern’s tab/session identity reporting without inventing unsupported Pi metadata.
7. Define a safe, explicit behavior for `HELPER_PERMISSION` with Pi:
   - Do not pass Claude/Cursor/Codex/Devin/Grok-specific permission flags to Pi.
   - Do not silently translate `dangerous` into a Pi bypass.
   - Prefer a documented no-extra-flag behavior or a clear unsupported-mode failure, consistent with the existing project’s conventions.
8. Add Pi to README/config examples and any routing documentation that enumerates helper CLIs.
9. Add focused shell tests using a fake Pi executable. Tests must assert the generated argv and relevant environment, not start a real interactive agent.
10. Preserve Linux/macOS/Windows Git Bash portability where the existing design requires it.

## Do not do

- Do not add OMP support in this PR; OMP is a separate helper and should not be conflated with Pi.
- Do not redesign the helper abstraction.
- Do not add a new dependency, Python package, daemon, web UI, or model service.
- Do not copy Codex’s `--dangerously-bypass-approvals-and-sandbox` behavior to Pi.
- Do not claim Pi has plan mode, permission prompts, background bash, or sub-agent support.
- Do not change existing helper behavior unless a regression test proves the current shared path must change.
- Do not modify Herdr itself.
- Do not install Lantern on the user’s active Herdr instance as part of this code change.
- Do not commit, push, open an issue, or open a pull request without the user’s explicit approval.
- Do not reset, clean, stash, or overwrite unrelated working-tree changes.

## Design constraints

- Follow the current shell architecture instead of introducing a second dispatch mechanism.
- Keep command arguments as separate argv elements; do not build unsafe shell strings.
- Preserve the wrapper’s inspect-by-default and `HERDR_HELPER_OK=1` mutation gate.
- Treat model phrases and user-configured values as untrusted input; retain the existing validation rules.
- Use Pi’s real CLI semantics from its current documentation or live `pi --help`; do not infer flags from another CLI.
- If a Lantern concept has no Pi equivalent, report or omit it rather than fabricating behavior.
- Keep the adapter small enough for a maintainer to review in one sitting.

## Suggested implementation slices

### Slice 1 — reconnaissance and baseline

- Read the files listed above.
- Run `sh tests/smoke.sh`.
- Trace helper detection, launch, and routing paths.
- Return findings and the proposed exact file list before editing if the seam is not as expected.

### Slice 2 — Pi routing tests first

- Add fake-CLI tests for detection and argv construction.
- Cover interactive launch, model/provider flags, and session continuation/resume behavior that the implementation supports.
- Cover unsupported permission modes according to the chosen safe behavior.
- Keep this slice production-code-free if following a strict RED/GREEN handoff.

### Slice 3 — implementation

- Add Pi to the shared helper dispatch.
- Implement only the tested Pi branches.
- Preserve all existing helper branches byte-for-byte where possible.

### Slice 4 — documentation and portability

- Update README/config examples and routing docs.
- Check shell quoting and path behavior under the existing Linux/macOS/Windows assumptions.
- Add a short security note explaining that Pi receives no Lantern-invented approval bypass.

### Slice 5 — verification

Run:

```bash
sh tests/smoke.sh
```

Also run the focused Pi tests directly if the suite exposes them. Verify:

- Clean or explicitly understood working tree.
- Pi is listed in every intended supported-helper location.
- Existing helper tests remain green.
- Fake Pi argv matches the documented Pi CLI.
- No dangerous Codex flags appear in Pi’s argv.
- Documentation does not promise unsupported Pi capabilities.

## Acceptance criteria

The PR-ready change must satisfy all of these:

- `HELPER_AGENT="pi"` is accepted and launches the `pi` executable.
- Empty helper selection can discover Pi when it is the available supported helper, without breaking existing detection priority.
- Pi model/provider flags use separate, correctly ordered argv values.
- At least one Pi session route (`--continue` or `--resume`) is implemented and tested, or the agent documents why Lantern’s current routing model cannot safely support it yet.
- Pi permission handling is explicit and does not silently bypass safety controls.
- Existing helper behavior is unchanged by focused regression tests.
- `sh tests/smoke.sh` passes.
- README and relevant plans document Pi configuration and limitations.
- No secrets are added to the repository.
- No commit, push, issue, or PR is performed until the user explicitly authorizes it.

## Deliverable from the coding agent

Return:

1. Summary of the implementation.
2. Exact changed files.
3. The Pi command/argv mapping.
4. Permission-mode decision and rationale.
5. Tests run and real results.
6. Working-tree status.
7. Any remaining uncertainty or maintainer-facing question.

## Copy-paste brief

> In `/home/nathan/dev/projects/herdr-lantern`, add narrow, upstream-quality support for the Pi coding-agent CLI. Read `README.md`, `herdr-plugin.toml`, `launch.sh`, `lib.sh`, `bin/herdr`, `bin/model-route`, `bin/model-preflight`, `tests/smoke.sh`, and the `plans/` docs first. Run `sh tests/smoke.sh` and record the baseline. Add Pi detection, `HELPER_AGENT=pi`, interactive launch, real Pi model/provider flags, one safe tested session route, documentation, and fake-CLI tests. Preserve existing helper behavior and the inspect-by-default mutation gate. Do not pass Codex’s dangerous bypass flag to Pi; Pi has no Lantern-equivalent permission-popup/plan model, so handle permission settings explicitly and safely. Do not add OMP support, redesign the abstraction, add dependencies, modify Herdr, install the plugin, commit, push, open an issue, or open a PR. Finish with changed files, exact argv behavior, permission rationale, real test output, and working-tree status.
