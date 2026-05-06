"""LLM client: Ollama / llama.cpp (local) and cloud providers.

Ollama:    HTTP POST /api/chat with JSONL streaming, native ``thinking``
           channel, ``format: json`` mode.
llama.cpp: HTTP POST /v1/chat/completions (OpenAI-compat shim from
           ``llama-server``), SSE streaming, ``response_format: {"type":
           "json_object"}`` mode, defensive ``<think>`` strip.
Cloud:     OpenAI SDK pointed at provider-specific base URLs (Gemini,
           OpenAI, Groq, Deepseek, Together, Mistral).

Routing happens in :func:`complete` via :meth:`Config.provider_kind`
(``local-ollama`` / ``local-openai-compat`` / ``cloud``).
"""

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Callable, Optional

import requests

from androscan.config import CLOUD_PROVIDERS, Config, load_config
from androscan.config.loader import LLM_PROVIDERS
from androscan.constants import OLLAMA_NUM_PREDICT_TIERS, OLLAMA_TIMEOUT_TIERS

_log = logging.getLogger(__name__)

OLLAMA_SETUP_TIP = "Ensure Ollama is running (e.g. ollama serve). See https://ollama.com"
LLAMACPP_SETUP_TIP = (
    "Ensure llama-server is running (e.g. llama-server -c 16384 -ngl 99 "
    "-fa --port 8033 --host 127.0.0.1 --jinja). See https://github.com/ggerganov/llama.cpp"
)


@dataclass
class CompleteResult:
    content: str
    thinking: str
    metadata: dict[str, Any]


# Match ``<think>...</think>`` blocks (case-insensitive, multi-line, lazy
# inner). Used by :func:`_strip_think_blocks` to defensively peel reasoning
# leak-through out of the content channel for both local providers.
_THINK_BLOCK_RE = re.compile(r"<think>(.*?)</think>", re.IGNORECASE | re.DOTALL)


def _strip_think_blocks(text: str) -> tuple[str, str]:
    """Strip ``<think>...</think>`` blocks from ``text`` and return both halves.

    Returns ``(content_without_think, extracted_thinking)`` where:

    * ``content_without_think`` is ``text`` with every ``<think>...</think>``
      block (and the surrounding whitespace) removed; the parser in
      :mod:`androscan.llm.parser` then sees clean JSON.
    * ``extracted_thinking`` is every captured think-block joined with a
      single newline, suitable for the :class:`CompleteResult.thinking`
      channel the workbench surfaces in the chat UI.

    Idempotent: if ``text`` has no think-blocks the function returns
    ``(text, "")`` unchanged. Defensive parity helper — Qwen3-family
    reasoning models can leak think-blocks into the content channel
    depending on the runtime's ``--reasoning-format`` flag (llama.cpp
    is the common offender; Ollama's ``message.thinking`` channel
    usually catches them but a malformed build can still leak).
    """
    if not text or "<think" not in text.lower():
        return text, ""
    captures = _THINK_BLOCK_RE.findall(text)
    if not captures:
        return text, ""
    cleaned = _THINK_BLOCK_RE.sub("", text).strip()
    extracted = "\n".join(c.strip() for c in captures if c and c.strip())
    return cleaned, extracted


def is_ollama_available(base_url: str, timeout: int = 5) -> tuple[bool, str]:
    """Check if Ollama is reachable at base_url (GET /api/tags).

    Returns (ok, detail) where detail is empty on success or a diagnostic message on failure.
    """
    url = (base_url or "").strip().rstrip("/") or "http://localhost:11434"
    tags_url = f"{url}/api/tags"
    try:
        resp = requests.get(tags_url, timeout=timeout)
        if resp.status_code == 200:
            return True, ""
        return False, f"HTTP {resp.status_code} from {tags_url}"
    except requests.ConnectionError:
        return False, f"Connection refused at {url}"
    except requests.Timeout:
        return False, f"Timeout connecting to {url}"


def _build_messages(
    system_content: Optional[str],
    user_content: str,
    images: Optional[list[str]] = None,
) -> list[dict[str, Any]]:
    msgs: list[dict[str, Any]] = []
    if system_content:
        msgs.append({"role": "system", "content": system_content})
    user_msg: dict[str, Any] = {"role": "user", "content": user_content}
    if images:
        user_msg["images"] = images
    msgs.append(user_msg)
    return msgs


