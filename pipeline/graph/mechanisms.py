"""Mechanism derivation: Disease -> Mechanism (and Variant -> Mechanism) edges from general rules.

A Mechanism node is ``mech:<GENE>:<GoF|LoF|DN>[:somatic]`` (README section 4). This module decides,
for every Disease node in the evidence store that has at least one ``associated_with`` gene, which
mechanism(s) the disease carries and with what evidence. Nothing here names a gene or a disease:
every rule is a regex over the disease's own text, a literature claim, or a row of the curated
seed file, and every decision is written into ``confidence_basis`` so the UI can show *why*.

Rules
-----
R1  Direction from the disease text (label + synonyms + description), :data:`RULES`:
    ``R1.gof`` gain of function / GOF / activating mutation / constitutive(ly) activ...;
    ``R1.lof`` loss of function / LOF / null mutation / hypomorph...; ``R1.lof.deficiency``
    "<something> deficiency|deficient"; ``R1.dn`` dominant negative. The name fields (label and
    synonyms) decide when they match exactly one direction; the description decides only when the
    name fields match nothing; two directions in the deciding fields is a conflict and nothing is
    assigned (coverage row). Confidence 0.6 for a match in one field group, 0.7 when both the name
    fields and the description agree.
    The gene: for ``R1.lof.deficiency`` the words before "deficiency" (a symbol such as
    "<GENE> deficiency", or a protein name such as "<chain name> deficiency") name a gene whose
    symbol, name or synonym contains that phrase; otherwise the gene whose
    symbol, name or synonym appears (word-bounded, case-insensitive) in the disease text, the name
    fields taking precedence over the description. A gene named in the text counts at any
    association score. When no gene is named and exactly one associated gene passes the seed
    threshold, that gene is taken; several candidates and none named is ambiguous: no edge, and a
    coverage row ``mechanism: ambiguous gene for <disease>``.
R2  Origin from the same text: somatic / acquired / clonal -> ``somatic``; autosomal dominant or
    recessive / X-linked / germline / familial / hereditary / inherited / congenital -> ``germline``;
    decided like R1; neither (or a conflict) -> unknown, ``attributes.origin`` omitted and a
    coverage row recorded.
R3  Literature claims (``data/build/claims.json``, :class:`pipeline.models.ClaimsBundle`) with
    predicate ``has_mechanism`` whose subject is a Disease or Variant node in the store and whose
    object is a mechanism id add or upgrade the edge to ``evidence_class: extracted`` with the
    claim confidence rubric and supporting / contradicting lists. When the upgraded edge was a
    curated R4 edge, :func:`strip_curated_provenance` removes what only the seed row asserted
    (``attributes.reason``, a seed-only ``attributes.origin``, the seed record in ``sources``,
    kept under ``confidence_basis.db_text.seed_record``) so nothing curated surfaces as sourced
    (hard rule 10); :mod:`pipeline.graph.evidence` applies the same helper on its own upgrade path.
    A claim-derived edge (created here, or a curated edge upgraded here) whose supporting claims
    are all ``in_vitro`` / ``review`` is labelled ``in_vitro_only`` and capped at 0.5
    (:func:`pipeline.graph.confidence.apply_in_vitro_only`); an R1 edge the claims merely enrich
    keeps its database support and is not capped.
R4  Curated fallback (``pipeline/seeds/mechanisms.yaml``), only for a (disease, gene) pair that
    R1-R3 left without a mechanism and only when the store associates that gene with that disease:
    ``evidence_class: inferred``, confidence 0.4, ``attributes.labels ["curated_unverified"]``,
    ``attributes.reason "curated seed from cluster doc (<tag>)"``. The asserting source of such an
    edge is the seed file (``sources[0]`` = ``"<seed source> <GENE>:<DIR>"`` dated
    ``transcribed_at``), the disease record it attaches to is listed second. The seed supplies the
    direction; the ORIGIN is read from the disease's own text first (R2, recorded as
    ``origin_rule`` / ``origin_source: "disease text"``) and taken from the seed row only when the
    text decides nothing (``origin_source: "seed row"``). A seed
    ``disease_terms`` entry is a name or a mapping ``{term, from}``; ``from`` (where the wording
    was seen) is copied into ``confidence_basis.disease_term_from``. When R1 already assigned the
    same gene a different direction the curated row is NOT applied and a coverage row says so.

Origin propagation: ``MechanismResult.origins`` (what the build copies onto the OBSERVED
``associated_with`` edge as ``attributes.origin``) holds only origins stated by the database text
(R1/R2) or by a claim (R3); a curated row's origin stays on its own inferred, labelled edge
(hard rule 10: Background facts never surface as sourced).

Every disease the rules leave without a mechanism gets a coverage row (source ``"mechanism
rules"``, hits 0) so the gap is visible in ``meta.coverage`` (hard rule 3). Disease-level rows are
dated with the disease record's ``retrieved_at`` (the date of the text that was searched); rows
keyed by a seed term (``seed:mechanism:...``) carry the build day.

Regex guards: every token is word-bounded (``\\bclonal\\b`` must not match "monoclonal",
``(?<![a-z])activating`` must not match "inactivating", ``\\bdeficien...`` must not match
"immunodeficiency"), which is why the patterns below are slightly stricter than the README prose.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from pipeline.graph import confidence as conf_rubric
from pipeline.ids import MECHANISM_DIRECTIONS, mechanism_id, parse_mechanism_id
from pipeline.models import NodeType, Polarity

__all__ = [
    "CLAIMS_SOURCE",
    "CURATED_CONFIDENCE",
    "CURATED_LABEL",
    "CURATED_RULE",
    "DIRECTION_LABEL",
    "MechanismResult",
    "REL_ASSOCIATED_WITH",
    "REL_HAS_MECHANISM",
    "RULES",
    "RULE_SOURCE",
    "TextRule",
    "association_passes",
    "derive",
    "derive_mechanisms",
    "mechanism_label",
    "resolve_terms",
    "seed_source_record",
    "strip_curated_provenance",
]

REL_ASSOCIATED_WITH = "associated_with"
REL_HAS_MECHANISM = "has_mechanism"
RULE_SOURCE = "mechanism rules"  # coverage source and source_db of R1/R2/R4 edges
CLAIMS_SOURCE = "claims"  # source_db of edges that exist only because of R3
CURATED_CONFIDENCE = 0.4
CURATED_LABEL = "curated_unverified"
CURATED_RULE = "R4.curated"  # confidence_basis.rule of a curated fallback edge
CURATED_SEED_FALLBACK_NAME = "curated mechanism seeds"  # seed record name when the seed file has no ``source``
TEXT_RULE_CONFIDENCE_ONE_FIELD = 0.6
TEXT_RULE_CONFIDENCE_BOTH_FIELDS = 0.7
VARIANT_SPECIFIC_LABEL = "variant_specific"
PREDICATE_HAS_MECHANISM = "has_mechanism"

DIRECTION_LABEL: dict[str, str] = {"GoF": "gain of function", "LoF": "loss of function", "DN": "dominant negative"}
NAME_FIELDS = ("label", "synonyms")
DESCRIPTION_FIELD = "description"
DEFAULT_THRESHOLDS: dict[str, Any] = {"disease_gene_min_score": 0.3, "keep_disgenet_direct": True}
DISGENET = "DISGENET"


@dataclass(frozen=True)
class TextRule:
    """One rule of the module-level table: ``kind`` is ``direction`` or ``origin``."""

    id: str
    kind: str
    value: str
    pattern: re.Pattern[str]


RULES: tuple[TextRule, ...] = (
    TextRule(
        "R1.gof",
        "direction",
        "GoF",
        re.compile(r"gain[- ]of[- ]function|\bGOF\b|(?<![a-z])activating mutation|\bconstitutive(?:ly)? activ", re.I),
    ),
    TextRule("R1.lof", "direction", "LoF", re.compile(r"loss[- ]of[- ]function|\bLOF\b|\bnull mutation|\bhypomorph", re.I)),
    TextRule("R1.lof.deficiency", "direction", "LoF", re.compile(r"\bdeficien(?:cy|t)\b", re.I)),
    TextRule("R1.dn", "direction", "DN", re.compile(r"dominant[- ]negative", re.I)),
    TextRule("R2.somatic", "origin", "somatic", re.compile(r"\bsomatic\b|\bacquired\b|\bclonal\b", re.I)),
    TextRule(
        "R2.germline",
        "origin",
        "germline",
        re.compile(r"\bautosomal (?:dominant|recessive)\b|\bX-linked\b|\bgermline\b|\bfamilial\b|\bhereditary\b|\binherited\b|\bcongenital\b", re.I),
    ),
)

# Words that end the "<entity> deficiency" phrase, and qualifiers skipped inside it.
_PHRASE_STOP = frozenset({"to", "of", "due", "with", "and", "or", "a", "an", "the", "in", "by", "from", "for", "type", "syndrome", "disease"})
_PHRASE_SKIP = frozenset({"partial", "complete", "total", "severe", "profound", "mild", "isolated", "primary", "secondary"})
_PHRASE_MAX_TOKENS = 3
_MIN_NAME_LEN = 3


# --------------------------------------------------------------------------------------
# small data holders
# --------------------------------------------------------------------------------------


@dataclass
class _Gene:
    id: str
    symbol: str
    names: tuple[str, ...]
    score: float | None = None
    strong: bool = False


@dataclass
class _Match:
    rule: TextRule
    field: str
    text: str
    start: int
    raw: str  # the full string the match was found in


@dataclass
class _Assignment:
    disease_id: str
    gene: _Gene
    direction: str
    origin: str | None
    evidence_class: str
    confidence: float
    basis: dict[str, Any]
    attributes: dict[str, Any] = field(default_factory=dict)
    source_db: str = RULE_SOURCE


@dataclass
class MechanismResult:
    """What :func:`derive` produces; :func:`derive_mechanisms` unpacks it."""

    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    coverage: list[dict[str, Any]]
    origins: dict[tuple[str, str], str]
    report: dict[str, Any]


# --------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------


def _now_iso(now: str | None) -> str:
    return now or datetime.now(timezone.utc).isoformat(timespec="seconds")


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def association_passes(props: Mapping[str, Any], thresholds: Mapping[str, Any] | None) -> bool:
    """The seed threshold for a disease-gene edge: ``evidence_score`` >= ``disease_gene_min_score``,
    or DISGENET among ``sources.direct`` when ``keep_disgenet_direct``. An edge with no
    ``evidence_score`` (a source other than the OptimusKG aggregate) passes."""
    t = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    score = _as_float(props.get("evidence_score"))
    if score is None:
        return True
    if score >= float(t["disease_gene_min_score"]):
        return True
    return bool(t.get("keep_disgenet_direct")) and DISGENET in {s.upper() for s in conf_rubric.sources_direct(props)}


_pattern_cache: dict[str, re.Pattern[str]] = {}


def _word_pattern(name: str) -> re.Pattern[str]:
    """Case-insensitive, word-bounded match of ``name``; runs of spaces and hyphens inside the
    name match any run of spaces/hyphens ("Janus kinase 3" finds "janus kinase-3")."""
    pat = _pattern_cache.get(name)
    if pat is None:
        tokens = [re.escape(t) for t in re.split(r"[-\s]+", name.strip()) if t]
        body = r"[-\s]+".join(tokens) if tokens else re.escape(name)
        pat = re.compile(r"(?<![A-Za-z0-9])" + body + r"(?![A-Za-z0-9])", re.I)
        _pattern_cache[name] = pat
    return pat


def _gene_names(node: Mapping[str, Any]) -> tuple[str, ...]:
    props = node.get("props") or {}
    raw = [props.get("symbol"), node.get("label"), props.get("name"), *(node.get("synonyms") or [])]
    out: list[str] = []
    for value in raw:
        if not isinstance(value, str):
            continue
        name = value.strip()
        if len(name) >= _MIN_NAME_LEN and not name.isdigit() and name not in out:
            out.append(name)
    return tuple(out)


def _gene_symbol(node: Mapping[str, Any]) -> str:
    props = node.get("props") or {}
    symbol = props.get("symbol")
    return str(symbol if isinstance(symbol, str) and symbol.strip() else node.get("label", "")).strip()


def _disease_fields(node: Mapping[str, Any]) -> list[tuple[str, str]]:
    """``[(field, text)]``: the label, each synonym, the description."""
    fields: list[tuple[str, str]] = [("label", str(node.get("label") or ""))]
    fields += [("synonyms", s) for s in (node.get("synonyms") or []) if isinstance(s, str) and s.strip()]
    description = (node.get("props") or {}).get("description")
    if isinstance(description, str) and description.strip():
        fields.append((DESCRIPTION_FIELD, description))
    return fields


def seed_source_record(seed_source: str, symbol: str, direction: str, seed_date: str) -> dict[str, str]:
    """The ``Edge.sources`` entry that names the curated seed row as the asserting source of an
    R4 edge: ``"<seed source> <GENE>:<DIR>"`` dated by the seed file's transcription date."""
    return {"kind": "db", "id": f"{seed_source or CURATED_SEED_FALLBACK_NAME} {symbol}:{direction}", "date": seed_date}


