"""Unit tests for :mod:`androscan.web.trace_summary` — the
cache-lookup / cache-store / class-name conversion / cache-key
derivation helpers that the dynamic-trace WebSocket multiplex
(Phase 13 / DEC-029, sub-step 13.3) delegates summary handling to.

The async :func:`default_summary_callable` is exercised via a
monkeypatched :func:`androscan.llm.client.complete` so the tests
stay hermetic + fast (no real LLM round-trip). End-to-end coverage
of the WS-handler-against-callable wiring lives in
:mod:`tests.test_trace_routes`.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from androscan.config import Config
from androscan.web.trace_summary import (
    SUMMARY_FAILED_KIND,
    SUMMARY_PENDING_KIND,
    SUMMARY_READY_KIND,
    SUMMARY_SKILL_ID,
    SUMMARY_TIMEOUT_S,
    default_summary_callable,
    java_class_to_smali,
    lookup_cached_summary,
    method_key,
    store_summary,
    summary_cache_params,
)


# ---------------------------------------------------------------------------
# java_class_to_smali


def test_java_to_smali_dotted_to_descriptor() -> None:
    """``com.example.Foo`` → ``Lcom/example/Foo;`` is the canonical
    static-analysis class-key shape (matches
    :attr:`MethodRef.smali_signature`'s class component)."""
    assert java_class_to_smali("com.example.Foo") == "Lcom/example/Foo;"


def test_java_to_smali_idempotent_on_smali_input() -> None:
    """An already-Smali string passes through unchanged so the helper
    can be called from sites that don't know which form they're
    holding (the WS handler reads the ``payload.class`` field which
    the ``behavior_trace_multi`` template emits in Java form, but
    older stub events / hand-built tests may pass either)."""
    assert java_class_to_smali("Lcom/example/Foo;") == "Lcom/example/Foo;"


def test_java_to_smali_handles_inner_class_dollar() -> None:
    """``com.example.Foo$Bar`` → ``Lcom/example/Foo$Bar;`` — only
    package-separator dots flip to slashes; inner-class ``$`` stays."""
    assert java_class_to_smali("com.example.Foo$Bar") == "Lcom/example/Foo$Bar;"


def test_java_to_smali_empty_input_returns_empty() -> None:
    """Empty / whitespace-only input → empty string. The route layer
    guards against summary firing on empty class names; the helper
    just stays defensively no-op-on-falsy."""
    assert java_class_to_smali("") == ""
    assert java_class_to_smali("   ") == ""


# ---------------------------------------------------------------------------
# method_key


def test_method_key_round_trips_against_smali_signature() -> None:
    """``method_key(class, method, descriptor)`` matches
    :attr:`MethodRef.smali_signature` shape — used by the WS handler
    as a per-method dedup token + by 13.6's frontend to correlate
    summary events back to a flowchart node."""
    key = method_key("com.example.Foo", "bar", "(I)Z")
    assert key == "Lcom/example/Foo;->bar(I)Z"


def test_method_key_handles_empty_descriptor() -> None:
    """``descriptor`` may be empty for events emitted before the
    serialiser populated it (shouldn't happen with the locked 13.1
    wire shape, but we stay robust)."""
    key = method_key("com.example.Foo", "bar", "")
    assert key == "Lcom/example/Foo;->bar"


# ---------------------------------------------------------------------------
# summary_cache_params


def test_summary_cache_params_full_shape() -> None:
    """Cache key params carry the four-tuple from the spec —
    ``(app_sha, class_smali, method_name, descriptor)``."""
    params = summary_cache_params("deadbeef", "com.example.Foo", "bar", "(I)Z")
    assert params == {
        "app_sha": "deadbeef",
        "class_smali": "Lcom/example/Foo;",
        "method_name": "bar",
        "descriptor": "(I)Z",
    }


def test_summary_cache_params_normalises_class_form() -> None:
    """Java form + Smali form produce identical cache params — keeps
    cache writes from a 13.3 caller (Java form on the wire) and
    cache reads from a hypothetical 13.4 caller (already-Smali
    `MethodRef.smali_signature`-derived) byte-equal."""
    a = summary_cache_params("x", "com.example.Foo", "bar", "(I)Z")
    b = summary_cache_params("x", "Lcom/example/Foo;", "bar", "(I)Z")
    assert a == b


# ---------------------------------------------------------------------------
# Cache lookup / store round-trip


def test_cache_round_trip(tmp_path: Path) -> None:
    """Store → lookup returns the stored summary text. Pins the
    contract so a future refactor of either side surfaces here."""
    app_id = "myapp"
    store_summary(
        run_folder_root=tmp_path,
        app_id=app_id,
        run_folder_name="some-run",
        app_sha="deadbeef",
        class_java_or_smali="com.example.Foo",
        method_name="bar",
        descriptor="(I)Z",
        summary_text="bar() validates the integer input is positive.",
    )
    out = lookup_cached_summary(
        run_folder_root=tmp_path,
        app_id=app_id,
        app_sha="deadbeef",
        class_java_or_smali="com.example.Foo",
        method_name="bar",
        descriptor="(I)Z",
    )
    assert out == "bar() validates the integer input is positive."


def test_cache_lookup_miss_returns_none(tmp_path: Path) -> None:
    """No cache file at all → ``None`` (route layer treats this as
    "fire the LLM")."""
    out = lookup_cached_summary(
        run_folder_root=tmp_path,
        app_id="myapp",
        app_sha="deadbeef",
        class_java_or_smali="com.example.Foo",
        method_name="bar",
        descriptor="(I)Z",
    )
    assert out is None


def test_cache_lookup_treats_empty_summary_as_miss(tmp_path: Path) -> None:
    """An entry whose ``result_text`` is empty / whitespace-only is
    treated as a miss — the LLM occasionally returns nothing on
    very short prompts and the route should re-fire rather than
    serve a useless cached blank."""
    app_id = "myapp"
    # Hand-write the cache with an empty result to simulate a stale
    # entry from a flaky LLM round.
    cache_path = tmp_path / app_id / "skill_results_cache.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    key = (
        SUMMARY_SKILL_ID
        + "\n"
        + json.dumps(
            summary_cache_params("deadbeef", "com.example.Foo", "bar", "(I)Z"),
            sort_keys=True,
        )
    )
    cache_path.write_text(
        json.dumps({
            "by_key": {
                key: {
                    "serial": 0,
                    "run_folder": "x",
                    "skill": SUMMARY_SKILL_ID,
                    "params": summary_cache_params("deadbeef", "com.example.Foo", "bar", "(I)Z"),
                    "result_text": "   ",
                }
            },
            "next_serial": 1,
        }),
        encoding="utf-8",
    )
    out = lookup_cached_summary(
        run_folder_root=tmp_path,
        app_id=app_id,
        app_sha="deadbeef",
        class_java_or_smali="com.example.Foo",
        method_name="bar",
        descriptor="(I)Z",
    )
    assert out is None


