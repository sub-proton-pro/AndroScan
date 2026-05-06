"""Load config from global_config.yaml and environment.

Merge order: defaults (constants) -> global_config.yaml (if present) -> env vars.
Env vars override file. Pass config file path via --config or use default search paths.
"""

import dataclasses
import os
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import yaml

from androscan import constants


# LLM provider registry — top-level table with two sub-sections splitting
# providers by transport story:
#
#   * ``LLM_PROVIDERS["local"]``: providers running on the operator's
#     own machine. Reached over loopback HTTP without an API key. Each
#     entry has its own request-shape (Ollama's ``/api/chat`` vs
#     llama.cpp's OpenAI-compat ``/v1/chat/completions``); the ``kind``
#     tag tells the LLM client which dispatch path to take.
#
#   * ``LLM_PROVIDERS["cloud"]``: hosted vendors reached via the OpenAI
#     Python SDK against provider-specific base URLs. Each entry has a
#     ``key_env`` pointing at the env var that supplies the API key.
#
# DEC-027 (LCP track) introduced this structure as a refactor of the
# previously-flat ``CLOUD_PROVIDERS`` dict. ``CLOUD_PROVIDERS`` is
# preserved below as a backwards-compat alias so existing imports
# (``from androscan.config import CLOUD_PROVIDERS``) keep resolving to
# the same six-entry mapping they did pre-DEC-027 — no per-call edits
# needed at LCP.1, the dispatch-side rewrite is deferred to LCP.2.
LLM_PROVIDERS: dict[str, dict[str, dict[str, Any]]] = {
    "local": {
        "ollama": {
            "base_url_default": "http://localhost:11434",
            "kind": "local-ollama",
            "key_env": None,
        },
        "llamacpp": {
            "base_url_default": "http://127.0.0.1:8033/v1",
            "kind": "local-openai-compat",
            "key_env": None,
        },
    },
    "cloud": {
        "gemini": {
            "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
            "key_env": "GEMINI_API_KEY",
            "kind": "cloud",
        },
        "openai": {
            "base_url": "https://api.openai.com/v1",
            "key_env": "OPENAI_API_KEY",
            "kind": "cloud",
        },
        "groq": {
            "base_url": "https://api.groq.com/openai/v1",
            "key_env": "GROQ_API_KEY",
            "kind": "cloud",
        },
        "deepseek": {
            "base_url": "https://api.deepseek.com",
            "key_env": "DEEPSEEK_API_KEY",
            "kind": "cloud",
        },
        "together": {
            "base_url": "https://api.together.xyz/v1",
            "key_env": "TOGETHERAI_API_KEY",
            "kind": "cloud",
        },
        "mistral": {
            "base_url": "https://api.mistral.ai/v1",
            "key_env": "MISTRAL_API_KEY",
            "kind": "cloud",
        },
    },
}


# Backwards-compat alias — points at the cloud sub-section so existing
# callers (``androscan/llm/client.py``, ``androscan.py``, ``androscan/
# config/__init__.py``, ``tests/test_llm.py``) keep working unchanged.
# The ``kind: "cloud"`` field added per-entry above is a harmless extra
# key for the .get()/.keys() patterns those callers use.
CLOUD_PROVIDERS = LLM_PROVIDERS["cloud"]


# Mapping from flat ``Config`` field name to its YAML location and the env
# variable (if any) that would override it. Used by :func:`effective_sources`
# to build the "source pills" the Settings UI shows next to each value, and
# by :func:`dump_to_yaml` to write a partial update back to the YAML file
# without disturbing keys it doesn't recognise.
#
# Each entry: ``field -> (yaml_section, yaml_key, env_var | None)``.
CONFIG_FIELD_MAP: dict[str, tuple[str, str, Optional[str]]] = {
    "ollama_base_url":             ("ollama",   "base_url",          "ANDROSCAN_OLLAMA_URL"),
    "ollama_timeout_sec":          ("ollama",   "timeout_sec",       "ANDROSCAN_OLLAMA_TIMEOUT"),
    "ollama_model":                ("ollama",   "model",             "ANDROSCAN_OLLAMA_MODEL"),
    "ollama_temperature":          ("ollama",   "temperature",        None),
    "ollama_num_predict":          ("ollama",   "num_predict",        None),
    # Phase 11 sub-step 11.6 / DEC-025 — Ollama context window.
    # Bumped above the Ollama default (8192) to absorb the v2
    # inter-procedural slicer's ~2× input prompt growth.
    "ollama_num_ctx":              ("ollama",   "num_ctx",           "ANDROSCAN_OLLAMA_NUM_CTX"),
    "llm_provider":                ("llm",      "provider",          "ANDROSCAN_LLM_PROVIDER"),
    "cloud_model":                 ("llm",      "cloud_model",       "ANDROSCAN_CLOUD_MODEL"),
    "cloud_api_key":               ("llm",      "cloud_api_key",      None),
    "cloud_temperature":           ("llm",      "cloud_temperature",  None),
    "run_folder_root":             ("paths",    "run_folder_root",   "ANDROSCAN_RUN_FOLDER"),
    "apktool_cmd":                 ("paths",    "apktool_cmd",        None),
    "jadx_cmd":                    ("paths",    "jadx_cmd",           None),
    "max_turns":                   ("workflow", "max_turns",          None),
    "max_hypotheses_per_report":   ("workflow", "max_hypotheses_per_report", None),
    "per_component_analysis":      ("workflow", "per_component_analysis",     None),
    "section_rule_char":           ("output",   "section_rule_char",  None),
    "section_rule_length":         ("output",   "section_rule_length", None),
    "web_host":                    ("web",      "host",              "ANDROSCAN_WEB_HOST"),
    "web_port":                    ("web",      "port",              "ANDROSCAN_WEB_PORT"),
    "web_screencap_interval_ms":   ("web",      "screencap_interval_ms", "ANDROSCAN_WEB_SCREENCAP_INTERVAL_MS"),
    "rag_embed_provider":          ("rag",      "embed_provider",    "ANDROSCAN_RAG_PROVIDER"),
    "rag_embed_model":             ("rag",      "embed_model",       "ANDROSCAN_RAG_MODEL"),
    "rag_top_k_default":           ("rag",      "top_k_default",     "ANDROSCAN_RAG_TOP_K"),
    "frida_trace_ring_buffer_size":("frida",    "trace_ring_buffer_size", "ANDROSCAN_FRIDA_TRACE_RING"),
    "trace_bypass_risk_max":       ("trace",    "bypass_risk_max",        "ANDROSCAN_TRACE_BYPASS_RISK_MAX"),
    "trace_max_hops_default":      ("trace",    "max_hops_default",       "ANDROSCAN_TRACE_MAX_HOPS_DEFAULT"),
    "trace_max_hops_hard_cap":     ("trace",    "max_hops_hard_cap",      "ANDROSCAN_TRACE_MAX_HOPS_HARD_CAP"),
    # Phase 11 sub-step 11.6 / DEC-025 — bounded inter-procedural
    # slicer descent depth knob. Hard cap of 4 enforced in
    # ``slicing._DescentBudget.fresh()`` regardless of YAML value.
    "trace_max_slice_depth":       ("trace",    "max_slice_depth",        "ANDROSCAN_TRACE_MAX_SLICE_DEPTH"),
}