def strip_curated_provenance(edge: dict[str, Any], prior_basis: dict[str, Any]) -> dict[str, Any]:
    """Hard rule 10 on upgrade: when a curated (R4) ``inferred`` edge becomes ``extracted`` because
    a claim supports it, the seed-only attributes must not survive as if they were sourced.
    ``attributes.reason`` ("curated seed from cluster doc ...") is removed; ``attributes.origin``
    is kept only when the mechanism id itself is somatic or a text rule (``prior_basis.origin_rule``,
    R1/R2) supplied it, never when the seed row alone did; the seed record leaves ``sources`` (which
    then lists only retrieved records: the disease record and the papers) and is kept in
    ``prior_basis["seed_record"]`` so the provenance stays auditable. Mutates and returns ``edge``.
    A non-curated prior basis is left alone."""
    if prior_basis.get("rule") != CURATED_RULE:
        return edge
    attributes = edge.setdefault("attributes", {})
    attributes.pop("reason", None)
    parsed = parse_mechanism_id(str(edge.get("target", "")))
    somatic = bool(parsed and parsed[2])
    if not somatic and not prior_basis.get("origin_rule"):
        attributes.pop("origin", None)
    prefix = f"{prior_basis.get('seed_source') or CURATED_SEED_FALLBACK_NAME} "
    kept: list[dict[str, Any]] = []
    for s in edge.get("sources") or []:
        if s.get("kind") == "db" and str(s.get("id", "")).startswith(prefix) and "seed_record" not in prior_basis:
            prior_basis["seed_record"] = dict(s)
        else:
            kept.append(s)
    edge["sources"] = kept
    return edge


