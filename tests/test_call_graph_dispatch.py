"""Dispatch tests for :mod:`androscan.analysis.dispatch`.

Exercises the v2 virtual-dispatch resolver against the fixture hierarchy:

* ``invoke-virtual`` on ``Animal.speak()`` expands to ``Animal``, ``Dog``, ``Cat``.
* ``invoke-interface`` on ``Greeter.greet()`` expands to ``HelloGreeter``.
* ``invoke-direct`` / ``invoke-static`` stay single-target.
* External destinations (``Landroid/util/Log;``, ``Ljava/lang/Class;``) are
  flagged via ``external_targets`` and get ``kind="external"`` on their
  edges regardless of opcode.
"""

from __future__ import annotations

from pathlib import Path

from androscan.analysis import dispatch, smali_parser


FIXTURES = Path(__file__).parent / "fixtures" / "call_graph_smali"


def _resolve_fixture() -> tuple[
    list[smali_parser.ClassDecl],
    list[dispatch.ResolvedEdge],
    set[str],
]:
    roots = [FIXTURES / "smali", FIXTURES / "smali_classes2"]
    classes, _ = smali_parser.parse_classes(roots)
    invokes, _refl, _ = smali_parser.parse_invokes(roots, classes)
    edges, _hier, external = dispatch.resolve_invokes(classes, invokes)
    return classes, edges, external


def test_build_hierarchy_adjacency() -> None:
    roots = [FIXTURES / "smali", FIXTURES / "smali_classes2"]
    classes, _ = smali_parser.parse_classes(roots)
    by_desc, children, hier = dispatch.build_hierarchy(classes)
    assert "Lcom/example/Animal;" in by_desc
    assert children["Lcom/example/Animal;"] == {"Lcom/example/Dog;", "Lcom/example/Cat;"}
    assert "Lcom/example/HelloGreeter;" in children["Lcom/example/Greeter;"]
    # extends Object is recorded but doesn't populate the children index.
    assert "Lcom/example/Animal;" not in children.get("Ljava/lang/Object;", set())
    rels = {(h.child, h.relation) for h in hier}
    assert ("Lcom/example/HelloGreeter;", "implements") in rels


def test_virtual_invoke_expands_to_all_subclass_overrides() -> None:
    """``App.main`` calls ``Animal.speak()`` via ``invoke-virtual``; fidelity
    v2 should yield three resolved targets (Animal, Dog, Cat)."""
    _classes, edges, _ext = _resolve_fixture()
    main_speak_edges = [
        e for e in edges
        if e.src_method_sig == "Lcom/example/App;->main()V"
        and e.invoke_op == "invoke-virtual"
    ]
    dsts = {e.dst_method_sig for e in main_speak_edges}
    assert "Lcom/example/Animal;->speak()V" in dsts
    assert "Lcom/example/Dog;->speak()V" in dsts
    assert "Lcom/example/Cat;->speak()V" in dsts
    assert all(e.kind == dispatch.KIND_VIRTUAL_DISPATCH for e in main_speak_edges)


def test_interface_invoke_expands_to_implementors() -> None:
    """``App.greetAll`` calls ``Greeter.greet(String)`` via ``invoke-interface``;
    must yield edges to both the interface method itself and the impl."""
    _classes, edges, _ext = _resolve_fixture()
    greet_edges = [
        e for e in edges
        if e.src_method_sig == "Lcom/example/App;->greetAll(Lcom/example/Greeter;)V"
        and e.invoke_op == "invoke-interface"
    ]
    dsts = {e.dst_method_sig for e in greet_edges}
    assert "Lcom/example/HelloGreeter;->greet(Ljava/lang/String;)V" in dsts
    assert all(e.kind == dispatch.KIND_INTERFACE_DISPATCH for e in greet_edges)


def test_direct_invoke_stays_single_edge() -> None:
    """``Dog.<init>`` → ``Animal.<init>`` via ``invoke-direct`` — single
    edge, kind="direct", since Animal is in-app."""
    _classes, edges, _ext = _resolve_fixture()
    direct = [
        e for e in edges
        if e.src_method_sig == "Lcom/example/Dog;-><init>()V"
        and e.dst_method_sig == "Lcom/example/Animal;-><init>()V"
    ]
    assert len(direct) == 1
    assert direct[0].kind == dispatch.KIND_DIRECT
    assert direct[0].invoke_op == "invoke-direct"


