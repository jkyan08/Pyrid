"""Lane B unit tests. No network and no OpenAI calls: the LLM and embedder are faked."""
from __future__ import annotations

import pytest

from eval.precision import precision
from pipeline.extract import claims as claims_mod
from pipeline.extract.claims import ExtractedClaim, PaperExtraction, quote_matches
from pipeline.extract.reconcile import Adjudication, NodeIndex, link, link_claims
from pipeline.llm.draft import NOTICE, draft
from pipeline.llm.explain import CitedSentence, CitedText, explain, subgraph
from pipeline.llm.validate import validate_sentences
from pipeline.sources.pubmed import parse_pubmed_xml, title_similarity

# Placeholder graph: labels are clearly fake and there are no real identifiers.
GRAPH = {
    "nodes": [
        {"id": "n:drugA", "type": "Drug", "label": "Drug A", "synonyms": ["DA-1"], "props": {}},
        {"id": "n:disX", "type": "Disease", "label": "Disease X gain of function", "synonyms": ["DX GoF"], "props": {}},
        {"id": "n:disY", "type": "Disease", "label": "Disease X loss of function", "synonyms": [], "props": {}},
        {"id": "n:geneG", "type": "Gene", "label": "GENEG", "synonyms": [], "props": {}},
    ],
    "edges": [
        {"id": "e_000001", "source": "n:drugA", "target": "n:disX", "relation": "treats", "evidence_class": "extracted",
         "confidence": 0.4, "confidence_basis": {}, "status": "contested", "sources": [],
         "supporting": ["c_000001"], "contradicting": ["c_000002"], "attributes": {}},
        {"id": "e_000002", "source": "n:geneG", "target": "n:disX", "relation": "causes", "evidence_class": "observed",
         "confidence": 0.9, "confidence_basis": {}, "status": "supported", "sources": [],
         "supporting": [], "contradicting": [], "attributes": {"origin": "germline"}},
        {"id": "e_000003", "source": "n:drugA", "target": "n:disY", "relation": "treats", "evidence_class": "inferred",
         "confidence": 0.1, "confidence_basis": {}, "status": "inferred_unsupported", "sources": [],
         "supporting": [], "contradicting": [], "attributes": {}},
    ],
    "claims": [
        {"id": "c_000001", "pmid": "111", "quote": "Drug A improved symptoms.", "polarity": "supports",
         "subject": "Drug A", "predicate": "treats", "object": "Disease X", "variant": "",
         "study_type": "case_report", "n_patients": 1},
        {"id": "c_000002", "pmid": "222", "quote": "Drug A failed in two patients.", "polarity": "contradicts",
         "subject": "Drug A", "predicate": "treats", "object": "Disease X", "variant": "",
         "study_type": "case_series", "n_patients": 2},
    ],
}


def scripted(*outputs):
    """A fake `parse` that returns the given outputs in order and records prompts."""
    calls = []

    def parser(system, user, schema):
        calls.append(user)
        return outputs[len(calls) - 1]

    parser.calls = calls
    return parser


def cited(*pairs):
    return CitedText(sentences=[CitedSentence(text=t, cites=list(c)) for t, c in pairs])


# --- quote check ------------------------------------------------------------

def test_quote_match_normalizes_whitespace_only():
    abstract = "Results: Drug A  improved\nsymptoms in one patient."
    assert quote_matches("Drug A improved symptoms", abstract)
    assert not quote_matches("drug a improved symptoms", abstract)  # case is not normalized
    assert not quote_matches("Drug A improved the symptoms", abstract)
    assert not quote_matches("   ", abstract)


