"""Lane B's single point of contact with M0's shared infrastructure.

M0 owns `pipeline/cache.py` (hash-keyed disk cache + rate-limited HTTP client).
Until it lands, this module provides a minimal stand-in with the same storage
layout idea (one JSON file per request hash under ATLAS_CACHE_DIR). When M0
merges, replace the fallback bodies with thin wrappers over `pipeline.cache`
and nothing else in lane B needs to change.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]


def cache_dir() -> Path:
    d = Path(os.environ.get("ATLAS_CACHE_DIR", REPO_ROOT / "data" / "cache"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def _key(namespace: str, request: Any) -> str:
    blob = json.dumps(request, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(f"{namespace}\n{blob}".encode()).hexdigest()


_MISS = object()


def cache_lookup(namespace: str, request: Any, default: Any = _MISS) -> Any:
    """Return the cached value for `request`, or `default` (raising KeyError if none given)."""
    path = cache_dir() / namespace / f"{_key(namespace, request)}.json"
    if path.exists():
        return json.loads(path.read_text())["value"]
    if default is _MISS:
        raise KeyError(request)
    return default


def cached(namespace: str, request: Any, compute: Callable[[], Any]) -> Any:
    """Return the cached JSON value for `request`, computing and storing it on a miss."""
    path = cache_dir() / namespace / f"{_key(namespace, request)}.json"
    if path.exists():
        return json.loads(path.read_text())["value"]
    value = compute()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"request": request, "value": value}, ensure_ascii=False))
    tmp.replace(path)
    return value


class _RateLimiter:
    def __init__(self, per_second: float):
        self.interval = 1.0 / per_second
        self.lock = threading.Lock()
        self.next_at = 0.0

    def wait(self) -> None:
        with self.lock:
            now = time.monotonic()
            if now < self.next_at:
                time.sleep(self.next_at - now)
            self.next_at = max(now, self.next_at) + self.interval


_NCBI = _RateLimiter(9.0 if os.environ.get("NCBI_API_KEY") else 2.8)
_OTHER = _RateLimiter(5.0)
_client = httpx.Client(timeout=60, headers={"User-Agent": "rare-disease-atlas/0.1 (hackathon)"})


def http_get(url: str, params: dict | None = None, *, as_json: bool = False) -> Any:
    """Cached, rate-limited GET. Returns text (or parsed JSON). Retries transient errors."""
    params = dict(params or {})
    is_ncbi = "ncbi.nlm.nih.gov" in url
    request = {"url": url, "params": params}  # API key excluded from the cache key

    def fetch() -> Any:
        send = dict(params)
        if is_ncbi and os.environ.get("NCBI_API_KEY"):
            send["api_key"] = os.environ["NCBI_API_KEY"]
        for attempt in range(5):
            (_NCBI if is_ncbi else _OTHER).wait()
            try:
                r = _client.get(url, params=send)
                if r.status_code in (429, 500, 502, 503, 504):
                    raise httpx.HTTPStatusError("retryable", request=r.request, response=r)
                r.raise_for_status()
                return r.json() if as_json else r.text
            except (httpx.TransportError, httpx.HTTPStatusError):
                if attempt == 4:
                    raise
                time.sleep(2**attempt)

    return cached("http", request, fetch)
