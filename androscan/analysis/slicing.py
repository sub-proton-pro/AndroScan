"""Backward slicer for decision predicates — Phase 10 sub-step 10.2,
extended in Phase 11 sub-step 11.4 to support **bounded
inter-procedural descent** through stateless helper methods, and in
Phase 11 sub-step 11.5 to support **same-class field-write-site
walking**.

Given a :class:`androscan.analysis.decisions.MethodDecisions` (one
method's body of branches + raw instruction stream), trace each
:class:`androscan.analysis.trace_types.DecisionPoint`'s
``predicate_registers`` backward through the instruction stream until
we find the closest defining instruction. Classify that instruction
into one of five :class:`PredicateOrigin` variants:

* :class:`MethodCallOrigin` — predicate came from a method's return
  value (``invoke-* ... ; move-result-* vN``).
* :class:`FieldReadOrigin` — predicate came from a field read
  (``iget-*`` for instance, ``sget-*`` for static).
* :class:`ConstOrigin` — predicate is a constant literal
  (``const-*`` family).
* :class:`ParamOrigin` — predicate is an unmodified Smali parameter
  register (``pN``); walk reached the start of the method body without
  finding a definition.
* :class:`CompositeOrigin` — predicate is the result of a composite
  expression (arithmetic, comparison, ``instance-of``, array access,
  cast, object allocation, ``move-exception``); v1 doesn't break these
  down further per DEC-024's intra-procedural slicing limitation.

When the slice fails outright (walk exhausts ``max_walk`` instructions
without resolution, or a ``vN`` register is undefined in scope), the
slicer surfaces ``predicate_origin = None`` honestly per DEC-024 — 10.5
will then either flag the gate as low-confidence or invoke the LLM
with the raw method body for re-classification.

Two-register predicate combination policy (DEC-024 v1)
------------------------------------------------------

For ``if-eq`` / ``if-ne`` / ``if-lt`` / ``if-le`` / ``if-gt`` /
``if-ge`` — comparisons of two registers — the slicer slices each
register independently and combines them via the priority rule

    MethodCall > FieldRead > Param > Const > Composite > None

The more-actionable side wins because operators typically want to
manipulate the non-constant operand (a comparison against a constant
encodes "compare against this value", and the value itself is rarely
the bypass lever). v2 may widen this to a full
``tuple[PredicateOrigin, ...]`` when both sides matter equally.

Phase 11 sub-step 11.4 — bounded inter-procedural descent
---------------------------------------------------------

The v1 slicer terminated at every :class:`MethodCallOrigin` without
inspecting the callee. v2 extends this: when the slicer would
otherwise terminate at a :class:`MethodCallOrigin` AND the callee is
known to be stateless (per :func:`is_stateless` + the
:data:`_STATELESS_LIB_DENYLIST` short-circuit) AND the descent budget
permits, the slicer **descends into the callee** and re-runs
:func:`_slice_register` on the callee's ``return-*`` instruction's
source register. The descended :class:`PredicateOrigin` *replaces*
the original :class:`MethodCallOrigin` in the slicer's output:
operators see the new terminal (e.g. ``ConstOrigin("premium")`` for
a getter that returns a constant), not the surface-level call.

Bounded by:

* :data:`MAX_SLICE_DEPTH` (default ``2``; hard cap ``4`` in code) —
  configurable via ``trace.max_slice_depth`` in 11.6.
* :class:`_DescentBudget`'s ``visited`` set keyed on
  ``(class_smali, method_name, descriptor)`` — terminates cycles +
  prevents redundant re-descent into hub helpers.
* :func:`is_stateless` — type-driven analyzer over the callee's body
  looking for side effects (field/array writes, monitor, throw,
  reflection, calls to other stateful methods).

Phase 11 sub-step 11.5 — same-class field-write-site walking
------------------------------------------------------------

When the slicer would otherwise terminate at a
:class:`FieldReadOrigin` AND the field is on the same class as the
reading method (cross-class field-flow stays out of scope per
ISSUE-013) AND the descent budget permits, the slicer walks every
``iput-*`` / ``sput-*`` write site for that field across the class's
methods, picks the most-recent write per the **constructor-priority
rule** (Q1 (A) of the 11.5 planning checkpoint — prefer the
textually-last write in ``<init>`` / ``<clinit>`` over any
non-constructor write; ties within a constructor broken by source
order), then re-runs :func:`_slice_register` on the written register
at that write site. The descended :class:`PredicateOrigin` *replaces*
the original :class:`FieldReadOrigin` in the slicer's output (same
UI density discipline as 11.4's method descent — operator sees the
new terminal, depth pill surfaces the "via 1 field write" signal).

Closed economy: the same :class:`_DescentBudget` is shared between
method descent (11.4) and field-write-site walking (11.5) per
DEC-025 — a method that descends 2 hops via callees can't *also*
walk a field-write site, and vice versa. The composed inner slice
is run through :func:`_maybe_descend` so a field-write source that
itself resolves to a :class:`MethodCallOrigin` or another
:class:`FieldReadOrigin` can be further descended (subject to the
same shared budget).

11.5 v1 approximations (to be revisited in v3 / Phase 12):

* **Strict same-class match** — Q2 (A) of the planning checkpoint;
  no superclass walking. A method on ``Child`` reading a field
  declared on ``Parent`` won't trigger field-write descent.
* **Receiver-agnostic** — Q3 (A) of the planning checkpoint;
  ``iput vSrc, vObj, ...`` write sites are matched by
  ``(class, field_name)`` only, ignoring the ``vObj`` receiver.
  The textbook ``this.mField`` field-cached idiom is correct under
  this rule (only-write-site scenario), but more aggressive aliasing
  cases may pick a write to a different instance's field.
* **Lazy walk per descent** — Q4 (A) of the planning checkpoint;
  no eager indexing pass. Bounded by the closed-economy budget;
  promote to indexed lookup in 11.7 only if telemetry shows
  megaclass cost.

Out of scope for v1 (still out for v2; deferred to v3 / Phase 12)
-----------------------------------------------------------------

* Aliasing / field-flow / escape analysis (DEC-024 / DEC-025
  limitation).
* SSA / phi-node reconstruction — the slicer follows the most-recent
  definition in linear instruction order; control-flow joins are
  flattened (the most-recent definition along the textual order wins).
* ``move-result-*`` orphan detection beyond the immediate predecessor
  — if a ``move-result-*`` isn't preceded directly by an ``invoke-*``,
  the slicer falls back to ``CompositeOrigin``.
"""

from __future__ import annotations

import dataclasses
import logging
import re
from typing import Optional

from androscan.analysis.decisions import MethodDecisions
from androscan.analysis.smali_parser import ClassDecl
from androscan.analysis.trace_types import (
    CompositeOrigin,
    ConstOrigin,
    DecisionPoint,
    FieldReadOrigin,
    FieldRef,
    MethodCallOrigin,
    MethodRef,
    ParamOrigin,
    PredicateOrigin,
)

logger = logging.getLogger(__name__)

DEFAULT_MAX_WALK = 64

# ---------------------------------------------------------------------------
# Phase 11 sub-step 11.4 — bounded inter-procedural descent

#: Default maximum descent depth (helper-method hops the slicer will
#: chase past a v1 :class:`MethodCallOrigin` terminal). 11.6 promotes
#: this to a ``trace.max_slice_depth`` config knob; until then this
#: module constant is the single source of truth.
MAX_SLICE_DEPTH = 2