def test_extract_paper_drops_unmatched_quotes(monkeypatch):
    paper = {"pmid": "123", "doi": None, "title": "T", "pub_types": [], "abstract": "Drug A improved symptoms. Other text."}
    good = ExtractedClaim(quote="Drug A improved  symptoms.", subject="Drug A", predicate="treats", object="Disease X",
                          polarity="supports", variant=None, n_patients=1)
    bad = good.model_copy(update={"quote": "Drug A cured symptoms."})
    monkeypatch.setattr(claims_mod, "parse", scripted(PaperExtraction(study_type="case_report", claims=[good, bad])))
    study_type, kept, dropped = claims_mod.extract_paper(paper)
    assert (study_type, len(kept), dropped) == ("case_report", 1, 1)
    assert kept[0]["quote"] == "Drug A improved symptoms."
    assert kept[0]["pmid"] == "123" and kept[0]["paper"] == "pmid:123" and kept[0]["variant"] == ""


def test_extract_paper_skips_missing_abstract(monkeypatch):
    monkeypatch.setattr(claims_mod, "parse", lambda *a: pytest.fail("should not call the model"))
    assert claims_mod.extract_paper({"pmid": "1", "doi": None, "title": "", "pub_types": [], "abstract": ""}) == (None, [], 0)


# --- validator --------------------------------------------------------------

@pytest.mark.parametrize("sentences,fragment", [
    ([{"text": "Uncited claim.", "cites": []}], "no citation"),
    ([{"text": "Cites the unknown.", "cites": ["e_999999"]}], "not in the input"),
    ([{"text": "See e_999999 too.", "cites": ["e_000001"]}], "mentions 'e_999999'"),
    ([{"text": "Per PMID 999999.", "cites": ["e_000001"]}], "PMID 999999"),
    ([{"text": "Give 10 mg/kg daily.", "cites": ["e_000001"]}], "dosing"),
    ([{"text": "The usual dose was lowered.", "cites": ["e_000001"]}], "dosing"),
    ([], "no sentences"),
])
def test_validator_rejects(sentences, fragment):
    v = validate_sentences(sentences, {"e_000001"}, {"111"})
    assert not v.ok and any(fragment in e for e in v.errors), v.errors


def test_validator_accepts_cited_text():
    v = validate_sentences([{"text": "Drug A helped one patient (PMID 111).", "cites": ["e_000001", "c_000001"]}],
                           {"e_000001", "c_000001"}, {"111"})
    assert v.ok, v.errors


# --- explain / draft ----------------------------------------------------------

def test_subgraph_single_node_pulls_incident_edges_and_claims():
    sub = subgraph(["n:disX"], GRAPH)
    assert {e["id"] for e in sub["edges"]} == {"e_000001", "e_000002"}
    assert {c["id"] for c in sub["claims"]} == {"c_000001", "c_000002"}


def test_explain_accepts_valid_first_answer():
    p = scripted(cited(("Drug A has mixed reports in Disease X.", ["e_000001", "c_000002"])))
    out = explain(["e_000001"], GRAPH, parser=p)
    assert out["mode"] == "llm" and len(p.calls) == 1
    assert out["text"].endswith("[e_000001, c_000002]")


def test_explain_retries_once_with_errors_then_succeeds():
    p = scripted(cited(("Uncited.", [])), cited(("Now cited.", ["e_000001"])))
    out = explain(["e_000001"], GRAPH, parser=p)
    assert out["mode"] == "llm_retry" and len(p.calls) == 2
    assert "previous answer was rejected" in p.calls[1] and "no citation" in p.calls[1]


def test_explain_falls_back_to_template_that_validates():
    p = scripted(cited(("Bad.", ["e_999999"])), None)  # invalid, then refusal
    assert explain(["n:disX"], GRAPH, parser=scripted(cited(("Bad.", ["e_999999"])), None)) is None  # seam default
    out = explain(["n:disX"], GRAPH, parser=p, fallback=True)
    assert out["mode"] == "template"
    allowed = {"e_000001", "e_000002", "c_000001", "c_000002"}
    assert validate_sentences(out["sentences"], allowed).ok
    assert any("contested" in s["text"] for s in out["sentences"])


def test_explain_unsupported_edge_template_says_so():
    out = explain(["e_000003"], GRAPH, parser=scripted(None, None), fallback=True)
    assert "not supported" in out["text"]


