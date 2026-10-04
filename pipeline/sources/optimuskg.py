"""OptimusKG backbone loader: Harvard Dataverse download + Polars slice into the evidence store.

Everything here follows docs/optimuskg-spike.md (the verified facts about the dataset) and the
shared design in AGENT-SPLIT.md / pipeline/store.py. Two halves:

1. **Obtain the stratified Parquet files** (:func:`resolve_files`). The dataset file list is
   read from the Dataverse native API through :class:`pipeline.cache.CachedHttp` (cached JSON;
   the default Python user agent is blocked with 403 on ``/api/datasets/*``, so every request
   carries :data:`pipeline.config.USER_AGENT`). Files are picked by NAME
   (``<directoryLabel>/<filename>``), never by numeric id; ``dataFile.id`` and ``dataFile.md5``
   come from the metadata. Each file is streamed once into
   ``RAW_DIR/optimuskg/<version>/<path>`` (Dataverse answers 303 to a presigned S3 URL, which
   is followed), md5-verified against the metadata (mismatch: file deleted, error raised) and
   recorded with its ``retrieved_at`` in ``manifest.json`` next to the files. A present file
   whose md5 matches is never fetched again. Offline (``offline=True`` or ``ATLAS_OFFLINE=1``)
   the network is never touched: the cached metadata or the manifest plus the raw files are
   used, or a clear error is raised.

2. **Slice** (:func:`slice_into_store`) with lazy Polars scans and filters; the 73 MB
   disease-gene file is never materialised unfiltered. Order of operations:

   * seed gene SYMBOLS -> gene nodes by exact ``properties.symbol`` match (exactly one node per
     symbol or :class:`GeneResolutionError`; a coverage row per symbol either way);
   * the disease set = targets of seed-gene DIS-GEN edges that pass the threshold rule
     (``evidence_score >= disease_gene_min_score`` or, with ``keep_disgenet_direct``, DISGENET
     in ``sources.direct``) + every disease whose name or synonym equals a seed ``disease_term``
     (case-insensitive) + their DIS-DIS parents up to ``include_parent_levels``;
   * DIS-GEN edges for every included disease: all edges to seed genes (raw props kept, so the
     below-threshold ones are visible with their low score) plus edges to other genes that pass
     the same rule; gene nodes for those genes;

   Two general safeguards, both read from ``thresholds`` with the defaults below, keep the
   backbone's density from swallowing the slice (observed on v3.2: every seed gene carries
   1,300-2,800 DIS-GEN edges and ~830 diseases pass the 0.3 floor, most of them ontology
   roll-ups such as "nervous system disease" or GWAS measurement traits; a literal slice was
   609k edges and a 770 MB store):

   * ``disease_gene_most_specific`` (default True). Open Targets propagates every association
     up the disease ontology, so an umbrella term passes whenever one of its descendants does.
     A disease is therefore pulled in by a seed-gene association only when none of its
     descendants (DIS-DIS hierarchy, full closure) carries a passing association to the SAME
     gene. Term-matched diseases and parents are never subject to this rule.
   * ``max_other_genes_per_disease`` (default 25). For each included disease the non-seed genes
     that pass the rule are ranked by ``evidence_score`` (then ``evidence_count``, then id) and
     only the top N are kept, like ``max_pathway_members_to_expand`` bounds pathway expansion.
     Edges to seed genes are never capped.
   * ``max_drugs_per_disease`` (default 25). Likewise for DRG-DIS edges: per included disease
     the drugs are ranked by ``highest_clinical_trial_phase`` (desc, nulls last), then
     INDICATION before OFF_LABEL_USE before CONTRAINDICATION, then id. Drugs that act on a seed
     gene (any DRG-GEN edge) or match a seed ``drug_term`` are never capped ("neoplasm" alone
     carries 1,776 indicated drugs in v3.2).
   * DIS-PHE edges for included diseases, the phenotype nodes, every PHE-PHE ancestor, and the
     ``is_a`` edges among included phenotypes and among included diseases. The relation follows
     the HPO annotation ``aspect`` (:data:`ASPECT_RELATIONS`): only ``P`` rows (phenotypic
     abnormalities, README section 4's definition of a Phenotype) become ``has_phenotype``;
     ``I`` / ``C`` / ``M`` / ``H`` rows become ``has_inheritance`` / ``has_clinical_course`` /
     ``has_clinical_modifier`` / ``has_medical_history``, so a mode of inheritance can never be
     counted as a shared symptom while nothing is lost. A row with an unmapped aspect is dropped
     and counted in a coverage row for its disease;
   * Reactome pathway and GO biological-process memberships of the seed genes (both are
     Pathway nodes: ``props.ontology`` = ``"Reactome"`` / ``"GO"``);
   * DRG-GEN edges of the seed genes, DRG-DIS edges of the included diseases, the drug nodes,
     and drug nodes matching a seed ``drug_term``.

   Hierarchy direction (spike section 3, second reviewer): the backbone stores PARENT edges as
   ``from`` = parent, ``to`` = child. They are flipped on load to CHILD -> PARENT ``is_a`` with
   ``props.backbone_relation = "PARENT"``.

   Drug -> gene relation: ``inhibits`` when the backbone relation or a ``mechanisms_of_action``
   entry says inhibitor / antagonist / blocker, else ``targets``; the raw action stays in
   ``props.backbone_relation``. Drug -> disease: INDICATION and OFF_LABEL_USE become
   ``indicated_for``; CONTRAINDICATION becomes ``contraindicated_for`` (a contraindication must
   never be displayed as an indication; the raw relation is in ``props.backbone_relation``).

   Ids are normalised once with :func:`pipeline.ids.normalize_curie`: underscore CURIEs
   (``MONDO_``, ``EFO_``, ``Orphanet_``, ``HP_``, ``GO_``) -> colon, bare ``ENSG`` ->
   ``ensembl:``, ids with a colon (``REACT:``, ``DRUGBANK:``, ``meddra:``, ``NCBIGene:``) kept.
   Bare drug ids are ChEMBL ids (``CHEMBL1789941``; spike section 3 "DRG") and get the
   ``CHEMBL`` namespace. The raw spelling is kept in ``props.backbone_id``.

Coverage rows (README hard rule 3): one per seed symbol (``seed:gene:<SYMBOL>``), per seed
gene for each edge file (hits before any threshold), per seed disease term
(``seed:disease:<term>``, 0 when nothing matches), per seed drug term (``seed:drug:<term>``),
per included disease for its phenotype, parent and drug lookups, and one per relation for edges
dropped because an endpoint is not a node in the slice (entity ``source:OptimusKG``).

Edge props keep the source edge's ``properties`` verbatim plus ``backbone_relation``,
``backbone_label`` and ``backbone_undirected``. Node props keep ``properties`` verbatim plus
``backbone_id`` and ``source_type`` (``GEN``/``DIS``/``PHE``/``PWY``/``BPO``/``DRG``), except
the columns listed in :data:`PROPS_DROP`: the base64 molecule columns and the DrugCentral
``mrdef`` free text of drug nodes (a pharmacology summary that can carry posology wording;
README hard rule 7 promises that no dosing text appears anywhere, so it is not exported), and
the bulky structural annotations of gene nodes (chemical probes, homologues, tractability, exon
and transcript lists, ...), which run to 100 KB per gene and have no place in a graph served to a
browser. Identity, naming, cross-references and the function descriptions are kept.

Upstream licenses: the ``sources`` table row records, under ``notes.upstream_sources``, every
database named in ``sources.direct`` / ``sources.indirect`` on the kept nodes and edges with the
license docs/optimuskg-spike.md section 5 verified for it (:data:`UPSTREAM_VERIFIED`), the
version and license the backbone embeds for HPO and GO (read from the nodes' ``ontology``
struct), and ``UNVERIFIED`` for everything else; the build turns each into a ``meta.sources``
record. A ``sources`` value spelled as a CURIE (``PMID:...``, ``GO_REF:...``, ``Reactome:...`` on
GO-annotation edges) cites a record, not a database: those are counted by prefix under
``notes.upstream_reference_ids`` instead, so a paper never appears as a licensed data source.

The loader owns every row it writes (``nodes.source`` / ``edges.source_db`` = ``optimuskg``,
coverage sources starting with ``OptimusKG``): a run first removes the rows of the previous run,
so changing a threshold cannot leave stale nodes or edges behind.

Export inclusion rule
---------------------
The store keeps the wide slice above (so that Lane B's reconciler, which resolves mentions
against the store's nodes, keeps finding its endpoints); ``pipeline.graph.build`` exports a
compact graph by applying :func:`disease_gene_included`, the stricter rule defined here next to
the slice floor so the two definitions sit together: a disease-gene edge is included when a
curated database is among ``sources.direct`` (``disease_gene_curated_sources``, default
:data:`DEFAULT_CURATED_SOURCES`) AND ``evidence_score >= disease_gene_min_score``, OR when
``evidence_score >= disease_gene_high_score`` (default :data:`DEFAULT_HIGH_SCORE`). An edge
without an ``evidence_score`` (not an OptimusKG aggregate) is included. The thresholds live in
``pipeline/seeds/cluster.yaml``.

Gap probe (ground-truth test 6)
-------------------------------
:func:`select_gap_probe` chooses the "obscure gene with no connections" the README asks for by
a general rule over the WHOLE backbone, never by name: the alphabetically first protein-coding
HGNC symbol among the genes with zero rows in ``disease_gene``, ``drug_gene``, ``pathway_gene``
and ``biological_process_gene`` (the gene side of all four files is the Ensembl id in ``to``;
spike section 3). The rule and the biotype live in ``pipeline/seeds/cluster.yaml``
(``gap_probe``); the export (``pipeline.export.run_gap_probe``) writes the chosen gene into the
store as a Gene node with ``props.gap_probe = true`` and records its backbone counts and live
lookups as coverage rows, so the API can answer ``no_supported_route`` with the searches shown.

No identifier value is written in this module; only formats and names (README hard rule 2).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import polars as pl

from pipeline import config
from pipeline.cache import CacheMissOffline, CachedHttp, default_http, is_offline
from pipeline.ids import normalize_curie
from pipeline.models import NodeType
from pipeline.store import Store

__all__ = [
    "ASPECT_RELATIONS",
    "ChecksumMismatch",
    "DEFAULT_CURATED_SOURCES",
    "DEFAULT_GAP_PROBE_BIOTYPE",
    "DEFAULT_GAP_PROBE_RULE",
    "DEFAULT_HIGH_SCORE",
    "DatasetFile",
    "DatasetInfo",
    "GAP_PROBE_COVERAGE",
    "GAP_PROBE_EDGE_FILES",
    "GAP_PROBE_FILES",
    "GAP_PROBE_PROP",
    "GAP_PROBE_XREF_SOURCE",
    "GapProbeError",
    "GeneResolutionError",
    "LocalFile",
    "NEEDED_FILES",
    "OfflineUnavailable",
    "SOURCE_DB",
    "SOURCE_NAME",
    "UPSTREAM_UNVERIFIED",
    "UPSTREAM_VERIFIED",
    "aspect_relation",
    "cap_drugs_per_disease",
    "cap_other_genes",
    "clear_previous",
    "disease_gene_included",
    "download_file",
    "drug_disease_relation",
    "drug_gene_relation",
    "ensure_files",
    "fetch_dataset",
    "inclusion_rule_text",
    "load",
    "md5_of",
    "most_specific",
    "parse_dataset",
    "resolve_files",
    "select_gap_probe",
    "slice_into_store",
    "reference_sources",
    "upstream_sources",
]

log = logging.getLogger(__name__)

SOURCE_NAME = "OptimusKG"
SOURCE_DB = "optimuskg"
DATASET_DOI = "doi:10.7910/DVN/IYNGEV"
DATAVERSE_SERVER = "https://dataverse.harvard.edu"
METADATA_URL = f"{DATAVERSE_SERVER}/api/datasets/:persistentId/"
DATAFILE_URL = DATAVERSE_SERVER + "/api/access/datafile/{file_id}"
DEFAULT_LICENSE = "CC BY-NC-SA 4.0"
RAW_SUBDIR = "optimuskg"
MANIFEST_NAME = "manifest.json"
DOWNLOAD_ATTEMPTS = 3
CHUNK_BYTES = 1 << 20

# Stratified files we need, keyed by "<directoryLabel>/<filename>" (docs/optimuskg-spike.md
# section 2). The unified nodes.parquet / edges.parquet are never touched.
GENE_NODES = "nodes/gene.parquet"
DISEASE_NODES = "nodes/disease.parquet"
PHENOTYPE_NODES = "nodes/phenotype.parquet"
PATHWAY_NODES = "nodes/pathway.parquet"
PROCESS_NODES = "nodes/biological_process.parquet"
DRUG_NODES = "nodes/drug.parquet"
DISEASE_GENE = "edges/disease_gene.parquet"
DISEASE_PHENOTYPE = "edges/disease_phenotype.parquet"
PHENOTYPE_PHENOTYPE = "edges/phenotype_phenotype.parquet"
DISEASE_DISEASE = "edges/disease_disease.parquet"
PATHWAY_GENE = "edges/pathway_gene.parquet"
PROCESS_GENE = "edges/biological_process_gene.parquet"
DRUG_GENE = "edges/drug_gene.parquet"
DRUG_DISEASE = "edges/drug_disease.parquet"
NEEDED_FILES: tuple[str, ...] = (
    GENE_NODES,
    DISEASE_NODES,
    PHENOTYPE_NODES,
    PATHWAY_NODES,
    PROCESS_NODES,
    DRUG_NODES,
    DISEASE_GENE,
    DISEASE_PHENOTYPE,
    PHENOTYPE_PHENOTYPE,
    DISEASE_DISEASE,
    PATHWAY_GENE,
    PROCESS_GENE,
    DRUG_GENE,
    DRUG_DISEASE,
)

# Relation vocabulary written to the store (README section 4; pipeline/store.py docstring).
REL_ASSOCIATED_WITH = "associated_with"
REL_HAS_PHENOTYPE = "has_phenotype"
REL_IS_A = "is_a"
REL_IN_PATHWAY = "in_pathway"
REL_INHIBITS = "inhibits"
REL_TARGETS = "targets"
REL_INDICATED_FOR = "indicated_for"
REL_CONTRAINDICATED_FOR = "contraindicated_for"
# HPO annotation aspect (phenotype.hpoa column 11, kept as the DIS-PHE `aspect` list) -> relation.
# Only "P" rows are phenotypic abnormalities (README section 4); the others keep their own
# relation so an IC-weighted phenotype overlap can never count a mode of inheritance as a symptom.
ASPECT_PHENOTYPIC = "P"
ASPECT_RELATIONS: dict[str, str] = {
    ASPECT_PHENOTYPIC: REL_HAS_PHENOTYPE,
    "I": "has_inheritance",
    "C": "has_clinical_course",  # onset and clinical course
    "M": "has_clinical_modifier",
    "H": "has_medical_history",  # past medical history
}

# Backbone type codes -> contract node types.
NODE_TYPES = {
    "GEN": NodeType.Gene.value,
    "DIS": NodeType.Disease.value,
    "PHE": NodeType.Phenotype.value,
    "PWY": NodeType.Pathway.value,
    "BPO": NodeType.Pathway.value,
    "DRG": NodeType.Drug.value,
}
# Bare drug ids are ChEMBL ids (spike section 3: `CHEMBL1789941`; DRUGBANK:/RXNORM: ids carry
# a colon and are kept as spelled).
DRUG_NAMESPACE_HINT = "CHEMBL"
# Drug -> gene actions that mean "inhibits" (backbone relation such as INHIBITOR, ANTAGONIST,
# BLOCKER, NEGATIVE_ALLOSTERIC_MODULATOR, NEGATIVE_MODULATOR, INVERSE_AGONIST, or the same words
# in a mechanisms_of_action entry; "[ _]" accepts both spellings).
INHIBITING_ACTION = re.compile(r"inhibitor|antagonist|blocker|negative[ _](?:allosteric[ _])?modulator|inverse[ _]agonist", re.IGNORECASE)
CONTRAINDICATION = "CONTRAINDICATION"
BACKBONE_PARENT = "PARENT"
DISGENET = "DISGENET"
SYNONYM_FIELDS = ("exact_synonyms", "related_synonyms", "narrow_synonyms", "broad_synonyms")
# Bulky columns left out of node props, per backbone type code (see the module docstring).
PROPS_DROP: dict[str, frozenset[str]] = {
    # mrdef: DrugCentral's free-text pharmacology summary, which can carry posology wording (hard rule 7).
    "DRG": frozenset({"mol_file_base64", "mol_image_base64", "mrdef"}),
    "GEN": frozenset(
        {
            "chemical_probes",
            "homologues",
            "tractability",
            "safety_liabilities",
            "target_enabling_package",
            "canonical_exons",
            "transcript_ids",
            "constraint_scores",
            "hallmarks_attributes",
            "cancer_hallmarks",
            "subcellular_locations",
            "associated_proteins",
        }
    ),
}
# Defaults for the safeguards when cluster.yaml does not set them.
DEFAULT_MOST_SPECIFIC = True
DEFAULT_MAX_OTHER_GENES = 25
DEFAULT_MAX_DRUGS = 25
# Export inclusion rule (module docstring "Export inclusion rule"): curated direct sources, as the
# backbone spells them in `sources.direct`, and the score that admits an edge on its own.
DEFAULT_CURATED_SOURCES: tuple[str, ...] = ("DISGENET", "ORPHANET", "CLINGEN", "OMIM")
DEFAULT_HIGH_SCORE = 0.6
DEFAULT_MIN_SCORE = 0.3
# Drug -> disease relations in the order a capped list prefers them.
DRUG_DISEASE_ORDER = {"INDICATION": 0, "OFF_LABEL_USE": 1, CONTRAINDICATION: 2}

# Upstream databases reached through OptimusKG whose terms docs/optimuskg-spike.md section 5
# checked: name (as spelled in `sources.direct` / `sources.indirect`) -> (version, license).
# HPO and GO are read from the ontology struct the backbone embeds in their nodes; every other
# value is recorded as UNVERIFIED (README section 5: "restrict commercial use, verify each").
UPSTREAM_VERIFIED: dict[str, tuple[str, str]] = {
    "DISGENET": ("7.0", "CC BY-NC-SA 4.0 (DisGeNET v7.0, January 2020; verified in docs/optimuskg-spike.md section 5)"),
    "DRUG_BANK": ("", "UNVERIFIED (DrugBank's own terms were not fetched; docs/optimuskg-spike.md section 5)"),
}
UPSTREAM_UNVERIFIED = "UNVERIFIED (per-source terms not fetched; docs/optimuskg-spike.md section 5)"
# `sources` value -> backbone type code whose nodes embed that ontology's {title, version, license}.
EMBEDDED_ONTOLOGY_SOURCES = {"HPO": "PHE", "GO": "BPO"}
COV_SOURCE_ENTITY = f"source:{SOURCE_NAME}"
COV_DANGLING = f"{SOURCE_NAME} dangling"

# Coverage source labels (what meta.coverage shows as "source").
COV_GENE = "OptimusKG gene"
COV_DISEASE = "OptimusKG disease"
COV_DRUG = "OptimusKG drug"
COV_DISEASE_GENE = "OptimusKG disease_gene"
COV_DISEASE_PHENOTYPE = "OptimusKG disease_phenotype"
COV_DISEASE_DISEASE = "OptimusKG disease_disease"
COV_PATHWAY_GENE = "OptimusKG pathway_gene"
COV_PROCESS_GENE = "OptimusKG biological_process_gene"
COV_DRUG_GENE = "OptimusKG drug_gene"
COV_DRUG_DISEASE = "OptimusKG drug_disease"

# Gap probe (module docstring "Gap probe"; cluster.yaml `gap_probe`). The four edge files whose
# gene side (`to`, an Ensembl id) must hold ZERO rows for the probe, keyed by the name the counts
# and the build report use, and the coverage source label each count is written under.
GAP_PROBE_EDGE_FILES: dict[str, str] = {
    "disease_gene": DISEASE_GENE,
    "drug_gene": DRUG_GENE,
    "pathway_gene": PATHWAY_GENE,
    "biological_process_gene": PROCESS_GENE,
}
GAP_PROBE_COVERAGE: dict[str, str] = {
    "disease_gene": COV_DISEASE_GENE,
    "drug_gene": COV_DRUG_GENE,
    "pathway_gene": COV_PATHWAY_GENE,
    "biological_process_gene": COV_PROCESS_GENE,
}
GAP_PROBE_FILES: tuple[str, ...] = (GENE_NODES, *GAP_PROBE_EDGE_FILES.values())
# The node prop that marks the probe (pipeline.graph.build exports such a node without edges).
GAP_PROBE_PROP = "gap_probe"
DEFAULT_GAP_PROBE_BIOTYPE = "protein_coding"
# "HGNC symbol" means: `properties.symbol` present, different from the node id (a gene without a
# symbol carries its own Ensembl id there), unique among gene nodes, and an xref of this source.
GAP_PROBE_XREF_SOURCE = "HGNC"
DEFAULT_GAP_PROBE_RULE = (
    "alphabetically first protein-coding HGNC symbol among backbone genes with zero "
    "disease_gene, drug_gene, pathway_gene and biological_process_gene rows"
)

# A few disease ids are "PREFIX_alnum" (OBA_VT…, NCIT_C…) rather than "PREFIX_digits", which
# pipeline.ids does not recognise; the same OBO rule (first underscore -> colon) applies.
_OBO_ALNUM = re.compile(r"^(?P<prefix>[A-Za-z][A-Za-z0-9]*)_(?P<local>[A-Za-z0-9.-]+)$")


class ChecksumMismatch(ValueError):
    """A downloaded file's md5 differs from the Dataverse metadata (the file was deleted)."""


