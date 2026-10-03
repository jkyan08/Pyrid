"""pipeline/sources/ctgov.py against the recorded ClinicalTrials.gov fixtures.

Fully offline. A :class:`FakeHttp` serves verbatim fixture bodies (tests/fixtures/sources/ctgov/)
by URL path and query parameters and records every call; one test drives the real
:class:`pipeline.cache.CachedHttp` through ``httpx.MockTransport`` to cover caching and
``ATLAS_OFFLINE`` behaviour. Stores live in ``tmp_path``. Pre-seeded Disease / Drug / Gene nodes
use ``mock:`` ids; their names are taken from the fixture records so the link matching can be
exercised. Every NCT id, PMID or MeSH id asserted below appears in the fixtures (observed data).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import httpx
import pytest

from pipeline import config
from pipeline.cache import CachedHttp, CachedResponse, CacheMissOffline, DiskCache, HostRateLimiter
from pipeline.sources import ctgov
from pipeline.sources.ctgov import (
    COVERAGE_SOURCE,
    DOSE_PLACEHOLDER,
    SOURCE_NAME,
    CtgovError,
    SeedQuery,
    build_page_params,
    load,
    params_string,
    parse_seeds,
    parse_study,
    scrub_dose,
    study_id,
    wrap_gene_symbols,
)
from pipeline.store import Store

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "sources" / "ctgov"
RETRIEVED_AT = "2026-10-03T19:40:00+00:00"
RETRIEVED_DATE = RETRIEVED_AT[:10]
STUDIES_URL = f"{ctgov.BASE_URL}/studies"
VERSION_URL = f"{ctgov.BASE_URL}/version"

STUDY_FILES = ("study_NCT07261891.json", "study_NCT05561985.json", "study_NCT07670156.json", "study_NCT07262983.json")


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def fixture_json(name: str) -> Any:
    return json.loads(fixture_text(name))


def nct_of(record: dict[str, Any]) -> str:
    return record["protocolSection"]["identificationModule"]["nctId"]


def envelope(records: list[dict[str, Any]], total: int | None = None, next_token: str | None = None) -> str:
    """A ``GET /studies`` response body built from full fixture records (same nested shape)."""
    body: dict[str, Any] = {"studies": records}
    if total is not None:
        body = {"totalCount": total, **body}
    if next_token is not None:
        body["nextPageToken"] = next_token
    return json.dumps(body)


# --------------------------------------------------------------------------------------
# fake http
# --------------------------------------------------------------------------------------


class _FakeCache:
    def __init__(self) -> None:
        self.offline = False


class FakeHttp:
    """Serves fixture bodies by (path, params); a request with no route is a test failure.

    Route params: ``{name: value}`` must equal the sent value; ``{name: None}`` means the parameter
    must be absent (how page 1 is told apart from later pages).
    """

    def __init__(self) -> None:
        self.routes: list[tuple[str, dict[str, str | None], int, str]] = []
        self.calls: list[tuple[str, list[tuple[str, str]], dict[str, str]]] = []
        self.limiter = HostRateLimiter()
        self.cache = _FakeCache()
        self.route("/api/v2/version", {}, fixture_text("version.json"))

    def route(self, path: str, params: dict[str, str | None], body: str, status: int = 200) -> None:
        self.routes.append((path, params, status, body))

    def search_route(
        self,
        body: str,
        *,
        cond: str | None = None,
        intr: str | None = None,
        term: str | None = None,
        page_token: str | None = None,
        page_size: str = "50",
        status: int = 200,
    ) -> None:
        params: dict[str, str | None] = {
            "query.cond": cond,
            "query.intr": intr,
            "query.term": term,
            "pageToken": page_token,
            "pageSize": page_size,
            "countTotal": "true" if page_token is None else None,
        }
        self.route("/api/v2/studies", params, body, status)

    def get_json(self, url: str, params: Any = None, headers: Any = None, *, namespace: str | None = None) -> CachedResponse:
        items = [(k, v) for k, v in (params or [])]
        sent = dict(items)
        self.calls.append((url, items, dict(headers or {})))
        path = httpx.URL(url).path
        for route_path, route_params, status, body in self.routes:
            if route_path != path:
                continue
            if all((sent.get(k) == v) if v is not None else (k not in sent) for k, v in route_params.items()):
                return CachedResponse(
                    status=status,
                    text=body,
                    url=str(httpx.URL(url, params=items)),
                    headers={"content-type": "application/json" if status == 200 else "text/plain; charset=UTF-8"},
                    from_cache=False,
                    retrieved_at=RETRIEVED_AT,
                    key="fake",
                )
        raise AssertionError(f"no fixture route for {path} {sent}")

    def search_calls(self) -> list[dict[str, str]]:
        return [dict(items) for url, items, _ in self.calls if httpx.URL(url).path == "/api/v2/studies"]


def seeds_for(*queries: dict[str, Any], page_size: int = 50, max_studies: int = 50) -> dict[str, Any]:
    return {
        "max_studies_per_query": max_studies,
        "page_size": page_size,
        "fields": list(ctgov.DEFAULT_FIELDS),
        "queries": list(queries),
    }


Q_STAT1_GOF = {"label": "stat1-gof", "term": '"STAT1 gain of function"'}
Q_HYPER_IGE = {"label": "hyper-ige", "cond": '"hyper IgE syndrome"'}
Q_RUX_STAT1 = {"label": "ruxolitinib-stat1", "intr": "ruxolitinib", "term": "STAT1", "gene_symbols_in_term": True}
Q_GENE = {"label": "scid-jak3", "cond": '"severe combined immunodeficiency"', "term": "JAK3", "gene_symbols_in_term": True}


def mock_node(node_id: str, node_type: str, label: str, synonyms: list[str] | None = None, props: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": node_id,
        "type": node_type,
        "label": label,
        "synonyms": synonyms or [],
        "props": props or {},
        "source": "mock",
        "retrieved_at": "2026-10-03T00:00:00+00:00",
    }


@pytest.fixture
def store(tmp_path: Path) -> Store:
    with Store(tmp_path / "ctgov.sqlite") as s:
        yield s


@pytest.fixture
def seeded_store(store: Store) -> Store:
    """Disease / Drug / Gene nodes named after fixture values so links can be made."""
    records = {nct_of(r): r for r in (fixture_json(f) for f in STUDY_FILES)}
    stat1_gof_condition = records["NCT07670156"]["protocolSection"]["conditionsModule"]["conditions"][1]
    assert stat1_gof_condition == "STAT1 GOF"
    mesh = records["NCT07670156"]["derivedSection"]["conditionBrowseModule"]["meshes"][0]
    store.upsert_nodes(
        [
            mock_node("mock:disease:1", "Disease", stat1_gof_condition),
            mock_node("mock:disease:2", "Disease", "Mock immunologic deficiency", props={"xrefs": [f"MeSH:{mesh['id']}"]}),
            mock_node("mock:drug:1", "Drug", "upadacitinib", synonyms=["Rinvoq"]),
            mock_node("mock:drug:2", "Drug", "Mock baricitinib", synonyms=["BARICITINIB"]),
            mock_node("mock:gene:1", "Gene", "STAT1", props={"symbol": "STAT1"}),
            mock_node("mock:gene:2", "Gene", "JAK3", props={"symbol": "JAK3"}),
        ]
    )
    return store


@pytest.fixture
def fake_http() -> FakeHttp:
    """Routes for the three standard queries: STAT1 GoF (3 full records, the ids of
    search_stat1_gof.json), hyper-IgE (one full record) and the zero-hit ruxolitinib + STAT1."""
    http = FakeHttp()
    gof_ids = [nct_of(s) for s in fixture_json("search_stat1_gof.json")["studies"]]
    gof_records = [fixture_json(f"study_{nct}.json") for nct in gof_ids]
    assert [nct_of(r) for r in gof_records] == gof_ids == ["NCT07261891", "NCT05561985", "NCT07670156"]
    http.search_route(envelope(gof_records, total=3), term='"STAT1 gain of function"')
    http.search_route(envelope([fixture_json("study_NCT07262983.json")], total=1), cond='"hyper IgE syndrome"')
    http.search_route(fixture_text("search_empty.json"), intr="ruxolitinib", term="EXPANSION[None]STAT1")
    return http


# --------------------------------------------------------------------------------------
# dose scrubbing
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Upadacitinib 15 mg once daily", "Upadacitinib [dose removed]"),
        ("Upadacitinib 15mg once daily", "Upadacitinib [dose removed]"),
        ("baricitinib 2 mg once daily", "baricitinib [dose removed]"),
        ("ruxolitinib 5 mg BID", "ruxolitinib [dose removed]"),
        ("ruxolitinib 20 mg b.i.d. for 24 weeks", "ruxolitinib [dose removed] for 24 weeks"),
        ("hydroxychloroquine 400 mg/day or 6.5 mg/kg/day, whichever is greater", "hydroxychloroquine [dose removed], whichever is greater"),
        ("which may be 15 mg, 30 mg, or 45 mg taken once daily", "which may be [dose removed] taken [dose removed]"),
        ("15-30 mg", "[dose removed]"),
        ("100 IU/kg twice weekly", "[dose removed]"),
        ("0.5 mg/m2 every 3 weeks", "[dose removed]"),
        ("200 mcg twice a day", "[dose removed]"),
        ("50 µg/kg", "[dose removed]"),
        ("Subject will come to the study site every 4 weeks.", "Subject will come to the study site [dose removed]."),
        ("1 or 2 tablets by mouth every day for 6 months", "[dose removed] by mouth [dose removed] for 6 months"),
        ("the dose must be less than 10 mg daily and stable", "the dose must be less than [dose removed] and stable"),
        ("body surface area tool score of 10% at screening", "body surface area tool score of [dose removed] at screening"),
        # Kept: no dose information.
        ("STAT1 GOF", "STAT1 GOF"),
        ("Aged >=12 years.", "Aged >=12 years."),
        ("Phase 1/2 basket trial; IC50 and AUC; SF-36; n=3; 1:1 randomization", "Phase 1/2 basket trial; IC50 and AUC; SF-36; n=3; 1:1 randomization"),
        ("Receipt of a live vaccine within 30 days prior to study treatment", "Receipt of a live vaccine within 30 days prior to study treatment"),
        ("Ex Vivo Evaluation of JAK-inhibitor and Gene Therapeutical Approach in JAK-STAT Related Disorders", "Ex Vivo Evaluation of JAK-inhibitor and Gene Therapeutical Approach in JAK-STAT Related Disorders"),
        ("", ""),
        (None, None),
    ],
)
def test_scrub_dose(text: str | None, expected: str | None) -> None:
    assert scrub_dose(text) == expected
    assert scrub_dose(scrub_dose(text)) == expected  # idempotent


def test_scrub_dose_on_fixture_text() -> None:
    """The baricitinib record's intervention description and eligibility criteria carry real doses."""
    record = fixture_json("study_NCT07262983.json")
    description = record["protocolSection"]["armsInterventionsModule"]["interventions"][0]["description"]
    assert "2 mg once daily" in description and "4 mg once daily" in description
    scrubbed = scrub_dose(description)
    assert "mg" not in scrubbed and "once daily" not in scrubbed
    assert scrubbed.count(DOSE_PLACEHOLDER) == 2
    criteria = scrub_dose(record["protocolSection"]["eligibilityModule"]["eligibilityCriteria"])
    assert DOSE_PLACEHOLDER in criteria
    assert re.search(r"\d\s*(?:mg|mcg|µg|g/dL|mg/dL)\b", criteria) is None
    assert "once daily" not in criteria.lower() and "per day" not in criteria.lower()


