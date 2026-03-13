"""Terminal output helpers: colored messages for CLI. No-op when stdout is not a TTY."""

import json
import sys
from typing import Any


def _use_color() -> bool:
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


# ANSI codes for JSON colorization (keys, strings, numbers, literals, structure)
_CYAN = "\033[36m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_DIM = "\033[90m"
_RED = "\033[31m"
_RESET = "\033[0m"


def orange(msg: str) -> str:
    """Return message with orange color (for errors/warnings). Plain text when not TTY."""
    if not _use_color():
        return msg
    return f"\033[33m{msg}\033[0m"


def grey(msg: str) -> str:
    """Return message with grey/dim color (for tips). Plain text when not TTY."""
    if not _use_color():
        return msg
    return f"\033[90m{msg}\033[0m"


def colored_json(obj: Any, indent: int = 2) -> str:
    """Format dict/list as indented JSON with syntax highlighting. Plain when not TTY."""
    if not _use_color():
        return json.dumps(obj, indent=indent)

    def _enc(s: str) -> str:
        return json.dumps(s)

    def _fmt(value: Any, depth: int, key: Any = None) -> str:
        pad = " " * (depth * indent)
        pad_inner = " " * ((depth + 1) * indent)
        if value is None:
            return f"{_DIM}null{_RESET}"
        if value is True:
            if key == "exported":
                return f"{_RED}true{_RESET}"
            return f"{_DIM}true{_RESET}"
        if value is False:
            return f"{_DIM}false{_RESET}"
        if isinstance(value, (int, float)):
            return f"{_YELLOW}{json.dumps(value)}{_RESET}"
        if isinstance(value, str):
            return f"{_GREEN}{_enc(value)}{_RESET}"
        if isinstance(value, list):
            if not value:
                return "[]"
            lines = ["["]
            for i, item in enumerate(value):
                lines.append(f"{pad_inner}{_fmt(item, depth + 1, None)}{',' if i < len(value) - 1 else ''}")
            lines.append(f"{pad}]")
            return "\n".join(lines)
        if isinstance(value, dict):
            if not value:
                return "{}"
            lines = ["{"]
            for i, (k, v) in enumerate(value.items()):
                key_part = f'{_CYAN}{_enc(k)}{_RESET}: '
                lines.append(f"{pad_inner}{key_part}{_fmt(v, depth + 1, k)}{',' if i < len(value) - 1 else ''}")
            lines.append(f"{pad}}}")
            return "\n".join(lines)
        return json.dumps(value)

    return _fmt(obj, 0, None)
