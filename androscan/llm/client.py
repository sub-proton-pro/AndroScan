"""Ollama client: HTTP POST to /api/chat. Uses config for base_url, timeout, model."""

from typing import Any, Optional

import requests

from androscan.config import Config, load_config

OLLAMA_SETUP_TIP = "Ensure Ollama is running (e.g. ollama serve). See https://ollama.com"


def is_ollama_available(base_url: str, timeout: int = 5) -> bool:
    """Return True if Ollama is reachable at base_url (GET /api/tags). Use short timeout for pre-flight."""
    url = (base_url or "").strip().rstrip("/") or "http://localhost:11434"
    try:
        resp = requests.get(f"{url}/api/tags", timeout=timeout)
        return resp.status_code == 200
    except (requests.ConnectionError, requests.Timeout):
        return False


def complete(
    prompt: str,
    config: Optional[Config] = None,
    model: Optional[str] = None,
    **kwargs: Any,
) -> str:
    """Call Ollama /api/chat and return raw response text.

    Uses config.ollama_base_url, config.ollama_timeout_sec, config.ollama_model.
    If config is None, loads via load_config(). On connection or API error, raises RuntimeError.
    """
    _ = kwargs
    if config is None:
        config = load_config()
    base_url = (config.ollama_base_url or "").strip().rstrip("/") or "http://localhost:11434"
    url = f"{base_url}/api/chat"
    payload = {
        "model": model or config.ollama_model or "llama2",
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
    }
    timeout = max(1, getattr(config, "ollama_timeout_sec", 120))

    try:
        resp = requests.post(url, json=payload, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        return (data.get("message", {}).get("content") or "").strip()
    except requests.ConnectionError:
        raise RuntimeError(
            f"Cannot connect to Ollama at {base_url}. Is it running? {OLLAMA_SETUP_TIP}"
        ) from None
    except requests.Timeout:
        raise RuntimeError(f"Ollama request timed out after {timeout}s. {OLLAMA_SETUP_TIP}") from None
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            raise RuntimeError(
                f"Ollama API endpoint not found at {base_url}. "
                f"Ensure Ollama is running and the URL is correct (e.g. http://localhost:11434). {OLLAMA_SETUP_TIP}"
            ) from None
        raise RuntimeError(f"Ollama API error: {e}. {OLLAMA_SETUP_TIP}") from e