# --------------------------------------------------------------------------------------
# seeds and query construction
# --------------------------------------------------------------------------------------


def test_wrap_gene_symbols_only_wraps_bare_symbols() -> None:
    assert wrap_gene_symbols("STAT1") == "EXPANSION[None]STAT1"
    assert wrap_gene_symbols("STAT1 AND JAK3") == "EXPANSION[None]STAT1 AND EXPANSION[None]JAK3"
    assert wrap_gene_symbols('"STAT1 gain of function"') == '"STAT1 gain of function"'
    assert wrap_gene_symbols('"chronic mucocutaneous candidiasis" AND IL2RG') == '"chronic mucocutaneous candidiasis" AND EXPANSION[None]IL2RG'
    assert wrap_gene_symbols("ruxolitinib") == "ruxolitinib"
    assert wrap_gene_symbols("EXPANSION[None]STAT1") == "EXPANSION[None]STAT1"
    assert wrap_gene_symbols("AREA[Condition]STAT1") == "AREA[Condition]STAT1"


def test_seed_query_sent_term_depends_on_flag() -> None:
    assert SeedQuery.from_mapping(Q_RUX_STAT1).sent_term == "EXPANSION[None]STAT1"
    assert SeedQuery.from_mapping(Q_RUX_STAT1).gene_symbols == ["STAT1"]
    assert SeedQuery.from_mapping({"label": "x", "term": "STAT1"}).sent_term == "STAT1"
    assert SeedQuery.from_mapping({"label": "x", "term": "STAT1"}).gene_symbols == []
    q = SeedQuery.from_mapping(Q_STAT1_GOF)
    assert q.sent_term == '"STAT1 gain of function"' and q.gene_symbols == []
    assert SeedQuery.from_mapping(Q_HYPER_IGE).cond_phrase == "hyper IgE syndrome"
    assert SeedQuery.from_mapping(Q_RUX_STAT1).intr_phrase == "ruxolitinib"
    for bad in ({"label": "x"}, {"label": "", "cond": "a"}, {"label": "x", "cond": "a", "bogus": 1}, {"label": "x", "cond": "a", "max_studies": 0}):
        with pytest.raises(ValueError):
            SeedQuery.from_mapping(bad)


