# OptimusKG reconnaissance (backbone KG for the JAK-STAT atlas)

Recon date: 2026-10-03. Read-only; all evidence below was observed in live HTTP responses or in the official docs/repo fetched today.
Working files (downloaded samples, scripts, raw JSON) are under
`/private/tmp/claude-501/-Users-jonathanyan-Desktop-projects-Pyrid/8911b015-0fc5-4590-95d8-bd29f4230acc/scratchpad/recon/`
(`dataverse_dataset.json`, `llms-full.txt`, `edges.mdx`, `nodes.mdx`, `biocypher_schema_config.yaml`, `client_src/*.py`, `parquet/*.parquet`, `scripts/*.py`, `inspect_small.out`).

Legend: **VERIFIED** = seen in a response today. **UNVERIFIED** = not checked.

---

## 0. TL;DR

* Dataset = Harvard Dataverse `doi:10.7910/DVN/IYNGEV`, version **3.2**, released 2026-09-09, license **CC BY-NC-SA 4.0**, 41 Parquet files, 822 MB total, **no token needed** for download. VERIFIED.
* Files we need (12 stratified files) total **158,398,652 bytes (158.4 MB)**; 125.4 MB if `nodes/drug.parquet` (33 MB) is skipped. VERIFIED from the file listing.
* PyPI `optimuskg` 1.0.0 (2026-04-17, `requires_python >=3.12`) is a 6-file thin wrapper: `get_file`, `load_parquet`, `load_graph`, `load_networkx`, `set/get_cache_dir`, `set/get_doi`, `set/get_server`. **No filtering API**; filtering = Polars on the returned DataFrame. VERIFIED (source read + installed and ran).
* **Gotcha (VERIFIED, reproduced 3x):** Harvard Dataverse returns **HTTP 403** to the default `python-requests/2.32.3` User-Agent on `/api/datasets/...` (the metadata endpoint the client calls first). Out of the box, `optimuskg.get_file(...)` fails with `HTTPError: 403`. A one-line UA monkeypatch fixes it (section 1.4). `curl` and any non-default UA get 200. `/api/access/datafile/<id>` is NOT blocked for python-requests.
* Spike exit criteria: **(1) PASS** — STAT1, STAT3, JAK1, JAK2, JAK3, TYK2, IL2RG each resolve to exactly one `GEN` node keyed by Ensembl gene id, with `HGNC:<n>` in `properties.xrefs`. **(2) PASS with caveats** — JAK3-SCID (MONDO_0010938, 20 DIS-PHE edges), HIES1/STAT3 (MONDO_0007818, 53), PV (EFO_0002429, 20), ET (10), PMF (11), IL2RG "gamma chain deficiency" (EFO_0005555, 22), STAT1 LoF "immunodeficiency 31B" (MONDO_0013427, 4), STAT1 GoF "immunodeficiency 31C" = MONDO_0013599 (42). **But** `MONDO_0015279` "chronic mucocutaneous candidiasis" itself has **0** DIS-PHE edges and `EFO_0010647` "STAT3 gain of function" has **0**; their phenotype coverage comes from children/related MONDO terms.
* Observed `evidence_score` range on DIS-GEN (from Parquet footer stats over all 9.73 M rows): **min 0.00091, max 0.9486**. `evidence_count` 1–197,300. `number_of_pmids` 0–136 (DisGeNET-only; null on 97.2 % of rows). `year_initial` '1940'–'2020', `year_final` '1966'–'2020' (strings; DisGeNET-only).
* Recommendation: **download the stratified Parquet files directly with plain HTTP (curl / requests with a custom UA) and read with Polars/pyarrow.** The client adds nothing we need, has the UA bug, pins `polars>=1.19` + `networkx`, and cannot do partial reads. Details in section 5.

---

## 1. Python client (`optimuskg` on PyPI) and docs

### 1.1 PyPI metadata — VERIFIED

```bash
curl -sL "https://pypi.org/pypi/optimuskg/json" | python3 -c "import json,sys; d=json.load(sys.stdin); i=d['info']; print(i['version'], i['requires_python'], i['summary']); print(i['requires_dist']); [print(v,f['upload_time'],f['filename'],f['size']) for v,fs in d['releases'].items() for f in fs]"
```
Response (trimmed):
```
version: 1.0.0
requires_python: >=3.12
summary: Client library for loading the OptimusKG biomedical knowledge graph from Harvard Dataverse.
requires_dist: ["networkx>=3.0", "platformdirs>=4.0.0", "polars>=1.19.0", "pyarrow>=19.0.0", "requests>=2.32.3"]
project_urls: Dataset=https://dataverse.harvard.edu/dataset.xhtml?persistentId=doi.org/10.7910/DVN/IYNGEV, Homepage=https://optimuskg.ai, Repository=https://github.com/mims-harvard/optimuskg
license: MIT License  Copyright (c) 2025 Zitnik Lab, Department of Biomedical Informatics, Harvard Medical School ...
releases:
   0.1.0 2026-04-17T02:50:16 optimuskg-0.1.0-py3-none-any.whl 9296
   0.1.0 2026-04-17T02:50:18 optimuskg-0.1.0.tar.gz 9721
   1.0.0 2026-04-17T03:34:56 optimuskg-1.0.0-py3-none-any.whl 10513
   1.0.0 2026-04-17T03:34:55 optimuskg-1.0.0.tar.gz 11019
```
Only two releases, both on 2026-04-17; wheel is ~10 KB. Installed package reports `optimuskg.__version__ == "0.1.0"` while `importlib.metadata.version("optimuskg") == "1.0.0"` (VERIFIED after install; the `__init__.py` string was never bumped).

### 1.2 Public API — VERIFIED from source (`packages/optimuskg/src/optimuskg/__init__.py`, `api.py`, `_config.py`, `_dataverse.py`)

