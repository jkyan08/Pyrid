"""pipeline/sources/reporter.py against the recorded NIH RePORTER fixtures.

Fully offline: every request goes through ``httpx.MockTransport`` serving the verbatim API
responses under ``tests/fixtures/sources/reporter/`` into a cache under ``tmp_path``; time is
faked so the 1 req/s RePORTER limiter never sleeps for real. Identifiers asserted on below are
observed values from those fixtures (recorded data, not typed facts); graph nodes pre-seeded into
the store use ``mock:`` ids and ``"Mock "`` labels.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest

from pipeline import config
from pipeline.cache import CachedHttp, CacheMissOffline, DiskCache
from pipeline.sources import reporter
from pipeline.sources.reporter import (
    COVERAGE_LINKS_SOURCE,
    COVERAGE_PROJECTS_SOURCE,
    COVERAGE_PUBLICATIONS_SOURCE,
    DOSE_PLACEHOLDER,
    PROJECTS_URL,
    PUBLICATIONS_URL,
    RELATION,
    REPORTER_HOST,
    SOURCE_NAME,
    ReporterApiError,
    build_investigator_id,
    error_message,
    fiscal_years_for,
    load,
    person_name,
    scrub_doses,
)
from pipeline.store import Store

FIXTURES = Path(__file__).parent / "fixtures" / "sources" / "reporter"
MOCK_TS = "2026-10-03T00:00:00+00:00"
TODAY = date(2026, 10, 3)  # FY2027 started on 2026-10-01

EMPTY_QUERY = "qzxv9876nonexistentterm"  # the zero-hit search recorded in projects_search_empty.json
DUP_QUERY = "mock duplicate query"  # served the page fixture plus a duplicated record (see dup_page)
DOSE_QUERY = "mock dose query"  # served the page fixture with dosing text injected into every free-text field (see dose_page)


# --------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def fixture_json(name: str) -> Any:
    return json.loads(fixture_bytes(name))


def page_records() -> list[dict[str, Any]]:
    return fixture_json("projects_search_page.json")["results"]


def dup_page() -> bytes:
    """The page fixture plus a second application record of the first grant: a different (mock)
    ``appl_id`` and the previous fiscal year, same ``core_project_num``."""
    payload = fixture_json("projects_search_page.json")
    first = payload["results"][0]
    dup = dict(first)
    dup["appl_id"] = -1  # mock: a record key that cannot exist in RePORTER
    dup["fiscal_year"] = int(first["fiscal_year"]) - 1
    dup["project_num"] = first["project_num"] + "-mockdup"
    dup["award_amount"] = 1000  # mock amount so the per-grant total is checkable
    payload["results"].append(dup)
    payload["meta"]["total"] = payload["meta"]["total"] + 1
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def dose_page() -> bytes:
    """The page fixture with mock dosing language injected into every free-text field the loader
    stores or scans: title, abstract, organisation and PI title."""
    payload = fixture_json("projects_search_page.json")
    first = payload["results"][0]
    first["project_title"] = first["project_title"] + " treated with 5 mg daily and 0.5 mg/kg BID"
    first["abstract_text"] = "Mock abstract: 400 mg/m2 every 2 weeks, then twice daily. " + (first["abstract_text"] or "")
    first["organization"]["org_name"] = "MOCK INSTITUTE 500 MG WING"
    first["organization"]["org_city"] = "Mock City 10 IU"
    first["principal_investigators"][0]["title"] = "PROFESSOR OF 10 mg/kg DOSING"
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.t = start
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds


class FakeReporter:
    """MockTransport that routes by endpoint and search_text and records every request."""

    def __init__(self, status_for: dict[str, int] | None = None) -> None:
        self.requests: list[dict[str, Any]] = []
        self.status_for = status_for or {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.read() or b"{}")
        self.requests.append({"url": str(request.url), "headers": dict(request.headers), "body": body})
        headers = {"content-type": "application/json; charset=utf-8"}
        path = request.url.path
        if path == "/v2/projects/search":
            query = body["criteria"]["advanced_text_search"]["search_text"]
            status = self.status_for.get(query, 200)
            if status == 400:
                return httpx.Response(400, content=fixture_bytes("error_400.json"), headers=headers)
            if status >= 500:
                return httpx.Response(status, content=b"upstream unavailable", headers={"content-type": "text/plain"})
            if query == EMPTY_QUERY:
                return httpx.Response(200, content=fixture_bytes("projects_search_empty.json"), headers=headers)
            if query == DUP_QUERY:
                return httpx.Response(200, content=dup_page(), headers=headers)
            if query == DOSE_QUERY:
                return httpx.Response(200, content=dose_page(), headers=headers)
            return httpx.Response(200, content=fixture_bytes("projects_search_page.json"), headers=headers)
        if path == "/v2/publications/search":
            status = self.status_for.get("publications", 200)
            if status != 200:
                return httpx.Response(status, content=fixture_bytes("error_400.json"), headers=headers)
            return httpx.Response(200, content=fixture_bytes("publications_search.json"), headers=headers)
        return httpx.Response(404, content=b"not found")

    def projects_requests(self) -> list[dict[str, Any]]:
        return [r for r in self.requests if r["url"] == PROJECTS_URL]

    def publications_requests(self) -> list[dict[str, Any]]:
        return [r for r in self.requests if r["url"] == PUBLICATIONS_URL]


def make_http(tmp_path: Path, fake: FakeReporter, clock: FakeClock | None = None, *, offline: bool | None = None) -> CachedHttp:
    clock = clock or FakeClock()
    return CachedHttp(
        DiskCache(tmp_path / "cache", offline=offline),
        transport=httpx.MockTransport(fake),
        clock=clock.now,
        sleep=clock.sleep,
    )


def seeds(queries: list[str] | None = None, **overrides: Any) -> dict[str, Any]:
    base = config.load_seeds("reporter")
    if queries is not None:
        base["queries"] = queries
    base.update(overrides)
    return base


def mock_gene(symbol: str, n: int = 1) -> dict[str, Any]:
    return {
        "id": f"mock:gene:{n}",
        "type": "Gene",
        "label": symbol,
        "synonyms": [],
        "props": {"symbol": symbol},
        "source": "mock",
        "retrieved_at": MOCK_TS,
    }


def mock_disease(label: str, n: int = 1, synonyms: list[str] | None = None) -> dict[str, Any]:
    return {
        "id": f"mock:disease:{n}",
        "type": "Disease",
        "label": label,
        "synonyms": synonyms or [],
        "props": {},
        "source": "mock",
        "retrieved_at": MOCK_TS,
    }


def walk(obj: Any):
    """Every (key, value) pair in a nested JSON-like structure."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k, v
            yield from walk(v)
    elif isinstance(obj, list):
        for item in obj:
            yield from walk(item)