def test_build_page_params_and_params_string() -> None:
    q = SeedQuery.from_mapping(Q_GENE)
    fields = ["NCTId", "BriefTitle"]
    page1 = build_page_params(q, page_size=50, fields=fields)
    assert page1 == [
        ("query.cond", '"severe combined immunodeficiency"'),
        ("query.term", "EXPANSION[None]JAK3"),
        ("countTotal", "true"),
        ("pageSize", "50"),
        ("fields", "NCTId,BriefTitle"),
    ]
    page2 = build_page_params(q, page_size=50, fields=fields, page_token="tok")
    assert ("countTotal", "true") not in page2
    assert page2[-1] == ("pageToken", "tok")
    assert [k for k, _ in page2] == ["query.cond", "query.term", "pageSize", "fields", "pageToken"]
    s = params_string(page1)
    assert "fields=" not in s
    assert s == "query.cond=%22severe+combined+immunodeficiency%22&query.term=EXPANSION%5BNone%5DJAK3&countTotal=true&pageSize=50"
    assert "fields=NCTId%2CBriefTitle" in params_string(page1, include_fields=True)


_ID_LIKE = re.compile(
    r"\b(?:MONDO|HP|EFO|Orphanet|ORPHA|OMIM|DOID|GO|CHEMBL|HGNC|MedGen|UMLS|PMC|PMID|NCBIGene|MeSH|MESH)[:_ ]?[CD]?\d{3,}\b"
    r"|\bNCT\d{6,}\b|\bENSG\d{6,}\b|\bVCV\d{6,}\b|\brs\d{4,}\b",
    re.IGNORECASE,
)


def test_seed_file_holds_names_only_and_the_required_queries() -> None:
    text = (config.SEEDS_DIR / "ctgov.yaml").read_text(encoding="utf-8")
    assert _ID_LIKE.search(text) is None, _ID_LIKE.search(text)
    seeds = config.load_seeds("ctgov")
    settings = parse_seeds(seeds)
    assert settings.page_size == 50 and settings.max_studies_per_query == 50
    assert settings.fields == ctgov.DEFAULT_FIELDS
    assert "NCTId" in settings.fields and "ReferencePMID" in settings.fields and "EligibilityModule" in settings.fields
    by_label = {q.label: q for q in settings.queries}
    assert len(by_label) == len(settings.queries) == 10
    assert by_label["stat1-gof"].sent_term == '"STAT1 gain of function"'
    assert by_label["stat3-gof"].sent_term == '"STAT3 gain of function"'
    assert by_label["cmc"].cond == '"chronic mucocutaneous candidiasis"'
    assert by_label["ruxolitinib-stat1"].intr == "ruxolitinib"
    assert by_label["ruxolitinib-stat1"].sent_term == "EXPANSION[None]STAT1"
    assert by_label["jak-inhibitor-pid"].intr == "JAK inhibitor" and by_label["jak-inhibitor-pid"].cond == '"primary immunodeficiency"'
    assert by_label["hyper-ige"].cond == '"hyper IgE syndrome"'
    assert by_label["scid-jak3"].sent_term == "EXPANSION[None]JAK3"
    for label, phrase in (("pv-ruxolitinib", "polycythemia vera"), ("et-ruxolitinib", "essential thrombocythemia"), ("pmf-ruxolitinib", "primary myelofibrosis")):
        assert by_label[label].cond_phrase == phrase and by_label[label].intr == "ruxolitinib"
        assert settings.cap_for(by_label[label]) == 20
    # The too-broad "JAK inhibitor" + "autoimmune" query is excluded.
    assert not any("autoimmune" in " ".join(filter(None, (q.cond, q.intr, q.term))).lower() for q in settings.queries)


def test_parse_seeds_rejects_bad_shapes() -> None:
    with pytest.raises(ValueError):
        parse_seeds({"queries": []})
    with pytest.raises(ValueError):
        parse_seeds(seeds_for(Q_STAT1_GOF, Q_STAT1_GOF))
    with pytest.raises(ValueError):
        parse_seeds({**seeds_for(Q_STAT1_GOF), "fields": ["BriefTitle"]})
    with pytest.raises(ValueError):
        parse_seeds({**seeds_for(Q_STAT1_GOF), "page_size": 0})


