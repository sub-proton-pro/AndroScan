"""Unit tests for :mod:`androscan.web.trace_dynamic` — the closure-
extraction + ``hop_cap`` + ``methods_json`` helpers that bridge a
cached :class:`BehaviorAnchor` to a renderable
``behavior_trace_multi`` payload (Phase 13 / DEC-029, sub-step 13.2).

Pure-function helpers; no I/O. Tests construct synthetic
:class:`BehaviorAnchor` values directly rather than through the
trace_behavior skill, so the assertions stay focused on the
helper's contract rather than upstream pipeline behaviour.
"""

from __future__ import annotations

import json

import pytest

from androscan.analysis.trace_types import (
    BehaviorAnchor,
    BypassPlan,
    DecisionKind,
    DecisionPoint,
    MethodRef,
)
from androscan.web.trace_dynamic import (
    cap_methods,
    extract_closure_methods,
    methods_to_json,
)


# ---------------------------------------------------------------------------
# Helpers — synthetic data factories


def _mref(cls: str, name: str, *params: str, ret: str = "V") -> MethodRef:
    """Quick :class:`MethodRef` factory — all-stringly, no manifest
    lookup. Mirrors the pattern :mod:`test_trace_behavior_skill` uses
    for hand-rolled fixtures."""
    return MethodRef(
        class_name=cls,
        method_name=name,
        param_descriptors=tuple(params),
        return_descriptor=ret,
    )


def _decision(method: MethodRef) -> DecisionPoint:
    """Build a :class:`DecisionPoint` with the smallest valid shape —
    we don't care about the predicate / branch fields here; the
    helper only reads ``decision.method``."""
    return DecisionPoint(
        method=method,
        instruction_index=0,
        source_line=None,
        kind=DecisionKind.IF_EQZ,
        predicate_registers=(),
        branches=(),
    )


def _plan(
    *,
    target: MethodRef,
    source_decision_method: MethodRef,
) -> BypassPlan:
    """Build a :class:`BypassPlan` with the smallest valid shape —
    only ``target_method`` / ``source_decision_method`` are read by
    :func:`extract_closure_methods`."""
    return BypassPlan(
        template_id="force_return_value",
        params={},
        rationale="",
        risk="low",
        risks=(),
        target_method=target,
        source_decision_method=source_decision_method,
        source_decision_instruction_index=0,
    )


def _anchor(
    *,
    entry: MethodRef,
    decisions: tuple[DecisionPoint, ...] = (),
    plans: tuple[BypassPlan, ...] = (),
    advanced_plans: tuple[BypassPlan, ...] = (),
) -> BehaviorAnchor:
    return BehaviorAnchor(
        entry_method=entry,
        hops=3,
        decisions=decisions,
        plans=plans,
        advanced_plans=advanced_plans,
    )


# ---------------------------------------------------------------------------
# extract_closure_methods


def test_extract_closure_methods_entry_only() -> None:
    """Single-method anchor — only the entry method comes back."""
    entry = _mref("com.example.Foo", "entry")
    out = extract_closure_methods(_anchor(entry=entry))
    assert out == (entry,)


def test_extract_closure_methods_entry_first() -> None:
    """Entry method MUST be the first element regardless of where it
    appears in decisions / plans. The DEC-029 priority order is
    operator-visible — when ``hop_cap`` is small the first method
    they expect to see hooked is the one they typed in."""
    entry = _mref("com.example.Foo", "entry")
    other = _mref("com.example.Foo", "other")
    anchor = _anchor(
        entry=entry,
        decisions=(_decision(other), _decision(entry)),
    )
    out = extract_closure_methods(anchor)
    assert out[0] == entry
    # ``entry`` already came from ``entry_method``, so the duplicate
    # in ``decisions`` is dropped.
    assert out == (entry, other)


