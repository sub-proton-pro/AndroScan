"""Pure-function extractor for conditional branches in Smali method
bodies — Phase 10 sub-step 10.1.

This is a third pass over the same apktool Smali tree that
:mod:`androscan.analysis.smali_parser` already walks twice (classes
then invokes). It deliberately does **not** modify or extend the call
graph — branches are not nodes in the call graph, and the call graph's
build flow is untouched.

For each method body we emit:

* one :class:`androscan.analysis.trace_types.DecisionPoint` per
  conditional opcode (``if-eq`` … ``if-gez``, ``packed-switch``,
  ``sparse-switch``);
* a per-method ``label_index`` mapping every Smali label
  (``:cond_0``, ``:pswitch_0``, …) to the 0-based instruction index of
  the *next* instruction following that label.

The label index lets 10.3's branch-outcome classifier walk the basic
block at each branch target without re-parsing the smali file. Switch
data blocks (``:pswitch_data_0`` + ``.packed-switch`` … ``.end packed-switch``)
are parsed inline so each switch decision's ``branches`` are populated
with one :class:`Branch` per case label plus a ``"default"`` branch
modelled as fall-through (``target_label=None``).

What this module *doesn't* do (per DEC-024, deferred):

* Backward slicing for predicate origin → 10.2.
* Heuristic deny/allow/neutral classification → 10.3.
* Bypass plan synthesis → 10.4.
* LLM interpretation, persistence, REST surface → 10.5 / 10.6.

Smali grammar tolerated
-----------------------

Strictly lexical — no full grammar. Every line falls into one of:

* ``.method`` … ``.end method`` brackets (delimits a method body).
* ``.line N`` (debug source-line anchor; attached to the next branch).
* ``:label`` (label definition; pinned to the next instruction's index).
* ``:pswitch_data_N`` + ``.packed-switch <start>`` … ``.end packed-switch``
  / ``:sswitch_data_N`` + ``.sparse-switch`` … ``.end sparse-switch``
  (switch data blocks, parsed inline).
* ``if-*`` and ``*-switch`` opcodes (the things we record).
* Any other ``opcode args…`` (counts as a real instruction for
  ``instruction_index`` purposes).

Anything else (``.registers``, ``.locals``, ``.parameter``,
``.prologue``, ``.catch``, ``# comments``, blank lines) is structurally
ignored — they do not advance the instruction index, so labels resolve
to the *real* next instruction, which is what 10.3's basic-block walker
expects.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, Optional

from androscan.analysis.smali_parser import ClassDecl
from androscan.analysis.trace_types import (
    Branch,
    DecisionKind,
    DecisionPoint,
    MethodRef,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Smali tokens we care about for decision extraction.
#
# Mirrors smali_parser.py's regex style — anchored to start-of-line
# whitespace so we can run them on the raw line stream without
# tokenisation. Each opcode-specific regex captures the predicate
# register(s) and the target label.

# .class Lcom/example/Foo;   — only used to recover the class descriptor
# inside the second-pass walk; matches smali_parser._RE_CLASS shape.
_RE_CLASS = re.compile(r"^\s*\.class\b[^L\n]*?\s+(?P<desc>L[^;\s]+;)")
# .method [flags...] name(params)return  — match shape mirrors smali_parser.
_RE_METHOD = re.compile(
    r"^\s*\.method\b(?P<flags>[^(]*?)\s+"
    r"(?P<sig><(?:cl)?init>|[A-Za-z_$][A-Za-z_$0-9]*)\((?P<params>[^)]*)\)(?P<ret>\S+)"
)
_RE_END_METHOD = re.compile(r"^\s*\.end\s+method\b")
_RE_LINE = re.compile(r"^\s*\.line\s+(?P<num>\d+)")

# :label_name (single-line label definition).
_RE_LABEL = re.compile(r"^\s*:(?P<name>[A-Za-z_$][A-Za-z_$0-9]*)\s*$")

# Two-register conditionals: ``if-eq vA, vB, :label``.
_RE_IF_TWO_REG = re.compile(
    r"^\s*if-(?P<op>eq|ne|lt|le|gt|ge)\s+"
    r"(?P<reg_a>[vp]\d+)\s*,\s*"
    r"(?P<reg_b>[vp]\d+)\s*,\s*"
    r":(?P<target>[A-Za-z_$][A-Za-z_$0-9]*)"
)
# Single-register zero-conditionals: ``if-eqz vA, :label``.
_RE_IF_ZERO = re.compile(
    r"^\s*if-(?P<op>eqz|nez|ltz|lez|gtz|gez)\s+"
    r"(?P<reg>[vp]\d+)\s*,\s*"
    r":(?P<target>[A-Za-z_$][A-Za-z_$0-9]*)"
)
# Switch dispatch: ``packed-switch v0, :pswitch_data_0`` or sparse-.
_RE_SWITCH_DISPATCH = re.compile(
    r"^\s*(?P<kind>packed|sparse)-switch\s+"
    r"(?P<reg>[vp]\d+)\s*,\s*"
    r":(?P<data>[A-Za-z_$][A-Za-z_$0-9]*)"
)

# Switch data block headers/footers.
_RE_PACKED_SWITCH_HEADER = re.compile(
    r"^\s*\.packed-switch\b\s*(?P<start>-?(?:0[xX][0-9a-fA-F]+|\d+))"
)
_RE_SPARSE_SWITCH_HEADER = re.compile(r"^\s*\.sparse-switch\b")
_RE_PACKED_SWITCH_END = re.compile(r"^\s*\.end\s+packed-switch\b")
_RE_SPARSE_SWITCH_END = re.compile(r"^\s*\.end\s+sparse-switch\b")
# Inside a packed-switch block: just ``:pswitch_0`` per line.
# Inside a sparse-switch block: ``0x1 -> :sswitch_0``.
_RE_SPARSE_SWITCH_ENTRY = re.compile(
    r"^\s*(?P<key>-?(?:0[xX][0-9a-fA-F]+|\d+))\s*->\s*:(?P<label>[A-Za-z_$][A-Za-z_$0-9]*)"
)

# Any line that "looks like" an opcode (used to decide whether to
# advance instruction_index). Smali opcodes are lowercase-with-hyphens.
# We're conservative: a line is a real instruction if it starts with a
# lowercase letter and is not a directive (``.``) / label (``:``) /
# comment (``#``). Switch data block lines (``:pswitch_0`` inside the
# block, ``0x1 -> :sswitch_0`` inside the block) are excluded by the
# explicit-block-skip in the walker, not by this regex.
_RE_REAL_INSTRUCTION = re.compile(r"^\s*[a-z][a-z0-9-]*\b")


# ---------------------------------------------------------------------------
# Output records


@dataclass(frozen=True)
class MethodDecisions:
    """All decision points and label index for one method body.

    The ``label_index`` is a tuple of ``(label_name, instruction_index)``
    pairs (rather than a dict) so the dataclass remains frozen-hashable
    — 10.3 / 10.5 build their own dicts when they need O(1) lookup.
    Order is the source order labels appeared in the method body.
    """
    method_signature: str                  # smali key, e.g. "Lcom/example/Foo;->bar(I)V"
    src_file: str                          # apktool-relative path
    decision_points: tuple[DecisionPoint, ...]
    label_index: tuple[tuple[str, int], ...]


@dataclass
class DecisionsParseSummary:
    """Aggregate counters mirroring :class:`androscan.analysis.smali_parser.ParseSummary`.

    Useful for the 10.5 trace skill to surface "static layer found N
    gates across M methods" deterministically; not persisted in any
    SQLite store.
    """
    smali_files: int = 0
    methods_with_decisions: int = 0
    decisions: int = 0
    switches: int = 0
    skipped_files: int = 0
    parse_errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Public API


def parse_decisions(
    roots: Iterable[Path],
    classes: list[ClassDecl],
) -> tuple[list[MethodDecisions], DecisionsParseSummary]:
    """Walk apktool's smali tree and emit decision points per method.

    ``classes`` is consulted only to skip files that pass-1 found no
    class in (keeps file counts consistent with the call-graph parser
    passes). Decision extraction itself does not need the class
    hierarchy — branches are intra-method.

    Result order: methods appear in source-file walk order; decisions
    within a method are in source order. Empty methods (no branches)
    yield no :class:`MethodDecisions` entry — callers that want a full
    method enumeration should join against
    :class:`androscan.analysis.smali_parser.ClassDecl.methods`.
    """
    out: list[MethodDecisions] = []
    summary = DecisionsParseSummary()
    classes_with_methods = {c.file for c in classes if c.methods}
    apktool_root = _common_parent(roots)

    for _root, path in _iter_smali_files(roots):
        summary.smali_files += 1
        rel = _relative_or_str(path, apktool_root)
        if rel not in classes_with_methods:
            # File had no class or no methods (interfaces with no
            # defaults, synthetic accessors stripped, etc.) — same
            # cheap skip as smali_parser.parse_invokes uses.
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            summary.skipped_files += 1
            summary.parse_errors.append(f"{rel}: read error: {e}")
            continue

        cls_match = _RE_CLASS.search(text)
        if not cls_match:
            continue
        class_desc = cls_match.group("desc")

        for md in _walk_file_methods(text, class_desc, rel, summary):
            out.append(md)
            summary.methods_with_decisions += 1
            for dp in md.decision_points:
                summary.decisions += 1
                if dp.is_switch:
                    summary.switches += 1
    return out, summary


# ---------------------------------------------------------------------------
# Per-file walk


def _walk_file_methods(
    text: str,
    class_desc: str,
    rel_path: str,
    summary: DecisionsParseSummary,
) -> Iterator[MethodDecisions]:
    """Iterate ``(method_signature, MethodDecisions)`` for every method
    body in *text* that contains at least one decision point.

    A method body that contains no ``if-*`` / ``*-switch`` opcodes does
    not yield — keeps the result list tight for downstream consumers.
    """
    lines = text.splitlines()
    cur_method_sig: Optional[str] = None
    method_state: Optional[_MethodState] = None
    i = 0
    n = len(lines)

    while i < n:
        raw = lines[i]
        if cur_method_sig is None:
            mm = _RE_METHOD.match(raw)
            if mm:
                cur_method_sig = (
                    f"{class_desc}->{mm.group('sig')}"
                    f"({mm.group('params')}){mm.group('ret')}"
                )
                method_state = _MethodState(
                    method_signature=cur_method_sig,
                    method_ref=MethodRef.from_smali_signature(cur_method_sig),
                    src_file=rel_path,
                )
            i += 1
            continue

        if _RE_END_METHOD.match(raw):
            assert method_state is not None
            md = method_state.finish()
            if md is not None:
                yield md
            cur_method_sig = None
            method_state = None
            i += 1
            continue

        # Inside a method body — delegate to method state.
        assert method_state is not None
        try:
            consumed = method_state.consume(lines, i)
        except _DecisionParseError as e:
            summary.parse_errors.append(f"{rel_path}:{i + 1}: {e}")
            consumed = 1
        i += consumed


# ---------------------------------------------------------------------------
# Per-method walker (mutable; finalises into a frozen MethodDecisions)


class _DecisionParseError(Exception):
    """Raised internally when a switch data block is malformed past the
    point of structural recovery. Caught by :func:`_walk_file_methods`,
    surfaced via ``DecisionsParseSummary.parse_errors``, and the rest of
    the file keeps parsing — same fail-soft posture as smali_parser."""


class _MethodState:
    """Mutable accumulator for one in-progress method body.

    Tracks the rolling instruction index, the most recent ``.line N``
    annotation, label definitions, and any switch decisions whose data
    blocks haven't been parsed yet. ``finish()`` returns either a
    frozen :class:`MethodDecisions` (when at least one branch was
    found) or ``None`` (linear method with no decisions to record).
    """

    def __init__(self, method_signature: str, method_ref: MethodRef, src_file: str) -> None:
        self.method_signature = method_signature
        self.method_ref = method_ref
        self.src_file = src_file
        # Output accumulators.
        self._decisions: list[DecisionPoint] = []
        self._label_index: list[tuple[str, int]] = []
        # Walk state.
        self._instruction_index = 0
        self._last_source_line: Optional[int] = None
        # Switches whose data block we haven't parsed yet, keyed by the
        # data-label name (``"pswitch_data_0"``). Value is the index in
        # ``self._decisions`` we'll back-fill once the block parses.
        self._pending_switches: dict[str, int] = {}

    def consume(self, lines: list[str], i: int) -> int:
        """Process the line at ``lines[i]`` and return how many lines
        were consumed (1 for normal lines; >1 when a switch data block
        is parsed inline)."""
        raw = lines[i]

        # Pseudo-directive: source line annotation. Doesn't advance
        # instruction_index (debug-only); attached to the next branch
        # we record so 10.3 / 10.7 can show "line 42 in Foo.java".
        ln = _RE_LINE.match(raw)
        if ln:
            try:
                self._last_source_line = int(ln.group("num"))
            except ValueError:  # pragma: no cover — regex guarantees digits
                self._last_source_line = None
            return 1

        # Switch data block start (``.packed-switch <start>`` /
        # ``.sparse-switch``). The preceding line was the
        # ``:pswitch_data_0`` label, which has already been recorded in
        # the label_index — but the label points to the *next*
        # instruction, which is the .packed-switch directive itself.
        # Treat the data block as a single "instruction" (one
        # instruction_index slot) for indexing purposes; this matches
        # the dispatch site's understanding that the data block is one
        # opaque table.
        ph = _RE_PACKED_SWITCH_HEADER.match(raw)
        if ph:
            return self._consume_packed_switch_block(lines, i, int(ph.group("start"), 0))
        sh = _RE_SPARSE_SWITCH_HEADER.match(raw)
        if sh:
            return self._consume_sparse_switch_block(lines, i)

        # Label definition: pin to the next instruction's index.
        lbl = _RE_LABEL.match(raw)
        if lbl:
            self._label_index.append((lbl.group("name"), self._instruction_index))
            return 1

        # Conditional opcode? Two-reg first (longer form so it can't
        # accidentally match the *z form's prefix on the wrong line).
        m2 = _RE_IF_TWO_REG.match(raw)
        if m2:
            self._record_if(
                kind=_KIND_BY_TWO_REG_OP[m2.group("op")],
                regs=(m2.group("reg_a"), m2.group("reg_b")),
                target=m2.group("target"),
            )
            return 1
        m1 = _RE_IF_ZERO.match(raw)
        if m1:
            self._record_if(
                kind=_KIND_BY_ZERO_OP[m1.group("op")],
                regs=(m1.group("reg"),),
                target=m1.group("target"),
            )
            return 1
        sw = _RE_SWITCH_DISPATCH.match(raw)
        if sw:
            self._record_switch_dispatch(
                kind=DecisionKind.PACKED_SWITCH if sw.group("kind") == "packed" else DecisionKind.SPARSE_SWITCH,
                reg=sw.group("reg"),
                data_label=sw.group("data"),
            )
            return 1

        # Any other line that "looks like" an opcode counts as a real
        # instruction. Doesn't matter what it is for indexing — only
        # that we count it consistently. Directives starting with ``.``
        # are intentionally rejected here so ``.registers`` / ``.locals``
        # / ``.prologue`` / ``.catch`` don't bump the index.
        if not raw.lstrip().startswith(".") and _RE_REAL_INSTRUCTION.match(raw):
            self._instruction_index += 1
        return 1

    # ---- Recording helpers ------------------------------------------------

    def _record_if(
        self,
        *,
        kind: DecisionKind,
        regs: tuple[str, ...],
        target: str,
    ) -> None:
        """Emit a 2-branch DecisionPoint for an if-* opcode. The "true"
        branch jumps to ``target``, the "false" branch falls through."""
        dp = DecisionPoint(
            method=self.method_ref,
            instruction_index=self._instruction_index,
            source_line=self._last_source_line,
            kind=kind,
            predicate_registers=regs,
            branches=(
                Branch(label="true", target_label=target),
                Branch(label="false", target_label=None),
            ),
        )
        self._decisions.append(dp)
        self._instruction_index += 1

    def _record_switch_dispatch(
        self,
        *,
        kind: DecisionKind,
        reg: str,
        data_label: str,
    ) -> None:
        """Emit a switch DecisionPoint with empty branches; remember
        the data-label so the data block parser can back-fill."""
        dp = DecisionPoint(
            method=self.method_ref,
            instruction_index=self._instruction_index,
            source_line=self._last_source_line,
            kind=kind,
            predicate_registers=(reg,),
            branches=(),  # back-filled when we hit the data block
        )
        self._decisions.append(dp)
        # Remember the index so we can replace it when the data block
        # parses. Frozen dataclasses → we'll rebuild the entry when we
        # know the branches.
        self._pending_switches[data_label] = len(self._decisions) - 1
        self._instruction_index += 1

    def _consume_packed_switch_block(
        self,
        lines: list[str],
        i: int,
        start_key: int,
    ) -> int:
        """Parse a ``.packed-switch <start> ... .end packed-switch`` block.

        Returns the number of lines consumed (header + body + footer).
        Back-fills the matching pending switch's branches if we can
        identify which switch dispatch pointed here. Even if no matching
        dispatch is found (defensive), we still consume the block so
        instruction_index stays consistent for everything past it.
        """
        # The label that pointed here (``:pswitch_data_0``) was the
        # most recently recorded label whose index matches the current
        # instruction_index. Recover it lazily so we don't have to
        # thread label-vs-data-block bookkeeping through the regular
        # walk.
        data_label = self._pop_recent_label_at(self._instruction_index)
        # The data block itself counts as one instruction slot.
        self._instruction_index += 1

        case_labels: list[str] = []
        consumed = 1  # for the .packed-switch header
        j = i + 1
        n = len(lines)
        while j < n:
            ln = lines[j]
            if _RE_PACKED_SWITCH_END.match(ln):
                consumed += 1
                break
            entry = _RE_LABEL.match(ln)
            if entry:
                case_labels.append(entry.group("name"))
            consumed += 1
            j += 1
        else:
            raise _DecisionParseError("unterminated .packed-switch block")

        if data_label is not None and data_label in self._pending_switches:
            idx = self._pending_switches.pop(data_label)
            old = self._decisions[idx]
            branches: list[Branch] = []
            for offset, lbl in enumerate(case_labels):
                branches.append(
                    Branch(label=f"case {start_key + offset}", target_label=lbl)
                )
            branches.append(Branch(label="default", target_label=None))
            self._decisions[idx] = DecisionPoint(
                method=old.method,
                instruction_index=old.instruction_index,
                source_line=old.source_line,
                kind=old.kind,
                predicate_registers=old.predicate_registers,
                branches=tuple(branches),
            )
        else:
            logger.debug(
                "decisions: orphan packed-switch data block in %s — no preceding dispatch",
                self.method_signature,
            )
        return consumed

    def _consume_sparse_switch_block(
        self,
        lines: list[str],
        i: int,
    ) -> int:
        """Parse a ``.sparse-switch ... .end sparse-switch`` block.

        Same shape as :meth:`_consume_packed_switch_block` but with
        explicit ``key -> :label`` rows. Branches are labelled with the
        original key (``"case 0x1"``) so the operator's mental model of
        "what key triggers this branch" survives into the UI.
        """
        data_label = self._pop_recent_label_at(self._instruction_index)
        self._instruction_index += 1

        entries: list[tuple[str, str]] = []  # (display_key, target_label)
        consumed = 1  # for the .sparse-switch header
        j = i + 1
        n = len(lines)
        while j < n:
            ln = lines[j]
            if _RE_SPARSE_SWITCH_END.match(ln):
                consumed += 1
                break
            entry = _RE_SPARSE_SWITCH_ENTRY.match(ln)
            if entry:
                entries.append((entry.group("key"), entry.group("label")))
            consumed += 1
            j += 1
        else:
            raise _DecisionParseError("unterminated .sparse-switch block")

        if data_label is not None and data_label in self._pending_switches:
            idx = self._pending_switches.pop(data_label)
            old = self._decisions[idx]
            branches: list[Branch] = [
                Branch(label=f"case {key}", target_label=lbl)
                for key, lbl in entries
            ]
            branches.append(Branch(label="default", target_label=None))
            self._decisions[idx] = DecisionPoint(
                method=old.method,
                instruction_index=old.instruction_index,
                source_line=old.source_line,
                kind=old.kind,
                predicate_registers=old.predicate_registers,
                branches=tuple(branches),
            )
        else:
            logger.debug(
                "decisions: orphan sparse-switch data block in %s — no preceding dispatch",
                self.method_signature,
            )
        return consumed

    def _pop_recent_label_at(self, instruction_index: int) -> Optional[str]:
        """Recover the most recently-recorded label that points at
        ``instruction_index``. Switch data labels (``:pswitch_data_0``)
        live in the same label_index as control-flow labels, so this
        lookup is what lets us tie a data block back to its dispatch."""
        for name, idx in reversed(self._label_index):
            if idx == instruction_index:
                return name
        return None

    def finish(self) -> Optional[MethodDecisions]:
        """Return a frozen :class:`MethodDecisions` for this method, or
        ``None`` if no decision points were found.

        Pending switches that never had a data block (defensive — would
        only happen on truncated apktool output) are dropped from the
        decisions list rather than emitted with empty branches; the
        operator and 10.3 alike would see an unparseable switch as
        worse than a missing one.
        """
        if self._pending_switches:
            for label, idx in list(self._pending_switches.items()):
                logger.debug(
                    "decisions: dropping switch dispatch with no data block in %s (label %s)",
                    self.method_signature,
                    label,
                )
                self._decisions[idx] = None  # type: ignore[call-overload]
            self._pending_switches.clear()
        decisions = tuple(d for d in self._decisions if d is not None)
        if not decisions:
            return None
        return MethodDecisions(
            method_signature=self.method_signature,
            src_file=self.src_file,
            decision_points=decisions,
            label_index=tuple(self._label_index),
        )


# ---------------------------------------------------------------------------
# Opcode → DecisionKind tables (lookup-only).


_KIND_BY_TWO_REG_OP: dict[str, DecisionKind] = {
    "eq": DecisionKind.IF_EQ,
    "ne": DecisionKind.IF_NE,
    "lt": DecisionKind.IF_LT,
    "le": DecisionKind.IF_LE,
    "gt": DecisionKind.IF_GT,
    "ge": DecisionKind.IF_GE,
}

_KIND_BY_ZERO_OP: dict[str, DecisionKind] = {
    "eqz": DecisionKind.IF_EQZ,
    "nez": DecisionKind.IF_NEZ,
    "ltz": DecisionKind.IF_LTZ,
    "lez": DecisionKind.IF_LEZ,
    "gtz": DecisionKind.IF_GTZ,
    "gez": DecisionKind.IF_GEZ,
}


# ---------------------------------------------------------------------------
# File-walk helpers (mirrors smali_parser private helpers; kept local
# rather than importing private names so the module stays decoupled).


def _iter_smali_files(roots: Iterable[Path]) -> Iterator[tuple[Path, Path]]:
    """Yield ``(root, file_path)`` for every ``*.smali`` under each root."""
    for root in roots:
        if not root.is_dir():
            continue
        for p in root.rglob("*.smali"):
            if p.is_file():
                yield root, p


def _common_parent(roots: Iterable[Path]) -> Optional[Path]:
    """Best-effort: the apktool output dir holding all smali roots."""
    for r in roots:
        return r.parent
    return None


def _relative_or_str(p: Path, base: Optional[Path]) -> str:
    if base is None:
        return str(p)
    try:
        return str(p.relative_to(base))
    except ValueError:
        return str(p)
