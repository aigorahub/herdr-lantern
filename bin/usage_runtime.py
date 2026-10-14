"""Private state, kernel locks, and bounded read operations for Lantern views."""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import signal
import ssl
import subprocess
import sys
import threading
import time
from pathlib import Path

import field_status as field


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


SYSTEM_CA_FILES = ("/etc/ssl/cert.pem", "/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt")


def tls_context() -> ssl.SSLContext:
    """Verify HTTPS when this Python ships without CA certificates, as Homebrew Python can."""
    context = ssl.create_default_context()
    if context.cert_store_stats()["x509_ca"] or os.environ.get("SSL_CERT_FILE") or os.environ.get("SSL_CERT_DIR"):
        return context
    for cafile in SYSTEM_CA_FILES:
        if os.path.isfile(cafile):
            return ssl.create_default_context(cafile=cafile)
    try:
        import certifi
    except ImportError:
        return context
    return ssl.create_default_context(cafile=certifi.where())


def private_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise RuntimeError("Private state directory is a symlink")
    if os.name != "nt":
        path.chmod(0o700)
    return path


class FileLock:
    """Hold one descriptor. Never delete the file or steal a live lock."""
    def __init__(self, path: Path):
        private_dir(path.parent)
        if path.is_symlink():
            raise RuntimeError("Lock file is a symlink")
        self.fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        if os.name != "nt":
            os.fchmod(self.fd, 0o600)
        self.held = False
        # Windows locks a byte range, including the first byte of this file.
        if os.fstat(self.fd).st_size == 0:
            os.write(self.fd, b"\0")

    def acquire(self) -> bool:
        if self.held:
            return True
        try:
            if os.name == "nt":
                import msvcrt
                os.lseek(self.fd, 0, os.SEEK_SET)
                msvcrt.locking(self.fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        self.held = True
        return True

    def close(self) -> None:
        if self.fd < 0:
            return
        if self.held:
            if os.name == "nt":
                import msvcrt
                os.lseek(self.fd, 0, os.SEEK_SET)
                msvcrt.locking(self.fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.fd, fcntl.LOCK_UN)
        os.close(self.fd)
        self.fd = -1
        self.held = False


def server_identity(binary: str, timeout: float) -> dict:
    proc = subprocess.run(field.herdr_command(binary, ["status", "--json"]),
                          env=field.herdr_environment(binary), capture_output=True,
                          text=True, timeout=timeout, check=False)
    if proc.returncode:
        raise RuntimeError("Herdr server status unavailable")
    server = json.loads(proc.stdout).get("server", {})
    endpoint = server.get("socket")
    if not server.get("running") or not isinstance(endpoint, str) or not endpoint:
        raise RuntimeError("Herdr server endpoint unavailable")
    lifetime = None
    if os.name != "nt":
        stat = Path(endpoint).stat()
        lifetime = digest([stat.st_dev, stat.st_ino, stat.st_ctime_ns,
                           getattr(stat, "st_birthtime", None)])
        endpoint = str(Path(endpoint).resolve())
    elif server.get("pid") and server.get("started_at"):
        lifetime = digest([server["pid"], server["started_at"]])
    return {"key": digest(endpoint), "lifetime": lifetime}


def shared_dir(server: dict) -> Path:
    base = os.environ.get("XDG_STATE_HOME", "")
    root = Path(base) if base and Path(base).is_absolute() else Path.home() / ".local" / "state"
    return private_dir(root / "herdr" / "plugins" / "aigora.lantern" / "usage" / server["key"])


def darwin_process_info(pid: int) -> bytes:
    # sys/proc_info.h: PROC_PIDTBSDINFO=3, proc_bsdinfo is 136 bytes.
    # Start seconds and microseconds are uint64 fields at offsets 120/128.
    lib = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    lib.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
    buffer = ctypes.create_string_buffer(136)
    if lib.proc_pidinfo(pid, 3, 0, buffer, len(buffer)) != len(buffer):
        raise OSError("Process identity unavailable")
    return buffer.raw


def process_start(pid: int) -> str:
    """Process creation marker. Used for diagnostics and receipt checks."""
    try:
        if sys.platform.startswith("linux"):
            tail = (Path("/proc") / str(pid) / "stat").read_text().rsplit(")", 1)[1].split()
            return tail[19]
        if sys.platform == "darwin":
            info = darwin_process_info(pid)
            return f"{int.from_bytes(info[120:128], sys.byteorder)}:{int.from_bytes(info[128:136], sys.byteorder)}"
        if os.name == "nt":
            from ctypes import wintypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel.OpenProcess.restype = wintypes.HANDLE
            kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            handle = kernel.OpenProcess(0x1000, False, pid)
            if not handle:
                return ""
            try:
                values = [wintypes.FILETIME() for _ in range(4)]
                if kernel.GetProcessTimes(handle, *(ctypes.byref(v) for v in values)):
                    return str((values[0].dwHighDateTime << 32) | values[0].dwLowDateTime)
            finally:
                kernel.CloseHandle(handle)
    except (OSError, subprocess.SubprocessError, IndexError):
        pass
    return ""


def process_executable(pid: int) -> str:
    if sys.platform.startswith("linux"):
        return os.readlink(Path("/proc") / str(pid) / "exe")
    if sys.platform == "darwin":
        lib = ctypes.CDLL("/usr/lib/libproc.dylib")
        lib.proc_pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
        buffer = ctypes.create_string_buffer(4096)
        if lib.proc_pidpath(pid, buffer, len(buffer)) > 0:
            return buffer.value.decode("utf-8", "strict")
    raise RuntimeError("Account unknown")


def process_environment(pid: int) -> dict[str, str]:
    """Read privately. Callers retain only allowed selectors, never this block."""
    if not isinstance(pid, int) or pid <= 0:
        raise RuntimeError("Account unknown")
    if sys.platform.startswith("linux"):
        path = Path("/proc") / str(pid) / "environ"
        if path.stat().st_uid != os.geteuid():
            raise RuntimeError("Account unknown")
        with path.open("rb") as stream:
            raw = stream.read(2 * 1024 * 1024 + 1)
    elif sys.platform == "darwin":
        info = darwin_process_info(pid)
        if int.from_bytes(info[20:24], sys.byteorder) != os.geteuid():
            raise RuntimeError("Account unknown")
        libc = ctypes.CDLL(None, use_errno=True)
        mib = (ctypes.c_int * 3)(1, 49, pid)  # CTL_KERN, KERN_PROCARGS2
        size = ctypes.c_size_t()
        if libc.sysctl(mib, 3, None, ctypes.byref(size), None, 0) or not 4 < size.value <= 2 * 1024 * 1024:
            raise RuntimeError("Account unknown")
        buffer = ctypes.create_string_buffer(size.value)
        if libc.sysctl(mib, 3, buffer, ctypes.byref(size), None, 0):
            raise RuntimeError("Account unknown")
        data = buffer.raw[:size.value]
        argc = int.from_bytes(data[:4], sys.byteorder)
        if not 0 < argc < 16384:
            raise RuntimeError("Account unknown")
        offset = data.index(b"\0", 4) + 1  # executable path and padding
        while offset < len(data) and data[offset] == 0:
            offset += 1
        for _ in range(argc):
            offset = data.index(b"\0", offset) + 1
        raw = data[offset:].lstrip(b"\0")
    else:
        # No ambient fallback. A future verified Windows receipt can supply scope.
        raise RuntimeError("Account unknown")
    if not raw or len(raw) > 2 * 1024 * 1024:
        raise RuntimeError("Account unknown")
    result = {}
    for entry in raw.split(b"\0"):
        key, sep, value = entry.partition(b"=")
        if sep:
            result[key.decode("utf-8", "strict")] = value.decode("utf-8", "strict")
    if not result.get("HOME") or not Path(result["HOME"]).is_absolute():
        raise RuntimeError("Account unknown")
    return result


def stop_process(proc: subprocess.Popen) -> None:
    """Stop the owned group, including on successful reads with helper children."""
    tracker = getattr(proc, "_usage_tracker", None)
    if tracker:
        tracker[0].set()
        tracker[1].join(timeout=2)
        # PTY helpers can create another process group. Verify each start
        # marker before stopping it so a reused PID never receives a signal.
        with tracker[3]:
            recorded = list(tracker[2].items())
        for pid, marker in recorded:
            if marker and process_start(pid) == marker:
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
    if os.name == "nt":
        if proc.poll() is None:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5,
                           check=False)
    else:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            proc.kill()
        else:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        proc.wait(timeout=2)
    if os.name != "nt":
        # The parent can exit before a helper in its owned group.
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def spawn(argv: list[str], env: dict[str, str], cwd: str | None = None) -> subprocess.Popen:
    lease_fd = None
    if os.name != "nt":
        # A separate lease survives an abrupt watcher exit. It owns and reaps
        # the provider tree when the watcher dies or the deadline expires.
        marker = process_start(os.getpid())
        if not marker:
            raise RuntimeError("Provider parent identity unavailable")
        argv = [sys.executable, str(Path(__file__).with_name("usage_child.py")),
                str(os.getpid()), marker, "100", *argv]
        if env.get("LANTERN_USAGE_LOCK_FD"):
            lease_fd = int(env["LANTERN_USAGE_LOCK_FD"])
            os.fstat(lease_fd)
    return spawn_owned(argv, env, lease_fd, cwd)