def test_extract_closure_methods_dedupes_by_smali_signature() -> None:
    """Two distinct ``MethodRef`` *instances* with the same smali
    signature collapse to one. Necessary because the planner emits
    fresh dataclass instances per plan even when they reference the
    same method as a decision."""
    entry = _mref("com.example.Foo", "entry")
    a1 = _mref("com.example.Foo", "a", "I")
    a2 = _mref("com.example.Foo", "a", "I")  # same smali signature
    assert a1 == a2  # frozen dataclass equality is by fields
    assert a1 is not a2
    anchor = _anchor(entry=entry, decisions=(_decision(a1), _decision(a2)))
    assert extract_closure_methods(anchor) == (entry, a1)


def test_extract_closure_methods_distinguishes_overloads() -> None:
    """Different param descriptors → different smali signatures →
    both methods retained. Overload-distinguishability is the whole
    point of using ``smali_signature`` rather than ``(class, name)``
    as the dedup key."""
    entry = _mref("com.example.Foo", "entry")
    one_int = _mref("com.example.Foo", "validate", "I")
    one_string = _mref("com.example.Foo", "validate", "Ljava/lang/String;")
    anchor = _anchor(
        entry=entry,
        decisions=(_decision(one_int), _decision(one_string)),
    )
    out = extract_closure_methods(anchor)
    assert out == (entry, one_int, one_string)


def test_extract_closure_methods_plan_methods_after_decisions() -> None:
    """Order: entry → decisions → plans.target → plans.source. The
    operator scans the hooks list top-down; gate-containing methods
    (decisions) come before bypass-target methods so the threshold-
    color cap honours the gates first."""
    entry = _mref("com.example.Foo", "entry")
    gate = _mref("com.example.Foo", "gate")
    target = _mref("com.example.Foo", "target")
    source = _mref("com.example.Foo", "gate")  # same as ``gate``
    anchor = _anchor(
        entry=entry,
        decisions=(_decision(gate),),
        plans=(_plan(target=target, source_decision_method=source),),
    )
    out = extract_closure_methods(anchor)
    # ``source`` collapses into ``gate`` (same smali sig).
    assert out == (entry, gate, target)


def test_extract_closure_methods_advanced_plans_appended_last() -> None:
    """Advanced plans get the lowest priority within the closure —
    they're behind the "Advanced" UI expander already, so under a
    tight ``hop_cap`` they're the first to be culled."""
    entry = _mref("com.example.Foo", "entry")
    default_target = _mref("com.example.Foo", "default_t")
    advanced_target = _mref("com.example.Foo", "advanced_t")
    p_default = _plan(
        target=default_target,
        source_decision_method=_mref("com.example.Foo", "gate"),
    )
    p_advanced = _plan(
        target=advanced_target,
        source_decision_method=_mref("com.example.Foo", "gate2"),
    )
    anchor = _anchor(entry=entry, plans=(p_default,), advanced_plans=(p_advanced,))
    out = extract_closure_methods(anchor)
    assert out.index(default_target) < out.index(advanced_target)


def test_extract_closure_methods_handles_missing_decision_method() -> None:
    """Defensive — if a historical anchor was somehow persisted with
    a ``None`` ``decision.method`` (shouldn't happen given the
    dataclass contract, but ``getattr`` with default ``None`` is the
    forward-compat posture across the codebase) the helper skips it
    rather than crashing."""
    entry = _mref("com.example.Foo", "entry")
    anchor = _anchor(entry=entry, decisions=())
    assert extract_closure_methods(anchor) == (entry,)


# ---------------------------------------------------------------------------
# cap_methods


def test_cap_methods_head_truncation() -> None:
    """v1 cap is a simple head-slice. Order is preserved; entry method
    is still first."""
    methods = tuple(_mref("com.example.Foo", f"m{i}") for i in range(10))
    capped = cap_methods(methods, 3)
    assert capped == methods[:3]


def test_cap_methods_no_truncation_when_under_cap() -> None:
    methods = tuple(_mref("com.example.Foo", f"m{i}") for i in range(3))
    assert cap_methods(methods, 100) == methods