def mechanism_label(symbol: str, direction: str, somatic: bool) -> str:
    """``"<GENE> gain of function"``, ``"<GENE> somatic gain of function"``."""
    words = DIRECTION_LABEL.get(direction, direction)
    return f"{symbol} somatic {words}" if somatic else f"{symbol} {words}"


def resolve_terms(nodes: Iterable[Mapping[str, Any]], terms: Iterable[str]) -> dict[str, list[tuple[str, str]]]:
    """Case-insensitive exact match of each term against node label and synonyms:
    ``{term: [(node_id, matched_field)]}`` (empty list when the term matches nothing)."""
    index: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for node in nodes:
        label = str(node.get("label") or "").strip().lower()
        if label:
            index[label].append((str(node["id"]), "label"))
        for syn in node.get("synonyms") or []:
            if isinstance(syn, str) and syn.strip():
                index[syn.strip().lower()].append((str(node["id"]), "synonyms"))
    out: dict[str, list[tuple[str, str]]] = {}
    for term in terms:
        key = str(term).strip().lower()
        hits = index.get(key, [])
        # label matches first, then synonym matches; stable and deduplicated by node id
        seen: set[str] = set()
        ordered: list[tuple[str, str]] = []
        for node_id, how in sorted(hits, key=lambda h: (h[1] != "label", h[0])):
            if node_id not in seen:
                seen.add(node_id)
                ordered.append((node_id, how))
        out[str(term)] = ordered
    return out


