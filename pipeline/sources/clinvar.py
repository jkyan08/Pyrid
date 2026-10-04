"""ClinVar loader: Variant nodes for the cluster genes via NCBI E-utilities (README section 5).

What it writes to the evidence store (:mod:`pipeline.store`)
------------------------------------------------------------
* One ``Variant`` node per ClinVar record returned by ``<SYMBOL>[gene]`` for every seed gene,
  id ``clinvar:<variation id>`` (:func:`pipeline.ids.normalize_curie` with the ``clinvar`` hint),
  label = ClinVar ``title``, synonyms = one-letter ``protein_change`` tokens + ``cdna_change`` +
  VCV accession + the ``p.`` HGVS from the title + ``"<GENE> <token>"`` for the MANE token.
  ``props`` keeps every parsed field the confidence rubric needs (classification, review status,
  last_evaluated, SCV/RCV counts, somatic blocks, traits with xrefs) plus ``record_id``,
  ``source_url`` and ``keep_in_graph``. Confidence is NOT computed here (build.py does that).
* Records naming more than ``max_genes_per_record`` genes are structural CNVs and are excluded
  (counted in coverage as ``excluded_cnv``). This is a general rule, never a per-gene special case.
* Edges ``Variant -> Gene`` (``variant_of``, one per gene symbol that resolves to a store Gene node
  by ``props.symbol``) and ``Variant -> Disease`` (``has_condition``, one per trait xref that
  resolves to a store Disease node: MONDO id directly, OMIM / Orphanet / MedGen (also as UMLS) /
  MeSH through ``props.xrefs``, else a case-insensitive exact name match on label or synonyms).
  Unresolved genes and traits stay in ``props.unlinked_genes`` / ``props.unlinked_conditions``;
  nothing is guessed. If the backbone has not been loaded yet the variants are still written and
  linking is simply redone on the real build.
* Coverage (hard rule 3): one row per gene esearch (hits = ClinVar count), one per gene for the
  excluded CNVs, one per failed esummary range, one per stored variant for the trait linking
  (hits = linked Disease nodes), and two per variant term: stored records carrying the token and a
  live ``<GENE>[gene] AND <term>`` esearch count. A term that ClinVar lacks (e.g. STAT1 N574I) comes
  out as 0 by the same generic rule, not by special-casing.
* A ``sources`` row ``ClinVar`` (version = esummary header ``dbbuild`` if present, else einfo
  ``dbbuild``, else the retrieval date).

HTTP
----
Every call goes through :class:`pipeline.cache.CachedHttp` (``default_http()`` by default so the
NCBI limiter is shared with the PubMed loader) with ``User-Agent: pipeline.config.USER_AGENT``.
Pages are ``esummary`` over the history server (``usehistory=y`` -> ``query_key``/``WebEnv``,
``retstart``/``retmax=page_size``, ``retmode=json``). The documented JSON error payloads
(``{"error": "Too many UIDs ..."}`` and ``{"eutilsresult": {"ERROR": "Input XML size ..."}}``,
docs/source-apis.md section 1c) make the loader halve ``retmax`` for that range, down to
``page_size // 8``; a range that still fails is recorded in coverage with 0 hits and skipped.
With ``ATLAS_OFFLINE=1`` (or ``offline=True``) everything is served from the cache or
:class:`pipeline.cache.CacheMissOffline` is raised.

Dosing (hard rule 7)
--------------------
:func:`scrub_doses` replaces dose amounts and dosing frequencies in every free-text field that is
stored (titles, variation names, aliases, trait names) with ``"[dose removed]"``. ClinVar titles
are HGVS and never carry doses; the scrubber is shared so the rule holds for any text this module
stores.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import time
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from pipeline import config
from pipeline.cache import CachedHttp, CachedResponse, CacheMissOffline, DiskCache, default_http
from pipeline.ids import normalize_curie
from pipeline.store import Store

__all__ = [
    "COVERAGE_ESEARCH",
    "COVERAGE_ESUMMARY",
    "COVERAGE_PROTEIN_CHANGE",
    "COVERAGE_TRAIT_LINK",
    "DOSE_PLACEHOLDER",
    "EINFO_URL",
    "ESEARCH_URL",
    "ESUMMARY_URL",
    "RELATION_HAS_CONDITION",
    "RELATION_VARIANT_OF",
    "SKIPPED_TRAIT_NAMES",
    "SOURCE_LICENSE",
    "SOURCE_NAME",
    "SOURCE_URL",
    "VARIANT_PREFIX",
    "StoreIndex",
    "clinvar_date",
    "keep_in_graph",
    "load",
    "main",
    "parse_esummary_payload",
    "parse_esummary_record",
    "record_url",
    "resolve_trait",
    "scrub_doses",
    "term_matches_token",
    "variant_node_row",
    "variant_terms_by_gene",
]

log = logging.getLogger(__name__)

EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
ESEARCH_URL = EUTILS_BASE + "esearch.fcgi"
ESUMMARY_URL = EUTILS_BASE + "esummary.fcgi"
EINFO_URL = EUTILS_BASE + "einfo.fcgi"
DB = "clinvar"

SOURCE_NAME = "ClinVar"
SOURCE_URL = "https://www.ncbi.nlm.nih.gov/clinvar/"
SOURCE_LICENSE = "Public domain (NCBI); see https://www.ncbi.nlm.nih.gov/home/about/policies/"
RECORD_URL_TEMPLATE = "https://www.ncbi.nlm.nih.gov/clinvar/variation/{uid}/"
VARIANT_PREFIX = "clinvar"

RELATION_VARIANT_OF = "variant_of"
RELATION_HAS_CONDITION = "has_condition"

COVERAGE_ESEARCH = "ClinVar esearch"
COVERAGE_ESUMMARY = "ClinVar esummary"
COVERAGE_PROTEIN_CHANGE = "ClinVar esummary protein_change"
COVERAGE_TRAIT_LINK = "ClinVar trait link"

# Low-information trait names that never describe a disease (docs/source-apis.md section 1c, 8).
SKIPPED_TRAIT_NAMES = frozenset({"not provided", "not specified"})

DEFAULT_PAGE_SIZE = 100
DEFAULT_MAX_GENES = 3
DEFAULT_KEEP_CLASSIFICATIONS = ("Pathogenic", "Likely pathogenic", "Pathogenic/Likely pathogenic")

HEADERS = {"User-Agent": config.USER_AGENT}


# --------------------------------------------------------------------------------------
# dose scrubber (README hard rule 7)
# --------------------------------------------------------------------------------------

DOSE_PLACEHOLDER = "[dose removed]"

_NUM = r"\d+(?:[.,]\d+)*"
_RANGE = rf"{_NUM}(?:\s*(?:-|–|to)\s*{_NUM})?"
# Units that may sit directly after the number ("5mg", "5 mg", "5 MG" as RxNorm-style drug names
# spell it). Multi-letter units are case-insensitive; longest spellings first.
_UNITS_ATTACHED = (
    r"(?i:international\s+units?|milligrams?|micrograms?|millilit(?:er|re)s?|grams?|units?|tablets?|capsules?|"
    r"mcg|µg|μg|ug|mg|ml|iu)"
)
# A bare percentage is a statistic ("comprises about 5% of cases", "response in 40% of patients"),
# not a dose, and stays. A percentage IS a dose when it states the strength of a formulation
# ("0.1% ointment", "2% cream", "0.1% tacrolimus ointment", "5 % w/v solution"): number, %,
# optional w/v / v/v, up to three words (drug or route name), then a formulation word.
_PERCENT_FORMULATION = (
    rf"(?<![A-Za-z0-9.:_/-])(?:{_RANGE})\s*%\s*(?:\(?[wv]/[wv]\)?\s*)?"
    r"(?:[A-Za-z][A-Za-z-]*\s+){0,3}"
    r"(?i:cream|ointment|gel|solution|lotion|drops|spray|foam|shampoo|paste|suspension|emulsion|infusion)\b"
)
# Single-letter units need whitespace before them and stay case-sensitive, so HGVS bases
# ("c.1915-11G>A"), gene names ("IL2RG") and "the 3 G proteins" are never mistaken for a dose.
_UNITS_SPACED = r"g|L|U"
_PER = r"(?i:(?:\s*/\s*(?:kg|m\^?2|m²|day|dose|d|hour|hr|h|week|wk)|\s+per\s+(?:kg|day|dose|m2|week))*)"
_DOSE_AMOUNT = (
    rf"(?<![A-Za-z0-9.:_/-])(?:{_RANGE})(?:\s*(?:{_UNITS_ATTACHED})|\s+(?:{_UNITS_SPACED})){_PER}(?![A-Za-z0-9])"
)
_FREQUENCY = (
    r"(?i:\b(?:once|twice|thrice|one|two|three|four|five|six)\s+(?:times\s+)?"
    r"(?:daily|a\s+day|per\s+day|each\s+day|weekly|a\s+week|per\s+week|monthly|a\s+month|per\s+month)\b)"
    r"|(?i:\bevery\s+(?:\d+|other|two|three|four|six|eight|twelve)\s+(?:hours?|hrs?|days?|weeks?|wks?|months?)\b)"
    r"|\b(?:BID|TID|QID|QD|QOD|QHS|QAM|QPM)\b"
    r"|(?i:\b[bt]\.i\.d\.?)(?![A-Za-z])"
    r"|(?i:\bq\.(?:i\.)?d\.?)(?![A-Za-z])"
    # "q8h" / "q12h" stay lower-case only: an upper-case Q<pos>H is a Gln->His protein-change token.
    r"|\bq\d{1,3}h\b"
)
_DOSE_RE = re.compile(f"{_DOSE_AMOUNT}|{_PERCENT_FORMULATION}|{_FREQUENCY}")
_PLACEHOLDER_RUN = re.compile(rf"{re.escape(DOSE_PLACEHOLDER)}(?:[\s,;/]+{re.escape(DOSE_PLACEHOLDER)})+")


def scrub_doses(text: Any) -> Any:
    """Replace dose amounts (``5 mg``, ``10 mg/kg/day``, ``200 IU``, ``5-10 mg``), formulation
    strengths (``0.1% ointment``, ``2% cream``) and dosing frequencies (``twice daily``, ``every 2
    weeks``, ``BID``, ``q8h``) with ``[dose removed]``. A bare percentage ("about 5% of cases") is a
    statistic, not a dose, and is left alone. Non-string input is returned unchanged. Consecutive
    placeholders collapse into one."""
    if not isinstance(text, str) or not text:
        return text
    out = _DOSE_RE.sub(DOSE_PLACEHOLDER, text)
    if out != text:
        out = _PLACEHOLDER_RUN.sub(DOSE_PLACEHOLDER, out)
    return out


# --------------------------------------------------------------------------------------
# record parsing (field paths from docs/source-apis.md section 1b and the fixture README)
# --------------------------------------------------------------------------------------

_TITLE_HGVS = re.compile(r"^(?P<tx>N[MR]_\d+\.\d+)\((?P<gene>[^)]+)\):(?P<c>c\.[^ ]+)(?: \((?P<p>p\.[^)]+)\))?$")
_P_RESIDUE = re.compile(r"^p\.(?:[A-Z][a-z]{2}|[A-Z*])(?P<pos>\d+)")
_CLINVAR_DATE = re.compile(r"^(?P<y>\d{4})/(?P<m>\d{2})/(?P<d>\d{2})(?: \d{2}:\d{2})?$")
_TOKEN_POSITION = re.compile(r"^(?P<ref>[A-Z*])(?P<pos>\d+)")
_BARE_POSITION_TERM = re.compile(r"^[A-Z*]\d+$")


def record_url(uid: str) -> str:
    return RECORD_URL_TEMPLATE.format(uid=uid)


def clinvar_date(raw: Any) -> str | None:
    """``"2026/08/11 00:00"`` -> ``"2026-08-11"``. The empty sentinel ``"1/01/01 00:00"`` (seen on
    classified records too) and anything else that is not a ``YYYY/MM/DD`` date give ``None``."""
    if not isinstance(raw, str):
        return None
    m = _CLINVAR_DATE.match(raw.strip())
    if m is None:
        return None
    return f"{m['y']}-{m['m']}-{m['d']}"


def _str(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ("" if value is None else str(value))


def _str_list(values: Any) -> list[str]:
    if not isinstance(values, Sequence) or isinstance(values, str):
        return []
    return [_str(v) for v in values if _str(v)]


def _protein_change_tokens(raw: Any) -> list[str]:
    """``"R274W, R241W"`` -> ``["R274W", "R241W"]`` (order kept, empties dropped)."""
    return [tok.strip() for tok in _str(raw).split(",") if tok.strip()]


def _parse_traits(trait_set: Any) -> list[dict[str, Any]]:
    """Deduplicated ``[{trait_name, xrefs: [{db_source, db_id}]}]`` with dose-scrubbed names."""
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, tuple[tuple[str, str], ...]]] = set()
    if not isinstance(trait_set, Sequence):
        return out
    for trait in trait_set:
        if not isinstance(trait, Mapping):
            continue
        name = scrub_doses(_str(trait.get("trait_name")))
        xrefs = [
            {"db_source": _str(x.get("db_source")), "db_id": _str(x.get("db_id"))}
            for x in (trait.get("trait_xrefs") or [])
            if isinstance(x, Mapping) and _str(x.get("db_id"))
        ]
        key = (name.lower(), tuple(sorted((x["db_source"], x["db_id"]) for x in xrefs)))
        if key in seen:
            continue
        seen.add(key)
        out.append({"trait_name": name, "xrefs": xrefs})
    return out


def _parse_classification(block: Any) -> dict[str, Any]:
    """One of the three classification blocks (germline / clinical_impact / oncogenicity): the raw
    rubric fields plus ``present`` (any of description, review_status or traits populated)."""
    block = block if isinstance(block, Mapping) else {}
    description = _str(block.get("description"))
    review_status = _str(block.get("review_status"))
    raw_date = _str(block.get("last_evaluated"))
    traits = _parse_traits(block.get("trait_set"))
    return {
        "description": description,
        "review_status": review_status,
        "last_evaluated": clinvar_date(raw_date),
        "last_evaluated_raw": raw_date,
        "fda_recognized_database": _str(block.get("fda_recognized_database")),
        "traits": traits,
        "present": bool(description or review_status or traits),
    }


def _parse_title(title: str) -> dict[str, str | None]:
    m = _TITLE_HGVS.match(title)
    if m is None:
        return {"hgvs_transcript": None, "hgvs_gene": None, "hgvs_c": None, "hgvs_p": None}
    return {"hgvs_transcript": m["tx"], "hgvs_gene": m["gene"], "hgvs_c": m["c"], "hgvs_p": m["p"]}


def _mane_token(tokens: Sequence[str], hgvs_p: str | None) -> str | None:
    """The protein_change token whose residue number equals the ``p.`` number in the title (the
    MANE isoform's token is not first in the list). None when there is no match."""
    if not hgvs_p:
        return None
    m = _P_RESIDUE.match(hgvs_p)
    if m is None:
        return None
    pos = m["pos"]
    for tok in tokens:
        tm = _TOKEN_POSITION.match(tok)
        if tm is not None and tm["pos"] == pos:
            return tok
    return None


def parse_esummary_record(rec: Mapping[str, Any]) -> dict[str, Any]:
    """Flatten one ``result[uid]`` esummary record into the props this loader stores.

    Everything comes from the response fields named in docs/source-apis.md section 1b; free text
    (title, variation name, aliases, trait names) is dose-scrubbed. Nothing is inferred.
    """
    uid = _str(rec.get("uid"))
    title = scrub_doses(_str(rec.get("title")))
    variation_set = rec.get("variation_set") if isinstance(rec.get("variation_set"), Sequence) else []
    vs0: Mapping[str, Any] = variation_set[0] if variation_set and isinstance(variation_set[0], Mapping) else {}
    genes = [
        {"symbol": _str(g.get("symbol")), "geneid": _str(g.get("geneid")), "strand": _str(g.get("strand"))}
        for g in (rec.get("genes") or [])
        if isinstance(g, Mapping) and _str(g.get("symbol"))
    ]
    gene_symbols = list(dict.fromkeys(g["symbol"] for g in genes))
    tokens = _protein_change_tokens(rec.get("protein_change"))
    title_parts = _parse_title(title)
    germline = _parse_classification(rec.get("germline_classification"))
    clinical_impact = _parse_classification(rec.get("clinical_impact_classification"))
    oncogenicity = _parse_classification(rec.get("oncogenicity_classification"))
    submissions = rec.get("supporting_submissions") if isinstance(rec.get("supporting_submissions"), Mapping) else {}
    scv = _str_list(submissions.get("scv"))
    rcv = _str_list(submissions.get("rcv"))
    locations = [
        {
            "assembly_name": _str(loc.get("assembly_name")),
            "status": _str(loc.get("status")),
            "chr": _str(loc.get("chr")),
            "band": _str(loc.get("band")),
            "start": _str(loc.get("start")),
            "stop": _str(loc.get("stop")),
            "inner_start": _str(loc.get("inner_start")),
            "inner_stop": _str(loc.get("inner_stop")),
            "ref": _str(loc.get("ref")),
            "alt": _str(loc.get("alt")),
        }
        for loc in (vs0.get("variation_loc") or [])
        if isinstance(loc, Mapping)
    ]
    current = next((loc for loc in locations if loc["status"] == "current"), locations[0] if locations else None)
    traits = [dict(t, classification_block="germline") for t in germline["traits"]]
    traits += [dict(t, classification_block="clinical_impact") for t in clinical_impact["traits"]]
    traits += [dict(t, classification_block="oncogenicity") for t in oncogenicity["traits"]]

    return {
        "record_id": uid,
        "uid": uid,
        "source_url": record_url(uid),
        "accession": _str(rec.get("accession")),
        "accession_version": _str(rec.get("accession_version")),
        "title": title,
        "obj_type": _str(rec.get("obj_type")),
        "variation_name": scrub_doses(_str(vs0.get("variation_name"))),
        "cdna_change": _str(vs0.get("cdna_change")),
        "canonical_spdi": _str(vs0.get("canonical_spdi")),
        "variant_type": _str(vs0.get("variant_type")),
        "measure_id": _str(vs0.get("measure_id")),
        "aliases": [scrub_doses(a) for a in _str_list(vs0.get("aliases"))],
        "common_name": scrub_doses(_str(vs0.get("common_name"))),
        "variation_set_size": len(variation_set),
        **title_parts,
        "protein_change": _str(rec.get("protein_change")),
        "protein_change_tokens": tokens,
        "protein_change_mane": _mane_token(tokens, title_parts["hgvs_p"]),
        "molecular_consequence": _str_list(rec.get("molecular_consequence_list")),
        "germline_classification": germline,
        "clinical_impact_classification": clinical_impact,
        "oncogenicity_classification": oncogenicity,
        # Top-level copies of the germline rubric fields so build.py can read them in one place.
        "classification": germline["description"],
        "review_status": germline["review_status"],
        "last_evaluated": germline["last_evaluated"],
        "germline_block_present": germline["present"],
        "has_somatic_block": clinical_impact["present"] or oncogenicity["present"],
        "traits": traits,
        "genes": genes,
        "gene_symbols": gene_symbols,
        "gene_count": len(genes),
        "gene_sort": _str(rec.get("gene_sort")),
        "variation_xrefs": [
            {"db_source": _str(x.get("db_source")), "db_id": _str(x.get("db_id"))}
            for x in (vs0.get("variation_xrefs") or [])
            if isinstance(x, Mapping)
        ],
        "allele_freq_set": [
            {"source": _str(a.get("source")), "value": _str(a.get("value")), "minor_allele": _str(a.get("minor_allele"))}
            for a in (vs0.get("allele_freq_set") or [])
            if isinstance(a, Mapping)
        ],
        "variation_loc": locations,
        "location": current,
        "scv": scv,
        "rcv": rcv,
        "n_scv": len(scv),
        "n_rcv": len(rcv),
        "record_status": _str(rec.get("record_status")),
    }


def parse_esummary_payload(payload: Any) -> tuple[list[dict[str, Any]], dict[str, Any], str | None]:
    """``(records, header, error)`` for a decoded esummary JSON body.

    ``error`` is the documented NCBI failure text when the body is ``{"error": "..."}`` or
    ``{"eutilsresult": {"ERROR": "..."}}`` or lacks ``result``; records are then empty. Per-record
    ``{"uid": ..., "error": ...}`` stubs are dropped from ``records``.
    """
    if not isinstance(payload, Mapping):
        return [], {}, "esummary body is not a JSON object"
    header = payload.get("header") if isinstance(payload.get("header"), Mapping) else {}
    if isinstance(payload.get("error"), str):
        return [], dict(header), payload["error"]
    eutils = payload.get("eutilsresult")
    if isinstance(eutils, Mapping) and eutils.get("ERROR"):
        return [], dict(header), str(eutils["ERROR"])
    result = payload.get("result")
    if not isinstance(result, Mapping):
        return [], dict(header), "esummary body has no result"
    uids = result.get("uids")
    if not isinstance(uids, Sequence) or isinstance(uids, str):
        uids = [k for k in result if k != "uids"]
    records = [result[str(u)] for u in uids if isinstance(result.get(str(u)), Mapping)]
    return records, dict(header), None


# --------------------------------------------------------------------------------------
# seeds: variant terms and keep rule
# --------------------------------------------------------------------------------------


def variant_terms_by_gene(seeds: Mapping[str, Any]) -> dict[str, list[str]]:
    """``seeds["variant_terms"]`` as ``{SYMBOL: [terms]}``. A plain list (no gene) is keyed by
    ``"*"`` and applies to every record."""
    raw = seeds.get("variant_terms") or {}
    out: dict[str, list[str]] = {}
    if isinstance(raw, Mapping):
        for gene, terms in raw.items():
            out[_str(gene).upper()] = [_str(t).upper() for t in (terms or []) if _str(t)]
    elif isinstance(raw, Sequence) and not isinstance(raw, str):
        out["*"] = [_str(t).upper() for t in raw if _str(t)]
    return out


def term_matches_token(token: str, term: str) -> bool:
    """Exact match on a one-letter protein-change token, or, for a bare position term (``R321``),
    any token at that position (``R321G``, ``R321*``) but not another position (``R3210X``)."""
    token = _str(token).upper()
    term = _str(term).upper()
    if not token or not term:
        return False
    if token == term:
        return True
    if _BARE_POSITION_TERM.match(term) and token.startswith(term):
        rest = token[len(term) :]
        return not rest[:1].isdigit()
    return False


def _terms_for_record(parsed: Mapping[str, Any], terms_by_gene: Mapping[str, Sequence[str]]) -> list[str]:
    """Variant terms whose gene is named by the record (or ``*``) that match a protein_change token."""
    symbols = {s.upper() for s in parsed.get("gene_symbols", [])}
    matched: list[str] = []
    for gene, terms in terms_by_gene.items():
        if gene != "*" and gene not in symbols:
            continue
        for term in terms:
            if any(term_matches_token(tok, term) for tok in parsed.get("protein_change_tokens", [])):
                tag = term if gene == "*" else f"{gene}:{term}"
                if tag not in matched:
                    matched.append(tag)
    return matched


def keep_in_graph(
    parsed: Mapping[str, Any], seeds: Mapping[str, Any]
) -> tuple[bool, list[str], list[str]]:
    """``(keep, reasons, matched_terms)``: keep when the germline classification is one of
    ``keep_classifications`` or any protein_change token matches a seed variant term."""
    keep_classes = {_str(c) for c in (seeds.get("keep_classifications") or DEFAULT_KEEP_CLASSIFICATIONS)}
    reasons: list[str] = []
    if parsed.get("classification") in keep_classes:
        reasons.append(f"classification:{parsed['classification']}")
    matched = _terms_for_record(parsed, variant_terms_by_gene(seeds))
    reasons.extend(f"variant_term:{t}" for t in matched)
    return bool(reasons), reasons, matched


# --------------------------------------------------------------------------------------
# linking to store nodes
# --------------------------------------------------------------------------------------


def _xref_key(xref: str) -> tuple[str, str] | None:
    """``"OMIM:<n>"`` -> ``("OMIM", "<n>")``; the backbone's ``"MONDO_<n>"`` spelling -> ``("MONDO", "<n>")``."""
    if not isinstance(xref, str) or not xref.strip():
        return None
    try:
        curie = normalize_curie(xref)
    except ValueError:
        return None
    prefix, sep, local = curie.partition(":")
    if not sep or not local:
        return None
    return prefix.upper(), local


class StoreIndex:
    """Lookup tables over the Gene and Disease nodes already in the store (built once per run)."""

    def __init__(self, store: Store) -> None:
        self.gene_by_symbol: dict[str, str] = {}
        self.disease_ids: set[str] = set()
        self.disease_by_xref: dict[tuple[str, str], list[str]] = {}
        self.disease_by_name: dict[str, list[str]] = {}
        for node in store.iter_nodes("Gene"):
            symbol = _str((node.get("props") or {}).get("symbol")) or _str(node.get("label"))
            if symbol:
                self.gene_by_symbol.setdefault(symbol.upper(), node["id"])
        for node in store.iter_nodes("Disease"):
            node_id = node["id"]
            self.disease_ids.add(node_id)
            props = node.get("props") or {}
            for xref in props.get("xrefs") or []:
                key = _xref_key(xref)
                if key is not None:
                    self._add(self.disease_by_xref, key, node_id)
            for name in [node.get("label"), *(node.get("synonyms") or [])]:
                if isinstance(name, str) and name.strip():
                    self._add(self.disease_by_name, name.strip().lower(), node_id)

    @staticmethod
    def _add(table: dict[Any, list[str]], key: Any, node_id: str) -> None:
        ids = table.setdefault(key, [])
        if node_id not in ids:
            ids.append(node_id)

    @property
    def has_backbone(self) -> bool:
        return bool(self.gene_by_symbol or self.disease_ids)


def _candidate_ids(index: StoreIndex, db_source: str, db_id: str) -> list[tuple[str, str]]:
    """``[(disease_id, method)]`` for one trait xref, in priority order."""
    out: list[tuple[str, str]] = []
    src = db_source.strip().lower()
    if src == "mondo":
        try:
            curie = normalize_curie(db_id, "MONDO")
        except ValueError:
            return out
        if curie in index.disease_ids:
            out.append((curie, "mondo_id"))
        key = _xref_key(curie)
        if key is not None:
            out.extend((i, "xref:MONDO") for i in index.disease_by_xref.get(key, []))
    elif src == "omim":
        out.extend((i, "xref:OMIM") for i in index.disease_by_xref.get(("OMIM", db_id), []))
    elif src == "orphanet":
        curie = f"Orphanet:{db_id}"
        if curie in index.disease_ids:
            out.append((curie, "orphanet_id"))
        out.extend((i, "xref:Orphanet") for i in index.disease_by_xref.get(("ORPHANET", db_id), []))
    elif src == "medgen":
        out.extend((i, "xref:MedGen") for i in index.disease_by_xref.get(("MEDGEN", db_id), []))
        out.extend((i, "xref:UMLS") for i in index.disease_by_xref.get(("UMLS", db_id), []))
    elif src == "mesh":
        out.extend((i, "xref:MeSH") for i in index.disease_by_xref.get(("MESH", db_id), []))
    # Human Phenotype Ontology xrefs are phenotypes, not diseases: never linked here.
    return out


def resolve_trait(trait: Mapping[str, Any], index: StoreIndex) -> list[dict[str, str]]:
    """Disease nodes a trait links to: ``[{disease_id, method, matched_text}]`` (deduplicated).
    Xrefs first (MONDO, OMIM, Orphanet, MedGen/UMLS, MeSH); a case-insensitive exact name match on
    label or synonyms only when no xref resolved. Empty when nothing matches (never a guess)."""
    hits: list[dict[str, str]] = []
    seen: set[str] = set()
    for xref in trait.get("xrefs") or []:
        for disease_id, method in _candidate_ids(index, xref.get("db_source", ""), xref.get("db_id", "")):
            if disease_id not in seen:
                seen.add(disease_id)
                hits.append({"disease_id": disease_id, "method": method, "matched_text": f"{xref['db_source']}:{xref['db_id']}"})
    if hits:
        return hits
    name = _str(trait.get("trait_name"))
    for disease_id in index.disease_by_name.get(name.lower(), []):
        if disease_id not in seen:
            seen.add(disease_id)
            hits.append({"disease_id": disease_id, "method": "name", "matched_text": name})
    return hits


# --------------------------------------------------------------------------------------
# node / edge rows
# --------------------------------------------------------------------------------------


def _dedupe(values: Iterable[Any]) -> list[str]:
    out: list[str] = []
    for v in values:
        if isinstance(v, str) and v.strip() and v not in out:
            out.append(v)
    return out


def variant_node_row(parsed: Mapping[str, Any], retrieved_at: str, extra_props: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """The store node row for a parsed record (ids normalised, label = title, synonyms as in the
    module docstring)."""
    node_id = normalize_curie(parsed["uid"], VARIANT_PREFIX)
    label = parsed["title"] or parsed["variation_name"] or parsed["accession"] or node_id
    gene_for_token = parsed.get("hgvs_gene") or (parsed["gene_symbols"][0] if len(parsed["gene_symbols"]) == 1 else None)
    mane = parsed.get("protein_change_mane")
    synonyms = _dedupe(
        [
            *parsed["protein_change_tokens"],
            parsed["cdna_change"],
            parsed["accession"],
            parsed["accession_version"],
            parsed.get("hgvs_p"),
            f"{gene_for_token} {mane}" if gene_for_token and mane else None,
            *parsed["aliases"],
            parsed["variation_name"],
        ]
    )
    synonyms = [s for s in synonyms if s != label]
    props = dict(parsed)
    props.update(extra_props or {})
    return {
        "id": node_id,
        "type": "Variant",
        "label": label,
        "synonyms": synonyms,
        "props": props,
        "source": SOURCE_NAME,
        "retrieved_at": retrieved_at,
    }


# --------------------------------------------------------------------------------------
# HTTP helpers
# --------------------------------------------------------------------------------------


@dataclass
class _Esearch:
    query: str
    count: int
    webenv: str
    query_key: str
    retrieved_at: str
    date: str
    error: str | None = None


@dataclass
class _Run:
    """Per-run bookkeeping (request counts, failures, latest timestamps)."""

    requests: int = 0
    from_cache: int = 0
    latest_retrieved_at: str = ""
    header_dbbuild: str | None = None
    page_failures: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def saw(self, resp: CachedResponse) -> CachedResponse:
        self.requests += 1
        if resp.from_cache:
            self.from_cache += 1
        if resp.retrieved_at > self.latest_retrieved_at:
            self.latest_retrieved_at = resp.retrieved_at
        return resp


def _http_for(http: CachedHttp | None, offline: bool) -> CachedHttp:
    """``http`` (or the process-wide client), forced offline when asked without mutating the
    caller's object: a new client over the same cache root, limiter and API key."""
    client = http if http is not None else default_http()
    if not offline or client.cache.is_offline():
        return client
    return CachedHttp(
        cache=DiskCache(client.cache.root, offline=True),
        namespace=client.namespace,
        limiter=client.limiter,
        ncbi_api_key=client.ncbi_api_key,
        headers=HEADERS,
    )


def _esearch(http: CachedHttp, run: _Run, term: str, *, history: bool) -> _Esearch:
    params: dict[str, str] = {"db": DB, "term": term, "retmax": "0", "retmode": "json"}
    if history:
        params["usehistory"] = "y"
    resp = run.saw(http.get_json(ESEARCH_URL, params, headers=HEADERS))
    result: Mapping[str, Any] = {}
    error: str | None = None
    if not resp.ok:
        error = f"HTTP {resp.status}"
    else:
        try:
            payload = resp.json()
        except ValueError:
            payload, error = {}, "esearch body is not JSON"
        if isinstance(payload, Mapping):
            if isinstance(payload.get("error"), str):
                error = payload["error"]
            elif isinstance(payload.get("esearchresult"), Mapping):
                result = payload["esearchresult"]
                if result.get("ERROR"):
                    error = str(result["ERROR"])
            elif error is None:
                error = "esearch body has no esearchresult"
    try:
        count = int(_str(result.get("count")) or 0)
    except ValueError:
        count = 0
    if error:
        run.errors.append(f"esearch {term!r}: {error}")
        log.warning("ClinVar esearch %r failed: %s", term, error)
    return _Esearch(
        query=term,
        count=count,
        webenv=_str(result.get("webenv")),
        query_key=_str(result.get("querykey")),
        retrieved_at=resp.retrieved_at,
        date=resp.retrieved_date,
        error=error,
    )


def _esummary_history(http: CachedHttp, run: _Run, es: _Esearch, retstart: int, retmax: int) -> CachedResponse:
    params = {
        "db": DB,
        "query_key": es.query_key,
        "WebEnv": es.webenv,
        "retstart": str(retstart),
        "retmax": str(retmax),
        "retmode": "json",
    }
    return run.saw(http.get_json(ESUMMARY_URL, params, headers=HEADERS))


def _iter_pages(
    http: CachedHttp, run: _Run, es: _Esearch, page_size: int, on_failure: Callable[[int, int, str], None]
) -> Iterator[tuple[list[dict[str, Any]], CachedResponse]]:
    """esummary pages for ``es`` as ``(records, response)``. A page that returns an NCBI error
    payload is split in two (down to ``page_size // 8``); a range that still fails goes to
    ``on_failure(retstart, retmax, error)`` and is skipped."""
    min_size = max(1, page_size // 8)

    def fetch(retstart: int, retmax: int) -> list[tuple[list[dict[str, Any]], CachedResponse]]:
        resp = _esummary_history(http, run, es, retstart, retmax)
        error: str | None
        if not resp.ok:
            records, header, error = [], {}, f"HTTP {resp.status}"
        else:
            try:
                records, header, error = parse_esummary_payload(resp.json())
            except ValueError:
                records, header, error = [], {}, "esummary body is not JSON"
        if error is None:
            dbbuild = _str(header.get("dbbuild"))
            if dbbuild and not run.header_dbbuild:
                run.header_dbbuild = dbbuild
            return [(records, resp)]
        if retmax > min_size:
            half = retmax // 2
            log.info("ClinVar esummary %s retstart=%d retmax=%d failed (%s); retrying as two halves", es.query, retstart, retmax, error)
            return fetch(retstart, half) + fetch(retstart + half, retmax - half)
        log.warning("ClinVar esummary %s retstart=%d retmax=%d failed at minimum page size: %s", es.query, retstart, retmax, error)
        on_failure(retstart, retmax, error)
        return []

    for retstart in range(0, es.count, page_size):
        yield from fetch(retstart, min(page_size, es.count - retstart))


def _einfo(http: CachedHttp, run: _Run) -> dict[str, str]:
    """``{dbbuild, lastupdate, count}`` from einfo (one cached call); empty on any failure."""
    try:
        resp = run.saw(http.get_json(EINFO_URL, {"db": DB, "retmode": "json"}, headers=HEADERS))
        if not resp.ok:
            return {}
        payload = resp.json()
        info = payload["einforesult"]["dbinfo"][0]
        return {k: _str(info.get(k)) for k in ("dbbuild", "lastupdate", "count") if _str(info.get(k))}
    except CacheMissOffline:
        raise
    except Exception as exc:  # noqa: BLE001 - version metadata is best effort, never fatal
        run.errors.append(f"einfo: {exc}")
        return {}


# --------------------------------------------------------------------------------------
# load
# --------------------------------------------------------------------------------------


def load(
    store: Store,
    seeds: Mapping[str, Any],
    cluster_seeds: Mapping[str, Any],
    *,
    http: CachedHttp | None = None,
    offline: bool = False,
) -> dict[str, Any]:
    """Load ClinVar variants for ``cluster_seeds["genes"]`` into ``store`` and return a report.

    ``seeds`` is ``pipeline/seeds/clinvar.yaml`` (variant_terms, max_genes_per_record, page_size,
    keep_classifications); ``cluster_seeds`` is ``pipeline/seeds/cluster.yaml``. ``http`` defaults
    to the process-wide :func:`pipeline.cache.default_http`; ``offline=True`` serves everything
    from the cache (a miss raises :class:`pipeline.cache.CacheMissOffline`), as does
    ``ATLAS_OFFLINE=1``. Idempotent: every write is an upsert.
    """
    t0 = time.monotonic()
    client = _http_for(http, offline)
    run = _Run()
    genes = _dedupe(_str(g) for g in (cluster_seeds.get("genes") or []))
    page_size = max(1, int(seeds.get("page_size") or DEFAULT_PAGE_SIZE))
    max_genes = int(seeds.get("max_genes_per_record") or DEFAULT_MAX_GENES)
    terms_by_gene = variant_terms_by_gene(seeds)
    index = StoreIndex(store)

    # uid -> {"parsed", "retrieved_at", "matched_seed_genes"}
    records: dict[str, dict[str, Any]] = {}
    by_gene: dict[str, dict[str, Any]] = {}
    gene_entity: dict[str, str] = {}

    for symbol in genes:
        entity = index.gene_by_symbol.get(symbol.upper()) or f"seed:gene:{symbol}"
        gene_entity[symbol] = entity
        es = _esearch(client, run, f"{symbol}[gene]", history=True)
        store.add_coverage(entity, COVERAGE_ESEARCH, es.query, es.date, es.count)
        stats = by_gene[symbol] = {
            "esearch_count": es.count,
            "records": 0,
            "excluded_cnv": 0,
            "record_errors": 0,
            "kept_in_graph": 0,
            "page_failures": 0,
            "esearch_error": es.error,
        }
        if es.error or not es.webenv or not es.query_key:
            if es.count:
                run.errors.append(f"esearch {es.query!r}: no history keys returned")
            continue

        def on_failure(retstart: int, retmax: int, error: str, *, _symbol: str = symbol, _entity: str = entity, _es: _Esearch = es) -> None:
            by_gene[_symbol]["page_failures"] += 1
            run.page_failures.append({"gene": _symbol, "retstart": retstart, "retmax": retmax, "error": error})
            store.add_coverage(
                _entity,
                COVERAGE_ESUMMARY,
                f"{_es.query} esummary retstart={retstart} retmax={retmax} failed: {error}",
                _es.date,
                0,
            )

        for page_records, resp in _iter_pages(client, run, es, page_size, on_failure):
            for rec in page_records:
                if "error" in rec or not _str(rec.get("uid")):
                    stats["record_errors"] += 1
                    continue
                parsed = parse_esummary_record(rec)
                if parsed["gene_count"] > max_genes:
                    stats["excluded_cnv"] += 1
                    continue
                stats["records"] += 1
                entry = records.get(parsed["uid"])
                if entry is None:
                    entry = records[parsed["uid"]] = {"parsed": parsed, "retrieved_at": resp.retrieved_at, "matched_seed_genes": []}
                if symbol not in entry["matched_seed_genes"]:
                    entry["matched_seed_genes"].append(symbol)
        store.add_coverage(
            entity,
            COVERAGE_ESUMMARY,
            f"{es.query} excluded_cnv: records naming more than {max_genes} genes",
            es.date,
            stats["excluded_cnv"],
        )
        log.info(
            "ClinVar %s: count=%d stored=%d excluded_cnv=%d page_failures=%d",
            symbol, es.count, stats["records"], stats["excluded_cnv"], stats["page_failures"],
        )

    # -- nodes and edges -------------------------------------------------------------
    node_rows: list[dict[str, Any]] = []
    edge_rows: dict[tuple[str, str, str], dict[str, Any]] = {}
    totals = {"kept_in_graph": 0, "variant_of_edges": 0, "has_condition_edges": 0, "unlinked_genes": 0,
              "unlinked_conditions": 0, "skipped_traits": 0, "variants_with_linked_condition": 0}
    term_hits: dict[str, int] = {f"{g}:{t}" if g != "*" else t: 0 for g, ts in terms_by_gene.items() for t in ts}

    for uid in sorted(records, key=lambda u: int(u) if u.isdigit() else 0):
        entry = records[uid]
        parsed = entry["parsed"]
        keep, reasons, matched_terms = keep_in_graph(parsed, seeds)
        for tag in matched_terms:
            term_hits[tag] = term_hits.get(tag, 0) + 1
        if keep:
            totals["kept_in_graph"] += 1
            for symbol in entry["matched_seed_genes"]:
                by_gene[symbol]["kept_in_graph"] += 1

        # Variant -> Gene
        linked_genes: list[dict[str, str]] = []
        unlinked_genes: list[str] = []
        for gene in parsed["genes"]:
            gene_id = index.gene_by_symbol.get(gene["symbol"].upper())
            if gene_id is None:
                unlinked_genes.append(gene["symbol"])
                continue
            linked_genes.append({"gene_id": gene_id, "symbol": gene["symbol"]})
        totals["unlinked_genes"] += len(unlinked_genes)

        # Variant -> Disease
        linked_conditions: list[dict[str, Any]] = []
        unlinked_conditions: list[dict[str, Any]] = []
        skipped_traits: list[str] = []
        for trait in parsed["traits"]:
            if trait["trait_name"].lower() in SKIPPED_TRAIT_NAMES:
                skipped_traits.append(trait["trait_name"])
                continue
            hits = resolve_trait(trait, index)
            if not hits:
                unlinked_conditions.append(trait)
                continue
            for hit in hits:
                linked_conditions.append({**hit, "trait_name": trait["trait_name"], "classification_block": trait["classification_block"]})
        totals["unlinked_conditions"] += len(unlinked_conditions)
        totals["skipped_traits"] += len(skipped_traits)

        node_id = normalize_curie(uid, VARIANT_PREFIX)
        node_rows.append(
            variant_node_row(
                parsed,
                entry["retrieved_at"],
                {
                    "keep_in_graph": keep,
                    "keep_reasons": reasons,
                    "matched_variant_terms": matched_terms,
                    "matched_seed_genes": entry["matched_seed_genes"],
                    "linked_genes": linked_genes,
                    "unlinked_genes": unlinked_genes,
                    "linked_conditions": linked_conditions,
                    "unlinked_conditions": unlinked_conditions,
                    "skipped_traits": skipped_traits,
                },
            )
        )

        for g in linked_genes:
            gene_meta = next((x for x in parsed["genes"] if x["symbol"] == g["symbol"]), {})
            key = (node_id, g["gene_id"], RELATION_VARIANT_OF)
            edge_rows[key] = {
                "source": node_id,
                "target": g["gene_id"],
                "relation": RELATION_VARIANT_OF,
                "source_db": SOURCE_NAME,
                "props": {
                    "evidence_class": "observed",
                    "gene_symbol": g["symbol"],
                    "geneid": gene_meta.get("geneid", ""),
                    "record_id": uid,
                    "match": {"method": "props.symbol", "matched_text": g["symbol"]},
                },
                "retrieved_at": entry["retrieved_at"],
            }
        for cond in linked_conditions:
            block = parsed[f"{cond['classification_block']}_classification"]
            key = (node_id, cond["disease_id"], RELATION_HAS_CONDITION)
            existing = edge_rows.get(key)
            if existing is not None:
                names = existing["props"].setdefault("trait_names", [existing["props"]["trait_name"]])
                if cond["trait_name"] not in names:
                    names.append(cond["trait_name"])
                continue
            trait = next(t for t in parsed["traits"] if t["trait_name"] == cond["trait_name"] and t["classification_block"] == cond["classification_block"])
            edge_rows[key] = {
                "source": node_id,
                "target": cond["disease_id"],
                "relation": RELATION_HAS_CONDITION,
                "source_db": SOURCE_NAME,
                "props": {
                    "evidence_class": "observed",
                    "record_id": uid,
                    "trait_name": cond["trait_name"],
                    "xrefs": trait["xrefs"],
                    "classification_block": cond["classification_block"],
                    "classification": block["description"],
                    "review_status": block["review_status"],
                    "last_evaluated": block["last_evaluated"],
                    "n_scv": parsed["n_scv"],
                    "n_rcv": parsed["n_rcv"],
                    "match": {"method": cond["method"], "matched_text": cond["matched_text"]},
                },
                "retrieved_at": entry["retrieved_at"],
            }
        n_linked = len({c["disease_id"] for c in linked_conditions})
        if n_linked:
            totals["variants_with_linked_condition"] += 1
        store.add_coverage(
            node_id,
            COVERAGE_TRAIT_LINK,
            "trait_xrefs -> Disease node (MONDO id / OMIM, Orphanet, MedGen, MeSH xref / exact name)",
            entry["retrieved_at"][:10],
            n_linked,
        )

    store.upsert_nodes(node_rows)
    store.upsert_edges(edge_rows.values())
    totals["variant_of_edges"] = sum(1 for k in edge_rows if k[2] == RELATION_VARIANT_OF)
    totals["has_condition_edges"] = sum(1 for k in edge_rows if k[2] == RELATION_HAS_CONDITION)

    # -- variant-term coverage: stored token counts and a live esearch per term ----------
    einfo = _einfo(client, run)
    retrieval_date = (run.latest_retrieved_at or DiskCache().timestamp())[:10]
    variant_terms_report: dict[str, dict[str, Any]] = {}
    for gene, terms in terms_by_gene.items():
        for term in terms:
            tag = term if gene == "*" else f"{gene}:{term}"
            entity = f"seed:variant:{tag}"
            stored = term_hits.get(tag, 0)
            scope = "" if gene == "*" else f"{gene}[gene] "
            store.add_coverage(entity, COVERAGE_PROTEIN_CHANGE, f"{scope}protein_change token {term}", retrieval_date, stored)
            live_term = term if gene == "*" else f"{gene}[gene] AND {term}"
            es = _esearch(client, run, live_term, history=False)
            store.add_coverage(entity, COVERAGE_ESEARCH, es.query, es.date, es.count)
            variant_terms_report[tag] = {"stored_hits": stored, "live_esearch_hits": es.count, "live_query": es.query}

    # -- sources row ----------------------------------------------------------------
    version = run.header_dbbuild or einfo.get("dbbuild") or retrieval_date
    retrieved_at = run.latest_retrieved_at or DiskCache().timestamp()
    store.upsert_source(
        SOURCE_NAME,
        version=version,
        retrieved_at=retrieved_at,
        license=SOURCE_LICENSE,
        url=SOURCE_URL,
        notes={
            "einfo": einfo,
            "genes": genes,
            "records": len(records),
            "kept_in_graph": totals["kept_in_graph"],
            "excluded_cnv": sum(s["excluded_cnv"] for s in by_gene.values()),
            "page_size": page_size,
            "max_genes_per_record": max_genes,
        },
    )

    report = {
        "source": SOURCE_NAME,
        "version": version,
        "retrieved_at": retrieved_at,
        "genes": genes,
        "records": len(records),
        "records_by_gene_sum": sum(s["records"] for s in by_gene.values()),
        "excluded_cnv": sum(s["excluded_cnv"] for s in by_gene.values()),
        **totals,
        "by_gene": by_gene,
        "variant_terms": variant_terms_report,
        "page_failures": run.page_failures,
        "errors": run.errors,
        "requests": run.requests,
        "requests_from_cache": run.from_cache,
        "backbone_present": index.has_backbone,
        "elapsed_s": round(time.monotonic() - t0, 2),
    }
    log.info("ClinVar load done: %d records, %d kept, %d requests (%d cached) in %.1fs",
             report["records"], report["kept_in_graph"], run.requests, run.from_cache, report["elapsed_s"])
    return report


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Load ClinVar variants for the cluster genes into the evidence store.")
    parser.add_argument("--store", default=str(config.STORE_PATH), help="SQLite store path (default: %(default)s)")
    parser.add_argument("--offline", action="store_true", help="serve everything from the cache; a miss is an error")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    seeds = config.load_seeds("clinvar")
    cluster = config.load_seeds("cluster")
    with Store(args.store) as store:
        report = load(store, seeds, cluster, offline=args.offline)
    print(json.dumps(report, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
