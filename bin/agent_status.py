"""Send Claude lifecycle reports to its Herdr terminal, not hook stdout."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


def terminal_path() -> str | None:
    for fd in (0, 1, 2):
        try:
            return os.ttyname(fd)
        except (OSError, AttributeError):
            pass
    # Hooks inherit pipes. The owning agent still has a controlling terminal.
    pid = os.getppid()
    seen = set()
    while pid > 1 and pid not in seen:
        seen.add(pid)
        try:
            result = subprocess.run(["ps", "-o", "ppid=", "-o", "tty=", "-p", str(pid)],
                                    capture_output=True, text=True, timeout=1, check=True)
            parent, tty = result.stdout.strip().split()
            if tty not in {"?", "??", "-"}:
                path = Path("/dev") / tty
                if path.is_absolute() and str(path).startswith("/dev/"):
                    return str(path)
            pid = int(parent)
        except (OSError, ValueError, subprocess.SubprocessError):
            return None
    return None


def main() -> None:
    if not os.environ.get("HC_TERMINAL_ID") or len(sys.argv) != 2:
        return
    state = sys.argv[1]
    if state not in {"idle", "working", "blocked", "done"}:
        return
    path = terminal_path()
    if not path:
        return
    try:
        fd = os.open(path, os.O_WRONLY | os.O_NOCTTY | os.O_NONBLOCK)
        try:
            if os.isatty(fd):
                os.write(fd, f"\x1b]7501;state={state}:app=claude-code\x07".encode())
        finally:
            os.close(fd)
    except OSError:
        pass


if __name__ == "__main__":
    main()