# --------------------------------------------------------------------------------------
# record parsing
# --------------------------------------------------------------------------------------


def test_study_id_format() -> None:
    assert study_id("NCT07670156") == "nct:NCT07670156"
    assert study_id(" nct07670156 ") == "nct:NCT07670156"
    for bad in ("NCT123", "07670156", "", "NCT0767015X"):
        with pytest.raises(ValueError):
            study_id(bad)


def test_parse_study_full_record() -> None:
    p = parse_study(fixture_json("study_NCT07670156.json"))
    assert p["nct_id"] == "NCT07670156"
    assert p["brief_title"] == "Upadacitinib in Treatment of JAK/STAT Pathway Disorders With Activating Mutations"
    assert p["official_title"].startswith("Safety and Efficacy of Upadacitinib")
    assert p["overall_status"] == "NOT_YET_RECRUITING"
    assert p["start_date"] == "2026-10" and p["start_date_type"] == "ESTIMATED"
    assert p["primary_completion_date"] == "2028-08"
    assert p["last_update"] == "2026-09-08" and p["study_first_post_date"] == "2026-06-26"
    assert p["sponsor"] == "Lisa Satter" and p["sponsor_class"] == "OTHER"
    assert p["collaborators"] == ["National Center for Advancing Translational Sciences (NCATS)", "AbbVie"]
    assert p["responsible_party_type"] == "SPONSOR_INVESTIGATOR"
    assert p["conditions"] == ["JAK1 GOF", "STAT1 GOF", "STAT3 GOF", "STAT5B GOF", "STAT6 GOF"]
    assert "Upadacitinib" in p["keywords"] and "STAT1" in p["keywords"]
    assert p["study_type"] == "INTERVENTIONAL" and p["phases"] == ["PHASE1", "PHASE2"]
    assert p["enrollment"] == 30 and p["enrollment_type"] == "ESTIMATED"
    assert p["design_allocation"] == "RANDOMIZED" and p["primary_purpose"] == "TREATMENT"
    assert p["interventions"] == [
        {"name": "Upadacitinib", "type": "DRUG", "other_names": ["Rinvoq"]},
        {"name": "Placebo", "type": "DRUG", "other_names": []},
    ]
    elig = p["eligibility"]
    assert elig["min_age"] == "12 Years" and elig["max_age"] == "65 Years" and elig["sex"] == "ALL"
    assert elig["healthy_volunteers"] is False and elig["std_ages"] == ["CHILD", "ADULT", "OLDER_ADULT"]
    assert elig["criteria"].startswith("Inclusion Criteria:") and elig["truncated"] is False
    assert p["overall_officials"] == ["Lisa F Satter, MD"]
    assert p["references"] == [] and p["reference_pmids"] == []
    assert p["condition_mesh_terms"] == ["Immunologic Deficiency Syndromes"] and p["condition_mesh_ids"] == ["D007153"]
    assert p["intervention_mesh_terms"] == ["upadacitinib"]
    assert p["version_holder"] == "2026-10-02" and p["has_results"] is False


def test_parse_study_sparse_expanded_access_record() -> None:
    p = parse_study(fixture_json("study_NCT05561985.json"))
    assert p["nct_id"] == "NCT05561985"
    assert p["study_type"] == "EXPANDED_ACCESS" and p["overall_status"] == "AVAILABLE"
    assert p["phases"] == [] and p["enrollment"] is None and p["enrollment_type"] is None
    assert p["start_date"] is None and p["official_title"] is None
    assert p["eligibility"]["sex"] is None and p["eligibility"]["min_age"] is None and p["eligibility"]["max_age"] is None
    assert p["eligibility"]["std_ages"] == ["CHILD", "ADULT", "OLDER_ADULT"]
    assert p["conditions"] == ["STAT1 Gain-of-Function Disease"]
    assert p["interventions"] == [{"name": "Itacitinib", "type": "DRUG", "other_names": ["INCB039110"]}]
    assert p["condition_mesh_terms"] == [] and p["condition_mesh_ids"] == []
    assert p["intervention_mesh_terms"] == ["itacitinib", "INCB039110"]
    assert p["reference_pmids"] == [] and p["last_update"] == "2026-06-10"
    assert p["sponsor"] == "Incyte Corporation" and p["has_results"] is False


def test_parse_study_reference_pmids_come_from_the_record() -> None:
    record = fixture_json("study_NCT07261891.json")
    expected = [r["pmid"] for r in record["protocolSection"]["referencesModule"]["references"]]
    p = parse_study(record)
    assert p["reference_pmids"] == expected and len(expected) == 3
    assert all(r["type"] == "BACKGROUND" for r in p["references"])
    assert p["conditions"] == ["Primary Immunodeficiency Diseases (PID)"]
    assert p["interventions"] == [{"name": "blood sampling", "type": "DIAGNOSTIC_TEST", "other_names": []}]
    assert p["phases"] == ["NA"] and p["enrollment"] == 20


def test_parse_study_search_hit_without_title_or_nct() -> None:
    sparse = fixture_json("search_page.json")["studies"][0]
    p = parse_study(sparse)
    assert p["nct_id"] == "NCT00005933" and p["brief_title"] is None and p["conditions"] == []
    with pytest.raises(ValueError):
        parse_study({"protocolSection": {}})


# --------------------------------------------------------------------------------------
# load: nodes, links, coverage, sources
# --------------------------------------------------------------------------------------