def _parse_http_error(e: requests.HTTPError, base_url: str, payload: dict) -> None:
    if e.response is not None and e.response.status_code == 404:
        err_msg = ""
        try:
            body = e.response.json()
            err_msg = (body.get("error") or "").strip()
        except Exception:
            pass
        if err_msg and "model" in err_msg.lower() and "not found" in err_msg.lower():
            raise RuntimeError(
                f"Ollama reported: {err_msg}. "
                f"Pull the model with: ollama pull {payload.get('model', '')}"
            ) from None
        raise RuntimeError(
            f"Ollama API endpoint not found at {base_url}. "
            f"Ensure Ollama is running and the URL is correct (e.g. http://localhost:11434). {OLLAMA_SETUP_TIP}"
        ) from None
    status = e.response.status_code if e.response is not None else "unknown"
    detail = ""
    if e.response is not None:
        try:
            body = e.response.json()
            detail = (body.get("error") or "").strip()
        except Exception:
            detail = (e.response.text or "")[:200].strip()
    msg = f"Ollama API error (HTTP {status})"
    if detail:
        msg += f": {detail}"
    raise RuntimeError(f"{msg}. {OLLAMA_SETUP_TIP}") from e


def _do_request(
    url: str,
    payload: dict,
    timeout: int,
    stream: bool,
    on_token: Optional[Callable[[str], None]],
    on_thinking: Optional[Callable[[str], None]],
) -> CompleteResult:
    """Single request (stream or non-stream). Raises on HTTP/connection/timeout."""
    if stream:
        return _stream_request(url, payload, timeout, on_token, on_thinking)
    resp = requests.post(url, json=payload, timeout=timeout)
    resp.raise_for_status()
    try:
        data = resp.json()
    except (ValueError, json.JSONDecodeError):
        body_preview = (resp.text or "")[:300]
        raise RuntimeError(
            f"Ollama returned non-JSON response (HTTP {resp.status_code}). "
            f"Body preview: {body_preview!r}"
        ) from None
    msg = data.get("message") or {}
    content = (msg.get("content") or "").strip()
    thinking = (msg.get("thinking") or "").strip()
    # Defensive parity with the llama.cpp path — strip any <think> blocks
    # that leaked into the content channel and merge them with the
    # native Ollama thinking channel. Idempotent for clean responses.
    content, leaked = _strip_think_blocks(content)
    if leaked:
        thinking = f"{thinking}\n{leaked}".strip() if thinking else leaked
    metadata = {
        "done_reason": data.get("done_reason"),
        "total_duration": data.get("total_duration"),
        "eval_count": data.get("eval_count"),
        "prompt_eval_count": data.get("prompt_eval_count"),
    }
    return CompleteResult(content=content, thinking=thinking, metadata=metadata)


def _stream_request(
    url: str,
    payload: dict,
    timeout: int,
    on_token: Optional[Callable[[str], None]],
    on_thinking: Optional[Callable[[str], None]],
) -> CompleteResult:
    content_parts: list[str] = []
    thinking_parts: list[str] = []
    metadata: dict[str, Any] = {}
    payload["stream"] = True
    with requests.post(url, json=payload, timeout=timeout, stream=True) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines(decode_unicode=True):
            if not line:
                continue
            try:
                chunk = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg = chunk.get("message") or {}
            if msg.get("thinking"):
                thinking_parts.append(msg["thinking"])
                if on_thinking:
                    on_thinking(msg["thinking"])
            if msg.get("content"):
                content_parts.append(msg["content"])
                if on_token:
                    on_token(msg["content"])
            if chunk.get("done"):
                metadata = {
                    "done_reason": chunk.get("done_reason"),
                    "total_duration": chunk.get("total_duration"),
                    "eval_count": chunk.get("eval_count"),
                    "prompt_eval_count": chunk.get("prompt_eval_count"),
                }
    content = "".join(content_parts).strip()
    thinking = "".join(thinking_parts).strip()
    # Same defensive <think> strip as the non-stream path — see
    # _do_request for the rationale.
    content, leaked = _strip_think_blocks(content)
    if leaked:
        thinking = f"{thinking}\n{leaked}".strip() if thinking else leaked
    return CompleteResult(content=content, thinking=thinking, metadata=metadata)


