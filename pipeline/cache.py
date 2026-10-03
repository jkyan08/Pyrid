"""Hash-keyed disk cache for every network and LLM call (README hard rule 6).

Everything the pipeline fetches goes through this module so that

* ``make data`` on a fresh clone rebuilds ``graph.json`` from ``data/cache/`` alone;
* the demo never depends on a live call: with ``ATLAS_OFFLINE=1`` a cache miss raises
  :class:`CacheMissOffline` instead of touching the network;
* a failed lookup is reproducible data (hard rule 3): non-2xx responses are stored like any other,
  each with a UTC ``retrieved_at`` timestamp that coverage records
  (``meta.coverage[].searched[].date``) can cite.

Layout
------
``<ATLAS_CACHE_DIR>/<namespace>/<first two hex of key>/<key>.json``, where ``key`` is the sha256
of the canonical JSON serialisation of the request description (:func:`request_key`). Each file
holds ``{"namespace", "key", "request", "status", "retrieved_at", "response"}``.

``ATLAS_CACHE_DIR`` defaults to ``data/cache``; a relative value is resolved against the repo
root (the parent of this package), so several worktrees can point at one absolute path and
share a cache. Keys are content hashes and writes are atomic, so concurrent writers are safe.

Three layers
------------
:class:`DiskCache`
    ``get`` / ``put`` / ``cached`` over JSON-serialisable request descriptions and payloads.
:class:`CachedHttp`
    ``get_json`` / ``get_text`` / ``post_json`` on top of ``httpx`` with retries (3 attempts,
    exponential backoff on 429, 5xx and transport errors) and a per-host token-bucket rate
    limiter. NCBI E-utilities are limited to 3 req/s, or 10 req/s when ``NCBI_API_KEY`` is set,
    in which case the key is appended as the ``api_key`` parameter automatically.
:func:`llm_cached`
    The hook lane B wraps its model calls in, under namespace ``"llm"``. This module does not
    import ``openai``.

Secrets (``Authorization``-style headers, ``api_key`` parameters) are stripped from the request
description, so they are neither part of the key nor written to disk.

Text and JSON only: binary downloads (e.g. OptimusKG Parquet slices) belong in ``data/raw/``.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import httpx

__all__ = [
    "CacheEntry",
    "CacheMissOffline",
    "CachedHttp",
    "CachedResponse",
    "DiskCache",
    "HostRateLimiter",
    "HttpStatusError",
    "TokenBucket",
    "canonical_json",
    "default_host_limits",
    "default_http",
    "is_offline",
    "llm_cached",
    "load_env",
    "request_key",
    "resolve_cache_dir",
]

REPO_ROOT = Path(__file__).resolve().parents[1]

ENV_CACHE_DIR = "ATLAS_CACHE_DIR"
ENV_OFFLINE = "ATLAS_OFFLINE"
ENV_NCBI_API_KEY = "NCBI_API_KEY"
DEFAULT_CACHE_DIR = "data/cache"

NCBI_HOST = "eutils.ncbi.nlm.nih.gov"
NCBI_RATE_NO_KEY = 3.0
NCBI_RATE_WITH_KEY = 10.0

DEFAULT_HTTP_NAMESPACE = "http"
LLM_NAMESPACE = "llm"
LLM_OK_STATUS = "ok"

USER_AGENT = "rare-disease-atlas/0.1"

# Never part of a cache key and never written to disk.
SECRET_HEADERS = frozenset({"authorization", "cookie", "x-api-key", "api-key", "openai-api-key"})
SECRET_PARAMS = frozenset({"api_key"})
# Vary between machines and runs without changing the answer, so they are left out of the key.
VOLATILE_HEADERS = frozenset({"user-agent", "accept-encoding", "connection", "host", "content-length"})
KEY_IGNORED_HEADERS = SECRET_HEADERS | VOLATILE_HEADERS
# The only response headers kept in the cache record.
STORED_RESPONSE_HEADERS = frozenset({"content-type", "date", "etag", "last-modified"})

# A cached response with one of these statuses is re-fetched when online (the stored failure is
# overwritten by whatever comes back) and served as-is when offline. Deterministic failures such
# as 404 are always served from the cache.
REVALIDATE_STATUSES = frozenset({429, 500, 502, 503, 504})

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


# --------------------------------------------------------------------------------------
# environment
# --------------------------------------------------------------------------------------


def load_env(path: str | os.PathLike[str] | None = None) -> bool:
    """Load ``.env`` from the repo root (or ``path``) without overriding real environment
    variables. Returns True when at least one variable was set. Never raises when python-dotenv
    or the file is missing."""
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - python-dotenv is a declared dependency
        return False
    env_path = REPO_ROOT / ".env" if path is None else Path(path)
    if not env_path.is_file():
        return False
    return bool(load_dotenv(env_path, override=False))


def env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _TRUE_VALUES


def is_offline() -> bool:
    """True when ``ATLAS_OFFLINE`` is set to 1/true/yes/on. Read at call time, not import time."""
    return env_flag(ENV_OFFLINE)


def resolve_cache_dir(root: str | os.PathLike[str] | None = None) -> Path:
    """``root`` if given, else ``$ATLAS_CACHE_DIR``, else ``data/cache``. Relative paths are
    resolved against the repo root so the cache does not depend on the working directory."""
    raw = os.environ.get(ENV_CACHE_DIR, "").strip() if root is None else os.fspath(root)
    path = Path(raw or DEFAULT_CACHE_DIR).expanduser()
    return path if path.is_absolute() else (REPO_ROOT / path).resolve()


load_env()


# --------------------------------------------------------------------------------------
# canonical keys
# --------------------------------------------------------------------------------------


def _json_default(obj: Any) -> Any:
    """Make the few non-JSON types that reach a request or response serialisable, without
    importing pydantic here (duck-typed ``model_dump``)."""
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, (set, frozenset)):
        return sorted(obj, key=canonical_json)
    if isinstance(obj, tuple):
        return list(obj)
    if isinstance(obj, Path):
        return str(obj)
    raise TypeError(f"{type(obj).__name__} is not JSON serialisable; cache requests and responses must be plain data")


def canonical_json(obj: Any) -> str:
    """Deterministic serialisation: sorted keys at every level, no whitespace, UTF-8 kept."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=_json_default)


