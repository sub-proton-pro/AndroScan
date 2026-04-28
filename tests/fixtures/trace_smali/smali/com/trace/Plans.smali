.class public Lcom/trace/Plans;
.super Ljava/lang/Object;
.source "Plans.java"


# Phase 10 sub-step 10.4 fixture — bypass-planner test methods.
#
# Each method is shaped so the pipeline (decisions → slicing →
# classify → plan_bypasses) emits a fully-deterministic plan list.
# The general pattern: predicate value is sourced via a recognisable
# means (method call / field / const / param / composite / String.equals);
# false branch (fall-through) carries an ALLOW signal (setResult) so
# the classifier emits a clean DENY/ALLOW pair; true branch (cond_deny)
# carries a strong DENY signal (throw / System.exit) — except where
# the test specifically wants both sides DENY or symmetric NEUTRAL.


.method public constructor <init>()V
    .registers 1

    invoke-direct {p0}, Ljava/lang/Object;-><init>()V

    return-void
.end method


# --- Plan A (Z return) + Plan B (void gate) ------------------------------
# Predicate value comes from boolean isPremium(); allow side is fall-through
# (false branch). Plan A forces isPremium() → true. Plan B (force_method_skip)
# fires because the gate method returns void.

.method public gateBoolPredicate()V
    .registers 2

    invoke-virtual {p0}, Lcom/trace/Plans;->isPremium()Z

    move-result v0

    if-eqz v0, :cond_deny

    const/4 v0, -0x1

    invoke-virtual {p0, v0}, Lcom/trace/Plans;->setResult(I)V

    return-void

    :cond_deny
    new-instance v0, Ljava/lang/SecurityException;

    invoke-direct {v0}, Ljava/lang/SecurityException;-><init>()V

    throw v0
.end method


# --- Plan A (I return) + Plan B (void gate) -----------------------------
# Predicate from int getCheckCode(); allow side is true branch (cond_allow).
# Plan A forces getCheckCode() → 1 (the non-zero literal needed so if-nez
# takes the cond_allow branch).

.method public gateIntPredicate()V
    .registers 2

    invoke-virtual {p0}, Lcom/trace/Plans;->getCheckCode()I

    move-result v0

    if-nez v0, :cond_allow

    new-instance v0, Ljava/lang/SecurityException;

    invoke-direct {v0}, Ljava/lang/SecurityException;-><init>()V

    throw v0

    :cond_allow
    const/4 v0, -0x1

    invoke-virtual {p0, v0}, Lcom/trace/Plans;->setResult(I)V

    return-void
.end method


# --- Plan A (L...; null direction) + Plan B ------------------------------
# Reference predicate where the allow side wants null. Plan A forces
# getDenyToken() → null.

.method public gateRefAllowNull()V
    .registers 2

    invoke-virtual {p0}, Lcom/trace/Plans;->getDenyToken()Ljava/lang/String;

    move-result-object v0

    if-eqz v0, :cond_allow

    new-instance v0, Ljava/lang/SecurityException;

    invoke-direct {v0}, Ljava/lang/SecurityException;-><init>()V

    throw v0

    :cond_allow
    const/4 v0, -0x1

    invoke-virtual {p0, v0}, Lcom/trace/Plans;->setResult(I)V

    return-void
.end method


# --- NO Plan A (L...; non-null side) + Plan B ----------------------------
# Reference predicate where the allow side wants non-null — the planner
# can't synthesise an instance, so Plan A is honestly skipped. Plan B
# still fires because the gate is void.

.method public gateRefAllowNonNull()V
    .registers 2

    invoke-virtual {p0}, Lcom/trace/Plans;->getAuthToken()Ljava/lang/String;

    move-result-object v0

    if-eqz v0, :cond_deny

    const/4 v1, -0x1

    invoke-virtual {p0, v1}, Lcom/trace/Plans;->setResult(I)V

    return-void

    :cond_deny
    new-instance v0, Ljava/lang/SecurityException;

    invoke-direct {v0}, Ljava/lang/SecurityException;-><init>()V

    throw v0
.end method


# --- Plan A + NO Plan B (non-void gate returns Z) ------------------------
# Same predicate-flip story as gateBoolPredicate but the *gate method*
# itself returns boolean. Plan B (force_method_skip on void) cannot fire.

