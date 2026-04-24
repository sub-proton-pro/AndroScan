"""Tests for inspect_map: element pick + handler grep + adb glue."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from androscan.web import inspect_map as im

UI_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes'?>
<hierarchy rotation="0">
  <node bounds="[0,0][1080,1920]" class="android.widget.FrameLayout" package="com.example.app" clickable="false" enabled="true" resource-id="" text="" content-desc="">
    <node bounds="[40,200][1040,400]" class="android.widget.LinearLayout" package="com.example.app" clickable="false" enabled="true" resource-id="" text="" content-desc="">
      <node bounds="[100,250][500,350]" class="android.widget.TextView" package="com.example.app" clickable="false" enabled="true" resource-id="com.example.app:id/title" text="Hi" content-desc=""/>
      <node bounds="[600,250][900,350]" class="android.widget.Button" package="com.example.app" clickable="true" enabled="true" resource-id="com.example.app:id/btn_login" text="Login" content-desc=""/>
    </node>
  </node>
</hierarchy>
"""


def test_find_element_at_picks_button() -> None:
    el = im.find_element_at(UI_XML, 700, 300)
    assert el is not None
    assert el.short_resource_id() == "btn_login"
    assert el.clickable is True


def test_find_element_at_prefers_clickable_over_container() -> None:
    el = im.find_element_at(UI_XML, 200, 300)  # over title (not clickable)
    assert el is not None
    assert el.short_resource_id() == "title"


def test_find_element_at_returns_none_for_outside_tap() -> None:
    assert im.find_element_at(UI_XML, 5000, 5000) is None


def test_find_element_at_invalid_xml() -> None:
    assert im.find_element_at("not xml", 1, 1) is None


# ---------------------------------------------------------------------------
# Handler grep


def _write_handler(tmp_path: Path) -> Path:
    src = tmp_path / "sources"
    pkg_dir = src / "com" / "example" / "app"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "MainActivity.java").write_text(
        """package com.example.app;
public class MainActivity {
  void onCreate() {
    Button b = (Button) findViewById(R.id.btn_login);
    b.setOnClickListener(new View.OnClickListener() {
      public void onClick(View v) { doLogin(); }
    });
    TextView t = findViewById(R.id.title);
  }
  void other() { int x = R.id.btn_login; }
}
""",
        encoding="utf-8",
    )
    (pkg_dir / "R.java").write_text(
        "package com.example.app;\npublic final class R {\n  public static final class id { public static final int btn_login = 0x7f000001; }\n}\n",
        encoding="utf-8",
    )
    return src


def test_find_handlers_prioritises_findViewById(tmp_path: Path) -> None:
    src = _write_handler(tmp_path)
    cands = im.find_handlers(src, "btn_login")
    assert cands, "expected at least one candidate"
    assert cands[0].kind == "findViewById"
    assert "MainActivity.java" in cands[0].file
    # The bare reference should still be present, but lower priority.
    kinds = [c.kind for c in cands]
    assert "reference" in kinds or "onClick_near" in kinds


def test_find_handlers_skips_R_java(tmp_path: Path) -> None:
    src = _write_handler(tmp_path)
    cands = im.find_handlers(src, "btn_login")
    assert all("R.java" not in c.file for c in cands)


def test_find_handlers_rejects_unsafe_id(tmp_path: Path) -> None:
    src = _write_handler(tmp_path)
    assert im.find_handlers(src, "../../etc/passwd") == []
    assert im.find_handlers(src, "") == []


def test_find_handlers_no_sources_dir(tmp_path: Path) -> None:
    assert im.find_handlers(tmp_path / "nope", "btn_login") == []


# ---------------------------------------------------------------------------
# adb glue (runner injected)


class _FakeProc:
    def __init__(self, returncode: int, out: bytes = b"", err: bytes = b"") -> None:
        self.returncode = returncode
        self._out = out
        self._err = err

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._out, self._err


def test_get_foreground_activity_parses_dumpsys() -> None:
    sample = b"""    ACTIVITY com.example.app/.MainActivity 12345 pid=678
"""

    async def runner(*args):
        return 0, sample, b""

    out = asyncio.run(im.get_foreground_activity(runner=runner))
    assert out == "com.example.app/com.example.app.MainActivity"


def test_get_foreground_activity_handles_no_match() -> None:
    async def runner(*args):
        return 0, b"nothing here", b""

    assert asyncio.run(im.get_foreground_activity(runner=runner)) is None


def test_dump_ui_xml_strips_footer() -> None:
    payload = UI_XML.encode() + b"\nUI hierarchy dumped to: /dev/tty"

    async def runner(*args):
        return 0, payload, b""

    xml = asyncio.run(im.dump_ui_xml(runner=runner))
    assert xml is not None
    assert xml.endswith("</hierarchy>")


def test_map_tap_to_code_end_to_end(tmp_path: Path) -> None:
    src = _write_handler(tmp_path)

    async def runner(*args):
        if args[0] == "shell":  # dumpsys
            return 0, b"ACTIVITY com.example.app/.MainActivity 1\n", b""
        return 0, UI_XML.encode(), b""

    result = asyncio.run(
        im.map_tap_to_code(
            app_dir=tmp_path,
            sha="abc",
            sources_dir=src,
            x=700,
            y=300,
            runner=runner,
        )
    )
    assert result["element"]["resource_id"].endswith("btn_login")
    assert result["short_resource_id"] == "btn_login"
    assert result["candidates"][0]["kind"] == "findViewById"
    assert result["foreground_activity"].endswith("MainActivity")
