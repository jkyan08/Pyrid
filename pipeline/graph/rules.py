"""Rules: Drug -> Disease edges inferred from a drug's action and a disease's mechanism direction
(README section 6 "Rules"; ground-truth test 5).

Two GENERAL rules, both reading only the graph's own edges. Nothing here names a gene, disease,
drug or pathway: a drug's targets come from its ``inhibits`` edges, a disease's mechanism
direction from the mechanism id (:func:`pipeline.ids.parse_mechanism_id`), pathway membership
from ``in_pathway`` edges, and the mechanism's gene from the Mechanism node's ``props.gene`` or
its symbol matched against ``Gene.props.symbol``.

direction_mismatch
    A drug that *inhibits* a gene (directly, or, when ``pathway_level.enabled``, any gene in a
    pathway the drug inhibits) pushes in the same direction as a *loss* mechanism on that gene
    (``loss_directions``, default ``LoF`` and ``DN``). Such a pair must never be presented as a
    lead, so the rule emits ``Drug -> Disease`` ``treats`` with ``evidence_class: inferred``,
    ``status: inferred_unsupported`` and ``attributes.reason "direction mismatch"``.
direction_match_candidate
    The same scan for a *gain* mechanism (``gain_directions``, default ``GoF``): the inhibitor
    opposes the defect. That is a
    hypothesis, not evidence, so the edge is ``status: supported`` (rendered with the inferred
    badge), labelled ``candidate / hypothesis / direction_match`` with
    ``reason "direction match (hypothesis, no literature)"``.

Neither rule speaks where the graph already carries a drug-disease assertion for the pair
(``existing_treatment_relations``: an observed or extracted ``treats`` edge, or a database
indication / contraindication). Edges an earlier run of THIS module emitted (recognised by the
``rules:`` prefix of a ``sources[].id``, the same marker ``pipeline.graph.logic`` strips) never
count as pre-existing, so running the rules twice over their own output is idempotent. A pair
that is both a loss hit and a gain hit (the drug inhibits two genes of the same disease with
opposite directions) resolves to ``direction_mismatch``: the unsupported flag is the safer
statement, and the gain hits are kept in ``confidence_basis.hits``. A pair reached both directly
and through a pathway uses the direct hit (full confidence); every hit stays in the basis.

Caveat (ground-truth test 5): the classification is per ``(drug, disease)`` over the genes the
drug REACHES. A disease carrying a gain mechanism on a reached gene and a loss mechanism on a
gene the drug does not reach gets a ``direction_match_candidate`` edge (``status: supported``),
while test 5 treats every disease with any ``mech:*:LoF`` edge as a "LoF disease" whose
inhibitor edges must all be ``inferred_unsupported``. The current graph has no such disease
(``pairs_mixed_direction`` 0 in the dry run) and the per-reached-gene reading is the biologically
defensible one; should test 5 fail after new mechanism rows, this is the first place to look, and
the conservative alternative (the candidate rule abstaining whenever the disease carries any
``loss_directions`` mechanism) would be a documented, general switch in ``logic.yaml``, never a
gene special case.

Confidence is a documented constant per rule (``pipeline/seeds/logic.yaml`` ``rules:``, mirrored
by :data:`DEFAULT_RULES_CFG`), multiplied by ``pathway_level.confidence_factor`` for a
pathway-level hit and capped at the inferred ceiling (:func:`pipeline.graph.confidence.cap_inferred`).
``confidence_basis`` keeps the whole breakdown: target gene, mechanism, direction, route,
base confidence, factor, the ids of the ``inhibits`` and ``has_mechanism`` edges the hit came
from, and every other hit for the pair.

Determinism: sorted iteration everywhere, no clock (``today`` is passed in), no randomness.
Drafts carry no ``id``; the orchestrator (``pipeline.graph.logic``) continues the graph's
``e_NNNNNN`` sequence.

Dry run (read-only)::

    uv run python -m pipeline.graph.rules --in data/build/graph.json [--examples 10]
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pipeline.graph.confidence import cap_inferred
from pipeline.ids import parse_mechanism_id
from pipeline.models import Graph, Node, NodeType

__all__ = [
    "CONFIDENCE_DECIMALS",
    "DEFAULT_RULES_CFG",
    "DRUG_ACTION",
    "GAIN_DIRECTIONS",
    "LABELS_DIRECTION_MATCH",
    "LABELS_DIRECTION_MISMATCH",
    "MechanismHit",
    "REASON_DIRECTION_MATCH",
    "REASON_DIRECTION_MISMATCH",
    "REL_HAS_MECHANISM",
    "REL_INHIBITS",
    "REL_IN_PATHWAY",
    "REL_TREATS",
    "RULE_DIRECTION_MATCH",
    "RULE_DIRECTION_MISMATCH",
    "RulesResult",
    "SOURCE_MODULE",
    "TargetHit",
    "VIA_PATHWAY",
    "VIA_TARGET",
    "apply_rules",
    "drug_targets",
    "examples_by_label",
    "existing_treatment_pairs",
    "is_rule_edge",
    "main",
    "mechanism_hits_by_gene",
    "rules_config",
    "run_rules",
    "summarize",
]

# Relation vocabulary (README section 4; constants mirrored in tests/conftest.py).
REL_INHIBITS = "inhibits"  # Drug -> Gene | Drug -> Pathway
REL_TREATS = "treats"  # Drug -> Disease
REL_HAS_MECHANISM = "has_mechanism"  # Disease -> Mechanism
REL_IN_PATHWAY = "in_pathway"  # Gene -> Pathway

RULE_DIRECTION_MISMATCH = "direction_mismatch"
RULE_DIRECTION_MATCH = "direction_match_candidate"
REASON_DIRECTION_MISMATCH = "direction mismatch"
REASON_DIRECTION_MATCH = "direction match (hypothesis, no literature)"
LABELS_DIRECTION_MISMATCH: tuple[str, ...] = ("inferred_unsupported", "direction_mismatch")
LABELS_DIRECTION_MATCH: tuple[str, ...] = ("candidate", "hypothesis", "direction_match")
SOURCE_MODULE = "rules"  # sources[].id = "<module>:<rule>"
VIA_TARGET = "target"
VIA_PATHWAY = "pathway"
DRUG_ACTION = REL_INHIBITS
GAIN_DIRECTIONS: tuple[str, ...] = ("GoF",)  # default for rules.direction_match_candidate.gain_directions
CONFIDENCE_DECIMALS = 4

# Names and numbers only; pipeline/seeds/logic.yaml ``rules:`` is the documented source and
# overrides any of them (these are the fallbacks when a key is absent).
DEFAULT_RULES_CFG: dict[str, Any] = {
    RULE_DIRECTION_MISMATCH: {"confidence": 0.1, "loss_directions": ["LoF", "DN"]},
    RULE_DIRECTION_MATCH: {"confidence": 0.3, "enabled": True, "gain_directions": list(GAIN_DIRECTIONS)},
    "pathway_level": {"enabled": True, "confidence_factor": 0.5},
    # Drug-Disease relations that mean "the graph already says something about this pair".
    "existing_treatment_relations": [REL_TREATS, "indicated_for", "contraindicated_for"],
}
_RULE_SOURCE_PREFIX = f"{SOURCE_MODULE}:"  # sources[].id of every edge this module emits

_RULE_ORDER = {RULE_DIRECTION_MISMATCH: 0, RULE_DIRECTION_MATCH: 1}


# --------------------------------------------------------------------------------------
# data holders
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class TargetHit:
    """One way a drug reaches a gene: an ``inhibits`` edge to the gene (``via == "target"``) or
    to a pathway the gene is a member of (``via == "pathway"``)."""

    gene_id: str
    via: str
    inhibits_edge: str
    pathway_id: str | None = None


@dataclass(frozen=True)
class MechanismHit:
    """A Disease -> Mechanism edge resolved to the gene the mechanism acts on."""

    disease_id: str
    mechanism_id: str
    direction: str
    somatic: bool
    has_mechanism_edge: str
    gene_id: str


@dataclass
class RulesResult:
    """What :func:`run_rules` produces: edge drafts (no ids) and a report of every decision."""

    drafts: list[dict[str, Any]]
    report: dict[str, Any]


# --------------------------------------------------------------------------------------
# configuration
# --------------------------------------------------------------------------------------


def rules_config(cfg: Mapping[str, Any] | None) -> dict[str, Any]:
    """The rules section merged over :data:`DEFAULT_RULES_CFG`.

    ``cfg`` may be the whole ``logic.yaml`` dict (its ``rules`` key is used), the ``rules``
    section itself, or ``None`` (defaults). Each rule's mapping is merged key by key, so a seed
    file may override only ``confidence``. The result shares nothing with
    :data:`DEFAULT_RULES_CFG` or ``cfg`` (deep copies), so a caller may edit it freely.
    """
    raw: Mapping[str, Any] = cfg or {}
    section = raw["rules"] if isinstance(raw.get("rules"), Mapping) else raw
    out: dict[str, Any] = {}
    for key, default in DEFAULT_RULES_CFG.items():
        given = section.get(key)
        if isinstance(default, Mapping):
            out[key] = copy.deepcopy({**default, **(dict(given) if isinstance(given, Mapping) else {})})
        else:
            out[key] = [str(x) for x in given] if isinstance(given, (list, tuple)) else list(default)
    return out


# --------------------------------------------------------------------------------------
# graph indexing (pure functions over pipeline.models.Graph)
# --------------------------------------------------------------------------------------


def _orient(nodes: Mapping[str, Node], edge: Any, first: NodeType) -> tuple[Node, Node] | None:
    """``(node of type first, other node)`` for an edge given in either direction, else ``None``."""
    s, t = nodes.get(edge.source), nodes.get(edge.target)
    if s is None or t is None:
        return None
    if s.type == first:
        return s, t
    if t.type == first:
        return t, s
    return None


def _symbol(gene: Node) -> str:
    symbol = gene.props.get("symbol")
    return str(symbol).strip() if isinstance(symbol, str) and symbol.strip() else gene.label.strip()


def _genes_by_name(graph: Graph) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """``(exact, lower-cased)`` indexes of Gene ids by ``props.symbol`` and label."""
    exact: dict[str, list[str]] = defaultdict(list)
    folded: dict[str, list[str]] = defaultdict(list)
    for n in graph.nodes:
        if n.type != NodeType.Gene:
            continue
        for name in {_symbol(n), n.label.strip()}:
            if name:
                exact[name].append(n.id)
                folded[name.lower()].append(n.id)
    return exact, folded


def _mechanism_gene(
    mech: Node,
    symbol: str,
    nodes: Mapping[str, Node],
    exact: Mapping[str, list[str]],
    folded: Mapping[str, list[str]],
) -> str | None:
    """The Gene node a mechanism acts on: ``props.gene`` when it is a Gene id, else the single
    Gene whose symbol (then label) equals the mechanism's symbol, exactly then case-insensitively."""
    gene_prop = mech.props.get("gene")
    if isinstance(gene_prop, str):
        g = nodes.get(gene_prop)
        if g is not None and g.type == NodeType.Gene:
            return g.id
    names: list[str] = []
    prop_symbol = mech.props.get("gene_symbol")
    if isinstance(prop_symbol, str) and prop_symbol.strip():
        names.append(prop_symbol.strip())
    if symbol and symbol not in names:
        names.append(symbol)
    for name in names:
        for index, key in ((exact, name), (folded, name.lower())):
            ids = sorted(set(index.get(key, [])))
            if len(ids) == 1:
                return ids[0]
    return None


