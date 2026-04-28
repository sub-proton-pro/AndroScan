.class public Lcom/trace/HiddenGate;
.super Ljava/lang/Object;
.source "HiddenGate.java"


# Lives under smali_classes2/ to verify the multi-dex walker reaches
# secondary dex roots — same pattern as call_graph_smali/Helper.

.method public constructor <init>()V
    .registers 1

    .line 1
    invoke-direct {p0}, Ljava/lang/Object;-><init>()V

    return-void
.end method


# A single decision in a multi-dex class. Asserting that this method
# shows up in parse_decisions output is what proves the secondary dex
# root is being walked.

.method public isJailbroken()Z
    .registers 2

    .line 5
    invoke-static {}, Lcom/trace/HiddenGate;->probe()Z

    move-result v0

    if-eqz v0, :cond_clean

    .line 8
    const/4 v0, 0x1

    return v0

    :cond_clean
    .line 11
    const/4 v0, 0x0

    return v0
.end method

.method public static probe()Z
    .registers 1

    const/4 v0, 0x0

    return v0
.end method
