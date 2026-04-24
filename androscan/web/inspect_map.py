"""Map a (mirror x, y) tap to the most likely Android UI element + code handler.

Pipeline:

1. ``adb shell dumpsys activity top | grep ACTIVITY`` — current foreground
   activity (helps the UI surface which class to look at first).
2. ``adb exec-out uiautomator dump /dev/tty`` — current view hierarchy XML.
3. Walk the XML and find the deepest ``<node>`` whose ``bounds`` contain
   (x, y). Extract ``resource-id``, ``text``, ``content-desc``, ``class``.
4. Grep the bulk-decompiled source tree for handler candidates referencing
   that resource id (or its short name).

Handler search is **regex-based, not AST-based**. We look for:

- ``R.id.<short>`` references (any file)
- ``findViewById(R.id.<short>)`` (likely the inflater)
- ``onClick``/``setOnClickListener`` near such references (within ~20 lines)
- String literal ``"<short>"`` matches (Compose-style)

Each candidate carries a small snippet for chat context. We cap the result
list so prompt budget stays bounded.
"""

from __future__ import annotations

import asyncio
import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Optional

# ---------------------------------------------------------------------------
# Element extraction


@dataclass
class Element:
    bounds: tuple[int, int, int, int]  # left, top, right, bottom
    cls: str
    resource_id: str
    text: str
    content_desc: str
    package: str
    clickable: bool
    enabled: bool

    def short_resource_id(self) -> str:
        if "/" in self.resource_id:
            return self.resource_id.rsplit("/", 1)[1]
        return self.resource_id


_BOUNDS_RE = re.compile(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]")


def _parse_bounds(s: str) -> Optional[tuple[int, int, int, int]]:
    m = _BOUNDS_RE.match(s or "")
    if not m:
        return None
    return tuple(int(v) for v in m.groups())  # type: ignore[return-value]


def _node_to_element(node: ET.Element) -> Optional[Element]:
    bounds = _parse_bounds(node.attrib.get("bounds", ""))
    if not bounds:
        return None
    return Element(
        bounds=bounds,
        cls=node.attrib.get("class", ""),
        resource_id=node.attrib.get("resource-id", ""),
        text=node.attrib.get("text", ""),
        content_desc=node.attrib.get("content-desc", ""),
        package=node.attrib.get("package", ""),
        clickable=node.attrib.get("clickable", "false") == "true",
        enabled=node.attrib.get("enabled", "false") == "true",
    )


def _contains(b: tuple[int, int, int, int], x: int, y: int) -> bool:
    return b[0] <= x <= b[2] and b[1] <= y <= b[3]


def _area(b: tuple[int, int, int, int]) -> int:
    return max(0, b[2] - b[0]) * max(0, b[3] - b[1])


def find_element_at(uiautomator_xml: str, x: int, y: int) -> Optional[Element]:
    """Pick the smallest-area node containing (x, y).

    Prefers clickable nodes when ties exist (smallest area among clickables;
    otherwise smallest area overall).
    """
    try:
        root = ET.fromstring(uiautomator_xml)
    except ET.ParseError:
        return None
    matches: list[Element] = []
    for node in root.iter("node"):
        el = _node_to_element(node)
        if el and _contains(el.bounds, x, y):
            matches.append(el)
    if not matches:
        return None
    clickables = [e for e in matches if e.clickable]
    pool = clickables or matches
    return min(pool, key=lambda e: _area(e.bounds))


# ---------------------------------------------------------------------------
# Handler grep
#
# We use Python's ``re`` over the on-disk decompiled tree. For the WeakBank
# class of apps this completes in < 100 ms; for a 10k-class corpus it's still
# << 1 s. If that ever becomes a hotspot, swap in ripgrep.


@dataclass
class Candidate:
    file: str  # relative path under sources_dir
    line: int
    snippet: str
    kind: str  # "findViewById" | "compose_id" | "onClick_near" | "reference"


_FINDVIEW_RE_TMPL = r"findViewById\s*\(\s*R\.id\.{name}\s*\)"
_RID_RE_TMPL = r"R\.id\.{name}\b"
_COMPOSE_RE_TMPL = r'"{name}"'
_ONCLICK_NEAR_RE = re.compile(
    r"\b(?:setOnClickListener|onClick|onLongClick|setOnLongClickListener)\b"
)


def _snippet(lines: list[str], idx: int, before: int = 1, after: int = 2) -> str:
    lo = max(0, idx - before)
    hi = min(len(lines), idx + after + 1)
    return "\n".join(lines[lo:hi]).rstrip()