def mechanism_hits_by_gene(graph: Graph) -> tuple[dict[str, list[MechanismHit]], list[dict[str, str]]]:
    """``({gene_id: [MechanismHit]}, unresolved)`` over every Disease -> Mechanism
    ``has_mechanism`` edge (either direction; Variant -> Mechanism edges are not diseases and are
    skipped). ``unresolved`` lists mechanism edges whose id is off-convention or whose gene is
    not a Gene node, each with a reason."""
    nodes = graph.node_index()
    exact, folded = _genes_by_name(graph)
    hits: dict[str, list[MechanismHit]] = defaultdict(list)
    unresolved: list[dict[str, str]] = []
    for e in graph.edges:
        if e.relation != REL_HAS_MECHANISM:
            continue
        pair = _orient(nodes, e, NodeType.Disease)
        if pair is None or pair[1].type != NodeType.Mechanism:
            continue
        disease, mech = pair
        parsed = parse_mechanism_id(mech.id)
        if parsed is None:
            unresolved.append({"edge": e.id, "mechanism": mech.id, "reason": "mechanism id is not mech:<GENE>:<DIR>[:somatic]"})
            continue
        symbol, direction, somatic = parsed
        gene_id = _mechanism_gene(mech, symbol, nodes, exact, folded)
        if gene_id is None:
            unresolved.append({"edge": e.id, "mechanism": mech.id, "reason": "mechanism gene is not a Gene node (props.gene / symbol)"})
            continue
        hits[gene_id].append(MechanismHit(disease.id, mech.id, direction, somatic, e.id, gene_id))
    for lst in hits.values():
        lst.sort(key=lambda h: (h.disease_id, h.mechanism_id, h.has_mechanism_edge))
    return dict(hits), sorted(unresolved, key=lambda u: u["edge"])


