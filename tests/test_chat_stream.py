"""Tests for the SSE chat streaming path (`POST /api/chat/stream`).

Covers:
  * Frame ordering (thinking deltas first, then content deltas, then ``done``).
  * Validation failures land as ``event: error`` on an HTTP 200 stream.
  * Rate limit failures land as ``event: error`` with retry_after_seconds.
  * Streamer exceptions are turned into a terminal ``event: error`` frame.
  * Transcript JSONL is written with ``streamed: true`` after a successful turn.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any, Callable

import pytest
from fastapi.testclient import TestClient

from androscan.config import Config
from androscan.web import chat as chat_module
from androscan.web.app import create_app
from androscan.web.chat import stream_chat_request


# ---------------------------------------------------------------------------
# Helpers


class FakeResult:
    def __init__(self, content: str = "", thinking: str = "", done_reason: str = "stop") -> None:
        self.content = content
        self.thinking = thinking
        self.metadata = {"done_reason": done_reason}


def parse_sse(blob: bytes | str) -> list[tuple[str, dict[str, Any]]]:
    """Return [(event, data_json), ...] in arrival order."""
    text = blob.decode("utf-8") if isinstance(blob, (bytes, bytearray)) else blob
    out: list[tuple[str, dict[str, Any]]] = []
    for raw_frame in text.split("\n\n"):
        if not raw_frame.strip():
            continue
        event = "message"
        data_lines: list[str] = []
        for line in raw_frame.split("\n"):
            if not line or line.startswith(":"):
                continue
            field, _, value = line.partition(":")
            if value.startswith(" "):
                value = value[1:]
            if field == "event":
                event = value
            elif field == "data":
                data_lines.append(value)
        try:
            payload = json.loads("\n".join(data_lines))
        except json.JSONDecodeError:
            payload = {}
        out.append((event, payload))
    return out


async def _drain(gen) -> bytes:
    chunks: list[bytes] = []
    async for c in gen:
        chunks.append(c)
    return b"".join(chunks)


def _make_streamer(
    thinking_chunks: list[str],
    content_chunks: list[str],
    *,
    raise_exc: BaseException | None = None,
) -> Callable[..., FakeResult]:
    """Build a streamer that fires the given deltas in order then returns."""

    def streamer(*, config: Any, messages: Any, on_token=None, on_thinking=None, response_format=None) -> FakeResult:
        for t in thinking_chunks:
            if on_thinking:
                on_thinking(t)
        for c in content_chunks:
            if on_token:
                on_token(c)
        if raise_exc is not None:
            raise raise_exc
        return FakeResult(
            content="".join(content_chunks),
            thinking="".join(thinking_chunks),
        )

    return streamer


# ---------------------------------------------------------------------------
# Direct (no HTTP) tests of the async generator


def test_stream_emits_thinking_then_content_then_done(tmp_path: Path) -> None:
    root = tmp_path / "apps"
    (root / "myapp" / "01-jan-26_12-00-00").mkdir(parents=True)

    streamer = _make_streamer(["I am ", "thinking…"], ["Hello, ", "world!"])

    body = {
        "tab": "reports",
        "prompt": "hi",
        "app_id": "myapp",
        "run_ts": "01-jan-26_12-00-00",
    }
    blob = asyncio.run(_drain(stream_chat_request(body, Config.default(), root, streamer=streamer)))
    events = parse_sse(blob)

    # Thinking deltas first (in order), then content deltas, then exactly one ``done``.
    kinds = [k for k, _ in events]
    assert kinds.count("done") == 1
    assert kinds[-1] == "done"
    assert "error" not in kinds

    thinking = [d["delta"] for k, d in events if k == "thinking"]
    content = [d["delta"] for k, d in events if k == "content"]
    assert thinking == ["I am ", "thinking…"]
    assert content == ["Hello, ", "world!"]

    done = events[-1][1]
    assert done["thinking_chars"] == len("I am thinking…")
    assert done["content_chars"] == len("Hello, world!")
    assert done["done_reason"] == "stop"
    assert done["transcript_path"]  # appended for valid app_id/run_ts


def test_stream_persists_transcript_with_streamed_flag(tmp_path: Path) -> None:
    root = tmp_path / "apps"
    (root / "myapp" / "01-jan-26_12-00-00").mkdir(parents=True)

    streamer = _make_streamer([], ["ok"])
    asyncio.run(_drain(stream_chat_request(
        {"tab": "reports", "prompt": "hi", "app_id": "myapp", "run_ts": "01-jan-26_12-00-00"},
        Config.default(), root, streamer=streamer,
    )))

    log = (root / "myapp" / "01-jan-26_12-00-00" / "chat" / "reports.jsonl").read_text()
    record = json.loads(log.strip().splitlines()[-1])
    assert record["streamed"] is True
    assert record["tab"] == "reports"
    assert record["reply_chars"] == 2
    assert record["thinking_chars"] == 0


def test_stream_validation_emits_error_only(tmp_path: Path) -> None:
    root = tmp_path / "apps"
    root.mkdir()

    blob = asyncio.run(_drain(stream_chat_request(
        {"tab": "elsewhere", "prompt": "hi"}, Config.default(), root,
        streamer=lambda **_: FakeResult("never called"),
    )))
    events = parse_sse(blob)
    assert [k for k, _ in events] == ["error"]
    assert "tab" in events[0][1]["error"]


def test_stream_rate_limit_emits_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "apps"
    (root / "myapp" / "01-jan-26_12-00-00").mkdir(parents=True)
    monkeypatch.setattr(chat_module, "_RATE_LIMITER", chat_module._TabRateLimiter(1))

    streamer = _make_streamer([], ["ok"])
    base = time.monotonic()
    body = {"tab": "reports", "prompt": "hi", "app_id": "myapp", "run_ts": "01-jan-26_12-00-00"}

    # First call succeeds, second is rate-limited.
    e1 = parse_sse(asyncio.run(_drain(
        stream_chat_request(body, Config.default(), root, streamer=streamer, now=base)
    )))
    assert "done" in [k for k, _ in e1]

    e2 = parse_sse(asyncio.run(_drain(
        stream_chat_request(body, Config.default(), root, streamer=streamer, now=base + 0.05)
    )))
    assert [k for k, _ in e2] == ["error"]
    assert "rate limit" in e2[0][1]["error"]
    assert isinstance(e2[0][1].get("retry_after_seconds"), int)


def test_stream_streamer_exception_becomes_error_frame(tmp_path: Path) -> None:
    root = tmp_path / "apps"
    (root / "myapp" / "01-jan-26_12-00-00").mkdir(parents=True)

    # Emit one content chunk before the exception so we also assert that
    # already-streamed deltas are preserved on the way to the error frame.
    streamer = _make_streamer([], ["partial"], raise_exc=RuntimeError("ollama down"))

    blob = asyncio.run(_drain(stream_chat_request(
        {"tab": "reports", "prompt": "hi", "app_id": "myapp", "run_ts": "01-jan-26_12-00-00"},
        Config.default(), root, streamer=streamer,
    )))
    events = parse_sse(blob)
    kinds = [k for k, _ in events]
    assert "content" in kinds
    assert kinds[-1] == "error"
    assert "ollama down" in events[-1][1]["error"]


# ---------------------------------------------------------------------------
# FastAPI route wiring


def test_chat_stream_route_returns_event_stream(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "apps" / "myapp" / "01-jan-26_12-00-00").mkdir(parents=True)

    streamer = _make_streamer(["thinking "], ["answer"])
    # Patch the lazy import inside stream_chat_request so the route uses our fake.
    import androscan.llm.client as llm_client
    monkeypatch.setattr(llm_client, "stream_complete", streamer)

    app = create_app(Config.default(), cwd=tmp_path)
    client = TestClient(app)
    with client.stream(
        "POST", "/api/chat/stream",
        json={
            "tab": "reports",
            "prompt": "hi",
            "history": [],
            "attachments": [],
            "app_id": "myapp",
            "run_ts": "01-jan-26_12-00-00",
        },
    ) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        body = b"".join(r.iter_bytes())

    events = parse_sse(body)
    kinds = [k for k, _ in events]
    assert kinds[-1] == "done"
    assert any(k == "thinking" for k in kinds)
    assert any(k == "content" for k in kinds)


# ---------------------------------------------------------------------------
# Hook Lab → Lab tab rename + back-compat alias (Phase 10 sub-step 10.6).
#
# The Hook Lab tab was renamed to ``Lab`` in 10.6. The chat layer accepts
# both ids (``"hook"`` is the legacy alias) — every request is routed
# through ``_normalise_tab`` so the system prompt lookup, the rate-limit
# bucket, and the transcript filename all collapse onto the canonical
# ``"lab"`` regardless of which alias the client sent. Pinning the
# round-trip here so a future rename can't accidentally split the alias's
# state across two buckets.


def test_lab_tab_writes_canonical_transcript_filename(tmp_path: Path) -> None:
    """A request with ``tab="lab"`` writes ``chat/lab.jsonl`` — the
    canonical filename going forward."""
    root = tmp_path / "apps"
    (root / "myapp" / "01-jan-26_12-00-00").mkdir(parents=True)

    streamer = _make_streamer([], ["ok"])
    asyncio.run(_drain(stream_chat_request(
        {"tab": "lab", "prompt": "hi", "app_id": "myapp", "run_ts": "01-jan-26_12-00-00"},
        Config.default(), root, streamer=streamer,
    )))

    log = (root / "myapp" / "01-jan-26_12-00-00" / "chat" / "lab.jsonl").read_text()
    record = json.loads(log.strip().splitlines()[-1])
    assert record["tab"] == "lab"
    assert record["reply_chars"] == 2


def test_legacy_hook_tab_is_aliased_to_lab(tmp_path: Path) -> None:
    """Legacy ``tab="hook"`` requests still validate, route to the
    same system prompt, and append to the ``chat/lab.jsonl`` file
    (NOT a stale ``hook.jsonl``) so an upgraded workspace doesn't end
    up with split transcripts. The ``record["tab"]`` written to disk
    is also normalised — chat history readers see one canonical id."""
    root = tmp_path / "apps"
    (root / "myapp" / "01-jan-26_12-00-00").mkdir(parents=True)

    streamer = _make_streamer([], ["legacy ok"])
    asyncio.run(_drain(stream_chat_request(
        {"tab": "hook", "prompt": "hi", "app_id": "myapp", "run_ts": "01-jan-26_12-00-00"},
        Config.default(), root, streamer=streamer,
    )))

    chat_dir = root / "myapp" / "01-jan-26_12-00-00" / "chat"
    assert (chat_dir / "lab.jsonl").is_file(), "alias must write to canonical filename"
    assert not (chat_dir / "hook.jsonl").is_file(), "no stale alias filename should appear"
    record = json.loads((chat_dir / "lab.jsonl").read_text().strip().splitlines()[-1])
    assert record["tab"] == "lab"


def test_lab_and_hook_share_the_same_system_prompt() -> None:
    """The Frida-instrumentation system prompt is keyed under ``"lab"``;
    the alias collapse means asking for ``"hook"`` returns the same
    prompt verbatim. Pin so a future copy-edit of the lab prompt
    doesn't accidentally only update the alias path."""
    assert chat_module.system_prompt_for("lab") == chat_module.system_prompt_for("hook")
    # And distinct from the other tabs (sanity).
    assert chat_module.system_prompt_for("lab") != chat_module.system_prompt_for("reports")


