"""Read plan limits from the login profile of an open agent. No model turns."""
from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import queue
import re
import shlex
import shutil
import stat
import subprocess
import tarfile
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import field_status as field
import usage_runtime as runtime

VERSION = "0.74.0"
# Plan Usage notes say what is missing and what to do about it.
CODEXBAR_MISSING = f"CodexBar {VERSION} not found. Ask Lantern to install it"
CODEXBAR_SETTING = f"LANTERN_CODEXBAR_BIN is not CodexBar {VERSION}"
CODEXBAR_WINDOWS = "CodexBar has no Windows build"
CODEXBAR_PLATFORM = "CodexBar has no build for this system"
DEPENDENCY_STATES = (CODEXBAR_MISSING, CODEXBAR_SETTING, CODEXBAR_WINDOWS, CODEXBAR_PLATFORM)
FUGU_KEY_MISSING = "Sakana API key missing"
NEEDS_PYTHON = "Needs Python 3.11 or newer"
AUTO_INSTALL_LOCK = threading.Lock()
AUTO_INSTALL_TRIED: set[str] = set()
SUPPORTED = {"codex", "claude", "grok", "agy", "fugu"}
SELECTORS = {"codex": ["CODEX_HOME"], "claude": ["CLAUDE_CONFIG_DIR"], "grok": ["GROK_HOME"], "agy": []}
OVERRIDES = {
    "codex": ["OPENAI_API_KEY", "OPENAI_BASE_URL", "CODEX_API_KEY"],
    "claude": ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
               "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
               "CLAUDE_CODE_USE_FOUNDRY"],
    # API keys do not select the saved Grok subscription login. Read its OAuth
    # quota even when an agent has an API key. Never pass that key to the probe.
    "grok": ["GROK_OAUTH_TOKEN"],
    "agy": ["AGY_HOME", "ANTIGRAVITY_HOME", "GEMINI_CLI_HOME"],
}


class SourceError(RuntimeError):
    def __init__(self, state: str, retry_after: float = 0, provider: str | None = None):
        super().__init__(state)
        self.state = state
        self.retry_after = retry_after
        self.provider = provider


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


def route_hint(provider: str, argv: object) -> str:
    if provider != "codex" or not isinstance(argv, list):
        return provider
    profile = ""
    for index, value in enumerate(argv):
        if value in ("-p", "--profile") and index + 1 < len(argv):
            profile = str(argv[index + 1])
        elif str(value).startswith("--profile="):
            profile = str(value).split("=", 1)[1]
    if profile == "fugu" or profile.startswith("fugu-") or (argv and "codex-fugu" in Path(str(argv[0])).name):
        return "fugu"
    return provider


def scope_for(provider: str, process: dict, env: dict[str, str]) -> dict:
    hint = route_hint(provider, process.get("argv"))
    try:
        return _scope_for(provider, process, env)
    except SourceError as exc:
        if hint == "fugu":
            exc.provider = "fugu"
        raise
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise SourceError("Account unknown", provider=hint) from None


def codex_settings(root: Path, profile: list[str], optional: bool = False) -> tuple | None:
    config = root / "config.toml"
    files = [root / "auth.json", config]
    if not config.exists() and not (profile and (root / f"{profile[0]}.config.toml").is_file()):
        if optional:
            return None
        if profile:
            raise SourceError("Limits unavailable" if profile == ["fugu"] else "Account unknown")
        return {}, {}, {}, files
    try:
        import tomllib
    except ImportError:
        # Codex settings are TOML, which Python reads from 3.11 on. Without
        # them a configured Fugu profile is only known from its own file.
        if optional and not (profile and (root / f"{profile[0]}.config.toml").is_file()):
            return None
        raise SourceError(NEEDS_PYTHON) from None
    try:
        data = tomllib.loads(config.read_text(encoding="utf-8")) if config.exists() else {}
    except ValueError:
        if optional and not (root / "fugu.config.toml").exists():
            return None
        raise
    selected = data
    extra = {}
    if profile:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", profile[0]):
            raise SourceError("Account unknown")
        profiles = data.get("profiles") or {}
        modern = root / f"{profile[0]}.config.toml"
        if optional and not modern.is_file() and (not isinstance(profiles, dict)
                                                  or not isinstance(profiles.get(profile[0]), dict)):
            return None
        if not isinstance(profiles, dict):
            raise SourceError("Account unknown")
        if modern.is_file():
            profile_data = tomllib.loads(modern.read_text(encoding="utf-8"))
            files.append(modern)
        elif isinstance(profiles.get(profile[0]), dict):
            profile_data = profiles[profile[0]]
        elif optional:
            return None
        else:
            raise SourceError("Limits unavailable" if profile == ["fugu"] else "Account unknown")
        selected = dict(data, **profile_data)
        extra = profile_data.get("model_providers") or {}
        if not isinstance(extra, dict):
            raise SourceError("Account unknown")
    providers = data.get("model_providers") or {}
    if not isinstance(providers, dict):
        raise SourceError("Account unknown")
    providers = dict(providers, **extra)
    return data, selected, providers, files