def drug_targets(graph: Graph, *, pathway_level: bool = True) -> dict[str, list[TargetHit]]:
    """``{drug_id: [TargetHit]}`` from ``inhibits`` edges: Drug -> Gene directly and, when
    ``pathway_level``, Drug -> Pathway expanded to the pathway's member genes (``in_pathway``)."""
    nodes = graph.node_index()
    members: dict[str, set[str]] = defaultdict(set)
    if pathway_level:
        for e in graph.edges:
            if e.relation != REL_IN_PATHWAY:
                continue
            pair = _orient(nodes, e, NodeType.Gene)
            if pair is not None and pair[1].type == NodeType.Pathway:
                members[pair[1].id].add(pair[0].id)
    hits: dict[str, set[TargetHit]] = defaultdict(set)
    for e in graph.edges:
        if e.relation != REL_INHIBITS:
            continue
        pair = _orient(nodes, e, NodeType.Drug)
        if pair is None:
            continue
        drug, other = pair
        if other.type == NodeType.Gene:
            hits[drug.id].add(TargetHit(other.id, VIA_TARGET, e.id))
        elif other.type == NodeType.Pathway and pathway_level:
            for gene_id in members.get(other.id, ()):
                hits[drug.id].add(TargetHit(gene_id, VIA_PATHWAY, e.id, other.id))
    return {d: sorted(hs, key=_target_key) for d, hs in sorted(hits.items())}


