"""Tests for the SSE chat streaming path (`POST /api/chat/stream`).

Covers:
  * Frame ordering (thinking deltas first, then content deltas, then ``done``).
  * Validation failures land as ``event: error`` on an HTTP 200 stream.
  * Rate limit failures land as ``event: error`` with retry_after_seconds.
  * Streamer exceptions are turned into a terminal ``event: error`` frame.
  * Transcript JSONL is written with ``streamed: true`` after a successful turn.

Phase 11 v2.1 sub-step v2.1.5 — bounded agentic-skill loop tests
(DEC-022 + DEC-025 v2.1 closing-note Q7). When the request body
opts in via ``agentic_loop=true``, ``stream_chat_request`` dispatches
to ``_stream_chat_agentic_request`` which:

  * Runs up to ``MAX_AGENTIC_TURNS`` blocking JSON LLM calls.
  * Emits ``skill_request`` / ``skill_result`` / ``skill_pending`` /
    ``widget`` SSE events as skills fire mid-loop.
  * Halts gracefully on consent-class skills (chat consent UI is
    still pending — ISSUE-009).
  * Halts gracefully on turn-budget exhaustion.

The agentic-loop tests live below the single-pass tests so a future
maintainer can read the file top-down to understand the v1 path
before reading the v2.1.5 path.
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


# ---------------------------------------------------------------------------
# Phase 11 v2.1 sub-step v2.1.5 — Bounded agentic-skill loop tests
# (DEC-022 + DEC-025 v2.1 closing-note Q7).
#
# The v2.1.5 path opts in via ``body.agentic_loop=true``. We monkeypatch
# the lazy ``complete()`` import so each turn returns a fixed JSON
# response, and the lazy ``execute()`` import so we control which
# skills "succeed" / "fail" / "halt for consent". The frontend
# (``streamChat`` SSE consumer) is expected to handle ``skill_request``,
# ``skill_result``, ``skill_pending`` and ``widget`` event kinds — we
# pin each shape here so a regression in the wire format is caught.


class _FakeLLMResult:
    def __init__(self, content: str) -> None:
        self.content = content
        self.metadata = {"done_reason": "stop"}


def _seed_agentic_app(tmp_path: Path) -> tuple[Path, str, str]:
    root = tmp_path / "apps"
    app_id = "myapp"
    run_ts = "01-jan-26_12-00-00"
    (root / app_id / run_ts).mkdir(parents=True)
    return root, app_id, run_ts


def _agentic_body(app_id: str, run_ts: str, prompt: str = "trace the login flow") -> dict[str, Any]:
    return {
        "tab": "lab",
        "prompt": prompt,
        "history": [],
        "attachments": [],
        "app_id": app_id,
        "run_ts": run_ts,
        "agentic_loop": True,
    }


def test_agentic_loop_no_skills_emits_content_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the LLM emits content directly without any skill_requests,
    the loop terminates after one turn — only ``content`` + ``done``
    events fire, no ``skill_request`` / ``skill_result``."""
    root, app_id, run_ts = _seed_agentic_app(tmp_path)

    def fake_complete(prompt: str, **kwargs: Any) -> _FakeLLMResult:
        return _FakeLLMResult(json.dumps({
            "content": "I have enough info already. Here's the answer.",
        }))

    import androscan.llm.client as llm_client
    monkeypatch.setattr(llm_client, "complete", fake_complete)

    blob = asyncio.run(_drain(stream_chat_request(
        _agentic_body(app_id, run_ts), Config.default(), root,
    )))
    events = parse_sse(blob)
    kinds = [k for k, _ in events]

    assert kinds[-1] == "done"
    assert "skill_request" not in kinds
    assert "widget" not in kinds
    contents = [d["delta"] for k, d in events if k == "content"]
    assert any("here's the answer" in c.lower() for c in contents)
    done = events[-1][1]
    assert done.get("agentic") is True
    assert done.get("skill_calls") == 0
    assert done.get("widgets") == 0