# Fields that the in-process FastAPI app can re-read after a write without a
# uvicorn restart. Anything **not** in this set requires the user to bounce
# the server (the Settings UI surfaces this with a "restart required" pill).
LIVE_RELOADABLE_FIELDS: frozenset[str] = frozenset({
    "ollama_base_url",
    "ollama_timeout_sec",
    "ollama_model",
    "ollama_temperature",
    "ollama_num_predict",
    "ollama_num_ctx",
    "llm_provider",
    "cloud_model",
    "cloud_api_key",
    "cloud_temperature",
    "apktool_cmd",
    "jadx_cmd",
    "max_turns",
    "max_hypotheses_per_report",
    "per_component_analysis",
    "section_rule_char",
    "section_rule_length",
    "rag_embed_provider",
    "rag_embed_model",
    "rag_top_k_default",
    "web_screencap_interval_ms",
    # frida.trace_ring_buffer_size only takes effect on new FridaSession
    # instances; nothing in flight needs to be torn down to pick up a change.
    "frida_trace_ring_buffer_size",
    # trace.bypass_risk_max is read per-call by 10.4's bypass_planner +
    # 10.5's trace_behavior skill; no in-flight state to bounce.
    "trace_bypass_risk_max",
    # trace.max_hops_* are read per-call by 10.5's trace_behavior skill
    # at the start of each closure walk; no in-flight state to bounce
    # (an active closure walk that's already past the new cap completes
    # under the old cap; the next call uses the new value).
    "trace_max_hops_default",
    "trace_max_hops_hard_cap",
    # Phase 11 sub-step 11.6 — trace.max_slice_depth is read per-call
    # by the trace_behavior skill at the start of each closure walk
    # (passed into ``_DescentBudget.fresh(max_depth=...)``); no
    # in-flight state to bounce.
    "trace_max_slice_depth",
})