def request_key(request: Any) -> str:
    """sha256 hex digest of :func:`canonical_json`\\ (request). Independent of dict ordering."""
    return hashlib.sha256(canonical_json(request).encode("utf-8")).hexdigest()


def to_jsonable(obj: Any) -> Any:
    """Round-trip through JSON so a value returned on a miss is the same plain data a later
    cache hit returns (pydantic models and dataclasses become dicts, tuples become lists)."""
    return json.loads(json.dumps(obj, default=_json_default))


# --------------------------------------------------------------------------------------
# disk cache
# --------------------------------------------------------------------------------------


class CacheMissOffline(LookupError):
    """Raised on a cache miss while ``ATLAS_OFFLINE`` is set (or ``DiskCache(offline=True)``)."""

    def __init__(self, namespace: str, key: str, request: Any) -> None:
        self.namespace = namespace
        self.key = key
        self.request = request
        summary = canonical_json(request)
        if len(summary) > 240:
            summary = summary[:237] + "..."
        super().__init__(f"offline ({ENV_OFFLINE}) and not cached: {namespace}/{key[:12]} {summary}")


@dataclass(frozen=True)
class CacheEntry:
    """One cache record. ``from_cache`` is False on the call that created it, True afterwards."""

    namespace: str
    key: str
    request: Any
    response: Any
    status: int | str
    retrieved_at: str
    path: Path
    from_cache: bool = False

    @property
    def retrieved_date(self) -> str:
        """``YYYY-MM-DD`` of ``retrieved_at``, the value coverage records cite as ``date``."""
        return self.retrieved_at[:10]

    def to_record(self) -> dict[str, Any]:
        return {
            "namespace": self.namespace,
            "key": self.key,
            "request": self.request,
            "status": self.status,
            "retrieved_at": self.retrieved_at,
            "response": self.response,
        }


def _check_namespace(namespace: str) -> str:
    if not namespace or namespace in {".", ".."} or any(ch in namespace for ch in "/\\\0"):
        raise ValueError(f"cache namespace must be a plain directory name, got {namespace!r}")
    return namespace