def stored_fugu_key(path: Path, key_name: str) -> str:
    """Read one key from the installer's private dotenv store. Never source it."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except FileNotFoundError:
        return ""
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or path.is_symlink() or info.st_size > 65536
                or os.name != "nt" and (info.st_uid != os.geteuid() or info.st_mode & 0o077)):
            raise SourceError("Account unknown", provider="fugu")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(65537)
        if len(raw) > 65536:
            raise SourceError("Account unknown", provider="fugu")
        credential = ""
        for line in raw.decode("utf-8").splitlines():
            match = re.fullmatch(r"\s*(?:export\s+)?" + re.escape(key_name) + r"\s*=\s*(.*)", line)
            if match:
                values = shlex.split(match[1], comments=True)
                if len(values) > 1:
                    raise SourceError("Account unknown", provider="fugu")
                credential = values[0] if values else ""
        return credential
    finally:
        os.close(fd)


def configured_fugu_scope(env: dict[str, str]) -> dict | None:
    """Keep the local Fugu plan visible when short review commands have exited."""
    home = env.get("HOME") or (env.get("USERPROFILE", "") if os.name == "nt" else "")
    root_value = env.get("CODEX_HOME", str(Path(home) / ".codex"))
    if not home or not Path(home).is_absolute() or not root_value or not Path(root_value).is_absolute():
        return None
    root = Path(root_value)
    try:
        before = {path: stamp(path) for path in (root / "auth.json", root / "config.toml",
                                                root / "fugu.config.toml", root / ".env")}
        settings = codex_settings(root, ["fugu"], optional=True)
        if settings is None:
            return None
        _, selected, providers, files = settings
        provider = providers.get(selected.get("model_provider", "openai")) or {}
        if not isinstance(provider, dict) or provider.get("base_url", "").rstrip("/") != "https://api.sakana.ai/v1":
            raise SourceError("Limits unavailable", provider="fugu")
        key_name = provider.get("env_key")
        if not isinstance(key_name, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key_name):
            raise SourceError("Account unknown", provider="fugu")
        credentials = {key_name: env.get(key_name, "")}
        if not credentials[key_name]:
            store = root / ".env"
            credentials[key_name] = stored_fugu_key(store, key_name)
            files.append(store)
        scope = fugu_scope({"HOME": home, "CODEX_HOME": str(root)}, credentials, root, files, provider)
        if scope["stamp"] != runtime.digest([before[path] for path in files]):
            raise SourceError("Account unknown", provider="fugu")
        return scope
    except SourceError as exc:
        exc.provider = "fugu"
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError, ImportError):
        raise SourceError("Account unknown", provider="fugu") from None


def configured_codex_scope(env: dict[str, str]) -> dict | None:
    """Keep this computer's signed-in Codex plan visible without a Codex tab.

    Lantern runs Codex reviews as short commands, like Fugu. Nothing shows when
    Codex isn't installed or signed in here, or when its default model provider
    isn't an OpenAI plan. A Codex tab on the same account shares this row.
    """
    home = env.get("HOME", "")
    root_value = env.get("CODEX_HOME") or str(Path(home) / ".codex")
    if not home or not Path(home).is_absolute() or not Path(root_value).is_absolute():
        return None
    if not (Path(root_value) / "auth.json").is_file():
        return None
    search = os.pathsep.join(p for p in env.get("PATH", "").split(os.pathsep) if p and Path(p).is_absolute())
    found = shutil.which("codex", path=search) if search else None
    if not found:
        return None
    # A tab's scope names the program's real path, so an open tab on this
    # login resolves to the same binding.
    executable = os.path.realpath(found)
    if not re.fullmatch(r"codex(?:\.exe)?", Path(executable).name.lower()):
        return None
    # API keys don't select the saved login, and the reader never receives them.
    login = {key: value for key, value in env.items() if key not in OVERRIDES["codex"]}
    try:
        scope = scope_for("codex", {"argv": [executable], "executable": executable}, login)
    except SourceError as exc:
        # A default Fugu profile is configured_fugu_scope's row, errors included.
        if exc.state == "Limits unavailable" or exc.provider == "fugu":
            return None
        raise
    return scope if scope["provider"] == "codex" else None


def native_claude(executable: str) -> bool:
    """Claude's native installer runs claude/versions/<version> through a claude symlink."""
    path = Path(executable)
    return (path.parent.name == "versions" and path.parent.parent.name == "claude"
            and re.fullmatch(r"\d+\.\d+\.\d+", path.name) is not None)


