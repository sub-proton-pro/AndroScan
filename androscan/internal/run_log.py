"""Run logger: writes task updates, LLM busy state, and thinking to run.log. Optional UI sink for CLI/web."""

from pathlib import Path
from typing import Callable, Optional

RUN_LOG_FILENAME = "run.log"


def _default_ui_sink(_kind: str, _payload: object) -> None:
    pass


class RunLogger:
    """Writes to run_folder/run.log. Optional ui_sink(kind, payload) for CLI or web."""

    def __init__(
        self,
        run_folder: Path,
        verbosity: int = 1,
        ui_sink: Optional[Callable[[str, object], None]] = None,
    ) -> None:
        self.run_folder = run_folder
        self.verbosity = verbosity  # 1 = default (-v), 2 = -vv
        self.log_path = run_folder / RUN_LOG_FILENAME
        self._ui_sink = ui_sink or _default_ui_sink

    def task_update(self, message: str) -> None:
        """Current task (e.g. for spinner line). Always written to run.log."""
        line = f"[task] {message}"
        self._append_log(line)
        self._ui_sink("task", message)

    def llm_busy(self, busy: bool) -> None:
        """LLM is working (show spinner or equivalent). Logged to run.log."""
        line = "[llm_busy]" if busy else "[llm_idle]"
        self._append_log(line)
        self._ui_sink("llm_busy", busy)

    def llm_thinking(self, thinking_text: str) -> None:
        """LLM thinking content. Always written to run.log. Shown in UI only when verbosity >= 2."""
        if not thinking_text.strip():
            return
        self._append_log(f"[thinking]\n{thinking_text}")
        if self.verbosity >= 2:
            self._ui_sink("thinking", thinking_text)

    def log_retry(self, reason: str, detail: str) -> None:
        """Log a retry (timeout or num_predict)."""
        self._append_log(f"[retry] {reason}: {detail}")
        self._ui_sink("retry", {"reason": reason, "detail": detail})

    def _append_log(self, content: str) -> None:
        try:
            self.run_folder.mkdir(parents=True, exist_ok=True)
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(content + "\n")
        except OSError:
            pass
