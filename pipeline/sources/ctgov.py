"""ClinicalTrials.gov API v2 -> ``Study`` nodes and ``studies`` / ``evaluates`` / ``mentions`` edges.

Endpoints: ``GET https://clinicaltrials.gov/api/v2/version`` (data timestamp) and
``GET /api/v2/studies`` (search with ``fields``), exactly as verified in docs/source-apis.md
section 2 and docs/recon/ctgov.md on 2026-10-03. Fixtures: tests/fixtures/sources/ctgov/.
Single-study fetches (``GET /studies/{nctId}``) are not needed: every search page already returns
the requested fields in the nested record shape.

What the loader writes (pipeline/store.py conventions)
-------------------------------------------------------
* One ``Study`` node per distinct NCT id across all seed queries: id ``nct:<NCTId>``, label =
  ``briefTitle`` (dose-scrubbed; the NCT id when a record carries no title), synonyms =
  [``officialTitle`` scrubbed, NCT id]. ``props`` (see :func:`study_node`) keep the raw fields the
  confidence rubric reads (``overall_status``, ``phases``, ``study_type``, ``enrollment``,
  ``has_results``, dates) verbatim: confidence is computed by ``pipeline.graph.build``, never here.
  ``props.record_id`` is the NCT id, ``props.source_url`` the public study page,
  ``props.data_timestamp`` / ``props.version_holder`` the data-processed dates the Terms and
  Conditions ask us to display, ``props.found_by_queries`` the seed labels that returned it.
* Edges (``source_db`` = ``"ClinicalTrials.gov"``, ``props.evidence_class`` set on every row):
  - Study -> Disease ``studies`` when a ``conditions[]`` string or a condition MeSH term equals
    (case-insensitive, whitespace-collapsed) a Disease node's label or synonym, or a condition MeSH
    id equals a Disease ``props.xrefs`` entry (``MESH:<id>``); ``props.match = {method, matched_text}``.
  - Study -> Drug ``evaluates`` when a scrubbed intervention name, other name or intervention MeSH
    term equals a Drug node's label or synonym.
  - Study -> Gene ``mentions`` (``evidence_class: inferred``, ``match.method: query_term``) when the
    study was found through a gene-symbol ``query.term`` and neither link above was made.
  Unmatched conditions / interventions / gene symbols stay in ``props.unlinked_*``; nothing is
  guessed. The backbone loader may not have run yet (scratch stores): the Study nodes are still
  written and linking is simply redone on the real build.
* Coverage (README hard rule 3). Source ``"ClinicalTrials.gov studies"``: one row per seed query
  under ``seed:ctgov_query:<label>`` (hits = page-1 ``totalCount``, zero included; query = the
  parameter string sent, without ``fields``) plus the same row under every store node the query is
  about (the Disease named in ``cond``, the Drug named in ``intr``, the Gene symbols in ``term``)
  when such a node exists. Source ``"ClinicalTrials.gov"``: one row per study for the linking step
  (hits = number of links made, zero included).
* ``sources`` row ``"ClinicalTrials.gov"``: version = ``/version.dataTimestamp``, the Terms and
  Conditions summary as ``license``, ``notes`` with the API version, every query's parameter string
  and count, the stated modifications, and ``ctgov_errors`` for any failed query (errors are plain
  text 400s; the failed query is recorded on the sources row, the remaining queries still run, and
  :class:`CtgovError` is raised at the end).

Hard rules honoured
-------------------
* No dosing information reaches the store: :func:`scrub_dose` replaces dose quantities and
  schedules in every stored free-text field (titles, intervention names and other names,
  eligibility criteria, conditions, keywords, sponsor and official names) with ``"[dose removed]"``.
* Contact data is never read: ``centralContacts`` and ``locations`` are not requested and the
  parser does not look at them (T&C: extracted email addresses must not be used for marketing).
* Every request goes through :class:`pipeline.cache.CachedHttp` with ``config.USER_AGENT``; the
  host is throttled at the NCBI courtesy rate (no limit is documented); a request is never sent
  twice; offline runs are served from the cache or raise :class:`pipeline.cache.CacheMissOffline`.
* Seeds (pipeline/seeds/ctgov.yaml) hold Essie expressions and settings only, never ids. Bare gene
  symbols in ``term`` are wrapped as ``EXPANSION[None]<SYMBOL>`` (cheat sheet gotcha 1); quoted
  phrases are sent exactly as written. ``retrieved_at`` comes from the cache entry, so a cached
  run reproduces the same dates.

CLI: ``uv run python -m pipeline.sources.ctgov --store data/build/scratch-ctgov.sqlite [--offline]``.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx

from pipeline import config
from pipeline.cache import CachedHttp, CachedResponse, DiskCache, default_http
from pipeline.ids import normalize_curie
from pipeline.models import NodeType
from pipeline.store import Store

__all__ = [
    "BASE_URL",
    "COVERAGE_SOURCE",
    "DOSE_PLACEHOLDER",
    "LICENSE_TEXT",
    "REL_EVALUATES",
    "REL_MENTIONS",
    "REL_STUDIES",
    "SOURCE_NAME",
    "CtgovError",
    "CtgovHttpError",
    "SearchResult",
    "SeedQuery",
    "Settings",
    "StoreIndex",
    "build_page_params",
    "fetch_version",
    "link_study",
    "load",
    "main",
    "parse_seeds",
    "parse_study",
    "params_string",
    "scrub_dose",
    "search",
    "study_id",
    "study_node",
    "wrap_gene_symbols",
]

SOURCE_NAME = "ClinicalTrials.gov"
COVERAGE_SOURCE = "ClinicalTrials.gov studies"
SEED_NAME = "ctgov"
BASE_URL = "https://clinicaltrials.gov/api/v2"
HOST = "clinicaltrials.gov"
# No rate limit is documented (cheat sheet 2d); the NCBI courtesy rate is reused for the host.
COURTESY_RATE = 3.0
STUDY_URL = "https://clinicaltrials.gov/study/{nct_id}"
TERMS_URL = "https://clinicaltrials.gov/about-site/terms-conditions"
API_DOCS_URL = "https://clinicaltrials.gov/data-api/api"

REL_STUDIES = "studies"
REL_EVALUATES = "evaluates"
REL_MENTIONS = "mentions"

LINK_QUERY = "link conditions and interventions to Disease and Drug nodes"
COVERAGE_SEED_PREFIX = "seed:ctgov_query:"
DOSE_PLACEHOLDER = "[dose removed]"
ELIGIBILITY_MAX_CHARS = 4000
DEFAULT_PAGE_SIZE = 50
DEFAULT_MAX_STUDIES = 50
API_PAGE_SIZE_MAX = 1000

LICENSE_TEXT = (
    "ClinicalTrials.gov Terms and Conditions (https://clinicaltrials.gov/about-site/terms-conditions, "
    "last updated 2023-01-31): a US Government database available to all requesters at no charge, "
    "without warranties. Any publication or distribution must attribute the source as "
    "ClinicalTrials.gov, keep the data current, clearly display the date the data were processed by "
    "ClinicalTrials.gov (dataTimestamp / versionHolder), and state any modifications made. No "
    "proprietary rights may be asserted over the database. Email addresses extracted from the "
    "database must not be used for marketing or other promotional purposes. Data may carry an "
    "international copyright outside the US."
)
MODIFICATIONS_TEXT = (
    "Modifications (stated per the Terms and Conditions): dose quantities and dosing schedules in "
    "stored free text (titles, intervention names, eligibility criteria, conditions, keywords) are "
    "replaced by '[dose removed]'; eligibility criteria are truncated to 4000 characters; contact "
    "fields (central contacts, location contacts, phone, email) are neither requested nor stored."
)

# The `fields` set the cheat sheet verified in one call, plus ReferencePMID (docs/source-apis.md 2b).
DEFAULT_FIELDS: tuple[str, ...] = (
    "NCTId", "BriefTitle", "OfficialTitle", "OverallStatus", "StartDate", "StartDateType",
    "PrimaryCompletionDate", "LastUpdatePostDate", "StudyFirstPostDate", "LeadSponsor",
    "CollaboratorName", "ResponsiblePartyType", "Condition", "Keyword", "StudyType", "Phase",
    "EnrollmentInfo", "DesignAllocation", "DesignPrimaryPurpose", "Intervention", "EligibilityModule",
    "OverallOfficialName", "ReferencePMID", "ReferenceType", "HasResults",
    "derivedSection.conditionBrowseModule", "derivedSection.interventionBrowseModule",
    "derivedSection.miscInfoModule",
)

_NCT_RE = re.compile(r"^NCT\d{8}$")
_PMID_RE = re.compile(r"^\d+$")
# A bare HGNC-style symbol: upper-case letter first, then upper-case letters / digits (STAT1, IL2RG, JAK3).
_GENE_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9]{1,9}(?:-[A-Z0-9]{1,4})?$")
_ESSIE_OPERATORS = frozenset({"AND", "OR", "NOT"})
_ESSIE_CONTEXT_PREFIXES = ("EXPANSION[", "AREA[", "COVERAGE[", "SEARCH[", "RANGE[", "MISSING", "TILT[")
_TERM_TOKEN_RE = re.compile(r'"[^"]*"|\S+')


# --------------------------------------------------------------------------------------
# dose scrubbing (README hard rule 7)
# --------------------------------------------------------------------------------------

_NUM = r"\d+(?:[.,]\d+)?"
_NUMS = rf"{_NUM}(?:\s*(?:-|–|—|to|or|and|,|x|×|/)\s*{_NUM})*"
_UNIT = (
    r"(?:mg|mcg|µg|μg|ug|ng|pg|kg|g|mL|ml|dL|dl|L|IU|international\s+units?|units?|%|percent|"
    r"mEq|meq|mmol|µmol|μmol|nmol|mol|cc|tablets?|tabs?|capsules?|caps?|pills?|drops?|puffs?|"
    r"sprays?|patch(?:es)?|vials?|ampoules?|sachets?|doses?|actuations?|inhalations?)"
)
# Longest alternatives first: "/dL" must not be read as "/d" + "L".
_PER = r"(?:\s*/\s*(?:dL|dl|mL|ml|m2|m²|m\^2|kg|dose|day|week|wk|hour|hr|min|d|h|L|m))*"
_FREQ_WORDS = r"(?:daily|day|weekly|week|monthly|month|nightly|night|hourly|hour)"
_TIMES = r"(?:once|twice|thrice|one\s+time|two\s+times|three\s+times|four\s+times|\d+\s*times|\d+\s*x)"
_EVERY = r"(?:every|each)\s+(?:other\s+)?(?:\d+\s*)?(?:hours?|hrs?|days?|weeks?|wks?|months?)(?![A-Za-z])"
_Q_INTERVAL = r"q\s?\d+\s?(?:hours?|hrs?|h|days?|d|weeks?|wks?|w)(?![A-Za-z])"
_LATIN = r"(?:q\.?d\.?|b\.?i\.?d\.?|t\.?i\.?d\.?|q\.?i\.?d\.?|q\.?h\.?s\.?|q\.?a\.?m\.?|q\.?p\.?m\.?|q\.?o\.?d\.?|q\.?w\.?|prn)(?![A-Za-z])"
_FREQ_TAIL = (
    rf"(?:{_TIMES}(?:\s+(?:a|per|each|every))?[\s-]*{_FREQ_WORDS}"
    rf"|{_EVERY}|{_Q_INTERVAL}|\b{_LATIN}"
    rf"|\b(?:daily|weekly|monthly|nightly|hourly|per\s+day|a\s+day|per\s+week|at\s+bedtime)(?![A-Za-z]))"
)
_DOSE = rf"(?<![A-Za-z0-9.]){_NUMS}\s*{_UNIT}(?![A-Za-z]){_PER}(?:\s*(?:,|and|or|to|-)?\s*{_FREQ_TAIL})?"
# Schedules without a quantity: "once daily", "every 4 weeks", "BID", "per day".
_FREQ_ALONE = (
    rf"(?<![A-Za-z])(?:{_TIMES}(?:\s+(?:a|per|each|every))?[\s-]*{_FREQ_WORDS}(?![A-Za-z])"
    rf"|{_EVERY}|\b{_Q_INTERVAL}|\b{_LATIN}|\bdaily\s+dose\b|\bper\s+day\b)"
)
DOSE_RE = re.compile(rf"{_DOSE}|{_FREQ_ALONE}", re.IGNORECASE)
_PLACEHOLDER = re.escape(DOSE_PLACEHOLDER)
_COLLAPSE_RE = re.compile(
    rf"{_PLACEHOLDER}(?:(?:\s*(?:,|;|and|or|to|-|–|/|per|x|×|\+|then)\s*|\s+)*{_PLACEHOLDER})+",
    re.IGNORECASE,
)


def scrub_dose(text: str | None) -> str | None:
    """Replace dose quantities and schedules with ``"[dose removed]"``.

    Removes numbers followed by a dose unit (mg, mcg, µg, g, kg, mL, IU, units, %, tablets, ...)
    together with per-weight / per-time suffixes (mg/kg, mg/m2, mg/day) and any schedule that
    follows ("once daily", "BID", "every 4 weeks"), and standalone schedules. Ranges and lists
    ("15, 30 or 45 mg", "15-30 mg") collapse to one placeholder. Drug names are kept, so
    ``"Upadacitinib 15 mg once daily"`` becomes ``"Upadacitinib [dose removed]"``. Over-scrubbing
    a lab threshold such as ``"9.0 g/dL"`` is accepted; under-scrubbing is not. ``None`` passes
    through. Idempotent.
    """
    if text is None:
        return None
    if not isinstance(text, str):
        text = str(text)
    if not text:
        return text
    out = DOSE_RE.sub(DOSE_PLACEHOLDER, text)
    return _COLLAPSE_RE.sub(DOSE_PLACEHOLDER, out)


def _scrub_list(values: Any) -> list[str]:
    if not isinstance(values, (list, tuple)):
        return []
    out: list[str] = []
    for v in values:
        if isinstance(v, str) and v.strip():
            out.append(scrub_dose(v) or "")
    return out


def _norm_name(value: str) -> str:
    """Case-insensitive, whitespace-collapsed key for exact name matching."""
    return " ".join(value.split()).casefold()


# --------------------------------------------------------------------------------------
# errors
# --------------------------------------------------------------------------------------


class CtgovError(RuntimeError):
    """One or more seed queries failed; the failures are recorded on the sources row
    (``notes.ctgov_errors``) and in :attr:`errors`. ``summary`` is what :func:`load` would have
    returned."""

    def __init__(self, errors: Sequence[Mapping[str, Any]], summary: Mapping[str, Any] | None = None) -> None:
        self.errors = [dict(e) for e in errors]
        self.summary = dict(summary or {})
        labels = ", ".join(str(e.get("label")) for e in self.errors)
        super().__init__(f"{SOURCE_NAME}: {len(self.errors)} query/queries failed: {labels}")


class CtgovHttpError(RuntimeError):
    """A non-2xx response (plain-text body) for one request."""

    def __init__(self, label: str, params: Sequence[tuple[str, str]], response: CachedResponse) -> None:
        self.label = label
        self.params = [list(p) for p in params]
        self.response = response
        super().__init__(f"HTTP {response.status} for query {label!r}: {response.text[:200]}")

    def to_record(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "url": self.response.url,
            "params": params_string(self.params, include_fields=True),
            "status": self.response.status,
            "error": self.response.text[:500],
            "date": self.response.retrieved_date,
        }


# --------------------------------------------------------------------------------------
# seeds
# --------------------------------------------------------------------------------------


def wrap_gene_symbols(term: str) -> str:
    """Wrap every bare gene symbol token of an Essie ``term`` as ``EXPANSION[None]<SYMBOL>``.

    Quoted phrases, Essie operators (AND / OR / NOT), tokens that already carry a context operator
    and lower-case words are left exactly as written (cheat sheet gotcha 1: ``EXPANSION[None]`` is
    case-sensitive and must never be applied to a quoted disease phrase).
    """
    out: list[str] = []
    for token in _TERM_TOKEN_RE.findall(term):
        if (
            token.startswith('"')
            or token in _ESSIE_OPERATORS
            or token.startswith(_ESSIE_CONTEXT_PREFIXES)
            or not _GENE_SYMBOL_RE.match(token)
        ):
            out.append(token)
        else:
            out.append(f"EXPANSION[None]{token}")
    return " ".join(out)


def _gene_symbols_in(term: str) -> list[str]:
    return [
        t
        for t in _TERM_TOKEN_RE.findall(term)
        if not t.startswith('"') and t not in _ESSIE_OPERATORS and not t.startswith(_ESSIE_CONTEXT_PREFIXES) and _GENE_SYMBOL_RE.match(t)
    ]


@dataclass(frozen=True)
class SeedQuery:
    """One row of ``seeds.queries``: a label plus any of ``cond`` / ``intr`` / ``term``."""

    label: str
    cond: str | None = None
    intr: str | None = None
    term: str | None = None
    gene_symbols_in_term: bool = False
    max_studies: int | None = None

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any]) -> SeedQuery:
        if not isinstance(row, Mapping):
            raise ValueError(f"ctgov seed query must be a mapping, got {type(row).__name__}")
        unknown = set(row) - {"label", "cond", "intr", "term", "gene_symbols_in_term", "max_studies"}
        if unknown:
            raise ValueError(f"ctgov seed query {row.get('label')!r}: unknown keys {sorted(unknown)}")
        label = row.get("label")
        if not isinstance(label, str) or not label.strip():
            raise ValueError(f"ctgov seed query needs a non-empty label: {row!r}")

        def text(key: str) -> str | None:
            v = row.get(key)
            if v is None:
                return None
            if not isinstance(v, str) or not v.strip():
                raise ValueError(f"ctgov seed query {label!r}: {key!r} must be a non-empty string")
            return v.strip()

        q = cls(
            label=label.strip(),
            cond=text("cond"),
            intr=text("intr"),
            term=text("term"),
            gene_symbols_in_term=bool(row.get("gene_symbols_in_term", False)),
            max_studies=None if row.get("max_studies") is None else int(row["max_studies"]),
        )
        if not (q.cond or q.intr or q.term):
            raise ValueError(f"ctgov seed query {label!r} needs at least one of cond / intr / term")
        if q.max_studies is not None and q.max_studies < 1:
            raise ValueError(f"ctgov seed query {label!r}: max_studies must be >= 1")
        return q

    @property
    def sent_term(self) -> str | None:
        if self.term is None:
            return None
        return wrap_gene_symbols(self.term) if self.gene_symbols_in_term else self.term

    @property
    def gene_symbols(self) -> list[str]:
        """Bare gene symbols this query searches for (only when ``gene_symbols_in_term``)."""
        if not self.gene_symbols_in_term or self.term is None:
            return []
        return _gene_symbols_in(self.term)

    @property
    def cond_phrase(self) -> str | None:
        """``cond`` without surrounding Essie quotes, for resolving the Disease node it is about."""
        return _unquote(self.cond)

    @property
    def intr_phrase(self) -> str | None:
        return _unquote(self.intr)

    def essie_params(self) -> list[tuple[str, str]]:
        items: list[tuple[str, str]] = []
        if self.cond:
            items.append(("query.cond", self.cond))
        if self.intr:
            items.append(("query.intr", self.intr))
        if self.sent_term:
            items.append(("query.term", self.sent_term))
        return items


def _unquote(value: str | None) -> str | None:
    if value is None:
        return None
    v = value.strip()
    if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
        v = v[1:-1]
    return v.strip() or None


@dataclass(frozen=True)
class Settings:
    queries: tuple[SeedQuery, ...]
    page_size: int = DEFAULT_PAGE_SIZE
    max_studies_per_query: int = DEFAULT_MAX_STUDIES
    fields: tuple[str, ...] = DEFAULT_FIELDS

    def cap_for(self, query: SeedQuery) -> int:
        return query.max_studies if query.max_studies is not None else self.max_studies_per_query


def parse_seeds(seeds: Mapping[str, Any]) -> Settings:
    """Validate ``pipeline/seeds/ctgov.yaml`` (as loaded by :func:`pipeline.config.load_seeds`)."""
    if not isinstance(seeds, Mapping):
        raise ValueError("ctgov seeds must be a mapping")
    rows = seeds.get("queries")
    if not isinstance(rows, list) or not rows:
        raise ValueError("ctgov seeds need a non-empty 'queries' list")
    queries = tuple(SeedQuery.from_mapping(r) for r in rows)
    labels = [q.label for q in queries]
    if len(set(labels)) != len(labels):
        dupes = sorted({l for l in labels if labels.count(l) > 1})
        raise ValueError(f"ctgov seeds: duplicate query labels {dupes}")
    page_size = int(seeds.get("page_size", DEFAULT_PAGE_SIZE))
    max_studies = int(seeds.get("max_studies_per_query", DEFAULT_MAX_STUDIES))
    if not 1 <= page_size <= API_PAGE_SIZE_MAX:
        raise ValueError(f"ctgov seeds: page_size must be 1..{API_PAGE_SIZE_MAX}, got {page_size}")
    if max_studies < 1:
        raise ValueError("ctgov seeds: max_studies_per_query must be >= 1")
    fields_raw = seeds.get("fields", list(DEFAULT_FIELDS))
    if not isinstance(fields_raw, list) or not all(isinstance(f, str) and f.strip() for f in fields_raw) or not fields_raw:
        raise ValueError("ctgov seeds: 'fields' must be a non-empty list of piece names")
    fields = tuple(f.strip() for f in fields_raw)
    if "NCTId" not in fields:
        raise ValueError("ctgov seeds: 'fields' must include NCTId")
    return Settings(queries=queries, page_size=page_size, max_studies_per_query=max_studies, fields=fields)


# --------------------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------------------


def _headers() -> dict[str, str]:
    return {"User-Agent": config.USER_AGENT}


def build_page_params(
    query: SeedQuery, *, page_size: int, fields: Sequence[str], page_token: str | None = None
) -> list[tuple[str, str]]:
    """Parameters for one ``GET /studies`` page. Page 1 carries ``countTotal=true``; later pages
    repeat every parameter except ``countTotal`` and add ``pageToken`` (spec rule)."""
    items = query.essie_params()
    if page_token is None:
        items.append(("countTotal", "true"))
    items.append(("pageSize", str(page_size)))
    items.append(("fields", ",".join(fields)))
    if page_token is not None:
        items.append(("pageToken", page_token))
    return items


def params_string(params: Sequence[Sequence[str]], *, include_fields: bool = False) -> str:
    """The URL-encoded parameter string as sent (``curl -G --data-urlencode`` equivalent).
    ``fields`` is dropped by default because it does not affect hits and is long."""
    items = [(k, v) for k, v in params if include_fields or k != "fields"]
    return str(httpx.QueryParams(items))


def fetch_version(http: Any) -> tuple[dict[str, Any], CachedResponse]:
    """``GET /version`` -> (``{"apiVersion", "dataTimestamp"}``, response). Raises
    :class:`CtgovHttpError` on a non-2xx status."""
    response: CachedResponse = http.get_json(f"{BASE_URL}/version", headers=_headers())
    if not response.ok:
        raise CtgovHttpError("version", [], response)
    data = response.json()
    if not isinstance(data, Mapping):
        raise CtgovHttpError("version", [], response)
    return dict(data), response


@dataclass
class SearchResult:
    query: SeedQuery
    params: list[tuple[str, str]]
    total_count: int
    total_count_from: str  # "totalCount" or "fetched" when the API omitted it
    studies: list[dict[str, Any]]
    pages: int
    retrieved_at: str
    retrieved_date: str
    truncated: bool
    page_retrieved_at: list[str] = field(default_factory=list)

    @property
    def params_string(self) -> str:
        return params_string(self.params)


def search(http: Any, query: SeedQuery, *, page_size: int, max_studies: int, fields: Sequence[str]) -> SearchResult:
    """Run one seed query, following ``nextPageToken`` until the last page or ``max_studies``.

    ``totalCount`` is read from page 1 only (it is absent afterwards); the returned studies are cut
    at ``max_studies``. A non-2xx page raises :class:`CtgovHttpError`.
    """
    page_size = max(1, min(int(page_size), API_PAGE_SIZE_MAX, int(max_studies)))
    url = f"{BASE_URL}/studies"
    studies: list[dict[str, Any]] = []
    page_token: str | None = None
    seen_tokens: set[str] = set()
    pages = 0
    max_pages = math.ceil(max_studies / page_size) + 1
    first_params: list[tuple[str, str]] = []
    total_count: int | None = None
    retrieved_at = ""
    page_retrieved_at: list[str] = []
    truncated = False
    while True:
        params = build_page_params(query, page_size=page_size, fields=fields, page_token=page_token)
        if page_token is None:
            first_params = params
        response: CachedResponse = http.get_json(url, params=params, headers=_headers())
        if not response.ok:
            raise CtgovHttpError(query.label, params, response)
        try:
            data = response.json()
        except ValueError as exc:
            raise CtgovHttpError(query.label, params, response) from exc
        if not isinstance(data, Mapping):
            raise CtgovHttpError(query.label, params, response)
        pages += 1
        page_retrieved_at.append(response.retrieved_at)
        if page_token is None:
            retrieved_at = response.retrieved_at
            raw_total = data.get("totalCount")
            total_count = int(raw_total) if isinstance(raw_total, int) and not isinstance(raw_total, bool) else None
        page_studies = data.get("studies") or []
        for rec in page_studies:
            if isinstance(rec, Mapping):
                studies.append(dict(rec))
        next_token = data.get("nextPageToken")
        if len(studies) >= max_studies:
            truncated = bool(next_token) or len(studies) > max_studies
            studies = studies[:max_studies]
            break
        if not next_token or not isinstance(next_token, str) or next_token in seen_tokens or pages >= max_pages:
            break
        seen_tokens.add(next_token)
        page_token = next_token
    if total_count is None:
        total_count, total_from = len(studies), "fetched"
    else:
        total_from = "totalCount"
    return SearchResult(
        query=query,
        params=first_params,
        total_count=total_count,
        total_count_from=total_from,
        studies=studies,
        pages=pages,
        retrieved_at=retrieved_at,
        retrieved_date=retrieved_at[:10],
        truncated=truncated,
        page_retrieved_at=page_retrieved_at,
    )


# --------------------------------------------------------------------------------------
# records
# --------------------------------------------------------------------------------------


def study_id(nct_id: str) -> str:
    """``nct:NCT00000000`` (contract id format for Study nodes); validates the NCT shape."""
    if not isinstance(nct_id, str):
        raise ValueError(f"NCT id must be a string, got {type(nct_id).__name__}")
    value = nct_id.strip().upper()
    if not _NCT_RE.match(value):
        raise ValueError(f"not an NCT id: {nct_id!r}")
    return normalize_curie(f"nct:{value}")


def _get(mapping: Any, *path: str) -> Any:
    cur = mapping
    for key in path:
        if not isinstance(cur, Mapping):
            return None
        cur = cur.get(key)
    return cur


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _str_list(values: Any) -> list[str]:
    return [v.strip() for v in values if isinstance(v, str) and v.strip()] if isinstance(values, (list, tuple)) else []


def parse_study(record: Mapping[str, Any]) -> dict[str, Any]:
    """Flatten one study record (search hit or ``/studies/{nctId}`` body) into the fields the
    store keeps. Every field is optional except ``nctId`` (expanded-access records lack phases,
    enrollment, start date, sex and ages). Free text is dose-scrubbed here; contact modules are
    never read."""
    ps = record.get("protocolSection") or {}
    ident = ps.get("identificationModule") or {}
    nct_raw = _text(ident.get("nctId"))
    if nct_raw is None:
        raise ValueError("study record has no protocolSection.identificationModule.nctId")
    nct_id = nct_raw.upper()
    status = ps.get("statusModule") or {}
    sponsor = ps.get("sponsorCollaboratorsModule") or {}
    conditions = ps.get("conditionsModule") or {}
    design = ps.get("designModule") or {}
    arms = ps.get("armsInterventionsModule") or {}
    elig = ps.get("eligibilityModule") or {}
    officials = _get(ps, "contactsLocationsModule", "overallOfficials") or []
    refs = _get(ps, "referencesModule", "references") or []
    derived = record.get("derivedSection") or {}

    interventions: list[dict[str, Any]] = []
    for item in arms.get("interventions") or []:
        if not isinstance(item, Mapping):
            continue
        name = scrub_dose(_text(item.get("name")))
        if name is None:
            continue
        interventions.append({"name": name, "type": _text(item.get("type")), "other_names": _scrub_list(item.get("otherNames"))})

    enrollment = _get(design, "enrollmentInfo", "count")
    if isinstance(enrollment, bool) or not isinstance(enrollment, int):
        enrollment = None

    criteria = scrub_dose(_text(elig.get("eligibilityCriteria")))
    truncated = criteria is not None and len(criteria) > ELIGIBILITY_MAX_CHARS
    if truncated:
        criteria = criteria[:ELIGIBILITY_MAX_CHARS]

    references: list[dict[str, Any]] = []
    reference_pmids: list[str] = []
    for ref in refs:
        if not isinstance(ref, Mapping):
            continue
        pmid = _text(ref.get("pmid"))
        rtype = _text(ref.get("type"))
        references.append({"pmid": pmid, "type": rtype})
        if pmid and _PMID_RE.match(pmid) and pmid not in reference_pmids:
            reference_pmids.append(pmid)

    meshes = _get(derived, "conditionBrowseModule", "meshes") or []
    intr_meshes = _get(derived, "interventionBrowseModule", "meshes") or []
    has_results = record.get("hasResults")

    return {
        "nct_id": nct_id,
        "nct_id_aliases": _str_list(ident.get("nctIdAliases")),
        "brief_title": scrub_dose(_text(ident.get("briefTitle"))),
        "official_title": scrub_dose(_text(ident.get("officialTitle"))),
        "overall_status": _text(status.get("overallStatus")),
        "start_date": _text(_get(status, "startDateStruct", "date")),
        "start_date_type": _text(_get(status, "startDateStruct", "type")),
        "primary_completion_date": _text(_get(status, "primaryCompletionDateStruct", "date")),
        "study_first_post_date": _text(_get(status, "studyFirstPostDateStruct", "date")),
        "last_update": _text(_get(status, "lastUpdatePostDateStruct", "date")),
        "sponsor": scrub_dose(_text(_get(sponsor, "leadSponsor", "name"))),
        "sponsor_class": _text(_get(sponsor, "leadSponsor", "class")),
        "collaborators": [scrub_dose(n) for n in (_text(c.get("name")) for c in sponsor.get("collaborators") or [] if isinstance(c, Mapping)) if n],
        "responsible_party_type": _text(_get(sponsor, "responsibleParty", "type")),
        "conditions": _scrub_list(conditions.get("conditions")),
        "keywords": _scrub_list(conditions.get("keywords")),
        "study_type": _text(design.get("studyType")),
        "phases": _str_list(design.get("phases")),
        "enrollment": enrollment,
        "enrollment_type": _text(_get(design, "enrollmentInfo", "type")),
        "design_allocation": _text(_get(design, "designInfo", "allocation")),
        "primary_purpose": _text(_get(design, "designInfo", "primaryPurpose")),
        "interventions": interventions,
        "eligibility": {
            "criteria": criteria,
            "truncated": truncated,
            "min_age": _text(elig.get("minimumAge")),
            "max_age": _text(elig.get("maximumAge")),
            "sex": _text(elig.get("sex")),
            "healthy_volunteers": elig.get("healthyVolunteers") if isinstance(elig.get("healthyVolunteers"), bool) else None,
            "std_ages": _str_list(elig.get("stdAges")),
        },
        "overall_officials": [scrub_dose(n) for n in (_text(o.get("name")) for o in officials if isinstance(o, Mapping)) if n],
        "references": references,
        "reference_pmids": reference_pmids,
        "condition_mesh_terms": [t for t in (_text(m.get("term")) for m in meshes if isinstance(m, Mapping)) if t],
        "condition_mesh_ids": [t for t in (_text(m.get("id")) for m in meshes if isinstance(m, Mapping)) if t],
        "intervention_mesh_terms": [t for t in (_text(m.get("term")) for m in intr_meshes if isinstance(m, Mapping)) if t],
        "version_holder": _text(_get(derived, "miscInfoModule", "versionHolder")),
        "has_results": has_results if isinstance(has_results, bool) else None,
    }


def study_node(parsed: Mapping[str, Any], *, retrieved_at: str, data_timestamp: str | None, found_by_queries: Sequence[str]) -> dict[str, Any]:
    """The store node row for a parsed study (see the module docstring for the props)."""
    nct_id = parsed["nct_id"]
    node_id = study_id(nct_id)
    label = parsed.get("brief_title") or nct_id
    synonyms: list[str] = []
    for syn in (parsed.get("official_title"), nct_id):
        if syn and syn != label and syn not in synonyms:
            synonyms.append(syn)
    props: dict[str, Any] = {
        "record_id": nct_id,
        "source_url": STUDY_URL.format(nct_id=nct_id),
        "backbone_id": nct_id,
        "source_type": "Study",
        "overall_status": parsed.get("overall_status"),
        "phases": list(parsed.get("phases") or []),
        "study_type": parsed.get("study_type"),
        "enrollment": parsed.get("enrollment"),
        "enrollment_type": parsed.get("enrollment_type"),
        "start_date": parsed.get("start_date"),
        "start_date_type": parsed.get("start_date_type"),
        "primary_completion_date": parsed.get("primary_completion_date"),
        "study_first_post_date": parsed.get("study_first_post_date"),
        "last_update": parsed.get("last_update"),
        "design_allocation": parsed.get("design_allocation"),
        "primary_purpose": parsed.get("primary_purpose"),
        "conditions": list(parsed.get("conditions") or []),
        "keywords": list(parsed.get("keywords") or []),
        "interventions": [dict(i) for i in parsed.get("interventions") or []],
        "eligibility": dict(parsed.get("eligibility") or {}),
        "sponsor": parsed.get("sponsor"),
        "sponsor_class": parsed.get("sponsor_class"),
        "collaborators": list(parsed.get("collaborators") or []),
        "responsible_party_type": parsed.get("responsible_party_type"),
        "overall_officials": list(parsed.get("overall_officials") or []),
        "has_results": parsed.get("has_results"),
        "reference_pmids": list(parsed.get("reference_pmids") or []),
        "references": [dict(r) for r in parsed.get("references") or []],
        "condition_mesh_terms": list(parsed.get("condition_mesh_terms") or []),
        "condition_mesh_ids": list(parsed.get("condition_mesh_ids") or []),
        "intervention_mesh_terms": list(parsed.get("intervention_mesh_terms") or []),
        "nct_id_aliases": list(parsed.get("nct_id_aliases") or []),
        "data_timestamp": data_timestamp,
        "version_holder": parsed.get("version_holder"),
        "found_by_queries": list(found_by_queries),
    }
    return {
        "id": node_id,
        "type": NodeType.Study.value,
        "label": label,
        "synonyms": synonyms,
        "props": props,
        "source": SOURCE_NAME,
        "retrieved_at": retrieved_at,
    }


# --------------------------------------------------------------------------------------
# linking to existing nodes
# --------------------------------------------------------------------------------------


def _xref_key(value: Any) -> str | None:
    """``PREFIX:local`` upper-cased prefix for an xref given as ``"OMIM:1"``, ``"MESH_D1"`` or a
    ``{"id"|"val"|"db_id"...}`` mapping; None when it cannot be read."""
    raw: Any = value
    if isinstance(value, Mapping):
        src = value.get("source") or value.get("db") or value.get("db_source") or value.get("prefix")
        local = value.get("id") or value.get("val") or value.get("db_id") or value.get("local")
        if isinstance(local, str) and ":" not in local and isinstance(src, str):
            raw = f"{src}:{local}"
        else:
            raw = local
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        curie = normalize_curie(raw)
    except ValueError:
        return None
    prefix, _, local = curie.partition(":")
    if not local:
        return None
    return f"{prefix.upper()}:{local.strip()}"


class StoreIndex:
    """Name / symbol / xref lookups over the Disease, Drug and Gene nodes already in a store.
    First node (by id order) wins when two nodes share a name."""

    def __init__(self, store: Store | None = None) -> None:
        self.disease_by_name: dict[str, str] = {}
        self.disease_by_xref: dict[str, str] = {}
        self.drug_by_name: dict[str, str] = {}
        self.gene_by_symbol: dict[str, str] = {}
        if store is not None:
            for node in store.iter_nodes(NodeType.Disease):
                self._add_names(self.disease_by_name, node)
                for xref in node.get("props", {}).get("xrefs") or []:
                    key = _xref_key(xref)
                    if key is not None:
                        self.disease_by_xref.setdefault(key, node["id"])
            for node in store.iter_nodes(NodeType.Drug):
                self._add_names(self.drug_by_name, node)
            for node in store.iter_nodes(NodeType.Gene):
                symbol = node.get("props", {}).get("symbol") or node.get("label")
                if isinstance(symbol, str) and symbol.strip():
                    self.gene_by_symbol.setdefault(symbol.strip().upper(), node["id"])

    @staticmethod
    def _add_names(index: dict[str, str], node: Mapping[str, Any]) -> None:
        names = [node.get("label"), *(node.get("synonyms") or [])]
        for name in names:
            if isinstance(name, str) and name.strip():
                index.setdefault(_norm_name(name), node["id"])

    def disease(self, name: str | None) -> str | None:
        return self.disease_by_name.get(_norm_name(name)) if name else None

    def disease_by_mesh(self, mesh_id: str | None) -> str | None:
        return self.disease_by_xref.get(f"MESH:{mesh_id.strip()}") if mesh_id else None

    def drug(self, name: str | None) -> str | None:
        return self.drug_by_name.get(_norm_name(name)) if name else None

    def gene(self, symbol: str | None) -> str | None:
        return self.gene_by_symbol.get(symbol.strip().upper()) if symbol else None


def _rubric_fields(parsed: Mapping[str, Any]) -> dict[str, Any]:
    """Raw study fields the confidence rubric reads, copied onto every edge."""
    return {
        "overall_status": parsed.get("overall_status"),
        "phases": list(parsed.get("phases") or []),
        "study_type": parsed.get("study_type"),
        "enrollment": parsed.get("enrollment"),
        "has_results": parsed.get("has_results"),
        "start_date": parsed.get("start_date"),
        "last_update": parsed.get("last_update"),
    }


def link_study(
    parsed: Mapping[str, Any], index: StoreIndex, *, gene_symbols: Sequence[str] = (), retrieved_at: str
) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    """Edges from a study to the Disease / Drug / Gene nodes it matches, plus the unlinked values.

    Returns ``(edge_rows, {"unlinked_conditions", "unlinked_interventions", "unlinked_genes"})``.
    One edge per (target, relation); further matching texts go to ``props.matched_texts``.
    """
    source = study_id(parsed["nct_id"])
    rubric = _rubric_fields(parsed)
    edges: dict[tuple[str, str], dict[str, Any]] = {}

    def add(target: str, relation: str, method: str, matched_text: str, evidence_class: str = "observed", **extra: Any) -> None:
        key = (target, relation)
        if key in edges:
            texts = edges[key]["props"].setdefault("matched_texts", [edges[key]["props"]["match"]["matched_text"]])
            if matched_text not in texts:
                texts.append(matched_text)
            return
        props: dict[str, Any] = {
            "evidence_class": evidence_class,
            "match": {"method": method, "matched_text": matched_text},
            "method": method,
            "matched_text": matched_text,
            "record_id": parsed["nct_id"],
            "source_url": STUDY_URL.format(nct_id=parsed["nct_id"]),
            **rubric,
            **extra,
        }
        edges[key] = {
            "source": source,
            "target": target,
            "relation": relation,
            "source_db": SOURCE_NAME,
            "props": props,
            "retrieved_at": retrieved_at,
        }

    unlinked_conditions: list[str] = []
    for cond in parsed.get("conditions") or []:
        target = index.disease(cond)
        if target:
            add(target, REL_STUDIES, "condition", cond)
        elif cond not in unlinked_conditions:
            unlinked_conditions.append(cond)
    mesh_terms = list(parsed.get("condition_mesh_terms") or [])
    mesh_ids = list(parsed.get("condition_mesh_ids") or [])
    for i, term in enumerate(mesh_terms):
        target = index.disease(term)
        method, text = "condition_mesh_term", term
        if not target and i < len(mesh_ids):
            target = index.disease_by_mesh(mesh_ids[i])
            method, text = "condition_mesh_xref", f"MESH:{mesh_ids[i]}"
        if target:
            add(target, REL_STUDIES, method, text)

    unlinked_interventions: list[str] = []
    for item in parsed.get("interventions") or []:
        names = [item.get("name"), *(item.get("other_names") or [])]
        linked = False
        for j, name in enumerate(names):
            target = index.drug(name)
            if target:
                add(target, REL_EVALUATES, "intervention_name" if j == 0 else "intervention_other_name", name, intervention_type=item.get("type"))
                linked = True
        if not linked and item.get("name") and item["name"] not in unlinked_interventions:
            unlinked_interventions.append(item["name"])
    for term in parsed.get("intervention_mesh_terms") or []:
        target = index.drug(term)
        if target:
            add(target, REL_EVALUATES, "intervention_mesh_term", term)

    unlinked_genes: list[str] = []
    if not edges:
        for symbol in gene_symbols:
            target = index.gene(symbol)
            if target:
                add(target, REL_MENTIONS, "query_term", symbol, evidence_class="inferred")
            elif symbol not in unlinked_genes:
                unlinked_genes.append(symbol)
    return list(edges.values()), {
        "unlinked_conditions": unlinked_conditions,
        "unlinked_interventions": unlinked_interventions,
        "unlinked_genes": unlinked_genes,
    }


# --------------------------------------------------------------------------------------
# load
# --------------------------------------------------------------------------------------


@dataclass
class _Found:
    record: dict[str, Any]
    retrieved_at: str
    labels: list[str] = field(default_factory=list)
    gene_symbols: list[str] = field(default_factory=list)


def _throttle(http: Any) -> None:
    limiter = getattr(http, "limiter", None)
    if limiter is not None and limiter.limit_for(HOST) is None:
        limiter.set_limit(HOST, COURTESY_RATE)


def _force_offline(http: Any, offline: bool) -> Callable[[], None]:
    """Make a provided client's cache refuse network misses while we run; returns the undo."""
    cache = getattr(http, "cache", None)
    if not offline or cache is None or not hasattr(cache, "offline"):
        return lambda: None
    previous = cache.offline
    cache.offline = True

    def restore() -> None:
        cache.offline = previous

    return restore