def current_native_claude(home: str, deleted: str) -> str:
    """The version Claude's launcher starts now, for a tab whose version was deleted.

    Claude's updater removes old versions while tabs still run them. Lantern
    runs the tab's Claude to read usage, so it uses the version the launcher
    link in the tab's home points to, and only from the same versions folder.
    """
    try:
        current = (Path(home) / ".local" / "bin" / "claude").resolve(strict=True)
        folder = Path(deleted).parent.resolve(strict=True)
    except (OSError, RuntimeError):
        raise SourceError("Account unknown") from None
    if current.parent != folder or not native_claude(str(current)):
        raise SourceError("Account unknown")
    return str(current)


def _scope_for(provider: str, process: dict, env: dict[str, str]) -> dict:
    home = env.get("HOME", "")
    argv = process.get("argv")
    if not home or not Path(home).is_absolute() or not isinstance(argv, list) or not argv:
        raise SourceError("Account unknown")
    if provider == "codex" and any(str(value).startswith(("--oss", "--local-provider")) for value in argv):
        raise SourceError("Limits unavailable")
    if provider != "codex" and any(env.get(key) for key in OVERRIDES[provider]):
        raise SourceError("Account unknown")
    # Keep only absolute executable search paths. Never use a shell alias.
    path = os.pathsep.join(p for p in env.get("PATH", "").split(os.pathsep) if Path(p).is_absolute() and p)
    executable = process.get("executable")
    if (provider == "claude" and executable and Path(executable).is_absolute()
            and native_claude(executable) and not os.path.exists(executable)):
        executable = current_native_claude(home, executable)
    if (not executable or not Path(executable).is_absolute()
            or (provider != "grok" and not os.access(executable, os.X_OK))):
        raise SourceError("Account unknown")
    basename = Path(executable).name.lower()
    expected = {"codex": r"codex(?:\.exe)?", "claude": r"claude(?:\.exe)?", "grok": r"grok(?:[-.].*)?",
                "agy": r"agy(?:\.exe)?"}
    if not re.fullmatch(expected[provider], basename) and not (provider == "claude" and native_claude(executable)):
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
        try:
            data, selected, providers, files = codex_settings(root, profile)
        except (ImportError, ValueError):
            raise SourceError("Account unknown") from None
        provider_config = providers.get(selected.get("model_provider", "openai")) or {}
        if not isinstance(provider_config, dict):
            raise SourceError("Account unknown")
        base_url = provider_config.get("base_url")
        if isinstance(base_url, str) and base_url.rstrip("/") == "https://api.sakana.ai/v1":
            return fugu_scope(native, env, root, files, provider_config)
        if (data.get("model_provider", "openai") != "openai"
                or data.get("chatgpt_base_url")
                or providers.get("openai")):
            raise SourceError("Limits unavailable")
        if profile or any("codex-fugu" in str(value).lower() for value in argv):
            raise SourceError("Limits unavailable" if profile == ["fugu"] else "Account unknown")
        if any(env.get(key) for key in OVERRIDES[provider]):
            raise SourceError("Account unknown")
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
    elif provider == "agy":
        root = root / ".gemini" / "antigravity-cli"
        files = [root / "antigravity-oauth-token", root / "settings.json", Path(home) / ".gemini" / "settings.json"]
        if not files[0].is_file() or not files[0].stat().st_size:
            raise SourceError("Sign in required")
    else:
        root = Path(native.get("GROK_HOME", str(root / ".grok")))
        if any(str(value).startswith(("--home", "--profile")) for value in argv):
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


