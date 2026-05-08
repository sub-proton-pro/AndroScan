"""Unit tests for :mod:`androscan.skills.summarise_method` — the
LLM-tier skill that backs Phase 13's per-method summary multiplex
(DEC-029, sub-step 13.4).

Coverage matrix:

* **Registration** — confirms the skill is auto-discovered by
  :mod:`androscan.skills` + the meta is shape-correct
  (``tier="llm"`` + ``requires_confirmation=False``).
* **Pure helpers** — :func:`smali_to_source_rel_path`,
  :func:`build_summary_prompt`, ``_normalise_descriptor`` all have
  test pins so the v1 contract stays locked. Cross-checked against
  the byte-equal cache-key requirement vs.
  :mod:`androscan.web.trace_summary`.
* **Skill happy path** — runs ``execute()`` against a stub
  decompile cache + a monkeypatched
  :func:`androscan.llm.client.complete`; asserts the summary text
  is returned + the cache was written.
* **Cache hit** — second invocation reads from
  :mod:`androscan.internal.skill_results_cache` and skips the LLM
  call entirely.
* **Fail-open paths** — missing source, missing app context,
  malformed Smali class — the skill returns operator-readable text
  WITHOUT crashing; LLM-side failures (network down, empty
  response) surface as ``success=False``.
* **Cache-key invariants** — descriptor whitespace differences hit
  the same cache slot; per-app-sha separation prevents cross-APK
  pollution.
* **End-to-end production-adapter wiring** — the
  :func:`androscan.web.app._build_summarise_method_callable` factory
  returns a callable with the locked four-arg signature that
  bounces through the registered skill.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from androscan.config import Config
from androscan.internal.app_meta import save_app_meta
from androscan.internal import skill_results_cache
from androscan.skills import _REGISTRY, execute as execute_skill
from androscan.skills.base import SkillContext, SkillResult
from androscan.skills.summarise_method import (
    SOURCE_BODY_BUDGET_BYTES,
    SUMMARY_SKILL_ID,
    USER_PROMPT_HEAD_TEMPLATE,
    _normalise_descriptor,
    _summary_cache_params,
    build_summary_prompt,
    execute,
    smali_to_source_rel_path,
)
from androscan.web.app import _build_summarise_method_callable
from androscan.web.trace_summary import (
    SUMMARY_SKILL_ID as TRACE_SUMMARY_SKILL_ID,
    summary_cache_params as trace_summary_cache_params,
)


# ---------------------------------------------------------------------------
# Registration


def test_skill_registered_in_registry() -> None:
    """``summarise_method`` is auto-discovered + lives in the
    skill registry alongside the other LLM-tier skills."""
    assert "summarise_method" in _REGISTRY


def test_skill_meta_shape_locked() -> None:
    """The skill's ``SkillMeta`` is shape-correct: ``tier="llm"``
    (so :func:`list_llm_skills` surfaces it to the agentic loop's
    catalog), ``requires_confirmation=False`` (read-only — no
    operator gate), and the params schema carries the four
    fields the WS handler injects."""
    meta, _fn = _REGISTRY["summarise_method"]
    assert meta.name == "summarise_method"
    assert meta.tier == "llm"
    assert meta.requires_confirmation is False
    assert set(meta.params_schema.keys()) == {
        "class_smali", "method_name", "descriptor", "app_id",
    }


def test_skill_id_byte_equal_to_trace_summary_constant() -> None:
    """The skill's ``SUMMARY_SKILL_ID`` is byte-equal to the
    web-tier constant in :mod:`androscan.web.trace_summary` —
    pinned so a future rename (if ever) lands in BOTH constants
    or the cache silently splits across two skill-id slots."""
    assert SUMMARY_SKILL_ID == TRACE_SUMMARY_SKILL_ID


# ---------------------------------------------------------------------------
# Pure helpers — smali_to_source_rel_path


def test_smali_to_source_rel_path_top_level() -> None:
    """``Lcom/example/Foo;`` → ``com/example/Foo.java`` — direct
    translation matching jadx's bulk decompile output layout."""
    assert smali_to_source_rel_path("Lcom/example/Foo;") == "com/example/Foo.java"


def test_smali_to_source_rel_path_inner_class_falls_back_to_outer() -> None:
    """Inner classes resolve to the OUTER class file — jadx emits
    inner classes inside the outer-class file, so the brace-matching
    method-extraction handles them naturally."""
    rel = smali_to_source_rel_path("Lcom/example/Foo$Bar;")
    assert rel == "com/example/Foo.java"