@pytest.fixture
def store(tmp_path: Path) -> Store:
    with Store(tmp_path / "scratch.sqlite") as s:
        yield s


@pytest.fixture(autouse=True)
def _no_env_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ATLAS_OFFLINE", raising=False)


# --------------------------------------------------------------------------------------
# seeds
# --------------------------------------------------------------------------------------


def test_seed_file_holds_names_only() -> None:
    s = config.load_seeds("reporter")
    assert s["queries"] == [
        "STAT1 gain of function",
        "chronic mucocutaneous candidiasis",
        "inborn errors of immunity",
        "JAK3 severe combined immunodeficiency",
        "STAT3 gain of function",
        "hyper IgE syndrome",
    ]
    assert s["search"]["operator"] == "and"
    assert s["search"]["search_field"] == "projecttitle,abstracttext,terms"
    assert s["search"]["fiscal_years_back"] == 6 and "fiscal_years" not in s["search"]
    assert s["search"]["limit"] == 50 and s["search"]["exclude_subprojects"] is True
    assert all(f[0].isupper() and "_" not in f for f in s["include_fields"])
    assert {"ApplId", "CoreProjectNum", "PrincipalInvestigators", "AbstractText", "ProjectDetailUrl"} <= set(s["include_fields"])
    # Every record field stored under props.projects[] must be requested, or it is null for every record.
    assert {"IsActive", "ActivityCode", "AwardType", "OpportunityNumber", "AwardAmount", "AgencyIcAdmin"} <= set(s["include_fields"])
    assert s["include_fields"] == list(reporter.DEFAULT_INCLUDE_FIELDS)
    # No identifier-shaped values anywhere in the seed file (hard rule 2).
    for _, value in walk(s):
        if isinstance(value, str):
            assert not value.isdigit() and not value.startswith(("R01", "R00", "U01", "PMID", "NCT"))


def test_fiscal_years_computed_from_today_not_hard_coded() -> None:
    assert fiscal_years_for(date(2026, 10, 3), 6) == [2022, 2023, 2024, 2025, 2026, 2027]  # FY starts 1 Oct
    assert fiscal_years_for(date(2026, 9, 30), 6) == [2021, 2022, 2023, 2024, 2025, 2026]
    assert fiscal_years_for(date(2026, 1, 15), 2) == [2025, 2026]
    assert fiscal_years_for(date(2026, 10, 3), 1) == [2027]


# --------------------------------------------------------------------------------------
# dose scrubber (hard rule 7)
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("received 5 mg daily", f"received {DOSE_PLACEHOLDER} daily"),
        ("0.5 mg/kg BID", f"{DOSE_PLACEHOLDER} {DOSE_PLACEHOLDER}"),
        ("400 mg/m2 every 2 weeks", f"{DOSE_PLACEHOLDER} {DOSE_PLACEHOLDER}"),
        ("10 IU/kg", DOSE_PLACEHOLDER),
        ("2.5 mcg", DOSE_PLACEHOLDER),
        ("50 µg", DOSE_PLACEHOLDER),
        ("200 mL infusion", f"{DOSE_PLACEHOLDER} infusion"),
        ("20 units", DOSE_PLACEHOLDER),
        ("in 25% of patients", f"in {DOSE_PLACEHOLDER} of patients"),
        ("10-20 mg", DOSE_PLACEHOLDER),
        ("given once daily", f"given {DOSE_PLACEHOLDER}"),
        ("given twice daily", f"given {DOSE_PLACEHOLDER}"),
        ("three times daily", DOSE_PLACEHOLDER),
        ("QD then TID", f"{DOSE_PLACEHOLDER} then {DOSE_PLACEHOLDER}"),
        ("q.d. dosing", f"{DOSE_PLACEHOLDER} dosing"),
        ("every 4 weeks", DOSE_PLACEHOLDER),
        ("every other day", DOSE_PLACEHOLDER),
        ("3 U/mL", DOSE_PLACEHOLDER),
        ("weight-based mg/kg dosing", f"weight-based {DOSE_PLACEHOLDER} dosing"),
        ("IU/kg", DOSE_PLACEHOLDER),
    ],
)
def test_scrubber_removes_dose_patterns(text: str, expected: str) -> None:
    assert scrub_doses(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "STAT1 gain of function",
        "JAK3 severe combined immunodeficiency",
        "R01AI127564 in FY2026",
        "10 patients followed for 3 years",
        "a bid to understand IL-17 and CD8 T cells",
        "Phase 2 Launch of the 5R01 renewal",
        "the STAT1 GOF cohort of 12 individuals",
        "Genomic regulation of immune response by a STAT1 gain of function mutation",
    ],
)
def test_scrubber_leaves_non_dose_text_alone(text: str) -> None:
    assert scrub_doses(text) == text


