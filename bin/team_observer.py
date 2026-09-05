"""Bounded Herdr event hints. This client cannot send agent commands."""
from __future__ import annotations

import json
import math
import socket
import time

ALLOWED_METHODS = frozenset({"events.subscribe", "session.snapshot"})
MAX_FRAME = 4 * 1024 * 1024
MAX_EVENTS = 256


class ObserverError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def request(sock, method, params, request_id):
    if method not in ALLOWED_METHODS:
        raise ObserverError("observer_method_forbidden")
    wire = {"id": request_id, "method": method, "params": params}
    sock.sendall((json.dumps(wire, separators=(",", ":")) + "\n").encode())


def first_response(sock, deadline):
    data = b""
    while b"\n" not in data:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ObserverError("observer_snapshot_unavailable")
        sock.settimeout(remaining)
        try:
            part = sock.recv(65536)
        except OSError as error:
            raise ObserverError("observer_snapshot_unavailable") from error
        if not part:
            raise ObserverError("observer_disconnected")
        data += part
        if len(data) > MAX_FRAME:
            raise ObserverError("observer_frame_too_large")
    line, rest = data.split(b"\n", 1)
    try:
        result = json.loads(line)
    except (ValueError, UnicodeError) as error:
        raise ObserverError("observer_invalid_json") from error
    if not isinstance(result, dict) or "error" in result:
        raise ObserverError("observer_request_rejected")
    return result, rest


def observe(path, panes, seconds=10):
    if not hasattr(socket, "AF_UNIX"):
        raise ObserverError("socket_platform_unsupported")
    if not isinstance(seconds, (float, int)) or not math.isfinite(seconds) or not 0 < seconds <= 60:
        raise ObserverError("invalid_observation_duration")
    if not isinstance(panes, list) or not 1 <= len(panes) <= 100 or any(
            not isinstance(pane, str) or not pane.strip() or len(pane) > 256 for pane in panes):
        raise ObserverError("invalid_observation_panes")
    deadline = time.monotonic() + seconds
    buffer = b""
    events = []
    snapshot = None
    subscribed = False
    truncated = False
    disconnected = False
    frames = 0
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(min(seconds, 5))
        try:
            sock.connect(path)
            request(sock, "events.subscribe", {"subscriptions": [
                {"type": "pane.agent_status_changed", "pane_id": pane} for pane in dict.fromkeys(panes)
            ]}, "team-subscribe")
            ack, buffer = first_response(sock, deadline)
            subscribed = ack.get("id") == "team-subscribe" and isinstance(ack.get("result"), dict) and ack["result"].get("type") == "subscription_started"
            if not subscribed:
                raise ObserverError("observer_subscription_rejected")
            # Herdr dedicates the first connection to its subscription stream.
            # Subscribe first; snapshot on another connection while events buffer.
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as snap:
                snap.settimeout(max(0.001, deadline - time.monotonic()))
                snap.connect(path)
                request(snap, "session.snapshot", {}, "team-snapshot")
                response, _ = first_response(snap, deadline)
                if response.get("id") != "team-snapshot" or not isinstance(response.get("result"), dict):
                    raise ObserverError("observer_invalid_snapshot")
                snapshot = response["result"]
        except OSError as error:
            raise ObserverError("observer_connect_failed") from error
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            if b"\n" not in buffer:
                sock.settimeout(remaining)
                try:
                    chunk = sock.recv(65536)
                except socket.timeout:
                    break
                except OSError:
                    disconnected = True
                    break
                if not chunk:
                    disconnected = True
                    break
                buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                if len(line) > MAX_FRAME:
                    raise ObserverError("observer_frame_too_large")
                try:
                    item = json.loads(line)
                except (ValueError, UnicodeError) as error:
                    raise ObserverError("observer_invalid_json") from error
                if not isinstance(item, dict):
                    raise ObserverError("observer_invalid_response")
                if "error" in item:
                    raise ObserverError("observer_request_rejected")
                if item.get("id") == "team-subscribe":
                    subscribed = item.get("result", {}).get("type") == "subscription_started"
                elif item.get("id") == "team-snapshot":
                    snapshot = item.get("result")
                else:
                    if len(events) < MAX_EVENTS:
                        events.append(item)
                    else:
                        truncated = True
                frames += 1
                if frames >= 4096:
                    truncated = True
                    break
            if len(buffer) > MAX_FRAME:
                raise ObserverError("observer_frame_too_large")
            if frames >= 4096:
                break
    if not subscribed or snapshot is None:
        raise ObserverError("observer_snapshot_unavailable")
    return {"protocol": 1, "snapshot": snapshot, "events": events,
            "disconnected": disconnected, "truncated": truncated,
            "reconcile_required": True, "automatic_wake": False}