def test_agentic_loop_skill_request_skill_result_widget_round_trip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Multi-turn flow: turn 0 asks for a skill, turn 1 returns final
    content. The skill result carries one widget. Wire format must
    surface skill_request → skill_result → widget → content → done
    in that order."""
    root, app_id, run_ts = _seed_agentic_app(tmp_path)

    turn_responses = [
        # Turn 0: request the suggest_trace_entry skill.
        json.dumps({
            "thinking": "Operator wants entry candidates; calling skill.",
            "skill_requests": [
                {"skill": "suggest_trace_entry",
                 "params": {"description": "login flow"}},
            ],
        }),
        # Turn 1: emit the final answer based on the skill result.
        json.dumps({
            "content": "Top candidate: Lcom/x/Login;->onClick(...)V",
        }),
    ]
    completer_calls = {"n": 0}

    def fake_complete(prompt: str, **kwargs: Any) -> _FakeLLMResult:
        idx = completer_calls["n"]
        completer_calls["n"] += 1
        return _FakeLLMResult(turn_responses[idx])

    from androscan.skills.base import (
        SkillResult,
        TraceEntryCandidateWidget,
    )

    fake_widget = TraceEntryCandidateWidget(
        smali_id="Lcom/x/Login;->onClick(Landroid/view/View;)V",
        rationale="Matches login UI handler",
        confidence=0.92,
    )

    def fake_execute(name: str, params: dict[str, Any], ctx: Any) -> SkillResult:
        assert name == "suggest_trace_entry"
        return SkillResult(
            success=True,
            text="[suggest_trace_entry] 1 candidate.",
            widgets=(fake_widget,),
        )

    import androscan.llm.client as llm_client
    monkeypatch.setattr(llm_client, "complete", fake_complete)
    import androscan.skills as skills_module
    monkeypatch.setattr(skills_module, "execute", fake_execute)

    blob = asyncio.run(_drain(stream_chat_request(
        _agentic_body(app_id, run_ts), Config.default(), root,
    )))
    events = parse_sse(blob)
    kinds = [k for k, _ in events]

    # Order discipline: skill_request precedes skill_result; widget
    # follows skill_result for that request_id; content is last
    # before done.
    sr_idx = kinds.index("skill_request")
    res_idx = kinds.index("skill_result")
    widget_idx = kinds.index("widget")
    content_idx = kinds.index("content")
    done_idx = kinds.index("done")
    assert sr_idx < res_idx < widget_idx < content_idx < done_idx

    # Skill-request payload
    sr = events[sr_idx][1]
    assert sr["skill"] == "suggest_trace_entry"
    assert sr["params"]["description"] == "login flow"
    assert sr.get("request_id")  # non-empty

    # Skill-result payload
    res = events[res_idx][1]
    assert res["skill"] == "suggest_trace_entry"
    assert res["success"] is True
    assert "1 candidate" in res["text"]
    assert res["request_id"] == sr["request_id"]

    # Widget payload
    w = events[widget_idx][1]
    assert w["skill"] == "suggest_trace_entry"
    assert w["request_id"] == sr["request_id"]
    payload = w["widget"]
    assert payload["kind"] == "trace_entry_candidate"
    assert payload["smali_id"] == "Lcom/x/Login;->onClick(Landroid/view/View;)V"
    assert payload["rationale"] == "Matches login UI handler"
    assert payload["confidence"] == 0.92

    # Done envelope reflects the agentic-loop counters.
    done = events[done_idx][1]
    assert done.get("agentic") is True
    assert done.get("skill_calls") == 1
    assert done.get("widgets") == 1


def test_agentic_loop_unknown_skill_surfaces_error_and_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM requests a skill that isn't registered — surfaces a
    skill_result with success=False but the loop continues so the
    LLM can self-correct on the next turn."""
    root, app_id, run_ts = _seed_agentic_app(tmp_path)

    turn_responses = [
        json.dumps({"skill_requests": [{"skill": "this_does_not_exist", "params": {}}]}),
        json.dumps({"content": "OK, I'll answer from the prompt directly."}),
    ]
    n = {"i": 0}

    def fake_complete(prompt: str, **kwargs: Any) -> _FakeLLMResult:
        r = turn_responses[n["i"]]
        n["i"] += 1
        return _FakeLLMResult(r)

    import androscan.llm.client as llm_client
    monkeypatch.setattr(llm_client, "complete", fake_complete)

    blob = asyncio.run(_drain(stream_chat_request(
        _agentic_body(app_id, run_ts), Config.default(), root,
    )))
    events = parse_sse(blob)
    kinds = [k for k, _ in events]

    sr = next(d for k, d in events if k == "skill_request")
    assert sr["skill"] == "this_does_not_exist"
    res = next(d for k, d in events if k == "skill_result")
    assert res["success"] is False
    assert "Unknown skill" in res["text"]
    assert kinds[-1] == "done"