#: Hard cap regardless of operator config (defensive). Even if 11.6's
#: config knob is set higher, descent stops at this depth — operators
#: rarely benefit from chains longer than 4 hops, and the worst-case
#: per-anchor cost grows linearly so an unlucky misconfig shouldn't
#: blow the per-anchor budget.
HARD_CAP_DEPTH = 4

#: Curated list of stdlib classes whose methods we treat as stateless
#: without inspecting their Smali (we don't ship Android SDK / JDK
#: smali in any test or runtime path, so we have to assume statelessness
#: from the class identity). Two value shapes:
#:
#:   * ``None`` → every method on the class is treated as stateless
#:     (whole-class entry, used when *every* common operation on the
#:     class is genuinely pure).
#:   * ``frozenset[str]`` → only the listed method names are treated
#:     as stateless (per-method allowlist; methods not in the set are
#:     treated as stateful — defensive for classes where some methods
#:     allocate or throw).
#:
#: Hand-curated, intentionally small, easy to audit. Lives next to
#: :class:`_DescentBudget` so additions are local + auditable. Per
#: the 11.4 planning checkpoint, the v1 seed is:
#:
#: * ``Ljava/lang/Math;`` (whole class — every static is pure).
#: * Primitive boxing classes (whole class each — getters / parsers
#:   / value-of / compareTo / equals / hashCode are all pure).
#: * ``Ljava/lang/String;`` per-method allowlist (length / charAt /
#:   substring / equals / hashCode / isEmpty / indexOf — explicit
#:   allowlist; methods that allocate like ``concat`` / ``replace``
#:   / ``trim`` / ``toLowerCase`` are NOT included so they fall
#:   through to "stateful" defensively).
#: * ``Lkotlin/jvm/internal/Intrinsics;`` (whole class — Kotlin's
#:   compiler-injected null-check + equality helpers).
#: * ``Ljava/lang/Object;`` per-method allowlist (hashCode / equals /
#:   toString / getClass — extremely common in
#:   ``if obj.equals(...)`` predicates; planning-checkpoint
#:   addition on top of the spec's seed).
_STATELESS_LIB_DENYLIST: dict[str, Optional[frozenset[str]]] = {
    # Whole-class entries (every method pure).
    "Ljava/lang/Math;": None,
    "Ljava/lang/Integer;": None,
    "Ljava/lang/Long;": None,
    "Ljava/lang/Boolean;": None,
    "Ljava/lang/Float;": None,
    "Ljava/lang/Double;": None,
    "Ljava/lang/Byte;": None,
    "Ljava/lang/Short;": None,
    "Ljava/lang/Character;": None,
    "Lkotlin/jvm/internal/Intrinsics;": None,
    # Per-method allowlists.
    "Ljava/lang/String;": frozenset({
        "length", "charAt", "substring", "equals", "hashCode",
        "isEmpty", "indexOf",
    }),
    "Ljava/lang/Object;": frozenset({
        "hashCode", "equals", "toString", "getClass",
    }),
}


@dataclasses.dataclass
class _DescentBudget:
    """Mutable budget shared across method descent and (in 11.5)
    field-write-site walking.

    ``remaining_depth`` decrements as the descent recurses into
    helper methods; reaches zero when the descent has consumed its
    full :data:`MAX_SLICE_DEPTH` allotment. ``visited`` keys on
    ``(class_smali_descriptor, method_name, smali_descriptor)``
    triples — descent skips any callee already in the set, which
    both terminates cycles and prevents repeated work on hub helpers
    that appear in many decision slices.

    Mutability is the chosen posture per the 11.4 planning checkpoint:
    "closed economy" in the spec text implies the same budget instance
    flows across passes, not that each pass instantiates a fresh
    immutable copy. The mutable form is also slightly cheaper (no
    ``dataclasses.replace`` per recursive call). Tests build fresh
    instances per ``slice_predicate_origins`` call so the budget
    never leaks across boundaries.

    Phase 11 sub-step 11.6 — ``current_descent_depth`` is a
    recursion-stack counter (mirrors ``remaining_depth`` but in the
    opposite direction): incremented in :func:`_descend_into_callee`
    and :func:`_walk_field_write_sites` BEFORE the recursive
    re-slice, decremented in their respective ``finally`` blocks.
    Read by :func:`_maybe_descend_method_call` /
    :func:`_maybe_descend_field_read` when they're about to return a
    cap-stop / cycle-blocked / external-callee terminal — the
    returned :class:`MethodCallOrigin` / :class:`FieldReadOrigin`
    is tagged with ``descent_depth = current_descent_depth`` so
    the frontend can render the "via N helpers" depth pill. The
    success path (``return descended``) does **not** re-tag the
    inner result — the inner result was tagged at its own
    deepest recursion frame and that's the depth operators care
    about. Default ``0`` so v1-style budgets (fresh ones at the
    top of every slice call) report no descent.
    """
    remaining_depth: int
    visited: set[tuple[str, str, str]]
    current_descent_depth: int = 0

    @classmethod
    def fresh(cls, max_depth: int = MAX_SLICE_DEPTH) -> "_DescentBudget":
        """Build a fresh budget with the configured max depth (clamped
        to :data:`HARD_CAP_DEPTH`). Default factory used by the
        public :func:`slice_predicate_origins` entry point when the
        caller doesn't supply its own."""
        return cls(
            remaining_depth=min(max(max_depth, 0), HARD_CAP_DEPTH),
            visited=set(),
            current_descent_depth=0,
        )


# ---------------------------------------------------------------------------
# Smali instruction tokenisation
#
# We only need to identify (a) the opcode, (b) the destination register
# (if any), and (c) opcode-specific operands (field signature for
# iget/sget, method signature for invoke, literal for const). Everything
# else is opaque to the slicer.

# Opcode + first register operand. Most defining opcodes write to the
# first register; the exceptions live in :data:`_NO_DEF_OPCODES` below.
_RE_OPCODE_DEST = re.compile(
    r"^(?P<op>[a-z][a-z0-9-]*(?:/[a-z0-9]+)*)"
    r"(?:\s+(?P<dest>[vp]\d+))?"
)

# move vDest, vSrc / move-wide / move-object (with /from16 / /16 widenings)
_RE_MOVE = re.compile(
    r"^move(?:-wide|-object)?(?:/(?:from16|16))?\s+"
    r"(?P<dest>[vp]\d+)\s*,\s*(?P<src>[vp]\d+)"
)

# move-result vDest / move-result-wide / move-result-object
_RE_MOVE_RESULT = re.compile(
    r"^move-result(?:-wide|-object)?\s+(?P<dest>[vp]\d+)"
)

# move-exception vDest
_RE_MOVE_EXCEPTION = re.compile(r"^move-exception\s+(?P<dest>[vp]\d+)")

# const vDest, value (covers all const-* variants; ``value`` is the
# raw text of the literal — int, hex, float, string, class, etc.).
_RE_CONST = re.compile(
    r"^(?P<op>const(?:/4|/16|/high16|-wide(?:/16|/32|/high16)?|-string(?:/jumbo)?|-class)?)"
    r"\s+(?P<dest>[vp]\d+)\s*,\s*(?P<value>.+?)\s*$"
)

