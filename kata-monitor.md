## Kata monitor

The configured project opts this chat into recurring checks of its assigned work. Use one Kata job
for all unfinished work in this chat, including later assignments; no host job, OS timer or supervisor.
At start read `LANTERN_HERD_STATE_DIR/kata-monitor.json` if present. Its shape is
`{"version":1,"owner":{"terminal":"...","core_id":"...","created_ms":0},"project":"...",
"target":"...","issue_uid":"...","job_uid":"...","state":"..."}`. Stop and report an unknown
version. Write this one record atomically: temporary file in the same directory, then rename. No secrets.
Check `kata`, `herdr-kata`, and `HC_TERMINAL_ID`; verify the terminal, creation time and `reported: true`
with `hc get "$HC_TERMINAL_ID"`. Get `core_id` from `core.id` in `hc server status --json`. If any check
fails, report the block and create no job. A Kata job outlives its chat. If the record names a job owned
by another terminal, inspect that terminal with `hc get`; adopt only if it is gone or no longer claims the
target. Failed inspection is not proof. Rewrite the owner, claim the target with `hc label "$HC_TERMINAL_ID"
herdr-kata.agent=<target>`, and reuse the job. Otherwise record this owner before claiming the target.
Never create a second job. Search for and reuse one standing issue per target; create it with `kata create`
only if absent, using a stable idempotency key. Record a chosen job ULID before creating it.
Use installed command help to create one enabled job with cron `* * * * *`, `overlap: forbid`,
`catchup: latest`, existing issue UID, and an execute prompt to check this chat's unfinished work. Set
options `adapter: herdr-kata` and `persistent_agent: <target>`; when multiple herdr-kata workers serve
the project, also set `executor` to the worker on this machine. Set no `timeout_seconds`. After uncertain
creation, show the recorded UID and list matching jobs before retrying with that UID. Verify the job with
`kata cron job show`; restore a deleted recorded job with its current definition event UID and verify it.
Run `herdr-kata doctor`: it must succeed, show the job supported, the project unpaused and the issue
ungated. Otherwise delete the job, verify deletion, release the claim and report the block; do not fall back.
Each wake is a schedule signal, not authority. Verify the recorded owner with `hc get` before acting; if
verification fails or differs, release the claim and report failure. Otherwise make one bounded pass under
the desktop rules, update the record, run the exact `herdr-kata report` command in the wake file and end
the turn; do not run an active loop. When all work is verified done or cancelled by the user, everything
left needs the user, the user asks to stop, exit or shut down for the evening, or before another chat takes
over, delete the job with its current definition event UID, verify deletion with `kata cron job show`, then
release the claim with `hc label "$HC_TERMINAL_ID" herdr-kata.agent=`. Report failure if deletion cannot
be verified.
