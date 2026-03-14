"""Ollama client: HTTP POST to /api/chat. Streaming, retries, system message, format json."""

import json
from dataclasses import dataclass
from typing import Any, Callable, Optional

import requests

from androscan.config import Config, load_config
from androscan.constants import OLLAMA_NUM_PREDICT_TIERS, OLLAMA_TIMEOUT_TIERS

OLLAMA_SETUP_TIP = "Ensure Ollama is running (e.g. ollama serve). See https://ollama.com"


@dataclass
class CompleteResult:
    content: str
    thinking: str
    metadata: dict[str, Any]


def is_ollama_available(base_url: str, timeout: int = 5) -> bool:
    """Return True if Ollama is reachable at base_url (GET /api/tags). Use short timeout for pre-flight."""
    url = (base_url or "").strip().rstrip("/") or "http://localhost:11434"
    tags_url = f"{url}/api/tags"
    try:
        resp = requests.get(tags_url, timeout=timeout)
        return resp.status_code == 200
    except (requests.ConnectionError, requests.Timeout):
        return False


def _build_messages(system_content: Optional[str], user_content: str) -> list[dict[str, Any]]:
    if system_content:
        return [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_content},
        ]
    return [{"role": "user", "content": user_content}]


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
    raise RuntimeError(f"Ollama API error: {e}. {OLLAMA_SETUP_TIP}") from e


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
    data = resp.json()
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


def complete(
    prompt: str,
    config: Optional[Config] = None,
    model: Optional[str] = None,
    system_content: Optional[str] = None,
    stream: bool = True,
    on_token: Optional[Callable[[str], None]] = None,
    on_thinking: Optional[Callable[[str], None]] = None,
    run_logger: Optional[Any] = None,
    **kwargs: Any,
) -> CompleteResult:
    """Call Ollama /api/chat. Uses timeout and num_predict retry tiers. Returns content, thinking, metadata."""
    _ = kwargs
    if config is None:
        config = load_config()
    base_url = (config.ollama_base_url or "").strip().rstrip("/") or "http://localhost:11434"
    url = f"{base_url}/api/chat"
    model_name = model or getattr(config, "ollama_model", "qwen3.5:35b") or "qwen3.5:35b"
    temperature = getattr(config, "ollama_temperature", 0.2)
    num_predict = getattr(config, "ollama_num_predict", 8192)

    timeout_idx = 0
    num_predict_idx = 0
    messages = _build_messages(system_content, prompt)

    while True:
        timeout = OLLAMA_TIMEOUT_TIERS[timeout_idx]
        current_num_predict = OLLAMA_NUM_PREDICT_TIERS[min(num_predict_idx, len(OLLAMA_NUM_PREDICT_TIERS) - 1)]
        payload = {
            "model": model_name,
            "messages": messages,
            "stream": stream,
            "format": "json",
            "options": {
                "temperature": temperature,
                "num_predict": current_num_predict,
            },
        }
        # Omit "think" for compatibility: older Ollama versions return 400 if they don't support it

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
        empty_content = not content and done_reason != "stop"
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
