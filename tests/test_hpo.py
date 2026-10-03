"""pipeline/sources/hpo.py: download bookkeeping, hpoa parsing, ontology closure, information content.

Fully offline: the "network" is an ``httpx.MockTransport`` serving a 16-line synthetic
phenotype.hpoa and a tiny OBO Graphs hp.json written into ``tmp_path``. Ids are ``MOCKHP:`` terms
and ``OMIM:mock*`` / ``ORPHA:mock*`` diseases, so nothing here is a real identifier. The one test
that touches the real files is marked ``network`` and skipped unless ``ATLAS_NETWORK_TESTS=1``.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path

import httpx
import pytest

from pipeline.sources import hpo
from pipeline.store import Store

# --------------------------------------------------------------------------------------
# synthetic sources
# --------------------------------------------------------------------------------------

OBO = "http://purl.obolibrary.org/obo/"
HPOA_VERSION = "2000-01-02"
HP_JSON_VERSION = "2000-01-01"
HP_JSON_RELEASE_URL = f"http://mock.example/obo/hp/releases/{HP_JSON_VERSION}/hp.json"

ROOT, PHENO, ORGAN, FEAT_A, FEAT_B, OTHER = (
    "MOCKHP:0000001",
    "MOCKHP:0000002",
    "MOCKHP:0000010",
    "MOCKHP:0000011",
    "MOCKHP:0000012",
    "MOCKHP:0000020",
)
INHERITANCE, FREQ, FREQ_EXCLUDED, FREQ_FREQUENT, OBSOLETE = (
    "MOCKHP:0000070",
    "MOCKHP:0000080",
    "MOCKHP:0000090",
    "MOCKHP:0000091",
    "MOCKHP:0000099",
)


def _iri(curie: str) -> str:
    return OBO + curie.replace(":", "_")


def _node(curie: str, label: str, **meta: object) -> dict:
    node: dict = {"id": _iri(curie), "lbl": label, "type": "CLASS"}
    if meta:
        node["meta"] = meta
    return node


def _edge(child: str, parent: str) -> dict:
    return {"sub": _iri(child), "pred": "is_a", "obj": _iri(parent)}


def hp_json_document() -> dict:
    """Mock HP ontology: a phenotype branch, an inheritance term, a frequency branch holding the
    "Excluded" term, and one obsolete term replaced by FEAT_B."""
    nodes = [
        _node(ROOT, "Mock all"),
        _node(PHENO, hpo.PHENOTYPIC_ABNORMALITY_LABEL),  # the real root label: IC is defined on its subtree
        _node(ORGAN, "Mock organ abnormality"),
        _node(FEAT_A, "Mock organ feature A"),
        _node(FEAT_B, "Mock organ feature B"),
        _node(OTHER, "Mock other abnormality"),
        _node(INHERITANCE, "Mock inheritance"),
        _node(FREQ, "Mock frequency"),
        _node(FREQ_EXCLUDED, hpo.EXCLUDED_FREQUENCY_LABEL),
        _node(FREQ_FREQUENT, "Mock frequent"),
        _node(
            OBSOLETE,
            "obsolete Mock organ feature",
            deprecated=True,
            basicPropertyValues=[{"pred": OBO + hpo.REPLACED_BY_PREDICATE, "val": FEAT_B}],
        ),
        # A property node and an untyped relation declaration must be ignored.
        {"id": OBO + "hp#layperson", "lbl": "layperson", "type": "PROPERTY"},
        {"id": OBO + "MOCKRO_0000001", "lbl": "mock has part"},
    ]
    edges = [
        _edge(PHENO, ROOT),
        _edge(ORGAN, PHENO),
        _edge(FEAT_A, ORGAN),
        _edge(FEAT_B, ORGAN),
        _edge(OTHER, PHENO),
        _edge(INHERITANCE, ROOT),
        _edge(FREQ, ROOT),
        _edge(FREQ_EXCLUDED, FREQ),
        _edge(FREQ_FREQUENT, FREQ),
        # a non-is_a edge must be ignored
        {"sub": _iri(FEAT_A), "pred": OBO + "RO_0002314", "obj": _iri(OTHER)},
    ]
    meta = {
        "version": HP_JSON_RELEASE_URL,
        "basicPropertyValues": [{"pred": "http://www.w3.org/2002/07/owl#versionInfo", "val": HP_JSON_VERSION}],
    }
    return {"graphs": [{"id": OBO + "hp.owl", "meta": meta, "nodes": nodes, "edges": edges}]}


def _row(disease: str, term: str, *, qualifier: str = "", evidence: str = "TAS", frequency: str = "", aspect: str = "P") -> str:
    name = f"Mock disease {disease.split(':')[1]}"
    return "\t".join([disease, name, qualifier, term, f"MOCKREF:{disease.split(':')[1]}", evidence, "", frequency, "", "", aspect, "MOCK:curator[2000-01-01]"])


def hpoa_text() -> str:
    """16 lines: 4 '#' header lines, the column header and 11 annotation rows."""
    lines = [
        '#description: "Mock HPO annotations"',
        f"#version: {HPOA_VERSION}",
        "#tracker: https://mock.example/tracker",
        f"#hpo-version: {HP_JSON_RELEASE_URL}",
        "\t".join(hpo.HPOA_COLUMNS),
        _row("OMIM:mockA", FEAT_A, evidence="PCS", frequency="3/4"),
        _row("OMIM:mockA", FEAT_B, frequency=FREQ_FREQUENT),  # HP frequency term
        _row("OMIM:mockA", INHERITANCE, aspect="I"),  # not a phenotypic abnormality
        _row("ORPHA:mockB", FEAT_A, frequency="25%"),  # percentage frequency, ORPHA: id
        _row("ORPHA:mockB", FEAT_B, qualifier="NOT"),  # NOT qualifier
        _row("ORPHA:mockC", FEAT_B),
        _row("DECIPHER:mockD", ORGAN, evidence="IEA"),  # direct annotation to a parent term
        _row("OMIM:mockE", FEAT_A, evidence="PCS", frequency="0/5"),  # looked for and absent
        _row("OMIM:mockE", FEAT_B, evidence="PCS", frequency=FREQ_EXCLUDED),  # "Excluded" term
        _row("OMIM:mockE", FEAT_B, aspect="C"),  # clinical course
        _row("OMIM:mockF", OBSOLETE, evidence="PCS"),  # obsolete term -> FEAT_B
    ]
    return "\n".join(lines) + "\n"


# Expected direct annotation sets after the filters (ORPHA: spelled Orphanet:).
EXPECTED_DIRECT = {
    FEAT_A: {"OMIM:mockA", "Orphanet:mockB"},
    FEAT_B: {"OMIM:mockA", "Orphanet:mockC", "OMIM:mockF"},
    ORGAN: {"DECIPHER:mockD"},
}
N_TOTAL = 5


@pytest.fixture
def fixture_files(tmp_path: Path) -> tuple[Path, Path]:
    hpoa = tmp_path / "fixture" / hpo.HPOA_FILENAME
    hp_json = tmp_path / "fixture" / hpo.HP_JSON_FILENAME
    hpoa.parent.mkdir()
    hpoa.write_text(hpoa_text(), encoding="utf-8")
    hp_json.write_text(json.dumps(hp_json_document()), encoding="utf-8")
    return hpoa, hp_json


@pytest.fixture
def ontology(fixture_files: tuple[Path, Path]) -> hpo.Ontology:
    return hpo.Ontology.load(fixture_files[1])


class MockServer:
    """MockTransport serving the two fixture files; records every request."""

    def __init__(self, hpoa_bytes: bytes, hp_json_bytes: bytes) -> None:
        self.hpoa_bytes = hpoa_bytes
        self.hp_json_bytes = hp_json_bytes
        self.requests: list[httpx.Request] = []
        self.failures_left = 0
        self.truncate_hpoa = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.failures_left > 0:
            self.failures_left -= 1
            return httpx.Response(503, text="mock outage")
        url = str(request.url)
        if url == hpo.HPOA_URL:
            body = self.hpoa_bytes
            declared = len(body) + 7 if self.truncate_hpoa else len(body)
            return httpx.Response(200, content=body, headers={"content-length": str(declared), "last-modified": "Mock date"})
        if url == HP_JSON_RELEASE_URL:
            return httpx.Response(200, content=self.hp_json_bytes, headers={"content-length": str(len(self.hp_json_bytes))})
        return httpx.Response(404, text=f"mock: no such file {url}")

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))


@pytest.fixture
def server() -> MockServer:
    return MockServer(hpoa_text().encode("utf-8"), json.dumps(hp_json_document()).encode("utf-8"))


@pytest.fixture
def raw_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv("ATLAS_OFFLINE", raising=False)
    return tmp_path / "raw"


# --------------------------------------------------------------------------------------
# identifiers
# --------------------------------------------------------------------------------------


def test_iri_to_curie() -> None:
    assert hpo.iri_to_curie(_iri(FEAT_A)) == FEAT_A
    assert hpo.iri_to_curie(FEAT_A) == FEAT_A
    assert hpo.iri_to_curie("MOCKHP_0000011") == FEAT_A
    # Not normalisable: returned as given rather than guessed.
    assert hpo.iri_to_curie("http://www.w3.org/2002/07/owl#Thing") == "http://www.w3.org/2002/07/owl#Thing"


def test_normalize_disease_id_spells_orphanet_like_mondo() -> None:
    assert hpo.normalize_disease_id("ORPHA:mock1") == "Orphanet:mock1"
    assert hpo.normalize_disease_id("OMIM:mock1") == "OMIM:mock1"
    assert hpo.normalize_disease_id("DECIPHER:mock1") == "DECIPHER:mock1"
    assert hpo.normalize_disease_id(" Orphanet:mock1 ") == "Orphanet:mock1"


# --------------------------------------------------------------------------------------
# ontology
# --------------------------------------------------------------------------------------


def test_ontology_parses_obographs(ontology: hpo.Ontology) -> None:
    assert ontology.version == HP_JSON_VERSION
    assert ontology.label(FEAT_A) == "Mock organ feature A"
    assert OBO + "hp#layperson" not in ontology  # property nodes are skipped
    assert "MOCKRO:0000001" not in ontology  # untyped relation declarations too
    assert ontology.parents[FEAT_A] == {ORGAN}  # the non-is_a edge is ignored
    assert ontology.children[ORGAN] == {FEAT_A, FEAT_B}
    assert ontology.deprecated == {OBSOLETE}
    assert ontology.resolve(OBSOLETE) == FEAT_B
    assert ontology.resolve(FEAT_B) == FEAT_B
    assert ontology.terms_with_label(hpo.EXCLUDED_FREQUENCY_LABEL) == {FREQ_EXCLUDED}
    assert ontology.active_terms == ontology.terms - {OBSOLETE}
    assert len(ontology) == 11


def test_ancestors_and_descendants(ontology: hpo.Ontology) -> None:
    assert ontology.ancestors(FEAT_A) == {FEAT_A, ORGAN, PHENO, ROOT}
    assert ontology.ancestors(FEAT_A, include_self=False) == {ORGAN, PHENO, ROOT}
    assert ontology.ancestors(ROOT) == {ROOT}
    assert ontology.ancestors(ROOT, include_self=False) == frozenset()
    assert ontology.ancestors("MOCKHP:unknown") == {"MOCKHP:unknown"}
    assert ontology.descendants(ORGAN) == {ORGAN, FEAT_A, FEAT_B}
    assert ontology.descendants(PHENO, include_self=False) == {ORGAN, FEAT_A, FEAT_B, OTHER}
    assert ontology.descendants(FEAT_A) == {FEAT_A}
    # Memoised results are consistent when a cached parent closure is reused.
    assert ontology.ancestors(FEAT_B) == {FEAT_B, ORGAN, PHENO, ROOT}
    assert ontology.descendants(ROOT) == ontology.terms - {OBSOLETE}


def test_ontology_tolerates_cycles_and_self_loops() -> None:
    o = hpo.Ontology({"mock:a": {"mock:b", "mock:a"}, "mock:b": {"mock:c"}, "mock:c": {"mock:a"}})
    assert o.ancestors("mock:a", include_self=False) == {"mock:b", "mock:c"}
    assert o.descendants("mock:a", include_self=False) == {"mock:b", "mock:c"}


def test_load_ontology_is_cached_per_path(fixture_files: tuple[Path, Path]) -> None:
    first = hpo.load_ontology(fixture_files[1])
    assert hpo.load_ontology(fixture_files[1]) is first


# --------------------------------------------------------------------------------------
# phenotype.hpoa
# --------------------------------------------------------------------------------------


def test_parse_hpoa_header(fixture_files: tuple[Path, Path]) -> None:
    header = hpo.parse_hpoa_header(fixture_files[0])
    assert header.version == HPOA_VERSION
    assert header.hpo_version == HP_JSON_RELEASE_URL
    assert header.hp_json_url == HP_JSON_RELEASE_URL
    assert len(header.lines) == 4 and all(line.startswith("#") for line in header.lines)


def test_parse_hpoa_header_without_version(tmp_path: Path) -> None:
    path = tmp_path / "bare.hpoa"
    path.write_text("#tracker: mock\n" + "\t".join(hpo.HPOA_COLUMNS) + "\n", encoding="utf-8")
    header = hpo.parse_hpoa_header(path)
    assert header.version == "" and header.hp_json_url == ""


def test_read_annotations_applies_the_filters(fixture_files: tuple[Path, Path], ontology: hpo.Ontology) -> None:
    annotations = hpo.read_annotations(fixture_files[0], ontology)
    assert annotations.direct == EXPECTED_DIRECT
    assert annotations.diseases == {"OMIM:mockA", "Orphanet:mockB", "Orphanet:mockC", "DECIPHER:mockD", "OMIM:mockF"}
    stats = annotations.stats
    assert stats["rows"] == 11
    assert stats["aspect_P_rows"] == 9
    assert stats["qualifier_NOT_rows"] == 1
    assert stats["zero_frequency_rows"] == 2  # "0/5" and the Excluded frequency term
    assert stats["excluded_frequency_terms"] == [FREQ_EXCLUDED]
    assert stats["kept_rows"] == 6
    assert stats["kept_rows_by_evidence"] == {"IEA": 1, "PCS": 2, "TAS": 3}
    assert stats["obsolete_terms_remapped"] == 1
    assert stats["distinct_diseases"] == N_TOTAL
    assert stats["distinct_terms"] == 3


def test_read_annotations_without_ontology_keeps_terms_as_written(fixture_files: tuple[Path, Path]) -> None:
    annotations = hpo.read_annotations(fixture_files[0])
    # No replacement map and no "Excluded" term: the obsolete term and the Excluded-frequency row stay.
    assert OBSOLETE in annotations.direct
    assert annotations.direct[FEAT_B] == {"OMIM:mockA", "Orphanet:mockC", "OMIM:mockE"}
    assert annotations.stats["zero_frequency_rows"] == 1
    assert annotations.stats["excluded_frequency_terms"] == []


def test_read_annotations_rejects_a_foreign_header(tmp_path: Path) -> None:
    path = tmp_path / "wrong.hpoa"
    path.write_text("#version: 2000-01-01\na\tb\tc\n1\t2\t3\n", encoding="utf-8")
    with pytest.raises(ValueError, match="expected columns"):
        hpo.read_annotations(path)


# --------------------------------------------------------------------------------------
# information content
# --------------------------------------------------------------------------------------


def test_compute_ic_values_and_propagation(ontology: hpo.Ontology) -> None:
    ic = hpo.compute_ic(EXPECTED_DIRECT, ontology)
    assert all(n_total == N_TOTAL for _, _, n_total in ic.values())
    # Direct counts.
    assert ic[FEAT_A][1] == 2 and math.isclose(ic[FEAT_A][0], -math.log(2 / 5))
    assert ic[FEAT_B][1] == 3 and math.isclose(ic[FEAT_B][0], -math.log(3 / 5))
    # The parent's disease set is the union over its children plus its own direct annotation.
    assert ic[ORGAN][1] == 5 and ic[ORGAN][0] == 0.0
    assert ic[PHENO] == (0.0, 5, 5) and ic[ROOT] == (0.0, 5, 5)
    # Never annotated: maximally specific, flagged with n_diseases 0.
    assert ic[OTHER] == (math.log(N_TOTAL), 0, N_TOTAL)
    # Outside the "Phenotypic abnormality" subtree (inheritance, frequency terms): NO value, never
    # the unannotated maximum (the most common HPO terms must not look the most specific).
    assert INHERITANCE not in ic and FREQ not in ic and FREQ_EXCLUDED not in ic and FREQ_FREQUENT not in ic
    # An obsolete term's annotations were remapped to its replacement; the term itself carries the
    # replacement's values (a backbone built on an older HPO release may still cite the old id).
    assert ic[OBSOLETE] == ic[FEAT_B]
    assert set(ic) == {ROOT, PHENO, ORGAN, FEAT_A, FEAT_B, OTHER, OBSOLETE}
    assert hpo.phenotypic_domain(ontology) == {PHENO, ORGAN, FEAT_A, FEAT_B, OTHER}


def test_compute_ic_covers_every_active_term_when_the_root_label_is_missing() -> None:
    ontology = hpo.Ontology({"mock:b": {"mock:a"}, "mock:c": {"mock:a"}}, {"mock:a": "Mock root", "mock:b": "Mock b", "mock:c": "Mock c"})
    assert hpo.phenotypic_domain(ontology) == frozenset()
    ic = hpo.compute_ic({"mock:b": {"OMIM:mock1"}}, ontology)
    assert set(ic) == {"mock:a", "mock:b", "mock:c"} and ic["mock:c"] == (0.0, 0, 1)


def test_compute_ic_is_monotone_along_is_a(ontology: hpo.Ontology) -> None:
    ic = hpo.compute_ic(EXPECTED_DIRECT, ontology)
    for child, parents in ontology.parents.items():
        if child not in ic:
            continue
        for parent in parents:
            assert ic[parent][0] <= ic[child][0] + 1e-12, (parent, child)
            assert ic[parent][1] >= ic[child][1], (parent, child)


def test_compute_ic_includes_annotated_terms_missing_from_the_ontology(ontology: hpo.Ontology) -> None:
    direct = {**EXPECTED_DIRECT, "MOCKHP:9999999": {"OMIM:mockA"}}
    ic = hpo.compute_ic(direct, ontology)
    assert ic["MOCKHP:9999999"] == (-math.log(1 / 5), 1, 5)


def test_compute_ic_needs_at_least_one_disease(ontology: hpo.Ontology) -> None:
    with pytest.raises(ValueError):
        hpo.compute_ic({}, ontology)


# --------------------------------------------------------------------------------------
# download bookkeeping
# --------------------------------------------------------------------------------------


def test_fetch_files_streams_once_into_a_version_directory(raw_dir: Path, server: MockServer) -> None:
    with server.client() as client:
        files = hpo.fetch_files(raw_dir, client=client)
        assert [str(r.url) for r in server.requests] == [hpo.HPOA_URL, HP_JSON_RELEASE_URL]  # hp.json pinned by the hpoa header
        assert files.version == HPOA_VERSION
        assert files.directory == raw_dir / "hpo" / HPOA_VERSION
        assert files.hpoa.read_bytes() == server.hpoa_bytes
        assert files.hp_json.read_bytes() == server.hp_json_bytes
        assert files.hp_json_version == HP_JSON_VERSION
        assert not (raw_dir / "hpo" / hpo.INCOMING_SUBDIR).exists()

        manifest = json.loads((files.directory / hpo.MANIFEST_FILENAME).read_text())
        hpoa_rec = manifest["files"][hpo.HPOA_FILENAME]
        assert hpoa_rec["sha256"] == hpo.sha256_file(files.hpoa)
        assert hpoa_rec["bytes"] == len(server.hpoa_bytes) == hpoa_rec["content_length"]
        assert hpoa_rec["url"] == hpo.HPOA_URL and hpoa_rec["last_modified"] == "Mock date"
        assert manifest["retrieved_at"] == hpoa_rec["retrieved_at"]
        assert manifest["hpo_version_iri"] == HP_JSON_RELEASE_URL
        assert manifest["files"][hpo.HP_JSON_FILENAME]["url"] == HP_JSON_RELEASE_URL
        # retrieved_at is an ISO timestamp the contract accepts.
        from datetime import datetime

        datetime.fromisoformat(files.retrieved_at)

        # Second call: nothing is fetched twice.
        again = hpo.fetch_files(raw_dir, client=client)
        assert again == files
        assert len(server.requests) == 2
        # Offline works from the files alone.
        assert hpo.fetch_files(raw_dir, offline=True, client=client) == files
        assert len(server.requests) == 2


def test_fetch_files_offline_without_files_raises(raw_dir: Path, server: MockServer, monkeypatch: pytest.MonkeyPatch) -> None:
    with server.client() as client:
        with pytest.raises(FileNotFoundError):
            hpo.fetch_files(raw_dir, offline=True, client=client)
        monkeypatch.setenv("ATLAS_OFFLINE", "1")
        with pytest.raises(FileNotFoundError):
            hpo.fetch_files(raw_dir, client=client)
    assert server.requests == []


def test_fetch_files_redownloads_when_the_checksum_no_longer_matches(raw_dir: Path, server: MockServer) -> None:
    with server.client() as client:
        files = hpo.fetch_files(raw_dir, client=client)
        files.hpoa.write_bytes(server.hpoa_bytes + b"corruption\n")
        assert hpo.find_local(raw_dir / "hpo") is None
        with pytest.raises(FileNotFoundError):
            hpo.fetch_files(raw_dir, offline=True, client=client)
        fresh = hpo.fetch_files(raw_dir, client=client)
        assert fresh.verify() and fresh.hpoa.read_bytes() == server.hpoa_bytes
        assert len(server.requests) == 4


def test_find_local_prefers_the_newest_complete_version(raw_dir: Path, server: MockServer) -> None:
    with server.client() as client:
        old = hpo.fetch_files(raw_dir, client=client)
    # A newer directory without a manifest is incomplete and ignored; a newer complete one wins.
    (raw_dir / "hpo" / "2000-02-01").mkdir()
    assert hpo.find_local(raw_dir / "hpo") == old
    newer_dir = raw_dir / "hpo" / "2000-03-01"
    newer_dir.mkdir()
    for name in (hpo.HPOA_FILENAME, hpo.HP_JSON_FILENAME):
        newer_dir.joinpath(name).write_bytes(old.directory.joinpath(name).read_bytes())
    manifest = dict(old.manifest, version="2000-03-01")
    newer_dir.joinpath(hpo.MANIFEST_FILENAME).write_text(json.dumps(manifest))
    assert hpo.find_local(raw_dir / "hpo").version == "2000-03-01"


def test_download_retries_transient_failures(raw_dir: Path, server: MockServer) -> None:
    server.failures_left = 2
    sleeps: list[float] = []
    with server.client() as client:
        record = hpo._download(client, hpo.HPOA_URL, raw_dir / "x" / "a.hpoa", sleep=sleeps.append)
    assert record["status"] == 200 and record["bytes"] == len(server.hpoa_bytes)
    assert sleeps == [0.5, 1.0]
    assert len(server.requests) == 3


def test_download_gives_up_after_the_last_attempt_and_leaves_no_file(raw_dir: Path, server: MockServer) -> None:
    server.failures_left = 10
    dest = raw_dir / "x" / "a.hpoa"
    with server.client() as client:
        with pytest.raises(hpo._Retryable):
            hpo._download(client, hpo.HPOA_URL, dest, sleep=lambda _s: None)
    assert not dest.exists() and list(dest.parent.iterdir()) == []


def test_download_rejects_a_short_body(raw_dir: Path, server: MockServer) -> None:
    server.truncate_hpoa = True
    dest = raw_dir / "x" / "a.hpoa"
    with server.client() as client:
        with pytest.raises(hpo._Retryable, match="short body"):
            hpo._download(client, hpo.HPOA_URL, dest, attempts=1)
    assert not dest.exists()


def test_download_does_not_retry_a_404(raw_dir: Path, server: MockServer) -> None:
    with server.client() as client:
        with pytest.raises(httpx.HTTPStatusError):
            hpo._download(client, "https://mock.example/missing", raw_dir / "x" / "m", sleep=lambda _s: None)
    assert len(server.requests) == 1


def test_fetch_files_rejects_an_hpoa_without_a_version(raw_dir: Path, server: MockServer) -> None:
    server.hpoa_bytes = hpoa_text().replace(f"#version: {HPOA_VERSION}\n", "").encode("utf-8")
    with server.client() as client:
        with pytest.raises(ValueError, match="#version"):
            hpo.fetch_files(raw_dir, client=client)
    assert hpo.find_local(raw_dir / "hpo") is None


# --------------------------------------------------------------------------------------
# load() into the store
# --------------------------------------------------------------------------------------


def test_load_writes_ic_source_and_coverage(raw_dir: Path, server: MockServer, tmp_path: Path) -> None:
    with server.client() as client, Store(tmp_path / "evidence.sqlite") as store:
        summary = hpo.load(store, client=client, raw_dir=raw_dir)
        # Idempotent: a second load reads the same files and rewrites the same rows.
        assert hpo.load(store, client=client, raw_dir=raw_dir) == summary
        assert len(server.requests) == 2

        assert summary["version"] == HPOA_VERSION and summary["hp_json_version"] == HP_JSON_VERSION
        # 5 active terms under "Phenotypic abnormality" + the root (an ancestor of annotated terms) + 1 obsolete with a replacement
        assert summary["n_total"] == N_TOTAL and summary["n_terms"] == 7

        ic = store.hpo_ic()
        assert len(ic) == 7
        assert INHERITANCE not in ic and FREQ_EXCLUDED not in ic
        assert ic[OBSOLETE] == ic[FEAT_B]
        assert ic[FEAT_A] == {"ic": pytest.approx(-math.log(2 / 5)), "n_diseases": 2, "n_total": N_TOTAL}
        assert ic[OTHER] == {"ic": pytest.approx(math.log(N_TOTAL)), "n_diseases": 0, "n_total": N_TOTAL}
        assert ic[ROOT]["ic"] == 0.0

        (source,) = store.sources_rows()
        assert source["name"] == hpo.SOURCE_NAME
        assert source["version"] == HPOA_VERSION
        assert source["license"] == hpo.LICENSE
        assert source["url"] == hpo.HPOA_URL
        assert source["retrieved_at"] == summary["retrieved_at"]
        notes = source["notes"]
        assert notes["hp_json_version"] == HP_JSON_VERSION
        assert notes["files"][hpo.HPOA_FILENAME]["sha256"] == hpo.sha256_file(raw_dir / "hpo" / HPOA_VERSION / hpo.HPOA_FILENAME)
        assert "OMIM" in notes["iea_caveat"]
        assert HPOA_VERSION in notes["attribution"] and HP_JSON_VERSION in notes["attribution"]
        assert notes["information_content"]["n_total"] == N_TOTAL
        assert notes["information_content"]["kept_rows"] == 6
        assert notes["information_content"]["terms_with_ic"].startswith(f"active terms under {hpo.PHENOTYPIC_ABNORMALITY_LABEL!r} (5 terms)")

        rows = store.coverage_rows()
        assert {r["entity_id"] for r in rows} == {hpo.COVERAGE_ENTITY}
        by_query = {r["query"]: r for r in rows}
        p_rows = by_query["aspect == 'P' annotation rows"]
        assert p_rows["source"] == f"{hpo.SOURCE_NAME} {hpo.HPOA_FILENAME}" and p_rows["hits"] == 9
        assert by_query["distinct diseases with >= 1 kept annotation (N_total)"]["hits"] == N_TOTAL
        assert by_query["terms with information content"]["hits"] == 7
        assert all(r["date"] == summary["retrieved_at"] for r in rows)


def test_load_replaces_the_whole_ic_table(raw_dir: Path, server: MockServer, tmp_path: Path) -> None:
    with server.client() as client, Store(tmp_path / "evidence.sqlite") as store:
        store.set_hpo_ic({"mock:stale": (1.0, 1, 1)})
        hpo.load(store, client=client, raw_dir=raw_dir)
        ic = store.hpo_ic()
    assert "mock:stale" not in ic and len(ic) == 7


def test_load_offline_uses_existing_files(raw_dir: Path, server: MockServer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with server.client() as client:
        hpo.fetch_files(raw_dir, client=client)
    monkeypatch.setenv("ATLAS_OFFLINE", "1")
    with Store(tmp_path / "evidence.sqlite") as store:
        summary = hpo.load(store, offline=True, raw_dir=raw_dir)
    assert summary["n_total"] == N_TOTAL
    assert len(server.requests) == 2


def test_module_level_ancestors_uses_the_local_download(raw_dir: Path, server: MockServer) -> None:
    with server.client() as client:
        hpo.fetch_files(raw_dir, client=client)
    assert hpo.ancestors(FEAT_A, raw_dir=raw_dir) == {FEAT_A, ORGAN, PHENO, ROOT}
    assert hpo.ancestors("MOCKHP_0000011", include_self=False, raw_dir=raw_dir) == {ORGAN, PHENO, ROOT}
    assert hpo.get_ontology(raw_dir, offline=True) is hpo.get_ontology(raw_dir, offline=True)


# --------------------------------------------------------------------------------------
# real files (opt-in)
# --------------------------------------------------------------------------------------


@pytest.mark.network
@pytest.mark.skipif(os.environ.get("ATLAS_NETWORK_TESTS") != "1", reason="set ATLAS_NETWORK_TESTS=1 to run against the real HPO files")
@pytest.mark.timeout(600)
def test_real_hpo_load_into_a_temporary_store(tmp_path: Path) -> None:
    """Uses the shared RAW_DIR download (fetching it once if absent); writes only to a tmp store."""
    with Store(tmp_path / "evidence.sqlite") as store:
        summary = hpo.load(store)
        ic = store.hpo_ic()
    assert summary["n_total"] > 1000
    assert len(ic) > 10000
    root_like = min(ic.values(), key=lambda v: v["ic"])
    assert root_like["ic"] == 0.0 and root_like["n_diseases"] == summary["n_total"]
