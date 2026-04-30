.class public Lcom/trace/Helpers;
.super Ljava/lang/Object;
.source "Helpers.java"


# Phase 11 sub-step 11.4 fixture — bounded inter-procedural slicer
# descent + type-driven `is_stateless` analyzer.
#
# Each method is shaped to exercise exactly one descent or
# statelessness path so a per-test fixture method can pin the
# slicer's behaviour without hidden cross-contamination from
# unrelated branches.
#
# Naming convention:
#   * gate*               — top-level entry method whose if-* slices
#                           into a helper; the slicer's depth-2
#                           descent should resolve through one or two
#                           helper hops to the underlying terminal.
#   * pure*               — helper methods the analyzer should
#                           classify as stateless (return-only,
#                           pure-arithmetic, allowed stdlib calls).
#   * stateful*           — helper methods the analyzer should
#                           classify as stateful (field/array writes,
#                           monitor, throw, reflection, calls to
#                           stateful callees).
#   * cycle*              — mutual-recursion pair to exercise the
#                           visited-set cycle termination.
#
# All boolean returns use Z; all int returns use I.


.method public constructor <init>()V
    .registers 1

    invoke-direct {p0}, Ljava/lang/Object;-><init>()V

    return-void
.end method


# ===========================================================================
# Descent — single hop through a stateless helper-method getter
# ===========================================================================
#
# `gateOneHopGetter` slices to a `MethodCallOrigin` for `pureGetFlag()`;
# depth-1 descent re-slices `pureGetFlag()`'s return register (v0)
# and finds a `const-string`. The depth pill UI surfaces "via 1 helper
# method" when 11.6 lights up the depth signal on PredicateOriginView.

.method public gateOneHopGetter()V
    .registers 2

    invoke-virtual {p0}, Lcom/trace/Helpers;->pureGetFlag()Ljava/lang/String;

    move-result-object v0

    if-nez v0, :cond_take

    return-void

    :cond_take
    return-void
.end method

.method public pureGetFlag()Ljava/lang/String;
    .registers 1

    const-string v0, "premium"

    return-object v0
.end method


# ===========================================================================
# Descent — two-hop chain (depth=2 reaches the terminal const)
# ===========================================================================
#
# `gateTwoHopChain` slices to MethodCall for `pureGetA()`; depth-1
# descent re-slices `pureGetA()` which itself slices to MethodCall for
# `pureGetB()`; depth-2 descent re-slices `pureGetB()` to a `const/4`
# terminal. With MAX_SLICE_DEPTH=2 this resolves to ConstOrigin.

.method public gateTwoHopChain()V
    .registers 2

    invoke-virtual {p0}, Lcom/trace/Helpers;->pureGetA()I

    move-result v0

    if-eqz v0, :cond_take

    return-void

    :cond_take
    return-void
.end method

.method public pureGetA()I
    .registers 1

    invoke-virtual {p0}, Lcom/trace/Helpers;->pureGetB()I

    move-result v0

    return v0
.end method

.method public pureGetB()I
    .registers 1

    const/4 v0, 0x1

    return v0
.end method


# ===========================================================================
# Descent — depth cap respected (3-hop chain stops at MethodCallOrigin)
# ===========================================================================
#
# `gateThreeHopChainCapped` would resolve to const at depth=3, but
# MAX_SLICE_DEPTH=2 caps it. The slicer should surface the depth-2
# helper as the terminal MethodCallOrigin (not the original) per
# the spec: "The descended `PredicateOrigin` *replaces* the original
# `MethodCallOrigin` in the slicer's output (operator sees the new
# terminal, not the chain that produced it)".

.method public gateThreeHopChainCapped()V
    .registers 2

    invoke-virtual {p0}, Lcom/trace/Helpers;->pureChainHopOne()I

    move-result v0

    if-eqz v0, :cond_take

    return-void

    :cond_take
    return-void
.end method

.method public pureChainHopOne()I
    .registers 1

    invoke-virtual {p0}, Lcom/trace/Helpers;->pureChainHopTwo()I

    move-result v0

    return v0
