# Plan: Herd runs and live models

Lantern must start and monitor selected work across many repositories through
clean merge. Each run has one Elves driver. Lantern routes work and reads
progress. The driver owns run records, changes, review, and authorized merge.

## Acceptance

- Match sweep, issue harvest, stage, landable loop, parallel pack, cutoff
  resume, and close bar. Use installed Herdr routes. Reuse one workspace
  per cwd. Never send a prompt to a working chat.
- Keep sweep bounded to high value issues. Keep issue harvest read only.
  Require a named run before staging. Bind each phase to its model and
  exact session. Preserve Elves prewalk and independent review rules.
- Monitor each selected run through review, fixes, docs, changelog, version,
  authorized merge, GitHub version publication, deploy check, and current
  main. Raise NEEDS YOU only for a question, quota death, or dirty review.
- Grant routine permissions within selected run scope. Verify the blocked
  prompt and resulting state. Keep login and broad bypass settings separate.
- Resume the exact session with the same kind and model. Restart login
  pickers without input keys. Keep competing drivers stopped.
- List close candidates only with merge, main, and deploy evidence or a
  stated deployment block. Close only user named targets. Keep home open.
- Resolve Astra phrases and all six live efforts. Use medium by default.
  Reject bare gpt-6. Pass Fast only on request and with live tier evidence.
- Resolve Claude Fable 5.1 from the live initialization catalog. Keep family quota
  checks and substitute consent. Resolve Cursor Fable 5.1 from exact live
  IDs. Do not invent a Cursor Astra ID.
- Keep Codex unattended flags and existing Pi support. Keep yolo off unless
  the user names it.
- Pass smoke tests on Linux, macOS, and Windows. Review the cumulative diff.
  Open a PR. Do not merge this PR.

## Evidence

Checked on 2026-09-05: Herdr 0.8.2, Codex 0.153.2, Claude Code 2.1.257,
and Elves 2.36.0 from the local installed skill.

`codex debug models` now lists Astra and the priority Fast tier. The kickoff
catalog had no Fast tier. Tests must cover both catalogs. Astra has low,
medium, high, xhigh, max, and ultra. Its default is medium.

`claude --help` gives claude-fable-5 as an example. It is not a model
allowlist. The installed Claude binary contains the Fable 5.1 model record.
Its live SDK initialization response lists claude-fable-5-1[1m] and resolves
it to claude-fable-5-1. It supports low, medium, high, xhigh, and max.
The resolver must use that catalog and pin its resolved model identity.

Anthropic confirms [Fable 5.1](https://www.anthropic.com/claude/fable).
The [SDK reference](https://code.claude.com/docs/en/agent-sdk/typescript)
documents initialization model discovery. No model prompt is needed.

`agent --list-models` lists claude-fable-5-1 variants. It does not list
Astra. Tests must keep the two provider catalogs separate.

## Checks

Run `sh tests/smoke.sh`, model regression tests, and `git diff --check`.
Use fake catalogs for missing models, ambiguous phrases, and quota errors.
Use fake Herdr panes to prove that working and login states receive no keys.
Inspect GitHub checks and review comments at the pushed commit.