# --------------------------------------------------------------------------------------
# R1 / R2: text rules
# --------------------------------------------------------------------------------------


def _find_matches(fields: list[tuple[str, str]], kind: str) -> list[_Match]:
    matches: list[_Match] = []
    for field_name, text in fields:
        for rule in RULES:
            if rule.kind != kind:
                continue
            for m in rule.pattern.finditer(text):
                matches.append(_Match(rule, field_name, m.group(0), m.start(), text))
    return matches


def _decide(matches: list[_Match], what: str) -> tuple[str | None, list[_Match], str | None]:
    """``(value, deciding matches, problem)``: the name fields decide when they agree on one
    value; the description decides only when the name fields are silent. ``what`` names the
    kind ("directions" / "origins") in the conflict message."""
    name_matches = [m for m in matches if m.field in NAME_FIELDS]
    desc_matches = [m for m in matches if m.field == DESCRIPTION_FIELD]
    name_values = {m.rule.value for m in name_matches}
    desc_values = {m.rule.value for m in desc_matches}
    if len(name_values) == 1:
        value = next(iter(name_values))
        return value, name_matches + [m for m in desc_matches if m.rule.value == value], None
    if not name_values and len(desc_values) == 1:
        return next(iter(desc_values)), desc_matches, None
    if not name_values and not desc_values:
        return None, [], None
    conflict = sorted(name_values or desc_values)
    return None, matches, f"conflicting {what} {conflict}"


def _preceding_phrase(text: str, start: int) -> str:
    """The up-to-three words before position ``start`` ("due to <GENE> deficiency" -> "<GENE>",
    "common gamma chain deficiency" -> "common gamma chain"), stopping at a function word."""
    tokens = re.findall(r"\S+", text[:start])
    phrase: list[str] = []
    for tok in reversed(tokens):
        clean = tok.strip(" ,;:()[]\"'").rstrip(".")
        low = clean.lower()
        if not clean or low in _PHRASE_STOP or clean in {"-", "/", "–"}:
            break
        if low in _PHRASE_SKIP:
            continue
        phrase.insert(0, clean)
        if len(phrase) >= _PHRASE_MAX_TOKENS:
            break
    return " ".join(phrase)


def _genes_named_by_phrase(phrase: str, genes: list[_Gene]) -> list[tuple[_Gene, str]]:
    if len(phrase) < _MIN_NAME_LEN:
        return []
    pat = _word_pattern(phrase)
    return [(g, name) for g in genes for name in g.names if pat.search(name)]


def _genes_named_in_text(fields: list[tuple[str, str]], genes: list[_Gene]) -> dict[str, tuple[str, str]]:
    """``{gene_id: (field, name)}`` for genes whose symbol/name/synonym occurs in the disease text,
    recording the first field (label, synonyms, description order) that names them."""
    named: dict[str, tuple[str, str]] = {}
    for field_name, text in fields:
        for g in genes:
            if g.id in named:
                continue
            for name in g.names:
                if _word_pattern(name).search(text):
                    named[g.id] = (field_name, name)
                    break
    return named


def _resolve_gene(
    fields: list[tuple[str, str]],
    genes: list[_Gene],
    direction_matches: list[_Match],
) -> tuple[_Gene | None, dict[str, Any], str | None]:
    """``(gene, how, problem)`` following the rule order in the module docstring."""
    by_id = {g.id: g for g in genes}
    # 1. "<entity> deficiency": the phrase names a gene
    for m in direction_matches:
        if m.rule.id != "R1.lof.deficiency":
            continue
        phrase = _preceding_phrase(m.raw, m.start)
        hits = _genes_named_by_phrase(phrase, genes)
        ids = sorted({g.id for g, _ in hits})
        if len(ids) == 1:
            g = by_id[ids[0]]
            name = next(n for gg, n in hits if gg.id == g.id)
            return g, {"how": "deficient_entity", "phrase": phrase, "gene_name": name, "field": m.field}, None
    # 2./3. a gene named in the text (name fields first, then anywhere)
    named = _genes_named_in_text(fields, genes)
    in_names = {gid: v for gid, v in named.items() if v[0] in NAME_FIELDS}
    for pool, how in ((in_names, "named_in_label_or_synonyms"), (named, "named_in_text")):
        if len(pool) == 1:
            gid, (field_name, name) = next(iter(pool.items()))
            return by_id[gid], {"how": how, "gene_name": name, "field": field_name}, None
    if named:
        detail = sorted(f"{by_id[g].symbol} ({f}: {n})" for g, (f, n) in named.items())
        return None, {"how": "ambiguous"}, f"ambiguous gene: several genes named {detail}"
    # 4. nothing named: exactly one associated gene above threshold
    strong = [g for g in genes if g.strong]
    if len(strong) == 1:
        g = strong[0]
        return g, {"how": "single_candidate_above_threshold", "evidence_score": g.score}, None
    if not strong:
        return None, {"how": "none"}, "no gene named and no associated gene passes the threshold"
    detail = sorted(g.symbol for g in strong)
    return None, {"how": "ambiguous"}, f"ambiguous gene: none named, candidates {detail}"


