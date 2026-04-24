"""Embedding-provider abstraction.

Supported providers (all imports are lazy):

* ``fastembed``  — default, runs locally via ONNX Runtime, no GPU needed.
                   Good speed/quality trade-off for code search.
* ``ollama``     — talk to a local Ollama instance (e.g. ``nomic-embed-text``)
                   over HTTP. Re-uses the host's existing model server.
* ``hash``       — deterministic hashing trick provider, used by tests and
                   as a never-fails fallback so the rest of the indexer is
                   exercised even on machines without ML libs installed.

A future ``llamacpp`` provider can be added behind the same protocol; the
``EmbedProvider`` interface intentionally only requires ``name``, ``dim``,
and ``embed``. Switching backends is a config change, never a caller change.

Resolution order
----------------
``get_provider(config)`` returns the first usable provider:

1. The provider named in ``config.rag_embed_provider`` if installed/reachable.
2. The internal ``hash`` fallback if and only if
   ``ANDROSCAN_RAG_ALLOW_HASH=1`` (CI / tests) — production builds raise.

Errors are raised as :class:`EmbedProviderError` so callers get a stable
exception type.
"""

from __future__ import annotations

import hashlib
import logging
import math
import os
from dataclasses import dataclass
from typing import Iterable, Optional, Protocol, Sequence

import requests

logger = logging.getLogger(__name__)

# Default model identifiers per provider. These are conservative defaults that
# embed code reasonably well in 384-dim vectors; users can override via
# ``rag.embed_model`` in global_config.yaml.
DEFAULTS = {
    "fastembed": "BAAI/bge-small-en-v1.5",
    "ollama": "nomic-embed-text",
    "hash": "sha256-hashing-trick",
}


class EmbedProviderError(RuntimeError):
    """Raised when no embedding provider is available or a call fails."""