def spawn_owned(argv: list[str], env: dict[str, str], lease_fd: int | None = None,
                cwd: str | None = None) -> subprocess.Popen:
    options = {"pass_fds": (lease_fd,)} if lease_fd is not None and os.name != "nt" else {}
    proc = subprocess.Popen(argv, env=env, cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, bufsize=0, start_new_session=os.name != "nt",
                            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0, **options)
    attach_tracker(proc)
    return proc


def attach_tracker(proc: subprocess.Popen) -> None:
    if os.name != "nt":
        stopped, children, guard = threading.Event(), {}, threading.Lock()

        def track() -> None:
            while not stopped.is_set():
                if proc.poll() is not None:
                    break
                try:
                    output = subprocess.check_output(["/bin/ps", "-axo", "pid=,ppid="], text=True, timeout=1)
                    parents = {int(parts[0]): int(parts[1]) for line in output.splitlines()
                               if len(parts := line.split()) == 2}
                    owned = {proc.pid, *(pid for pid, marker in children.items() if process_start(pid) == marker)}
                    changed = True
                    while changed:
                        found = {pid for pid, parent in parents.items() if parent in owned} - owned
                        changed = bool(found)
                        owned.update(found)
                    for pid in owned - {proc.pid} - children.keys():
                        marker = process_start(pid)
                        if marker and not stopped.is_set():
                            with guard:
                                children[pid] = marker
                except (OSError, ValueError, subprocess.SubprocessError):
                    pass
                if proc.poll() is not None:
                    break
                stopped.wait(0.2)

        thread = threading.Thread(target=track, daemon=True)
        proc._usage_tracker = (stopped, thread, children, guard)
        thread.start()


def capture(argv: list[str], env: dict[str, str], deadline: float,
            cancel: threading.Event | None = None, limit: int = 1024 * 1024,
            cwd: str | None = None) -> tuple[int, bytes]:
    """Bound output during execution, not after communicate has allocated it."""
    proc = spawn(argv, env, cwd)
    output = bytearray()
    overflow = threading.Event()
    done = threading.Event()

    def drain() -> None:
        try:
            while True:
                chunk = proc.stdout.read(8192)
                if not chunk:
                    break
                if len(output) + len(chunk) > limit:
                    overflow.set()
                    break
                output.extend(chunk)
        except OSError:
            pass
        finally:
            done.set()

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    try:
        proc.stdin.close()
        while not (done.is_set() and proc.poll() is not None):
            if overflow.is_set():
                raise RuntimeError("Usage response too large")
            if time.monotonic() >= deadline or (cancel and cancel.is_set()):
                raise RuntimeError("Usage request timed out")
            done.wait(0.05) if not done.is_set() else time.sleep(0.05)
        if overflow.is_set():
            raise RuntimeError("Usage response too large")
        return proc.returncode, bytes(output)
    finally:
        stop_process(proc)
        reader.join(timeout=2)
        proc.stdout.close()
