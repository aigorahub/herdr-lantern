"""Shared parsing for model routes and availability checks."""
from __future__ import annotations

import json
import os
import re
import tempfile


def listed_codex_models(text: str) -> list[dict[str, object]]:
    try:
        catalog = json.loads(text)
        rows = catalog["models"]
        if not isinstance(rows, list):
            raise ValueError("models is not a list")
        models = []
        for model in rows:
            if not isinstance(model, dict):
                raise ValueError("model is not an object")
            if model.get("visibility") != "list":
                continue
            if not isinstance(model.get("slug"), str) or not model["slug"]:
                raise ValueError("model has no slug")
            for field, key in (("supported_reasoning_levels", "effort"), ("service_tiers", "id")):
                entries = model.get(field, [])
                if not isinstance(entries, list) or any(
                    not isinstance(entry, dict) or not isinstance(entry.get(key), str)
                    for entry in entries
                ):
                    raise ValueError(f"model has invalid {field}")
            if not isinstance(model.get("additional_speed_tiers", []), list):
                raise ValueError("model has invalid speed tiers")
            models.append(model)
        return models
    except (ValueError, KeyError, TypeError) as error:
        raise ValueError(f"Codex returned an unparseable catalog ({error})") from error


_TERMINAL_CONTROL = re.compile(
    r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"       # OSC ... BEL or ST
    r"|\x1b[@-Z\\\]^_]"                          # two-byte Fe escapes
    r"|\x1b\[[0-9:;<=>?]*[ -/]*[@-~]"           # CSI sequences (colors, bold, etc.)
    r"|[\x00-\x08\x0b-\x1f\x7f]"                 # stray C0/DEL control bytes
)
_TERMINAL_BADGE = re.compile(r"(?:\[[0-9]+[a-zA-Z]+\])+$")


def strip_terminal_control(value: str) -> str:
    """Remove ANSI escape sequences and stray control bytes from a model identity."""
    return _TERMINAL_CONTROL.sub("", value)


def canonical_model_identity(value: str) -> str:
    """Drop a trailing terminal-style badge (e.g. "[1m]" for 1M context) from a
    model identity. The badge is display decoration, not part of the identity, so
    lookups by the bare name must still find the model it decorates."""
    return _TERMINAL_BADGE.sub("", strip_terminal_control(value))


def model_words(value: str) -> list[str]:
    # Keep integer generations. Normalize dotted and hyphenated versions alike.
    value = re.sub(r"(?<=\d)-(?=\d)", ".", value.lower())
    return re.findall(r"\d+(?:\.\d+)*|[a-z][a-z0-9]*", value)


class TruncatedHelpError(ValueError):
    """Claude help ended before a required option; usually a cut-off capture."""


def parse_claude_capabilities(help_text: str) -> tuple[set[str], set[str]]:
    def option(name: str, argument: str) -> str:
        match = re.search(
            rf"^[ \t]*--{name}\s+<{argument}>.*?(?=^[ \t]*(?:-[a-zA-Z],\s*)?--[a-zA-Z]|\Z)",
            help_text,
            re.MULTILINE | re.DOTALL,
        )
        if match is None:
            raise TruncatedHelpError(f"Claude help has no {name} choices")
        return match.group(0)

    models = {value.lower() for value in re.findall(r"['\"]([a-zA-Z0-9.-]+)['\"]", option("model", "model"))}
    choices = re.search(r"\((low(?:\s*,\s*(?:medium|high|xhigh|max))+?)\)", option("effort", "level"))
    efforts = set(re.findall(r"low|medium|high|xhigh|max", choices.group(1))) if choices else set()
    if not models or not efforts:
        raise ValueError("Claude returned unparseable model or effort choices")
    return models, efforts


CLAUDE_HELP_ATTEMPTS = 3


def capture_to_file(invoke) -> str:
    fd, path = tempfile.mkstemp(prefix="lantern-stdout-", suffix=".txt")
    try:
        handle = os.fdopen(fd, "wb")
        fd = -1
        with handle:
            invoke(handle)
        with open(path, encoding="utf-8", errors="replace") as reader:
            return reader.read()
    finally:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            os.unlink(path)
        except OSError:
            # On Windows a timed-out child may still hold the file open, so
            # unlink can fail; ignore that deliberately.
            pass


def claude_model_catalog(run) -> dict[str, tuple[str, set[str]]]:
    return claude_initialization(run)[0]


