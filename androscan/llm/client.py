"""Ollama client: HTTP POST to /api/chat. Uses config for base_url, timeout, model."""

import json
import os
import time
from typing import Any, Optional

import requests

from androscan.config import Config, load_config

# #region agent log
_DEBUG_LOG = "/Users/sujoy.chakravarti/TW_Work/AI_security/Cursor_AI/AndroScan/.cursor/debug-795ffe.log"
def _dbg(msg: str, data: dict, hypothesis_id: str = "") -> None:
    try:
        os.makedirs(os.path.dirname(_DEBUG_LOG), exist_ok=True)
        with open(_DEBUG_LOG, "a") as f:
            f.write(json.dumps({"sessionId": "795ffe", "location": "client.py", "message": msg, "data": data, "timestamp": int(time.time() * 1000), "hypothesisId": hypothesis_id}) + "\n")
    except Exception:
        pass
# #endregion

OLLAMA_SETUP_TIP = "Ensure Ollama is running (e.g. ollama serve). See https://ollama.com"


def is_ollama_available(base_url: str, timeout: int = 5) -> bool:
    """Return True if Ollama is reachable at base_url (GET /api/tags). Use short timeout for pre-flight."""
    url = (base_url or "").strip().rstrip("/") or "http://localhost:11434"
    tags_url = f"{url}/api/tags"
    try:
        resp = requests.get(tags_url, timeout=timeout)
        # #region agent log
        _dbg("is_ollama_available", {"url": tags_url, "status_code": resp.status_code, "result": resp.status_code == 200}, "H_preflight")
        # #endregion
        return resp.status_code == 200
    except (requests.ConnectionError, requests.Timeout) as e:
        # #region agent log
        _dbg("is_ollama_available", {"url": tags_url, "error": type(e).__name__, "result": False}, "H_preflight")
        # #endregion
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
        "model": model or config.ollama_model or "qwen3.5:35b",
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
    }
    timeout = max(1, getattr(config, "ollama_timeout_sec", 120))

    # #region agent log
    _dbg("complete() request", {"base_url": base_url, "url": url, "model": payload["model"], "stream": payload["stream"]}, "H1_H2")
    # #endregion

    try:
        resp = requests.post(url, json=payload, timeout=timeout)
        # #region agent log
        _dbg("Ollama response", {"status_code": resp.status_code, "response_text_preview": (resp.text or "")[:500]}, "H3")
        # #endregion
        resp.raise_for_status()
        data = resp.json()
        content = (data.get("message", {}).get("content") or "").strip()
        # #region agent log
        _dbg("parse success", {"response_has_message": "message" in data, "content_len": len(content)}, "H5")
        # #endregion
        return content
    except requests.ConnectionError:
        # #region agent log
        _dbg("ConnectionError", {"error": "ConnectionError"}, "H4")
        # #endregion
        raise RuntimeError(
            f"Cannot connect to Ollama at {base_url}. Is it running? {OLLAMA_SETUP_TIP}"
        ) from None
    except requests.Timeout:
        # #region agent log
        _dbg("Timeout", {"error": "Timeout"}, "H4")
        # #endregion
        raise RuntimeError(f"Ollama request timed out after {timeout}s. {OLLAMA_SETUP_TIP}") from None
    except requests.HTTPError as e:
        # #region agent log
        _dbg("HTTPError", {"status_code": e.response.status_code if e.response else None, "response_text_preview": (e.response.text[:500] if e.response and e.response.text else "")}, "H3")
        # #endregion
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
                    f"Pull the model with: ollama pull {payload['model']}"
                ) from None
            raise RuntimeError(
                f"Ollama API endpoint not found at {base_url}. "
                f"Ensure Ollama is running and the URL is correct (e.g. http://localhost:11434). {OLLAMA_SETUP_TIP}"
            ) from None
        raise RuntimeError(f"Ollama API error: {e}. {OLLAMA_SETUP_TIP}") from e
