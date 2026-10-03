"""NIH RePORTER API v2 loader: funded projects -> Investigator nodes -> inferred links to Gene and
Disease nodes (README section 5 "Grants / investigators"; docs/source-apis.md section 3).

What it does
------------
1. ``POST /v2/projects/search`` once per seed query (``pipeline/seeds/reporter.yaml``), restricted
   to the last N NIH fiscal years computed from today's date, one page of ``limit`` records,
   ``exclude_subprojects: true``. Every search is a coverage row with ``hits = meta.total``
   (zero included; hard rule 3). Records are deduplicated on ``core_project_num`` (one grant has
   one application per fiscal year); ``appl_id`` stays the per-record key.
2. One ``Investigator`` node per (principal investigator, organisation). Names are built from
   ``first_name`` / ``middle_name`` / ``last_name`` with whitespace collapsed, never from the dirty
   ``contact_pi_name``. RePORTER has no ORCID, so ``props.match_confidence`` is ``"low"`` and the
   id is the internal ``person:<slug(last-first)>-<slug(org)>[-<profile_id>]``. No e-mail or phone
   is stored (the API has none).
3. Projects are linked to graph entities by text, ``evidence_class: "inferred"``: the scrubbed
   title + abstract is scanned for Gene symbols already in the store (uppercase, >= 3 chars,
   case-sensitive, word boundary) and Disease labels / synonyms (>= 12 chars, case-insensitive
   exact phrase). Each match is an ``Investigator -> Gene`` or ``Investigator -> Disease`` edge
   with relation ``has_funded_project`` and the raw fields the confidence rubric needs
   (``award_amount_total``, ``fiscal_years``, ``agency``, ``matched_text``). Projects with no
   match are kept in ``props.unlinked_projects`` and counted in a coverage row per query.
4. ``POST /v2/publications/search`` with the deduped ``core_project_nums`` in batches gives
   ``props.projects[].pmids`` (strings) so Lane B can join to Paper nodes.
5. A ``sources`` row ``"NIH RePORTER"`` with the retrieval date as version.

Hard rules honoured here: every HTTP call goes through :class:`pipeline.cache.CachedHttp` with
``User-Agent: pipeline.config.USER_AGENT`` and a 1 req/s limiter for ``api.reporter.nih.gov``;
nothing is fetched twice; offline runs serve the cache or raise :class:`CacheMissOffline`; every
free-text field that reaches the store passes :func:`scrub_doses` (no dosing information, hard
rule 7); no identifier is hand-typed; confidence is NOT computed here (build.py does that).

Run the loader on its own into a scratch store::

    uv run python -m pipeline.sources.reporter --store data/build/scratch-reporter.sqlite
"""

from __future__ import annotations

import argparse
import html
import json
import logging
import re
import time
import unicodedata
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from pipeline import config
from pipeline.cache import CachedHttp, CachedResponse, DiskCache
from pipeline.models import NodeType
from pipeline.store import Store

__all__ = [
    "BASE_URL",
    "COVERAGE_LINKS_SOURCE",
    "COVERAGE_PROJECTS_SOURCE",
    "COVERAGE_PUBLICATIONS_SOURCE",
    "DEFAULT_INCLUDE_FIELDS",
    "DOSE_PLACEHOLDER",
    "LICENSE",
    "PROJECTS_URL",
    "PUBLICATIONS_URL",
    "RELATION",
    "REPORTER_HOST",
    "REPORTER_RATE",
    "SOURCE_NAME",
    "Grant",
    "ReporterApiError",
    "build_investigator_id",
    "build_projects_body",
    "build_publications_body",
    "collapse_whitespace",
    "error_message",
    "fiscal_year_of",
    "fiscal_years_for",
    "load",
    "person_name",
    "scrub_doses",
    "slugify",
]

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------------------
# constants
# --------------------------------------------------------------------------------------

SOURCE_NAME = "NIH RePORTER"
BASE_URL = "https://api.reporter.nih.gov"
REPORTER_HOST = "api.reporter.nih.gov"
PROJECTS_URL = f"{BASE_URL}/v2/projects/search"
PUBLICATIONS_URL = f"{BASE_URL}/v2/publications/search"
REPORTER_RATE = 1.0  # requests per second (docs: "no more than one URL request per second")

COVERAGE_PROJECTS_SOURCE = "NIH RePORTER projects"
COVERAGE_PUBLICATIONS_SOURCE = "NIH RePORTER publications"
COVERAGE_LINKS_SOURCE = "NIH RePORTER project links"
SEED_PREFIX = "seed:reporter:"

RELATION = "has_funded_project"
EVIDENCE_CLASS = "inferred"
MATCH_CONFIDENCE = "low"  # no ORCID anywhere in RePORTER (README section 5)