def test_cache_store_no_op_on_empty_summary(tmp_path: Path) -> None:
    """Symmetric to the lookup discipline — never persist an empty
    summary. Pin so a future refactor doesn't accidentally start
    writing blanks (would silently poison the cache)."""
    app_id = "myapp"
    store_summary(
        run_folder_root=tmp_path,
        app_id=app_id,
        run_folder_name="some-run",
        app_sha="deadbeef",
        class_java_or_smali="com.example.Foo",
        method_name="bar",
        descriptor="(I)Z",
        summary_text="   ",
    )
    # No file written.
    cache_file = tmp_path / app_id / "skill_results_cache.json"
    assert not cache_file.exists()


def test_cache_separates_overloads_by_descriptor(tmp_path: Path) -> None:
    """``validate(I)Z`` and ``validate(Ljava/lang/String;)Z`` get
    separate cache entries — the ``behavior_trace_multi`` template
    hooks every overload, so each overload needs its own summary."""
    app_id = "myapp"
    store_summary(
        run_folder_root=tmp_path, app_id=app_id, run_folder_name="r",
        app_sha="x", class_java_or_smali="com.example.Foo",
        method_name="validate", descriptor="(I)Z",
        summary_text="int overload",
    )
    store_summary(
        run_folder_root=tmp_path, app_id=app_id, run_folder_name="r",
        app_sha="x", class_java_or_smali="com.example.Foo",
        method_name="validate", descriptor="(Ljava/lang/String;)Z",
        summary_text="string overload",
    )
    a = lookup_cached_summary(
        run_folder_root=tmp_path, app_id=app_id, app_sha="x",
        class_java_or_smali="com.example.Foo",
        method_name="validate", descriptor="(I)Z",
    )
    b = lookup_cached_summary(
        run_folder_root=tmp_path, app_id=app_id, app_sha="x",
        class_java_or_smali="com.example.Foo",
        method_name="validate", descriptor="(Ljava/lang/String;)Z",
    )
    assert a == "int overload"
    assert b == "string overload"


def test_cache_separates_apps_by_sha(tmp_path: Path) -> None:
    """Same class/method/descriptor across two ``app_sha``s gets two
    cache entries — operator iterates on a target app + the new
    APK build's summaries don't collide with the old build's."""
    app_id = "myapp"
    store_summary(
        run_folder_root=tmp_path, app_id=app_id, run_folder_name="r",
        app_sha="sha-v1", class_java_or_smali="com.example.Foo",
        method_name="bar", descriptor="(I)Z",
        summary_text="v1 summary",
    )
    store_summary(
        run_folder_root=tmp_path, app_id=app_id, run_folder_name="r",
        app_sha="sha-v2", class_java_or_smali="com.example.Foo",
        method_name="bar", descriptor="(I)Z",
        summary_text="v2 summary",
    )
    v1 = lookup_cached_summary(
        run_folder_root=tmp_path, app_id=app_id, app_sha="sha-v1",
        class_java_or_smali="com.example.Foo",
        method_name="bar", descriptor="(I)Z",
    )
    v2 = lookup_cached_summary(
        run_folder_root=tmp_path, app_id=app_id, app_sha="sha-v2",
        class_java_or_smali="com.example.Foo",
        method_name="bar", descriptor="(I)Z",
    )
    assert v1 == "v1 summary"
    assert v2 == "v2 summary"