def test_scrubber_handles_none_and_the_fixture_titles() -> None:
    assert scrub_doses(None) == ""
    assert scrub_doses("") == ""
    for record in page_records():
        assert scrub_doses(record["project_title"]) == record["project_title"]


# --------------------------------------------------------------------------------------
# names and ids
# --------------------------------------------------------------------------------------


def test_person_name_collapses_whitespace_and_ignores_contact_pi_name() -> None:
    # Third fixture record: middle_name "" gives full_name "Gino  Cingolani" (double space) and
    # contact_pi_name "CINGOLANI, GINO " (trailing space).
    record = page_records()[2]
    pi = record["principal_investigators"][0]
    assert "  " in pi["full_name"] and record["contact_pi_name"].endswith(" ")
    label, full = person_name(pi)
    assert label == "Gino Cingolani" and full == "Gino Cingolani"
    assert "  " not in label and label != record["contact_pi_name"].strip()
    # A middle name is kept in the full name, not in the label.
    first = page_records()[0]["principal_investigators"][0]
    assert person_name(first) == ("Rachael Philips", "Rachael Laura Philips")
    # Whitespace dirt in the parts themselves is collapsed too; full_name is only a fallback.
    assert person_name({"first_name": " Ada  ", "middle_name": None, "last_name": "  Lovelace "}) == ("Ada Lovelace", "Ada Lovelace")
    assert person_name({"first_name": "", "last_name": "", "full_name": "Only  Fullname"}) == ("Only Fullname", "Only Fullname")


def test_investigator_id_format() -> None:
    pi = {"first_name": "Gino", "middle_name": "", "last_name": "Cingolani", "profile_id": 8029719}
    assert build_investigator_id(pi, "UNIVERSITY OF ALABAMA AT BIRMINGHAM") == (
        "person:cingolani-gino-university-of-alabama-at-birmingham-8029719"
    )
    no_profile = {"first_name": "Mock", "last_name": "Person"}
    assert build_investigator_id(no_profile, "Mock Org") == "person:person-mock-mock-org"
    assert build_investigator_id(no_profile, None) == "person:person-mock-unknown-organization"
    assert build_investigator_id({"first_name": "Zoë", "last_name": "D'Amico"}, "Ünïversity") == "person:d-amico-zoe-university"


# --------------------------------------------------------------------------------------
# request shape
# --------------------------------------------------------------------------------------


def test_posted_body_uses_snake_case_keys_and_pascal_case_include_fields(store: Store, tmp_path: Path) -> None:
    fake = FakeReporter()
    clock = FakeClock()
    http = make_http(tmp_path, fake, clock)
    load(store, seeds(["STAT1 gain of function"]), http=http, today=TODAY)

    projects = fake.projects_requests()
    assert len(projects) == 1
    body = projects[0]["body"]
    assert set(body) == {"criteria", "offset", "limit", "include_fields"}
    assert body["criteria"]["advanced_text_search"] == {
        "operator": "and",
        "search_field": "projecttitle,abstracttext,terms",
        "search_text": "STAT1 gain of function",
    }
    assert body["criteria"]["fiscal_years"] == [2022, 2023, 2024, 2025, 2026, 2027]
    assert body["criteria"]["exclude_subprojects"] is True
    assert body["offset"] == 0 and body["limit"] == 50
    assert body["include_fields"] == config.load_seeds("reporter")["include_fields"]
    assert all(f[0].isupper() and "_" not in f for f in body["include_fields"]), "include_fields values must be PascalCase"
    assert "ApplId" in body["include_fields"] and "appl_id" not in body["include_fields"]
    assert projects[0]["headers"]["user-agent"] == config.USER_AGENT
    assert projects[0]["headers"]["content-type"].startswith("application/json")

    pubs = fake.publications_requests()
    assert len(pubs) == 1
    assert pubs[0]["body"] == {
        "criteria": {"core_project_nums": sorted(r["core_project_num"] for r in page_records())},
        "offset": 0,
        "limit": 500,
    }
    assert pubs[0]["headers"]["user-agent"] == config.USER_AGENT


