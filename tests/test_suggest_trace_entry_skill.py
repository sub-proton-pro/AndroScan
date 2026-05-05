"""Tests for the LLM-tier ``suggest_trace_entry`` skill (Phase 11 v2.1
sub-step v2.1.5 / DEC-025 v2.1 closing-note Q7 / Q11).

The skill combines RAG semantic search + call-graph context + an LLM
ranking pass to surface clickable trace-entry-method candidates as
``TraceEntryCandidateWidget`` rows on the ``SkillResult.widgets``
channel. Tests cover:

  * Registration / catalog invariants (tier=llm, requires_confirmation=False).
  * Pure helpers (Java→Smali class conversion, prompt builder, response
    parser with hallucination guard, summary formatter).
  * Fail-open posture on every unavailability mode (no app context,
    decompile cache not ready, RAG not ready, call graph not ready,
    no RAG hits, no candidate pool, LLM transport error, malformed
    JSON response). All must return ``success=True`` with empty
    widgets and a clear ``[suggest_trace_entry] …`` text — mirrors
    :mod:`test_query_call_graph_skill` / :mod:`test_search_decompiled_sources_skill`.
  * Happy path with mocked RAG + mocked LLM ranking — widget shape,
    rationale length cap, confidence clamping, hallucination guard.

The fixture pattern matches :mod:`test_query_call_graph_skill`: seed
``apps/<app_id>/.decompiled/<sha>/`` with the same call-graph fixtures
and build the call graph synchronously, then mock the RAG layer +
LLM client. This keeps the call graph "real" so the
``list_methods_on_class`` integration is exercised end-to-end.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from androscan.analysis import call_graph
from androscan.config import Config
from androscan.skills import (
    SkillContext,
    execute,
    list_llm_skills,
    list_skills_by_tier,
)
from androscan.skills.base import TraceEntryCandidateWidget
from androscan.skills.suggest_trace_entry import (
    _build_ranking_prompt,
    _format_summary_text,
    _java_class_to_smali,
    _parse_ranking_response,
)
from androscan.web import decompile_cache as dc

FIXTURES = Path(__file__).parent / "fixtures" / "call_graph_smali"


# ---------------------------------------------------------------------------
# Test fixtures (mirrors test_query_call_graph_skill.py)


def _seed_apps_with_graph(tmp_path: Path, app_id: str = "myapp") -> Path:
    """Seed a real decompile cache + call graph; matches the
    fixture used by the call-graph skill tests so the integration
    against ``list_methods_on_class`` is a real database read."""
    apps_root = tmp_path / "apps"
    app_dir = apps_root / app_id
    app_dir.mkdir(parents=True)
    sha = "f" * 40
    apk = tmp_path / "fake.apk"
    apk.write_bytes(b"PK")
    (app_dir / "app_meta.json").write_text(
        json.dumps({"apk_sha256": sha, "apk_path": str(apk), "dossier": {}}),
        encoding="utf-8",
    )
    dc.sources_dir(app_dir, sha).mkdir(parents=True)
    dc._write_index(
        app_dir, sha,
        {"status": "ready", "apk_path": str(apk), "apk_sha256": sha, "file_count": 0},
    )
    cache = dc.cache_root_for(app_dir, sha)
    smali_out = cache / call_graph.APKTOOL_OUT_SUBDIR
    smali_out.mkdir(parents=True)
    shutil.copytree(FIXTURES / "smali", smali_out / "smali")
    shutil.copytree(FIXTURES / "smali_classes2", smali_out / "smali_classes2")
    st = call_graph.build_index(cache, apk_path=apk, sha=sha)
    assert st.status == "ready", st.error
    return app_dir


def _ctx_for(app_dir: Path) -> SkillContext:
    """Build a SkillContext with ``run_folder.parent`` = app_dir."""
    run_folder = app_dir / "run-1"
    run_folder.mkdir(parents=True, exist_ok=True)
    return SkillContext(
        config=Config.default(),
        run_folder=run_folder,
        dossier_dict={},
        apk_path=str(app_dir / "fake.apk"),
    )


@dataclass
class _FakeHit:
    """Minimal Hit-shaped object — covers the fields
    ``_gather_candidates`` reads via ``hit.to_dict()``."""

    file: str
    start_line: int
    end_line: int
    class_name: str
    method_name: str | None
    content: str
    score: float = 0.9
    kind: str = "method"
    package: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "class_name": self.class_name,
            "method_name": self.method_name,
            "content": self.content,
            "score": self.score,
            "kind": self.kind,
            "package": self.package,
        }


@dataclass
class _LLMResponse:
    content: str
    metadata: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.metadata is None:
            self.metadata = {"done_reason": "stop"}


# ---------------------------------------------------------------------------
# Registration / catalog invariants


def test_suggest_trace_entry_in_llm_catalog():
    """The skill is advertised to the LLM as a read-only entry-discovery
    tool — tier=llm, requires_confirmation=False, schema documents
    description + app_id params."""
    metas = list_llm_skills()
    by_name = {m.name: m for m in metas}
    assert "suggest_trace_entry" in by_name
    meta = by_name["suggest_trace_entry"]
    assert meta.tier == "llm"
    assert meta.requires_confirmation is False
    assert "description" in meta.params_schema
    assert "app_id" in meta.params_schema


def test_suggest_trace_entry_not_in_exploit_catalog():
    """v2.1.5 is read-only by design (DEC-025 § 'Trace itself is read-only');
    the skill must not appear in the exploit-tier catalog."""
    exploit_names = {m.name for m in list_skills_by_tier("exploit")}
    assert "suggest_trace_entry" not in exploit_names


# ---------------------------------------------------------------------------
# Pure helpers — fast unit tests with no I/O


class TestJavaClassToSmali:
    def test_dotted_to_descriptor(self):
        assert _java_class_to_smali("com.example.Foo") == "Lcom/example/Foo;"

    def test_inner_class_dollar_preserved(self):
        # Inner classes use ``$`` in both Java and Smali — must NOT be
        # rewritten.
        assert _java_class_to_smali("com.example.Foo$Bar") == "Lcom/example/Foo$Bar;"

    def test_empty_returns_empty(self):
        assert _java_class_to_smali("") == ""


class TestBuildRankingPrompt:
    def test_emits_json_schema_in_system_prompt(self):
        system, user = _build_ranking_prompt(
            "trace login flow",
            [
                {
                    "smali_id": "Lcom/x/Y;->onClick(Landroid/view/View;)V",
                    "java_class": "com.x.Y",
                    "method_name": "onClick",
                    "preview": "void onClick(View v) { ... }",
                    "evidence": "Y.java:10-30",
                },
            ],
        )
        assert "candidates" in system
        assert "smali_id" in system
        assert "rationale" in system
        assert "confidence" in system
        # Hard cap on output stated explicitly in the prompt
        assert "AT MOST 3 candidates" in system
        assert "200 char" in system

    def test_user_prompt_includes_description_and_pool(self):
        system, user = _build_ranking_prompt(
            "the password validation",
            [
                {
                    "smali_id": "Lcom/x/Login;->validate(Ljava/lang/String;)Z",
                    "java_class": "com.x.Login",
                    "method_name": "validate",
                    "preview": "if (Intrinsics.areEqual(pin, expected)) ...",
                    "evidence": "Login.java:42-58",
                },
            ],
        )
        assert "the password validation" in user
        assert "Lcom/x/Login;->validate(Ljava/lang/String;)Z" in user
        assert "Login.java:42-58" in user


class TestParseRankingResponse:
    def test_happy_path(self):
        valid_ids = {"Lcom/x/A;->m()V", "Lcom/x/B;->n()V"}
        raw = json.dumps({
            "candidates": [
                {"smali_id": "Lcom/x/A;->m()V", "rationale": "obvious match",
                 "confidence": 0.9},
                {"smali_id": "Lcom/x/B;->n()V", "rationale": "plausible",
                 "confidence": 0.5},
            ],
        })
        widgets = _parse_ranking_response(raw, valid_ids)
        assert len(widgets) == 2
        assert all(isinstance(w, TraceEntryCandidateWidget) for w in widgets)
        assert widgets[0].smali_id == "Lcom/x/A;->m()V"
        assert widgets[0].confidence == 0.9
        assert widgets[1].rationale == "plausible"

    def test_hallucinated_smali_id_filtered_out(self):
        """The LLM occasionally paraphrases — we must drop candidates
        that aren't in the pool, otherwise the chat-widget renderer's
        auto-fire would seed garbage into pendingTraceEntry and the
        downstream trace_behavior skill would blow up."""
        valid_ids = {"Lcom/x/A;->m()V"}
        raw = json.dumps({
            "candidates": [
                {"smali_id": "Lcom/x/A;->m()V", "rationale": "ok", "confidence": 0.9},
                {"smali_id": "Lcom/HALLUCINATED;->fake()V",
                 "rationale": "not in pool", "confidence": 0.8},
            ],
        })
        widgets = _parse_ranking_response(raw, valid_ids)
        assert len(widgets) == 1
        assert widgets[0].smali_id == "Lcom/x/A;->m()V"

    def test_rationale_length_cap_at_200_chars(self):
        valid_ids = {"Lcom/x/A;->m()V"}
        long_rationale = "x" * 500
        raw = json.dumps({
            "candidates": [
                {"smali_id": "Lcom/x/A;->m()V",
                 "rationale": long_rationale, "confidence": 0.5},
            ],
        })
        widgets = _parse_ranking_response(raw, valid_ids)
        assert len(widgets) == 1
        # 200 char cap (199 + ellipsis = 200 displayed, but capped at 200 internally)
        assert len(widgets[0].rationale) <= 200

    def test_confidence_clamped_to_unit_interval(self):
        valid_ids = {"Lcom/x/A;->m()V", "Lcom/x/B;->n()V", "Lcom/x/C;->o()V"}
        raw = json.dumps({
            "candidates": [
                {"smali_id": "Lcom/x/A;->m()V", "rationale": "high", "confidence": 5.0},
                {"smali_id": "Lcom/x/B;->n()V", "rationale": "neg", "confidence": -0.3},
                {"smali_id": "Lcom/x/C;->o()V", "rationale": "ok", "confidence": 0.42},
            ],
        })
        widgets = _parse_ranking_response(raw, valid_ids)
        assert len(widgets) == 3
        assert widgets[0].confidence == 1.0
        assert widgets[1].confidence == 0.0
        assert widgets[2].confidence == 0.42

    def test_hard_cap_at_three_candidates(self):
        """v2.1.5 caps the candidate list at 3 — operator UX."""
        valid_ids = {f"Lcom/x/A{i};->m()V" for i in range(10)}
        raw = json.dumps({
            "candidates": [
                {"smali_id": f"Lcom/x/A{i};->m()V",
                 "rationale": "ok", "confidence": 0.5}
                for i in range(10)
            ],
        })
        widgets = _parse_ranking_response(raw, valid_ids)
        assert len(widgets) == 3

    def test_malformed_json_returns_empty(self):
        widgets = _parse_ranking_response("not json", {"Lcom/x/A;->m()V"})
        assert widgets == []

    def test_missing_candidates_field_returns_empty(self):
        widgets = _parse_ranking_response(
            json.dumps({"thinking": "no candidates emitted"}),
            {"Lcom/x/A;->m()V"},
        )
        assert widgets == []

    def test_non_object_returns_empty(self):
        widgets = _parse_ranking_response("[1, 2, 3]", {"Lcom/x/A;->m()V"})
        assert widgets == []


class TestFormatSummaryText:
    def test_empty_widgets(self):
        text = _format_summary_text("login flow", (), pool_size=5)
        assert "No confident matches" in text
        assert "login flow" in text
        assert "pool of 5" in text

    def test_with_widgets_lists_each(self):
        widgets = (
            TraceEntryCandidateWidget(
                smali_id="Lcom/x/A;->m()V", rationale="r1", confidence=0.9,
            ),
            TraceEntryCandidateWidget(
                smali_id="Lcom/x/B;->n()V", rationale="r2", confidence=0.5,
            ),
        )
        text = _format_summary_text("login flow", widgets, pool_size=8)
        assert "Lcom/x/A;->m()V" in text
        assert "Lcom/x/B;->n()V" in text
        assert "0.90" in text
        assert "0.50" in text
        assert "r1" in text
        assert "r2" in text


# ---------------------------------------------------------------------------
# Input-validation


def test_missing_description_fails_fast(tmp_path: Path):
    app_dir = _seed_apps_with_graph(tmp_path)
    r = execute("suggest_trace_entry", {}, _ctx_for(app_dir))
    assert r.success is False
    assert "description" in r.text.lower()
    assert r.widgets == ()


# ---------------------------------------------------------------------------
# Fail-open paths (mirrors search_decompiled_sources / query_call_graph)


def test_no_app_context_fails_open(tmp_path: Path):
    """No run_folder → no app dir → success=True with empty widgets."""
    ctx = SkillContext(
        config=Config.default(),
        run_folder=tmp_path / "doesnotexist",
        dossier_dict={},
        apk_path=None,
    )
    r = execute("suggest_trace_entry", {"description": "anything"}, ctx)
    assert r.success is True
    assert r.widgets == ()
    assert "[suggest_trace_entry]" in r.text


def test_decompile_not_ready_fails_open(tmp_path: Path):
    """run_folder exists but decompile cache isn't built — fail-open."""
    apps_root = tmp_path / "apps"
    app_dir = apps_root / "myapp"
    app_dir.mkdir(parents=True)
    run_folder = app_dir / "run-1"
    run_folder.mkdir(parents=True)
    ctx = SkillContext(
        config=Config.default(), run_folder=run_folder,
        dossier_dict={}, apk_path=None,
    )
    r = execute("suggest_trace_entry", {"description": "anything"}, ctx)
    assert r.success is True
    assert r.widgets == ()
    assert "Decompile cache not ready" in r.text


