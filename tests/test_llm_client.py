"""Tests for the LCP.2 llama.cpp HTTP path + the unified provider_kind()
router in :mod:`androscan.llm.client`.

The existing ``tests/test_llm.py`` covers the Ollama path and the
prompt builder + parser; this file focuses on the new
``_complete_llamacpp`` codepath, the ``<think>`` strip helper, and
the three-way ``complete()`` dispatcher introduced in LCP.2 (DEC-027).
"""

from unittest.mock import MagicMock, patch

import pytest
import requests

from androscan.config import Config
from androscan.llm.client import (
    CompleteResult,
    LLAMACPP_DEFAULT_MODEL_LABEL,
    _build_llamacpp_messages,
    _complete_llamacpp,
    _resolve_llamacpp_base_url,
    _strip_think_blocks,
    _stream_llamacpp_sse,
    complete,
)


# ---------------------------------------------------------------------------
# <think>...</think> strip helper
# ---------------------------------------------------------------------------


class TestStripThinkBlocks:
    """The defensive helper both local providers call for parity with
    the Ollama native ``message.thinking`` channel."""

    def test_no_think_block_is_idempotent(self) -> None:
        text = '{"hypotheses": []}'
        content, thinking = _strip_think_blocks(text)
        assert content == text
        assert thinking == ""

    def test_empty_string_is_idempotent(self) -> None:
        assert _strip_think_blocks("") == ("", "")

    def test_extracts_single_think_block(self) -> None:
        raw = '<think>Reasoning step.</think>\n{"hypotheses": []}'
        content, thinking = _strip_think_blocks(raw)
        assert content == '{"hypotheses": []}'
        assert thinking == "Reasoning step."

    def test_extracts_multiple_think_blocks(self) -> None:
        raw = "<think>step 1</think> body <think>step 2</think> more"
        content, thinking = _strip_think_blocks(raw)
        assert "<think>" not in content.lower()
        assert "step 1" in thinking
        assert "step 2" in thinking

    def test_handles_multiline_think_block(self) -> None:
        raw = "<think>line1\nline2\nline3</think>\nbody"
        content, thinking = _strip_think_blocks(raw)
        assert content == "body"
        assert "line1" in thinking and "line3" in thinking

    def test_case_insensitive_matching(self) -> None:
        raw = "<THINK>capital</THINK>body"
        content, thinking = _strip_think_blocks(raw)
        assert content == "body"
        assert thinking == "capital"

    def test_partial_open_tag_without_close_is_left_alone(self) -> None:
        """A stray ``<think`` substring with no matching close tag must
        NOT eat the rest of the response — operators would lose real
        JSON content. The helper returns the input unchanged."""
        raw = "{'foo': '<think malformed'}"
        content, thinking = _strip_think_blocks(raw)
        assert content == raw
        assert thinking == ""


# ---------------------------------------------------------------------------
# llama.cpp base URL resolution
# ---------------------------------------------------------------------------


class TestResolveLlamacppBaseUrl:
    """v1 LCP.2 falls back to the registry default; LCP.4 will add
    ``llamacpp_base_url`` Config field."""

    def test_falls_back_to_registry_default_for_default_config(self) -> None:
        cfg = Config.default()
        assert _resolve_llamacpp_base_url(cfg) == "http://127.0.0.1:8033/v1"

    def test_strips_trailing_slash_from_custom_field(self) -> None:
        """Future LCP.4 field-level override must be normalised the same
        way as the registry default — no double-slash when joined to
        ``/chat/completions``."""
        cfg = MagicMock(llamacpp_base_url="http://127.0.0.1:9999/v1/")
        assert _resolve_llamacpp_base_url(cfg) == "http://127.0.0.1:9999/v1"

    def test_empty_custom_field_falls_back_to_default(self) -> None:
        cfg = MagicMock(llamacpp_base_url="")
        assert _resolve_llamacpp_base_url(cfg) == "http://127.0.0.1:8033/v1"


# ---------------------------------------------------------------------------
# Request body shape
# ---------------------------------------------------------------------------


