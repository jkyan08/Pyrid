"""Cached OpenAI calls shared by the evidence pipeline (extract, reconcile, explain, draft).

Model names come from OPENAI_MODEL / OPENAI_EMBED_MODEL (hard rule 9). Every call
is cached on disk keyed by a hash of the full request (hard rule 6), so re-runs
and the demo never depend on live calls.

Provider for structured-output calls: ATLAS_LLM_PROVIDER=anthropic uses Claude
(ANTHROPIC_MODEL, e.g. `claude-sonnet-5-5`) via the Anthropic SDK; otherwise OpenAI
directly, or OpenAI models through OpenRouter when OPENROUTER_API_KEY is set.
Embeddings always use OpenAI models (Anthropic has no embeddings endpoint). Model names stay bare (e.g. `gpt-6-astra`); the
`openai/` prefix OpenRouter needs is added here, and cache keys use the bare
name, so cached results are shared across providers.
"""
from __future__ import annotations

import os
import threading
import time
from typing import TypeVar

from pydantic import BaseModel

from pipeline.cache import is_offline
from pipeline.extract._compat import CacheMissOffline, cache_lookup, cached, load_env

T = TypeVar("T", bound=BaseModel)

_client = None


# Offline (ATLAS_OFFLINE=1) cache misses raise M0's CacheMissOffline.
CacheMiss = CacheMissOffline


OPENROUTER_BASE = "https://openrouter.ai/api/v1"
MAX_ATTEMPTS = 6
# Output cap. Kept out of the cache key: it bounds length, not content (responses run ~1k tokens),
# and it stops OpenRouter reserving credit for its 65k default on every request.
MAX_OUTPUT_TOKENS = int(os.environ.get("ATLAS_LLM_MAX_TOKENS", "8000"))


class _Pacer:
    """Spaces live model calls to at most ATLAS_LLM_RPM per minute (default 18: OpenRouter
    limits new accounts to 20 rpm per model). Cache hits never wait."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.next_at = 0.0

    def wait(self) -> None:
        interval = 60.0 / float(os.environ.get("ATLAS_LLM_RPM", "18"))
        with self.lock:
            now = time.monotonic()
            start = max(now, self.next_at)
            self.next_at = start + interval
        time.sleep(max(0.0, start - now))


_pacer = _Pacer()


def _retry_after(exc) -> float | None:
    """Seconds to wait for a retryable provider error, or None if it is not retryable.

    Retryable: rate limits, transient server/network errors, and OpenRouter's 402
    `in_flight_budget_exhausted` (too many concurrent requests for the credit balance),
    which says to retry after in-flight requests settle."""
    from openai import APIConnectionError, APIStatusError, APITimeoutError, InternalServerError, RateLimitError
    if isinstance(exc, (RateLimitError, APIConnectionError, APITimeoutError, InternalServerError)):
        return 0.0
    if isinstance(exc, APIStatusError) and exc.status_code == 402 and "in_flight" in str(exc):
        try:
            return float(exc.response.headers.get("retry-after") or 120)
        except ValueError:
            return 120.0
    return None


def _with_retry(fn):
    """Pace, then retry retryable errors with exponential backoff (honouring Retry-After)."""
    for attempt in range(MAX_ATTEMPTS):
        _pacer.wait()
        try:
            return fn()
        except Exception as exc:
            wait = _retry_after(exc)
            if wait is None or attempt == MAX_ATTEMPTS - 1:
                raise
            time.sleep(min(150, max(wait, 5 * 2 ** attempt)))


load_env()  # fills unset variables from the repo's .env; the shell's own environment wins


def via_openrouter() -> bool:
    return bool(os.environ.get("OPENROUTER_API_KEY"))


def _wire_model(model: str) -> str:
    return f"openai/{model}" if via_openrouter() and "/" not in model else model


def _openai():
    global _client
    if _client is None:
        from openai import OpenAI
        if via_openrouter():
            _client = OpenAI(base_url=OPENROUTER_BASE, api_key=os.environ["OPENROUTER_API_KEY"])
        else:
            _client = OpenAI()
    return _client


def provider() -> str:
    return "anthropic" if os.environ.get("ATLAS_LLM_PROVIDER", "").lower() == "anthropic" else "openai"


_anthropic_client = None


def _anthropic():
    global _anthropic_client
    if _anthropic_client is None:
        import anthropic  # not in pyproject.toml: run with `uv run --with anthropic`
        # The SDK retries 429/5xx/connection errors itself with backoff.
        _anthropic_client = anthropic.Anthropic(max_retries=6)
    return _anthropic_client


def _anthropic_parse(system: str, user: str, schema: type[T], model: str, effort: str | None = None) -> T | None:
    """Claude structured output. Returns None on a refusal (or a truncated answer).

    `fallbacks: "default"` re-runs a safety-declined request server-side on Anthropic's
    recommended fallback model (routed by refusal category), so a false-positive decline on
    biomedical text doesn't cost us the paper."""
    resp = _anthropic().beta.messages.parse(
        model=model,
        max_tokens=16000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        system=system,
        messages=[{"role": "user", "content": user}],
        output_format=schema,
        **({"output_config": {"effort": effort}} if effort else {}),
    )
    if resp.stop_reason in ("refusal", "max_tokens"):
        return None
    return resp.parsed_output