def test_explain_empty_subgraph_is_honest_gap():
    no_call = lambda *a: pytest.fail("no model call for an empty subgraph")
    assert explain(["n:nothing"], GRAPH, parser=no_call) is None
    out = explain(["n:nothing"], GRAPH, parser=no_call, fallback=True)
    assert out["mode"] == "empty" and out["sentences"] == []


def test_draft_carries_never_sent_notice_and_validates():
    out = draft(["e_000001"], GRAPH, parser=scripted(None, None), fallback=True)
    assert out["notice"] == NOTICE and "Nothing is sent" in out["notice"]
    assert validate_sentences(out["sentences"], {"e_000001", "c_000001", "c_000002"}).ok


# --- reconcile ------------------------------------------------------------------

def fake_embed(texts):
    # Bag-of-letters vectors: similar strings get similar vectors.
    return [[t.lower().count(ch) for ch in "abcdefghijklmnopqrstuvwxyz"] for t in texts]


def test_reconcile_exact_synonym_needs_no_model():
    idx = NodeIndex(GRAPH["nodes"], embed=fake_embed)
    l = link("dx gof", idx, parser=lambda *a: pytest.fail("exact match should not call the model"))
    assert (l.node_id, l.method, l.confidence) == ("n:disX", "exact", "high")


def test_reconcile_model_pick_must_be_in_shortlist_and_high():
    idx = NodeIndex(GRAPH["nodes"], embed=fake_embed)
    high = link("drug-A therapy", idx, parser=scripted(Adjudication(choice="n:drugA", confidence="high", reason="r")))
    assert high.node_id == "n:drugA" and high.method == "model"
    assert len(high.shortlist) <= 5
    off_list = link("something", idx, parser=scripted(Adjudication(choice="n:invented", confidence="high", reason="r")))
    assert off_list.node_id is None


def test_low_confidence_links_go_to_review_queue_not_graph():
    claims = [{"id": "c_1", "subject": "Drug A", "predicate": "treats", "object": "the disease", "quote": "q"}]
    p = scripted(Adjudication(choice="n:disX", confidence="medium", reason="ambiguous GoF/LoF"))
    links, review = link_claims(claims, GRAPH["nodes"], embed=fake_embed, parser=p)
    assert links["c_1"] == {"subject": "n:drugA", "object": None, "variant": ""}
    assert review[0]["proposed"] == "n:disX" and review[0]["confidence"] == "medium"


def test_reconcile_type_filter():
    idx = NodeIndex(GRAPH["nodes"], embed=fake_embed, types={"Gene"})
    assert set(idx.nodes) == {"n:geneG"}


# --- pubmed parsing -----------------------------------------------------------

PUBMED_XML = """<?xml version="1.0"?>
<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>42</PMID><Article>
<Journal><JournalIssue><PubDate><Year>2017</Year></PubDate></JournalIssue><Title>Placeholder Journal</Title></Journal>
<ArticleTitle>A placeholder title about <i>GENEG</i>.</ArticleTitle>
<Abstract><AbstractText Label="BACKGROUND">First part.</AbstractText><AbstractText Label="RESULTS">Second part.</AbstractText></Abstract>
<AuthorList><Author><LastName>Doe</LastName><ForeName>Jane</ForeName><Initials>J</Initials>
<Identifier Source="ORCID">https://orcid.org/0000-0000-0000-0000</Identifier>
<AffiliationInfo><Affiliation>Placeholder Institute</Affiliation></AffiliationInfo></Author>
<Author><CollectiveName>A Consortium</CollectiveName></Author></AuthorList>
<PublicationTypeList><PublicationType>Case Reports</PublicationType></PublicationTypeList>
</Article></MedlineCitation>
<PubmedData><ArticleIdList><ArticleId IdType="doi">10.0000/placeholder</ArticleId></ArticleIdList></PubmedData>
</PubmedArticle></PubmedArticleSet>"""


