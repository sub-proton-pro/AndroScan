"""Virtual-dispatch resolution for the static call graph (fidelity v2).

Why this module exists
----------------------

A raw ``invoke-virtual {p0}, Lcom/example/Animal;->speak()V`` only tells
us the *static* receiver type. At runtime the JVM dispatches to the most
derived override on the concrete receiver. Without modelling that, the
graph would say "all virtual calls go to the declared owner" which is
wrong for any polymorphic code (which is most code).

Fidelity v2 trade-off:

* ``invoke-direct`` / ``invoke-static`` / ``invoke-super`` → kept as a
  single edge to the static target (exact at runtime).
* ``invoke-virtual`` / ``invoke-interface`` → expanded via BFS over the
  class hierarchy to every in-app subclass / implementor override with
  a matching ``(name, params, ret)``. The static target itself is
  always emitted so the graph reaches *something* even when no
  observed overrides exist.
* Externally-owned targets (``java/*``, ``kotlin/*``, ``androidx/*``,
  anything not declared in the parsed class set) are flagged and the
  persist layer materialises them as ``classes.is_external=1`` +
  ``nodes.is_external=1`` rows so neighbour queries are uniform.
* Edge kind enum is fixed: ``direct | static | super |
  virtual_dispatch | interface_dispatch | external``. The destination-
  level ``external`` wins over opcode kind: if the resolved destination
  class is external, the edge is ``external``; opcode-specific kinds
  only apply when the destination is in-app.

Expansion is bounded by :data:`MAX_OVERRIDES_PER_INVOKE` so pathological
hierarchies (``Object.toString`` overridden in 500 places) don't blow up
the SQLite write phase. Truncated invokes carry a ``truncated=True`` flag
on the edge so the UI can warn.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Iterable, Optional

from androscan.analysis.smali_parser import ClassDecl, InvokeRecord, MethodDecl


MAX_OVERRIDES_PER_INVOKE = 64

# Edge-kind enum — locked in the schema and used by future sub-steps
# (4.2 Cytoscape renderer, 4.7 query_call_graph skill). Keep narrow +
# stable.
KIND_DIRECT = "direct"
KIND_STATIC = "static"
KIND_SUPER = "super"
KIND_VIRTUAL_DISPATCH = "virtual_dispatch"
KIND_INTERFACE_DISPATCH = "interface_dispatch"
KIND_EXTERNAL = "external"


@dataclass(frozen=True)
class ResolvedEdge:
    """One edge ready for SQLite insert (after FK resolution at persist time)."""
    src_method_sig: str
    dst_method_sig: str
    kind: str
    src_file: str
    src_line: Optional[int]
    invoke_op: str   # "invoke-virtual" / "invoke-direct" / ...
    truncated: bool


@dataclass(frozen=True)
class HierarchyEdge:
    """One row for the ``hierarchy`` view — child→parent or child→interface.

    Note: ``hierarchy`` isn't a first-class table in the plan's schema;
    the class-parent relationship lives in ``classes.super_class`` and
    interfaces live in ``class_interfaces``. This dataclass is kept as a
    convenience for future skills that want a flat adjacency list.
    """
    child: str
    parent: str
    relation: str  # "extends" | "implements"


def build_hierarchy(classes: Iterable[ClassDecl]) -> tuple[
    dict[str, ClassDecl],
    dict[str, set[str]],
    list[HierarchyEdge],
]:
    """Index classes for dispatch.

    Returns:

    * ``by_desc``: descriptor → :class:`ClassDecl`.
    * ``children``: parent descriptor → set of immediate descendants
      (via ``extends`` *and* ``implements``, because an interface call
      site must walk all implementors).
    * ``edges``: flat hierarchy adjacency (unused by persist, handy for
      tests and future skills).
    """
    by_desc: dict[str, ClassDecl] = {}
    children: dict[str, set[str]] = defaultdict(set)
    edges: list[HierarchyEdge] = []

    for c in classes:
        by_desc[c.class_desc] = c  # defensive last-wins; apktool doesn't dup

    for c in classes:
        if c.super_desc:
            # Record extends for the adjacency list regardless of Object;
            # only skip adding to ``children`` for Object — virtual dispatch
            # never meaningfully walks through it in a call graph.
            if c.super_desc != "Ljava/lang/Object;":
                children[c.super_desc].add(c.class_desc)
            edges.append(HierarchyEdge(c.class_desc, c.super_desc, "extends"))
        for iface in c.interfaces:
            children[iface].add(c.class_desc)
            edges.append(HierarchyEdge(c.class_desc, iface, "implements"))

    return by_desc, dict(children), edges


def _method_index(
    by_desc: dict[str, ClassDecl],
) -> dict[str, dict[tuple[str, str, str], MethodDecl]]:
    """For each class, map ``(name, params, ret)`` → :class:`MethodDecl`."""
    out: dict[str, dict[tuple[str, str, str], MethodDecl]] = {}
    for desc, cls in by_desc.items():
        bucket: dict[tuple[str, str, str], MethodDecl] = {}
        for m in cls.methods:
            bucket[(m.name, m.params, m.ret)] = m
        out[desc] = bucket
    return out


def _bfs_overrides(
    invoke: InvokeRecord,
    children: dict[str, set[str]],
    method_idx: dict[str, dict[tuple[str, str, str], MethodDecl]],
) -> tuple[list[str], bool]:
    """BFS the subclass / implementor tree for same-signature overrides.

    Always includes the static target (as a synthetic signature when the
    static owner isn't in the parsed class set). Returns
    ``(target_sigs, truncated)``.
    """
    static_owner = invoke.target_owner
    sig_key = (invoke.target_name, invoke.target_params, invoke.target_ret)
    static_sig = invoke.target_static_sig

    targets: list[str] = []
    seen: set[str] = set()

    # Seed with the static target. If the owner is in-app and declares
    # the method itself, use the concrete MethodDecl's signature (same
    # bytes — kept explicit for clarity).
    static_decl = method_idx.get(static_owner, {}).get(sig_key)
    if static_decl is not None:
        targets.append(static_decl.signature)
        seen.add(static_decl.signature)
    else:
        targets.append(static_sig)
        seen.add(static_sig)

    queue: deque[str] = deque([static_owner])
    visited_classes: set[str] = {static_owner}
    truncated = False
    while queue:
        cls = queue.popleft()
        for child in children.get(cls, ()):
            if child in visited_classes:
                continue
            visited_classes.add(child)
            md = method_idx.get(child, {}).get(sig_key)
            if md is not None and md.signature not in seen:
                if len(targets) >= MAX_OVERRIDES_PER_INVOKE:
                    truncated = True
                    break
                targets.append(md.signature)
                seen.add(md.signature)
            queue.append(child)
        if truncated:
            break
    return targets, truncated


def _is_external(owner_desc: str, by_desc: dict[str, ClassDecl]) -> bool:
    """True when ``owner_desc`` is not declared in the parsed class set."""
    return owner_desc not in by_desc


def _owner_of(sig: str) -> str:
    """Return the class descriptor from a method signature.

    ``Lcom/example/Foo;->bar(I)V`` → ``Lcom/example/Foo;``. Defensive:
    returns the full signature back if the separator is missing.
    """
    idx = sig.find(";->")
    if idx < 0:
        return sig
    return sig[: idx + 1]


def _edge_kind(invoke_kind: str, dst_is_external: bool) -> str:
    """Pick the final edge-kind per the locked taxonomy.

    Destination-level externality wins over opcode kind — a call to
    ``Landroid/util/Log;->d`` is rendered muted as ``external`` regardless
    of whether the bytecode said ``invoke-static`` or ``invoke-virtual``.
    """
    if dst_is_external:
        return KIND_EXTERNAL
    if invoke_kind == "direct":
        return KIND_DIRECT
    if invoke_kind == "static":
        return KIND_STATIC
    if invoke_kind == "super":
        return KIND_SUPER
    if invoke_kind == "virtual":
        return KIND_VIRTUAL_DISPATCH
    if invoke_kind == "interface":
        return KIND_INTERFACE_DISPATCH
    # polymorphic / custom land here — treat as external since the real
    # target is resolved at runtime by the JVM bootstrap method.
    return KIND_EXTERNAL


def resolve_invokes(
    classes: list[ClassDecl],
    invokes: list[InvokeRecord],
) -> tuple[list[ResolvedEdge], list[HierarchyEdge], set[str]]:
    """Top-level entry — apply dispatch rules to every parsed invoke.

    Returns:

    * ``edges`` — ready for SQLite insert.
    * ``hierarchy`` — flat adjacency (not strictly needed by persist
      because the plan stores parents on ``classes`` + interfaces on
      ``class_interfaces``, but kept for tests and future skills).
    * ``external_targets`` — method signatures whose owner isn't in the
      parsed class set. Persist layer materialises these as
      ``is_external=1`` ``nodes`` rows.
    """
    by_desc, children, hierarchy = build_hierarchy(classes)
    method_idx = _method_index(by_desc)
    edges: list[ResolvedEdge] = []
    external_targets: set[str] = set()

    for inv in invokes:
        invoke_op = f"invoke-{inv.kind}"

        if inv.kind in ("direct", "static", "super"):
            dst_sig = inv.target_static_sig
            dst_external = _is_external(inv.target_owner, by_desc)
            kind = _edge_kind(inv.kind, dst_external)
            edges.append(
                ResolvedEdge(
                    src_method_sig=inv.src_method_sig,
                    dst_method_sig=dst_sig,
                    kind=kind,
                    src_file=inv.src_file,
                    src_line=inv.src_line,
                    invoke_op=invoke_op,
                    truncated=False,
                )
            )
            if dst_external:
                external_targets.add(dst_sig)
            continue

        if inv.kind in ("virtual", "interface"):
            targets, truncated = _bfs_overrides(inv, children, method_idx)
            for t in targets:
                dst_external = _is_external(_owner_of(t), by_desc)
                kind = _edge_kind(inv.kind, dst_external)
                edges.append(
                    ResolvedEdge(
                        src_method_sig=inv.src_method_sig,
                        dst_method_sig=t,
                        kind=kind,
                        src_file=inv.src_file,
                        src_line=inv.src_line,
                        invoke_op=invoke_op,
                        truncated=truncated,
                    )
                )
                if dst_external:
                    external_targets.add(t)
            continue

        # invoke-polymorphic / invoke-custom — single external edge.
        dst_sig = inv.target_static_sig
        dst_external = _is_external(inv.target_owner, by_desc)
        edges.append(
            ResolvedEdge(
                src_method_sig=inv.src_method_sig,
                dst_method_sig=dst_sig,
                kind=_edge_kind(inv.kind, dst_external),
                src_file=inv.src_file,
                src_line=inv.src_line,
                invoke_op=invoke_op,
                truncated=False,
            )
        )
        if dst_external:
            external_targets.add(dst_sig)

    return edges, hierarchy, external_targets
