"""Intra-procedural backward slicer for decision predicates — Phase 10
sub-step 10.2.

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

Out of scope for v1
-------------------

* Aliasing / field-flow / escape analysis (DEC-024 limitation).
* Inter-procedural slicing — the slicer never crosses method
  boundaries; ``MethodCallOrigin`` carries the called method's
  :class:`MethodRef` but doesn't recurse into its body.
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
) -> MethodDecisions:
    """Return a :class:`MethodDecisions` with every decision's
    ``predicate_origin`` populated (or ``None`` on slice failure).

    The input is not mutated — frozen dataclass replaced via
    :func:`dataclasses.replace`. Callers can chain
    ``slice_predicate_origins(method_decisions)`` directly into 10.3's
    classifier without bookkeeping.
    """
    instructions = method_decisions.instructions
    enriched: list[DecisionPoint] = []
    for dp in method_decisions.decision_points:
        origin = _slice_one(dp, instructions, max_walk=max_walk)
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