def stream_complete(
    config: Config,
    messages: list[dict[str, Any]],
    *,
    on_token: Optional[Callable[[str], None]] = None,
    on_thinking: Optional[Callable[[str], None]] = None,
    response_format: Optional[str] = None,
    model: Optional[str] = None,
) -> CompleteResult:
    """Single-shot streaming call against Ollama.

    Used by the workbench chat SSE endpoint where re-streaming on a
    truncation retry (as ``_complete_ollama()`` would do) is
    unacceptable — duplicate tokens would land in the user's message.
    We make exactly one HTTP request and let the caller surface
    ``done_reason == "length"`` to the user via the terminal SSE event.

    Cloud providers don't currently flow through this helper; the
    workbench SSE endpoint is Ollama-only by design (matches the
    streaming/thinking semantics of the local model).
    """
    base_url = (config.ollama_base_url or "").strip().rstrip("/") or "http://localhost:11434"
    url = f"{base_url}/api/chat"
    model_name = model or getattr(config, "ollama_model", "qwen3.5:35b") or "qwen3.5:35b"
    temperature = getattr(config, "ollama_temperature", 0.2)
    num_predict = getattr(config, "ollama_num_predict", 8192)
    # Phase 11 sub-step 11.6 / DEC-025 — forward num_ctx to Ollama
    # alongside num_predict / temperature. Ollama's default context
    # window is 8192; v2's deeper inter-procedural slicer chains can
    # squeeze that, so the operator-tunable Config knob is bumped to
    # 16384 by default. ``getattr`` fallback preserves callers using
    # a ``MagicMock`` config in tests.
    num_ctx = getattr(config, "ollama_num_ctx", 16384)
    timeout = OLLAMA_TIMEOUT_TIERS[-1]

    payload: dict[str, Any] = {
        "model": model_name,
        "messages": messages,
        "stream": True,
        "options": {
            "temperature": temperature,
            "num_predict": num_predict,
            "num_ctx": num_ctx,
        },
    }
    if response_format:
        payload["format"] = response_format

    try:
        return _stream_request(url, payload, timeout, on_token, on_thinking)
    except requests.ConnectionError:
        raise RuntimeError(
            f"Cannot connect to Ollama at {base_url}. Is it running? {OLLAMA_SETUP_TIP}"
        ) from None
    except requests.Timeout:
        raise RuntimeError(
            f"Ollama request timed out after {timeout}s. {OLLAMA_SETUP_TIP}"
        ) from None
    except requests.HTTPError as e:
        _parse_http_error(e, base_url, payload)
        raise  # _parse_http_error always raises, but mypy doesn't know that


def _complete_ollama(
    prompt: str,
    config: Config,
    model: Optional[str] = None,
    system_content: Optional[str] = None,
    stream: bool = True,
    on_token: Optional[Callable[[str], None]] = None,
    on_thinking: Optional[Callable[[str], None]] = None,
    run_logger: Optional[Any] = None,
    images: Optional[list[str]] = None,
    response_format: Optional[str] = "json",
    messages: Optional[list[dict[str, Any]]] = None,
) -> CompleteResult:
    """Call Ollama /api/chat. Uses timeout and num_predict retry tiers.

    response_format: "json" (default, for the analysis pipeline) or
        None (prose; e.g. the RE Workbench chat).
    messages: optional pre-built message list (overrides
        ``system_content`` + ``prompt``). The workbench builds these
        with its own guardrails before calling.
    """
    base_url = (config.ollama_base_url or "").strip().rstrip("/") or "http://localhost:11434"
    url = f"{base_url}/api/chat"
    model_name = model or getattr(config, "ollama_model", "qwen3.5:35b") or "qwen3.5:35b"
    temperature = getattr(config, "ollama_temperature", 0.2)
    # Phase 11 sub-step 11.6 / DEC-025 — see ``_stream_ollama`` for
    # the rationale on the num_ctx forward.
    num_ctx = getattr(config, "ollama_num_ctx", 16384)

    timeout_idx = 0
    num_predict_idx = 0
    msgs = messages if messages is not None else _build_messages(system_content, prompt, images=images)

    while True:
        timeout = OLLAMA_TIMEOUT_TIERS[timeout_idx]
        current_num_predict = OLLAMA_NUM_PREDICT_TIERS[min(num_predict_idx, len(OLLAMA_NUM_PREDICT_TIERS) - 1)]
        payload: dict[str, Any] = {
            "model": model_name,
            "messages": msgs,
            "stream": stream,
            "options": {
                "temperature": temperature,
                "num_predict": current_num_predict,
                "num_ctx": num_ctx,
            },
        }
        if response_format:
            payload["format"] = response_format

        try:
            result = _do_request(url, payload, timeout, stream, on_token, on_thinking)
        except requests.ConnectionError:
            raise RuntimeError(
                f"Cannot connect to Ollama at {base_url}. Is it running? {OLLAMA_SETUP_TIP}"
            ) from None
        except requests.Timeout:
            if timeout_idx + 1 < len(OLLAMA_TIMEOUT_TIERS):
                next_timeout = OLLAMA_TIMEOUT_TIERS[timeout_idx + 1]
                if run_logger:
                    run_logger.log_retry("timeout", f"Retrying with timeout {next_timeout}s")
                timeout_idx += 1
                continue
            raise RuntimeError(
                f"Ollama request timed out after {timeout}s. {OLLAMA_SETUP_TIP}"
            ) from None
        except requests.HTTPError as e:
            _parse_http_error(e, base_url, payload)

        done_reason = result.metadata.get("done_reason")
        content = result.content
        truncated = done_reason == "length"
        empty_content = not content
        if truncated or empty_content:
            if num_predict_idx + 1 < len(OLLAMA_NUM_PREDICT_TIERS):
                next_np = OLLAMA_NUM_PREDICT_TIERS[num_predict_idx + 1]
                if run_logger:
                    run_logger.log_retry("num_predict", f"Response truncated or empty, retrying with num_predict={next_np}")
                num_predict_idx += 1
                continue
            raise RuntimeError(
                "Ollama response was truncated or empty (insufficient num_predict). "
                "Increase ollama.num_predict in config."
            )
        return result


