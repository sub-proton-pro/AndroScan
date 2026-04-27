"""Pure-function Smali parser feeding the static call-graph index, plus
small lexical helpers used by :mod:`androscan.analysis.call_graph` to
denormalise Smali descriptors into Java-form strings for the UI.

Two-pass design
---------------

Walking apktool's smali output once is cheap, but call resolution needs
the *whole* class hierarchy before we expand virtual dispatches. So we
walk twice:

* :func:`parse_classes` — first pass. For every ``.smali`` file: extract
  the class descriptor, super-class, implemented interfaces, and the list
  of declared methods (with access flags, signature, line spans). No
  invoke parsing here.
* :func:`parse_invokes` — second pass. For every ``.smali`` file: for each
  declared method, walk its body and record every ``invoke-*`` instruction
  as an :class:`InvokeRecord`. Reflection sentinels (calls to
  ``Method->invoke`` / ``Class->forName``) are flagged so the UI can warn
  "graph may be incomplete".

The two passes share no state — both return immutable dataclasses, and
:mod:`androscan.analysis.dispatch` later joins them. This makes both
phases independently testable on tiny smali fixtures.

Smali grammar tolerated
-----------------------

We deliberately implement a *lexical* parser, not a full grammar:

* Class header: ``.class`` line + optional ``.super`` / ``.implements``.
* Method headers: ``.method <flags> <name>(<params>)<return>`` … ``.end method``.
* Method body lines beginning with ``invoke-`` capture call sites.
* ``.line N`` directives interleave with body lines; the most recently
  seen ``.line`` is attached to the next ``invoke-*``.

Anything else (annotations, .registers, .locals, packed-switch, fill-array
data, etc.) is ignored by design — call-graph fidelity v2 only needs
class hierarchy + invokes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, Optional


# ---------------------------------------------------------------------------
# Smali tokens
#
# Class descriptors are JVM-style: ``Lcom/example/Foo;``. We keep them in
# that form throughout so equality/hash comparisons against ``.super`` and
# invoke targets are byte-exact (no normalisation needed).

# .class [public] [final] [abstract] [interface] Lcom/example/Foo;
# Non-greedy ``flags`` lets us recover both the class flag tokens (for
# ``is_interface`` / ``is_abstract``) and the descriptor in one match.
_RE_CLASS = re.compile(r"^\s*\.class\b(?P<flags>[^L\n]*?)\s+(?P<desc>L[^;\s]+;)")
# .super Lcom/example/Bar;
_RE_SUPER = re.compile(r"^\s*\.super\s+(?P<desc>L[^;\s]+;)")
# .implements Lcom/example/IBaz;
_RE_IMPL = re.compile(r"^\s*\.implements\s+(?P<desc>L[^;\s]+;)")
# .method [flags...] name(params)return
_RE_METHOD = re.compile(
    r"^\s*\.method\b(?P<flags>[^(]*?)\s+"
    r"(?P<sig><(?:cl)?init>|[A-Za-z_$][A-Za-z_$0-9]*)\((?P<params>[^)]*)\)(?P<ret>\S+)"
)
_RE_END_METHOD = re.compile(r"^\s*\.end\s+method\b")
# .line 42
_RE_LINE = re.compile(r"^\s*\.line\s+(?P<num>\d+)")
# invoke-virtual {p0, p1}, Lcom/example/Foo;->bar(I)V
# invoke-static  {}, Lcom/example/Util;->u()V
# invoke-direct/range, invoke-super, invoke-interface, invoke-polymorphic, invoke-custom
_RE_INVOKE = re.compile(
    r"^\s*invoke-(?P<kind>virtual|super|direct|static|interface|polymorphic|custom)"
    r"(?:/range)?\s+\{[^}]*\}\s*,\s*"
    r"(?P<owner>L[^;\s]+;)->"
    r"(?P<method><(?:cl)?init>|[A-Za-z_$][A-Za-z_$0-9]*)"
    r"\((?P<params>[^)]*)\)(?P<ret>\S+)"
)

# Reflection sentinels — called *via* invoke-virtual on a Method or Class
# instance. Marking them lets the UI annotate "this method may call code we
# can't see" without the parser having to model reflection semantics.
_REFLECTION_TARGETS = frozenset({
    "Ljava/lang/reflect/Method;->invoke",
    "Ljava/lang/Class;->forName",
    "Ljava/lang/Class;->getMethod",
    "Ljava/lang/Class;->getDeclaredMethod",
    "Ljava/lang/ClassLoader;->loadClass",
})


# ---------------------------------------------------------------------------
# Records returned by the two passes


@dataclass(frozen=True)
class MethodDecl:
    """One declared method on a class."""
    class_desc: str   # e.g. "Lcom/example/Foo;"
    name: str         # e.g. "onCreate"
    params: str       # raw smali param list, e.g. "Landroid/os/Bundle;"
    ret: str          # raw smali return type, e.g. "V"
    flags: tuple[str, ...]
    file: str         # apktool-relative path (e.g. "smali/com/example/Foo.smali")
    line_start: int   # line of the .method directive
    line_end: int     # line of the matching .end method

    @property
    def signature(self) -> str:
        """Compact unique key: ``Lcom/example/Foo;->onCreate(Landroid/os/Bundle;)V``."""
        return f"{self.class_desc}->{self.name}({self.params}){self.ret}"

    @property
    def is_static(self) -> bool:
        return "static" in self.flags

    @property
    def is_abstract(self) -> bool:
        return "abstract" in self.flags

    @property
    def is_constructor(self) -> bool:
        return self.name in ("<init>", "<clinit>")


@dataclass(frozen=True)
class ClassDecl:
    """One class declaration. Methods captured in :attr:`methods`."""
    class_desc: str
    super_desc: Optional[str]
    interfaces: tuple[str, ...]
    file: str
    flags: tuple[str, ...] = ()
    methods: tuple[MethodDecl, ...] = ()

    @property
    def is_interface(self) -> bool:
        return "interface" in self.flags

    @property
    def is_abstract(self) -> bool:
        return "abstract" in self.flags


@dataclass(frozen=True)
class InvokeRecord:
    """One invoke-* instruction recorded inside a method body."""
    src_method_sig: str        # caller, e.g. "Lcom/example/A;->m()V"
    src_file: str
    src_line: Optional[int]    # last seen .line directive, or None
    kind: str                  # virtual | super | direct | static | interface | polymorphic | custom
    target_owner: str          # owner descriptor, e.g. "Lcom/example/B;"
    target_name: str
    target_params: str
    target_ret: str

    @property
    def target_static_sig(self) -> str:
        """Statically-resolved target signature (no virtual expansion yet)."""
        return f"{self.target_owner}->{self.target_name}({self.target_params}){self.target_ret}"

    @property
    def target_owner_method(self) -> str:
        """``owner;->name`` without the descriptor — for reflection-sentinel match."""
        return f"{self.target_owner}->{self.target_name}"


@dataclass(frozen=True)
class ReflectionHit:
    """One reflection-style invoke flagged for UI annotation."""
    src_method_sig: str
    src_file: str
    src_line: Optional[int]
    target: str   # e.g. "Ljava/lang/reflect/Method;->invoke"


@dataclass
class ParseSummary:
    """Aggregate counters surfaced via the call-graph ``meta`` table."""
    smali_files: int = 0
    classes: int = 0
    methods: int = 0
    invokes: int = 0
    reflection_hits: int = 0
    skipped_files: int = 0
    parse_errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Pass 1: classes / methods


def _iter_smali_files(roots: Iterable[Path]) -> Iterator[tuple[Path, Path]]:
    """Yield ``(root, file_path)`` for every ``*.smali`` under each root.

    ``root`` is returned alongside so the caller can compute root-relative
    paths uniformly across multi-dex apps.
    """
    for root in roots:
        if not root.is_dir():
            continue
        for p in root.rglob("*.smali"):
            if p.is_file():
                yield root, p


def parse_classes(roots: Iterable[Path]) -> tuple[list[ClassDecl], ParseSummary]:
    """First pass — return the class hierarchy and per-class method lists.

    Result is a list (not a dict) so callers can iterate in deterministic
    order. Multiple files declaring the same class are tolerated (last
    one wins for super/interfaces; methods accumulate); apktool itself
    won't emit duplicates so this is purely a defensive choice.
    """
    classes: list[ClassDecl] = []
    summary = ParseSummary()
    apktool_root = _common_parent(roots)

    for root, path in _iter_smali_files(roots):
        summary.smali_files += 1
        rel = _relative_or_str(path, apktool_root)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            summary.skipped_files += 1
            summary.parse_errors.append(f"{rel}: read error: {e}")
            continue

        decl = _parse_one_class(text, rel)
        if decl is None:
            summary.skipped_files += 1
            continue
        classes.append(decl)
        summary.classes += 1
        summary.methods += len(decl.methods)
    return classes, summary


def _parse_one_class(text: str, rel_path: str) -> Optional[ClassDecl]:
    """Parse a single ``.smali`` file into a :class:`ClassDecl`.

    Returns ``None`` if no ``.class`` directive is found in the first ~200
    lines (e.g. a stray comment-only file).
    """
    lines = text.splitlines()
    class_desc: Optional[str] = None
    class_flags: tuple[str, ...] = ()
    super_desc: Optional[str] = None
    interfaces: list[str] = []

    method_open: Optional[tuple[int, re.Match[str]]] = None
    methods: list[MethodDecl] = []

    for i, raw in enumerate(lines, start=1):
        if class_desc is None:
            m = _RE_CLASS.match(raw)
            if m:
                class_desc = m.group("desc")
                class_flags = tuple(t for t in m.group("flags").split() if t)
                continue
        else:
            m = _RE_SUPER.match(raw)
            if m:
                super_desc = m.group("desc")
                continue
            m = _RE_IMPL.match(raw)
            if m:
                interfaces.append(m.group("desc"))
                continue

        if class_desc is None:
            # Don't bother parsing method bodies before we know the class.
            continue

        if method_open is None:
            m = _RE_METHOD.match(raw)
            if m:
                method_open = (i, m)
                continue
        else:
            if _RE_END_METHOD.match(raw):
                start_line, mm = method_open
                flags = tuple(t for t in mm.group("flags").split() if t)
                methods.append(
                    MethodDecl(
                        class_desc=class_desc,
                        name=mm.group("sig"),
                        params=mm.group("params"),
                        ret=mm.group("ret"),
                        flags=flags,
                        file=rel_path,
                        line_start=start_line,
                        line_end=i,
                    )
                )
                method_open = None

    if class_desc is None:
        return None
    return ClassDecl(
        class_desc=class_desc,
        super_desc=super_desc,
        interfaces=tuple(interfaces),
        file=rel_path,
        flags=class_flags,
        methods=tuple(methods),
    )


# ---------------------------------------------------------------------------
# Pass 2: invokes + reflection sentinels


def parse_invokes(
    roots: Iterable[Path],
    classes: list[ClassDecl],
) -> tuple[list[InvokeRecord], list[ReflectionHit], ParseSummary]:
    """Second pass — re-walk smali files to extract invoke-* sites.

    We resolve the *caller* method per invoke by tracking which method
    body we're currently inside (``.method`` ... ``.end method``). This is
    cheaper than indexing methods by line range up-front and works on
    files that pass-1 skipped.

    ``classes`` is accepted only to skip files that didn't yield a class
    (keeps counts consistent with pass 1). It is not consulted for
    resolution — that's :mod:`androscan.analysis.dispatch`'s job.
    """
    invokes: list[InvokeRecord] = []
    refl: list[ReflectionHit] = []
    summary = ParseSummary()
    classes_with_methods = {c.file for c in classes if c.methods}
    apktool_root = _common_parent(roots)

    for _root, path in _iter_smali_files(roots):
        summary.smali_files += 1
        rel = _relative_or_str(path, apktool_root)
        if rel not in classes_with_methods:
            # File had no class or no methods (interfaces with no defaults,
            # synthetic accessors stripped, etc.). Nothing to scan.
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            summary.skipped_files += 1
            summary.parse_errors.append(f"{rel}: read error: {e}")
            continue

        cls_match = _RE_CLASS.search(text)
        if not cls_match:
            continue
        class_desc = cls_match.group("desc")

        cur_caller: Optional[str] = None
        last_line: Optional[int] = None

        for raw in text.splitlines():
            mm = _RE_METHOD.match(raw)
            if mm:
                cur_caller = (
                    f"{class_desc}->{mm.group('sig')}"
                    f"({mm.group('params')}){mm.group('ret')}"
                )
                last_line = None
                continue
            if _RE_END_METHOD.match(raw):
                cur_caller = None
                last_line = None
                continue
            if cur_caller is None:
                continue
            ln = _RE_LINE.match(raw)
            if ln:
                try:
                    last_line = int(ln.group("num"))
                except ValueError:  # pragma: no cover - regex guarantees digits
                    last_line = None
                continue
            inv = _RE_INVOKE.match(raw)
            if inv:
                rec = InvokeRecord(
                    src_method_sig=cur_caller,
                    src_file=rel,
                    src_line=last_line,
                    kind=inv.group("kind"),
                    target_owner=inv.group("owner"),
                    target_name=inv.group("method"),
                    target_params=inv.group("params"),
                    target_ret=inv.group("ret"),
                )
                invokes.append(rec)
                summary.invokes += 1
                if rec.target_owner_method in _REFLECTION_TARGETS:
                    refl.append(
                        ReflectionHit(
                            src_method_sig=cur_caller,
                            src_file=rel,
                            src_line=last_line,
                            target=rec.target_owner_method,
                        )
                    )
                    summary.reflection_hits += 1
    return invokes, refl, summary


# ---------------------------------------------------------------------------
# Helpers


def _common_parent(roots: Iterable[Path]) -> Optional[Path]:
    """Best-effort: the apktool output dir holding all smali roots.

    All ``smali_classes*`` siblings live under ``<apktool_out>``, so we
    just take the first root's parent. When ``roots`` is empty (callers
    that pass an empty iterator) we return ``None`` and fall back to
    str(path) for relpaths.
    """
    for r in roots:
        return r.parent
    return None


def _relative_or_str(p: Path, base: Optional[Path]) -> str:
    """``p.relative_to(base)`` when possible, else ``str(p)``.

    We never raise on weird symlink layouts — the call graph would lose
    line context but not blow up the worker.
    """
    if base is None:
        return str(p)
    try:
        return str(p.relative_to(base))
    except ValueError:
        return str(p)