# iget vDest, vObj, Lcom/Foo;->mField:Type (covers all iget-* variants).
_RE_IGET = re.compile(
    r"^iget(?:-wide|-object|-boolean|-byte|-char|-short)?\s+"
    r"(?P<dest>[vp]\d+)\s*,\s*[vp]\d+\s*,\s*(?P<field>L[^;\s]+;->[A-Za-z_$][A-Za-z_$0-9]*:\S+)"
)
# sget vDest, Lcom/Foo;->mField:Type (covers all sget-* variants).
_RE_SGET = re.compile(
    r"^sget(?:-wide|-object|-boolean|-byte|-char|-short)?\s+"
    r"(?P<dest>[vp]\d+)\s*,\s*(?P<field>L[^;\s]+;->[A-Za-z_$][A-Za-z_$0-9]*:\S+)"
)
# iput vSrc, vObj, Lcom/Foo;->mField:Type (covers all iput-* variants).
# Phase 11 sub-step 11.5 — used by ``_walk_field_write_sites`` to
# locate field-write candidates. Note ``vSrc`` is the *source*
# register (the value being written) — opposite role to iget's vDest.
_RE_IPUT = re.compile(
    r"^iput(?:-wide|-object|-boolean|-byte|-char|-short)?\s+"
    r"(?P<src>[vp]\d+)\s*,\s*[vp]\d+\s*,\s*(?P<field>L[^;\s]+;->[A-Za-z_$][A-Za-z_$0-9]*:\S+)"
)
# sput vSrc, Lcom/Foo;->mField:Type (covers all sput-* variants).
_RE_SPUT = re.compile(
    r"^sput(?:-wide|-object|-boolean|-byte|-char|-short)?\s+"
    r"(?P<src>[vp]\d+)\s*,\s*(?P<field>L[^;\s]+;->[A-Za-z_$][A-Za-z_$0-9]*:\S+)"
)

# invoke-{kind}{,/range} {regs}, owner;->name(params)return
_RE_INVOKE = re.compile(
    r"^invoke-(?P<kind>virtual|super|direct|static|interface|polymorphic|custom)"
    r"(?:/range)?\s+\{[^}]*\}\s*,\s*"
    r"(?P<owner>L[^;\s]+;)->"
    r"(?P<method><(?:cl)?init>|[A-Za-z_$][A-Za-z_$0-9]*)"
    r"\((?P<params>[^)]*)\)(?P<ret>\S+)"
)


# Opcodes that do not define any register — the slicer must skip past
# these without treating them as candidates. (``check-cast`` reads its
# register and asserts a type but doesn't write a new value;
# ``filled-new-array`` writes a hidden result that's read via a
# subsequent ``move-result-object``, so it's not a direct definer.)
_NO_DEF_OPCODES = frozenset({
    # Field writes
    "iput", "iput-wide", "iput-object", "iput-boolean",
    "iput-byte", "iput-char", "iput-short",
    "sput", "sput-wide", "sput-object", "sput-boolean",
    "sput-byte", "sput-char", "sput-short",
    # Array writes
    "aput", "aput-wide", "aput-object", "aput-boolean",
    "aput-byte", "aput-char", "aput-short",
    # Control flow
    "goto", "goto/16", "goto/32",
    "return", "return-void", "return-wide", "return-object",
    "throw",
    # Synchronisation
    "monitor-enter", "monitor-exit",
    # Type assertion (in-place)
    "check-cast",
    # Misc
    "nop",
    # Array data block (data-only, not a real instruction)
    "fill-array-data",
    # Filled-new-array writes a hidden result, read via move-result-object.
    "filled-new-array", "filled-new-array/range",
})


_CONST_OPCODES = frozenset({
    "const", "const/4", "const/16", "const/high16",
    "const-wide", "const-wide/16", "const-wide/32", "const-wide/high16",
    "const-string", "const-string/jumbo", "const-class",
})

_MOVE_OPCODES = frozenset({
    "move", "move/from16", "move/16",
    "move-wide", "move-wide/from16", "move-wide/16",
    "move-object", "move-object/from16", "move-object/16",
})

_MOVE_RESULT_OPCODES = frozenset({
    "move-result", "move-result-wide", "move-result-object",
})

_IGET_OPCODES = frozenset({
    "iget", "iget-wide", "iget-object", "iget-boolean",
    "iget-byte", "iget-char", "iget-short",
})

_SGET_OPCODES = frozenset({
    "sget", "sget-wide", "sget-object", "sget-boolean",
    "sget-byte", "sget-char", "sget-short",
})


# ---------------------------------------------------------------------------
# Public API


def slice_predicate_origins(
    method_decisions: MethodDecisions,
    *,
    max_walk: int = DEFAULT_MAX_WALK,
    classes_by_smali: Optional[dict[str, ClassDecl]] = None,
    decisions_by_method_sig: Optional[dict[str, MethodDecisions]] = None,
    reflective_method_sigs: frozenset[str] = frozenset(),
    descent_budget: Optional[_DescentBudget] = None,
) -> MethodDecisions:
    """Return a :class:`MethodDecisions` with every decision's
    ``predicate_origin`` populated (or ``None`` on slice failure).

    The input is not mutated — frozen dataclass replaced via
    :func:`dataclasses.replace`. Callers can chain
    ``slice_predicate_origins(method_decisions)`` directly into 10.3's
    classifier without bookkeeping.

    Phase 11 sub-step 11.4 — bounded inter-procedural descent. When
    ``classes_by_smali`` AND ``decisions_by_method_sig`` are both
    provided, the slicer descends past v1 :class:`MethodCallOrigin`
    terminals into stateless helper-method bodies (up to
    :data:`MAX_SLICE_DEPTH` hops). When either is ``None``, the
    slicer falls back to v1 intra-procedural behaviour — every
    :class:`MethodCallOrigin` surfaces as a terminal regardless of
    callee statelessness. Tests and ad-hoc callers that only care
    about v1 semantics can omit both arguments entirely; the public
    signature stays backwards-compatible.

    ``reflective_method_sigs`` is a hint set of method signatures the
    call-graph indexer flagged as ``may_have_unresolved_reflection``;
    :func:`is_stateless` treats any descent into one of these as
    stateful (defensive — reflection results can have arbitrary side
    effects). When omitted, no method is treated as reflective —
    safe for tests but the production caller (the ``trace_behavior``
    skill) populates this from the call-graph SQLite cache.

    ``descent_budget`` lets a caller share a budget across multiple
    slice calls (the "closed economy" pattern from DEC-025 — 11.5's
    field-write-site walking will draw from the same budget). When
    omitted, a fresh budget is built per call.
    """
    instructions = method_decisions.instructions
    enriched: list[DecisionPoint] = []
    descent_enabled = (
        classes_by_smali is not None and decisions_by_method_sig is not None
    )
    budget = descent_budget if descent_budget is not None else _DescentBudget.fresh()
    # Phase 11 sub-step 11.5 — derive the reading method's class for
    # the same-class field-write gating. ``method_signature`` is
    # always the smali form ``Lcom/Foo;->bar(I)V``; the class part
    # before ``->`` is the gate input.
    current_class_smali = _class_from_method_sig(method_decisions.method_signature)
    for dp in method_decisions.decision_points:
        origin = _slice_one(dp, instructions, max_walk=max_walk)
        if descent_enabled and origin is not None:
            origin = _maybe_descend(
                origin,
                budget=budget,
                max_walk=max_walk,
                classes_by_smali=classes_by_smali,  # type: ignore[arg-type]
                decisions_by_method_sig=decisions_by_method_sig,  # type: ignore[arg-type]
                reflective_method_sigs=reflective_method_sigs,
                current_class_smali=current_class_smali,
            )
        enriched.append(dataclasses.replace(dp, predicate_origin=origin))
    return dataclasses.replace(method_decisions, decision_points=tuple(enriched))