# ---------------------------------------------------------------------------
# Happy path with mocked RAG + mocked LLM ranking


def _stub_rag_provider(monkeypatch) -> None:
    """Replace the embed-provider getter with a no-op stub. The RAG
    query function is mocked separately so the provider is never
    actually used for embeddings."""

    class _FakeProvider:
        def embed(self, text: str) -> list[float]:
            return [0.0]

    monkeypatch.setattr(
        "androscan.rag.embed.get_provider", lambda config: _FakeProvider(),
    )


def _stub_rag_status_ready(monkeypatch) -> None:
    """The RAG index status check is mocked to ``ready`` so the skill
    proceeds past the unavailability gate without us needing to
    actually build a RAG SQLite store in the test fixture."""
    from androscan.rag.index import IndexStatus

    monkeypatch.setattr(
        "androscan.rag.index.get_status",
        lambda cache_dir: IndexStatus(status="ready", error=None),
    )


def test_no_rag_hits_returns_empty_widgets(tmp_path: Path, monkeypatch):
    """RAG returns no hits — fail-open with success=True + clear text."""
    app_dir = _seed_apps_with_graph(tmp_path)
    _stub_rag_status_ready(monkeypatch)
    _stub_rag_provider(monkeypatch)
    monkeypatch.setattr(
        "androscan.rag.search.query",
        lambda *a, **k: [],
    )

    r = execute(
        "suggest_trace_entry",
        {"description": "the deep link handler"},
        _ctx_for(app_dir),
    )
    assert r.success is True
    assert r.widgets == ()
    assert "no hits" in r.text.lower()


