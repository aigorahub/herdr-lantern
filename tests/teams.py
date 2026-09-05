"""Team transport contract tests. All actors and messages use private fixtures."""

from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import socket
import stat
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "bin" / "team_mailbox.py"


class Mailbox(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="lantern-team-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.state = self.root / "state"
        self.counter = 0

    def invoke(self, *args, ok=True, state=True):
        command = [sys.executable, str(CLI)]
        if state:
            command += ["--state-dir", str(self.state)]
        result = subprocess.run(command + list(map(str, args)), stdin=subprocess.DEVNULL,
                                capture_output=True, text=True, timeout=15)
        try:
            value = json.loads(result.stdout)
        except ValueError:
            self.fail(f"CLI did not return JSON: {result.stdout!r}; {result.stderr!r}")
        if ok:
            self.assertEqual(result.returncode, 0, value)
        else:
            self.assertNotEqual(result.returncode, 0, value)
            self.assertTrue(value.get("error"), value)
        return value

    def fixture(self, data):
        self.counter += 1
        path = self.root / f"input-{self.counter}.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def actor(self, name, role="helper", run="run-a", tasks=None, peers=None):
        data = {"actor_id": name, "run_id": run, "role": role,
                "server_id": "server-a", "pane_id": f"pane-{name}",
                "session_id": f"session-{name}", "kind": "codex",
                "model": "gpt-6-astra", "generation": "1",
                "task_ids": tasks or ["task-a"], "peers": peers or []}
        credential = self.state / f"credential-{name}.json"
        result = self.invoke("register", "--input", self.fixture(data), "--output", credential)
        token = json.loads(credential.read_text())["token"]
        self.assertTrue(token)
        self.assertNotIn(token, json.dumps(result))
        if os.name != "nt":
            self.assertEqual(stat.S_IMODE(credential.stat().st_mode), 0o600)
        return credential

    def pair(self):
        return (self.actor("driver", "driver", peers=["helper"]),
                self.actor("helper", peers=["driver"]))

    def message(self, message_id="message-a", recipient="helper", kind="question", **fields):
        return {"schema_version": 1, "message_id": message_id, "run_id": "run-a",
                "task_id": "task-a", "recipient": recipient, "kind": kind,
                "body": {"text": "Read the relevant tests."}, **fields}

    def post(self, credential, message=None, ok=True):
        return self.invoke("post", "--actor", credential,
                           "--input", self.fixture(message or self.message()), ok=ok)

    def receive(self, credential):
        return self.invoke("receive", "--actor", credential, "--limit", "20")

    def test_capabilities_do_not_need_state(self):
        value = self.invoke("capabilities", state=False)
        self.assertEqual(value["protocol"], 1)
        self.assertEqual(value["delivery"], "checkpoint")
        self.assertIs(value["automatic_wake"], False)
        self.assertFalse(self.state.exists())

    def test_registration_cannot_replace_identity_or_credential(self):
        credential = self.actor("helper")
        original = credential.read_bytes()
        data = {}
        data.update(actor_id="helper", run_id="run-a", role="helper",
                    server_id="server-a", pane_id="different-pane",
                    session_id="different-session", kind="codex", model="gpt-6-astra",
                    generation="2", task_ids=["task-a"], peers=[])
        self.invoke("register", "--input", self.fixture(data), "--output", credential, ok=False)
        self.assertEqual(credential.read_bytes(), original)

    def test_post_receive_ack_survives_separate_processes(self):
        driver, helper = self.pair()
        self.post(driver)
        self.assertEqual(self.receive(driver)["messages"], [])
        messages = self.receive(helper)["messages"]
        self.assertEqual(len(messages), 1)
        message = messages[0]
        self.assertEqual(message["message_id"], "message-a")
        self.assertEqual(message["sender"]["actor_id"], "driver")
        self.assertEqual(message["sender"]["session_id"], "session-driver")
        self.assertNotIn("token", message["sender"])
        self.assertEqual(message["status"], "claimed")
        self.assertEqual(self.receive(helper)["messages"], [])
        self.invoke("ack", "--actor", helper, "--message-id", "message-a",
                    "--receipt", message["receipt"])
        self.assertEqual(self.receive(helper)["messages"], [])

    def test_duplicate_is_safe_but_changed_content_fails(self):
        driver, helper = self.pair()
        self.post(driver)
        self.post(driver)
        self.post(driver, self.message(body={"text": "Different work"}), ok=False)
        self.assertEqual(len(self.receive(helper)["messages"]), 1)

    def test_run_task_and_peer_scopes(self):
        driver = self.actor("driver", "driver", peers=["helper", "other-run", "other-task"])
        helper = self.actor("helper", peers=["driver"])
        stranger = self.actor("stranger", peers=["driver"])
        self.actor("other-run", run="run-b")
        self.actor("other-task", tasks=["task-b"])
        for message in (self.message(run_id="run-b"), self.message(task_id="task-b"),
                        self.message(recipient="stranger"), self.message(recipient="other-run"),
                        self.message(recipient="other-task"), self.message(recipient="missing")):
            with self.subTest(message=message):
                self.post(driver, message, ok=False)
        self.post(stranger, self.message(recipient="helper"), ok=False)
        self.assertEqual(self.receive(helper)["messages"], [])

    def test_helper_cannot_assign_or_cancel(self):
        driver, helper = self.pair()
        for kind in ("assignment", "cancellation"):
            self.post(helper, self.message(recipient="driver", kind=kind), ok=False)
        self.post(driver, self.message(kind="assignment"))
        self.assertEqual(len(self.receive(helper)["messages"]), 1)

    def test_lantern_assignments_target_drivers_only(self):
        lantern = self.actor("lantern", "lantern", peers=["helper", "driver"])
        self.actor("helper")
        driver = self.actor("driver", "driver")
        self.post(lantern, self.message(kind="assignment"), ok=False)
        self.post(lantern, self.message(kind="assignment", recipient="driver"))
        self.assertEqual(len(self.receive(driver)["messages"]), 1)

    def test_receive_ack_and_reconcile_require_recipient_credentials(self):
        driver, helper = self.pair()
        self.post(driver)
        message = self.receive(helper)["messages"][0]
        self.invoke("ack", "--actor", driver, "--message-id", "message-a",
                    "--receipt", message["receipt"], ok=False)
        self.invoke("ack", "--actor", helper, "--message-id", "message-a",
                    "--receipt", "incorrect", ok=False)
        self.invoke("reconcile", "--actor", driver, "--message-id", "message-a",
                    "--outcome", "consumed", ok=False)
        forged = json.loads(helper.read_text())
        forged["token"] = "incorrect"
        self.invoke("receive", "--actor", self.fixture(forged), ok=False)
        self.assertEqual(self.receive(helper)["messages"], [])

    def test_credential_identity_drift_is_rejected(self):
        _, helper = self.pair()
        original = json.loads(helper.read_text())
        # Credentials bind the full registered identity, not only the actor ID.
        identity = original.get("actor", original.get("identity", original))
        identity["session_id"] = "replaced-session"
        self.invoke("receive", "--actor", self.fixture(original), ok=False)

    def test_retirement_prevents_new_delivery_and_reuse(self):
        driver, helper = self.pair()
        self.post(driver)
        self.invoke("retire", "--actor", helper)
        self.invoke("receive", "--actor", helper, ok=False)
        self.post(driver, self.message(message_id="after-retirement"), ok=False)
        original = json.loads(helper.read_text())["actor"]
        self.invoke("register", "--input", self.fixture(original), "--output",
                    self.state / "replacement.json", ok=False)

    def expire_claim(self, message_id="message-a"):
        with sqlite3.connect(self.state / "mailbox.sqlite3") as db:
            db.execute("UPDATE messages SET claim_until=0 WHERE message_id=?", (message_id,))

    def test_expired_claim_needs_reconciliation_before_retry(self):
        driver, helper = self.pair()
        self.post(driver)
        claimed = self.receive(helper)["messages"][0]
        self.expire_claim()
        inbox = self.receive(helper)
        self.assertEqual(inbox["messages"], [])
        self.assertEqual(inbox["unresolved"], ["message-a"])
        self.invoke("ack", "--actor", helper, "--message-id", "message-a",
                    "--receipt", claimed["receipt"], ok=False)
        view = self.invoke("inspect", "--actor", helper, "--message-id", "message-a")
        self.assertEqual(view["status"], "unresolved")
        self.assertEqual(view["message"]["body"], self.message()["body"])
        self.invoke("inspect", "--actor", driver, "--message-id", "message-a", ok=False)
        self.invoke("reconcile", "--actor", helper, "--message-id", "message-a",
                    "--outcome", "retry")
        retried = self.receive(helper)["messages"][0]
        self.assertNotEqual(retried["receipt"], claimed["receipt"])
        self.invoke("ack", "--actor", helper, "--message-id", "message-a",
                    "--receipt", claimed["receipt"], ok=False)
        self.invoke("ack", "--actor", helper, "--message-id", "message-a",
                    "--receipt", retried["receipt"])

    def test_reconciled_consumption_never_delivers_again(self):
        driver, helper = self.pair()
        self.post(driver)
        self.receive(helper)
        self.expire_claim()
        self.invoke("reconcile", "--actor", helper, "--message-id", "message-a",
                    "--outcome", "consumed")
        self.post(driver)
        inbox = self.receive(helper)
        self.assertEqual(inbox["messages"], [])
        self.assertEqual(inbox["unresolved"], [])

    def test_expired_queued_message_is_not_delivered(self):
        driver, helper = self.pair()
        self.post(driver)
        with sqlite3.connect(self.state / "mailbox.sqlite3") as db:
            db.execute("UPDATE messages SET expires_at=0")
        self.assertEqual(self.receive(helper)["messages"], [])
        view = self.invoke("inspect", "--actor", helper, "--message-id", "message-a")
        self.assertEqual(view["status"], "expired")

    def test_retired_sender_reports_need_reconciliation(self):
        driver, helper = self.pair()
        self.post(driver)
        self.invoke("retire", "--actor", driver)
        inbox = self.receive(helper)
        self.assertEqual(inbox["messages"], [])
        self.assertEqual(inbox["unresolved"], ["message-a"])
        self.invoke("reconcile", "--actor", helper, "--message-id", "message-a",
                    "--outcome", "retry", ok=False)
        self.invoke("reconcile", "--actor", helper, "--message-id", "message-a",
                    "--outcome", "consumed")

    def test_message_cannot_spoof_sender_or_expand_authority(self):
        driver, helper = self.pair()
        self.post(driver, self.message(sender={"actor_id": "lantern"}), ok=False)
        for fields in ({"schema_version": 2}, {"body": "not an object"},
                       {"ttl_seconds": 0}, {"ttl_seconds": 604801},
                       {"ttl_seconds": True}, {"kind": "grant_permission"}):
            with self.subTest(fields=fields):
                self.post(driver, self.message(**fields), ok=False)
        self.assertEqual(self.receive(helper)["messages"], [])

    def test_credentials_cannot_be_written_outside_private_state(self):
        credential = self.actor("helper")
        actor = json.loads(credential.read_text())["actor"]
        actor["actor_id"] = "other-helper"
        output = self.root / "outside.json"
        self.invoke("register", "--input", self.fixture(actor), "--output", output, ok=False)
        self.assertFalse(output.exists())

    def test_concurrent_claims_never_return_a_message_twice(self):
        driver, helper = self.pair()
        for index in range(12):
            self.post(driver, self.message(message_id=f"message-{index}"))
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(lambda _: self.receive(helper), range(6)))
        ids = [message["message_id"] for result in results for message in result["messages"]]
        self.assertEqual(len(ids), 12)
        self.assertEqual(len(set(ids)), 12)

    def test_concurrent_duplicate_posts_store_one_message(self):
        driver, helper = self.pair()
        source = self.fixture(self.message())
        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(lambda _: self.invoke("post", "--actor", driver,
                                                "--input", source), range(6)))
        self.assertEqual(len(self.receive(helper)["messages"]), 1)