LICENSE = (
    "Public domain (U.S. Government work); RePORTER data access policy — see docs/source-apis.md "
    "(UNVERIFIED wording)"
)

DEFAULT_INCLUDE_FIELDS: tuple[str, ...] = (
    "ApplId",
    "ProjectNum",
    "CoreProjectNum",
    "ProjectTitle",
    "AbstractText",
    "FiscalYear",
    "AwardAmount",
    "AgencyIcAdmin",
    "Organization",
    "PrincipalInvestigators",
    "ContactPiName",
    "ProjectStartDate",
    "ProjectEndDate",
    "ProjectDetailUrl",
    "Terms",
    # Raw fields the confidence rubric reads from props.projects[] (null unless requested here).
    "IsActive",
    "ActivityCode",
    "AwardType",
    "OpportunityNumber",
)
DEFAULT_SEARCH = {
    "operator": "and",
    "search_field": "projecttitle,abstracttext,terms",
    "fiscal_years_back": 6,
    "limit": 50,
    "exclude_subprojects": True,
}
DEFAULT_PUBLICATIONS = {"batch_size": 50, "limit": 500}
DEFAULT_LINKING = {"min_gene_symbol_length": 3, "min_disease_name_length": 12}
PUBLICATIONS_MAX_OFFSET = 9_999  # API cap (docs/source-apis.md section 3c.5)

DOSE_PLACEHOLDER = "[dose removed]"

# --------------------------------------------------------------------------------------
# dose scrubber (hard rule 7: no dosing information anywhere)
# --------------------------------------------------------------------------------------

_NUMBER = r"\d+(?:[.,]\d+)?"
_RANGE = rf"{_NUMBER}(?:\s*(?:-|–|to)\s*{_NUMBER})?"
_UNIT = (
    r"(?:mg|mcg|µg|μg|ug|ng|g|kg|mL|ml|dL|dl|L|cc|IU|units?|U|%|mmol|µmol|μmol|mEq|meq|"
    r"tablets?|capsules?|pills?|puffs?|drops?|doses?)"
)
_PER = r"(?:\s*/\s*(?:kg|m2|m²|m\^2|mL|ml|dL|dl|L|day|d|h|hr|hour|wk|week|month|mo|dose|min))*"
_DOSE_AMOUNT = rf"(?<![A-Za-z0-9.]){_RANGE}\s*{_UNIT}{_PER}(?![A-Za-z0-9])"
# A dose unit ratio without a number ("mg/kg", "IU/kg", "mg/m2 dosing") is still dosing language.
_UNIT_RATIO = (
    r"(?<![A-Za-z0-9])(?:mg|mcg|µg|μg|ug|g|IU|units?|U|mL|ml)\s*/\s*(?:kg|m2|m²|m\^2|day|d|dose|h|hr|hour)"
    r"(?![A-Za-z0-9])"
)
_FREQUENCY_WORDS = (
    r"\b(?:once|twice|thrice|one time|two times|three times|four times|[1-4]\s*x|[1-4]\s*times)\s+"
    r"(?:daily|a day|per day|weekly|a week|per week|monthly|a month|per month|nightly)\b"
)
# Upper-case abbreviations are matched case-sensitively ((?-i:...)) so the English word "bid" survives;
# the dotted Latin forms need their dots.
_FREQUENCY_ABBREV = (
    r"(?-i:(?<![A-Za-z])(?:BID|TID|QID|QD|QOD|QHS|QAM|QPM|PRN)(?![A-Za-z]))"
    r"|(?<![A-Za-z])(?:q\.d\.|b\.i\.d\.|t\.i\.d\.|q\.i\.d\.|q\.o\.d\.)(?![A-Za-z])"
)
_EVERY_N = (
    r"\bevery\s+(?:\d+|other|two|three|four|five|six|seven|eight|ten|twelve|twenty-four|few|second|third)"
    r"\s+(?:hours?|hrs?|days?|weeks?|wks?|months?)\b"
)
_Q_HOURS = r"(?<![A-Za-z0-9])q\s*\d+\s*(?:h|hr|hrs|hours?)(?![A-Za-z0-9])"
_DAILY_DOSE_WORDS = r"\b(?:daily|weekly|monthly)\s+dose\b"

# ONE shared regex; every free-text field stored by this module passes through it.
DOSE_PATTERN = re.compile(
    "|".join(
        (
            _DOSE_AMOUNT,
            _UNIT_RATIO,
            _FREQUENCY_WORDS,
            _FREQUENCY_ABBREV,
            _EVERY_N,
            _Q_HOURS,
            _DAILY_DOSE_WORDS,
        )
    ),
    re.IGNORECASE,
)


