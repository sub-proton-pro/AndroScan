"""Tests for :mod:`androscan.adapters.frida_hooks._jsparse`.

The wrapper is the seam the Hook Lab uses to gate the Inject button —
it must:

* return a structured ``ParseResult`` for clean JS,
* surface ``Line N`` errors with the line extracted,
* degrade gracefully when ``pyjsparser`` isn't installed (the
  ``[frida]`` extra is optional; the workbench must not refuse to
  serve the route just because the parser is missing).

We exercise the install-missing path by monkeypatching the
``_pyjsparser_module`` seam to raise ``ImportError`` — same idea as
``_frida_python`` in ``test_frida_client.py``.
"""

from __future__ import annotations

import pytest

from androscan.adapters.frida_hooks import _jsparse


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_clean_js_parses_ok() -> None:
    js = "Java.perform(function () { console.log('ok'); });"
    res = _jsparse.parse_frida_js(js)
    assert res.ok is True
    assert res.error is None
    assert res.line is None
    assert res.column is None
    assert res.available is True


def test_multi_statement_js_parses_ok() -> None:
    js = """
    Java.perform(function () {
      var Cipher = Java.use('javax.crypto.Cipher');
      Cipher.init.implementation = function (mode, key) {
        send({phase: 'init'});
        return this.init(mode, key);
      };
    });
    """
    res = _jsparse.parse_frida_js(js)
    assert res.ok is True


# ---------------------------------------------------------------------------
# Syntax errors
# ---------------------------------------------------------------------------


def test_syntax_error_extracts_line() -> None:
    res = _jsparse.parse_frida_js("var x = ;;;")
    assert res.ok is False
    assert res.available is True
    assert res.line == 1
    assert res.error and "Unexpected" in res.error
    # ``column`` is intentionally None in v1 — pyjsparser doesn't expose it.
    assert res.column is None


def test_unclosed_brace_yields_line_2() -> None:
    js = "Java.perform(function () {\n  console.log(1);\n  // missing close brace"
    res = _jsparse.parse_frida_js(js)
    assert res.ok is False
    # Either line 2 or 3 depending on parser; both are acceptable as
    # long as a number was extracted.
    assert res.line is not None
    assert res.line >= 2


# ---------------------------------------------------------------------------
# Defensive input handling
# ---------------------------------------------------------------------------


def test_empty_string_is_not_ok() -> None:
    res = _jsparse.parse_frida_js("")
    assert res.ok is False
    assert res.error and "empty" in res.error.lower()
    assert res.available is True  # the parser was reachable; the input was bad


def test_whitespace_only_is_not_ok() -> None:
    res = _jsparse.parse_frida_js("   \n\t  ")
    assert res.ok is False
    assert res.error and "empty" in res.error.lower()


def test_non_string_input_returns_structured_error() -> None:
    res = _jsparse.parse_frida_js(123)  # type: ignore[arg-type]
    assert res.ok is False
    assert res.error and "str" in res.error
    assert res.available is True


# ---------------------------------------------------------------------------
# pyjsparser unavailable (graceful degradation)
# ---------------------------------------------------------------------------


def test_pyjsparser_missing_returns_unavailable_result(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing ``pyjsparser`` must NOT raise — it returns ``available=False``.

    The route layer treats this as a soft warning (Inject can still
    proceed under operator acknowledgment) rather than blocking the
    whole feature behind an optional dep.
    """
    def _raise() -> object:
        raise ImportError("No module named 'pyjsparser'")

    monkeypatch.setattr(_jsparse, "_pyjsparser_module", _raise)

    res = _jsparse.parse_frida_js("Java.perform(function () {});")
    assert res.ok is False
    assert res.available is False
    assert res.error and "pyjsparser" in res.error.lower()
    # No line/col when the parser was never reached.
    assert res.line is None
    assert res.column is None


# ---------------------------------------------------------------------------
# _extract_line helper
# ---------------------------------------------------------------------------


def test_extract_line_handles_pyjsparser_format() -> None:
    line, msg = _jsparse._extract_line("Line 7: Unexpected token ;")
    assert line == 7
    assert msg == "Unexpected token ;"


def test_extract_line_falls_back_when_format_does_not_match() -> None:
    line, msg = _jsparse._extract_line("something blew up")
    assert line is None
    assert msg == "something blew up"


def test_extract_line_strips_whitespace() -> None:
    line, msg = _jsparse._extract_line("  Line 3: oops   ")
    assert line == 3
    assert msg == "oops"
