"""Platform-neutral data model for Phase 10 — Behavior Trace.

This module is the data-model layer for Trace mode. v1 ships an Android
adapter (Smali parser in :mod:`androscan.analysis.decisions`, slicing /
classifier / planner in sibling modules added by sub-steps 10.2–10.4),
but the shapes here are deliberately platform-neutral so iOS / desktop /
embedded adapters can land later without invalidating callers (the
`trace_behavior` skill, the `/api/trace` routes, the Lab UI).

Contract per **DEC-024**:

* :class:`MethodRef` and :class:`FieldRef` are canonical references that
  round-trip to/from the platform-specific bytecode signature. The
  Smali round-trip helpers are baked in here because Android is the
  only adapter today; an iOS adapter would either add ``from_dex_id`` /
  ``from_objc_selector`` factories alongside, or wrap them in an adapter
  module — this DEC does not lock that choice.
* :class:`DecisionPoint` is what 10.1 emits, what 10.2 slices, what
  10.3 classifies, and what 10.4 plans bypasses for. Its shape is
  frozen for v1 — extensions (e.g. ``post_dominator_index`` for v2
  interprocedural slicing) land as additive optional fields.
* :class:`BehaviorAnchor` and :class:`BypassPlan` ship as *minimal
  shells* in 10.1 — just enough to lock the public field names that
  10.4 / 10.5 will populate. Adding fields to a frozen dataclass after
  consumers are wired is harder than locking the canonical fields now.

Everything in this module is a pure value type. No I/O, no SQLite,
no network. Round-trips through ``dataclasses.asdict`` + ``json.dumps``
without surprises so the trace cache (10.5) and the wire format (10.6)
both serialize uniformly.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Union


# ---------------------------------------------------------------------------
# Decision kind enum
#
# String-valued so JSON serialisation matches operator-readable names
# without a custom encoder. The set is closed for v1 — every kind below
# corresponds to a Smali opcode in the Android adapter; iOS / native
# adapters would either reuse these or add platform-specific kinds in a
# v2 expansion.


class DecisionKind(str, Enum):
    """Canonical conditional-branch kinds.

    The 12 ``IF_*`` kinds map 1:1 to Dalvik's conditional opcodes
    (`if-eq`, `if-ne`, …, `if-gez`). The 2 switch kinds map to
    `packed-switch` and `sparse-switch`. Comparisons against constant
    zero use the ``*Z`` variants (one register predicate); two-register
    comparisons use the non-``Z`` variants (two register predicates).
    """

    IF_EQ = "if_eq"
    IF_NE = "if_ne"
    IF_LT = "if_lt"
    IF_LE = "if_le"
    IF_GT = "if_gt"
    IF_GE = "if_ge"
    IF_EQZ = "if_eqz"
    IF_NEZ = "if_nez"
    IF_LTZ = "if_ltz"
    IF_LEZ = "if_lez"
    IF_GTZ = "if_gtz"
    IF_GEZ = "if_gez"
    PACKED_SWITCH = "packed_switch"
    SPARSE_SWITCH = "sparse_switch"


_TWO_REG_KINDS = frozenset({
    DecisionKind.IF_EQ,
    DecisionKind.IF_NE,
    DecisionKind.IF_LT,
    DecisionKind.IF_LE,
    DecisionKind.IF_GT,
    DecisionKind.IF_GE,
})

_SWITCH_KINDS = frozenset({DecisionKind.PACKED_SWITCH, DecisionKind.SPARSE_SWITCH})


# ---------------------------------------------------------------------------
# Method / field references
#
# Both carry the Java-form names plus the raw Smali descriptors so we
# can round-trip cleanly to the bytecode signature (which is what
# call_graph.nodes is keyed by — see :func:`MethodRef.smali_signature`).
#
# Java names (dotted) match what operators see in jadx output. Smali
# descriptors (slashed, ``L...;``-wrapped) match what the parser reads
# straight off the .smali files. Keeping both means no consumer has to
# guess which form they need.

# Smali method signature format: ``Lcom/example/Foo;->bar(IL...;)V``
_RE_SMALI_METHOD = re.compile(
    r"^(?P<class>L[^;\s]+;)->"
    r"(?P<name><(?:cl)?init>|[A-Za-z_$][A-Za-z_$0-9]*)"
    r"\((?P<params>[^)]*)\)(?P<ret>\S+)$"
)
# Smali field signature format: ``Lcom/example/Foo;->mFlag:Z``
_RE_SMALI_FIELD = re.compile(
    r"^(?P<class>L[^;\s]+;)->"
    r"(?P<name>[A-Za-z_$][A-Za-z_$0-9]*):"
    r"(?P<type>\S+)$"
)


def _split_smali_params(params: str) -> tuple[str, ...]:
    """``ILjava/lang/String;[B`` → ``("I", "Ljava/lang/String;", "[B")``.

    Local copy of :func:`androscan.analysis.smali_types.parse_params` so
    this module can be imported without dragging in the call-graph
    parser layer (keeps Phase 10 modules independently importable —
    helps adapters that don't need the call-graph backend at all).
    Defensive: unterminated ``L...`` stops the walk rather than hanging.
    """
    out: list[str] = []
    i = 0
    n = len(params)
    while i < n:
        start = i
        while i < n and params[i] == "[":
            i += 1
        if i >= n:
            break
        c = params[i]
        if c == "L":
            end = params.find(";", i)
            if end < 0:
                break
            out.append(params[start:end + 1])
            i = end + 1
        else:
            out.append(params[start:i + 1])
            i += 1
    return tuple(out)


def _smali_class_to_java(class_desc: str) -> str:
    """``Lcom/example/Foo;`` → ``com.example.Foo``. Inner classes keep
    their ``$`` separator (matches ``Class.getName()``)."""
    if class_desc.startswith("L") and class_desc.endswith(";"):
        return class_desc[1:-1].replace("/", ".")
    return class_desc


def _java_class_to_smali(java_name: str) -> str:
    """``com.example.Foo`` → ``Lcom/example/Foo;``. Inverse of
    :func:`_smali_class_to_java`."""
    return f"L{java_name.replace('.', '/')};"


@dataclass(frozen=True)
class MethodRef:
    """Canonical reference to one method, platform-neutral.

    The ``smali_signature`` round-trip is the canonical join key against
    the call-graph store (``nodes`` table). Other adapters would expose
    their own bytecode-signature accessor alongside.
    """
    class_name: str                       # Java form, e.g. "com.example.Foo"
    method_name: str                      # e.g. "bar"
    param_descriptors: tuple[str, ...]    # raw Smali, e.g. ("I", "Ljava/lang/String;")
    return_descriptor: str                # raw Smali, e.g. "V"

    @property
    def smali_signature(self) -> str:
        """``Lcom/example/Foo;->bar(IL...;)V`` — joins against
        ``call_graph.nodes`` and ``smali_parser.MethodDecl.signature``."""
        params = "".join(self.param_descriptors)
        return f"{_java_class_to_smali(self.class_name)}->{self.method_name}({params}){self.return_descriptor}"

    @classmethod
    def from_smali_signature(cls, sig: str) -> "MethodRef":
        """Parse a smali method signature back into a :class:`MethodRef`.

        Raises ``ValueError`` on malformed input. The 10.5 trace skill
        catches and surfaces these as fail-open empty results rather
        than propagating, but at the data-model layer we stay strict.
        """
        m = _RE_SMALI_METHOD.match(sig.strip())
        if not m:
            raise ValueError(f"not a smali method signature: {sig!r}")
        return cls(
            class_name=_smali_class_to_java(m.group("class")),
            method_name=m.group("name"),
            param_descriptors=_split_smali_params(m.group("params")),
            return_descriptor=m.group("ret"),
        )


@dataclass(frozen=True)
class FieldRef:
    """Canonical reference to one field. Used by 10.2's slicing pass to
    record predicate origins of the form ``iget v0, vB, Lcom/...;->f:Z``.

    Same round-trip discipline as :class:`MethodRef` — Java-form class
    name + raw Smali type descriptor + a ``smali_signature`` accessor.
    """
    class_name: str           # Java form, e.g. "com.example.Foo"
    field_name: str           # e.g. "mIsPremium"
    type_descriptor: str      # raw Smali, e.g. "Z" or "Ljava/lang/String;"

    @property
    def smali_signature(self) -> str:
        """``Lcom/example/Foo;->mIsPremium:Z``."""
        return f"{_java_class_to_smali(self.class_name)}->{self.field_name}:{self.type_descriptor}"

    @classmethod
    def from_smali_signature(cls, sig: str) -> "FieldRef":
        """Parse a smali field signature. Raises ``ValueError`` on
        malformed input (same posture as :meth:`MethodRef.from_smali_signature`)."""
        m = _RE_SMALI_FIELD.match(sig.strip())
        if not m:
            raise ValueError(f"not a smali field signature: {sig!r}")
        return cls(
            class_name=_smali_class_to_java(m.group("class")),
            field_name=m.group("name"),
            type_descriptor=m.group("type"),
        )


# ---------------------------------------------------------------------------
# Branches and decision points
#
# A DecisionPoint's ``branches`` list captures the structural fan-out of
# the conditional. The classifier in 10.3 will tag each branch with a
# ``deny`` / ``allow`` / ``neutral`` outcome by walking the basic block
# starting at the branch target — *not* by inspecting the branches
# themselves. So at this layer we only need to know **where each branch
# goes**, not what it does.
#
# Fall-through is modelled as ``target_label=None`` rather than as a
# synthetic label. This is honest about the Smali model (fall-through is
# implicit, the next instruction wins) and lets the 10.3 walker join
# ``target_label`` against the per-method ``label_index`` map without a
# special case for synthetic labels.


@dataclass(frozen=True)
class Branch:
    """One outgoing control-flow edge from a :class:`DecisionPoint`.

    ``label`` is operator-readable (``"true"`` / ``"false"`` /
    ``"case 0"`` / ``"default"`` etc.) — the UI in 10.7 renders this
    verbatim above the per-branch outcome badge.

    ``target_label`` is the Smali-level label the branch jumps to (e.g.
    ``":cond_0"``, ``":pswitch_3"``); ``None`` means fall-through to
    the next instruction. The classifier resolves it against the
    method's ``label_index`` when it walks the basic block.
    """
    label: str
    target_label: Optional[str]


@dataclass(frozen=True)
class DecisionPoint:
    """One conditional branch (or switch) in a method body.

    Emitted by 10.1's ``decisions.parse_decisions``. Enriched by 10.2's
    ``slicing.slice_predicate_origins`` (populates ``predicate_origin``).
    Classified by 10.3's ``branch_classifier.classify_branch_outcomes``
    (populates ``branch_outcome``). Fed to 10.4's bypass planner.
    Persisted as part of a :class:`BehaviorAnchor` payload by 10.5.
    Rendered by 10.7's ``DecisionTimeline``.

    The platform-neutral fields below are what the LLM, the cache, and
    the UI all consume. Parser-tier metadata (``src_file``, smali line
    number, raw opcode text) lives one layer above on
    :class:`androscan.analysis.decisions.MethodDecisions`.

    ``predicate_origin`` defaults to ``None`` so 10.1 emits decisions
    without it; 10.2 returns a ``MethodDecisions`` whose decisions all
    have ``predicate_origin`` populated (or kept ``None`` on slice
    failure). Pre-slice ``None`` and post-slice ``None`` look identical
    by design — 10.5 only invokes the slicer once per anchor walk so
    the distinction never matters at consumption time.
    """
    method: MethodRef
    instruction_index: int           # 0-based within the containing method body
    source_line: Optional[int]       # most recent ``.line N`` debug info, if present
    kind: DecisionKind
    predicate_registers: tuple[str, ...]  # platform-specific register strings (Smali: "v0", "p1", ...)
    branches: tuple[Branch, ...]
    predicate_origin: Optional["PredicateOrigin"] = None
    branch_outcome: Optional["BranchOutcome"] = None

    @property
    def is_switch(self) -> bool:
        return self.kind in _SWITCH_KINDS

    @property
    def is_two_register_predicate(self) -> bool:
        """``True`` for ``if-eq`` / ``if-ne`` / etc. (compares two
        registers); ``False`` for ``*z`` variants and switches."""
        return self.kind in _TWO_REG_KINDS


# ---------------------------------------------------------------------------
# Predicate origin (10.2 — backward slicing result)
#
# Each variant carries a ``kind`` discriminator field so JSON round-trip
# (``dataclasses.asdict`` + ``json.dumps``) preserves the union variant
# unambiguously — no custom encoder needed, and 10.5's trace.sqlite +
# 10.6's wire format both deserialise via a simple ``kind``-keyed
# dispatch.
#
# **Two-register predicate combination policy (DEC-024 v1):** when the
# decision compares two registers (``if-eq`` / ``if-ne`` / ``if-lt`` /
# ``if-le`` / ``if-gt`` / ``if-ge``), the slicer slices each register
# independently and combines them via the priority rule
# ``MethodCall > FieldRead > Param > Const > Composite > None``. The
# more-actionable side wins because operators typically want to
# manipulate the non-constant operand of a value comparison; for
# two-non-Const cases the higher-priority kind still gives 10.4's
# bypass planner a usable lever. v2 may widen this to a
# ``tuple[PredicateOrigin, ...]`` when both sides are equally relevant.


@dataclass(frozen=True)
class MethodCallOrigin:
    """Predicate value came from a method's return value (``invoke-*``
    immediately preceding a ``move-result-*``).

    ``invoke_kind`` carries the Smali dispatch kind (``virtual`` /
    ``static`` / ``direct`` / ``super`` / ``interface`` /
    ``polymorphic`` / ``custom``) so 10.4's bypass planner can select
    the right hook template without re-parsing the call site.
    """
    method: MethodRef
    invoke_kind: str
    kind: str = "method_call"


@dataclass(frozen=True)
class FieldReadOrigin:
    """Predicate value came from a field read (``iget-*`` for instance
    fields or ``sget-*`` for static fields).

    ``is_static`` distinguishes the two so 10.4 picks the right
    ``Java.use(class).fieldName.value = ...`` shape (instance fields
    need ``this`` capture; static fields don't).
    """
    field: FieldRef
    is_static: bool
    kind: str = "field_read"


@dataclass(frozen=True)
class ConstOrigin:
    """Predicate value is a constant literal loaded by a ``const-*``
    opcode. ``value`` is the raw Smali literal text (``"0x1"``,
    ``"\\"premium\\""``, ``"#42"``) — preserved verbatim so the
    operator's mental model of the source survives into the UI.
    ``smali_op`` records which ``const-*`` variant produced it
    (``const`` / ``const/4`` / ``const-string`` / etc.).
    """
    value: str
    smali_op: str
    kind: str = "const"


@dataclass(frozen=True)
class ParamOrigin:
    """Predicate is an unmodified method parameter — backward walk
    reached the start of the method body without finding a definition,
    and the tracked register is a Smali parameter register (``pN``).

    ``register`` is the raw Smali register (``"p0"``, ``"p1"``, ...).
    Mapping back to a 0-based parameter index requires knowing whether
    the enclosing method is static (apktool's convention is ``p0 =
    this`` for instance methods); the slicer doesn't carry that
    context, so consumers join against
    :class:`androscan.analysis.smali_parser.MethodDecl.is_static` (or
    the call-graph store) when they need the index.
    """
    register: str
    kind: str = "param"


@dataclass(frozen=True)
class CompositeOrigin:
    """Predicate is the result of a composite expression — arithmetic
    (``add-int``, ``sub-int``, ...), comparison (``cmp-long``,
    ``cmpl-float``, ...), instance-of (``instance-of``), array access
    (``aget-*``, ``array-length``), cast (``int-to-byte``, ...),
    object allocation (``new-instance``, ``new-array``), or exception
    capture (``move-exception``).

    ``reason`` carries the Smali opcode (e.g. ``"add-int"``,
    ``"instance-of"``, ``"move-exception"``) so 10.3 / 10.5 / the
    operator know what kind of computation produced the value. v1
    deliberately doesn't break composites down further (intra-
    procedural slicing limitation per DEC-024); 10.5's LLM call can
    drill in via the source line if needed.
    """
    reason: str
    kind: str = "composite"


PredicateOrigin = Union[
    MethodCallOrigin,
    FieldReadOrigin,
    ConstOrigin,
    ParamOrigin,
    CompositeOrigin,
]


# ---------------------------------------------------------------------------
# Branch outcome (10.3 — heuristic deterministic classification)
#
# Per :class:`DecisionPoint`, the classifier walks each branch's basic
# block and scores it against a fixed catalog of pentest-relevant
# signals (DENY: ``throw`` / ``System.exit`` / ``Process.killProcess``
# / ``Activity.finish`` / curated string-keyword regex; ALLOW:
# ``setResult`` / ``startActivity*``; weak DENY: branch-length ratio).
# Each branch ends up with a verdict in ``{deny, allow, neutral}``
# plus a signed ``score`` and the human-readable list of ``reasons``
# that fired.
#
# **Confidence tiers (10.3 contract)** — based on
# ``max(|branch.score|)`` across all branches:
#
# * ``>= 1.0`` → ``1.00`` (strong signal — operator can trust the
#   verdict without LLM review)
# * ``>= 0.7`` → ``0.85`` (moderate / string-keyword signal — still
#   above the 0.6 threshold, no LLM re-classification needed)
# * ``>= 0.3`` → ``0.45`` (weak signal only — *below* threshold;
#   10.5's ``trace_behavior`` skill will invoke the LLM to re-classify)
# * else → ``0.00`` (no signals — LLM re-classifies)
#
# Pre-classification, ``DecisionPoint.branch_outcome`` is ``None``;
# post-classification it's always populated (even when verdicts are
# all neutral and confidence is 0.0) so 10.5 can distinguish "didn't
# run the classifier" from "ran it and found nothing".


@dataclass(frozen=True)
class BranchVerdict:
    """One branch's classified outcome.

    ``branch_label`` matches the corresponding :class:`Branch.label` on
    the parent :class:`DecisionPoint` (e.g. ``"true"`` / ``"false"`` /
    ``"case 0"`` / ``"default"``); the ``verdicts`` tuple on
    :class:`BranchOutcome` is ordered to match
    ``DecisionPoint.branches`` so consumers can join by index *or* by
    label as convenient.
    """
    branch_label: str
    verdict: str                     # "deny" | "allow" | "neutral"
    score: float                     # signed accumulated score (negative = deny, positive = allow)
    reasons: tuple[str, ...] = ()    # human-readable reasons that fired for this branch


@dataclass(frozen=True)
class BranchOutcome:
    """Per-:class:`DecisionPoint` classification result from the
    heuristic classifier (:mod:`androscan.analysis.branch_classifier`).

    ``confidence`` is a deterministic float in ``[0.0, 1.0]``; gates
    with ``confidence < 0.6`` are flagged for LLM re-classification by
    the 10.5 ``trace_behavior`` skill. ``reasons`` carries
    method-level / cross-branch reasons (e.g. ``"branch length ratio
    3:1 → weak deny on shorter side"``) — per-branch reasons live on
    each :class:`BranchVerdict`.
    """
    verdicts: tuple[BranchVerdict, ...]
    confidence: float
    reasons: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# BehaviorAnchor + BypassPlan — minimal shells, populated by 10.4 / 10.5
#
# Per DEC-024 the canonical field names for these types are locked at
# 10.1 so consumers wired in 10.6+ aren't blocked on a frozen-dataclass
# field rename. The shells below are authoritative for v1 field names;
# adding *new* optional fields in 10.4 / 10.5 stays backwards-compatible.


@dataclass(frozen=True)
class BehaviorAnchor:
    """A populated trace payload — one entry method + the closure of
    decision points reachable within ``hops`` hops.

    Populated by the 10.5 ``trace_behavior`` skill; persisted to the
    per-app ``trace.sqlite`` cache (one row per
    ``(entry_method.smali_signature, hops)`` key). 10.7's
    ``BehaviorAnchorCard`` renders the header; ``DecisionTimeline``
    renders the per-decision verdicts; ``BypassPlanCard`` renders the
    plans. Exact populated shape (decision/outcome/plan groupings)
    finalised in 10.5; the *outer* shape is locked here.
    """
    entry_method: MethodRef
    hops: int
    truncated: bool = False               # True when MAX_TRACE_HOPS or MAX_TRACE_METHODS hit
    incomplete: bool = False              # True when any decision has unresolved predicate origin
    decisions: tuple[DecisionPoint, ...] = ()
    plans: tuple["BypassPlan", ...] = ()


@dataclass(frozen=True)
class BypassPlan:
    """A concrete strategy to circumvent a :class:`DecisionPoint`.

    Template-bound per DEC-024 — every plan references one of the
    existing ``frida_hooks/`` templates (or new override templates added
    in 10.4). Free-form LLM JS is explicitly v2.

    ``risk`` and ``risks`` are the operator-facing risk affordances; the
    planner refuses to emit any plan whose ``risk`` exceeds the
    operator-configured ``trace.bypass_risk_max`` threshold (default
    ``"medium"``). High-risk plans are surfaced behind an "Advanced"
    expander in 10.7's UI rather than emitted into the default list.
    """
    template_id: str
    params: dict[str, str]
    rationale: str
    risk: str                             # "low" | "medium" | "high" — locked enum 10.4
    risks: tuple[str, ...] = ()
