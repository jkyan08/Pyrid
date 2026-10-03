"""HPO: phenotype information content over every HPO-annotated disease, plus the HP ontology.

README section 5: "Phenotype information content is computed across ALL HPO-annotated diseases,
not just our slice", so broad symptoms count for little and distinctive ones a lot when
similarity (``pipeline/graph/similarity.py``) weights phenotype overlap.

Files (docs/source-apis.md section 4, verified 2026-10-03)
----------------------------------------------------------
* ``phenotype.hpoa``: :data:`HPOA_URL` (a PURL that redirects to the GitHub release asset).
  ``#`` header lines carry ``#version: YYYY-MM-DD`` and ``#hpo-version: <release IRI of hp.json>``,
  then a header row and 12 tab columns (:data:`HPOA_COLUMNS`).
* ``hp.json`` (OBO Graphs): the release the hpoa header points at, falling back to
  :data:`HP_JSON_URL`. Edge keys are ``sub`` / ``pred`` / ``obj`` (not ``subj``); node ids are IRIs
  (``.../obo/HP_0001234``), normalised to colon CURIEs with :func:`pipeline.ids.normalize_curie`.

Both files are streamed once into ``<RAW_DIR>/hpo/<hpoa version>/`` (``pipeline.cache`` is text
and JSON only). ``manifest.json`` next to them records each file's URL, final URL, sha256, byte
count and UTC ``retrieved_at``. Later runs, and ``ATLAS_OFFLINE=1``, reuse the files after
re-checking size and sha256 and never fetch again; offline with no complete local copy raises
``FileNotFoundError``. No checksum is published for these assets, so the check is against the
value recorded at download time (plus ``Content-Length`` while downloading).

Information content
-------------------
Rows kept: ``aspect == "P"`` (phenotypic abnormality) and ``qualifier != "NOT"``. Annotations
whose frequency says the feature was looked for and absent (``0/n``, ``0%``, or the frequency
term labelled "Excluded" in hp.json) are not counted either (docs/source-apis.md section 4c,
item 9). ``N_total`` is the number of distinct ``database_id`` values with at least one kept
annotation; ``ORPHA:`` ids are spelled ``Orphanet:`` as MONDO does. For a term ``t``, ``D(t)``
is the union of the disease sets of ``t`` and all its ``is_a`` descendants, and
``IC(t) = -ln(|D(t)| / N_total)``. Terms never annotated get ``-ln(1 / N_total)`` with
``n_diseases = 0`` (maximally specific, but flagged). Obsolete terms that carry a replacement
(``IAO_0100001``) are counted under the replacement. All evidence codes (PCS, TAS, IEA) count
toward IC; this module exports no annotation as an edge.

IC is defined only for the "Phenotypic abnormality" subtree (found by label, like the "Excluded"
frequency term): a mode of inheritance, a clinical modifier or an onset term is annotated to
thousands of diseases in phenotype.hpoa under another aspect, so giving it the "never annotated"
maximum would make the most common HPO terms look the most specific. Such terms get no row;
a Phenotype node outside the subtree therefore has no ``props.ic`` (a visible gap, never a
fabricated maximum). Ancestors of annotated terms (the root "All") keep their true value.

License
-------
HPO is under a custom license, not CC BY: cite the HPO Consortium, display the version(s) of
the files used, do not alter their content, and add the attribution statement to the site. IEA
rows are parsed from OMIM and the HPO docs say OMIM must be contacted to reuse them in other
software products. Both are recorded on the ``HPO`` row of the store's ``sources`` table.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
import time
from collections import defaultdict
from collections.abc import Callable, Collection, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import polars as pl

from pipeline import config
from pipeline.cache import is_offline
from pipeline.ids import normalize_curie
from pipeline.store import Store

__all__ = [
    "ATTRIBUTION",
    "HPOA_COLUMNS",
    "HPOA_URL",
    "HP_JSON_URL",
    "LICENSE",
    "SOURCE_NAME",
    "Annotations",
    "HpoFiles",
    "HpoaHeader",
    "Ontology",
    "PHENOTYPIC_ABNORMALITY_LABEL",
    "ancestors",
    "compute_ic",
    "fetch_files",
    "find_local",
    "get_ontology",
    "iri_to_curie",
    "load",
    "load_ontology",
    "normalize_disease_id",
    "parse_hpoa_header",
    "phenotypic_domain",
    "read_annotations",
]

SOURCE_NAME = "HPO"
HPOA_URL = "https://purl.obolibrary.org/obo/hp/hpoa/phenotype.hpoa"
HP_JSON_URL = "https://purl.obolibrary.org/obo/hp.json"
HPOA_FILENAME = "phenotype.hpoa"
HP_JSON_FILENAME = "hp.json"
MANIFEST_FILENAME = "manifest.json"
RAW_SUBDIR = "hpo"
INCOMING_SUBDIR = ".incoming"
COVERAGE_ENTITY = "source:HPO"

# docs/source-apis.md section 5 (license table), quoted as written there.
LICENSE = "HPO custom (cite, show version, don't alter; IEA rows: OMIM reuse caveat)"
LICENSE_URL = "https://hpo.jax.org/license"
LICENSE_URL_NOTE = (
    "The license page renders client-side and answers HTTP 404 to non-browser clients; cite it, "
    "do not check its status code."
)
ATTRIBUTION = (
    "This service/product uses the Human Phenotype Ontology (version {version}). "
    "Find out more at http://www.human-phenotype-ontology.org"
)
IEA_CAVEAT = (
    "IEA annotations are parsed from the Clinical Features sections of OMIM; the HPO docs say "
    "'you need to contact OMIM in order to reuse these annotations in other software products'. "
    "This loader counts them toward information content only and exports no annotation as an edge."
)

HPOA_COLUMNS = (
    "database_id",
    "disease_name",
    "qualifier",
    "hpo_id",
    "reference",
    "evidence",
    "onset",
    "frequency",
    "sex",
    "modifier",
    "aspect",
    "biocuration",
)
ASPECT_PHENOTYPIC = "P"
QUALIFIER_NOT = "NOT"
EXCLUDED_FREQUENCY_LABEL = "Excluded"  # the HPO frequency term for "looked for and absent (0%)"
PHENOTYPIC_ABNORMALITY_LABEL = "Phenotypic abnormality"  # the subtree information content is defined on
# Frequency formats meaning "absent": counts ``0/n`` and percentages ``0%`` (``n/m``, ``\d+%`` and
# HP frequency terms are the three documented formats).
_ZERO_FREQUENCY = re.compile(r"^0+(?:/\d+|%)$")

# Id spellings: phenotype.hpoa writes ``ORPHA:``, MONDO / SSSOM write ``Orphanet:``.
ORPHA_PREFIX = "ORPHA:"
ORPHANET_PREFIX = "Orphanet:"

# OBO Graphs vocabulary (predicate names, not record ids).
IS_A_PREDICATES = frozenset({"is_a", "subClassOf"})
REPLACED_BY_PREDICATE = "IAO_0100001"  # "term replaced by", on obsolete terms
VERSION_INFO_PREDICATE = "versionInfo"  # owl:versionInfo on the graph meta
NODE_TYPE_CLASS = "CLASS"

_HEADER_VERSION = re.compile(r"^#version:\s*(\S+)")
_HEADER_HPO_VERSION = re.compile(r"^#hpo-version:\s*(\S+)")
_RELEASE_IN_IRI = re.compile(r"/releases/([^/]+)/")

DOWNLOAD_ATTEMPTS = 3
DOWNLOAD_BACKOFF = 0.5
CHUNK_BYTES = 1 << 20


# --------------------------------------------------------------------------------------
# identifiers
# --------------------------------------------------------------------------------------


def _local_name(iri: str) -> str:
    return iri.rsplit("/", 1)[-1].rsplit("#", 1)[-1]


def iri_to_curie(iri: str) -> str:
    """``http://purl.obolibrary.org/obo/HP_0001234`` -> ``HP:0001234``; CURIEs pass through
    :func:`normalize_curie`; anything that cannot be normalised is returned as given."""
    value = iri.strip()
    candidate = _local_name(value) if value.startswith(("http://", "https://")) else value
    try:
        return normalize_curie(candidate)
    except ValueError:
        return value


def normalize_disease_id(raw: str) -> str:
    """phenotype.hpoa ``database_id`` with ``ORPHA:`` spelled ``Orphanet:``; other prefixes kept."""
    value = raw.strip()
    if value.startswith(ORPHA_PREFIX):
        return ORPHANET_PREFIX + value[len(ORPHA_PREFIX) :]
    return normalize_curie(value)


# --------------------------------------------------------------------------------------
# ontology
# --------------------------------------------------------------------------------------


class Ontology:
    """The HP ontology from an OBO Graphs ``hp.json``: labels, ``is_a`` parents and children,
    obsolete terms with their replacements, and memoised ancestor / descendant closures.
    Everything is dicts of sets; nothing else from the file is kept."""

    def __init__(
        self,
        parents: Mapping[str, Iterable[str]],
        labels: Mapping[str, str] | None = None,
        *,
        deprecated: Iterable[str] = (),
        replaced_by: Mapping[str, str] | None = None,
        version: str = "",
    ) -> None:
        self.parents: dict[str, set[str]] = defaultdict(set)
        self.children: dict[str, set[str]] = defaultdict(set)
        self.labels: dict[str, str] = dict(labels or {})
        self.deprecated: set[str] = set(deprecated)
        self.replaced_by: dict[str, str] = dict(replaced_by or {})
        self.version = version
        terms: set[str] = set(self.labels) | self.deprecated
        for child, ps in parents.items():
            terms.add(child)
            for parent in ps:
                if parent == child:
                    continue
                self.parents[child].add(parent)
                self.children[parent].add(child)
                terms.add(parent)
        self._terms = frozenset(terms)
        self._ancestors: dict[str, frozenset[str]] = {}
        self._descendants: dict[str, frozenset[str]] = {}

    # -- construction -------------------------------------------------------------------

    @classmethod
    def from_obographs(cls, data: Mapping[str, Any]) -> Ontology:
        """Build from a parsed OBO Graphs document (``{"graphs": [{"nodes", "edges", "meta"}]}``)."""
        graphs = data.get("graphs") or []
        if not graphs:
            raise ValueError("OBO Graphs document has no 'graphs' entry")
        graph = graphs[0]
        labels: dict[str, str] = {}
        deprecated: set[str] = set()
        replaced_by: dict[str, str] = {}
        for node in graph.get("nodes") or []:
            # Only nodes declared CLASS are terms: PROPERTY nodes and the untyped relation
            # declarations (``has part``, ``towards``, ...) in the real hp.json are not.
            if node.get("type") != NODE_TYPE_CLASS or not node.get("id"):
                continue
            term = iri_to_curie(str(node["id"]))
            label = node.get("lbl")
            if isinstance(label, str) and label.strip():
                labels[term] = label
            meta = node.get("meta") or {}
            if meta.get("deprecated"):
                deprecated.add(term)
            for bpv in meta.get("basicPropertyValues") or []:
                if _local_name(str(bpv.get("pred", ""))) == REPLACED_BY_PREDICATE and bpv.get("val"):
                    replaced_by[term] = iri_to_curie(str(bpv["val"]))
        parents: dict[str, set[str]] = defaultdict(set)
        for edge in graph.get("edges") or []:
            if _local_name(str(edge.get("pred", ""))) not in IS_A_PREDICATES:
                continue
            sub, obj = edge.get("sub"), edge.get("obj")
            if sub and obj:
                parents[iri_to_curie(str(sub))].add(iri_to_curie(str(obj)))
        meta = graph.get("meta") or {}
        version = ""
        for bpv in meta.get("basicPropertyValues") or []:
            if _local_name(str(bpv.get("pred", ""))) == VERSION_INFO_PREDICATE and bpv.get("val"):
                version = str(bpv["val"])
        if not version:
            m = _RELEASE_IN_IRI.search(str(meta.get("version", "")))
            version = m.group(1) if m else ""
        return cls(parents, labels, deprecated=deprecated, replaced_by=replaced_by, version=version)

    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> Ontology:
        with Path(path).open("rb") as fh:
            return cls.from_obographs(json.load(fh))

    # -- lookups ------------------------------------------------------------------------

    @property
    def terms(self) -> frozenset[str]:
        """Every term id seen as a class node or an ``is_a`` endpoint, obsolete ones included."""
        return self._terms

    @property
    def active_terms(self) -> frozenset[str]:
        return self._terms - self.deprecated

    def __contains__(self, term: object) -> bool:
        return term in self._terms

    def __len__(self) -> int:
        return len(self._terms)

    def label(self, term: str) -> str:
        return self.labels.get(term, "")

    def terms_with_label(self, label: str) -> set[str]:
        """Non-obsolete terms whose label equals ``label`` exactly."""
        return {t for t, lbl in self.labels.items() if lbl == label and t not in self.deprecated}

    def resolve(self, term: str) -> str:
        """Follow ``replaced_by`` from an obsolete term to its current replacement (bounded)."""
        current = term
        for _ in range(10):
            nxt = self.replaced_by.get(current)
            if nxt is None or nxt == current:
                break
            current = nxt
        return current

    def ancestors(self, term: str, include_self: bool = True) -> frozenset[str]:
        """All ``is_a`` ancestors of ``term`` (transitive); unknown terms have none."""
        closure = self._ancestors.get(term)
        if closure is None:
            closure = self._closure(term, self.parents, self._ancestors)
        return closure | {term} if include_self else closure

    def descendants(self, term: str, include_self: bool = True) -> frozenset[str]:
        """All ``is_a`` descendants of ``term`` (transitive)."""
        closure = self._descendants.get(term)
        if closure is None:
            closure = self._closure(term, self.children, self._descendants)
        return closure | {term} if include_self else closure

    @staticmethod
    def _closure(
        term: str, step: Mapping[str, set[str]], memo: dict[str, frozenset[str]]
    ) -> frozenset[str]:
        seen: set[str] = set()
        stack = list(step.get(term, ()))
        while stack:
            node = stack.pop()
            if node in seen or node == term:
                continue
            seen.add(node)
            cached = memo.get(node)
            if cached is not None:
                seen |= cached
            else:
                stack.extend(step.get(node, ()))
        seen.discard(term)
        closure = frozenset(seen)
        memo[term] = closure
        return closure


_ONTOLOGIES: dict[Path, Ontology] = {}
_ONTOLOGY_LOCK = threading.Lock()


def load_ontology(path: str | os.PathLike[str]) -> Ontology:
    """:meth:`Ontology.load` cached per file path for the life of the process."""
    key = Path(path).resolve()
    with _ONTOLOGY_LOCK:
        ontology = _ONTOLOGIES.get(key)
        if ontology is None:
            ontology = _ONTOLOGIES[key] = Ontology.load(key)
    return ontology


# --------------------------------------------------------------------------------------
# files on disk
# --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class HpoFiles:
    """One HPO download: ``<raw_dir>/hpo/<version>/`` holding phenotype.hpoa, hp.json and the
    manifest that records their provenance."""

    version: str
    directory: Path
    manifest: dict[str, Any] = field(repr=False)

    @property
    def hpoa(self) -> Path:
        return self.directory / HPOA_FILENAME

    @property
    def hp_json(self) -> Path:
        return self.directory / HP_JSON_FILENAME

    @property
    def retrieved_at(self) -> str:
        return str(self.manifest.get("retrieved_at", ""))

    @property
    def hp_json_version(self) -> str:
        return str(self.manifest.get("hp_json_version", ""))

    def file_record(self, name: str) -> dict[str, Any]:
        return dict(self.manifest.get("files", {}).get(name, {}))

    @classmethod
    def read(cls, directory: Path) -> HpoFiles | None:
        """The download described by ``directory/manifest.json``, or None when there is none."""
        try:
            manifest = json.loads((directory / MANIFEST_FILENAME).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        files = manifest.get("files") if isinstance(manifest, dict) else None
        if not isinstance(files, dict) or not manifest.get("version"):
            return None
        if HPOA_FILENAME not in files or HP_JSON_FILENAME not in files:
            return None
        return cls(str(manifest["version"]), directory, manifest)

    def verify(self) -> bool:
        """True when every file is present with the recorded byte count and sha256."""
        for name, record in self.manifest["files"].items():
            path = self.directory / name
            if not path.is_file() or path.stat().st_size != record.get("bytes"):
                return False
            if sha256_file(path) != record.get("sha256"):
                return False
        return True


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _hpo_dir(raw_dir: str | os.PathLike[str] | None) -> Path:
    root = Path(raw_dir) if raw_dir is not None else config.RAW_DIR
    return root / RAW_SUBDIR


def find_local(hpo_dir: Path) -> HpoFiles | None:
    """The newest complete, checksum-verified download under ``hpo_dir`` (versions are ISO
    dates, so the directory name sorts), or None."""
    if not hpo_dir.is_dir():
        return None
    candidates = sorted((d for d in hpo_dir.iterdir() if d.is_dir() and not d.name.startswith(".")), key=lambda d: d.name, reverse=True)
    for directory in candidates:
        files = HpoFiles.read(directory)
        if files is not None and files.verify():
            return files
    return None


@dataclass(frozen=True)
class HpoaHeader:
    """The ``#`` header lines of phenotype.hpoa."""

    version: str
    hpo_version: str
    lines: tuple[str, ...]

    @property
    def hp_json_url(self) -> str:
        """The hp.json release IRI named by ``#hpo-version`` when it is a URL, else ``""``."""
        return self.hpo_version if self.hpo_version.startswith(("http://", "https://")) else ""