@dataclass(frozen=True)
class Config:
    """Runtime configuration. Load via load_config()."""

    ollama_base_url: str
    ollama_timeout_sec: int
    ollama_model: str
    ollama_temperature: float
    ollama_num_predict: int
    ollama_num_ctx: int  # Phase 11 sub-step 11.6 / DEC-025
    # "ollama" / "llamacpp" (local) or any key in LLM_PROVIDERS["cloud"]
    # (gemini / openai / groq / deepseek / together / mistral).
    llm_provider: str
    cloud_model: str
    cloud_api_key: str
    cloud_temperature: float
    run_folder_root: str
    max_turns: int
    max_hypotheses_per_report: int
    per_component_analysis: bool
    apktool_cmd: str
    jadx_cmd: str
    section_rule_char: str
    section_rule_length: int
    web_host: str
    web_port: int
    web_screencap_interval_ms: int
    rag_embed_provider: str
    rag_embed_model: str
    rag_top_k_default: int
    frida_trace_ring_buffer_size: int
    trace_bypass_risk_max: str   # "low" | "medium" | "high" — Phase 10 / DEC-024
    trace_max_hops_default: int  # default closure depth for trace_behavior — Phase 10 / DEC-024
    trace_max_hops_hard_cap: int # absolute closure depth ceiling — Phase 10 / DEC-024
    trace_max_slice_depth: int   # bounded inter-procedural slicer depth — Phase 11 / DEC-025

    @classmethod
    def default(cls) -> "Config":
        return cls(
            ollama_base_url="http://localhost:11434",
            ollama_timeout_sec=150,
            ollama_model="qwen3.5:35b",
            ollama_temperature=0.2,
            ollama_num_predict=constants.OLLAMA_NUM_PREDICT_DEFAULT,
            ollama_num_ctx=constants.OLLAMA_NUM_CTX_DEFAULT,
            llm_provider="ollama",
            cloud_model="",
            cloud_api_key="",
            cloud_temperature=0.2,
            run_folder_root="apps",
            max_turns=constants.MAX_TURNS_DEFAULT,
            max_hypotheses_per_report=constants.MAX_HYPOTHESES_PER_REPORT_DEFAULT,
            per_component_analysis=constants.PER_COMPONENT_ANALYSIS_DEFAULT,
            apktool_cmd=constants.APKTOOL_CMD_DEFAULT,
            jadx_cmd=constants.JADX_CMD_DEFAULT,
            section_rule_char=constants.SECTION_RULE_CHAR,
            section_rule_length=constants.SECTION_RULE_LENGTH,
            web_host="127.0.0.1",
            web_port=8420,
            web_screencap_interval_ms=500,
            rag_embed_provider="fastembed",
            rag_embed_model="",
            rag_top_k_default=8,
            frida_trace_ring_buffer_size=5000,
            trace_bypass_risk_max="medium",
            trace_max_hops_default=3,
            trace_max_hops_hard_cap=6,
            trace_max_slice_depth=constants.TRACE_MAX_SLICE_DEPTH_DEFAULT,
        )

    def provider_kind(self) -> str:
        """Return the dispatch kind for the configured LLM provider.

        Reverse-lookup on :data:`LLM_PROVIDERS`. Returns one of:

        * ``"local-ollama"`` — Ollama HTTP ``/api/chat`` (existing path)
        * ``"local-openai-compat"`` — llama.cpp ``llama-server``
          OpenAI-compat ``/v1/chat/completions`` (added in LCP.2)
        * ``"cloud"`` — hosted vendor reached via the OpenAI Python SDK

        Falls back to ``"cloud"`` for unrecognised provider names so
        that a typo in ``llm_provider`` surfaces as the existing "no
        API key configured" error from :meth:`resolve_cloud_api_key`
        rather than a silent local dispatch.
        """
        name = (self.llm_provider or "").strip().lower()
        local_entry = LLM_PROVIDERS["local"].get(name)
        if local_entry is not None:
            return local_entry["kind"]
        cloud_entry = LLM_PROVIDERS["cloud"].get(name)
        if cloud_entry is not None:
            return cloud_entry["kind"]
        return "cloud"

    @property
    def is_cloud(self) -> bool:
        """True iff the configured provider is a hosted cloud vendor.

        Backwards-compat shim — pre-DEC-027 this returned
        ``llm_provider != "ollama"``, which would have wrongly
        classified llama.cpp (a new local provider) as cloud the
        moment LCP.4 lets the operator select it. Post-DEC-027
        delegates to :meth:`provider_kind` so both Ollama and
        llama.cpp resolve to ``False``; the six existing cloud
        provider names continue to resolve to ``True``.
        """
        return self.provider_kind() == "cloud"

    @property
    def active_model(self) -> str:
        """Return the model name for the active provider."""
        if self.is_cloud:
            return self.cloud_model or "(not set)"
        return self.ollama_model

    def resolve_cloud_api_key(self) -> str:
        """Return cloud API key from config or environment."""
        if self.cloud_api_key:
            return self.cloud_api_key
        provider_info = CLOUD_PROVIDERS.get(self.llm_provider, {})
        env_var = provider_info.get("key_env", "")
        return os.environ.get(env_var, "") if env_var else ""

    def resolve_cloud_base_url(self) -> str:
        """Return the OpenAI-compatible base URL for the cloud provider."""
        provider_info = CLOUD_PROVIDERS.get(self.llm_provider, {})
        return provider_info.get("base_url", "")

    @property
    def section_rule(self) -> str:
        return self.section_rule_char * self.section_rule_length


def _load_yaml(path: Path, explicit: bool = False) -> dict[str, Any]:
    """Load YAML file; return empty dict if missing (auto-discovered) or raise if explicit and broken."""
    if not path.exists():
        if explicit:
            raise FileNotFoundError(f"Config file not found: {path}")
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data if isinstance(data, dict) else {}
    except yaml.YAMLError as e:
        if explicit:
            raise ValueError(f"Invalid YAML in config file {path}: {e}") from e
        return {}
    except OSError as e:
        if explicit:
            raise OSError(f"Cannot read config file {path}: {e}") from e
        return {}


def _safe_int(value: Any, default: int, name: str) -> int:
    """Coerce value to int with a clear error on failure."""
    if value is None:
        return default
    try:
        return int(value)
    except (ValueError, TypeError):
        raise ValueError(f"Config key '{name}' must be an integer, got: {value!r}") from None


def _safe_float(value: Any, default: float, name: str) -> float:
    """Coerce value to float with a clear error on failure."""
    if value is None:
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        raise ValueError(f"Config key '{name}' must be a number, got: {value!r}") from None