class DiskCache:
    """JSON files under ``<root>/<namespace>/<key[:2]>/<key>.json``, created on demand.

    ``offline=None`` (default) follows ``ATLAS_OFFLINE`` at call time; True/False forces it.
    ``now`` is injectable for tests and must return an aware UTC datetime.
    """

    def __init__(
        self,
        root: str | os.PathLike[str] | None = None,
        *,
        offline: bool | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.root = resolve_cache_dir(root)
        self.offline = offline
        self._now = now or (lambda: datetime.now(timezone.utc))

    def __repr__(self) -> str:
        return f"DiskCache({str(self.root)!r})"

    def is_offline(self) -> bool:
        return is_offline() if self.offline is None else self.offline

    def path_for(self, namespace: str, key: str) -> Path:
        return self.root / _check_namespace(namespace) / key[:2] / f"{key}.json"

    def timestamp(self) -> str:
        """Current UTC time as an ISO 8601 timestamp with second precision."""
        now = self._now()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        return now.astimezone(timezone.utc).isoformat(timespec="seconds")

    def get(self, namespace: str, request: Any) -> CacheEntry | None:
        """The stored entry for ``request``, or None on a miss. A corrupt file is a miss."""
        key = request_key(request)
        return self._read(namespace, key, self.path_for(namespace, key))

    def put(self, namespace: str, request: Any, response: Any, status: int | str) -> CacheEntry:
        """Store ``response`` under ``request`` (atomic write) and return the new entry."""
        key = request_key(request)
        path = self.path_for(namespace, key)
        entry = CacheEntry(
            namespace=namespace,
            key=key,
            request=to_jsonable(request),
            response=to_jsonable(response),
            status=status,
            retrieved_at=self.timestamp(),
            path=path,
            from_cache=False,
        )
        self._write(path, entry.to_record())
        return entry

    def cached(
        self,
        namespace: str,
        request: Any,
        fetch_fn: Callable[[], tuple[Any, int | str]],
        *,
        stale: Callable[[CacheEntry], bool] | None = None,
    ) -> CacheEntry:
        """Return the cached entry for ``request``; on a miss call ``fetch_fn() -> (response,
        status)`` and store the result. Offline, a miss raises :class:`CacheMissOffline`.

        ``stale(entry)`` may mark a hit as worth re-fetching (e.g. a stored 503). A stale entry
        is still served when offline, so the demo reproduces the gap instead of crashing.
        """
        key = request_key(request)
        path = self.path_for(namespace, key)
        entry = self._read(namespace, key, path)
        offline = self.is_offline()
        if entry is not None and (offline or stale is None or not stale(entry)):
            return entry
        if offline:
            raise CacheMissOffline(namespace, key, request)
        response, status = fetch_fn()
        return self.put(namespace, request, response, status)

    # -- files ------------------------------------------------------------------------

    def _read(self, namespace: str, key: str, path: Path) -> CacheEntry | None:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(record, dict) or "response" not in record or "status" not in record:
            return None
        return CacheEntry(
            namespace=namespace,
            key=key,
            request=record.get("request"),
            response=record["response"],
            status=record["status"],
            retrieved_at=str(record.get("retrieved_at", "")),
            path=path,
            from_cache=True,
        )

    @staticmethod
    def _write(path: Path, record: Mapping[str, Any]) -> None:
        text = json.dumps(record, indent=1, sort_keys=True, ensure_ascii=False, default=_json_default)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        try:
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, path)
        finally:
            if tmp.exists():
                tmp.unlink(missing_ok=True)


# --------------------------------------------------------------------------------------
# rate limiting
# --------------------------------------------------------------------------------------