def test_happy_path_emits_widgets(tmp_path: Path, monkeypatch):
    """RAG returns hits → call graph has matches → LLM ranks →
    widgets come back in SkillResult.widgets with the expected shape.

    We use the call-graph fixture's ``Greeter`` class so the
    ``list_methods_on_class`` lookup hits real rows.
    """
    app_dir = _seed_apps_with_graph(tmp_path)
    _stub_rag_status_ready(monkeypatch)
    _stub_rag_provider(monkeypatch)

    # Discover what methods exist on the fixture's ``Greeter`` class so
    # the test is robust to fixture refactors.
    cache = dc.cache_root_for(app_dir, "f" * 40)
    methods_payload = call_graph.list_methods_on_class(
        cache, "Lcom/example/Greeter;",
    )
    methods = methods_payload.get("methods") or []
    assert methods, "fixture expected to have Greeter methods"
    real_smali_id = methods[0]["smali_id"]
    real_method_name = methods[0]["method_name"]

    monkeypatch.setattr(
        "androscan.rag.search.query",
        lambda *a, **k: [
            _FakeHit(
                file="com/example/Greeter.java",
                start_line=10,
                end_line=30,
                class_name="com.example.Greeter",
                method_name=real_method_name,
                content="public void greet() { ... }",
            ),
        ],
    )

    captured: dict[str, Any] = {}

    def fake_complete(prompt: str, **kwargs: Any) -> Any:
        captured["prompt"] = prompt
        captured["system"] = kwargs.get("system_content")
        captured["response_format"] = kwargs.get("response_format")
        captured["messages"] = kwargs.get("messages")
        return _LLMResponse(json.dumps({
            "candidates": [
                {
                    "smali_id": real_smali_id,
                    "rationale": "Matches the operator's description "
                    "verbatim — first method in the Greeter class.",
                    "confidence": 0.85,
                },
            ],
        }))

    monkeypatch.setattr("androscan.llm.client.complete", fake_complete)

    r = execute(
        "suggest_trace_entry",
        {"description": "where greeting starts"},
        _ctx_for(app_dir),
    )

    assert r.success is True
    assert len(r.widgets) == 1
    w = r.widgets[0]
    assert isinstance(w, TraceEntryCandidateWidget)
    assert w.kind == "trace_entry_candidate"
    assert w.smali_id == real_smali_id
    assert "Greeter" in w.rationale or "operator" in w.rationale
    assert 0.0 <= w.confidence <= 1.0
    # System prompt should advertise the JSON schema + rules
    assert "candidates" in (captured["system"] or "")
    # response_format must be json so the LLM's structured output is
    # safe to json.loads on the way back
    assert captured["response_format"] == "json"