# ---------------------------------------------------------------------------
# Cloud provider via OpenAI-compatible SDK
# ---------------------------------------------------------------------------

_CLOUD_TIMEOUT_TIERS = [120, 240, 480]


def _build_cloud_messages(
    system_content: Optional[str],
    user_content: str,
    images: Optional[list[str]] = None,
) -> list[dict[str, Any]]:
    """Build OpenAI-format messages with optional multimodal image parts."""
    msgs: list[dict[str, Any]] = []
    if system_content:
        msgs.append({"role": "system", "content": system_content})

    if images:
        content_parts: list[dict[str, Any]] = [{"type": "text", "text": user_content}]
        for b64 in images:
            content_parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{b64}"},
            })
        msgs.append({"role": "user", "content": content_parts})
    else:
        msgs.append({"role": "user", "content": user_content})
    return msgs


def _complete_cloud(
    prompt: str,
    config: Config,
    model: Optional[str] = None,
    system_content: Optional[str] = None,
    stream: bool = True,
    on_token: Optional[Callable[[str], None]] = None,
    on_thinking: Optional[Callable[[str], None]] = None,
    run_logger: Optional[Any] = None,
    images: Optional[list[str]] = None,
) -> CompleteResult:
    """Call a cloud LLM via OpenAI-compatible endpoint. Supports all providers in CLOUD_PROVIDERS."""
    try:
        from openai import OpenAI, APITimeoutError, APIConnectionError, APIStatusError
    except ImportError:
        raise RuntimeError(
            "openai package is required for cloud providers. Install with: pip install openai"
        ) from None

    provider = config.llm_provider
    api_key = config.resolve_cloud_api_key()
    if not api_key:
        provider_info = CLOUD_PROVIDERS.get(provider, {})
        key_env = provider_info.get("key_env", "???")
        raise RuntimeError(
            f"No API key for provider '{provider}'. "
            f"Set env var {key_env} or pass --cloud-api-key."
        )

    base_url = config.resolve_cloud_base_url()
    if not base_url:
        raise RuntimeError(
            f"Unknown cloud provider '{provider}'. "
            f"Supported: {', '.join(sorted(CLOUD_PROVIDERS.keys()))}"
        )

    model_name = model or config.cloud_model
    if not model_name:
        raise RuntimeError(
            f"No model specified for cloud provider '{provider}'. Use --model <name>."
        )

    temperature = config.cloud_temperature
    messages = _build_cloud_messages(system_content, prompt, images=images)

    client = OpenAI(api_key=api_key, base_url=base_url)

    timeout_idx = 0
    while True:
        timeout = _CLOUD_TIMEOUT_TIERS[min(timeout_idx, len(_CLOUD_TIMEOUT_TIERS) - 1)]
        try:
            if stream:
                content_parts: list[str] = []
                thinking_parts: list[str] = []
                resp_stream = client.chat.completions.create(
                    model=model_name,
                    messages=messages,
                    temperature=temperature,
                    response_format={"type": "json_object"},
                    stream=True,
                    timeout=timeout,
                )
                for chunk in resp_stream:
                    delta = chunk.choices[0].delta if chunk.choices else None
                    if delta and delta.content:
                        content_parts.append(delta.content)
                        if on_token:
                            on_token(delta.content)
                content = "".join(content_parts).strip()
                thinking = "".join(thinking_parts).strip()
                finish_reason = None
                if chunk and chunk.choices:
                    finish_reason = chunk.choices[0].finish_reason
                metadata = {
                    "done_reason": finish_reason,
                    "provider": provider,
                    "model": model_name,
                }
            else:
                resp = client.chat.completions.create(
                    model=model_name,
                    messages=messages,
                    temperature=temperature,
                    response_format={"type": "json_object"},
                    stream=False,
                    timeout=timeout,
                )
                choice = resp.choices[0] if resp.choices else None
                content = (choice.message.content or "").strip() if choice else ""
                thinking = ""
                metadata = {
                    "done_reason": choice.finish_reason if choice else None,
                    "provider": provider,
                    "model": model_name,
                    "usage": {
                        "prompt_tokens": resp.usage.prompt_tokens if resp.usage else 0,
                        "completion_tokens": resp.usage.completion_tokens if resp.usage else 0,
                        "total_tokens": resp.usage.total_tokens if resp.usage else 0,
                    },
                }

        except APITimeoutError:
            if timeout_idx + 1 < len(_CLOUD_TIMEOUT_TIERS):
                next_timeout = _CLOUD_TIMEOUT_TIERS[timeout_idx + 1]
                _log.warning("Cloud API timeout after %ds, retrying with %ds", timeout, next_timeout)
                if run_logger:
                    run_logger.log_retry("timeout", f"Cloud API timed out, retrying with timeout {next_timeout}s")
                timeout_idx += 1
                continue
            raise RuntimeError(
                f"Cloud API ({provider}) timed out after {timeout}s."
            ) from None
        except APIConnectionError as e:
            raise RuntimeError(
                f"Cannot connect to {provider} API at {base_url}: {e}"
            ) from None
        except APIStatusError as e:
            status = e.status_code
            detail = str(e.message) if hasattr(e, "message") else str(e)
            if status == 429:
                _log.warning("Cloud API rate limit hit, retrying in 5s...")
                if run_logger:
                    run_logger.log_retry("rate_limit", "Cloud API rate limit, retrying after delay")
                import time
                time.sleep(5)
                continue
            raise RuntimeError(
                f"Cloud API error ({provider}, HTTP {status}): {detail}"
            ) from None

        if not content:
            raise RuntimeError(
                f"Cloud API ({provider}) returned empty response for model {model_name}."
            )

        _log.info(
            "Cloud API response: provider=%s model=%s tokens=%s",
            provider, model_name, metadata.get("usage", "N/A"),
        )
        return CompleteResult(content=content, thinking=thinking, metadata=metadata)


