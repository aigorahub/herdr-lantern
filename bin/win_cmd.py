"""Quote argv for cmd.exe /d /s /c.

list2cmdline quotes for CreateProcess. cmd.exe still treats an unquoted
& | < > ^ % as syntax, so a path such as C:\\Work\\R&D is split before the
batch shim runs.
"""

from __future__ import annotations

import subprocess


def quote_for_cmd(arg: str) -> str:
    """Return one argument safe inside a cmd.exe /d /s /c command string."""
    rendered = subprocess.list2cmdline([arg])
    if rendered.startswith('"') and rendered.endswith('"') and rendered.count('"') == 2:
        inner = rendered[1:-1]
        if "%" not in inner:
            return rendered
        return '"' + inner.replace("%", '"^%"') + '"'
    return "".join("^" + ch if ch in "&|<>^%" else ch for ch in rendered)


def cmd_command_line(argv: list[str]) -> str:
    return " ".join(quote_for_cmd(arg) for arg in argv)


def cmd_argv(argv: list[str], comspec: str) -> list[str]:
    return [comspec, "/d", "/s", "/c", cmd_command_line(argv)]
