"""LLM layer: Ollama client, prompts, response parsing."""

from androscan.llm.client import CompleteResult, complete, is_ollama_available
from androscan.llm.parser import LLMResponse, Hypothesis, SkillRequest, parse_response
from androscan.llm.prompts import (
    build_component_prompt,
    build_consolidation_prompt,
    build_consolidation_system_content,
    build_prompt,
    build_system_content,
    iter_dossier_components,
)

__all__ = [
    "complete",
    "CompleteResult",
    "build_prompt",
    "build_component_prompt",
    "build_consolidation_prompt",
    "build_consolidation_system_content",
    "build_system_content",
    "iter_dossier_components",
    "is_ollama_available",
    "parse_response",
    "LLMResponse",
    "Hypothesis",
    "SkillRequest",
]