def parse_hpoa_header(path: str | os.PathLike[str]) -> HpoaHeader:
    lines: list[str] = []
    version = hpo_version = ""
    with Path(path).open(encoding="utf-8") as fh:
        for line in fh:
            if not line.startswith("#"):
                break
            stripped = line.rstrip("\n")
            lines.append(stripped)
            if m := _HEADER_VERSION.match(stripped):
                version = m.group(1)
            elif m := _HEADER_HPO_VERSION.match(stripped):
                hpo_version = m.group(1)
    return HpoaHeader(version, hpo_version, tuple(lines))


# --------------------------------------------------------------------------------------
# download
# --------------------------------------------------------------------------------------


class _Retryable(RuntimeError):
    """A download attempt that may succeed on retry (429 / 5xx / short body)."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _make_client() -> httpx.Client:
    return httpx.Client(
        follow_redirects=True,
        timeout=httpx.Timeout(60.0),
        headers={"User-Agent": config.USER_AGENT},
    )


def _download(
    client: httpx.Client,
    url: str,
    dest: Path,
    *,
    attempts: int = DOWNLOAD_ATTEMPTS,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Stream ``url`` into ``dest`` (atomic replace), hashing as it goes. Returns the provenance
    record: url, final_url, status, bytes, sha256, retrieved_at, content_length, last_modified,
    etag. Retries transport errors, 429, 5xx and a body shorter than ``Content-Length``."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f"{dest.name}.{os.getpid()}.part")
    for attempt in range(1, max(1, attempts) + 1):
        try:
            with client.stream("GET", url) as response:
                if response.status_code == 429 or response.status_code >= 500:
                    raise _Retryable(f"HTTP {response.status_code} for {url}")
                response.raise_for_status()
                digest = hashlib.sha256()
                n_bytes = 0
                with tmp.open("wb") as fh:
                    for chunk in response.iter_bytes(CHUNK_BYTES):
                        fh.write(chunk)
                        digest.update(chunk)
                        n_bytes += len(chunk)
                declared = response.headers.get("content-length")
                content_length = int(declared) if declared and declared.isdigit() else None
                if content_length is not None and content_length != n_bytes:
                    raise _Retryable(f"short body for {url}: {n_bytes} of {content_length} bytes")
                record = {
                    "url": url,
                    # GitHub release assets redirect to a signed, short-lived URL: keep the
                    # location, never the signature (docs/source-apis.md section 4a).
                    "final_url": str(response.url.copy_with(query=None)),
                    "status": response.status_code,
                    "bytes": n_bytes,
                    "sha256": digest.hexdigest(),
                    "retrieved_at": _utc_now(),
                    "content_length": content_length,
                    "last_modified": response.headers.get("last-modified", ""),
                    "etag": response.headers.get("etag", ""),
                }
            os.replace(tmp, dest)
            return record
        except (httpx.TransportError, _Retryable):
            tmp.unlink(missing_ok=True)
            if attempt >= attempts:
                raise
            sleep(DOWNLOAD_BACKOFF * (2 ** (attempt - 1)))
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
    raise AssertionError("unreachable: the loop returns or raises")


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def fetch_files(
    raw_dir: str | os.PathLike[str] | None = None,
    *,
    offline: bool = False,
    client: httpx.Client | None = None,
    refresh: bool = False,
) -> HpoFiles:
    """The local HPO download, fetching phenotype.hpoa and hp.json once when none exists.

    A complete, checksum-verified copy under ``<raw_dir>/hpo/<version>/`` is reused as is
    (``refresh=True`` forces a new download). Offline (``offline=True`` or ``ATLAS_OFFLINE``)
    with no local copy raises ``FileNotFoundError``. ``client`` lets tests inject an
    ``httpx.Client`` with a mock transport.
    """
    hpo_dir = _hpo_dir(raw_dir)
    if not refresh:
        local = find_local(hpo_dir)
        if local is not None:
            return local
    if offline or is_offline():
        raise FileNotFoundError(
            f"offline and no complete HPO download under {hpo_dir} "
            f"(need {HPOA_FILENAME}, {HP_JSON_FILENAME} and {MANIFEST_FILENAME} in a version directory)"
        )
    own_client = client is None
    http = client if client is not None else _make_client()
    try:
        incoming = hpo_dir / INCOMING_SUBDIR
        staged = incoming / HPOA_FILENAME
        hpoa_record = _download(http, HPOA_URL, staged)
        header = parse_hpoa_header(staged)
        if not header.version:
            staged.unlink(missing_ok=True)
            raise ValueError(f"{HPOA_FILENAME} from {HPOA_URL} has no '#version:' header line")
        directory = hpo_dir / header.version
        directory.mkdir(parents=True, exist_ok=True)
        os.replace(staged, directory / HPOA_FILENAME)
        hp_json_record = _download(http, header.hp_json_url or HP_JSON_URL, directory / HP_JSON_FILENAME)
        ontology = load_ontology(directory / HP_JSON_FILENAME)
        manifest = {
            "source": SOURCE_NAME,
            "version": header.version,
            "hp_json_version": ontology.version,
            "hpo_version_iri": header.hpo_version,
            "header": list(header.lines),
            "retrieved_at": hpoa_record["retrieved_at"],
            "files": {HPOA_FILENAME: hpoa_record, HP_JSON_FILENAME: hp_json_record},
        }
        _write_json(directory / MANIFEST_FILENAME, manifest)
        try:
            incoming.rmdir()
        except OSError:
            pass
        return HpoFiles(header.version, directory, manifest)
    finally:
        if own_client:
            http.close()


def get_ontology(raw_dir: str | os.PathLike[str] | None = None, *, offline: bool = False) -> Ontology:
    """The cached HP ontology of the local HPO download (fetched first when absent and online)."""
    return load_ontology(fetch_files(raw_dir, offline=offline).hp_json)


def ancestors(term: str, include_self: bool = True, *, raw_dir: str | os.PathLike[str] | None = None) -> frozenset[str]:
    """``is_a`` ancestor closure of an HP term (any id spelling) from the cached ontology."""
    return get_ontology(raw_dir).ancestors(normalize_curie(term), include_self)


# --------------------------------------------------------------------------------------
# annotations and information content
# --------------------------------------------------------------------------------------


@dataclass
class Annotations:
    """Kept phenotype annotations: ``direct[term]`` is the set of disease ids annotated to
    ``term`` itself (no propagation); ``stats`` holds the row counts behind the filtering."""

    direct: dict[str, set[str]]
    stats: dict[str, Any]

    @property
    def diseases(self) -> set[str]:
        return set().union(*self.direct.values()) if self.direct else set()


def read_annotations(path: str | os.PathLike[str], ontology: Ontology | None = None) -> Annotations:
    """Parse phenotype.hpoa with polars and apply the IC filters (module docstring). With an
    ontology, obsolete terms are mapped to their replacement and the "Excluded" frequency term
    is recognised."""
    df = pl.read_csv(
        Path(path),
        separator="\t",
        comment_prefix="#",
        has_header=True,
        infer_schema=False,
        quote_char=None,
        empty_string_is_null=False,  # an empty qualifier / frequency is "", not null
    )
    if tuple(df.columns) != HPOA_COLUMNS:
        raise ValueError(f"{path}: expected columns {list(HPOA_COLUMNS)}, got {df.columns}")
    stats: dict[str, Any] = {"rows": df.height}

    phenotypic = df.filter(pl.col("aspect") == ASPECT_PHENOTYPIC)
    stats["aspect_P_rows"] = phenotypic.height
    is_not = pl.col("qualifier") == QUALIFIER_NOT
    stats["qualifier_NOT_rows"] = phenotypic.filter(is_not).height
    kept = phenotypic.filter(~is_not)

    excluded_terms = sorted(ontology.terms_with_label(EXCLUDED_FREQUENCY_LABEL)) if ontology else []
    zero = pl.col("frequency").str.contains(_ZERO_FREQUENCY.pattern)
    if excluded_terms:
        zero = zero | pl.col("frequency").is_in(excluded_terms)
    stats["zero_frequency_rows"] = kept.filter(zero).height
    stats["excluded_frequency_terms"] = excluded_terms
    kept = kept.filter(~zero)
    stats["kept_rows"] = kept.height
    stats["kept_rows_by_evidence"] = {
        str(k): int(v) for k, v in sorted(kept.group_by("evidence").len().iter_rows())
    }

    term_map: dict[str, str] = {}
    remapped = 0
    for raw in kept.get_column("hpo_id").unique().to_list():
        term = normalize_curie(raw)
        if ontology is not None:
            resolved = ontology.resolve(term)
            remapped += resolved != term
            term = resolved
        term_map[raw] = term
    stats["obsolete_terms_remapped"] = remapped

    direct: dict[str, set[str]] = defaultdict(set)
    for disease, raw_term in kept.select("database_id", "hpo_id").iter_rows():
        direct[term_map[raw_term]].add(normalize_disease_id(disease))
    annotations = Annotations(dict(direct), stats)
    stats["distinct_terms"] = len(direct)
    stats["distinct_diseases"] = len(annotations.diseases)
    return annotations


def phenotypic_domain(ontology: Ontology) -> frozenset[str]:
    """Every term under the "Phenotypic abnormality" root(s) (found by label, roots included);
    empty when the ontology has no such label."""
    roots = ontology.terms_with_label(PHENOTYPIC_ABNORMALITY_LABEL)
    return frozenset().union(*(ontology.descendants(r, include_self=True) for r in roots)) if roots else frozenset()


def compute_ic(direct: Mapping[str, Collection[str]], ontology: Ontology) -> dict[str, tuple[float, int, int]]:
    """``{term: (ic, n_diseases, n_total)}`` over the active terms of the "Phenotypic abnormality"
    subtree (:func:`phenotypic_domain`; every active term when the ontology has no such label)
    plus every annotated term and its ancestors. ``n_diseases`` counts the diseases annotated to
    the term or any descendant; ``ic = -ln(n_diseases / n_total)``, or ``-ln(1 / n_total)`` with
    ``n_diseases = 0`` for terms never annotated. Terms outside the subtree (modes of inheritance,
    clinical modifiers, onset, frequency) get NO row. An obsolete term that ``replaced_by`` points
    at a term with a value carries that term's values, so a backbone built on an older HPO release
    (OptimusKG embeds 2025-05-06) that still cites the old id gets an information content."""
    all_diseases: set[str] = set()
    for diseases in direct.values():
        all_diseases.update(diseases)
    n_total = len(all_diseases)
    if n_total == 0:
        raise ValueError("no phenotype annotations: information content is undefined")

    closure: dict[str, set[str]] = defaultdict(set)
    for term, diseases in direct.items():
        for ancestor in ontology.ancestors(term, include_self=True):
            closure[ancestor].update(diseases)

    domain = phenotypic_domain(ontology) or ontology.active_terms
    unannotated_ic = -math.log(1.0 / n_total)
    out: dict[str, tuple[float, int, int]] = {}
    for term in sorted((ontology.active_terms & domain) | closure.keys()):
        n = len(closure.get(term, ()))
        ic = max(0.0, -math.log(n / n_total)) if n else unannotated_ic
        out[term] = (ic, n, n_total)
    for term in sorted(ontology.deprecated):
        replacement = ontology.resolve(term)
        if term not in out and replacement != term and replacement in out:
            out[term] = out[replacement]
    return out


# --------------------------------------------------------------------------------------
# loader
# --------------------------------------------------------------------------------------


def _replace_hpo_ic(store: Store, ic: Mapping[str, tuple[float, int, int]]) -> int:
    """``hpo_ic`` is owned by this loader: rewrite the whole table in one transaction so a
    re-run never leaves rows from an earlier computation behind (``set_hpo_ic`` only upserts)."""
    with store.conn:
        store.conn.execute("DELETE FROM hpo_ic")
        return store.set_hpo_ic(ic)


def load(
    store: Store,
    *,
    offline: bool = False,
    raw_dir: str | os.PathLike[str] | None = None,
    client: httpx.Client | None = None,
) -> dict[str, Any]:
    """Fetch (once) and parse HPO, compute information content over all annotated diseases and
    write ``hpo_ic``, the ``HPO`` sources row and the coverage rows. Idempotent. Everything is
    computed before the first write, so the store is touched only briefly at the end."""
    files = fetch_files(raw_dir, offline=offline, client=client)
    ontology = load_ontology(files.hp_json)
    annotations = read_annotations(files.hpoa, ontology)
    ic = compute_ic(annotations.direct, ontology)
    domain = phenotypic_domain(ontology)
    ic_domain = f"active terms under {PHENOTYPIC_ABNORMALITY_LABEL!r} ({len(domain)} terms) plus annotated terms and their ancestors" if domain else f"every active term ({PHENOTYPIC_ABNORMALITY_LABEL!r} not found by label)"
    n_total = annotations.stats["distinct_diseases"]
    retrieved_at = files.retrieved_at
    attribution = ATTRIBUTION.format(
        version=f"hp.json {files.hp_json_version or 'unknown'}; phenotype.hpoa {files.version}"
    )

    _replace_hpo_ic(store, ic)
    store.upsert_source(
        SOURCE_NAME,
        version=files.version,
        retrieved_at=retrieved_at,
        license=LICENSE,
        url=HPOA_URL,
        notes={
            "files": {name: files.file_record(name) for name in (HPOA_FILENAME, HP_JSON_FILENAME)},
            "hpoa_version": files.version,
            "hp_json_version": files.hp_json_version,
            "hpo_version_iri": files.manifest.get("hpo_version_iri", ""),
            "directory": str(files.directory),
            "license_url": LICENSE_URL,
            "license_url_note": LICENSE_URL_NOTE,
            "attribution": attribution,
            "iea_caveat": IEA_CAVEAT,
            "information_content": {
                "n_total": n_total,
                "n_terms": len(ic),
                "filters": [
                    f"aspect == {ASPECT_PHENOTYPIC!r}",
                    f"qualifier != {QUALIFIER_NOT!r}",
                    f"frequency not {_ZERO_FREQUENCY.pattern!r} and not the {EXCLUDED_FREQUENCY_LABEL!r} term",
                ],
                "unannotated_terms": "ic = -ln(1 / n_total), n_diseases = 0",
                "terms_with_ic": ic_domain,
                **annotations.stats,
            },
        },
    )
    coverage_source = f"{SOURCE_NAME} {HPOA_FILENAME}"
    store.add_coverage(COVERAGE_ENTITY, coverage_source, "aspect == 'P' annotation rows", retrieved_at, annotations.stats["aspect_P_rows"])
    store.add_coverage(
        COVERAGE_ENTITY,
        coverage_source,
        "annotation rows kept for information content (aspect P, qualifier != NOT, frequency not zero)",
        retrieved_at,
        annotations.stats["kept_rows"],
    )
    store.add_coverage(COVERAGE_ENTITY, coverage_source, "distinct diseases with >= 1 kept annotation (N_total)", retrieved_at, n_total)
    store.add_coverage(COVERAGE_ENTITY, f"{SOURCE_NAME} {HP_JSON_FILENAME}", "terms with information content", retrieved_at, len(ic))

    return {
        "source": SOURCE_NAME,
        "version": files.version,
        "hp_json_version": files.hp_json_version,
        "directory": str(files.directory),
        "retrieved_at": retrieved_at,
        "n_total": n_total,
        "n_terms": len(ic),
        "stats": annotations.stats,
    }
