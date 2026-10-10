"""Read verified account limits. No model turns, cookies, or ambient accounts."""
from __future__ import annotations

import json
import math
import os
import queue
import re
import shutil
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import field_status as field
import usage_runtime as runtime

VERSION = "0.74.0"
SUPPORTED = {"codex", "claude", "grok"}
SELECTORS = {"codex": ["CODEX_HOME"], "claude": ["CLAUDE_CONFIG_DIR"], "grok": ["GROK_HOME"]}
OVERRIDES = {
    "codex": ["OPENAI_API_KEY", "OPENAI_BASE_URL", "CODEX_API_KEY"],
    "claude": ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
               "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
               "CLAUDE_CODE_USE_FOUNDRY"],
    "grok": ["XAI_API_KEY", "GROK_API_KEY", "GROK_OAUTH_TOKEN", "XAI_BASE_URL"],
}


class SourceError(RuntimeError):
    def __init__(self, state: str, retry_after: float = 0):
        super().__init__(state)
        self.state = state
        self.retry_after = retry_after


def stamp(path: Path) -> list:
    try:
        stat = path.stat()
        return [stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]
    except FileNotFoundError:
        return []


def current_stamp(scope: dict) -> str:
    values = []
    for value in scope["files"]:
        path = Path(value)
        if scope["provider"] == "claude" and path.name == ".claude.json":
            # Claude writes UI counters here during the usage probe. Only
            # account metadata binds auth. Settings and credentials below
            # still use their complete file identity and change stamp.
            if path.exists():
                with path.open("rb") as stream:
                    raw = stream.read(1024 * 1024 + 1)
                if len(raw) > 1024 * 1024:
                    raise SourceError("Account unknown")
                data = json.loads(raw)
                if not isinstance(data, dict):
                    raise SourceError("Account unknown")
                values.append(runtime.digest(data.get("oauthAccount")))
            else:
                values.append(runtime.digest(None))
        else:
            values.append(stamp(path))
    return runtime.digest(values)


