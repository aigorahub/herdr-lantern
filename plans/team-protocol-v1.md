# Team callback protocol v1

The Lantern helper is `bin/team-mailbox`. All operations return one JSON object.
Failures return a nonzero exit and an `error` code. Use argument arrays, never
shell interpolation. This interface is optional for standalone Elves.

`team-mailbox capabilities` reports `protocol: 1`, `delivery: checkpoint`, and
`automatic_wake: false`. Other commands require `--state-dir PATH`, or
`LANTERN_HERD_STATE_DIR`. State is private and outside product repos.

The driver registers each actor with:

```
team-mailbox --state-dir STATE register --input ACTOR.json --output CREDENTIAL.json
```

Actor fields: `actor_id`, `run_id`, `role` (`driver`, `helper`, `reviewer`, or
`lantern`), `server_id`, `pane_id`, `session_id`, `kind`, `model`, `generation`,
`task_ids` (nonempty array), and `peers` (array of permitted recipient actor IDs).
All identity fields are nonempty strings. Registration cannot replace an existing
actor ID. The output file contains protocol version, actor identity, and a private
random token. Treat it as a scoped local credential. Do not put it in Git or logs.
Repeating the exact registration with the same output file is idempotent. If a
process dies after publishing the credential but before committing the actor row,
retry completes that exact registration with the existing token. Changed identity,
another credential path for the same actor, or a retired actor cannot replace it.
The driver passes only an actor's own credential to that actor. Same-user local
processes are not an adversarial security boundary.
The credential file must live directly in STATE. The launch environment exposes
`LANTERN_TEAM_MAILBOX` as the absolute Python entry point. Portable adapters call
that `.py` file with their Python interpreter. The shell shim remains available
for interactive shell commands.
`LANTERN_TEAM_STATE_DIR` gives the native absolute state path for callback JSON
on Windows. `LANTERN_HERD_STATE_DIR` remains the existing shell state path.
Elves team execution on Windows uses WSL2. Its callback executable, state, and
credential paths must resolve inside WSL2. Native Windows transport tests do
not qualify cross-environment path mapping; validate that configuration locally.

```
team-mailbox --state-dir STATE post --actor CREDENTIAL.json --input MESSAGE.json
team-mailbox --state-dir STATE receive --actor CREDENTIAL.json --limit 20
team-mailbox --state-dir STATE ack --actor CREDENTIAL.json --message-id ID --receipt RECEIPT
team-mailbox --state-dir STATE inspect --actor CREDENTIAL.json --message-id ID
team-mailbox --state-dir STATE reconcile --actor CREDENTIAL.json --message-id ID --outcome consumed
team-mailbox --state-dir STATE retire --actor CREDENTIAL.json
```

Message fields: `schema_version: 1`, `message_id`, `run_id`, `task_id`,
`recipient`, `kind`, `body` (JSON object), optional `correlation_id`, and
`ttl_seconds` (default 86400, at most 604800). Allowed kinds: `assignment`,
`progress`, `question`, `answer`, `decision`, `pr_opened`, `review_requested`,
`review_result`, `blocked`, `completion`, `cancellation`.
Sender identity is derived from the credential, not trusted from the message.
Sender and recipient must share the run and task, and sender.peers must contain
the recipient. Only driver or lantern actors can send assignments or cancellation;
lantern assignments target drivers only. Messages do not alter task acceptance.

`post` stores once per message ID and returns `message_id` and `status`.
Repeating identical content is safe. Reusing an ID with different content fails.
`receive` atomically claims queued messages for that actor and returns `messages`.
Each message includes the original fields plus `sender` (registered identity),
`receipt`, and `status: claimed`. A receipt expires after 120 seconds. Expired
claims become `unresolved`; they are never automatically sent again. Receive
limits the total claimed JSON to 512 KiB and leaves excess messages queued.
Input messages are limited to 64 KiB and actor identities to 32 KiB. Capability
output advertises the message and batch limits. Output uses ASCII JSON escapes
so redirected Windows streams preserve Unicode messages. Expired queued messages
are not delivered. `receive` also reports `unresolved` message IDs. A consumer
acknowledges only after recording
the result. `inspect` returns the addressed message body and status without
claiming it, so the consumer can inspect an unresolved delivery before acting on
it. `reconcile` supports `consumed` or `retry` after inspecting actual
effects. Only the addressed recipient can acknowledge or reconcile its message.

Receive is a pull at a safe checkpoint. The helper never prompts a pane, executes
message content, changes product files, grants permissions, or starts an agent.
Retirement prevents new delivery to that identity. Its pending inbox entries
become terminal `retired` records and no longer consume active queue capacity.
The exact old credential permits archive `inspect` only, never receive, post,
ack, or retry. Reports from a retired sender to a live recipient remain unresolved
for that recipient to inspect. Retirement does not mean consumed or verified.
Retired identities cannot be replaced under the same actor ID. Any fresh assignment
requires external effect reconciliation and a new registered identity.

Elves stores callback configuration in run state: protocol, absolute executable
path, state directory, and actor credential path. It probes capabilities before
use. Explicit `team configure-callback --input callback.json` records private
local authorization outside the checkout. A saved session alone cannot execute
a callback. Elves sends a minimal subprocess environment without provider keys.
It publishes through this CLI and consumes at existing safe checkpoints.
Capture subprocess output, use closed stdin and a timeout. Do not retry an
ambiguous post with a new message ID. No executable or callback configuration
from a worker report can overwrite driver configuration.

Herdr observation is separate: `team-mailbox observe --socket PATH --pane ID
--seconds 10`. It uses only `events.subscribe` and `session.snapshot`. It returns
bounded event hints and a snapshot for reconciliation. It never wakes a model.
An unsupported socket platform returns an explicit error so the existing CLI
monitor remains available.