def slice_one_decision(
    decision: DecisionPoint,
    instructions: tuple[str, ...],
    *,
    max_walk: int = DEFAULT_MAX_WALK,
) -> Optional[PredicateOrigin]:
    """Standalone slice for one :class:`DecisionPoint` against a raw
    instruction stream. Useful for tests and for 10.5's per-anchor
    walk where the caller may hold the decision separately from the
    enclosing :class:`MethodDecisions`. Returns the origin (or
    ``None`` on slice failure) without wrapping.

    No inter-procedural descent — for descent the caller must use
    :func:`slice_predicate_origins` with the optional
    ``classes_by_smali`` + ``decisions_by_method_sig`` kwargs.
    """
    return _slice_one(decision, instructions, max_walk=max_walk)


# ---------------------------------------------------------------------------
# Slice walker


def _slice_one(
    decision: DecisionPoint,
    instructions: tuple[str, ...],
    *,
    max_walk: int,
) -> Optional[PredicateOrigin]:
    """Slice one decision's predicate. Dispatches to single-register
    or two-register handler based on :attr:`DecisionPoint.is_two_register_predicate`."""
    if decision.is_two_register_predicate:
        # Slice both operands independently then combine via priority.
        if len(decision.predicate_registers) < 2:
            # Defensive — shouldn't happen but if it does, fall back.
            return _slice_register(
                decision.predicate_registers[0],
                decision.instruction_index,
                instructions,
                max_walk=max_walk,
            )
        a = _slice_register(
            decision.predicate_registers[0],
            decision.instruction_index,
            instructions,
            max_walk=max_walk,
        )
        b = _slice_register(
            decision.predicate_registers[1],
            decision.instruction_index,
            instructions,
            max_walk=max_walk,
        )
        return _combine_two_origins(a, b)
    # Single-register predicate (if-*z, packed-switch, sparse-switch).
    return _slice_register(
        decision.predicate_registers[0],
        decision.instruction_index,
        instructions,
        max_walk=max_walk,
    )


def _slice_register(
    register: str,
    start_index: int,
    instructions: tuple[str, ...],
    *,
    max_walk: int,
) -> Optional[PredicateOrigin]:
    """Walk backward from ``instructions[start_index - 1]`` until we
    find a definition of ``register`` (or one it aliases via ``move``).

    Returns the origin or ``None`` on slice failure. The walk:

    * follows ``move`` chains by replacing the tracked register with
      the source register and continuing,
    * stops at the first non-``move`` definition of any tracked
      register and classifies it,
    * gives up after ``max_walk`` steps,
    * returns :class:`ParamOrigin` if the walk reaches index ``-1``
      with a ``pN`` register still tracked,
    * returns ``None`` for unresolved ``vN`` (undefined in scope).
    """
    tracked = register
    steps = 0
    k = start_index - 1
    while k >= 0 and steps < max_walk:
        steps += 1
        line = instructions[k]
        op_match = _RE_OPCODE_DEST.match(line)
        if not op_match:
            k -= 1
            continue
        opcode = op_match.group("op")
        dest = op_match.group("dest")
        # Skip lines that don't define a register at all.
        if opcode in _NO_DEF_OPCODES or opcode.startswith("invoke-"):
            k -= 1
            continue
        # If-* and *-switch are the slice-start opcodes; we should
        # never see them mid-walk because the walk starts at
        # ``start_index - 1``. Defensive skip.
        if opcode.startswith("if-") or opcode.endswith("-switch"):
            k -= 1
            continue
        if dest != tracked:
            k -= 1
            continue

        # Definition found for the tracked register — classify.
        # ``move`` chain: follow the source register and continue.
        mv = _RE_MOVE.match(line)
        if mv:
            tracked = mv.group("src")
            k -= 1
            continue

        # ``move-result-*``: look back one step for the invoke-*.
        if opcode in _MOVE_RESULT_OPCODES:
            return _classify_move_result(instructions, k)

        # ``move-exception``: caught exception → composite (runtime).
        if opcode == "move-exception":
            return CompositeOrigin(reason="move-exception")

        # ``const-*``: direct literal load.
        if opcode in _CONST_OPCODES:
            return _classify_const(line, opcode)

        # ``iget-*``: instance field read.
        if opcode in _IGET_OPCODES:
            return _classify_field_read(line, is_static=False, regex=_RE_IGET)

        # ``sget-*``: static field read.
        if opcode in _SGET_OPCODES:
            return _classify_field_read(line, is_static=True, regex=_RE_SGET)

        # Anything else that defines this register is a composite
        # computation (arithmetic / comparison / instance-of / cast /
        # array access / new-instance / new-array / etc.). The opcode
        # itself is the most useful "reason" — surface it verbatim so
        # 10.3 / the operator know what kind of computation produced
        # the value.
        return CompositeOrigin(reason=opcode)

    # Walk exhausted without finding a definition.
    if steps >= max_walk:
        # Honest slice failure — the predicate may well be definable,
        # but we capped the walk to keep 10.5's per-anchor budget
        # bounded. None signals "trace is incomplete here" to 10.5/10.7.
        return None
    # k < 0 — reached the start of the method body. If the tracked
    # register is a parameter (``pN``), this is a ParamOrigin.
    # Otherwise the predicate is reading an undefined ``vN`` —
    # malformed Smali; surface as None.
    if tracked.startswith("p"):
        return ParamOrigin(register=tracked)
    return None


def _classify_move_result(
    instructions: tuple[str, ...],
    move_result_index: int,
) -> PredicateOrigin:
    """Look back one step for the ``invoke-*`` that produced the
    result. Falls back to :class:`CompositeOrigin` when the predecessor
    isn't an invoke (malformed bytecode — shouldn't happen in apktool
    output but defensive)."""
    if move_result_index - 1 < 0:
        return CompositeOrigin(reason="orphan-move-result")
    prev = instructions[move_result_index - 1]
    inv = _RE_INVOKE.match(prev)
    if not inv:
        return CompositeOrigin(reason="orphan-move-result")
    sig = (
        f"{inv.group('owner')}->{inv.group('method')}"
        f"({inv.group('params')}){inv.group('ret')}"
    )
    try:
        method_ref = MethodRef.from_smali_signature(sig)
    except ValueError:
        return CompositeOrigin(reason="orphan-move-result")
    return MethodCallOrigin(method=method_ref, invoke_kind=inv.group("kind"))


def _classify_const(line: str, opcode: str) -> PredicateOrigin:
    """Build a :class:`ConstOrigin` from a ``const-*`` instruction line."""
    m = _RE_CONST.match(line)
    if not m:
        return CompositeOrigin(reason=opcode)
    return ConstOrigin(value=m.group("value"), smali_op=opcode)


def _classify_field_read(
    line: str,
    *,
    is_static: bool,
    regex: re.Pattern[str],
) -> PredicateOrigin:
    """Build a :class:`FieldReadOrigin` from an ``iget-*`` / ``sget-*``
    instruction line."""
    m = regex.match(line)
    if not m:
        return CompositeOrigin(reason="sget" if is_static else "iget")
    try:
        field_ref = FieldRef.from_smali_signature(m.group("field"))
    except ValueError:
        return CompositeOrigin(reason="sget" if is_static else "iget")
    return FieldReadOrigin(field=field_ref, is_static=is_static)


