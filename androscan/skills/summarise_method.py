"""LLM-tier ``summarise_method`` skill — Phase 13 / DEC-029, sub-step
13.4.

Plugs into the WebSocket multiplex seam shipped at sub-step 13.3 to
produce per-method natural-language summaries the operator sees in
the Inspector pane (13.7 wires the visual surface). Inputs are the
three identifiers the ``behavior_trace_multi`` template's
``phase: "entry"`` event payload carries (``class_smali`` after
13.3's :func:`java_class_to_smali` normalisation, ``method_name``,
``descriptor``) plus the ``app_id`` segment from the WS URL.

Pipeline:

1. **Resolve the app_dir + decompile cache** via the same
   ``run_folder.parent.parent`` ladder every other tier-3 skill uses
   (mirrors :mod:`androscan.skills.trace_behavior` /
   :mod:`androscan.skills.query_call_graph`). Fail-open on a
   missing cache — return ``success=True`` with ``text="Source
   unavailable for summary"`` rather than blocking the operator.
2. **Read the method body** directly from the warm decompile cache
   (``apps/<app_id>/.decompiled/<sha>/sources/<package>/<Class>.java``),
   re-using :func:`androscan.skills.get_decompiled_method._extract_method_bodies`
   for the brace-matching method-extraction (handles overloads
   correctly — same mechanism the existing
   ``get_decompiled_method`` skill uses, no duplication). The
   path-resolution layer DOES NOT shell out to jadx — the dynamic
   trace flow REQUIRES a cached anchor (sub-step 13.2 returns 409
   if the decompile isn't ready), so the cache is guaranteed
   warm by the time this skill fires.
3. **Build the prompt** — system prompt steers the model toward
   operator-actionable prose; user prompt carries the smali class +
   method name + descriptor + (when available) the decompiled
   method body, capped at a configurable byte budget so a
   pathologically large method body doesn't blow the token budget.
4. **Call the LLM** via :func:`androscan.llm.client.complete` with
   ``response_format=None`` (prose, not JSON) and ``stream=False``
   (the WS handler awaits a single string — streaming would just
   add complexity without operator-visible benefit at this layer;
   13.7 may surface incremental tokens as a UX polish, but that's
   a separate decision).
5. **Cache** the result via
   :mod:`androscan.internal.skill_results_cache` keyed by
   ``(app_sha, class_smali, method_name, descriptor)`` under
   ``skill_id="summarise_method"`` — byte-equal to the cache wiring
   sub-step 13.3 already laid down. 13.3's WebSocket-tier writes
   (from the inline-LLM default callable) and 13.4's skill-tier
   writes share the same cache slot, so an operator who built up
   summaries under 13.3 keeps every cached entry across the cutover
   with zero rebuild.

``requires_confirmation=False`` per DEC-022 — read-only skill, no
device touching, no APK mutation. Fail-soft posture
(:func:`_unavailable_result` / :func:`_failed_result`): return
``success=True`` with a clear ``text`` explanation when the cache
lookup or the source-read fails, return ``success=False`` only on
LLM-side failures the operator should know about (network down,
JSON parse errors are downstream issues, etc.).

Wire-shape note for the route layer: this skill is called as the
production :data:`SummaryCallable` injected into
:func:`androscan.web.trace_routes.build_trace_router` via the
adapter built in :mod:`androscan.web.app`. The adapter bounces the
sync :func:`execute` into the default executor (mirrors
:func:`androscan.web.trace_summary.default_summary_callable`) so the
WS event loop stays unblocked while the LLM round-trip is in flight.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Optional

from androscan.skills.base import SkillContext, SkillMeta, SkillResult
from androscan.skills.get_decompiled_method import _extract_method_bodies


logger = logging.getLogger(__name__)


__all__ = [
    "SKILL_META",
    "execute",
    "smali_to_source_rel_path",
    "build_summary_prompt",
    "USER_PROMPT_HEAD_TEMPLATE",
    "SOURCE_BODY_BUDGET_BYTES",
]


# ---------------------------------------------------------------------------
# Locked v1 constants (justified inline so the route + tests can pin against
# the same values without re-deriving them)


#: Byte budget for the decompiled method body included in the LLM
#: prompt. Caps a pathologically large method (e.g. a generated
#: switch-statement handler with hundreds of cases) from blowing
#: the token budget. The operator's summary doesn't need every
#: byte — a representative head + a tail-truncation marker is enough
#: for the LLM to write an operator-actionable paragraph. v2
#: candidate: a config knob if real-app dogfooding shows operators
#: hitting the cap on legitimately-interesting methods.
SOURCE_BODY_BUDGET_BYTES = 4096

#: Same skill-id constant as in :mod:`androscan.web.trace_summary`.
#: Duplicated here (rather than imported across the skills/web
#: boundary) to keep the skills layer self-contained — the value
#: is fixed at the wire layer and a future rename has to land in
#: BOTH constants for the cache to round-trip cleanly. Tests pin
#: equality with the trace_summary constant.
SUMMARY_SKILL_ID = "summarise_method"


SKILL_META = SkillMeta(
    name=SUMMARY_SKILL_ID,
    description=(
        "One-paragraph operator-readable summary of an Android method "
        "that fired during a dynamic Frida trace. Reads the decompiled "
        "source from the warm decompile cache (apps/<app_id>/."
        "decompiled/<sha>/sources/...), enriches with the class + "
        "method + descriptor, and asks the LLM for a short prose "
        "explanation of what the method does and what its return "
        "value means to a security tester. Caches results in "
        "skill_results_cache so subsequent traces of the same "
        "method skip the LLM round-trip. Read-only; safe to call "
        "without confirmation."
    ),
    params_schema={
        "class_smali": (
            "Smali-form class descriptor (e.g. "
            "'Lcom/example/MainActivity;'). Required."
        ),
        "method_name": (
            "Method name (e.g. 'onClick'). Required; identifies "
            "which method body to extract from the class file."
        ),
        "descriptor": (
            "Smali method descriptor (e.g. "
            "'(Landroid/view/View;)V'). Used to disambiguate "
            "overloads and as part of the cache key."
        ),
        "app_id": (
            "app_id (apps/<app_id>/) the method belongs to. "
            "Optional; defaults to the current run's app_id "
            "derived from the skill context."
        ),
    },
    tier="llm",
    requires_confirmation=False,
)


# ---------------------------------------------------------------------------
# Prompt templates


_SYSTEM_PROMPT = (
    "You are summarising an Android method that fired during a dynamic "
    "Frida trace for a security tester. Output one short paragraph "
    "(3-5 sentences) explaining what this method does and what its "
    "return value means to the operator triaging the trace. Focus on "
    "operator-actionable behaviour, not implementation detail. If the "
    "decompiled source has been provided, ground your answer in it; "
    "if only the name + signature is available, say so plainly when "
    "the purpose is ambiguous from the name alone — better to flag "
    "uncertainty than to confabulate. Do NOT wrap the output in "
    "markdown code fences; plain prose only."
)


#: User-prompt prelude — gets the four identifying fields. Source
#: body (when available) is appended after this prelude with a
#: Markdown ``Source:`` divider so the LLM clearly sees the
#: structure. Kept small so the per-method prompt stays well under
#: any reasonable context budget even with full source attached.
USER_PROMPT_HEAD_TEMPLATE = (
    "Class:      {class_smali}\n"
    "Method:     {method_name}\n"
    "Descriptor: {descriptor}\n"
)


# ---------------------------------------------------------------------------
# Pure helpers


def smali_to_source_rel_path(class_smali: str) -> Optional[str]:
    """Translate ``Lcom/example/Foo;`` → ``com/example/Foo.java``.

    Inner classes (``Lcom/example/Foo$Bar;``) resolve to the OUTER
    class's source file (``com/example/Foo.java``) — jadx's bulk
    decompile output emits inner classes inside the outer class's
    file, so the brace-matching method-extraction handles them
    naturally.

    Returns ``None`` for malformed input (missing ``L``/``;``
    sentinels, empty body, etc.) so callers can fail-open on
    bad data without raising.
    """

    s = (class_smali or "").strip()
    if not s.startswith("L") or not s.endswith(";"):
        return None
    inner = s[1:-1]
    if not inner:
        return None
    # Drop inner-class suffix — jadx emits inner classes inside
    # the outer-class file.
    if "$" in inner:
        inner = inner.split("$", 1)[0]
    if not inner:
        return None
    return f"{inner}.java"


def build_summary_prompt(
    class_smali: str,
    method_name: str,
    descriptor: str,
    method_source: Optional[str] = None,
) -> str:
    """Build the user-prompt text from the four identifying fields
    plus the optional decompiled method body. Method source is
    truncated at :data:`SOURCE_BODY_BUDGET_BYTES` with a clear
    operator-visible marker so the LLM knows the body was clipped
    (avoids the LLM confidently summarising a half-truncated method
    as if it had seen the whole thing).
    """

    head = USER_PROMPT_HEAD_TEMPLATE.format(
        class_smali=class_smali,
        method_name=method_name,
        descriptor=descriptor,
    )
    if not method_source or not method_source.strip():
        return (
            head
            + "\n"
            + "(Decompiled source not available; summarise from name + "
            "signature alone.)\n"
            + "\nSummarise this method for the operator."
        )
    body = method_source
    if len(body.encode("utf-8")) > SOURCE_BODY_BUDGET_BYTES:
        # Clip on a character boundary safely (truncate by
        # bytes-budget worth of chars, then append a marker).
        body = body[:SOURCE_BODY_BUDGET_BYTES]
        body = body.rstrip() + "\n// ... (truncated for prompt budget)"
    return (
        head
        + "\nSource:\n```\n"
        + body
        + "\n```\n"
        + "\nSummarise this method for the operator."
    )


# ---------------------------------------------------------------------------
# App / cache resolution (mirrors trace_behavior._resolve_app_dir)


def _resolve_app_dir(context: SkillContext, app_id: Optional[str]) -> Optional[Path]:
    """Same fallback ladder as :func:`androscan.skills.trace_behavior._resolve_app_dir`.

    Explicit ``app_id`` wins (``apps/<app_id>/``); otherwise fall
    back to ``run_folder.parent`` (the per-app root inferred from
    the active run folder). Returns ``None`` when neither is
    available.
    """

    rf = getattr(context, "run_folder", None)
    if rf is None:
        return None
    rf_path = Path(rf)
    apps_root = rf_path.parent.parent if rf_path.parent.parent.exists() else None
    if app_id and apps_root and (apps_root / app_id).is_dir():
        return apps_root / app_id
    if rf_path.parent.exists():
        return rf_path.parent
    return None


def _read_method_source(
    app_dir: Path, class_smali: str, method_name: str
) -> Optional[str]:
    """Read the method body from the warm decompile cache. Returns
    ``None`` on every unavailability mode (cache not built, file
    missing, method not found in the file) — caller fail-opens on
    ``None`` and feeds the LLM a name-only prompt."""

    rel_path = smali_to_source_rel_path(class_smali)
    if rel_path is None:
        return None

    # Lazy import — keeps the skills layer importable without the
    # web stack being fully wired (mirrors trace_behavior's
    # decompile_cache lazy import).
    try:
        from androscan.web.decompile_cache import (
            get_status as decompile_status,
            read_source_file,
        )
    except Exception:  # pragma: no cover - defensive
        return None

    ds = decompile_status(app_dir)
    sha = ds.get("sha") if isinstance(ds, dict) else None
    if not sha or ds.get("status") != "ready":
        return None

    source = read_source_file(app_dir, rel_path, sha)
    if not source:
        return None

    body = _extract_method_bodies(source, method_name)
    return body or None


# ---------------------------------------------------------------------------
# Cache wrappers — byte-equal keying with sub-step 13.3's
# trace_summary helpers (verified by tests; the constant
# ``SUMMARY_SKILL_ID`` is duplicated rather than imported to keep
# the skills layer self-contained, but a test in
# ``test_summarise_method_skill.py`` pins string equality with
# trace_summary's constant).


def _summary_cache_params(
    app_sha: str, class_smali: str, method_name: str, descriptor: str
) -> dict[str, str]:
    """Same shape as
    :func:`androscan.web.trace_summary.summary_cache_params` —
    pinned by tests so 13.3's WS-tier cache writes and 13.4's
    skill-tier writes hit byte-equal-keyed cache entries."""

    return {
        "app_sha": str(app_sha),
        "class_smali": str(class_smali),
        "method_name": str(method_name),
        "descriptor": str(descriptor),
    }


def _lookup_cached(
    run_folder_root: Path,
    app_id: str,
    app_sha: str,
    class_smali: str,
    method_name: str,
    descriptor: str,
) -> Optional[str]:
    from androscan.internal import skill_results_cache

    entry = skill_results_cache.lookup(
        Path(run_folder_root),
        app_id,
        SUMMARY_SKILL_ID,
        _summary_cache_params(app_sha, class_smali, method_name, descriptor),
    )
    if not entry:
        return None
    text = entry.get("result_text")
    if not isinstance(text, str) or not text.strip():
        return None
    return text


def _store_cached(
    run_folder_root: Path,
    app_id: str,
    run_folder_name: str,
    app_sha: str,
    class_smali: str,
    method_name: str,
    descriptor: str,
    summary_text: str,
) -> None:
    if not (summary_text or "").strip():
        return
    try:
        from androscan.internal import skill_results_cache

        skill_results_cache.store(
            Path(run_folder_root),
            app_id,
            run_folder_name,
            SUMMARY_SKILL_ID,
            _summary_cache_params(app_sha, class_smali, method_name, descriptor),
            summary_text,
        )
    except Exception as exc:  # noqa: BLE001 — fail-soft on cache writes
        logger.warning(
            "summarise_method cache store failed for %s/%s: %s",
            class_smali,
            method_name,
            exc,
        )


# ---------------------------------------------------------------------------
# Result-shape helpers — keep the routes / tests reading off the same
# canonical shape rather than re-deriving the failure-mode strings.


def _unavailable_result(reason: str) -> SkillResult:
    """Fail-open result: ``success=True``, no LLM call, operator
    sees the explanation in the WS handler's ``summary_failed``
    event (or the chat dock's text). Used for missing source,
    decompile cache not ready, etc. — the operator can still
    triage the trace, just without the LLM-augmented summary."""
    return SkillResult(
        success=True, data=None, text=f"[summarise_method] {reason}"
    )


def _failed_result(reason: str) -> SkillResult:
    """Fail-soft result: ``success=False``, LLM-side failure the
    operator should know about (network down, JSON parse, etc.)."""
    return SkillResult(
        success=False, data=None, text=f"[summarise_method] {reason}"
    )


# ---------------------------------------------------------------------------
# Public entry point


# Whitespace normalisation regex — collapses any run of whitespace
# (including newlines) to a single space. Used in the cache-key
# normalisation pass on the descriptor so semantically-identical
# descriptors with stray whitespace differences hit the same
# cache entry.
_DESCRIPTOR_WS_RE = re.compile(r"\s+")


def _normalise_descriptor(descriptor: str) -> str:
    """Strip whitespace from a Smali descriptor so cache keys are
    invariant under whitespace differences (the wire-shape is
    expected to be tight, but the LLM's :func:`get_decompiled_method`
    callers occasionally emit stray spaces — pinning the
    normalisation here makes the cache hit-rate predictable)."""
    return _DESCRIPTOR_WS_RE.sub("", descriptor or "")


def execute(params: dict, context: SkillContext) -> SkillResult:
    """Run one summarise_method call.

    Param shape (all strings; ``app_id`` optional):
        ``class_smali``, ``method_name``, ``descriptor``, ``app_id?``
    """

    class_smali = (params.get("class_smali") or "").strip()
    method_name = (params.get("method_name") or "").strip()
    descriptor_raw = params.get("descriptor") or ""
    descriptor = _normalise_descriptor(descriptor_raw)
    app_id = (params.get("app_id") or "").strip() or None

    if not class_smali:
        return _failed_result("'class_smali' is required.")
    if not method_name:
        return _failed_result("'method_name' is required.")

    # Resolve app + sha for the cache key. Missing app context is
    # NOT fatal at this layer — the LLM call still works without
    # source enrichment, and the route layer's ``app_sha``
    # resolution may have already gated the call.
    app_dir = _resolve_app_dir(context, app_id)
    app_sha: Optional[str] = None
    if app_dir is not None:
        try:
            from androscan.internal.app_meta import load_app_meta

            meta = load_app_meta(app_dir) or {}
            sha_val = meta.get("apk_sha256")
            if isinstance(sha_val, str) and sha_val:
                app_sha = sha_val
        except Exception:  # pragma: no cover - defensive
            app_sha = None

    # Cache lookup happens BEFORE the source read so a warm cache
    # hit is fast (no decompile-cache I/O, no LLM round-trip).
    run_folder_path = Path(getattr(context, "run_folder", "."))
    apps_root = (
        run_folder_path.parent.parent if run_folder_path.parent.parent.exists() else None
    )
    run_folder_name = run_folder_path.name
    effective_app_id = app_id or (run_folder_path.parent.name if app_dir else None)

    cached: Optional[str] = None
    if apps_root is not None and effective_app_id and app_sha is not None:
        cached = _lookup_cached(
            apps_root, effective_app_id, app_sha,
            class_smali, method_name, descriptor,
        )
    if cached is not None:
        return SkillResult(success=True, data=cached, text=cached)

    # Source enrichment — fail-open on every unavailability path.
    method_source: Optional[str] = None
    if app_dir is not None:
        try:
            method_source = _read_method_source(
                app_dir, class_smali, method_name
            )
        except Exception as exc:  # noqa: BLE001 — defensive
            logger.warning(
                "summarise_method source-read raised for %s/%s: %s",
                class_smali, method_name, exc,
            )
            method_source = None

    prompt = build_summary_prompt(
        class_smali, method_name, descriptor, method_source
    )

    # LLM call. Lazy import same as trace_summary's default callable
    # — keeps the skill importable in test contexts that haven't
    # installed the full LLM client surface.
    try:
        from androscan.llm import client as llm_client

        result = llm_client.complete(
            prompt,
            config=getattr(context, "config", None),
            system_content=_SYSTEM_PROMPT,
            stream=False,
            response_format=None,  # prose, not JSON
        )
    except Exception as exc:  # noqa: BLE001 — surface LLM-side failures
        logger.warning(
            "summarise_method LLM call raised for %s/%s: %s",
            class_smali, method_name, exc,
        )
        return _failed_result(f"LLM call failed: {exc}")

    summary_text = (result.content or "").strip()
    if not summary_text:
        return _failed_result("LLM returned empty summary.")

    # Cache only on success + only when ``app_sha`` was resolvable.
    if apps_root is not None and effective_app_id and app_sha is not None:
        _store_cached(
            apps_root,
            effective_app_id,
            run_folder_name or "summarise-method",
            app_sha,
            class_smali,
            method_name,
            descriptor,
            summary_text,
        )

    return SkillResult(success=True, data=summary_text, text=summary_text)