def test_rate_limiter_spaces_reporter_calls_one_second_apart(store: Store, tmp_path: Path) -> None:
    fake = FakeReporter()
    clock = FakeClock()
    http = make_http(tmp_path, fake, clock)
    assert http.limiter.limit_for(REPORTER_HOST) is None
    load(store, seeds(["STAT1 gain of function", EMPTY_QUERY]), http=http, today=TODAY)
    assert http.limiter.limit_for(REPORTER_HOST) == 1.0
    # 2 project searches + 1 publications batch = 3 calls -> 2 waits of >= 1 s (no retries happened).
    assert len(fake.requests) == 3
    assert len(clock.sleeps) == 2 and all(s >= 1.0 for s in clock.sleeps)


# --------------------------------------------------------------------------------------
# parsing, dedupe, investigators
# --------------------------------------------------------------------------------------


def test_parses_three_records_into_three_investigators(store: Store, tmp_path: Path) -> None:
    fake = FakeReporter()
    summary = load(store, seeds(["STAT1 gain of function"]), http=make_http(tmp_path, fake), today=TODAY)

    assert summary["totals"] == {"STAT1 gain of function": 289}  # meta.total, not len(results)
    assert summary["grants"] == 3 and summary["investigators"] == 3
    nodes = list(store.iter_nodes("Investigator"))
    assert len(nodes) == 3
    by_label = {n["label"]: n for n in nodes}
    assert set(by_label) == {"Rachael Philips", "Shaun Jackson", "Gino Cingolani"}
    for n in nodes:
        assert n["source"] == SOURCE_NAME
        assert n["retrieved_at"] and n["synonyms"] == []
        assert n["id"].startswith("person:")
        p = n["props"]
        assert p["match_confidence"] == "low"
        assert p["record_id"] == str(p["profile_id"])
        assert p["n_projects"] == 1 and len(p["projects"]) == 1
        assert p["found_by_queries"] == ["STAT1 gain of function"]
        assert p["role_label"] == 'PI on 1 NIH project matching "STAT1 gain of function"'
        assert "leader" not in p["role_label"].lower()
        assert p["is_contact_pi"] is True
        assert set(p["organization"]) == {"org_name", "org_city", "org_state", "org_country"}

    philips = by_label["Rachael Philips"]
    rec = page_records()[0]
    assert philips["id"] == f"person:philips-rachael-ohio-state-university-{rec['principal_investigators'][0]['profile_id']}"
    assert philips["props"]["name"] == {"full": "Rachael Laura Philips"}
    assert philips["props"]["organization"] == {
        "org_name": "OHIO STATE UNIVERSITY",
        "org_city": "Columbus",
        "org_state": "OH",
        "org_country": "UNITED STATES",
    }
    project = philips["props"]["projects"][0]
    assert project["appl_id"] == rec["appl_id"]
    assert project["core_project_num"] == rec["core_project_num"] == "R00AI177758"
    assert project["project_num"] == rec["project_num"]
    assert project["title"] == rec["project_title"]
    assert project["fiscal_year"] == rec["fiscal_year"] == 2026 and project["fiscal_years"] == [2026]
    assert project["award_amount"] == rec["award_amount"] == project["award_amount_total"]
    assert project["agency_ic_admin"] == {"code": "AI", "abbreviation": "NIAID", "name": rec["agency_ic_admin"]["name"]}
    assert project["start"] == rec["project_start_date"] and project["end"] == rec["project_end_date"]
    assert project["detail_url"] == rec["project_detail_url"] == philips["props"]["source_url"]
    # Rubric inputs come from the record as-is (the fixture record carries all 45 keys).
    assert project["is_active"] is rec["is_active"] is True
    assert project["activity_code"] == rec["activity_code"] == "R00"
    assert project["award_type"] == rec["award_type"] and project["award_type"]
    assert project["opportunity_number"] == rec["opportunity_number"] and project["opportunity_number"].startswith("PAR-")
    assert project["pmids"] == ["40446018"]  # from publications_search.json; strings
    assert philips["props"]["pmids"] == ["40446018"]
    # No abstract or terms blob is stored on the node; no contact fields exist anywhere.
    for key, _ in walk(philips["props"]):
        assert key not in {"abstract_text", "terms", "pref_terms", "email", "phone", "contact_pi_name"}


def test_dedupes_on_core_project_num_across_queries(store: Store, tmp_path: Path) -> None:
    fake = FakeReporter()
    summary = load(store, seeds(["STAT1 gain of function", "STAT3 gain of function"]), http=make_http(tmp_path, fake), today=TODAY)
    assert len(fake.projects_requests()) == 2
    assert summary["grants"] == 3 and summary["investigators"] == 3
    assert summary["records_seen"] == 3  # the same three appl_ids both times
    for n in store.iter_nodes("Investigator"):
        assert n["props"]["found_by_queries"] == ["STAT1 gain of function", "STAT3 gain of function"]
        assert n["props"]["n_projects"] == 1
        assert n["props"]["role_label"] == 'PI on 1 NIH project matching "STAT1 gain of function", "STAT3 gain of function"'


