"""PubMed (and bioRxiv) retrieval for lane B.

Resolves the Section 11 papers by title (seeds hold titles and search terms, never
IDs), runs capped topic searches over the cluster, fetches abstracts and author
lists, and writes `data/build/papers.json`. Every search is logged with its query,
date and hit count, including zero-hit searches, so failed lookups become coverage
data (hard rule 3).

Run: `python -m pipeline.sources.pubmed`
"""
from __future__ import annotations

import datetime as dt
import difflib
import json
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from pathlib import Path

from pipeline.extract._compat import REPO_ROOT, http_get

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
BIORXIV = "https://api.biorxiv.org/details/biorxiv"
OUT = REPO_ROOT / "data" / "build" / "papers.json"

TOTAL_CAP = 300
TITLE_MATCH_MIN = 0.85

# Section 11 papers, by title. `doi` is only used for the one preprint, whose DOI
# the cluster doc gives; the fetched record's title is still checked against ours.
SECTION11 = [
    {"key": "zimmerman2017", "title": "Risks of Ruxolitinib in STAT1 Gain-of-Function-Associated Severe Fungal Disease"},
    {"key": "rux_th_2017", "title": "Ruxolitinib reverses dysregulated T helper cell responses and controls autoimmunity caused by a novel STAT1 gain-of-function mutation"},
    {"key": "vargas_nk", "title": "Ruxolitinib partially reverses functional NK cell deficiency in patients with STAT1 gain-of-function mutations", "preprint_doi": "10.1101/157271"},
    {"key": "jci2024", "title": "Ruxolitinib Improves Immune-Dysregulation Features but not Epigenetic Abnormality in a Patient with STAT1 GOF"},
    {"key": "stat1_sysreview", "title": "Clinical Relevance of Gain- and Loss-of-Function Germline Mutations in STAT1: A Systematic Review"},
    {"key": "stat1_transcriptional", "title": "Transcriptional Profiling of STAT1 Gain-of-Function Reveals Common and Mutation-Specific Fingerprints"},
    {"key": "usidnet_type2", "title": "Inborn Errors of Immunity Associated With Type 2 Inflammation in the USIDNET Registry"},
]

# Topic searches: (label, query, cap). Caps sum under TOTAL_CAP after dedup.
TOPICS = [
    ("stat1_gof_jaki", '(STAT1 AND ("gain of function" OR "gain-of-function" OR GOF)) AND (ruxolitinib OR baricitinib OR tofacitinib OR "JAK inhibitor" OR "JAK inhibitors")', 60),
    ("stat1_gof", '(STAT1 AND ("gain of function" OR "gain-of-function")) AND (candidiasis OR "chronic mucocutaneous" OR autoimmunity OR variant OR mutation)', 50),
    ("stat1_lof", '(STAT1 AND ("loss of function" OR "loss-of-function" OR deficiency)) AND (mycobacterial OR mycobacteria OR herpes OR infection)', 30),
    ("stat3_gof", '(STAT3 AND ("gain of function" OR "gain-of-function")) AND (autoimmunity OR lymphoproliferation OR ruxolitinib OR tocilizumab)', 30),
    ("stat3_hies", '(STAT3 AND ("hyper-IgE" OR "hyper IgE" OR "Job syndrome")) AND (dominant negative OR mutation)', 25),
    ("jak3_scid", '(JAK3 AND ("severe combined immunodeficiency" OR SCID))', 20),
    ("il2rg_scid", '(IL2RG AND ("severe combined immunodeficiency" OR SCID OR X-linked))', 20),
    ("jak1", '(JAK1 AND ("gain of function" OR "loss of function" OR germline) AND (immunodeficiency OR eosinophilia OR mycobacterial))', 15),
    ("tyk2", '(TYK2 AND (deficiency OR "loss of function" OR recessive) AND (infection OR mycobacterial OR autoimmun*))', 15),
    ("jak2_mpn", '(JAK2 V617F AND (polycythemia vera OR essential thrombocythemia OR myelofibrosis) AND ruxolitinib)', 30),
    ("jaki_lof_scid", '((ruxolitinib OR "JAK inhibitor") AND ("severe combined immunodeficiency" OR SCID))', 10),
]


