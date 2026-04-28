.class public Lcom/trace/Slices;
.super Ljava/lang/Object;
.source "Slices.java"


# Each method is the smallest realistic Smali shape that exercises one
# of the slicer's classification paths. The if-* (or *-switch) is
# always the last "real" instruction before its target label, so the
# decision's instruction_index is well-defined and the slicer's
# backward walk has a known terminus.

.method public constructor <init>()V
    .registers 1

    .line 1
    invoke-direct {p0}, Ljava/lang/Object;-><init>()V

    return-void
.end method


# --- MethodCallOrigin: invoke-static + move-result + if-eqz ---------------

.method public sliceMethodCall()V
    .registers 2

    .line 10
    invoke-static {}, Lcom/trace/Slices;->getFlag()Z

    move-result v0

    if-eqz v0, :cond_take

    .line 13
    return-void

    :cond_take
    .line 16
    return-void
.end method


# --- FieldReadOrigin (instance): iget-boolean + if-eqz --------------------

.method public sliceIgetField()V
    .registers 2

    .line 20
    iget-boolean v0, p0, Lcom/trace/Slices;->mFlag:Z

    if-eqz v0, :cond_take

    .line 22
    return-void

    :cond_take
    .line 25
    return-void
.end method


# --- FieldReadOrigin (static): sget-boolean + if-eqz ----------------------

.method public sliceSgetField()V
    .registers 2

    .line 30
    sget-boolean v0, Lcom/trace/Slices;->sFlag:Z

    if-eqz v0, :cond_take

    .line 32
    return-void

    :cond_take
    .line 35
    return-void
.end method


# --- ConstOrigin: const/4 + if-eqz ----------------------------------------

.method public sliceConstInt()V
    .registers 2

    .line 40
    const/4 v0, 0x1

    if-eqz v0, :cond_take

    .line 42
    return-void

    :cond_take
    .line 45
    return-void
.end method


# --- ConstOrigin (string): const-string + if-nez --------------------------

.method public sliceConstString()V
    .registers 2

    .line 50
    const-string v0, "premium"

    if-nez v0, :cond_take

    .line 52
    return-void

    :cond_take
    .line 55
    return-void
.end method


# --- ParamOrigin: pN unmodified -------------------------------------------

.method public sliceParam(Z)V
    .registers 2

    .line 60
    if-eqz p1, :cond_take

    .line 62
    return-void

    :cond_take
    .line 65
    return-void
.end method


# --- CompositeOrigin (arithmetic): add-int + if-gtz -----------------------

.method public sliceArithmetic(II)V
    .registers 3

    .line 70
    add-int v0, p1, p2

    if-gtz v0, :cond_take

    .line 72
    return-void

    :cond_take
    .line 75
    return-void
.end method


# --- CompositeOrigin (instance-of) ----------------------------------------

.method public sliceInstanceOf(Ljava/lang/Object;)V
    .registers 2

    .line 80
    instance-of v0, p1, Ljava/lang/String;

    if-eqz v0, :cond_take

    .line 82
    return-void

    :cond_take
    .line 85
    return-void
.end method


# --- CompositeOrigin (move-exception via try/catch) -----------------------

.method public sliceMoveException()V
    .registers 2

    :try_start_0
    .line 90
    invoke-virtual {p0}, Lcom/trace/Slices;->riskyOp()V
    :try_end_0
    .catch Ljava/lang/Exception; {:try_start_0 .. :try_end_0} :catch_0

    .line 91
    return-void

    :catch_0
    move-exception v0

    if-eqz v0, :cond_take

    .line 95
    return-void

    :cond_take
    .line 98
    return-void
.end method


# --- Move chain: iget v1 → move v0, v1 → if-eqz v0 → resolves to FieldRead

.method public sliceMoveChain()V
    .registers 3

    .line 100
    iget-boolean v1, p0, Lcom/trace/Slices;->mFlag:Z

    move v0, v1

    if-eqz v0, :cond_take

    .line 103
    return-void

    :cond_take
    .line 106
    return-void
.end method


# --- Two-register: iget vs const → returns FieldReadOrigin (more actionable)

.method public sliceTwoRegConstAndField()V
    .registers 3

    .line 110
    iget v0, p0, Lcom/trace/Slices;->mLevel:I

    const/4 v1, 0x5

    if-ge v0, v1, :cond_take

    .line 113
    return-void

    :cond_take
    .line 116
    return-void
.end method


# --- Two-register: invoke vs invoke → returns MethodCallOrigin (LHS wins) -

.method public sliceTwoRegBothMethodCalls()V
    .registers 3

    .line 120
    invoke-virtual {p0}, Lcom/trace/Slices;->getA()I

    move-result v0

    invoke-virtual {p0}, Lcom/trace/Slices;->getB()I

    move-result v1

    if-eq v0, v1, :cond_take

    .line 125
    return-void

    :cond_take
    .line 128
    return-void
.end method


# --- Slice failure: max_walk exhaustion. We pad the method body with a
# long run of unrelated instructions before the predicate so a small
# max_walk caps the slice. The test sets max_walk explicitly low.

.method public sliceWalkExhausted()V
    .registers 2

    .line 130
    const/4 v0, 0x1
    const/4 v1, 0x0
    const/4 v1, 0x0
    const/4 v1, 0x0
    const/4 v1, 0x0
    const/4 v1, 0x0
    const/4 v1, 0x0
    const/4 v1, 0x0
    const/4 v1, 0x0
    const/4 v1, 0x0
    const/4 v1, 0x0

    if-eqz v0, :cond_take

    return-void

    :cond_take
    return-void
.end method


# --- Helper methods (no branches; here only as invoke targets) ------------

.method public static getFlag()Z
    .registers 1
    const/4 v0, 0x1
    return v0
.end method

.method public riskyOp()V
    .registers 1
    return-void
.end method

.method public getA()I
    .registers 1
    const/4 v0, 0x0
    return v0
.end method

.method public getB()I
    .registers 1
    const/4 v0, 0x0
    return v0
.end method