def scrub_doses(text: str | None) -> str:
    """Replace every dose-like pattern (amount + unit, mg/kg, %, BID, "twice daily", "every N
    weeks", ...) with :data:`DOSE_PLACEHOLDER`. Applied to titles, abstracts and every other free
    text before it is stored or matched. ``None`` becomes ``""``."""
    if not text:
        return ""
    return DOSE_PATTERN.sub(DOSE_PLACEHOLDER, text)


# --------------------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------------------


def collapse_whitespace(text: str | None) -> str:
    """Runs of whitespace (incl. the hard line breaks in RePORTER abstracts) -> one space; ends stripped."""
    return " ".join((text or "").split())


def slugify(text: str | None) -> str:
    """ASCII, lower-case, ``[a-z0-9]`` runs joined by single hyphens; accents stripped."""
    normalised = unicodedata.normalize("NFKD", text or "")
    ascii_text = normalised.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")


def fiscal_year_of(day: date) -> int:
    """NIH fiscal year containing ``day`` (FY starts 1 October: 2026-10-03 is FY2027)."""
    return day.year + 1 if day.month >= 10 else day.year


def fiscal_years_for(today: date | None = None, back: int = 6) -> list[int]:
    """The last ``back`` fiscal years ending with the current one, ascending. Computed, never typed."""
    current = fiscal_year_of(today or date.today())
    n = max(1, int(back))
    return list(range(current - n + 1, current + 1))


def person_name(pi: Mapping[str, Any]) -> tuple[str, str]:
    """``(label, full_name)`` from ``first_name`` / ``middle_name`` / ``last_name`` with whitespace
    collapsed (``full_name`` in the API has double and trailing spaces). ``label`` is "First Last".
    Falls back to the collapsed API ``full_name`` when the parts are empty. Never uses
    ``contact_pi_name``."""
    first = collapse_whitespace(pi.get("first_name"))
    middle = collapse_whitespace(pi.get("middle_name"))
    last = collapse_whitespace(pi.get("last_name"))
    label = collapse_whitespace(f"{first} {last}")
    full = collapse_whitespace(" ".join(p for p in (first, middle, last) if p))
    if not label:
        label = full = collapse_whitespace(pi.get("full_name"))
    return label, full


def build_investigator_id(pi: Mapping[str, Any], org_name: str | None) -> str:
    """``person:<slug(last-first)>-<slug(org_name)>[-<profile_id>]``."""
    last = collapse_whitespace(pi.get("last_name"))
    first = collapse_whitespace(pi.get("first_name"))
    if not (last or first):
        label, _ = person_name(pi)
        name_slug = slugify(label)
    else:
        name_slug = slugify(f"{last}-{first}")
    parts = [p for p in (name_slug, slugify(org_name) or "unknown-organization") if p]
    profile_id = pi.get("profile_id")
    if profile_id not in (None, ""):
        parts.append(str(profile_id))
    return "person:" + "-".join(parts)


def error_message(response: CachedResponse) -> str:
    """Human-readable text of a RePORTER error body: ``{"message": ...}`` (cap errors) or a JSON
    array of HTML-entity-escaped strings (model-binding errors); anything else is the raw text."""
    text = response.text or ""
    try:
        payload = json.loads(text)
    except ValueError:
        return html.unescape(text.strip())[:500]
    if isinstance(payload, Mapping):
        return html.unescape(str(payload.get("message") or payload))
    if isinstance(payload, list):
        return html.unescape("; ".join(str(item) for item in payload))
    return html.unescape(str(payload))


class ReporterApiError(RuntimeError):
    """A 4xx answer from RePORTER: a request-shape bug, not a data gap, so the run stops."""

    def __init__(self, response: CachedResponse, what: str) -> None:
        self.response = response
        super().__init__(f"RePORTER {what}: HTTP {response.status}: {error_message(response)}")


# --------------------------------------------------------------------------------------
# request bodies
# --------------------------------------------------------------------------------------


def build_projects_body(
    query: str,
    fiscal_years: Sequence[int],
    *,
    operator: str,
    search_field: str,
    limit: int,
    exclude_subprojects: bool,
    include_fields: Sequence[str],
    offset: int = 0,
) -> dict[str, Any]:
    """snake_case body keys, PascalCase ``include_fields`` values (docs/source-apis.md section 3c.1)."""
    return {
        "criteria": {
            "advanced_text_search": {
                "operator": operator,
                "search_field": search_field,
                "search_text": query,
            },
            "fiscal_years": [int(y) for y in fiscal_years],
            "exclude_subprojects": bool(exclude_subprojects),
        },
        "offset": int(offset),
        "limit": int(limit),
        "include_fields": list(include_fields),
    }