@dataclass
class Author:
    last: str
    fore: str
    initials: str
    affiliations: list[str] = field(default_factory=list)
    orcid: str | None = None


@dataclass
class Paper:
    pmid: str | None
    doi: str | None
    title: str
    abstract: str
    journal: str
    year: int | None
    pub_types: list[str]
    authors: list[Author]
    mesh: list[str]
    preprint: bool = False
    found_by: list[str] = field(default_factory=list)


def today() -> str:
    return dt.date.today().isoformat()


def norm_title(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()


STOPWORDS = {"with", "from", "that", "this", "into", "than", "their", "after", "between", "caused", "associated"}


def title_similarity(candidate: str, seed: str) -> float:
    """Max of character similarity and seed-token containment.

    Containment handles published titles that expand an abbreviation the seed kept
    short (e.g. "signal transducer and activator of transcription 1 (STAT1)").
    """
    a, b = norm_title(candidate), norm_title(seed)
    ratio = difflib.SequenceMatcher(None, a, b).ratio()
    seed_tokens = b.split()
    cand = set(a.split())
    containment = sum(t in cand for t in seed_tokens) / len(seed_tokens) if seed_tokens else 0.0
    return max(ratio, containment)


def esearch(term: str, retmax: int) -> tuple[list[str], int]:
    d = http_get(f"{EUTILS}/esearch.fcgi",
                 {"db": "pubmed", "term": term, "retmax": retmax, "retmode": "json", "sort": "relevance"},
                 as_json=True)
    r = d["esearchresult"]
    return r.get("idlist", []), int(r.get("count", 0))


def _text(el: ET.Element | None) -> str:
    return "".join(el.itertext()).strip() if el is not None else ""


def parse_pubmed_xml(xml: str) -> list[Paper]:
    papers = []
    for art in ET.fromstring(xml).findall("PubmedArticle"):
        mc = art.find("MedlineCitation")
        a = mc.find("Article")
        parts = []
        for at in a.findall("Abstract/AbstractText"):
            label, body = at.get("Label"), _text(at)
            if body:
                parts.append(f"{label}: {body}" if label else body)
        year = None
        for path in ("Journal/JournalIssue/PubDate/Year", "ArticleDate/Year"):
            if (y := a.find(path)) is not None and y.text and y.text.isdigit():
                year = int(y.text)
                break
        if year is None and (md := _text(a.find("Journal/JournalIssue/PubDate/MedlineDate"))):
            m = re.search(r"\d{4}", md)
            year = int(m.group()) if m else None
        authors = []
        for au in a.findall("AuthorList/Author"):
            if au.find("LastName") is None:
                continue
            orcid = next((_text(i) for i in au.findall("Identifier") if i.get("Source") == "ORCID"), None)
            if orcid:
                orcid = orcid.rsplit("/", 1)[-1]
            authors.append(Author(_text(au.find("LastName")), _text(au.find("ForeName")),
                                  _text(au.find("Initials")),
                                  [_text(x) for x in au.findall("AffiliationInfo/Affiliation")], orcid))
        doi = next((_text(i) for i in art.findall("PubmedData/ArticleIdList/ArticleId")
                    if i.get("IdType") == "doi"), None)
        papers.append(Paper(
            pmid=_text(mc.find("PMID")), doi=doi, title=_text(a.find("ArticleTitle")),
            abstract="\n".join(parts), journal=_text(a.find("Journal/Title")), year=year,
            pub_types=[_text(p) for p in a.findall("PublicationTypeList/PublicationType")],
            authors=authors, mesh=[_text(m) for m in mc.findall("MeshHeadingList/MeshHeading/DescriptorName")],
        ))
    return papers


def efetch(pmids: list[str]) -> list[Paper]:
    out = []
    for i in range(0, len(pmids), 100):
        batch = sorted(pmids[i:i + 100])  # sorted so the cache key is stable
        xml = http_get(f"{EUTILS}/efetch.fcgi", {"db": "pubmed", "id": ",".join(batch), "retmode": "xml"})
        out += parse_pubmed_xml(xml)
    return out


def fetch_biorxiv(doi: str) -> Paper | None:
    d = http_get(f"{BIORXIV}/{doi}/na/json", as_json=True)
    coll = d.get("collection") or []
    if not coll:
        return None
    rec = coll[-1]  # latest version
    authors = [Author(last=n.split(",")[0].strip(), fore=(n.split(",")[1].strip() if "," in n else ""), initials="")
               for n in rec.get("authors", "").split(";") if n.strip()]
    return Paper(pmid=None, doi=rec["doi"], title=rec["title"], abstract=rec.get("abstract", "").strip(),
                 journal="bioRxiv", year=int(rec["date"][:4]), pub_types=["Preprint"], authors=authors,
                 mesh=[], preprint=True)


def resolve_title(seed: dict, coverage: list[dict]) -> Paper | None:
    """Exact-title search, then a looser keyword search; accept only a close title match."""
    title = seed["title"]
    words = sorted({w for w in norm_title(title).split() if len(w) > 3 and w not in STOPWORDS},
                   key=lambda w: (-len(w), w))[:5]
    for label, term in (("title_exact", f'"{title}"[ti]'),
                        ("title_words", " AND ".join(f"{w}[ti]" for w in words))):
        ids, count = esearch(term, 10)
        coverage.append({"source": "pubmed", "query": term, "date": today(), "hits": count, "for": seed["key"]})
        if not ids:
            continue
        best = max(efetch(ids), key=lambda p: title_similarity(p.title, title))
        if title_similarity(best.title, title) >= TITLE_MATCH_MIN:
            best.found_by.append(f"section11:{seed['key']}")
            return best
    if doi := seed.get("preprint_doi"):
        p = fetch_biorxiv(doi)
        ok = p is not None and title_similarity(p.title, title) >= TITLE_MATCH_MIN
        coverage.append({"source": "biorxiv", "query": doi, "date": today(), "hits": int(ok), "for": seed["key"]})
        if ok:
            p.found_by.append(f"section11:{seed['key']}")
            return p
    return None


def build() -> dict:
    coverage: list[dict] = []
    by_id: dict[str, Paper] = {}
    unresolved = []

    for seed in SECTION11:
        p = resolve_title(seed, coverage)
        if p is None:
            unresolved.append(seed)
            continue
        k = p.pmid or f"doi:{p.doi}"
        if k in by_id:
            by_id[k].found_by += p.found_by
        else:
            by_id[k] = p

    for label, query, cap in TOPICS:
        ids, count = esearch(query, cap)
        coverage.append({"source": "pubmed", "query": query, "date": today(), "hits": count,
                         "for": f"topic:{label}", "kept": len(ids)})
        new = [i for i in ids if i not in by_id]
        room = TOTAL_CAP - len(by_id)
        for p in efetch(new[:max(room, 0)]):
            p.found_by.append(f"topic:{label}")
            by_id[p.pmid] = p
        for i in ids:
            if i in by_id and f"topic:{label}" not in by_id[i].found_by:
                by_id[i].found_by.append(f"topic:{label}")

    papers = sorted(by_id.values(), key=lambda p: (not any(f.startswith("section11") for f in p.found_by), p.pmid or ""))
    return {
        "built_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "papers": [asdict(p) for p in papers],
        "coverage": coverage,
        "unresolved": unresolved,
    }


def main() -> None:
    result = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=1, ensure_ascii=False))
    s11 = [p for p in result["papers"] if any(f.startswith("section11") for f in p["found_by"])]
    no_abs = sum(1 for p in result["papers"] if not p["abstract"])
    print(f"papers: {len(result['papers'])} (section 11 resolved: {len(s11)}/{len(SECTION11)}, "
          f"no abstract: {no_abs})", file=sys.stderr)
    for s in result["unresolved"]:
        print(f"  unresolved (coverage gap): {s['key']}: {s['title']}", file=sys.stderr)


if __name__ == "__main__":
    main()