def test_llm_transport_error_fails_open(tmp_path: Path, monkeypatch):
    """LLM client raises → success=True with empty widgets + clear text."""
    app_dir = _seed_apps_with_graph(tmp_path)
    _stub_rag_status_ready(monkeypatch)
    _stub_rag_provider(monkeypatch)

    monkeypatch.setattr(
        "androscan.rag.search.query",
        lambda *a, **k: [
            _FakeHit(
                file="com/example/Greeter.java",
                start_line=10, end_line=30,
                class_name="com.example.Greeter",
                method_name=None, content="...",
            ),
        ],
    )

    def boom(prompt: str, **kwargs: Any) -> Any:
        raise RuntimeError("ollama unreachable")

    monkeypatch.setattr("androscan.llm.client.complete", boom)

    r = execute(
        "suggest_trace_entry",
        {"description": "anything"},
        _ctx_for(app_dir),
    )
    assert r.success is True
    assert r.widgets == ()
    assert "ollama unreachable" in r.text


def test_malformed_llm_json_fails_open(tmp_path: Path, monkeypatch):
    """LLM returns non-JSON garbage → empty widgets, success=True."""
    app_dir = _seed_apps_with_graph(tmp_path)
    _stub_rag_status_ready(monkeypatch)
    _stub_rag_provider(monkeypatch)

    monkeypatch.setattr(
        "androscan.rag.search.query",
        lambda *a, **k: [
            _FakeHit(
                file="com/example/Greeter.java",
                start_line=10, end_line=30,
                class_name="com.example.Greeter",
                method_name=None, content="...",
            ),
        ],
    )
    monkeypatch.setattr(
        "androscan.llm.client.complete",
        lambda prompt, **kwargs: _LLMResponse("this is not json"),
    )

    r = execute(
        "suggest_trace_entry",
        {"description": "anything"},
        _ctx_for(app_dir),
    )
    assert r.success is True
    assert r.widgets == ()


def test_widget_payload_is_jsonable(tmp_path: Path, monkeypatch):
    """SkillResult.widgets must round-trip through ``dataclasses.asdict``
    to JSON — that's the chat agentic loop's wire format. A widget
    that crashes ``asdict`` would break the SSE event emission."""
    import dataclasses

    w = TraceEntryCandidateWidget(
        smali_id="Lcom/x/Y;->m()V", rationale="r", confidence=0.5,
    )
    payload = dataclasses.asdict(w)
    json_text = json.dumps(payload)
    parsed = json.loads(json_text)
    assert parsed["kind"] == "trace_entry_candidate"
    assert parsed["smali_id"] == "Lcom/x/Y;->m()V"
    assert parsed["rationale"] == "r"
    assert parsed["confidence"] == 0.5