def fugu_scope(native: dict, env: dict, root: Path, files: list[Path], provider_config: dict) -> dict:
    key_name = provider_config.get("env_key")
    if not isinstance(key_name, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key_name):
        raise SourceError("Account unknown", provider="fugu")
    credential = env.get(key_name)
    if not credential:
        raise SourceError(FUGU_KEY_MISSING, provider="fugu")
    scope = {"provider": "fugu", "env": native, "api_key": credential,
             "files": [str(path) for path in files]}
    scope["stamp"] = current_stamp(scope)
    scope["key"] = runtime.digest(["fugu", str(root.resolve()), scope["stamp"], credential])
    return scope


def parse_fugu(payload: object, credential: str, observed: float) -> dict:
    if not isinstance(payload, dict):
        raise SourceError("Limits unavailable")
    windows = []
    for key, label, minutes in (("window_usage", "Session", number(payload.get("window_seconds"))),
                               ("weekly_usage", "Weekly", 10080 * 60)):
        raw = payload.get(key)
        if not isinstance(raw, dict):
            continue
        parsed = window({"usedPercent": raw.get("usage_percent"),
                         "windowMinutes": minutes / 60 if minutes else None,
                         "resetsAt": raw.get("reset_at")}, label, key)
        if parsed:
            parsed["remaining"] = round(parsed["remaining"], 1)
            windows.append(parsed)
    if not windows:
        raise SourceError("Limits unavailable")
    return {"account": runtime.digest(["fugu", credential]), "provider": "fugu", "windows": windows,
            "credits": [], "observed_at": observed, "source": "Sakana /v1/usage"}


def fugu_read(scope: dict, deadline: float, cancel: threading.Event) -> dict:
    if cancel.is_set() or time.monotonic() >= deadline:
        raise SourceError("Last update failed")
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(),
                                         urllib.request.HTTPSHandler(context=runtime.tls_context()))
    request = urllib.request.Request("https://api.sakana.ai/v1/usage",
                                     headers={"Authorization": "Bearer " + scope["api_key"],
                                              "Accept": "application/json"})
    try:
        with opener.open(request, timeout=min(10, max(.1, deadline - time.monotonic()))) as response:
            raw = response.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024 or cancel.is_set():
            raise SourceError("Last update failed")
        return parse_fugu(json.loads(raw), scope["api_key"], time.time())
    except urllib.error.HTTPError as exc:
        raise SourceError("Sign in required" if exc.code in (401, 403) else "Last update failed") from None
    except (urllib.error.URLError, TimeoutError):
        raise SourceError("Last update failed") from None


def parse_agy(payload: object, scope: dict, observed: float) -> dict:
    if not isinstance(payload, dict) or payload.get("status") != "SUCCESS":
        raise SourceError("Last update failed")
    command = payload.get("command") or {}
    if (type(payload.get("num_turns")) is not int or payload["num_turns"] != 0
            or not isinstance(command, dict) or command.get("name") != "usage"):
        raise SourceError("Limits unavailable")
    data = command.get("data") or {}
    groups = data.get("groups") if isinstance(data, dict) else None
    if not isinstance(groups, list):
        raise SourceError("Limits unavailable")
    windows = []
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get("buckets"), list):
            raise SourceError("Limits unavailable")
        for bucket in group["buckets"]:
            if not isinstance(bucket, dict):
                raise SourceError("Limits unavailable")
            remaining = number(bucket.get("remaining_fraction"))
            minutes = {"weekly": 10080, "5h": 300}.get(bucket.get("window"))
            if remaining is None or not 0 <= remaining <= 1 or minutes is None:
                continue
            windows.append({"id": field.clean(bucket.get("id") or "quota"),
                            "label": "Weekly" if minutes == 10080 else "Session",
                            "remaining": round(remaining * 100, 1), "minutes": minutes,
                            "reset_at": epoch(bucket.get("reset_time"))})
    if not windows:
        raise SourceError("Limits unavailable")
    return {"account": runtime.digest(["agy", "login-profile", scope["env"]["HOME"]]),
            "provider": "agy", "windows": windows, "credits": [], "observed_at": observed,
            "source": "agy /usage"}


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