def test_load_writes_study_nodes_with_props(seeded_store: Store, fake_http: FakeHttp) -> None:
    summary = load(seeded_store, seeds_for(Q_STAT1_GOF, Q_HYPER_IGE, Q_RUX_STAT1), http=fake_http)
    assert summary["studies"] == 4 and summary["errors"] == []
    assert seeded_store.nodes_by_type()["Study"] == 4
    node = seeded_store.get_node("nct:NCT07670156")
    assert node is not None
    assert node["type"] == "Study" and node["source"] == SOURCE_NAME and node["retrieved_at"] == RETRIEVED_AT
    assert node["label"] == "Upadacitinib in Treatment of JAK/STAT Pathway Disorders With Activating Mutations"
    assert node["synonyms"] == ["Safety and Efficacy of Upadacitinib in Treatment of JAK/STAT Pathway Disorders With Activating Mutations", "NCT07670156"]
    props = node["props"]
    assert props["record_id"] == "NCT07670156"
    assert props["source_url"] == "https://clinicaltrials.gov/study/NCT07670156"
    assert props["overall_status"] == "NOT_YET_RECRUITING" and props["phases"] == ["PHASE1", "PHASE2"]
    assert props["study_type"] == "INTERVENTIONAL" and props["enrollment"] == 30
    assert props["start_date"] == "2026-10" and props["last_update"] == "2026-09-08"
    assert props["conditions"] == ["JAK1 GOF", "STAT1 GOF", "STAT3 GOF", "STAT5B GOF", "STAT6 GOF"]
    assert props["interventions"][0] == {"name": "Upadacitinib", "type": "DRUG", "other_names": ["Rinvoq"]}
    assert props["eligibility"]["min_age"] == "12 Years" and props["eligibility"]["sex"] == "ALL"
    assert props["sponsor"] == "Lisa Satter" and props["has_results"] is False
    assert props["reference_pmids"] == [] and props["condition_mesh_terms"] == ["Immunologic Deficiency Syndromes"]
    assert props["data_timestamp"] == "2026-10-02T09:00:04" and props["version_holder"] == "2026-10-02"
    assert props["found_by_queries"] == ["stat1-gof"]
    # Sparse expanded-access record: every optional field is present and None / empty.
    sparse = seeded_store.get_node("nct:NCT05561985")["props"]
    assert sparse["phases"] == [] and sparse["enrollment"] is None and sparse["start_date"] is None
    assert sparse["eligibility"]["sex"] is None and sparse["study_type"] == "EXPANDED_ACCESS"
    # Study found by a query gets the query label; the baricitinib record has 3 reference PMIDs.
    bari = seeded_store.get_node("nct:NCT07262983")["props"]
    assert bari["found_by_queries"] == ["hyper-ige"] and len(bari["reference_pmids"]) == 3
    assert bari["eligibility"]["truncated"] is True and len(bari["eligibility"]["criteria"]) == 4000


def test_load_links_to_existing_disease_drug_nodes_and_records_unlinked(seeded_store: Store, fake_http: FakeHttp) -> None:
    load(seeded_store, seeds_for(Q_STAT1_GOF, Q_HYPER_IGE, Q_RUX_STAT1), http=fake_http)
    edges = {(e["source"], e["target"], e["relation"]): e for e in seeded_store.iter_edges()}
    # condition "STAT1 GOF" == fake Disease label (exact, case-insensitive).
    e = edges[("nct:NCT07670156", "mock:disease:1", "studies")]
    assert e["source_db"] == SOURCE_NAME and e["retrieved_at"] == RETRIEVED_AT
    assert e["props"]["match"] == {"method": "condition", "matched_text": "STAT1 GOF"}
    assert e["props"]["matched_text"] == "STAT1 GOF" and e["props"]["method"] == "condition"
    assert e["props"]["evidence_class"] == "observed"
    assert e["props"]["overall_status"] == "NOT_YET_RECRUITING" and e["props"]["phases"] == ["PHASE1", "PHASE2"]
    assert e["props"]["enrollment"] == 30 and e["props"]["study_type"] == "INTERVENTIONAL" and e["props"]["has_results"] is False
    # MeSH id xref on a Disease node.
    m = edges[("nct:NCT07670156", "mock:disease:2", "studies")]
    assert m["props"]["match"] == {"method": "condition_mesh_xref", "matched_text": "MESH:D007153"}
    # Intervention name == fake Drug label, case-insensitive ("Upadacitinib" vs "upadacitinib").
    d = edges[("nct:NCT07670156", "mock:drug:1", "evaluates")]
    assert d["props"]["match"]["method"] == "intervention_name" and d["props"]["match"]["matched_text"] == "Upadacitinib"
    assert d["props"]["intervention_type"] == "DRUG"
    assert sorted(d["props"]["matched_texts"]) == ["Rinvoq", "Upadacitinib", "upadacitinib"]  # other name + MeSH term also matched
    # Synonym match for the baricitinib record ("baricitinib" vs synonym "BARICITINIB").
    b = edges[("nct:NCT07262983", "mock:drug:2", "evaluates")]
    assert b["props"]["match"]["matched_text"] == "baricitinib"
    # No Study -> Gene edge: every study found by the gene query ... the zero-hit query found none.
    assert seeded_store.edges_by_relation() == {"evaluates": 2, "studies": 2}
    # Unlinked values are kept raw on the node, never guessed.
    props = seeded_store.get_node("nct:NCT07670156")["props"]
    assert props["unlinked_conditions"] == ["JAK1 GOF", "STAT3 GOF", "STAT5B GOF", "STAT6 GOF"]
    assert props["unlinked_interventions"] == ["Placebo"] and props["unlinked_genes"] == []
    sparse = seeded_store.get_node("nct:NCT05561985")["props"]
    assert sparse["unlinked_conditions"] == ["STAT1 Gain-of-Function Disease"]
    assert sparse["unlinked_interventions"] == ["Itacitinib"]
    assert seeded_store.get_node("nct:NCT07261891")["props"]["unlinked_interventions"] == ["blood sampling"]