def is_rule_edge(edge: Any) -> bool:
    """True for an edge this module emitted earlier (a ``sources[].id`` starting ``rules:``)."""
    return any(str(getattr(src, "id", "")).startswith(_RULE_SOURCE_PREFIX) for src in edge.sources)


def existing_treatment_pairs(graph: Graph, relations: Iterable[str]) -> tuple[dict[tuple[str, str], list[str]], int]:
    """``({(drug_id, disease_id): [relations]}, n_rule_edges_ignored)`` over Drug-Disease edges
    (either direction) whose relation is in ``relations``, any evidence class, EXCEPT the edges an
    earlier run of this module emitted (:func:`is_rule_edge`): the rules never treat their own
    output as a pre-existing assertion, so a graph that already carries a logic pass yields the
    same drafts as the graph without it."""
    wanted = set(relations)
    nodes = graph.node_index()
    out: dict[tuple[str, str], list[str]] = defaultdict(list)
    ignored = 0
    for e in graph.edges:
        if e.relation not in wanted:
            continue
        pair = _orient(nodes, e, NodeType.Drug)
        if pair is None or pair[1].type != NodeType.Disease:
            continue
        if is_rule_edge(e):
            ignored += 1
            continue
        out[(pair[0].id, pair[1].id)].append(e.relation)
    return {k: sorted(v) for k, v in out.items()}, ignored