Fetched with:
```bash
curl -sL https://raw.githubusercontent.com/mims-harvard/optimuskg/main/packages/optimuskg/src/optimuskg/api.py
```
`__all__` (exact):
```
['__version__', 'get_cache_dir', 'get_doi', 'get_file', 'get_server', 'load_graph', 'load_networkx', 'load_parquet', 'set_cache_dir', 'set_doi', 'set_server']
```
Signatures (exact, from `api.py` and the autogenerated reference page https://optimuskg.ai/docs/optimuskg-client/reference):

| Function | Signature | Returns | What it does |
|---|---|---|---|
| `get_file` | `(relative_path: str, *, force: bool = False) -> Path` | local path | `_dataverse.download(relative_path, force=force)`: fetches dataset metadata, resolves the Dataverse file id, streams `/api/access/datafile/<id>` to `<cache_dir>/<doi_slug>/<version>/<relative_path>` |
| `load_parquet` | `(relative_path: str, *, force: bool = False, **read_parquet_kwargs) -> pl.DataFrame` | Polars DF | `pl.read_parquet(get_file(...), **read_parquet_kwargs)` — extra kwargs (e.g. `columns=[...]`) go straight to Polars |
| `load_graph` | `(*, lcc: bool = False, force: bool = False) -> tuple[pl.DataFrame, pl.DataFrame]` | (nodes, edges) | loads `nodes.parquet` + `edges.parquet` (116 MB + 180 MB) or the two `largest_connected_component_*.parquet` files. **Never call this for the MVP.** |
| `load_networkx` | `(*, lcc: bool = False, force: bool = False, parse_properties: bool = True) -> nx.MultiDiGraph` | NetworkX | warns "Loading the full graph into NetworkX requires several GB of memory (190k nodes, 21M edges)" |
| `set_cache_dir` / `get_cache_dir` | `(path) -> None` / `() -> Path` | | default `platformdirs.user_cache_dir("optimuskg")`; env `OPTIMUSKG_CACHE_DIR` |
| `set_doi` / `get_doi` | `(doi) -> None` / `() -> str` | | default `"doi:10.7910/DVN/IYNGEV"`; env `OPTIMUSKG_DOI` |
| `set_server` / `get_server` | `(url) -> None` / `() -> str` | | default `"https://dataverse.harvard.edu"`; env `OPTIMUSKG_SERVER` |

Quoted from the docs (https://optimuskg.ai/docs, "Using OptimusKG"):
> "The client fetches files from the gold layer with local caching, and supports loading the graph either as Polars DataFrames or as a NetworkX MultiDiGraph"

Quoted from `skills/optimuskg/SKILL.md` ("File paths"):
> "`nodes/<type>.parquet` files use the lowercase entity name (`gene`, `drug`, …); `edges/<a>_<b>.parquet` files use the lowercase node pair (`disease_gene`, `drug_gene`, …)."

Quoted from the docs client page (https://optimuskg.ai/docs/optimuskg-client, "DataFrames"):
> "`load_parquet` calls `get_file` then `polars.read_parquet`. Any extra keyword arguments are forwarded directly to `pl.read_parquet`."
> `only_ids = optimuskg.load_parquet("nodes/drug.parquet", columns=["id"])`

**There is no filter/query function in the client.** The docs' only filtering guidance is plain Polars/NetworkX (quoted from the SKILL.md "Common patterns"):
```python
nodes, edges = optimuskg.load_graph(lcc=True)
genes = nodes.filter(pl.col("label") == "GEN")
dis_gen = edges.filter(pl.col("relation") == "ASSOCIATED_WITH")
```
and from the edges doc page:
```python
edges = pl.read_parquet("edges/drug_disease.parquet")
conflicting = edges.filter(pl.col("properties").struct.field("relation_conflict"))
conflicting.select("from", "to", "relation", pl.col("properties").struct.field("relation_assertions"))
```

How the client resolves files (`_dataverse.py`, VERIFIED): `fetch_metadata()` GETs `{server}/api/datasets/:persistentId/` with `params={"persistentId": doi}`, reads `data.latestVersion.versionNumber`/`versionMinorNumber` (→ `"3.2"`) and `files[].dataFile.id`, `files[].dataFile.filename`, `files[].directoryLabel`; `to_relative(directoryLabel, filename)` builds keys like `edges/disease_gene.parquet`. `download()` then GETs `{server}/api/access/datafile/{entry.id}` with `stream=True, timeout=300`, 1 MiB chunks, writes `*.part` then renames. No retries, no checksum verification, no custom User-Agent, no Range support.

### 1.3 Docs site — VERIFIED

* https://optimuskg.ai/docs (HTTP 200) pages: `/docs`, `/docs/optimuskg-client`, `/docs/optimuskg-client/reference`, `/docs/graph-schema/nodes`, `/docs/graph-schema/edges`, `/docs/license`, `/docs/mcp-bundle`, `/docs/showcase`, `/docs/citation`, `/docs/funding`.
* Machine-readable: `https://optimuskg.ai/llms.txt` (index) and `https://optimuskg.ai/llms-full.txt` (55 KB, HTTP 200). **Caveat:** the schema tables on the nodes/edges pages are React components (`<DocsSchemaTree fields="disGenFields" />`) and are *stripped* from llms-full.txt; the field definitions live in the raw MDX: `https://raw.githubusercontent.com/mims-harvard/optimuskg/main/docs/content/docs/graph-schema/edges.mdx` and `.../nodes.mdx` (fetched; see section 3).
* Repo tree (HTTP 200 via `https://api.github.com/repos/mims-harvard/optimuskg/git/trees/main?recursive=1`): the pipeline is Kedro-based (`pyproject.toml` at repo root is the *pipeline*, version 0.74.2, with 30+ heavy deps — not the client). The client is `packages/optimuskg/` (own `pyproject.toml`, version 1.0.0, hatchling). Agent skill: `skills/optimuskg/SKILL.md` + `skills/optimuskg/reference/graph-schema.md`. BioCypher schema: `conf/base/biocypher/schema_config.yaml`.

### 1.4 Client smoke test — VERIFIED (installed in a scratchpad venv, not in the project)

```bash
cd <scratchpad>/recon && /opt/homebrew/bin/uv venv .venv-okg && /opt/homebrew/bin/uv pip install --python .venv-okg/bin/python optimuskg
OPTIMUSKG_CACHE_DIR=<scratchpad>/recon/okg-cache .venv-okg/bin/python -c 'import optimuskg; print(optimuskg.get_file("nodes/pathway.parquet"))'
```
Result (twice, minutes apart):
```
requests.exceptions.HTTPError: 403 Client Error: Forbidden for url: https://dataverse.harvard.edu/api/datasets/:persistentId/?persistentId=doi%3A10.7910%2FDVN%2FIYNGEV
```
Root cause isolated with curl (all VERIFIED today):
```
curl -A "python-requests/2.32.3" ".../api/datasets/:persistentId/?persistentId=doi:10.7910/DVN/IYNGEV"  -> HTTP 403 (Apache "Forbidden" HTML)
curl -A "python-requests/2.32.3" ".../api/datasets/13645730"                                            -> HTTP 403
curl -A "Python-urllib/3.13"     ".../api/datasets/:persistentId/?persistentId=..."                     -> HTTP 403
curl                              (default curl UA, same URL)                                             -> HTTP 200
curl -A "optimuskg/1.0.0"         (same URL)                                                              -> HTTP 200
curl -A ""                        (same URL)                                                              -> HTTP 200
curl -I -A "python-requests/2.32.3" ".../api/access/datafile/14220888"                                   -> HTTP/2 303 (NOT blocked)
```
So Harvard Dataverse's WAF blocks the `python-requests` and `Python-urllib` User-Agents on `/api/datasets/*` only. Working workaround (VERIFIED, then `get_file`, `load_parquet(columns=...)` and `fetch_metadata()` all succeeded; dataset version seen by client = `3.2`, 41 files):
```python
import requests.utils
requests.utils.default_user_agent = lambda name="python-requests": "pyrid-atlas/0.1 (+optimuskg client)"
import optimuskg  # must come after the patch
```
Cache layout observed: `okg-cache/doi_10_7910_DVN_IYNGEV/3.2/nodes/pathway.parquet`.
Any hand-rolled `requests` code in `pipeline/cache.py` must also set a non-default `User-Agent` header or it will 403 on the metadata endpoint.

---

## 2. Harvard Dataverse dataset `doi:10.7910/DVN/IYNGEV` — VERIFIED

### 2.1 Dataset metadata
```bash
curl -sL "https://dataverse.harvard.edu/api/datasets/:persistentId/?persistentId=doi:10.7910/DVN/IYNGEV" -o dataverse_dataset.json
```
Response (trimmed; full JSON saved as `dataverse_dataset.json`, 39 KB):
```
status: OK
data.id: 13645730   data.persistentUrl: https://doi.org/10.7910/DVN/IYNGEV   publisher: Harvard Dataverse
latestVersion.versionNumber: 3  versionMinorNumber: 2  versionState: RELEASED  releaseTime: 2026-09-09T04:14:10Z
latestVersion.license: {"name": "CC BY-NC-SA 4.0", "uri": "http://creativecommons.org/licenses/by-nc-sa/4.0", "iconUri": "https://licensebuttons.net/l/by-nc-sa/4.0/88x31.png"}
latestVersion.termsOfUse: None      latestVersion.fileAccessRequest: True     every file: restricted=False
citation.title: "OptimusKG"   depositor: "Vittor, Lucas"   dateOfDeposit: "2026-04-04"   kindOfData: ["knowledge graph"]
citation.dsDescription: "OptimusKG is a multimodal biomedical labeled property graph (LPG) ... contains 190,939 nodes across 10 entity types, 21,818,752 edges across 27 edge types ... distributed as Apache Parquet files ..."
citation.notesText: "OptimusKG is available at https://optimuskg.ai. The OptimusKG codebase ... is released under the MIT License ... The OptimusKG client is available at https://pypi.org/project/optimuskg."
```
Each `files[].dataFile` entry also carries `md5` and `checksum: {"type": "MD5", "value": ...}` (e.g. `nodes/gene.parquet` md5 `edf81a21db68658381c063e21d1080d2`) — usable for integrity checks. (The node/edge counts in the Dataverse description, the docs, and the SKILL.md disagree slightly — 190,531 / 190,939 / 192,813 nodes — the Parquet footers are authoritative: `nodes.parquet` = 192,813 rows, `edges.parquet` = 21,834,669 rows.)

### 2.2 Complete file list (41 files, `latestVersion.files[]`, total 822,113,189 bytes)

All `contentType = application/octet-stream`, `restricted = False`, no per-file persistentId. `directoryLabel` is `nodes` or `edges` or absent (root).

| directoryLabel/label | dataFile.id | filesize (bytes) |
|---|---:|---:|
| `nodes.parquet` (root, unified) | 14220908 | 116,094,167 |
| `edges.parquet` (root, unified) | 14220909 | 179,606,962 |
| `largest_connected_component_nodes.parquet` | 14220921 | 116,047,497 |
| `largest_connected_component_edges.parquet` | 14220897 | 179,502,680 |
| `nodes/anatomy.parquet` | 14220913 | 1,085,455 |
| `nodes/biological_process.parquet` | 14220903 | 1,605,528 |
| `nodes/cellular_component.parquet` | 14220899 | 343,164 |
| `nodes/disease.parquet` | 14220892 | 2,968,496 |
| `nodes/drug.parquet` | 14220910 | 32,960,859 |
| `nodes/exposure.parquet` | 14220912 | 21,702 |
| `nodes/gene.parquet` | 14220882 | 43,261,973 |
| `nodes/molecular_function.parquet` | 14220891 | 735,889 |
| `nodes/pathway.parquet` | 14220888 | 50,610 |
| `nodes/phenotype.parquet` | 14220905 | 975,819 |
| `edges/anatomy_anatomy.parquet` | 14220889 | 73,685 |
| `edges/anatomy_gene.parquet` | 14220890 | 42,537,331 |
| `edges/biological_process_biological_process.parquet` | 14220884 | 238,692 |
| `edges/biological_process_gene.parquet` | 14220919 | 1,952,119 |
| `edges/cellular_component_cellular_component.parquet` | 14220902 | 20,582 |
| `edges/cellular_component_gene.parquet` | 14220917 | 1,670,338 |
| `edges/disease_disease.parquet` | 14220916 | 179,050 |
| `edges/disease_gene.parquet` | 14220918 | 72,754,866 |
| `edges/disease_phenotype.parquet` | 14220915 | 1,356,623 |
| `edges/drug_biological_process.parquet` | 14220898 | 5,377 |
| `edges/drug_disease.parquet` | 14220904 | 1,329,176 |
| `edges/drug_drug.parquet` | 14220881 | 14,487,108 |
| `edges/drug_gene.parquet` | 14220895 | 499,643 |
| `edges/drug_phenotype.parquet` | 14220883 | 206,326 |
| `edges/exposure_biological_process.parquet` | 14220911 | 210,537 |
| `edges/exposure_cellular_component.parquet` | 14220893 | 20,187 |
| `edges/exposure_disease.parquet` | 14220887 | 210,761 |
| `edges/exposure_exposure.parquet` | 14220886 | 494,865 |
| `edges/exposure_gene.parquet` | 14220920 | 167,000 |
| `edges/exposure_molecular_function.parquet` | 14220901 | 25,106 |
| `edges/gene_gene.parquet` | 14220907 | 1,833,634 |
| `edges/molecular_function_gene.parquet` | 14220894 | 1,390,380 |
| `edges/molecular_function_molecular_function.parquet` | 14220900 | 46,099 |
| `edges/pathway_gene.parquet` | 14220896 | 134,262 |
| `edges/pathway_pathway.parquet` | 14220906 | 19,052 |
| `edges/phenotype_gene.parquet` | 14220885 | 4,835,383 |
| `edges/phenotype_phenotype.parquet` | 14220914 | 154,206 |

### 2.3 Files we need and total size

| Need | File | id | bytes |
|---|---|---:|---:|
| gene nodes | `nodes/gene.parquet` | 14220882 | 43,261,973 |
| disease nodes | `nodes/disease.parquet` | 14220892 | 2,968,496 |
| phenotype nodes | `nodes/phenotype.parquet` | 14220905 | 975,819 |
| pathway nodes | `nodes/pathway.parquet` | 14220888 | 50,610 |
| drug nodes | `nodes/drug.parquet` | 14220910 | 32,960,859 |
| disease–gene | `edges/disease_gene.parquet` | 14220918 | 72,754,866 |
| disease–phenotype | `edges/disease_phenotype.parquet` | 14220915 | 1,356,623 |
| phenotype–phenotype | `edges/phenotype_phenotype.parquet` | 14220914 | 154,206 |
| pathway–gene | `edges/pathway_gene.parquet` | 14220896 | 134,262 |
| GO-process–gene | `edges/biological_process_gene.parquet` | 14220919 | 1,952,119 |
| drug–gene | `edges/drug_gene.parquet` | 14220895 | 499,643 |
| drug–disease | `edges/drug_disease.parquet` | 14220904 | 1,329,176 |
| **Core total (12 files)** | | | **158,398,652 (158.4 MB)** |
| optional: GO term names | `nodes/biological_process.parquet` | 14220903 | 1,605,528 |
| optional: disease hierarchy (MONDO/EFO `PARENT`) | `edges/disease_disease.parquet` | 14220916 | 179,050 |
| optional: Reactome hierarchy | `edges/pathway_pathway.parquet` | 14220906 | 19,052 |
| **Core + optional (15 files)** | | | **160,202,282 (160.2 MB)** |

Without `nodes/drug.parquet` (33 MB, mostly base64 MOL files/images) the core set is 125,437,793 bytes; the ruxolitinib node (`CHEMBL1789941`) can instead be fetched by reading only a few columns (section 3.6).

### 2.4 Download URL pattern and auth — VERIFIED
```bash
curl -L "https://dataverse.harvard.edu/api/access/datafile/14220888" -o pathway.parquet
# -> 303 to https://dvn-cloud-iqss.s3.amazonaws.com/10.7910/DVN/IYNGEV/<key>?X-Amz-...&X-Amz-Expires=3600 ; then 200, 50,610 bytes, content_type=application/octet-stream
```
* **No API token needed**: every file downloaded (11 files + Range reads) with no `X-Dataverse-key`/token, from a plain curl. `fileAccessRequest: True` on the dataset is irrelevant because every file has `restricted: False`.
* The redirect is a **pre-signed S3 URL valid 3600 s**, `Accept-Ranges: bytes`, HTTP Range requests work (`Range: bytes=0-7` → 206, `Content-Range: bytes 0-7/43261973`). **A HEAD on the presigned URL returns 403** (S3 signatures are method-specific) — use GET with `Range` to probe sizes, or read `filesize` from the dataset JSON.
* Multi-file zip bundle also works, no token: `https://dataverse.harvard.edu/api/access/datafiles/14220888,14220906` → 200, `application/zip; name="dataverse_files.zip"`, contains `nodes/pathway.parquet` and `edges/pathway_pathway.parquet` with their directory labels. UNVERIFIED whether the bundle has a size cap for our 158 MB set.
* Gotcha for zsh loops: `for p in "id name"; do set -- $p` does **not** word-split in zsh — this produced `curl: (3) URL rejected` in a first attempt. Use `while read -r id name`.

---

## 3. Schema (VERIFIED from Parquet footers/data + docs MDX + `conf/base/biocypher/schema_config.yaml`)

### 3.1 Base layout
Unified `nodes.parquet` (192,813 rows, 2 row groups): `id: large_string, label: large_string, properties: large_string` (JSON string). Unified `edges.parquet` (21,834,669 rows, 178 row groups): `from, to, label, relation: large_string; undirected: bool; properties: large_string` (JSON). Docs: "In the stratified per-type parquet files (`nodes/<type>.parquet` / `edges/<label>.parquet`), `properties` is expanded into native typed columns as a Polars `Struct`." — confirmed on every per-type file read. All files `created_by = Polars`, ZSTD, with `ARROW:schema` metadata and per-column min/max statistics (so footer-only Range reads give global ranges for free).

Node type codes (SKILL.md, verified in data): `GEN` Gene 61,306 · `DIS` Disease 36,753 · `BPO` 25,754 · `PHE` 19,341 · `DRG` 18,640 · `ANA` · `MFN` · `CCO` · `PWY` 2,805 · `EXP`.
Edge labels/relations we use (docs edges table, verified in data): `DIS-GEN ASSOCIATED_WITH` (9,734,774; `undirected=True`), `DIS-PHE PHENOTYPE_PRESENT` (157,144), `PHE-PHE PARENT` (26,121; directed), `PWY-GEN INTERACTS_WITH` (46,977), `BPO-GEN INTERACTS_WITH` (158,410), `DRG-GEN` 33 relation values incl. `INHIBITOR`, `TARGET`, `ANTAGONIST`… (36,611; directed), `DRG-DIS INDICATION | CONTRAINDICATION | OFF_LABEL_USE` (70,380), `DIS-DIS PARENT` (47,892; directed).

Docs guarantee (edges page, "One edge per node pair"): "OptimusKG guarantees **at most one edge per (`from`, `to`, `label`) triple**" — the winning `relation` is by `RELATION_PRIORITY`, and the losers are kept in `properties.relation_assertions`.

### 3.2 Gene nodes — how genes are keyed
`nodes/gene.parquet` (61,306 rows, 1 row group; schema from footer via Range read, 65 KB fetched):
```
id: large_string            # Ensembl gene id WITHOUT prefix, e.g. 'ENSG00000115415' (61,108 rows); 198 rows are 'NCBIGene:<n>' (DisGeNET-only genes)
label: large_string         # 'GEN'
properties: struct<
  sources: struct<direct: list<str>, indirect: list<str>>,     # e.g. {'direct': ['OPEN_TARGETS','BGEE'], 'indirect': []}
  symbol: str,              # HGNC symbol  (docs: "Official HGNC gene symbol (e.g. TP53)")
  biotype: str, transcript_ids: list<str>, canonical_transcript: struct<id,chromosome,start,end,strand>, canonical_exons: list<str>,
  genomic_location: struct<chromosome: str, start: int64, end: int64, strand: int32>, alternative_genes: list<str>,
  name: str,                # full gene name
  hallmarks_attributes, cancer_hallmarks: list<struct<pmid,...>>,
  synonyms: list<struct<label: str, source: str>>,
  symbol_synonyms: list<struct<label: str, source: str>>,      # sources seen: NCBI_entrez, uniprot, HGNC
  name_synonyms: list<struct<label, source>>, function_descriptions: list<str>, subcellular_locations: list<struct<...>>,
  target_class: list<struct<id: int64, label, level>>, obsolete_symbols: list<struct<label, source>>, obsolete_names: list<struct<label, source>>,
  constraint_scores: list<struct<constraint_type, score, exp, obs, oe, oe_lower, oe_upper, upper_rank, upper_bin, upper_bin6>>,
  target_enabling_package: struct<...>, associated_proteins: list<struct<id, source>>,
  xrefs: list<struct<id: str, source: str>>,                   # sources seen: PDB 120k, InterPro, Reactome, HGNC 41,270, DrugBank 27,955, ChEMBL, ProjectScore, signalP
  chemical_probes, homologues, tractability, safety_liabilities: list<struct<...>>, transcription_start_site: int64 >
```
**HGNC id lives in `properties.xrefs` as `{id: '11362', source: 'HGNC'}`; the HGNC symbol is `properties.symbol`; synonyms are `properties.symbol_synonyms[].label` (+`.source`) and `properties.obsolete_symbols`.** No `hgnc_id` column.

Cluster gene resolution (read via Range only the columns `id,label,properties.sources,properties.symbol,properties.name,properties.biotype,properties.xrefs,properties.symbol_synonyms,properties.obsolete_symbols` = 3.3 MB of the 43 MB file; exact match on `properties.symbol`):

| symbol | id | name | HGNC xref | notable symbol_synonyms / obsolete_symbols |
|---|---|---|---|---|
| STAT1 | `ENSG00000115415` | signal transducer and activator of transcription 1 | HGNC:11362 | STAT91, ISGF-3, CANDF7, IMD31A/B/C |
| STAT3 | `ENSG00000168610` | signal transducer and activator of transcription 3 | HGNC:11364 | APRF, ADMIO, ADMIO1, HIES |
| JAK1 | `ENSG00000162434` | Janus kinase 1 | HGNC:6190 | JAK1A, JTK3, AIIDE; obsolete JAK1B |
| JAK2 | `ENSG00000096968` | Janus kinase 2 | HGNC:6192 | JTK10 |
| JAK3 | `ENSG00000105639` | Janus kinase 3 | HGNC:6193 | L-JAK, JAKL, LJAK |
| TYK2 | `ENSG00000105397` | tyrosine kinase 2 | HGNC:12440 | JTK1, IMD35 |
| IL2RG | `ENSG00000147168` | interleukin 2 receptor subunit gamma | HGNC:6010 | CD132, CIDX, IMD4; obsolete SCIDX1 |

Each symbol matched **exactly one** node (exit criterion 1 PASS). All seven: `biotype='protein_coding'`, `sources.direct=['OPEN_TARGETS','BGEE']`.

### 3.3 Disease nodes — how diseases are keyed
`nodes/disease.parquet` (36,753 rows): `id` is an **OBO-style underscore CURIE, not `MONDO:`** — prefixes observed: `EFO_` 11,960, `MONDO_` 11,724, `OBA_` 10,997 (measurement traits, from Open Targets), `Orphanet_` 2,045, `OTAR_` 9, `DOID_` 6, `OBI_`, `NCIT_`, `MP_`, `PATO_`. `properties.code` is the IRI (e.g. `http://purl.obolibrary.org/obo/MONDO_0015279`, `http://www.ebi.ac.uk/efo/EFO_0002429`). `sources.direct` = `['OPEN_TARGETS','DRUG_CENTRAL','MONDO']` on every row.
Properties present (actual Parquet struct): `sources, name, description, code, xrefs: list<str> (e.g. 'OMIM:600802', 'Orphanet:35078', 'DOID:5810', 'UMLS:C1833275', 'MEDGEN:..', 'SCTID:..', 'GARD:..', 'MESH:..', 'icd11.foundation:..', 'ICD9:..', 'MONDO:..'), exact_synonyms, related_synonyms, narrow_synonyms, broad_synonyms, obsolete_terms, obsolete_xrefs, therapeutic_areas (EFO/OTAR ids), concept_ids, concept_names, umls_cui, snomed_full_names, cui_semantic_type, snomed_concept_ids`.
**Docs/data mismatch:** the docs' `diseaseFields` list `parents`, `children`, `ancestors`, `descendants`, `is_leaf` — **these columns do not exist** in `nodes/disease.parquet` v3.2 (nor in `phenotype.parquet`). The hierarchy is in `edges/disease_disease.parquet` (`DIS-DIS PARENT`, `from`=child → `to`=parent, `sources.direct=['OPEN_TARGETS']`) and `edges/phenotype_phenotype.parquet` (`PHE-PHE PARENT`, `sources.direct=['HPO']`). Verified: `MONDO_0015279` (CMC) has parents `MONDO_0003778` 'inborn error of immunity' and `MONDO_0100118`, and children `MONDO_0013140` 'candidiasis, familial, 4', `MONDO_0013500` 'immunodeficiency 51', `MONDO_0013599` (IMD31C/STAT1 GoF). `MONDO_0010938` (JAK3-SCID) parents: `MONDO_0031520` 'familial severe combined immunodeficiency', `MONDO_0044200` 'T-B+ severe combined immunodeficiency'.
Many EFO-keyed diseases have `xrefs = []` (e.g. `EFO_0002429` polycythemia vera, `EFO_0000479` ET, `EFO_0002430` PMF, `MONDO_0015279` CMC) — OMIM/Orphanet cross-walks must come from elsewhere for those.

Cluster diseases found (name/synonym search over `name`, `*_synonyms`, `description`), with DIS-PHE edge counts:

| id | name | DIS-PHE edges | notes |
|---|---|---:|---|
| `MONDO_0015279` | chronic mucocutaneous candidiasis | **0** | xrefs []; children carry the phenotypes |
| `MONDO_0013599` | autoimmune enteropathy and endocrinopathy - susceptibility to chronic infections syndrome | 42 | exact_synonyms include 'immunodeficiency type 31C', 'immunodeficiency 31C, chronic mucocutaneous candidiasis, autosomal dominant', 'CANDF7' → **this is STAT1 GoF**; xrefs OMIM:614162, Orphanet:391487, DOID:0111946 |
| `MONDO_0013427` | immunodeficiency 31B | 4 | exact_synonym 'STAT1 deficiency' (STAT1 LoF, AR); xrefs [] |
| `Orphanet_319543` | Autosomal dominant mendelian susceptibility to mycobacterial diseases due to a partial deficiency | 0 | description mentions STAT1 (AD LoF) |
| `MONDO_0013140` | candidiasis, familial, 4 | 3 | OMIM:613108 |
| `MONDO_0010938` | T-B+ severe combined immunodeficiency due to JAK3 deficiency | 20 | OMIM:600802, Orphanet:35078 |
| `EFO_0005555` | gamma chain deficiency | 22 | only IL2RG hit (X-SCID); xrefs [] |
| `MONDO_0007818` | hyper-IgE recurrent infection syndrome 1, autosomal dominant | 53 | STAT3 DN; OMIM:146840/147060, Orphanet:2314 |
| `EFO_0010647` | STAT3 gain of function | **0** | exists as a node but no phenotypes |
| `MONDO_0018037` | hyper-IgE syndrome | 0 | parent of MONDO_0007818 |
| `EFO_0002429` | polycythemia vera | 20 | xrefs [] |
| `EFO_0000479` | essential thrombocythemia | 10 | |
| `EFO_0002430` | primary myelofibrosis | 11 | |
| `EFO_0004251` / `EFO_0002428` | myeloproliferative disorder / chronic myeloproliferative disorder | 0 / 4 | parents of PV |
| `MONDO_0012682` | immunodeficiency 35 (TYK2 deficiency) | 7 | OMIM:611521 |
| `Orphanet_748` | Mendelian susceptibility to mycobacterial diseases | 0 | |
| JAK1 | — | — | **no disease node mentions JAK1** (0 hits) |

Exit criterion 2 (cluster diseases have DIS-PHE edges): PASS for JAK3-SCID, X-SCID, HIES1, PV/ET/PMF, STAT1 GoF (via MONDO_0013599) and STAT1 LoF (MONDO_0013427); FAIL for the umbrella CMC node and for `EFO_0010647` "STAT3 gain of function" — use the `DIS-DIS PARENT` edges to roll phenotypes up.

### 3.4 Phenotype nodes
`nodes/phenotype.parquet` (19,341 rows): `id` = `HP_0001250` style (19,177 `HP_`, 164 `meddra…`). `sources.direct=['OPEN_TARGETS','HPO','MEDDRA']`. Properties: `sources, name, description, code, xrefs, exact_synonyms, related_synonyms, narrow_synonyms, broad_synonyms, obsolete_terms, obsolete_xrefs, type ('phenotype'), ontology: struct<description, title='Human Phenotype Ontology', license='https://hpo.jax.org/app/license', version='2025-05-06'>, concept_ids, concept_names, umls_cui, snomed_full_names, cui_semantic_type, snomed_concept_ids`. HPO version embedded = **2025-05-06**. Note the `HP_` underscore form here vs `HP:0003577` colon form inside DIS-PHE `onset`/`modifiers` values.

### 3.5 Edge property names — which of the requested ones exist (VERIFIED in Parquet structs)

**`edges/disease_gene.parquet`** (9,734,774 rows, 80 row groups × 122,880; `from`=disease id, `to`=Ensembl gene id; sorted by `from`):
```
properties: struct<evidence_score: double, evidence_count: int64, disease_specificity_index: double, disease_pleiotropy_index: double,
                   evidence_index: double, disgenet_score: double, year_initial: large_string, year_final: large_string,
                   number_of_pmids: int16, number_of_snps: int16, sources: struct<direct: list<str>, indirect: list<str>>>
```
Footer statistics over all rows (0 bytes of data read): `evidence_score` **0.0009095 – 0.9485794**, nulls 0; `evidence_count` 1 – 197,300, nulls 0; `disgenet_score` 0.3 – 1.0, **nulls 9,463,636 (97.2 %)**; `number_of_pmids` 0 – 136 (same null count); `year_initial` '1940'–'2020', `year_final` '1966'–'2020' (strings, nulls 9,465,027); `disease_specificity_index` 0.231–1.0; `disease_pleiotropy_index` 0.038–0.962; `evidence_index` -0.0–1.0; `sources.direct` ∈ {`DISGENET`, `OPEN_TARGETS`}; `sources.indirect` ranges `CGI`…`UNIPROT` (seen: CTD, ORPHANET, GENOMICS_ENGLAND, UNIPROT, CGI). Docs describe `evidence_score` as "Aggregated association evidence score" and `evidence_count` as "Number of evidence items supporting the association"; `year_initial`/`year_final`/`number_of_pmids`/`number_of_snps`/`disgenet_score`/DSI/DPI are DisGeNET v7.0 fields (null unless DisGeNET contributed). **No `references`, `relation_assertions`, `relation_conflict`, `evidence_type` on DIS-GEN.**
Sample row: `{'from': 'DOID_0050890', 'to': 'ENSG00000000971', 'label': 'DIS-GEN', 'relation': 'ASSOCIATED_WITH', 'undirected': True, 'properties': {'evidence_score': 0.0037, 'evidence_count': 1, ... 'sources': {'direct': ['OPEN_TARGETS'], 'indirect': []}}}`.
Cluster check (read only the 4 row groups holding these diseases, 3.7 MB): `MONDO_0010938` JAK3-SCID → JAK3 `evidence_score 0.8389`, `evidence_count 1172`, `disgenet_score 0.6`, `number_of_pmids 6`, years 1995–2004, sources DISGENET+OPEN_TARGETS (indirect CTD, GENOMICS_ENGLAND, ORPHANET, UNIPROT); also IL2RG 0.0774 (OT only). `MONDO_0015279` CMC → STAT1 0.7443 (737 evidence, disgenet 0.5, 1 pmid, 2011–2020) and 467 other genes (TYK2 0.003, STAT3 0.0209 …) — Open Targets' indirect/aggregated associations make DIS-GEN very dense (468 genes for CMC, 1,157 for PV): **threshold on `evidence_score` and/or require `DISGENET` in `sources.direct`.** `EFO_0002429` PV → JAK2 0.8015 (disgenet 1.0, 12 pmids), JAK1 0.5391. `MONDO_0007818` HIES1 → STAT3 0.7044 (OT only; 3 edges total).

**`edges/disease_phenotype.parquet`** (157,144 rows; `from` ∈ MONDO_ 119,497 / Orphanet_ 29,653 / EFO_ 7,994; `to` = `HP_…`; `sources.direct=['OPEN_TARGETS']`, `indirect=['HPO']`):
```
properties: struct<aspect: list<str>, bio_curation: list<str>, evidence_type: list<str>, frequency: list<str>, modifiers: list<str>, onset: list<str>,
                   qualifier_not: bool, references: list<str>, sexes: list<str>, sources: struct<direct, indirect>,
                   relation_assertions: list<struct<source: str, relation: str>>, relation_conflict: bool>
```
Observed values: `evidence_type` ∈ `["PCS"]` 98,848 · `["IEA"]` 33,132 · `["TAS"]` 21,954 · mixed lists; `frequency` e.g. `["20/20"]`, `["1/1"]`, empty 65,753 (HPO `HP:0040281`-style frequency terms UNVERIFIED — only ratio strings observed in the top values); `onset` e.g. `["HP:0003577"]` (Congenital onset), empty 154,018; `aspect` ∈ P/C/I/M/H; `references` e.g. `["OMIM:151400"]`, `["PMID:21700266"]`, ISBN; `bio_curation` e.g. `"HPO:iea[2009-02-17];HPO:skoehler[2012-10-17]"`; `sexes` ∈ MALE/FEMALE; `qualifier_not` all False; `relation_conflict` all False. **frequency, onset, evidence_type, references: all exist here.** Phenotype information-content can be computed from this file (157k annotations over ~all HPO-annotated diseases).

**`edges/phenotype_phenotype.parquet`** (26,121; `PARENT`, directed): `properties: struct<sources>` only.
**`edges/pathway_gene.parquet`** (46,977; `from`=`REACT:R-HSA-…` (colon CURIE!), `to`=Ensembl; `INTERACTS_WITH`; `sources.direct=['OPEN_TARGETS'], indirect=['REACTOME']`): `properties: struct<sources>` only. 174 edges touch the 7 cluster genes (e.g. `REACT:R-HSA-1059683` 'Interleukin-6 signaling' ↔ JAK1/JAK2/TYK2/STAT1/STAT3; `REACT:R-HSA-1169408` 'ISG15 antiviral mechanism' ↔ STAT1/JAK1). Pathway node ids are `REACT:R-HSA-1059683` (2,805 rows, `properties: sources, name, species`).
**`edges/biological_process_gene.parquet`** (158,410; `from`=`GO_0000002`, `to`=Ensembl):
```
properties: struct<sources: struct<direct, indirect>, evidence: list<str>, gene_product: list<str>, eco_ids: list<str>>
```
**GO evidence codes exist as `properties.evidence`** (values seen: IEA 63,886 · IDA 17,001 · IMP 11,930 · IBA 11,367 · TAS 6,636 · ISS …) plus `eco_ids` (e.g. `ECO:0000265`, `ECO:0000314`) and `gene_product` (UniProt acc). `sources.indirect` carries the reference (`PMID:9924029`, `GO_REF:0000024`). 378 BPO-GEN edges touch cluster genes (JAK2 111, STAT3 104, STAT1 51, JAK3 34, JAK1 28, TYK2 27, IL2RG 23).
**`edges/drug_gene.parquet`** (36,611; `from`=drug id, `to`=Ensembl; directed):
```
properties: struct<source_ids: list<str>, source_urls: list<str>, mechanisms_of_action: list<str>, sources: struct<direct, indirect>,
                   relation_assertions: list<struct<source, relation>>, relation_conflict: bool>
```
`relation` values seen: TARGET 13,459 · INHIBITOR 5,956 · ENZYME 5,301 · TRANSPORTER · ANTAGONIST · AGONIST · BLOCKER …; `relation_conflict=True` on only 2 rows. 124 edges touch cluster genes (JAK2 39, JAK1 29, JAK3 25, TYK2 18, IL2RG 10, STAT3 3, **STAT1 0**). Ruxolitinib `CHEMBL1789941` → JAK2 `INHIBITOR` and → JAK1 `INHIBITOR` (`mechanisms_of_action=['Tyrosine-protein kinase JAK2 inhibitor']`, `source_urls=['http://www.accessdata.fda.gov/drugsatfda_docs/label/2012/202192s001lbl.pdf']`, `sources.direct=['DRUG_BANK','OPEN_TARGETS'], indirect=['FDA']`), plus → `ENSG00000160868` `ENZYME` (DrugBank).
**`edges/drug_disease.parquet`** (70,380; `to` ∈ EFO_ 49,666 / MONDO_ 19,847 / Orphanet_ / MP_ / DOID_):
```
properties: struct<sources, structure_id: str, drug_disease_id: str, reference_ids: list<str>, highest_clinical_trial_phase: double,
                   relation_assertions: list<struct<source, relation>>, relation_conflict: bool>
```
`relation`: INDICATION 58,174 · CONTRAINDICATION 11,379 · OFF_LABEL_USE 827; `relation_conflict=True` on 573 rows. `reference_ids` are NCT ids (`['NCT01392443', …]`, `sources.indirect=['CLINICAL_TRIALS']`) or ATC codes. Ruxolitinib has 66 DRG-DIS edges incl. `EFO_0000479` essential thrombocythemia `INDICATION` phase 2.0 (NCT01392443, NCT02577926, NCT03878199). **Nothing links ruxolitinib to STAT1 GoF / CMC** (expected — that is the literature layer's job).

Summary of requested property names → where they exist:

| property | DIS-GEN | DIS-PHE | BPO-GEN | DRG-GEN | DRG-DIS | PWY-GEN / PHE-PHE / DIS-DIS |
|---|---|---|---|---|---|---|
| `evidence_score`, `evidence_count` | yes | – | – | – | – | – |
| `number_of_pmids`, `year_initial`, `year_final` (+`number_of_snps`, `disgenet_score`, DSI, DPI, `evidence_index`) | yes (DisGeNET rows only) | – | – | – | – | – |
| `frequency`, `onset`, `evidence_type`, `references` (+`aspect`, `modifiers`, `sexes`, `qualifier_not`, `bio_curation`) | – | yes | – | – | – | – |
| GO evidence codes | – | – | `evidence` + `eco_ids` | – | – | – |
| `sources` (`direct`/`indirect`) | yes | yes | yes | yes | yes | yes |
| `relation_assertions`, `relation_conflict` | **no** | yes | **no** | yes | yes | no |
| `reference_ids`, `highest_clinical_trial_phase` | – | – | – | – | yes | – |
| `mechanisms_of_action`, `source_ids`, `source_urls` | – | – | – | yes | – | – |

### 3.6 Drug nodes
`nodes/drug.parquet` (18,640 rows, 1 row group, 33 MB — 2 base64 columns dominate). `id` = `CHEMBL1789941` style (bare ChEMBL id; 2,758 rows `DRUGBANK…`, 62 `RXNORM…`). Properties: `name, inchi_key, type, synonyms: list<str>, description, accession_numbers, canonical_smiles, chemical_abstracts_service_number, unique_ingredient_identifier, black_box_warning, year_of_first_approval, maximum_clinical_trial_phase, has_been_withdrawn, is_approved, trade_names, sources, source_ids, cd_id, cd_formula, cd_mol_weight, …, mol_file_base64, mol_image_base64, …, status, struct_id`. Ruxolitinib: `id CHEMBL1789941`, `name 'RUXOLITINIB'`, `type 'Small molecule'`, `is_approved True`, `trade_names ['Jakavi']`, `source_ids ['66919','DB08877']` (DrugCentral id, DrugBank id), `sources.direct ['DRUG_BANK','DRUG_CENTRAL','OPEN_TARGETS']`. Reading only `id,label,properties.name,properties.synonyms,properties.trade_names,properties.sources,properties.source_ids,properties.is_approved,properties.type` via Range cost 0.99 MB instead of 33 MB.

### 3.7 Range-read recipe (works; no full download)
`scripts/range_footer.py`, `scripts/range_cols.py`, `scripts/range_stats.py`, `scripts/range_disgen_for_diseases.py` in the recon dir implement a `pa.PythonFile` over HTTP `Range` on the presigned S3 URL (`requests.get(api, allow_redirects=False).headers["Location"]`, then `Range: bytes=a-b`). pyarrow 19 (`/opt/anaconda3/bin/python3 -c 'import pyarrow'` OK) reads footers (65–200 KB) and selected leaf columns (`pf.read(columns=["id","properties.symbol"])`). Note `polars` and `duckdb` are **not** installed in `/opt/anaconda3`; `fsspec 2025.3.2` and `pandas 2.2.3` are.

---

## 4. Licenses — VERIFIED text

* **Data (Dataverse `latestVersion.license`)**: `{"name": "CC BY-NC-SA 4.0", "uri": "http://creativecommons.org/licenses/by-nc-sa/4.0"}`. No additional `termsOfUse`.
* **Code (repo `LICENSE`, `packages/optimuskg/LICENSE`, PyPI `license`)**: MIT, "Copyright (c) 2025 Zitnik Lab, Department of Biomedical Informatics, Harvard Medical School".
* **Official license page** (https://optimuskg.ai/docs/license and `docs/content/docs/license.mdx`), full text: "The OptimusKG codebase is released under the MIT License. OptimusKG integrates multiple primary data resources, each of which is subject to its own license and terms of use. These terms may impose restrictions on redistribution, commercial use, or downstream applications of the resulting knowledge graph or its subsets. Some resources provide data under academic or noncommercial licenses, while others may impose attribution or usage requirements. As a result, use of OptimusKG may be partially restricted depending on the specific data components included in a given instantiation. Users are responsible for reviewing and complying with the license and terms of use of each primary dataset, as specified by the original data providers. OptimusKG does not alter or override these source-specific licensing conditions."
* **Upstream notes found in the repo:**
  * DisGeNET — `conf/base/catalog/landing/disgenet/disgenet.yml` header: "Provenance (DisGeNET v7.0, January 2020): Original download: https://www.disgenet.org/static/disgenet_ap1/files/downloads/curated_gene_disease_associations.tsv.gz … License: Creative Commons Attribution-NonCommercial-ShareAlike 4.0 (CC BY-NC-SA 4.0)." → the DIS-GEN DisGeNET fields (`disgenet_score`, `number_of_pmids`, years, DSI/DPI) are NC-SA.
  * DrugBank — `conf/base/catalog/landing/drugbank/full_database.yml` loads `data/landing/drugbank/full database.xml` (DrugBank full database, v5-1-13 per `vocabulary.yml` `origin.version`); the repo README says private datasets are placed under `data/loading` and the pipeline creates empty placeholders if absent. DrugBank's own license terms were **not fetched (UNVERIFIED)**; in the released Parquet DrugBank appears as `sources.direct` value `DRUG_BANK` on DRG-GEN/DRG-DIS/drug nodes and as `xrefs.source='DrugBank'` on genes.
  * HPO — embedded in every phenotype node: `properties.ontology.license = 'https://hpo.jax.org/app/license'`, version 2025-05-06. GO nodes embed an `ontology` struct too.
  * Other `sources.direct`/`indirect` values seen: OPEN_TARGETS, BGEE, MONDO, DRUG_CENTRAL, ONSIDES, HPO, MEDDRA, GO, REACTOME, CTD, ORPHANET, GENOMICS_ENGLAND, UNIPROT, CGI, FDA, DAILY_MED, PUBMED, CLINICAL_TRIALS, ATC. Per-source terms UNVERIFIED (not fetched).
* Practical reading: the released graph is CC BY-NC-SA 4.0 → our derived `graph.json` must be non-commercial + share-alike + attributed ("Vittor L, Noori A, Arango I, Polonuer J, Rodriques S, White A, Clifton DA, Zitnik M. OptimusKG. Harvard Dataverse, doi:10.7910/DVN/IYNGEV, v3.2" — citation format per https://optimuskg.ai/docs/citation UNVERIFIED beyond the BibTeX in the README).

---

## 5. Recommendation

**Download the stratified Parquet files directly over HTTP and read them with Polars (or pyarrow); do not depend on the `optimuskg` client.** Reasons, all observed today:

1. The client is ~250 lines that do exactly `GET /api/datasets/:persistentId/` → map `directoryLabel/filename` → id → `GET /api/access/datafile/<id>` → `pl.read_parquet`. We already have the 41 file ids (section 2.2); hard-coding `id` + `md5` for the 12–15 files we need removes the metadata call entirely.
2. **Out of the box the client fails with HTTP 403** because Harvard Dataverse blocks the `python-requests` User-Agent on `/api/datasets/*` (reproduced three times; curl, empty UA and `optimuskg/1.0.0` UA all get 200). It is fixable with a monkeypatch, but that is a trap for a 45-minute spike and for anyone reproducing the dataset later.
3. Pulling the client pulls `polars>=1.19` **and** `networkx>=3.0` into `pyproject.toml`/`uv.lock` (locked after M0). Plain HTTP needs only pyarrow/polars.
4. The client always downloads whole files; the two big ones (`gene.parquet` 43 MB, `disease_gene.parquet` 73 MB) can be read partially (Range + column projection: 3.3 MB for all gene ids/symbols/xrefs; row-group pruning by disease id using footer stats for DIS-GEN).
5. Direct download gives us the md5 from the dataset JSON for integrity checks, and the dataset `versionNumber.versionMinorNumber` (`3.2`) and `releaseTime` for the README license table ("version + retrieval date").

Concrete plan for `pipeline/sources/optimuskg.py` (fits the 45-minute box):
```bash
# one-off, or inside pipeline/cache.py's rate-limited client with a custom User-Agent
for id in 14220882 14220892 14220905 14220888 14220918 14220915 14220914 14220896 14220919 14220895 14220904 14220916; do
  curl -fL --retry 5 -A "pyrid-atlas/0.1" "https://dataverse.harvard.edu/api/access/datafile/$id" -o "data/cache/optimuskg/$id.parquet"
done   # 125 MB without drug.parquet (14220910, +33 MB)
```
```python
import polars as pl
genes = pl.read_parquet("nodes/gene.parquet", columns=["id","properties"]).select(
    "id", pl.col("properties").struct.field("symbol"), pl.col("properties").struct.field("name"),
    pl.col("properties").struct.field("xrefs"), pl.col("properties").struct.field("symbol_synonyms"))
cluster = genes.filter(pl.col("symbol").is_in(["STAT1","STAT3","JAK1","JAK2","JAK3","TYK2","IL2RG"]))
dg = pl.scan_parquet("edges/disease_gene.parquet").filter(pl.col("to").is_in(cluster["id"])).collect()  # lazy scan avoids materialising 9.7M rows
```
(Polars `scan_parquet` + filter on the 73 MB file is UNVERIFIED here because polars is not in the anaconda env; pyarrow `ParquetFile.iter_batches` is the fallback.)

Gotchas to carry into the spike doc:
* Disease ids are `MONDO_0015279` / `EFO_0002429` / `Orphanet_35078` (underscore), gene ids are bare `ENSG…`, pathway ids are `REACT:R-HSA-…` (colon), GO ids `GO_0000002`, HPO ids `HP_0001250` in node/edge keys but `HP:0003577` inside DIS-PHE `onset`/`modifiers` values. Normalise CURIEs once at load.
* `nodes/disease.parquet` has **no** `parents/ancestors` columns despite the docs; use `edges/disease_disease.parquet` (179 KB).
* DIS-GEN is dominated by Open Targets aggregate associations (468 genes for CMC); `evidence_score` spans 0.0009–0.9486; require DisGeNET or a score threshold before treating an edge as "known gene". DisGeNET is v7.0 (Jan 2020), so `year_final` ≤ 2020.
* The CMC umbrella node and `EFO_0010647` "STAT3 gain of function" have zero DIS-PHE edges; STAT1 GoF phenotypes live on `MONDO_0013599` (IMD31C) and STAT1 LoF on `MONDO_0013427` (IMD31B). No disease node mentions JAK1.
* `drug_gene` has no STAT1 edges; ruxolitinib targets are JAK1/JAK2 only (`INHIBITOR`).
* If the client *is* used anyway: patch `requests.utils.default_user_agent` before `import optimuskg`, set `OPTIMUSKG_CACHE_DIR` to the shared `ATLAS_CACHE_DIR`, and never call `load_graph`/`load_networkx`.

---

## Re-run check (second reviewer)

Re-run date: 2026-10-03 (same day, ~2 h after the original). Working dir for this pass:
`/private/tmp/claude-501/-Users-jonathanyan-Desktop-projects-Pyrid/8911b015-0fc5-4590-95d8-bd29f4230acc/scratchpad/recon/rerun_optimuskg/`
(every raw response and script output referenced below is saved there: `pypi.json`, `dataverse_dataset.json`, `pathway.parquet`, `dataverse_files.zip`, `llms-full.txt`, `_dataverse.py`, `api.py`, `__init__.py`, `edges.mdx`, `nodes.mdx`, `schema_config.yaml`, `disgenet.yml`, `gh_tree.json`, `range_footer_gene.out`, `range_stats_disgen.out`, `range_cols_gene.out`, `gene_subset.parquet`, `range_disgen.out`, `inspect_small.out`, `footer_nodes_unified.out`, `footer_edges_unified.out`, `drug_subset.parquet`, `gene_check.out`, `disease_check.out`, `edge_check.out`, `direction_check.out`, `final_spot.out`, `.venv-okg2/`, `okg-cache2/`).
No NCBI endpoint is involved in this source, so the 3 req/s rule did not apply; nothing larger than 3.7 MB was fetched (the biggest single read was the column-projected gene subset, 3,285,085 bytes).

**Verdict: every command listed under "verified" ran and matched.** The numbers, URLs, file ids, md5s, field paths and HTTP behaviours in sections 0–5 hold as of today. I found no URL that redirects somewhere other than claimed, no count copied wrong, and nothing marked VERIFIED that I could not reproduce. I did find **one semantic error** (the direction of `PARENT` hierarchy edges is stated backwards), **one UNVERIFIED item that resolves the opposite way** (HP-term frequencies do exist, in underscore form) and a handful of imprecise wordings. Details below.

### R.1 Command-by-command results

| # | Command (as listed) | Result | Matches notes? |
|---|---|---|---|
| 1 | `curl -sL "https://pypi.org/pypi/optimuskg/json"` | HTTP 200. `version 1.0.0`, `requires_python >=3.12`, `requires_dist ['networkx>=3.0','platformdirs>=4.0.0','polars>=1.19.0','pyarrow>=19.0.0','requests>=2.32.3']`, 4 release files `0.1.0` (9296 / 9721 B, 2026-04-17T02:50) and `1.0.0` (10513 / 11019 B, 2026-04-17T03:34), license text starts "MIT License  Copyright (c) 2025 Zitnik Lab…" | yes |
| 2 | `curl -sL "https://dataverse.harvard.edu/api/datasets/:persistentId/?persistentId=doi:10.7910/DVN/IYNGEV" -o dataverse_dataset.json` | HTTP 200, 39,086 B. `status OK`, `data.id 13645730`, `versionNumber 3`, `versionMinorNumber 2`, `versionState RELEASED`, `releaseTime 2026-09-09T04:14:10Z`, `license {"name":"CC BY-NC-SA 4.0","uri":"http://creativecommons.org/licenses/by-nc-sa/4.0"}`, `termsOfUse None`, `fileAccessRequest True`, 41 files, every `restricted False`, every `contentType application/octet-stream`, `directoryLabel` ∈ {`nodes`,`edges`,absent}. Sum of all 41 `filesize` = **822,113,189**. All 41 `dataFile.id`/`filesize` pairs in §2.2 re-checked one by one: identical. `nodes/gene.parquet` md5 `edf81a21db68658381c063e21d1080d2`. Core-12 total **158,398,652**, minus drug **125,437,793**, core+optional-15 **160,202,282**. `dataFile.persistentId` is `""` on every file (so "no per-file persistentId" holds). | yes |
| 3 | `curl -sS -L --max-time 180 "https://dataverse.harvard.edu/api/access/datafile/14220888" -o pathway.parquet` | 303 → `https://dvn-cloud-iqss.s3.amazonaws.com/10.7910/DVN/IYNGEV/1a08437db2b-cbd88a9eaf55?...&X-Amz-Expires=3600&...` → 200, **50,610 B**, `content_type application/octet-stream`; local md5 `7f2b6f03145447405a236eb566a402fa` = Dataverse md5. Parquet: 2,805 rows, 1 row group, `created_by Polars`, ids all `REACT:` (e.g. `REACT:R-HSA-1059683` 'Interleukin-6 signaling', `REACT:R-HSA-1169408` 'ISG15 antiviral mechanism'), `properties: struct<sources, name, species>`. | yes |
| 4 | `curl -sS -L "https://dataverse.harvard.edu/api/access/datafiles/14220888,14220906" -o dataverse_files.zip` | HTTP 200, 67,432 B, `content_type application/zip; name="dataverse_files.zip"`; `unzip -l` → `nodes/pathway.parquet` 50,610 B + `edges/pathway_pathway.parquet` 19,052 B (directory labels preserved). | yes |
| 5 | `curl -sI "https://dataverse.harvard.edu/api/access/datafile/14220888"` | `HTTP/2 303`, `location: https://dvn-cloud-iqss.s3.amazonaws.com/10.7910/DVN/IYNGEV/<key>?response-content-disposition=attachment%3B%20filename%2A%3DUTF-8%27%27pathway.parquet&response-content-type=application%2Foctet-stream&X-Amz-Algorithm=AWS4-HMAC-SHA256&...&X-Amz-Expires=3600&X-Amz-Signature=...`, `server: Apache`. | yes |
| 6 | `curl -s -o /dev/null -w "HTTP %{http_code}\n" -A "python-requests/2.32.3" ".../api/datasets/:persistentId/?persistentId=doi:10.7910/DVN/IYNGEV"` | **HTTP 403**. Also 403 with `-A "Python-urllib/3.13"` and on `/api/datasets/13645730`. | yes |
| 7 | same with `-A "optimuskg/1.0.0"` | **HTTP 200**. Also 200 with `-A ""`. `-I -A "python-requests/2.32.3"` on `/api/access/datafile/14220888` → 303 (not blocked). | yes |
| 8 | `curl -sL "https://optimuskg.ai/llms-full.txt" -o llms-full.txt` | HTTP 200, **55,131 B**. Contains 39 `<DocsSchemaTree fields="…" />` tags (e.g. line 396 `fields="disGenFields"`, line 584 `fields="diseaseFields"`) but **zero** occurrences of `parents`/`ancestors`/`export const diseaseFields` → the field tables really are absent from llms-full.txt. Quotes at lines 64 ("The client fetches files from the gold layer with local caching…") and 715 ("`load_parquet` calls `get_file` then `polars.read_parquet`. Any extra keyword arguments are forwarded directly to `pl.read_parquet`.") and 719 (`only_ids = optimuskg.load_parquet("nodes/drug.parquet", columns=["id"])`) are verbatim. | yes |
| 9 | `curl -sL "https://raw.githubusercontent.com/mims-harvard/optimuskg/main/packages/optimuskg/src/optimuskg/_dataverse.py"` | HTTP 200. Confirms: `requests.get(f"{server}/api/datasets/:persistentId/", params={"persistentId": doi}, timeout=60)`; version = `f"{versionNumber}.{versionMinorNumber}"`; reads `files[].dataFile.id`, `.filename`, `directoryLabel`; `to_relative(directory_label, filename)`; download `GET {server}/api/access/datafile/{entry.id}` with `stream=True, timeout=300`, `_CHUNK = 1 << 20`, writes `.part` then `tmp.replace(local_path)`; cache layout `<cache_dir>/<doi_slug>/<version>/<relative_path>`; no custom headers, no retries, no checksum. `__init__.py`: `__version__ = "0.1.0"`; `__all__` exactly as quoted. `api.py` line 60–62: `warnings.warn("Loading the full graph into NetworkX requires several GB of memory (190k nodes, 21M edges). …")`. `_config.py`: `DEFAULT_SERVER = "https://dataverse.harvard.edu"`, `DEFAULT_DOI = "doi:10.7910/DVN/IYNGEV"`, env `OPTIMUSKG_SERVER` / `OPTIMUSKG_DOI` / `OPTIMUSKG_CACHE_DIR`, `user_cache_dir("optimuskg")`. | yes |
| 10 | `curl -sL ".../docs/content/docs/graph-schema/edges.mdx" -o edges.mdx` | HTTP 200, 18,564 B. Line 236–238: "## One edge per node pair … OptimusKG guarantees **at most one edge per (`from`, `to`, `label`) triple**"; line 244 `RELATION_PRIORITY`; lines 35–47 define `relation_assertions` / `relation_conflict`. `nodes.mdx` (23,790 B) lines 294–317: `export const diseaseFields` includes `parents`, `ancestors`, `descendants`, `is_leaf` (and lines 342–345 the same for phenotype) → the docs/data mismatch in §3.3 is real. | yes |
| 11 | `curl -sL ".../conf/base/biocypher/schema_config.yaml"` | HTTP 200, 14,729 B; starts with `gene: represented_as: node, input_label: GEN, properties: sources, symbol, biotype, …, xrefs: str[]`. | yes |
| 12 | `curl -sL ".../conf/base/catalog/landing/disgenet/disgenet.yml"` | HTTP 200, 1,531 B. Header: "# Provenance (DisGeNET v7.0, January 2020): Original download: https://www.disgenet.org/static/disgenet_ap1/files/downloads/curated_gene_disease_associations.tsv.gz … License: Creative Commons Attribution-NonCommercial-ShareAlike 4.0 (CC BY-NC-SA 4.0)." Columns `DSI`, `DPI`, `score`, `EI`, `YearInitial`, `YearFinal`, `NofPmids`, `NofSnps`. | yes |
| 13 | `curl -sL "https://api.github.com/repos/mims-harvard/optimuskg/git/trees/main?recursive=1" -o gh_tree.json` | HTTP 200, 255,012 B, `truncated: false`, 826 paths. Every path the notes cite exists: `packages/optimuskg/src/optimuskg/{__init__,api,_config,_dataverse,_graph,_paths}.py`, `packages/optimuskg/pyproject.toml` (`version = "1.0.0"`, hatchling), root `pyproject.toml` (`version = "0.74.2"`), `skills/optimuskg/SKILL.md`, `skills/optimuskg/reference/graph-schema.md`, `conf/base/biocypher/schema_config.yaml`, `conf/base/catalog/landing/disgenet/disgenet.yml`, `conf/base/catalog/landing/drugbank/{full_database,vocabulary}.yml` (`filepath: data/landing/drugbank/full database.xml`; `origin.version: "5-1-13"`), `docs/content/docs/graph-schema/{edges,nodes}.mdx`, `docs/content/docs/license.mdx` (text identical to the §4 quote), `LICENSE` + `packages/optimuskg/LICENSE` (both "MIT License / Copyright (c) 2025 Zitnik Lab…"). GitHub API `license.spdx_id: MIT`. | yes |
| 14 | `/opt/anaconda3/bin/python3 -c 'import pyarrow; print(pyarrow.__version__)'` | `19.0.0`; `requests 2.32.3`; `polars`, `duckdb` → `ModuleNotFoundError`; `fsspec 2025.3.2`, `pandas 2.2.3`. | yes |
| 15 | `range_footer.py 14220882 "nodes/gene.parquet"` | 303 → S3; `Range: bytes=0-7` → 206, `Content-Range: bytes 0-7/43261973`, `Accept-Ranges: bytes`, magic `PAR1`; `rows=61,306 row_groups=1 created_by=Polars`; schema identical to §3.2 (`properties.xrefs: large_list<struct<id, source>>`, `properties.symbol: large_string`, …); **65,536 bytes fetched in 1 range request**. | yes |
| 16 | `range_stats.py 14220918 "edges/disease_gene.parquet" 0` | `size=72,754,866 rows=9,734,774 row_groups=80`; `properties.evidence_score nulls=0 min=0.0009095429946858309 max=0.9485794097813081`; `evidence_count 1–197300 nulls 0`; `disgenet_score 0.3–1.0 nulls 9,463,636`; `number_of_pmids 0–136`; `number_of_snps 0–2258`; `year_initial '1940'–'2020'`, `year_final '1966'–'2020'` nulls 9,465,027; `disease_specificity_index 0.231–1.0`; `disease_pleiotropy_index 0.038–0.962`; `evidence_index -0.0–1.0`; `sources.direct` min `DISGENET` max `OPEN_TARGETS`; `sources.indirect` `CGI`…`UNIPROT`. Row group 0 = 122,880 rows, first row `{'from': 'DOID_0050890', 'to': 'ENSG00000000971', 'label': 'DIS-GEN', 'relation': 'ASSOCIATED_WITH', 'undirected': True, 'properties': {'evidence_score': 0.00369…, 'evidence_count': 1, …}}`. 1,155,960 bytes fetched. | yes |
| 17 | `range_cols.py 14220882 "nodes/gene.parquet" gene_subset.parquet id label properties.sources … properties.obsolete_symbols` | 61,306 rows; **3,285,085 bytes fetched in 14 range requests** (of 43,261,973). From the fresh subset: ids `ENSG…` 61,108 / `NCBIGene:…` 198; `xrefs.source` counts PDB 120,205 · InterPro 89,213 · Reactome 46,618 · **HGNC 41,270** · DrugBank 27,955 · ProjectScore 7,186 · ChEMBL 4,867 · signalP 3,686; `symbol_synonyms.source` ∈ NCBI_entrez / uniprot / HGNC. **Each of STAT1/STAT3/JAK1/JAK2/JAK3/TYK2/IL2RG → exactly 1 node on `properties.symbol`** with the ids and HGNC xrefs (11362, 11364, 6190, 6192, 6193, 12440, 6010) in the §3.2 table; all `protein_coding`, `sources.direct ['OPEN_TARGETS','BGEE']`; no other node lists any of the seven as a `symbol_synonyms[].label`. (395 symbols in the file are shared by >1 node, none of ours.) | yes |
| 18 | `range_disgen_for_diseases.py` | row groups `{'EFO_0002429': [8], 'MONDO_0007818': [49], 'MONDO_0010938': [51], 'MONDO_0015279': [54]}`; CMC 468 edges (STAT1 0.7443 / 737 / disgenet 0.5 / 1 pmid / 2011–2020, sources OPEN_TARGETS+DISGENET indirect CTD; TYK2 0.003; STAT3 0.0209; JAK3 0.0374; IL2RG 0.0314); JAK3-SCID 701 edges (JAK3 0.8389 / 1172 / 0.6 / 6 pmids / 1995–2004, indirect CTD, GENOMICS_ENGLAND, ORPHANET, UNIPROT; IL2RG 0.0774 OT-only); PV 1,157 edges (JAK2 0.8015 / disgenet 1.0 / 12 pmids; JAK1 0.5391); HIES1 3 edges (STAT3 0.7044 OT-only). **3,719,301 bytes in 70 range requests.** | yes |
| 19 | `inspect_small.py` | exit 0, 360 lines. Reads the first reviewer's `parquet/*.parquet`; **I md5-checked all 12 Dataverse-origin files there against `dataFile.md5` — all MATCH** (so the local copies are byte-identical to what Dataverse serves today). Counts reproduced: disease 36,753 (`EFO` 11,960 · `MONDO` 11,724 · `OBA` 10,997 · `Orphanet` 2,045 · `OTAR` 9 · `DOID` 6 …), phenotype 19,341 (`HP` 19,177 · `meddra` 164), DIS-PHE 157,144 (2 row groups), PHE-PHE 26,121, PWY-GEN 46,977 (174 touch cluster genes), PWY-PWY 2,819, BPO 25,754, BPO-GEN 158,410 (378 touch cluster genes: JAK2 111, STAT3 104, STAT1 51, JAK3 34, JAK1 28, TYK2 27, IL2RG 23), DRG-GEN 36,611 (TARGET 13,459 · INHIBITOR 5,956 · ENZYME 5,301 …; 33 distinct relations; `relation_conflict` true on 2; 124 touch cluster genes, **STAT1 0**; ruxolitinib 3 edges), DRG-DIS 70,380 (INDICATION 58,174 · CONTRAINDICATION 11,379 · OFF_LABEL_USE 827; conflict 573; ruxolitinib 66). | yes |
| 20 | `uv venv .venv-okg && uv pip install --python .venv-okg/bin/python optimuskg` (fresh dir `.venv-okg2`, uv 0.12.1) | Installs `optimuskg==1.0.0`, `networkx==3.7`, `platformdirs==4.12.2`, `polars==1.44.2`, `pyarrow==25.0.1`, `requests==2.34.2`, `urllib3==2.8.0` on CPython 3.12.13. `optimuskg.__version__ == "0.1.0"`, `importlib.metadata.version("optimuskg") == "1.0.0"`. | yes |
| 21 | `OPTIMUSKG_CACHE_DIR=… python -c '…get_file("nodes/pathway.parquet")'` without / with UA patch | **Without patch (2 attempts): `requests.exceptions.HTTPError: 403 Client Error: Forbidden for url: https://dataverse.harvard.edu/api/datasets/:persistentId/?persistentId=doi%3A10.7910%2FDVN%2FIYNGEV`** (default UA is now `python-requests/2.34.2`, still blocked; the 403 body is the Apache "403 Forbidden" HTML). **With** `requests.utils.default_user_agent = lambda name="python-requests": "pyrid-atlas/0.1"` before `import optimuskg`: returns `okg-cache2/doi_10_7910_DVN_IYNGEV/3.2/nodes/pathway.parquet`, `fetch_metadata().version == "3.2"`, 41 files, `load_parquet("nodes/pathway.parquet", columns=["id"])` → `(2805, 1)` Polars frame. Raw `requests.get` with default UA: `/api/datasets/...` → 403, `/api/access/datafile/14220888` → 303 then 200 (50,610 B). | yes |

Extra verifications not in the command list but claimed VERIFIED in the notes, all reproduced: unified `nodes.parquet` (id 14220908) footer → `rows=192,813 row_groups=2`, schema `id, label, properties: large_string`, 65,536 B fetched; unified `edges.parquet` (14220909) footer → `rows=21,834,669 row_groups=178`, schema `from, to, label, relation: large_string; undirected: bool; properties: large_string`, 187,461 B fetched; `nodes/drug.parquet` column-projected read (`id label properties.name properties.synonyms properties.trade_names properties.sources properties.source_ids properties.is_approved properties.type`) → 18,640 rows, 1 row group, **983,089 B fetched**, ids `CHEMBL` 15,820 / `DRUGBANK` 2,758 / `RXNORM` 62, ruxolitinib = `{"id": "CHEMBL1789941", "label": "DRG", "properties": {"name": "RUXOLITINIB", "type": "Small molecule", "synonyms": ["Ruxolitinib","INC-424","INC424","INCB-018424","INCB-18424","INCB018424"], "is_approved": true, "trade_names": ["Jakavi"], "sources": {"direct": ["DRUG_BANK","DRUG_CENTRAL","OPEN_TARGETS"], "indirect": []}, "source_ids": ["66919","DB08877"]}}`; `HEAD` on the presigned S3 URL → 403 while `GET` + `Range: bytes=0-7` → 206 (so "use GET+Range, not HEAD" holds); all 10 docs pages + `/llms.txt` → 200; `disease.parquet`/`phenotype.parquet` struct fields contain none of `parents, children, ancestors, descendants, is_leaf`; DIS-PHE edge counts for every id in the §3.3 table reproduced exactly (0, 42, 4, 0, 3, 20, 22, 53, 0, 0, 20, 10, 11, 0/4, 7, 0); `MONDO_0013599` xrefs `['Orphanet:391487','DOID:0111946','GARD:12314','MEDGEN:481620','UMLS:C3279990','OMIM:614162']` and exact_synonyms incl. `'immunodeficiency 31C, chronic mucocutaneous candidiasis, autosomal dominant'`, `'CANDF7'`, `'IMD31C'`; `MONDO_0007818` xrefs incl. `OMIM:146840`, `OMIM:147060`, `Orphanet:2314`; no disease node's name/description/synonyms/concept_names contains `JAK1`; `xrefs == []` on 28,061 of 36,753 disease nodes (incl. `EFO_0002429`, `EFO_0000479`, `EFO_0002430`, `MONDO_0015279`, `EFO_0005555`, `EFO_0010647`, `MONDO_0013427`); DIS-PHE `evidence_type` `["PCS"]` 98,848 · `["IEA"]` 33,132 · `["TAS"]` 21,954 · mixed 1,442/1,065/353; `frequency` `[]` 65,753; `onset` `[]` 154,018, `["HP:0003577"]` 945; `aspect` P 142,204 · C 7,438 · I 7,370 · M 95 · H 37; `qualifier_not` and `relation_conflict` all False; `sexes` `[]`/MALE/FEMALE; `relation_assertions` e.g. `[{'source': 'OPEN_TARGETS', 'relation': 'PHENOTYPE_PRESENT'}]`; ruxolitinib DRG-DIS → `EFO_0000479` INDICATION `highest_clinical_trial_phase 2.0` `reference_ids ['NCT01392443','NCT02577926','NCT03878199']`, → `EFO_0002429` INDICATION phase 3.0, → `EFO_0002430` INDICATION phase 4.0 (`sources.indirect ['DAILY_MED']`, `reference_ids` is a DailyMed set-id GUID, not an NCT).

### R.2 Corrections to the original text

1. **`PARENT` edge direction is stated backwards (§3.3 and §5 gotchas).** The notes say `DIS-DIS PARENT`, "`from`=child → `to`=parent". In the data it is **`from` = parent → `to` = child** (read it as "`from` is PARENT of `to`"). Evidence from `edges/disease_disease.parquet` (md5-verified): rows with `from == 'MONDO_0015279'` (CMC) have `to` ∈ {`MONDO_0013140` 'candidiasis, familial, 4', `MONDO_0013500` 'immunodeficiency 51', `MONDO_0013599` IMD31C} — these are CMC's *children*; rows with `to == 'MONDO_0015279'` have `from` ∈ {`MONDO_0003778` 'inborn error of immunity', `MONDO_0100118` 'hereditary skin disorder'} — its *parents*. Highest out-degree `from` nodes are the broadest terms (`PATO_0000070` 'amount' 6,511; `EFO_0004747` 'protein measurement' 5,680; `EFO_0000508` 'genetic disorder' 966) and the highest in-degree is only 10, which is only possible if `from` is the parent. Same for `edges/phenotype_phenotype.parquet`: `from == 'HP_0000001'` ('All') → `to` ∈ {`HP_0000005`, `HP_0000118` 'Phenotypic abnormality', `HP_0012823`, …}, nothing has `to == 'HP_0000001'`; `from == 'HP_0001250'` 'Seizure' → `HP_0002069`, `HP_0002133`, … and its single parent row is `from == 'HP_0012638'` → `to == 'HP_0001250'`. The *biological* parents/children the notes list for CMC, JAK3-SCID and HIES1 are correct; only the column semantics are inverted. The docs (`edges.mdx`, `graph-schema.md`, `llms-full.txt`) do not state the direction anywhere — only "`from`: Source node ID", "`to`: Target node ID" — so the roll-up code must use **`to` = child, `from` = parent**: to roll phenotypes *up* from children of X, select rows where `from == X` and take `to`. Also: `EFO_0005555` 'gamma chain deficiency' shares exactly the same two parents as `MONDO_0010938` (`MONDO_0031520` 'familial severe combined immunodeficiency', `MONDO_0044200` 'T-B+ severe combined immunodeficiency'); `EFO_0010647` 'STAT3 gain of function' has parents `EFO_0000508` 'genetic disorder' and `EFO_0005140` 'autoimmune disease' only.

2. **DIS-PHE `frequency` HP-term values exist — and in underscore form (§3.5 said UNVERIFIED).** Across all 157,144 edges the `frequency` list elements are: 92,565 `n/m` ratio strings, **4,274 HPO frequency terms written as `HP_0040283` (3,044) · `HP_0040282` (570) · `HP_0040284` (461) · `HP_0040281` (162) · `HP_0040280` (37)**, and 146 percentages (`'10%'`, `'5%'`, `'50%'`). So `frequency` uses the **underscore** CURIE form (`HP_…`, 4,268 edges) whereas `onset` (3,160 values) and `modifiers` (1,122 values) use the **colon** form (`HP:0003577`, `HP:0012825`) — three different HP spellings inside one file (`to` column `HP_…`, `frequency` `HP_…`, `onset`/`modifiers` `HP:…`). Add to the CURIE-normalisation gotcha in §5.

3. **BPO-GEN evidence-code counts in §3.5 are "edges whose `evidence` list is exactly `[X]`", not per-code totals.** `inspect_small.py` counts the JSON of the whole list, so "IEA 63,886 · IDA 17,001 · IMP 11,930 · IBA 11,367 · TAS 6,636" are `["IEA"]`, `["IDA"]`, … (1,405 distinct list values; `["IEA","ISS"]` 4,987 etc.). Counting every element: IEA 92,673 · IDA 27,948 · IBA 24,580 · IMP 19,509 · ISS 18,346 · TAS 11,071 · NAS 6,360 · IGI 1,460. Same caveat for the `eco_ids` numbers. Not wrong, but use the per-element totals if the numbers go into a README.

4. **PHE-PHE `sources.direct` is not only `['HPO']`** (§3.3): 24,862 edges have `HPO`, 1,259 have `OPEN_TARGETS`. (Side note: `skills/optimuskg/reference/graph-schema.md` lists `PHE-PHE 24,862` and `DIS-DIS 44,215`, i.e. it is a stale snapshot; `edges.mdx`, `llms-full.txt` and the Parquet all agree on 26,121 / 47,892.)

5. **Phenotype `type` is not always `'phenotype'`** (§3.4): `'phenotype'` on the 19,177 `HP_` rows, `'PT'` 154 and `'LLT'` 10 on the 164 `meddra…` rows (which also have `ontology = null`).

6. **"`EFO_0005555` … only IL2RG hit" (§3.3) is loose wording.** No disease node's text contains the string `IL2RG` at all (0 hits over name, description, all synonym lists, concept_names, snomed_full_names). `EFO_0005555` is reachable only by searching "gamma chain" (14 hits, most of them OBA measurement traits) or via its `related_synonyms` `['X-linked SCID', 'severe combined immunodeficiency, X-linked', 'SCIDX', 'immunodeficiency 4', 'SCID, X-linked', 'X-SCID', …]`; its description starts "Severe combined immunodeficiency (SCID) due to gamma chain deficiency, also called SCID-X1…". For the pipeline: map IL2RG → X-SCID via the gene's `symbol_synonyms` `IMD4`/`SCIDX`/`CIDX` ↔ the disease's `related_synonyms` `immunodeficiency 4`/`SCIDX`, not via a gene-symbol text search.

7. **Citation author list can be upgraded from UNVERIFIED to VERIFIED** (§4): repo `README.md` lines 147–153 contain the BibTeX `@article{vittor2026optimuskg, title={OptimusKG: Unifying biomedical knowledge in a modern multimodal graph}, author={Vittor, Lucas and Noori, Ayush and Arango, Iñaki and Polonuer, Joaquín and Rodriques, Sam and White, Andrew and Clifton, David A. and Zitnik, Marinka}, journal={In review}, year={2026}}`. (I did not fetch the rendered `/docs/citation` page body, only its 200 status.)

8. **Minor: ruxolitinib DRG-GEN `mechanisms_of_action` differs per edge** (§3.5 quotes the JAK2 value for both): JAK2 edge `['Tyrosine-protein kinase JAK2 inhibitor']`, JAK1 edge `['Tyrosine-protein kinase JAK1 inhibitor']`; both carry `source_ids ['label/2012/202192s001lbl.pdf']`, `source_urls ['http://www.accessdata.fda.gov/drugsatfda_docs/label/2012/202192s001lbl.pdf']`, `sources {'direct': ['DRUG_BANK','OPEN_TARGETS'], 'indirect': ['FDA']}`. The third edge → `ENSG00000160868` `ENZYME` has all three lists `null` and `sources.direct ['DRUG_BANK']` only.

9. **Minor: `MONDO_0010938` xrefs** also include `UMLS:C1833275`, `MESH:C563440`, `SCTID:718107000`, `GARD:16632`, `MEDGEN:331474` (the notes list only OMIM/Orphanet, which is fine, but the UMLS id is what DisGeNET keys on).

### R.3 Things that are still UNVERIFIED after this pass
* Whether the `/api/access/datafiles/<id,id,…>` zip bundle has a size cap for the full 158 MB set (only tested the 2-small-file bundle, as in the original).
* DrugBank's own licence terms and every other upstream source's terms (not fetched, same as the original).
* Polars `scan_parquet` + filter performance on the 73 MB DIS-GEN file (polars is not in `/opt/anaconda3`; it is in `.venv-okg2` but I did not download the file).
* The rendered content of `https://optimuskg.ai/docs/citation` (status 200 only).
