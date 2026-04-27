.class public Lcom/example/Cat;
.super Lcom/example/Animal;
.source "Cat.java"


# direct methods
.method public constructor <init>()V
    .registers 1

    .line 1
    invoke-direct {p0}, Lcom/example/Animal;-><init>()V

    return-void
.end method


# virtual methods
.method public speak()V
    .registers 1

    .line 15
    return-void
.end method