def scope_for(provider: str, process: dict, env: dict[str, str]) -> dict:
    home = env.get("HOME", "")
    argv = process.get("argv")
    if not home or not Path(home).is_absolute() or not isinstance(argv, list) or not argv:
        raise SourceError("Account unknown")
    if provider == "codex" and any("codex-fugu" in str(value).lower() for value in argv):
        raise SourceError("Limits unavailable")
    if provider == "codex" and any(str(value).startswith(("--oss", "--local-provider")) for value in argv):
        raise SourceError("Limits unavailable")
    if any(env.get(key) for key in OVERRIDES[provider]):
        raise SourceError("Account unknown")
    # Keep only absolute executable search paths. Never use a shell alias.
    path = os.pathsep.join(p for p in env.get("PATH", "").split(os.pathsep) if Path(p).is_absolute() and p)
    executable = process.get("executable")
    if (not executable or not Path(executable).is_absolute()
            or (provider != "grok" and not os.access(executable, os.X_OK))):
        raise SourceError("Account unknown")
    basename = Path(executable).name.lower()
    expected = {"codex": r"codex(?:\.exe)?", "claude": r"claude(?:\.exe)?", "grok": r"grok(?:[-.].*)?"}
    if not re.fullmatch(expected[provider], basename):
        raise SourceError("Account unknown")
    native = {"HOME": home, "PATH": path}
    for key in ("USER", "LOGNAME", "LANG", "LC_ALL", "TMPDIR", "TMP", "TEMP", "SystemRoot", "COMSPEC", "USERPROFILE", "APPDATA", "LOCALAPPDATA"):
        if env.get(key):
            native[key] = env[key]
    for key in SELECTORS[provider]:
        if key in env:
            if not env[key] or not Path(env[key]).is_absolute():
                raise SourceError("Account unknown")
            native[key] = env[key]
    root = Path(home)
    profile = []
    if provider == "codex":
        root = Path(native.get("CODEX_HOME", str(root / ".codex")))
        if any("codex-fugu" in str(value).lower() for value in argv):
            raise SourceError("Limits unavailable")
        for index, value in enumerate(argv):
            if value in ("-p", "--profile"):
                if index + 1 >= len(argv):
                    raise SourceError("Account unknown")
                profile = [str(argv[index + 1])]
            elif str(value).startswith("--profile="):
                profile = [str(value).split("=", 1)[1]]
            if value in ("-c", "--config"):
                setting = str(argv[index + 1]) if index + 1 < len(argv) else ""
                if not re.match(r'^(model|model_reasoning_effort|approval_policy|sandbox_mode|service_tier)\s*=', setting):
                    raise SourceError("Account unknown")
            elif str(value).startswith("--config="):
                raise SourceError("Account unknown")
        if profile:
            raise SourceError("Limits unavailable" if profile == ["fugu"] else "Account unknown")
        config = root / "config.toml"
        if config.exists():
            try:
                import tomllib
                data = tomllib.loads(config.read_text(encoding="utf-8"))
            except (ImportError, ValueError):
                raise SourceError("Account unknown")
            providers = data.get("model_providers") or {}
            if not isinstance(providers, dict):
                raise SourceError("Account unknown")
            if (data.get("model_provider", "openai") != "openai"
                    or data.get("chatgpt_base_url")
                    or providers.get("openai")):
                raise SourceError("Limits unavailable")
        files = [root / "auth.json", config]
        native["CODEX_HOME"] = str(root)
    elif provider == "claude":
        root = Path(native.get("CLAUDE_CONFIG_DIR", str(root / ".claude")))
        if any(str(value).startswith(("--settings", "--setting-sources")) for value in argv):
            raise SourceError("Account unknown")
        files = [root / ".credentials.json", root / "settings.json", Path(home) / ".claude.json"]
        # Settings can inject API credentials into the CLI without a process env override.
        for config in (root / "settings.json", Path(process.get("cwd") or home) / ".claude" / "settings.json",
                       Path(process.get("cwd") or home) / ".claude" / "settings.local.json"):
            if config.exists():
                data = json.loads(config.read_text(encoding="utf-8"))
                if not isinstance(data, dict) or not isinstance(data.get("env", {}), dict):
                    raise SourceError("Account unknown")
                if data.get("apiKeyHelper") or any(data.get("env", {}).get(k) for k in OVERRIDES[provider]):
                    raise SourceError("Account unknown")
                files.append(config)
        # An explicit default path changes Claude's macOS keychain namespace.
        # Preserve an absent selector as absent.
    else:
        root = Path(native.get("GROK_HOME", str(root / ".grok")))
        if any(str(value).startswith(("--home", "--profile", "--api-key", "--base-url")) for value in argv):
            raise SourceError("Account unknown")
        files = [root / "auth.json", root / "settings.json"]
        native["GROK_HOME"] = str(root)
    scope = {"provider": provider, "env": native, "executable": str(Path(executable).absolute()),
             "files": [str(path) for path in files]}
    scope["stamp"] = current_stamp(scope)
    # Grok OAuth reads scoped credentials. It never invokes the agent binary.
    # A running old binary can remain valid after its updater removes the file.
    scope["key"] = runtime.digest([provider, str(root.resolve()), profile, scope["stamp"],
                                  native["HOME"], [(key, native.get(key)) for key in SELECTORS[provider]],
                                  executable if provider != "grok" else "oauth"])
    return scope