def _target_key(hit: TargetHit) -> tuple[int, str, str, str]:
    return (0 if hit.via == VIA_TARGET else 1, hit.gene_id, hit.pathway_id or "", hit.inhibits_edge)


def _pair_key(item: tuple[TargetHit, MechanismHit]) -> tuple[int, str, str, str, str]:
    th, mh = item
    return (0 if th.via == VIA_TARGET else 1, mh.gene_id, mh.mechanism_id, th.pathway_id or "", th.inhibits_edge)


# --------------------------------------------------------------------------------------
# the rules
# --------------------------------------------------------------------------------------


def _label(nodes: Mapping[str, Node], node_id: str | None) -> str | None:
    n = nodes.get(node_id) if node_id else None
    return n.label if n is not None else None


def _hit_record(nodes: Mapping[str, Node], th: TargetHit, mh: MechanismHit, kind: str) -> dict[str, Any]:
    gene = nodes.get(mh.gene_id)
    return {
        "gene": mh.gene_id,
        "gene_symbol": _symbol(gene) if gene is not None else None,
        "mechanism": mh.mechanism_id,
        "direction": mh.direction,
        "somatic": mh.somatic,
        "classified_as": kind,
        "via": th.via,
        "pathway": th.pathway_id,
        "pathway_label": _label(nodes, th.pathway_id),
        "inhibits_edge": th.inhibits_edge,
        "has_mechanism_edge": mh.has_mechanism_edge,
    }


def _draft(
    nodes: Mapping[str, Node],
    drug_id: str,
    disease_id: str,
    rule: str,
    chosen: tuple[TargetHit, MechanismHit],
    hits: list[tuple[TargetHit, MechanismHit, str]],
    cfg: Mapping[str, Any],
    today: str,
) -> dict[str, Any]:
    th, mh = chosen
    base = float(cfg[rule]["confidence"])
    factor = float(cfg["pathway_level"]["confidence_factor"]) if th.via == VIA_PATHWAY else 1.0
    confidence = round(cap_inferred(base * factor), CONFIDENCE_DECIMALS)
    gene = nodes.get(mh.gene_id)
    if rule == RULE_DIRECTION_MISMATCH:
        status, reason, labels = "inferred_unsupported", REASON_DIRECTION_MISMATCH, list(LABELS_DIRECTION_MISMATCH)
    else:
        status, reason, labels = "supported", REASON_DIRECTION_MATCH, list(LABELS_DIRECTION_MATCH)
    basis: dict[str, Any] = {
        "rule": rule,
        "via": th.via,
        "drug_target_gene": mh.gene_id,
        "drug_target_symbol": _symbol(gene) if gene is not None else None,
        "mechanism": mh.mechanism_id,
        "mechanism_direction": mh.direction,
        "mechanism_somatic": mh.somatic,
        "drug_action": DRUG_ACTION,
        "pathway": th.pathway_id,
        "pathway_label": _label(nodes, th.pathway_id),
        "inhibits_edge": th.inhibits_edge,
        "has_mechanism_edge": mh.has_mechanism_edge,
        "base_confidence": base,
        "confidence_factor": factor,
        "confidence": confidence,
        "loss_directions": list(cfg[RULE_DIRECTION_MISMATCH]["loss_directions"]),
        "gain_directions": list(cfg[RULE_DIRECTION_MATCH]["gain_directions"]),
        "n_direction_mismatch_hits": sum(1 for _, _, k in hits if k == "loss"),
        "n_direction_match_hits": sum(1 for _, _, k in hits if k == "gain"),
        "hits": [_hit_record(nodes, t, m, k) for t, m, k in hits],
    }
    return {
        "source": drug_id,
        "target": disease_id,
        "relation": REL_TREATS,
        "evidence_class": "inferred",
        "confidence": confidence,
        "confidence_basis": basis,
        "status": status,
        "sources": [{"kind": "db", "id": f"{SOURCE_MODULE}:{rule}", "date": today}],
        "supporting": [],
        "contradicting": [],
        "attributes": {"reason": reason, "labels": labels, "rule": rule, "via": th.via},
    }