def build_publications_body(core_project_nums: Sequence[str], *, limit: int, offset: int = 0) -> dict[str, Any]:
    return {
        "criteria": {"core_project_nums": list(core_project_nums)},
        "offset": int(offset),
        "limit": int(limit),
    }


# --------------------------------------------------------------------------------------
# HTTP plumbing
# --------------------------------------------------------------------------------------


def _request_headers() -> dict[str, str]:
    return {"User-Agent": config.USER_AGENT}


def _ensure_rate_limit(http: Any) -> None:
    """Make sure the client spaces RePORTER calls >= 1 s apart (per-host token bucket)."""
    limiter = getattr(http, "limiter", None)
    if limiter is None:
        return
    current = limiter.limit_for(REPORTER_HOST)
    if current is None or current > REPORTER_RATE:
        limiter.set_limit(REPORTER_HOST, REPORTER_RATE)


@contextmanager
def _offline_mode(http: Any, offline: bool) -> Iterator[None]:
    """Force the client's cache offline for the duration when ``offline=True`` was requested."""
    cache = getattr(http, "cache", None)
    if not offline or cache is None or cache.is_offline():
        yield
        return
    previous = cache.offline
    cache.offline = True
    try:
        yield
    finally:
        cache.offline = previous


def _make_http(offline: bool) -> CachedHttp:
    return CachedHttp(
        DiskCache(offline=True if offline else None),
        headers=_request_headers(),
        host_limits={REPORTER_HOST: REPORTER_RATE},
    )


def _post(http: Any, url: str, body: Mapping[str, Any]) -> CachedResponse:
    return http.post_json(url, body, headers=_request_headers())


# --------------------------------------------------------------------------------------
# records
# --------------------------------------------------------------------------------------


@dataclass
class Grant:
    """One grant (``core_project_num``) assembled from its per-fiscal-year application records."""

    core_project_num: str
    record: dict[str, Any]  # the latest fiscal-year record
    appl_ids: set[int] = field(default_factory=set)
    fiscal_years: set[int] = field(default_factory=set)
    award_amounts: dict[int, int] = field(default_factory=dict)  # appl_id -> award_amount
    queries: set[str] = field(default_factory=set)
    retrieved_at: str = ""
    pmids: list[str] = field(default_factory=list)
    matched_genes: dict[str, str] = field(default_factory=dict)  # node id -> matched text
    matched_diseases: dict[str, str] = field(default_factory=dict)

    @property
    def appl_id(self) -> int | None:
        value = self.record.get("appl_id")
        return int(value) if value is not None else None

    @property
    def award_amount_total(self) -> int:
        return int(sum(self.award_amounts.values()))

    @property
    def title(self) -> str:
        return collapse_whitespace(scrub_doses(self.record.get("project_title")))

    @property
    def abstract(self) -> str:
        return collapse_whitespace(scrub_doses(self.record.get("abstract_text")))

    @property
    def matched(self) -> bool:
        return bool(self.matched_genes or self.matched_diseases)

    def absorb(self, record: Mapping[str, Any], query: str, retrieved_at: str) -> None:
        appl_id = record.get("appl_id")
        if appl_id is not None:
            self.appl_ids.add(int(appl_id))
            amount = record.get("award_amount")
            if isinstance(amount, (int, float)) and not isinstance(amount, bool):
                self.award_amounts[int(appl_id)] = int(amount)
        fy = record.get("fiscal_year")
        if fy is not None:
            self.fiscal_years.add(int(fy))
        self.queries.add(query)
        if retrieved_at > self.retrieved_at:
            self.retrieved_at = retrieved_at
        if _record_order(record) > _record_order(self.record):
            self.record = dict(record)

    def to_props(self) -> dict[str, Any]:
        r = self.record
        agency = r.get("agency_ic_admin") or {}
        return {
            "appl_id": self.appl_id,
            "appl_ids": sorted(self.appl_ids),
            "core_project_num": self.core_project_num,
            "project_num": r.get("project_num"),
            "title": self.title,
            "fiscal_year": r.get("fiscal_year"),
            "fiscal_years": sorted(self.fiscal_years),
            "award_amount": r.get("award_amount"),
            "award_amount_total": self.award_amount_total,
            "agency_ic_admin": {
                "code": agency.get("code"),
                "abbreviation": agency.get("abbreviation"),
                "name": agency.get("name"),
            },
            "activity_code": r.get("activity_code"),
            "award_type": r.get("award_type"),
            "is_active": r.get("is_active"),
            "opportunity_number": r.get("opportunity_number"),
            "start": r.get("project_start_date"),
            "end": r.get("project_end_date"),
            "detail_url": r.get("project_detail_url"),
            "found_by_queries": sorted(self.queries),
            "pmids": list(self.pmids),
            "matched": {
                "genes": sorted(self.matched_genes),
                "diseases": sorted(self.matched_diseases),
            },
        }