def test_duplicate_application_record_collapses_into_one_grant(store: Store, tmp_path: Path) -> None:
    fake = FakeReporter()
    summary = load(store, seeds([DUP_QUERY]), http=make_http(tmp_path, fake), today=TODAY)
    assert summary["totals"] == {DUP_QUERY: 290}
    assert summary["records_seen"] == 4 and summary["grants"] == 3 and summary["investigators"] == 3
    rec = page_records()[0]
    node = next(n for n in store.iter_nodes("Investigator") if n["label"] == "Rachael Philips")
    project = node["props"]["projects"][0]
    assert project["core_project_num"] == rec["core_project_num"]
    assert project["appl_id"] == rec["appl_id"], "the latest fiscal year is the record of reference"
    assert project["appl_ids"] == sorted([-1, rec["appl_id"]])
    assert project["fiscal_years"] == [rec["fiscal_year"] - 1, rec["fiscal_year"]]
    assert project["award_amount"] == rec["award_amount"]
    assert project["award_amount_total"] == rec["award_amount"] + 1000


def test_zero_hits_write_a_coverage_row_and_nothing_else(store: Store, tmp_path: Path) -> None:
    fake = FakeReporter()
    summary = load(store, seeds([EMPTY_QUERY]), http=make_http(tmp_path, fake), today=TODAY)
    assert summary["totals"] == {EMPTY_QUERY: 0}
    assert summary["grants"] == 0 and summary["investigators"] == 0 and summary["links"] == 0
    assert store.counts()["nodes"] == 0 and store.counts()["edges"] == 0
    assert fake.publications_requests() == []  # nothing to look up
    rows = store.coverage_rows()
    projects_rows = [r for r in rows if r["source"] == COVERAGE_PROJECTS_SOURCE]
    assert projects_rows == [
        {"entity_id": f"seed:reporter:{EMPTY_QUERY}", "source": COVERAGE_PROJECTS_SOURCE, "query": EMPTY_QUERY, "date": projects_rows[0]["date"], "hits": 0}
    ]
    assert len(projects_rows[0]["date"]) == 10
    assert [r for r in rows if r["source"] == COVERAGE_LINKS_SOURCE][0]["hits"] == 0
    assert store.sources_rows()[0]["name"] == SOURCE_NAME


# --------------------------------------------------------------------------------------
# linking
# --------------------------------------------------------------------------------------


def test_links_projects_to_pre_seeded_gene_and_disease_nodes(store: Store, tmp_path: Path) -> None:
    # "STAT1" appears in all three fixture abstracts/titles; "STAT3" in the third title;
    # "inborn errors of immunity" (25 chars) is in the second title; the synonym "STAT1 gain of
    # function" (22 chars) is in the first title, case-insensitively. Must never link: the short
    # synonym "SLE" (3 chars, present as "(SLE)" in the second title), the lower-case symbol
    # "mock", the 2-char symbol "AB", and the upper-case symbol "MUTATION" (gene symbols match
    # case-sensitively; the first title only has lower-case "mutation").
    store.upsert_nodes(
        [
            mock_gene("STAT1", 1),
            mock_gene("STAT3", 2),
            mock_gene("mock", 3),
            mock_gene("AB", 4),
            mock_gene("MUTATION", 5),
            mock_disease("Mock inborn errors of immunity", 1, synonyms=["inborn errors of immunity"]),
            mock_disease("Mock STAT1 GoF disease", 2, synonyms=["STAT1 GAIN OF FUNCTION"]),
            mock_disease("Mock SLE", 3, synonyms=["SLE"]),
        ]
    )
    assert "mutation" in page_records()[0]["project_title"] and "(SLE)" in page_records()[1]["project_title"]
    fake = FakeReporter()
    summary = load(store, seeds(["STAT1 gain of function"]), http=make_http(tmp_path, fake), today=TODAY)

    edges = list(store.iter_edges(relation=RELATION))
    assert summary["links"] == len(edges)
    by_label = {n["label"]: n["id"] for n in store.iter_nodes("Investigator")}
    pairs = {(e["source"], e["target"]) for e in edges}
    assert pairs == {
        (by_label["Rachael Philips"], "mock:gene:1"),
        (by_label["Rachael Philips"], "mock:disease:2"),
        (by_label["Shaun Jackson"], "mock:gene:1"),
        (by_label["Shaun Jackson"], "mock:disease:1"),
        (by_label["Gino Cingolani"], "mock:gene:1"),
        (by_label["Gino Cingolani"], "mock:gene:2"),
    }
    for e in edges:
        assert e["source_db"] == SOURCE_NAME and e["retrieved_at"]
        p = e["props"]
        assert p["evidence_class"] == "inferred"
        assert p["match"]["method"] == p["method"] and p["match"]["matched_text"]
        assert p["core_project_nums"] and p["fiscal_years"] == [2026] and p["agency"]
        assert isinstance(p["award_amount_total"], int) and p["award_amount_total"] > 0
        assert "confidence" not in p  # build.py computes confidence, not the loader
    gene_edge = next(e for e in edges if e["source"] == by_label["Rachael Philips"] and e["target"] == "mock:gene:1")
    assert gene_edge["props"]["matched_text"] == ["STAT1"]
    assert gene_edge["props"]["method"] == "gene_symbol_in_title_or_abstract"
    assert gene_edge["props"]["core_project_nums"] == ["R00AI177758"]
    assert gene_edge["props"]["award_amount_total"] == page_records()[0]["award_amount"]
    assert gene_edge["props"]["agency"] == ["NIAID"]
    assert gene_edge["props"]["pmids"] == ["40446018"]
    disease_edge = next(e for e in edges if e["target"] == "mock:disease:2")
    assert disease_edge["props"]["matched_text"] == ["STAT1 gain of function"]  # as it appears in the title
    assert disease_edge["props"]["method"] == "disease_name_in_title_or_abstract"
    iei_edge = next(e for e in edges if e["target"] == "mock:disease:1")
    assert iei_edge["props"]["matched_text"] == ["inborn errors of immunity"]

    # Every grant matched something -> no unlinked projects, link coverage hits == 3.
    for n in store.iter_nodes("Investigator"):
        assert n["props"]["unlinked_projects"] == []
        assert n["props"]["projects"][0]["matched"]["genes"] == sorted(
            t for s, t in pairs if s == n["id"] and t.startswith("mock:gene:")
        )
    link_rows = [r for r in store.coverage_rows() if r["source"] == COVERAGE_LINKS_SOURCE]
    assert link_rows == [{"entity_id": "seed:reporter:STAT1 gain of function", "source": COVERAGE_LINKS_SOURCE, "query": "STAT1 gain of function", "date": link_rows[0]["date"], "hits": 3}]


