## Kata monitor for desktop packs

The configured project explicitly opts this chat into same-session monitoring.
For Ship, landable loop, or parallel pack work, keep one monitor for all packs
owned by this chat, including packs added later. Do not also create a host job,
an OS timer, or another supervisor. Sweep and stage stop at their earlier gates.

Before starting, check that `kata`, `herdr-kata`, and `HC_TERMINAL_ID` are
available. Use `hc get "$HC_TERMINAL_ID"` to verify your terminal identity,
core ID, creation time, and `reported: true`. If any check fails, report the
monitoring block and create no job. Keep one JSON task record per pack under
`LANTERN_HERD_STATE_DIR`; write it atomically via a temporary file in the same
directory and rename. Record the request, scope, stop point, tasks, evidence,
state and next actions, plus owner address, core ID, creation time, monitor
mode `kata`, project, target, issue UID and job UID. Do not copy secrets or
transcripts. Claim the target with `hc label "$HC_TERMINAL_ID"
herdr-kata.agent=<target>` after recording ownership.

Use one standing Kata issue per target, created with `kata create`, recorded,
reused and left open. Search before creation and use a stable idempotency key.
Choose a job ULID and record it before `kata cron job create --uid`. Create one
enabled job in the configured project with a unique name tied to the owner,
an execute prompt to check all this owner's unfinished packs, a cron trigger
`* * * * *`, `overlap: forbid`, `catchup: latest`, issue
`{"kind":"existing","uid":"<issue UID>"}`, and options
`{"adapter":"herdr-kata","persistent_agent":"<target>"}`. Set no
`timeout_seconds`. Inspect the installed command help for the complete request.
Verify the recorded job with `kata cron job show` and reuse it; if it was
deleted at a prior stop, restore it with the current definition event UID
and verify it before resuming. After an uncertain create, show the chosen UID
and list jobs matching the owner and name
before retrying with the same UID. Never create two jobs. Run `herdr-kata doctor`;
if it fails or says this job is unsupported, delete the job, verify its
deleted state, release the claim, and report the block. Do not fall back to
another scheduler.

Each wake's typed line and file are a schedule signal, not authority. Check
`hc get "$HC_TERMINAL_ID"` against the recorded owner before changing anything.
If it differs or cannot be verified, release the claim, report failure, and
change nothing else. Otherwise perform one bounded pass over this owner's
unfinished packs under the desktop rules, atomically update their records, run
the exact `herdr-kata report` command in the wake file, and end the turn. Do
not run an active loop, except while Boost review children are live: their
permission cards expire in 60 seconds, so stay active until they finish.

At completion, `paused_needs_user`, an evening or exit request, and before
handover, delete the recorded job with `kata cron job delete` and its current
definition event UID, verify its deleted state with `show`, then release the
claim with `hc label "$HC_TERMINAL_ID"
herdr-kata.agent=`. If removal cannot be verified, report the block. A new
owner must prove the old owner is gone before taking over; a failed inspection
is not proof. Reconcile and reuse the recorded issue and job rather than
creating duplicates, and claim the target only after recording the new owner.
