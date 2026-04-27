"""Smali descriptor <-> Java-name helpers used by the call-graph persist layer.

All helpers are pure functions so they're trivially unit-testable and
can be reused by future sub-steps (4.5 LLM skill, 4.7 ``query_call_graph``)
without pulling in the full parser.

Vocabulary:

* **Descriptor**: the JVM internal form used in smali/DEX, e.g.
  ``Lcom/example/Foo;``, ``[B``, ``I``, ``V``.
* **Java name**: the dotted human-readable form, e.g. ``com.example.Foo``,
  ``byte[]``, ``int``, ``void``.

Edge cases handled:

* Array levels (``[``, ``[[``, ``[[[``) — recurse unambiguously.
* Inner classes (``Lcom/example/Foo$Inner;``) — we keep the ``$`` in the
  Java name as ``com.example.Foo$Inner`` (matches ``Class.getName()``).
* Primitives (``V`` → ``void``, ``Z`` → ``boolean``, ``B`` → ``byte``,
  ``S`` → ``short``, ``C`` → ``char``, ``I`` → ``int``, ``J`` → ``long``,
  ``F`` → ``float``, ``D`` → ``double``).
"""

from __future__ import annotations

from typing import Iterable


_PRIMITIVE_NAMES: dict[str, str] = {
    "V": "void",
    "Z": "boolean",
    "B": "byte",
    "S": "short",
    "C": "char",
    "I": "int",
    "J": "long",
    "F": "float",
    "D": "double",
}


# Access-flag bits matching the JVM spec. We set these from smali flag
# tokens so that the ``access_flags`` column exposes the same integer a
# reader would get from reflection on the original class file.
_ACCESS_BITS: dict[str, int] = {
    "public":       0x0001,
    "private":      0x0002,
    "protected":    0x0004,
    "static":       0x0008,
    "final":        0x0010,
    "synchronized": 0x0020,
    "volatile":     0x0040,
    "bridge":       0x0040,  # ACC_BRIDGE shares bit 0x40 on methods
    "transient":    0x0080,
    "varargs":      0x0080,  # ACC_VARARGS shares bit 0x80 on methods
    "native":       0x0100,
    "interface":    0x0200,
    "abstract":     0x0400,
    "strict":       0x0800,
    "synthetic":    0x1000,
    "annotation":   0x2000,
    "enum":         0x4000,
    "constructor":  0x10000,  # DEX extension — not standard JVM
    "declared-synchronized": 0x20000,
}


def descriptor_to_java(desc: str) -> str:
    """Convert a single type descriptor to its Java name.

    ``Lcom/example/Foo;`` → ``com.example.Foo``
    ``[B`` → ``byte[]``
    ``[[Ljava/lang/String;`` → ``java.lang.String[][]``
    ``V`` → ``void``
    """
    if not desc:
        return ""
    arr = 0
    while desc.startswith("["):
        arr += 1
        desc = desc[1:]
    if desc.startswith("L") and desc.endswith(";"):
        base = desc[1:-1].replace("/", ".")
    else:
        base = _PRIMITIVE_NAMES.get(desc, desc)
    return base + ("[]" * arr)


def class_desc_to_java(class_desc: str) -> str:
    """``Lcom/example/Foo;`` → ``com.example.Foo``. Arrays should use
    :func:`descriptor_to_java`; this helper is for class descriptors
    specifically (the ``classes.class_name`` column).
    """
    if class_desc.startswith("L") and class_desc.endswith(";"):
        return class_desc[1:-1].replace("/", ".")
    # Fallback for malformed input — don't crash, surface raw.
    return class_desc


def split_class_name(java_class_name: str) -> tuple[str, str]:
    """Split ``com.example.Foo$Inner`` into ``("com.example", "Foo$Inner")``.

    Returns ``("", <name>)`` for the default package and for malformed input.
    """
    idx = java_class_name.rfind(".")
    if idx < 0:
        return "", java_class_name
    return java_class_name[:idx], java_class_name[idx + 1:]


def parse_params(params: str) -> list[str]:
    """Split a concatenated smali parameter list into descriptors.

    ``ILjava/lang/String;[B`` → ``["I", "Ljava/lang/String;", "[B"]``.
    Returns ``[]`` for an empty parameter list. Defensive: unterminated
    ``L...`` tokens stop the walk rather than hanging the parser.
    """
    out: list[str] = []
    i = 0
    n = len(params)
    while i < n:
        start = i
        while i < n and params[i] == "[":
            i += 1
        if i >= n:
            break
        c = params[i]
        if c == "L":
            end = params.find(";", i)
            if end < 0:
                break
            out.append(params[start:end + 1])
            i = end + 1
        else:
            out.append(params[start:i + 1])
            i += 1
    return out


def params_to_java(params: str) -> list[str]:
    """Return the human-readable Java names of every parameter.

    ``ILjava/lang/String;`` → ``["int", "java.lang.String"]``.
    """
    return [descriptor_to_java(p) for p in parse_params(params)]


def compute_access_flags(tokens: Iterable[str]) -> int:
    """Fold a sequence of smali flag tokens (``public``, ``static``, ...)
    into a JVM ``access_flags`` integer. Unknown tokens are ignored.
    """
    out = 0
    for t in tokens:
        out |= _ACCESS_BITS.get(t, 0)
    return out


def method_descriptor(params: str, ret: str) -> str:
    """``(params)ret`` — reconstruct the JVM method descriptor from parts."""
    return f"({params}){ret}"