def _merge_from_yaml(config_dict: dict[str, Any]) -> dict[str, Any]:
    """Extract flat keys from nested YAML for Config. Uses constants as defaults."""
    out: dict[str, Any] = {}
    ollama = config_dict.get("ollama") or {}
    llm = config_dict.get("llm") or {}
    paths = config_dict.get("paths") or {}
    workflow = config_dict.get("workflow") or {}
    output = config_dict.get("output") or {}
    out["ollama_base_url"] = (ollama.get("base_url") or "").strip().rstrip("/") or "http://localhost:11434"
    out["ollama_timeout_sec"] = _safe_int(ollama.get("timeout_sec"), 150, "ollama.timeout_sec")
    out["ollama_model"] = ollama.get("model") or "qwen3.5:35b"
    out["ollama_temperature"] = _safe_float(ollama.get("temperature"), 0.2, "ollama.temperature")
    out["ollama_num_predict"] = _safe_int(ollama.get("num_predict"), constants.OLLAMA_NUM_PREDICT_DEFAULT, "ollama.num_predict")
    out["ollama_num_ctx"] = _safe_int(ollama.get("num_ctx"), constants.OLLAMA_NUM_CTX_DEFAULT, "ollama.num_ctx")
    out["llm_provider"] = (llm.get("provider") or "ollama").strip().lower()
    out["cloud_model"] = (llm.get("cloud_model") or "").strip()
    out["cloud_api_key"] = (llm.get("cloud_api_key") or "").strip()
    out["cloud_temperature"] = _safe_float(llm.get("cloud_temperature"), 0.2, "llm.cloud_temperature")
    out["run_folder_root"] = paths.get("run_folder_root") or "apps"
    out["max_turns"] = _safe_int(workflow.get("max_turns"), constants.MAX_TURNS_DEFAULT, "workflow.max_turns")
    out["max_hypotheses_per_report"] = _safe_int(workflow.get("max_hypotheses_per_report"), constants.MAX_HYPOTHESES_PER_REPORT_DEFAULT, "workflow.max_hypotheses_per_report")
    out["per_component_analysis"] = bool(workflow.get("per_component_analysis") if workflow.get("per_component_analysis") is not None else constants.PER_COMPONENT_ANALYSIS_DEFAULT)
    out["apktool_cmd"] = paths.get("apktool_cmd") or constants.APKTOOL_CMD_DEFAULT
    out["jadx_cmd"] = paths.get("jadx_cmd") or constants.JADX_CMD_DEFAULT
    out["section_rule_char"] = output.get("section_rule_char") or constants.SECTION_RULE_CHAR
    out["section_rule_length"] = _safe_int(output.get("section_rule_length"), constants.SECTION_RULE_LENGTH, "output.section_rule_length")
    web = config_dict.get("web") or {}
    out["web_host"] = (web.get("host") or "127.0.0.1").strip() or "127.0.0.1"
    out["web_port"] = _safe_int(web.get("port"), 8420, "web.port")
    out["web_screencap_interval_ms"] = _safe_int(web.get("screencap_interval_ms"), 500, "web.screencap_interval_ms")
    rag = config_dict.get("rag") or {}
    out["rag_embed_provider"] = (rag.get("embed_provider") or "fastembed").strip() or "fastembed"
    out["rag_embed_model"] = (rag.get("embed_model") or "").strip()
    out["rag_top_k_default"] = _safe_int(rag.get("top_k_default"), 8, "rag.top_k_default")
    frida = config_dict.get("frida") or {}
    out["frida_trace_ring_buffer_size"] = _safe_int(
        frida.get("trace_ring_buffer_size"), 5000, "frida.trace_ring_buffer_size"
    )
    trace = config_dict.get("trace") or {}
    # Loader stays permissive (any string accepted) — the planner +
    # 10.5 skill validate against {"low", "medium", "high"} downstream
    # with a fail-soft fallback to "medium" on invalid input. Mirrors
    # how llm_provider is handled (strings out of-set don't blow up
    # config loading; consumers handle the fallback).
    out["trace_bypass_risk_max"] = (trace.get("bypass_risk_max") or "medium").strip() or "medium"
    out["trace_max_hops_default"] = _safe_int(trace.get("max_hops_default"), 3, "trace.max_hops_default")
    out["trace_max_hops_hard_cap"] = _safe_int(trace.get("max_hops_hard_cap"), 6, "trace.max_hops_hard_cap")
    out["trace_max_slice_depth"] = _safe_int(
        trace.get("max_slice_depth"),
        constants.TRACE_MAX_SLICE_DEPTH_DEFAULT,
        "trace.max_slice_depth",
    )
    return out