def test_smali_to_source_rel_path_double_nested_inner() -> None:
    """``Foo$Bar$Baz`` still resolves to the outermost class file."""
    rel = smali_to_source_rel_path("Lcom/example/Foo$Bar$Baz;")
    assert rel == "com/example/Foo.java"


def test_smali_to_source_rel_path_malformed_returns_none() -> None:
    """Missing ``L`` or ``;`` sentinels → ``None`` so callers can
    fail-open on bad data rather than raise."""
    assert smali_to_source_rel_path("com.example.Foo") is None
    assert smali_to_source_rel_path("Lcom/example/Foo") is None
    assert smali_to_source_rel_path("com/example/Foo;") is None
    assert smali_to_source_rel_path("L;") is None
    assert smali_to_source_rel_path("") is None


# ---------------------------------------------------------------------------
# Pure helpers — build_summary_prompt


def test_build_summary_prompt_no_source_uses_name_only_clause() -> None:
    """Without source, the prompt asks the LLM to summarise from
    the name + signature alone + flags the absence so the LLM
    knows to express uncertainty rather than confabulate."""
    prompt = build_summary_prompt(
        "Lcom/example/Foo;", "bar", "(I)Z", method_source=None
    )
    assert "Lcom/example/Foo;" in prompt
    assert "bar" in prompt
    assert "(I)Z" in prompt
    assert "not available" in prompt.lower() or "name + signature" in prompt.lower()


def test_build_summary_prompt_with_source_includes_body() -> None:
    """When source is provided, it lands inside a fenced ```Source:```
    block so the LLM can ground its summary in the actual code."""
    body = "public boolean validatePin(int pin) { return pin >= 0 && pin <= 9999; }"
    prompt = build_summary_prompt(
        "Lcom/example/Foo;", "validatePin", "(I)Z", method_source=body
    )
    assert body in prompt
    assert "Source:" in prompt