.method public gateNonVoidReturn()Z
    .registers 2

    invoke-virtual {p0}, Lcom/trace/Plans;->isPremium()Z

    move-result v0

    if-eqz v0, :cond_deny

    const/4 v0, -0x1

    invoke-virtual {p0, v0}, Lcom/trace/Plans;->setResult(I)V

    const/4 v0, 0x1

    return v0

    :cond_deny
    new-instance v0, Ljava/lang/SecurityException;

    invoke-direct {v0}, Ljava/lang/SecurityException;-><init>()V

    throw v0
.end method


# --- NO Plan A (ConstOrigin) + Plan B -----------------------------------

.method public gateConstPredicate()V
    .registers 2

    const/4 v0, 0x0

    if-eqz v0, :cond_deny

    const/4 v0, -0x1

    invoke-virtual {p0, v0}, Lcom/trace/Plans;->setResult(I)V

    return-void

    :cond_deny
    new-instance v0, Ljava/lang/SecurityException;

    invoke-direct {v0}, Ljava/lang/SecurityException;-><init>()V

    throw v0
.end method


# --- NO Plan A (FieldReadOrigin) + Plan B -------------------------------

.method public gateFieldPredicate()V
    .registers 2

    iget-boolean v0, p0, Lcom/trace/Plans;->mFlag:Z

    if-eqz v0, :cond_deny

    const/4 v0, -0x1

    invoke-virtual {p0, v0}, Lcom/trace/Plans;->setResult(I)V

    return-void

    :cond_deny
    new-instance v0, Ljava/lang/SecurityException;

    invoke-direct {v0}, Ljava/lang/SecurityException;-><init>()V

    throw v0
.end method


# --- NO Plan A (CompositeOrigin from add-int) + Plan B ------------------

.method public gateCompositePredicate(II)V
    .registers 4

    add-int v0, p1, p2

    if-eqz v0, :cond_deny

    const/4 v0, -0x1

    invoke-virtual {p0, v0}, Lcom/trace/Plans;->setResult(I)V

    return-void

    :cond_deny
    new-instance v0, Ljava/lang/SecurityException;

    invoke-direct {v0}, Ljava/lang/SecurityException;-><init>()V

    throw v0
.end method


# --- NO Plan A (String.equals routes to C) + Plan B + Plan C ------------
# Canonical license-check shape. The const-string "LICENSE_VALID_42" is
# above the equals call (backward scan from decision finds it).

.method public gateStringEqualsWithLiteral(Ljava/lang/String;)V
    .registers 3

    const-string v0, "LICENSE_VALID_42"

    invoke-virtual {p1, v0}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z

    move-result v0

    if-eqz v0, :cond_deny

    const/4 v0, -0x1

    invoke-virtual {p0, v0}, Lcom/trace/Plans;->setResult(I)V

    return-void

    :cond_deny
    new-instance v0, Ljava/lang/SecurityException;

    invoke-direct {v0}, Ljava/lang/SecurityException;-><init>()V

    throw v0
.end method


# --- NO Plan A + Plan B + NO Plan C (String.equals but no const-string) -

.method public gateStringEqualsNoLiteral(Ljava/lang/String;Ljava/lang/String;)V
    .registers 4

    invoke-virtual {p1, p2}, Ljava/lang/String;->equals(Ljava/lang/Object;)Z

    move-result v0

    if-eqz v0, :cond_deny

    const/4 v0, -0x1

    invoke-virtual {p0, v0}, Lcom/trace/Plans;->setResult(I)V

    return-void

    :cond_deny
    new-instance v0, Ljava/lang/SecurityException;

    invoke-direct {v0}, Ljava/lang/SecurityException;-><init>()V

    throw v0
.end method


# --- No plans (both branches DENY → no flip target) ---------------------

.method public gateBothBranchesDeny(Z)V
    .registers 2

    if-eqz p1, :cond_other_deny

    new-instance v0, Ljava/lang/SecurityException;

    invoke-direct {v0}, Ljava/lang/SecurityException;-><init>()V

    throw v0

    :cond_other_deny
    const/4 v0, 0x0

    invoke-static {v0}, Ljava/lang/System;->exit(I)V

    return-void
.end method
