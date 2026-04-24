"""LLM client: Ollama (local) and cloud providers via OpenAI-compatible API.

Ollama: HTTP POST to /api/chat with streaming, retries, system message, format json.
Cloud:  OpenAI SDK pointed at provider-specific base URLs (Gemini, OpenAI, Groq, etc.).
"""

import json
import logging
from dataclasses import dataclass
from typing import Any, Callable, Optional

import requests

from androscan.config import CLOUD_PROVIDERS, Config, load_config
from androscan.constants import OLLAMA_NUM_PREDICT_TIERS, OLLAMA_TIMEOUT_TIERS

_log = logging.getLogger(__name__)

OLLAMA_SETUP_TIP = "Ensure Ollama is running (e.g. ollama serve). See https://ollama.com"


@dataclass
class CompleteResult:
    content: str
    thinking: str
    metadata: dict[str, Any]


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
    return CompleteResult(
        content="".join(content_parts).strip(),
        thinking="".join(thinking_parts).strip(),
        metadata=metadata,
    )


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
    timeout = OLLAMA_TIMEOUT_TIERS[-1]

    payload: dict[str, Any] = {
        "model": model_name,
        "messages": messages,
        "stream": True,
        "options": {"temperature": temperature, "num_predict": num_predict},
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
# Public dispatcher — routes to Ollama or cloud based on config
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
    """Unified LLM call. Routes to Ollama or cloud provider based on ``config.llm_provider``.

    images: optional list of base64-encoded image strings for multimodal models.
    response_format: "json" (default, analysis pipeline) or None (prose).
        Currently honored only by the Ollama path; cloud responses are
        always JSON-formatted today.
    messages: optional pre-built message list. Honored by Ollama only;
        cloud path will raise if used (the workbench chat is Ollama-only).
    """
    _ = kwargs
    if config is None:
        config = load_config()

    if config.is_cloud:
        if messages is not None:
            raise NotImplementedError(
                "Pre-built messages are not yet supported for cloud providers; "
                "switch to ollama for the RE Workbench chat or extend _complete_cloud."
            )
        return _complete_cloud(
            prompt, config, model=model, system_content=system_content,
            stream=stream, on_token=on_token, on_thinking=on_thinking,
            run_logger=run_logger, images=images,
        )
    return _complete_ollama(
        prompt, config, model=model, system_content=system_content,
        stream=stream, on_token=on_token, on_thinking=on_thinking,
        run_logger=run_logger, images=images,
        response_format=response_format, messages=messages,
    )