def number(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def epoch(value: object) -> float | None:
    result = number(value)
    if result is not None:
        return result if 0 < result < 253402300800 else None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.timestamp() if parsed.tzinfo else None
        except ValueError:
            pass
    return None


def window(data: object, label: str, key: str) -> dict | None:
    if not isinstance(data, dict):
        return None
    used = number(data.get("usedPercent"))
    if used is None or not 0 <= used <= 100:
        return None
    minutes = number(data.get("windowMinutes", data.get("windowDurationMins")))
    return {"id": field.clean(key), "label": field.clean(label), "remaining": 100 - used,
            "minutes": minutes, "reset_at": epoch(data.get("resetsAt")),
            "reset_text": field.clean(data.get("resetDescription"), 180)}


def cadence(minutes: object, fallback: str) -> str:
    return {300: "5 hours", 10080: "Weekly", 43200: "Monthly"}.get(number(minutes), fallback)


def parse_codex(account: dict, limits: dict, observed: float) -> dict:
    identity = limits.get("accountId")
    if account.get("type") != "chatgpt" or not isinstance(identity, str) or not identity.strip():
        raise SourceError("Account unknown")
    pools = dict(limits.get("rateLimitsByLimitId") or {})
    default = limits.get("rateLimits")
    if isinstance(default, dict) and default not in pools.values():
        pools.setdefault(default.get("limitId") or "default", default)
    windows, credits, plan = [], [], account.get("planType")
    for pool_id, pool in pools.items():
        if not isinstance(pool, dict):
            continue
        plan = pool.get("planType") or plan
        for position in ("primary", "secondary"):
            value = pool.get(position)
            if isinstance(value, dict):
                label = f"{pool.get('limitName') or pool_id} {cadence(value.get('windowDurationMins'), position)}"
                parsed = window(value, label, f"{pool_id}:{position}")
                if parsed:
                    windows.append(parsed)
        value = pool.get("credits") or {}
        balance = number(value.get("balance"))
        if value.get("unlimited"):
            credits.append({"label": f"{pool_id} credits", "unlimited": True})
        elif value.get("hasCredits") and balance is not None and balance >= 0:
            credits.append({"label": f"{pool_id} credits", "balance": balance, "unit": "credits"})
    if not windows:
        raise SourceError("Limits unavailable")
    return {"account": runtime.digest(["codex", identity]), "provider": "codex", "plan": field.clean(plan or "Plan unknown"),
            "windows": windows, "credits": credits, "observed_at": observed, "source": "account/rateLimits/read"}


def parse_codexbar(payload: object, provider: str, observed: float, auth: dict | None = None) -> dict:
    rows = payload if isinstance(payload, list) else [payload]
    candidates = [row for row in rows if isinstance(row, dict) and row.get("provider") == provider]
    if len(candidates) != 1:
        raise SourceError("Limits unavailable")
    row = candidates[0]
    if row.get("error"):
        error = row["error"] if isinstance(row["error"], dict) else {}
        message = str(error.get("message", "")).lower()
        retry = number(error.get("retryAfterSeconds")) or 0
        raise SourceError("Sign in required" if any(x in message for x in ("credentials not found", "sign in", "not authenticated", "expired", "not logged in")) else "Last update failed", max(0, retry))
    allowed = {"claude": "claude", "grok": "grok-web", "antigravity": "cli"}
    if row.get("source") != allowed.get(provider):
        raise SourceError("Limits unavailable")
    usage = row.get("usage") or {}
    if not isinstance(usage, dict):
        raise SourceError("Limits unavailable")
    identity = usage.get("identity") or {}
    if not isinstance(identity, dict):
        raise SourceError("Account unknown")
    email = identity.get("accountEmail") or usage.get("accountEmail")
    if not isinstance(email, str) or not email.strip():
        raise SourceError("Account unknown")
    organization = identity.get("accountOrganization") or usage.get("accountOrganization") or ""
    plan = identity.get("loginMethod") or usage.get("loginMethod") or "Plan unknown"
    if provider == "claude":
        if not auth or not auth.get("loggedIn") or auth.get("email", "").lower() != email.lower():
            raise SourceError("Account unknown")
        organization, plan = auth.get("orgId") or "", auth.get("subscriptionType") or plan
    labels = row.get("rateWindowLabels") or {}
    if not isinstance(labels, dict):
        raise SourceError("Limits unavailable")
    windows = []
    extras = usage.get("extraRateWindows") or []
    if not isinstance(extras, list) or any(not isinstance(extra, dict) for extra in extras):
        raise SourceError("Limits unavailable")
    # Antigravity's primary fields duplicate its complete grouped windows.
    if provider != "antigravity" or not extras:
        for position in ("primary", "secondary", "tertiary"):
            value = usage.get(position)
            if isinstance(value, dict):
                label = labels.get(position) or cadence(value.get("windowMinutes"), position)
                parsed = window(value, label, position)
                if parsed:
                    windows.append(parsed)
    for extra in extras:
        parsed = window(extra.get("window"), extra.get("title") or "Allowance", extra.get("id") or "extra")
        if parsed:
            windows.append(parsed)
    if not windows:
        raise SourceError("Limits unavailable")
    credits = []
    cost = usage.get("providerCost") or {}
    balance = number(cost.get("balance"))
    if balance is not None and balance >= 0:
        credits.append({"label": "Purchased balance", "balance": balance, "unit": field.clean(cost.get("currencyCode") or "credits")})
    return {"account": runtime.digest([provider, email.lower(), organization]), "provider": provider,
            "plan": field.clean(plan), "windows": windows, "credits": credits,
            "observed_at": epoch(usage.get("updatedAt")) or observed, "source": row["source"]}


def codex_read(scope: dict, deadline: float, cancel: threading.Event) -> dict:
    proc = runtime.spawn([scope["executable"], "app-server"], scope["env"])
    messages = queue.Queue(maxsize=256)
    failed = threading.Event()

    def drain() -> None:
        try:
            total = 0
            while True:
                line = proc.stdout.readline(1024 * 1024 + 1)
                if not line:
                    break
                total += len(line)
                if total > 1024 * 1024:
                    failed.set()
                    break
                messages.put_nowait(json.loads(line))
        except (ValueError, queue.Full, OSError):
            failed.set()

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()

    def send(message: dict) -> None:
        proc.stdin.write(json.dumps(message).encode() + b"\n")
        proc.stdin.flush()

    def request(identifier: int, method: str, params: dict) -> dict:
        send({"id": identifier, "method": method, "params": params})
        while time.monotonic() < deadline and not cancel.is_set():
            if failed.is_set():
                raise SourceError("Last update failed")
            try:
                data = messages.get(timeout=0.1)
            except queue.Empty:
                if proc.poll() is not None:
                    raise SourceError("Last update failed")
                continue
            if not isinstance(data, dict) or data.get("id") != identifier:
                continue
            if data.get("error") or not isinstance(data.get("result"), dict):
                raise SourceError("Sign in required" if method == "account/read" else "Last update failed")
            return data["result"]
        raise SourceError("Last update failed")

    try:
        request(1, "initialize", {"clientInfo": {"name": "lantern_usage", "version": "1.0"},
                                  "capabilities": {"experimentalApi": True}})
        send({"method": "initialized"})
        account = request(2, "account/read", {"refreshToken": False}).get("account")
        if account is None:
            raise SourceError("Sign in required")
        limits = request(3, "account/rateLimits/read", {})
        return parse_codex(account, limits, time.time())
    finally:
        runtime.stop_process(proc)
        reader.join(timeout=2)
        proc.stdin.close()
        proc.stdout.close()


def tools_dir(state: Path) -> Path:
    configured = os.environ.get("HERDR_PLUGIN_STATE_DIR")
    return (Path(configured) if configured else state.parent) / "tools" / "codexbar" / VERSION


def resolve_codexbar(state: Path, deadline: float, cancel: threading.Event) -> str:
    if os.name == "nt":
        raise SourceError("Dependency unavailable")
    explicit = os.environ.get("LANTERN_CODEXBAR_BIN")
    candidates = [explicit] if explicit is not None else [str(tools_dir(state) / "CodexBarCLI"), shutil.which("codexbar")]
    for candidate in candidates:
        if not candidate or not Path(candidate).is_absolute() or not os.access(candidate, os.X_OK):
            continue
        code, output = runtime.capture([candidate, "--version"], {"PATH": os.environ.get("PATH", "")},
                                       min(deadline, time.monotonic() + 3), cancel)
        if code == 0 and re.search(rb'\bCodexBar 0\.74\.0\b', output):
            return candidate
    raise SourceError("Dependency unavailable")


def auth_status(scope: dict, deadline: float, cancel: threading.Event) -> dict:
    code, output = runtime.capture([scope["executable"], "auth", "status", "--json"], scope["env"],
                                   min(deadline, time.monotonic() + 10), cancel)
    data = json.loads(output)
    if code or not isinstance(data, dict) or not data.get("loggedIn"):
        raise SourceError("Sign in required")
    if not isinstance(data.get("email"), str) or not data["email"].strip():
        raise SourceError("Account unknown")
    return {key: data.get(key) for key in ("loggedIn", "email", "orgId", "subscriptionType")}


def fetch(scope: dict, state: Path, cancel: threading.Event) -> dict:
    if scope.get("owner_fd") is not None:
        scope = dict(scope, env=dict(scope["env"], LANTERN_USAGE_LOCK_FD=str(scope["owner_fd"])))
    provider = scope["provider"]
    if provider not in SUPPORTED:
        raise SourceError("Limits unavailable")
    deadline = time.monotonic() + 100
    try:
        if current_stamp(scope) != scope["stamp"]:
            raise SourceError("Account unknown")
        if provider == "codex":
            result = codex_read(scope, deadline, cancel)
        else:
            tool = resolve_codexbar(state, deadline, cancel)
            config = state / "usage-provider-config.json"
            # Config is generated by the collector, never copied from user config.
            env = dict(scope["env"], CODEXBAR_CONFIG=str(config))
            if provider == "claude":
                env["CLAUDE_CLI_PATH"] = scope["executable"]
            before = auth_status(scope, deadline, cancel) if provider == "claude" else None
            code, output = runtime.capture([tool, "usage", "--provider", provider, "--source",
                                            "cli" if provider == "claude" else "oauth", "--format", "json"],
                                           env, deadline, cancel)
            after = auth_status(scope, deadline, cancel) if provider == "claude" else None
            if before != after:
                raise SourceError("Account unknown")
            result = parse_codexbar(json.loads(output), provider, time.time(), before)
            if code:
                raise SourceError("Last update failed")
        if current_stamp(scope) != scope["stamp"]:
            raise SourceError("Account unknown")
        return result
    except SourceError:
        raise
    except (OSError, ValueError, RuntimeError, TypeError, KeyError, AttributeError):
        raise SourceError("Last update failed") from None
