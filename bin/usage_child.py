#!/usr/bin/env python3
"""Bound a provider child even after its watcher exits without cleanup."""
import os
import signal
import subprocess
import sys
import threading
import time

import usage_runtime as runtime


def main() -> int:
    parent, marker = int(sys.argv[1]), sys.argv[2]
    deadline = time.monotonic() + min(100, max(0, float(sys.argv[3])))
    stop = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(signum, lambda *_: stop.set())
    # The native child reads the original pipes directly. The lease never
    # reads or prints protocol messages or credentials.
    native_env = dict(os.environ)
    native_env.pop("LANTERN_USAGE_LOCK_FD", None)
    # The lease holds the inherited lock descriptor. Native providers do not
    # inherit it. A new collector waits until cleanup ends after owner death.
    proc = subprocess.Popen(sys.argv[4:], env=native_env, stdin=sys.stdin.buffer,
                            stdout=sys.stdout.buffer, stderr=subprocess.DEVNULL,
                            start_new_session=True)
    runtime.attach_tracker(proc)
    try:
        while proc.poll() is None and not stop.is_set():
            if time.monotonic() >= deadline or runtime.process_start(parent) != marker:
                break
            stop.wait(0.2)
        return proc.returncode if proc.returncode is not None else 1
    finally:
        runtime.stop_process(proc)


if __name__ == "__main__":
    raise SystemExit(main())