class Observer(unittest.TestCase):
    @unittest.skipUnless(hasattr(socket, "AF_UNIX") and os.name != "nt",
                         "Fake Unix socket server requires Unix")
    def test_subscription_and_snapshot_use_separate_read_only_connections(self):
        with tempfile.TemporaryDirectory(prefix="lantern-observe-") as directory:
            path = str(Path(directory) / "s")
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
                server.bind(path)
                server.listen(2)
                server.settimeout(5)

                def serve():
                    requests = []
                    with server.accept()[0] as subscriber:
                        subscriber.settimeout(5)
                        request = json.loads(subscriber.makefile("rb").readline())
                        requests.append(request)
                        subscriber.sendall((json.dumps({"id": request["id"],
                            "result": {"type": "subscription_started"}}) + "\n").encode())
                        # An event that arrives while the snapshot is requested must survive.
                        event = {"type": "pane.agent_status_changed", "pane_id": "pane-a",
                                 "status": "done"}
                        subscriber.sendall((json.dumps(event) + "\n").encode())
                        with server.accept()[0] as snapshot:
                            snapshot.settimeout(5)
                            request = json.loads(snapshot.makefile("rb").readline())
                            requests.append(request)
                            snapshot.sendall((json.dumps({"id": request["id"],
                                "result": {"workspaces": [], "test_marker": "snapshot"}}) + "\n").encode())
                    return requests

                with ThreadPoolExecutor(max_workers=1) as pool:
                    serving = pool.submit(serve)
                    result = subprocess.run([sys.executable, str(CLI), "observe", "--socket", path,
                                             "--pane", "pane-a", "--seconds", "1"],
                                            stdin=subprocess.DEVNULL, capture_output=True,
                                            text=True, timeout=10)
                    requests = serving.result(timeout=6)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                value = json.loads(result.stdout)
                self.assertEqual([item["method"] for item in requests],
                                 ["events.subscribe", "session.snapshot"])
                self.assertEqual(requests[0]["params"]["subscriptions"],
                                 [{"type": "pane.agent_status_changed", "pane_id": "pane-a"}])
                self.assertEqual(value["snapshot"]["test_marker"], "snapshot")
                self.assertEqual(len(value["events"]), 1)
                self.assertEqual(value["events"][0]["pane_id"], "pane-a")
                self.assertIs(value["automatic_wake"], False)
                self.assertIs(value["reconcile_required"], True)
                self.assertIs(value["disconnected"], True)

    def test_observer_rejects_agent_control_before_writing_to_socket(self):
        spec = importlib.util.spec_from_file_location("team_observer_test", ROOT / "bin/team_observer.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        class SocketThatMustNotWrite:
            def sendall(self, _):
                raise AssertionError("Observer attempted a forbidden socket write")

        for method in ("agent.prompt", "agent.send_keys", "agent.start", "tab.close"):
            with self.subTest(method=method):
                with self.assertRaises(module.ObserverError):
                    module.request(SocketThatMustNotWrite(), method, {}, "test")

    def test_observer_failure_returns_json_for_monitor_fallback(self):
        with tempfile.TemporaryDirectory(prefix="lantern-missing-socket-") as directory:
            result = subprocess.run([sys.executable, str(CLI), "observe", "--socket",
                                     str(Path(directory) / "missing"), "--pane", "pane-a",
                                     "--seconds", "0.1"], stdin=subprocess.DEVNULL,
                                    capture_output=True, text=True, timeout=5)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(json.loads(result.stdout)["error"])
        self.assertEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()
