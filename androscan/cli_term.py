"""Terminal output helpers: colored messages for CLI. No-op when stdout is not a TTY."""

import sys


def _use_color() -> bool:
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


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
