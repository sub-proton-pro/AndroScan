.class public Lcom/trace/Gates;
.super Ljava/lang/Object;
.source "Gates.java"


# All twelve if-* opcodes plus realistic pentest gates. Each method is
# crafted so the decision-extractor's branch / register / label results
# can be asserted directly.

.method public constructor <init>()V
    .registers 1

    .line 1
    invoke-direct {p0}, Ljava/lang/Object;-><init>()V

    return-void
.end method


# --- Linear method (no decisions) -------------------------------------------
# Used to assert that empty-decision methods are excluded from output.

.method public greet()V
    .registers 1

    .line 5
    const-string v0, "hello"

    invoke-static {v0}, Landroid/util/Log;->d(Ljava/lang/String;)I

    return-void
.end method


# --- Single zero-comparison: if-eqz -----------------------------------------
# Realistic root-detection gate. v0 is the "is rooted" boolean; if it is
# zero we proceed to the protected feature, else we deny.

.method public checkRoot(Z)V
    .registers 2

    .line 10
    if-eqz p1, :cond_safe

    .line 11
    new-instance v0, Ljava/lang/SecurityException;
    invoke-direct {v0}, Ljava/lang/SecurityException;-><init>()V
    throw v0

    :cond_safe
    .line 14
    return-void
.end method


# --- All five remaining zero-comparisons ------------------------------------
# Pure coverage method; not realistic, but exercises if-nez, if-ltz,
# if-lez, if-gtz, if-gez each exactly once with predictable branch
# targets.

.method public coverZero(I)V
    .registers 2

    .line 20
    if-nez p1, :cond_a

    :cond_a
    if-ltz p1, :cond_b

    :cond_b
    if-lez p1, :cond_c

    :cond_c
    if-gtz p1, :cond_d

    :cond_d
    if-gez p1, :cond_e

    :cond_e
    return-void
.end method


# --- All six two-register comparisons ---------------------------------------
# Same coverage shape as coverZero but for the if-eq / if-ne / if-lt /
# if-le / if-gt / if-ge family. Each comparison uses two distinct
# parameter registers so predicate_registers tuples can be asserted in
# pairs.

.method public coverTwoReg(II)V
    .registers 3

    .line 30
    if-eq p1, p2, :cond_a

    :cond_a
    if-ne p1, p2, :cond_b

    :cond_b
    if-lt p1, p2, :cond_c

    :cond_c
    if-le p1, p2, :cond_d

    :cond_d
    if-gt p1, p2, :cond_e

    :cond_e
    if-ge p1, p2, :cond_f

    :cond_f
    return-void
.end method


# --- License gate (representative pentest target) --------------------------
# Two decisions in one method, one with a deny branch (throw + finish)
# and one with the "allowed" path. Used by the test that asserts source
# lines are correctly attached to the decision following the most
# recent .line directive.

.method public openPremium(Z)V
    .registers 3

    .line 40
    if-nez p1, :cond_premium

    .line 41
    invoke-virtual {p0}, Lcom/trace/Gates;->showUpsell()V

    return-void

    :cond_premium
    .line 45
    invoke-virtual {p0}, Lcom/trace/Gates;->loadPremiumScreen()V

    return-void
.end method


# Helper methods referenced above; we don't care about their bodies for
# decision-extractor purposes (no branches inside).

.method public showUpsell()V
    .registers 1

    return-void
.end method

.method public loadPremiumScreen()V
    .registers 1

    return-void
.end method