def _query_entities(query: SeedQuery, index: StoreIndex) -> list[str]:
    """Store nodes a seed query is about (coverage rows are written under each of them)."""
    ids: list[str] = []
    for node_id in (index.disease(query.cond_phrase), index.drug(query.intr_phrase), *(index.gene(s) for s in query.gene_symbols)):
        if node_id and node_id not in ids:
            ids.append(node_id)
    return ids


def load(store: Store, seeds: Mapping[str, Any], *, http: Any = None, offline: bool = False) -> dict[str, Any]:
    """Run every seed query and write Study nodes, link edges, coverage and the sources row.

    ``http`` is any object with ``get_json(url, params=None, headers=None) -> CachedResponse``
    (:class:`pipeline.cache.CachedHttp`, or a fixture-backed fake in tests). ``offline=True`` makes
    cache misses raise :class:`pipeline.cache.CacheMissOffline` instead of touching the network.
    Returns a summary (queries with totals, node / edge / coverage counts, unlinked values). Raises
    :class:`CtgovError` after writing everything when a query failed (the failure is on the sources
    row under ``notes.ctgov_errors``).
    """
    settings = parse_seeds(seeds)
    own_http = http is None
    if own_http:
        http = CachedHttp(DiskCache(offline=True)) if offline else default_http()
    restore = _force_offline(http, offline) if not own_http else (lambda: None)
    try:
        _throttle(http)
        version, version_resp = fetch_version(http)
        data_timestamp = _text(version.get("dataTimestamp"))
        notes: dict[str, Any] = {
            "api_version": _text(version.get("apiVersion")),
            "data_timestamp": data_timestamp,
            "api_url": BASE_URL,
            "api_docs_url": API_DOCS_URL,
            "terms_url": TERMS_URL,
            "modifications": MODIFICATIONS_TEXT,
            "fields": list(settings.fields),
            "page_size": settings.page_size,
            "max_studies_per_query": settings.max_studies_per_query,
            "queries": [],
        }

        def write_source() -> None:
            store.upsert_source(
                SOURCE_NAME,
                version=data_timestamp or "",
                retrieved_at=version_resp.retrieved_at,
                license=LICENSE_TEXT,
                url=f"{BASE_URL}/studies",
                notes=notes,
            )

        write_source()
        index = StoreIndex(store)
        found: dict[str, _Found] = {}
        errors: list[dict[str, Any]] = []
        query_rows: list[dict[str, Any]] = []
        for query in settings.queries:
            cap = settings.cap_for(query)
            try:
                result = search(http, query, page_size=settings.page_size, max_studies=cap, fields=settings.fields)
            except CtgovHttpError as exc:
                errors.append(exc.to_record())
                notes["ctgov_errors"] = errors
                write_source()
                continue
            entities = [f"{COVERAGE_SEED_PREFIX}{query.label}", *_query_entities(query, index)]
            for entity_id in entities:
                store.add_coverage(entity_id, COVERAGE_SOURCE, result.params_string, result.retrieved_date, result.total_count)
            nct_ids: list[str] = []
            for rec in result.studies:
                nct_raw = _text(_get(rec, "protocolSection", "identificationModule", "nctId"))
                if nct_raw is None:
                    continue
                nct = nct_raw.upper()
                nct_ids.append(nct)
                entry = found.get(nct)
                if entry is None:
                    entry = found[nct] = _Found(record=rec, retrieved_at=result.retrieved_at)
                if query.label not in entry.labels:
                    entry.labels.append(query.label)
                for symbol in query.gene_symbols:
                    if symbol not in entry.gene_symbols:
                        entry.gene_symbols.append(symbol)
            row = {
                "label": query.label,
                "params": result.params_string,
                "total_count": result.total_count,
                "total_count_from": result.total_count_from,
                "fetched": len(result.studies),
                "pages": result.pages,
                "truncated": result.truncated,
                "date": result.retrieved_date,
                "coverage_entities": entities,
                "nct_ids": nct_ids,
            }
            query_rows.append(row)
            notes["queries"] = query_rows
            write_source()

        nodes: list[dict[str, Any]] = []
        edges: list[dict[str, Any]] = []
        coverage: list[tuple[str, str, int]] = []
        unlinked: dict[str, dict[str, list[str]]] = {}
        for nct in sorted(found):
            entry = found[nct]
            parsed = parse_study(entry.record)
            node = study_node(parsed, retrieved_at=entry.retrieved_at, data_timestamp=data_timestamp, found_by_queries=entry.labels)
            study_edges, missing = link_study(parsed, index, gene_symbols=entry.gene_symbols, retrieved_at=entry.retrieved_at)
            node["props"].update(missing)
            nodes.append(node)
            edges.extend(study_edges)
            coverage.append((node["id"], entry.retrieved_at[:10], len(study_edges)))
            if any(missing.values()):
                unlinked[node["id"]] = {k: v for k, v in missing.items() if v}
        if nodes:
            store.upsert_nodes(nodes)
        if edges:
            store.upsert_edges(edges)
        for node_id, date, hits in coverage:
            store.add_coverage(node_id, SOURCE_NAME, LINK_QUERY, date, hits)

        edges_by_relation: dict[str, int] = {}
        for e in edges:
            edges_by_relation[e["relation"]] = edges_by_relation.get(e["relation"], 0) + 1
        summary = {
            "source": SOURCE_NAME,
            "api_version": notes["api_version"],
            "data_timestamp": data_timestamp,
            "retrieved_at": version_resp.retrieved_at,
            "queries": query_rows,
            "studies": len(nodes),
            "nodes": len(nodes),
            "edges": len(edges),
            "edges_by_relation": edges_by_relation,
            "coverage_rows": sum(len(r["coverage_entities"]) for r in query_rows) + len(coverage),
            "unlinked": unlinked,
            "errors": errors,
        }
        notes["summary"] = {k: v for k, v in summary.items() if k not in {"queries", "unlinked", "errors"}}
        write_source()
        if errors:
            raise CtgovError(errors, summary)
        return summary
    finally:
        restore()
        if own_http and offline:
            http.close()


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Load ClinicalTrials.gov studies for the seed queries into an evidence store.")
    parser.add_argument("--store", required=True, help="SQLite store to write (e.g. data/build/scratch-ctgov.sqlite)")
    parser.add_argument("--seeds", default=SEED_NAME, help="seed file name under pipeline/seeds (default: ctgov)")
    parser.add_argument("--offline", action="store_true", help="serve everything from the cache; a miss is an error")
    args = parser.parse_args(argv)
    seeds = config.load_seeds(args.seeds)
    started = time.perf_counter()
    status = 0
    with Store(args.store) as store:
        try:
            summary = load(store, seeds, offline=args.offline)
        except CtgovError as exc:
            summary = exc.summary
            status = 1
        summary["store"] = str(store.path)
        summary["store_counts"] = store.counts()
    summary["seconds"] = round(time.perf_counter() - started, 2)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return status


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
