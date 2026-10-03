"""Generate ``data/build/graph.mock.json`` from the contract in ``pipeline/models.py``.

    uv run python tests/fixtures/make_mock.py             # writes data/build/graph.mock.json
    uv run python tests/fixtures/make_mock.py out.json    # writes somewhere else
    uv run python -m tests.fixtures.make_mock             # same as the first form

The mock exists so the frontend and the contract test have something to render and check
before the pipeline produces a real graph (README section 12, M0). It is built with the
Pydantic models, written with :meth:`Graph.dump` and read back with :meth:`Graph.load`, so
the file on disk is exactly what the strict file-level contract accepts.

Everything in this file is a placeholder (README hard rules 1 and 2):

* Node ids are ``mock:<type>:<n>``. Two exceptions are forced by the contract itself:
  Mechanism ids must have the shape ``mech:<GENE>:<GoF|LoF|DN>[:somatic]`` (the contract
  test checks this on every graph, mock included), so they use the fake gene symbols
  ``MOCKGENE1..3``; and a ``Claim`` node's id must equal its ``claims[]`` entry
  (``c_000001``). Edges are ``e_000001``, claims ``c_000001``, clusters ``cl_001``.
* Every label starts with ``"Mock "``. No real gene symbol, disease, drug, person, PMID,
  DOI, NCT number or URL appears anywhere; every edge source is ``kind: "db"`` with a
  ``mock:`` record id, and ``meta.sources`` use ``license: "mock"``.
* Paper abstracts are invented filler that says so, and every claim's ``quote`` is a
  verbatim span of its paper's abstract after whitespace normalization (some spans cross a
  line break on purpose, so the quote check is exercised for real).
* Treatment claims only say what an intervention was "followed by"; nothing states how any
  intervention was given (README hard rule 7).

``built_at`` is a fixed timestamp (the date the mock was written) so re-running the script
is a no-op for git.

What the mock exercises
-----------------------
Every node type; every evidence class; every status, including one ``contested`` edge whose
studies are all < 10 patients (``"small_n"`` carried in ``attributes.labels``) and one
``inferred_unsupported`` edge with ``attributes.reason == "direction mismatch"``; two clusters
(one robust, one not); three ``similar_to`` edges with a full score breakdown; variant ->
mechanism edges (one with a variant-specific claim); germline and somatic ``origin``
attributes; asset edges with ``verdict`` / ``differs`` / ``needs_review``; a preprint-flagged
edge; a ``Claim`` node used as an edge endpoint; and ``meta.coverage`` with zero-hit searches.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:  # allow `python tests/fixtures/make_mock.py` from anywhere
    sys.path.insert(0, str(REPO_ROOT))

from pipeline.models import (  # noqa: E402  (after the sys.path fix on purpose)
    Claim,
    Cluster,
    Coverage,
    CoverageSearch,
    Edge,
    EdgeSource,
    EdgeStatus,
    EvidenceClass,
    Graph,
    Meta,
    Node,
    NodeType,
    Polarity,
    SourceKind,
    SourceRecord,
    StudyType,
)

DEFAULT_OUT = REPO_ROOT / "data" / "build" / "graph.mock.json"

# The date the mock was written. Fixed on purpose: the output must be byte-for-byte stable.
MOCK_DATE = "2026-10-03"
BUILT_AT = "2026-10-03T00:00:00+00:00"

# --------------------------------------------------------------------------------------
# Ids (formats only; see the module docstring)
# --------------------------------------------------------------------------------------

GENE_A, GENE_B, GENE_C = "mock:gene:1", "mock:gene:2", "mock:gene:3"
SYMBOL = {GENE_A: "MOCKGENE1", GENE_B: "MOCKGENE2", GENE_C: "MOCKGENE3"}

VAR_A1, VAR_A2, VAR_A3 = "mock:variant:1", "mock:variant:2", "mock:variant:3"

MECH_A_GOF = "mech:MOCKGENE1:GoF"
MECH_A_LOF = "mech:MOCKGENE1:LoF"
MECH_B_LOF = "mech:MOCKGENE2:LoF"
MECH_C_GOF_SOMATIC = "mech:MOCKGENE3:GoF:somatic"

DIS_1, DIS_2, DIS_3, DIS_4 = (f"mock:disease:{i}" for i in range(1, 5))
PHE_P, PHE_Q, PHE_R, PHE_S = (f"mock:phenotype:{i}" for i in range(1, 5))
PATHWAY = "mock:pathway:1"
DRUG_X, DRUG_Y = "mock:drug:1", "mock:drug:2"
PAPER_1, PAPER_2, PAPER_3 = "mock:paper:1", "mock:paper:2", "mock:paper:3"
INV_1, INV_2 = "mock:investigator:1", "mock:investigator:2"
GROUP = "mock:patientgroup:1"
REGISTRY = "mock:registry:1"
STUDY = "mock:study:1"

CLAIM_1, CLAIM_2, CLAIM_3, CLAIM_4, CLAIM_5, CLAIM_6 = (f"c_{i:06d}" for i in range(1, 7))

# Mock "database" record ids used as edge sources (kind "db" is the only kind whose id shape
# is unconstrained, so nothing here can be mistaken for a PMID, DOI, NCT number or URL).
SRC_BACKBONE = "mock:source:backbone"
SRC_VARIANT_DB = "mock:source:variant_db"
SRC_PHENOTYPE_DB = "mock:source:phenotype_db"
SRC_LITERATURE = "mock:source:literature_index"
SRC_TRIAL_REGISTRY = "mock:source:trial_registry"
SRC_SEEDS = "mock:source:patient_group_seeds"
SRC_RULES = "mock:source:rules"
SRC_SIMILARITY = "mock:source:similarity"

# --------------------------------------------------------------------------------------
# Paper abstracts: invented filler. Claims quote verbatim spans of these (after whitespace
# normalization), so edit the two together.
# --------------------------------------------------------------------------------------

ABSTRACT_1 = (
    "Mock abstract for Mock Paper One. This placeholder study describes a mock cohort of twelve\n"
    "individuals with Mock Disease One. In this mock cohort, gain-of-function changes in Mock Gene A\n"
    "were present in every individual with Mock Disease One. In mock cell assays, Mock Variant A1\n"
    "increased signalling through Mock Pathway Alpha compared with the reference sequence, and the\n"
    "increase was larger than for other mock variants of Mock Gene A. In a mock case series of six\n"
    "treated individuals, Mock Drug X was followed by fewer episodes of Mock Phenotype P. Every name,\n"
    "number and finding in this abstract is a placeholder and describes no real study."
)

ABSTRACT_2 = (
    "Mock abstract for Mock Paper Two. This placeholder report describes seven mock individuals with\n"
    "Mock Disease One: four who received Mock Drug X and three who received Mock Drug Y.  Mock Drug X\n"
    "did not reduce the frequency of Mock Phenotype P in the four mock individuals who received it.\n"
    "Mock Drug Y was followed by fewer episodes of Mock Phenotype Q in all three mock individuals who\n"
    "received it. Every statement in this abstract is a placeholder and describes no real study."
)

ABSTRACT_3 = (
    "Mock abstract for Mock Preprint Three. In a placeholder cohort of nine mock individuals with Mock\n"
    "Disease Three, loss-of-function changes in Mock Gene B were identified in nine of nine mock\n"
    "individuals. This mock preprint has not been peer reviewed and describes no real study."
)

# Similarity weights (README section 6); the same triple is stored on every similar_to edge.
WEIGHTS = {"w_m": 0.4, "w_p": 0.4, "w_c": 0.2}
INFERRED_CAP = 0.5


# --------------------------------------------------------------------------------------
# Small builders
# --------------------------------------------------------------------------------------


def node(id: str, type: NodeType, label: str, synonyms: list[str] | None = None, **props: Any) -> Node:
    return Node(id=id, type=type, label=label, synonyms=synonyms or [], props=props)


def db_source(record: str, date: str = MOCK_DATE) -> EdgeSource:
    return EdgeSource(kind=SourceKind.db, id=record, date=date)


def similarity(mechanism_score: float, phenotype_overlap: float, conflict_penalty: float) -> tuple[float, dict[str, Any]]:
    """Score a similar_to edge the way README section 6 describes, keeping every raw input."""
    raw = (
        WEIGHTS["w_m"] * mechanism_score
        + WEIGHTS["w_p"] * phenotype_overlap
        - WEIGHTS["w_c"] * conflict_penalty
    )
    raw = round(raw, 4)
    score = min(INFERRED_CAP, max(0.0, raw))
    basis = {
        "mechanism_score": mechanism_score,
        "phenotype_overlap": phenotype_overlap,
        "conflict_penalty": conflict_penalty,
        "weights": dict(WEIGHTS),
        "raw_score": raw,
        "cap": INFERRED_CAP,
        "clamped": score != raw,
    }
    return score, basis


class EdgeList:
    """Allocates ``e_000001``-style ids in insertion order."""

    def __init__(self) -> None:
        self.items: list[Edge] = []

    def add(
        self,
        source: str,
        target: str,
        relation: str,
        *,
        evidence_class: EvidenceClass,
        confidence: float,
        confidence_basis: dict[str, Any],
        sources: list[EdgeSource],
        status: EdgeStatus = EdgeStatus.supported,
        supporting: list[str] | None = None,
        contradicting: list[str] | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> str:
        eid = f"e_{len(self.items) + 1:06d}"
        self.items.append(
            Edge(
                id=eid,
                source=source,
                target=target,
                relation=relation,
                evidence_class=evidence_class,
                confidence=confidence,
                confidence_basis=confidence_basis,
                status=status,
                sources=sources,
                supporting=supporting or [],
                contradicting=contradicting or [],
                attributes=attributes or {},
            )
        )
        return eid


# --------------------------------------------------------------------------------------
# The graph
# --------------------------------------------------------------------------------------


def build_nodes() -> list[Node]:
    N = NodeType
    return [
        # Genes
        node(GENE_A, N.Gene, "Mock Gene A", ["Mock gene alpha"], symbol=SYMBOL[GENE_A], pathway=PATHWAY),
        node(GENE_B, N.Gene, "Mock Gene B", ["Mock gene beta"], symbol=SYMBOL[GENE_B], pathway=PATHWAY),
        node(GENE_C, N.Gene, "Mock Gene C", ["Mock gene gamma"], symbol=SYMBOL[GENE_C], pathway=PATHWAY),
        # Variants: never collapsed into one node (ground-truth test 7)
        node(VAR_A1, N.Variant, "Mock Variant A1", gene=GENE_A, gene_symbol=SYMBOL[GENE_A], protein_change="Mock change A1", review="mock:reviewed"),
        node(VAR_A2, N.Variant, "Mock Variant A2", gene=GENE_A, gene_symbol=SYMBOL[GENE_A], protein_change="Mock change A2", review="mock:reviewed"),
        node(VAR_A3, N.Variant, "Mock Variant A3", gene=GENE_A, gene_symbol=SYMBOL[GENE_A], protein_change="Mock change A3", review="mock:single_submitter"),
        # Mechanisms: mech:<GENE>:<GoF|LoF|DN>[:somatic]
        node(MECH_A_GOF, N.Mechanism, "Mock Mechanism A gain-of-function", gene=GENE_A, direction="GoF", origin="germline"),
        node(MECH_A_LOF, N.Mechanism, "Mock Mechanism A loss-of-function", gene=GENE_A, direction="LoF", origin="germline"),
        node(MECH_B_LOF, N.Mechanism, "Mock Mechanism B loss-of-function", gene=GENE_B, direction="LoF", origin="germline"),
        node(MECH_C_GOF_SOMATIC, N.Mechanism, "Mock Mechanism C gain-of-function, somatic", gene=GENE_C, direction="GoF", origin="somatic"),
        # Diseases: One and Two share a gene but not a mechanism direction; Four is somatic
        node(DIS_1, N.Disease, "Mock Disease One", ["Mock disease 1", "Mock condition one"], origin="germline", mechanism=MECH_A_GOF),
        node(DIS_2, N.Disease, "Mock Disease Two", ["Mock disease 2"], origin="germline", mechanism=MECH_A_LOF),
        node(DIS_3, N.Disease, "Mock Disease Three", ["Mock disease 3"], origin="germline", mechanism=MECH_B_LOF),
        node(DIS_4, N.Disease, "Mock Disease Four", ["Mock disease 4"], origin="somatic", mechanism=MECH_C_GOF_SOMATIC),
        # Phenotypes, with a mock information content (README section 5)
        node(PHE_P, N.Phenotype, "Mock Phenotype P", ["Mock symptom P"], information_content=3.1),
        node(PHE_Q, N.Phenotype, "Mock Phenotype Q", ["Mock symptom Q"], information_content=1.4),
        node(PHE_R, N.Phenotype, "Mock Phenotype R", ["Mock symptom R"], information_content=2.7),
        node(PHE_S, N.Phenotype, "Mock Phenotype S", ["Mock symptom S"], information_content=2.2),
        # Pathway
        node(PATHWAY, N.Pathway, "Mock Pathway Alpha", ["Mock signalling pathway alpha"], description="Mock signalling pathway used only for fixtures"),
        # Drugs: X inhibits the pathway, Y inhibits Gene A
        node(DRUG_X, N.Drug, "Mock Drug X", ["Mock inhibitor X"], action="inhibits", target=PATHWAY),
        node(DRUG_Y, N.Drug, "Mock Drug Y", ["Mock inhibitor Y"], action="inhibits", target=GENE_A),
        # Papers: abstracts stored so quotes are checkable from graph.json alone
        node(
            PAPER_1, N.Paper, "Mock Paper One",
            title="Mock Paper One: a placeholder cohort of Mock Disease One",
            abstract=ABSTRACT_1, year=2000, journal="Mock Journal of Placeholder Studies",
            authors=["Mock Investigator One", "Mock Investigator Two"],
        ),
        node(
            PAPER_2, N.Paper, "Mock Paper Two",
            title="Mock Paper Two: a placeholder treatment report",
            abstract=ABSTRACT_2, year=2001, journal="Mock Journal of Placeholder Studies",
            authors=["Mock Investigator One"],
        ),
        node(
            PAPER_3, N.Paper, "Mock Preprint Three",
            title="Mock Preprint Three: a placeholder cohort of Mock Disease Three",
            abstract=ABSTRACT_3, year=2002, journal="Mock Preprint Server",
            authors=["Mock Investigator Two"], preprint=True,
        ),
        # A Claim node: exists only because it is an edge endpoint; claims[] stays the truth
        node(CLAIM_1, N.Claim, "Mock Claim 1"),
        # Investigators: "author of N papers on X", never "field leader"; no guessed contact
        node(INV_1, N.Investigator, "Mock Investigator One", affiliation="Mock Institute of Placeholder Studies", n_papers=2, role_label="author of 2 mock papers on Mock Pathway Alpha", contact=None),
        node(INV_2, N.Investigator, "Mock Investigator Two", affiliation="Mock Institute of Placeholder Studies", n_papers=2, role_label="author of 2 mock papers on Mock Pathway Alpha", contact=None),
        # Patient group, registry, study
        node(GROUP, N.PatientGroup, "Mock Patient Group", ["Mock family alliance"], scope="Mock Disease One", verified=False, note="placeholder; no real organisation"),
        node(REGISTRY, N.Registry, "Mock Registry", scope="Mock Disease One and related mock conditions", verified=False, note="placeholder; no real registry"),
        node(
            STUDY, N.Study, "Mock Study One",
            status="mock:recruiting", conditions=[DIS_1], interventions=[DRUG_X], mechanism=MECH_A_GOF,
            eligibility="Mock eligibility: placeholder criteria limited to Mock Disease One with a gain-of-function mechanism.",
        ),
    ]


def build_claims() -> list[Claim]:
    return [
        Claim(
            id=CLAIM_1, pmid="", paper=PAPER_1, polarity=Polarity.supports,
            quote="gain-of-function changes in Mock Gene A were present in every individual with Mock Disease One",
            subject=MECH_A_GOF, predicate="causes", object=DIS_1,
            study_type=StudyType.cohort, n_patients=12,
        ),
        Claim(
            id=CLAIM_2, pmid="", paper=PAPER_1, polarity=Polarity.supports,
            quote="Mock Variant A1 increased signalling through Mock Pathway Alpha compared with the reference sequence",
            subject=VAR_A1, predicate="produces", object=MECH_A_GOF, variant=VAR_A1,
            study_type=StudyType.in_vitro, n_patients=None,
        ),
        Claim(
            id=CLAIM_3, pmid="", paper=PAPER_1, polarity=Polarity.supports,
            quote="In a mock case series of six treated individuals, Mock Drug X was followed by fewer episodes of Mock Phenotype P",
            subject=DRUG_X, predicate="improves", object=DIS_1,
            study_type=StudyType.case_series, n_patients=6,
        ),
        Claim(
            id=CLAIM_4, pmid="", paper=PAPER_2, polarity=Polarity.contradicts,
            quote="Mock Drug X did not reduce the frequency of Mock Phenotype P in the four mock individuals who received it",
            subject=DRUG_X, predicate="improves", object=DIS_1,
            study_type=StudyType.case_series, n_patients=4,
        ),
        Claim(
            id=CLAIM_5, pmid="", paper=PAPER_2, polarity=Polarity.supports,
            quote="Mock Drug Y was followed by fewer episodes of Mock Phenotype Q in all three mock individuals who received it",
            subject=DRUG_Y, predicate="improves", object=DIS_1,
            study_type=StudyType.case_report, n_patients=3,
        ),
        Claim(
            id=CLAIM_6, pmid="", paper=PAPER_3, polarity=Polarity.supports,
            quote="loss-of-function changes in Mock Gene B were identified in nine of nine mock individuals",
            subject=GENE_B, predicate="associated_with", object=DIS_3,
            study_type=StudyType.cohort, n_patients=9,
        ),
    ]


def build_edges() -> tuple[list[Edge], dict[str, str]]:
    """Returns the edges and a name -> edge id map for the clusters' ``why`` blocks."""
    E = EvidenceClass
    S = EdgeStatus
    edges = EdgeList()
    named: dict[str, str] = {}

    backbone = [db_source(SRC_BACKBONE)]
    variant_db = [db_source(SRC_VARIANT_DB)]
    phenotype_db = [db_source(SRC_PHENOTYPE_DB)]

    # Gene -> Pathway (observed, backbone)
    for gene in (GENE_A, GENE_B, GENE_C):
        edges.add(
            gene, PATHWAY, "in_pathway",
            evidence_class=E.observed, confidence=0.9,
            confidence_basis={"source": SRC_BACKBONE, "evidence_code": "mock:curated", "rubric": "curated > automated"},
            sources=backbone,
        )

    # Disease -> Gene (observed, backbone; origin on every disease-gene edge)
    def disease_gene(disease: str, gene: str, origin: str, score: float, n_pmids: int, **extra: Any) -> str:
        return edges.add(
            disease, gene, "associated_with",
            evidence_class=E.observed, confidence=round(min(1.0, score + 0.05 * (n_pmids >= 10)), 2),
            confidence_basis={
                "source": SRC_BACKBONE, "evidence_score": score, "evidence_score_range": [0.0, 1.0],
                "number_of_pmids": n_pmids, "multi_pmid_bonus": 0.05 if n_pmids >= 10 else 0.0,
            },
            sources=backbone, **extra,
        )

    disease_gene(DIS_1, GENE_A, "germline", 0.82, 14, attributes={"origin": "germline"})
    disease_gene(DIS_2, GENE_A, "germline", 0.74, 6, attributes={"origin": "germline"})
    # An observed edge enriched by a (preprint) paper keeps evidence_class observed.
    disease_gene(
        DIS_3, GENE_B, "germline", 0.61, 3,
        attributes={"origin": "germline", "preprint": True, "labels": ["preprint"]},
        supporting=[CLAIM_6],
    )
    disease_gene(DIS_4, GENE_C, "somatic", 0.88, 22, attributes={"origin": "somatic"})

    # Disease -> Mechanism (origin on every disease-mechanism edge)
    edges.add(
        DIS_1, MECH_A_GOF, "has_mechanism",
        evidence_class=E.extracted, confidence=0.7,
        confidence_basis={"claims": [CLAIM_1], "study_types": ["cohort"], "max_n_patients": 12, "rubric": "trial > cohort > case_series > case_report > in_vitro"},
        sources=[db_source(PAPER_1, "2000")], supporting=[CLAIM_1], attributes={"origin": "germline"},
    )
    for disease, mech, origin in ((DIS_2, MECH_A_LOF, "germline"), (DIS_3, MECH_B_LOF, "germline"), (DIS_4, MECH_C_GOF_SOMATIC, "somatic")):
        edges.add(
            disease, mech, "has_mechanism",
            evidence_class=E.observed, confidence=0.75,
            confidence_basis={"source": SRC_VARIANT_DB, "review_status": "mock:reviewed", "n_submitters": 2},
            sources=variant_db, attributes={"origin": origin},
        )

    # Variant -> Gene and Variant -> Mechanism; A1 carries a variant-specific claim
    for variant in (VAR_A1, VAR_A2, VAR_A3):
        edges.add(
            variant, GENE_A, "variant_of",
            evidence_class=E.observed, confidence=1.0,
            confidence_basis={"source": SRC_VARIANT_DB, "mapping": "mock:exact"},
            sources=variant_db,
        )
    named["variant_mechanism_claim"] = edges.add(
        VAR_A1, MECH_A_GOF, "has_mechanism",
        evidence_class=E.extracted, confidence=0.6,
        confidence_basis={"claims": [CLAIM_2], "study_types": ["in_vitro"], "max_n_patients": None, "rubric": "trial > cohort > case_series > case_report > in_vitro"},
        sources=[db_source(PAPER_1, "2000")], supporting=[CLAIM_2],
        attributes={"origin": "germline", "labels": ["variant_specific"]},
    )
    edges.add(
        VAR_A2, MECH_A_GOF, "has_mechanism",
        evidence_class=E.observed, confidence=0.8,
        confidence_basis={"source": SRC_VARIANT_DB, "review_status": "mock:reviewed", "n_submitters": 3},
        sources=variant_db, attributes={"origin": "germline"},
    )
    edges.add(
        VAR_A3, MECH_A_LOF, "has_mechanism",
        evidence_class=E.observed, confidence=0.55,
        confidence_basis={"source": SRC_VARIANT_DB, "review_status": "mock:single_submitter", "n_submitters": 1},
        sources=variant_db, attributes={"origin": "germline"},
    )

    # Disease -> Phenotype (observed)
    for disease, phenotype, code, conf in (
        (DIS_1, PHE_P, "mock:curated", 0.9), (DIS_1, PHE_Q, "mock:curated", 0.85),
        (DIS_2, PHE_R, "mock:curated", 0.9), (DIS_2, PHE_S, "mock:automated", 0.5),
        (DIS_3, PHE_R, "mock:curated", 0.9), (DIS_3, PHE_S, "mock:curated", 0.85),
        (DIS_4, PHE_Q, "mock:automated", 0.5),
    ):
        edges.add(
            disease, phenotype, "has_phenotype",
            evidence_class=E.observed, confidence=conf,
            confidence_basis={"source": SRC_PHENOTYPE_DB, "evidence_code": code, "rubric": "curated > automated"},
            sources=phenotype_db,
        )

    # Drug -> Pathway (observed)
    edges.add(
        DRUG_X, PATHWAY, "inhibits",
        evidence_class=E.observed, confidence=0.9,
        confidence_basis={"source": SRC_BACKBONE, "action": "inhibits", "evidence_code": "mock:curated"},
        sources=backbone,
    )

    # Treatment edge 1: contested, every study < 10 patients -> "small_n" in labels
    named["contested_small_n"] = edges.add(
        DRUG_X, DIS_1, "treats",
        evidence_class=E.extracted, confidence=0.35, status=S.contested,
        confidence_basis={
            "supporting": [CLAIM_3], "contradicting": [CLAIM_4],
            "study_types": ["case_series", "case_series"], "n_patients": [6, 4], "max_n_patients": 6,
            "all_small_n": True, "contested_downgrade": 0.2,
            "rubric": "trial > cohort > case_series > case_report > in_vitro",
        },
        sources=[db_source(PAPER_1, "2000"), db_source(PAPER_2, "2001")],
        supporting=[CLAIM_3], contradicting=[CLAIM_4],
        attributes={"labels": ["promising", "contested", "small_n"]},
    )

    # Treatment edge 2: supported by one small study -> small_n
    named["small_n"] = edges.add(
        DRUG_Y, DIS_1, "treats",
        evidence_class=E.extracted, confidence=0.3, status=S.small_n,
        confidence_basis={
            "supporting": [CLAIM_5], "contradicting": [], "study_types": ["case_report"],
            "n_patients": [3], "max_n_patients": 3, "all_small_n": True,
            "rubric": "trial > cohort > case_series > case_report > in_vitro",
        },
        sources=[db_source(PAPER_2, "2001")], supporting=[CLAIM_5],
        attributes={"labels": ["small_n"]},
    )

    # Rule: pathway-inhibiting drug -> loss-of-function disease => inferred_unsupported
    named["direction_mismatch"] = edges.add(
        DRUG_X, DIS_2, "treats",
        evidence_class=E.inferred, confidence=0.1, status=S.inferred_unsupported,
        confidence_basis={
            "rule": "pathway_inhibitor_to_lof_mechanism", "drug_action": "inhibits",
            "drug_target": PATHWAY, "disease_mechanism": MECH_A_LOF, "mechanism_direction": "LoF",
            "cap": INFERRED_CAP,
        },
        sources=[db_source(SRC_RULES)],
        attributes={"reason": "direction mismatch", "labels": ["inferred", "unsupported"]},
    )

    # Similarity edges between mechanism-level disease entities (inferred, capped at 0.5)
    sim_sources = [db_source(SRC_SIMILARITY)]
    score, basis = similarity(mechanism_score=0.7, phenotype_overlap=0.5, conflict_penalty=0.0)
    named["similar_2_3"] = edges.add(
        DIS_2, DIS_3, "similar_to", evidence_class=E.inferred, confidence=score,
        confidence_basis={**basis, "shared_phenotypes": [PHE_R, PHE_S], "same_direction": "LoF", "same_origin": "germline"},
        sources=sim_sources, attributes={"labels": ["inferred"]},
    )
    score, basis = similarity(mechanism_score=0.5, phenotype_overlap=0.3, conflict_penalty=0.5)
    named["similar_1_4"] = edges.add(
        DIS_1, DIS_4, "similar_to", evidence_class=E.inferred, confidence=score,
        confidence_basis={**basis, "shared_phenotypes": [PHE_Q], "same_direction": "GoF", "penalty_reason": "germline vs somatic"},
        sources=sim_sources, attributes={"labels": ["inferred"]},
    )
    score, basis = similarity(mechanism_score=0.2, phenotype_overlap=0.1, conflict_penalty=1.0)
    named["similar_1_2"] = edges.add(
        DIS_1, DIS_2, "similar_to", evidence_class=E.inferred, confidence=score,
        confidence_basis={**basis, "shared_phenotypes": [], "penalty_reason": "opposite direction on the same gene"},
        sources=sim_sources, attributes={"labels": ["inferred"]},
    )

    # Assets (Act tab): verdict, what differs, what needs expert review
    seeds = [db_source(SRC_SEEDS)]
    edges.add(
        REGISTRY, DIS_1, "covers",
        evidence_class=E.observed, confidence=0.8,
        confidence_basis={"source": SRC_SEEDS, "retrieved_at": MOCK_DATE, "verified": False},
        sources=seeds, attributes={"verdict": "reusable_now", "differs": [], "needs_review": []},
    )
    edges.add(
        REGISTRY, DIS_3, "covers",
        evidence_class=E.inferred, confidence=0.4,
        confidence_basis={"source": SRC_SEEDS, "rule": "registry_scope_shares_cluster", "cluster": "cl_002", "cap": INFERRED_CAP},
        sources=seeds,
        attributes={"verdict": "adaptable_needs_review", "differs": ["mechanism gene", "phenotype S frequency"], "needs_review": ["biological", "eligibility"], "labels": ["inferred"]},
    )
    edges.add(
        GROUP, DIS_1, "supports_community",
        evidence_class=E.observed, confidence=0.8,
        confidence_basis={"source": SRC_SEEDS, "retrieved_at": MOCK_DATE, "verified": False},
        sources=seeds, attributes={"verdict": "reusable_now"},
    )
    trial_registry = [db_source(SRC_TRIAL_REGISTRY)]
    edges.add(
        STUDY, DIS_1, "studies",
        evidence_class=E.observed, confidence=0.9,
        confidence_basis={"source": SRC_TRIAL_REGISTRY, "query": "Mock Disease One", "hits": 1},
        sources=trial_registry,
        attributes={"verdict": "adaptable_needs_review", "differs": ["eligibility age range"], "needs_review": ["eligibility"]},
    )
    edges.add(
        STUDY, DRUG_X, "evaluates",
        evidence_class=E.observed, confidence=0.9,
        confidence_basis={"source": SRC_TRIAL_REGISTRY, "query": "Mock Disease One", "hits": 1},
        sources=trial_registry,
    )

    # Investigators -> Papers
    literature = [db_source(SRC_LITERATURE)]
    for investigator, paper, position in ((INV_1, PAPER_1, 1), (INV_2, PAPER_1, 2), (INV_1, PAPER_2, 1), (INV_2, PAPER_3, 1)):
        edges.add(
            investigator, paper, "author_of",
            evidence_class=E.observed, confidence=1.0,
            confidence_basis={"source": SRC_LITERATURE, "author_position": position},
            sources=literature,
        )

    # Claim node -> Paper (the only reason the Claim node exists)
    edges.add(
        CLAIM_1, PAPER_1, "extracted_from",
        evidence_class=E.observed, confidence=1.0,
        confidence_basis={"source": SRC_LITERATURE, "quote_matched": True},
        sources=[db_source(PAPER_1, "2000")],
    )

    return edges.items, named