def claude_initialization(run) -> tuple[dict[str, tuple[str, set[str]]], str | None]:
    # Help supplies grammar and alias examples, not a model allowlist.
    # Claude Code can exit before a pipe drains --help, so capture via a
    # regular file and retry truncated output.
    aliases: set[str] = set()
    for attempt in range(CLAUDE_HELP_ATTEMPTS):
        help_text = run(["claude", "--help"], stdout_via_file=True)
        try:
            aliases, _ = parse_claude_capabilities(help_text)
            break
        except TruncatedHelpError:
            if attempt + 1 == CLAUDE_HELP_ATTEMPTS:
                raise
    request_id = "lantern-model-catalog"
    request = {"type": "control_request", "request_id": request_id,
               "request": {"subtype": "initialize"}}
    output = run(
        ["claude", "--safe-mode", "--print", "--input-format", "stream-json",
         "--output-format", "stream-json", "--verbose", "--no-session-persistence"],
        input_text=json.dumps(request) + "\n",
    )
    # This is the SDK initialization exchange. No user message or model turn.
    try:
        responses = [json.loads(line) for line in output.splitlines() if line.strip()]
        response = next(
            row["response"] for row in responses
            if isinstance(row, dict) and row.get("type") == "control_response"
            and isinstance(row.get("response"), dict)
            and row["response"].get("request_id") == request_id
        )
        if response.get("subtype") != "success":
            raise ValueError("initialization failed")
        rows = response["response"]["models"]
        if not isinstance(rows, list) or not rows:
            raise ValueError("models is not a nonempty list")
        catalog = {}
        for row in rows:
            value, model = row["value"], row["resolvedModel"]
            if isinstance(value, str):
                value = strip_terminal_control(value)
            if isinstance(model, str):
                model = strip_terminal_control(model)
            levels = row.get("supportedEffortLevels", [])
            if (not isinstance(value, str) or not value or not isinstance(model, str) or not model
                    or not isinstance(levels, list) or any(not isinstance(level, str) for level in levels)):
                raise ValueError("invalid model identity or effort levels")
            # Pin every lookup to resolvedModel. A badged value such as
            # opus[1m] is a picker label for that same resolved id. The bare
            # name is registered too, and it does not replace an explicit row.
            entry = (model, set(levels))
            catalog[model] = entry
            catalog[value] = entry
            for identity in (model, value):
                canonical = canonical_model_identity(identity)
                if canonical and canonical != identity:
                    catalog.setdefault(canonical, entry)
            display_name = row.get("displayName", "")
            alias = strip_terminal_control(display_name).lower() if isinstance(display_name, str) else ""
            if alias in aliases:
                catalog[alias] = entry
        account = response["response"].get("account")
        token_source = account.get("tokenSource") if isinstance(account, dict) else None
        token_source = token_source.strip() if isinstance(token_source, str) else None
        return catalog, token_source or None
    except (ValueError, KeyError, TypeError, AttributeError, StopIteration) as error:
        raise ValueError(f"Claude returned an unparseable initialization catalog ({error})") from error


# Reviewed 2026-10-28. A bounded lifecycle policy, not an account allowlist.
# Cloud retirement dates do not establish Developer API retirement.
GEMINI_POLICY_DATE = "2026-10-28"
GEMINI_CURRENT = {
    "gemini-3.1-pro-preview": {"low", "medium", "high"},
    "gemini-3.8-flash": {"low", "medium", "high"},
    "gemini-3.5-flash": {"minimal", "low", "medium", "high"},
    "gemini-3-flash-preview": {"minimal", "low", "medium", "high"},
    "gemini-3.5-flash-lite": {"minimal", "low", "medium", "high"},
    "gemini-3.1-flash-lite": {"minimal", "low", "medium", "high"},
}
GEMINI_BLOCKED = {
    "gemini-3-pro-preview": "original preview shut down 2026-03-09; alias behavior is not portable",
    **{name: "Cloud retirement was 2026-10-20; choose an explicit current ID"
       for name in ("gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.5-flash-lite")},
    **{name: "Developer API ID documented; Cloud route compatibility is unverified"
       for name in ("gemma-4-31b-it", "gemma-4-26b-a4b-it")},
}