.end method

.method public pureChainHopTwo()I
    .registers 1

    invoke-virtual {p0}, Lcom/trace/Helpers;->pureChainHopThree()I

    move-result v0

    return v0
.end method

.method public pureChainHopThree()I
    .registers 1

    const/4 v0, 0x1

    return v0
.end method


# ===========================================================================
# Descent blocked — callee is stateful (field write)
# ===========================================================================
#
# `gateStatefulFieldWriteCallee` slices to MethodCall for
# `statefulIputCallee()`; the analyzer detects the `iput-boolean` and
# returns False. Descent does NOT fire — the original MethodCallOrigin
# is preserved (operator sees the v1 terminal, the slicer doesn't
# spuriously claim the deeper origin).

.method public gateStatefulFieldWriteCallee()V
    .registers 2

    invoke-virtual {p0}, Lcom/trace/Helpers;->statefulIputCallee()I

    move-result v0

    if-eqz v0, :cond_take

    return-void

    :cond_take
    return-void
.end method

.method public statefulIputCallee()I
    .registers 2

    const/4 v0, 0x1

    iput-boolean v0, p0, Lcom/trace/Helpers;->mDirty:Z

    return v0
.end method


# ===========================================================================
# Descent blocked — callee is stateful (sput / array put)
# ===========================================================================

.method public statefulSputCallee()I
    .registers 2

    const/4 v0, 0x1

    sput-boolean v0, Lcom/trace/Helpers;->sDirty:Z

    return v0
.end method