def test_load_coverage_rows_including_zero_hits(seeded_store: Store, fake_http: FakeHttp) -> None:
    load(seeded_store, seeds_for(Q_STAT1_GOF, Q_HYPER_IGE, Q_RUX_STAT1), http=fake_http)
    rows = seeded_store.coverage_rows()
    searches = [r for r in rows if r["source"] == COVERAGE_SOURCE]
    zero = [r for r in searches if r["hits"] == 0]
    rux_query = "query.intr=ruxolitinib&query.term=EXPANSION%5BNone%5DSTAT1&countTotal=true&pageSize=50"
    # The zero-hit query is recorded under its seed key AND under the Drug and Gene nodes it is about.
    # (the seeded store has no ruxolitinib Drug node, so no Drug row; see the next test for Gene + Drug rows).
    assert {(r["entity_id"], r["query"], r["date"]) for r in zero} == {
        ("seed:ctgov_query:ruxolitinib-stat1", rux_query, RETRIEVED_DATE),
        ("mock:gene:1", rux_query, RETRIEVED_DATE),
    }
    gof = [r for r in searches if r["entity_id"] == "seed:ctgov_query:stat1-gof"]
    assert gof == [{"entity_id": "seed:ctgov_query:stat1-gof", "source": COVERAGE_SOURCE, "query": "query.term=%22STAT1+gain+of+function%22&countTotal=true&pageSize=50", "date": RETRIEVED_DATE, "hits": 3}]
    assert [r["hits"] for r in searches if r["entity_id"] == "seed:ctgov_query:hyper-ige"] == [1]
    # One linking row per study: hits = number of links made (0 for the itacitinib record).
    links = {r["entity_id"]: r["hits"] for r in rows if r["source"] == SOURCE_NAME}
    assert links == {"nct:NCT05561985": 0, "nct:NCT07261891": 0, "nct:NCT07262983": 1, "nct:NCT07670156": 3}
    assert all(r["query"] == ctgov.LINK_QUERY and r["date"] == RETRIEVED_DATE for r in rows if r["source"] == SOURCE_NAME)


def test_load_sources_row_and_terms(store: Store, fake_http: FakeHttp) -> None:
    load(store, seeds_for(Q_STAT1_GOF), http=fake_http)
    (row,) = store.sources_rows()
    assert row["name"] == SOURCE_NAME
    assert row["version"] == fixture_json("version.json")["dataTimestamp"] == "2026-10-02T09:00:04"
    assert row["retrieved_at"] == RETRIEVED_AT
    assert row["url"].startswith("https://clinicaltrials.gov/api/v2")
    lic = row["license"]
    assert "ClinicalTrials.gov" in lic and "terms-conditions" in lic
    assert "email" in lic.lower() and "marketing" in lic.lower()
    assert "attribute" in lic.lower() and "date the data were processed" in lic.lower()
    notes = row["notes"]
    assert notes["api_version"] == "2.0.5" and notes["data_timestamp"] == "2026-10-02T09:00:04"
    assert "[dose removed]" in notes["modifications"]
    assert notes["queries"][0]["label"] == "stat1-gof" and notes["queries"][0]["total_count"] == 3
    assert notes["queries"][0]["nct_ids"] == ["NCT07261891", "NCT05561985", "NCT07670156"]
    assert "ctgov_errors" not in notes


def test_load_sends_user_agent_and_never_fetches_twice(store: Store, fake_http: FakeHttp) -> None:
    load(store, seeds_for(Q_STAT1_GOF, Q_HYPER_IGE, Q_RUX_STAT1), http=fake_http)
    assert len(fake_http.calls) == 4  # version + three single-page searches
    for url, _items, headers in fake_http.calls:
        assert headers == {"User-Agent": config.USER_AGENT}
        assert url.startswith(ctgov.BASE_URL)
    keys = [(url, tuple(items)) for url, items, _ in fake_http.calls]
    assert len(keys) == len(set(keys))
    for sent in fake_http.search_calls():
        assert sent["fields"] == ",".join(ctgov.DEFAULT_FIELDS)
        assert sent["countTotal"] == "true" and sent["pageSize"] == "50"
    # Courtesy throttle for the host (no rate limit is documented).
    assert fake_http.limiter.limit_for("clinicaltrials.gov") == ctgov.COURTESY_RATE


def test_load_works_without_backbone_nodes(store: Store, fake_http: FakeHttp) -> None:
    """A scratch store with no Disease / Drug / Gene nodes still gets the Study nodes."""
    summary = load(store, seeds_for(Q_STAT1_GOF, Q_HYPER_IGE, Q_RUX_STAT1), http=fake_http)
    assert summary["studies"] == 4 and summary["edges"] == 0
    assert store.counts()["edges"] == 0 and store.nodes_by_type() == {"Study": 4}
    props = store.get_node("nct:NCT07670156")["props"]
    assert props["unlinked_conditions"] == props["conditions"]
    assert props["unlinked_interventions"] == ["Upadacitinib", "Placebo"]
    assert {r["entity_id"]: r["hits"] for r in store.coverage_rows() if r["source"] == SOURCE_NAME} == {
        "nct:NCT05561985": 0, "nct:NCT07261891": 0, "nct:NCT07262983": 0, "nct:NCT07670156": 0,
    }
    assert summary["unlinked"]["nct:NCT07670156"]["unlinked_interventions"] == ["Upadacitinib", "Placebo"]


