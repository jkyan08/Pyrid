"""pipeline/sources/clinvar.py against the recorded fixtures in tests/fixtures/sources/clinvar/.

Fully offline: every E-utility request goes through ``httpx.MockTransport`` serving the verbatim
fixture records, the cache lives in ``tmp_path`` and the rate limiter's sleep is a no-op. The ids
asserted below (uids, OMIM / MONDO xrefs, trait names) are observed values from the fixture
responses, not typed facts; store ids for the fake backbone are ``mock:`` placeholders.
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

import httpx
import pytest

from pipeline import config
from pipeline.cache import CachedHttp, CacheMissOffline, DiskCache
from pipeline.sources import clinvar
from pipeline.sources.clinvar import (
    COVERAGE_ESEARCH,
    COVERAGE_ESUMMARY,
    COVERAGE_PROTEIN_CHANGE,
    COVERAGE_TRAIT_LINK,
    DOSE_PLACEHOLDER,
    RELATION_HAS_CONDITION,
    RELATION_VARIANT_OF,
    SOURCE_LICENSE,
    SOURCE_NAME,
    StoreIndex,
    clinvar_date,
    keep_in_graph,
    load,
    parse_esummary_payload,
    parse_esummary_record,
    scrub_doses,
    term_matches_token,
    variant_node_row,
)
from pipeline.store import Store

FIXTURES = Path(__file__).parent / "fixtures" / "sources" / "clinvar"
ESUMMARY = json.loads((FIXTURES / "esummary_small.json").read_text(encoding="utf-8"))
ESEARCH_SHAPE = json.loads((FIXTURES / "esearch.json").read_text(encoding="utf-8"))
EINFO = json.loads((FIXTURES / "einfo.json").read_text(encoding="utf-8"))
RECORDS: list[dict[str, Any]] = [ESUMMARY["result"][uid] for uid in ESUMMARY["result"]["uids"]]
BY_UID = {r["uid"]: r for r in RECORDS}

# Verbatim NCBI error payloads (docs/source-apis.md section 1c, gotcha 1).
ERROR_10MB = {
    "header": {"type": "eutils", "version": "0.3"},
    "eutilsresult": {"ERROR": "Input XML size is 10845566 bytes, and cannot be transformed to JSON. the max size is 10MB"},
}
ERROR_TOO_MANY = {
    "header": {"type": "esummary", "version": "0.3"},
    "error": "Too many UIDs in request. Maximum number of UIDs is 500 for JSON format output.",
}

SEEDS: dict[str, Any] = {
    "variant_terms": {"STAT1": ["R274W", "T419R", "N574I", "R321"]},
    "max_genes_per_record": 3,
    "page_size": 100,
    "keep_classifications": ["Pathogenic", "Likely pathogenic", "Pathogenic/Likely pathogenic"],
}
CLUSTER: dict[str, Any] = {"genes": ["STAT1", "JAK2"]}
MOCK_TS = "2026-10-03T00:00:00+00:00"

# Fixture facts used by the backbone fake below (observed in esummary_small.json).
R274W_UID = "30083"
V617F_UID = "14662"
CNV_UID = "59158"
OMIM_XREF_IMD31B = next(
    f"OMIM:{x['db_id']}"
    for t in BY_UID[R274W_UID]["germline_classification"]["trait_set"]
    for x in t["trait_xrefs"]
    if t["trait_name"] == "Immunodeficiency 31B" and x["db_source"] == "OMIM"
)
MONDO_ID_AUTOIMMUNE = next(
    x["db_id"]
    for t in BY_UID[R274W_UID]["germline_classification"]["trait_set"]
    for x in t["trait_xrefs"]
    if t["trait_name"].startswith("Autoimmune enteropathy") and x["db_source"] == "MONDO"
)
PMF_TRAIT_NAME = "Primary myelofibrosis"
assert any(t["trait_name"] == PMF_TRAIT_NAME for t in BY_UID[V617F_UID]["germline_classification"]["trait_set"])


# --------------------------------------------------------------------------------------
# fake E-utilities
# --------------------------------------------------------------------------------------


class FakeEutils:
    """MockTransport handler serving the fixture records as if they were ClinVar."""

    def __init__(self, records: list[dict[str, Any]] | None = None, fail_retmax: set[int] | None = None) -> None:
        self.records = records if records is not None else RECORDS
        self.fail_retmax = set(fail_retmax or ())
        self.seen: list[httpx.Request] = []

    def records_for(self, symbol: str) -> list[dict[str, Any]]:
        # The CNV record came from the STAT1 history page; the fixture truncated its 1705-gene list
        # to the first 5 (alphabetical, so STAT1 was dropped). Serve it for STAT1 as ClinVar did.
        return [
            r for r in self.records
            if symbol in {g["symbol"] for g in r["genes"]} or (r["uid"] == CNV_UID and symbol == "STAT1")
        ]

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        params = dict(request.url.params)
        path = request.url.path
        if path.endswith("esearch.fcgi"):
            term = params["term"]
            m = re.fullmatch(r"(\w+)\[gene\](?: AND (\S+))?", term)
            assert m, term
            hits = self.records_for(m[1])
            if m[2]:  # emulate an all-fields text hit on the protein-change token
                hits = [r for r in hits if m[2] in r["protein_change"]]
            body = deepcopy(ESEARCH_SHAPE)
            es = body["esearchresult"]
            es.update({"count": str(len(hits)), "querytranslation": term, "translationstack": []})
            if params.get("usehistory") == "y":
                es.update({"webenv": f"MOCK_WEBENV_{m[1]}", "querykey": "1"})
            else:
                es.pop("webenv", None)
                es.pop("querykey", None)
            return httpx.Response(200, json=body)
        if path.endswith("esummary.fcgi"):
            symbol = params["WebEnv"].removeprefix("MOCK_WEBENV_")
            retstart, retmax = int(params["retstart"]), int(params["retmax"])
            if retmax in self.fail_retmax:
                return httpx.Response(200, json=ERROR_10MB)
            page = self.records_for(symbol)[retstart : retstart + retmax]
            body = {
                "header": {"type": "esummary", "version": "0.3"},
                "result": {"uids": [r["uid"] for r in page], **{r["uid"]: r for r in page}},
            }
            return httpx.Response(200, json=body)
        if path.endswith("einfo.fcgi"):
            return httpx.Response(200, json=EINFO)
        return httpx.Response(404, text="unexpected endpoint")

    def esummary_requests(self) -> list[dict[str, str]]:
        return [dict(r.url.params) for r in self.seen if r.url.path.endswith("esummary.fcgi")]


def make_http(tmp_path: Path, fake: FakeEutils) -> CachedHttp:
    # No User-Agent is configured on the client on purpose: the loader must send config.USER_AGENT
    # on every request itself (the CachedHttp default is a different string), so the header
    # assertion in test_load_links_variants_to_backbone_and_records_coverage is not vacuous.
    return CachedHttp(
        DiskCache(tmp_path / "cache"),
        transport=httpx.MockTransport(fake.handler),
        ncbi_api_key="",
        sleep=lambda s: None,
    )


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv("ATLAS_OFFLINE", raising=False)
    monkeypatch.delenv("NCBI_API_KEY", raising=False)
    return tmp_path


@pytest.fixture
def store(tmp_path: Path) -> Store:
    with Store(tmp_path / "x.sqlite") as s:
        yield s


def mock_node(node_id: str, node_type: str, label: str, **props: Any) -> dict[str, Any]:
    synonyms = props.pop("synonyms", [])
    return {"id": node_id, "type": node_type, "label": label, "synonyms": synonyms, "props": props, "source": "mock", "retrieved_at": MOCK_TS}


def seed_backbone(store: Store) -> None:
    """A fake backbone: one Gene (props.symbol) and three Diseases reachable by OMIM xref, by MONDO
    id and by exact name respectively. Ids are mock placeholders except the MONDO id, which is the
    observed fixture xref (a direct-id match needs the real spelling)."""
    store.upsert_nodes(
        [
            mock_node("mock:gene:1", "Gene", "STAT1", symbol="STAT1"),
            mock_node("mock:disease:1", "Disease", "Mock immunodeficiency", xrefs=[OMIM_XREF_IMD31B]),
            mock_node(MONDO_ID_AUTOIMMUNE, "Disease", "Mock autoimmune syndrome", xrefs=[]),
            mock_node("mock:disease:3", "Disease", "Mock myelofibrosis", synonyms=[PMF_TRAIT_NAME], xrefs=[]),
        ]
    )


def coverage(store: Store, entity: str | None = None, source: str | None = None) -> list[dict[str, Any]]:
    rows = store.coverage_rows()
    if entity is not None:
        rows = [r for r in rows if r["entity_id"] == entity]
    if source is not None:
        rows = [r for r in rows if r["source"] == source]
    return rows


# --------------------------------------------------------------------------------------
# dose scrubber (hard rule 7)
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("ruxolitinib 5 mg twice daily", f"ruxolitinib {DOSE_PLACEHOLDER}"),
        ("10 mg/kg/day", DOSE_PLACEHOLDER),
        ("0.5 mg/m2 every 2 weeks", DOSE_PLACEHOLDER),
        ("200 IU", DOSE_PLACEHOLDER),
        # a percentage is a dose only as the strength of a formulation
        ("0.1% tacrolimus ointment", DOSE_PLACEHOLDER),
        ("2 % cream twice daily", DOSE_PLACEHOLDER),
        ("5% w/v solution", DOSE_PLACEHOLDER),
        ("0.03% ophthalmic solution", DOSE_PLACEHOLDER),
        ("5-10 mg", DOSE_PLACEHOLDER),
        ("1,000 mg BID", DOSE_PLACEHOLDER),
        ("2.5 mcg once daily", DOSE_PLACEHOLDER),
        ("took 5 g daily", f"took {DOSE_PLACEHOLDER} daily"),
        ("20 units q8h", DOSE_PLACEHOLDER),
        ("dose 15mg b.i.d.", f"dose {DOSE_PLACEHOLDER}"),
        ("2 tablets per day", DOSE_PLACEHOLDER),
        ("given every 6 weeks", f"given {DOSE_PLACEHOLDER}"),
        ("QD dosing", f"{DOSE_PLACEHOLDER} dosing"),
        ("500 mL infusion", f"{DOSE_PLACEHOLDER} infusion"),
        # Upper-case units as RxNorm-style intervention names spell them.
        ("Tofacitinib 5 MG Oral Tablet", f"Tofacitinib {DOSE_PLACEHOLDER} Oral Tablet"),
        ("500 ML infusion", f"{DOSE_PLACEHOLDER} infusion"),
        ("100 Units", DOSE_PLACEHOLDER),
        ("0.5 MG/KG", DOSE_PLACEHOLDER),
        ("200 iu", DOSE_PLACEHOLDER),
    ],
)
def test_scrub_doses_removes_amounts_and_frequencies(text: str, expected: str) -> None:
    assert scrub_doses(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "NM_007315.4(STAT1):c.986C>T (p.Pro329Leu)",
        "NM_000215.4(JAK3):c.1915-11G>A",
        "GRCh38/hg38 2q32.2-37.3(chr2:188818195-242065208)x3",
        "NC_000002.11:g.190995185G>A",
        "5 genes",
        "IL2RG",
        "p.Gln9Ter",
        "R274W, R241W, R244W",
        "Q463H",  # Gln->His token, not "every 463 hours"
        "Q8H, Q46H",
        "HP:0004812",
        "Immunodeficiency 31B",
        "the 3 G proteins",
        "a bid for",
        "Budd-Chiari syndrome",
        "0.00035",
        "Tier I - Strong",
        # a bare percentage is a statistic, not a dose
        "2.5%",
        "comprises about 5% of childhood cases",
        "asymptomatic presentation in 40% of patients",
        "10.1111/j.0105-2896.2005.00220.x",  # a DOI
    ],
)
def test_scrub_doses_leaves_hgvs_ids_and_plain_text_alone(text: str) -> None:
    assert scrub_doses(text) == text


def test_scrub_doses_passes_non_strings_through() -> None:
    assert scrub_doses(None) is None
    assert scrub_doses(3) == 3
    assert scrub_doses("") == ""


def test_every_fixture_title_is_dose_free() -> None:
    for rec in RECORDS:
        assert scrub_doses(rec["title"]) == rec["title"]


def test_scrubbing_is_applied_to_stored_text_fields() -> None:
    rec = deepcopy(BY_UID[R274W_UID])
    rec["title"] = "Mock title 10 mg/kg"
    rec["variation_set"][0]["variation_name"] = "Mock name 5 mg"
    rec["germline_classification"]["trait_set"][1]["trait_name"] = "Mock condition 5 mg daily"
    parsed = parse_esummary_record(rec)
    assert parsed["title"] == f"Mock title {DOSE_PLACEHOLDER}"
    assert parsed["variation_name"] == f"Mock name {DOSE_PLACEHOLDER}"
    assert f"Mock condition {DOSE_PLACEHOLDER} daily" in [t["trait_name"] for t in parsed["traits"]]
    row = variant_node_row(parsed, MOCK_TS)
    assert "mg" not in json.dumps(row)


# --------------------------------------------------------------------------------------
# record parsing
# --------------------------------------------------------------------------------------


def test_multi_isoform_protein_change_is_tokenised_and_mane_token_found() -> None:
    parsed = parse_esummary_record(BY_UID["4959183"])
    assert parsed["protein_change_tokens"] == ["P299L", "P329L", "P296L", "P276L", "P331L", "P341L"]
    assert parsed["protein_change_mane"] == "P329L"  # residue 329 from title p.Pro329Leu, not the first token
    assert parsed["hgvs_transcript"] == "NM_007315.4"
    assert parsed["hgvs_gene"] == "STAT1"
    assert parsed["hgvs_c"] == "c.986C>T"
    assert parsed["hgvs_p"] == "p.Pro329Leu"
    assert parsed["cdna_change"] == "c.986C>T"
    assert parsed["molecular_consequence"] == ["missense variant", "intron variant"]
    assert parsed["classification"] == "Likely pathogenic"
    assert parsed["review_status"] == "criteria provided, single submitter"
    assert parsed["last_evaluated"] == "2021-04-12"
    assert parsed["gene_symbols"] == ["STAT1"]
    assert parsed["genes"][0]["geneid"] == "6772"
    assert parsed["canonical_spdi"] == "NC_000002.12:190991278:G:A"
    assert parsed["location"]["assembly_name"] == "GRCh38"
    assert parsed["n_scv"] == 1 and parsed["n_rcv"] == 1
    assert parsed["record_id"] == "4959183"
    assert parsed["source_url"] == "https://www.ncbi.nlm.nih.gov/clinvar/variation/4959183/"


def test_stop_codon_tokens_and_sentinel_date() -> None:
    parsed = parse_esummary_record(BY_UID["2500092"])
    assert parsed["protein_change_tokens"] == ["Q11*", "Q9*"]
    assert parsed["protein_change_mane"] == "Q9*"
    assert parsed["classification"] == "Pathogenic"
    assert parsed["last_evaluated"] is None  # sentinel 1/01/01 00:00 on a classified record
    assert parsed["germline_classification"]["last_evaluated_raw"] == "1/01/01 00:00"
    assert parsed["allele_freq_set"][0]["value"] == "0.00000"


def test_clinvar_date_parsing() -> None:
    assert clinvar_date("2026/08/11 00:00") == "2026-08-11"
    assert clinvar_date("1/01/01 00:00") is None
    assert clinvar_date("") is None
    assert clinvar_date(None) is None


def test_cnv_record_shape_and_gene_count() -> None:
    parsed = parse_esummary_record(BY_UID[CNV_UID])
    assert parsed["gene_count"] == 5 > SEEDS["max_genes_per_record"]
    assert parsed["obj_type"] == "copy number gain"
    assert parsed["canonical_spdi"] == ""
    assert parsed["molecular_consequence"] == []
    assert parsed["protein_change_tokens"] == []
    assert parsed["hgvs_transcript"] is None
    assert parsed["location"]["inner_start"] == "188818195"
    assert parsed["traits"][0]["trait_name"] == "See cases" and parsed["traits"][0]["xrefs"] == []


@pytest.mark.parametrize(
    "uid, has_somatic, germline_present",
    [
        ("4530519", True, False),  # clinical impact block only
        ("2958894", True, True),  # oncogenicity + germline
        (V617F_UID, False, True),  # V617F: germline block only
        (R274W_UID, False, True),
    ],
)
def test_somatic_and_germline_block_flags(uid: str, has_somatic: bool, germline_present: bool) -> None:
    parsed = parse_esummary_record(BY_UID[uid])
    assert parsed["has_somatic_block"] is has_somatic
    assert parsed["germline_block_present"] is germline_present
    if uid == "4530519":
        assert parsed["clinical_impact_classification"]["description"] == "Tier I - Strong"
        assert parsed["clinical_impact_classification"]["last_evaluated"] == "2024-04-24"
        assert parsed["classification"] == ""
        assert [t["classification_block"] for t in parsed["traits"]] == ["clinical_impact"]
    if uid == "2958894":
        assert parsed["oncogenicity_classification"]["description"] == "Uncertain significance"
        assert {t["classification_block"] for t in parsed["traits"]} == {"germline", "oncogenicity"}


def test_trait_xrefs_are_parsed_and_duplicates_dropped() -> None:
    parsed = parse_esummary_record(BY_UID[R274W_UID])
    raw_traits = BY_UID[R274W_UID]["germline_classification"]["trait_set"]
    assert len(raw_traits) == 6 and len(parsed["traits"]) == 5  # one exact duplicate trait
    by_name = {t["trait_name"]: t for t in parsed["traits"]}
    imd = by_name["Immunodeficiency 31B"]
    sources = {x["db_source"] for x in imd["xrefs"]}
    assert {"MONDO", "OMIM", "Orphanet", "MedGen"} <= sources
    assert next(x["db_id"] for x in imd["xrefs"] if x["db_source"] == "MONDO").startswith("MONDO:")
    assert by_name["STAT1-related disorder"]["xrefs"] == []
    assert by_name["not provided"]["xrefs"][0]["db_source"] == "MedGen"


def test_parse_esummary_payload_recognises_ncbi_error_shapes() -> None:
    assert parse_esummary_payload(ERROR_10MB)[2].startswith("Input XML size")
    assert parse_esummary_payload(ERROR_TOO_MANY)[2].startswith("Too many UIDs")
    assert parse_esummary_payload({"header": {}})[2] == "esummary body has no result"
    assert parse_esummary_payload("nope")[2]
    records, header, error = parse_esummary_payload(ESUMMARY)
    assert error is None and header == ESUMMARY["header"]
    assert [r["uid"] for r in records] == ESUMMARY["result"]["uids"]
    stub = {"result": {"uids": ["1", "2"], "1": {"uid": "1", "error": "cannot get document summary"}, "2": BY_UID[R274W_UID]}}
    records, _, error = parse_esummary_payload(stub)
    assert error is None and [r["uid"] for r in records] == ["1", R274W_UID]  # stubs are filtered by load()


# --------------------------------------------------------------------------------------
# keep rule and variant terms
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "token, term, expected",
    [
        ("R274W", "R274W", True),
        ("r274w", "R274W", True),
        ("R274W", "N574I", False),
        ("R321G", "R321", True),
        ("R321*", "R321", True),
        ("R321", "R321", True),
        ("R3210X", "R321", False),
        ("R274W", "R27", False),  # bare position only matches whole residue numbers
        ("", "R274W", False),
    ],
)
def test_term_matches_token(token: str, term: str, expected: bool) -> None:
    assert term_matches_token(token, term) is expected


def test_keep_in_graph_by_classification_or_variant_term() -> None:
    keep, reasons, terms = keep_in_graph(parse_esummary_record(BY_UID[R274W_UID]), SEEDS)
    assert keep and "classification:Pathogenic" in reasons and terms == ["STAT1:R274W"]
    keep, reasons, _ = keep_in_graph(parse_esummary_record(BY_UID["4959183"]), SEEDS)
    assert keep and reasons == ["classification:Likely pathogenic"]
    assert keep_in_graph(parse_esummary_record(BY_UID["2958894"]), SEEDS)[0] is False  # VUS, no term
    assert keep_in_graph(parse_esummary_record(BY_UID["4530519"]), SEEDS)[0] is False  # no germline classification
    # A VUS carrying a seed term is kept because of the term alone.
    vus = deepcopy(BY_UID[R274W_UID])
    vus["germline_classification"]["description"] = "Uncertain significance"
    vus["protein_change"] = "T419R, T386R"
    keep, reasons, terms = keep_in_graph(parse_esummary_record(vus), SEEDS)
    assert keep and reasons == ["variant_term:STAT1:T419R"] and terms == ["STAT1:T419R"]
    # Terms are gene-scoped: the same token on a JAK2 record does not count.
    jak2 = deepcopy(BY_UID["2958894"])
    jak2["protein_change"] = "R274W"
    assert keep_in_graph(parse_esummary_record(jak2), SEEDS) == (False, [], [])
    # Bare position term matches any change at that residue.
    pos = deepcopy(vus)
    pos["protein_change"] = "R321G, R288G"
    assert keep_in_graph(parse_esummary_record(pos), SEEDS)[2] == ["STAT1:R321"]


def test_variant_node_row_shape() -> None:
    row = variant_node_row(parse_esummary_record(BY_UID[R274W_UID]), MOCK_TS, {"keep_in_graph": True})
    assert row["id"] == f"clinvar:{R274W_UID}"
    assert row["type"] == "Variant"
    assert row["label"] == BY_UID[R274W_UID]["title"]
    for syn in ("R274W", "R241W", "c.820C>T", "VCV000030083", "p.Arg274Trp", "STAT1 R274W"):
        assert syn in row["synonyms"]
    assert row["label"] not in row["synonyms"]
    assert row["source"] == SOURCE_NAME and row["retrieved_at"] == MOCK_TS
    assert row["props"]["record_id"] == R274W_UID
    assert row["props"]["source_url"].endswith(f"/variation/{R274W_UID}/")
    assert row["props"]["keep_in_graph"] is True
    json.dumps(row)  # props must be plain JSON data


# --------------------------------------------------------------------------------------
# load(): linking, coverage, sources
# --------------------------------------------------------------------------------------


def test_load_links_variants_to_backbone_and_records_coverage(env: Path, store: Store) -> None:
    seed_backbone(store)
    fake = FakeEutils()
    report = load(store, SEEDS, CLUSTER, http=make_http(env, fake))

    # Every request carried our User-Agent and nothing was requested twice.
    assert fake.seen and all(r.headers["user-agent"] == config.USER_AGENT for r in fake.seen)
    descriptions = [(r.url.path, tuple(sorted(r.url.params.multi_items()))) for r in fake.seen]
    assert len(descriptions) == len(set(descriptions))
    assert all(r.url.host == "eutils.ncbi.nlm.nih.gov" for r in fake.seen)

    # Nodes: every fixture record except the CNV (5 genes > 3).
    variants = {n["id"]: n for n in store.iter_nodes("Variant")}
    assert set(variants) == {f"clinvar:{r['uid']}" for r in RECORDS if r["uid"] != CNV_UID}
    assert report["records"] == 6 and report["excluded_cnv"] == 1
    assert report["by_gene"]["STAT1"] == {
        "esearch_count": 4, "records": 3, "excluded_cnv": 1, "record_errors": 0, "kept_in_graph": 3,
        "page_failures": 0, "esearch_error": None,
    }
    assert report["by_gene"]["JAK2"]["records"] == 3 and report["by_gene"]["JAK2"]["kept_in_graph"] == 1
    assert report["kept_in_graph"] == 4  # 4959183, 30083, 2500092, 14662
    assert report["backbone_present"] is True

    r274w = variants[f"clinvar:{R274W_UID}"]
    assert r274w["props"]["keep_in_graph"] is True
    assert r274w["props"]["matched_seed_genes"] == ["STAT1"]
    assert variants[f"clinvar:2958894"]["props"]["keep_in_graph"] is False

    # Variant -> Gene by props.symbol; JAK2 has no Gene node in this store.
    variant_of = {(e["source"], e["target"]): e for e in store.iter_edges(RELATION_VARIANT_OF)}
    assert set(variant_of) == {(f"clinvar:{u}", "mock:gene:1") for u in ("4959183", R274W_UID, "2500092")}
    e = variant_of[(f"clinvar:{R274W_UID}", "mock:gene:1")]
    assert e["source_db"] == SOURCE_NAME
    assert e["props"]["match"] == {"method": "props.symbol", "matched_text": "STAT1"}
    assert e["props"]["geneid"] == "6772"
    v617f = variants[f"clinvar:{V617F_UID}"]
    assert set(v617f["props"]["unlinked_genes"]) == {"INSL6", "JAK2"}
    assert r274w["props"]["unlinked_genes"] == []

    # Variant -> Disease: OMIM xref, direct MONDO id, exact name; unresolved traits kept raw.
    has_condition = {(e["source"], e["target"]): e for e in store.iter_edges(RELATION_HAS_CONDITION)}
    omim_edge = has_condition[(f"clinvar:{R274W_UID}", "mock:disease:1")]
    assert omim_edge["props"]["match"] == {"method": "xref:OMIM", "matched_text": OMIM_XREF_IMD31B}
    assert omim_edge["props"]["trait_name"] == "Immunodeficiency 31B"
    assert omim_edge["props"]["classification"] == "Pathogenic"
    assert omim_edge["props"]["review_status"] == "criteria provided, multiple submitters, no conflicts"
    assert omim_edge["props"]["last_evaluated"] == "2026-08-11"
    assert omim_edge["props"]["classification_block"] == "germline"
    assert omim_edge["props"]["n_scv"] == 10 and omim_edge["props"]["n_rcv"] == 4
    assert {x["db_source"] for x in omim_edge["props"]["xrefs"]} >= {"MONDO", "OMIM", "Orphanet", "MedGen"}
    mondo_edge = has_condition[(f"clinvar:{R274W_UID}", MONDO_ID_AUTOIMMUNE)]
    assert mondo_edge["props"]["match"]["method"] == "mondo_id"
    name_edge = has_condition[(f"clinvar:{V617F_UID}", "mock:disease:3")]
    assert name_edge["props"]["match"] == {"method": "name", "matched_text": PMF_TRAIT_NAME}
    assert name_edge["props"]["classification"] == "Pathogenic/Likely pathogenic"
    # The same OMIM xref links the nonsense variant too; the CNV never got an edge.
    assert (f"clinvar:2500092", "mock:disease:1") in has_condition
    assert not any(s.endswith(CNV_UID) for s, _ in has_condition)
    unlinked = [t["trait_name"] for t in r274w["props"]["unlinked_conditions"]]
    assert "Mendelian susceptibility to mycobacterial diseases due to partial STAT1 deficiency" in unlinked
    assert "STAT1-related disorder" in unlinked
    assert r274w["props"]["skipped_traits"] == ["not provided"]
    r867w = variants["clinvar:2958894"]
    assert r867w["props"]["skipped_traits"] == ["not provided"]
    assert [t["trait_name"] for t in r867w["props"]["unlinked_conditions"]] == ["Neoplasm"]
    assert r867w["props"]["unlinked_conditions"][0]["classification_block"] == "oncogenicity"

    # Coverage: gene searches (entity = Gene node id when resolved, else seed key).
    stat1_search = coverage(store, "mock:gene:1", COVERAGE_ESEARCH)
    assert [(r["query"], r["hits"]) for r in stat1_search] == [("STAT1[gene]", 4)]
    jak2_search = coverage(store, "seed:gene:JAK2", COVERAGE_ESEARCH)
    assert [(r["query"], r["hits"]) for r in jak2_search] == [("JAK2[gene]", 3)]
    excluded = [r for r in coverage(store, "mock:gene:1", COVERAGE_ESUMMARY) if "excluded_cnv" in r["query"]]
    assert len(excluded) == 1 and excluded[0]["hits"] == 1
    assert all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", r["date"]) for r in store.coverage_rows())

    # Coverage: variant terms, stored token count and live esearch, 0 for the absent N574I.
    for term, stored, live in (("R274W", 1, 1), ("T419R", 0, 0), ("N574I", 0, 0), ("R321", 0, 0)):
        rows = coverage(store, f"seed:variant:STAT1:{term}")
        by_source = {r["source"]: r for r in rows}
        assert by_source[COVERAGE_PROTEIN_CHANGE]["hits"] == stored, term
        assert by_source[COVERAGE_ESEARCH]["hits"] == live, term
        assert by_source[COVERAGE_ESEARCH]["query"] == f"STAT1[gene] AND {term}"
    assert report["variant_terms"]["STAT1:N574I"] == {"stored_hits": 0, "live_esearch_hits": 0, "live_query": "STAT1[gene] AND N574I"}

    # Coverage: trait linking per variant (hits = distinct Disease nodes linked).
    link_rows = {r["entity_id"]: r["hits"] for r in coverage(store, source=COVERAGE_TRAIT_LINK)}
    assert set(link_rows) == set(variants)
    assert link_rows[f"clinvar:{R274W_UID}"] == 2
    assert link_rows[f"clinvar:{V617F_UID}"] == 1
    assert link_rows["clinvar:4530519"] == 0

    # Sources row: dbbuild from einfo (the JSON esummary header has none), public-domain license.
    sources = {s["name"]: s for s in store.sources_rows()}
    src = sources[SOURCE_NAME]
    assert src["version"] == EINFO["einforesult"]["dbinfo"][0]["dbbuild"]
    assert src["license"] == SOURCE_LICENSE
    assert src["url"].startswith("https://www.ncbi.nlm.nih.gov/clinvar")
    assert src["notes"]["records"] == 6 and src["notes"]["kept_in_graph"] == 4
    assert report["version"] == src["version"]
    assert report["errors"] == [] and report["page_failures"] == []


def test_load_without_backbone_still_writes_variants(env: Path, store: Store) -> None:
    report = load(store, SEEDS, CLUSTER, http=make_http(env, FakeEutils()))
    assert report["backbone_present"] is False
    assert store.nodes_by_type() == {"Variant": 6}
    assert store.edges_by_relation() == {}
    r274w = store.get_node(f"clinvar:{R274W_UID}")
    assert r274w["props"]["unlinked_genes"] == ["STAT1"]
    assert len(r274w["props"]["unlinked_conditions"]) == 4  # 5 unique traits minus "not provided"
    assert coverage(store, "seed:gene:STAT1", COVERAGE_ESEARCH)[0]["hits"] == 4
    assert coverage(store, f"clinvar:{R274W_UID}", COVERAGE_TRAIT_LINK)[0]["hits"] == 0


def test_gene_with_zero_esearch_hits_still_gets_coverage_rows(env: Path, store: Store) -> None:
    """Hard rule 3: a gene search that returns nothing is recorded with hits 0 (and no esummary
    page is requested for it). MOCKGENE is a placeholder symbol absent from the fixtures."""
    fake = FakeEutils()
    report = load(store, SEEDS, {"genes": ["MOCKGENE", "JAK2"]}, http=make_http(env, fake))
    rows = coverage(store, "seed:gene:MOCKGENE")
    assert [(r["source"], r["query"], r["hits"]) for r in rows if r["source"] == COVERAGE_ESEARCH] == [
        (COVERAGE_ESEARCH, "MOCKGENE[gene]", 0)
    ]
    assert [r["hits"] for r in rows if r["source"] == COVERAGE_ESUMMARY and "excluded_cnv" in r["query"]] == [0]
    assert report["by_gene"]["MOCKGENE"]["esearch_count"] == 0 and report["by_gene"]["MOCKGENE"]["records"] == 0
    assert report["errors"] == []
    assert not any(p["WebEnv"].endswith("MOCKGENE") for p in fake.esummary_requests())
    assert store.nodes_by_type() == {"Variant": 3}  # only the JAK2 records


def test_load_is_idempotent_and_second_run_touches_no_network(env: Path, store: Store) -> None:
    seed_backbone(store)
    fake = FakeEutils()
    http = make_http(env, fake)
    first = load(store, SEEDS, CLUSTER, http=http)
    n_requests = len(fake.seen)
    assert first["requests"] == n_requests and first["requests_from_cache"] == 0
    second = load(store, SEEDS, CLUSTER, http=http)
    assert len(fake.seen) == n_requests  # every call served from the cache
    assert second["requests"] == n_requests and second["requests_from_cache"] == n_requests
    assert second["retrieved_at"] == first["retrieved_at"]
    assert store.counts()["nodes"] == 10 and store.counts()["edges"] == first["variant_of_edges"] + first["has_condition_edges"]
    assert store.counts()["coverage"] == len(store.coverage_rows())


def test_offline_raises_on_a_cold_cache_and_works_from_a_warm_one(env: Path, store: Store, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeEutils()
    http = make_http(env, fake)
    with pytest.raises(CacheMissOffline):
        load(store, SEEDS, CLUSTER, http=http, offline=True)
    assert fake.seen == []
    monkeypatch.setenv("ATLAS_OFFLINE", "1")
    with pytest.raises(CacheMissOffline):
        load(store, SEEDS, CLUSTER, http=http)
    assert fake.seen == []
    monkeypatch.delenv("ATLAS_OFFLINE")

    online = load(store, SEEDS, CLUSTER, http=http)
    n_requests = len(fake.seen)
    offline = load(store, SEEDS, CLUSTER, http=http, offline=True)
    assert len(fake.seen) == n_requests
    assert offline["records"] == online["records"] == 6
    assert offline["requests_from_cache"] == offline["requests"] == online["requests"]


def test_page_error_halves_the_range(env: Path, store: Store) -> None:
    seeds = {**SEEDS, "page_size": 4}
    fake = FakeEutils(fail_retmax={4})
    report = load(store, seeds, CLUSTER, http=make_http(env, fake))
    assert store.nodes_by_type() == {"Variant": 6}
    assert report["page_failures"] == [] and report["by_gene"]["STAT1"]["page_failures"] == 0
    pages = fake.esummary_requests()
    stat1 = [p for p in pages if p["WebEnv"].endswith("STAT1")]
    assert [(int(p["retstart"]), int(p["retmax"])) for p in stat1] == [(0, 4), (0, 2), (2, 2)]
    jak2 = [p for p in pages if p["WebEnv"].endswith("JAK2")]
    assert [(int(p["retstart"]), int(p["retmax"])) for p in jak2] == [(0, 3)]
    assert all(p["retmode"] == "json" and p["query_key"] == "1" for p in pages)


def test_persistent_page_error_is_recorded_in_coverage_and_skipped(env: Path, store: Store) -> None:
    seeds = {**SEEDS, "page_size": 16}  # minimum page size 16 // 8 = 2
    fake = FakeEutils(fail_retmax={16, 8, 4, 2})
    report = load(store, seeds, CLUSTER, http=make_http(env, fake))
    assert store.nodes_by_type() == {"Variant": 3}  # only the JAK2 records
    assert len(report["page_failures"]) == 2 and report["by_gene"]["STAT1"]["page_failures"] == 2
    assert [(f["retstart"], f["retmax"]) for f in report["page_failures"]] == [(0, 2), (2, 2)]
    failed = [r for r in coverage(store, "seed:gene:STAT1", COVERAGE_ESUMMARY) if "failed" in r["query"]]
    assert len(failed) == 2 and all(r["hits"] == 0 for r in failed)
    assert all("Input XML size" in r["query"] for r in failed)
    assert coverage(store, "seed:gene:STAT1", COVERAGE_ESEARCH)[0]["hits"] == 4  # the search itself is still recorded
    assert coverage(store, "seed:variant:STAT1:R274W", COVERAGE_PROTEIN_CHANGE)[0]["hits"] == 0


def test_store_index_resolves_xref_spellings_and_names(store: Store) -> None:
    seed_backbone(store)
    store.upsert_nodes([mock_node("mock:disease:4", "Disease", "Mock underscore", xrefs=["MONDO_0000001", "UMLS:C0000001"])])
    index = StoreIndex(store)
    assert index.gene_by_symbol == {"STAT1": "mock:gene:1"}
    assert index.disease_by_xref[("OMIM", OMIM_XREF_IMD31B.split(":")[1])] == ["mock:disease:1"]
    assert index.disease_by_xref[("MONDO", "0000001")] == ["mock:disease:4"]
    assert index.disease_by_xref[("UMLS", "C0000001")] == ["mock:disease:4"]
    assert index.disease_by_name[PMF_TRAIT_NAME.lower()] == ["mock:disease:3"]
    trait = {"trait_name": "Mock nothing", "xrefs": [{"db_source": "MedGen", "db_id": "C0000001"}]}
    assert clinvar.resolve_trait(trait, index) == [{"disease_id": "mock:disease:4", "method": "xref:UMLS", "matched_text": "MedGen:C0000001"}]
    hpo_only = {"trait_name": "Mock phenotype", "xrefs": [{"db_source": "Human Phenotype Ontology", "db_id": "HP:0000001"}]}
    assert clinvar.resolve_trait(hpo_only, index) == []


# --------------------------------------------------------------------------------------
# seeds and hard rule 2
# --------------------------------------------------------------------------------------

_ID_SHAPES = re.compile(r"(MONDO|OMIM|Orphanet|MedGen|VCV|RCV|SCV|PMID|NCT|HP)[:_]?\s*\d{4,}")


def test_real_seed_file_has_names_only_and_the_expected_keys() -> None:
    seeds = config.load_seeds("clinvar")
    assert set(seeds) == {"variant_terms", "max_genes_per_record", "page_size", "keep_classifications"}
    assert seeds["variant_terms"] == {"STAT1": ["R274W", "T419R", "N574I", "R321"]}
    assert seeds["max_genes_per_record"] == 3 and seeds["page_size"] == 100
    assert "Pathogenic" in seeds["keep_classifications"]
    text = (config.SEEDS_DIR / "clinvar.yaml").read_text(encoding="utf-8")
    assert _ID_SHAPES.search(text) is None
    assert "genes" not in seeds  # genes come from cluster.yaml
    cluster = config.load_seeds("cluster")
    assert "STAT1" in cluster["genes"]


def test_module_source_contains_no_hand_typed_identifiers() -> None:
    source = Path(clinvar.__file__).read_text(encoding="utf-8")
    assert _ID_SHAPES.search(source) is None
    assert not re.search(r"\b\d{5,}\b", source)
