.class public Lcom/trace/SwitchCases;
.super Ljava/lang/Object;
.source "SwitchCases.java"


# Packed and sparse switches with a representative shape: each case has
# a non-empty body so the case label_index entries point at real
# instructions. The default fall-through is modelled as
# Branch(label="default", target_label=None) by the extractor.

.method public constructor <init>()V
    .registers 1

    .line 1
    invoke-direct {p0}, Ljava/lang/Object;-><init>()V

    return-void
.end method


# --- packed-switch on an int parameter --------------------------------------
# Three sequential cases starting at key 0. Operator framing: an
# error-code dispatcher where case 0 is "ok", 1 is "denied", 2 is
# "blocked" — exactly the pattern the 10.3 classifier will look at.

.method public dispatchCode(I)V
    .registers 2

    .line 10
    packed-switch p1, :pswitch_data_0

    .line 11
    return-void

    :pswitch_0
    .line 14
    invoke-virtual {p0}, Lcom/trace/SwitchCases;->onOk()V

    return-void

    :pswitch_1
    .line 18
    invoke-virtual {p0}, Lcom/trace/SwitchCases;->onDenied()V

    return-void

    :pswitch_2
    .line 22
    invoke-virtual {p0}, Lcom/trace/SwitchCases;->onBlocked()V

    return-void

    :pswitch_data_0
    .packed-switch 0x0
        :pswitch_0
        :pswitch_1
        :pswitch_2
    .end packed-switch
.end method


# --- sparse-switch on a hash-like int ---------------------------------------
# Three non-contiguous cases (the realistic shape — Java's switch on
# String compiles down to a hash-discriminated sparse switch).

.method public dispatchHash(I)V
    .registers 2

    .line 30
    sparse-switch p1, :sswitch_data_0

    .line 31
    return-void

    :sswitch_0
    .line 34
    invoke-virtual {p0}, Lcom/trace/SwitchCases;->onLow()V

    return-void

    :sswitch_1
    .line 38
    invoke-virtual {p0}, Lcom/trace/SwitchCases;->onMid()V

    return-void

    :sswitch_2
    .line 42
    invoke-virtual {p0}, Lcom/trace/SwitchCases;->onHigh()V

    return-void

    :sswitch_data_0
    .sparse-switch
        0x1 -> :sswitch_0
        0x5 -> :sswitch_1
        0xa -> :sswitch_2
    .end sparse-switch
.end method


# Helper methods (no branches; here just so jadx-style dispatch targets
# can be referenced by the cases above).

.method public onOk()V
    .registers 1
    return-void
.end method

.method public onDenied()V
    .registers 1
    return-void
.end method

.method public onBlocked()V
    .registers 1
    return-void
.end method

.method public onLow()V
    .registers 1
    return-void
.end method

.method public onMid()V
    .registers 1
    return-void
.end method

.method public onHigh()V
    .registers 1
    return-void
.end method