def chat_model() -> str:
    if provider() == "anthropic":
        m = os.environ.get("ANTHROPIC_MODEL")
        if not m:
            raise RuntimeError("ANTHROPIC_MODEL is not set (ATLAS_LLM_PROVIDER=anthropic)")
        return m
    m = os.environ.get("OPENAI_MODEL")
    if not m:
        raise RuntimeError("OPENAI_MODEL is not set (see .env.example)")
    return m


def embed_model() -> str:
    m = os.environ.get("OPENAI_EMBED_MODEL")
    if not m:
        raise RuntimeError("OPENAI_EMBED_MODEL is not set (see .env.example)")
    return m


def parse(system: str, user: str, schema: type[T], *, model: str | None = None, effort: str | None = None) -> T | None:
    """Structured-output call. Returns None on a model refusal. `effort` ("low" for simple
    selection tasks) is passed to Claude; the OpenAI path ignores it."""
    model = model or chat_model()
    request = {"model": model, "system": system, "user": user, "schema": schema.model_json_schema()}
    if effort and provider() == "anthropic":
        request["effort"] = effort

    def call():
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        if provider() == "anthropic":
            parsed = _anthropic_parse(system, user, schema, model, effort)
        elif via_openrouter():
            # OpenRouter's Responses API is beta; Chat Completions structured outputs is the stable path.
            msg = _with_retry(lambda: _openai().chat.completions.parse(
                model=_wire_model(model), messages=messages, response_format=schema,
                max_completion_tokens=MAX_OUTPUT_TOKENS)).choices[0].message
            parsed = None if msg.refusal else msg.parsed
        else:
            parsed = _with_retry(lambda: _openai().responses.parse(
                model=model, input=messages, text_format=schema, max_output_tokens=MAX_OUTPUT_TOKENS)).output_parsed
        return None if parsed is None else parsed.model_dump(mode="json")

    value = cached("llm", request, call)
    return None if value is None else schema.model_validate(value)


def embed(texts: list[str], *, model: str | None = None) -> list[list[float]]:
    """Embeddings, cached per text so growing the index only embeds new strings."""
    model = model or embed_model()
    out: list[list[float] | None] = [None] * len(texts)
    missing: list[int] = []
    for i, t in enumerate(texts):
        out[i] = cache_lookup("embed", {"model": model, "text": t}, None)
        if out[i] is None:
            missing.append(i)
    if missing and is_offline():
        raise CacheMissOffline("embed", "", {"model": model, "missing": len(missing)})
    for start in range(0, len(missing), 256):
        idx = missing[start:start + 256]
        resp = _with_retry(lambda: _openai().embeddings.create(model=_wire_model(model), input=[texts[i] for i in idx]))
        for i, d in zip(idx, resp.data):
            out[i] = cached("embed", {"model": model, "text": texts[i]}, lambda v=d.embedding: list(v))
    return out  # type: ignore[return-value]