def test_unlinked_projects_are_recorded_not_guessed(store: Store, tmp_path: Path) -> None:
    store.upsert_nodes([mock_disease("Mock inborn errors of immunity", 1, synonyms=["inborn errors of immunity"])])
    fake = FakeReporter()
    summary = load(store, seeds(["STAT1 gain of function"]), http=make_http(tmp_path, fake), today=TODAY)
    assert summary["links"] == 1 and summary["linked_grants"] == 1 and summary["unlinked_grants"] == 2
    nodes = {n["label"]: n for n in store.iter_nodes("Investigator")}
    assert nodes["Shaun Jackson"]["props"]["unlinked_projects"] == []
    assert nodes["Rachael Philips"]["props"]["unlinked_projects"] == ["R00AI177758"]
    assert nodes["Gino Cingolani"]["props"]["unlinked_projects"] == ["R01AI191107"]
    link_rows = [r for r in store.coverage_rows() if r["source"] == COVERAGE_LINKS_SOURCE]
    assert len(link_rows) == 1 and link_rows[0]["hits"] == 1


def test_loader_works_in_an_empty_store_without_backbone_nodes(store: Store, tmp_path: Path) -> None:
    summary = load(store, seeds(["STAT1 gain of function"]), http=make_http(tmp_path, FakeReporter()), today=TODAY)
    assert summary["investigators"] == 3 and summary["links"] == 0
    assert store.counts()["nodes_by_type"] == {"Investigator": 3}
    assert store.counts()["edges"] == 0
    for n in store.iter_nodes("Investigator"):
        assert len(n["props"]["unlinked_projects"]) == 1


# --------------------------------------------------------------------------------------
# coverage, sources, publications
# --------------------------------------------------------------------------------------


def test_coverage_rows_cover_every_search_including_publications(store: Store, tmp_path: Path) -> None:
    fake = FakeReporter()
    queries = ["STAT1 gain of function", EMPTY_QUERY]
    load(store, seeds(queries), http=make_http(tmp_path, fake), today=TODAY)
    rows = store.coverage_rows()
    by_source: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_source.setdefault(r["source"], []).append(r)
    assert set(by_source) == {COVERAGE_PROJECTS_SOURCE, COVERAGE_LINKS_SOURCE, COVERAGE_PUBLICATIONS_SOURCE}

    projects = {r["query"]: r for r in by_source[COVERAGE_PROJECTS_SOURCE]}
    assert set(projects) == set(queries)
    assert projects["STAT1 gain of function"]["hits"] == 289 and projects[EMPTY_QUERY]["hits"] == 0
    for q, r in projects.items():
        assert r["entity_id"] == f"seed:reporter:{q}"
        assert len(r["date"]) == 10 and date.fromisoformat(r["date"])  # the cache entry's retrieved date

    pubs = by_source[COVERAGE_PUBLICATIONS_SOURCE]
    assert len(pubs) == 1
    assert pubs[0]["entity_id"] == "seed:reporter:publications:batch:1"
    assert pubs[0]["query"] == ",".join(sorted(r["core_project_num"] for r in page_records()))
    assert pubs[0]["hits"] == 124  # meta.total of the trimmed fixture, not len(results)

    links = {r["query"]: r["hits"] for r in by_source[COVERAGE_LINKS_SOURCE]}
    assert links == {"STAT1 gain of function": 0, EMPTY_QUERY: 0}  # empty store: nothing to link to


def test_sources_row(store: Store, tmp_path: Path) -> None:
    load(store, seeds(["STAT1 gain of function"]), http=make_http(tmp_path, FakeReporter()), today=TODAY)
    [row] = store.sources_rows()
    assert row["name"] == SOURCE_NAME
    assert row["license"].startswith("Public domain (U.S. Government work)")
    assert "UNVERIFIED" in row["license"]
    assert row["url"] == "https://api.reporter.nih.gov"
    assert row["version"] == row["retrieved_at"][:10] and len(row["version"]) == 10
    notes = row["notes"]
    assert notes["fiscal_years"] == [2022, 2023, 2024, 2025, 2026, 2027]
    assert notes["totals"] == {"STAT1 gain of function": 289}
    assert notes["grants"] == 3 and notes["errors"] == []
    assert notes["include_fields"] == config.load_seeds("reporter")["include_fields"]


