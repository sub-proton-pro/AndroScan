"""Dynamic-trace orchestration helpers — Phase 13 / DEC-029, sub-step 13.2.

Bridges a cached :class:`BehaviorAnchor` to a renderable
``behavior_trace_multi`` payload. Pure functions; no I/O. The HTTP
route in :mod:`androscan.web.trace_routes` wires this into the Frida
session lifecycle (``FridaClient.attach`` →
``session.set_persistence_path`` → ``session.load_script``).

Three deliverables, each one small enough to test independently:

1. :func:`extract_closure_methods` — flatten the anchor's closure
   into a deduped, entry-first list of :class:`MethodRef`s.
2. :func:`cap_methods` — cap the list at the operator-supplied
   ``hop_cap`` (the threshold-color cap from DEC-029).
3. :func:`methods_to_json` — encode the list as the
   JS-object-literal-formatted ``methods_json`` parameter expected
   by the ``behavior_trace_multi`` template's ``str.format`` step.

The order in (1) is deliberately stable: entry method first
(operators expect the gate they picked to be hooked even when the
``hop_cap`` is small), then decisions in the BFS order produced by
sub-step 10.5, then plan-target methods (these are the bypass-relevant
calls the planner identified — frequently the predicate-source
methods that flip a gate's outcome). v1 truncates to ``hop_cap`` via
simple head-slicing; v2 may layer call-graph hop distance + LLM
prioritisation on top, but the wire shape is intentionally cap-only
so the upgrade is additive.
"""

from __future__ import annotations

import json
from typing import Iterable

from androscan.analysis.trace_types import BehaviorAnchor, MethodRef


__all__ = [
    "extract_closure_methods",
    "cap_methods",
    "methods_to_json",
]


def extract_closure_methods(anchor: BehaviorAnchor) -> tuple[MethodRef, ...]:
    """Return the deduped list of methods to hook for ``anchor``.

    Order:

    1. ``anchor.entry_method`` (always first; the gate the operator
       picked, or the gate the Mirror→Trace handoff picked for them).
    2. ``anchor.decisions[*].method`` — every method that contains a
       decision in the closure, in the BFS order sub-step 10.5
       produced. These are the gate-containing methods.
    3. ``anchor.plans[*].target_method`` — bypass-target methods.
       Frequently the predicate-source method whose return-value
       drives the gate; hooking it gives the operator visibility into
       the value that determined the gate's verdict.
    4. ``anchor.plans[*].source_decision_method`` — gate-containing
       method for each plan. Usually a duplicate of (2) but kept
       explicit for the rare planner-emits-a-cross-method-plan case.
    5. ``anchor.advanced_plans[*]`` — same fields, appended after the
       default plans so they get the lowest hook priority within the
       closure.

    Dedup uses :attr:`MethodRef.smali_signature` as the key —
    ensures overload-distinguishability without keying on the
    dataclass tuple (which would treat two equally-signed methods
    from different decision contexts as separate entries even though
    they're the same method at runtime).

    Plan fields are read via :func:`getattr` with a ``None`` default
    so this works even against historical anchors written before
    ``target_method`` / ``source_decision_method`` were added in
    sub-step 10.4 — a forward-compat posture matching the rest of
    Phase 10's data-model discipline.
    """

    seen: set[str] = set()
    out: list[MethodRef] = []

    def _add(m: MethodRef | None) -> None:
        if m is None:
            return
        sig = m.smali_signature
        if sig in seen:
            return
        seen.add(sig)
        out.append(m)

    _add(anchor.entry_method)
    for dp in anchor.decisions:
        _add(getattr(dp, "method", None))
    for plan in (*anchor.plans, *anchor.advanced_plans):
        _add(getattr(plan, "target_method", None))
        _add(getattr(plan, "source_decision_method", None))

    return tuple(out)


def cap_methods(methods: tuple[MethodRef, ...], hop_cap: int) -> tuple[MethodRef, ...]:
    """Return the first ``hop_cap`` entries of ``methods``.

    The caller's ``methods`` is already in priority order (entry
    method first; see :func:`extract_closure_methods`). v1's cap is
    a simple head-truncation. ``hop_cap <= 0`` returns the empty
    tuple — the route layer treats that as a 422 "empty closure
    after cap" so the operator gets a clear error rather than a
    silently-no-op trace.
    """

    if hop_cap <= 0:
        return ()
    return methods[:hop_cap]


def methods_to_json(methods: Iterable[MethodRef]) -> str:
    """Serialise ``methods`` to the JS-object-literal-formatted JSON
    string expected by the ``behavior_trace_multi`` template's
    ``methods_json`` parameter.

    The template's renderer substitutes the value verbatim into a JS
    expression context (``var methodsList = {methods_json};``). JSON
    with quoted-string keys is syntactically-valid JS (since ES5),
    so no ``JSON.parse`` round-trip is needed at runtime — the
    rendered Frida script gets a JS array literal directly.

    Field shape per method:

    * ``class``      — Java-form class name (e.g. ``"com.example.Foo"``);
                       what ``Java.use(...)`` accepts at runtime.
    * ``method``     — bare method name (no signature).
    * ``descriptor`` — ``(IL...;)V``-style parameter tuple + return
                       descriptor. Informational on the trace event
                       payload; the Frida tracer hooks every overload
                       regardless of descriptor (matches the
                       :data:`entry_exit_log` precedent).
    """

    payload = [
        {
            "class": m.class_name,
            "method": m.method_name,
            "descriptor": _format_descriptor(m),
        }
        for m in methods
    ]
    return json.dumps(payload)


def _format_descriptor(m: MethodRef) -> str:
    """``(IL...;)V`` — the parameter-tuple + return-type slice of
    the smali signature. Pulled out as a helper so the round-trip
    against :attr:`MethodRef.smali_signature` is obvious to readers.
    """

    params = "".join(m.param_descriptors)
    return f"({params}){m.return_descriptor}"
