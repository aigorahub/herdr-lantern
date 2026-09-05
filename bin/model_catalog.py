"""Shared parsing for model routes and availability checks."""

import json
import re


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


def model_words(value: str) -> list[str]:
    # Keep integer generations. Normalize dotted and hyphenated versions alike.
    value = re.sub(r"(?<=\d)-(?=\d)", ".", value.lower())
    return re.findall(r"\d+(?:\.\d+)*|[a-z][a-z0-9]*", value)


def parse_claude_capabilities(help_text: str) -> tuple[set[str], set[str]]:
    def option(name: str, argument: str) -> str:
        match = re.search(
            rf"^[ \t]*--{name}\s+<{argument}>.*?(?=^[ \t]*(?:-[a-zA-Z],\s*)?--[a-zA-Z]|\Z)",
            help_text,
            re.MULTILINE | re.DOTALL,
        )
        if match is None:
            raise ValueError(f"Claude help has no {name} choices")
        return match.group(0)

    models = {value.lower() for value in re.findall(r"['\"]([a-zA-Z0-9.-]+)['\"]", option("model", "model"))}
    choices = re.search(r"\((low(?:\s*,\s*(?:medium|high|xhigh|max))+?)\)", option("effort", "level"))
    efforts = set(re.findall(r"low|medium|high|xhigh|max", choices.group(1))) if choices else set()
    if not models or not efforts:
        raise ValueError("Claude returned unparseable model or effort choices")
    return models, efforts
