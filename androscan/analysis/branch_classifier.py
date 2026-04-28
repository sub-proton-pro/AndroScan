"""Heuristic deterministic branch outcome classifier — Phase 10
sub-step 10.3.

Given a :class:`androscan.analysis.decisions.MethodDecisions` (one
method's body of decision points + raw instruction stream + label
index), walk each branch's basic block forward from its target label
and score it against a fixed catalog of pentest-relevant signals to
classify the branch as ``"deny"`` / ``"allow"`` / ``"neutral"``.

Each :class:`androscan.analysis.trace_types.DecisionPoint` is enriched
with a :class:`BranchOutcome` carrying per-branch
:class:`BranchVerdict` instances (verdict + signed score + reasons),
plus an overall ``confidence`` float in ``[0.0, 1.0]``. Per DEC-024,
gates with ``confidence < 0.6`` are flagged for LLM re-classification
by 10.5's ``trace_behavior`` skill — heuristics produce the confident
classifications, the LLM only sees the ambiguous cases.

Heuristic catalog (locked in v1)
--------------------------------

Strong DENY (score ``-1.0``):
  * ``throw vN`` — exception unconditionally raised in this branch.
  * ``invoke-static {...}, Ljava/lang/System;->exit(...)`` — process
    termination.
  * ``invoke-static {...}, Landroid/os/Process;->killProcess(...)``
    — process killed by PID.

Strong ALLOW (score ``+1.0``):
  * ``invoke-virtual {...}, L...;->setResult(...)`` — Activity
    reports a result back to its caller. v1 doesn't distinguish
    ``setResult(RESULT_OK)`` from ``setResult(RESULT_CANCELED)``;
    both signal "this branch reports a result" which the operator
    can disambiguate from context.

Moderate DENY (score ``-0.7``):
  * ``invoke-virtual {...}, L...;->finish(\\|Affinity\\|AndRemoveTask)()V``
    *when no preceding* ``setResult`` *in the same basic block* — the
    Activity is being closed without reporting success. Suppression
    matters: the legitimate "I reported a result and now I'm closing"
    flow is not a deny gate.
  * ``const-string vN, "..."`` whose literal matches the curated
    deny-keyword regex (case-insensitive, word-boundary-anchored to
    avoid ``rootView`` / ``debugger`` false positives).

Moderate ALLOW (score ``+0.7``):
  * ``invoke-virtual {...}, L...;->startActivity(ForResult)?(...)`` —
    a new Activity is launched (the "happy path" continues).

Weak DENY (score ``-0.3``):
  * Branch-length ratio ≥ 3:1 with the shorter branch ≤ 3
    instructions, applied *only* when both branches are otherwise
    NEUTRAL. This catches the early-exit gate pattern (short
    deny-branch + long real-work-branch) when no explicit signal
    fired. Restricted to 2-branch ifs — switches use per-case
    verdicts directly, no cross-branch ratio.

Verdict thresholds:
  * ``score <= -0.5`` → ``"deny"``
  * ``score >= +0.5`` → ``"allow"``
  * else → ``"neutral"``

Confidence tiers (from ``max(|branch.score|)`` across all branches):
  * ``>= 1.0`` → ``1.00`` (strong)
  * ``>= 0.7`` → ``0.85`` (moderate / string-keyword)
  * ``>= 0.3`` → ``0.45`` (weak only — *below* the 0.6 threshold;
    10.5 LLM re-classifies)
  * else → ``0.00`` (no signals — LLM re-classifies)

Out of scope for v1
-------------------

* Cross-method analysis — the basic-block walker stays inside one
  method.
* Inter-procedural string flow — only ``const-string`` literals in
  the immediate basic block are matched against the keyword regex;
  strings loaded from resources / fields / decoded at runtime are not
  followed.
* Locale-aware string matching — the regex is English-only. Apps with
  localised security messages will have those gates flagged for LLM
  re-classification (correctly — the LLM speaks every language).
* ``setResult(RESULT_OK)`` vs ``setResult(RESULT_CANCELED)``
  discrimination — both currently signal "result reported"; 10.5's
  LLM can refine via the const slice that precedes the invoke.
"""

from __future__ import annotations

import dataclasses
import logging
import re
from typing import Optional

