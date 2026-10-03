"""Investigators from PubMed author lists (README Section 5, "Grants / investigators").

Deterministic, no LLM. Authors are grouped by ORCID when present (confidence
`high`), otherwise by last name + first initial + a shared affiliation token
(confidence `low`). Each investigator is described only as "author of N papers on
<topic>", never as a field leader. A contact route is recorded only when the
paper itself prints an email in the author's affiliation (the corresponding-author
address); emails are never guessed.

Reads `data/build/papers.json`, writes `data/build/investigators.json`.
Run: `python -m pipeline.extract.investigators`
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from collections import defaultdict

from pipeline.extract._compat import REPO_ROOT

PAPERS = REPO_ROOT / "data" / "build" / "papers.json"
OUT = REPO_ROOT / "data" / "build" / "investigators.json"

EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
AFFIL_STOP = {"department", "dept", "university", "of", "and", "the", "for", "institute", "school", "medicine",
              "medical", "center", "centre", "hospital", "usa", "division", "laboratory", "research", "national",
              "health", "clinical", "sciences", "science", "college", "faculty", "unit", "program"}

# Topic labels from the search that found the paper (pubmed.TOPICS / SECTION11).
TOPIC_NAMES = {
    "stat1_gof_jaki": "JAK inhibitors in STAT1 gain of function",
    "stat1_gof": "STAT1 gain of function",
    "stat1_lof": "STAT1 loss of function",
    "stat3_gof": "STAT3 gain of function",
    "stat3_hies": "STAT3 hyper-IgE syndrome",
    "jak3_scid": "JAK3 SCID",
    "il2rg_scid": "IL2RG SCID",
    "jak1": "JAK1 disease",
    "tyk2": "TYK2 deficiency",
    "jak2_mpn": "JAK2 myeloproliferative neoplasms",
    "jaki_lof_scid": "JAK inhibitors and SCID",
}


def affil_tokens(affs: list[str]) -> set[str]:
    words = set()
    for a in affs:
        a = EMAIL.sub(" ", a)
        words |= {w for w in re.findall(r"[a-z]{4,}", a.lower()) if w not in AFFIL_STOP}
    return words


def topics_of(paper: dict) -> list[str]:
    out = []
    for f in paper["found_by"]:
        kind, _, key = f.partition(":")
        if kind == "topic":
            out.append(TOPIC_NAMES.get(key, key))
        elif kind == "section11":
            out.append("Section 11 key paper")
    return out


def build(papers: list[dict]) -> list[dict]:
    groups: dict[str, dict] = {}
    by_name: dict[str, list[str]] = defaultdict(list)  # name key → group ids without ORCID

    for p in papers:
        ref = p["pmid"] or f"doi:{p['doi']}"
        for pos, a in enumerate(p["authors"]):
            if not a["last"]:
                continue
            name_key = f"{a['last'].lower()}|{(a['fore'] or a['initials'] or '?')[0].lower()}"
            toks = affil_tokens(a["affiliations"])
            gid = None
            if a.get("orcid"):
                gid = f"orcid:{a['orcid']}"
            else:
                for cand in by_name[name_key]:
                    if groups[cand]["_tokens"] & toks:
                        gid = cand
                        break
                if gid is None and not toks:
                    # No affiliation printed: join the one existing group with the identical full name, if unique.
                    full = f"{a['fore']} {a['last']}".strip().lower()
                    same = [c for c in by_name[name_key] if groups[c]["name"].lower() == full]
                    same += [c for c, g in groups.items() if c.startswith("orcid:") and g["name"].lower() == full]
                    if len(set(same)) == 1:
                        gid = same[0]
                if gid is None:
                    gid = "inv:" + hashlib.sha1(f"{name_key}|{sorted(toks)[:5]}|{ref}".encode()).hexdigest()[:10]
                    by_name[name_key].append(gid)
            g = groups.setdefault(gid, {
                "id": gid, "name": f"{a['fore']} {a['last']}".strip(), "orcid": a.get("orcid"),
                "confidence": "high" if a.get("orcid") else "low",
                "affiliations": [], "papers": [], "topics": defaultdict(int), "contact": None,
                "_tokens": set(), "_last_author_count": 0,
            })
            g["_tokens"] |= toks
            if ref not in g["papers"]:
                g["papers"].append(ref)
                for t in topics_of(p):
                    g["topics"][t] += 1
            for aff in a["affiliations"]:
                clean = EMAIL.sub("", aff).strip(" .;,")
                if clean and clean not in g["affiliations"]:
                    g["affiliations"].append(clean)
                if g["contact"] is None and (m := EMAIL.search(aff)):
                    g["contact"] = {"email": m.group().rstrip("."), "source": {"kind": "pmid" if p["pmid"] else "doi",
                                                                              "id": p["pmid"] or p["doi"]}}
            if pos == len(p["authors"]) - 1 and len(p["authors"]) > 1:
                g["_last_author_count"] += 1

    # Merge ORCID-less groups into an ORCID group with the same name and a shared affiliation token.
    for gid in [g for g in groups if g.startswith("inv:")]:
        g = groups[gid]
        for oid, o in groups.items():
            same_name = o["name"].lower() == g["name"].lower()
            if oid.startswith("orcid:") and o["name"].split()[-1].lower() == g["name"].split()[-1].lower() \
                    and o["name"][:1].lower() == g["name"][:1].lower() \
                    and (o["_tokens"] & g["_tokens"] or (same_name and not g["_tokens"])):
                for ref in g["papers"]:
                    if ref not in o["papers"]:
                        o["papers"].append(ref)
                for t, n in g["topics"].items():
                    o["topics"][t] += n
                o["affiliations"] += [x for x in g["affiliations"] if x not in o["affiliations"]]
                o["contact"] = o["contact"] or g["contact"]
                o["_last_author_count"] += g["_last_author_count"]
                del groups[gid]
                break

    out = []
    for g in groups.values():
        topics = dict(sorted(g["topics"].items(), key=lambda kv: -kv[1]))
        main = next((t for t in topics if t != "Section 11 key paper"), None)
        n = len(g["papers"])
        out.append({
            "id": g["id"], "name": g["name"], "orcid": g["orcid"], "confidence": g["confidence"],
            "affiliations": g["affiliations"][:5], "papers": g["papers"], "n_papers": n,
            "last_author_papers": g["_last_author_count"], "topics": topics,
            "description": f"author of {n} paper{'s' if n != 1 else ''}" + (f" on {main}" if main else ""),
            "contact": g["contact"],
        })
    out.sort(key=lambda g: (-g["n_papers"], g["name"]))
    return out


def main() -> None:
    papers = json.loads(PAPERS.read_text())["papers"]
    invs = build(papers)
    OUT.write_text(json.dumps({"investigators": invs}, indent=1, ensure_ascii=False))
    multi = [i for i in invs if i["n_papers"] > 1]
    print(f"investigators: {len(invs)} ({len(multi)} with 2+ papers, "
          f"{sum(1 for i in invs if i['orcid'])} with ORCID, {sum(1 for i in invs if i['contact'])} with a public email)",
          file=sys.stderr)
    for i in invs[:12]:
        print(f"  {i['name']:<28} {i['description']:<60} {i['confidence']}", file=sys.stderr)


if __name__ == "__main__":
    main()
