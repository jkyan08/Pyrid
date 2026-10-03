"""pipeline/cache.py: hash-keyed disk cache, cached HTTP with retries, rate limiter, offline switch.

Fully offline: every request goes through ``httpx.MockTransport`` and time is faked. URLs and
payloads are obviously mock values.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import httpx
import pytest

from pipeline.cache import (
    NCBI_HOST,
    REPO_ROOT,
    CachedHttp,
    CacheMissOffline,
    DiskCache,
    HostRateLimiter,
    HttpStatusError,
    TokenBucket,
    llm_cached,
    request_key,
    resolve_cache_dir,
)

MOCK_URL = "https://mock.example/api/mock-lookup"
NCBI_URL = f"https://{NCBI_HOST}/entrez/eutils/esearch.fcgi"


# --------------------------------------------------------------------------------------
# fixtures and helpers
# --------------------------------------------------------------------------------------


@pytest.fixture
def cache_env(tmp_path, monkeypatch):
    """Point the cache at tmp_path and clear every switch the module reads from the environment."""
    monkeypatch.setenv("ATLAS_CACHE_DIR", str(tmp_path))
    monkeypatch.delenv("ATLAS_OFFLINE", raising=False)
    monkeypatch.delenv("NCBI_API_KEY", raising=False)
    return tmp_path


class FakeClock:
    """Monotonic clock whose sleep() advances time instead of waiting."""

    def __init__(self, start: float = 1000.0) -> None:
        self.t = start
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


def counting_transport(handler):
    """MockTransport that records every request it receives."""
    seen: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    return httpx.MockTransport(wrapped), seen


def ok_json(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"label": "Mock result", "path": request.url.path})


def make_http(handler, clock: FakeClock | None = None, **kwargs) -> tuple[CachedHttp, list[httpx.Request]]:
    clock = clock or FakeClock()
    transport, seen = counting_transport(handler)
    http = CachedHttp(DiskCache(), transport=transport, clock=clock.now, sleep=clock.sleep, **kwargs)
    return http, seen


# --------------------------------------------------------------------------------------
# keys
# --------------------------------------------------------------------------------------


def test_key_independent_of_dict_order():
    a = {"model": "mock-model", "temperature": 0, "messages": [{"role": "user", "content": "Mock prompt"}]}
    b = {"messages": [{"content": "Mock prompt", "role": "user"}], "temperature": 0, "model": "mock-model"}
    assert request_key(a) == request_key(b)
    assert len(request_key(a)) == 64
    assert request_key({**a, "temperature": 1}) != request_key(a)


def test_http_key_independent_of_param_order_and_url_query(cache_env):
    http = CachedHttp(DiskCache(), transport=httpx.MockTransport(ok_json))
    d1 = http.describe("GET", MOCK_URL, params={"term": "mock", "retmax": 5})
    d2 = http.describe("GET", MOCK_URL, params=[("retmax", "5"), ("term", "mock")])
    d3 = http.describe("GET", MOCK_URL + "?retmax=5&term=mock")
    assert request_key(d1) == request_key(d2) == request_key(d3)
    assert d1["params"] == [["retmax", "5"], ["term", "mock"]]
    assert d1["url"] == MOCK_URL
    # The HTTP method, the body and content-negotiation headers all matter.
    assert request_key(http.describe("POST", MOCK_URL, params={"term": "mock", "retmax": 5})) != request_key(d1)
    assert request_key(http.describe("POST", MOCK_URL, body={"a": 1})) != request_key(http.describe("POST", MOCK_URL, body={"a": 2}))
    assert request_key(http.describe("POST", MOCK_URL, body={"a": 1, "b": 2})) == request_key(
        http.describe("POST", MOCK_URL, body={"b": 2, "a": 1})
    )


def test_secrets_and_volatile_headers_are_not_part_of_the_key(cache_env):
    http = CachedHttp(DiskCache(), transport=httpx.MockTransport(ok_json))
    plain = http.describe("GET", MOCK_URL, params={"q": "mock"})
    with_secrets = http.describe(
        "GET",
        MOCK_URL,
        params={"q": "mock", "api_key": "mock-secret-value"},
        headers={"Authorization": "Bearer mock-secret-value", "User-Agent": "mock-agent"},
    )
    assert request_key(plain) == request_key(with_secrets)
    assert "mock-secret-value" not in json.dumps(with_secrets)
    assert request_key(http.describe("GET", MOCK_URL, params={"q": "mock"}, headers={"Accept": "text/xml"})) != request_key(plain)


# --------------------------------------------------------------------------------------
# DiskCache
# --------------------------------------------------------------------------------------


def test_cache_dir_from_env_and_default(cache_env, monkeypatch):
    assert DiskCache().root == cache_env
    monkeypatch.setenv("ATLAS_CACHE_DIR", "mock-relative/cache")
    assert resolve_cache_dir() == (REPO_ROOT / "mock-relative/cache").resolve()
    monkeypatch.delenv("ATLAS_CACHE_DIR")
    assert resolve_cache_dir() == (REPO_ROOT / "data/cache").resolve()
    assert DiskCache(cache_env / "explicit").root == cache_env / "explicit"


def test_get_miss_put_hit_and_layout(cache_env):
    cache = DiskCache()
    request = {"method": "GET", "url": MOCK_URL, "params": [["q", "mock"]]}
    assert cache.get("mockns", request) is None
    assert not (cache_env / "mockns").exists()  # created on demand only

    before = datetime.now(timezone.utc)
    entry = cache.put("mockns", request, {"text": "Mock body"}, 200)
    assert entry.from_cache is False
    key = request_key(request)
    assert entry.key == key
    assert entry.path == cache_env / "mockns" / key[:2] / f"{key}.json"
    assert entry.path.is_file()

    record = json.loads(entry.path.read_text(encoding="utf-8"))
    assert set(record) == {"namespace", "key", "request", "status", "retrieved_at", "response"}
    assert record["request"] == request
    assert record["response"] == {"text": "Mock body"}
    assert record["status"] == 200
    retrieved = datetime.fromisoformat(record["retrieved_at"])
    assert retrieved.tzinfo is not None and retrieved.utcoffset().total_seconds() == 0
    assert before.replace(microsecond=0) <= retrieved <= datetime.now(timezone.utc)
    assert entry.retrieved_date == record["retrieved_at"][:10]

    hit = cache.get("mockns", request)
    assert hit is not None
    assert hit.from_cache is True
    assert hit.response == {"text": "Mock body"}
    assert hit.status == 200
    assert hit.retrieved_at == record["retrieved_at"]
    # Same request, different namespace: a separate record.
    assert cache.get("othermockns", request) is None


def test_corrupt_file_is_a_miss(cache_env):
    cache = DiskCache()
    request = {"q": "mock"}
    entry = cache.put("mockns", request, "x", 200)
    entry.path.write_text("not json", encoding="utf-8")
    assert cache.get("mockns", request) is None


def test_cached_helper_fetches_once(cache_env):
    cache = DiskCache()
    calls = 0

    def fetch():
        nonlocal calls
        calls += 1
        return {"value": "Mock payload"}, 200

    first = cache.cached("mockns", {"q": "mock"}, fetch)
    second = cache.cached("mockns", {"q": "mock"}, fetch)
    assert calls == 1
    assert first.from_cache is False and second.from_cache is True
    assert first.response == second.response == {"value": "Mock payload"}


def test_namespace_must_be_a_plain_name(cache_env):
    with pytest.raises(ValueError):
        DiskCache().path_for("../escape", "ab" * 32)


# --------------------------------------------------------------------------------------
# CachedHttp
# --------------------------------------------------------------------------------------


def test_second_get_json_does_not_touch_the_network(cache_env):
    http, seen = make_http(ok_json)
    first = http.get_json(MOCK_URL, params={"q": "mock"}, namespace="mocksrc")
    second = http.get_json(MOCK_URL, params={"q": "mock"}, namespace="mocksrc")
    assert len(seen) == 1
    assert first.from_cache is False and second.from_cache is True
    assert first.status == second.status == 200
    assert first.json() == second.json() == {"label": "Mock result", "path": "/api/mock-lookup"}
    assert first.retrieved_at == second.retrieved_at
    assert second.ok
    assert seen[0].headers["accept"] == "application/json"
    assert seen[0].headers["user-agent"].startswith("rare-disease-atlas/")
    # A fresh client over the same directory also hits the cache.
    again, seen_again = make_http(ok_json)
    assert again.get_json(MOCK_URL, params={"q": "mock"}, namespace="mocksrc").from_cache is True
    assert seen_again == []
    assert (cache_env / "mocksrc").is_dir()


def test_param_order_does_not_cause_a_second_request(cache_env):
    http, seen = make_http(ok_json)
    http.get_json(MOCK_URL, params={"a": 1, "b": 2})
    http.get_json(MOCK_URL, params={"b": 2, "a": 1})
    http.get_json(MOCK_URL + "?b=2&a=1")
    assert len(seen) == 1


def test_get_text_and_post_json(cache_env):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"echo": json.loads(request.content)})
        return httpx.Response(200, text="<mock>Mock XML</mock>", headers={"content-type": "text/xml"})

    http, seen = make_http(handler)
    text = http.get_text(MOCK_URL, params={"rettype": "xml"})
    assert text.text == "<mock>Mock XML</mock>"
    assert text.headers["content-type"] == "text/xml"
    assert seen[0].headers["accept"] == "*/*"  # httpx's default: get_text does not force JSON

    a = http.post_json(MOCK_URL, body={"ids": ["mock:1"]})
    b = http.post_json(MOCK_URL, body={"ids": ["mock:2"]})
    c = http.post_json(MOCK_URL, body={"ids": ["mock:1"]})
    assert a.json() == {"echo": {"ids": ["mock:1"]}}
    assert b.json() == {"echo": {"ids": ["mock:2"]}}
    assert c.from_cache is True and c.json() == a.json()
    assert len(seen) == 3


def test_non_2xx_response_is_cached(cache_env):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "Mock not found"})

    http, seen = make_http(handler)
    first = http.get_json(MOCK_URL, params={"id": "mock:missing"})
    second = http.get_json(MOCK_URL, params={"id": "mock:missing"})
    assert len(seen) == 1
    assert first.status == second.status == 404
    assert not second.ok
    assert second.from_cache is True
    assert second.json() == {"error": "Mock not found"}
    with pytest.raises(HttpStatusError):
        second.raise_for_status()
    entry = DiskCache().get("http", http.describe("GET", MOCK_URL, params={"id": "mock:missing"}, headers={"accept": "application/json"}))
    assert entry is not None and entry.status == 404


def test_server_error_is_retried_with_backoff_stored_and_served_offline(cache_env, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="Mock unavailable")

    clock = FakeClock()
    http, seen = make_http(handler, clock)
    response = http.get_json(MOCK_URL, params={"q": "mock"})
    assert len(seen) == 3  # three attempts
    assert clock.sleeps == [0.5, 1.0]  # exponential backoff between attempts
    assert response.status == 503 and response.from_cache is False
    assert response.text == "Mock unavailable"

    # The failure is on disk, so it is reproducible offline without a network call.
    monkeypatch.setenv("ATLAS_OFFLINE", "1")
    offline = http.get_json(MOCK_URL, params={"q": "mock"})
    assert offline.from_cache is True and offline.status == 503
    assert len(seen) == 3

    # Online again, a stored 5xx is revalidated rather than trusted forever.
    monkeypatch.delenv("ATLAS_OFFLINE")
    http.get_json(MOCK_URL, params={"q": "mock"})
    assert len(seen) == 6


def test_retry_after_header_is_honoured(cache_env):
    state = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["n"] += 1
        if state["n"] == 1:
            return httpx.Response(429, headers={"retry-after": "2"})
        return ok_json(request)

    clock = FakeClock()
    http, seen = make_http(handler, clock)
    response = http.get_json(MOCK_URL)
    assert response.status == 200 and len(seen) == 2
    assert clock.sleeps == [2.0]


def test_transport_error_is_retried_then_succeeds(cache_env):
    state = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["n"] += 1
        if state["n"] < 3:
            raise httpx.ConnectError("mock connection failure")
        return ok_json(request)

    clock = FakeClock()
    http, seen = make_http(handler, clock)
    response = http.get_json(MOCK_URL)
    assert response.status == 200 and response.from_cache is False
    assert len(seen) == 3
    assert clock.sleeps == [0.5, 1.0]


def test_transport_error_exhausted_raises_and_caches_nothing(cache_env):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("mock timeout")

    clock = FakeClock()
    http, seen = make_http(handler, clock)
    with pytest.raises(httpx.TransportError):
        http.get_json(MOCK_URL)
    assert len(seen) == 3
    assert DiskCache().get("http", http.describe("GET", MOCK_URL, headers={"accept": "application/json"})) is None


def test_offline_raises_on_miss_and_serves_hits(cache_env, monkeypatch):
    http, seen = make_http(ok_json)
    http.get_json(MOCK_URL, params={"q": "cached-mock"})
    assert len(seen) == 1

    monkeypatch.setenv("ATLAS_OFFLINE", "1")
    hit = http.get_json(MOCK_URL, params={"q": "cached-mock"})
    assert hit.from_cache is True and hit.json()["label"] == "Mock result"
    with pytest.raises(CacheMissOffline) as excinfo:
        http.get_json(MOCK_URL, params={"q": "never-fetched-mock"})
    assert len(seen) == 1
    assert excinfo.value.namespace == "http"
    assert MOCK_URL in str(excinfo.value)

    # The generic helpers follow the same switch.
    with pytest.raises(CacheMissOffline):
        DiskCache().cached("mockns", {"q": "mock"}, lambda: ("never", 200))
    with pytest.raises(CacheMissOffline):
        llm_cached({"model": "mock-model", "input": "Mock prompt"}, lambda: {"never": True})
    monkeypatch.setenv("ATLAS_OFFLINE", "0")
    assert DiskCache().cached("mockns", {"q": "mock"}, lambda: ("fetched", 200)).response == "fetched"


# --------------------------------------------------------------------------------------
# rate limiting
# --------------------------------------------------------------------------------------


def test_token_bucket_spacing_with_mock_time():
    clock = FakeClock()
    bucket = TokenBucket(2.0, clock=clock.now, sleep=clock.sleep)
    waits = [bucket.acquire() for _ in range(4)]
    assert waits == pytest.approx([0.0, 0.5, 0.5, 0.5])
    assert clock.sleeps == pytest.approx([0.5, 0.5, 0.5])
    # After a long pause there is no wait, but tokens never bank beyond the burst of 1.
    clock.t += 10
    assert bucket.acquire() == 0.0
    assert bucket.acquire() == pytest.approx(0.5)


def test_host_rate_limiter_matches_hosts_and_parent_domains():
    clock = FakeClock()
    limiter = HostRateLimiter({"mock.example": 4.0}, clock=clock.now, sleep=clock.sleep)
    assert limiter.limit_for("mock.example") == 4.0
    assert limiter.limit_for("api.mock.example") == 4.0  # parent-domain limit applies
    assert limiter.limit_for("unrelated.example") is None
    assert [limiter.acquire("unrelated.example") for _ in range(5)] == [0.0] * 5
    assert clock.sleeps == []
    # Subdomains of one limited domain share a bucket.
    assert limiter.acquire("mock.example") == 0.0
    assert limiter.acquire("api.mock.example") == pytest.approx(0.25)
    limiter.set_limit("mock.example", None)
    assert limiter.acquire("mock.example") == 0.0


def test_ncbi_default_is_three_per_second(cache_env):
    clock = FakeClock()
    http, seen = make_http(ok_json, clock)
    assert http.limiter.limit_for(NCBI_HOST) == 3.0
    for i in range(3):
        http.get_json(NCBI_URL, params={"db": "pubmed", "term": f"Mock term {i}"})
    assert len(seen) == 3
    assert clock.sleeps == pytest.approx([1 / 3, 1 / 3])
    # Other hosts are unlimited by default.
    http.get_json(MOCK_URL)
    assert clock.sleeps == pytest.approx([1 / 3, 1 / 3])
    # Cache hits never wait on the limiter.
    http.get_json(NCBI_URL, params={"db": "pubmed", "term": "Mock term 0"})
    assert clock.sleeps == pytest.approx([1 / 3, 1 / 3])


def test_ncbi_api_key_raises_rate_and_is_sent_but_never_stored(cache_env, monkeypatch):
    monkeypatch.setenv("NCBI_API_KEY", "mock-ncbi-key-not-real")
    clock = FakeClock()
    http, seen = make_http(ok_json, clock)
    assert http.limiter.limit_for(NCBI_HOST) == 10.0
    for i in range(3):
        http.get_json(NCBI_URL, params={"db": "pubmed", "term": f"Mock term {i}"})
    assert clock.sleeps == pytest.approx([0.1, 0.1])
    assert all(r.url.params["api_key"] == "mock-ncbi-key-not-real" for r in seen)
    # Not appended for other hosts.
    http.get_json(MOCK_URL)
    assert "api_key" not in seen[-1].url.params

    files = list(cache_env.rglob("*.json"))
    assert len(files) == 4
    for f in files:
        assert "mock-ncbi-key-not-real" not in f.read_text(encoding="utf-8")
    # The same request without a key is a cache hit: the key is not part of the request description.
    monkeypatch.delenv("NCBI_API_KEY")
    plain, seen_plain = make_http(ok_json)
    assert plain.get_json(NCBI_URL, params={"db": "pubmed", "term": "Mock term 1"}).from_cache is True
    assert seen_plain == []


def test_host_limits_are_configurable(cache_env):
    clock = FakeClock()
    http, seen = make_http(ok_json, clock, host_limits={"mock.example": 5.0})
    for i in range(3):
        http.get_json(MOCK_URL, params={"i": i})
    assert clock.sleeps == pytest.approx([0.2, 0.2])
    http.limiter.set_limit("mock.example", 1.0)
    http.get_json(MOCK_URL, params={"i": 10})
    http.get_json(MOCK_URL, params={"i": 11})
    assert clock.sleeps[-1] == pytest.approx(1.0)


# --------------------------------------------------------------------------------------
# LLM hook
# --------------------------------------------------------------------------------------


def test_llm_cached_uses_llm_namespace_and_calls_once(cache_env):
    calls = 0
    request = {
        "model": "mock-model",
        "messages": [{"role": "user", "content": "Mock prompt"}],
        "response_format": {"type": "json_schema", "json_schema": {"name": "MockClaim"}},
        "temperature": 0,
    }

    def call():
        nonlocal calls
        calls += 1
        return {"output": [{"type": "message", "content": "Mock answer"}], "usage": {"total_tokens": 1}}

    first = llm_cached(request, call)
    reordered = {k: request[k] for k in reversed(list(request))}
    second = llm_cached(reordered, call)
    assert calls == 1
    assert first == second == {"output": [{"type": "message", "content": "Mock answer"}], "usage": {"total_tokens": 1}}

    entry = DiskCache().get("llm", request)
    assert entry is not None
    assert entry.status == "ok"
    assert entry.path.parent.parent == cache_env / "llm"
    assert datetime.fromisoformat(entry.retrieved_at).tzinfo is not None

    # A different temperature is a different call.
    llm_cached({**request, "temperature": 0.7}, call)
    assert calls == 2