def load_config(config_path: Optional[str] = None) -> Config:
    """Load config: defaults -> global_config.yaml (if found) -> env overrides.

    config_path: explicit path to YAML file. If None, search:
      - cwd / global_config.yaml
      - cwd / config / global_config.yaml
    Env: ANDROSCAN_OLLAMA_URL, ANDROSCAN_OLLAMA_TIMEOUT, ANDROSCAN_OLLAMA_MODEL, ANDROSCAN_RUN_FOLDER,
    ANDROSCAN_WEB_HOST, ANDROSCAN_WEB_PORT, ANDROSCAN_WEB_SCREENCAP_INTERVAL_MS.
    """
    defaults = Config.default()
    yaml_data: dict[str, Any] = {}

    if config_path:
        yaml_data = _load_yaml(Path(config_path), explicit=True)
    else:
        cwd = Path.cwd()
        for candidate in [cwd / "global_config.yaml", cwd / "config" / "global_config.yaml"]:
            yaml_data = _load_yaml(candidate)
            if yaml_data:
                break

    merged = _merge_from_yaml(yaml_data)

    # Env overrides
    if os.environ.get("ANDROSCAN_OLLAMA_URL"):
        merged["ollama_base_url"] = os.environ["ANDROSCAN_OLLAMA_URL"].strip().rstrip("/")
    if os.environ.get("ANDROSCAN_OLLAMA_TIMEOUT"):
        try:
            merged["ollama_timeout_sec"] = max(1, int(os.environ["ANDROSCAN_OLLAMA_TIMEOUT"]))
        except ValueError:
            print(
                f"Warning: ANDROSCAN_OLLAMA_TIMEOUT={os.environ['ANDROSCAN_OLLAMA_TIMEOUT']!r} is not a valid integer; using default.",
                file=sys.stderr,
            )
    if os.environ.get("ANDROSCAN_OLLAMA_MODEL"):
        merged["ollama_model"] = os.environ["ANDROSCAN_OLLAMA_MODEL"].strip()
    if os.environ.get("ANDROSCAN_RUN_FOLDER"):
        merged["run_folder_root"] = os.environ["ANDROSCAN_RUN_FOLDER"]
    if os.environ.get("ANDROSCAN_LLM_PROVIDER"):
        merged["llm_provider"] = os.environ["ANDROSCAN_LLM_PROVIDER"].strip().lower()
    if os.environ.get("ANDROSCAN_CLOUD_MODEL"):
        merged["cloud_model"] = os.environ["ANDROSCAN_CLOUD_MODEL"].strip()
    if os.environ.get("ANDROSCAN_WEB_HOST"):
        merged["web_host"] = os.environ["ANDROSCAN_WEB_HOST"].strip() or merged.get("web_host", "127.0.0.1")
    if os.environ.get("ANDROSCAN_WEB_PORT"):
        try:
            merged["web_port"] = max(1, min(65535, int(os.environ["ANDROSCAN_WEB_PORT"].strip())))
        except ValueError:
            print(
                f"Warning: ANDROSCAN_WEB_PORT={os.environ['ANDROSCAN_WEB_PORT']!r} is not a valid integer; using YAML/default.",
                file=sys.stderr,
            )
    if os.environ.get("ANDROSCAN_RAG_PROVIDER"):
        merged["rag_embed_provider"] = os.environ["ANDROSCAN_RAG_PROVIDER"].strip() or merged.get("rag_embed_provider", "fastembed")
    if os.environ.get("ANDROSCAN_RAG_MODEL"):
        merged["rag_embed_model"] = os.environ["ANDROSCAN_RAG_MODEL"].strip()
    if os.environ.get("ANDROSCAN_RAG_TOP_K"):
        try:
            merged["rag_top_k_default"] = max(1, int(os.environ["ANDROSCAN_RAG_TOP_K"].strip()))
        except ValueError:
            print(
                f"Warning: ANDROSCAN_RAG_TOP_K={os.environ['ANDROSCAN_RAG_TOP_K']!r} invalid; using default.",
                file=sys.stderr,
            )
    if os.environ.get("ANDROSCAN_FRIDA_TRACE_RING"):
        try:
            merged["frida_trace_ring_buffer_size"] = max(
                100, int(os.environ["ANDROSCAN_FRIDA_TRACE_RING"].strip())
            )
        except ValueError:
            print(
                f"Warning: ANDROSCAN_FRIDA_TRACE_RING={os.environ['ANDROSCAN_FRIDA_TRACE_RING']!r} invalid; using default.",
                file=sys.stderr,
            )
    if os.environ.get("ANDROSCAN_TRACE_BYPASS_RISK_MAX"):
        merged["trace_bypass_risk_max"] = os.environ["ANDROSCAN_TRACE_BYPASS_RISK_MAX"].strip().lower() or merged.get("trace_bypass_risk_max", "medium")
    if os.environ.get("ANDROSCAN_TRACE_MAX_HOPS_DEFAULT"):
        try:
            merged["trace_max_hops_default"] = max(1, int(os.environ["ANDROSCAN_TRACE_MAX_HOPS_DEFAULT"].strip()))
        except ValueError:
            print(
                f"Warning: ANDROSCAN_TRACE_MAX_HOPS_DEFAULT={os.environ['ANDROSCAN_TRACE_MAX_HOPS_DEFAULT']!r} invalid; using default.",
                file=sys.stderr,
            )
    if os.environ.get("ANDROSCAN_TRACE_MAX_HOPS_HARD_CAP"):
        try:
            merged["trace_max_hops_hard_cap"] = max(1, int(os.environ["ANDROSCAN_TRACE_MAX_HOPS_HARD_CAP"].strip()))
        except ValueError:
            print(
                f"Warning: ANDROSCAN_TRACE_MAX_HOPS_HARD_CAP={os.environ['ANDROSCAN_TRACE_MAX_HOPS_HARD_CAP']!r} invalid; using default.",
                file=sys.stderr,
            )
    # Phase 11 sub-step 11.6 — trace.max_slice_depth env override.
    if os.environ.get("ANDROSCAN_TRACE_MAX_SLICE_DEPTH"):
        try:
            merged["trace_max_slice_depth"] = max(0, int(os.environ["ANDROSCAN_TRACE_MAX_SLICE_DEPTH"].strip()))
        except ValueError:
            print(
                f"Warning: ANDROSCAN_TRACE_MAX_SLICE_DEPTH={os.environ['ANDROSCAN_TRACE_MAX_SLICE_DEPTH']!r} invalid; using default.",
                file=sys.stderr,
            )
    # Phase 11 sub-step 11.6 — ollama.num_ctx env override.
    if os.environ.get("ANDROSCAN_OLLAMA_NUM_CTX"):
        try:
            merged["ollama_num_ctx"] = max(1, int(os.environ["ANDROSCAN_OLLAMA_NUM_CTX"].strip()))
        except ValueError:
            print(
                f"Warning: ANDROSCAN_OLLAMA_NUM_CTX={os.environ['ANDROSCAN_OLLAMA_NUM_CTX']!r} invalid; using default.",
                file=sys.stderr,
            )
    if os.environ.get("ANDROSCAN_WEB_SCREENCAP_INTERVAL_MS"):
        try:
            merged["web_screencap_interval_ms"] = max(50, int(os.environ["ANDROSCAN_WEB_SCREENCAP_INTERVAL_MS"].strip()))
        except ValueError:
            print(
                f"Warning: ANDROSCAN_WEB_SCREENCAP_INTERVAL_MS={os.environ['ANDROSCAN_WEB_SCREENCAP_INTERVAL_MS']!r} invalid; using default.",
                file=sys.stderr,
            )

    return Config(
        ollama_base_url=merged["ollama_base_url"],
        ollama_timeout_sec=merged["ollama_timeout_sec"],
        ollama_model=merged["ollama_model"],
        ollama_temperature=merged["ollama_temperature"],
        ollama_num_predict=max(1, merged["ollama_num_predict"]),
        ollama_num_ctx=max(1, merged["ollama_num_ctx"]),
        llm_provider=merged["llm_provider"],
        cloud_model=merged["cloud_model"],
        cloud_api_key=merged["cloud_api_key"],
        cloud_temperature=merged["cloud_temperature"],
        run_folder_root=merged["run_folder_root"],
        max_turns=max(1, merged["max_turns"]),
        max_hypotheses_per_report=max(0, merged["max_hypotheses_per_report"]),
        per_component_analysis=bool(merged.get("per_component_analysis", constants.PER_COMPONENT_ANALYSIS_DEFAULT)),
        apktool_cmd=merged["apktool_cmd"],
        jadx_cmd=merged["jadx_cmd"],
        section_rule_char=merged["section_rule_char"] or constants.SECTION_RULE_CHAR,
        section_rule_length=max(1, merged["section_rule_length"]),
        web_host=str(merged.get("web_host") or "127.0.0.1").strip() or "127.0.0.1",
        web_port=max(1, min(65535, int(merged.get("web_port", 8420)))),
        web_screencap_interval_ms=max(50, int(merged.get("web_screencap_interval_ms", 500))),
        rag_embed_provider=str(merged.get("rag_embed_provider") or "fastembed").strip() or "fastembed",
        rag_embed_model=str(merged.get("rag_embed_model") or "").strip(),
        rag_top_k_default=max(1, int(merged.get("rag_top_k_default", 8))),
        frida_trace_ring_buffer_size=max(100, int(merged.get("frida_trace_ring_buffer_size", 5000))),
        trace_bypass_risk_max=str(merged.get("trace_bypass_risk_max") or "medium").strip().lower() or "medium",
        trace_max_hops_default=max(1, int(merged.get("trace_max_hops_default", 3))),
        trace_max_hops_hard_cap=max(1, int(merged.get("trace_max_hops_hard_cap", 6))),
        trace_max_slice_depth=max(0, int(merged.get("trace_max_slice_depth", constants.TRACE_MAX_SLICE_DEPTH_DEFAULT))),
    )