# ---------------------------------------------------------------------------
# Local llama.cpp via ``llama-server`` OpenAI-compat HTTP shim
# ---------------------------------------------------------------------------
#
# Architectural note (DEC-027 / LCP.2): kept on raw ``requests`` rather
# than the ``openai`` SDK to mirror :func:`_complete_ollama` (both are
# loopback, no API key, simple JSON request/response). The cloud
# section keeps the SDK because it needs the SDK's per-vendor quirks
# (e.g. Together's prompt token accounting, Mistral's safe-mode flag
# routing). v1 reuses the existing ``ollama_*`` Config fields for
# temperature + max_tokens; LCP.4 introduces parallel ``llamacpp_*``
# fields if operators report drift.

_LLAMACPP_TIMEOUT_TIERS = [120, 240, 480]
_LLAMACPP_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})
LLAMACPP_DEFAULT_MODEL_LABEL = "local-llamacpp"


def _resolve_llamacpp_base_url(config: Config) -> str:
    """Return the base URL for the configured ``llama-server`` instance.

    Resolution order (LCP.2):

    1. ``config.llamacpp_base_url`` if the field exists (LCP.4 adds it
       to :class:`Config`; v1 ``Config`` lacks the field, so this
       branch is dormant in LCP.2).
    2. The registry default ``LLM_PROVIDERS["local"]["llamacpp"]
       ["base_url_default"]`` (= ``http://127.0.0.1:8033/v1`` per
       DEC-027 Q4).

    The trailing ``/v1`` suffix is preserved — :meth:`complete` joins
    ``/chat/completions`` directly so the OpenAI-compat path works
    without further mangling.
    """
    custom = getattr(config, "llamacpp_base_url", None)
    if custom:
        return str(custom).strip().rstrip("/")
    fallback = LLM_PROVIDERS["local"]["llamacpp"]["base_url_default"]
    return str(fallback).strip().rstrip("/")


