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

from model_catalog import listed_codex_models, model_words, parse_claude_capabilities


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


def run(command: list[str]) -> str:
    prefix = resolve_command(command[0])
    if len(prefix) > 1:
        command_line = subprocess.list2cmdline([prefix[-1], *command[1:]])
        process_command = [*prefix[:-1], command_line]
    else:
        process_command = [*prefix, *command[1:]]
    try:
        result = subprocess.run(
            process_command,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
        fail(f"availability check failed: {command[0]} ({error})")
    return result.stdout


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
        choice = cursor_choice(models, {"cursor", "grok", "high", "fast"}, model)
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


def claude_capabilities() -> tuple[set[str], set[str]]:
    try:
        return parse_claude_capabilities(run(["claude", "--help"]))
    except ValueError as error:
        fail(f"availability check failed: {error}")


def claude_alias_available(
    alias: str,
    effort: str,
    models: set[str],
    efforts: set[str],
    buckets: list[UsageBucket],
) -> bool:
    return alias in models and effort in efforts and exhausted_bucket(alias, buckets) is None


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
    models: set[str],
    efforts: set[str],
) -> dict[str, object] | None:
    if not claude_global_exhausted(buckets) and "fable" in model.lower() and claude_alias_available(
        "opus", "xhigh", models, efforts, buckets
    ):
        return {"kind": "claude", "model": "opus", "effort": "xhigh", "fast": False, "argv": ["--model", "opus", "--effort", "xhigh"]}
    if not claude_global_exhausted(buckets) and "opus" in model.lower() and claude_alias_available(
        "sonnet", "high", models, efforts, buckets
    ):
        return {"kind": "claude", "model": "sonnet", "effort": "high", "fast": False, "argv": ["--model", "sonnet", "--effort", "high"]}
    return cursor_sol_substitute()


def report_available(kind: str, model: str, effort: str) -> int:
    print(json.dumps({"available": True, "kind": kind, "model": model, "effort": effort}, separators=(",", ":")))
    return 0


def report_unavailable(kind: str, model: str, reason: str, substitute: dict[str, object] | None) -> int:
    payload = {"available": False, "kind": kind, "model": model, "reason": reason, "substitute": substitute}
    print(json.dumps(payload, separators=(",", ":")))
    return 3


def check(kind: str, model: str, effort: str) -> int:
    if kind == "claude":
        models, efforts = claude_capabilities()
        _, buckets = parse_claude_usage()
        if not buckets:
            fail("availability check failed: Claude returned no usage buckets")
        if model.lower() not in models:
            return report_unavailable(
                kind,
                model,
                f"Claude model {model} is not verified by claude --help",
                claude_substitute(model, buckets, models, efforts),
            )
        if effort and effort.lower() not in efforts:
            return report_unavailable(
                kind,
                model,
                f"Claude effort {effort} is not verified by claude --help",
                claude_substitute(model, buckets, models, efforts),
            )
        exhausted = exhausted_bucket(model, buckets)
        if exhausted:
            return report_unavailable(
                kind,
                model,
                exhausted_reason(exhausted),
                claude_substitute(model, buckets, models, efforts),
            )
        return report_available(kind, model, effort)
    if kind == "cursor":
        models = cursor_models()
        if model not in models:
            return report_unavailable(kind, model, f"{model} is absent from agent --list-models", cursor_substitute(model, models))
        return report_available(kind, model, effort)
    if kind == "grok":
        models = grok_models()
        if model not in models:
            choice = cursor_choice(models, {"grok", "4.5"})
            substitute = None
            if choice:
                substitute = {"kind": "grok", "model": choice, "effort": "high", "fast": False, "argv": ["-m", choice, "--reasoning-effort", "high"]}
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
    if len(sys.argv) not in {3, 4} or sys.argv[1] not in {"claude", "cursor", "grok", "codex"}:
        print("usage: model-preflight <claude|cursor|grok|codex> <model> [effort]", file=sys.stderr)
        return 2
    try:
        return check(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) == 4 else "")
    except CheckError as error:
        print(f"model-preflight: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