def test_agentic_loop_consent_class_skill_emits_skill_pending_and_halts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``generate_frida_hook`` is the canonical consent-class skill
    (``requires_confirmation=True``). Until the chat consent UI ships
    (ISSUE-009), the agentic loop emits ``skill_pending`` and halts
    with an operator-readable explanation in ``content``."""
    root, app_id, run_ts = _seed_agentic_app(tmp_path)

    def fake_complete(prompt: str, **kwargs: Any) -> _FakeLLMResult:
        return _FakeLLMResult(json.dumps({
            "skill_requests": [
                {"skill": "generate_frida_hook", "params": {"template_id": "x"}},
            ],
        }))

    import androscan.llm.client as llm_client
    monkeypatch.setattr(llm_client, "complete", fake_complete)

    blob = asyncio.run(_drain(stream_chat_request(
        _agentic_body(app_id, run_ts), Config.default(), root,
    )))
    events = parse_sse(blob)
    kinds = [k for k, _ in events]

    # skill_request and skill_pending precede the halt content.
    assert "skill_pending" in kinds
    pending = next(d for k, d in events if k == "skill_pending")
    assert pending["skill"] == "generate_frida_hook"
    assert "Consent" in pending["reason"] or "consent" in pending["reason"]

    contents = [d["delta"] for k, d in events if k == "content"]
    assert any("consent" in c.lower() for c in contents)
    assert kinds[-1] == "done"
    done = events[-1][1]
    assert done.get("done_reason") == "consent_required"


def test_agentic_loop_turn_budget_exhausted_emits_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the LLM never emits final content within MAX_AGENTIC_TURNS,
    the loop halts with an explicit error so the operator knows the
    turn budget was hit. We force this by having every turn emit
    skill_requests with no content."""
    root, app_id, run_ts = _seed_agentic_app(tmp_path)

    def fake_complete(prompt: str, **kwargs: Any) -> _FakeLLMResult:
        return _FakeLLMResult(json.dumps({
            "skill_requests": [{"skill": "search_decompiled_sources",
                                "params": {"query": "x"}}],
        }))

    from androscan.skills.base import SkillResult

    def fake_execute(name: str, params: dict[str, Any], ctx: Any) -> SkillResult:
        return SkillResult(success=True, text="empty result")

    import androscan.llm.client as llm_client
    monkeypatch.setattr(llm_client, "complete", fake_complete)
    import androscan.skills as skills_module
    monkeypatch.setattr(skills_module, "execute", fake_execute)

    blob = asyncio.run(_drain(stream_chat_request(
        _agentic_body(app_id, run_ts), Config.default(), root,
    )))
    events = parse_sse(blob)
    kinds = [k for k, _ in events]

    assert kinds[-1] == "error"
    err = events[-1][1]["error"]
    assert "exhausted" in err.lower()
    assert str(chat_module.MAX_AGENTIC_TURNS) in err


def test_agentic_loop_max_skills_per_turn_caps_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A single turn requesting 10 skills must only dispatch the first
    ``MAX_SKILLS_PER_TURN``. Defends DEC-022's per-turn fan-out
    envelope from a runaway LLM."""
    root, app_id, run_ts = _seed_agentic_app(tmp_path)

    requested = [
        {"skill": "search_decompiled_sources", "params": {"query": f"q{i}"}}
        for i in range(10)
    ]
    turn_responses = [
        json.dumps({"skill_requests": requested}),
        json.dumps({"content": "done"}),
    ]
    n = {"i": 0}

    def fake_complete(prompt: str, **kwargs: Any) -> _FakeLLMResult:
        r = turn_responses[n["i"]]
        n["i"] += 1
        return _FakeLLMResult(r)

    from androscan.skills.base import SkillResult

    def fake_execute(name: str, params: dict[str, Any], ctx: Any) -> SkillResult:
        return SkillResult(success=True, text="ok")

    import androscan.llm.client as llm_client
    monkeypatch.setattr(llm_client, "complete", fake_complete)
    import androscan.skills as skills_module
    monkeypatch.setattr(skills_module, "execute", fake_execute)

    blob = asyncio.run(_drain(stream_chat_request(
        _agentic_body(app_id, run_ts), Config.default(), root,
    )))
    events = parse_sse(blob)
    skill_requests = [d for k, d in events if k == "skill_request"]
    assert len(skill_requests) == chat_module.MAX_SKILLS_PER_TURN


def test_agentic_loop_validation_rejects_non_bool_flag(tmp_path: Path) -> None:
    """The validation layer must reject a non-boolean ``agentic_loop``
    so a malformed client gets a clear error rather than a silent
    fall-through to single-pass mode."""
    root, _, _ = _seed_agentic_app(tmp_path)
    body = {
        "tab": "lab", "prompt": "hi", "app_id": "myapp",
        "run_ts": "01-jan-26_12-00-00",
        "agentic_loop": "yes please",  # str — must be rejected
    }
    blob = asyncio.run(_drain(stream_chat_request(
        body, Config.default(), root,
    )))
    events = parse_sse(blob)
    assert [k for k, _ in events] == ["error"]
    assert "agentic_loop" in events[0][1]["error"]


def test_agentic_loop_default_off_preserves_single_pass_behaviour(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without the opt-in flag, the chat must take the legacy
    single-pass streaming path — important for back-compat with
    existing tests / Reports tab Q&A / Inspect tab RAG enrichment."""
    root, app_id, run_ts = _seed_agentic_app(tmp_path)
    streamer = _make_streamer([], ["legacy"])

    body = {
        "tab": "reports", "prompt": "hi", "app_id": app_id, "run_ts": run_ts,
        # No agentic_loop flag at all.
    }
    blob = asyncio.run(_drain(stream_chat_request(
        body, Config.default(), root, streamer=streamer,
    )))
    events = parse_sse(blob)
    kinds = [k for k, _ in events]
    # Legacy path emits content + done; never skill_request / widget /
    # skill_pending.
    assert "content" in kinds
    assert "done" in kinds
    assert "skill_request" not in kinds
    assert "widget" not in kinds
    # The agentic field on the done payload is only set by the agentic
    # path — single-pass leaves it absent.
    done = events[-1][1]
    assert done.get("agentic") is None or done.get("agentic") is False