class TokenBucket:
    """``rate`` tokens per second, at most ``burst`` banked. ``burst=1`` (the default) means a
    strict spacing of ``1/rate`` seconds between requests, which is the conservative reading of
    NCBI's "N requests per second". ``clock``/``sleep`` are injectable for tests."""

    def __init__(
        self,
        rate: float,
        burst: float = 1.0,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if rate <= 0:
            raise ValueError("rate must be positive (omit the host to leave it unlimited)")
        self.rate = float(rate)
        self.burst = max(1.0, float(burst))
        self._clock = clock
        self._sleep = sleep
        self._tokens = self.burst
        self._updated = clock()
        self._lock = threading.Lock()

    def acquire(self) -> float:
        """Take one token, sleeping until one is available. Returns the seconds waited."""
        with self._lock:
            now = self._clock()
            self._tokens = min(self.burst, self._tokens + (now - self._updated) * self.rate)
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                self._updated = now
                return 0.0
            wait = (1.0 - self._tokens) / self.rate
            self._sleep(wait)
            # The token we waited for is spent; account from the moment it became available.
            self._tokens = 0.0
            self._updated = now + wait
            return wait


class HostRateLimiter:
    """Per-host token buckets. Hosts without a limit are unlimited. A limit on a parent domain
    (``ncbi.nlm.nih.gov``) covers its subdomains, and they share one bucket, unless a subdomain
    has its own entry."""

    def __init__(
        self,
        limits: Mapping[str, float] | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._clock = clock
        self._sleep = sleep
        self._limits: dict[str, tuple[float, float]] = {}
        self._buckets: dict[str, TokenBucket] = {}
        self._lock = threading.Lock()
        for host, rate in (limits or {}).items():
            self.set_limit(host, rate)

    def set_limit(self, host: str, rate: float | None, burst: float = 1.0) -> None:
        """``rate`` requests per second for ``host`` (None removes the limit)."""
        host = host.lower()
        with self._lock:
            self._buckets.pop(host, None)
            if rate is None:
                self._limits.pop(host, None)
            else:
                self._limits[host] = (float(rate), float(burst))

    def _match(self, host: str) -> str | None:
        labels = host.lower().split(".")
        for i in range(len(labels)):
            candidate = ".".join(labels[i:])
            if candidate in self._limits:
                return candidate
        return None

    def limit_for(self, host: str) -> float | None:
        """Requests per second that apply to ``host``, or None when unlimited."""
        match = self._match(host)
        return None if match is None else self._limits[match][0]

    def acquire(self, host: str) -> float:
        """Wait for a token for ``host`` and return the seconds waited (0 when unlimited)."""
        match = self._match(host)
        if match is None:
            return 0.0
        with self._lock:
            bucket = self._buckets.get(match)
            if bucket is None:
                rate, burst = self._limits[match]
                bucket = self._buckets[match] = TokenBucket(rate, burst, clock=self._clock, sleep=self._sleep)
        return bucket.acquire()


def default_host_limits(ncbi_api_key: str | None = None) -> dict[str, float]:
    """NCBI E-utilities: 3 req/s, or 10 req/s with an API key. Everything else unlimited."""
    return {NCBI_HOST: NCBI_RATE_WITH_KEY if ncbi_api_key else NCBI_RATE_NO_KEY}


# --------------------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CachedResponse:
    """What callers get back from :class:`CachedHttp`: the same shape whether it came from the
    network or the cache."""

    status: int
    text: str
    url: str
    headers: Mapping[str, str]
    from_cache: bool
    retrieved_at: str
    key: str

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    @property
    def retrieved_date(self) -> str:
        return self.retrieved_at[:10]

    def json(self) -> Any:
        return json.loads(self.text)

    def raise_for_status(self) -> CachedResponse:
        if not self.ok:
            raise HttpStatusError(self)
        return self

    @classmethod
    def from_entry(cls, entry: CacheEntry) -> CachedResponse:
        payload = entry.response if isinstance(entry.response, Mapping) else {}
        return cls(
            status=int(entry.status),
            text=str(payload.get("text", "")),
            url=str(payload.get("url", "")),
            headers=dict(payload.get("headers") or {}),
            from_cache=entry.from_cache,
            retrieved_at=entry.retrieved_at,
            key=entry.key,
        )


class HttpStatusError(RuntimeError):
    def __init__(self, response: CachedResponse) -> None:
        self.response = response
        super().__init__(f"HTTP {response.status} for {response.url}")


ParamsType = Mapping[str, Any] | Sequence[tuple[str, Any]] | None
HeadersType = Mapping[str, str] | None


def _public_url(url: httpx.URL) -> str:
    """``url`` without secret query parameters (what gets written to disk)."""
    items = [(k, v) for k, v in url.params.multi_items() if k not in SECRET_PARAMS]
    return str(httpx.URL(str(url.copy_with(query=None)), params=items))


class CachedHttp:
    """Cached, retried, rate-limited HTTP on top of ``httpx``.

    On a cache hit the network is never touched. On a miss the request is sent with up to
    ``max_attempts`` attempts (exponential backoff from ``backoff_base`` seconds, honouring a
    numeric ``Retry-After``), retrying on 429, 5xx and transport errors. The final response is
    stored whatever its status, so a 404 or an exhausted 503 is reproducible data. A transport
    error that survives every attempt propagates as ``httpx.TransportError``: a timeout is not a
    result and must not poison the cache.

    ``transport`` (e.g. ``httpx.MockTransport``) or ``client`` lets tests stay offline;
    ``clock``/``sleep`` make backoff and rate limiting deterministic.
    """

    def __init__(
        self,
        cache: DiskCache | None = None,
        *,
        namespace: str = DEFAULT_HTTP_NAMESPACE,
        client: httpx.Client | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 30.0,
        headers: HeadersType = None,
        host_limits: Mapping[str, float] | None = None,
        limiter: HostRateLimiter | None = None,
        ncbi_api_key: str | None = None,
        max_attempts: int = 3,
        backoff_base: float = 0.5,
        backoff_max: float = 30.0,
        revalidate_statuses: frozenset[int] = REVALIDATE_STATUSES,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.cache = cache if cache is not None else DiskCache()
        self.namespace = _check_namespace(namespace)
        key = os.environ.get(ENV_NCBI_API_KEY, "") if ncbi_api_key is None else ncbi_api_key
        self.ncbi_api_key = key.strip()
        self.api_key_hosts = frozenset({NCBI_HOST})
        limits = default_host_limits(self.ncbi_api_key)
        limits.update(host_limits or {})
        self.limiter = limiter if limiter is not None else HostRateLimiter(limits, clock=clock, sleep=sleep)
        self.max_attempts = max(1, int(max_attempts))
        self.backoff_base = float(backoff_base)
        self.backoff_max = float(backoff_max)
        self.revalidate_statuses = frozenset(revalidate_statuses)
        self._sleep = sleep
        self._owns_client = client is None
        self._client = client if client is not None else httpx.Client(
            transport=transport,
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT, **(headers or {})},
        )

    # -- lifecycle --------------------------------------------------------------------

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> CachedHttp:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- public API -------------------------------------------------------------------

    def get_json(
        self, url: str, params: ParamsType = None, headers: HeadersType = None, *, namespace: str | None = None
    ) -> CachedResponse:
        """GET with ``Accept: application/json``; parse with ``.json()``."""
        return self.request("GET", url, params=params, headers=_merge_headers({"accept": "application/json"}, headers), namespace=namespace)

    def get_text(
        self, url: str, params: ParamsType = None, headers: HeadersType = None, *, namespace: str | None = None
    ) -> CachedResponse:
        """GET for XML or plain text (e.g. E-utilities ``efetch``); read ``.text``."""
        return self.request("GET", url, params=params, headers=headers, namespace=namespace)

    def post_json(
        self,
        url: str,
        body: Any = None,
        params: ParamsType = None,
        headers: HeadersType = None,
        *,
        namespace: str | None = None,
    ) -> CachedResponse:
        """POST ``body`` as JSON with ``Accept: application/json``. The body is part of the key."""
        return self.request(
            "POST",
            url,
            params=params,
            headers=_merge_headers({"accept": "application/json"}, headers),
            body=body,
            namespace=namespace,
        )

    def request(
        self,
        method: str,
        url: str,
        *,
        params: ParamsType = None,
        headers: HeadersType = None,
        body: Any = None,
        namespace: str | None = None,
    ) -> CachedResponse:
        ns = self.namespace if namespace is None else _check_namespace(namespace)
        description, send = self._prepare(method, url, params, headers, body)
        entry = self.cache.cached(ns, description, lambda: self._fetch(**send), stale=self._is_stale)
        return CachedResponse.from_entry(entry)

    def describe(
        self, method: str, url: str, params: ParamsType = None, headers: HeadersType = None, body: Any = None
    ) -> dict[str, Any]:
        """The request description that is hashed into the cache key: method, URL without its
        query, sorted query items (secrets removed), headers that matter, JSON body."""
        return self._prepare(method, url, params, headers, body)[0]

    # -- internals --------------------------------------------------------------------

    def _prepare(
        self, method: str, url: str, params: ParamsType, headers: HeadersType, body: Any
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        base = httpx.URL(url)
        host = base.host.lower()
        items: list[tuple[str, str]] = list(base.params.multi_items())
        if params is not None:
            items.extend(httpx.QueryParams(params).multi_items())
        all_headers = _merge_headers(headers)
        description = {
            "method": method.upper(),
            "url": str(base.copy_with(query=None)),
            "params": [[k, v] for k, v in sorted(items) if k not in SECRET_PARAMS],
            "headers": {k: v for k, v in all_headers.items() if k not in KEY_IGNORED_HEADERS},
            "body": body,
        }
        send_items = list(items)
        if host in self.api_key_hosts and self.ncbi_api_key and not any(k == "api_key" for k, _ in send_items):
            send_items.append(("api_key", self.ncbi_api_key))
        send = {
            "method": method.upper(),
            "url": description["url"],
            "params": send_items,
            "headers": all_headers,
            "body": body,
            "host": host,
        }
        return description, send

    def _is_stale(self, entry: CacheEntry) -> bool:
        return isinstance(entry.status, int) and entry.status in self.revalidate_statuses

    @staticmethod
    def _should_retry(status: int) -> bool:
        return status == 429 or status >= 500

    def _backoff(self, attempt: int, retry_after: str | None = None) -> float:
        delay = self.backoff_base * (2 ** (attempt - 1))
        if retry_after:
            try:
                delay = max(delay, float(retry_after))
            except ValueError:
                pass
        return min(delay, self.backoff_max)

    def _fetch(
        self,
        *,
        method: str,
        url: str,
        params: list[tuple[str, str]],
        headers: dict[str, str],
        body: Any,
        host: str,
    ) -> tuple[dict[str, Any], int]:
        for attempt in range(1, self.max_attempts + 1):
            self.limiter.acquire(host)
            try:
                response = self._client.request(method, url, params=params or None, headers=headers or None, json=body)
            except httpx.TransportError:
                if attempt >= self.max_attempts:
                    raise
                self._sleep(self._backoff(attempt))
                continue
            if self._should_retry(response.status_code) and attempt < self.max_attempts:
                self._sleep(self._backoff(attempt, response.headers.get("retry-after")))
                continue
            payload = {
                "url": _public_url(response.url),
                "headers": {k: v for k, v in response.headers.items() if k.lower() in STORED_RESPONSE_HEADERS},
                "text": response.text,
            }
            return payload, response.status_code
        raise AssertionError("unreachable: the loop returns or raises")


def _merge_headers(*parts: HeadersType) -> dict[str, str]:
    """Lower-cased header names (HTTP header names are case-insensitive); later parts win."""
    merged: dict[str, str] = {}
    for part in parts:
        for k, v in (part or {}).items():
            merged[k.lower()] = v
    return merged


_default_http: CachedHttp | None = None
_default_http_lock = threading.Lock()


def default_http() -> CachedHttp:
    """One shared client for the process, so every source module shares the NCBI rate
    limiter. Built lazily from the environment on first use."""
    global _default_http
    with _default_http_lock:
        if _default_http is None:
            _default_http = CachedHttp()
        return _default_http


# --------------------------------------------------------------------------------------
# LLM hook
# --------------------------------------------------------------------------------------


def llm_cached(
    request: Mapping[str, Any],
    call_fn: Callable[[], Any],
    *,
    cache: DiskCache | None = None,
    namespace: str = LLM_NAMESPACE,
) -> Any:
    """Return ``call_fn()``'s result, cached under namespace ``"llm"`` by ``request``.

    ``request`` must hold everything that determines the answer: ``model``, ``messages`` or
    ``input``, ``response_format`` or the JSON schema, ``temperature`` (plus ``seed`` or tools
    when used). Lane B builds it from the same dict it passes to the client. The result must be
    JSON-serialisable (a pydantic response object is accepted and stored as ``model_dump``); the
    value returned is always the plain JSON data, so a miss and a later hit look identical.
    Offline, a miss raises :class:`CacheMissOffline`. A provider exception propagates and nothing
    is stored. For the ``retrieved_at`` of a hit, use ``DiskCache().get("llm", request)``.
    """
    store = cache if cache is not None else DiskCache()
    entry = store.cached(namespace, dict(request), lambda: (call_fn(), LLM_OK_STATUS))
    return entry.response