def _match_summary(matches: list[_Match]) -> dict[str, Any]:
    return {
        "rule": "+".join(sorted({m.rule.id for m in matches})),
        "matched_text": sorted({m.text for m in matches}),
        "field": sorted({m.field for m in matches}),
    }


def _text_rules(node: Mapping[str, Any], genes: list[_Gene]) -> tuple[_Assignment | None, list[str]]:
    """Apply R1 and R2 to one disease; returns the assignment and a list of gap descriptions."""
    fields = _disease_fields(node)
    direction, dir_matches, dir_problem = _decide(_find_matches(fields, "direction"), "directions")
    if direction is None:
        if dir_problem:
            return None, [f"mechanism: {dir_problem} for {node['label']}"]
        return None, [f"mechanism: no direction rule matched for {node['label']}"]
    gene, resolution, gene_problem = _resolve_gene(fields, genes, dir_matches)
    if gene is None:
        return None, [f"mechanism: {gene_problem} for {node['label']}"]
    origin, origin_matches, origin_problem = _decide(_find_matches(fields, "origin"), "origins")
    gaps: list[str] = []
    if origin is None:
        why = origin_problem or "no origin rule matched"
        gaps.append(f"mechanism: origin unknown ({why}) for {node['label']}")
    fields_hit = {m.field for m in dir_matches}
    both = bool(fields_hit & set(NAME_FIELDS)) and DESCRIPTION_FIELD in fields_hit
    confidence = TEXT_RULE_CONFIDENCE_BOTH_FIELDS if both else TEXT_RULE_CONFIDENCE_ONE_FIELD
    basis: dict[str, Any] = {**_match_summary(dir_matches), "gene_resolution": {**resolution, "gene_symbol": gene.symbol, "evidence_score": gene.score}}
    if origin is not None:
        o = _match_summary(origin_matches)
        basis.update({"origin_rule": o["rule"], "origin_matched_text": o["matched_text"], "origin_field": o["field"]})
    else:
        basis["origin_rule"] = None
    attributes = {"origin": origin} if origin else {}
    return _Assignment(str(node["id"]), gene, direction, origin, "observed", confidence, basis, attributes), gaps


# --------------------------------------------------------------------------------------
# derive
# --------------------------------------------------------------------------------------


def _load_genes(store: Any) -> dict[str, _Gene]:
    genes: dict[str, _Gene] = {}
    for node in store.iter_nodes(NodeType.Gene):
        genes[node["id"]] = _Gene(node["id"], _gene_symbol(node), _gene_names(node))
    return genes


def _associations(store: Any, genes: Mapping[str, _Gene], thresholds: Mapping[str, Any] | None) -> dict[str, list[_Gene]]:
    """Disease id -> associated genes (store ``associated_with`` edges, Disease -> Gene)."""
    out: dict[str, dict[str, _Gene]] = defaultdict(dict)
    for edge in store.iter_edges(relation=REL_ASSOCIATED_WITH):
        base = genes.get(edge["target"])
        if base is None:
            continue
        props = edge.get("props") or {}
        score = _as_float(props.get("evidence_score"))
        strong = association_passes(props, thresholds)
        current = out[edge["source"]].get(base.id)
        if current is None:
            out[edge["source"]][base.id] = _Gene(base.id, base.symbol, base.names, score, strong)
        else:  # the same pair from two loaders: keep the best score, pass if either passes
            current.strong = current.strong or strong
            if score is not None and (current.score is None or score > current.score):
                current.score = score
    return {d: sorted(gs.values(), key=lambda g: g.id) for d, gs in out.items()}


def _seed_rows(seeds: Mapping[str, Any]) -> tuple[str, list[Mapping[str, Any]]]:
    """``(source, rows)`` of the curated seed: the mechanisms.yaml dict or its bare row list."""
    raw = seeds.get("mechanisms")
    if raw is None:
        return "", []
    if isinstance(raw, Mapping):
        return str(raw.get("source", "")), [r for r in (raw.get("mechanisms") or []) if isinstance(r, Mapping)]
    return "", [r for r in raw if isinstance(r, Mapping)]


def _seed_date(seeds: Mapping[str, Any]) -> str:
    """The seed file's ``transcribed_at`` (the date its rows were written), or ``""``."""
    raw = seeds.get("mechanisms")
    return str(raw.get("transcribed_at") or "").strip() if isinstance(raw, Mapping) else ""


