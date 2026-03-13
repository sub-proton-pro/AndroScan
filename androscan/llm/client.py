"""Ollama client: HTTP POST to /api/generate. Uses config for base_url, timeout, model."""

from typing import Any, Optional

import requests

from androscan.config import Config, load_config


def complete(
    prompt: str,
    config: Optional[Config] = None,
    model: Optional[str] = None,
    **kwargs: Any,
) -> str:
    """Call Ollama /api/generate and return raw response text.

    Uses config.ollama_base_url, config.ollama_timeout_sec, config.ollama_model.
    If config is None, loads via load_config(). On connection or API error, raises RuntimeError.
    """
    _ = kwargs
    if config is None:
        config = load_config()
    base_url = (config.ollama_base_url or "").strip().rstrip("/") or "http://localhost:11434"
    url = f"{base_url}/api/generate"
    payload = {
        "model": model or config.ollama_model or "llama2",
        "prompt": prompt,
        "stream": False,
    }
    timeout = max(1, getattr(config, "ollama_timeout_sec", 120))

    try:
        resp = requests.post(url, json=payload, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        return (data.get("response") or "").strip()
    except requests.ConnectionError:
        raise RuntimeError(f"Cannot connect to Ollama at {base_url}. Is it running?") from None
    except requests.Timeout:
        raise RuntimeError(f"Ollama request timed out after {timeout}s") from None
    except requests.HTTPError as e:
        raise RuntimeError(f"Ollama API error: {e}") from e