def test_gene_symbol_query_yields_inferred_mentions_edges(seeded_store: Store) -> None:
    """Studies found through a gene-symbol query and linked to nothing else point at the Gene."""
    http = FakeHttp()
    http.search_route(fixture_text("search_stat1_gof.json"), cond='"severe combined immunodeficiency"', term="EXPANSION[None]JAK3")
    load(seeded_store, seeds_for(Q_GENE), http=http)
    sent = http.search_calls()[0]
    assert sent["query.term"] == "EXPANSION[None]JAK3" and sent["query.cond"] == '"severe combined immunodeficiency"'
    nodes = {n["id"]: n for n in seeded_store.iter_nodes("Study")}
    assert set(nodes) == {"nct:NCT07261891", "nct:NCT05561985", "nct:NCT07670156"}
    # Sparse search hits: label = briefTitle, status kept, everything else empty.
    n = nodes["nct:NCT05561985"]
    assert n["label"].startswith("Expanded Access Use of Itacitinib") and n["synonyms"] == ["NCT05561985"]
    assert n["props"]["overall_status"] == "AVAILABLE" and n["props"]["conditions"] == [] and n["props"]["found_by_queries"] == ["scid-jak3"]
    edges = list(seeded_store.iter_edges(relation="mentions"))
    assert {e["source"] for e in edges} == set(nodes) and {e["target"] for e in edges} == {"mock:gene:2"}
    for e in edges:
        assert e["props"]["evidence_class"] == "inferred"
        assert e["props"]["match"] == {"method": "query_term", "matched_text": "JAK3"}
    assert seeded_store.edges_by_relation() == {"mentions": 3}
    assert n["props"]["unlinked_genes"] == []
    # Coverage under the seed key and under the Gene node the query is about; hits = totalCount 3.
    cov = {(r["entity_id"], r["hits"]) for r in seeded_store.coverage_rows() if r["source"] == COVERAGE_SOURCE}
    assert cov == {("seed:ctgov_query:scid-jak3", 3), ("mock:gene:2", 3)}


def test_gene_symbol_query_without_gene_node_records_unlinked_gene(store: Store) -> None:
    http = FakeHttp()
    http.search_route(fixture_text("search_stat1_gof.json"), cond='"severe combined immunodeficiency"', term="EXPANSION[None]JAK3")
    load(store, seeds_for(Q_GENE), http=http)
    assert store.counts()["edges"] == 0
    assert store.get_node("nct:NCT07670156")["props"]["unlinked_genes"] == ["JAK3"]


def test_pagination_follows_tokens_and_honours_the_cap(store: Store) -> None:
    page1, page2, last = (fixture_json(f) for f in ("search_page.json", "search_page_2.json", "search_page_last.json"))
    assert "totalCount" in page1 and "totalCount" not in page2 and "nextPageToken" not in last
    cond = '"hyper IgE syndrome"'

    def make_http() -> FakeHttp:
        http = FakeHttp()
        http.search_route(fixture_text("search_page.json"), cond=cond, page_size="5")
        http.search_route(fixture_text("search_page_2.json"), cond=cond, page_size="5", page_token=page1["nextPageToken"])
        http.search_route(fixture_text("search_page_last.json"), cond=cond, page_size="5", page_token=page2["nextPageToken"])
        return http

    all_ids = [nct_of(s) for p in (page1, page2, last) for s in p["studies"]]
    assert len(all_ids) == 11 and len(set(all_ids)) == 11

    # No cap reached: every page is fetched, 11 studies, totalCount 14 recorded as hits.
    http = make_http()
    summary = load(store, seeds_for(Q_HYPER_IGE, page_size=5, max_studies=50), http=http)
    assert summary["queries"][0]["pages"] == 3 and summary["queries"][0]["fetched"] == 11 and summary["queries"][0]["truncated"] is False
    assert sorted(n["props"]["record_id"] for n in store.iter_nodes("Study")) == sorted(all_ids)
    calls = http.search_calls()
    assert len(calls) == 3
    assert "pageToken" not in calls[0] and calls[0]["countTotal"] == "true"
    assert calls[1]["pageToken"] == page1["nextPageToken"] and "countTotal" not in calls[1]
    assert calls[2]["pageToken"] == page2["nextPageToken"] and "countTotal" not in calls[2]
    for c in calls:
        assert c["query.cond"] == cond and c["pageSize"] == "5" and c["fields"] == ",".join(ctgov.DEFAULT_FIELDS)
    cov = [r for r in store.coverage_rows() if r["entity_id"] == "seed:ctgov_query:hyper-ige"]
    assert cov == [{"entity_id": "seed:ctgov_query:hyper-ige", "source": COVERAGE_SOURCE, "query": "query.cond=%22hyper+IgE+syndrome%22&countTotal=true&pageSize=5", "date": RETRIEVED_DATE, "hits": 14}]

    # Cap 7: page 1 (2) + page 2 (5) reach the cap, the last page is never requested.
    with Store(Path(store.path).with_name("capped.sqlite")) as capped:
        http = make_http()
        summary = load(capped, seeds_for(Q_HYPER_IGE, page_size=5, max_studies=7), http=http)
        assert len(http.search_calls()) == 2
        assert summary["queries"][0]["fetched"] == 7 and summary["queries"][0]["pages"] == 2 and summary["queries"][0]["truncated"] is True
        assert capped.nodes_by_type() == {"Study": 7}
        assert [r["hits"] for r in capped.coverage_rows() if r["source"] == COVERAGE_SOURCE] == [14]


def test_study_found_by_two_queries_is_one_node_with_both_labels(store: Store) -> None:
    http = FakeHttp()
    gof = [fixture_json("study_NCT07670156.json")]
    http.search_route(envelope(gof, total=1), term='"STAT1 gain of function"')
    http.search_route(envelope(gof, total=1), term='"STAT3 gain of function"')
    load(store, seeds_for(Q_STAT1_GOF, {"label": "stat3-gof", "term": '"STAT3 gain of function"'}), http=http)
    assert store.nodes_by_type() == {"Study": 1}
    assert store.get_node("nct:NCT07670156")["props"]["found_by_queries"] == ["stat1-gof", "stat3-gof"]


def test_plain_text_error_is_recorded_on_the_sources_row_then_raised(store: Store, fake_http: FakeHttp) -> None:
    bad = {"label": "bad-fields", "cond": '"nonexistent fixture condition"'}
    fake_http.search_route("Parameter 'fields' contains invalid field name: 'NotARealField'", cond=bad["cond"], status=400)
    with pytest.raises(CtgovError) as info:
        load(store, seeds_for(Q_STAT1_GOF, bad, Q_HYPER_IGE), http=fake_http)
    err = info.value
    assert [e["label"] for e in err.errors] == ["bad-fields"]
    assert err.errors[0]["status"] == 400 and "invalid field name" in err.errors[0]["error"]
    assert err.summary["studies"] == 4
    (row,) = store.sources_rows()
    assert row["notes"]["ctgov_errors"] == err.errors
    assert [q["label"] for q in row["notes"]["queries"]] == ["stat1-gof", "hyper-ige"]
    # The other queries still ran and were written.
    assert store.nodes_by_type() == {"Study": 4}
    assert not any(r["entity_id"] == "seed:ctgov_query:bad-fields" for r in store.coverage_rows())


