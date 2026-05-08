"""Summary-emission helpers for the dynamic-trace WebSocket — Phase 13 /
DEC-029, sub-step 13.3.

Owns three concerns the WebSocket handler in :mod:`trace_routes`
delegates:

1. **Cache lookup / store** keyed by ``(app_sha, class_smali,
   method_name, descriptor)`` against the existing
   :mod:`androscan.internal.skill_results_cache`. Cache hits
   short-circuit the LLM round-trip and emit ``summary_ready``
   with ``cached: true`` immediately.
2. **Method-key derivation** — translate Java-form class names from
   the ``behavior_trace_multi`` template's wire shape (the
   ``methods_json`` payload uses Java-form class names; see the
   13.1 wire-shape lock in DEC-029) into the Smali-form class
   descriptor the cache key + the existing
   :class:`MethodRef.smali_signature` round-trip use.
3. **Summary callable seam** — the
   :data:`SummaryCallable` async interface that lets the WebSocket
   handler stay LLM-agnostic. The default for production
   (:func:`default_summary_callable`) makes a thin inline
   :func:`androscan.llm.client.complete` call with a hand-rolled
   name-based prompt; sub-step 13.4 replaces it with the formal
   ``summarise_method`` skill (with decompiled-source enrichment +
   registry registration). The seam stays — 13.4 swaps the
   implementation, not the WS endpoint.

Pure-ish: the cache wrappers do touch disk via
``skill_results_cache``, and :func:`default_summary_callable` does
make a network round-trip to the LLM, but every other helper is
pure-function. Tests can inject deterministic stubs at the
:data:`SummaryCallable` seam without monkeypatching the LLM client.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Awaitable, Callable, Optional

from androscan.config import Config
from androscan.internal import skill_results_cache


__all__ = [
    "SUMMARY_SKILL_ID",
    "SUMMARY_TIMEOUT_S",
    "SUMMARY_PENDING_KIND",
    "SUMMARY_READY_KIND",
    "SUMMARY_FAILED_KIND",
    "SummaryCallable",
    "java_class_to_smali",
    "method_key",
    "summary_cache_params",
    "lookup_cached_summary",
    "store_summary",
    "default_summary_callable",
]


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants — wire shape + cache-key + timeout knobs

#: Skill identifier used as the cache discriminator. Even though the
#: formal :class:`SkillMeta` registration happens in 13.4, the cache
#: key uses this name **now** so 13.4 can land transparently — its
#: cached results are byte-equal-keyed against 13.3's writes, no
#: cache rebuild required at the 13.4 cutover.
SUMMARY_SKILL_ID = "summarise_method"

#: Per-method LLM call timeout. Beyond this we emit ``summary_failed``
#: and let the operator re-trigger if they care. 30 s is generous
#: enough for slow local models (Qwen3 quants on an old laptop) but
#: tight enough that a stuck task doesn't keep a WebSocket pinned
#: indefinitely. v2 candidate: a ``trace.summary_timeout_s``
#: ``Config`` knob if operator-visible tuning becomes useful — the
#: WS handler reads this constant via :func:`get_summary_timeout`
#: (added when the knob lands; unused for now).
SUMMARY_TIMEOUT_S = 30.0

#: Wire-shape ``kind`` values for the three new event kinds, locked
#: at DEC-029. Same envelope as
#: :func:`androscan.adapters.frida_client._event_to_jsonable`
#: (``{ts, session_id, kind, payload, raw}``); old clients that
#: filter on the existing trace ``kind``s ignore these silently
#: (additive-by-design discipline).
SUMMARY_PENDING_KIND = "summary_pending"
SUMMARY_READY_KIND = "summary_ready"
SUMMARY_FAILED_KIND = "summary_failed"


#: Async callable that produces a per-method summary. Inputs are the
#: three fields the ``behavior_trace_multi`` event payload carries
#: at ``phase: "entry"`` (``class`` Java-form / ``method`` /
#: ``descriptor``). Output is the operator-facing summary text. May
#: raise :class:`asyncio.TimeoutError` (caller wraps this in
#: ``asyncio.wait_for``) or any exception — the WS handler
#: translates both into a ``summary_failed`` event.
SummaryCallable = Callable[[str, str, str], Awaitable[str]]


# ---------------------------------------------------------------------------
# Pure helpers — class-name + cache-key derivation


def java_class_to_smali(java_class: str) -> str:
    """Translate ``"com.example.Foo"`` → ``"Lcom/example/Foo;"``.

    Mirrors the inverse of
    :func:`androscan.analysis.trace_types._smali_class_to_java`.
    Defensive against an already-Smali input — strings that already
    start with ``L`` and end with ``;`` are returned unchanged so the
    helper is idempotent (the WS handler can pass either shape
    without first probing the format).
    """

    s = (java_class or "").strip()
    if not s:
        return s
    if s.startswith("L") and s.endswith(";") and "/" in s:
        return s
    return f"L{s.replace('.', '/')};"


def method_key(class_java_or_smali: str, method_name: str, descriptor: str) -> str:
    """Stable per-method dedup key.

    Used by the WS handler to track which methods have already had
    a summary fired in the current session — avoids re-firing the
    LLM on every re-invocation of the same method (the operator
    only cares about the summary once per method per session; the
    fired/not-fired state visible to the UI lives one layer up via
    the cache contents).

    Format mirrors :attr:`MethodRef.smali_signature` (class +
    ``->`` + method + descriptor) so a key produced here round-trips
    against the canonical static-analysis identifier — useful when
    the frontend correlates summary events back to a specific
    flowchart node in 13.6.
    """

    cls_smali = java_class_to_smali(class_java_or_smali)
    return f"{cls_smali}->{method_name}{descriptor}"


def summary_cache_params(
    app_sha: str, class_java_or_smali: str, method_name: str, descriptor: str
) -> dict[str, str]:
    """Build the params dict :mod:`skill_results_cache` keys against.

    Why ``app_sha`` is in the key: a single ``apps/<app_id>/`` may
    survive across multiple analyses of newer APK builds (operator
    iterates on the target app); the sha pins the summary to the
    exact bytes the methods came from so a stale summary from an
    older build never bleeds into a new run.
    """

    return {
        "app_sha": str(app_sha),
        "class_smali": java_class_to_smali(class_java_or_smali),
        "method_name": str(method_name),
        "descriptor": str(descriptor),
    }


# ---------------------------------------------------------------------------
# Cache wrappers


def lookup_cached_summary(
    run_folder_root: Path,
    app_id: str,
    app_sha: str,
    class_java_or_smali: str,
    method_name: str,
    descriptor: str,
) -> Optional[str]:
    """Return the cached summary text or ``None`` on miss.

    Cache miss includes corrupt cache, missing file, and the entry's
    ``result_text`` being empty / whitespace-only — operators
    occasionally see "the LLM returned nothing" and expect the WS
    to re-fire the call rather than emit a useless cached blank.
    """

    entry = skill_results_cache.lookup(
        Path(run_folder_root),
        app_id,
        SUMMARY_SKILL_ID,
        summary_cache_params(app_sha, class_java_or_smali, method_name, descriptor),
    )
    if not entry:
        return None
    text = entry.get("result_text")
    if not isinstance(text, str) or not text.strip():
        return None
    return text


def store_summary(
    run_folder_root: Path,
    app_id: str,
    run_folder_name: str,
    app_sha: str,
    class_java_or_smali: str,
    method_name: str,
    descriptor: str,
    summary_text: str,
) -> None:
    """Persist a successful summary to ``skill_results_cache.json``.

    Empty / whitespace-only ``summary_text`` is a no-op — see
    :func:`lookup_cached_summary` for the matching read-side
    discipline. Errors at the cache-write level are logged but
    swallowed; the in-memory summary still flows over the WebSocket
    so the operator gets the result even when the cache is unhappy
    (e.g. read-only filesystem on a CI runner).
    """

    if not (summary_text or "").strip():
        return
    try:
        skill_results_cache.store(
            Path(run_folder_root),
            app_id,
            run_folder_name,
            SUMMARY_SKILL_ID,
            summary_cache_params(app_sha, class_java_or_smali, method_name, descriptor),
            summary_text,
        )
    except Exception as exc:  # noqa: BLE001 — fail-soft on cache writes
        logger.warning(
            "trace_summary cache store failed for %s/%s: %s",
            class_java_or_smali,
            method_name,
            exc,
        )


# ---------------------------------------------------------------------------
# Default summary callable — thin inline LLM call (13.4 will swap in skill)


# v1 prompt — name-based only, no decompiled source. The summary
# ends up name-quality (i.e. as good as the LLM's prior over the
# class + method name's semantic intent); 13.4 will enrich with a
# decompiled-source snippet via :mod:`androscan.web.decompile_cache`
# and the existing ``get_decompiled_method`` skill, dramatically
# upgrading the quality. v1 ships with the thin prompt so operators
# can dogfood the WS multiplexing pipeline end-to-end immediately
# rather than waiting for 13.4 to land — every signal we get from
# real-app dogfooding before 13.4 ships flows back into 13.4's
# prompt-engineering iteration.
_SYSTEM_PROMPT = (
    "You are summarising an Android method that fired during a dynamic Frida "
    "trace for a security tester. Output one short paragraph (3-5 sentences) "
    "explaining what this method likely does and what its return value means "
    "to the operator triaging the trace. Focus on operator-actionable "
    "behaviour, not implementation detail. If the method's purpose is "
    "ambiguous from the name + signature alone, say so plainly — better "
    "to flag uncertainty than to confabulate. Do NOT wrap the output in "
    "markdown code fences; plain prose only."
)

_USER_PROMPT_TEMPLATE = (
    "Class:      {class_smali}\n"
    "Method:     {method_name}\n"
    "Descriptor: {descriptor}\n"
    "\n"
    "Summarise this method for the operator."
)


def default_summary_callable(
    config_provider: Callable[[], Config],
    *,
    timeout_s: float = SUMMARY_TIMEOUT_S,
) -> SummaryCallable:
    """Build the production :data:`SummaryCallable` bound to the live
    :class:`Config` provider.

    Returned callable is async; internally it bounces the sync
    :func:`androscan.llm.client.complete` into the default executor
    via :meth:`asyncio.AbstractEventLoop.run_in_executor` so the
    WS event loop stays unblocked while the LLM round-trip is in
    flight. ``timeout_s`` enforces an upper bound — beyond it the
    coroutine raises :class:`asyncio.TimeoutError`, which the WS
    handler translates into a ``summary_failed`` event.

    13.4 replaces this with a registered ``summarise_method`` skill
    that ALSO reads the decompiled source. The seam stays — 13.4's
    factory wires its own :data:`SummaryCallable` into
    :func:`androscan.web.trace_routes.build_trace_router` exactly
    where this default plugs in today.
    """

    async def _call(class_smali: str, method_name: str, descriptor: str) -> str:
        # Lazy import — keeps :mod:`androscan.web.trace_summary`
        # importable in test contexts that haven't installed the
        # full LLM client surface yet (mirrors the
        # ``frida_routes.py`` lazy-Frida pattern).
        from androscan.llm import client as llm_client

        prompt = _USER_PROMPT_TEMPLATE.format(
            class_smali=class_smali,
            method_name=method_name,
            descriptor=descriptor,
        )
        cfg = config_provider()
        loop = asyncio.get_running_loop()

        def _do_call() -> str:
            result = llm_client.complete(
                prompt,
                config=cfg,
                system_content=_SYSTEM_PROMPT,
                stream=False,
                response_format=None,  # prose, not JSON
            )
            return (result.content or "").strip()

        return await asyncio.wait_for(
            loop.run_in_executor(None, _do_call),
            timeout=timeout_s,
        )

    return _call