def parse_reset_credits(summary: object) -> dict | None:
    """Codex's saved usage resets: how many, and when the oldest expires.

    The credit list can be left out or capped, so the oldest expiry is known
    only when every available credit is listed. Never spends a reset.
    """
    if not isinstance(summary, dict):
        return None
    count = summary.get("availableCount")
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        return None
    listed = summary.get("credits")
    available = ([credit for credit in listed if isinstance(credit, dict) and credit.get("status") == "available"]
                 if isinstance(listed, list) else [])
    known = len(available) >= count
    raw = [credit.get("expiresAt") for credit in available] if known else []
    dated = [epoch(value) for value in raw if value is not None]
    if None in dated:
        known, dated = False, []
    return {"count": count, "expiry_known": known, "expires_at": min(dated) if dated else None}


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
    result = {"account": runtime.digest(["codex", identity]), "provider": "codex", "plan": field.clean(plan or "Plan unknown"),
              "windows": windows, "credits": credits, "observed_at": observed, "source": "account/rateLimits/read"}
    resets = parse_reset_credits(limits.get("rateLimitResetCredits"))
    if resets is not None:
        result["resets"] = resets
    return result


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
    allowed = {"claude": {"claude"}, "grok": {"grok-web", "grok-cli-proxy"},
               "antigravity": {"cli"}}
    if row.get("source") not in allowed.get(provider, set()):
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
                    if provider == "claude":
                        parsed["scope"] = "model" if position == "tertiary" else "service"
                    windows.append(parsed)
    for extra in extras:
        parsed = window(extra.get("window"), extra.get("title") or "Allowance", extra.get("id") or "extra")
        if parsed:
            if provider == "claude":
                parsed["scope"] = "model"
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
        raise SourceError(CODEXBAR_WINDOWS)
    explicit = os.environ.get("LANTERN_CODEXBAR_BIN")
    candidates = [explicit] if explicit is not None else [str(tools_dir(state) / "CodexBarCLI"), shutil.which("codexbar")]
    for candidate in candidates:
        if usable_codexbar(candidate, deadline, cancel):
            return candidate
    if explicit is not None:
        raise SourceError(CODEXBAR_SETTING)
    codexbar_asset()
    return auto_install(state, deadline, cancel)


def usable_codexbar(candidate: str | None, deadline: float, cancel: threading.Event) -> bool:
    if not candidate or not Path(candidate).is_absolute() or not os.access(candidate, os.X_OK):
        return False
    code, output = runtime.capture([candidate, "--version"], {"PATH": os.environ.get("PATH", "")},
                                   min(deadline, time.monotonic() + 3), cancel)
    return code == 0 and re.search(rb'\bCodexBar 0\.74\.0\b', output) is not None


def auto_install(state: Path, deadline: float, cancel: threading.Event) -> str:
    """Install the pinned CodexBar once per run, the way `plan-usage install` does.

    A new machine has no CodexBar yet. One read installs it while the others
    wait and then use it. After a failed try the note asks for the explicit
    install, which shows the error.
    """
    tool = str(tools_dir(state) / "CodexBarCLI")
    with AUTO_INSTALL_LOCK:
        # A read that waited while the watcher stopped starts no download.
        if cancel.is_set():
            raise SourceError(CODEXBAR_MISSING)
        if usable_codexbar(tool, deadline, cancel):
            return tool
        if tool in AUTO_INSTALL_TRIED:
            raise SourceError(CODEXBAR_MISSING)
        AUTO_INSTALL_TRIED.add(tool)
        try:
            return str(install(state, cancel))
        except Exception:
            # Any failure leaves CodexBar missing. A stopped watcher's try doesn't count.
            if cancel.is_set():
                AUTO_INSTALL_TRIED.discard(tool)
            raise SourceError(CODEXBAR_MISSING) from None


ASSETS = {
    "macos-arm64": "9aab1b9229f5d1d77de21b99aa887a60a47921bfd9b66e4b941a0e8a9d6e38e8",
    "macos-x86_64": "4d239b7412683f1eb529d55b851fe922bb462527850b767d6c9b89ba14be3fdb",
    "linux-aarch64": "46dc3b259b1df48b3fd6f337c754dd60dbece6772d7840cdc9514ab4977a0007",
    "linux-x86_64": "fac8b2c474e4e37a2b067224437cb182693411ebf639441cd0facbb57bf086b1",
    "linux-musl-aarch64": "9dab39f1197d9ccc501d1f678234cf15f94e8b84aa7349ba87103a5866dd793b",
    "linux-musl-x86_64": "e7b0bdc6d6931a4e6fd5ce4037e1125a073ec6cf38cd72f1b3bd8aeecfde51f5",
}


