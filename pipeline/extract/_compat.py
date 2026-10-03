"""Lane B's single point of contact with M0's shared cache (`pipeline/cache.py`).

Thin wrappers so lane B modules keep a small, stable call surface:
`cached` / `cache_lookup` over `DiskCache`, and `http_get` over the shared
rate-limited `CachedHttp` client (NCBI limits and the API key are handled there).
"""
from __future__ import annotations

from typing import Any, Callable

from pipeline.cache import (
    LLM_OK_STATUS,
    REPO_ROOT,
    CacheMissOffline,
    DiskCache,
    default_http,
    load_env,
)

__all__ = ["REPO_ROOT", "CacheMissOffline", "cached", "cache_lookup", "http_get", "load_env"]

_MISS = object()


def cache_lookup(namespace: str, request: Any, default: Any = _MISS) -> Any:
    """The cached value for `request`, or `default` (raising KeyError if none given)."""
    entry = DiskCache().get(namespace, request)
    if entry is not None:
        return entry.response
    if default is _MISS:
        raise KeyError(request)
    return default


def cached(namespace: str, request: Any, compute: Callable[[], Any]) -> Any:
    """Return the cached JSON value for `request`, computing and storing it on a miss.
    Offline (ATLAS_OFFLINE=1), a miss raises `CacheMissOffline`."""
    return DiskCache().cached(namespace, request, lambda: (compute(), LLM_OK_STATUS)).response


def http_get(url: str, params: dict | None = None, *, as_json: bool = False) -> Any:
    """Cached, retried, rate-limited GET. Returns text, or parsed JSON. Non-2xx raises."""
    http = default_http()
    resp = (http.get_json if as_json else http.get_text)(url, params=params)
    resp.raise_for_status()
    return resp.json() if as_json else resp.text
