#!/usr/bin/env python3
"""Resolve a spoken model phrase against an installed CLI catalog."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys

from dataclasses import dataclass

from model_catalog import listed_codex_models, model_words, parse_claude_capabilities


EFFORT_ALIASES = {
    "none": "none",
    "minimal": "minimal",
    "low": "low",
    "medium": "medium",
    "high": "high",
    "xhigh": "xhigh",
    "extra-high": "xhigh",
    "max": "max",
    "ultra": "ultra",
}
STOP_WORDS = {"model", "use", "with", "please", "the"}
PROVIDER_WORDS = {"gpt", "codex", "cursor", "claude"}


class RouteError(RuntimeError):
    pass


@dataclass(frozen=True)
class ParsedPhrase:
    terms: tuple[str, ...]
    effort: str | None
    fast: bool


def fail(message: str) -> None:
    raise RouteError(message)


def run_catalog(command: list[str]) -> str:
    command_name = command[0]
    executable = shutil.which(command_name)
    if os.name == "nt" and executable is not None:
        suffix = os.path.splitext(executable)[1].lower()
        if suffix not in {".exe", ".com", ".cmd", ".bat"}:
            executable = next(
                (
                    candidate
                    for extension in (".exe", ".com", ".cmd", ".bat")
                    if (candidate := shutil.which(f"{command_name}{extension}")) is not None
                ),
                executable,
            )
    if executable is None:
        fail(f"catalog unavailable: {command_name} (command not found)")
    process_command = [executable, *command[1:]]
    if os.name == "nt" and executable.lower().endswith((".cmd", ".bat")):
        command_line = subprocess.list2cmdline(process_command)
        process_command = [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c", command_line]
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
        fail(f"catalog unavailable: {command_name} ({error})")
    return result.stdout


def words(value: str) -> list[str]:
    value = value.lower().replace("extra high", "xhigh").replace("extra-high", "xhigh")
    return model_words(value)


def parse_phrase(value: str, *, keep_effort: bool) -> ParsedPhrase:
    tokens = words(value)
    efforts = [EFFORT_ALIASES[token] for token in tokens if token in EFFORT_ALIASES]
    if len(set(efforts)) > 1:
        fail("model phrase has more than one effort")
    effort = efforts[0] if efforts else None
    fast = "fast" in tokens
    omitted = set(STOP_WORDS)
    if not keep_effort:
        omitted.update(EFFORT_ALIASES)
        omitted.add("fast")
    terms = tuple(token for token in tokens if token not in omitted)
    if not terms:
        fail("model phrase does not name a model family")
    return ParsedPhrase(terms=terms, effort=effort, fast=fast)


def candidate_tokens(*values: str) -> set[str]:
    return {token for value in values for token in words(value)}


def choose(candidates: list[tuple[str, set[str]]], terms: tuple[str, ...]) -> str:
    requested = set(terms)
    # A complete catalog ID wins over token scoring.
    exact = [name for name, _ in candidates if tuple(words(name)) == terms]
    if len(exact) == 1:
        return exact[0]
    matches = [(name, tokens) for name, tokens in candidates if requested <= tokens]
    if not matches:
        fail("model phrase does not match the live catalog")
    smallest = min(len(tokens - requested - PROVIDER_WORDS) for _, tokens in matches)
    names = sorted(name for name, tokens in matches if len(tokens - requested - PROVIDER_WORDS) == smallest)
    if len(names) != 1:
        fail(f"model phrase is ambiguous: {', '.join(names)}")
    return names[0]


def codex_route(phrase: str) -> dict[str, object]:
    if phrase.strip().lower() == "default":
        phrase = "astra"
    parsed = parse_phrase(phrase, keep_effort=False)
    if set(parsed.terms) <= {"gpt", "codex", "6"} and "6" in parsed.terms:
        fail("model phrase is ambiguous: name astra or an exact model such as gpt-5.5")
    try:
        models = listed_codex_models(run_catalog(["codex", "debug", "models"]))
    except ValueError as error:
        fail(str(error))
    model_id = choose(
        [
            (
                str(model.get("slug", "")),
                candidate_tokens(str(model.get("slug", "")), str(model.get("display_name", ""))),
            )
            for model in models
        ],
        parsed.terms,
    )
    model = next(item for item in models if item.get("slug") == model_id)
    efforts = {str(item.get("effort")) for item in model.get("supported_reasoning_levels", [])}
    if parsed.effort and parsed.effort not in efforts:
        fail(f"{model_id} does not support effort {parsed.effort}")
    service_tiers = model.get("service_tiers", [])
    fast_tier_ids = {
        str(item.get("id", "")).strip()
        for item in service_tiers
        if str(item.get("name", "")).lower() == "fast" and str(item.get("id", "")).strip()
    }
    fast_advertised = "fast" in model.get("additional_speed_tiers", []) or bool(fast_tier_ids)
    if parsed.fast and not fast_advertised:
        fail(f"{model_id} does not support fast service")
    if parsed.fast and len(fast_tier_ids) != 1:
        fail(f"{model_id} does not publish one Fast service tier ID")
    fast_tier = next(iter(fast_tier_ids)) if parsed.fast else None
    effort = parsed.effort or model.get("default_reasoning_level")
    if effort and effort not in efforts:
        fail(f"{model_id} has an invalid default effort")
    argv = ["-m", model_id]
    if effort:
        argv.extend(["-c", f'model_reasoning_effort="{effort}"'])
    if parsed.fast:
        argv.extend(["-c", f'service_tier="{fast_tier}"'])
    return {
        "kind": "codex",
        "model": model_id,
        "effort": effort,
        "fast": parsed.fast,
        "service_tier": fast_tier,
        "argv": argv,
    }


def claude_route(phrase: str) -> dict[str, object]:
    if phrase.strip().lower() == "default":
        phrase = "opus high"
    parsed = parse_phrase(phrase, keep_effort=False)
    try:
        models, efforts = parse_claude_capabilities(run_catalog(["claude", "--help"]))
    except ValueError as error:
        fail(str(error))
    model_id = choose([(name, candidate_tokens(name)) for name in sorted(models)], parsed.terms)
    if parsed.fast:
        fail("Claude help does not publish a Fast route")
    if parsed.effort and parsed.effort not in efforts:
        fail(f"Claude does not support effort {parsed.effort}")
    argv = ["--model", model_id]
    if parsed.effort:
        argv.extend(["--effort", parsed.effort])
    return {"kind": "claude", "model": model_id, "effort": parsed.effort, "fast": False, "argv": argv}


def cursor_catalog() -> list[tuple[str, set[str]]]:
    rows: list[tuple[str, set[str]]] = []
    for line in run_catalog(["agent", "--list-models"]).splitlines():
        match = re.match(r"^(\S+)\s+-\s+(.+)$", line.strip())
        if match:
            rows.append((match.group(1), candidate_tokens(match.group(1), match.group(2))))
    return rows


def cursor_route(phrase: str) -> dict[str, object]:
    rows = cursor_catalog()
    if phrase.strip().lower() == "default":
        names = [name for name, _ in rows]
        for preferred in ("gpt-5.6-sol-high-fast",):
            if preferred in names:
                model_id = preferred
                break
        else:
            eligible = [
                name
                for name, tokens in rows
                if "high" in tokens
                and "fast" in tokens
                and "composer" not in tokens
                and "grok" not in tokens
                and name != "auto"
            ]
            if not eligible:
                fail("Cursor catalog has no high and fast default")
            model_id = eligible[0]
        return {"kind": "cursor", "model": model_id, "effort": "high", "fast": True, "argv": ["--model", model_id]}
    parsed = parse_phrase(phrase, keep_effort=True)
    model_id = choose(rows, parsed.terms)
    return {"kind": "cursor", "model": model_id, "effort": parsed.effort, "fast": parsed.fast, "argv": ["--model", model_id]}


def grok_catalog() -> list[tuple[str, set[str]]]:
    rows: list[tuple[str, set[str]]] = []
    for line in run_catalog(["grok", "models"]).splitlines():
        match = re.match(r"^[*-]\s+(\S+)", line.strip())
        if match:
            rows.append((match.group(1), candidate_tokens(match.group(1))))
    return rows


def grok_route(phrase: str) -> dict[str, object]:
    rows = grok_catalog()
    if phrase.strip().lower() == "default":
        names = [name for name, _ in rows]
        preferred = (
            "grok-4.6-high-fast",
            "grok-4.6-fast",
            "grok-4.6",
            "grok-4.5-high-fast",
            "grok-4.5-fast",
            "grok-4.5",
        )
        model_id = next((name for name in preferred if name in names), None)
        if model_id is None:
            fail("Grok catalog has no approved default")
        fast = "fast" in candidate_tokens(model_id)
        argv = ["-m", model_id, "--reasoning-effort", "high"]
        return {"kind": "grok", "model": model_id, "effort": "high", "fast": fast, "argv": argv}
    parsed = parse_phrase(phrase, keep_effort=False)
    model_id = choose(rows, parsed.terms)
    if parsed.fast and "fast" not in candidate_tokens(model_id):
        fail(f"{model_id} does not list a fast variant")
    argv = ["-m", model_id]
    if parsed.effort:
        argv.extend(["--reasoning-effort", parsed.effort])
    return {"kind": "grok", "model": model_id, "effort": parsed.effort, "fast": parsed.fast, "argv": argv}


def main() -> int:
    if len(sys.argv) < 3 or sys.argv[1] not in {"codex", "claude", "cursor", "grok"}:
        print("usage: model-route <codex|claude|cursor|grok> <spoken model phrase|default>", file=sys.stderr)
        return 2
    phrase = " ".join(sys.argv[2:]).strip()
    try:
        routes = {"claude": claude_route, "codex": codex_route, "cursor": cursor_route, "grok": grok_route}
        route = routes[sys.argv[1]](phrase)
    except RouteError as error:
        print(f"model-route: {error}", file=sys.stderr)
        return 2
    print(json.dumps(route, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