def gemini_package_catalog() -> dict[str, object]:
    """Read package metadata and JS constants only; never start Gemini or read auth."""
    from pathlib import Path
    import shutil
    explicit = os.environ.get("LANTERN_GEMINI_PACKAGE_ROOT")
    candidates = []
    if explicit:
        candidates.append(Path(explicit))
    else:
        executable = shutil.which("gemini")
        if executable:
            resolved = Path(executable).resolve()
            for parent in resolved.parents:
                candidates.extend((parent, parent / "libexec/lib/node_modules/@google/gemini-cli"))
    for root in candidates:
        metadata = root / "package.json"
        if not metadata.is_file():
            continue
        try:
            package = json.loads(metadata.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise ValueError(f"Gemini package metadata unreadable: {error}") from error
        if not isinstance(package, dict) or package.get("name") != "@google/gemini-cli":
            continue
        if not isinstance(package.get("version"), str):
            raise ValueError("Gemini package version missing")
        # Scan only shipped JS, never import or execute it. Reading constants
        # avoids treating documentation strings as a live backend inventory.
        models = set()
        paths = sorted((root / "bundle").glob("*.js"))
        paths += sorted((root / "dist").glob("**/models.js"))
        for path in paths:
            text = path.read_text(encoding="utf-8", errors="replace")
            models.update(re.findall(
                r'\b(?:var|const|let)\s+[A-Z][A-Z0-9_]*\s*=\s*["\']((?:gemini|gemma)-[a-z0-9.-]+)["\']', text))
        if not models:
            raise ValueError("Gemini package contains no recognized model constants")
        return {"models": models, "version": package["version"], "source": "installed-package-static"}
    raise ValueError("Gemini package not found; set LANTERN_GEMINI_PACKAGE_ROOT to the installed @google/gemini-cli package (not settings)")


def gemini_inventory() -> dict[str, object]:
    catalog = gemini_package_catalog()
    models = []
    for name in [*GEMINI_CURRENT, *GEMINI_BLOCKED]:
        present = name in catalog["models"]
        models.append({"model": name, "packaged_constant": present,
            "policy": "candidate" if name in GEMINI_CURRENT else "blocked",
            "reason": GEMINI_BLOCKED.get(name, "current documented ID; requires package presence and explicit routing acknowledgement"),
            "thinking_levels": sorted(GEMINI_CURRENT.get(name, set())),
            "entitlement": "unknown", "inference": "untested"})
    return {"kind": "gemini", "models": models, "cli_version": catalog["version"],
        "catalog_source": catalog["source"], "policy_date": GEMINI_POLICY_DATE,
        "policy_scope": "conservative-cloud-compatible", "actual_model_verified": False}


def gemini_route_info(phrase: str) -> dict[str, object]:
    parts = phrase.strip().split()
    if len(parts) not in (1, 2) or parts[0] not in GEMINI_CURRENT:
        reason = GEMINI_BLOCKED.get(parts[0] if parts else "")
        raise ValueError(reason or "Gemini requires an exact current model ID; no default, alias, or unknown model is validated")
    model = parts[0]
    effort = parts[1] if len(parts) == 2 else None
    if effort and effort not in GEMINI_CURRENT[model]:
        raise ValueError(f"{model} does not support requested thinking level {effort}")
    catalog = gemini_package_catalog()
    if model not in catalog["models"]:
        raise ValueError(f"{model} absent from installed Gemini package constants")
    if os.environ.get("LANTERN_GEMINI_ACK_NATIVE_ROUTING") != "1":
        raise ValueError("Gemini CLI may remap/fallback from the requested model; actual model is unverified. Set LANTERN_GEMINI_ACK_NATIVE_ROUTING=1 only after acknowledging this limitation; strict backend routing is unsupported")
    command = "gemini"  # Herdr starts this canonical name in the target shell.
    import shutil
    if shutil.which(command) is None:
        raise ValueError("Gemini canonical executable is absent from controller PATH")
    argv = ["--model", model]
    if effort:
        if os.environ.get("LANTERN_GEMINI_EFFORT_BRIDGE") != "1":
            raise ValueError("Gemini effort bridge is not configured: launcher owner must install a --thinking-level consumer and native modelConfigs.customOverrides environment bridge; this is not a Gemini --effort flag")
        import subprocess
        try:
            executable = shutil.which(command)
            if executable is None:
                raise ValueError("Gemini launcher disappeared from PATH")
            capability_command = [executable, "--lantern-capabilities"]
            if os.name == "nt" and executable.lower().endswith((".cmd", ".bat")):
                capability_command = [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c",
                    subprocess.list2cmdline(capability_command)]
            capability = subprocess.run(capability_command,
                check=True, capture_output=True, text=True, timeout=5)
            metadata = json.loads(capability.stdout)
            if not isinstance(metadata, dict) or metadata.get("schema_version") != 1 or metadata.get("native_thinking_bridge") is not True:
                raise ValueError("bridge capability metadata is incompatible")
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            raise ValueError(f"Gemini launcher capability check failed: {error}") from error
        argv += ["--thinking-level", effort]
    return {
        "kind": "gemini", "command": command, "model": model,
        "requested_model": model, "effort": effort, "fast": False, "argv": argv,
        "catalog_source": catalog["source"], "cli_version": catalog["version"],
        "policy_date": GEMINI_POLICY_DATE, "entitlement": "unknown", "inference": "untested",
        "actual_model_verified": False, "cli_may_remap": True,
        "requested_effort": effort, "actual_effort_verified": False,
        "target_launcher_verified": False,
        "effort_mechanism": "managed-native-settings-bridge" if effort else "cli-default",
    }