def _seed_terms(row: Mapping[str, Any]) -> list[tuple[str, str]]:
    """``[(term, from)]`` from a row's ``disease_terms``: each entry is a name or a mapping
    ``{term: <name>, from: <where the wording was seen>}``; ``from`` is ``""`` for a bare name."""
    out: list[tuple[str, str]] = []
    for entry in row.get("disease_terms") or []:
        if isinstance(entry, Mapping):
            term = str(entry.get("term", "")).strip()
            if term:
                out.append((term, str(entry.get("from", "")).strip()))
        elif str(entry).strip():
            out.append((str(entry).strip(), ""))
    return out


def _claims_by_pair(claims_bundle: Any, disease_ids: set[str], variant_ids: set[str]) -> tuple[dict[tuple[str, str], list[Any]], list[str]]:
    grouped: dict[tuple[str, str], list[Any]] = defaultdict(list)
    unlinked: list[str] = []
    if claims_bundle is None:
        return grouped, unlinked
    for claim in claims_bundle.claims:
        if claim.predicate != PREDICATE_HAS_MECHANISM:
            continue
        if parse_mechanism_id(claim.object) is None or claim.subject not in disease_ids | variant_ids:
            unlinked.append(claim.id)
            continue
        grouped[(claim.subject, claim.object)].append(claim)
    return grouped, unlinked


def _claim_sources(claims: list[Any], papers: Mapping[str, Any], fallback_date: str) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for c in claims:
        paper = papers.get(c.paper)
        year = (paper.props.get("year") if paper is not None else None)
        date = str(year) if isinstance(year, int) and year > 0 else fallback_date
        if c.pmid:
            src = {"kind": "pmid", "id": c.pmid, "date": date}
        elif c.paper.startswith("doi:"):
            src = {"kind": "doi", "id": c.paper[len("doi:"):], "date": date}
        else:
            src = {"kind": "db", "id": c.paper, "date": date}
        if src not in out:
            out.append(src)
    return out