def test_publications_batches_of_at_most_fifty(store: Store, tmp_path: Path) -> None:
    fake = FakeReporter()
    # 3 grants with batch_size 2 -> 2 publications calls.
    s = seeds(["STAT1 gain of function"])
    s["publications"] = {"batch_size": 2, "limit": 500}
    load(store, s, http=make_http(tmp_path, fake), today=TODAY)
    pubs = fake.publications_requests()
    assert [len(p["body"]["criteria"]["core_project_nums"]) for p in pubs] == [2, 1]
    assert all(len(p["body"]["criteria"]["core_project_nums"]) <= 50 for p in pubs)
    pub_rows = [r for r in store.coverage_rows() if r["source"] == COVERAGE_PUBLICATIONS_SOURCE]
    assert [r["entity_id"] for r in pub_rows] == ["seed:reporter:publications:batch:1", "seed:reporter:publications:batch:2"]
    # The default seed value is the hard cap.
    assert config.load_seeds("reporter")["publications"]["batch_size"] <= 50


def test_pmids_are_strings_joined_per_core_project_num(store: Store, tmp_path: Path) -> None:
    load(store, seeds(["STAT1 gain of function"]), http=make_http(tmp_path, FakeReporter()), today=TODAY)
    expected: dict[str, list[str]] = {}
    for row in fixture_json("publications_search.json")["results"]:
        expected.setdefault(row["coreproject"], []).append(str(row["pmid"]))
    seen: dict[str, list[str]] = {}
    for n in store.iter_nodes("Investigator"):
        for p in n["props"]["projects"]:
            seen[p["core_project_num"]] = p["pmids"]
            assert all(isinstance(x, str) and x.isdigit() for x in p["pmids"])
    # Only one fixture grant (R00AI177758) is among the publications fixture's three grants.
    assert seen["R00AI177758"] == expected["R00AI177758"] == ["40446018"]
    assert seen["R01AR073938"] == [] and seen["R01AI191107"] == []


# --------------------------------------------------------------------------------------
# caching and offline behaviour (hard rule 6)
# --------------------------------------------------------------------------------------


def test_nothing_is_fetched_twice_and_cached_runs_reproduce_dates(store: Store, tmp_path: Path) -> None:
    fake = FakeReporter()
    http = make_http(tmp_path, fake)
    first = load(store, seeds(["STAT1 gain of function", EMPTY_QUERY]), http=http, today=TODAY)
    n_calls = len(fake.requests)
    assert n_calls == 3
    nodes_before = {n["id"]: n for n in store.iter_nodes()}
    edges_before = list(store.iter_edges())
    coverage_before = store.coverage_rows()

    second = load(store, seeds(["STAT1 gain of function", EMPTY_QUERY]), http=http, today=TODAY)
    assert len(fake.requests) == n_calls, "a second run must be served entirely from the cache"
    assert {n["id"]: n for n in store.iter_nodes()} == nodes_before
    assert list(store.iter_edges()) == edges_before
    assert store.coverage_rows() == coverage_before
    assert second["retrieved_at"] == first["retrieved_at"]
    assert store.counts()["nodes"] == 3  # idempotent upserts


def test_offline_flag_raises_on_a_cache_miss(store: Store, tmp_path: Path) -> None:
    fake = FakeReporter()
    with pytest.raises(CacheMissOffline):
        load(store, seeds(["STAT1 gain of function"]), http=make_http(tmp_path, fake), offline=True, today=TODAY)
    assert fake.requests == [], "offline mode must never touch the network"
    assert store.counts()["nodes"] == 0


