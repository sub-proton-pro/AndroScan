.class public Lcom/trace/Outcomes;
.super Ljava/lang/Object;
.source "Outcomes.java"


# Each method is shaped so the classifier's expected per-branch
# verdict + score + confidence is fully deterministic. The if-eqz
# pattern is canonical: TRUE branch jumps to :cond_safe (typically a
# return), FALSE branch falls through into the "interesting" basic
# block (throw / setResult / etc.) so the test asserts on the false
# branch's verdict.

.method public constructor <init>()V
    .registers 1

    .line 1
    invoke-direct {p0}, Ljava/lang/Object;-><init>()V

    return-void
.end method


# --- Strong DENY: throw on fall-through -----------------------------------
# Pentester framing: a SecurityException raised when the gate fails.

.method public denyByThrow(Z)V
    .registers 2

    if-eqz p1, :cond_safe

    new-instance v0, Ljava/lang/SecurityException;

    invoke-direct {v0}, Ljava/lang/SecurityException;-><init>()V

    throw v0

    :cond_safe
    return-void
.end method


# --- Strong DENY: System.exit(int) on fall-through ------------------------

.method public denyBySystemExit(Z)V
    .registers 2

    if-eqz p1, :cond_safe

    const/4 v0, -0x1

    invoke-static {v0}, Ljava/lang/System;->exit(I)V

    return-void

    :cond_safe
    return-void
.end method


# --- Strong DENY: Process.killProcess(int) on fall-through ----------------

.method public denyByKillProcess(Z)V
    .registers 2

    if-eqz p1, :cond_safe

    invoke-static {}, Landroid/os/Process;->myPid()I

    move-result v0

    invoke-static {v0}, Landroid/os/Process;->killProcess(I)V

    return-void

    :cond_safe
    return-void
.end method


# --- Moderate DENY: Activity.finish() with no preceding setResult --------

.method public denyByFinish(Z)V
    .registers 1

    if-eqz p1, :cond_safe

    invoke-virtual {p0}, Lcom/trace/Outcomes;->finish()V

    return-void

    :cond_safe
    return-void
.end method


# --- Suppression: setResult preceding finish → ALLOW (finish suppressed) -

.method public setResultBeforeFinish(Z)V
    .registers 2

    if-eqz p1, :cond_safe

    const/4 v0, -0x1

    invoke-virtual {p0, v0}, Lcom/trace/Outcomes;->setResult(I)V

    invoke-virtual {p0}, Lcom/trace/Outcomes;->finish()V

    return-void

    :cond_safe
    return-void
.end method


# --- Strong ALLOW: setResult alone on fall-through -----------------------

.method public allowBySetResult(Z)V
    .registers 2

    if-eqz p1, :cond_safe

    const/4 v0, -0x1

    invoke-virtual {p0, v0}, Lcom/trace/Outcomes;->setResult(I)V

    return-void

    :cond_safe
    return-void
.end method


# --- Moderate ALLOW: startActivity on fall-through -----------------------

.method public allowByStartActivity(Z)V
    .registers 2

    if-eqz p1, :cond_safe

    new-instance v0, Landroid/content/Intent;

    invoke-direct {v0}, Landroid/content/Intent;-><init>()V

    invoke-virtual {p0, v0}, Lcom/trace/Outcomes;->startActivity(Landroid/content/Intent;)V

    return-void

    :cond_safe
    return-void
.end method


# --- Moderate DENY: const-string with deny-keyword ("rooted") ------------

.method public denyByRootedString(Z)V
    .registers 2

    if-eqz p1, :cond_safe

    const-string v0, "Device is rooted"

    return-void

    :cond_safe
    return-void
.end method


# --- Moderate DENY: const-string "PREMIUM ONLY" (case-insensitive match) -

.method public denyByPremiumString(Z)V
    .registers 2

    if-eqz p1, :cond_safe

    const-string v0, "PREMIUM ONLY"

    return-void

    :cond_safe
    return-void
.end method


# --- Moderate DENY: const-string "User is unauthorised" (British spelling)

.method public denyByUnauthorizedString(Z)V
    .registers 2

    if-eqz p1, :cond_safe

    const-string v0, "User is unauthorised"

    return-void

    :cond_safe
    return-void
.end method


# --- Word-boundary check: "rootView" should NOT match "root(ed|ing)?" ---

.method public wordBoundaryExcludesRootView(Z)V
    .registers 2

    if-eqz p1, :cond_safe

    const-string v0, "rootView focused"

    return-void

    :cond_safe
    return-void
.end method


# --- Weak DENY via length ratio: short branch (1 instr) vs long (8 instr)

.method public lengthRatioGate(Z)V
    .registers 2

    if-eqz p1, :cond_short

    const/4 v0, 0x0

    const/4 v0, 0x1

    const/4 v0, 0x2

    const/4 v0, 0x3

    const/4 v0, 0x4

    const/4 v0, 0x5

    const/4 v0, 0x6

    return-void

    :cond_short
    return-void
.end method


# --- Neutral when both branches are similar and signal-free --------------

.method public neutralWhenSymmetric(Z)V
    .registers 2

    if-eqz p1, :cond_b

    const/4 v0, 0x1

    return-void

    :cond_b
    const/4 v0, 0x2

    return-void
.end method


# --- Switch with throw / setResult / neutral cases + neutral default ----

.method public switchOutcomes(I)V
    .registers 3

    packed-switch p1, :pswitch_data_0

    return-void

    :pswitch_0
    new-instance v0, Ljava/lang/SecurityException;

    invoke-direct {v0}, Ljava/lang/SecurityException;-><init>()V

    throw v0

    :pswitch_1
    const/4 v0, -0x1

    invoke-virtual {p0, v0}, Lcom/trace/Outcomes;->setResult(I)V

    return-void

    :pswitch_2
    return-void

    :pswitch_data_0
    .packed-switch 0x0
        :pswitch_0
        :pswitch_1
        :pswitch_2
    .end packed-switch
.end method


# --- Clean DENY/ALLOW split: throw on one side, setResult on the other --

.method public denyAllowSplit(Z)V
    .registers 2

    if-eqz p1, :cond_allow

    new-instance v0, Ljava/lang/SecurityException;

    invoke-direct {v0}, Ljava/lang/SecurityException;-><init>()V

    throw v0

    :cond_allow
    const/4 v0, -0x1

    invoke-virtual {p0, v0}, Lcom/trace/Outcomes;->setResult(I)V

    return-void
.end method
