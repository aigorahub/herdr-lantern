#!/usr/bin/env python3
"""Check a resolved model before Lantern asks to create a seat."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys

from dataclasses import dataclass

import model_catalog
from model_catalog import claude_initialization, listed_codex_models, model_words


class CheckError(RuntimeError):
    pass


@dataclass(frozen=True)
class UsageBucket:
    label: str
    percent: int
    reset: str


def fail(message: str) -> None:
    raise CheckError(message)


def resolve_command(name: str) -> list[str]:
    executable = shutil.which(name)
    if os.name == "nt" and executable is not None:
        suffix = os.path.splitext(executable)[1].lower()
        if suffix not in {".exe", ".com", ".cmd", ".bat"}:
            executable = next(
                (
                    candidate
                    for extension in (".exe", ".com", ".cmd", ".bat")
                    if (candidate := shutil.which(f"{name}{extension}")) is not None
                ),
                executable,
            )
    if executable is None:
        fail(f"availability check failed: {name} command not found")
    command = [executable]
    if os.name == "nt" and executable.lower().endswith((".cmd", ".bat")):
        command = [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c", executable]
    return command


def run(
    command: list[str], *, input_text: str | None = None, stdout_via_file: bool = False
) -> str:
    prefix = resolve_command(command[0])
    if len(prefix) > 1:
        command_line = subprocess.list2cmdline([prefix[-1], *command[1:]])
        process_command = [*prefix[:-1], command_line]
    else:
        process_command = [*prefix, *command[1:]]

    def invoke(stdout):
        return subprocess.run(
            process_command,
            check=True,
            input=input_text,
            stdout=stdout,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )

    try:
        if stdout_via_file:
            return model_catalog.capture_to_file(invoke)
        return invoke(subprocess.PIPE).stdout
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        fail(f"availability check failed: {command[0]} ({error})")


def words(value: str) -> set[str]:
    return set(model_words(value))


def cursor_models() -> list[str]:
    models = []
    for line in run(["agent", "--list-models"]).splitlines():
        match = re.match(r"^(\S+)\s+-\s+.+$", line.strip())
        if match:
            models.append(match.group(1))
    if not models:
        fail("availability check failed: Cursor returned an unparseable catalog")
    return models


def grok_models() -> list[str]:
    models = []
    for line in run(["grok", "models"]).splitlines():
        match = re.match(r"^[*-]\s+(\S+)", line.strip())
        if match:
            models.append(match.group(1))
    if not models:
        fail("availability check failed: Grok returned an unparseable catalog")
    return models


def codex_models() -> list[dict[str, object]]:
    try:
        models = listed_codex_models(run(["codex", "debug", "models"]))
    except ValueError as error:
        fail(f"availability check failed: {error}")
    if not models:
        fail("availability check failed: Codex returned an empty catalog")
    return models


def cursor_choice(models: list[str], required: set[str], excluded: str = "") -> str | None:
    return next(
        (
            model
            for model in models
            if model != excluded and required <= words(model) and "composer" not in words(model)
        ),
        None,
    )


def cursor_substitute(model: str, models: list[str]) -> dict[str, object] | None:
    tokens = words(model)
    choice = None
    if "grok" in tokens:
        choice = cursor_choice(models, {"grok", "4.7", "high", "fast"}, model)
        choice = choice or cursor_choice(models, {"grok", "4.6", "high", "fast"}, model)
        choice = choice or cursor_choice(models, {"gpt", "5.6", "sol", "high", "fast"})
    elif "sol" in tokens:
        choice = cursor_choice(models, {"gpt", "5.6", "terra", "high", "fast"})
        choice = choice or cursor_choice(models, {"gpt", "5.6", "luna", "high", "fast"})
    if choice is None:
        return None
    return {"kind": "cursor", "model": choice, "effort": "high", "fast": "fast" in words(choice), "argv": ["--model", choice]}


def cursor_sol_substitute() -> dict[str, object] | None:
    models = cursor_models()
    choice = cursor_choice(models, {"gpt", "5.6", "sol", "high", "fast"})
    if choice is None:
        return None
    return {"kind": "cursor", "model": choice, "effort": "high", "fast": True, "argv": ["--model", choice]}


def exhausted_reason(bucket: UsageBucket) -> str:
    reason = f"{bucket.label} is {bucket.percent}% used"
    if bucket.reset:
        reason += f"; resets {bucket.reset}"
    return reason


def parse_claude_usage() -> tuple[str, list[UsageBucket]]:
    try:
        payload = json.loads(run(["claude", "/usage", "-p", "--output-format", "json"]))
        result = payload["result"]
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        fail(f"availability check failed: Claude returned unparseable usage JSON ({error})")
    if not isinstance(result, str):
        fail("availability check failed: Claude usage result is not text")
    is_error = payload.get("is_error", False)
    if not isinstance(is_error, bool):
        fail("availability check failed: Claude usage error flag is not boolean")
    if is_error:
        fail("availability check failed: Claude usage command reported an error")
    if "subtype" in payload and payload["subtype"] != "success":
        fail("availability check failed: Claude usage returned an unsuccessful result")
    buckets = []
    limit = re.search(r"hit your\s+(.+?)\s+limit(?:[^\n]*?resets\s+([^\n]+))?", result, re.IGNORECASE)
    if limit:
        reset = limit.group(2).strip() if limit.group(2) else ""
        buckets.append(UsageBucket(f"Limit: {limit.group(1).strip()}", 100, reset))
    pattern = re.compile(
        r"^(Current session|Current week \([^)]+\)):\s*(\d+)% used(?:\s*[·•\-]\s*resets\s+(.+))?$",
        re.IGNORECASE | re.MULTILINE,
    )
    for match in pattern.finditer(result):
        reset = match.group(3).strip() if match.group(3) else ""
        buckets.append(UsageBucket(match.group(1), int(match.group(2)), reset))
    return result, buckets


def claude_cost_usage(result: str) -> bool:
    """Recognize the complete successful cost view for token logins."""
    if re.search(r"\b(?:API Error|Authentication failed|Not logged in|Failed to retrieve usage)\b", result, re.IGNORECASE):
        return False
    duration = r"(?:\d+(?:\.\d+)?(?:ms|s|m|h|d)\s*)+"
    count = r"\d+(?:\.\d+)?[kKmMbB]?"
    usage = count + r" input,\s*" + count + r" output(?:,\s*" + count + r" cache read,\s*" + count + r" cache write)?"
    cause = r"(?:(?!likely cause:|tokens re-cached)[^·\r\n])+?"
    miss_detail = (r"\(last\s*" + duration + r"ago(?:,\s*" + count
                   + r" tokens re-cached(?:,\s*likely cause:\s*" + cause + r")?"
                   r"|\s*\u2014\s*likely cause:\s*" + cause + r",\s*" + count + r" tokens re-cached)\)")
    temperature = (r"(?:(?:warm|cold)\s*\(" + duration + r"TTL,\s*last activity\s*" + duration + r"ago\)"
                   r"|cold\s*\u2014\s*idle\s*" + duration
                   + r",\s*next turn re-caches (?:~" + count + r" tokens|the compacted prompt)"
                   r"|no prompt caching reported by the API)")
    cache = (r"Prompt cache \(main\):\s*\d+ requests?\s*·\s*\d+(?:\.\d+)?% of input tokens from cache"
             r"\s*·\s*(?:no misses|\d+ miss(?:es)?(?:\s*" + miss_detail + r")?)"
             r"(?:\s*·\s*\d+ expected rebuilds?(?:\s*\((?:compaction|tool-result clearing|compaction or tool-result clearing)\))?)?"
             r"\s*·\s*" + temperature)
    patterns = {
        "cost": r"Total cost:\s*\$\d+(?:\.\d+)?(?:\s+at your organization's configured rates|\s+\(at your organization's configured rates\))?",
        "api": r"Total duration \(API\):\s*" + duration,
        "wall": r"Total duration \(wall\):\s*" + duration,
        "changes": r"Total code changes:\s*\d+ lines added,\s*\d+ lines removed",
        "usage": r"Usage:\s*" + usage,
        "models": r"Usage by model:",
        "cache": cache,
    }
    seen = set()
    models = set()
    model_usage = r"(\S+):\s*" + usage + r"(?:\s*\(\$\d+(?:\.\d+)?\))?"
    lines = []
    for line in result.splitlines():
        if line.strip().startswith("·") and lines and lines[-1].startswith("Prompt cache (main):"):
            lines[-1] += " " + line.strip()
        else:
            lines.append(line.strip())
    for line in lines:
        if not line.strip():
            continue
        key = next((key for key, pattern in patterns.items() if re.fullmatch(pattern, line.strip())), None)
        if key is None:
            model = re.fullmatch(model_usage, line.strip()) if "models" in seen else None
            if model is None or model[1] in models:
                return False
            models.add(model[1])
            continue
        if key in seen or key == "models" and "usage" in seen or key == "usage" and "models" in seen:
            return False
        seen.add(key)
    if "usage" in seen:
        return "cost" in seen
    return {"cost", "api", "wall", "changes", "models"} <= seen and bool(models)


def exhausted_bucket(model: str, buckets: list[UsageBucket]) -> UsageBucket | None:
    family = next((name for name in ("fable", "opus", "sonnet") if name in model.lower()), "")
    for bucket in buckets:
        label = bucket.label.lower()
        if label.startswith("limit:"):
            limit_family = next((name for name in ("fable", "opus", "sonnet") if name in label), "")
            applies = not limit_family or limit_family == family
        else:
            applies = label == "current session" or label == "current week (all models)"
        applies = applies or bool(family and family in label)
        if applies and bucket.percent >= 100:
            return bucket
    return None


def claude_capabilities() -> tuple[dict[str, tuple[str, set[str]]], str | None]:
    try:
        return claude_initialization(run)
    except ValueError as error:
        fail(f"availability check failed: {error}")


def claude_alias_available(
    alias: str,
    effort: str,
    models: dict[str, tuple[str, set[str]]],
    buckets: list[UsageBucket],
) -> bool:
    return alias in models and effort in models[alias][1] and exhausted_bucket(models[alias][0], buckets) is None


def claude_global_exhausted(buckets: list[UsageBucket]) -> bool:
    for bucket in buckets:
        if bucket.percent < 100:
            continue
        label = bucket.label.lower()
        if label in {"current session", "current week (all models)"}:
            return True
        if label.startswith("limit:") and not any(name in label for name in ("fable", "opus", "sonnet")):
            return True
    return False


def claude_substitute(
    model: str,
    buckets: list[UsageBucket],
    models: dict[str, tuple[str, set[str]]],
) -> dict[str, object] | None:
    if not claude_global_exhausted(buckets) and "fable" in model.lower() and claude_alias_available(
        "opus", "xhigh", models, buckets
    ):
        opus_id = models["opus"][0]
        return {"kind": "claude", "model": "opus", "effort": "xhigh", "fast": False, "argv": ["--model", opus_id, "--effort", "xhigh"]}
    if not claude_global_exhausted(buckets) and "opus" in model.lower() and claude_alias_available(
        "sonnet", "high", models, buckets
    ):
        sonnet_id = models["sonnet"][0]
        return {"kind": "claude", "model": "sonnet", "effort": "high", "fast": False, "argv": ["--model", sonnet_id, "--effort", "high"]}
    return cursor_sol_substitute()


def report_available(kind: str, model: str, effort: str, *, quota: str | None = None) -> int:
    payload = {"available": True, "kind": kind, "model": model, "effort": effort}
    if quota is not None:
        payload["quota"] = quota
    print(json.dumps(payload, separators=(",", ":")))
    return 0


def report_unavailable(kind: str, model: str, reason: str, substitute: dict[str, object] | None) -> int:
    payload = {"available": False, "kind": kind, "model": model, "reason": reason, "substitute": substitute}
    print(json.dumps(payload, separators=(",", ":")))
    return 3


def fugu_models() -> dict[str, set[str]]:
    path = os.path.join(os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex"), "fugu.json")
    try:
        with open(path, encoding="utf-8") as handle:
            catalog = json.load(handle)
        rows = catalog["models"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
        fail(f"availability check failed: Fugu catalog unavailable ({error})")
    if not isinstance(rows, list):
        fail("availability check failed: Fugu catalog models is not a list")
    models: dict[str, set[str]] = {}
    for row in rows:
        if not isinstance(row, dict) or row.get("visibility") != "list":
            continue
        slug = row.get("slug")
        levels = row.get("supported_reasoning_levels", [])
        if not isinstance(slug, str) or not slug or not isinstance(levels, list):
            fail("availability check failed: Fugu catalog has an invalid model")
        models[slug] = {str(level.get("effort")) for level in levels if isinstance(level, dict)}
    if not models:
        fail("availability check failed: Fugu catalog is empty")
    return models


def check(kind: str, model: str, effort: str) -> int:
    if kind == "fugu":
        models = fugu_models()
        if model not in models:
            substitute = None
            for slug in ("fugu",):
                if slug in models:
                    chosen = "high" if "high" in models[slug] else sorted(models[slug])[0]
                    substitute = {
                        "kind": "fugu",
                        "model": slug,
                        "effort": chosen,
                        "fast": False,
                        "argv": ["-p", "fugu", "-m", slug, "-c", f'model_reasoning_effort="{chosen}"'],
                    }
                    break
            return report_unavailable(kind, model, f"{model} is absent from the installed Fugu catalog", substitute)
        if effort and effort not in models[model]:
            return report_unavailable(
                kind,
                model,
                f"{model} does not support effort {effort}",
                None,
            )
        return report_available(kind, model, effort)
    if kind == "claude":
        models, token_source = claude_capabilities()
        if token_source and token_source.lower() == "none":
            fail("availability check failed: Claude is not logged in")
        usage, buckets = parse_claude_usage()
        if not buckets and not token_source:
            # No token source and no quota buckets: nothing shows a login.
            fail("availability check failed: Claude is not logged in "
                 "(no token source and no usage buckets)")
        if not buckets and not claude_cost_usage(usage):
            fail("availability check failed: Claude returned unrecognized usage text")
        if model.lower() not in models:
            return report_unavailable(
                kind,
                model,
                f"Claude model {model} is absent from the Claude initialization catalog",
                claude_substitute(model, buckets, models),
            )
        if effort and effort.lower() not in models[model.lower()][1]:
            return report_unavailable(
                kind,
                model,
                f"Claude effort {effort} is absent from the Claude initialization catalog",
                claude_substitute(model, buckets, models),
            )
        exhausted = exhausted_bucket(models[model.lower()][0], buckets)
        if exhausted:
            return report_unavailable(
                kind,
                model,
                exhausted_reason(exhausted),
                claude_substitute(model, buckets, models),
            )
        return report_available(kind, model, effort, quota="unknown" if not buckets else None)
    if kind == "cursor":
        models = cursor_models()
        if model not in models:
            return report_unavailable(kind, model, f"{model} is absent from agent --list-models", cursor_substitute(model, models))
        return report_available(kind, model, effort)
    if kind == "grok":
        models = grok_models()
        if model not in models:
            ranked = (
                ({"grok", "4.7", "build", "fast"}, "medium"),
                ({"grok", "4.7"}, "high"),
                ({"grok", "4.6"}, "high"),
                ({"grok", "4.5"}, "high"),
            )
            choice = None
            effort = "high"
            for required, ranked_effort in ranked:
                choice = cursor_choice(models, required, model)
                if choice:
                    effort = ranked_effort
                    break
            substitute = None
            if choice:
                substitute = {"kind": "grok", "model": choice, "effort": effort, "fast": "fast" in words(choice), "argv": ["-m", choice, "--reasoning-effort", effort]}
            return report_unavailable(kind, model, f"{model} is absent from grok models", substitute)
        return report_available(kind, model, effort)
    models = codex_models()
    entry = next((entry for entry in models if entry["slug"] == model), None)
    if entry is None:
        return report_unavailable(kind, model, f"{model} is absent from codex debug models", None)
    levels = {level.get("effort") for level in entry.get("supported_reasoning_levels", [])}
    if effort and effort not in levels:
        return report_unavailable(kind, model, f"{model} does not support effort {effort}", None)
    return report_available(kind, model, effort)


def main() -> int:
    if len(sys.argv) not in {3, 4} or sys.argv[1] not in {"claude", "cursor", "grok", "codex", "fugu"}:
        print("usage: model-preflight <claude|cursor|grok|codex|fugu> <model> [effort]", file=sys.stderr)
        return 2
    try:
        return check(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) == 4 else "")
    except CheckError as error:
        print(f"model-preflight: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