def derive(store: Any, seeds: Mapping[str, Any], claims_bundle: Any = None, *, now: str | None = None) -> MechanismResult:
    """Apply R1-R4 to every disease in ``store`` (see the module docstring).

    ``seeds`` is the cluster seed dict (``thresholds`` used) optionally carrying ``"mechanisms"``:
    the mechanisms.yaml dict or its row list, for R4. Without it R4 is skipped.
    """
    stamp = _now_iso(now)
    day = stamp[:10]
    thresholds = seeds.get("thresholds") or {}
    genes = _load_genes(store)
    gene_by_symbol = {g.symbol.upper(): g for g in genes.values() if g.symbol}
    associations = _associations(store, genes, thresholds)
    diseases = {n["id"]: n for n in store.iter_nodes(NodeType.Disease)}
    variant_ids = {n["id"] for n in store.iter_nodes(NodeType.Variant)}

    assignments: dict[tuple[str, str], _Assignment] = {}  # (disease, gene) -> R1/R2 result
    coverage: list[dict[str, Any]] = []
    by_rule: dict[str, int] = defaultdict(int)

    def gap(entity_id: str, query: str) -> None:
        """Coverage row dated with the disease record's retrieval date when ``entity_id`` is a
        disease (the text that was searched), else with the build day (seed-key rows)."""
        node = diseases.get(entity_id)
        date = str(node.get("retrieved_at") or stamp)[:10] if node is not None else day
        coverage.append({"entity_id": entity_id, "source": RULE_SOURCE, "query": query, "date": date, "hits": 0})

    # R1 / R2
    for disease_id, node in diseases.items():
        candidates = associations.get(disease_id, [])
        if not candidates:
            gap(disease_id, f"mechanism: no associated gene in the store for {node['label']}")
            continue
        assignment, gaps = _text_rules(node, candidates)
        for g in gaps:
            gap(disease_id, g)
        if assignment is not None:
            assignments[(disease_id, assignment.gene.id)] = assignment
            by_rule[assignment.basis["rule"]] += 1

    # R4: curated fallback where R1 produced nothing for the pair
    seed_source, seed_rows = _seed_rows(seeds)
    seed_date = _seed_date(seeds) or day
    seed_hits: dict[str, Any] = {}
    curated: list[_Assignment] = []
    for row in seed_rows:
        symbol = str(row.get("gene", "")).strip()
        direction = str(row.get("direction", "")).strip()
        origin = str(row.get("origin", "")).strip() or None
        tag = str(row.get("cluster_doc_confidence", "")).strip() or "unknown"
        key = f"seed:mechanism:{symbol}:{direction}"
        if direction not in MECHANISM_DIRECTIONS or not symbol:
            gap(key, f"mechanism: curated row has no valid gene/direction ({row})")
            continue
        gene = gene_by_symbol.get(symbol.upper())
        term_from = dict(_seed_terms(row))
        resolved = resolve_terms(diseases.values(), term_from)
        seed_hits[key] = {t: [d for d, _ in hits] for t, hits in resolved.items()}
        if gene is None:
            gap(key, f"mechanism: curated gene {symbol} is not a Gene node in the store")
            continue
        for term, hits in resolved.items():
            if not hits:
                gap(key, f"mechanism: curated disease term {term!r} matches no Disease node")
                continue
            for disease_id, how in hits:
                prior = assignments.get((disease_id, gene.id))
                if prior is not None:  # an earlier rule already decided this pair
                    if prior.direction != direction:
                        gap(disease_id, f"mechanism: curated {symbol} {direction} not applied, {prior.basis['rule']} gave {prior.direction} for {diseases[disease_id]['label']}")
                    continue
                associated = {g.id for g in associations.get(disease_id, [])}
                if gene.id not in associated:
                    gap(disease_id, f"mechanism: curated {symbol} {direction} skipped, store does not associate {symbol} with {diseases[disease_id]['label']}")
                    continue
                basis = {
                    "rule": CURATED_RULE,
                    "seed_source": seed_source,
                    "seed_row": {k: row[k] for k in ("gene", "direction", "origin", "cluster_doc_confidence", "note") if k in row},
                    "disease_term": term,
                    "matched_field": how,
                    "gene_resolution": {"how": "curated_seed", "gene_symbol": symbol},
                }
                if term_from.get(term):
                    basis["disease_term_from"] = term_from[term]
                # The seed supplies the DIRECTION; the ORIGIN is read from the disease's own text
                # first (R2, a database fact that survives a later upgrade) and from the seed row
                # only when the text decides nothing (seed-only: dropped if a claim upgrades the edge).
                text_origin, origin_matches, _origin_problem = _decide(_find_matches(_disease_fields(diseases[disease_id]), "origin"), "origins")
                if text_origin is not None:
                    o = _match_summary(origin_matches)
                    basis.update({"origin_rule": o["rule"], "origin_matched_text": o["matched_text"], "origin_field": o["field"], "origin_source": "disease text"})
                    edge_origin: str | None = text_origin
                else:
                    edge_origin = origin if origin in ("germline", "somatic") else None
                    basis.update({"origin_rule": None, "origin_source": "seed row" if edge_origin else None})
                attributes = {"labels": [CURATED_LABEL], "reason": f"curated seed from cluster doc ({tag})"}
                if edge_origin:
                    attributes["origin"] = edge_origin
                a = _Assignment(disease_id, gene, direction, edge_origin, "inferred", conf_rubric.cap_inferred(CURATED_CONFIDENCE), basis, attributes)
                assignments[(disease_id, gene.id)] = a
                curated.append(a)
                by_rule["R4.curated"] += 1

    # nodes and edges from R1/R2/R4
    mech_nodes: dict[str, dict[str, Any]] = {}
    edges: dict[tuple[str, str, str], dict[str, Any]] = {}
    origins: dict[tuple[str, str], str] = {}

    def ensure_node(mech_id: str, symbol: str, direction: str, somatic: bool, origin: str | None, gene_id: str | None) -> None:
        node = mech_nodes.get(mech_id)
        if node is None:
            props: dict[str, Any] = {"gene_symbol": symbol, "direction": direction}
            if gene_id:
                props["gene"] = gene_id
            node = mech_nodes[mech_id] = {"id": mech_id, "type": NodeType.Mechanism.value, "label": mechanism_label(symbol, direction, somatic), "synonyms": [], "props": props}
        if somatic:
            node["props"]["origin"] = "somatic"
        elif origin == "germline":
            node["props"]["origin"] = "germline"

    for (disease_id, gene_id), a in assignments.items():
        somatic = a.origin == "somatic"
        mech_id = mechanism_id(a.gene.symbol, a.direction, somatic)
        ensure_node(mech_id, a.gene.symbol, a.direction, somatic, a.origin, gene_id)
        disease = diseases[disease_id]
        backbone = (disease.get("props") or {}).get("backbone_id") or disease_id
        disease_record = {"kind": "db", "id": str(backbone), "date": str(disease.get("retrieved_at") or stamp)}
        if a.evidence_class == "inferred":
            # The assertion comes from the curated seed file; the disease record only supplied the name match.
            sources = [seed_source_record(seed_source, a.gene.symbol, a.direction, seed_date), disease_record]
        else:
            sources = [disease_record]
        edges[(disease_id, mech_id, a.source_db)] = {
            "source": disease_id,
            "target": mech_id,
            "relation": REL_HAS_MECHANISM,
            "source_db": a.source_db,
            "evidence_class": a.evidence_class,
            "confidence": a.confidence,
            "confidence_basis": a.basis,
            "status": "supported",
            "sources": sources,
            "supporting": [],
            "contradicting": [],
            "attributes": dict(a.attributes),
        }
        if a.origin and a.evidence_class != "inferred":  # hard rule 10: a curated origin never reaches an observed edge
            prior_origin = origins.get((disease_id, gene_id))
            if prior_origin is None:
                origins[(disease_id, gene_id)] = a.origin
            elif prior_origin != a.origin:
                origins.pop((disease_id, gene_id))  # disagreement: do not propagate

    # R3: literature claims
    grouped, unlinked = _claims_by_pair(claims_bundle, set(diseases), variant_ids)
    papers = {p.id: p for p in claims_bundle.papers} if claims_bundle is not None else {}
    claim_edges = 0
    for (subject, mech_id), claims in grouped.items():
        parsed = parse_mechanism_id(mech_id)
        assert parsed is not None
        symbol, direction, somatic = parsed
        supporting = [c for c in claims if c.polarity is Polarity.supports]
        contradicting = [c for c in claims if c.polarity is Polarity.contradicts]
        existing = next((e for (s, t, _db), e in edges.items() if s == subject and t == mech_id), None)
        if existing is None and not supporting:
            unlinked.extend(c.id for c in claims)  # only contradicted and no DB assertion: no edge to hang it on
            continue
        gene = gene_by_symbol.get(symbol.upper())
        ensure_node(mech_id, symbol, direction, somatic, "somatic" if somatic else None, gene.id if gene else None)
        per_claim: list[dict[str, Any]] = []
        claim_conf = 0.0
        for c in claims:
            conf, basis = conf_rubric.claim_confidence(c.study_type.value, c.n_patients)
            per_claim.append({"id": c.id, "polarity": c.polarity.value, "confidence": conf, **basis})
            if c.polarity is Polarity.supports:
                claim_conf = max(claim_conf, conf)
        status, labels, status_basis = conf_rubric.status_from_claims(supporting, contradicting)
        claim_basis = {"rule": "R3.claims", "claims": per_claim, **status_basis}
        sources = _claim_sources(claims, papers, claims_bundle.extracted_at)
        if existing is not None:
            prior_basis = dict(existing["confidence_basis"])
            was_curated = existing["evidence_class"] == "inferred"
            if supporting and was_curated:
                # a supporting claim verifies a curated seed: seed-only reason / origin / source go (hard rule 10)
                strip_curated_provenance(existing, prior_basis)
            existing["evidence_class"] = "extracted"
            existing["confidence"] = max(float(existing["confidence"]), claim_conf)
            existing["confidence_basis"] = {**claim_basis, "db_text": prior_basis}
            existing["sources"] = existing["sources"] + [s for s in sources if s not in existing["sources"]]
            existing["supporting"] = [c.id for c in supporting]
            existing["contradicting"] = [c.id for c in contradicting]
            existing["status"] = status
            attrs = existing["attributes"]
            merged = list(attrs.get("labels", [])) + [l for l in labels if l not in attrs.get("labels", [])]
            if supporting:  # a supporting claim verifies a curated seed: the edge is extracted, not curated_unverified
                merged = [l for l in merged if l != CURATED_LABEL]
            if merged:
                attrs["labels"] = merged
            else:
                attrs.pop("labels", None)
            if "preprint" in labels:
                attrs["preprint"] = True
            if was_curated:  # the seed is not evidence: the claims are the edge's only support
                conf_rubric.apply_in_vitro_only(existing, supporting)
                if attrs.get("labels") == []:
                    attrs.pop("labels", None)
        else:
            attributes: dict[str, Any] = {}
            if labels:
                attributes["labels"] = list(labels)
            if "preprint" in labels:
                attributes["preprint"] = True
            if somatic:
                attributes["origin"] = "somatic"
            if subject in variant_ids and any(c.variant == subject for c in supporting):
                attributes["labels"] = attributes.get("labels", []) + [VARIANT_SPECIFIC_LABEL]
            created = edges[(subject, mech_id, CLAIMS_SOURCE)] = {
                "source": subject,
                "target": mech_id,
                "relation": REL_HAS_MECHANISM,
                "source_db": CLAIMS_SOURCE,
                "evidence_class": "extracted",
                "confidence": claim_conf,
                "confidence_basis": claim_basis,
                "status": status,
                "sources": sources,
                "supporting": [c.id for c in supporting],
                "contradicting": [c.id for c in contradicting],
                "attributes": attributes,
            }
            conf_rubric.apply_in_vitro_only(created, supporting)
            if created["attributes"].get("labels") == []:
                created["attributes"].pop("labels", None)
            if subject in diseases and gene is not None and somatic:
                origins.setdefault((subject, gene.id), "somatic")
        claim_edges += 1
        by_rule["R3.claims"] += 1

    nodes = [mech_nodes[k] for k in sorted(mech_nodes)]
    edge_list = [edges[k] for k in sorted(edges)]
    report = {
        "diseases_considered": len(diseases),
        "diseases_with_associated_genes": sum(1 for d in diseases if associations.get(d)),
        "assignments": len(assignments),
        "mechanism_nodes": len(nodes),
        "mechanism_edges": len(edge_list),
        "by_rule": dict(sorted(by_rule.items())),
        "curated_edges": len(curated),
        "claim_edges": claim_edges,
        "claims_unlinked": sorted(unlinked),
        "seed_term_resolution": seed_hits,
        "coverage_rows": len(coverage),
        "thresholds": {**DEFAULT_THRESHOLDS, **dict(thresholds)},
    }
    return MechanismResult(nodes, edge_list, coverage, origins, report)


def derive_mechanisms(
    store: Any, seeds: Mapping[str, Any], claims_bundle: Any = None, *, now: str | None = None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[tuple[str, str], str]]:
    """``(mechanism_nodes, mechanism_edges, coverage_rows, origin_by_disease_gene)``.

    Nodes and edges are plain dicts shaped like :class:`pipeline.models.Node` / ``Edge`` inputs;
    edges carry no ``id`` (the build assigns them) and an extra ``source_db`` the build uses for
    deterministic ordering and then drops.
    """
    r = derive(store, seeds, claims_bundle, now=now)
    return r.nodes, r.edges, r.coverage, r.origins