# ---------------------------------------------------------------------------
# Two-register origin combination


_PRIORITY: dict[type, int] = {
    MethodCallOrigin: 5,
    FieldReadOrigin: 4,
    ParamOrigin: 3,
    ConstOrigin: 2,
    CompositeOrigin: 1,
}


def _origin_priority(origin: Optional[PredicateOrigin]) -> int:
    if origin is None:
        return 0
    return _PRIORITY.get(type(origin), 0)


def _combine_two_origins(
    a: Optional[PredicateOrigin],
    b: Optional[PredicateOrigin],
) -> Optional[PredicateOrigin]:
    """Pick the more-actionable side of a two-register comparison.

    Priority order: ``MethodCall > FieldRead > Param > Const >
    Composite > None``. Ties broken in favour of the first operand
    (left-hand side of the smali ``if-*`` instruction) so two slices
    of the same kind produce a stable, source-order result.
    """
    if a is None and b is None:
        return None
    pa = _origin_priority(a)
    pb = _origin_priority(b)
    return a if pa >= pb else b


# ===========================================================================
# Phase 11 sub-step 11.4 — bounded inter-procedural descent
# ===========================================================================
#
# The descent layer sits ABOVE the v1 slicer. The v1 :func:`_slice_one`
# stays untouched — it always terminates at the closest defining
# instruction, including :class:`MethodCallOrigin` for invoke-* +
# move-result patterns. The descent code below decides, AFTER v1
# slicing, whether to chase past a :class:`MethodCallOrigin` into the
# callee's body and re-slice. The descended :class:`PredicateOrigin`
# *replaces* the original (operator sees the new terminal, not the
# chain that produced it).


# Smali ``return`` / ``return-wide`` / ``return-object`` (NOT
# ``return-void`` — void methods have no return register to slice).
_RE_RETURN_WITH_REG = re.compile(
    r"^return(?:-wide|-object)?\s+(?P<src>[vp]\d+)\s*$"
)


def _denylist_says_stateless(class_desc: str, method_name: str) -> Optional[bool]:
    """Three-valued lookup against :data:`_STATELESS_LIB_DENYLIST`.

    Returns ``True`` if the class is whole-class-listed OR the method
    is in the per-method allowlist; ``False`` if the class is in the
    deny-list with an allowlist that doesn't include this method
    (treat as stateful — defensive); ``None`` if the class isn't in
    the deny-list at all (caller falls through to body-walking).
    """
    entry = _STATELESS_LIB_DENYLIST.get(class_desc)
    if entry is None and class_desc not in _STATELESS_LIB_DENYLIST:
        return None
    if entry is None:
        # Whole-class entry — every method is stateless.
        return True
    return method_name in entry


def is_stateless(
    method_sig: str,
    *,
    classes_by_smali: dict[str, ClassDecl],
    decisions_by_method_sig: dict[str, MethodDecisions],
    reflective_method_sigs: frozenset[str] = frozenset(),
    visited: Optional[set[tuple[str, str, str]]] = None,
) -> bool:
    """Type-driven analyzer — does the body of ``method_sig`` have
    any side effect we'd care about for slicer descent?

    Walks the method's instruction stream looking for:

    * ``iput-*`` / ``sput-*`` / ``aput-*`` (field / array writes —
      definitely stateful).
    * ``monitor-enter`` / ``monitor-exit`` (synchronization —
      defensively stateful; locks observe shared state).
    * ``throw vN`` (exception throw — short-circuits the method's
      return value semantics; descending past this would claim the
      wrong terminal).
    * ``invoke-*`` to non-stateless callees (recursive — reuses the
      ``visited`` set to terminate cycles; cycles are treated as
      stateful per the v2 defensive default — see the cycle test
      fixture in ``Helpers.smali`` for the rationale).
    * The method is flagged ``may_have_unresolved_reflection`` in
      the call graph (passed via ``reflective_method_sigs``) —
      reflection results can have arbitrary effects.

    Pure-arithmetic / comparison / ``move-*`` / ``return-*`` /
    ``nop`` / ``goto*`` / ``if-*`` / ``const-*`` / ``new-instance``
    / ``new-array`` / type-cast / ``instance-of`` / field READS
    (``iget-*`` / ``sget-*`` — read, no write) / array READS
    (``aget-*``) / ``move-result-*`` / ``move-exception`` are all
    stateless from the operator's perspective.

    Returns ``True`` when no side effect is found and every recursed
    callee is also stateless.

    The ``method_sig`` lookup uses the canonical smali signature
    (``Lcom/example/Foo;->bar(I)V``) to find the
    :class:`MethodDecisions` body. When the signature isn't in
    ``decisions_by_method_sig``, the analyzer falls back to the
    deny-list (handles stdlib classes we have no Smali source for);
    when the deny-list also doesn't know the method, the analyzer
    returns ``False`` (defensive — assume stateful when uncertain).
    """
    if visited is None:
        visited = set()

    # Cycle break — if we're already analyzing this method up the
    # stack, treat as stateful (defensive — don't claim purity
    # without proof). The cycle case is rare in practice but
    # mutual-recursion between helpers does happen.
    parsed = _parse_signature_for_cycle_key(method_sig)
    if parsed in visited:
        return False
    visited.add(parsed)
    try:
        # Reflection check — single most-restrictive predicate.
        if method_sig in reflective_method_sigs:
            return False

        # Body lookup. When the method has no decisions registered
        # (linear method body — no if-* / *-switch), it still has an
        # instruction stream IF ``parse_decisions`` parsed it. v1's
        # parser only emits ``MethodDecisions`` for methods with at
        # least one decision, so linear stateless helpers like
        # ``return v0`` won't appear in ``decisions_by_method_sig``.
        # Fall back to the deny-list for those.
        md = decisions_by_method_sig.get(method_sig)
        if md is None:
            class_desc, method_name, _ = parsed
            denylist = _denylist_says_stateless(class_desc, method_name)
            if denylist is not None:
                return denylist
            # Method body unknown + not on the deny-list. Could be
            # an external (Android framework) method we don't model,
            # an in-app linear method the parser skipped, or just
            # something the analyzer hasn't been taught about. The
            # safe default is False (assume stateful) — descending
            # into a method we can't see is the bad case.
            return False

        # Body-walking — examine every instruction.
        for line in md.instructions:
            if _line_is_stateful(
                line,
                classes_by_smali=classes_by_smali,
                decisions_by_method_sig=decisions_by_method_sig,
                reflective_method_sigs=reflective_method_sigs,
                visited=visited,
            ):
                return False
        return True
    finally:
        # Pop the cycle-key on the way out so siblings don't see this
        # method as already-visited (only ancestors should).
        visited.discard(parsed)