def test_parse_pubmed_xml():
    [p] = parse_pubmed_xml(PUBMED_XML)
    assert p.pmid == "42" and p.year == 2017 and p.doi == "10.0000/placeholder"
    assert p.title == "A placeholder title about GENEG."
    assert p.abstract == "BACKGROUND: First part.\nRESULTS: Second part."
    assert len(p.authors) == 1 and p.authors[0].orcid == "0000-0000-0000-0000"
    assert p.authors[0].affiliations == ["Placeholder Institute"]


def test_title_similarity_handles_expanded_abbreviation():
    seed = "Drug reverses responses caused by a novel GENEG gain-of-function mutation"
    published = "Drug reverses responses caused by a novel gene G (GENEG) gain-of-function mutation."
    assert title_similarity(published, seed) >= 0.85
    assert title_similarity("An unrelated paper on something else", seed) < 0.85


# --- eval -------------------------------------------------------------------------

def test_precision_strict_and_lenient():
    rows = [{"label": "correct"}, {"label": "partial"}, {"label": "wrong"}, {"label": "Correct"}, {"label": ""}]
    r = precision(rows)
    assert (r["n_labelled"], r["strict"], r["lenient"]) == (4, 0.5, 0.75)
    with pytest.raises(ValueError):
        precision([{"label": "maybe"}])


# --- investigators ------------------------------------------------------------------

def _paper(ref, authors, found_by=("topic:stat1_gof",)):
    return {"pmid": ref, "doi": None, "found_by": list(found_by),
            "authors": [{"last": l, "fore": f, "initials": f[:1], "affiliations": aff, "orcid": o}
                        for l, f, aff, o in authors]}


def test_investigators_group_by_orcid_then_name_and_affiliation():
    from pipeline.extract.investigators import build as build_investigators
    papers = [
        _paper("1", [("Doe", "Jane", ["Placeholder Institute, Springfield. jane@placeholder.org"], "0000-0000-0000-0001")]),
        _paper("2", [("Doe", "Jane", ["Placeholder Institute, Springfield"], None)]),
        _paper("3", [("Doe", "Jane", ["Elsewhere Clinic, Shelbyville"], None)]),
        _paper("4", [("Roe", "Rick", [], None), ("Doe", "Jane", [], None)]),
    ]
    invs = {i["id"]: i for i in build_investigators(papers)}
    orc = invs["orcid:0000-0000-0000-0001"]
    assert sorted(orc["papers"]) == ["1", "2", "4"]  # 4: identical full name, no affiliation printed
    assert orc["contact"] == {"email": "jane@placeholder.org", "source": {"kind": "pmid", "id": "1"}}
    assert orc["description"] == "author of 3 papers on STAT1 gain of function"
    other = [i for i in invs.values() if i["papers"] == ["3"]]
    assert other and other[0]["confidence"] == "low" and other[0]["contact"] is None  # never guessed
    assert "leader" not in " ".join(i["description"] for i in invs.values())


# --- contract output ----------------------------------------------------------------

def test_contract_claims_keep_unresolved_as_text_and_bundle_validates():
    from pipeline.extract.reconcile import contract_claims
    from pipeline.models import ClaimsBundle
    papers = [{"pmid": "42", "doi": None, "title": "Mock Paper", "abstract": "Mock Drug X helped. Other text.",
               "journal": "Mock Journal", "year": 2000, "pub_types": [], "preprint": False,
               "authors": [{"fore": "Jane", "last": "Doe", "affiliations": ["Mock Institute"], "orcid": None}]}]
    base = {"pmid": "42", "paper": "pmid:42", "quote": "Mock Drug X helped.", "polarity": "supports",
            "predicate": "treats", "study_type": "case_report", "n_patients": 1}
    raw = [
        {**base, "id": "c_000001", "subject": "Mock Drug X", "object": "Mock Disease", "variant": ""},
        {**base, "id": "c_000002", "subject": "Mock Drug X", "object": "unknown thing", "variant": ""},
        {**base, "id": "c_000003", "subject": "Mock Drug X", "object": "Mock Disease", "variant": "Z99Q"},
    ]
    links = {"c_000001": {"subject": "mock:drug:1", "object": "mock:disease:1", "variant": ""},
             "c_000002": {"subject": "mock:drug:1", "object": None, "variant": ""},
             "c_000003": {"subject": "mock:drug:1", "object": "mock:disease:1", "variant": None}}
    claims, paper_nodes, counts = contract_claims(raw, links, papers)
    assert [c["object"] for c in claims] == ["mock:disease:1", "text:unknown thing", "mock:disease:1"]
    assert claims[2]["variant"] == "text:Z99Q"
    assert counts == {"claims": 3, "fully_resolved": 1, "unresolved_subject_or_object": 1, "unresolved_variant": 1}
    assert paper_nodes[0]["props"]["authors"] == [{"name": "Jane Doe", "affiliation": "Mock Institute"}]
    ClaimsBundle.model_validate({"extracted_at": "2026-10-03T00:00:00+00:00", "model": "m", "papers": paper_nodes,
                                 "claims": claims, "unresolved": [], "coverage": {}})


