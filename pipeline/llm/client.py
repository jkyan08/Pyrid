"""Cached OpenAI calls shared by lane B (extract, reconcile, explain, draft).

Model names come from OPENAI_MODEL / OPENAI_EMBED_MODEL (hard rule 9). Every call
is cached on disk keyed by a hash of the full request (hard rule 6), so re-runs
and the demo never depend on live calls.

Provider: OpenAI directly, or OpenAI models through OpenRouter when
OPENROUTER_API_KEY is set. Model names stay bare (e.g. `gpt-6-astra`); the
`openai/` prefix OpenRouter needs is added here, and cache keys use the bare
name, so cached results are shared across providers.
"""
from __future__ import annotations

import os
from typing import TypeVar

from pydantic import BaseModel

from pipeline.extract._compat import REPO_ROOT, cache_lookup, cached

T = TypeVar("T", bound=BaseModel)

_client = None


class CacheMiss(RuntimeError):
    """Raised in offline mode (ATLAS_OFFLINE=1) when a call is not already cached."""


OPENROUTER_BASE = "https://openrouter.ai/api/v1"


def _load_dotenv() -> None:
    """Fill unset variables from the repo's .env (the shell's own environment wins)."""
    path = REPO_ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()


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


def chat_model() -> str:
    m = os.environ.get("OPENAI_MODEL")
    if not m:
        raise RuntimeError("OPENAI_MODEL is not set (see .env.example)")
    return m


def embed_model() -> str:
    m = os.environ.get("OPENAI_EMBED_MODEL")
    if not m:
        raise RuntimeError("OPENAI_EMBED_MODEL is not set (see .env.example)")
    return m


def _offline() -> bool:
    return os.environ.get("ATLAS_OFFLINE") == "1"


def parse(system: str, user: str, schema: type[T], *, model: str | None = None) -> T | None:
    """Structured-output call. Returns None on a model refusal."""
    model = model or chat_model()
    request = {"model": model, "system": system, "user": user, "schema": schema.model_json_schema()}

    def call():
        if _offline():
            raise CacheMiss(f"uncached {schema.__name__} call in offline mode")
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        if via_openrouter():
            # OpenRouter's Responses API is beta; Chat Completions structured outputs is the stable path.
            msg = _openai().chat.completions.parse(
                model=_wire_model(model), messages=messages, response_format=schema).choices[0].message
            parsed = None if msg.refusal else msg.parsed
        else:
            parsed = _openai().responses.parse(model=model, input=messages, text_format=schema).output_parsed
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
    if missing and _offline():
        raise CacheMiss(f"{len(missing)} uncached embeddings in offline mode")
    for start in range(0, len(missing), 256):
        idx = missing[start:start + 256]
        resp = _openai().embeddings.create(model=_wire_model(model), input=[texts[i] for i in idx])
        for i, d in zip(idx, resp.data):
            out[i] = cached("embed", {"model": model, "text": texts[i]}, lambda v=d.embedding: list(v))
    return out  # type: ignore[return-value]