def test_version_failure_raises_before_any_query(store: Store) -> None:
    http = FakeHttp()
    http.routes.clear()
    http.route("/api/v2/version", {}, "Service Unavailable", status=503)
    with pytest.raises(ctgov.CtgovHttpError):
        load(store, seeds_for(Q_STAT1_GOF), http=http)
    assert store.counts()["nodes"] == 0 and len(http.calls) == 1


# --------------------------------------------------------------------------------------
# no dosing / contact text reaches the store
# --------------------------------------------------------------------------------------

_DOSE_LEAK = re.compile(r"\d\s*(?:mg|mcg|µg|μg|IU)\b|\b(?:once|twice)\s+daily\b|\bb\.?i\.?d\.?\b|\bmg/kg\b", re.IGNORECASE)


def test_no_dose_or_contact_text_reaches_the_store(seeded_store: Store, fake_http: FakeHttp) -> None:
    load(seeded_store, seeds_for(Q_STAT1_GOF, Q_HYPER_IGE, Q_RUX_STAT1), http=fake_http)
    dumped = json.dumps([n for n in seeded_store.iter_nodes("Study")] + list(seeded_store.iter_edges()), ensure_ascii=False)
    assert _DOSE_LEAK.search(dumped) is None, _DOSE_LEAK.search(dumped)
    assert "[scrubbed]" not in dumped  # the fixtures' contact placeholders are never read
    assert '"email"' not in dumped and '"phone"' not in dumped and "centralContacts" not in dumped
    criteria = seeded_store.get_node("nct:NCT07262983")["props"]["eligibility"]["criteria"]
    assert DOSE_PLACEHOLDER in criteria


# --------------------------------------------------------------------------------------
# real CachedHttp: cache, offline, user agent
# --------------------------------------------------------------------------------------


def _mock_transport(hits: list[httpx.Request]) -> httpx.MockTransport:
    gof_records = [fixture_json(f"study_{nct_of(s)}.json") for s in fixture_json("search_stat1_gof.json")["studies"]]

    def handler(request: httpx.Request) -> httpx.Response:
        hits.append(request)
        if request.url.path == "/api/v2/version":
            return httpx.Response(200, text=fixture_text("version.json"), headers={"content-type": "application/json"})
        if request.url.path == "/api/v2/studies":
            if request.url.params.get("query.term") == '"STAT1 gain of function"':
                return httpx.Response(200, text=envelope(gof_records, total=3), headers={"content-type": "application/json"})
            if request.url.params.get("query.intr") == "ruxolitinib":
                return httpx.Response(200, text=fixture_text("search_empty.json"), headers={"content-type": "application/json"})
        return httpx.Response(404, text="NCT number NCT04409146 not found", headers={"content-type": "text/plain; charset=UTF-8"})

    return httpx.MockTransport(handler)


def test_cached_http_round_trip_and_offline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ATLAS_OFFLINE", raising=False)
    cache_dir = tmp_path / "cache"
    seeds = seeds_for(Q_STAT1_GOF, Q_RUX_STAT1)

    hits: list[httpx.Request] = []
    with CachedHttp(DiskCache(cache_dir, offline=False), transport=_mock_transport(hits)) as http, Store(tmp_path / "a.sqlite") as store:
        summary = load(store, seeds, http=http)
        assert summary["studies"] == 3
        first_nodes = list(store.iter_nodes("Study"))
        assert len(hits) == 3  # version + two searches, each sent exactly once
        assert all(r.headers["user-agent"] == config.USER_AGENT for r in hits)
        assert all(r.url.params["pageSize"] == "50" for r in hits if r.url.path == "/api/v2/studies")
        assert http.limiter.limit_for("clinicaltrials.gov") == ctgov.COURTESY_RATE
        # Second run in the same process: everything from cache, nothing fetched twice.
        load(store, seeds, http=http)
        assert len(hits) == 3

    # A fresh offline client over the same cache reproduces the store, never touching the network.
    offline_hits: list[httpx.Request] = []
    with CachedHttp(DiskCache(cache_dir, offline=True), transport=_mock_transport(offline_hits)) as http, Store(tmp_path / "b.sqlite") as store:
        load(store, seeds, http=http, offline=True)
        assert offline_hits == []
        assert list(store.iter_nodes("Study")) == first_nodes  # same retrieved_at: the cache entry's
        assert {r["date"] for r in store.coverage_rows()} == {first_nodes[0]["retrieved_at"][:10]}

    # Offline with an empty cache is a CacheMissOffline, not a network call.
    miss_hits: list[httpx.Request] = []
    with CachedHttp(DiskCache(tmp_path / "empty", offline=False), transport=_mock_transport(miss_hits)) as http, Store(tmp_path / "c.sqlite") as store:
        with pytest.raises(CacheMissOffline):
            load(store, seeds, http=http, offline=True)
        assert miss_hits == []
        assert http.cache.offline is False  # the offline switch was restored on the shared client


def test_main_cli_writes_the_given_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    fake = FakeHttp()
    fake.search_route(envelope([fixture_json("study_NCT07670156.json")], total=1), term='"STAT1 gain of function"')
    monkeypatch.setattr(ctgov, "default_http", lambda: fake)
    monkeypatch.setattr(config, "load_seeds", lambda name: seeds_for(Q_STAT1_GOF))
    path = tmp_path / "cli.sqlite"
    assert ctgov.main(["--store", str(path), "--seeds", "ctgov"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["studies"] == 1 and out["store_counts"]["nodes"] == 1 and "seconds" in out
    with Store(path) as s:
        assert s.nodes_by_type() == {"Study": 1}
