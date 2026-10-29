#!/usr/bin/env python3
"""Validate native Gemini seat argv before Herdr mutates a pane. No inference."""
import sys
import model_catalog


# Native 0.65 grammar: these options consume the next token as data.
VALUE_OPTIONS = {
    "--prompt", "-p", "--prompt-interactive", "-i",
    "--approval-mode", "--policy", "--admin-policy", "--allowed-mcp-server-names",
    "--allowed-tools", "--extensions", "-e", "--session-file",
    "--session-id", "--delete-session", "--include-directories", "--output-format", "-o",
}
OPTIONAL_VALUE_OPTIONS = {"--resume", "-r", "--worktree", "-w"}
BOOLEAN_SHORT_OPTIONS = {"-d", "-s", "-y", "-l", "-v", "-h"}


def check(arguments):
    model = effort = None
    index = 0
    while index < len(arguments):
        arg = arguments[index]
        if arg == '--':
            raise ValueError('nested option terminator is not supported for Gemini seats')
        key, separator, value = arg.partition('=')
        if key in OPTIONAL_VALUE_OPTIONS:
            if not separator and index + 1 < len(arguments) and not arguments[index + 1].startswith('-'):
                index += 1
            index += 1
            continue
        if key in VALUE_OPTIONS:
            if not separator:
                index += 1
                if index >= len(arguments):
                    raise ValueError(f'{key} requires a value')
                if arguments[index].startswith('-'):
                    raise ValueError(f'{key} requires an unambiguous value; use equals syntax')
            index += 1
            continue
        if key.startswith('-') and not key.startswith('--') and key not in BOOLEAN_SHORT_OPTIONS and key != '-m':
            raise ValueError('packed or unknown short options are not supported for Gemini seats')
        if key == '--effort':
            raise ValueError('Gemini has no native --effort; use the configured thinking bridge')
        if key in ('--model', '-m', '--m', '--thinking-level'):
            if not separator:
                index += 1
                if index >= len(arguments) or arguments[index].startswith('-'):
                    raise ValueError(f'{key} requires a value')
                value = arguments[index]
            if not value:
                raise ValueError(f'{key} requires a nonempty value')
            if key in ('--model', '-m', '--m'):
                if model is not None:
                    raise ValueError('Gemini requires exactly one explicit model')
                if value not in model_catalog.GEMINI_CURRENT:
                    raise ValueError('Gemini native model must be exactly one current model ID')
                model = value
            else:
                if effort is not None:
                    raise ValueError('Gemini accepts only one thinking level')
                effort = value.lower()
        index += 1
    if not model:
        raise ValueError('Gemini requires one explicit current --model ID')
    route = model_catalog.gemini_route_info(model + (' ' + effort if effort else ''))
    if route['model'] != model or route['effort'] != effort:
        raise ValueError('Gemini route does not match native model/thinking fields')
    return route


if __name__ == '__main__':
    try:
        route = check(sys.argv[1:])
    except (ValueError, OSError) as error:
        print(f'gemini-start: {error}', file=sys.stderr)
        raise SystemExit(2)
    print(f"Gemini requested {route['requested_model']}; CLI may remap or fallback. Actual model, entitlement and target-shell launcher are unverified.", file=sys.stderr)
