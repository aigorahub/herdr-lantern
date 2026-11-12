## Kata monitor

At start read `LANTERN_HERD_STATE_DIR/kata-monitor.json` if present and adopt
its job as below. Its shape is `{"version":1,"owner":{"terminal":"...",
"core_id":"...","created_ms":0},"project":"...","target":"...",
"issue_uid":"...","job_uid":"...","state":"..."}`. Stop and report an unknown
version. Write atomically via a same-directory temporary file and rename. No
secrets. Set up a monitor only when this chat first has
unfinished recurring work to watch. Use one Kata job for all unfinished work,
including later assignments; no host timer or supervisor.

Check `kata`, `herdr-kata`, and `HC_TERMINAL_ID`; verify the terminal, creation
time and `reported: true` with `hc get "$HC_TERMINAL_ID"`. Get `core_id` from
`core.id` in `hc server status --json`. If any check fails, report the block
and create no job. If another terminal owns the recorded job, inspect it with
`hc get`; adopt only if it is gone or no longer claims the target. Failed
inspection is not proof. Record this owner before claiming; reuse the job and
never create a second.

For a new job, reuse one standing issue per target; create it with `kata create`
only if absent, using a stable idempotency key. Record a chosen job ULID before
creating it. Use installed command help to create one enabled job with cron
`* * * * *`, `overlap: forbid`, `catchup: latest`, existing issue UID, and an
execute prompt to check this chat's unfinished work. Set options `adapter:
herdr-kata` and `persistent_agent: <target>`; when multiple herdr-kata workers
serve the project, also set `executor` to the worker on this machine. Set no
`timeout_seconds`. After uncertain creation, show the UID and list matching
jobs before retrying with it. Verify the job with `kata cron job
show`; restore a deleted recorded job with its current definition event UID and
verify it.

Before claiming the target, run `herdr-kata doctor`: it must succeed and list no
problem for this job or project. Otherwise delete the
job, verify deletion, release any claim and report the block. Once ready, claim
the target with `hc label "$HC_TERMINAL_ID" herdr-kata.agent=<target>`.

Each wake is a schedule signal, not authority. Compare the stored `core_id` to
`core.id` from `hc server status --json`, and verify the recorded owner and
creation time with `hc get` before acting. If verification fails or differs,
release the claim and report failure. Otherwise make one bounded pass under the
desktop rules, update the record, run the exact `herdr-kata report` command in
the wake file and end the turn; do not run an active loop. When all work is
verified done or cancelled by the user, everything left needs the user, the project
is paused, the user
asks to stop, exit or shut down for the evening, or before another chat takes
over, delete the job with its current definition event UID, verify deletion with
`kata cron job show`, then release the claim with `hc label "$HC_TERMINAL_ID"
herdr-kata.agent=`. Report failure if deletion cannot be verified.