# ---------------------------------------------------------------------------
# Settings-tab helpers: introspection, partial overrides, and YAML write-back
# ---------------------------------------------------------------------------


def config_as_flat_dict(config: Config) -> dict[str, Any]:
    """``dataclasses.asdict`` for ``Config`` (alias kept for clarity at call sites)."""
    return dataclasses.asdict(config)


def global_view_from_config(config: Config) -> dict[str, dict[str, Any]]:
    """Return the YAML-shaped nested view of ``config``.

    Mirrors :func:`_merge_from_yaml` in reverse: every flat field is grouped
    by the section it lives under in ``global_config.yaml``. Used by the
    Settings tab and by :func:`androscan.web.per_app_settings.effective_settings`
    to overlay per-app overrides without each consumer hand-rolling the
    section grouping.
    """
    flat = config_as_flat_dict(config)
    out: dict[str, dict[str, Any]] = {}
    for field, (section, key, _env) in CONFIG_FIELD_MAP.items():
        if field not in flat:
            continue
        out.setdefault(section, {})[key] = flat[field]
    # Convenience mirrors so per-app sections can inherit from a clean
    # root even when they have no equivalent global key (e.g. inspect).
    out.setdefault("inspect", {})
    out.setdefault("decompile", {})
    out.setdefault("exploit", {})
    out.setdefault("chat", {})
    return out