# ---------------------------------------------------------------------------
# default_summary_callable — async wrapper around llm_client.complete


def test_default_summary_callable_returns_complete_content(monkeypatch) -> None:
    """The async callable bounces the sync :func:`complete` to the
    default executor and returns its ``content``. Monkeypatched
    here so no real LLM is contacted."""

    captured: list[dict[str, Any]] = []

    class _StubResult:
        content = "foo() validates the int input is non-negative."
        thinking = ""
        metadata = {}

    def _stub_complete(prompt: str, **kwargs: Any) -> _StubResult:
        captured.append({"prompt": prompt, **kwargs})
        return _StubResult()

    monkeypatch.setattr("androscan.llm.client.complete", _stub_complete)

    callable_ = default_summary_callable(lambda: Config.default())
    result = asyncio.run(
        callable_("Lcom/example/Foo;", "bar", "(I)Z")
    )
    assert result == "foo() validates the int input is non-negative."
    assert len(captured) == 1
    call = captured[0]
    # The prompt mentions all three method-shape fields so the LLM
    # has the context it needs from the name + signature alone (v1
    # is name-quality only; 13.4 will enrich with decompiled source).
    assert "Lcom/example/Foo;" in call["prompt"]
    assert "bar" in call["prompt"]
    assert "(I)Z" in call["prompt"]
    # Prose-mode + non-streaming: pinned so a future refactor can't
    # accidentally start streaming JSON back into the WS handler.
    assert call["response_format"] is None
    assert call["stream"] is False


def test_default_summary_callable_strips_whitespace(monkeypatch) -> None:
    """Trailing newlines / leading spaces from the LLM are stripped
    so the cache + the WS payload carry tight prose."""

    class _StubResult:
        content = "  \nfoo() does X.\n  "
        thinking = ""
        metadata = {}

    monkeypatch.setattr(
        "androscan.llm.client.complete", lambda *a, **kw: _StubResult()
    )

    callable_ = default_summary_callable(lambda: Config.default())
    result = asyncio.run(callable_("Lcom/example/Foo;", "bar", "(I)Z"))
    assert result == "foo() does X."


def test_default_summary_callable_propagates_timeout(monkeypatch) -> None:
    """An LLM call that takes longer than ``timeout_s`` raises
    :class:`asyncio.TimeoutError`, which the WS handler translates
    into ``summary_failed`` with ``error: "summary_timeout"``."""

    import time

    class _StubResult:
        content = "shouldnt-reach-here"
        thinking = ""
        metadata = {}

    def _slow_complete(prompt: str, **kwargs: Any) -> _StubResult:
        time.sleep(0.5)
        return _StubResult()

    monkeypatch.setattr("androscan.llm.client.complete", _slow_complete)
    callable_ = default_summary_callable(lambda: Config.default(), timeout_s=0.05)
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(callable_("Lcom/example/Foo;", "bar", "(I)Z"))


def test_default_summary_callable_propagates_exception(monkeypatch) -> None:
    """LLM-side exceptions (network failure, model not loaded, etc.)
    propagate so the WS handler can translate them into
    ``summary_failed`` with the operator-facing error message."""

    def _boom(prompt: str, **kwargs: Any) -> Any:
        raise RuntimeError("ollama gone")

    monkeypatch.setattr("androscan.llm.client.complete", _boom)
    callable_ = default_summary_callable(lambda: Config.default())
    with pytest.raises(RuntimeError, match="ollama gone"):
        asyncio.run(callable_("Lcom/example/Foo;", "bar", "(I)Z"))


# ---------------------------------------------------------------------------
# Wire-shape constants — pin so a future rename surfaces here


def test_event_kind_constants_locked() -> None:
    """The three new ``kind`` values are operator-facing through the
    UI; renaming any of them breaks 13.6's frontend dispatcher.
    Pin so any rename has to touch this test."""
    assert SUMMARY_PENDING_KIND == "summary_pending"
    assert SUMMARY_READY_KIND == "summary_ready"
    assert SUMMARY_FAILED_KIND == "summary_failed"


def test_summary_skill_id_locked() -> None:
    """Cache discriminator — must stay byte-equal across 13.3 → 13.4
    so 13.4's skill-tier writes hit the same cache key as 13.3's
    inline-LLM writes (no cache rebuild required at the cutover)."""
    assert SUMMARY_SKILL_ID == "summarise_method"


def test_default_timeout_locked() -> None:
    """30 s default is operator-facing UX (the timeout is the
    upper bound on how long a method can sit in
    ``summary_pending`` before flipping to ``summary_failed``).
    Pin so a future tuning conversation has to surface here."""
    assert SUMMARY_TIMEOUT_S == 30.0
