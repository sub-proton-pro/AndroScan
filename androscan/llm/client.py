"""Ollama client. Stub returns fixed JSON so CI/local runs need no Ollama.

Phase 3 will switch to real HTTP POST to config.ollama_base_url.
"""

from typing import Any, Optional

from androscan.config import Config


def complete(prompt: str, config: Optional[Config] = None, model: str = "llama2", **kwargs: Any) -> str:
    """Call Ollama (or stub) and return raw response text.

    Stub: ignores config and returns a fixed JSON string so no live Ollama is required.
    """
    _ = config
    _ = model
    _ = kwargs
    # Fixed response so workflow multi-turn and parser can be tested without Ollama.
    return '{"summary": "Stub analysis.", "hypotheses": [{"id": "H1", "component_type": "activity", "component_name": "com.example.app.MainActivity", "title": "Stub finding", "description": "Stub.", "evidence_refs": ["exported_activities[0]"], "exploitability": 3, "confidence": 2, "remediation_hint": "N/A"}]}'