def test_lab_and_hook_share_the_same_rate_limit_bucket(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A burst that mixes ``hook`` + ``lab`` tab ids must consume the
    same rate-limit bucket — otherwise a malicious / buggy client
    could double its quota by alternating aliases."""
    root = tmp_path / "apps"
    (root / "myapp" / "01-jan-26_12-00-00").mkdir(parents=True)
    monkeypatch.setattr(chat_module, "_RATE_LIMITER", chat_module._TabRateLimiter(1))

    streamer = _make_streamer([], ["ok"])
    base = time.monotonic()
    body_lab = {"tab": "lab", "prompt": "hi", "app_id": "myapp", "run_ts": "01-jan-26_12-00-00"}
    body_hook = {"tab": "hook", "prompt": "hi", "app_id": "myapp", "run_ts": "01-jan-26_12-00-00"}

    e1 = parse_sse(asyncio.run(_drain(
        stream_chat_request(body_lab, Config.default(), root, streamer=streamer, now=base)
    )))
    assert "done" in [k for k, _ in e1]

    # Second call uses the legacy alias — the bucket is shared so it
    # must trip the rate limiter (1 turn/min).
    e2 = parse_sse(asyncio.run(_drain(
        stream_chat_request(body_hook, Config.default(), root, streamer=streamer, now=base + 0.05)
    )))
    assert [k for k, _ in e2] == ["error"]
    assert "rate limit" in e2[0][1]["error"]