def run_rules(graph: Graph, cfg: Mapping[str, Any] | None, *, today: str) -> RulesResult:
    """Apply both rules (module docstring) and report every decision.

    ``cfg`` is the ``logic.yaml`` dict or its ``rules`` section (:func:`rules_config`);
    ``today`` is the build date written to ``sources[].date`` (ISO 8601, passed in so the run is
    reproducible).
    """
    if not isinstance(today, str) or not today.strip():
        raise ValueError("today must be a non-empty ISO 8601 date string")
    rules = rules_config(cfg)
    loss_dirs = {str(d) for d in rules[RULE_DIRECTION_MISMATCH]["loss_directions"]}
    gain_dirs = {str(d) for d in rules[RULE_DIRECTION_MATCH]["gain_directions"]}
    candidate_enabled = bool(rules[RULE_DIRECTION_MATCH]["enabled"])
    pathway_level = bool(rules["pathway_level"]["enabled"])

    nodes = graph.node_index()
    targets = drug_targets(graph, pathway_level=pathway_level)
    mech_by_gene, unresolved = mechanism_hits_by_gene(graph)
    existing, prior_rule_edges = existing_treatment_pairs(graph, rules["existing_treatment_relations"])

    drafts: list[dict[str, Any]] = []
    suppressed: dict[str, int] = defaultdict(int)
    emitted: dict[str, dict[str, int]] = {RULE_DIRECTION_MISMATCH: defaultdict(int), RULE_DIRECTION_MATCH: defaultdict(int)}
    pairs_considered = 0
    pairs_unclassified = 0
    pairs_mixed_direction = 0

    for drug_id in sorted(targets):
        per_disease: dict[str, list[tuple[TargetHit, MechanismHit]]] = defaultdict(list)
        for th in targets[drug_id]:
            for mh in mech_by_gene.get(th.gene_id, ()):
                per_disease[mh.disease_id].append((th, mh))
        for disease_id in sorted(per_disease):
            pairs_considered += 1
            prior = existing.get((drug_id, disease_id))
            if prior:
                for rel in prior:
                    suppressed[rel] += 1
                continue
            classified: list[tuple[TargetHit, MechanismHit, str]] = []
            for th, mh in sorted(per_disease[disease_id], key=_pair_key):
                kind = "loss" if mh.direction in loss_dirs else "gain" if mh.direction in gain_dirs else "other"
                classified.append((th, mh, kind))
            loss = [(t, m) for t, m, k in classified if k == "loss"]
            gain = [(t, m) for t, m, k in classified if k == "gain"]
            if loss and gain:
                pairs_mixed_direction += 1
            if loss:
                rule, chosen = RULE_DIRECTION_MISMATCH, loss[0]
            elif gain and candidate_enabled:
                rule, chosen = RULE_DIRECTION_MATCH, gain[0]
            else:
                pairs_unclassified += 1
                continue
            drafts.append(_draft(nodes, drug_id, disease_id, rule, chosen, classified, rules, today))
            emitted[rule][chosen[0].via] += 1

    drafts.sort(key=lambda d: (d["relation"], d["source"], d["target"]))
    report = {
        "today": today,
        "config": rules,
        "drugs_with_inhibits_edges": len(targets),
        "target_hits": {
            VIA_TARGET: sum(1 for hs in targets.values() for h in hs if h.via == VIA_TARGET),
            VIA_PATHWAY: sum(1 for hs in targets.values() for h in hs if h.via == VIA_PATHWAY),
        },
        "genes_with_mechanisms": len(mech_by_gene),
        "mechanism_edges_considered": sum(len(v) for v in mech_by_gene.values()),
        "mechanism_edges_unresolved": unresolved,
        "pairs_considered": pairs_considered,
        "prior_rule_edges_ignored": prior_rule_edges,
        "pairs_suppressed_by_relation": dict(sorted(suppressed.items())),
        "pairs_unclassified": pairs_unclassified,
        "pairs_mixed_direction": pairs_mixed_direction,
        "emitted": {rule: dict(sorted(by_via.items())) for rule, by_via in emitted.items()},
        "n_edges": len(drafts),
    }
    return RulesResult(drafts, report)