def test_atlas_offline_env_raises_on_a_cache_miss_and_serves_hits(store: Store, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeReporter()
    http = make_http(tmp_path, fake)  # DiskCache(offline=None) follows ATLAS_OFFLINE at call time
    monkeypatch.setenv("ATLAS_OFFLINE", "1")
    with pytest.raises(CacheMissOffline):
        load(store, seeds(["STAT1 gain of function"]), http=http, today=TODAY)
    assert fake.requests == []
    monkeypatch.delenv("ATLAS_OFFLINE")
    load(store, seeds(["STAT1 gain of function"]), http=http, today=TODAY)
    assert len(fake.requests) == 2
    monkeypatch.setenv("ATLAS_OFFLINE", "1")
    summary = load(store, seeds(["STAT1 gain of function"]), http=http, today=TODAY)  # all hits now
    assert len(fake.requests) == 2 and summary["grants"] == 3


def test_offline_flag_is_restored_on_an_injected_client(store: Store, tmp_path: Path) -> None:
    fake = FakeReporter()
    http = make_http(tmp_path, fake, offline=False)
    load(store, seeds(["STAT1 gain of function"]), http=http, today=TODAY)
    load(store, seeds(["STAT1 gain of function"]), http=http, offline=True, today=TODAY)  # cache hits
    assert http.cache.offline is False


# --------------------------------------------------------------------------------------
# errors
# --------------------------------------------------------------------------------------


def test_error_message_handles_both_400_shapes() -> None:
    from pipeline.cache import CachedResponse

    def resp(name: str) -> CachedResponse:
        return CachedResponse(status=400, text=fixture_bytes(name).decode("utf-8"), url=PROJECTS_URL, headers={}, from_cache=False, retrieved_at=MOCK_TS, key="k")

    assert error_message(resp("error_400.json")) == "System doesn't support limit value greater than 500. Please reduce your limit value."
    binding = error_message(resp("error_400_model_binding.json"))
    assert binding.startswith('Error converting value "28139313"') and "&quot;" not in binding and "'criteria.publications_search.pm_id'" in binding


def test_client_error_records_coverage_then_raises(store: Store, tmp_path: Path) -> None:
    fake = FakeReporter(status_for={"STAT1 gain of function": 400})
    with pytest.raises(ReporterApiError, match="limit value greater than 500"):
        load(store, seeds(["STAT1 gain of function"]), http=make_http(tmp_path, fake), today=TODAY)
    [row] = store.coverage_rows()
    assert row["source"] == COVERAGE_PROJECTS_SOURCE and row["hits"] == 0


def test_server_error_is_a_coverage_gap_not_a_crash(store: Store, tmp_path: Path) -> None:
    fake = FakeReporter(status_for={EMPTY_QUERY: 503})
    summary = load(store, seeds(["STAT1 gain of function", EMPTY_QUERY]), http=make_http(tmp_path, fake), today=TODAY)
    assert summary["totals"] == {"STAT1 gain of function": 289, EMPTY_QUERY: 0}
    assert summary["errors"] == [{"query": EMPTY_QUERY, "status": 503, "message": "upstream unavailable"}]
    assert summary["grants"] == 3
    rows = {(r["source"], r["query"]): r["hits"] for r in store.coverage_rows()}
    assert rows[(COVERAGE_PROJECTS_SOURCE, EMPTY_QUERY)] == 0
    assert store.sources_rows()[0]["notes"]["errors"] == summary["errors"]


def test_no_dosing_or_contact_data_reaches_the_store(store: Store, tmp_path: Path) -> None:
    store.upsert_nodes([mock_gene("STAT1", 1)])
    load(store, seeds(["STAT1 gain of function"]), http=make_http(tmp_path, FakeReporter()), today=TODAY)
    forbidden_keys = {"email", "phone", "contact_pi_name", "abstract_text", "terms", "pref_terms", "confidence"}
    for n in store.iter_nodes("Investigator"):
        for key, value in walk(n["props"]):
            assert key not in forbidden_keys
            if isinstance(value, str):
                assert not reporter.DOSE_PATTERN.search(value), f"unscrubbed text in {key!r}: {value!r}"
    for e in store.iter_edges():
        for key, value in walk(e["props"]):
            assert key not in forbidden_keys
            if isinstance(value, str):
                assert not reporter.DOSE_PATTERN.search(value)


def test_dosing_text_in_every_stored_field_is_scrubbed(store: Store, tmp_path: Path) -> None:
    store.upsert_nodes([mock_gene("STAT1", 1)])
    load(store, seeds([DOSE_QUERY]), http=make_http(tmp_path, FakeReporter()), today=TODAY)
    nodes = {n["label"]: n for n in store.iter_nodes("Investigator")}
    philips = nodes["Rachael Philips"]
    project = philips["props"]["projects"][0]
    rec = page_records()[0]
    assert project["title"] == f"{rec['project_title']} treated with {DOSE_PLACEHOLDER} daily and {DOSE_PLACEHOLDER} {DOSE_PLACEHOLDER}"
    assert philips["props"]["organization"]["org_name"] == f"MOCK INSTITUTE {DOSE_PLACEHOLDER} WING"
    assert philips["props"]["organization"]["org_city"] == f"Mock City {DOSE_PLACEHOLDER}"
    assert philips["props"]["title"] == f"PROFESSOR OF {DOSE_PLACEHOLDER} DOSING"
    # The id slug is built from the scrubbed organisation too, so no dose reaches any column.
    assert philips["id"] == f"person:philips-rachael-mock-institute-dose-removed-wing-{rec['principal_investigators'][0]['profile_id']}"
    for n in store.iter_nodes():
        for text in (n["id"], n["label"], *n["synonyms"], *(v for _, v in walk(n["props"]) if isinstance(v, str))):
            assert not reporter.DOSE_PATTERN.search(text), f"unscrubbed text on {n['id']}: {text!r}"
    edges = list(store.iter_edges(relation=RELATION))
    assert edges, "the STAT1 symbol still links after scrubbing"
    for e in edges:
        for _, value in walk(e["props"]):
            if isinstance(value, str):
                assert not reporter.DOSE_PATTERN.search(value)


def test_seeds_without_queries_are_rejected(store: Store, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="queries"):
        load(store, seeds([]), http=make_http(tmp_path, FakeReporter()), today=TODAY)
    with pytest.raises(ValueError, match="PascalCase"):
        load(store, seeds(["x"], include_fields=["appl_id"]), http=make_http(tmp_path, FakeReporter()), today=TODAY)