class OfflineUnavailable(RuntimeError):
    """Offline and the metadata / raw files needed are not available locally."""


class GeneResolutionError(ValueError):
    """A seed gene symbol did not resolve to exactly one gene node."""


class GapProbeError(ValueError):
    """No backbone gene satisfies the gap-probe rule (cluster.yaml ``gap_probe``)."""


# --------------------------------------------------------------------------------------
# Dataset metadata
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class DatasetFile:
    """One file of the dataset as the Dataverse metadata describes it."""

    path: str
    file_id: int
    md5: str
    size: int


@dataclass(frozen=True)
class DatasetInfo:
    """The dataset version we are reading, with its license and file list."""

    doi: str
    version: str
    release_time: str
    license_name: str
    license_uri: str
    persistent_url: str
    retrieved_at: str
    files: dict[str, DatasetFile]

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        d["files"] = {p: asdict(f) for p, f in self.files.items()}
        return d

    @classmethod
    def from_json(cls, d: Mapping[str, Any]) -> DatasetInfo:
        files = {p: DatasetFile(**f) for p, f in d["files"].items()}
        return cls(**{k: v for k, v in d.items() if k != "files"}, files=files)


@dataclass(frozen=True)
class LocalFile:
    """A verified local copy of a dataset file. ``retrieved_at_source`` says where the timestamp
    comes from: ``"download"`` (this run), ``"manifest"`` (an earlier run) or ``"file mtime (not in
    manifest)"`` for a file that was put in place by hand; a retrieval time is never invented."""

    path: str
    local: Path
    md5: str
    size: int
    retrieved_at: str
    retrieved_at_source: str = "download"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_dataset(payload: Mapping[str, Any], retrieved_at: str) -> DatasetInfo:
    """Build :class:`DatasetInfo` from the Dataverse ``/api/datasets/:persistentId/`` JSON."""
    data = payload["data"]
    latest = data["latestVersion"]
    version = f"{latest['versionNumber']}.{latest.get('versionMinorNumber', 0)}"
    license_ = latest.get("license") or {}
    files: dict[str, DatasetFile] = {}
    for entry in latest["files"]:
        df = entry["dataFile"]
        directory = entry.get("directoryLabel") or df.get("directoryLabel") or ""
        path = f"{directory}/{df['filename']}" if directory else df["filename"]
        md5 = df.get("md5") or (df.get("checksum") or {}).get("value") or ""
        files[path] = DatasetFile(path=path, file_id=int(df["id"]), md5=md5.lower(), size=int(df["filesize"]))
    return DatasetInfo(
        doi=latest.get("datasetPersistentId") or DATASET_DOI,
        version=version,
        release_time=str(latest.get("releaseTime") or ""),
        license_name=str(license_.get("name") or DEFAULT_LICENSE),
        license_uri=str(license_.get("uri") or ""),
        persistent_url=str(data.get("persistentUrl") or ""),
        retrieved_at=retrieved_at,
        files=files,
    )