.method public statefulAputCallee([I)I
    .registers 3

    const/4 v0, 0x0

    const/4 v1, 0x2a

    aput v1, p1, v0

    return v0
.end method


# ===========================================================================
# Descent blocked — callee is stateful (monitor / throw)
# ===========================================================================
#
# `monitor-enter` is defensively classified as stateful because it
# observes shared state and is generally the wrong thing to descend
# into (operators investigating predicates rarely care about lock
# semantics; mis-attributing past a monitor would be misleading).
#
# `throw vN` is stateful — it short-circuits the method's return
# value semantics; descending past it claims the wrong terminal.

.method public statefulMonitorCallee()I
    .registers 2

    monitor-enter p0

    const/4 v0, 0x1

    monitor-exit p0

    return v0
.end method

.method public statefulThrowCallee()I
    .registers 2

    new-instance v0, Ljava/lang/RuntimeException;

    invoke-direct {v0}, Ljava/lang/RuntimeException;-><init>()V

    throw v0
.end method


# ===========================================================================
# Stateless via deny-list — String.length / Math.abs / Object.hashCode
# ===========================================================================
#
# `pureStringLength` calls `String.length()` (in the deny-list's
# String allowlist) — analyzer returns True even though we have no
# Smali source for `Ljava/lang/String;`. Descent fires; the helper's
# return register slices back through the invoke + into a CompositeOrigin
# (the invoke-virtual itself). The terminal classification of the
# helper is the operator's call (CompositeOrigin = "this returns the
# result of an unmodelled deterministic library computation").

.method public pureStringLength(Ljava/lang/String;)I
    .registers 2

    invoke-virtual {p1}, Ljava/lang/String;->length()I

    move-result v0

    return v0
.end method

.method public pureMathAbs(I)I
    .registers 2

    invoke-static {p1}, Ljava/lang/Math;->abs(I)I

    move-result v0

    return v0
.end method

.method public pureObjectHashCode(Ljava/lang/Object;)I
    .registers 2

    invoke-virtual {p1}, Ljava/lang/Object;->hashCode()I

    move-result v0

    return v0
.end method


# ===========================================================================
# Stateful via deny-list — String.concat NOT in the allowlist
# ===========================================================================
#
# `String.length` / `String.charAt` / etc. are explicitly enumerated
# as stateless; `String.concat` is NOT — it allocates. The analyzer
# treats not-in-allowlist methods on a deny-listed-with-allowlist
# class as stateful (defensive: assume an allocation/side-effect
# rather than spuriously claim purity).

.method public statefulStringConcat(Ljava/lang/String;)Ljava/lang/String;
    .registers 3

    const-string v0, "x"

    invoke-virtual {p1, v0}, Ljava/lang/String;->concat(Ljava/lang/String;)Ljava/lang/String;

    move-result-object v1

    return-object v1
.end method


# ===========================================================================
# Stateless — pure arithmetic + move + return (no invokes, no field ops)
# ===========================================================================

.method public pureArithmeticOnly(II)I
    .registers 4

    add-int v0, p1, p2

    mul-int/lit8 v1, v0, 0x2

    return v1
.end method


# ===========================================================================
# Stateless via Kotlin Intrinsics (whole-class deny-list entry)
# ===========================================================================
#
# `Intrinsics.areEqual` is a hot Kotlin codegen call site for `==`
# comparisons. The whole class is in the deny-list so any method on
# it is treated stateless without per-method enumeration.

.method public pureKotlinAreEqual(Ljava/lang/Object;Ljava/lang/Object;)Z
    .registers 3

    invoke-static {p1, p2}, Lkotlin/jvm/internal/Intrinsics;->areEqual(Ljava/lang/Object;Ljava/lang/Object;)Z

    move-result v0

    return v0
.end method


# ===========================================================================
# Cycle — mutual recursion between two helpers
# ===========================================================================
#
# `cycleA -> cycleB -> cycleA -> ...`. The visited set in
# `_DescentBudget` (and the same set inside `is_stateless`) terminates
# the walk on the second visit. Both helpers contain only invoke +
# move-result + return, so without a side-effect they should classify
# stateless on full traversal — but cycle termination kicks in first
# and the analyzer's contract is "cycle = defensive False" per the
# spec ("if we hit a cycle without confirming stateless, don't trust
# it"). So both classify stateful → descent does not fire on either.

.method public cycleA()I
    .registers 1

    invoke-virtual {p0}, Lcom/trace/Helpers;->cycleB()I

    move-result v0

    return v0
.end method

.method public cycleB()I
    .registers 1

    invoke-virtual {p0}, Lcom/trace/Helpers;->cycleA()I

    move-result v0

    return v0
.end method


# ===========================================================================
# Cross-class descent — helper lives on a sibling class in the same APK
# ===========================================================================
#
# `gateCrossClassDescent` slices to MethodCall on `Lcom/trace/Slices;
# ->getFlag()Z` (defined at the bottom of Slices.smali as a pure
# `const/4 v0, 0x1; return v0`). The descent should resolve through
# `classes_by_smali` — Slices class is in the index, the method body
# is in `decisions_by_method_sig` (via include_branchless=True), and
# `is_stateless` confirms purity. The terminal becomes
# `ConstOrigin("0x1")`. This pins the cross-class resolution path.

.method public gateCrossClassDescent()V
    .registers 2

    invoke-static {}, Lcom/trace/Slices;->getFlag()Z

    move-result v0

    if-eqz v0, :cond_take

    return-void

    :cond_take
    return-void
.end method


# ===========================================================================
# External callee — terminates as v1 MethodCallOrigin (no source to walk)
# ===========================================================================
#
# `gateExternalAndroidCallee` slices to MethodCall on
# `Landroid/util/Log;->d(...)I`, which is `is_external = true` in the
# call graph. Descent skips it per the spec contract ("AND the callee
# resolves in the call graph (not external)"). The slicer surfaces
# the original MethodCallOrigin unchanged.

.method public gateExternalAndroidCallee(Ljava/lang/String;)V
    .registers 3

    const-string v0, "TAG"

    invoke-static {v0, p1}, Landroid/util/Log;->d(Ljava/lang/String;Ljava/lang/String;)I

    move-result v1

    if-eqz v1, :cond_take

    return-void

    :cond_take
    return-void
.end method


# ===========================================================================
# Helper fields the stateful* methods write to (so smali_parser
# accepts the iput/sput targets as well-formed).
# ===========================================================================

.field public mDirty:Z

.field public static sDirty:Z
