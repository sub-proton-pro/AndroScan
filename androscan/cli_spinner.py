"""Terminal spinner for long-running operations. Dim italic style; no-op when not a TTY."""

import sys
import threading
import time
from contextlib import contextmanager
from typing import Optional

# Braille frames
_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
_INTERVAL = 0.12

# Dim italic ANSI (3=italic, 90=dim)
_DIM_ITALIC = "\033[3;90m"
_RESET = "\033[0m"

_active: Optional["_Spinner"] = None


def _is_tty() -> bool:
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


class _Spinner:
    """Background-threaded spinner that updates a single terminal line in dim italic."""

    def __init__(self, message: str):
        self._message = message
        self._running = False
        self._paused = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    def start(self) -> None:
        self._running = True
        self._paused = False
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()

    def _spin(self) -> None:
        idx = 0
        while self._running:
            if self._paused:
                time.sleep(_INTERVAL)
                continue
            frame = _FRAMES[idx % len(_FRAMES)]
            with self._lock:
                msg = self._message
            line = f"\r    {_DIM_ITALIC}{frame} {msg}{_RESET}"
            sys.stdout.write(line)
            sys.stdout.flush()
            idx += 1
            time.sleep(_INTERVAL)

    def update(self, message: str) -> None:
        """Change the spinner message while it's running."""
        with self._lock:
            self._message = message

    def pause(self) -> None:
        """Temporarily stop animation and clear the spinner line."""
        self._paused = True
        time.sleep(_INTERVAL + 0.02)
        sys.stdout.write("\r" + " " * 80 + "\r")
        sys.stdout.flush()

    def resume(self) -> None:
        """Resume animation after a pause."""
        self._paused = False

    def stop(self, final_message: Optional[str] = None) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=1)
        if final_message:
            sys.stdout.write("\r" + " " * 80 + "\r")
            sys.stdout.write(f"    ✓ {final_message}\n")
        else:
            sys.stdout.write("\r" + " " * 80 + "\r")
        sys.stdout.flush()


def pause_active() -> None:
    """Pause the currently active spinner (if any) to allow clean printing."""
    global _active
    if _active is not None:
        _active.pause()


def resume_active() -> None:
    """Resume the currently active spinner after printing."""
    global _active
    if _active is not None:
        _active.resume()


@contextmanager
def spinner(message: str, done_message: Optional[str] = None):
    """Context manager: animated spinner with message. Dim italic when TTY.

    If not a TTY, no spinner is shown (yields a no-op object with update() that does nothing).
    """
    global _active
    if not _is_tty():
        yield _NoOpSpinner()
        return
    s = _Spinner(message)
    s.start()
    _active = s
    try:
        yield s
    finally:
        _active = None
        s.stop(done_message)


class _NoOpSpinner:
    """No-op spinner when not a TTY."""

    def update(self, message: str) -> None:
        pass
