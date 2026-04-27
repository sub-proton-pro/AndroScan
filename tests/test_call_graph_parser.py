"""Parser tests for :mod:`androscan.analysis.smali_parser`.

These run purely against the fixture smali under ``tests/fixtures/call_graph_smali/``
— no apktool or SQLite involved. Keeping the parser pure means any future
smali quirks surface here first, before the persist layer has a chance
to obscure them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from androscan.analysis import smali_parser
from androscan.analysis.smali_types import (
    compute_access_flags,
    descriptor_to_java,
    params_to_java,
    split_class_name,
)


FIXTURES = Path(__file__).parent / "fixtures" / "call_graph_smali"


def _roots() -> list[Path]:
    return [FIXTURES / "smali", FIXTURES / "smali_classes2"]


# ---------------------------------------------------------------------------
# Pass 1: classes / methods


def test_parse_classes_finds_all_fixture_classes() -> None:
    classes, summary = smali_parser.parse_classes(_roots())
    descs = {c.class_desc for c in classes}
    assert descs == {
        "Lcom/example/Animal;",
        "Lcom/example/Dog;",
        "Lcom/example/Cat;",
        "Lcom/example/Greeter;",
        "Lcom/example/HelloGreeter;",
        "Lcom/example/App;",
        "Lcom/example/Helper;",
    }
    assert summary.classes == 7
    assert summary.skipped_files == 0


def test_parse_classes_detects_interface_flag() -> None:
    classes, _ = smali_parser.parse_classes(_roots())
    by_desc = {c.class_desc: c for c in classes}
    assert by_desc["Lcom/example/Greeter;"].is_interface is True
    assert by_desc["Lcom/example/Greeter;"].is_abstract is True
    assert by_desc["Lcom/example/Animal;"].is_interface is False
    assert by_desc["Lcom/example/Animal;"].is_abstract is False


def test_parse_classes_captures_super_and_interfaces() -> None:
    classes, _ = smali_parser.parse_classes(_roots())
    by_desc = {c.class_desc: c for c in classes}
    assert by_desc["Lcom/example/Dog;"].super_desc == "Lcom/example/Animal;"
    assert by_desc["Lcom/example/HelloGreeter;"].interfaces == ("Lcom/example/Greeter;",)


def test_parse_classes_captures_methods_with_spans() -> None:
    classes, _ = smali_parser.parse_classes(_roots())
    dog = next(c for c in classes if c.class_desc == "Lcom/example/Dog;")
    method_names = [m.name for m in dog.methods]
    assert "<init>" in method_names
    assert "speak" in method_names
    speak = next(m for m in dog.methods if m.name == "speak")
    assert speak.line_start < speak.line_end
    assert speak.params == ""
    assert speak.ret == "V"


def test_parse_classes_multi_dex_helper_picked_up() -> None:
    """The multi-dex fixture lives under ``smali_classes2/``; pass 1 must
    walk both dex roots."""
    classes, _ = smali_parser.parse_classes(_roots())
    helper = next(c for c in classes if c.class_desc == "Lcom/example/Helper;")
    assert helper.file.startswith("smali_classes2")
    help_method = next(m for m in helper.methods if m.name == "help")
    assert help_method.is_static is True


# ---------------------------------------------------------------------------
# Pass 2: invokes + reflection sentinels


def test_parse_invokes_captures_all_opcodes() -> None:
    classes, _ = smali_parser.parse_classes(_roots())
    invokes, _refl, summary = smali_parser.parse_invokes(_roots(), classes)
    kinds = {i.kind for i in invokes}
    assert kinds >= {"virtual", "direct", "static", "interface"}
    assert summary.invokes > 0


def test_parse_invokes_attaches_line_numbers() -> None:
    classes, _ = smali_parser.parse_classes(_roots())
    invokes, _refl, _ = smali_parser.parse_invokes(_roots(), classes)
    app_main = [
        i for i in invokes
        if i.src_method_sig == "Lcom/example/App;->main()V"
    ]
    # App.main has two invokes: invoke-direct to Dog.<init> (line 10-ish)
    # and invoke-virtual to Animal.speak() (line 11).
    assert any(
        i.kind == "virtual"
        and i.target_owner == "Lcom/example/Animal;"
        and i.target_name == "speak"
        and i.src_line == 11
        for i in app_main
    )


def test_parse_invokes_detects_reflection_sentinels() -> None:
    classes, _ = smali_parser.parse_classes(_roots())
    _invokes, reflection, summary = smali_parser.parse_invokes(_roots(), classes)
    refl_targets = {r.target for r in reflection}
    assert "Ljava/lang/Class;->forName" in refl_targets
    assert "Ljava/lang/Class;->getMethod" in refl_targets
    # All reflection hits must be attributed to App.reflect().
    assert all(
        r.src_method_sig == "Lcom/example/App;->reflect()V"
        for r in reflection
    )
    assert summary.reflection_hits == len(reflection)


def test_parse_invokes_interface_kind_for_greet() -> None:
    classes, _ = smali_parser.parse_classes(_roots())
    invokes, _refl, _ = smali_parser.parse_invokes(_roots(), classes)
    greet_invokes = [
        i for i in invokes
        if i.target_name == "greet" and i.target_owner == "Lcom/example/Greeter;"
    ]
    assert greet_invokes and all(i.kind == "interface" for i in greet_invokes)


def test_parse_invokes_external_log_call() -> None:
    classes, _ = smali_parser.parse_classes(_roots())
    invokes, _refl, _ = smali_parser.parse_invokes(_roots(), classes)
    log_calls = [
        i for i in invokes
        if i.target_owner == "Landroid/util/Log;"
    ]
    assert log_calls
    assert all(i.kind == "static" for i in log_calls)


# ---------------------------------------------------------------------------
# smali_types helpers — small tests keep the edge cases honest


@pytest.mark.parametrize("desc,java", [
    ("V", "void"),
    ("I", "int"),
    ("Ljava/lang/String;", "java.lang.String"),
    ("[B", "byte[]"),
    ("[[I", "int[][]"),
    ("[Lcom/example/Foo;", "com.example.Foo[]"),
    ("Lcom/example/Foo$Inner;", "com.example.Foo$Inner"),
])
def test_descriptor_to_java(desc: str, java: str) -> None:
    assert descriptor_to_java(desc) == java


def test_params_to_java_splits_complex_list() -> None:
    assert params_to_java("ILjava/lang/String;[B") == [
        "int", "java.lang.String", "byte[]",
    ]
    assert params_to_java("") == []


def test_split_class_name_default_and_packaged() -> None:
    assert split_class_name("com.example.Foo") == ("com.example", "Foo")
    assert split_class_name("com.example.Foo$Inner") == ("com.example", "Foo$Inner")
    assert split_class_name("NoPackage") == ("", "NoPackage")


def test_compute_access_flags_or_bits_for_public_static() -> None:
    flags = compute_access_flags(("public", "static"))
    assert flags & 0x0001, "public bit set"
    assert flags & 0x0008, "static bit set"
    assert flags & 0x0200 == 0, "interface bit NOT set"