def apply_rules(graph: Graph, cfg: Mapping[str, Any] | None, *, today: str) -> list[dict[str, Any]]:
    """Edge drafts (no ``id``) for both rules; see :func:`run_rules` for the report."""
    return run_rules(graph, cfg, today=today).drafts


# --------------------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------------------


def summarize(drafts: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Counts by rule, by (rule, via) and by status."""
    by_rule: dict[str, int] = defaultdict(int)
    by_rule_via: dict[str, int] = defaultdict(int)
    by_status: dict[str, int] = defaultdict(int)
    n = 0
    for d in drafts:
        n += 1
        attrs = d.get("attributes") or {}
        rule, via = str(attrs.get("rule")), str(attrs.get("via"))
        by_rule[rule] += 1
        by_rule_via[f"{rule}/{via}"] += 1
        by_status[str(d.get("status"))] += 1
    return {
        "n_edges": n,
        "by_rule": dict(sorted(by_rule.items())),
        "by_rule_via": dict(sorted(by_rule_via.items())),
        "by_status": dict(sorted(by_status.items())),
    }


def examples_by_label(graph: Graph, drafts: Iterable[Mapping[str, Any]], limit: int = 10) -> list[str]:
    """Human-readable lines ``"<drug> -> <disease> [rule via conf] <mechanism>"``, rule-mismatch
    first, then by labels, at most ``limit``."""
    nodes = graph.node_index()
    rows: list[tuple[int, str, str, str]] = []
    for d in drafts:
        attrs = d.get("attributes") or {}
        basis = d.get("confidence_basis") or {}
        drug, disease = _label(nodes, d["source"]) or d["source"], _label(nodes, d["target"]) or d["target"]
        rule = str(attrs.get("rule"))
        line = f"{drug} -> {disease} [{rule} via {attrs.get('via')} conf {d.get('confidence')}] {basis.get('mechanism')}"
        rows.append((_RULE_ORDER.get(rule, 9), drug, disease, line))
    return [line for _, _, _, line in sorted(rows)[:limit]]


# --------------------------------------------------------------------------------------
# CLI (read-only dry run)
# --------------------------------------------------------------------------------------


def _load_logic_cfg() -> dict[str, Any]:
    """``pipeline/seeds/logic.yaml`` when it exists, else ``{}`` (defaults)."""
    try:
        from pipeline.config import load_seeds

        return load_seeds("logic")
    except (FileNotFoundError, ValueError):
        return {}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m pipeline.graph.rules", description="Dry-run the rule layer on a graph.json (nothing is written unless --out is given).")
    parser.add_argument("--in", dest="path", required=True, help="graph.json to read")
    parser.add_argument("--examples", type=int, default=10, help="how many example edges to print by label")
    parser.add_argument("--today", default=None, help="build date (ISO 8601); default: today's UTC date")
    parser.add_argument("--out", default=None, help="optional path to write the edge drafts (JSON list)")
    args = parser.parse_args(argv)

    graph = Graph.load(args.path)
    today = args.today or datetime.now(timezone.utc).date().isoformat()
    result = run_rules(graph, _load_logic_cfg(), today=today)
    out = {
        "graph": str(Path(args.path)),
        "report": result.report,
        "summary": summarize(result.drafts),
        "examples": examples_by_label(graph, result.drafts, args.examples),
    }
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(result.drafts, indent=2) + "\n", encoding="utf-8")
        out["drafts_written_to"] = str(Path(args.out))
    json.dump(out, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