def _record_order(record: Mapping[str, Any]) -> tuple[int, int]:
    """Latest fiscal year wins; ties go to the highest appl_id (deterministic)."""
    return (int(record.get("fiscal_year") or 0), int(record.get("appl_id") or 0))


@dataclass
class Investigator:
    node_id: str
    label: str
    full_name: str
    profile_id: int | None
    title: str | None
    organization: dict[str, Any]
    grants: dict[str, Grant] = field(default_factory=dict)
    is_contact_pi: bool = False

    @property
    def retrieved_at(self) -> str:
        return max((g.retrieved_at for g in self.grants.values()), default="")

    @property
    def queries(self) -> list[str]:
        return sorted({q for g in self.grants.values() for q in g.queries})

    def role_label(self) -> str:
        n = len(self.grants)
        labels = ", ".join(f'"{q}"' for q in self.queries)
        return f"PI on {n} NIH project{'s' if n != 1 else ''} matching {labels}"

    def to_node(self) -> dict[str, Any]:
        grants = sorted(self.grants.values(), key=lambda g: _record_order(g.record), reverse=True)
        unlinked = [g.core_project_num for g in grants if not g.matched]
        props: dict[str, Any] = {
            "record_id": str(self.profile_id) if self.profile_id is not None else self.node_id.removeprefix("person:"),
            "match_confidence": MATCH_CONFIDENCE,
            "profile_id": self.profile_id,
            "name": {"full": self.full_name},
            "title": self.title,
            "organization": self.organization,
            "is_contact_pi": self.is_contact_pi,
            "projects": [g.to_props() for g in grants],
            "n_projects": len(grants),
            "found_by_queries": self.queries,
            "unlinked_projects": unlinked,
            "role_label": self.role_label(),
            "pmids": sorted({p for g in grants for p in g.pmids}),
        }
        detail_urls = [g.record.get("project_detail_url") for g in grants if g.record.get("project_detail_url")]
        if detail_urls:
            props["source_url"] = detail_urls[0]  # the public record page of the latest project
        return {
            "id": self.node_id,
            "type": NodeType.Investigator.value,
            "label": self.label,
            "synonyms": [],
            "props": props,
            "source": SOURCE_NAME,
            "retrieved_at": self.retrieved_at,
        }


def _scrubbed_text(value: Any) -> Any:
    """Free text that reaches the store passes the dose scrubber; non-strings pass through."""
    if isinstance(value, str):
        return collapse_whitespace(scrub_doses(value)) or None
    return value


def _organization(record: Mapping[str, Any]) -> dict[str, Any]:
    """The four organisation fields, dose-scrubbed (they feed both the node id slug and props)."""
    org = record.get("organization") or {}
    return {
        "org_name": _scrubbed_text(org.get("org_name")),
        "org_city": _scrubbed_text(org.get("org_city")),
        "org_state": _scrubbed_text(org.get("org_state")),
        "org_country": _scrubbed_text(org.get("org_country")),
    }


# --------------------------------------------------------------------------------------
# text linking
# --------------------------------------------------------------------------------------


def _gene_patterns(store: Store, min_len: int) -> list[tuple[str, str, re.Pattern[str]]]:
    out: list[tuple[str, str, re.Pattern[str]]] = []
    seen: set[str] = set()
    for node in store.iter_nodes(NodeType.Gene):
        symbol = node["props"].get("symbol") or node["label"]
        if not isinstance(symbol, str):
            continue
        symbol = symbol.strip()
        if len(symbol) < min_len or symbol != symbol.upper() or not any(c.isalpha() for c in symbol):
            continue
        if symbol in seen:
            continue
        seen.add(symbol)
        out.append((node["id"], symbol, re.compile(rf"(?<![A-Za-z0-9]){re.escape(symbol)}(?![A-Za-z0-9])")))
    return out


def _disease_patterns(store: Store, min_len: int) -> list[tuple[str, str, re.Pattern[str]]]:
    out: list[tuple[str, str, re.Pattern[str]]] = []
    seen: set[tuple[str, str]] = set()
    for node in store.iter_nodes(NodeType.Disease):
        names = [node["label"], *node.get("synonyms", [])]
        for raw in names:
            if not isinstance(raw, str):
                continue
            name = collapse_whitespace(raw)
            if len(name) < min_len or not any(c.isalpha() for c in name):
                continue
            key = (node["id"], name.lower())
            if key in seen:
                continue
            seen.add(key)
            tokens = [re.escape(t) for t in name.split(" ")]
            pattern = re.compile(rf"(?<![A-Za-z0-9]){r'\s+'.join(tokens)}(?![A-Za-z0-9])", re.IGNORECASE)
            out.append((node["id"], name, pattern))
    return out


