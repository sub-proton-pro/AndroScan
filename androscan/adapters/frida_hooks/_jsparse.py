"""``pyjsparser`` wrapper for Hook Lab JS pre-validation (DEC-023 risk #1 mitigation).

The Inject button in 4.5's Hook Lab UI stays disabled until the
rendered Frida JS parses cleanly. This module is the seam that
performs that check; it returns a structured ``ParseResult`` so the
frontend can place an inline error marker in Monaco at the offending
line (column not exposed by ``pyjsparser`` — we surface ``None`` for
column and let Monaco render a line-wide marker).

Like :func:`androscan.adapters.frida_client._frida_python`,
``pyjsparser`` is imported lazily through the
:func:`_pyjsparser_module` test seam so the default ``pytest`` suite
(which doesn't install the ``[frida]`` extra) can monkeypatch a stub
without ``ImportError``. ``pyjsparser`` itself is pure Python and ~30 KB
— if a future operator wants to ship hooks without the ``[frida]``
extra, the seam degrades to ``ParseResult(ok=False, available=False,
…)`` rather than blowing up with an exception.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional


_LINE_RE = re.compile(r"^Line\s+(\d+):\s*(.+)$", re.IGNORECASE)


@dataclass(frozen=True)
class ParseResult:
    """Outcome of a single ``parse_frida_js`` call.

    ``ok=True`` is the only success state. The other three fields are
    only meaningful on failure:

    * ``error`` — human-readable message; lifted verbatim from
      ``pyjsparser.pyjsparserdata.JsSyntaxError`` so operators see the
      same error text Frida itself would surface for these cases.
    * ``line`` — 1-indexed line number from the parser; ``None`` if the
      message format didn't match the expected ``Line N:`` shape.
    * ``column`` — always ``None`` in v1; ``pyjsparser`` doesn't expose
      a column, and parsing the message text for column hints would be
      brittle. Monaco's marker still lands on the right line; column
      precision can be added later if telemetry shows operators want
      it.

    ``available`` distinguishes "JS is broken" from "the parser itself
    isn't installed" — the latter is a graceful degradation case
    (``[frida]`` extra not installed) that the route layer surfaces as
    a soft warning rather than a hard 503.
    """

    ok: bool
    error: Optional[str]
    line: Optional[int]
    column: Optional[int]
    available: bool


def _pyjsparser_module() -> Any:
    """Lazy import of ``pyjsparser``. Test seam — see module docstring."""
    import pyjsparser  # local import so the [frida] extra stays optional

    return pyjsparser


def parse_frida_js(source: str) -> ParseResult:
    """Parse ``source`` as ECMAScript via ``pyjsparser``.

    Three outcomes:

    * **OK** — ``ParseResult(ok=True, error=None, line=None, column=None, available=True)``.
    * **Syntax error** — ``ok=False``, ``error`` is the message, ``line``
      extracted from the message when present.
    * **Parser unavailable** — ``ok=False``, ``available=False``,
      ``error`` carries an install hint. Callers (the Inject route /
      the Hook Lab UI) treat this as a soft warning that lets Inject
      proceed under operator-acknowledged risk; we don't want a
      missing ``[frida]`` extra to brick the whole feature.

    A non-string ``source`` returns ``ok=False`` with a structured
    ``error`` rather than raising — keeps the route handler thin.
    """

    if not isinstance(source, str):
        return ParseResult(
            ok=False,
            error=f"source must be str, got {type(source).__name__}",
            line=None,
            column=None,
            available=True,
        )
    if not source.strip():
        return ParseResult(
            ok=False,
            error="empty source (renderer produced no JS)",
            line=None,
            column=None,
            available=True,
        )

    try:
        pyjsparser_mod = _pyjsparser_module()
    except ImportError:
        return ParseResult(
            ok=False,
            error=(
                "pyjsparser not installed; install the optional extra with "
                "`pip install -e '.[frida]'` to enable Inject pre-validation"
            ),
            line=None,
            column=None,
            available=False,
        )

    parser = pyjsparser_mod.PyJsParser()
    try:
        parser.parse(source)
    except Exception as exc:  # JsSyntaxError + any pyjsparser internal raise
        msg = str(exc)
        line, cleaned = _extract_line(msg)
        return ParseResult(
            ok=False,
            error=cleaned,
            line=line,
            column=None,
            available=True,
        )
    return ParseResult(ok=True, error=None, line=None, column=None, available=True)


def _extract_line(message: str) -> tuple[Optional[int], str]:
    """Return ``(line, cleaned_message)`` from ``pyjsparser``'s ``"Line N: msg"`` format.

    Falls back to ``(None, message)`` when the format doesn't match —
    ``pyjsparser`` is generally consistent, but defensive-default in
    case a future version changes the wording.
    """

    m = _LINE_RE.match(message.strip())
    if m is None:
        return None, message.strip()
    try:
        line = int(m.group(1))
    except ValueError:  # pragma: no cover - regex guarantees digits
        return None, message.strip()
    return line, m.group(2).strip()


__all__ = ["ParseResult", "parse_frida_js"]