def test_build_summary_prompt_truncates_oversized_body() -> None:
    """A body larger than :data:`SOURCE_BODY_BUDGET_BYTES` is
    truncated with an explicit operator-visible marker so the LLM
    knows the body was clipped (avoids confidently summarising a
    half-truncated method as if it had seen the whole thing)."""
    huge = "// padding\n" * (SOURCE_BODY_BUDGET_BYTES // 10 + 100)
    prompt = build_summary_prompt(
        "Lcom/example/Foo;", "huge", "()V", method_source=huge
    )
    assert "(truncated for prompt budget)" in prompt
    assert len(prompt.encode("utf-8")) < len(huge.encode("utf-8")) + 1024


def test_build_summary_prompt_head_template_carries_all_three_ids() -> None:
    """The fixed ``USER_PROMPT_HEAD_TEMPLATE`` carries class +
    method + descriptor on three lines — this layout is part of
    the v1 contract (operators dogfooding the trace WS see the
    rendered summary inline; the LLM relies on the head shape
    being parseable)."""
    head = USER_PROMPT_HEAD_TEMPLATE.format(
        class_smali="Lx/Y;", method_name="z", descriptor="()V",
    )
    assert "Class:" in head
    assert "Method:" in head
    assert "Descriptor:" in head


# ---------------------------------------------------------------------------
# Pure helpers — descriptor normalisation + cache key


def test_normalise_descriptor_strips_internal_whitespace() -> None:
    """Whitespace inside a descriptor is invisible to Smali —
    pinning the normalisation makes the cache hit-rate
    predictable regardless of stray spaces injected by upstream
    LLM tools."""
    assert _normalise_descriptor("  ( I  ) Z  ") == "(I)Z"
    assert _normalise_descriptor("(I)Z") == "(I)Z"
    assert _normalise_descriptor("") == ""


def test_summary_cache_params_byte_equal_to_trace_summary_helper() -> None:
    """The skill's cache params dict is byte-equal to the web-tier
    helper's — pinned so 13.3's WS-tier writes and 13.4's
    skill-tier writes collide on the SAME cache slot. If this
    test ever fails, an APK upgrade no longer invalidates the
    cache (or the inverse — cache splits across the cutover)."""
    a = _summary_cache_params(
        "deadbeef", "Lcom/example/Foo;", "bar", "(I)Z",
    )
    b = trace_summary_cache_params(
        "deadbeef", "Lcom/example/Foo;", "bar", "(I)Z",
    )
    assert a == b


# ---------------------------------------------------------------------------
# Test fixtures — minimal app_dir layout + monkeypatched LLM


_APP_SHA = "00abcdef1234"


def _make_app_with_decompile(tmp_path: Path, *, with_source: bool = True) -> tuple[Path, Path]:
    """Set up ``apps/<app_id>/`` + ``app_meta.json`` + a decompile
    cache that ``decompile_cache.get_status`` will report as
    ``ready``. Returns ``(apps_root, app_dir)``."""
    apps_root = tmp_path / "apps"
    app_id = "test-app"
    app_dir = apps_root / app_id
    app_dir.mkdir(parents=True)

    save_app_meta(app_dir, _APP_SHA, dossier={"apk_info": {"package": "com.example"}})

    cache_root = app_dir / ".decompiled" / _APP_SHA
    cache_root.mkdir(parents=True)
    sources = cache_root / "sources"
    sources.mkdir()
    (cache_root / "index.json").write_text(
        json.dumps({"status": "ready", "apk_path": "/tmp/app.apk", "file_count": 1}),
        encoding="utf-8",
    )

    if with_source:
        cls_dir = sources / "com" / "example"
        cls_dir.mkdir(parents=True)
        (cls_dir / "Foo.java").write_text(
            "package com.example;\n"
            "public class Foo {\n"
            "  public boolean validatePin(int pin) {\n"
            "    return pin >= 0 && pin <= 9999;\n"
            "  }\n"
            "}\n",
            encoding="utf-8",
        )

    return apps_root, app_dir


def _make_skill_context(app_dir: Path) -> SkillContext:
    """Build a SkillContext with a synthetic run_folder that
    matches the production adapter's path-shape — ``apps/<app_id>/
    __ws_trace__/`` → walks back to ``apps/`` root via
    ``run_folder.parent.parent``."""
    return SkillContext(
        config=Config.default(),
        run_folder=app_dir / "__ws_trace__",
        dossier_dict=None,
        apk_path=None,
    )


# ---------------------------------------------------------------------------
# Skill happy path


def test_execute_happy_path_calls_llm_returns_summary(monkeypatch, tmp_path: Path) -> None:
    """Cache empty → LLM is called with a source-enriched prompt
    → result is returned as ``SkillResult.text``."""
    apps_root, app_dir = _make_app_with_decompile(tmp_path)
    captured: dict[str, Any] = {}

    class _StubResult:
        content = "Validates a 4-digit PIN; returns true when in range."
        thinking = ""
        metadata = {}

    def _stub_complete(prompt: str, **kwargs: Any) -> _StubResult:
        captured["prompt"] = prompt
        captured["kwargs"] = kwargs
        return _StubResult()

    monkeypatch.setattr("androscan.llm.client.complete", _stub_complete)

    ctx = _make_skill_context(app_dir)
    result = execute(
        {
            "class_smali": "Lcom/example/Foo;",
            "method_name": "validatePin",
            "descriptor": "(I)Z",
            "app_id": "test-app",
        },
        ctx,
    )
    assert result.success is True
    assert "Validates" in result.text
    assert "validatePin" in captured["prompt"]
    assert "pin >= 0" in captured["prompt"]


def test_execute_happy_path_writes_cache(monkeypatch, tmp_path: Path) -> None:
    """A successful summary is persisted via
    :mod:`skill_results_cache` keyed under skill_id
    ``"summarise_method"`` + the (app_sha, class, method,
    descriptor) tuple — this is the cache slot 13.3 already
    populates from the WS-handler-tier."""
    apps_root, app_dir = _make_app_with_decompile(tmp_path)

    class _StubResult:
        content = "summary text"
        thinking = ""
        metadata = {}

    monkeypatch.setattr(
        "androscan.llm.client.complete", lambda prompt, **kw: _StubResult(),
    )

    ctx = _make_skill_context(app_dir)
    execute(
        {
            "class_smali": "Lcom/example/Foo;",
            "method_name": "validatePin",
            "descriptor": "(I)Z",
            "app_id": "test-app",
        },
        ctx,
    )

    cached = skill_results_cache.lookup(
        apps_root, "test-app", "summarise_method",
        {"app_sha": _APP_SHA, "class_smali": "Lcom/example/Foo;",
         "method_name": "validatePin", "descriptor": "(I)Z"},
    )
    assert cached is not None
    assert cached["result_text"] == "summary text"


def test_execute_cache_hit_skips_llm(monkeypatch, tmp_path: Path) -> None:
    """A second invocation hits the cache + does NOT call the
    LLM — verifies cross-session reuse + the dedup discipline
    13.3 set up at the WS-handler tier."""
    apps_root, app_dir = _make_app_with_decompile(tmp_path)
    skill_results_cache.store(
        apps_root, "test-app", "test-run", "summarise_method",
        {"app_sha": _APP_SHA, "class_smali": "Lcom/example/Foo;",
         "method_name": "validatePin", "descriptor": "(I)Z"},
        "previously cached summary",
    )

    call_count = {"n": 0}

    def _boom(prompt: str, **kwargs: Any) -> Any:
        call_count["n"] += 1
        raise AssertionError("LLM should not be called on cache hit")

    monkeypatch.setattr("androscan.llm.client.complete", _boom)

    ctx = _make_skill_context(app_dir)
    result = execute(
        {
            "class_smali": "Lcom/example/Foo;",
            "method_name": "validatePin",
            "descriptor": "(I)Z",
            "app_id": "test-app",
        },
        ctx,
    )
    assert result.success is True
    assert result.text == "previously cached summary"
    assert call_count["n"] == 0


def test_execute_cache_key_invariant_under_descriptor_whitespace(
    monkeypatch, tmp_path: Path,
) -> None:
    """Stray whitespace inside the descriptor hits the same cache
    slot — pinned so an upstream caller passing ``" (I)  Z"``
    doesn't double-spend the LLM budget."""
    apps_root, app_dir = _make_app_with_decompile(tmp_path)
    skill_results_cache.store(
        apps_root, "test-app", "test-run", "summarise_method",
        {"app_sha": _APP_SHA, "class_smali": "Lcom/example/Foo;",
         "method_name": "validatePin", "descriptor": "(I)Z"},
        "cached",
    )

    monkeypatch.setattr(
        "androscan.llm.client.complete",
        lambda prompt, **kw: pytest.fail("LLM should not be called"),
    )

    ctx = _make_skill_context(app_dir)
    result = execute(
        {
            "class_smali": "Lcom/example/Foo;",
            "method_name": "validatePin",
            "descriptor": "  (I)  Z  ",
            "app_id": "test-app",
        },
        ctx,
    )
    assert result.text == "cached"


# ---------------------------------------------------------------------------
# Fail-open / fail-soft paths


def test_execute_missing_class_smali_returns_failed() -> None:
    """No ``class_smali`` → ``success=False`` with operator-readable
    text. Same posture as every other tier-3 skill's required-arg
    handling."""
    ctx = SkillContext(config=Config.default(), run_folder=Path("apps/test/r"))
    result = execute({"method_name": "x", "descriptor": "()V"}, ctx)
    assert result.success is False
    assert "class_smali" in result.text


def test_execute_missing_method_name_returns_failed() -> None:
    """No ``method_name`` → ``success=False``."""
    ctx = SkillContext(config=Config.default(), run_folder=Path("apps/test/r"))
    result = execute(
        {"class_smali": "Lcom/example/Foo;", "descriptor": "()V"}, ctx,
    )
    assert result.success is False
    assert "method_name" in result.text


def test_execute_missing_app_context_still_calls_llm_no_cache(
    monkeypatch, tmp_path: Path,
) -> None:
    """No app_dir resolvable → no source enrichment + no cache
    write, but the LLM call still happens with the name-only
    prompt. Operator gets a summary; just no cross-session reuse
    because the cache key is undefined without ``app_sha``."""

    class _StubResult:
        content = "name-based summary"
        thinking = ""
        metadata = {}

    captured = {}
    def _stub(prompt, **kw):
        captured["prompt"] = prompt
        return _StubResult()
    monkeypatch.setattr("androscan.llm.client.complete", _stub)

    # run_folder points at a non-existent path → _resolve_app_dir
    # returns None → no app_sha → no cache write.
    ctx = SkillContext(
        config=Config.default(),
        run_folder=tmp_path / "nope" / "run",
    )
    result = execute(
        {
            "class_smali": "Lcom/example/Foo;",
            "method_name": "bar",
            "descriptor": "()V",
        },
        ctx,
    )
    assert result.success is True
    assert result.text == "name-based summary"
    assert "not available" in captured["prompt"].lower()


def test_execute_missing_decompile_cache_falls_back_to_name_only(
    monkeypatch, tmp_path: Path,
) -> None:
    """app_meta.json present but no ``.decompiled/`` cache →
    source unavailable, skill fails-open with the name-only
    prompt. Operator still gets a summary."""
    apps_root = tmp_path / "apps"
    app_dir = apps_root / "test-app"
    app_dir.mkdir(parents=True)
    save_app_meta(app_dir, _APP_SHA, dossier={"apk_info": {"package": "com.example"}})

    class _StubResult:
        content = "fallback summary"
        thinking = ""
        metadata = {}

    captured = {}
    def _stub(prompt, **kw):
        captured["prompt"] = prompt
        return _StubResult()
    monkeypatch.setattr("androscan.llm.client.complete", _stub)

    ctx = _make_skill_context(app_dir)
    result = execute(
        {
            "class_smali": "Lcom/example/Foo;",
            "method_name": "bar",
            "descriptor": "()V",
            "app_id": "test-app",
        },
        ctx,
    )
    assert result.success is True
    assert result.text == "fallback summary"
    assert "not available" in captured["prompt"].lower()


def test_execute_method_not_in_source_falls_back_to_name_only(
    monkeypatch, tmp_path: Path,
) -> None:
    """Class file exists in cache but the method body isn't found
    (e.g. R8 inlined / wrong-class lookup) → fall-back to
    name-only prompt rather than raising."""
    apps_root, app_dir = _make_app_with_decompile(tmp_path)

    class _StubResult:
        content = "name-only fallback"
        thinking = ""
        metadata = {}

    captured = {}
    def _stub(prompt, **kw):
        captured["prompt"] = prompt
        return _StubResult()
    monkeypatch.setattr("androscan.llm.client.complete", _stub)

    ctx = _make_skill_context(app_dir)
    result = execute(
        {
            "class_smali": "Lcom/example/Foo;",
            "method_name": "doesNotExist",
            "descriptor": "()V",
            "app_id": "test-app",
        },
        ctx,
    )
    assert result.success is True
    assert result.text == "name-only fallback"
    # No source-block → name-only marker prelude in the prompt
    assert "Source:" not in captured["prompt"]


def test_execute_llm_exception_returns_failed(monkeypatch, tmp_path: Path) -> None:
    """LLM-side network / runtime error → ``success=False`` with
    operator-readable text. The WS handler translates this to a
    ``summary_failed`` event."""
    apps_root, app_dir = _make_app_with_decompile(tmp_path)

    def _boom(prompt, **kw):
        raise RuntimeError("ollama unreachable")
    monkeypatch.setattr("androscan.llm.client.complete", _boom)

    ctx = _make_skill_context(app_dir)
    result = execute(
        {
            "class_smali": "Lcom/example/Foo;",
            "method_name": "validatePin",
            "descriptor": "(I)Z",
            "app_id": "test-app",
        },
        ctx,
    )
    assert result.success is False
    assert "ollama unreachable" in result.text


def test_execute_empty_llm_response_returns_failed(monkeypatch, tmp_path: Path) -> None:
    """LLM returns whitespace-only ``content`` → ``success=False``.
    Mirrors the 13.3 ``empty_summary`` failure mode at the
    WS-handler tier (which also DOESN'T cache empty replies so a
    next session can re-fire)."""
    apps_root, app_dir = _make_app_with_decompile(tmp_path)

    class _Stub:
        content = "   \n  "
        thinking = ""
        metadata = {}

    monkeypatch.setattr("androscan.llm.client.complete", lambda p, **k: _Stub())

    ctx = _make_skill_context(app_dir)
    result = execute(
        {
            "class_smali": "Lcom/example/Foo;",
            "method_name": "validatePin",
            "descriptor": "(I)Z",
            "app_id": "test-app",
        },
        ctx,
    )
    assert result.success is False
    assert "empty" in result.text.lower()


def test_execute_failed_llm_does_not_pollute_cache(
    monkeypatch, tmp_path: Path,
) -> None:
    """Failed summaries are NOT written to the cache — pinned so
    a transient LLM blip doesn't lock in a "[summarise_method]
    LLM call failed: ..." string for every subsequent session.
    Mirrors 13.3's WS-handler-tier discipline."""
    apps_root, app_dir = _make_app_with_decompile(tmp_path)

    monkeypatch.setattr(
        "androscan.llm.client.complete",
        lambda p, **k: (_ for _ in ()).throw(RuntimeError("blip")),
    )

    ctx = _make_skill_context(app_dir)
    execute(
        {
            "class_smali": "Lcom/example/Foo;",
            "method_name": "validatePin",
            "descriptor": "(I)Z",
            "app_id": "test-app",
        },
        ctx,
    )

    cached = skill_results_cache.lookup(
        apps_root, "test-app", "summarise_method",
        {"app_sha": _APP_SHA, "class_smali": "Lcom/example/Foo;",
         "method_name": "validatePin", "descriptor": "(I)Z"},
    )
    assert cached is None


def test_execute_cache_per_app_sha_separation(
    monkeypatch, tmp_path: Path,
) -> None:
    """An APK upgrade (different ``app_sha``) hits a different
    cache slot — pinned so summaries don't bleed across APK
    versions when method bodies have changed underneath."""
    apps_root, app_dir = _make_app_with_decompile(tmp_path)

    # Pre-seed cache under a DIFFERENT app_sha — should NOT be
    # picked up by the skill's lookup keyed on the meta's
    # ``apk_sha256``.
    other_sha = "ffffffffffff"
    skill_results_cache.store(
        apps_root, "test-app", "test-run", "summarise_method",
        {"app_sha": other_sha, "class_smali": "Lcom/example/Foo;",
         "method_name": "validatePin", "descriptor": "(I)Z"},
        "stale-cross-version-summary",
    )

    class _Stub:
        content = "fresh summary"
        thinking = ""
        metadata = {}

    monkeypatch.setattr("androscan.llm.client.complete", lambda p, **k: _Stub())

    ctx = _make_skill_context(app_dir)
    result = execute(
        {
            "class_smali": "Lcom/example/Foo;",
            "method_name": "validatePin",
            "descriptor": "(I)Z",
            "app_id": "test-app",
        },
        ctx,
    )
    assert result.text == "fresh summary"


# ---------------------------------------------------------------------------
# Production-adapter wiring (androscan.web.app._build_summarise_method_callable)


def test_build_summarise_method_callable_signature_locked() -> None:
    """The factory returns a coroutine that accepts the
    13.4-locked four-arg :data:`SummaryCallable` signature
    ``(app_id, class_smali, method_name, descriptor) -> str``."""
    cb = _build_summarise_method_callable(
        Config.default, lambda app_id: Path("apps") / app_id,
    )
    import inspect
    sig = inspect.signature(cb)
    assert list(sig.parameters.keys()) == [
        "app_id", "class_smali", "method_name", "descriptor",
    ]


def test_build_summarise_method_callable_invokes_skill(monkeypatch) -> None:
    """The adapter delegates to :func:`androscan.skills.execute`
    + returns the skill's ``text`` field."""
    captured: dict[str, Any] = {}

    def _stub_skill(name: str, params: dict, ctx: SkillContext) -> SkillResult:
        captured["name"] = name
        captured["params"] = params
        return SkillResult(success=True, text="adapter-summary")

    monkeypatch.setattr("androscan.skills.execute", _stub_skill)

    cb = _build_summarise_method_callable(
        Config.default, lambda app_id: Path("apps") / app_id,
    )
    out = asyncio.run(cb("test-app", "Lcom/example/Foo;", "bar", "(I)Z"))
    assert out == "adapter-summary"
    assert captured["name"] == "summarise_method"
    assert captured["params"] == {
        "class_smali": "Lcom/example/Foo;",
        "method_name": "bar",
        "descriptor": "(I)Z",
        "app_id": "test-app",
    }


def test_build_summarise_method_callable_failed_skill_raises(monkeypatch) -> None:
    """A skill ``SkillResult`` with ``success=False`` is surfaced
    as a :exc:`RuntimeError` so the WS handler's
    ``summary_failed`` path emits the operator-facing reason —
    same translation discipline 13.3's
    :func:`default_summary_callable` used."""

    def _stub_skill(name: str, params: dict, ctx: SkillContext) -> SkillResult:
        return SkillResult(success=False, text="[summarise_method] LLM down.")

    monkeypatch.setattr("androscan.skills.execute", _stub_skill)

    cb = _build_summarise_method_callable(
        Config.default, lambda app_id: Path("apps") / app_id,
    )
    with pytest.raises(RuntimeError, match="LLM down"):
        asyncio.run(cb("test-app", "Lcom/example/Foo;", "bar", "(I)Z"))
