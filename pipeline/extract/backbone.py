"""Candidate nodes for `reconcile`, built without waiting for lane A's graph.

The contract fixes node ID formats (pipeline/models.py "ID conventions") and M0's
OptimusKG spike fixes which backbone files lane A loads, so lane B can resolve
claim mentions to the same IDs lane A will emit:

- Gene, Disease, Phenotype, Drug, Pathway: read from the OptimusKG node tables in
  ATLAS_RAW_DIR/optimuskg/, IDs normalised per the contract (`ensembl:ENSG...`,
  underscore CURIEs → colon, ChEMBL ids prefixed `CHEMBL:` as lane A's pipeline/ids.py does).
- Mechanism: `mech:<GENE>:<GoF|LoF|DN>[:somatic]` for the cluster genes (internal ids).
- Variant: looked up in ClinVar on demand (`clinvar:<variation id>`).

Nothing here is hand-typed: every external id comes from a file or an API response.
When graph.json exists, `reconcile` checks every resolved id against it.
"""
from __future__ import annotations

import math
import os
import re
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

from pipeline.extract._compat import REPO_ROOT, http_get

CLUSTER_GENES = ["STAT1", "STAT3", "JAK1", "JAK2", "JAK3", "TYK2", "IL2RG"]
DIRECTIONS = {"GoF": "gain of function", "LoF": "loss of function", "DN": "dominant negative"}
# Disease-table prefixes that are measurement traits or non-disease ontologies, not diseases.
NON_DISEASE_PREFIXES = ("OBA_", "MP_", "PATO_", "OBI_", "NCIT_", "OTAR_")

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


def raw_dir() -> Path:
    d = Path(os.environ.get("ATLAS_RAW_DIR", "data/raw"))
    return (d if d.is_absolute() else REPO_ROOT / d) / "optimuskg"


def norm_curie(raw: str) -> str:
    """`MONDO_0013599` → `MONDO:0013599`; ids that already have a colon, or have no prefix
    (ChEMBL), are returned unchanged."""
    if ":" in raw:
        return raw
    m = re.match(r"^([A-Za-z]+)_(.+)$", raw)
    return f"{m.group(1)}:{m.group(2)}" if m else raw


def drug_id(raw: str) -> str:
    """Lane A's rule (pipeline/ids.py normalize_curie with hint "CHEMBL"): ids with a colon are kept
    (`DRUGBANK:DB00004`); bare ChEMBL ids get the prefix: `CHEMBL1789941` -> `CHEMBL:CHEMBL1789941`."""
    return raw if ":" in raw else f"CHEMBL:{raw}"


def gene_id(raw: str) -> str:
    return f"ensembl:{raw}" if raw.startswith("ENSG") else norm_curie(raw)


def _names(*lists) -> list[str]:
    seen, out = set(), []
    for lst in lists:
        for x in lst or []:
            if isinstance(x, dict):
                x = x.get("label")
            if x and x.lower() not in seen:
                seen.add(x.lower())
                out.append(x)
    return out


@lru_cache(maxsize=1)
def load_nodes() -> tuple[dict, ...]:
    """All backbone candidate nodes in the contract's node shape (props kept small)."""
    import polars as pl

    R = raw_dir()
    nodes: list[dict] = []

    g = pl.read_parquet(R / "gene.parquet", columns=["id", "properties"]).unnest("properties")
    for r in g.select("id", "symbol", "name", "symbol_synonyms", "obsolete_symbols").iter_rows(named=True):
        if not r["symbol"]:
            continue
        nodes.append({"id": gene_id(r["id"]), "type": "Gene", "label": r["symbol"],
                      "synonyms": _names([r["name"]], r["symbol_synonyms"], r["obsolete_symbols"]),
                      "props": {"symbol": r["symbol"], "backbone_id": r["id"]}})

    for table, ntype in (("disease", "Disease"), ("phenotype", "Phenotype")):
        df = pl.read_parquet(R / f"{table}.parquet", columns=["id", "properties"]).unnest("properties")
        for r in df.select("id", "name", "exact_synonyms", "related_synonyms").iter_rows(named=True):
            if not r["name"] or r["id"].startswith(NON_DISEASE_PREFIXES):
                continue
            if ntype == "Phenotype" and not r["id"].startswith("HP_"):
                continue
            nodes.append({"id": norm_curie(r["id"]), "type": ntype, "label": r["name"],
                          "synonyms": _names(r["exact_synonyms"], r["related_synonyms"]),
                          "props": {"backbone_id": r["id"]}})

    d = pl.read_parquet(R / "drug.parquet", columns=["id", "properties"]).select(
        "id", pl.col("properties").struct.field("name"), pl.col("properties").struct.field("synonyms"),
        pl.col("properties").struct.field("trade_names"))
    for r in d.iter_rows(named=True):
        if r["name"]:
            nodes.append({"id": drug_id(r["id"]), "type": "Drug", "label": r["name"].lower(),
                          "synonyms": _names(r["synonyms"], r["trade_names"]), "props": {"backbone_id": r["id"]}})

    p = pl.read_parquet(R / "pathway.parquet", columns=["id", "properties"]).select(
        "id", pl.col("properties").struct.field("name"))
    for r in p.iter_rows(named=True):
        if r["name"]:
            nodes.append({"id": r["id"], "type": "Pathway", "label": r["name"], "synonyms": [], "props": {}})

    nodes += mechanism_nodes()
    return tuple(nodes)