from androscan.analysis.decisions import MethodDecisions
from androscan.analysis.trace_types import (
    Branch,
    BranchOutcome,
    BranchVerdict,
    DecisionPoint,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Smali pattern regexes for the heuristic catalog.
#
# All patterns assume the input line is whitespace-stripped (which is
# what ``MethodDecisions.instructions`` carries — see
# :meth:`androscan.analysis.decisions._MethodState._emit_instruction`).

# throw vN — exception unconditionally raised.
_RE_THROW = re.compile(r"^throw\s+[vp]\d+")

# invoke-static {...}, Ljava/lang/System;->exit(...)...
_RE_SYSTEM_EXIT = re.compile(
    r"^invoke-static(?:/range)?\s+\{[^}]*\}\s*,\s*"
    r"Ljava/lang/System;->exit\("
)

# invoke-static {...}, Landroid/os/Process;->killProcess(...)...
_RE_KILL_PROCESS = re.compile(
    r"^invoke-static(?:/range)?\s+\{[^}]*\}\s*,\s*"
    r"Landroid/os/Process;->killProcess\("
)

# invoke-virtual {...}, L...;->finish()V (also finishAffinity / finishAndRemoveTask)
_RE_FINISH = re.compile(
    r"^invoke-virtual(?:/range)?\s+\{[^}]*\}\s*,\s*"
    r"L[^;]+;->(?:finish|finishAffinity|finishAndRemoveTask)\(\)V"
)

# invoke-virtual {...}, L...;->setResult(...)
_RE_SET_RESULT = re.compile(
    r"^invoke-virtual(?:/range)?\s+\{[^}]*\}\s*,\s*"
    r"L[^;]+;->setResult\("
)

# invoke-virtual {...}, L...;->startActivity(...)... (covers ForResult)
_RE_START_ACTIVITY = re.compile(
    r"^invoke-virtual(?:/range)?\s+\{[^}]*\}\s*,\s*"
    r"L[^;]+;->startActivity(?:ForResult)?\("
)

# const-string vN, "..." (covers /jumbo). Captures the literal text
# verbatim including any embedded escape sequences.
_RE_CONST_STRING = re.compile(
    r"^const-string(?:/jumbo)?\s+[vp]\d+\s*,\s*\"(?P<value>(?:[^\"\\]|\\.)*)\""
)

# Curated deny-keyword regex. Extends DEC-024's spec
# ``/(premium|locked|jailbroken|unauthori[sz]ed|forbidden|denied|tamper|root|debug)/i``
# with two strict-additive improvements:
#
# 1. Word boundaries (``\b...\b``) around the alternation prevent
#    partial-word false positives — ``rootView`` does not match
#    ``root``, ``debugger`` does not match ``debug``.
#
# 2. Optional ``(?:ed|ing)?`` suffixes on ``tamper`` / ``root`` /
#    ``debug`` (and ``ger`` for ``debug``) so common pentest
#    vocabulary like ``rooted`` / ``tampered`` / ``debugger`` matches
#    — without these, the canonical "Device is rooted" / "App was
#    tampered" detection strings would slip past the heuristic.
#
# Both extensions are operator-facing improvements that strictly
# enlarge the match set; v2 may add more pentest vocabulary
# (``frida``, ``xposed``, ``emulator``, etc.) per operator feedback.
_DENY_KEYWORD_RE = re.compile(
    r"\b(?:premium|locked|jailbroken|unauthori[sz]ed|forbidden|denied|"
    r"tamper(?:ed|ing)?|root(?:ed|ing)?|debug(?:ger|ging)?)\b",
    re.IGNORECASE,
)

# Basic-block terminators — instructions that end the current basic
# block. Walking forward from a branch target stops *after* the first
# terminator (terminator IS part of the block since ``throw`` is itself
# a deny signal). End-of-method-body is also a terminator.
_TERMINATOR_PREFIXES = ("return", "throw", "goto", "if-")
_TERMINATOR_SUFFIX_SWITCH = "-switch"


# ---------------------------------------------------------------------------
# Verdict thresholds + confidence tiers.

_VERDICT_DENY_THRESHOLD = -0.5
_VERDICT_ALLOW_THRESHOLD = 0.5

# Confidence threshold below which 10.5 invokes the LLM. This value is
# *consumed* by 10.5; we surface it here so tests can pin the boundary
# behaviour deterministically.
LLM_RECLASSIFY_THRESHOLD = 0.6

# Length-ratio heuristic constants. Conservative defaults — a 3:1
# ratio with a tiny shorter branch is the canonical early-exit gate
# pattern; tighter than this generates noise in normal code.
_LENGTH_RATIO_MIN = 3.0
_LENGTH_RATIO_SHORT_MAX = 3


# ---------------------------------------------------------------------------
# Public API


def classify_branch_outcomes(method_decisions: MethodDecisions) -> MethodDecisions:
    """Return a :class:`MethodDecisions` with every decision's
    ``branch_outcome`` populated.

    The input is not mutated — frozen dataclass replaced via
    :func:`dataclasses.replace`. Matches 10.2's slicer API exactly so
    10.5's ``trace_behavior`` skill can chain
    ``parse_decisions → slice_predicate_origins → classify_branch_outcomes``
    without intermediate bookkeeping.

    Even when no signals fire (verdicts all ``"neutral"``,
    ``confidence == 0.0``), ``branch_outcome`` is still populated —
    callers distinguish "didn't run the classifier" (``None``) from
    "ran it and found nothing" (``confidence == 0.0``).
    """
    label_index = dict(method_decisions.label_index)
    enriched: list[DecisionPoint] = []
    for dp in method_decisions.decision_points:
        outcome = _classify_one(dp, method_decisions.instructions, label_index)
        enriched.append(dataclasses.replace(dp, branch_outcome=outcome))
    return dataclasses.replace(method_decisions, decision_points=tuple(enriched))


def classify_one_decision(
    decision: DecisionPoint,
    instructions: tuple[str, ...],
    label_index: dict[str, int],
) -> BranchOutcome:
    """Standalone classifier for one :class:`DecisionPoint` — useful
    for tests and 10.5's per-anchor walk where the caller may hold the
    decision separately from its enclosing :class:`MethodDecisions`."""
    return _classify_one(decision, instructions, label_index)


# ---------------------------------------------------------------------------
# Per-decision classifier


def _classify_one(
    decision: DecisionPoint,
    instructions: tuple[str, ...],
    label_index: dict[str, int],
) -> BranchOutcome:
    """Score every branch's basic block, optionally apply the
    cross-branch length-ratio heuristic, then assemble the
    :class:`BranchOutcome`."""
    # Step 1 — score each branch's basic block in isolation.
    per_branch: list[tuple[Branch, float, list[str]]] = []
    block_lengths: list[int] = []
    for branch in decision.branches:
        block = _basic_block(
            branch.target_label,
            decision.instruction_index,
            instructions,
            label_index,
        )
        block_lengths.append(len(block))
        score, reasons = _score_block(block)
        per_branch.append((branch, score, reasons))

    # Step 2 — cross-branch length-ratio heuristic. Only applies to
    # 2-branch ifs (switches use per-case verdicts directly) and only
    # when neither branch already has a non-neutral signal — otherwise
    # we'd be adding noise on top of a clear classification.
    cross_reasons: list[str] = []
    if (
        len(per_branch) == 2
        and not decision.is_switch
        and all(s == 0.0 for _b, s, _r in per_branch)
    ):
        ratio_signal = _length_ratio_signal(block_lengths)
        if ratio_signal is not None:
            short_index, ratio = ratio_signal
            short_branch, _s, short_reasons = per_branch[short_index]
            short_reasons.append(
                f"branch length ratio {ratio:.1f}:1 → weak deny on shorter side ({block_lengths[short_index]} instr)"
            )
            per_branch[short_index] = (short_branch, -0.3, short_reasons)
            cross_reasons.append(
                f"branch length ratio {ratio:.1f}:1 between branches"
            )

    # Step 3 — assemble verdicts + confidence.
    verdicts: list[BranchVerdict] = []
    max_signal = 0.0
    for branch, score, reasons in per_branch:
        verdict = _verdict_from_score(score)
        verdicts.append(
            BranchVerdict(
                branch_label=branch.label,
                verdict=verdict,
                score=score,
                reasons=tuple(reasons),
            )
        )
        if abs(score) > max_signal:
            max_signal = abs(score)

    confidence = _confidence_from_max_signal(max_signal)
    return BranchOutcome(
        verdicts=tuple(verdicts),
        confidence=confidence,
        reasons=tuple(cross_reasons),
    )


# ---------------------------------------------------------------------------
# Basic-block walker


def _basic_block(
    target_label: Optional[str],
    decision_instruction_index: int,
    instructions: tuple[str, ...],
    label_index: dict[str, int],
) -> tuple[str, ...]:
    """Walk forward from ``target_label`` (or fall-through from
    ``decision_instruction_index + 1``) until we hit a basic-block
    terminator. The terminator is included in the returned block so
    ``throw`` (which IS a deny signal) is scoreable in-place."""
    if target_label is None:
        # Fall-through: the next instruction after the decision starts
        # this branch's basic block.
        start = decision_instruction_index + 1
    else:
        start = label_index.get(target_label)
        if start is None:
            # Unresolved label — defensive; shouldn't happen on
            # well-formed apktool output but if it does, treat the
            # block as empty (will score 0 → neutral).
            return ()

    block: list[str] = []
    i = start
    n = len(instructions)
    while i < n:
        line = instructions[i]
        block.append(line)
        if _is_terminator(line):
            break
        i += 1
    return tuple(block)


def _is_terminator(line: str) -> bool:
    """``True`` if ``line`` ends a basic block — ``return*`` /
    ``throw`` / ``goto*`` / ``if-*`` / ``*-switch``."""
    if any(line.startswith(p) for p in _TERMINATOR_PREFIXES):
        return True
    # packed-switch / sparse-switch — opcode contains "-switch".
    head = line.split(None, 1)[0] if line else ""
    return head.endswith(_TERMINATOR_SUFFIX_SWITCH)


# ---------------------------------------------------------------------------
# Per-block scoring


def _score_block(block: tuple[str, ...]) -> tuple[float, list[str]]:
    """Score one basic block against the heuristic catalog. Returns
    ``(signed_score, reasons)``.

    Linear walk so the ``finish``-after-``setResult`` suppression
    rule is order-aware.
    """
    score = 0.0
    reasons: list[str] = []
    setresult_seen = False

    for line in block:
        # Strong DENY: throw / System.exit / Process.killProcess.
        if _RE_THROW.match(line):
            score -= 1.0
            reasons.append("strong deny: throw")
            continue
        if _RE_SYSTEM_EXIT.match(line):
            score -= 1.0
            reasons.append("strong deny: System.exit")
            continue
        if _RE_KILL_PROCESS.match(line):
            score -= 1.0
            reasons.append("strong deny: Process.killProcess")
            continue

        # Strong ALLOW: setResult.
        if _RE_SET_RESULT.match(line):
            score += 1.0
            reasons.append("strong allow: setResult")
            setresult_seen = True
            continue

        # Moderate ALLOW: startActivity*.
        if _RE_START_ACTIVITY.match(line):
            score += 0.7
            reasons.append("moderate allow: startActivity")
            continue

        # Moderate DENY: finish (only when no preceding setResult).
        if _RE_FINISH.match(line):
            if not setresult_seen:
                score -= 0.7
                reasons.append("moderate deny: finish without setResult")
            # else: setResult preceded → allow flow, suppress finish signal.
            continue

        # Moderate DENY: const-string with deny-keyword match.
        cs = _RE_CONST_STRING.match(line)
        if cs:
            value = cs.group("value")
            keyword_match = _DENY_KEYWORD_RE.search(value)
            if keyword_match:
                score -= 0.7
                reasons.append(
                    f"moderate deny: const-string matched deny keyword '{keyword_match.group(0)}'"
                )
            continue

    return score, reasons


# ---------------------------------------------------------------------------
# Cross-branch length-ratio heuristic


def _length_ratio_signal(block_lengths: list[int]) -> Optional[tuple[int, float]]:
    """For 2-branch ifs with no other signals, return
    ``(short_index, ratio)`` if the length ratio crosses the
    early-exit-gate threshold, else ``None``."""
    if len(block_lengths) != 2:
        return None
    a, b = block_lengths
    if a == 0 or b == 0:
        # An empty block (unresolved label) shouldn't trigger the
        # ratio heuristic — would dominate any comparison.
        return None
    if a <= b:
        short_index, short_len, long_len = 0, a, b
    else:
        short_index, short_len, long_len = 1, b, a
    if short_len > _LENGTH_RATIO_SHORT_MAX:
        return None
    ratio = long_len / short_len
    if ratio < _LENGTH_RATIO_MIN:
        return None
    return short_index, ratio


# ---------------------------------------------------------------------------
# Verdict + confidence assembly


def _verdict_from_score(score: float) -> str:
    if score <= _VERDICT_DENY_THRESHOLD:
        return "deny"
    if score >= _VERDICT_ALLOW_THRESHOLD:
        return "allow"
    return "neutral"


def _confidence_from_max_signal(max_signal: float) -> float:
    """Tiered confidence per the locked v1 contract — see module
    docstring. Values are pinned by ``test_branch_classifier.py`` so
    consumers (10.5's LLM gate, the UI in 10.7) can rely on them."""
    if max_signal >= 1.0:
        return 1.0
    if max_signal >= 0.7:
        return 0.85
    if max_signal >= 0.3:
        return 0.45
    return 0.0