def _build_llamacpp_messages(
    system_content: Optional[str],
    user_content: str,
    images: Optional[list[str]] = None,
) -> list[dict[str, Any]]:
    """Build OpenAI-compat ``messages`` for the ``/v1/chat/completions`` body.

    Identical wire-shape to :func:`_build_cloud_messages` because
    ``llama-server`` follows the same OpenAI-compat schema; kept as a
    separate function so a future image-handling tweak (llama.cpp's
    multimodal support is GGUF-bundle-dependent) doesn't have to
    branch inside the cloud builder.
    """
    msgs: list[dict[str, Any]] = []
    if system_content:
        msgs.append({"role": "system", "content": system_content})

    if images:
        content_parts: list[dict[str, Any]] = [{"type": "text", "text": user_content}]
        for b64 in images:
            content_parts.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{b64}"},
            })
        msgs.append({"role": "user", "content": content_parts})
    else:
        msgs.append({"role": "user", "content": user_content})
    return msgs


def _stream_llamacpp_sse(
    url: str,
    payload: dict,
    timeout: int,
    on_token: Optional[Callable[[str], None]],
    on_thinking: Optional[Callable[[str], None]],
) -> CompleteResult:
    """Parse OpenAI-style SSE chunks from ``llama-server``.

    Each frame is a line of the form ``data: {json}`` or the sentinel
    ``data: [DONE]``. Empty lines / unrelated lines are tolerated and
    skipped. ``finish_reason`` from the last terminating chunk feeds
    the same metadata shape :class:`CompleteResult` uses for the
    Ollama + cloud paths.
    """
    content_parts: list[str] = []
    finish_reason: Optional[str] = None
    payload["stream"] = True
    with requests.post(url, json=payload, timeout=timeout, stream=True) as resp:
        resp.raise_for_status()
        for line in resp.iter_lines(decode_unicode=True):
            if not line:
                continue
            if not line.startswith("data:"):
                continue
            body = line[5:].strip()
            if not body or body == "[DONE]":
                continue
            try:
                chunk = json.loads(body)
            except json.JSONDecodeError:
                continue
            choices = chunk.get("choices") or []
            if not choices:
                continue
            choice = choices[0] or {}
            delta = choice.get("delta") or {}
            piece = delta.get("content")
            if piece:
                content_parts.append(piece)
                if on_token:
                    on_token(piece)
            fr = choice.get("finish_reason")
            if fr:
                finish_reason = fr

    raw_content = "".join(content_parts).strip()
    content, leaked_thinking = _strip_think_blocks(raw_content)
    if leaked_thinking and on_thinking:
        on_thinking(leaked_thinking)
    metadata = {"done_reason": finish_reason}
    return CompleteResult(content=content, thinking=leaked_thinking, metadata=metadata)


def _llamacpp_status_is_retryable(resp: Optional[requests.Response]) -> bool:
    if resp is None:
        return False
    try:
        return resp.status_code in _LLAMACPP_RETRYABLE_STATUSES
    except Exception:
        return False