def fetch_dataset(http: CachedHttp | None = None) -> DatasetInfo:
    """The dataset metadata, through the disk cache (offline: cache hit or CacheMissOffline)."""
    client = http if http is not None else default_http()
    response = client.get_json(
        METADATA_URL, params={"persistentId": DATASET_DOI}, headers={"User-Agent": config.USER_AGENT}
    ).raise_for_status()
    return parse_dataset(response.json(), response.retrieved_at)


# --------------------------------------------------------------------------------------
# Download, checksum, manifest
# --------------------------------------------------------------------------------------


def md5_of(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def dataset_dir(raw_dir: Path, version: str) -> Path:
    return raw_dir / RAW_SUBDIR / version


def _download_client() -> httpx.Client:
    return httpx.Client(
        follow_redirects=True,
        timeout=httpx.Timeout(600.0, connect=30.0),
        headers={"User-Agent": config.USER_AGENT},
    )


def download_file(client: httpx.Client, file: DatasetFile, dest: Path, *, sleep=time.sleep) -> str:
    """Stream ``file`` to ``dest`` (through a ``.part`` file), verify its md5 and return it.

    Transport errors and 5xx answers are retried up to :data:`DOWNLOAD_ATTEMPTS` times. On an
    md5 mismatch the file is deleted and :class:`ChecksumMismatch` raised.
    """
    url = DATAFILE_URL.format(file_id=file.file_id)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
        digest = hashlib.md5()
        try:
            with client.stream("GET", url) as response:
                if response.status_code >= 500 and attempt < DOWNLOAD_ATTEMPTS:
                    raise httpx.TransportError(f"HTTP {response.status_code}")
                response.raise_for_status()
                with part.open("wb") as out:
                    for chunk in response.iter_bytes(CHUNK_BYTES):
                        digest.update(chunk)
                        out.write(chunk)
        except httpx.TransportError as exc:
            part.unlink(missing_ok=True)
            if attempt >= DOWNLOAD_ATTEMPTS:
                raise
            log.warning("optimuskg: %s attempt %d failed (%s); retrying", file.path, attempt, exc)
            sleep(2.0 ** (attempt - 1))
            continue
        actual = digest.hexdigest()
        if file.md5 and actual != file.md5:
            part.unlink(missing_ok=True)
            raise ChecksumMismatch(f"{file.path}: md5 {actual} != {file.md5} from the dataset metadata")
        part.replace(dest)
        return actual
    raise AssertionError("unreachable")


def read_manifest(directory: Path) -> dict[str, Any] | None:
    try:
        return json.loads((directory / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_manifest(directory: Path, info: DatasetInfo, files: Mapping[str, LocalFile]) -> None:
    existing = read_manifest(directory) or {}
    recorded = dict(existing.get("files") or {})
    for path, f in files.items():
        recorded[path] = {"md5": f.md5, "size": f.size, "retrieved_at": f.retrieved_at, "retrieved_at_source": f.retrieved_at_source}
    record = {"dataset": info.to_json(), "files": recorded}
    directory.mkdir(parents=True, exist_ok=True)
    tmp = directory / (MANIFEST_NAME + ".tmp")
    tmp.write_text(json.dumps(record, indent=1, sort_keys=True), encoding="utf-8")
    tmp.replace(directory / MANIFEST_NAME)


def ensure_files(
    info: DatasetInfo,
    paths: Sequence[str],
    *,
    raw_dir: Path,
    offline: bool,
    client: httpx.Client | None = None,
) -> dict[str, LocalFile]:
    """Make sure every path in ``paths`` is present under ``raw_dir`` with the metadata's md5.

    Present + matching files are reused (their ``retrieved_at`` from the manifest, or the file's
    own mtime, flagged ``retrieved_at_source = "file mtime (not in manifest)"``, when the manifest
    does not know them; the current time is never recorded for a file this run did not fetch).
    Missing or mismatching files are downloaded, unless ``offline``, which raises
    :class:`OfflineUnavailable`.
    """
    directory = dataset_dir(raw_dir, info.version)
    manifest = (read_manifest(directory) or {}).get("files") or {}
    missing = [p for p in paths if p not in info.files]
    if missing:
        raise KeyError(f"dataset {info.version} has no file(s) {missing}; names come from the metadata")
    result: dict[str, LocalFile] = {}
    to_fetch: list[DatasetFile] = []
    for path in paths:
        file = info.files[path]
        local = directory / path
        if local.is_file() and md5_of(local) == file.md5:
            known = manifest.get(path) or {}
            stat = local.stat()
            if known.get("retrieved_at"):
                retrieved_at, how = str(known["retrieved_at"]), str(known.get("retrieved_at_source") or "manifest")
            else:
                retrieved_at = datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(timespec="seconds")
                how = "file mtime (not in manifest)"
            result[path] = LocalFile(path, local, file.md5, stat.st_size, retrieved_at, how)
            continue
        if local.exists():
            log.warning("optimuskg: %s is present but its md5 does not match; re-downloading", path)
            local.unlink()
        to_fetch.append(file)
    if to_fetch:
        if offline:
            names = ", ".join(f.path for f in to_fetch)
            raise OfflineUnavailable(
                f"offline and {len(to_fetch)} OptimusKG file(s) are missing under {directory}: {names}"
            )
        own = client is None
        client = client if client is not None else _download_client()
        try:
            for file in to_fetch:
                t0 = time.monotonic()
                log.info("optimuskg: downloading %s (%d bytes)", file.path, file.size)
                md5 = download_file(client, file, directory / file.path)
                result[file.path] = LocalFile(file.path, directory / file.path, md5, file.size, _utc_now())
                log.info("optimuskg: %s done in %.1fs", file.path, time.monotonic() - t0)
        finally:
            if own:
                client.close()
    write_manifest(directory, info, result)
    return {p: result[p] for p in paths}


def _dataset_from_manifest(raw_dir: Path) -> DatasetInfo | None:
    """The newest manifest under ``raw_dir/optimuskg/*/`` as a :class:`DatasetInfo`."""
    root = raw_dir / RAW_SUBDIR
    if not root.is_dir():
        return None
    candidates = []
    for sub in root.iterdir():
        manifest = read_manifest(sub)
        if manifest and "dataset" in manifest:
            candidates.append((_version_key(sub.name), manifest["dataset"]))
    if not candidates:
        return None
    candidates.sort(key=lambda c: c[0])
    return DatasetInfo.from_json(candidates[-1][1])


def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(p) if p.isdigit() else 0 for p in version.split("."))


def resolve_files(
    paths: Sequence[str] = NEEDED_FILES,
    *,
    raw_dir: Path | None = None,
    offline: bool | None = None,
    http: CachedHttp | None = None,
    client: httpx.Client | None = None,
) -> tuple[DatasetInfo, dict[str, LocalFile]]:
    """Metadata (cached) + verified local files for ``paths``. Offline, nothing is fetched."""
    raw = Path(raw_dir) if raw_dir is not None else config.RAW_DIR
    off = is_offline() if offline is None else offline
    try:
        info = fetch_dataset(http)
    except CacheMissOffline:
        info = _dataset_from_manifest(raw)
        if info is None:
            raise OfflineUnavailable(
                "offline and the OptimusKG dataset metadata is neither cached nor recorded in a "
                f"manifest under {raw / RAW_SUBDIR}; run once online first"
            ) from None
    files = ensure_files(info, paths, raw_dir=raw, offline=off, client=client)
    return info, files


# --------------------------------------------------------------------------------------
# Row helpers
# --------------------------------------------------------------------------------------


def _curie(raw: str, hint: str | None = None) -> str:
    try:
        return normalize_curie(raw, hint)
    except ValueError:
        m = _OBO_ALNUM.match(raw.strip())
        if m is None:
            raise
        return f"{m['prefix']}:{m['local']}"


def _norm_term(text: str) -> str:
    return " ".join(text.split()).casefold()


def _dedupe(values: Iterable[Any]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        if isinstance(v, str) and v.strip() and v not in seen:
            seen.add(v)
            out.append(v)
    return out


def _labels(items: Any) -> list[str]:
    """``[{"label": ...}, ...]`` -> labels (gene symbol_synonyms / obsolete_symbols)."""
    return [i.get("label") for i in (items or []) if isinstance(i, Mapping)]


def _prop(name: str) -> pl.Expr:
    return pl.col("properties").struct.field(name)


def _struct_fields(lf: pl.LazyFrame) -> list[str]:
    dtype = lf.collect_schema()["properties"]
    return [f.name for f in dtype.fields] if isinstance(dtype, pl.Struct) else []


def _scan_nodes(path: Path, type_code: str) -> pl.LazyFrame:
    """Lazy scan of a node file, without the columns in :data:`PROPS_DROP` for that type."""
    lf = pl.scan_parquet(path)
    drop = PROPS_DROP.get(type_code, frozenset())
    fields = _struct_fields(lf)
    keep = [f for f in fields if f not in drop]
    if len(keep) != len(fields):
        lf = lf.select("id", "label", pl.struct([_prop(f) for f in keep]).alias("properties"))
    return lf


def _node_row(
    row: Mapping[str, Any],
    type_code: str,
    *,
    label: str | None,
    synonyms: Iterable[Any],
    retrieved_at: str,
    hint: str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    raw_id = row["id"]
    props = dict(row.get("properties") or {})
    props["backbone_id"] = raw_id
    props["source_type"] = row.get("label") or type_code
    if extra:
        props.update(extra)
    return {
        "id": _curie(raw_id, hint),
        "type": NODE_TYPES[type_code],
        "label": label.strip() if isinstance(label, str) and label.strip() else raw_id,
        "synonyms": _dedupe(synonyms),
        "props": props,
        "source": SOURCE_DB,
        "retrieved_at": retrieved_at,
    }


def _edge_row(
    source: str, target: str, relation: str, row: Mapping[str, Any], retrieved_at: str
) -> dict[str, Any]:
    props = dict(row.get("properties") or {})
    props["backbone_relation"] = row.get("relation")
    props["backbone_label"] = row.get("label")
    props["backbone_undirected"] = row.get("undirected")
    return {
        "source": source,
        "target": target,
        "relation": relation,
        "source_db": SOURCE_DB,
        "props": props,
        "retrieved_at": retrieved_at,
    }


def _synonyms(props: Mapping[str, Any]) -> list[Any]:
    out: list[Any] = []
    for key in SYNONYM_FIELDS:
        out.extend(props.get(key) or [])
    return out


def _passes_expr(thresholds: Mapping[str, Any]) -> pl.Expr:
    """The DIS-GEN inclusion rule: ``evidence_score >= disease_gene_min_score``, or DISGENET in
    ``sources.direct`` when ``keep_disgenet_direct`` (the single definition; the graph side
    re-evaluates the same rule in :func:`pipeline.graph.mechanisms.association_passes`)."""
    expr = _prop("evidence_score") >= float(thresholds["disease_gene_min_score"])
    if thresholds.get("keep_disgenet_direct"):
        expr = expr | _prop("sources").struct.field("direct").list.contains(DISGENET)
    return expr


def _curated_sources(thresholds: Mapping[str, Any]) -> set[str]:
    raw = thresholds.get("disease_gene_curated_sources")
    names = raw if isinstance(raw, (list, tuple, set)) else DEFAULT_CURATED_SOURCES
    return {str(s).strip().upper() for s in names if str(s).strip()}


def disease_gene_included(props: Mapping[str, Any], thresholds: Mapping[str, Any] | None = None) -> bool:
    """The EXPORT inclusion rule for a disease-gene edge (module docstring): a curated database
    among ``sources.direct`` AND ``evidence_score >= disease_gene_min_score``, OR
    ``evidence_score >= disease_gene_high_score``. ``sources`` may be the backbone struct
    (``{"direct": [...], "indirect": [...]}``) or a plain list. No ``evidence_score`` -> included
    (the edge is not an OptimusKG aggregate)."""
    t = thresholds or {}
    raw_score = props.get("evidence_score")
    if raw_score is None or isinstance(raw_score, bool):
        return True
    try:
        score = float(raw_score)
    except (TypeError, ValueError):
        return True
    min_score = float(t.get("disease_gene_min_score", DEFAULT_MIN_SCORE))
    high_score = float(t.get("disease_gene_high_score", DEFAULT_HIGH_SCORE))
    if score >= high_score:
        return True
    if score < min_score:
        return False
    sources = props.get("sources")
    direct = sources.get("direct") if isinstance(sources, Mapping) else sources
    direct_names = {str(s).strip().upper() for s in (direct or []) if isinstance(s, str)}
    return bool(direct_names & _curated_sources(t))


def inclusion_rule_text(thresholds: Mapping[str, Any] | None = None) -> str:
    """One line describing :func:`disease_gene_included` with the thresholds in force."""
    t = thresholds or {}
    curated = ", ".join(sorted(_curated_sources(t)))
    return (
        f"direct source in [{curated}] and evidence_score >= {float(t.get('disease_gene_min_score', DEFAULT_MIN_SCORE))}, "
        f"or evidence_score >= {float(t.get('disease_gene_high_score', DEFAULT_HIGH_SCORE))}"
    )


def drug_gene_relation(relation: str | None, props: Mapping[str, Any]) -> str:
    text = " ".join([relation or "", *(props.get("mechanisms_of_action") or [])])
    return REL_INHIBITS if INHIBITING_ACTION.search(text) else REL_TARGETS


def drug_disease_relation(relation: str | None) -> str:
    return REL_CONTRAINDICATED_FOR if (relation or "").upper() == CONTRAINDICATION else REL_INDICATED_FOR


def aspect_relation(aspect: Any) -> str | None:
    """Relation for a DIS-PHE row from its ``aspect`` (a list such as ``["P"]``, or a string):
    ``has_phenotype`` when "P" is among the codes, else the first mapped code's relation, else
    None (unmapped: the caller drops the row and records it)."""
    codes = [str(c).strip().upper() for c in ([aspect] if isinstance(aspect, str) else (aspect or [])) if c is not None]
    if ASPECT_PHENOTYPIC in codes:
        return REL_HAS_PHENOTYPE
    return next((ASPECT_RELATIONS[c] for c in codes if c in ASPECT_RELATIONS), None)


def _is_reference_id(value: str) -> bool:
    """A ``sources`` value spelled as a CURIE (``PMID:123``, ``GO_REF:0000002``,
    ``Reactome:R-HSA-...``) cites a record, not a database; database names are bare tokens."""
    return ":" in value


def _source_values(records: Iterable[Mapping[str, Any]]) -> Iterable[tuple[str, str]]:
    """``(kind, value)`` for every entry of ``properties.sources.direct`` / ``.indirect``."""
    for r in records:
        sources = (r.get("props") or {}).get("sources")
        if not isinstance(sources, Mapping):
            continue
        for kind in ("direct", "indirect"):
            for name in sources.get(kind) or []:
                if isinstance(name, str) and name.strip():
                    yield kind, name.strip()


def reference_sources(records: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, int]]:
    """``{prefix: {distinct, direct, indirect}}`` for the CURIE-form ``sources`` values (record
    references such as PMIDs or GO_REFs on GO-annotation edges): how many distinct ids of each
    prefix were cited and in how many records, so the build report shows them without turning
    each paper into an "upstream database" (see :func:`upstream_sources`)."""
    ids: dict[str, set[str]] = defaultdict(set)
    counts: dict[str, dict[str, int]] = defaultdict(lambda: {"direct": 0, "indirect": 0})
    for kind, value in _source_values(records):
        if _is_reference_id(value):
            prefix = value.split(":", 1)[0]
            ids[prefix].add(value)
            counts[prefix][kind] += 1
    return {p: {"distinct": len(ids[p]), **counts[p]} for p in sorted(ids)}


def upstream_sources(
    records: Iterable[Mapping[str, Any]], embedded: Mapping[str, Mapping[str, Any]]
) -> dict[str, dict[str, Any]]:
    """``{upstream name: {version, license, direct, indirect}}`` over the ``properties.sources``
    of the given node / edge rows (``direct`` / ``indirect`` count the records naming it). The
    license is the one :data:`UPSTREAM_VERIFIED` records, or the embedded ontology's own
    ``license`` / ``version`` for the sources in ``embedded`` (HPO, GO), else UNVERIFIED.
    CURIE-form values are record references, not databases, and are left to
    :func:`reference_sources`."""
    counts: dict[str, dict[str, int]] = defaultdict(lambda: {"direct": 0, "indirect": 0})
    for kind, value in _source_values(records):
        if not _is_reference_id(value):
            counts[value][kind] += 1
    out: dict[str, dict[str, Any]] = {}
    for name in sorted(counts):
        version, license_ = UPSTREAM_VERIFIED.get(name, ("", UPSTREAM_UNVERIFIED))
        meta = embedded.get(name)
        if meta:
            version = str(meta.get("version") or version)
            license_ = f"{meta.get('title') or name} license: {meta.get('license') or 'not stated'} (as embedded in OptimusKG, version {version or 'unknown'})"
        out[name] = {"version": version, "license": license_, **counts[name]}
    return out


def _ancestors(start: Iterable[str], parents_of: Mapping[str, Sequence[str]], levels: int | None) -> set[str]:
    """Nodes reachable upwards from ``start`` (``levels`` None = closure), excluding ``start``."""
    seen = set(start)
    frontier = set(start)
    out: set[str] = set()
    level = 0
    while frontier and (levels is None or level < levels):
        nxt: set[str] = set()
        for node in frontier:
            for parent in parents_of.get(node, ()):
                if parent not in seen:
                    seen.add(parent)
                    nxt.add(parent)
        out |= nxt
        frontier = nxt
        level += 1
    return out


def _parents_map(frame: pl.DataFrame) -> dict[str, list[str]]:
    """``to`` (child) -> [``from`` (parent), ...] for a PARENT edge frame."""
    parents: dict[str, list[str]] = defaultdict(list)
    for parent, child in zip(frame["from"].to_list(), frame["to"].to_list(), strict=True):
        parents[child].append(parent)
    return parents


def _children_map(frame: pl.DataFrame) -> dict[str, list[str]]:
    """``from`` (parent) -> [``to`` (child), ...] for a PARENT edge frame."""
    children: dict[str, list[str]] = defaultdict(list)
    for parent, child in zip(frame["from"].to_list(), frame["to"].to_list(), strict=True):
        children[parent].append(child)
    return children


def most_specific(pairs: Iterable[tuple[str, str]], children_of: Mapping[str, Sequence[str]]) -> set[str]:
    """Diseases from ``(disease, gene)`` pairs that no descendant explains for the same gene.

    Open Targets propagates associations up the ontology, so an umbrella term passes the
    threshold whenever a descendant does; keeping only the most specific term per gene undoes
    that roll-up. A disease is kept when it is specific for at least one gene.
    """
    by_gene: dict[str, set[str]] = defaultdict(set)
    for disease, gene in pairs:
        by_gene[gene].add(disease)
    descendants_cache: dict[str, set[str]] = {}

    def descendants(node: str) -> set[str]:
        if node not in descendants_cache:
            descendants_cache[node] = _ancestors([node], children_of, None)
        return descendants_cache[node]

    keep: set[str] = set()
    for gene, diseases in by_gene.items():
        for disease in diseases:
            if not (descendants(disease) & diseases):
                keep.add(disease)
    return keep


def cap_other_genes(rows: list[dict[str, Any]], seed_ids: set[str], limit: int | None) -> list[dict[str, Any]]:
    """Keep every row whose ``to`` is a seed gene plus, per ``from`` disease, the ``limit``
    strongest other-gene rows (``evidence_score`` desc, ``evidence_count`` desc, ``to`` asc)."""
    if limit is None or limit < 0:
        return rows
    kept = [r for r in rows if r["to"] in seed_ids]
    others: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        if r["to"] not in seed_ids:
            others[r["from"]].append(r)

    def rank(r: dict[str, Any]) -> tuple[float, float, str]:
        props = r.get("properties") or {}
        return (-(props.get("evidence_score") or 0.0), -(props.get("evidence_count") or 0), r["to"])

    for group in others.values():
        kept.extend(sorted(group, key=rank)[:limit])
    return kept


def cap_drugs_per_disease(rows: list[dict[str, Any]], keep_drugs: set[str], limit: int | None) -> list[dict[str, Any]]:
    """Keep every DRG-DIS row whose drug is in ``keep_drugs`` plus, per ``to`` disease, the
    ``limit`` best other rows (``highest_clinical_trial_phase`` desc with nulls last, then
    INDICATION < OFF_LABEL_USE < CONTRAINDICATION, then drug id)."""
    if limit is None or limit < 0:
        return rows
    kept = [r for r in rows if r["from"] in keep_drugs]
    others: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        if r["from"] not in keep_drugs:
            others[r["to"]].append(r)

    def rank(r: dict[str, Any]) -> tuple[float, int, str]:
        phase = (r.get("properties") or {}).get("highest_clinical_trial_phase")
        order = DRUG_DISEASE_ORDER.get((r.get("relation") or "").upper(), len(DRUG_DISEASE_ORDER))
        return (-(phase if phase is not None else -1.0), order, r["from"])

    for group in others.values():
        kept.extend(sorted(group, key=rank)[:limit])
    return kept


def clear_previous(store: Store) -> dict[str, int]:
    """Remove the rows a previous OptimusKG run wrote, so a re-run cannot leave stale data.
    The file is vacuumed when anything was removed (SQLite never shrinks on DELETE)."""
    with store.conn:
        n_nodes = store.conn.execute("DELETE FROM nodes WHERE source = ?", (SOURCE_DB,)).rowcount
        n_edges = store.conn.execute("DELETE FROM edges WHERE source_db = ?", (SOURCE_DB,)).rowcount
        n_cov = store.conn.execute("DELETE FROM coverage WHERE source LIKE ?", (f"{SOURCE_NAME} %",)).rowcount
    if (n_nodes or n_edges or n_cov) and store.path != ":memory:":
        store.conn.execute("VACUUM")
    return {"nodes": n_nodes, "edges": n_edges, "coverage": n_cov}


def _counts(values: Iterable[str]) -> dict[str, int]:
    out: dict[str, int] = defaultdict(int)
    for v in values:
        out[v] += 1
    return dict(out)


# --------------------------------------------------------------------------------------
# Slice
# --------------------------------------------------------------------------------------


def slice_into_store(
    store: Store, seeds: Mapping[str, Any], info: DatasetInfo, files: Mapping[str, LocalFile]
) -> dict[str, Any]:
    """Run the slice described in the module docstring and write it to ``store``.

    Returns a summary (seed resolution table, set sizes, counts, timings) for the build report.
    """
    timings: dict[str, float] = {}
    t_start = time.monotonic()

    def lap(name: str) -> None:
        timings[name] = round(time.monotonic() - t_start - sum(timings.values()), 3)

    thresholds = dict(seeds.get("thresholds") or {})
    thresholds.setdefault("disease_gene_min_score", 0.0)
    thresholds.setdefault("keep_disgenet_direct", False)
    thresholds.setdefault("include_parent_levels", 0)
    thresholds.setdefault("disease_gene_most_specific", DEFAULT_MOST_SPECIFIC)
    thresholds.setdefault("max_other_genes_per_disease", DEFAULT_MAX_OTHER_GENES)
    thresholds.setdefault("max_drugs_per_disease", DEFAULT_MAX_DRUGS)
    seed_symbols = [str(s).strip() for s in seeds.get("genes") or []]
    disease_terms = [str(t) for t in seeds.get("disease_terms") or []]
    drug_terms = [str(t) for t in seeds.get("drug_terms") or []]

    def when(path: str) -> str:
        return files[path].retrieved_at

    def date(path: str) -> str:
        return files[path].retrieved_at[:10]

    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    resolution: dict[str, Any] = {"genes": {}, "disease_terms": {}, "drug_terms": {}}
    cleared = clear_previous(store)
    lap("clear_previous")

    # -- 1. seed genes --------------------------------------------------------------------
    genes_lf = _scan_nodes(files[GENE_NODES].local, "GEN")
    seed_gene_rows = genes_lf.filter(_prop("symbol").is_in(seed_symbols)).collect().to_dicts()
    by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in seed_gene_rows:
        by_symbol[row["properties"]["symbol"]].append(row)
    problems = []
    for symbol in seed_symbols:
        hits = by_symbol.get(symbol, [])
        store.add_coverage(f"seed:gene:{symbol}", COV_GENE, symbol, date(GENE_NODES), len(hits))
        if len(hits) != 1:
            problems.append(f"{symbol!r} matched {len(hits)} gene nodes by properties.symbol")
    if problems:
        raise GeneResolutionError("; ".join(problems))
    seed_raw_ids: dict[str, str] = {s: by_symbol[s][0]["id"] for s in seed_symbols}
    seed_ids = list(seed_raw_ids.values())

    def gene_node(row: Mapping[str, Any]) -> dict[str, Any]:
        props = row.get("properties") or {}
        return _node_row(
            row,
            "GEN",
            label=props.get("symbol"),
            synonyms=_labels(props.get("symbol_synonyms")) + _labels(props.get("obsolete_symbols")),
            retrieved_at=when(GENE_NODES),
        )

    for row in seed_gene_rows:
        node = gene_node(row)
        nodes[node["id"]] = node
    lap("genes")

    # -- 2. disease-gene: evidence_score range, seed-gene edges, disease set ----------------
    dg_lf = pl.scan_parquet(files[DISEASE_GENE].local)
    score = _prop("evidence_score")
    stats = dg_lf.select(score.min().alias("min"), score.max().alias("max"), pl.len().alias("rows")).collect()
    score_stats = stats.row(0, named=True)
    passes = _passes_expr(thresholds)
    seed_dg = dg_lf.filter(pl.col("to").is_in(seed_ids)).with_columns(passes.alias("_passes")).collect()
    hits_by_gene = _counts(seed_dg["to"].to_list())
    for symbol, raw in seed_raw_ids.items():
        gid = _curie(raw)
        store.add_coverage(gid, COV_DISEASE_GENE, symbol, date(DISEASE_GENE), hits_by_gene.get(raw, 0))
        resolution["genes"][symbol] = {"node_id": gid, "backbone_id": raw, "disease_gene_edges": hits_by_gene.get(raw, 0)}
    passing = seed_dg.filter(pl.col("_passes"))
    passing_pairs = list(zip(passing["from"].to_list(), passing["to"].to_list(), strict=True))
    candidate_diseases = {d for d, _ in passing_pairs}
    dd = pl.read_parquet(files[DISEASE_DISEASE].local)
    if thresholds["disease_gene_most_specific"]:
        direct_diseases = most_specific(passing_pairs, _children_map(dd.select("from", "to")))
    else:
        direct_diseases = set(candidate_diseases)
    lap("disease_gene_seed")

    # -- 3. disease terms (name / synonym, case-insensitive) --------------------------------
    dis_lf = _scan_nodes(files[DISEASE_NODES].local, "DIS")
    dis_fields = set(_struct_fields(dis_lf))
    text_cols = [_prop("name").alias("name")] + [_prop(f).alias(f) for f in SYNONYM_FIELDS if f in dis_fields]
    names = dis_lf.select("id", *text_cols).collect()
    all_disease_ids = set(names["id"].to_list())
    term_index: dict[str, list[str]] = defaultdict(list)
    for row in names.to_dicts():
        texts = [row.get("name")] + [s for f in SYNONYM_FIELDS for s in (row.get(f) or [])]
        for text in texts:
            if isinstance(text, str) and text.strip():
                term_index[_norm_term(text)].append(row["id"])
    term_diseases: set[str] = set()
    for term in disease_terms:
        matched = _dedupe(term_index.get(_norm_term(term), []))
        store.add_coverage(f"seed:disease:{term}", COV_DISEASE, term, date(DISEASE_NODES), len(matched))
        resolution["disease_terms"][term] = [_curie(m) for m in matched]
        term_diseases |= set(matched)
    lap("disease_terms")

    # -- 4. parents (backbone PARENT: from = parent, to = child) -----------------------------
    disease_parents = _parents_map(dd.select("from", "to"))
    primary = direct_diseases | term_diseases
    levels = int(thresholds["include_parent_levels"])
    known_parents = {c: [p for p in ps if p in all_disease_ids] for c, ps in disease_parents.items()}
    parent_diseases = _ancestors(primary, known_parents, levels) if levels > 0 else set()
    included = primary | parent_diseases
    for raw in sorted(included):
        store.add_coverage(_curie(raw), COV_DISEASE_DISEASE, raw, date(DISEASE_DISEASE), len(known_parents.get(raw, [])))
    for row in dd.filter(pl.col("from").is_in(list(included)) & pl.col("to").is_in(list(included))).to_dicts():
        edges.append(_edge_row(_curie(row["to"]), _curie(row["from"]), REL_IS_A, row, when(DISEASE_DISEASE)))
    for row in dis_lf.filter(pl.col("id").is_in(list(included))).collect().to_dicts():
        props = row.get("properties") or {}
        node = _node_row(row, "DIS", label=props.get("name"), synonyms=_synonyms(props), retrieved_at=when(DISEASE_NODES))
        nodes[node["id"]] = node
    lap("diseases")

    # -- 5. disease-gene edges for included diseases (seed genes: all; others: threshold + cap) --
    dis_dg = dg_lf.filter(pl.col("from").is_in(list(included)) & (pl.col("to").is_in(seed_ids) | passes)).collect()
    dg_rows = cap_other_genes(dis_dg.to_dicts(), set(seed_ids), thresholds["max_other_genes_per_disease"])
    for row in dg_rows:
        edges.append(_edge_row(_curie(row["from"]), _curie(row["to"]), REL_ASSOCIATED_WITH, row, when(DISEASE_GENE)))
    other_gene_ids = sorted({r["to"] for r in dg_rows} - set(seed_ids))
    if other_gene_ids:
        for row in genes_lf.filter(pl.col("id").is_in(other_gene_ids)).collect().to_dicts():
            node = gene_node(row)
            nodes.setdefault(node["id"], node)
    lap("disease_gene_included")

    # -- 6. phenotypes: annotations by aspect, ancestors, is_a ------------------------------
    dp = pl.scan_parquet(files[DISEASE_PHENOTYPE].local).filter(pl.col("from").is_in(list(included))).collect()
    phe_hits = _counts(dp["from"].to_list())
    for raw in sorted(included):
        store.add_coverage(_curie(raw), COV_DISEASE_PHENOTYPE, raw, date(DISEASE_PHENOTYPE), phe_hits.get(raw, 0))
    unmapped_aspect: dict[str, int] = defaultdict(int)
    annotated: set[str] = set()
    for row in dp.to_dicts():
        rel = aspect_relation((row.get("properties") or {}).get("aspect"))
        if rel is None:
            unmapped_aspect[row["from"]] += 1
            continue
        edges.append(_edge_row(_curie(row["from"]), _curie(row["to"]), rel, row, when(DISEASE_PHENOTYPE)))
        annotated.add(row["to"])
    for raw, n in sorted(unmapped_aspect.items()):
        store.add_coverage(_curie(raw), COV_DISEASE_PHENOTYPE, f"{raw} annotations dropped: aspect not in {sorted(ASPECT_RELATIONS)}", date(DISEASE_PHENOTYPE), n)
    pp = pl.read_parquet(files[PHENOTYPE_PHENOTYPE].local)
    phenotypes = annotated | _ancestors(annotated, _parents_map(pp.select("from", "to")), None)
    for row in pp.filter(pl.col("from").is_in(list(phenotypes)) & pl.col("to").is_in(list(phenotypes))).to_dicts():
        edges.append(_edge_row(_curie(row["to"]), _curie(row["from"]), REL_IS_A, row, when(PHENOTYPE_PHENOTYPE)))
    embedded: dict[str, dict[str, Any]] = {}  # upstream name -> the ontology struct the backbone embeds in its nodes
    phe_lf = _scan_nodes(files[PHENOTYPE_NODES].local, "PHE")
    for row in phe_lf.filter(pl.col("id").is_in(list(phenotypes))).collect().to_dicts():
        props = row.get("properties") or {}
        node = _node_row(row, "PHE", label=props.get("name"), synonyms=_synonyms(props), retrieved_at=when(PHENOTYPE_NODES))
        nodes[node["id"]] = node
        if isinstance(props.get("ontology"), Mapping) and "HPO" not in embedded:
            embedded["HPO"] = dict(props["ontology"])
    lap("phenotypes")

    # -- 7. pathways (Reactome) and GO biological processes of the seed genes ----------------
    pg = pl.read_parquet(files[PATHWAY_GENE].local).filter(pl.col("to").is_in(seed_ids))
    bg = pl.read_parquet(files[PROCESS_GENE].local).filter(pl.col("to").is_in(seed_ids))
    pg_hits, bg_hits = _counts(pg["to"].to_list()), _counts(bg["to"].to_list())
    for symbol, raw in seed_raw_ids.items():
        store.add_coverage(_curie(raw), COV_PATHWAY_GENE, symbol, date(PATHWAY_GENE), pg_hits.get(raw, 0))
        store.add_coverage(_curie(raw), COV_PROCESS_GENE, symbol, date(PROCESS_GENE), bg_hits.get(raw, 0))
    for row in pg.to_dicts():
        edges.append(_edge_row(_curie(row["to"]), _curie(row["from"]), REL_IN_PATHWAY, row, when(PATHWAY_GENE)))
    for row in bg.to_dicts():
        edges.append(_edge_row(_curie(row["to"]), _curie(row["from"]), REL_IN_PATHWAY, row, when(PROCESS_GENE)))
    pathway_ids = sorted(set(pg["from"].to_list()))
    if pathway_ids:
        lf = _scan_nodes(files[PATHWAY_NODES].local, "PWY").filter(pl.col("id").is_in(pathway_ids))
        for row in lf.collect().to_dicts():
            props = row.get("properties") or {}
            node = _node_row(
                row, "PWY", label=props.get("name"), synonyms=[], retrieved_at=when(PATHWAY_NODES), extra={"ontology": "Reactome"}
            )
            nodes[node["id"]] = node
    process_ids = sorted(set(bg["from"].to_list()))
    if process_ids:
        lf = _scan_nodes(files[PROCESS_NODES].local, "BPO").filter(pl.col("id").is_in(process_ids))
        for row in lf.collect().to_dicts():
            props = row.get("properties") or {}
            # The GO node's own `ontology` struct (title, version, license) moves to
            # `ontology_meta`; `ontology` is the contract's "GO" / "Reactome" tag.
            extra = {"ontology": "GO", "ontology_meta": props.get("ontology")}
            node = _node_row(
                row, "BPO", label=props.get("name"), synonyms=props.get("synonyms") or [], retrieved_at=when(PROCESS_NODES), extra=extra
            )
            nodes[node["id"]] = node
            if isinstance(props.get("ontology"), Mapping) and "GO" not in embedded:
                embedded["GO"] = dict(props["ontology"])
    lap("pathways")

    # -- 8. drugs: seed-gene targets, included-disease indications, seed drug terms ----------
    drg_gene = pl.read_parquet(files[DRUG_GENE].local).filter(pl.col("to").is_in(seed_ids))
    drg_dis = pl.read_parquet(files[DRUG_DISEASE].local).filter(pl.col("to").is_in(list(included)))
    dg_hits, dd_hits = _counts(drg_gene["to"].to_list()), _counts(drg_dis["to"].to_list())
    for symbol, raw in seed_raw_ids.items():
        store.add_coverage(_curie(raw), COV_DRUG_GENE, symbol, date(DRUG_GENE), dg_hits.get(raw, 0))
    for raw in sorted(included):
        store.add_coverage(_curie(raw), COV_DRUG_DISEASE, raw, date(DRUG_DISEASE), dd_hits.get(raw, 0))
    for row in drg_gene.to_dicts():
        rel = drug_gene_relation(row.get("relation"), row.get("properties") or {})
        edges.append(_edge_row(_curie(row["from"], DRUG_NAMESPACE_HINT), _curie(row["to"]), rel, row, when(DRUG_GENE)))
    drug_lf = _scan_nodes(files[DRUG_NODES].local, "DRG")
    drug_fields = set(_struct_fields(drug_lf))
    name_cols = [_prop("name").alias("name")] + [_prop(f).alias(f) for f in ("synonyms", "trade_names") if f in drug_fields]
    drug_index: dict[str, list[str]] = defaultdict(list)
    for row in drug_lf.select("id", *name_cols).collect().to_dicts():
        texts = [row.get("name")] + list(row.get("synonyms") or []) + list(row.get("trade_names") or [])
        for text in texts:
            if isinstance(text, str) and text.strip():
                drug_index[_norm_term(text)].append(row["id"])
    seed_drugs = set(drg_gene["from"].to_list())
    for term in drug_terms:
        matched = _dedupe(drug_index.get(_norm_term(term), []))
        store.add_coverage(f"seed:drug:{term}", COV_DRUG, term, date(DRUG_NODES), len(matched))
        resolution["drug_terms"][term] = [_curie(m, DRUG_NAMESPACE_HINT) for m in matched]
        seed_drugs |= set(matched)
    dd_rows = cap_drugs_per_disease(drg_dis.to_dicts(), seed_drugs, thresholds["max_drugs_per_disease"])
    for row in dd_rows:
        rel = drug_disease_relation(row.get("relation"))
        edges.append(_edge_row(_curie(row["from"], DRUG_NAMESPACE_HINT), _curie(row["to"]), rel, row, when(DRUG_DISEASE)))
    drug_ids = seed_drugs | {r["from"] for r in dd_rows}
    if drug_ids:
        for row in drug_lf.filter(pl.col("id").is_in(sorted(drug_ids))).collect().to_dicts():
            props = row.get("properties") or {}
            synonyms = list(props.get("synonyms") or []) + list(props.get("trade_names") or [])
            node = _node_row(
                row, "DRG", label=props.get("name"), synonyms=synonyms, retrieved_at=when(DRUG_NODES), hint=DRUG_NAMESPACE_HINT
            )
            nodes[node["id"]] = node
    lap("drugs")

    # -- 9. write ---------------------------------------------------------------------------
    retrieved = max((f.retrieved_at for f in files.values()), default=info.retrieved_at)
    kept, dropped = [], defaultdict(int)
    for e in edges:
        if e["source"] in nodes and e["target"] in nodes and e["source"] != e["target"]:
            kept.append(e)
        else:
            dropped[e["relation"]] += 1
    for relation, n in sorted(dropped.items()):  # the drop is data, not a log line (hard rule 3)
        store.add_coverage(COV_SOURCE_ENTITY, COV_DANGLING, f"{relation} edges dropped: endpoint not a node in the slice", retrieved[:10], n)
    if dropped:
        log.warning("optimuskg: dropped edges whose endpoint is not a node in the slice: %s", dict(dropped))
    store.upsert_nodes(nodes.values())
    store.upsert_edges(kept)
    upstream = upstream_sources([*nodes.values(), *kept], {k: v for k, v in embedded.items() if k in EMBEDDED_ONTOLOGY_SOURCES})
    references = reference_sources([*nodes.values(), *kept])
    notes = {
        "doi": info.doi,
        "release_time": info.release_time,
        "license_uri": info.license_uri,
        "persistent_url": info.persistent_url,
        "metadata_retrieved_at": info.retrieved_at,
        "files": {p: {"id": info.files[p].file_id, "md5": f.md5, "size": f.size, "retrieved_at": f.retrieved_at, "retrieved_at_source": f.retrieved_at_source} for p, f in files.items()},
        "evidence_score": score_stats,
        "thresholds": thresholds,
        "seed_resolution": resolution,
        "upstream_sources": upstream,
        "upstream_reference_ids": references,
    }
    store.upsert_source(
        SOURCE_NAME,
        version=info.version,
        retrieved_at=retrieved,
        license=info.license_name or DEFAULT_LICENSE,
        url=info.persistent_url,
        notes=notes,
    )
    lap("write")

    return {
        "dataset": {"doi": info.doi, "version": info.version, "release_time": info.release_time, "license": info.license_name, "retrieved_at": retrieved},
        "files": {p: str(f.local) for p, f in files.items()},
        "thresholds": thresholds,
        "evidence_score": score_stats,
        "seed_resolution": resolution,
        "diseases": {
            "passing_before_specificity": len(candidate_diseases),
            "direct": len(direct_diseases),
            "term_matched": len(term_diseases),
            "parents": len(parent_diseases),
            "included": len(included),
        },
        "disease_gene_rows": {"before_cap": dis_dg.height, "after_cap": len(dg_rows)},
        "drug_disease_rows": {"before_cap": drg_dis.height, "after_cap": len(dd_rows)},
        "phenotypes": {"annotated": len(annotated), "with_ancestors": len(phenotypes), "unmapped_aspect_rows": sum(unmapped_aspect.values())},
        "nodes_by_type": _counts(n["type"] for n in nodes.values()),
        "edges_by_relation": _counts(e["relation"] for e in kept),
        "dropped_dangling_edges": dict(dropped),
        "upstream_sources": upstream,
        "upstream_reference_ids": references,
        "cleared_previous_run": cleared,
        "timings_s": timings,
    }


# --------------------------------------------------------------------------------------
# Gap probe (ground-truth test 6)
# --------------------------------------------------------------------------------------


def _local_path(entry: LocalFile | Path | str) -> Path:
    return Path(entry.local) if isinstance(entry, LocalFile) else Path(entry)


def _retrieved_at(entry: LocalFile | Path | str) -> str:
    """The file's recorded retrieval time, or its mtime for a bare path (never the current time)."""
    if isinstance(entry, LocalFile):
        return entry.retrieved_at
    stat = Path(entry).stat()
    return datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(timespec="seconds")


def select_gap_probe(files: Mapping[str, LocalFile | Path | str], cfg: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Choose the gap-probe gene (module docstring "Gap probe") from the WHOLE backbone.

    ``files`` maps at least every path in :data:`GAP_PROBE_FILES` to its local file
    (:class:`LocalFile` from :func:`resolve_files`, or a bare path). ``cfg`` is the
    ``gap_probe`` block of ``pipeline/seeds/cluster.yaml``: ``biotype`` (default
    :data:`DEFAULT_GAP_PROBE_BIOTYPE`) and ``rule`` (the sentence recorded with the result; default
    :data:`DEFAULT_GAP_PROBE_RULE`). The rule itself is fixed here:

    1. candidate genes are the gene nodes whose ``properties.biotype`` equals ``biotype`` and whose
       ``properties.symbol`` is an HGNC symbol (present, different from the node id, unique among
       all gene nodes, with an xref whose source is :data:`GAP_PROBE_XREF_SOURCE`);
    2. a candidate must have ZERO rows in every file of :data:`GAP_PROBE_EDGE_FILES` (gene side:
       the ``to`` column, lazy scans reading only that column; the files are never materialised);
    3. the survivor with the alphabetically first ``(symbol, id)`` wins (plain string order, so two
       builds of one dataset choose the same gene).

    Returns ``{symbol, gene_id, backbone_id, name, biotype, counts, file_dates, rule, candidates,
    node}``: ``counts`` are the rows actually counted for the chosen gene in each edge file (all 0
    by construction, recorded so coverage rows are measured, not assumed), ``file_dates`` the
    retrieval date of each edge file (for those coverage rows), ``candidates`` how many genes
    passed steps 1 and 2, and ``node`` the store-ready Gene row (``props.gap_probe = true``,
    ``props.gap_probe_rule``, ``props.gap_probe_counts``; backbone props as the loader keeps them).
    Raises :class:`GapProbeError` when nothing qualifies. No symbol appears in code or seeds.
    """
    cfg = dict(cfg or {})
    biotype = str(cfg.get("biotype") or DEFAULT_GAP_PROBE_BIOTYPE)
    rule = str(cfg.get("rule") or DEFAULT_GAP_PROBE_RULE)
    missing = [p for p in GAP_PROBE_FILES if p not in files]
    if missing:
        raise KeyError(f"gap probe needs the files {missing}")

    genes_lf = _scan_nodes(_local_path(files[GENE_NODES]), "GEN")
    fields = set(_struct_fields(genes_lf))
    symbol_col = pl.col("properties").struct.field("symbol")
    has_xref = (
        pl.col("properties").struct.field("xrefs").list.eval(pl.element().struct.field("source")).list.contains(GAP_PROBE_XREF_SOURCE)
        if "xrefs" in fields
        else pl.lit(False)
    )
    # symbols shared by more than one gene node are not a resolvable HGNC symbol (seed rule, exit criterion 1)
    symbol_counts = genes_lf.select(symbol_col.alias("symbol")).drop_nulls().group_by("symbol").len()
    typed = (
        genes_lf.filter(
            (pl.col("properties").struct.field("biotype") == biotype)
            & symbol_col.is_not_null()
            & (symbol_col != pl.col("id"))
            & has_xref
        )
        .with_columns(symbol_col.alias("symbol"))
        .join(symbol_counts, on="symbol", how="left")
        .filter(pl.col("len") == 1)
        .select("id", "symbol")
        .collect()
    )
    connected: set[str] = set()
    for path in GAP_PROBE_EDGE_FILES.values():
        connected |= set(pl.scan_parquet(_local_path(files[path])).select(pl.col("to").unique()).collect()["to"].to_list())
    unconnected = sorted(
        ((row["symbol"], row["id"]) for row in typed.iter_rows(named=True) if row["id"] not in connected),
    )
    if not unconnected:
        raise GapProbeError(f"no {biotype} gene with an HGNC symbol has zero rows in {sorted(GAP_PROBE_EDGE_FILES)}")
    symbol, raw_id = unconnected[0]

    counts: dict[str, int] = {}
    file_dates: dict[str, str] = {}
    for key, path in GAP_PROBE_EDGE_FILES.items():
        counts[key] = int(pl.scan_parquet(_local_path(files[path])).filter(pl.col("to") == raw_id).select(pl.len()).collect().item())
        file_dates[key] = _retrieved_at(files[path])[:10]

    row = genes_lf.filter(pl.col("id") == raw_id).collect().to_dicts()[0]
    props = row.get("properties") or {}
    node = _node_row(
        row,
        "GEN",
        label=props.get("symbol"),
        synonyms=_labels(props.get("symbol_synonyms")) + _labels(props.get("obsolete_symbols")),
        retrieved_at=_retrieved_at(files[GENE_NODES]),
        extra={GAP_PROBE_PROP: True, f"{GAP_PROBE_PROP}_rule": rule, f"{GAP_PROBE_PROP}_counts": dict(counts)},
    )
    return {
        "symbol": symbol,
        "gene_id": node["id"],
        "backbone_id": raw_id,
        "name": props.get("name"),
        "biotype": biotype,
        "counts": counts,
        "file_dates": file_dates,
        "rule": rule,
        "candidates": len(unconnected),
        "node": node,
    }


# --------------------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------------------


def load(
    store: Store,
    seeds: Mapping[str, Any],
    *,
    offline: bool = False,
    raw_dir: Path | None = None,
    http: CachedHttp | None = None,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Download (once) and slice OptimusKG into ``store``. Idempotent; returns the summary.

    ``offline=True`` or ``ATLAS_OFFLINE=1`` forbids every network call. ``raw_dir`` defaults to
    :data:`pipeline.config.RAW_DIR`; ``http`` / ``client`` are injectable for tests.
    """
    t0 = time.monotonic()
    info, files = resolve_files(NEEDED_FILES, raw_dir=raw_dir, offline=offline or is_offline(), http=http, client=client)
    t1 = time.monotonic()
    summary = slice_into_store(store, seeds, info, files)
    summary["timings_s"]["resolve_files"] = round(t1 - t0, 3)
    summary["timings_s"]["total"] = round(time.monotonic() - t0, 3)
    return summary