def test_static_invoke_stays_single_edge() -> None:
    """``App.useHelper`` → ``Helper.help()`` via ``invoke-static``."""
    _classes, edges, _ext = _resolve_fixture()
    helper = [
        e for e in edges
        if e.src_method_sig == "Lcom/example/App;->useHelper()V"
        and e.dst_method_sig == "Lcom/example/Helper;->help()V"
    ]
    assert len(helper) == 1
    assert helper[0].kind == dispatch.KIND_STATIC


def test_external_log_call_gets_external_kind() -> None:
    """Destination-externality wins: ``invoke-static`` on ``Log.d`` becomes
    kind=external, not kind=static."""
    _classes, edges, external = _resolve_fixture()
    log_edges = [
        e for e in edges
        if e.dst_method_sig.startswith("Landroid/util/Log;->d(")
    ]
    assert log_edges and all(e.kind == dispatch.KIND_EXTERNAL for e in log_edges)
    # Materialised as an external target.
    assert any(sig.startswith("Landroid/util/Log;") for sig in external)


def test_reflection_targets_flagged_external() -> None:
    _classes, edges, external = _resolve_fixture()
    class_forname = [
        e for e in edges
        if e.dst_method_sig.startswith("Ljava/lang/Class;->forName(")
    ]
    assert class_forname and all(e.kind == dispatch.KIND_EXTERNAL for e in class_forname)
    assert any(sig.startswith("Ljava/lang/Class;") for sig in external)


def test_direct_init_to_object_is_external() -> None:
    """``Animal.<init>`` → ``Object.<init>`` — Object is external, so
    kind must be ``external`` even though opcode was ``invoke-direct``."""
    _classes, edges, _ext = _resolve_fixture()
    animal_ctor = [
        e for e in edges
        if e.src_method_sig == "Lcom/example/Animal;-><init>()V"
        and e.dst_method_sig == "Ljava/lang/Object;-><init>()V"
    ]
    assert len(animal_ctor) == 1
    assert animal_ctor[0].kind == dispatch.KIND_EXTERNAL
    assert animal_ctor[0].invoke_op == "invoke-direct"


def test_bfs_truncation_flag() -> None:
    """Synthetic test — build a fake class hierarchy with too many
    overrides and assert ``truncated=True`` gets propagated."""
    # Fake class set: Base, Sub1..Sub100 all overriding speak().
    from androscan.analysis.smali_parser import ClassDecl, InvokeRecord, MethodDecl

    base = ClassDecl(
        class_desc="Lt/Base;",
        super_desc="Ljava/lang/Object;",
        interfaces=(),
        file="t/Base.smali",
        methods=(MethodDecl(
            class_desc="Lt/Base;", name="speak", params="", ret="V",
            flags=(), file="t/Base.smali", line_start=1, line_end=2,
        ),),
    )
    subs = []
    N = dispatch.MAX_OVERRIDES_PER_INVOKE + 20
    for i in range(N):
        subs.append(ClassDecl(
            class_desc=f"Lt/Sub{i};",
            super_desc="Lt/Base;",
            interfaces=(),
            file=f"t/Sub{i}.smali",
            methods=(MethodDecl(
                class_desc=f"Lt/Sub{i};", name="speak", params="", ret="V",
                flags=(), file=f"t/Sub{i}.smali", line_start=1, line_end=2,
            ),),
        ))
    inv = InvokeRecord(
        src_method_sig="Lt/Caller;->call()V",
        src_file="t/Caller.smali",
        src_line=10,
        kind="virtual",
        target_owner="Lt/Base;",
        target_name="speak",
        target_params="",
        target_ret="V",
    )
    edges, _h, _ext = dispatch.resolve_invokes([base, *subs], [inv])
    assert len(edges) == dispatch.MAX_OVERRIDES_PER_INVOKE
    assert all(e.truncated is True for e in edges)
