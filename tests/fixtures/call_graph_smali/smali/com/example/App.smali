.class public Lcom/example/App;
.super Ljava/lang/Object;
.source "App.java"


# direct methods
.method public constructor <init>()V
    .registers 1

    .line 1
    invoke-direct {p0}, Ljava/lang/Object;-><init>()V

    return-void
.end method

.method public static main()V
    .registers 1

    .line 10
    new-instance v0, Lcom/example/Dog;

    invoke-direct {v0}, Lcom/example/Dog;-><init>()V

    .line 11
    invoke-virtual {v0}, Lcom/example/Animal;->speak()V

    return-void
.end method

.method public static greetAll(Lcom/example/Greeter;)V
    .registers 2

    .line 20
    const-string v1, "hi"

    .line 21
    invoke-interface {p0, v1}, Lcom/example/Greeter;->greet(Ljava/lang/String;)V

    return-void
.end method

.method public static reflect()V
    .registers 2

    .line 30
    const-string v0, "com.example.Target"

    .line 31
    invoke-static {v0}, Ljava/lang/Class;->forName(Ljava/lang/String;)Ljava/lang/Class;

    move-result-object v1

    .line 32
    const-string v0, "m"

    invoke-virtual {v1, v0}, Ljava/lang/Class;->getMethod(Ljava/lang/String;)Ljava/lang/reflect/Method;

    return-void
.end method

.method public static logIt()V
    .registers 2

    .line 40
    const-string v0, "TAG"

    const-string v1, "msg"

    invoke-static {v0, v1}, Landroid/util/Log;->d(Ljava/lang/String;Ljava/lang/String;)I

    return-void
.end method

.method public static useHelper()V
    .registers 0

    .line 50
    invoke-static {}, Lcom/example/Helper;->help()V

    return-void
.end method