def codexbar_asset() -> str:
    """The CodexBar release asset for this system."""
    system, machine = platform.system(), platform.machine().lower()
    arch = {"arm64": "aarch64", "aarch64": "aarch64", "amd64": "x86_64", "x86_64": "x86_64"}.get(machine)
    if system == "Darwin" and arch:
        return "macos-" + ("arm64" if arch == "aarch64" else arch)
    if system == "Linux" and arch:
        musl = bool(list(Path("/lib").glob("ld-musl-*.so.1"))) or platform.libc_ver()[0] == "musl"
        return ("linux-musl-" if musl else "linux-") + arch
    raise SourceError(CODEXBAR_PLATFORM)


def install(state: Path, cancel: threading.Event | None = None) -> Path:
    """Install the pinned CodexBar CLI into private Lantern state and return its path."""
    asset = codexbar_asset()
    target = tools_dir(state)
    runtime.private_dir(target.parent)
    lock = runtime.FileLock(target.parent / "install.lock")
    try:
        if not lock.acquire():
            raise RuntimeError("CodexBar install is in progress")
        if target.exists():
            if target.is_symlink():
                raise RuntimeError("CodexBar install path is a symlink")
            tool = target / "CodexBarCLI"
            code, output = runtime.capture([str(tool), "--version"], {"PATH": os.environ.get("PATH", "")},
                                           time.monotonic() + 3)
            if code == 0 and b"CodexBar 0.74.0" in output:
                return tool
            raise RuntimeError("Private CodexBar install has the wrong version")
        with tempfile.TemporaryDirectory(prefix=".install-", dir=target.parent) as temporary:
            root = Path(temporary)
            archive = root / "release.tar.gz"
            url = f"https://github.com/steipete/CodexBar/releases/download/v{VERSION}/CodexBarCLI-v{VERSION}-{asset}.tar.gz"
            digest = hashlib.sha256()
            with urllib.request.urlopen(url, timeout=30, context=runtime.tls_context()) as response, archive.open("wb") as stream:
                total = 0
                while chunk := response.read(65536):
                    if cancel and cancel.is_set():
                        raise RuntimeError("CodexBar install was stopped")
                    total += len(chunk)
                    if total > 100 * 1024 * 1024:
                        raise RuntimeError("CodexBar release is too large")
                    digest.update(chunk)
                    stream.write(chunk)
            if digest.hexdigest() != ASSETS[asset]:
                raise RuntimeError("CodexBar release digest mismatch")
            unpacked = runtime.private_dir(root / "files")
            with tarfile.open(archive) as tar:
                total = 0
                for member in tar.getmembers():
                    path = unpacked / member.name
                    if not path.resolve().is_relative_to(unpacked.resolve()):
                        raise RuntimeError("CodexBar archive path is invalid")
                    if member.isdir():
                        path.mkdir(parents=True, exist_ok=True)
                    elif member.isfile():
                        total += member.size
                        if total > 200 * 1024 * 1024:
                            raise RuntimeError("CodexBar archive is too large")
                        path.parent.mkdir(parents=True, exist_ok=True)
                        with tar.extractfile(member) as source, path.open("wb") as output:
                            shutil.copyfileobj(source, output)
                        path.chmod(0o700 if member.mode & 0o111 else 0o600)
                    elif not member.issym() and not member.islnk():
                        raise RuntimeError("CodexBar archive entry is invalid")
            cli = unpacked / "CodexBarCLI"
            code, output = runtime.capture([str(cli), "--version"], {"PATH": os.environ.get("PATH", "")}, time.monotonic() + 3)
            if code or b"CodexBar 0.74.0" not in output:
                raise RuntimeError("CodexBar release version mismatch")
            os.replace(unpacked, target)
            return target / "CodexBarCLI"
    finally:
        lock.close()


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
        elif provider == "fugu":
            result = fugu_read(scope, deadline, cancel)
        elif provider == "agy":
            code, output = runtime.capture([scope["executable"], "-p", "/usage", "--output-format", "json"],
                                           scope["env"], min(deadline, time.monotonic() + 20), cancel,
                                           cwd=scope["env"]["HOME"])
            if code:
                raise SourceError("Last update failed")
            result = parse_agy(json.loads(output), scope, time.time())
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
    # Stopping a probe can time out on a busy machine. That is a failed read,
    # not a reason for the watcher to exit.
    except (OSError, ValueError, RuntimeError, TypeError, KeyError, AttributeError, subprocess.SubprocessError):
        raise SourceError("Last update failed") from None
