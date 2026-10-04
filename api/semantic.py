"""Natural-language search: embed the query, rank nodes by meaning (README §10 global search).

``data/build/search_index.json`` (built by ``pipeline/search_index.py``) holds one
int8-packed embedding per searchable node. At query time we embed the user's
text with the same model and take cosine similarity against every vector.

Stdlib only (the deploy image ships FastAPI + uvicorn and nothing else). The
query embedding is one call to an OpenAI-compatible ``/embeddings`` endpoint:
OpenAI when ``OPENAI_API_KEY`` is set, else OpenRouter when ``OPENROUTER_API_KEY``
is (both are tried in turn, and the one that works is remembered). With no key,
``ATLAS_OFFLINE=1``, or every provider failing, ``embed_query`` returns None and
/search falls back to lexical matching; it never raises.
"""
from __future__ import annotations

import base64
import json
import logging
import math
import os
import urllib.request
from pathlib import Path
from typing import Callable

logger = logging.getLogger("api.semantic")

Embedder = Callable[[str], "list[float] | None"]

TIMEOUT_S = float(os.environ.get("ATLAS_SEARCH_EMBED_TIMEOUT", "4"))


class SemanticIndex:
    def __init__(self, data: dict):
        self.model: str = data["model"]
        self.dims: int = data["dims"]
        self.graph_built_at: str = data.get("graph_built_at", "")
        raw = base64.b64decode(data["vectors_int8_b64"])
        ids = data["ids"]
        if len(raw) != len(ids) * self.dims:
            raise ValueError("search index: vector bytes do not match ids x dims")
        self.ids: list[str] = ids
        self.vectors: list[list[float]] = [
            [(b - 256 if b > 127 else b) / 127.0 for b in raw[i * self.dims:(i + 1) * self.dims]]
            for i in range(len(ids))
        ]

    @classmethod
    def load(cls, path: Path | str | None) -> "SemanticIndex | None":
        if not path or not Path(path).is_file():
            return None
        try:
            return cls(json.loads(Path(path).read_text()))
        except Exception:  # a broken index must not take the API down
            logger.warning("search index at %s is unreadable; semantic search disabled", path, exc_info=True)
            return None

    def query(self, vector: list[float], k: int = 30, min_sim: float = 0.0) -> list[tuple[str, float]]:
        q = vector[: self.dims]
        n = math.sqrt(sum(x * x for x in q)) or 1.0
        q = [x / n for x in q]
        scored = []
        for nid, v in zip(self.ids, self.vectors):
            s = sum(a * b for a, b in zip(q, v))
            if s >= min_sim:
                scored.append((nid, s))
        scored.sort(key=lambda t: (-t[1], t[0]))
        return scored[:k]


def _endpoints() -> list[tuple[str, str, bool]]:
    """(url, key, via_openrouter) for each provider with a key, preferred first."""
    if os.environ.get("ATLAS_OFFLINE") in ("1", "true", "yes", "on"):
        return []
    eps = []
    if os.environ.get("OPENAI_API_KEY"):
        eps.append(("https://api.openai.com/v1/embeddings", os.environ["OPENAI_API_KEY"], False))
    if os.environ.get("OPENROUTER_API_KEY"):
        eps.append(("https://openrouter.ai/api/v1/embeddings", os.environ["OPENROUTER_API_KEY"], True))
    return eps


_WORKING: dict[str, str] = {}  # remembers the endpoint that last succeeded


def _call(url: str, key: str, via_openrouter: bool, text: str, model: str) -> tuple[float, ...]:
    wire_model = f"openai/{model}" if via_openrouter and "/" not in model else model
    req = urllib.request.Request(
        url,
        data=json.dumps({"model": wire_model, "input": text}).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
        return tuple(json.loads(resp.read())["data"][0]["embedding"])


_CACHE: dict[tuple[str, str], tuple[float, ...]] = {}
_CACHE_MAX = 2048


def _embed_cached(text: str, model: str) -> tuple[float, ...] | None:
    """Successful embeddings are cached in memory; failures are not, so a transient error
    doesn't disable semantic search for that query."""
    if (text, model) in _CACHE:
        return _CACHE[(text, model)]
    eps = _endpoints()
    eps.sort(key=lambda e: e[0] != _WORKING.get("url"))  # last working provider first
    vec = None
    for url, key, via_openrouter in eps:
        try:
            vec = _call(url, key, via_openrouter, text, model)
            _WORKING["url"] = url
            break
        except Exception as exc:  # quota, auth, network: try the next provider
            logger.warning("query embedding via %s failed (%s)", url.split("/")[2], type(exc).__name__)
    if vec is None:
        return None  # no provider worked: lexical search only
    if len(_CACHE) >= _CACHE_MAX:
        _CACHE.pop(next(iter(_CACHE)))
    _CACHE[(text, model)] = vec
    return vec


def embed_query(text: str, model: str) -> list[float] | None:
    v = _embed_cached(text.strip().lower(), model)
    return list(v) if v is not None else None