def effective_sources(
    config_path: Optional[Path],
) -> dict[str, str]:
    """Return ``{field: 'yaml' | 'env' | 'default'}`` for every Config field.

    The Settings UI uses this to disable inputs whose value comes from an
    environment variable (since saving the YAML wouldn't take effect) and
    to badge "default" entries the user hasn't customised.
    """
    yaml_data: dict[str, Any] = {}
    if config_path and config_path.is_file():
        yaml_data = _load_yaml(config_path)
    sources: dict[str, str] = {}
    for field, (section, key, env_var) in CONFIG_FIELD_MAP.items():
        if env_var and os.environ.get(env_var):
            sources[field] = "env"
            continue
        sec = yaml_data.get(section) or {}
        if isinstance(sec, dict) and key in sec and sec[key] is not None and sec[key] != "":
            sources[field] = "yaml"
            continue
        sources[field] = "default"
    return sources


def env_overrides() -> dict[str, str]:
    """Snapshot of currently-set ``ANDROSCAN_*`` env vars (value redacted-safe).

    Names only — values are echoed back so the UI can show "ollama_model
    is locked because $ANDROSCAN_OLLAMA_MODEL=qwen2:7b is set".
    """
    out: dict[str, str] = {}
    for _field, (_s, _k, env) in CONFIG_FIELD_MAP.items():
        if env and os.environ.get(env):
            out[env] = os.environ[env]
    return out


def with_overrides(config: Config, **overrides: Any) -> Config:
    """Return a new ``Config`` with ``overrides`` applied (validated + clamped).

    Used for live-reload after a YAML save: we re-run :func:`load_config`
    in the general case, but simple per-app effective-config calculations
    can use this to apply just a few tweaks without I/O. Unknown keys
    raise ``ValueError`` so a typo doesn't silently no-op.
    """
    flat = config_as_flat_dict(config)
    for k, v in overrides.items():
        if k not in flat:
            raise ValueError(f"Unknown Config field: {k!r}")
        flat[k] = v
    # Re-apply the same clamps load_config() does so callers can't smuggle
    # negative timeouts or out-of-range ports through this path.
    return Config(
        ollama_base_url=str(flat["ollama_base_url"]).strip().rstrip("/") or "http://localhost:11434",
        ollama_timeout_sec=max(1, int(flat["ollama_timeout_sec"])),
        ollama_model=str(flat["ollama_model"] or "qwen3.5:35b"),
        ollama_temperature=float(flat["ollama_temperature"]),
        ollama_num_predict=max(1, int(flat["ollama_num_predict"])),
        ollama_num_ctx=max(1, int(flat["ollama_num_ctx"])),
        llm_provider=str(flat.get("llm_provider") or "ollama").strip().lower() or "ollama",
        cloud_model=str(flat.get("cloud_model") or "").strip(),
        cloud_api_key=str(flat.get("cloud_api_key") or "").strip(),
        cloud_temperature=float(flat.get("cloud_temperature") or 0.2),
        run_folder_root=str(flat["run_folder_root"] or "apps"),
        max_turns=max(1, int(flat["max_turns"])),
        max_hypotheses_per_report=max(0, int(flat["max_hypotheses_per_report"])),
        per_component_analysis=bool(flat["per_component_analysis"]),
        apktool_cmd=str(flat["apktool_cmd"] or constants.APKTOOL_CMD_DEFAULT),
        jadx_cmd=str(flat["jadx_cmd"] or constants.JADX_CMD_DEFAULT),
        section_rule_char=str(flat["section_rule_char"] or constants.SECTION_RULE_CHAR),
        section_rule_length=max(1, int(flat["section_rule_length"])),
        web_host=str(flat["web_host"] or "127.0.0.1").strip() or "127.0.0.1",
        web_port=max(1, min(65535, int(flat["web_port"]))),
        web_screencap_interval_ms=max(50, int(flat["web_screencap_interval_ms"])),
        rag_embed_provider=str(flat["rag_embed_provider"] or "fastembed").strip() or "fastembed",
        rag_embed_model=str(flat["rag_embed_model"] or "").strip(),
        rag_top_k_default=max(1, int(flat["rag_top_k_default"])),
        frida_trace_ring_buffer_size=max(100, int(flat["frida_trace_ring_buffer_size"])),
        trace_bypass_risk_max=str(flat["trace_bypass_risk_max"] or "medium").strip().lower() or "medium",
        trace_max_hops_default=max(1, int(flat["trace_max_hops_default"])),
        trace_max_hops_hard_cap=max(1, int(flat["trace_max_hops_hard_cap"])),
        trace_max_slice_depth=max(0, int(flat["trace_max_slice_depth"])),
    )