def find_handlers(
    sources_dir: Path,
    short_resource_id: str,
    *,
    max_files: int = 40,
    max_per_file: int = 4,
    max_total: int = 25,
) -> list[Candidate]:
    """Grep decompiled sources for handler candidates referencing the id.

    Returns at most ``max_total`` candidates, prioritising ``findViewById``
    > ``onClick_near`` > ``compose_id`` > ``reference``.
    """
    if not short_resource_id or not short_resource_id.replace("_", "").isalnum():
        return []
    if not sources_dir.is_dir():
        return []

    name = re.escape(short_resource_id)
    rx_findview = re.compile(_FINDVIEW_RE_TMPL.format(name=name))
    rx_rid = re.compile(_RID_RE_TMPL.format(name=name))
    rx_compose = re.compile(_COMPOSE_RE_TMPL.format(name=name))

    candidates: list[Candidate] = []
    files_seen = 0

    for p in sorted(sources_dir.rglob("*")):
        if not p.is_file() or p.suffix not in (".java", ".kt"):
            continue
        # R.java itself is the symbol table — not a handler. Skip it.
        if p.name == "R.java" or "/R$" in str(p) or p.name.startswith("R$"):
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        # Cheap pre-filter so we don't tokenise files that don't mention us.
        if short_resource_id not in text:
            continue
        files_seen += 1
        if files_seen > max_files:
            break
        rel = str(p.relative_to(sources_dir))
        lines = text.splitlines()

        per_file = 0
        for i, line in enumerate(lines):
            if per_file >= max_per_file:
                break
            kind: Optional[str] = None
            if rx_findview.search(line):
                kind = "findViewById"
            elif rx_rid.search(line):
                # Look ahead/behind for an onClick-ish call within 20 lines.
                window = "\n".join(lines[max(0, i - 10): i + 10])
                kind = "onClick_near" if _ONCLICK_NEAR_RE.search(window) else "reference"
            elif rx_compose.search(line):
                kind = "compose_id"
            if kind is None:
                continue
            candidates.append(Candidate(
                file=rel,
                line=i + 1,
                snippet=_snippet(lines, i),
                kind=kind,
            ))
            per_file += 1

    priority = {"findViewById": 0, "onClick_near": 1, "compose_id": 2, "reference": 3}
    candidates.sort(key=lambda c: (priority.get(c.kind, 9), c.file, c.line))
    return candidates[:max_total]


# ---------------------------------------------------------------------------
# adb glue (async; injectable for tests)


_FOREGROUND_RE = re.compile(r"ACTIVITY\s+([\w.]+)/(\.?[\w.$]+)")

AdbRunner = Callable[..., "asyncio.Future[tuple[int, bytes, bytes]]"]


async def _run_adb(*args: str) -> tuple[int, bytes, bytes]:
    proc = await asyncio.create_subprocess_exec(
        "adb", *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    return int(proc.returncode or 0), out, err


async def get_foreground_activity(runner: Optional[AdbRunner] = None) -> Optional[str]:
    """Return ``com.example.app/.SomeActivity`` for the focused activity, or None."""
    run = runner or _run_adb
    code, out, _ = await run("shell", "dumpsys", "activity", "top")
    if code != 0:
        return None
    text = out.decode(errors="replace")
    matches = list(_FOREGROUND_RE.finditer(text))
    if not matches:
        return None
    pkg, cls = matches[-1].group(1), matches[-1].group(2)
    if cls.startswith("."):
        cls = pkg + cls
    return f"{pkg}/{cls}"


async def dump_ui_xml(runner: Optional[AdbRunner] = None) -> Optional[str]:
    """Stream uiautomator dump straight to stdout (no /sdcard write)."""
    run = runner or _run_adb
    code, out, _ = await run("exec-out", "uiautomator", "dump", "/dev/tty")
    if code != 0 or not out:
        return None
    text = out.decode(errors="replace")
    # Strip the trailing "UI hierarchy dumped to: ..." footer if present.
    idx = text.rfind("</hierarchy>")
    if idx >= 0:
        return text[: idx + len("</hierarchy>")]
    return text if text.lstrip().startswith("<") else None


# ---------------------------------------------------------------------------
# Public coordinator


async def map_tap_to_code(
    *,
    app_dir: Path,
    sha: Optional[str],
    sources_dir: Path,
    x: int,
    y: int,
    runner: Optional[AdbRunner] = None,
) -> dict[str, Any]:
    """Run the full map pipeline. Returns a JSON-friendly dict."""
    foreground = await get_foreground_activity(runner=runner)
    xml = await dump_ui_xml(runner=runner)
    element: Optional[Element] = find_element_at(xml or "", x, y) if xml else None

    candidates: list[Candidate] = []
    short = element.short_resource_id() if element else ""
    if short:
        candidates = find_handlers(sources_dir, short)

    return {
        "x": x,
        "y": y,
        "sha": sha,
        "foreground_activity": foreground,
        "element": asdict(element) if element else None,
        "short_resource_id": short or None,
        "candidates": [asdict(c) for c in candidates],
        "ui_dump_ok": xml is not None,
    }