def _line_is_stateful(
    line: str,
    *,
    classes_by_smali: dict[str, ClassDecl],
    decisions_by_method_sig: dict[str, MethodDecisions],
    reflective_method_sigs: frozenset[str],
    visited: set[tuple[str, str, str]],
) -> bool:
    """Per-instruction stateful check used by :func:`is_stateless`.
    Returns ``True`` iff the line constitutes a side effect the
    analyzer cares about. Conservative — any line we can't parse
    cleanly is ``True`` (assume stateful)."""
    op_match = _RE_OPCODE_DEST.match(line)
    if not op_match:
        # Unparseable line — could be a comment / directive that
        # slipped through. Defensively treat as stateless (don't
        # block on parser quirks).
        return False
    opcode = op_match.group("op")

    # Field / array writes.
    if opcode.startswith("iput") or opcode.startswith("sput") or opcode.startswith("aput"):
        return True
    # Synchronization (monitor-enter / monitor-exit). Even though
    # monitor-enter is in :data:`_NO_DEF_OPCODES`, locks observe
    # shared state and are the wrong thing to descend past.
    if opcode.startswith("monitor-"):
        return True
    # Exception throw.
    if opcode == "throw":
        return True
    # Invoke — recurse on the callee.
    if opcode.startswith("invoke-"):
        inv = _RE_INVOKE.match(line)
        if not inv:
            # Malformed invoke line — defensive False on the
            # statelessness side (assume stateful).
            return True
        callee_owner = inv.group("owner")
        callee_name = inv.group("method")
        callee_sig = (
            f"{callee_owner}->{callee_name}"
            f"({inv.group('params')}){inv.group('ret')}"
        )
        # Constructor invocation (`<init>`) is part of object
        # construction; if the new instance escapes to a field
        # we'd already have caught the `iput-object`. Treat
        # constructor calls themselves as stateless for the
        # analyzer (they don't write the caller's state).
        if callee_name in ("<init>", "<clinit>"):
            return False
        # Recurse — `is_stateless` handles deny-list short-circuit
        # + body lookup + cycle termination internally.
        if is_stateless(
            callee_sig,
            classes_by_smali=classes_by_smali,
            decisions_by_method_sig=decisions_by_method_sig,
            reflective_method_sigs=reflective_method_sigs,
            visited=visited,
        ):
            return False
        return True
    return False


def _parse_signature_for_cycle_key(method_sig: str) -> tuple[str, str, str]:
    """Split a smali method signature into the
    ``(class_smali, method_name, descriptor)`` triple used as the
    cycle / visited key.

    ``Lcom/example/Foo;->bar(I)V`` → ``("Lcom/example/Foo;", "bar", "(I)V")``.

    Falls back to ``("", method_sig, "")`` on a malformed input so
    the visited set still has *some* key (degraded but doesn't crash).
    """
    arrow = method_sig.find("->")
    if arrow < 0:
        return ("", method_sig, "")
    class_part = method_sig[:arrow]
    rest = method_sig[arrow + 2:]
    paren = rest.find("(")
    if paren < 0:
        return (class_part, rest, "")
    return (class_part, rest[:paren], rest[paren:])


def _tag_descent_depth(
    origin: PredicateOrigin, budget: _DescentBudget
) -> PredicateOrigin:
    """Phase 11 sub-step 11.6 — tag a :class:`MethodCallOrigin` /
    :class:`FieldReadOrigin` cap-stop terminal with the current
    descent-stack depth, so the frontend's depth pill can render
    "via N helper method(s)" / "via N field write(s)".

    Q1 (A) literal-spec rule: only ``MethodCallOrigin`` and
    ``FieldReadOrigin`` carry the ``descent_depth`` field — Const /
    Param / Composite stay terminal in v2 with no depth tag (the
    success path that resolves all the way to a v1-terminal variant
    drops the depth signal). Tag is a no-op when:

    * ``current_descent_depth == 0`` (top-level entry; no descent
      happened) — keeps the v1 wire shape verbatim for callers that
      exercise the v1 path.
    * ``origin`` is already tagged with the same depth (idempotency —
      this can happen on the success-path unwind where each frame
      sees the inner result already carries its terminal depth).
    * ``origin`` is not a ``MethodCallOrigin`` / ``FieldReadOrigin``
      (Const / Param / Composite — defensive; the dispatcher only
      calls us with the two tagged variants today).
    """
    depth = budget.current_descent_depth
    if depth <= 0:
        return origin
    if isinstance(origin, MethodCallOrigin):
        if origin.descent_depth == depth:
            return origin
        return dataclasses.replace(origin, descent_depth=depth)
    if isinstance(origin, FieldReadOrigin):
        if origin.descent_depth == depth:
            return origin
        return dataclasses.replace(origin, descent_depth=depth)
    return origin


def _maybe_descend(
    origin: PredicateOrigin,
    *,
    budget: _DescentBudget,
    max_walk: int,
    classes_by_smali: dict[str, ClassDecl],
    decisions_by_method_sig: dict[str, MethodDecisions],
    reflective_method_sigs: frozenset[str],
    current_class_smali: str = "",
) -> PredicateOrigin:
    """Dispatch to the appropriate descent path based on ``origin``'s
    variant.

    * :class:`MethodCallOrigin` → 11.4's :func:`_descend_into_callee`
      (bounded inter-procedural slicer descent through stateless
      helper methods).
    * :class:`FieldReadOrigin` → 11.5's :func:`_walk_field_write_sites`
      (same-class field-write-site walking with constructor-priority).
    * Any other variant (Const / Param / Composite) is already a
      terminal — descent is a no-op.

    The descended :class:`PredicateOrigin` *replaces* ``origin``
    (operator sees the new terminal). When any precondition fails,
    ``origin`` is returned unchanged — the v1 terminal remains the
    operator's view.

    Recursive: the descent's inner re-slice may itself produce a
    :class:`MethodCallOrigin` or :class:`FieldReadOrigin`, in which
    case the inner descent fires (subject to the shared budget).
    The recursion bottoms out when the budget hits zero, when the
    inner result is a terminal variant, or when any precondition
    fails (deny-list / cycle / external / cross-class / no write
    site).

    ``current_class_smali`` is the class smali descriptor of the
    *reading* method — used by the field-write-site descent to gate
    same-class-only candidates per the 11.5 planning checkpoint
    Q2 (A). Defaults to ``""`` so callers from tests / ad-hoc paths
    that don't care about field-write descent can omit it; the
    11.5 field-write path skips when the gate is empty.
    """
    if isinstance(origin, MethodCallOrigin):
        return _maybe_descend_method_call(
            origin,
            budget=budget,
            max_walk=max_walk,
            classes_by_smali=classes_by_smali,
            decisions_by_method_sig=decisions_by_method_sig,
            reflective_method_sigs=reflective_method_sigs,
            current_class_smali=current_class_smali,
        )
    if isinstance(origin, FieldReadOrigin):
        return _maybe_descend_field_read(
            origin,
            budget=budget,
            max_walk=max_walk,
            classes_by_smali=classes_by_smali,
            decisions_by_method_sig=decisions_by_method_sig,
            reflective_method_sigs=reflective_method_sigs,
            current_class_smali=current_class_smali,
        )
    return origin