def coerce_yaml_value(field: str, raw: Any) -> Any:
    """Coerce a JSON-parsed value to the type ``Config.<field>`` expects.

    Returns the cleaned value or raises ``ValueError`` so the HTTP layer
    can return a clean 400 with the offending field name.
    """
    if field not in CONFIG_FIELD_MAP:
        raise ValueError(f"Unknown Config field: {field!r}")
    int_fields = {
        "ollama_timeout_sec", "ollama_num_predict", "ollama_num_ctx",
        "max_turns", "max_hypotheses_per_report",
        "section_rule_length",
        "web_port", "web_screencap_interval_ms",
        "rag_top_k_default",
        "frida_trace_ring_buffer_size",
        "trace_max_hops_default",
        "trace_max_hops_hard_cap",
        "trace_max_slice_depth",
    }
    float_fields = {"ollama_temperature", "cloud_temperature"}
    bool_fields = {"per_component_analysis"}
    str_fields = {
        "ollama_base_url", "ollama_model",
        "llm_provider", "cloud_model", "cloud_api_key",
        "run_folder_root", "apktool_cmd", "jadx_cmd",
        "section_rule_char",
        "web_host",
        "rag_embed_provider", "rag_embed_model",
        "trace_bypass_risk_max",
    }
    if field in int_fields:
        return _safe_int(raw, 0, field)
    if field in float_fields:
        return _safe_float(raw, 0.0, field)
    if field in bool_fields:
        if isinstance(raw, str):
            return raw.strip().lower() in ("1", "true", "yes", "on")
        return bool(raw)
    if field in str_fields:
        if raw is None:
            return ""
        return str(raw).strip()
    raise ValueError(f"Unhandled coercion for {field!r}")


def dump_to_yaml(config_path: Path, partial: dict[str, Any]) -> dict[str, Any]:
    """Atomically merge ``partial`` (flat field dict) into the YAML at ``config_path``.

    Preserves any keys / sections the loader doesn't know about (so user
    comments aren't *removed* but their hand-edits to unrelated keys
    survive a UI save). Returns the new full YAML dict that was written.
    """
    existing: dict[str, Any] = {}
    if config_path.is_file():
        try:
            with open(config_path, encoding="utf-8") as f:
                loaded = yaml.safe_load(f)
            if isinstance(loaded, dict):
                existing = loaded
        except (yaml.YAMLError, OSError):
            existing = {}

    for field, value in partial.items():
        if field not in CONFIG_FIELD_MAP:
            raise ValueError(f"Unknown Config field: {field!r}")
        section, key, _env = CONFIG_FIELD_MAP[field]
        sec = existing.get(section)
        if not isinstance(sec, dict):
            sec = {}
        coerced = coerce_yaml_value(field, value)
        sec[key] = coerced
        existing[section] = sec

    config_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_name = tempfile.mkstemp(
        prefix=".global_config.", suffix=".yaml.tmp", dir=str(config_path.parent)
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            yaml.safe_dump(existing, f, sort_keys=False, default_flow_style=False)
        os.replace(tmp_name, config_path)
    except OSError:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return existing


def validate_raw_yaml(raw_text: str) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """Parse + validate a YAML string the user typed in the Settings UI.

    Returns ``(parsed_dict, None)`` on success or ``(None, error_message)``
    on parse / shape failure. We also dry-run :func:`_merge_from_yaml` so
    the user gets type errors *before* the file is overwritten.
    """
    if not isinstance(raw_text, str):
        return None, "raw YAML must be a string"
    try:
        loaded = yaml.safe_load(raw_text)
    except yaml.YAMLError as e:
        return None, f"YAML parse error: {e}"
    if loaded is None:
        # An empty file is valid — it just means "everything default".
        loaded = {}
    if not isinstance(loaded, dict):
        return None, "top-level YAML must be a mapping (got something else)"
    try:
        _merge_from_yaml(loaded)
    except (ValueError, TypeError) as e:
        return None, f"validation error: {e}"
    return loaded, None


def save_raw_yaml(config_path: Path, raw_text: str) -> dict[str, Any]:
    """Atomically replace ``config_path`` with the user-edited YAML text.

    Validates first via :func:`validate_raw_yaml`; raises ``ValueError`` on
    bad input so the HTTP layer returns a 400 with the offending message.
    Returns the parsed dict on success.
    """
    parsed, err = validate_raw_yaml(raw_text)
    if err is not None:
        raise ValueError(err)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_name = tempfile.mkstemp(
        prefix=".global_config.", suffix=".yaml.tmp", dir=str(config_path.parent)
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            f.write(raw_text)
        os.replace(tmp_name, config_path)
    except OSError:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return parsed or {}


def read_raw_yaml(config_path: Path) -> str:
    """Return the on-disk YAML text (empty string if the file doesn't exist)."""
    if not config_path.is_file():
        return ""
    try:
        return config_path.read_text(encoding="utf-8")
    except OSError:
        return ""


def restore_defaults_yaml(config_path: Path) -> dict[str, Any]:
    """Overwrite ``config_path`` with the default YAML view of ``Config.default()``.

    "Reset to defaults" path the Settings UI exposes. Preserves the file
    location and any hand-added env-var overrides (those still win at
    ``load_config`` time even after we wipe the YAML).
    """
    defaults = Config.default()
    full = global_view_from_config(defaults)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_name = tempfile.mkstemp(
        prefix=".global_config.", suffix=".yaml.tmp", dir=str(config_path.parent)
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            yaml.safe_dump(full, f, sort_keys=False, default_flow_style=False)
        os.replace(tmp_name, config_path)
    except OSError:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return full


def discover_config_path(cwd: Optional[Path] = None) -> Path:
    """Best-effort discovery of where to write ``global_config.yaml``.

    Mirrors the search order in :func:`load_config`. If the file exists,
    return it. Otherwise return the canonical default location so the
    Settings "save" path creates it there rather than in some arbitrary
    cwd at request-time.
    """
    base = cwd or Path.cwd()
    candidates = [base / "global_config.yaml", base / "config" / "global_config.yaml"]
    for c in candidates:
        if c.is_file():
            return c
    return candidates[0]