def mechanism_nodes() -> list[dict]:
    out = []
    for gene in CLUSTER_GENES:
        for code, words in DIRECTIONS.items():
            syn = [f"{gene} {code}", f"{gene} {words}", f"{gene} {words.replace(' ', '-')}",
                   f"{gene} {code} mutations", f"{words} {gene} mutations", f"{gene} {words} variants"]
            out.append({"id": f"mech:{gene}:{code}", "type": "Mechanism", "label": f"{gene} {words}",
                        "synonyms": syn, "props": {"gene_symbol": gene, "direction": code}})
    out.append({"id": "mech:JAK2:GoF:somatic", "type": "Mechanism", "label": "JAK2 gain of function (somatic)",
                "synonyms": ["somatic JAK2 GoF", "JAK2 V617F", "somatic JAK2 gain-of-function"],
                "props": {"gene_symbol": "JAK2", "direction": "GoF", "somatic": True}})
    return out


MAX_GENES_PER_SPECIFIC_DISEASE = 60


@lru_cache(maxsize=1)
def gene_linked_diseases() -> dict[str, list[str]]:
    """Cluster gene symbol → specific disease ids (contract form) the backbone links to it.

    DIS-GEN links STAT1 alone to ~2,300 diseases, led by umbrella terms ("immune system
    disease"). Keeping diseases linked to at most MAX_GENES_PER_SPECIFIC_DISEASE genes, then
    ordering by evidence_score, puts the Mendelian entities first (no hand-picked ids)."""
    import polars as pl

    genes = {n["props"]["backbone_id"]: n["label"] for n in load_nodes()
             if n["type"] == "Gene" and n["label"] in CLUSTER_GENES}
    edges = pl.scan_parquet(raw_dir() / "disease_gene.parquet")
    degree = edges.group_by("from").agg(pl.len().alias("n_genes"))
    dg = (edges.filter(pl.col("to").is_in(list(genes)))
          .select("from", "to", pl.col("properties").struct.field("evidence_score"))
          .join(degree, on="from")
          .filter(pl.col("n_genes") <= MAX_GENES_PER_SPECIFIC_DISEASE)
          .collect())
    out: dict[str, list[tuple[float, str]]] = defaultdict(list)
    for r in dg.iter_rows(named=True):
        if not r["from"].startswith(NON_DISEASE_PREFIXES):
            out[genes[r["to"]]].append((r["evidence_score"] or 0.0, norm_curie(r["from"])))
    return {g: [d for _, d in sorted(v, reverse=True)] for g, v in out.items()}


def clinvar_variants(gene: str, protein_change: str, limit: int = 5) -> list[dict]:
    """ClinVar records for `gene` + a protein change like 'R274W', as candidate Variant nodes."""
    term = f"{gene}[gene] AND {protein_change}"
    ids = http_get(f"{EUTILS}/esearch.fcgi", {"db": "clinvar", "term": term, "retmode": "json",
                                              "retmax": limit}, as_json=True)["esearchresult"].get("idlist", [])
    if not ids:
        return []
    summ = http_get(f"{EUTILS}/esummary.fcgi", {"db": "clinvar", "id": ",".join(sorted(ids)), "retmode": "json"},
                    as_json=True)["result"]
    out = []
    for uid in ids:
        s = summ.get(uid, {})
        sig = (s.get("germline_classification") or {}).get("description", "")
        out.append({"id": f"clinvar:{uid}", "type": "Variant", "label": s.get("title", uid),
                    "synonyms": [protein_change, f"{gene} {protein_change}"],
                    "props": {"gene_symbol": gene, "protein_change": s.get("protein_change", ""),
                              "clinical_significance": sig}})
    return out


# --- lexical shortlist over a large vocabulary ------------------------------------------

_TOKEN = re.compile(r"[a-z0-9]+")


def tokens(s: str) -> list[str]:
    return _TOKEN.findall(s.lower())


class LexicalIndex:
    """IDF-weighted token overlap over node names. Used to cut the backbone (~110k nodes,
    several names each) down to a few dozen candidates before embeddings rerank them."""

    def __init__(self, nodes: list[dict]):
        self.nodes = {n["id"]: n for n in nodes}
        self.postings: dict[str, set[str]] = defaultdict(set)
        for n in nodes:
            for name in [n["label"], *n.get("synonyms", [])]:
                for t in tokens(name):
                    self.postings[t].add(n["id"])
        N = max(len(self.nodes), 1)
        self.idf = {t: math.log(N / len(ids)) for t, ids in self.postings.items()}

    def search(self, text: str, k: int = 30) -> list[str]:
        qt = sorted(set(tokens(text)))
        scores: dict[str, float] = defaultdict(float)
        for t in qt:
            ids = self.postings.get(t)
            if ids and len(ids) < 5000:  # skip near-stopwords ("disease", "syndrome")
                for nid in ids:
                    scores[nid] += self.idf[t]
        return sorted(scores, key=lambda i: (-scores[i], i))[:k]  # id tie-break: same pool every run
