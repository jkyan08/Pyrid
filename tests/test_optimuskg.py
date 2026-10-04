"""pipeline/sources/optimuskg.py: the OptimusKG backbone loader, fully offline.

The fixtures are tiny Parquet files written with Polars into ``tmp_path`` using the real column
names and nested types observed in docs/optimuskg-spike.md section 3. Every id is a FORMAT
exemplar with an obviously synthetic body (``MONDO_9000001``, ``ENSG99000000001``,
``CHEMBL9000001``, ...) and every label starts with "Mock", so nothing here can be mistaken for
a resolved record (README hard rule 2). The real download is never run; the one test that would
touch the network is marked ``network`` and skipped unless ``ATLAS_NETWORK_TESTS=1``.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import httpx
import polars as pl
import pytest

from pipeline.cache import CachedHttp, DiskCache
from pipeline.sources import optimuskg
from pipeline.sources.optimuskg import (
    DISEASE_DISEASE,
    DISEASE_GENE,
    DISEASE_NODES,
    DISEASE_PHENOTYPE,
    DRUG_DISEASE,
    DRUG_GENE,
    DRUG_NODES,
    GENE_NODES,
    NEEDED_FILES,
    PATHWAY_GENE,
    PATHWAY_NODES,
    PHENOTYPE_NODES,
    PHENOTYPE_PHENOTYPE,
    PROCESS_GENE,
    PROCESS_NODES,
    REL_ASSOCIATED_WITH,
    REL_CONTRAINDICATED_FOR,
    REL_HAS_PHENOTYPE,
    REL_IN_PATHWAY,
    REL_INDICATED_FOR,
    REL_INHIBITS,
    REL_IS_A,
    REL_TARGETS,
    SOURCE_DB,
    SOURCE_NAME,
    ChecksumMismatch,
    DatasetFile,
    DatasetInfo,
    GeneResolutionError,
    LocalFile,
    OfflineUnavailable,
    download_file,
    ensure_files,
    load,
    md5_of,
    parse_dataset,
    resolve_files,
    slice_into_store,
)
from pipeline.store import Store

MOCK_TS = "2026-10-03T00:00:00+00:00"
MOCK_VERSION = "9.9"

# --------------------------------------------------------------------------------------
# Parquet schemas (real column names and nested types; spike section 3)
# --------------------------------------------------------------------------------------

STR, F64, I64, I16, BOOL = pl.String, pl.Float64, pl.Int64, pl.Int16, pl.Boolean
LS = pl.List(pl.String)
SOURCES = pl.Struct({"direct": LS, "indirect": LS})
LABELED = pl.List(pl.Struct({"label": STR, "source": STR}))
ASSERTIONS = pl.List(pl.Struct({"source": STR, "relation": STR}))
ONTOLOGY = pl.Struct({"description": STR, "title": STR, "license": STR, "version": STR})
TEXT_FIELDS = {
    "name": STR,
    "description": STR,
    "code": STR,
    "xrefs": LS,
    "exact_synonyms": LS,
    "related_synonyms": LS,
    "narrow_synonyms": LS,
    "broad_synonyms": LS,
}


def node_schema(props: dict[str, Any]) -> dict[str, Any]:
    return {"id": STR, "label": STR, "properties": pl.Struct(props)}


def edge_schema(props: dict[str, Any]) -> dict[str, Any]:
    return {"from": STR, "to": STR, "label": STR, "relation": STR, "undirected": BOOL, "properties": pl.Struct(props)}


GENE_SCHEMA = node_schema(
    {
        "sources": SOURCES,
        "symbol": STR,
        "biotype": STR,
        "name": STR,
        "symbol_synonyms": LABELED,
        "obsolete_symbols": LABELED,
        "xrefs": pl.List(pl.Struct({"id": STR, "source": STR})),
    }
)
DISEASE_SCHEMA = node_schema({"sources": SOURCES, **TEXT_FIELDS})
PHENOTYPE_SCHEMA = node_schema({"sources": SOURCES, **TEXT_FIELDS, "type": STR, "ontology": ONTOLOGY})
PATHWAY_SCHEMA = node_schema({"sources": SOURCES, "name": STR, "species": STR})
PROCESS_SCHEMA = node_schema(
    {"sources": SOURCES, "name": STR, "definition": STR, "xrefs": LS, "synonyms": LS, "ontology": ONTOLOGY}
)
DRUG_SCHEMA = node_schema(
    {
        "name": STR,
        "type": STR,
        "synonyms": LS,
        "is_approved": BOOL,
        "trade_names": LS,
        "sources": SOURCES,
        "source_ids": LS,
        "mol_file_base64": STR,
        "mol_image_base64": STR,
        "mrdef": STR,
    }
)
DISEASE_GENE_SCHEMA = edge_schema(
    {
        "evidence_score": F64,
        "evidence_count": I64,
        "disease_specificity_index": F64,
        "disease_pleiotropy_index": F64,
        "evidence_index": F64,
        "disgenet_score": F64,
        "year_initial": STR,
        "year_final": STR,
        "number_of_pmids": I16,
        "number_of_snps": I16,
        "sources": SOURCES,
    }
)
DISEASE_PHENOTYPE_SCHEMA = edge_schema(
    {
        "aspect": LS,
        "bio_curation": LS,
        "evidence_type": LS,
        "frequency": LS,
        "modifiers": LS,
        "onset": LS,
        "qualifier_not": BOOL,
        "references": LS,
        "sexes": LS,
        "sources": SOURCES,
        "relation_assertions": ASSERTIONS,
        "relation_conflict": BOOL,
    }
)
HIERARCHY_SCHEMA = edge_schema({"sources": SOURCES})
PROCESS_GENE_SCHEMA = edge_schema({"sources": SOURCES, "evidence": LS, "gene_product": LS, "eco_ids": LS})
DRUG_GENE_SCHEMA = edge_schema(
    {
        "source_ids": LS,
        "source_urls": LS,
        "mechanisms_of_action": LS,
        "sources": SOURCES,
        "relation_assertions": ASSERTIONS,
        "relation_conflict": BOOL,
    }
)
DRUG_DISEASE_SCHEMA = edge_schema(
    {
        "sources": SOURCES,
        "structure_id": STR,
        "drug_disease_id": STR,
        "reference_ids": LS,
        "highest_clinical_trial_phase": F64,
        "relation_assertions": ASSERTIONS,
        "relation_conflict": BOOL,
    }
)

# --------------------------------------------------------------------------------------
# The synthetic world
# --------------------------------------------------------------------------------------

OT = {"direct": ["OPEN_TARGETS"], "indirect": []}
OT_DISGENET = {"direct": ["DISGENET", "OPEN_TARGETS"], "indirect": ["CTD"]}
DISGENET_ONLY = {"direct": ["DISGENET"], "indirect": []}
HPO = {"direct": ["HPO"], "indirect": []}

# genes (bare Ensembl ids; one NCBIGene: id with a colon; two nodes sharing a symbol)
G_A, G_B, G_C, G_D = "ENSG99000000001", "ENSG99000000002", "ENSG99000000003", "ENSG99000000004"
G_E = "NCBIGene:999001"
G_F = "ENSG99000000007"
G_DUP1, G_DUP2 = "ENSG99000000005", "ENSG99000000006"
# diseases (underscore CURIEs of several prefixes; one PREFIX_alnum id)
D_A, D_B, D_C, D_D = "MONDO_9000001", "MONDO_9000002", "EFO_9000003", "Orphanet_9000004"
D_E, D_F, D_G, D_X = "MONDO_9000005", "MONDO_9000006", "OBA_VT9000007", "MONDO_9000010"
# phenotypes (P6 is a mode-of-inheritance term, annotated under aspect "I", not a phenotypic abnormality)
P1, P2, P3, P4, P5, P6 = "HP_9000001", "HP_9000002", "HP_9000003", "HP_9000004", "HP_9000005", "HP_9000006"
P_MEDDRA = "meddra:9000001"
# an HPO frequency term as the backbone spells one; a FORMAT exemplar, not a real term id
P_FREQ = "HP_9000040"
# pathways, drugs
PWY = "REACT:R-HSA-9000001"
GO = "GO_9000001"
DRUG_1, DRUG_2, DRUG_3, DRUG_4 = "CHEMBL9000001", "DRUGBANK:DB99001", "CHEMBL9000003", "CHEMBL9000004"

SEEDS: dict[str, Any] = {
    "genes": ["MOCKA", "MOCKB"],
    "disease_terms": ["mock synonym two", "MOCK A SYNDROME", "no such mock disease"],
    "drug_terms": ["mockazole", "no such mock drug"],
    "thresholds": {
        "disease_gene_min_score": 0.3,
        "keep_disgenet_direct": True,
        "max_pathway_members_to_expand": 0,
        "include_parent_levels": 1,
    },
}


def gene(gid: str, symbol: str, **over: Any) -> dict[str, Any]:
    props = {
        "sources": {"direct": ["OPEN_TARGETS", "BGEE"], "indirect": []},
        "symbol": symbol,
        "biotype": "protein_coding",
        "name": f"Mock gene {symbol}",
        "symbol_synonyms": [],
        "obsolete_symbols": [],
        "xrefs": [],
    }
    props.update(over)
    return {"id": gid, "label": "GEN", "properties": props}


def disease(did: str, name: str, **over: Any) -> dict[str, Any]:
    props: dict[str, Any] = {
        "sources": {"direct": ["OPEN_TARGETS", "DRUG_CENTRAL", "MONDO"], "indirect": []},
        "name": name,
        "description": f"{name} description.",
        "code": f"http://mock.invalid/{did}",
        "xrefs": None,
        "exact_synonyms": None,
        "related_synonyms": [],
        "narrow_synonyms": [],
        "broad_synonyms": None,
    }
    props.update(over)
    return {"id": did, "label": "DIS", "properties": props}


def phenotype(pid: str, name: str, **over: Any) -> dict[str, Any]:
    props: dict[str, Any] = {
        "sources": {"direct": ["OPEN_TARGETS", "HPO"], "indirect": []},
        "name": name,
        "description": None,
        "code": None,
        "xrefs": None,
        "exact_synonyms": [],
        "related_synonyms": None,
        "narrow_synonyms": None,
        "broad_synonyms": None,
        "type": "phenotype",
        "ontology": {"description": "Mock HPO", "title": "Mock Phenotype Ontology", "license": "mock", "version": "0"},
    }
    props.update(over)
    return {"id": pid, "label": "PHE", "properties": props}


def drug(did: str, name: str, **over: Any) -> dict[str, Any]:
    props: dict[str, Any] = {
        "name": name,
        "type": "Small molecule",
        "synonyms": [],
        "is_approved": True,
        "trade_names": [],
        "sources": {"direct": ["DRUG_BANK", "OPEN_TARGETS"], "indirect": []},
        "source_ids": [],
        "mol_file_base64": "QUFBQQ==",
        "mol_image_base64": "QUFBQQ==",
        "mrdef": "Mock pharmacology summary text.",
    }
    props.update(over)
    return {"id": did, "label": "DRG", "properties": props}


def edge(frm: str, to: str, label: str, relation: str, undirected: bool, props: dict[str, Any]) -> dict[str, Any]:
    return {"from": frm, "to": to, "label": label, "relation": relation, "undirected": undirected, "properties": props}


def dis_gen(frm: str, to: str, score: float, sources: dict[str, Any], pmids: int | None = None) -> dict[str, Any]:
    props = {
        "evidence_score": score,
        "evidence_count": 3,
        "disease_specificity_index": None,
        "disease_pleiotropy_index": None,
        "evidence_index": None,
        "disgenet_score": 0.5 if pmids is not None else None,
        "year_initial": "2011" if pmids is not None else None,
        "year_final": "2020" if pmids is not None else None,
        "number_of_pmids": pmids,
        "number_of_snps": None,
        "sources": sources,
    }
    return edge(frm, to, "DIS-GEN", "ASSOCIATED_WITH", True, props)


def dis_phe(frm: str, to: str, evidence: str, frequency: list[str], aspect: list[str] = ("P",)) -> dict[str, Any]:
    props = {
        "aspect": list(aspect),
        "bio_curation": ["HPO:mock[2020-01-01]"],
        "evidence_type": [evidence],
        "frequency": frequency,
        "modifiers": [],
        "onset": [],
        "qualifier_not": False,
        "references": ["mock:ref"],
        "sexes": [],
        "sources": {"direct": ["OPEN_TARGETS"], "indirect": ["HPO"]},
        "relation_assertions": [{"source": "OPEN_TARGETS", "relation": "PHENOTYPE_PRESENT"}],
        "relation_conflict": False,
    }
    return edge(frm, to, "DIS-PHE", "PHENOTYPE_PRESENT", True, props)


def parent(frm_parent: str, to_child: str, label: str, sources: dict[str, Any]) -> dict[str, Any]:
    """Backbone PARENT edge: ``from`` is the PARENT of ``to`` (spike section 3)."""
    return edge(frm_parent, to_child, label, "PARENT", False, {"sources": sources})


def drg_gen(frm: str, to: str, relation: str, moa: list[str] | None) -> dict[str, Any]:
    props = {
        "source_ids": None,
        "source_urls": None,
        "mechanisms_of_action": moa,
        "sources": {"direct": ["DRUG_BANK", "OPEN_TARGETS"], "indirect": ["FDA"]},
        "relation_assertions": [{"source": "OPEN_TARGETS", "relation": relation}],
        "relation_conflict": False,
    }
    return edge(frm, to, "DRG-GEN", relation, False, props)


def drg_dis(frm: str, to: str, relation: str, phase: float | None) -> dict[str, Any]:
    props = {
        "sources": {"direct": ["DRUG_CENTRAL", "OPEN_TARGETS"], "indirect": []},
        "structure_id": "1",
        "drug_disease_id": "1",
        "reference_ids": ["mock:ref"],
        "highest_clinical_trial_phase": phase,
        "relation_assertions": [{"source": "OPEN_TARGETS", "relation": relation}],
        "relation_conflict": False,
    }
    return edge(frm, to, "DRG-DIS", relation, True, props)


def world_frames() -> dict[str, tuple[list[dict[str, Any]], dict[str, Any]]]:
    """path -> (rows, schema) for every file the loader needs."""
    hp_only = {"sources": {"direct": ["OPEN_TARGETS"], "indirect": []}}
    return {
        GENE_NODES: (
            [
                gene(
                    G_A,
                    "MOCKA",
                    symbol_synonyms=[{"label": "MOCKA1", "source": "HGNC"}],
                    obsolete_symbols=[{"label": "OLDA", "source": "HGNC"}],
                    xrefs=[{"id": "1", "source": "HGNC"}],
                ),
                gene(G_B, "MOCKB"),
                gene(G_C, "MOCKC"),
                gene(G_D, "MOCKD"),
                gene(G_E, "MOCKE"),
                gene(G_F, "MOCKF", xrefs=None, symbol_synonyms=None),
                gene(G_DUP1, "DUPL"),
                gene(G_DUP2, "DUPL"),
            ],
            GENE_SCHEMA,
        ),
        DISEASE_NODES: (
            [
                disease(D_A, "Mock disease A", exact_synonyms=["Mock A syndrome"], xrefs=["mock:xref"]),
                disease(D_B, "Mock disease B"),
                disease(D_C, "Mock disease C"),
                disease(D_D, "Mock disease D", related_synonyms=["Mock Synonym Two", "Mock disease D alias"]),
                disease(D_E, "Mock parent E"),
                disease(D_F, "Mock grandparent F"),
                disease(D_G, "Mock trait G"),
                disease(D_X, "Mock unrelated X"),
            ],
            DISEASE_SCHEMA,
        ),
        PHENOTYPE_NODES: (
            [
                phenotype(P1, "Mock phenotype one", exact_synonyms=["Mock pheno 1"]),
                phenotype(P2, "Mock phenotype two"),
                phenotype(P3, "Mock phenotype three"),
                phenotype(P4, "Mock phenotype four"),
                phenotype(P5, "Mock phenotype five"),
                phenotype(P6, "Mock inheritance mode"),
                phenotype(P_MEDDRA, "Mock meddra term", type="PT", ontology=None),
            ],
            PHENOTYPE_SCHEMA,
        ),
        PATHWAY_NODES: (
            [
                {
                    "id": PWY,
                    "label": "PWY",
                    "properties": {"sources": {"direct": ["REACTOME", "OPEN_TARGETS"], "indirect": []}, "name": "Mock pathway", "species": "Homo sapiens"},
                }
            ],
            PATHWAY_SCHEMA,
        ),
        PROCESS_NODES: (
            [
                {
                    "id": GO,
                    "label": "BPO",
                    "properties": {
                        "sources": {"direct": ["GO"], "indirect": []},
                        "name": "Mock process",
                        "definition": "Mock process definition.",
                        "xrefs": [],
                        "synonyms": ["mock proc"],
                        "ontology": {"description": "Mock GO", "title": "Mock Gene Ontology", "license": "mock", "version": "0"},
                    },
                }
            ],
            PROCESS_SCHEMA,
        ),
        DRUG_NODES: (
            [
                drug(DRUG_1, "MOCKINIB", synonyms=["Mockinib"], trade_names=["Mockavi"]),
                drug(DRUG_2, "MOCKAMAB", type="Antibody"),
                drug(DRUG_3, "MOCKAZOLE"),
                drug(DRUG_4, "MOCKUMAB", type="Antibody"),  # indicated for A only; not a seed-gene drug
            ],
            DRUG_SCHEMA,
        ),
        DISEASE_GENE: (
            [
                dis_gen(D_A, G_A, 0.9, OT_DISGENET, pmids=6),  # seed gene, passes
                dis_gen(D_A, G_C, 0.5, OT),  # other gene, passes
                dis_gen(D_A, G_F, 0.4, OT),  # other gene, passes (second-ranked for the cap test)
                dis_gen(D_A, G_D, 0.05, OT),  # other gene, excluded
                dis_gen(D_B, G_A, 0.1, DISGENET_ONLY, pmids=1),  # low score, DISGENET direct -> included
                dis_gen(D_C, G_A, 0.1, OT),  # low score, OT only -> excluded
                dis_gen(D_E, G_B, 0.2, OT),  # parent E, seed gene below threshold -> edge kept
                dis_gen(D_E, G_E, 0.7, OT),  # parent E, other gene (colon id) passes
                dis_gen(D_F, G_A, 0.8, OT),  # grandparent F: passes, but its descendant A passes for the same gene
                dis_gen(D_X, G_D, 0.9999, OT),  # global max, disease not included
                dis_gen(D_X, G_C, 0.001, OT),  # global min
            ],
            DISEASE_GENE_SCHEMA,
        ),
        DISEASE_PHENOTYPE: (
            [
                dis_phe(D_A, P1, "PCS", ["3/4"]),
                dis_phe(D_A, P2, "IEA", []),
                dis_phe(D_B, P2, "TAS", [P_FREQ]),
                dis_phe(D_B, P6, "IEA", [], aspect=["I"]),  # mode of inheritance: not a has_phenotype edge
                dis_phe(D_C, P5, "PCS", []),  # C is not included
            ],
            DISEASE_PHENOTYPE_SCHEMA,
        ),
        PHENOTYPE_PHENOTYPE: (
            [
                parent(P3, P1, "PHE-PHE", HPO),
                parent(P4, P3, "PHE-PHE", HPO),
                parent(P4, P2, "PHE-PHE", HPO),
                parent(P4, P5, "PHE-PHE", HPO),  # P5 is not reachable from an annotation
            ],
            HIERARCHY_SCHEMA,
        ),
        DISEASE_DISEASE: (
            [
                parent(D_E, D_A, "DIS-DIS", hp_only["sources"]),
                parent(D_F, D_E, "DIS-DIS", hp_only["sources"]),
                parent(D_G, D_B, "DIS-DIS", hp_only["sources"]),
                parent("HP_9000099", D_A, "DIS-DIS", hp_only["sources"]),  # parent absent from the disease file
            ],
            HIERARCHY_SCHEMA,
        ),
        PATHWAY_GENE: (
            [
                edge(PWY, G_A, "PWY-GEN", "INTERACTS_WITH", True, {"sources": {"direct": ["OPEN_TARGETS"], "indirect": ["REACTOME"]}}),
                edge(PWY, G_C, "PWY-GEN", "INTERACTS_WITH", True, {"sources": {"direct": ["OPEN_TARGETS"], "indirect": ["REACTOME"]}}),
            ],
            HIERARCHY_SCHEMA,
        ),
        PROCESS_GENE: (
            [
                edge(
                    GO,
                    G_B,
                    "BPO-GEN",
                    "INTERACTS_WITH",
                    True,
                    {"sources": {"direct": ["OPEN_TARGETS"], "indirect": ["mock:ref"]}, "evidence": ["IDA"], "gene_product": ["MOCK1"], "eco_ids": ["ECO:0000314"]},
                ),
            ],
            PROCESS_GENE_SCHEMA,
        ),
        DRUG_GENE: (
            [
                drg_gen(DRUG_1, G_A, "INHIBITOR", ["Mock kinase inhibitor"]),
                drg_gen(DRUG_2, G_B, "TARGET", None),
                drg_gen(DRUG_1, G_C, "TARGET", None),  # not a seed gene
            ],
            DRUG_GENE_SCHEMA,
        ),
        DRUG_DISEASE: (
            [
                drg_dis(DRUG_1, D_A, "INDICATION", 3.0),
                drg_dis(DRUG_1, D_B, "CONTRAINDICATION", None),
                drg_dis(DRUG_4, D_A, "INDICATION", 1.0),  # capped away when max_drugs_per_disease is 0
                drg_dis(DRUG_2, D_C, "INDICATION", 2.0),  # C is not included
            ],
            DRUG_DISEASE_SCHEMA,
        ),
    }


def write_world(root: Path, frames: dict[str, tuple[list[dict[str, Any]], dict[str, Any]]] | None = None) -> tuple[DatasetInfo, dict[str, LocalFile]]:
    """Write the synthetic Parquet files under ``root`` and describe them like the downloader would."""
    frames = frames if frames is not None else world_frames()
    files: dict[str, LocalFile] = {}
    dataset_files: dict[str, DatasetFile] = {}
    for i, (path, (rows, schema)) in enumerate(frames.items(), start=1):
        local = root / path
        local.parent.mkdir(parents=True, exist_ok=True)
        pl.DataFrame(rows, schema=schema).write_parquet(local)
        md5 = md5_of(local)
        size = local.stat().st_size
        files[path] = LocalFile(path, local, md5, size, MOCK_TS)
        dataset_files[path] = DatasetFile(path, i, md5, size)
    info = DatasetInfo(
        doi="doi:mock",
        version=MOCK_VERSION,
        release_time=MOCK_TS,
        license_name="mock",
        license_uri="",
        persistent_url="",
        retrieved_at=MOCK_TS,
        files=dataset_files,
    )
    return info, files


@pytest.fixture
def store(tmp_path: Path) -> Store:
    with Store(tmp_path / "evidence.sqlite") as s:
        yield s


@pytest.fixture
def world(tmp_path: Path) -> tuple[DatasetInfo, dict[str, LocalFile]]:
    return write_world(tmp_path / "raw")


@pytest.fixture
def sliced(store: Store, world: tuple[DatasetInfo, dict[str, LocalFile]]) -> tuple[Store, dict[str, Any]]:
    info, files = world
    summary = slice_into_store(store, SEEDS, info, files)
    return store, summary


def edges_of(store: Store, relation: str) -> list[dict[str, Any]]:
    return list(store.iter_edges(relation=relation))


def coverage(store: Store, entity_id: str, source: str) -> list[dict[str, Any]]:
    return [r for r in store.coverage_rows() if r["entity_id"] == entity_id and r["source"] == source]


# --------------------------------------------------------------------------------------
# metadata parsing
# --------------------------------------------------------------------------------------


def dataverse_payload() -> dict[str, Any]:
    """The shape of ``/api/datasets/:persistentId/`` (spike section 2) with synthetic values."""

    def entry(directory: str | None, filename: str, file_id: int, md5: str, size: int) -> dict[str, Any]:
        e: dict[str, Any] = {
            "label": filename,
            "restricted": False,
            "version": 1,
            "dataFile": {
                "id": file_id,
                "persistentId": "",
                "filename": filename,
                "contentType": "application/octet-stream",
                "filesize": size,
                "md5": md5,
                "checksum": {"type": "MD5", "value": md5},
            },
        }
        if directory:
            e["directoryLabel"] = directory
            e["dataFile"]["directoryLabel"] = directory
        return e

    return {
        "status": "OK",
        "data": {
            "id": 1,
            "persistentUrl": "https://doi.invalid/mock",
            "latestVersion": {
                "datasetPersistentId": "doi:mock",
                "versionNumber": 9,
                "versionMinorNumber": 9,
                "versionState": "RELEASED",
                "releaseTime": MOCK_TS,
                "license": {"name": "mock license", "uri": "https://license.invalid/mock"},
                "files": [
                    entry("nodes", "mock.parquet", 11, "0" * 32, 10),
                    entry("edges", "mock_mock.parquet", 12, "1" * 32, 20),
                    entry(None, "unified.parquet", 13, "2" * 32, 30),
                ],
            },
        },
    }


def test_parse_dataset_reads_version_license_and_files_by_name() -> None:
    info = parse_dataset(dataverse_payload(), MOCK_TS)
    assert info.version == "9.9"
    assert info.license_name == "mock license"
    assert info.license_uri == "https://license.invalid/mock"
    assert info.doi == "doi:mock"
    assert info.persistent_url == "https://doi.invalid/mock"
    assert set(info.files) == {"nodes/mock.parquet", "edges/mock_mock.parquet", "unified.parquet"}
    f = info.files["nodes/mock.parquet"]
    assert (f.file_id, f.md5, f.size) == (11, "0" * 32, 10)
    # round-trips through the manifest representation
    assert DatasetInfo.from_json(json.loads(json.dumps(info.to_json()))) == info


# --------------------------------------------------------------------------------------
# download + md5
# --------------------------------------------------------------------------------------

PAYLOAD = b"PAR1 mock parquet bytes PAR1"
PAYLOAD_MD5 = hashlib.md5(PAYLOAD).hexdigest()


def mock_client(statuses: list[int] | None = None) -> tuple[httpx.Client, list[str]]:
    """A client whose Dataverse access endpoint answers 303 -> presigned URL -> bytes."""
    calls: list[str] = []
    pending = list(statuses or [])

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.host == "dataverse.harvard.edu":
            assert request.headers["user-agent"].startswith("pyrid-atlas/")
            return httpx.Response(303, headers={"Location": "https://mock-s3.invalid/key?X-Amz-Expires=3600"})
        if pending:
            status = pending.pop(0)
            if status != 200:
                return httpx.Response(status)
        return httpx.Response(200, content=PAYLOAD)

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True, headers={"User-Agent": "pyrid-atlas/0.1 (test)"})
    return client, calls


def test_download_file_follows_redirect_and_verifies_md5(tmp_path: Path) -> None:
    client, calls = mock_client()
    dest = tmp_path / "raw" / "nodes" / "mock.parquet"
    md5 = download_file(client, DatasetFile("nodes/mock.parquet", 11, PAYLOAD_MD5, len(PAYLOAD)), dest)
    assert md5 == PAYLOAD_MD5
    assert dest.read_bytes() == PAYLOAD
    assert not dest.with_name(dest.name + ".part").exists()
    assert calls[0].startswith("https://dataverse.harvard.edu/api/access/datafile/11")
    assert calls[1].startswith("https://mock-s3.invalid/")


def test_download_file_deletes_and_raises_on_md5_mismatch(tmp_path: Path) -> None:
    client, _ = mock_client()
    dest = tmp_path / "mock.parquet"
    with pytest.raises(ChecksumMismatch):
        download_file(client, DatasetFile("nodes/mock.parquet", 11, "f" * 32, len(PAYLOAD)), dest)
    assert not dest.exists()
    assert not dest.with_name(dest.name + ".part").exists()


def test_download_file_retries_server_errors(tmp_path: Path) -> None:
    client, calls = mock_client(statuses=[503, 200])
    dest = tmp_path / "mock.parquet"
    slept: list[float] = []
    md5 = download_file(client, DatasetFile("nodes/mock.parquet", 11, PAYLOAD_MD5, len(PAYLOAD)), dest, sleep=slept.append)
    assert md5 == PAYLOAD_MD5 and dest.read_bytes() == PAYLOAD
    assert slept == [1.0]
    assert sum(1 for c in calls if "mock-s3" in c) == 2


def test_ensure_files_reuses_matching_files_and_writes_manifest(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    present = raw / "optimuskg" / MOCK_VERSION / "nodes" / "present.parquet"
    present.parent.mkdir(parents=True)
    present.write_bytes(PAYLOAD)
    info = DatasetInfo(
        "doi:mock",
        MOCK_VERSION,
        MOCK_TS,
        "mock",
        "",
        "",
        MOCK_TS,
        {
            "nodes/present.parquet": DatasetFile("nodes/present.parquet", 1, PAYLOAD_MD5, len(PAYLOAD)),
            "nodes/missing.parquet": DatasetFile("nodes/missing.parquet", 11, PAYLOAD_MD5, len(PAYLOAD)),
        },
    )
    client, calls = mock_client()
    files = ensure_files(info, ["nodes/present.parquet", "nodes/missing.parquet"], raw_dir=raw, offline=False, client=client)
    assert set(files) == {"nodes/present.parquet", "nodes/missing.parquet"}
    assert all("datafile/1/" not in c and not c.endswith("datafile/1") for c in calls), "present file must not be fetched"
    assert any(c.endswith("datafile/11") for c in calls)
    manifest = json.loads((raw / "optimuskg" / MOCK_VERSION / "manifest.json").read_text())
    assert manifest["dataset"]["version"] == MOCK_VERSION
    assert manifest["files"]["nodes/missing.parquet"]["md5"] == PAYLOAD_MD5
    assert manifest["files"]["nodes/missing.parquet"]["retrieved_at_source"] == "download"
    # A present file the manifest does not know gets its mtime, flagged as such; never "now".
    from datetime import datetime, timezone

    mtime = datetime.fromtimestamp(present.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")
    assert manifest["files"]["nodes/present.parquet"] == {"md5": PAYLOAD_MD5, "size": len(PAYLOAD), "retrieved_at": mtime, "retrieved_at_source": "file mtime (not in manifest)"}
    assert files["nodes/present.parquet"].retrieved_at_source == "file mtime (not in manifest)"
    # second call: nothing fetched, retrieved_at (and where it came from) kept from the manifest
    client2, calls2 = mock_client()
    again = ensure_files(info, ["nodes/present.parquet", "nodes/missing.parquet"], raw_dir=raw, offline=False, client=client2)
    assert calls2 == []
    assert again["nodes/missing.parquet"].retrieved_at == files["nodes/missing.parquet"].retrieved_at
    assert again["nodes/missing.parquet"].retrieved_at_source == "download"
    assert again["nodes/present.parquet"] == files["nodes/present.parquet"]


def test_ensure_files_offline_without_files_raises_clear_error(tmp_path: Path) -> None:
    info = DatasetInfo(
        "doi:mock", MOCK_VERSION, MOCK_TS, "mock", "", "", MOCK_TS, {"nodes/missing.parquet": DatasetFile("nodes/missing.parquet", 11, PAYLOAD_MD5, 1)}
    )
    with pytest.raises(OfflineUnavailable, match="nodes/missing.parquet"):
        ensure_files(info, ["nodes/missing.parquet"], raw_dir=tmp_path, offline=True)


def test_ensure_files_rejects_unknown_file_name(tmp_path: Path) -> None:
    info = DatasetInfo("doi:mock", MOCK_VERSION, MOCK_TS, "mock", "", "", MOCK_TS, {})
    with pytest.raises(KeyError):
        ensure_files(info, ["nodes/not_in_dataset.parquet"], raw_dir=tmp_path, offline=False)


def test_resolve_files_offline_falls_back_to_manifest(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    info, _ = write_world(raw / "optimuskg" / MOCK_VERSION)
    optimuskg.write_manifest(raw / "optimuskg" / MOCK_VERSION, info, {})
    http = CachedHttp(cache=DiskCache(tmp_path / "cache", offline=True), transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    got_info, files = resolve_files(NEEDED_FILES, raw_dir=raw, offline=True, http=http)
    assert got_info.version == MOCK_VERSION
    assert set(files) == set(NEEDED_FILES)
    assert all(f.local.is_file() for f in files.values())


def test_resolve_files_offline_without_metadata_or_manifest_raises(tmp_path: Path) -> None:
    http = CachedHttp(cache=DiskCache(tmp_path / "cache", offline=True), transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    with pytest.raises(OfflineUnavailable, match="offline"):
        resolve_files(NEEDED_FILES, raw_dir=tmp_path / "raw", offline=True, http=http)


# --------------------------------------------------------------------------------------
# slice: genes
# --------------------------------------------------------------------------------------


def test_seed_symbol_resolves_to_exactly_one_gene_node(sliced: tuple[Store, dict[str, Any]]) -> None:
    store, summary = sliced
    node = store.get_node("ensembl:" + G_A)
    assert node is not None
    assert node["type"] == "Gene"
    assert node["label"] == "MOCKA"
    assert node["props"]["symbol"] == "MOCKA"
    assert node["props"]["backbone_id"] == G_A
    assert node["props"]["source_type"] == "GEN"
    assert node["props"]["xrefs"] == [{"id": "1", "source": "HGNC"}]
    assert node["synonyms"] == ["MOCKA1", "OLDA"]
    assert node["source"] == SOURCE_DB and node["retrieved_at"] == MOCK_TS
    assert summary["seed_resolution"]["genes"]["MOCKA"] == {"node_id": "ensembl:" + G_A, "backbone_id": G_A, "disease_gene_edges": 4}
    rows = coverage(store, "seed:gene:MOCKA", "OptimusKG gene")
    assert len(rows) == 1 and rows[0]["hits"] == 1 and rows[0]["query"] == "MOCKA"
    dg_rows = coverage(store, "ensembl:" + G_A, "OptimusKG disease_gene")
    assert len(dg_rows) == 1 and dg_rows[0]["hits"] == 4, "hits are counted before any threshold (A, B, C, F)"


def test_ambiguous_or_missing_seed_symbol_raises_after_recording_coverage(store: Store, world: tuple[DatasetInfo, dict[str, LocalFile]]) -> None:
    info, files = world
    seeds = dict(SEEDS, genes=["DUPL", "NOSUCHGENE"])
    with pytest.raises(GeneResolutionError) as exc:
        slice_into_store(store, seeds, info, files)
    assert "DUPL" in str(exc.value) and "NOSUCHGENE" in str(exc.value)
    assert coverage(store, "seed:gene:DUPL", "OptimusKG gene")[0]["hits"] == 2
    assert coverage(store, "seed:gene:NOSUCHGENE", "OptimusKG gene")[0]["hits"] == 0
    assert store.counts()["nodes"] == 0


# --------------------------------------------------------------------------------------
# slice: disease inclusion, threshold / DISGENET, terms, parents
# --------------------------------------------------------------------------------------


def test_disease_inclusion_threshold_disgenet_term_and_parent(sliced: tuple[Store, dict[str, Any]]) -> None:
    store, summary = sliced
    diseases = {n["props"]["backbone_id"] for n in store.iter_nodes("Disease")}
    assert diseases == {D_A, D_B, D_D, D_E, D_G}, "A (score), B (DISGENET), D (term), E and G (parents); not C, F, X"
    assert summary["diseases"] == {
        "passing_before_specificity": 3,  # A, B and the umbrella F
        "direct": 2,
        "term_matched": 2,
        "parents": 2,
        "included": 5,
    }
    assoc = {(e["source"], e["target"]): e for e in edges_of(store, REL_ASSOCIATED_WITH)}
    assert set(assoc) == {
        ("MONDO:9000001", "ensembl:" + G_A),
        ("MONDO:9000001", "ensembl:" + G_C),
        ("MONDO:9000001", "ensembl:" + G_F),
        ("MONDO:9000002", "ensembl:" + G_A),
        ("MONDO:9000005", "ensembl:" + G_B),  # seed gene below threshold: kept with its raw score
        ("MONDO:9000005", G_E),  # other gene with a colon id, passes
    }
    assert ("MONDO:9000001", "ensembl:" + G_D) not in assoc, "other gene below threshold is excluded"
    e = assoc[("MONDO:9000001", "ensembl:" + G_A)]
    assert e["props"]["evidence_score"] == 0.9
    assert e["props"]["number_of_pmids"] == 6
    assert e["props"]["sources"] == OT_DISGENET
    assert e["props"]["backbone_relation"] == "ASSOCIATED_WITH"
    assert e["props"]["backbone_label"] == "DIS-GEN"
    assert e["props"]["backbone_undirected"] is True
    assert e["source_db"] == SOURCE_DB
    genes = {n["label"] for n in store.iter_nodes("Gene")}
    assert genes == {"MOCKA", "MOCKB", "MOCKC", "MOCKE", "MOCKF"}
    f = store.get_node("ensembl:" + G_F)
    assert f is not None and f["synonyms"] == [] and f["props"]["xrefs"] is None, "null lists are tolerated"


def test_most_specific_drops_umbrella_terms_explained_by_a_descendant(store: Store, world: tuple[DatasetInfo, dict[str, LocalFile]]) -> None:
    """F (grandparent of A) passes for MOCKA, but A passes for MOCKA too, so F is a roll-up."""
    info, files = world
    assert store.get_node("MONDO:9000006") is None
    slice_into_store(store, SEEDS, info, files)
    assert store.get_node("MONDO:9000006") is None
    with Store(":memory:") as literal:
        seeds = dict(SEEDS, thresholds=dict(SEEDS["thresholds"], disease_gene_most_specific=False))
        summary = slice_into_store(literal, seeds, info, files)
        assert summary["diseases"]["direct"] == 3 and literal.get_node("MONDO:9000006") is not None
        assert summary["thresholds"]["disease_gene_most_specific"] is False


def test_most_specific_rule_is_per_gene() -> None:
    children = {"U": ["A"], "A": []}
    # U is explained by A for gene g1, but U is the only term for g2 -> kept
    assert optimuskg.most_specific([("U", "g1"), ("A", "g1"), ("U", "g2")], children) == {"A", "U"}
    assert optimuskg.most_specific([("U", "g1"), ("A", "g1")], children) == {"A"}
    assert optimuskg.most_specific([("U", "g1")], children) == {"U"}
    assert optimuskg.most_specific([], children) == set()


def test_other_genes_per_disease_cap(store: Store, world: tuple[DatasetInfo, dict[str, LocalFile]]) -> None:
    info, files = world
    seeds = dict(SEEDS, thresholds=dict(SEEDS["thresholds"], max_other_genes_per_disease=1))
    summary = slice_into_store(store, seeds, info, files)
    assoc = {(e["source"], e["target"]) for e in edges_of(store, REL_ASSOCIATED_WITH)}
    assert ("MONDO:9000001", "ensembl:" + G_C) in assoc, "the strongest other gene is kept"
    assert ("MONDO:9000001", "ensembl:" + G_F) not in assoc, "the second-ranked other gene is capped"
    assert ("MONDO:9000005", "ensembl:" + G_B) in assoc, "seed-gene edges are never capped"
    assert store.get_node("ensembl:" + G_F) is None
    assert summary["disease_gene_rows"] == {"before_cap": 6, "after_cap": 5}
    with Store(":memory:") as none:
        seeds0 = dict(SEEDS, thresholds=dict(SEEDS["thresholds"], max_other_genes_per_disease=0))
        slice_into_store(none, seeds0, info, files)
        assert {n["label"] for n in none.iter_nodes("Gene")} == {"MOCKA", "MOCKB"}
        assert {(e["source"], e["target"]) for e in none.iter_edges(relation=REL_ASSOCIATED_WITH)} == {
            ("MONDO:9000001", "ensembl:" + G_A),
            ("MONDO:9000002", "ensembl:" + G_A),
            ("MONDO:9000005", "ensembl:" + G_B),
        }


def test_cap_other_genes_ranking() -> None:
    def row(frm: str, to: str, score: float | None, count: int | None = None) -> dict[str, Any]:
        return {"from": frm, "to": to, "properties": {"evidence_score": score, "evidence_count": count}}

    rows = [row("d", "seed", 0.01), row("d", "g1", 0.5, 1), row("d", "g2", 0.5, 9), row("d", "g3", None), row("e", "g4", 0.3)]
    kept = optimuskg.cap_other_genes(rows, {"seed"}, 1)
    assert [(r["from"], r["to"]) for r in kept] == [("d", "seed"), ("d", "g2"), ("e", "g4")]
    assert optimuskg.cap_other_genes(rows, {"seed"}, None) == rows
    assert [r["to"] for r in optimuskg.cap_other_genes(rows, {"seed"}, 0)] == ["seed"]


def test_loader_owns_its_rows_and_leaves_other_sources_alone(store: Store, world: tuple[DatasetInfo, dict[str, LocalFile]]) -> None:
    info, files = world
    stale = {"id": "mock:stale:1", "type": "Gene", "label": "Mock stale", "synonyms": [], "props": {}, "source": SOURCE_DB, "retrieved_at": MOCK_TS}
    foreign = {"id": "mock:foreign:1", "type": "Gene", "label": "Mock foreign", "synonyms": [], "props": {}, "source": "mock", "retrieved_at": MOCK_TS}
    store.upsert_nodes([stale, foreign])
    store.upsert_edges([{"source": "mock:stale:1", "target": "mock:foreign:1", "relation": "associated_with", "source_db": SOURCE_DB, "props": {}, "retrieved_at": MOCK_TS}])
    store.add_coverage("mock:stale:1", "OptimusKG gene", "STALE", "2026-10-03", 0)
    store.add_coverage("mock:foreign:1", "mock source", "x", "2026-10-03", 0)
    summary = slice_into_store(store, SEEDS, info, files)
    assert summary["cleared_previous_run"] == {"nodes": 1, "edges": 1, "coverage": 1}
    assert store.get_node("mock:stale:1") is None and store.get_node("mock:foreign:1") is not None
    assert [r for r in store.coverage_rows() if r["entity_id"] == "mock:foreign:1"]
    assert not [r for r in store.coverage_rows() if r["entity_id"] == "mock:stale:1"]


def test_seed_disease_term_matches_name_and_synonym_case_insensitively(sliced: tuple[Store, dict[str, Any]]) -> None:
    store, summary = sliced
    assert summary["seed_resolution"]["disease_terms"] == {
        "mock synonym two": ["Orphanet:9000004"],
        "MOCK A SYNDROME": ["MONDO:9000001"],
        "no such mock disease": [],
    }
    assert coverage(store, "seed:disease:mock synonym two", "OptimusKG disease")[0]["hits"] == 1
    assert coverage(store, "seed:disease:no such mock disease", "OptimusKG disease")[0]["hits"] == 0
    d = store.get_node("Orphanet:9000004")
    assert d is not None and d["label"] == "Mock disease D"
    assert d["synonyms"] == ["Mock Synonym Two", "Mock disease D alias"]
    assert d["props"]["exact_synonyms"] is None, "raw props are kept verbatim"


def test_parent_inclusion_and_is_a_direction_child_to_parent(sliced: tuple[Store, dict[str, Any]]) -> None:
    store, _ = sliced
    is_a = {(e["source"], e["target"]): e for e in edges_of(store, REL_IS_A)}
    disease_is_a = {k: v for k, v in is_a.items() if v["props"]["backbone_label"] == "DIS-DIS"}
    assert set(disease_is_a) == {("MONDO:9000001", "MONDO:9000005"), ("MONDO:9000002", "OBA:VT9000007")}
    assert all(v["props"]["backbone_relation"] == "PARENT" for v in disease_is_a.values())
    assert store.get_node("MONDO:9000006") is None, "grandparent is beyond include_parent_levels=1"
    assert coverage(store, "MONDO:9000001", "OptimusKG disease_disease")[0]["hits"] == 1, "the HP_ parent is not a disease node"


def test_parent_levels_zero_and_two(store: Store, world: tuple[DatasetInfo, dict[str, LocalFile]]) -> None:
    info, files = world
    seeds = dict(SEEDS, thresholds=dict(SEEDS["thresholds"], include_parent_levels=0))
    summary = slice_into_store(store, seeds, info, files)
    assert summary["diseases"]["parents"] == 0 and summary["diseases"]["included"] == 3
    with Store(":memory:") as other:
        seeds2 = dict(SEEDS, thresholds=dict(SEEDS["thresholds"], include_parent_levels=2))
        summary2 = slice_into_store(other, seeds2, info, files)
        assert summary2["diseases"]["parents"] == 3
        assert other.get_node("MONDO:9000006") is not None


# --------------------------------------------------------------------------------------
# slice: phenotypes
# --------------------------------------------------------------------------------------


def test_phenotype_annotations_ancestor_closure_and_is_a_flip(sliced: tuple[Store, dict[str, Any]]) -> None:
    store, summary = sliced
    has_phe = {(e["source"], e["target"]): e for e in edges_of(store, REL_HAS_PHENOTYPE)}
    assert set(has_phe) == {("MONDO:9000001", "HP:9000001"), ("MONDO:9000001", "HP:9000002"), ("MONDO:9000002", "HP:9000002")}, "aspect P rows only"
    e = has_phe[("MONDO:9000001", "HP:9000001")]
    assert e["props"]["evidence_type"] == ["PCS"] and e["props"]["frequency"] == ["3/4"] and e["props"]["aspect"] == ["P"]
    assert e["props"]["references"] == ["mock:ref"] and e["props"]["relation_conflict"] is False
    # the aspect "I" annotation is kept under its own relation, with the raw aspect in props
    inheritance = edges_of(store, "has_inheritance")
    assert [(e["source"], e["target"]) for e in inheritance] == [("MONDO:9000002", "HP:9000006")]
    assert inheritance[0]["props"]["aspect"] == ["I"] and inheritance[0]["props"]["backbone_label"] == "DIS-PHE"
    phenotypes = {n["id"] for n in store.iter_nodes("Phenotype")}
    assert phenotypes == {"HP:9000001", "HP:9000002", "HP:9000003", "HP:9000004", "HP:9000006"}, "annotated terms plus every ancestor; not P5 or meddra"
    assert summary["phenotypes"] == {"annotated": 3, "with_ancestors": 5, "unmapped_aspect_rows": 0}
    phe_is_a = {(e["source"], e["target"]) for e in edges_of(store, REL_IS_A) if e["props"]["backbone_label"] == "PHE-PHE"}
    assert phe_is_a == {("HP:9000001", "HP:9000003"), ("HP:9000003", "HP:9000004"), ("HP:9000002", "HP:9000004")}
    p1 = store.get_node("HP:9000001")
    assert p1 is not None and p1["label"] == "Mock phenotype one" and p1["synonyms"] == ["Mock pheno 1"]
    assert p1["props"]["backbone_id"] == P1 and p1["props"]["source_type"] == "PHE"
    assert coverage(store, "MONDO:9000001", "OptimusKG disease_phenotype")[0]["hits"] == 2
    assert coverage(store, "MONDO:9000002", "OptimusKG disease_phenotype")[0]["hits"] == 2, "hits count every annotation row, whatever its aspect"
    assert coverage(store, "Orphanet:9000004", "OptimusKG disease_phenotype")[0]["hits"] == 0


@pytest.mark.parametrize(
    ("aspect", "expected"),
    [
        (["P"], REL_HAS_PHENOTYPE),
        (["I"], "has_inheritance"),
        (["C"], "has_clinical_course"),
        (["M"], "has_clinical_modifier"),
        (["H"], "has_medical_history"),
        (["I", "P"], REL_HAS_PHENOTYPE),
        ("p", REL_HAS_PHENOTYPE),
        (["X"], None),
        ([], None),
        (None, None),
    ],
)
def test_aspect_relation(aspect: Any, expected: str | None) -> None:
    assert optimuskg.aspect_relation(aspect) == expected


def test_unmapped_aspect_rows_are_dropped_and_recorded(store: Store, tmp_path: Path) -> None:
    frames = world_frames()
    rows, schema = frames[DISEASE_PHENOTYPE]
    frames[DISEASE_PHENOTYPE] = (rows + [dis_phe(D_A, P5, "PCS", [], aspect=["X"])], schema)
    info, files = write_world(tmp_path / "raw2", frames)
    summary = slice_into_store(store, SEEDS, info, files)
    assert summary["phenotypes"]["unmapped_aspect_rows"] == 1
    assert store.get_node("HP:9000005") is None, "a dropped annotation pulls in no node"
    rows_a = coverage(store, "MONDO:9000001", "OptimusKG disease_phenotype")
    assert [r["hits"] for r in rows_a] == [3, 1] and "aspect not in" in rows_a[1]["query"]


def test_dangling_edges_are_recorded_as_coverage(store: Store, tmp_path: Path) -> None:
    frames = world_frames()
    rows, schema = frames[DISEASE_PHENOTYPE]
    frames[DISEASE_PHENOTYPE] = (rows + [dis_phe(D_A, "HP_9000999", "PCS", [])], schema)  # no such phenotype node
    info, files = write_world(tmp_path / "raw2", frames)
    summary = slice_into_store(store, SEEDS, info, files)
    assert summary["dropped_dangling_edges"] == {REL_HAS_PHENOTYPE: 1}
    rows_d = coverage(store, "source:OptimusKG", "OptimusKG dangling")
    assert len(rows_d) == 1 and rows_d[0]["hits"] == 1 and rows_d[0]["date"] == MOCK_TS[:10] and REL_HAS_PHENOTYPE in rows_d[0]["query"]
    # a re-run clears the row with the rest of the loader's output
    slice_into_store(store, SEEDS, info, files)
    assert len(coverage(store, "source:OptimusKG", "OptimusKG dangling")) == 1


# --------------------------------------------------------------------------------------
# slice: pathways, GO, drugs
# --------------------------------------------------------------------------------------


def test_pathway_and_go_membership_of_seed_genes_only(sliced: tuple[Store, dict[str, Any]]) -> None:
    store, _ = sliced
    in_pathway = {(e["source"], e["target"]): e for e in edges_of(store, REL_IN_PATHWAY)}
    assert set(in_pathway) == {("ensembl:" + G_A, PWY), ("ensembl:" + G_B, "GO:9000001")}
    assert in_pathway[("ensembl:" + G_B, "GO:9000001")]["props"]["evidence"] == ["IDA"]
    pathways = {n["id"]: n for n in store.iter_nodes("Pathway")}
    assert set(pathways) == {PWY, "GO:9000001"}
    assert pathways[PWY]["props"]["ontology"] == "Reactome" and pathways[PWY]["label"] == "Mock pathway"
    go = pathways["GO:9000001"]
    assert go["props"]["ontology"] == "GO" and go["props"]["ontology_meta"]["title"] == "Mock Gene Ontology"
    assert go["label"] == "Mock process" and go["synonyms"] == ["mock proc"]
    assert coverage(store, "ensembl:" + G_A, "OptimusKG pathway_gene")[0]["hits"] == 1
    assert coverage(store, "ensembl:" + G_A, "OptimusKG biological_process_gene")[0]["hits"] == 0


def test_drug_edges_relations_nodes_and_terms(sliced: tuple[Store, dict[str, Any]]) -> None:
    store, summary = sliced
    inhibits = edges_of(store, REL_INHIBITS)
    targets = edges_of(store, REL_TARGETS)
    assert [(e["source"], e["target"]) for e in inhibits] == [("CHEMBL:" + DRUG_1, "ensembl:" + G_A)]
    assert inhibits[0]["props"]["backbone_relation"] == "INHIBITOR"
    assert inhibits[0]["props"]["mechanisms_of_action"] == ["Mock kinase inhibitor"]
    assert [(e["source"], e["target"]) for e in targets] == [(DRUG_2, "ensembl:" + G_B)]
    assert targets[0]["props"]["backbone_relation"] == "TARGET"
    indicated = {(e["source"], e["target"]): e for e in edges_of(store, REL_INDICATED_FOR)}
    contra = edges_of(store, REL_CONTRAINDICATED_FOR)
    assert set(indicated) == {("CHEMBL:" + DRUG_1, "MONDO:9000001"), ("CHEMBL:" + DRUG_4, "MONDO:9000001")}
    assert indicated[("CHEMBL:" + DRUG_1, "MONDO:9000001")]["props"]["highest_clinical_trial_phase"] == 3.0
    assert [(e["source"], e["target"]) for e in contra] == [("CHEMBL:" + DRUG_1, "MONDO:9000002")]
    assert contra[0]["props"]["backbone_relation"] == "CONTRAINDICATION"
    drugs = {n["id"]: n for n in store.iter_nodes("Drug")}
    assert set(drugs) == {"CHEMBL:" + DRUG_1, DRUG_2, "CHEMBL:" + DRUG_3, "CHEMBL:" + DRUG_4}
    d1 = drugs["CHEMBL:" + DRUG_1]
    assert d1["label"] == "MOCKINIB" and d1["synonyms"] == ["Mockinib", "Mockavi"]
    assert d1["props"]["backbone_id"] == DRUG_1 and d1["props"]["source_type"] == "DRG"
    assert "mol_file_base64" not in d1["props"] and "mol_image_base64" not in d1["props"]
    assert "mrdef" not in d1["props"], "DrugCentral free text can carry posology wording (hard rule 7)"
    assert d1["props"]["is_approved"] is True
    assert summary["seed_resolution"]["drug_terms"] == {"mockazole": ["CHEMBL:" + DRUG_3], "no such mock drug": []}
    assert coverage(store, "seed:drug:no such mock drug", "OptimusKG drug")[0]["hits"] == 0
    assert coverage(store, "ensembl:" + G_A, "OptimusKG drug_gene")[0]["hits"] == 1
    assert coverage(store, "MONDO:9000002", "OptimusKG drug_disease")[0]["hits"] == 1


@pytest.mark.parametrize(
    ("relation", "moa", "expected"),
    [
        ("INHIBITOR", None, REL_INHIBITS),
        ("ANTAGONIST", None, REL_INHIBITS),
        ("BLOCKER", [], REL_INHIBITS),
        ("ALLOSTERIC_ANTAGONIST", None, REL_INHIBITS),
        ("NEGATIVE_ALLOSTERIC_MODULATOR", None, REL_INHIBITS),
        ("NEGATIVE_MODULATOR", None, REL_INHIBITS),
        ("INVERSE_AGONIST", None, REL_INHIBITS),
        ("ANTISENSE_INHIBITOR", None, REL_INHIBITS),
        ("TARGET", ["Mock kinase inhibitor"], REL_INHIBITS),
        ("TARGET", ["Mock kinase negative allosteric modulator"], REL_INHIBITS),
        ("TARGET", None, REL_TARGETS),
        ("AGONIST", ["Mock receptor agonist"], REL_TARGETS),
        ("POSITIVE_ALLOSTERIC_MODULATOR", None, REL_TARGETS),
        ("MODULATOR", None, REL_TARGETS),
        ("ENZYME", None, REL_TARGETS),
    ],
)
def test_drug_gene_relation_rule(relation: str, moa: list[str] | None, expected: str) -> None:
    assert optimuskg.drug_gene_relation(relation, {"mechanisms_of_action": moa}) == expected


def test_drug_disease_relation_rule() -> None:
    assert optimuskg.drug_disease_relation("INDICATION") == REL_INDICATED_FOR
    assert optimuskg.drug_disease_relation("OFF_LABEL_USE") == REL_INDICATED_FOR
    assert optimuskg.drug_disease_relation("CONTRAINDICATION") == REL_CONTRAINDICATED_FOR


def test_drugs_per_disease_cap_spares_seed_gene_drugs(store: Store, world: tuple[DatasetInfo, dict[str, LocalFile]]) -> None:
    info, files = world
    seeds = dict(SEEDS, thresholds=dict(SEEDS["thresholds"], max_drugs_per_disease=0))
    summary = slice_into_store(store, seeds, info, files)
    indicated = {(e["source"], e["target"]) for e in edges_of(store, REL_INDICATED_FOR)}
    assert indicated == {("CHEMBL:" + DRUG_1, "MONDO:9000001")}, "the drug that inhibits a seed gene is never capped"
    assert store.get_node("CHEMBL:" + DRUG_4) is None
    assert store.get_node("CHEMBL:" + DRUG_3) is not None, "seed drug terms are always nodes"
    assert summary["drug_disease_rows"] == {"before_cap": 3, "after_cap": 2}


def test_cap_drugs_per_disease_ranking() -> None:
    def row(frm: str, to: str, relation: str, phase: float | None) -> dict[str, Any]:
        return {"from": frm, "to": to, "relation": relation, "properties": {"highest_clinical_trial_phase": phase}}

    rows = [
        row("seed", "d", "CONTRAINDICATION", None),
        row("x1", "d", "INDICATION", 2.0),
        row("x2", "d", "INDICATION", 4.0),
        row("x3", "d", "CONTRAINDICATION", 4.0),
        row("x4", "d", "OFF_LABEL_USE", None),
        row("x5", "e", "INDICATION", None),
    ]
    kept = optimuskg.cap_drugs_per_disease(rows, {"seed"}, 2)
    assert [(r["from"], r["to"]) for r in kept] == [("seed", "d"), ("x2", "d"), ("x3", "d"), ("x5", "e")]
    assert optimuskg.cap_drugs_per_disease(rows, {"seed"}, None) == rows
    assert [r["from"] for r in optimuskg.cap_drugs_per_disease(rows, {"seed"}, 0)] == ["seed"]


# --------------------------------------------------------------------------------------
# slice: ids, sources row, integrity, idempotence
# --------------------------------------------------------------------------------------


def test_every_id_is_normalised_and_backbone_id_kept(sliced: tuple[Store, dict[str, Any]]) -> None:
    store, _ = sliced
    ids = {n["id"]: n["props"]["backbone_id"] for n in store.iter_nodes()}
    expected = {
        "MONDO:9000001": D_A,
        "Orphanet:9000004": D_D,
        "OBA:VT9000007": D_G,
        "ensembl:" + G_A: G_A,
        G_E: G_E,
        "HP:9000001": P1,
        "GO:9000001": GO,
        PWY: PWY,
        "CHEMBL:" + DRUG_1: DRUG_1,
        DRUG_2: DRUG_2,
    }
    for norm, raw in expected.items():
        assert ids[norm] == raw
    assert not any("_" in i.split(":")[0] for i in ids), "no underscore CURIE survives"
    node_ids = set(ids)
    for e in store.iter_edges():
        assert e["source"] in node_ids and e["target"] in node_ids


def test_sources_row_records_version_license_files_and_score_range(sliced: tuple[Store, dict[str, Any]]) -> None:
    store, summary = sliced
    rows = store.sources_rows()
    assert len(rows) == 1
    src = rows[0]
    assert src["name"] == SOURCE_NAME and src["version"] == MOCK_VERSION and src["license"] == "mock"
    assert src["retrieved_at"] == MOCK_TS
    notes = src["notes"]
    assert set(notes["files"]) == set(NEEDED_FILES)
    assert all(len(f["md5"]) == 32 and f["retrieved_at"] == MOCK_TS and f["id"] > 0 and f["retrieved_at_source"] == "download" for f in notes["files"].values())
    assert notes["evidence_score"] == {"min": 0.001, "max": 0.9999, "rows": 11}, "range over the WHOLE file, not the slice"
    # every upstream database named on a kept node or edge, with the license the spike verified or UNVERIFIED
    up = notes["upstream_sources"]
    assert up == summary["upstream_sources"]
    assert set(up) == {"BGEE", "CTD", "DISGENET", "DRUG_BANK", "DRUG_CENTRAL", "FDA", "GO", "HPO", "MONDO", "OPEN_TARGETS", "REACTOME"}
    # a CURIE-form sources value (the GO edge's "mock:ref") cites a record, not a database: counted by prefix, never a licensed source
    assert "mock:ref" not in up
    assert notes["upstream_reference_ids"] == summary["upstream_reference_ids"] == {"mock": {"distinct": 1, "direct": 0, "indirect": 1}}
    assert up["DISGENET"]["license"].startswith("CC BY-NC-SA 4.0") and up["DISGENET"]["version"] == "7.0"
    assert up["DRUG_BANK"]["license"].startswith("UNVERIFIED") and up["OPEN_TARGETS"]["license"] == optimuskg.UPSTREAM_UNVERIFIED
    # HPO / GO: version and license read from the ontology struct the backbone embeds in its nodes
    # (5 phenotype nodes + 3 PHE-PHE edges name HPO directly; the 4 kept DIS-PHE edges indirectly)
    assert up["HPO"] == {"version": "0", "license": "Mock Phenotype Ontology license: mock (as embedded in OptimusKG, version 0)", "direct": 8, "indirect": 4}
    assert up["GO"]["version"] == "0" and up["GO"]["license"].startswith("Mock Gene Ontology license: mock")
    assert up["DISGENET"]["direct"] == 2 and up["CTD"] == {"version": "", "license": optimuskg.UPSTREAM_UNVERIFIED, "direct": 0, "indirect": 1}
    assert notes["thresholds"]["disease_gene_min_score"] == 0.3
    assert notes["thresholds"]["disease_gene_most_specific"] is True
    assert notes["thresholds"]["max_other_genes_per_disease"] == optimuskg.DEFAULT_MAX_OTHER_GENES
    assert notes["thresholds"]["max_drugs_per_disease"] == optimuskg.DEFAULT_MAX_DRUGS
    assert notes["seed_resolution"] == summary["seed_resolution"]
    assert summary["evidence_score"] == notes["evidence_score"]


def test_upstream_sources_split_databases_from_reference_ids() -> None:
    """Bare tokens are databases (licensed, one meta.sources record each); CURIE-form values are
    record references, grouped by prefix with distinct-id and record counts."""
    records = [
        {"props": {"sources": {"direct": ["OPEN_TARGETS", "DISGENET"], "indirect": ["CTD"]}}},
        {"props": {"sources": {"direct": ["OPEN_TARGETS"], "indirect": ["mockref:1", "mockref:2", "other:x"]}}},
        {"props": {"sources": {"direct": ["mockref:1"], "indirect": []}}},
        {"props": {"sources": ["not a struct"]}},
        {"props": {}},
    ]
    up = optimuskg.upstream_sources(records, {})
    assert set(up) == {"OPEN_TARGETS", "DISGENET", "CTD"}
    assert up["OPEN_TARGETS"] == {"version": "", "license": optimuskg.UPSTREAM_UNVERIFIED, "direct": 2, "indirect": 0}
    assert up["DISGENET"]["version"] == "7.0" and up["DISGENET"]["license"].startswith("CC BY-NC-SA 4.0")
    assert optimuskg.reference_sources(records) == {
        "mockref": {"distinct": 2, "direct": 1, "indirect": 2},
        "other": {"distinct": 1, "direct": 0, "indirect": 1},
    }


def test_counts_summary_matches_store(sliced: tuple[Store, dict[str, Any]]) -> None:
    store, summary = sliced
    counts = store.counts()
    assert summary["nodes_by_type"] == counts["nodes_by_type"] == {"Disease": 5, "Drug": 4, "Gene": 5, "Pathway": 2, "Phenotype": 5}
    assert summary["edges_by_relation"] == counts["edges_by_relation"] == {
        REL_ASSOCIATED_WITH: 6,
        REL_CONTRAINDICATED_FOR: 1,
        "has_inheritance": 1,
        REL_HAS_PHENOTYPE: 3,
        REL_IN_PATHWAY: 2,
        REL_INDICATED_FOR: 2,
        REL_INHIBITS: 1,
        REL_IS_A: 5,
        REL_TARGETS: 1,
    }
    assert summary["dropped_dangling_edges"] == {}
    assert set(summary["timings_s"]) >= {"genes", "diseases", "phenotypes", "drugs", "write"}


def test_load_uses_resolved_files_and_is_idempotent(store: Store, world: tuple[DatasetInfo, dict[str, LocalFile]], monkeypatch: pytest.MonkeyPatch) -> None:
    info, files = world
    calls: list[dict[str, Any]] = []

    def fake_resolve(paths: Any, **kwargs: Any) -> tuple[DatasetInfo, dict[str, LocalFile]]:
        calls.append(kwargs)
        assert tuple(paths) == NEEDED_FILES
        return info, files

    monkeypatch.setattr(optimuskg, "resolve_files", fake_resolve)
    first = load(store, SEEDS, offline=True, raw_dir=Path("/nonexistent"))
    counts = store.counts()
    second = load(store, SEEDS, offline=True, raw_dir=Path("/nonexistent"))
    assert calls[0]["offline"] is True and calls[0]["raw_dir"] == Path("/nonexistent")
    assert store.counts() == counts, "re-running the loader does not change the store"
    assert first["nodes_by_type"] == second["nodes_by_type"]
    assert "total" in first["timings_s"] and "resolve_files" in first["timings_s"]


def test_load_end_to_end_from_raw_dir_offline(store: Store, tmp_path: Path) -> None:
    """Files already under RAW_DIR/optimuskg/<version>/ with a manifest: no network needed."""
    raw = tmp_path / "raw"
    info, _ = write_world(raw / "optimuskg" / MOCK_VERSION)
    optimuskg.write_manifest(raw / "optimuskg" / MOCK_VERSION, info, {})
    http = CachedHttp(cache=DiskCache(tmp_path / "cache", offline=True), transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    summary = load(store, SEEDS, offline=True, raw_dir=raw, http=http)
    assert summary["dataset"]["version"] == MOCK_VERSION
    assert store.counts()["nodes"] == 21


# --------------------------------------------------------------------------------------
# network (opt-in)
# --------------------------------------------------------------------------------------


@pytest.mark.network
@pytest.mark.skipif(os.environ.get("ATLAS_NETWORK_TESTS") != "1", reason="set ATLAS_NETWORK_TESTS=1 to hit Harvard Dataverse")
def test_live_metadata_lists_every_needed_file() -> None:
    info = optimuskg.fetch_dataset()
    assert set(NEEDED_FILES) <= set(info.files)
    assert all(len(info.files[p].md5) == 32 for p in NEEDED_FILES)
    assert info.license_name


# --------------------------------------------------------------------------------------
# export inclusion rule (shared with pipeline.graph.build.prune_graph)
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("props", "expected"),
    [
        ({"evidence_score": 0.8, "sources": OT_DISGENET}, True),  # curated and above the floor
        ({"evidence_score": 0.35, "sources": DISGENET_ONLY}, True),  # curated, just above the floor
        ({"evidence_score": 0.1, "sources": DISGENET_ONLY}, False),  # curated but below the floor
        ({"evidence_score": 0.5, "sources": OT}, False),  # above the floor, not curated, below the high score
        ({"evidence_score": 0.6, "sources": OT}, True),  # the high score admits an edge on its own
        ({"evidence_score": 0.9}, True),  # no sources at all, high score
        ({"evidence_score": 0.4, "sources": {"direct": ["orphanet"], "indirect": []}}, True),  # names compare case-insensitively
        ({"evidence_score": 0.4, "sources": ["CLINGEN"]}, True),  # a plain list counts as direct sources
        ({"evidence_score": 0.4, "sources": {"direct": [], "indirect": ["DISGENET"]}}, False),  # indirect sources do not count
        ({}, True),  # not an OptimusKG aggregate (no score): included
        ({"evidence_score": "not a number"}, True),
    ],
)
def test_disease_gene_included_rule(props: dict[str, Any], expected: bool) -> None:
    assert optimuskg.disease_gene_included(props, {"disease_gene_min_score": 0.3, "disease_gene_high_score": 0.6}) is expected
    assert optimuskg.disease_gene_included(props) is expected, "the defaults are the documented thresholds"


def test_disease_gene_included_reads_the_configured_sources_and_scores() -> None:
    t = {"disease_gene_min_score": 0.2, "disease_gene_high_score": 0.95, "disease_gene_curated_sources": ["MOCK_CURATED"]}
    assert optimuskg.disease_gene_included({"evidence_score": 0.25, "sources": {"direct": ["MOCK_CURATED"]}}, t) is True
    assert optimuskg.disease_gene_included({"evidence_score": 0.9, "sources": DISGENET_ONLY}, t) is False, "DISGENET is no longer curated and 0.9 is below the high score"
    assert optimuskg.inclusion_rule_text(t) == "direct source in [MOCK_CURATED] and evidence_score >= 0.2, or evidence_score >= 0.95"
    assert optimuskg.inclusion_rule_text() == "direct source in [CLINGEN, DISGENET, OMIM, ORPHANET] and evidence_score >= 0.3, or evidence_score >= 0.6"
    assert optimuskg.DEFAULT_CURATED_SOURCES == ("DISGENET", "ORPHANET", "CLINGEN", "OMIM") and optimuskg.DEFAULT_HIGH_SCORE == 0.6