def _complete_llamacpp(
    prompt: str,
    config: Config,
    model: Optional[str] = None,
    system_content: Optional[str] = None,
    stream: bool = True,
    on_token: Optional[Callable[[str], None]] = None,
    on_thinking: Optional[Callable[[str], None]] = None,
    run_logger: Optional[Any] = None,
    images: Optional[list[str]] = None,
    response_format: Optional[str] = "json",
    messages: Optional[list[dict[str, Any]]] = None,
) -> CompleteResult:
    """Call llama.cpp's ``llama-server`` ``/v1/chat/completions`` shim.

    Parallel to :func:`_complete_ollama` but speaks the OpenAI-compat
    wire format: ``messages`` array, ``temperature``, ``max_tokens``,
    optional ``response_format: {"type": "json_object"}``. Streaming
    is the OpenAI SSE flavour (``data: {...}\\n\\n``) parsed by
    :func:`_stream_llamacpp_sse`.

    v1 fields (LCP.2 introduced; LCP.4 wired the dedicated knobs):
      * ``base_url``   ← :func:`_resolve_llamacpp_base_url`
        (Config.llamacpp_base_url, falling back to the registry default
        ``http://127.0.0.1:8033/v1``)
      * ``model``      ← ``model`` arg or ``config.llamacpp_model`` or
        :data:`LLAMACPP_DEFAULT_MODEL_LABEL`. ``llama-server`` ignores
        the request-body model field; the actual model is the GGUF
        loaded at server startup. The Config field is purely a label
        for log/metric readability — operators write their GGUF
        identifier (e.g. ``qwen3-27b-q5km``) in Settings.
      * ``temperature`` ← ``config.ollama_temperature`` (shared across
        both local providers — operators rarely tune this differently
        between Ollama and llama.cpp).
      * ``max_tokens``  ← ``config.llamacpp_max_tokens`` (LCP.4 added
        the dedicated knob; falls back to ``config.ollama_num_predict``
        when the new field is absent so MagicMock-based unit tests
        from LCP.2 keep working without per-test edits).
      * ``response_format`` ← ``{"type": "json_object"}`` when
        ``response_format == "json"`` (Ollama parity); GBNF grammar
        enforcement is the LCP.6 follow-up.

    Retry posture mirrors the cloud path: timeout-tier escalation
    (120s → 240s → 480s) for :class:`requests.Timeout` and the
    retryable HTTP status set ``{429, 500, 502, 503, 504}``.
    Connection errors fail fast (``llama-server`` not running is the
    common case and a setup hint is the right operator nudge).
    """
    base_url = _resolve_llamacpp_base_url(config)
    if not base_url:
        raise RuntimeError(
            "llama.cpp base_url is empty. Set llamacpp.base_url in "
            "global_config.yaml (default http://127.0.0.1:8033/v1) or "
            "ANDROSCAN_LLAMACPP_BASE_URL env var."
        )
    url = f"{base_url}/chat/completions"

    model_name = (
        model
        or getattr(config, "llamacpp_model", None)
        or LLAMACPP_DEFAULT_MODEL_LABEL
    )
    temperature = getattr(config, "ollama_temperature", 0.2)
    # LCP.4: prefer the dedicated llamacpp_max_tokens field, falling
    # back to ollama_num_predict for backwards-compat with the MagicMock
    # configs in tests/test_llm_client.py that pre-date the new field.
    max_tokens = getattr(config, "llamacpp_max_tokens", None)
    if not max_tokens:
        max_tokens = getattr(config, "ollama_num_predict", OLLAMA_NUM_PREDICT_TIERS[0])

    if messages is not None:
        msgs = messages
    else:
        msgs = _build_llamacpp_messages(system_content, prompt, images=images)

    payload: dict[str, Any] = {
        "model": model_name,
        "messages": msgs,
        "stream": stream,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if response_format == "json":
        payload["response_format"] = {"type": "json_object"}

    timeout_idx = 0
    retry_idx = 0
    while True:
        timeout = _LLAMACPP_TIMEOUT_TIERS[
            min(timeout_idx, len(_LLAMACPP_TIMEOUT_TIERS) - 1)
        ]
        try:
            if stream:
                result = _stream_llamacpp_sse(url, payload, timeout, on_token, on_thinking)
            else:
                resp = requests.post(url, json=payload, timeout=timeout)
                resp.raise_for_status()
                try:
                    data = resp.json()
                except (ValueError, json.JSONDecodeError):
                    body_preview = (resp.text or "")[:300]
                    raise RuntimeError(
                        f"llama.cpp returned non-JSON response (HTTP "
                        f"{resp.status_code}). Body preview: {body_preview!r}"
                    ) from None
                choices = data.get("choices") or []
                choice = choices[0] if choices else {}
                msg = choice.get("message") or {}
                raw_content = (msg.get("content") or "").strip()
                content, leaked_thinking = _strip_think_blocks(raw_content)
                metadata = {
                    "done_reason": choice.get("finish_reason"),
                    "usage": {
                        "prompt_tokens": (data.get("usage") or {}).get("prompt_tokens", 0),
                        "completion_tokens": (data.get("usage") or {}).get("completion_tokens", 0),
                        "total_tokens": (data.get("usage") or {}).get("total_tokens", 0),
                    },
                }
                result = CompleteResult(
                    content=content, thinking=leaked_thinking, metadata=metadata,
                )
        except requests.ConnectionError:
            raise RuntimeError(
                f"Cannot connect to llama-server at {base_url}. Is it running? "
                f"{LLAMACPP_SETUP_TIP}"
            ) from None
        except requests.Timeout:
            if timeout_idx + 1 < len(_LLAMACPP_TIMEOUT_TIERS):
                next_timeout = _LLAMACPP_TIMEOUT_TIERS[timeout_idx + 1]
                _log.warning(
                    "llama.cpp timeout after %ds, retrying with %ds", timeout, next_timeout,
                )
                if run_logger:
                    run_logger.log_retry(
                        "timeout",
                        f"llama.cpp request timed out, retrying with timeout {next_timeout}s",
                    )
                timeout_idx += 1
                continue
            raise RuntimeError(
                f"llama.cpp request timed out after {timeout}s. {LLAMACPP_SETUP_TIP}"
            ) from None
        except requests.HTTPError as e:
            resp = e.response if hasattr(e, "response") else None
            if _llamacpp_status_is_retryable(resp) and retry_idx < 2:
                retry_idx += 1
                if run_logger:
                    status = resp.status_code if resp is not None else "unknown"
                    run_logger.log_retry(
                        "http",
                        f"llama.cpp HTTP {status}, retry {retry_idx}/2",
                    )
                continue
            status = resp.status_code if resp is not None else "unknown"
            detail = ""
            if resp is not None:
                try:
                    body = resp.json()
                    detail = (body.get("error") or "").strip() if isinstance(body, dict) else ""
                except Exception:
                    detail = (resp.text or "")[:200].strip()
            msg = f"llama.cpp API error (HTTP {status})"
            if detail:
                msg += f": {detail}"
            raise RuntimeError(f"{msg}. {LLAMACPP_SETUP_TIP}") from e

        if not result.content and not result.thinking:
            raise RuntimeError(
                "llama.cpp returned empty response. Check that the model "
                "loaded successfully and that the request fits within the "
                f"server-side --ctx-size budget. {LLAMACPP_SETUP_TIP}"
            )
        return result


# ---------------------------------------------------------------------------
# Public dispatcher — routes via Config.provider_kind() to one of three paths
# ---------------------------------------------------------------------------

def complete(
    prompt: str,
    config: Optional[Config] = None,
    model: Optional[str] = None,
    system_content: Optional[str] = None,
    stream: bool = True,
    on_token: Optional[Callable[[str], None]] = None,
    on_thinking: Optional[Callable[[str], None]] = None,
    run_logger: Optional[Any] = None,
    images: Optional[list[str]] = None,
    response_format: Optional[str] = "json",
    messages: Optional[list[dict[str, Any]]] = None,
    **kwargs: Any,
) -> CompleteResult:
    """Unified LLM call. Routes via :meth:`Config.provider_kind` (DEC-027).

    Three-way switch added in LCP.2:

    * ``"local-ollama"``        → :func:`_complete_ollama`
    * ``"local-openai-compat"`` → :func:`_complete_llamacpp`
    * ``"cloud"``               → :func:`_complete_cloud`

    images: optional list of base64-encoded image strings for multimodal models.
    response_format: "json" (default, analysis pipeline) or None (prose).
        Honored by both local paths (Ollama via ``format: "json"``,
        llama.cpp via ``response_format: {"type": "json_object"}``);
        cloud responses are always JSON-formatted today.
    messages: optional pre-built message list. Honored by both local
        paths (Ollama + llama.cpp); cloud path will raise if used
        (the workbench chat is local-only).
    """
    _ = kwargs
    if config is None:
        config = load_config()

    kind = config.provider_kind()
    if kind == "local-ollama":
        return _complete_ollama(
            prompt, config, model=model, system_content=system_content,
            stream=stream, on_token=on_token, on_thinking=on_thinking,
            run_logger=run_logger, images=images,
            response_format=response_format, messages=messages,
        )
    if kind == "local-openai-compat":
        return _complete_llamacpp(
            prompt, config, model=model, system_content=system_content,
            stream=stream, on_token=on_token, on_thinking=on_thinking,
            run_logger=run_logger, images=images,
            response_format=response_format, messages=messages,
        )
    # kind == "cloud" (also the typo-fallback path; see Config.provider_kind)
    if messages is not None:
        raise NotImplementedError(
            "Pre-built messages are not yet supported for cloud providers; "
            "switch to a local provider for the RE Workbench chat or extend _complete_cloud."
        )
    return _complete_cloud(
        prompt, config, model=model, system_content=system_content,
        stream=stream, on_token=on_token, on_thinking=on_thinking,
        run_logger=run_logger, images=images,
    )
