"""A terminal viewport for readouts with keyboard scrolling and resize support."""
from __future__ import annotations

import os
import re
import unicodedata
import shutil
import sys
import threading
import time

TOKENS = re.compile(r"\x1b\[[0-9;]*m|.\ufe0f?")


def cell_width(token: str) -> int:
    if token.startswith("\x1b"):
        return 0
    character = token[0]
    if unicodedata.combining(character) or unicodedata.category(character) == "Cf":
        return 0
    return 2 if len(token) == 2 or unicodedata.east_asian_width(character) in ("W", "F") else 1


def fit(line: str, width: int, column: int = 0) -> str:
    result, position = [], 0
    visible_base = column == 0
    for token in TOKENS.findall(line):
        if token.startswith("\x1b"):
            result.append(token)
            continue
        cells = cell_width(token)
        if not cells:
            if visible_base:
                result.append(token)
            continue
        end = position + cells
        if end > column + width:
            return "".join(result) + ("\033[0m" if "\x1b" in line else "")
        visible_base = position >= column
        if visible_base:
            result.append(token)
        elif end > column:
            result.append(" " * (end - column))
        position = end
    return "".join(result) if column else line


def page(output: str, offset: int, width: int, height: int, *, column: int = 0,
         sideways: bool = False) -> tuple[str, int]:
    width, height = max(1, width), max(1, height)
    lines = [fit(line, width, column) for line in output.rstrip("\n").splitlines()]
    if len(lines) <= height and not sideways:
        return "\n".join(lines), 0
    has_footer = height > 1 or not sideways
    body_height = height - 1 if has_footer else height
    offset = min(max(0, offset), max(0, len(lines) - body_height))
    body = lines[offset:offset + body_height]
    if has_footer:
        if sideways and len(lines) <= body_height:
            footer = "Left/Right scroll  q close"
        else:
            keys = "Arrow keys" if sideways else "Up/Down"
            footer = f"{offset + 1}-{min(len(lines), offset + body_height)}/{len(lines)}  {keys} scroll  q close"
        body.append(footer[:width])
    return "\n".join(body), offset


class Readout:
    def __init__(self, stop: threading.Event | None = None, *, horizontal: bool = False):
        self.stop = stop if stop is not None else threading.Event()
        self.interactive = os.isatty(0) and os.isatty(1)
        self.output = ""
        self.offset = 0
        self.horizontal = horizontal
        self.column = 0
        self.max_column = 0
        self.size = None
        self.saved = None

    def __enter__(self):
        if self.interactive and os.name != "nt":
            import termios
            import tty
            self.saved = termios.tcgetattr(0)
            tty.setcbreak(0)
        if self.interactive:
            sys.stdout.write("\033[?1049h\033[?25l")
            sys.stdout.flush()
        return self

    def __exit__(self, *_):
        try:
            if self.saved is not None:
                import termios
                termios.tcsetattr(0, termios.TCSANOW, self.saved)
        finally:
            if self.interactive:
                sys.stdout.write("\033[0m\033[?25h\033[?1049l")
                sys.stdout.flush()

    def draw(self, output: str):
        self.output = output
        if not self.interactive:
            print(output, end="", flush=True)
            return
        self.size = shutil.get_terminal_size((88, 24))
        width = max(1, self.size.columns)
        if self.horizontal:
            widest = max((sum(map(cell_width, TOKENS.findall(line))) for line in output.splitlines()), default=0)
            self.max_column = max(0, widest - width)
            self.column = min(self.column, self.max_column)
        # Do not print a final newline. It can scroll the top row off screen.
        visible, self.offset = page(output, self.offset, width, self.size.lines,
                                    column=self.column, sideways=self.max_column > 0)
        print("\033[H\033[2J" + visible, end="", flush=True)

    def key(self, key: str):
        height = self.size.lines if self.size else 24
        if key in ("q", "Q", "\x03"):
            self.stop.set()
        elif key in ("j", "\033[B", "\033OB"):
            self.offset += 1
        elif key in ("k", "\033[A", "\033OA"):
            self.offset = max(0, self.offset - 1)
        elif self.horizontal and key in ("\033[C", "\033OC", "\033[D", "\033OD"):
            width = self.size.columns if self.size else 88
            step = max(1, min(8, width - 1))
            change = step if key.endswith("C") else -step
            self.column = min(self.max_column, max(0, self.column + change))
        elif key in (" ", "\033[6~"):
            self.offset += max(1, height - 2)
        elif key in ("b", "\033[5~"):
            self.offset = max(0, self.offset - max(1, height - 2))
        elif key in ("g", "\033[H", "\033[1~", "\033OH"):
            self.offset = 0
        elif key in ("G", "\033[F", "\033[4~", "\033OF"):
            self.offset = len(self.output.splitlines())

    def feed(self, keys: str):
        for key in re.findall(r"\x1b(?:\[[0-9;]*[A-Za-z~]|O[A-Z])|.", keys):
            self.key(key)

    def wait(self, seconds: float):
        if not self.interactive:
            self.stop.wait(seconds)
            return
        end = time.monotonic() + seconds
        while not self.stop.is_set() and time.monotonic() < end:
            key = ""
            if os.name == "nt":
                import msvcrt
                if msvcrt.kbhit():
                    key = msvcrt.getwch()
                    if key in ("\x00", "\xe0"):
                        key = {"H": "\033[A", "P": "\033[B", "M": "\033[C", "K": "\033[D",
                               "I": "\033[5~", "Q": "\033[6~", "G": "\033[H", "O": "\033[F"}.get(msvcrt.getwch(), "")
            else:
                import select
                if select.select([0], [], [], 0)[0]:
                    key = os.read(0, 32).decode("ascii", errors="ignore")
                    if key == "\033" and select.select([0], [], [], .02)[0]:
                        key += os.read(0, 32).decode("ascii", errors="ignore")
            previous = (self.offset, self.column)
            if key:
                self.feed(key)
            if previous != (self.offset, self.column) or self.size != shutil.get_terminal_size((88, 24)):
                self.draw(self.output)
            self.stop.wait(min(.1, max(0, end - time.monotonic())))