def test_coverage_keyed_by_entity():
    from pipeline.extract.reconcile import coverage_by_entity
    log = [{"source": "pubmed", "query": "q1", "date": "2026-10-03", "hits": 0, "for": "topic:stat1_gof"},
           {"source": "pubmed", "query": "q2", "date": "2026-10-03", "hits": 1, "for": "paperA"},
           {"source": "pubmed", "query": "q3", "date": "2026-10-03", "hits": 0, "for": "paperB"}]
    papers = [{"pmid": "42", "doi": None, "found_by": ["section11:paperA"]}]
    cov = coverage_by_entity(log, papers, {"STAT1": "ensembl:G1"})
    assert cov["ensembl:G1"]["searched"][0]["hits"] == 0  # zero-hit searches are kept
    assert set(cov) == {"ensembl:G1", "pmid:42", "text:paperB"}
    from pipeline.models import Coverage
    for v in cov.values():
        Coverage.model_validate(v)


# --- lane C seam ----------------------------------------------------------------------

def test_lane_c_seam_shapes(monkeypatch):
    """Mirrors api/llm_seam.py: attribute lookup on the package; a validated LLM answer comes back
    with `text` + `citations`; a provider failure or empty subgraph returns None so lane C falls
    through to its precomputed explanations and fallback."""
    import importlib

    from pipeline.llm import client

    llm = importlib.import_module("pipeline.llm")
    monkeypatch.setattr(client, "parse", scripted(cited(("Drug A has mixed reports.", ["e_000001"])),
                                                  cited(("We propose pooling data.", ["e_000001"]))))
    out = llm.explain(["n:disX"], GRAPH)
    assert out["mode"] == "llm" and out["text"] and out["citations"] == ["e_000001"]
    d = llm.draft("n:disX", GRAPH, focus="treatment_response")
    assert d["mode"] == "llm" and d["notice"] == NOTICE

    def boom(*a, **k):
        raise RuntimeError("provider down")

    monkeypatch.setattr(client, "parse", boom)
    assert llm.explain(["n:disX"], GRAPH) is None
    assert llm.draft("n:disX", GRAPH) is None
    assert llm.explain(["x"], {}) is None


def test_cache_key_matches_lane_c_lookup():
    from pipeline.llm.explain import cache_key
    assert cache_key(["n:disX"]) == "n:disX"
    assert cache_key(["n:a", "n:b"]) == "n:a|n:b"
    assert cache_key("n:disX", "draft") == "draft:n:disX"


# --- backbone resolution ---------------------------------------------------------------

