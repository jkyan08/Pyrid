"""Claim extraction from PubMed abstracts (README Section 7, use 1).

Structured outputs turn each abstract into claim objects in the `graph.json`
contract shape. Every claim must carry a verbatim quote that string-matches its
abstract after whitespace normalization (hard rule 5); claims that fail are
dropped and counted, never repaired.

Reads `data/build/papers.json`, writes `data/build/claims.json`.
Run: `python -m pipeline.extract.claims`
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import Literal, Optional

from pydantic import BaseModel, Field

from pipeline.extract._compat import REPO_ROOT
from pipeline.llm.client import chat_model, parse

PAPERS = REPO_ROOT / "data" / "build" / "papers.json"
OUT = REPO_ROOT / "data" / "build" / "claims.json"

PROMPT_VERSION = "claims-v1"

Predicate = Literal["treats", "causes", "has_phenotype", "has_mechanism", "modulates", "associated_with"]
StudyType = Literal["trial", "cohort", "case_series", "case_report", "in_vitro", "review"]


class ExtractedClaim(BaseModel):
    quote: str = Field(description="One or two consecutive sentences copied character-for-character from the abstract.")
    subject: str = Field(description="Entity as named in the abstract: a drug, gene, variant, mechanism or disease.")
    predicate: Predicate
    object: str = Field(description="Entity as named in the abstract.")
    polarity: Literal["supports", "contradicts"] = Field(
        description="'contradicts' when the quote reports the relation failing or not holding, e.g. a treatment failure.")
    variant: Optional[str] = Field(description="Protein-level variant if the claim is about a specific one, e.g. 'R274W'; else null.")
    n_patients: Optional[int] = Field(description="Number of patients the claim rests on, if stated; else null.")


class PaperExtraction(BaseModel):
    study_type: StudyType
    claims: list[ExtractedClaim]


SYSTEM = """You extract evidence claims from biomedical abstracts for a rare-disease research atlas focused on the JAK-STAT pathway \
(STAT1, STAT3, JAK1, JAK2, JAK3, TYK2, IL2RG).

Rules:
- Extract only claims the abstract itself reports as findings. Skip background statements, aims and speculation.
- Prefer claims about: drug effects on a disease or patients (treats), variant or gene to mechanism direction \
(has_mechanism: gain of function, loss of function, dominant negative), gene or variant causing a disease (causes), \
disease or variant to clinical features (has_phenotype), drug effects on a molecular readout (modulates).
- quote MUST be copied exactly from the abstract, including punctuation and capitalization. Never paraphrase, never join \
non-adjacent text, never fix typos.
- A treatment that did not work, or that worsened outcomes, is predicate 'treats' with polarity 'contradicts'.
- Normalize the claim's direction to the positive relation; use polarity for negation.
- study_type describes the paper: trial, cohort, case_series (2+ patients), case_report (1 patient), in_vitro, review.
- Return at most 8 claims, the most informative first. Return no claims if the abstract has none in scope.
- This is research navigation. Do not include dosing."""


def normalize_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def quote_matches(quote: str, abstract: str) -> bool:
    q = normalize_ws(quote)
    return bool(q) and q in normalize_ws(abstract)


def paper_ref(p: dict) -> str:
    return p["pmid"] or f"doi:{p['doi']}"


def extract_paper(p: dict) -> tuple[str | None, list[dict], int]:
    """Returns (study_type, kept claims, dropped count) for one paper."""
    if not p["abstract"]:
        return None, [], 0
    user = f"[{PROMPT_VERSION}]\nTitle: {p['title']}\nPublication types: {', '.join(p['pub_types'])}\n\nAbstract:\n{p['abstract']}"
    result = parse(SYSTEM, user, PaperExtraction)
    if result is None:
        return None, [], 0
    kept, dropped = [], 0
    for c in result.claims:
        if not quote_matches(c.quote, p["abstract"]):
            dropped += 1
            continue
        kept.append({
            "pmid": paper_ref(p), "quote": normalize_ws(c.quote), "polarity": c.polarity,
            "subject": c.subject, "predicate": c.predicate, "object": c.object,
            "variant": c.variant or "", "study_type": result.study_type, "n_patients": c.n_patients,
        })
    return result.study_type, kept, dropped


def build(papers: list[dict], workers: int = 8) -> dict:
    with ThreadPoolExecutor(workers) as pool:
        results = list(pool.map(extract_paper, papers))
    claims, per_paper, dropped_total, seen = [], [], 0, set()
    for p, (study_type, kept, dropped) in zip(papers, results):
        dropped_total += dropped
        n_kept = 0
        for c in kept:
            key = (c["pmid"], c["quote"], c["subject"], c["predicate"], c["object"])
            if key in seen:
                continue
            seen.add(key)
            c = {"id": f"c_{len(claims) + 1:06d}", **c}
            claims.append(c)
            n_kept += 1
        per_paper.append({"ref": paper_ref(p), "study_type": study_type, "kept": n_kept, "dropped": dropped,
                          "has_abstract": bool(p["abstract"])})
    extracted = len(claims) + dropped_total
    return {
        "built_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "model": chat_model(),
        "prompt_version": PROMPT_VERSION,
        "claims": claims,
        "stats": {
            "papers": len(papers),
            "papers_with_abstract": sum(1 for p in papers if p["abstract"]),
            "extracted": extracted,
            "kept": len(claims),
            "dropped_quote_mismatch": dropped_total,
            "drop_rate": round(dropped_total / extracted, 4) if extracted else 0.0,
        },
        "per_paper": per_paper,
    }


def main() -> None:
    papers = json.loads(PAPERS.read_text())["papers"]
    result = build(papers)
    OUT.write_text(json.dumps(result, indent=1, ensure_ascii=False))
    print(json.dumps(result["stats"]), file=sys.stderr)


if __name__ == "__main__":
    main()