def _make_llamacpp_mock_config(**overrides) -> MagicMock:
    """Build a MagicMock Config that the LCP.2 dispatcher routes
    through the llama.cpp branch.

    Pins ``llamacpp_base_url`` / ``llamacpp_model`` /
    ``llamacpp_max_tokens`` to ``None`` explicitly by default —
    without these, MagicMock's auto-attribute spawning would shadow
    the ``getattr(config, "...", None)`` defaults inside
    :func:`_resolve_llamacpp_base_url` and :func:`_complete_llamacpp`,
    making the registry-default + ollama-num-predict fallback paths
    un-testable. Tests that want to exercise the LCP.4 happy path
    (where the dedicated ``llamacpp_*`` fields ARE set) override
    those via ``**overrides``."""
    base = dict(
        ollama_temperature=0.2,
        ollama_num_predict=8192,
        llm_provider="llamacpp",
        llamacpp_base_url=None,
        llamacpp_model=None,
        llamacpp_max_tokens=None,
    )
    base.update(overrides)
    cfg = MagicMock(**base)
    cfg.provider_kind.return_value = "local-openai-compat"
    return cfg


def _make_llamacpp_mock_resp(content: str = '{"hypotheses": []}') -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {
        "choices": [{
            "message": {"role": "assistant", "content": content},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }
    return resp


class TestLlamacppRequestBodyShape:
    """The body sent to ``/v1/chat/completions`` MUST match the OpenAI
    chat-completions schema so ``llama-server`` (and any other
    OpenAI-compat shim) accepts it."""

    @staticmethod
    def _mock_resp(content: str = '{"hypotheses": []}') -> MagicMock:
        return _make_llamacpp_mock_resp(content)

    @staticmethod
    def _mock_config() -> MagicMock:
        return _make_llamacpp_mock_config()

    def test_body_contains_messages_with_system_and_user(self) -> None:
        config = self._mock_config()
        with patch("androscan.llm.client.requests.post", return_value=self._mock_resp()) as post_mock:
            _complete_llamacpp(
                "test prompt", config, system_content="sys hint", stream=False,
            )
        body = post_mock.call_args.kwargs["json"]
        roles = [m["role"] for m in body["messages"]]
        assert roles == ["system", "user"]
        assert body["messages"][0]["content"] == "sys hint"
        assert body["messages"][1]["content"] == "test prompt"

    def test_body_max_tokens_falls_back_to_ollama_num_predict_when_llamacpp_field_unset(self) -> None:
        """Backwards-compat path: when ``llamacpp_max_tokens`` is unset
        (the default helper pins it to ``None`` explicitly), the
        dispatcher falls back to ``ollama_num_predict`` — so operators
        upgrading from the LCP.2-only build keep getting the same
        max_tokens value as before LCP.4 wired the dedicated knob."""
        config = self._mock_config()
        with patch("androscan.llm.client.requests.post", return_value=self._mock_resp()) as post_mock:
            _complete_llamacpp("test", config, stream=False)
        body = post_mock.call_args.kwargs["json"]
        assert body["temperature"] == 0.2
        assert body["max_tokens"] == 8192

    def test_body_max_tokens_prefers_dedicated_llamacpp_field_when_set(self) -> None:
        """LCP.4 happy path: when the operator has set
        ``llamacpp_max_tokens`` (via Settings UI / YAML / env var),
        the dispatcher uses it instead of ``ollama_num_predict`` —
        letting the operator tune the two providers independently."""
        config = _make_llamacpp_mock_config(
            ollama_num_predict=8192,
            llamacpp_max_tokens=4096,
        )
        with patch("androscan.llm.client.requests.post", return_value=self._mock_resp()) as post_mock:
            _complete_llamacpp("test", config, stream=False)
        body = post_mock.call_args.kwargs["json"]
        assert body["max_tokens"] == 4096

    def test_body_sets_response_format_when_json_requested(self) -> None:
        """response_format='json' (analysis pipeline default) must
        translate to the OpenAI ``response_format: {"type":
        "json_object"}`` flavour."""
        config = self._mock_config()
        with patch("androscan.llm.client.requests.post", return_value=self._mock_resp()) as post_mock:
            _complete_llamacpp(
                "test", config, stream=False, response_format="json",
            )
        body = post_mock.call_args.kwargs["json"]
        assert body["response_format"] == {"type": "json_object"}

    def test_body_omits_response_format_when_not_json(self) -> None:
        """workbench chat path passes ``response_format=None`` for prose
        replies — no ``response_format`` key at all on the wire."""
        config = self._mock_config()
        with patch("androscan.llm.client.requests.post", return_value=self._mock_resp()) as post_mock:
            _complete_llamacpp(
                "test", config, stream=False, response_format=None,
            )
        body = post_mock.call_args.kwargs["json"]
        assert "response_format" not in body

    def test_body_uses_default_model_label_when_no_model_arg(self) -> None:
        """``llama-server`` ignores the request-body ``model`` field
        (it serves whatever was loaded at startup), but the schema
        still requires the field. The default label keeps logs +
        upstream metrics readable."""
        config = self._mock_config()
        with patch("androscan.llm.client.requests.post", return_value=self._mock_resp()) as post_mock:
            _complete_llamacpp("test", config, stream=False)
        body = post_mock.call_args.kwargs["json"]
        assert body["model"] == LLAMACPP_DEFAULT_MODEL_LABEL

    def test_url_targets_chat_completions_endpoint(self) -> None:
        config = self._mock_config()
        with patch("androscan.llm.client.requests.post", return_value=self._mock_resp()) as post_mock:
            _complete_llamacpp("test", config, stream=False)
        url = post_mock.call_args.args[0]
        assert url.endswith("/v1/chat/completions")


# ---------------------------------------------------------------------------
# SSE stream parsing
# ---------------------------------------------------------------------------


class _FakeStreamResponse:
    """Minimal stand-in for :class:`requests.Response` in stream=True
    mode — yields the canned SSE lines on ``iter_lines()`` and is a
    valid context manager."""

    def __init__(self, lines: list[str]) -> None:
        self._lines = lines

    def __enter__(self) -> "_FakeStreamResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    def iter_lines(self, decode_unicode: bool = True) -> list[str]:
        return list(self._lines)


class TestLlamacppSseParsing:
    """OpenAI-style SSE chunks: ``data: {json}\\n\\n``, terminating
    with ``data: [DONE]``. Empty + unrelated lines are tolerated."""

    def test_concatenates_delta_content_across_chunks(self) -> None:
        lines = [
            'data: {"choices":[{"delta":{"content":"Hello "},"finish_reason":null}]}',
            'data: {"choices":[{"delta":{"content":"world"},"finish_reason":null}]}',
            'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}',
            "data: [DONE]",
        ]
        with patch("androscan.llm.client.requests.post", return_value=_FakeStreamResponse(lines)):
            result = _stream_llamacpp_sse(
                "http://x/v1/chat/completions", {"model": "m", "messages": []},
                timeout=10, on_token=None, on_thinking=None,
            )
        assert result.content == "Hello world"
        assert result.metadata["done_reason"] == "stop"

    def test_invokes_on_token_for_each_content_delta(self) -> None:
        lines = [
            'data: {"choices":[{"delta":{"content":"A"}}]}',
            'data: {"choices":[{"delta":{"content":"B"}}]}',
            "data: [DONE]",
        ]
        seen: list[str] = []
        with patch("androscan.llm.client.requests.post", return_value=_FakeStreamResponse(lines)):
            _stream_llamacpp_sse(
                "http://x", {}, timeout=10,
                on_token=seen.append, on_thinking=None,
            )
        assert seen == ["A", "B"]

    def test_tolerates_empty_lines_and_done_sentinel(self) -> None:
        lines = [
            "",
            'data: {"choices":[{"delta":{"content":"X"}}]}',
            "",
            "data: [DONE]",
            "",
        ]
        with patch("androscan.llm.client.requests.post", return_value=_FakeStreamResponse(lines)):
            result = _stream_llamacpp_sse(
                "http://x", {}, timeout=10,
                on_token=None, on_thinking=None,
            )
        assert result.content == "X"

    def test_strips_leaked_think_blocks_during_stream_collection(self) -> None:
        """If the model leaks ``<think>...</think>`` into the content
        delta, the assembled content must be the post-strip text and
        the captured thinking flows through the thinking channel."""
        lines = [
            'data: {"choices":[{"delta":{"content":"<think>scratch</think>"}}]}',
            'data: {"choices":[{"delta":{"content":"final"}}]}',
            "data: [DONE]",
        ]
        with patch("androscan.llm.client.requests.post", return_value=_FakeStreamResponse(lines)):
            result = _stream_llamacpp_sse(
                "http://x", {}, timeout=10,
                on_token=None, on_thinking=None,
            )
        assert result.content == "final"
        assert "scratch" in result.thinking

    def test_skips_malformed_sse_chunks_without_crashing(self) -> None:
        """A real-world ``llama-server`` run can emit a malformed line
        on quantized variants — the parser must not abort the stream."""
        lines = [
            'data: {"choices":[{"delta":{"content":"ok "}}]}',
            "data: not-json-at-all",
            'data: {"choices":[{"delta":{"content":"recovered"}}]}',
            "data: [DONE]",
        ]
        with patch("androscan.llm.client.requests.post", return_value=_FakeStreamResponse(lines)):
            result = _stream_llamacpp_sse(
                "http://x", {}, timeout=10,
                on_token=None, on_thinking=None,
            )
        assert result.content == "ok recovered"


# ---------------------------------------------------------------------------
# Retry logic on transient HTTP errors
# ---------------------------------------------------------------------------


class TestLlamacppTransientErrorRetry:
    """Timeout escalation + retryable HTTP status set
    ``{429, 500, 502, 503, 504}``. Connection errors fail fast — the
    operator's most likely cause is "llama-server isn't running" and
    a setup hint is the right nudge."""

    @staticmethod
    def _mock_config() -> MagicMock:
        return _make_llamacpp_mock_config()

    def test_connection_error_fails_fast_with_setup_hint(self) -> None:
        config = self._mock_config()
        with patch("androscan.llm.client.requests.post", side_effect=requests.ConnectionError):
            with pytest.raises(RuntimeError, match="Cannot connect to llama-server"):
                _complete_llamacpp("test", config, stream=False)

    def test_timeout_escalates_through_tiers_then_raises(self) -> None:
        """Three timeout tiers (120 / 240 / 480) — after exhausting
        them the function raises with ``timed out`` in the message."""
        config = self._mock_config()
        with patch(
            "androscan.llm.client.requests.post", side_effect=requests.Timeout,
        ) as post_mock:
            with pytest.raises(RuntimeError, match="timed out"):
                _complete_llamacpp("test", config, stream=False)
        # Exactly three POST attempts (one per timeout tier).
        assert post_mock.call_count == 3

    def test_http_503_retries_then_succeeds(self) -> None:
        """Transient 503 (model still loading) retries; the third
        attempt succeeds and the function returns the parsed
        result."""
        config = self._mock_config()

        bad_resp = MagicMock()
        bad_resp.status_code = 503
        bad_err = requests.HTTPError("503 Service Unavailable")
        bad_err.response = bad_resp
        bad_resp.raise_for_status.side_effect = bad_err
        bad_resp.json.return_value = {}
        bad_resp.text = ""

        good_resp = MagicMock()
        good_resp.raise_for_status = MagicMock()
        good_resp.json.return_value = {
            "choices": [{
                "message": {"role": "assistant", "content": '{"ok": true}'},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

        responses = [bad_resp, bad_resp, good_resp]
        with patch(
            "androscan.llm.client.requests.post", side_effect=responses,
        ) as post_mock:
            result = _complete_llamacpp("test", config, stream=False)
        assert result.content == '{"ok": true}'
        assert post_mock.call_count == 3

    def test_http_400_does_not_retry(self) -> None:
        """A 4xx (other than 429) is operator error (bad payload, bad
        model name); retrying would just amplify the wrong request."""
        config = self._mock_config()
        bad_resp = MagicMock()
        bad_resp.status_code = 400
        bad_err = requests.HTTPError("400 Bad Request")
        bad_err.response = bad_resp
        bad_resp.raise_for_status.side_effect = bad_err
        bad_resp.json.return_value = {"error": "bad model name"}
        bad_resp.text = '{"error":"bad model name"}'

        with patch(
            "androscan.llm.client.requests.post", return_value=bad_resp,
        ) as post_mock:
            with pytest.raises(RuntimeError, match="HTTP 400"):
                _complete_llamacpp("test", config, stream=False)
        assert post_mock.call_count == 1


# ---------------------------------------------------------------------------
# Response handling — empty body + <think> strip in non-stream path
# ---------------------------------------------------------------------------


class TestLlamacppNonStreamResponseHandling:
    """Non-stream path mirrors the SSE path's <think> strip; the
    operator-visible empty-body error includes the setup tip."""

    @staticmethod
    def _mock_config() -> MagicMock:
        return _make_llamacpp_mock_config()

    def test_empty_response_raises_with_setup_tip(self) -> None:
        config = self._mock_config()
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {
            "choices": [{
                "message": {"role": "assistant", "content": ""},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 1, "completion_tokens": 0, "total_tokens": 1},
        }
        with patch("androscan.llm.client.requests.post", return_value=resp):
            with pytest.raises(RuntimeError, match="empty response"):
                _complete_llamacpp("test", config, stream=False)

    def test_strips_think_block_from_non_stream_content(self) -> None:
        config = self._mock_config()
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": '<think>plan</think>{"hypotheses": []}',
                },
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        with patch("androscan.llm.client.requests.post", return_value=resp):
            result = _complete_llamacpp("test", config, stream=False)
        assert result.content == '{"hypotheses": []}'
        assert "plan" in result.thinking


# ---------------------------------------------------------------------------
# Three-way complete() router
# ---------------------------------------------------------------------------


class TestCompleteRouterDispatch:
    """The LCP.2 router is a switch on ``Config.provider_kind()``. The
    tests for each branch use a real ``Config`` (not MagicMock) so
    the actual ``provider_kind()`` reverse-lookup is exercised — the
    other branches are mocked at the next layer down."""

    @staticmethod
    def _config_with_provider(name: str, **overrides) -> Config:
        import dataclasses
        defaults = dict(llm_provider=name, cloud_api_key="")
        defaults.update(overrides)
        return dataclasses.replace(Config.default(), **defaults)

    def test_provider_ollama_dispatches_to_complete_ollama(self) -> None:
        config = self._config_with_provider("ollama")
        sentinel = CompleteResult(content="o", thinking="", metadata={})
        with patch(
            "androscan.llm.client._complete_ollama", return_value=sentinel,
        ) as ollama_mock, patch(
            "androscan.llm.client._complete_llamacpp", return_value=None,
        ) as llamacpp_mock, patch(
            "androscan.llm.client._complete_cloud", return_value=None,
        ) as cloud_mock:
            result = complete("p", config=config)
        assert result is sentinel
        ollama_mock.assert_called_once()
        llamacpp_mock.assert_not_called()
        cloud_mock.assert_not_called()

    def test_provider_llamacpp_dispatches_to_complete_llamacpp(self) -> None:
        config = self._config_with_provider("llamacpp")
        sentinel = CompleteResult(content="l", thinking="", metadata={})
        with patch(
            "androscan.llm.client._complete_ollama", return_value=None,
        ) as ollama_mock, patch(
            "androscan.llm.client._complete_llamacpp", return_value=sentinel,
        ) as llamacpp_mock, patch(
            "androscan.llm.client._complete_cloud", return_value=None,
        ) as cloud_mock:
            result = complete("p", config=config)
        assert result is sentinel
        llamacpp_mock.assert_called_once()
        ollama_mock.assert_not_called()
        cloud_mock.assert_not_called()

    def test_provider_openai_dispatches_to_complete_cloud(self) -> None:
        """Any cloud provider name routes through the cloud branch.
        Picking ``openai`` here is representative."""
        config = self._config_with_provider(
            "openai", cloud_model="gpt-x", cloud_api_key="sk-test",
        )
        sentinel = CompleteResult(content="c", thinking="", metadata={})
        with patch(
            "androscan.llm.client._complete_ollama", return_value=None,
        ) as ollama_mock, patch(
            "androscan.llm.client._complete_llamacpp", return_value=None,
        ) as llamacpp_mock, patch(
            "androscan.llm.client._complete_cloud", return_value=sentinel,
        ) as cloud_mock:
            result = complete("p", config=config)
        assert result is sentinel
        cloud_mock.assert_called_once()
        ollama_mock.assert_not_called()
        llamacpp_mock.assert_not_called()

    def test_unknown_provider_falls_back_to_cloud_branch(self) -> None:
        """``provider_kind`` returns ``cloud`` for unknown names —
        :func:`_complete_cloud` then surfaces the existing "no API
        key" error path. Pin this in: it's the contract Settings UI
        relies on for typo-detection."""
        config = self._config_with_provider("unknown-provider")
        sentinel = CompleteResult(content="x", thinking="", metadata={})
        with patch(
            "androscan.llm.client._complete_cloud", return_value=sentinel,
        ) as cloud_mock:
            result = complete("p", config=config)
        assert result is sentinel
        cloud_mock.assert_called_once()


# ---------------------------------------------------------------------------
# Build helpers
# ---------------------------------------------------------------------------


class TestBuildLlamacppMessages:
    """Same OpenAI-compat schema as the cloud path — but kept as a
    parallel function so future llama.cpp-specific image-handling
    tweaks don't have to branch inside the cloud builder."""

    def test_text_only_message_omits_image_parts(self) -> None:
        msgs = _build_llamacpp_messages("sys", "user")
        assert msgs == [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "user"},
        ]

    def test_no_system_content_emits_user_only(self) -> None:
        msgs = _build_llamacpp_messages(None, "user")
        assert msgs == [{"role": "user", "content": "user"}]

    def test_images_produce_multimodal_content_array(self) -> None:
        msgs = _build_llamacpp_messages("sys", "describe", images=["B64DATA"])
        user_msg = msgs[1]
        assert user_msg["role"] == "user"
        parts = user_msg["content"]
        assert isinstance(parts, list)
        assert parts[0] == {"type": "text", "text": "describe"}
        assert parts[1]["type"] == "image_url"
        assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")


# ---------------------------------------------------------------------------
# LCP.6 — local-provider grammar / JSON-schema enforcement
#
# These tests cover the wiring added in :mod:`androscan.llm.client` for
# the LCP.6 grammar follow-up. The grammar itself is exercised by
# ``tests/test_grammar.py`` — here we verify the CLIENT correctly:
#
#   * Sends ``format: <json-schema>`` to Ollama and ``grammar: <gbnf>``
#     to llama.cpp when ``Config.local_grammar_enabled`` is True (the
#     default).
#   * Falls back to the v1-LCP wire shape per-base-url on the first
#     HTTP 400 that mentions schema / grammar (older runtime path).
#   * Honours the kill-switch — flipping ``local_grammar_enabled``
#     False reverts to the v1-LCP wire shape immediately.
#   * Cloud path is unaffected (the OpenAI-compat ``response_format``
#     contract enforces validity at the SDK layer).
# ---------------------------------------------------------------------------


from androscan.llm.client import (  # noqa: E402 — import next to its tests
    _LLAMACPP_GRAMMAR_DISABLED_URLS,
    _OLLAMA_SCHEMA_DISABLED_URLS,
    _complete_ollama,
)


@pytest.fixture(autouse=False)
def _reset_grammar_caches():
    """Clear the per-base-url disabled-set caches before each test that
    asks for the fixture so tests don't leak state into each other.
    The caches are intentionally process-lifetime so production
    operators don't pay the rejection round-trip on every call, but
    that means tests must clear them explicitly."""
    _OLLAMA_SCHEMA_DISABLED_URLS.clear()
    _LLAMACPP_GRAMMAR_DISABLED_URLS.clear()
    yield
    _OLLAMA_SCHEMA_DISABLED_URLS.clear()
    _LLAMACPP_GRAMMAR_DISABLED_URLS.clear()


def _make_ollama_mock_config(local_grammar_enabled: bool = True, **overrides):
    """Build a MagicMock Config for the Ollama branch of the LCP.6
    grammar wiring tests. Matches the shape ``_complete_ollama``
    actually reads — model / temperature / num_predict / num_ctx /
    base_url + the LCP.6 ``local_grammar_enabled`` knob."""
    base = dict(
        ollama_base_url="http://localhost:11434",
        ollama_model="qwen3.5:35b",
        ollama_temperature=0.2,
        ollama_num_predict=8192,
        ollama_num_ctx=16384,
        local_grammar_enabled=local_grammar_enabled,
    )
    base.update(overrides)
    cfg = MagicMock(**base)
    return cfg


def _make_ollama_mock_resp(content: str = '{"hypotheses": []}'):
    """Successful Ollama /api/chat response (non-stream)."""
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {
        "message": {"role": "assistant", "content": content, "thinking": ""},
        "done_reason": "stop",
        "total_duration": 1,
        "eval_count": 1,
        "prompt_eval_count": 1,
    }
    resp.text = content
    resp.status_code = 200
    return resp


def _make_400_resp(error_text: str):
    """An Ollama / llama.cpp HTTP 400 response carrying the given
    error text in the JSON body. Used to drive the schema- and
    grammar-fallback codepaths."""
    resp = MagicMock()
    resp.status_code = 400
    err = requests.HTTPError(f"400 Bad Request: {error_text}")
    err.response = resp
    resp.raise_for_status.side_effect = err
    resp.json.return_value = {"error": error_text}
    resp.text = f'{{"error":{error_text!r}}}'
    return resp


class TestOllamaSchemaFormatMode:
    """Ollama path — JSON-schema in the ``format`` field when
    ``local_grammar_enabled`` is True; ``"json"`` string when off
    or after a 400 fallback."""

    def test_default_config_sends_json_schema_in_format(
        self, _reset_grammar_caches
    ) -> None:
        """Happy path: a fresh Config with the v1-default
        ``local_grammar_enabled=True`` triggers ``format: <dict>``."""
        cfg = _make_ollama_mock_config()
        resp = _make_ollama_mock_resp()
        with patch("androscan.llm.client.requests.post", return_value=resp) as post_mock:
            _complete_ollama("test", cfg, stream=False, response_format="json")
        body = post_mock.call_args.kwargs["json"]
        # The format value is the schema dict, NOT the string "json".
        assert isinstance(body["format"], dict)
        assert body["format"]["type"] == "object"
        # Sanity: the schema contains the discriminated-union skill enum.
        sr = body["format"]["properties"]["skill_requests"]["items"]["properties"]
        assert "enum" in sr["skill"]

    def test_kill_switch_off_sends_string_format(
        self, _reset_grammar_caches
    ) -> None:
        """``local_grammar_enabled=False`` reverts to the v1-LCP wire
        shape (``format: "json"``)."""
        cfg = _make_ollama_mock_config(local_grammar_enabled=False)
        resp = _make_ollama_mock_resp()
        with patch("androscan.llm.client.requests.post", return_value=resp) as post_mock:
            _complete_ollama("test", cfg, stream=False, response_format="json")
        body = post_mock.call_args.kwargs["json"]
        assert body["format"] == "json"

    def test_400_format_error_falls_back_to_string_format_and_caches(
        self, _reset_grammar_caches
    ) -> None:
        """Older Ollama (< 0.5.0) rejects the dict ``format`` value;
        the client must catch the 400, cache the base_url in
        :data:`_OLLAMA_SCHEMA_DISABLED_URLS`, and retry with
        ``format: "json"``. Both attempts hit the SAME base_url, so
        the second call sees the cache and doesn't re-attempt the
        schema mode."""
        cfg = _make_ollama_mock_config()
        bad = _make_400_resp("format must be 'json' or empty")
        good = _make_ollama_mock_resp()
        with patch(
            "androscan.llm.client.requests.post",
            side_effect=[bad, good],
        ) as post_mock:
            result = _complete_ollama("test", cfg, stream=False, response_format="json")
        # Two POSTs total — one rejected, one fallback success.
        assert post_mock.call_count == 2
        # First call sent the dict; second sent the string.
        first_body = post_mock.call_args_list[0].kwargs["json"]
        second_body = post_mock.call_args_list[1].kwargs["json"]
        assert isinstance(first_body["format"], dict)
        assert second_body["format"] == "json"
        assert result.content == '{"hypotheses": []}'
        # The base_url is now in the disabled set.
        assert "http://localhost:11434" in _OLLAMA_SCHEMA_DISABLED_URLS

    def test_400_with_unrelated_error_does_not_trigger_fallback(
        self, _reset_grammar_caches
    ) -> None:
        """A 400 that doesn't look format-related (e.g. malformed
        messages) flows through the existing error path and the
        client raises — no silent retry, no schema-disabled caching."""
        cfg = _make_ollama_mock_config()
        bad = _make_400_resp("missing required field: messages")
        with patch(
            "androscan.llm.client.requests.post", return_value=bad,
        ) as post_mock:
            with pytest.raises(RuntimeError):
                _complete_ollama("test", cfg, stream=False, response_format="json")
        # Single attempt — no fallback retry.
        assert post_mock.call_count == 1
        # Cache is still empty.
        assert "http://localhost:11434" not in _OLLAMA_SCHEMA_DISABLED_URLS

    def test_subsequent_call_after_fallback_skips_schema_mode(
        self, _reset_grammar_caches
    ) -> None:
        """Process-lifetime cache: once a base_url is in the
        disabled set, subsequent calls go straight to ``format:
        "json"`` without paying the rejection round-trip."""
        cfg = _make_ollama_mock_config()
        # Pre-seed the cache (simulating a prior call that triggered
        # the fallback).
        _OLLAMA_SCHEMA_DISABLED_URLS.add("http://localhost:11434")
        good = _make_ollama_mock_resp()
        with patch("androscan.llm.client.requests.post", return_value=good) as post_mock:
            _complete_ollama("test", cfg, stream=False, response_format="json")
        # Single POST — no rejection / no retry.
        assert post_mock.call_count == 1
        body = post_mock.call_args.kwargs["json"]
        assert body["format"] == "json"

    def test_response_format_none_omits_format_key_entirely(
        self, _reset_grammar_caches
    ) -> None:
        """Workbench chat path passes ``response_format=None`` for
        prose replies — no ``format`` key at all on the wire,
        regardless of the grammar kill-switch."""
        cfg = _make_ollama_mock_config()
        resp = _make_ollama_mock_resp(content="prose response text")
        with patch("androscan.llm.client.requests.post", return_value=resp) as post_mock:
            _complete_ollama("test", cfg, stream=False, response_format=None)
        body = post_mock.call_args.kwargs["json"]
        assert "format" not in body


class TestLlamacppGrammarMode:
    """llama.cpp path — ``grammar: <gbnf>`` field added when
    ``local_grammar_enabled`` is True; absent when off or after a
    400 fallback."""

    def test_default_config_attaches_gbnf_grammar_field(
        self, _reset_grammar_caches
    ) -> None:
        cfg = _make_llamacpp_mock_config(local_grammar_enabled=True)
        with patch(
            "androscan.llm.client.requests.post",
            return_value=_make_llamacpp_mock_resp(),
        ) as post_mock:
            _complete_llamacpp("test", cfg, stream=False, response_format="json")
        body = post_mock.call_args.kwargs["json"]
        assert "grammar" in body
        assert "root ::=" in body["grammar"]
        # response_format is also present (belt-and-suspenders).
        assert body["response_format"] == {"type": "json_object"}

    def test_kill_switch_off_omits_grammar_field(
        self, _reset_grammar_caches
    ) -> None:
        cfg = _make_llamacpp_mock_config(local_grammar_enabled=False)
        with patch(
            "androscan.llm.client.requests.post",
            return_value=_make_llamacpp_mock_resp(),
        ) as post_mock:
            _complete_llamacpp("test", cfg, stream=False, response_format="json")
        body = post_mock.call_args.kwargs["json"]
        assert "grammar" not in body

    def test_400_grammar_error_falls_back_and_caches(
        self, _reset_grammar_caches
    ) -> None:
        """Older ``llama-server`` builds (no grammar-field support)
        return a 400 mentioning grammar / unknown field. The client
        catches it, drops the grammar field, retries once, and
        caches the base_url in the disabled set.

        The retry path mutates the SAME ``payload`` dict in-place
        (``payload.pop("grammar", None)``), so a naive
        ``post_mock.call_args_list[i].kwargs["json"]`` snapshot
        sees the post-mutation state on both calls. We work around
        that by side-effect-capturing a deep copy of the body at
        each call site."""
        import copy

        cfg = _make_llamacpp_mock_config(local_grammar_enabled=True)
        bad = _make_400_resp("unknown field: grammar")
        good = _make_llamacpp_mock_resp()
        bodies: list[dict] = []
        responses = iter([bad, good])

        def _capturing_post(url, **kwargs):
            bodies.append(copy.deepcopy(kwargs.get("json") or {}))
            return next(responses)

        with patch(
            "androscan.llm.client.requests.post",
            side_effect=_capturing_post,
        ):
            result = _complete_llamacpp("test", cfg, stream=False, response_format="json")
        assert len(bodies) == 2
        assert "grammar" in bodies[0]
        assert "grammar" not in bodies[1]
        assert result.content == '{"hypotheses": []}'
        # Cache populated for the resolved llama.cpp base_url.
        cached = {url for url in _LLAMACPP_GRAMMAR_DISABLED_URLS}
        assert any("8033" in u for u in cached) or "http://127.0.0.1:8033/v1" in cached

    def test_400_unrelated_does_not_trigger_grammar_fallback(
        self, _reset_grammar_caches
    ) -> None:
        """A 400 with an unrelated error (e.g. context size exceeded)
        flows through the existing error path; no silent retry."""
        cfg = _make_llamacpp_mock_config(local_grammar_enabled=True)
        bad = _make_400_resp("context size exceeded")
        with patch(
            "androscan.llm.client.requests.post", return_value=bad,
        ) as post_mock:
            with pytest.raises(RuntimeError):
                _complete_llamacpp("test", cfg, stream=False, response_format="json")
        assert post_mock.call_count == 1
        # Cache still empty.
        assert not _LLAMACPP_GRAMMAR_DISABLED_URLS

    def test_response_format_none_skips_grammar(
        self, _reset_grammar_caches
    ) -> None:
        """Prose mode (``response_format=None``) is the workbench
        chat path — no grammar / no JSON enforcement at all."""
        cfg = _make_llamacpp_mock_config(local_grammar_enabled=True)
        with patch(
            "androscan.llm.client.requests.post",
            return_value=_make_llamacpp_mock_resp(content="prose text here"),
        ) as post_mock:
            _complete_llamacpp("test", cfg, stream=False, response_format=None)
        body = post_mock.call_args.kwargs["json"]
        assert "grammar" not in body
        assert "response_format" not in body
