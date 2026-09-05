#!/usr/bin/env python3
"""Private, pull-only team reports. No chat input or product authority."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import sqlite3
import stat
import sys
import time

PROTOCOL = 1
MAX_BYTES = 65536
MAX_ACTOR_BYTES = 32768
MAX_REPLY_BYTES = 512 * 1024
MAX_PENDING = 10000
IDENTITY = {"actor_id", "run_id", "role", "server_id", "pane_id", "session_id",
            "kind", "model", "generation", "task_ids", "peers"}
KINDS = {"assignment", "progress", "question", "answer", "decision", "pr_opened",
         "review_requested", "review_result", "blocked", "completion", "cancellation"}


class MailboxError(ValueError):
    pass


def encode(value, ascii=False):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=ascii,
                      allow_nan=False)


def string(value, field):
    if not isinstance(value, str) or not value.strip() or len(value) > 256 or any(
            ord(c) < 32 for c in value):
        raise MailboxError(f"invalid_{field}")
    return value


def read_json(path):
    with open(path, "rb") as stream:
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise MailboxError("input_too_large")
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise MailboxError("duplicate_json_key")
            result[key] = value
        return result
    value = json.loads(data, object_pairs_hook=pairs,
                       parse_constant=lambda _: (_ for _ in ()).throw(MailboxError("invalid_number")))
    if not isinstance(value, dict):
        raise MailboxError("object_required")
    return value


def actor_identity(actor):
    if not isinstance(actor, dict) or set(actor) != IDENTITY:
        raise MailboxError("invalid_actor_fields")
    for field in IDENTITY - {"task_ids", "peers"}:
        string(actor[field], field)
    if actor["role"] not in {"driver", "helper", "reviewer", "lantern"}:
        raise MailboxError("invalid_role")
    for field in ("task_ids", "peers"):
        values = actor[field]
        if not isinstance(values, list) or len(values) > 1000:
            raise MailboxError(f"invalid_{field}")
        for value in values:
            string(value, field)
        if len(set(values)) != len(values):
            raise MailboxError(f"duplicate_{field}")
    if not actor["task_ids"]:
        raise MailboxError("task_scope_required")
    if len(encode(actor).encode()) > MAX_ACTOR_BYTES:
        raise MailboxError("actor_too_large")
    return actor


def private_directory(path):
    path = Path(path).expanduser().absolute()
    if path.is_symlink():
        raise MailboxError("state_symlink")
    # Canonicalize system aliases such as macOS /var and /tmp.
    path = path.resolve()
    # State must not live in a disposable Git worktree.
    for parent in (path, *path.parents):
        if (parent / ".git").exists():
            raise MailboxError("state_inside_product_repo")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not path.is_dir():
        raise MailboxError("invalid_state_directory")
    if os.name != "nt":
        if path.stat().st_uid != os.getuid():
            raise MailboxError("state_owner_mismatch")
        os.chmod(path, 0o700)
    return path


class Mailbox:
    def __init__(self, state_dir):
        self.root = private_directory(state_dir)
        path = self.root / "mailbox.sqlite3"
        if path.exists() or path.is_symlink():
            st = path.lstat()
            if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
                raise MailboxError("unsafe_database_path")
        # Secure permissions before SQLite opens the database.
        fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        os.close(fd)
        if os.name != "nt":
            os.chmod(path, 0o600)
        self.db = sqlite3.connect(path, timeout=5, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA busy_timeout=5000")
        with self.transaction():
            version = self.db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, PROTOCOL):
                raise MailboxError("unsupported_store_version")
            self.db.execute("""CREATE TABLE IF NOT EXISTS actors (
                actor_id TEXT PRIMARY KEY, identity TEXT NOT NULL,
                token_hash TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1)""")
            self.db.execute("""CREATE TABLE IF NOT EXISTS messages (
                message_id TEXT PRIMARY KEY, sender TEXT NOT NULL,
                recipient TEXT NOT NULL, payload TEXT NOT NULL, digest TEXT NOT NULL,
                status TEXT NOT NULL, created_at REAL NOT NULL, expires_at REAL NOT NULL,
                claim_until REAL, receipt TEXT, priority INTEGER NOT NULL DEFAULT 3)""")
            self.db.execute("CREATE INDEX IF NOT EXISTS inbox ON messages(recipient,status,created_at)")
            self.db.execute(f"PRAGMA user_version={PROTOCOL}")

    def close(self):
        self.db.close()

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def expire(self):
        now = time.time()
        self.db.execute("UPDATE messages SET status='unresolved' WHERE status='claimed' AND claim_until<=?", (now,))
        self.db.execute("UPDATE messages SET status='expired' WHERE status='queued' AND expires_at<=?", (now,))

    def authenticate(self, credential, archive=False):
        if set(credential) != {"protocol", "actor", "token"} or type(credential["protocol"]) is not int or credential["protocol"] != PROTOCOL:
            raise MailboxError("invalid_credential")
        actor = actor_identity(credential["actor"])
        token = string(credential["token"], "token")
        row = self.db.execute("SELECT * FROM actors WHERE actor_id=?", (actor["actor_id"],)).fetchone()
        digest = hashlib.sha256(token.encode()).hexdigest()
        if not row or (not row["active"] and not archive) or row["identity"] != encode(actor) or not hmac.compare_digest(digest, row["token_hash"]):
            raise MailboxError("actor_identity_mismatch")
        return actor

    def register(self, actor, output):
        actor = actor_identity(actor)
        output = Path(output).expanduser().absolute()
        if output.is_symlink():
            raise MailboxError("unsafe_credential_path")
        # Keep credentials beside private state, never in product files.
        if output.parent.resolve() != self.root.resolve():
            raise MailboxError("credential_must_be_in_state_directory")
        with self.transaction():
            # A durable credential can precede the database commit after a crash.
            # Resume only the exact registration, never issue a replacement token.
            if output.exists():
                if not output.is_file():
                    raise MailboxError("unsafe_credential_path")
                # A crash between link and unlink leaves the private temporary
                # hard link. Remove only links to this exact credential inode.
                info = output.stat()
                if info.st_nlink > 1:
                    for pending in self.root.glob(".credential-*"):
                        if pending.name == output.name:
                            continue
                        st = pending.lstat()
                        if stat.S_ISREG(st.st_mode) and (st.st_dev, st.st_ino) == (info.st_dev, info.st_ino):
                            pending.unlink()
                    if output.stat().st_nlink != 1:
                        raise MailboxError("unsafe_credential_path")
                saved = read_json(output)
                if set(saved) != {"protocol", "actor", "token"} or type(saved["protocol"]) is not int or saved["protocol"] != PROTOCOL or saved["actor"] != actor:
                    raise MailboxError("credential_exists")
                token = string(saved["token"], "token")
            else:
                token = secrets.token_urlsafe(32)
            row = self.db.execute("SELECT * FROM actors WHERE actor_id=?", (actor["actor_id"],)).fetchone()
            if row:
                if not output.exists() or not row["active"] or row["identity"] != encode(actor) or not hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(), row["token_hash"]):
                    raise MailboxError("actor_exists")
                return {"actor_id": actor["actor_id"], "credential_path": str(output), "status": "registered", "duplicate": True}
            self.db.execute("INSERT INTO actors(actor_id,identity,token_hash) VALUES(?,?,?)",
                            (actor["actor_id"], encode(actor), hashlib.sha256(token.encode()).hexdigest()))
            if not output.exists():
                temporary = self.root / (".credential-" + secrets.token_hex(16))
                try:
                    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(fd, "w", encoding="utf-8") as stream:
                        stream.write(encode({"protocol": PROTOCOL, "actor": actor, "token": token}) + "\n")
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.link(temporary, output)
                finally:
                    temporary.unlink(missing_ok=True)
        return {"actor_id": actor["actor_id"], "credential_path": str(output), "status": "registered"}

    def post(self, credential, message):
        required = {"schema_version", "message_id", "run_id", "task_id", "recipient", "kind", "body"}
        if not required <= set(message) or set(message) - required - {"correlation_id", "ttl_seconds"}:
            raise MailboxError("invalid_message_fields")
        if type(message["schema_version"]) is not int or message["schema_version"] != PROTOCOL:
            raise MailboxError("unsupported_protocol")
        for field in required - {"schema_version", "body"}:
            string(message[field], field)
        if "correlation_id" in message:
            string(message["correlation_id"], "correlation_id")
        if message["kind"] not in KINDS or not isinstance(message["body"], dict):
            raise MailboxError("invalid_message")
        ttl = message.get("ttl_seconds", 86400)
        if type(ttl) is not int or not 1 <= ttl <= 604800:
            raise MailboxError("invalid_ttl")
        message = dict(message, ttl_seconds=ttl)
        if len(encode(message).encode()) > MAX_BYTES:
            raise MailboxError("input_too_large")
        with self.transaction():
            self.expire()
            actor = self.authenticate(credential)
            recipient = self.db.execute("SELECT * FROM actors WHERE actor_id=? AND active=1", (message["recipient"],)).fetchone()
            if not recipient:
                raise MailboxError("recipient_unavailable")
            target = json.loads(recipient["identity"])
            if message["run_id"] != actor["run_id"] or target["run_id"] != actor["run_id"] or message["task_id"] not in actor["task_ids"] or message["task_id"] not in target["task_ids"] or target["actor_id"] not in actor["peers"]:
                raise MailboxError("message_out_of_scope")
            if (actor["server_id"], actor["generation"]) != (target["server_id"], target["generation"]):
                raise MailboxError("generation_mismatch")
            if message["kind"] in {"assignment", "cancellation"} and actor["role"] not in {"driver", "lantern"}:
                raise MailboxError("assignment_forbidden")
            if actor["role"] == "lantern" and message["kind"] in {"assignment", "cancellation"} and target["role"] != "driver":
                raise MailboxError("driver_target_required")
            payload = dict(message, sender=actor)
            digest = hashlib.sha256(encode(payload).encode()).hexdigest()
            existing = self.db.execute("SELECT digest,status FROM messages WHERE message_id=?", (message["message_id"],)).fetchone()
            if existing:
                if not hmac.compare_digest(digest, existing["digest"]):
                    raise MailboxError("message_id_conflict")
                return {"message_id": message["message_id"], "status": existing["status"], "duplicate": True}
            count = self.db.execute("SELECT count(*) FROM messages WHERE status IN ('queued','claimed','unresolved')").fetchone()[0]
            if count >= MAX_PENDING:
                raise MailboxError("queue_full")
            now = time.time()
            priority = {"blocked": 0, "question": 1, "completion": 2}.get(message["kind"], 3)
            self.db.execute("INSERT INTO messages(message_id,sender,recipient,payload,digest,status,created_at,expires_at,priority) VALUES(?,?,?,?,?,'queued',?,?,?)",
                            (message["message_id"], actor["actor_id"], target["actor_id"], encode(payload), digest, now, now + ttl, priority))
            return {"message_id": message["message_id"], "status": "queued", "duplicate": False}

    def receive(self, credential, limit=20):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise MailboxError("invalid_limit")
        with self.transaction():
            self.expire()
            actor = self.authenticate(credential)
            rows = self.db.execute("""SELECT * FROM messages WHERE recipient=? AND status='queued'
                ORDER BY priority, created_at, message_id LIMIT ?""",
                                   (actor["actor_id"], limit)).fetchall()
            messages = []
            reply_bytes = 0
            for row in rows:
                receipt = secrets.token_hex(24)
                message = dict(json.loads(row["payload"]), receipt=receipt, status="claimed")
                message_bytes = len(encode(message, ascii=True)) + 1
                if reply_bytes + message_bytes > MAX_REPLY_BYTES:
                    break
                self.db.execute("UPDATE messages SET status='claimed',receipt=?,claim_until=? WHERE message_id=?",
                                (receipt, time.time() + 120, row["message_id"]))
                messages.append(message)
                reply_bytes += message_bytes
            unresolved = self.db.execute("SELECT message_id FROM messages WHERE recipient=? AND status='unresolved' ORDER BY created_at LIMIT 100",
                                         (actor["actor_id"],)).fetchall()
            return {"messages": messages, "unresolved": [r[0] for r in unresolved]}

    def addressed(self, actor, message_id):
        row = self.db.execute("SELECT * FROM messages WHERE message_id=? AND recipient=?", (message_id, actor["actor_id"])).fetchone()
        if not row:
            raise MailboxError("message_not_addressed")
        return row

    def inspect(self, credential, message_id):
        with self.transaction():
            self.expire()
            row = self.addressed(self.authenticate(credential, archive=True), message_id)
            return {"message": json.loads(row["payload"]), "status": row["status"], "expires_at": row["expires_at"]}

    def ack(self, credential, message_id, receipt):
        with self.transaction():
            self.expire()
            row = self.addressed(self.authenticate(credential), message_id)
            if row["status"] not in {"claimed", "consumed"} or not hmac.compare_digest(row["receipt"] or "", receipt):
                raise MailboxError("receipt_invalid_or_unresolved")
            self.db.execute("UPDATE messages SET status='consumed' WHERE message_id=?", (message_id,))
            return {"message_id": message_id, "status": "consumed"}

    def reconcile(self, credential, message_id, outcome):
        if outcome not in {"consumed", "retry"}:
            raise MailboxError("invalid_outcome")
        with self.transaction():
            self.expire()
            row = self.addressed(self.authenticate(credential), message_id)
            if row["status"] != "unresolved":
                raise MailboxError("reconciliation_not_required")
            sender = self.db.execute("SELECT active FROM actors WHERE actor_id=?", (row["sender"],)).fetchone()
            if outcome == "retry" and (not sender or not sender[0]):
                raise MailboxError("sender_retired")
            status = "consumed" if outcome == "consumed" else ("queued" if row["expires_at"] > time.time() else "expired")
            self.db.execute("UPDATE messages SET status=?,receipt=NULL,claim_until=NULL WHERE message_id=?", (status, message_id))
            return {"message_id": message_id, "status": status}

    def retire(self, credential):
        with self.transaction():
            actor = self.authenticate(credential)
            self.db.execute("UPDATE actors SET active=0 WHERE actor_id=?", (actor["actor_id"],))
            self.db.execute("UPDATE messages SET status='retired' WHERE recipient=? AND status IN ('queued','claimed','unresolved')", (actor["actor_id"],))
            self.db.execute("UPDATE messages SET status='unresolved' WHERE sender=? AND status IN ('queued','claimed')", (actor["actor_id"],))
            return {"actor_id": actor["actor_id"], "status": "retired"}


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--state-dir", default=os.environ.get("LANTERN_HERD_STATE_DIR"))
    commands = p.add_subparsers(dest="command", required=True)
    commands.add_parser("capabilities")
    reg = commands.add_parser("register")
    reg.add_argument("--input", required=True)
    reg.add_argument("--output", required=True)
    for name in ("post", "receive", "ack", "inspect", "reconcile", "retire"):
        sub = commands.add_parser(name)
        sub.add_argument("--actor", required=True)
        if name == "post":
            sub.add_argument("--input", required=True)
        if name == "receive":
            sub.add_argument("--limit", type=int, default=20)
        if name in {"ack", "inspect", "reconcile"}:
            sub.add_argument("--message-id", required=True)
        if name == "ack":
            sub.add_argument("--receipt", required=True)
        if name == "reconcile":
            sub.add_argument("--outcome", choices=["consumed", "retry"], required=True)
    obs = commands.add_parser("observe")
    obs.add_argument("--socket", required=True)
    obs.add_argument("--pane", action="append", required=True)
    obs.add_argument("--seconds", type=float, default=10)
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    box = None
    try:
        if args.command == "capabilities":
            result = {"protocol": PROTOCOL, "delivery": "checkpoint", "automatic_wake": False,
                      "max_message_bytes": MAX_BYTES, "max_claimed_bytes": MAX_REPLY_BYTES,
                      "commands": ["register", "post", "receive", "ack", "inspect", "reconcile", "retire", "observe"]}
        elif args.command == "observe":
            from team_observer import observe
            result = observe(args.socket, args.pane, args.seconds)
        else:
            if not args.state_dir:
                raise MailboxError("state_directory_required")
            box = Mailbox(args.state_dir)
            if args.command == "register":
                result = box.register(read_json(args.input), args.output)
            else:
                credential = read_json(args.actor)
                if args.command == "post":
                    result = box.post(credential, read_json(args.input))
                elif args.command == "receive":
                    result = box.receive(credential, args.limit)
                elif args.command == "ack":
                    result = box.ack(credential, args.message_id, args.receipt)
                elif args.command == "inspect":
                    result = box.inspect(credential, args.message_id)
                elif args.command == "reconcile":
                    result = box.reconcile(credential, args.message_id, args.outcome)
                else:
                    result = box.retire(credential)
        print(encode(result, ascii=True))
        return 0
    except (OSError, ValueError, sqlite3.Error) as error:
        # Never emit credentials, input content, or raw SQLite errors.
        code = str(error) if isinstance(error, MailboxError) else getattr(error, "code", type(error).__name__)
        print(encode({"error": code, "protocol": PROTOCOL}, ascii=True))
        return 2
    finally:
        if box is not None:
            box.close()


if __name__ == "__main__":
    sys.exit(main())