def build_clusters(named: dict[str, str]) -> list[Cluster]:
    method = {"method": "mock stand-in for Leiden over similar_to edges", "of_weightings": 3}
    return [
        Cluster(
            id="cl_001",
            members=[DIS_1, MECH_A_GOF, VAR_A1, VAR_A2],
            robust=True,
            why={
                **method,
                "weightings_agreeing": 3,
                "shared_mechanism": MECH_A_GOF,
                "shared_origin": "germline",
                "shared_phenotypes": [PHE_P, PHE_Q],
                "not_members": {
                    DIS_2: "same gene, opposite direction; see " + named["similar_1_2"],
                    DIS_4: "same direction but somatic origin; see " + named["similar_1_4"],
                },
            },
        ),
        Cluster(
            id="cl_002",
            members=[DIS_2, DIS_3, MECH_A_LOF, MECH_B_LOF, VAR_A3],
            robust=False,
            why={
                **method,
                "weightings_agreeing": 2,
                "unstable_member": DIS_3,
                "shared_direction": "LoF",
                "shared_origin": "germline",
                "shared_phenotypes": [PHE_R, PHE_S],
                "similarity_edges": [named["similar_2_3"]],
            },
        ),
    ]


def build_meta() -> Meta:
    def record(name: str, version: str = "") -> SourceRecord:
        return SourceRecord(name=name, version=version, retrieved_at=MOCK_DATE, license="mock")

    def searched(*runs: tuple[str, str, int]) -> Coverage:
        return Coverage(searched=[CoverageSearch(source=s, query=q, date=MOCK_DATE, hits=h) for s, q, h in runs])

    return Meta(
        built_at=BUILT_AT,
        sources=[
            record("Mock backbone", "mock-0"),
            record("Mock variant database"),
            record("Mock phenotype database", "mock-0"),
            record("Mock literature index"),
            record("Mock trial registry"),
            record("Mock patient group seeds"),
        ],
        coverage={
            DIS_1: searched(
                (SRC_LITERATURE, "Mock Disease One", 2),
                (SRC_TRIAL_REGISTRY, "Mock Disease One", 1),
                (SRC_SEEDS, "Mock Disease One patient group", 1),
            ),
            DIS_3: searched((SRC_LITERATURE, "Mock Disease Three", 1), (SRC_TRIAL_REGISTRY, "Mock Disease Three", 0)),
            # Zero-hit searches are data (README hard rule 3)
            DIS_4: searched((SRC_TRIAL_REGISTRY, "Mock Disease Four", 0), (SRC_SEEDS, "Mock Disease Four patient group", 0)),
            GENE_C: searched((SRC_SEEDS, "Mock Gene C patient group", 0)),
            DRUG_X: searched((SRC_LITERATURE, "Mock Drug X Mock Disease One", 2)),
        },
    )


def build_mock_graph() -> Graph:
    edges, named = build_edges()
    return Graph(meta=build_meta(), nodes=build_nodes(), edges=edges, claims=build_claims(), clusters=build_clusters(named))


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    out = Path(args[0]) if args else DEFAULT_OUT
    graph = build_mock_graph()
    graph.dump(out)
    reloaded = Graph.load(out)  # the strict file-level contract
    print(
        f"wrote {out}: {len(reloaded.nodes)} nodes, {len(reloaded.edges)} edges, "
        f"{len(reloaded.claims)} claims, {len(reloaded.clusters)} clusters"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
