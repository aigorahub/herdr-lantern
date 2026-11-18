#!/usr/bin/env python3
"""Check a resolved model before Lantern asks to create a seat."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys

import model_catalog
from model_catalog import claude_initialization, listed_codex_models, model_words


class CheckError(RuntimeError):
    pass


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


def claude_capabilities() -> tuple[dict[str, tuple[str, set[str]]], str | None]:
    try:
        return claude_initialization(run)
    except ValueError as error:
        fail(f"availability check failed: {error}")


def claude_substitute(model: str, models: dict[str, tuple[str, set[str]]]) -> dict[str, object] | None:
    """Propose one live substitute for a Claude model or effort absent from the catalog."""
    for family, alias, effort in (("fable", "opus", "xhigh"), ("opus", "sonnet", "high")):
        if family in model.lower() and alias in models and effort in models[alias][1]:
            resolved = models[alias][0]
            return {"kind": "claude", "model": alias, "effort": effort, "fast": False,
                    "argv": ["--model", resolved, "--effort", effort]}
    return cursor_sol_substitute()


def report_available(kind: str, model: str, effort: str, *, quota: str | None = None) -> int:
    payload = {"available": True, "kind": kind, "model": model, "effort": effort}
    if quota is not None:
        payload["quota"] = quota
    print(json.dumps(payload, separators=(",", ":")))
    return 0


def report_unavailable(kind: str, model: str, reason: str, substitute: dict[str, object] | None,
                       *, quota: str | None = None) -> int:
    payload = {"available": False, "kind": kind, "model": model, "reason": reason, "substitute": substitute}
    if quota is not None:
        payload["quota"] = quota
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
    if kind == "gemini":
        try:
            payload = model_catalog.gemini_route_info(model + (" " + effort if effort else ""))
        except (ValueError, OSError) as error:
            return report_unavailable(kind, model, str(error), None)
        payload.update(available=None, launchable=True, substitute=None, quota="unknown")
        print(json.dumps(payload, separators=(",", ":")))
        return 0
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
        # Never read Claude usage or quota here. A usage read can take longer
        # than the catalog timeout, and it must never gate seating.
        models, token_source = claude_capabilities()
        if token_source and token_source.lower() == "none":
            fail("availability check failed: Claude is not logged in")
        if model.lower() not in models:
            return report_unavailable(
                kind,
                model,
                f"Claude model {model} is absent from the Claude initialization catalog",
                claude_substitute(model, models),
            )
        if effort and effort.lower() not in models[model.lower()][1]:
            return report_unavailable(
                kind,
                model,
                f"Claude effort {effort} is absent from the Claude initialization catalog",
                claude_substitute(model, models),
            )
        return report_available(kind, model, effort, quota="unknown")
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
    if len(sys.argv) not in {3, 4} or sys.argv[1] not in {"claude", "cursor", "grok", "codex", "fugu", "gemini"}:
        print("usage: model-preflight <claude|cursor|grok|codex|fugu|gemini> <model> [effort]", file=sys.stderr)
        return 2
    try:
        return check(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) == 4 else "")
    except CheckError as error:
        print(f"model-preflight: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