def _link_grant(
    grant: Grant,
    gene_patterns: Sequence[tuple[str, str, re.Pattern[str]]],
    disease_patterns: Sequence[tuple[str, str, re.Pattern[str]]],
) -> None:
    """Scan the scrubbed, whitespace-collapsed title + abstract for every store Gene symbol and
    Disease name; record the first matched text per node id."""
    text = f"{grant.title} {grant.abstract}"
    for node_id, _symbol, pattern in gene_patterns:
        m = pattern.search(text)
        if m is not None:
            grant.matched_genes.setdefault(node_id, m.group(0))
    for node_id, _name, pattern in disease_patterns:
        m = pattern.search(text)
        if m is not None:
            grant.matched_diseases.setdefault(node_id, collapse_whitespace(m.group(0)))


def _edge_rows(investigators: Iterable[Investigator]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for inv in investigators:
        per_target: dict[tuple[str, str], list[tuple[Grant, str]]] = {}
        for grant in inv.grants.values():
            for node_id, text in grant.matched_genes.items():
                per_target.setdefault(("gene_symbol_in_title_or_abstract", node_id), []).append((grant, text))
            for node_id, text in grant.matched_diseases.items():
                per_target.setdefault(("disease_name_in_title_or_abstract", node_id), []).append((grant, text))
        for (method, target), hits in sorted(per_target.items(), key=lambda kv: kv[0][1]):
            grants = sorted({g.core_project_num: g for g, _ in hits}.values(), key=lambda g: g.core_project_num)
            matched_text = sorted({t for _, t in hits})
            agencies = sorted(
                {
                    (g.record.get("agency_ic_admin") or {}).get("abbreviation")
                    for g in grants
                    if (g.record.get("agency_ic_admin") or {}).get("abbreviation")
                }
            )
            rows.append(
                {
                    "source": inv.node_id,
                    "target": target,
                    "relation": RELATION,
                    "source_db": SOURCE_NAME,
                    "props": {
                        "evidence_class": EVIDENCE_CLASS,
                        "method": method,
                        "match": {"method": method, "matched_text": "; ".join(matched_text)},
                        "matched_text": matched_text,
                        "core_project_nums": [g.core_project_num for g in grants],
                        "appl_ids": sorted({a for g in grants for a in g.appl_ids}),
                        "fiscal_years": sorted({fy for g in grants for fy in g.fiscal_years}),
                        "agency": agencies,
                        "award_amount_total": int(sum(g.award_amount_total for g in grants)),
                        "n_projects": len(grants),
                        "detail_urls": [g.record.get("project_detail_url") for g in grants if g.record.get("project_detail_url")],
                        "found_by_queries": sorted({q for g in grants for q in g.queries}),
                        "pmids": sorted({p for g in grants for p in g.pmids}),
                    },
                    "retrieved_at": max(g.retrieved_at for g in grants),
                }
            )
    return rows


# --------------------------------------------------------------------------------------
# loader
# --------------------------------------------------------------------------------------


def _settings(seeds: Mapping[str, Any]) -> tuple[list[str], dict[str, Any], list[str], dict[str, Any], dict[str, Any]]:
    queries = [str(q).strip() for q in (seeds.get("queries") or []) if str(q).strip()]
    if not queries:
        raise ValueError("reporter seeds: 'queries' must list at least one search term")
    search = {**DEFAULT_SEARCH, **(seeds.get("search") or {})}
    include_fields = [str(f) for f in (seeds.get("include_fields") or DEFAULT_INCLUDE_FIELDS)]
    bad = [f for f in include_fields if not re.fullmatch(r"[A-Z][A-Za-z0-9]*", f)]
    if bad:
        raise ValueError(f"reporter seeds: include_fields must be PascalCase data-element names, got {bad}")
    publications = {**DEFAULT_PUBLICATIONS, **(seeds.get("publications") or {})}
    linking = {**DEFAULT_LINKING, **(seeds.get("linking") or {})}
    return queries, search, include_fields, publications, linking


def _results(payload: Any) -> tuple[int, list[dict[str, Any]]]:
    if not isinstance(payload, Mapping):
        raise ValueError(f"RePORTER response is not a JSON object: {type(payload).__name__}")
    meta = payload.get("meta") or {}
    total = meta.get("total")
    results = payload.get("results") or []
    hits = int(total) if isinstance(total, int) and not isinstance(total, bool) else len(results)
    return hits, [r for r in results if isinstance(r, Mapping)]


def load(
    store: Store,
    seeds: Mapping[str, Any],
    *,
    http: Any | None = None,
    offline: bool = False,
    today: date | None = None,
) -> dict[str, Any]:
    """Run the RePORTER loader into ``store``. Returns a summary dict (totals per query, grants,
    investigators, links, PMIDs, timing). ``http`` defaults to a fresh :class:`CachedHttp`;
    ``offline=True`` forces cache-only mode (a miss raises :class:`CacheMissOffline`)."""
    started = time.monotonic()
    queries, search, include_fields, publications, linking = _settings(seeds)
    fiscal_years = fiscal_years_for(today, int(search["fiscal_years_back"]))
    own_http = http is None
    client = _make_http(offline) if own_http else http
    _ensure_rate_limit(client)

    totals: dict[str, int] = {}
    errors: list[dict[str, Any]] = []
    grants: dict[str, Grant] = {}
    grants_by_query: dict[str, set[str]] = {q: set() for q in queries}
    latest_retrieved = ""

    try:
        with _offline_mode(client, offline):
            # 1. projects search, one page per query ---------------------------------------
            for query in queries:
                body = build_projects_body(
                    query,
                    fiscal_years,
                    operator=str(search["operator"]),
                    search_field=str(search["search_field"]),
                    limit=int(search["limit"]),
                    exclude_subprojects=bool(search["exclude_subprojects"]),
                    include_fields=include_fields,
                )
                resp = _post(client, PROJECTS_URL, body)
                latest_retrieved = max(latest_retrieved, resp.retrieved_at)
                entity = f"{SEED_PREFIX}{query}"
                if not resp.ok:
                    store.add_coverage(entity, COVERAGE_PROJECTS_SOURCE, query, resp.retrieved_date, 0)
                    if 400 <= resp.status < 500:
                        raise ReporterApiError(resp, f"projects search for {query!r}")
                    message = error_message(resp)
                    log.warning("RePORTER projects search %r failed: HTTP %s %s", query, resp.status, message)
                    errors.append({"query": query, "status": resp.status, "message": message})
                    totals[query] = 0
                    continue
                hits, records = _results(resp.json())
                totals[query] = hits
                store.add_coverage(entity, COVERAGE_PROJECTS_SOURCE, query, resp.retrieved_date, hits)
                for record in records:
                    core = record.get("core_project_num")
                    if not core:
                        core = record.get("project_num") or f"appl:{record.get('appl_id')}"
                    core = str(core)
                    grant = grants.get(core)
                    if grant is None:
                        grant = grants[core] = Grant(core_project_num=core, record=dict(record))
                    grant.absorb(record, query, resp.retrieved_at)
                    grants_by_query[query].add(core)

            # 2. publications per batch of core_project_nums -------------------------------
            pmids_by_core: dict[str, set[str]] = {}
            core_nums = sorted(grants)
            batch_size = max(1, int(publications["batch_size"]))
            pub_limit = max(1, min(500, int(publications["limit"])))
            for i in range(0, len(core_nums), batch_size):
                batch = core_nums[i : i + batch_size]
                entity = f"{SEED_PREFIX}publications:batch:{i // batch_size + 1}"
                query_text = ",".join(batch)
                offset = 0
                while True:
                    body = build_publications_body(batch, limit=pub_limit, offset=offset)
                    resp = _post(client, PUBLICATIONS_URL, body)
                    latest_retrieved = max(latest_retrieved, resp.retrieved_at)
                    if not resp.ok:
                        if offset == 0:
                            store.add_coverage(entity, COVERAGE_PUBLICATIONS_SOURCE, query_text, resp.retrieved_date, 0)
                        if 400 <= resp.status < 500:
                            raise ReporterApiError(resp, f"publications search batch {i // batch_size + 1}")
                        message = error_message(resp)
                        log.warning("RePORTER publications batch %d failed: HTTP %s %s", i // batch_size + 1, resp.status, message)
                        errors.append({"publications_batch": i // batch_size + 1, "status": resp.status, "message": message})
                        break
                    hits, rows = _results(resp.json())
                    if offset == 0:
                        store.add_coverage(entity, COVERAGE_PUBLICATIONS_SOURCE, query_text, resp.retrieved_date, hits)
                    for row in rows:
                        core = row.get("coreproject")
                        pmid = row.get("pmid")
                        if core and pmid is not None:
                            pmids_by_core.setdefault(str(core), set()).add(str(pmid))
                    offset += pub_limit
                    if offset >= hits or not rows or offset > PUBLICATIONS_MAX_OFFSET:
                        break
            for core, grant in grants.items():
                grant.pmids = sorted(pmids_by_core.get(core, set()), key=lambda p: (len(p), p))
    finally:
        if own_http:
            client.close()

    # 3. text links to Gene / Disease nodes already in the store -------------------------
    gene_patterns = _gene_patterns(store, int(linking["min_gene_symbol_length"]))
    disease_patterns = _disease_patterns(store, int(linking["min_disease_name_length"]))
    for grant in grants.values():
        _link_grant(grant, gene_patterns, disease_patterns)
    link_date = (latest_retrieved or "")[:10]
    if link_date:
        for query in queries:
            matched = sum(1 for core in grants_by_query[query] if grants[core].matched)
            store.add_coverage(f"{SEED_PREFIX}{query}", COVERAGE_LINKS_SOURCE, query, link_date, matched)

    # 4. investigators -------------------------------------------------------------------
    investigators: dict[str, Investigator] = {}
    grants_without_pi: list[str] = []
    skipped_pis = 0
    for grant in grants.values():
        pis = grant.record.get("principal_investigators") or []
        organization = _organization(grant.record)
        if not pis:
            grants_without_pi.append(grant.core_project_num)
            continue
        for pi in pis:
            if not isinstance(pi, Mapping):
                continue
            label, full = person_name(pi)
            if not label:
                skipped_pis += 1
                continue
            node_id = build_investigator_id(pi, organization.get("org_name"))
            inv = investigators.get(node_id)
            if inv is None:
                raw_profile = pi.get("profile_id")
                inv = investigators[node_id] = Investigator(
                    node_id=node_id,
                    label=label,
                    full_name=full,
                    profile_id=int(raw_profile) if raw_profile not in (None, "") else None,
                    title=_scrubbed_text(pi.get("title")),
                    organization=organization,
                )
            inv.grants[grant.core_project_num] = grant
            inv.is_contact_pi = inv.is_contact_pi or bool(pi.get("is_contact_pi"))

    node_rows = [inv.to_node() for inv in sorted(investigators.values(), key=lambda i: i.node_id)]
    edge_rows = _edge_rows(sorted(investigators.values(), key=lambda i: i.node_id))
    if node_rows:
        store.upsert_nodes(node_rows)
    if edge_rows:
        store.upsert_edges(edge_rows)

    # 5. sources row ---------------------------------------------------------------------
    retrieved_at = latest_retrieved or ""
    if retrieved_at:
        store.upsert_source(
            SOURCE_NAME,
            version=retrieved_at[:10],
            retrieved_at=retrieved_at,
            license=LICENSE,
            url=BASE_URL,
            notes={
                "api": "NIH RePORTER API v2",
                "endpoints": [PROJECTS_URL, PUBLICATIONS_URL],
                "queries": queries,
                "fiscal_years": fiscal_years,
                "operator": search["operator"],
                "search_field": search["search_field"],
                "limit": int(search["limit"]),
                "exclude_subprojects": bool(search["exclude_subprojects"]),
                "include_fields": include_fields,
                "totals": totals,
                "grants": len(grants),
                "errors": errors,
            },
        )

    pmid_total = len({p for g in grants.values() for p in g.pmids})
    summary = {
        "source": SOURCE_NAME,
        "fiscal_years": fiscal_years,
        "totals": totals,
        "records_seen": sum(len(g.appl_ids) for g in grants.values()),
        "grants": len(grants),
        "grants_without_pi": grants_without_pi,
        "skipped_pis": skipped_pis,
        "investigators": len(node_rows),
        "links": len(edge_rows),
        "linked_grants": sum(1 for g in grants.values() if g.matched),
        "unlinked_grants": sum(1 for g in grants.values() if not g.matched),
        "pmids": pmid_total,
        "grants_with_pmids": sum(1 for g in grants.values() if g.pmids),
        "errors": errors,
        "retrieved_at": retrieved_at,
        "seconds": round(time.monotonic() - started, 2),
    }
    log.info("RePORTER: %s", json.dumps(summary, default=str))
    return summary


# --------------------------------------------------------------------------------------
# CLI (scratch runs)
# --------------------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Load NIH RePORTER grants into the evidence store.")
    parser.add_argument("--store", type=Path, default=config.STORE_PATH, help="SQLite store path")
    parser.add_argument("--offline", action="store_true", help="cache only; a miss raises")
    parser.add_argument("--seeds", default="reporter", help="seed file name under pipeline/seeds")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    seeds = config.load_seeds(args.seeds)
    with Store(args.store) as store:
        summary = load(store, seeds, offline=args.offline)
        summary["store_counts"] = store.counts()
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