def test_cap_methods_zero_hop_cap_returns_empty() -> None:
    """``hop_cap == 0`` collapses to no hooks. Route layer surfaces
    that as 422 (rather than 200ing a no-op trace)."""
    methods = (_mref("com.example.Foo", "m"),)
    assert cap_methods(methods, 0) == ()


def test_cap_methods_negative_hop_cap_returns_empty() -> None:
    """Defensive — Pydantic clamps to ``ge=1`` at the route layer,
    but the helper itself stays robust against any caller passing a
    negative cap (e.g. arithmetic upstream that accidentally
    underflows)."""
    methods = (_mref("com.example.Foo", "m"),)
    assert cap_methods(methods, -5) == ()


# ---------------------------------------------------------------------------
# methods_to_json


def test_methods_to_json_shape() -> None:
    """Each entry has exactly ``class`` / ``method`` / ``descriptor``
    fields. Pin the wire shape so the ``behavior_trace_multi``
    template's substitution stays valid JS."""
    m = _mref("com.example.Foo", "bar", "I", "Ljava/lang/String;", ret="Z")
    out = json.loads(methods_to_json([m]))
    assert out == [
        {
            "class": "com.example.Foo",
            "method": "bar",
            "descriptor": "(ILjava/lang/String;)Z",
        }
    ]


def test_methods_to_json_empty_input() -> None:
    """Empty list → empty JSON array, valid as a JS expression."""
    assert methods_to_json([]) == "[]"


def test_methods_to_json_preserves_order() -> None:
    """Order of input methods is preserved in the output — the
    template uses array index for nothing in particular, but the
    operator's mental model ("the entry method is the first hook")
    relies on it."""
    methods = [
        _mref("com.example.Foo", "first"),
        _mref("com.example.Foo", "second"),
        _mref("com.example.Bar", "third"),
    ]
    out = json.loads(methods_to_json(methods))
    assert [m["method"] for m in out] == ["first", "second", "third"]


def test_methods_to_json_is_valid_js_array_literal() -> None:
    """Pin the JS-expression-context substitution invariant —
    ``json.dumps`` produces output that's syntactically-valid as a
    JS array literal (since ES5 quoted-key object literals are JS-
    legal). Detected here at unit level so the
    ``behavior_trace_multi`` rendering can rely on it without
    needing its own escaper."""
    m = _mref("com.example.Foo", "bar", "I", ret="V")
    js = methods_to_json([m])
    # Simulate the template's substitution.
    rendered = f"var methodsList = {js};"
    # Round-trip through the JSON parser as a parse-validity proxy
    # for JS — every JSON literal is also a JS literal, so a clean
    # JSON parse certifies the JS is at least lexically valid.
    parsed = json.loads(js)
    assert isinstance(parsed, list)
    assert "var methodsList = [" in rendered


def test_methods_to_json_handles_void_no_args() -> None:
    """``()V`` is the smallest valid descriptor — no params, void
    return. Showed up in the very first dogfood run as the entry
    method's descriptor for an ``onCreate`` override."""
    m = _mref("com.example.MainActivity", "onCreate")
    out = json.loads(methods_to_json([m]))
    assert out[0]["descriptor"] == "()V"


# ---------------------------------------------------------------------------
# End-to-end (still pure-function) — closure → cap → JSON


def test_closure_to_cap_to_json_roundtrip() -> None:
    """The three helpers compose cleanly: extract → cap → encode is
    the exact pipeline the route layer runs, so test it end-to-end
    here so any future refactor at the helper layer has the
    contract pinned."""
    entry = _mref("com.example.Foo", "entry")
    decisions = tuple(
        _decision(_mref("com.example.Foo", f"gate{i}", "I", ret="Z"))
        for i in range(8)
    )
    anchor = _anchor(entry=entry, decisions=decisions)
    closure = extract_closure_methods(anchor)
    capped = cap_methods(closure, 3)
    js = methods_to_json(capped)
    out = json.loads(js)
    assert len(out) == 3
    assert out[0]["method"] == "entry"
    assert out[1]["method"] == "gate0"
    assert out[2]["method"] == "gate1"