def _maybe_descend_method_call(
    origin: MethodCallOrigin,
    *,
    budget: _DescentBudget,
    max_walk: int,
    classes_by_smali: dict[str, ClassDecl],
    decisions_by_method_sig: dict[str, MethodDecisions],
    reflective_method_sigs: frozenset[str],
    current_class_smali: str,
) -> PredicateOrigin:
    """Phase 11 sub-step 11.4 — bounded inter-procedural descent
    through a stateless helper-method callee. See :func:`_maybe_descend`
    for the dispatch contract.

    Phase 11 sub-step 11.6 — when an early-exit path returns the
    received ``origin`` unchanged AND the caller is itself inside an
    active descent recursion (``budget.current_descent_depth > 0``),
    the returned origin is tagged with that depth so the frontend's
    depth pill can render "via N helper method(s)". Top-level entry
    (depth 0) leaves the field at its default ``0`` — operator sees
    a v1-style terminal with no pill.
    """
    if budget.remaining_depth <= 0:
        return _tag_descent_depth(origin, budget)
    callee_sig = origin.method.smali_signature
    cycle_key = _parse_signature_for_cycle_key(callee_sig)
    if cycle_key in budget.visited:
        # Already descended into this callee in the current top-level
        # slice — don't redo work. Surface the v1 terminal so the
        # operator at least sees the call (vs. a misleading "no
        # origin").
        return _tag_descent_depth(origin, budget)
    # External callee → not in the in-app classes index → can't
    # descend (no Smali body for stdlib / framework classes).
    callee_class_desc = cycle_key[0]
    if callee_class_desc not in classes_by_smali:
        return _tag_descent_depth(origin, budget)
    # Statelessness gate. Use a fresh visited set for the
    # is_stateless walk so it doesn't pollute the descent's own
    # visited set (the two passes have different semantics: descent
    # tracks "have I already descended into this", is_stateless
    # tracks "am I already analyzing this on the current
    # statelessness recursion stack").
    if not is_stateless(
        callee_sig,
        classes_by_smali=classes_by_smali,
        decisions_by_method_sig=decisions_by_method_sig,
        reflective_method_sigs=reflective_method_sigs,
    ):
        return _tag_descent_depth(origin, budget)
    descended = _descend_into_callee(
        origin,
        budget=budget,
        max_walk=max_walk,
        classes_by_smali=classes_by_smali,
        decisions_by_method_sig=decisions_by_method_sig,
        reflective_method_sigs=reflective_method_sigs,
        current_class_smali=current_class_smali,
    )
    if descended is None:
        return _tag_descent_depth(origin, budget)
    return descended


def _descend_into_callee(
    origin: MethodCallOrigin,
    *,
    budget: _DescentBudget,
    max_walk: int,
    classes_by_smali: dict[str, ClassDecl],
    decisions_by_method_sig: dict[str, MethodDecisions],
    reflective_method_sigs: frozenset[str],
    current_class_smali: str,
) -> Optional[PredicateOrigin]:
    """Re-run :func:`_slice_register` on the callee's ``return-*``
    instruction's source register. The callee's body is sourced from
    ``decisions_by_method_sig`` (via the canonical smali signature
    on ``origin.method``).

    Returns the descended :class:`PredicateOrigin`, or ``None`` if
    the callee's body isn't available, has no return-with-register,
    or the inner slice itself fails. Caller decides whether to fall
    back to the original on ``None``.

    ``current_class_smali`` here is the *outer caller's* class — but
    the inner ``_maybe_descend`` call below uses the *callee's* class
    so that any 11.5 field-write descent fired from inside the
    descended slice is gated against the callee's class (the inner
    slice runs over the callee's body, so its same-class check
    must be against the callee).
    """
    callee_sig = origin.method.smali_signature
    cycle_key = _parse_signature_for_cycle_key(callee_sig)
    md = decisions_by_method_sig.get(callee_sig)
    if md is None:
        # Linear method (no decisions parsed) — no instruction stream
        # available even though the callee is in our index. Could
        # extend the parser to emit MethodDecisions for branchless
        # methods too, but that's a 11.7+ ergonomics fix; for now
        # the descent stops here.
        return None
    # Find the `return-*` (with a source register) — typically the
    # last real instruction in the body. Walk from the end backwards
    # for cheap last-return semantics; multiple returns (rare) take
    # the textually-last one, matching the v1 slicer's "most-recent
    # definition wins" framing.
    return_index = -1
    return_reg: Optional[str] = None
    for k in range(len(md.instructions) - 1, -1, -1):
        m = _RE_RETURN_WITH_REG.match(md.instructions[k])
        if m:
            return_index = k
            return_reg = m.group("src")
            break
    if return_index < 0 or return_reg is None:
        # `return-void` (no return register) or no return at all —
        # nothing to slice further; preserve v1 terminal.
        return None

    # Mark this callee visited + decrement depth BEFORE recursing so
    # the inner re-slice's own descent attempts see the updated
    # budget (closed economy — same instance shared across the
    # entire slice operation). 11.6 — also bump the
    # ``current_descent_depth`` counter so any cap-stop terminal
    # downstream can self-report its depth-from-top.
    budget.visited.add(cycle_key)
    budget.remaining_depth -= 1
    budget.current_descent_depth += 1
    try:
        inner = _slice_register(
            return_reg,
            return_index,
            md.instructions,
            max_walk=max_walk,
        )
        if inner is None:
            return None
        # Recursive descent — the inner slice may itself produce a
        # MethodCallOrigin OR FieldReadOrigin. _maybe_descend handles
        # both via dispatch + budget check. The inner descent's
        # same-class gate is against the *callee's* class (we're
        # slicing the callee's body, so any field read inside is
        # relative to the callee's class).
        callee_class_desc = cycle_key[0]
        return _maybe_descend(
            inner,
            budget=budget,
            max_walk=max_walk,
            classes_by_smali=classes_by_smali,
            decisions_by_method_sig=decisions_by_method_sig,
            reflective_method_sigs=reflective_method_sigs,
            current_class_smali=callee_class_desc,
        )
    finally:
        # Restore depth on the way out so a *sibling* decision in the
        # caller method can still descend into a different helper.
        # The visited entry stays — we never want to descend into
        # the same callee twice in one top-level slice operation.
        budget.remaining_depth += 1
        budget.current_descent_depth -= 1


# ===========================================================================
# Phase 11 sub-step 11.5 — same-class field-write-site walking
# ===========================================================================
#
# When the v1 slicer terminates at a :class:`FieldReadOrigin` AND the
# field is on the same class as the reading method AND the budget
# permits, walk the class's ``iput-*`` / ``sput-*`` instructions for
# the matching field, pick the most-recent write per the
# constructor-priority rule (Q1 (A) of the 11.5 planning checkpoint),
# re-slice the source register at that write site.
#
# The descended PredicateOrigin replaces the original FieldReadOrigin
# (operator sees the new terminal, depth pill surfaces the "via 1
# field write" signal in 11.6's UI).