class EmbedProvider(Protocol):
    """Minimal contract for an embedding backend."""

    name: str
    model: str
    dim: int

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Return one vector per input text. Length-zero inputs raise."""
        ...


# ---------------------------------------------------------------------------
# Hash fallback (always available; used by tests and as a documented fallback)


@dataclass
class HashProvider:
    """Deterministic SHA256-based hashing trick — *not* semantic.

    Useful for tests and for keeping the indexer end-to-end testable on
    machines without ML libraries. Vectors are L2-normalized so cosine
    similarity behaves consistently with the real providers.
    """

    name: str = "hash"
    model: str = DEFAULTS["hash"]
    dim: int = 256

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            raise EmbedProviderError("embed() called with no inputs")
        out: list[list[float]] = []
        for t in texts:
            v = [0.0] * self.dim
            # Hash word-grams so similar code chunks land in similar buckets.
            tokens = t.lower().split()
            for tok in tokens:
                h = hashlib.sha256(tok.encode("utf-8")).digest()
                for i in range(0, len(h), 4):
                    bucket = int.from_bytes(h[i:i + 2], "big") % self.dim
                    sign = 1.0 if (h[i + 2] & 1) else -1.0
                    v[bucket] += sign
            norm = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / norm for x in v])
        return out


# ---------------------------------------------------------------------------
# FastEmbed (local ONNX Runtime; recommended default)


class FastEmbedProvider:
    """Wrap ``fastembed.TextEmbedding`` behind the protocol.

    fastembed downloads the chosen ONNX model on first use and caches it
    under ``~/.cache/fastembed``. Calls are CPU-only by default; users can
    install ``onnxruntime-gpu`` separately if desired.
    """

    name = "fastembed"

    def __init__(self, model: Optional[str] = None) -> None:
        try:
            from fastembed import TextEmbedding  # type: ignore[import-not-found]
        except Exception as e:  # pragma: no cover - exercised on missing dep
            raise EmbedProviderError(
                "fastembed not installed. Install with: pip install 'fastembed>=0.3'"
            ) from e
        self.model = model or DEFAULTS["fastembed"]
        try:
            self._impl = TextEmbedding(model_name=self.model)
        except Exception as e:
            raise EmbedProviderError(
                f"fastembed failed to load model '{self.model}': {e}"
            ) from e
        # fastembed exposes the model dim via _model.config or via a probe.
        try:
            probe = list(self._impl.embed(["dim_probe"]))
            self.dim = len(probe[0])
        except Exception as e:
            raise EmbedProviderError(
                f"fastembed dim probe failed for '{self.model}': {e}"
            ) from e

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            raise EmbedProviderError("embed() called with no inputs")
        try:
            return [list(map(float, v)) for v in self._impl.embed(list(texts))]
        except Exception as e:
            raise EmbedProviderError(f"fastembed.embed failed: {e}") from e


# ---------------------------------------------------------------------------
# Ollama HTTP embeddings


class OllamaEmbedProvider:
    """Embed via ``POST {base_url}/api/embeddings``.

    Re-uses the same Ollama instance the workbench already talks to. The
    model defaults to ``nomic-embed-text``; users with a smaller machine
    can switch to ``all-minilm`` etc.
    """

    name = "ollama"

    def __init__(
        self,
        *,
        base_url: str,
        model: Optional[str] = None,
        timeout: int = 60,
    ) -> None:
        self.base_url = (base_url or "").rstrip("/") or "http://localhost:11434"
        self.model = model or DEFAULTS["ollama"]
        self.timeout = max(1, int(timeout))
        # Probe to discover dim and validate model availability.
        self.dim = self._probe_dim()

    def _probe_dim(self) -> int:
        url = f"{self.base_url}/api/embeddings"
        try:
            resp = requests.post(
                url,
                json={"model": self.model, "prompt": "dim_probe"},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            raise EmbedProviderError(
                f"Ollama embeddings unreachable at {url}: {e}. "
                f"Run: ollama pull {self.model}"
            ) from e
        except ValueError as e:  # JSONDecodeError subclasses ValueError
            raise EmbedProviderError(
                f"Ollama embeddings returned non-JSON from {url}: {e}"
            ) from e
        vec = data.get("embedding") or []
        if not vec:
            raise EmbedProviderError(
                f"Ollama embeddings returned empty vector for model '{self.model}'. "
                f"Have you run: ollama pull {self.model}?"
            )
        return len(vec)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            raise EmbedProviderError("embed() called with no inputs")
        out: list[list[float]] = []
        url = f"{self.base_url}/api/embeddings"
        for t in texts:
            try:
                resp = requests.post(
                    url,
                    json={"model": self.model, "prompt": t},
                    timeout=self.timeout,
                )
                resp.raise_for_status()
                data = resp.json()
            except requests.RequestException as e:
                raise EmbedProviderError(f"ollama embed failed: {e}") from e
            vec = data.get("embedding") or []
            if not vec:
                raise EmbedProviderError("ollama returned empty embedding")
            out.append([float(x) for x in vec])
        return out


# ---------------------------------------------------------------------------
# Resolver


def _config_attr(config: object, name: str, default: object) -> object:
    return getattr(config, name, default)


def get_provider(config: object) -> EmbedProvider:
    """Return the configured provider, instantiated.

    Reads:
      * ``config.rag_embed_provider`` (default ``"fastembed"``)
      * ``config.rag_embed_model`` (provider-specific default)
      * ``config.ollama_base_url`` for the ``ollama`` provider.

    Honors ``ANDROSCAN_RAG_PROVIDER`` and ``ANDROSCAN_RAG_MODEL`` overrides.
    """
    requested = (
        os.environ.get("ANDROSCAN_RAG_PROVIDER")
        or _config_attr(config, "rag_embed_provider", "fastembed")
    )
    requested = str(requested or "").strip().lower() or "fastembed"
    model = (
        os.environ.get("ANDROSCAN_RAG_MODEL")
        or _config_attr(config, "rag_embed_model", "")
    )
    model = str(model or "").strip() or None

    if requested == "fastembed":
        return FastEmbedProvider(model=model)
    if requested == "ollama":
        base = str(_config_attr(config, "ollama_base_url", "http://localhost:11434"))
        return OllamaEmbedProvider(base_url=base, model=model)
    if requested == "hash":
        if os.environ.get("ANDROSCAN_RAG_ALLOW_HASH") != "1":
            raise EmbedProviderError(
                "'hash' provider is only enabled when "
                "ANDROSCAN_RAG_ALLOW_HASH=1 (intended for tests)."
            )
        return HashProvider()
    raise EmbedProviderError(
        f"Unknown rag.embed_provider: {requested!r}. "
        f"Choose one of: fastembed | ollama | hash"
    )