def test_backbone_id_normalisation_and_mechanisms():
    from pipeline.extract.backbone import gene_id, mechanism_nodes, norm_curie
    assert norm_curie("MONDO_0000001") == "MONDO:0000001"
    assert norm_curie("HP_0000001") == "HP:0000001"
    assert norm_curie("REACT:R-HSA-1") == "REACT:R-HSA-1"
    assert norm_curie("CHEMBL1") == "CHEMBL1"  # ChEMBL ids are kept as the backbone gives them
    assert gene_id("ENSG00000000001") == "ensembl:ENSG00000000001"
    ids = {n["id"] for n in mechanism_nodes()}
    assert {"mech:STAT1:GoF", "mech:STAT1:LoF", "mech:STAT1:DN", "mech:JAK2:GoF:somatic"} <= ids
    from pipeline.models import Node
    for n in mechanism_nodes():
        Node.model_validate(n)


def test_lexical_index_ranks_rare_tokens():
    from pipeline.extract.backbone import LexicalIndex
    nodes = [{"id": f"n{i}", "type": "Disease", "label": f"common disease {i}", "synonyms": []} for i in range(50)]
    nodes.append({"id": "target", "type": "Disease", "label": "placeholdritis familial", "synonyms": ["PLHD"]})
    idx = LexicalIndex(nodes)
    assert idx.search("familial placeholdritis")[0] == "target"
    assert idx.search("PLHD")[0] == "target"


def test_role_types_and_variant_candidates():
    """treats objects resolve only to Diseases; has_mechanism objects to Mechanisms; a variant
    mention gets its candidates from the injected ClinVar lookup."""
    nodes = GRAPH["nodes"] + [
        {"id": "mech:STAT1:GoF", "type": "Mechanism", "label": "STAT1 gain of function", "synonyms": ["STAT1 GoF"], "props": {}},
    ]
    seen = []

    def lookup(gene, change):
        seen.append((gene, change))
        return [{"id": "clinvar:1", "type": "Variant", "label": f"{gene} p.{change}", "synonyms": [change], "props": {}}]

    claims = [
        {"id": "c_1", "subject": "Drug A", "predicate": "treats", "object": "GENEG", "quote": "q", "variant": ""},
        {"id": "c_2", "subject": "STAT1 R1W", "predicate": "has_mechanism", "object": "STAT1 GoF", "quote": "q",
         "variant": "R1W"},
    ]
    p = scripted(Adjudication(choice="none", confidence="low", reason="gene is not a disease"),
                 Adjudication(choice="clinvar:1", confidence="high", reason="same variant"))
    links, review = link_claims(claims, nodes, embed=fake_embed, parser=p, variant_lookup=lookup)
    assert links["c_1"]["subject"] == "n:drugA" and links["c_1"]["object"] is None  # GENEG is a Gene, not a Disease
    assert links["c_2"]["object"] == "mech:STAT1:GoF"
    assert links["c_2"]["variant"] == "clinvar:1" and ("STAT1", "R1W") in seen


def test_check_against_graph_nulls_missing_ids_but_keeps_variants():
    from pipeline.extract.reconcile import check_against_graph
    links = {"c_1": {"subject": "CHEMBL1", "object": "MONDO:1", "variant": "clinvar:9"}}
    review = check_against_graph(links, {"CHEMBL1"})
    assert links["c_1"] == {"subject": "CHEMBL1", "object": None, "variant": "clinvar:9"}
    assert review[0]["proposed"] == "MONDO:1"


def test_norm_keeps_greek_letters_and_gene_mechanism_check():
    from pipeline.extract.reconcile import NodeIndex, _gene_mechanism_mismatches, norm
    assert norm("IFN-γ") != norm("IFN") and norm("STAT1 GoF") == norm("stat1-gof")
    nodes = [{"id": "g:IFNA1", "type": "Gene", "label": "IFNA1", "synonyms": ["IFN"], "props": {}},
             {"id": "mech:STAT1:GoF", "type": "Mechanism", "label": "STAT1 gain of function", "synonyms": [],
              "props": {"gene_symbol": "STAT1"}}]
    links = {"c_1": {"subject": "g:IFNA1", "object": "mech:STAT1:GoF", "variant": ""}}
    review = _gene_mechanism_mismatches(links, NodeIndex(nodes, embed=fake_embed))
    assert links["c_1"]["subject"] is None and "gene mismatch" in review[0]["reason"]