def _maybe_descend_field_read(
    origin: FieldReadOrigin,
    *,
    budget: _DescentBudget,
    max_walk: int,
    classes_by_smali: dict[str, ClassDecl],
    decisions_by_method_sig: dict[str, MethodDecisions],
    reflective_method_sigs: frozenset[str],
    current_class_smali: str,
) -> PredicateOrigin:
    """Phase 11 sub-step 11.5 — same-class field-write-site walking.
    See :func:`_maybe_descend` for the dispatch contract.

    Gates (any failure → return ``origin`` unchanged, v1 terminal
    preserved):

    * Budget exhaustion (``budget.remaining_depth <= 0``).
    * Empty ``current_class_smali`` (test / ad-hoc caller didn't
      provide it; can't gate same-class).
    * Cross-class field — Q2 (A) strict same-class match policy.
    * Cycle — field already in budget.visited (prevents redundant
      re-walking of the same field across multiple decisions).
    * No eligible write sites found (declared field never written
      anywhere in the class).
    * Inner re-slice itself fails.
    """
    if budget.remaining_depth <= 0:
        return _tag_descent_depth(origin, budget)
    if not current_class_smali:
        return _tag_descent_depth(origin, budget)
    field_class_smali = _java_class_to_smali(origin.field.class_name)
    # Strict same-class match per planning-checkpoint Q2 (A) — no
    # superclass walking in v1; v3 / Phase 12 may extend with
    # ClassDecl.super_desc traversal.
    if field_class_smali != current_class_smali:
        return _tag_descent_depth(origin, budget)
    # Cycle key for fields uses the full smali signature (class +
    # name + type). Lives in the same ``budget.visited`` set as
    # method-descent cycle keys — the third tuple slot ("descriptor"
    # for methods) holds the type descriptor for fields, so the two
    # never collide (methods have parens-wrapped descriptors;
    # fields have bare type tokens).
    field_cycle_key = (
        field_class_smali,
        origin.field.field_name,
        origin.field.type_descriptor,
    )
    if field_cycle_key in budget.visited:
        return _tag_descent_depth(origin, budget)
    descended = _walk_field_write_sites(
        origin,
        budget=budget,
        max_walk=max_walk,
        classes_by_smali=classes_by_smali,
        decisions_by_method_sig=decisions_by_method_sig,
        reflective_method_sigs=reflective_method_sigs,
        current_class_smali=current_class_smali,
        field_cycle_key=field_cycle_key,
    )
    if descended is None:
        return _tag_descent_depth(origin, budget)
    return descended


def _walk_field_write_sites(
    origin: FieldReadOrigin,
    *,
    budget: _DescentBudget,
    max_walk: int,
    classes_by_smali: dict[str, ClassDecl],
    decisions_by_method_sig: dict[str, MethodDecisions],
    reflective_method_sigs: frozenset[str],
    current_class_smali: str,
    field_cycle_key: tuple[str, str, str],
) -> Optional[PredicateOrigin]:
    """Find the most-recent write to ``origin.field`` in ``current_class_smali``
    per the constructor-priority rule, then re-slice the written
    register at that write site.

    **Constructor-priority rule** (Q1 (A) of the 11.5 planning
    checkpoint):

    1. Look at every ``iput-*`` / ``sput-*`` write to the matching
       field across every method on the class.
    2. If at least one write lives in ``<init>`` or ``<clinit>``,
       prefer the textually-last such write within those constructors
       (constructor source order).
    3. Else (no constructor writes), prefer the textually-last write
       across all non-constructor methods.

    Returns ``None`` when no eligible write site exists OR when the
    inner re-slice fails. Caller falls back to the original
    :class:`FieldReadOrigin`.

    Note on Q3 (A) receiver-agnostic matching: ``iput-*`` write sites
    are matched by ``(field_class_smali, field_name)`` only — the
    receiver register (``vObj`` in ``iput vSrc, vObj, ...``) is
    ignored. The textbook ``this.mField`` field-cached idiom is
    correct under this rule (only-write-site scenario), but more
    aggressive aliasing cases may pick a write to a different
    instance's field on the same class.

    Note on Q4 (A) lazy-walk: this walk runs on every field-read
    descent. For typical app classes (~50 methods × ~30 instructions)
    the cost is trivial; megaclasses may benefit from eager indexing
    in 11.7 if telemetry shows demand. The closed-economy
    :class:`_DescentBudget` caps total descents at the v1 default
    of 2 per top-level slice, so the worst-case is bounded.
    """
    cls = classes_by_smali.get(current_class_smali)
    if cls is None:
        # Defensive — should never happen since we already gated
        # on field_class_smali == current_class_smali above and the
        # outer slice's class must be in the index by definition.
        return None
    field_target_smali = origin.field.smali_signature

    # Collect every (method_sig, instruction_index, source_register,
    # is_constructor) tuple for matching writes across the class.
    constructor_sites: list[tuple[str, int, str]] = []
    other_sites: list[tuple[str, int, str]] = []
    write_regex = _RE_SPUT if origin.is_static else _RE_IPUT
    for method_decl in cls.methods:
        method_sig = method_decl.signature
        md = decisions_by_method_sig.get(method_sig)
        if md is None:
            # Method body unavailable (e.g. abstract / native /
            # parser hadn't been called with include_branchless).
            # Skip — can't walk what isn't there.
            continue
        for idx, line in enumerate(md.instructions):
            m = write_regex.match(line)
            if not m:
                continue
            if m.group("field") != field_target_smali:
                continue
            site = (method_sig, idx, m.group("src"))
            if method_decl.is_constructor:
                constructor_sites.append(site)
            else:
                other_sites.append(site)

    # Constructor-priority + source order. Within a constructor we
    # take the textually-last (latest) write — matches the operator
    # intuition that the *last* assignment in `<init>` is what the
    # field's value will be after construction completes. Across
    # multiple constructors (rare; usually just `<init>` and `<clinit>`
    # both on the same class), the same "last" rule applies — and
    # since both are constructor-priority candidates, the choice
    # between them is by source order in the methods tuple (which
    # mirrors smali file order).
    if constructor_sites:
        chosen = constructor_sites[-1]
    elif other_sites:
        chosen = other_sites[-1]
    else:
        # No write site found — descent gracefully fails; caller
        # preserves v1 FieldReadOrigin terminal.
        return None

    write_method_sig, write_index, write_src_reg = chosen
    write_md = decisions_by_method_sig[write_method_sig]
    write_class_smali = _class_from_method_sig(write_method_sig)

    # Mark the field visited + decrement depth BEFORE recursing so
    # the inner re-slice's own descent attempts see the updated
    # budget (closed economy). 11.6 — also bump
    # ``current_descent_depth`` (mirrors the method-descent path
    # above) so any cap-stop terminal downstream of this field-walk
    # can self-report its depth-from-top.
    budget.visited.add(field_cycle_key)
    budget.remaining_depth -= 1
    budget.current_descent_depth += 1
    try:
        inner = _slice_register(
            write_src_reg,
            write_index,
            write_md.instructions,
            max_walk=max_walk,
        )
        if inner is None:
            return None
        # The inner slice runs over the WRITER method's body, so
        # any further descent's same-class check is against the
        # writer's class (which == current_class_smali by our
        # gating, but spelt out explicitly for clarity).
        return _maybe_descend(
            inner,
            budget=budget,
            max_walk=max_walk,
            classes_by_smali=classes_by_smali,
            decisions_by_method_sig=decisions_by_method_sig,
            reflective_method_sigs=reflective_method_sigs,
            current_class_smali=write_class_smali,
        )
    finally:
        budget.remaining_depth += 1
        budget.current_descent_depth -= 1


def _class_from_method_sig(method_sig: str) -> str:
    """Extract the class smali descriptor from a full method
    signature: ``Lcom/Foo;->bar(I)V`` → ``Lcom/Foo;``.

    Returns ``""`` on a malformed signature so callers can defensively
    skip the same-class gate without crashing.
    """
    arrow = method_sig.find("->")
    if arrow < 0:
        return ""
    return method_sig[:arrow]


def _java_class_to_smali(java_name: str) -> str:
    """Local mirror of :func:`androscan.analysis.trace_types._java_class_to_smali`
    so the slicer doesn't have to import a private helper from
    trace_types. ``com.example.Foo`` → ``Lcom/example/Foo;``."""
    return f"L{java_name.replace('.', '/')};"
