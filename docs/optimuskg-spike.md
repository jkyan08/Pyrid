# OptimusKG spike — decision record

Spike performed 2026-10-03 by read-only probing; see notes path for raw evidence:
`docs/recon/optimuskg.md`
(original notes plus the "Re-run check (second reviewer)" section at the end, whose corrections override the original text; raw responses, scripts and sample Parquet files sit in `recon/` and `recon/rerun_optimuskg/`). Every fact below comes from those notes. **VERIFIED** = seen in a live response that day (and re-run by the second reviewer ~2 h later); **UNVERIFIED** = not checked.

Dataset observed: Harvard Dataverse `doi:10.7910/DVN/IYNGEV`, version **3.2**, released 2026-09-09. Time box per README §5: 45 min.

---

## 1. Decision: GO — use OptimusKG as the backbone

Both README §5 exit criteria pass. The HPO/MONDO/HGNC/Reactome/GO raw-file fallback is not needed.

### Exit criterion 1 — STAT1, STAT3, JAK1, JAK2, JAK3, TYK2, IL2RG each resolve to a single gene node: PASS

Source: `nodes/gene.parquet` (61,306 rows). Exact match on `properties.symbol` returned exactly one node per symbol; no other node lists any of the seven as a `symbol_synonyms[].label` (395 symbols in the file are shared by >1 node, none of ours). VERIFIED by both reviewers.

| symbol | node `id` (Ensembl gene id, no prefix) | `properties.name` | HGNC xref | notable `symbol_synonyms` / `obsolete_symbols` |
|---|---|---|---|---|
| STAT1 | `ENSG00000115415` | signal transducer and activator of transcription 1 | HGNC:11362 | STAT91, ISGF-3, CANDF7, IMD31A/B/C |
| STAT3 | `ENSG00000168610` | signal transducer and activator of transcription 3 | HGNC:11364 | APRF, ADMIO, ADMIO1, HIES |
| JAK1 | `ENSG00000162434` | Janus kinase 1 | HGNC:6190 | JAK1A, JTK3, AIIDE; obsolete JAK1B |
| JAK2 | `ENSG00000096968` | Janus kinase 2 | HGNC:6192 | JTK10 |
| JAK3 | `ENSG00000105639` | Janus kinase 3 | HGNC:6193 | L-JAK, JAKL, LJAK |
| TYK2 | `ENSG00000105397` | tyrosine kinase 2 | HGNC:12440 | JTK1, IMD35 |
| IL2RG | `ENSG00000147168` | interleukin 2 receptor subunit gamma | HGNC:6010 | CD132, CIDX, IMD4; obsolete SCIDX1 |

How the identity data is stored: the HGNC symbol is `properties.symbol`; the HGNC id is **not** a column but an entry in `properties.xrefs` as `{id: '11362', source: 'HGNC'}` (no `hgnc_id` column); synonyms are `properties.symbol_synonyms[].label` (+ `.source` ∈ NCBI_entrez, uniprot, HGNC) and `properties.obsolete_symbols`; full name is `properties.name`. All seven: `biotype='protein_coding'`, `sources.direct=['OPEN_TARGETS','BGEE']`. Gene ids are bare `ENSG…` on 61,108 rows; 198 rows are `NCBIGene:<n>` (DisGeNET-only genes).

### Exit criterion 2 — cluster diseases have disease–phenotype edges: PASS with caveats

Source: `nodes/disease.parquet` (36,753 rows) searched over `name`, `*_synonyms`, `description`; counts from `edges/disease_phenotype.parquet` (`DIS-PHE PHENOTYPE_PRESENT`). All counts reproduced exactly by the second reviewer.

| disease node `id` | name | DIS-PHE edges | role in cluster |
|---|---|---:|---|
| `MONDO_0013599` | autoimmune enteropathy and endocrinopathy - susceptibility to chronic infections syndrome | 42 | **STAT1 GoF** (IMD31C; see §4) |
| `MONDO_0013427` | immunodeficiency 31B | 4 | STAT1 LoF, AR ('STAT1 deficiency') |
| `MONDO_0010938` | T-B+ severe combined immunodeficiency due to JAK3 deficiency | 20 | JAK3 SCID |
| `EFO_0005555` | gamma chain deficiency | 22 | X-SCID (IL2RG) |
| `MONDO_0007818` | hyper-IgE recurrent infection syndrome 1, autosomal dominant | 53 | HIES1 / STAT3 DN |
| `EFO_0002429` | polycythemia vera | 20 | JAK2 MPN |
| `EFO_0000479` | essential thrombocythemia | 10 | JAK2 MPN |
| `EFO_0002430` | primary myelofibrosis | 11 | JAK2 MPN |
| `MONDO_0012682` | immunodeficiency 35 (TYK2 deficiency) | 7 | TYK2 |
| `MONDO_0013140` | candidiasis, familial, 4 | 3 | child of CMC |
| `EFO_0002428` | chronic myeloproliferative disorder | 4 | parent of PV |

**Caveats (zero DIS-PHE edges):**
- `MONDO_0015279` chronic mucocutaneous candidiasis (the umbrella CMC node): **0** edges, `xrefs = []`. Its phenotype coverage lives on its children (`MONDO_0013140`, `MONDO_0013500` 'immunodeficiency 51', `MONDO_0013599`).
- `EFO_0010647` "STAT3 gain of function": exists as a node but **0** edges; its only parents are `EFO_0000508` 'genetic disorder' and `EFO_0005140` 'autoimmune disease'.
- Also 0: `MONDO_0018037` hyper-IgE syndrome (parent of `MONDO_0007818`), `Orphanet_319543` (AD partial STAT1 deficiency / MSMD), `Orphanet_748` Mendelian susceptibility to mycobacterial diseases, `EFO_0004251` myeloproliferative disorder.
- Mitigation: roll phenotypes up from children using `edges/disease_disease.parquet` (`DIS-DIS PARENT`, direction in §3).

`evidence_score` observed range on DIS-GEN (README §5 asks for it, needed by the confidence rubric): **min 0.0009095, max 0.9485794**, nulls 0, over all 9,734,774 rows (from Parquet footer statistics). Details in §3.

---

## 2. How to obtain the data

**Dataset.** Harvard Dataverse `doi:10.7910/DVN/IYNGEV` (`data.persistentUrl` https://doi.org/10.7910/DVN/IYNGEV; dataset `data.id` 13645730), version 3.2, `releaseTime 2026-09-09T04:14:10Z`, 41 Parquet files, 822,113,189 bytes total, all `restricted=False`, `contentType application/octet-stream`, no per-file persistentId. **No API token is needed** (`fileAccessRequest: True` is irrelevant because no file is restricted). VERIFIED.

**File listing (Dataverse native API).** `https://dataverse.harvard.edu/api/datasets/:persistentId/?persistentId=doi:10.7910/DVN/IYNGEV` → JSON with `latestVersion.versionNumber`/`versionMinorNumber` (3 / 2), `latestVersion.license`, and `latestVersion.files[]` carrying `directoryLabel` (`nodes`, `edges` or absent), `dataFile.id`, `dataFile.filename`, `dataFile.filesize`, `dataFile.md5` and `checksum {type: MD5, value}`. Saved copy: `recon/dataverse_dataset.json` (39 KB).

**Per-file download.** `https://dataverse.harvard.edu/api/access/datafile/<dataFile.id>` → HTTP 303 to a pre-signed S3 URL (`https://dvn-cloud-iqss.s3.amazonaws.com/10.7910/DVN/IYNGEV/<key>?X-Amz-...&X-Amz-Expires=3600`, valid 3600 s) → 200. `Accept-Ranges: bytes`; HTTP Range requests work (`Range: bytes=0-7` → 206). **A HEAD on the presigned URL returns 403** (S3 signatures are method-specific): use GET + Range to probe, or read `filesize` from the dataset JSON. Multi-file zip bundle `https://dataverse.harvard.edu/api/access/datafiles/<id>,<id>` works with no token for a 2-file test (directory labels preserved); whether it has a size cap for our 158 MB set is UNVERIFIED.

**User-Agent gotcha (VERIFIED, reproduced 3× by the first reviewer and 2× by the second).** Harvard Dataverse's WAF returns **HTTP 403** (Apache "Forbidden" HTML) to the default `python-requests/*` and `Python-urllib/*` User-Agents on `/api/datasets/*` only — the metadata endpoint the `optimuskg` client calls first. `/api/access/datafile/<id>` is **not** blocked. So out of the box `optimuskg.get_file(...)` fails with `requests.exceptions.HTTPError: 403 Client Error: Forbidden for url: https://dataverse.harvard.edu/api/datasets/:persistentId/?persistentId=doi%3A10.7910%2FDVN%2FIYNGEV`. `curl` (default UA), `-A ""` and `-A "optimuskg/1.0.0"` all get 200. Fix if the client is used:

```python
import requests.utils
requests.utils.default_user_agent = lambda name="python-requests": "pyrid-atlas/0.1 (+optimuskg client)"
import optimuskg  # must come after the patch
```

After the patch `get_file`, `fetch_metadata()` (version `3.2`, 41 files) and `load_parquet("nodes/pathway.parquet", columns=["id"])` all succeed; cache layout `<cache_dir>/doi_10_7910_DVN_IYNGEV/3.2/nodes/pathway.parquet`. **Any hand-rolled `requests` code in `pipeline/cache.py` must also set a non-default `User-Agent` or it will 403 on the metadata endpoint.**

**Files we need (stratified Parquet only; never the unified `nodes.parquet` 116 MB / `edges.parquet` 180 MB).** Ids and sizes from the dataset JSON, re-checked one by one by the second reviewer. The notes transcribe md5s for two files only; every file's md5 is in `dataFile.md5` of the dataset JSON.

| need | file | `dataFile.id` | bytes | md5 (where in notes) |
|---|---|---:|---:|---|
| gene nodes | `nodes/gene.parquet` | 14220882 | 43,261,973 | `edf81a21db68658381c063e21d1080d2` |
| disease nodes | `nodes/disease.parquet` | 14220892 | 2,968,496 | — |
| phenotype nodes | `nodes/phenotype.parquet` | 14220905 | 975,819 | — |
| pathway nodes | `nodes/pathway.parquet` | 14220888 | 50,610 | `7f2b6f03145447405a236eb566a402fa` |
| drug nodes | `nodes/drug.parquet` | 14220910 | 32,960,859 | — |
| disease–gene | `edges/disease_gene.parquet` | 14220918 | 72,754,866 | — |
| disease–phenotype | `edges/disease_phenotype.parquet` | 14220915 | 1,356,623 | — |
| phenotype–phenotype | `edges/phenotype_phenotype.parquet` | 14220914 | 154,206 | — |
| pathway–gene | `edges/pathway_gene.parquet` | 14220896 | 134,262 | — |
| GO-process–gene | `edges/biological_process_gene.parquet` | 14220919 | 1,952,119 | — |
| drug–gene | `edges/drug_gene.parquet` | 14220895 | 499,643 | — |
| drug–disease | `edges/drug_disease.parquet` | 14220904 | 1,329,176 | — |
| **core total (12 files)** | | | **158,398,652 (158.4 MB)** | |
| optional: GO term names | `nodes/biological_process.parquet` | 14220903 | 1,605,528 | — |
| optional: disease hierarchy (`DIS-DIS PARENT`) | `edges/disease_disease.parquet` | 14220916 | 179,050 | — |
| optional: Reactome hierarchy | `edges/pathway_pathway.parquet` | 14220906 | 19,052 | — |
| **core + optional (15 files)** | | | **160,202,282 (160.2 MB)** | |

Without `nodes/drug.parquet` (33 MB, dominated by two base64 MOL/image columns) the core set is 125,437,793 bytes; the ruxolitinib node can instead be read with a column projection (0.99 MB fetched instead of 33 MB). The second reviewer md5-checked all 12 Dataverse-origin files in `recon/parquet/` against `dataFile.md5`: all match.

**Recommended approach: download the stratified files directly over HTTP (curl, or `requests` with a custom UA) and read them with Polars or pyarrow; do not depend on the `optimuskg` client.** Reasons (all observed): (1) the client is ~250 lines doing metadata GET → id lookup → `GET /api/access/datafile/<id>` → `pl.read_parquet`, and we already have the ids, so the metadata call can be skipped entirely; (2) it 403s out of the box (above); (3) it pins `polars>=1.19` **and** `networkx>=3.0` into `pyproject.toml`/`uv.lock`; (4) it always downloads whole files, whereas Range + column projection reads all gene ids/symbols/xrefs/synonyms in 3.3 MB of the 43 MB file and row-group pruning by disease id works on DIS-GEN (footer stats; file sorted by `from`); (5) direct download gives the md5 for integrity checks and the version/`releaseTime` for the README license table.

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
Polars `scan_parquet` + filter performance on the 73 MB DIS-GEN file is UNVERIFIED (polars was not in the anaconda env; pyarrow `ParquetFile.iter_batches` is the fallback). Range-read scripts (`scripts/range_footer.py`, `range_cols.py`, `range_stats.py`, `range_disgen_for_diseases.py` in the recon dir) implement a `pa.PythonFile` over HTTP Range on the presigned URL with pyarrow 19.

**Client facts, for the record.** PyPI `optimuskg` 1.0.0 (both releases 0.1.0 and 1.0.0 uploaded 2026-04-17; `requires_python >=3.12`; deps `networkx>=3.0, platformdirs>=4.0.0, polars>=1.19.0, pyarrow>=19.0.0, requests>=2.32.3`; wheel ~10 KB). Public API: `get_file`, `load_parquet` (extra kwargs go to `pl.read_parquet`), `load_graph`, `load_networkx`, `set/get_cache_dir` (env `OPTIMUSKG_CACHE_DIR`), `set/get_doi`, `set/get_server`. **No filter/query API** — filtering is plain Polars. `optimuskg.__version__` reports `"0.1.0"` while `importlib.metadata.version` says `"1.0.0"`. Never call `load_graph`/`load_networkx` (190k nodes, 21M edges, several GB). If the client is used anyway: patch the UA before `import optimuskg`, set `OPTIMUSKG_CACHE_DIR` to the shared cache dir, never call `load_graph`.

---

## 3. Schema facts the pipeline relies on

**Base layout.** In the stratified files `properties` is a native typed Polars `Struct` (the unified files hold it as a JSON string). All files `created_by = Polars`, ZSTD, with `ARROW:schema` metadata and per-column min/max statistics, so footer-only reads give global ranges. Edge columns: `from, to, label, relation, undirected: bool, properties`. Docs guarantee **at most one edge per (`from`, `to`, `label`) triple**: the winning `relation` is chosen by `RELATION_PRIORITY`, losers are kept in `properties.relation_assertions`.

**Node id formats per type (normalise CURIEs once at load):**
- `GEN` (61,306): bare Ensembl `ENSG00000115415`; 198 rows `NCBIGene:<n>`.
- `DIS` (36,753): OBO-style **underscore** CURIE, not `MONDO:` — prefixes `EFO_` 11,960, `MONDO_` 11,724, `OBA_` 10,997 (measurement traits), `Orphanet_` 2,045, `OTAR_` 9, `DOID_` 6, plus `OBI_`, `NCIT_`, `MP_`, `PATO_`. `properties.code` is the IRI (e.g. `http://purl.obolibrary.org/obo/MONDO_0015279`).
- `PHE` (19,341): `HP_0001250` style (19,177 `HP_`, 164 `meddra…`).
- `PWY` (2,805): `REACT:R-HSA-1059683` — **colon** CURIE.
- `BPO` (25,754): `GO_0000002`.
- `DRG` (18,640): bare `CHEMBL1789941`; 2,758 `DRUGBANK…`, 62 `RXNORM…`.
- **Three HP spellings inside `edges/disease_phenotype.parquet`** (second reviewer): `to` column `HP_…`; `frequency` values `HP_0040283` (underscore); `onset`/`modifiers` values `HP:0003577` / `HP:0012825` (colon).

**Where symbol / synonyms / xrefs / description live:**
- Gene: `properties.symbol`, `.name`, `.symbol_synonyms[] {label, source}`, `.obsolete_symbols`, `.name_synonyms`, `.xrefs[] {id, source}` (sources: PDB 120,205 · InterPro 89,213 · Reactome 46,618 · HGNC 41,270 · DrugBank 27,955 · ProjectScore 7,186 · ChEMBL 4,867 · signalP 3,686), `.function_descriptions`, `.sources {direct, indirect}`.
- Disease: `properties.name`, `.description`, `.code`, `.xrefs: list<str>` (strings like `'OMIM:600802'`, `'Orphanet:35078'`, `'UMLS:C1833275'`, `'MEDGEN:..'`, `'SCTID:..'`, `'GARD:..'`, `'MESH:..'`, `'DOID:..'`, `'MONDO:..'`), `.exact_synonyms`, `.related_synonyms`, `.narrow_synonyms`, `.broad_synonyms`, `.obsolete_terms`, `.therapeutic_areas`, `.umls_cui`, `.concept_names`, `.snomed_full_names`; `sources.direct=['OPEN_TARGETS','DRUG_CENTRAL','MONDO']` on every row. **`xrefs == []` on 28,061 of 36,753 disease nodes**, including PV/ET/PMF, CMC, `EFO_0005555`, `EFO_0010647`, `MONDO_0013427`. **Docs/data mismatch:** the docs' `diseaseFields` list `parents`, `children`, `ancestors`, `descendants`, `is_leaf` — these columns do **not** exist in v3.2 (nor in `phenotype.parquet`); the hierarchy is only in `edges/disease_disease.parquet` and `edges/phenotype_phenotype.parquet`.
- Phenotype: `name`, `description`, `code`, `xrefs`, the four `*_synonyms`, `type` (`'phenotype'` on `HP_` rows; `'PT'` 154 / `'LLT'` 10 on `meddra…` rows, which have `ontology = null`), `ontology {title 'Human Phenotype Ontology', version '2025-05-06', license 'https://hpo.jax.org/app/license'}`.
- Drug: `name`, `synonyms: list<str>`, `trade_names`, `type`, `is_approved`, `source_ids`, `sources`, `description`, plus `mol_file_base64` / `mol_image_base64` (the size).

**Edge columns per file:**
- **`DIS-GEN ASSOCIATED_WITH`** (`edges/disease_gene.parquet`, 9,734,774 rows, `undirected=True`, `from`=disease, `to`=Ensembl, sorted by `from`, 80 row groups × 122,880): `evidence_score: double` **0.0009095–0.9485794, nulls 0**; `evidence_count: int64` 1–197,300, nulls 0; `disgenet_score` 0.3–1.0, **null on 9,463,636 rows (97.2 %)**; `number_of_pmids: int16` 0–136 (same null count); `number_of_snps` 0–2258; `year_initial` '1940'–'2020' and `year_final` '1966'–'2020' (**strings**, null on 9,465,027 rows); `disease_specificity_index` 0.231–1.0; `disease_pleiotropy_index` 0.038–0.962; `evidence_index` -0.0–1.0; `sources.direct` ∈ {`DISGENET`, `OPEN_TARGETS`}, `sources.indirect` seen: CTD, ORPHANET, GENOMICS_ENGLAND, UNIPROT, CGI. The DisGeNET v7.0 fields are null unless DisGeNET contributed. **No `references`, `relation_assertions`, `relation_conflict`, `evidence_type` on DIS-GEN.**
- **`DIS-PHE PHENOTYPE_PRESENT`** (157,144; `from` ∈ `MONDO_` 119,497 / `Orphanet_` 29,653 / `EFO_` 7,994; `to` = `HP_…`; `sources.direct=['OPEN_TARGETS']`, `indirect=['HPO']`): `aspect` (P 142,204 · C 7,438 · I 7,370 · M 95 · H 37), `bio_curation` (e.g. `"HPO:iea[2009-02-17];HPO:skoehler[2012-10-17]"`), `evidence_type` (`["PCS"]` 98,848 · `["IEA"]` 33,132 · `["TAS"]` 21,954 · mixed lists 1,442/1,065/353), `frequency` (92,565 `n/m` ratio strings; 4,274 HPO frequency terms as `HP_0040283` 3,044 · `HP_0040282` 570 · `HP_0040284` 461 · `HP_0040281` 162 · `HP_0040280` 37; 146 percentages like `'10%'`; empty on 65,753 edges), `modifiers`, `onset` (`["HP:0003577"]` 945; empty 154,018), `qualifier_not` (all False), `references` (e.g. `["OMIM:151400"]`, `["PMID:21700266"]`, ISBN), `sexes` (MALE/FEMALE/empty), `sources`, `relation_assertions` (e.g. `[{'source': 'OPEN_TARGETS', 'relation': 'PHENOTYPE_PRESENT'}]`), `relation_conflict` (all False). Phenotype information content can be computed from this file (157k annotations over ~all HPO-annotated diseases).
- **`BPO-GEN INTERACTS_WITH`** (158,410; `from`=`GO_…`, `to`=Ensembl): `evidence: list<str>` = GO evidence codes, `eco_ids` (e.g. `ECO:0000265`, `ECO:0000314`), `gene_product` (UniProt acc), `sources` (`indirect` carries the reference, e.g. `PMID:9924029`, `GO_REF:0000024`). Per-element code totals (second reviewer): IEA 92,673 · IDA 27,948 · IBA 24,580 · IMP 19,509 · ISS 18,346 · TAS 11,071 · NAS 6,360 · IGI 1,460 (the original's "IEA 63,886 · IDA 17,001 …" counted edges whose list is exactly `[X]`).
- **`PWY-GEN INTERACTS_WITH`** (46,977; `from`=`REACT:…`, `to`=Ensembl; `sources.direct=['OPEN_TARGETS']`, `indirect=['REACTOME']`): `properties = {sources}` only.
- **`DRG-GEN`** (36,611; directed; `from`=drug, `to`=Ensembl; 33 relations: TARGET 13,459 · INHIBITOR 5,956 · ENZYME 5,301 · TRANSPORTER · ANTAGONIST · AGONIST · BLOCKER …): `source_ids`, `source_urls`, `mechanisms_of_action`, `sources`, `relation_assertions`, `relation_conflict` (True on 2 rows).
- **`DRG-DIS`** (70,380; `to` ∈ `EFO_` 49,666 / `MONDO_` 19,847 / `Orphanet_` / `MP_` / `DOID_`; INDICATION 58,174 · CONTRAINDICATION 11,379 · OFF_LABEL_USE 827): `sources`, `structure_id`, `drug_disease_id`, `reference_ids` (NCT ids with `sources.indirect=['CLINICAL_TRIALS']`, ATC codes, or a DailyMed set-id GUID), `highest_clinical_trial_phase: double`, `relation_assertions`, `relation_conflict` (True on 573 rows).
- **`DIS-DIS PARENT`** (47,892; directed; `sources.direct=['OPEN_TARGETS']`) and **`PHE-PHE PARENT`** (26,121; directed; `sources.direct` HPO on 24,862 edges, OPEN_TARGETS on 1,259): `properties = {sources}` only. **Direction — corrected by the second reviewer: `from` = parent, `to` = child** (read "`from` is PARENT of `to`"). Evidence: `from == 'MONDO_0015279'` → `to` ∈ {`MONDO_0013140`, `MONDO_0013500`, `MONDO_0013599`} (CMC's children); `to == 'MONDO_0015279'` ← `from` ∈ {`MONDO_0003778`, `MONDO_0100118`} (its parents); `from == 'HP_0000001'` ('All') fans out and nothing has `to == 'HP_0000001'`; the broadest terms have the highest out-degree. The docs never state the direction. **To roll phenotypes up from the children of X: select rows where `from == X` and take `to`.**

| property | DIS-GEN | DIS-PHE | BPO-GEN | DRG-GEN | DRG-DIS | PWY-GEN / PHE-PHE / DIS-DIS |
|---|---|---|---|---|---|---|
| `evidence_score`, `evidence_count` | yes | – | – | – | – | – |
| `number_of_pmids`, `year_initial`, `year_final` (+`number_of_snps`, `disgenet_score`, DSI, DPI, `evidence_index`) | DisGeNET rows only | – | – | – | – | – |
| `frequency`, `onset`, `evidence_type`, `references` (+`aspect`, `modifiers`, `sexes`, `qualifier_not`, `bio_curation`) | – | yes | – | – | – | – |
| GO evidence codes | – | – | `evidence` + `eco_ids` | – | – | – |
| `sources` (`direct` / `indirect`) | yes | yes | yes | yes | yes | yes |
| `relation_assertions`, `relation_conflict` | **no** | yes | **no** | yes | yes | no |
| `reference_ids`, `highest_clinical_trial_phase` | – | – | – | – | yes | – |
| `mechanisms_of_action`, `source_ids`, `source_urls` | – | – | – | yes | – | – |

Counts caveat: `skills/optimuskg/reference/graph-schema.md` is a stale snapshot (PHE-PHE 24,862 / DIS-DIS 44,215); `edges.mdx`, `llms-full.txt` and the Parquet agree on 26,121 / 47,892. The Dataverse description, docs and SKILL.md also disagree on node counts (190,531 / 190,939 / 192,813); the Parquet footers are authoritative (`nodes.parquet` 192,813 rows, `edges.parquet` 21,834,669 rows).

---

## 4. Cluster-specific observations relevant to the ground-truth tests

- **STAT1 GoF = `MONDO_0013599`** (42 DIS-PHE). `exact_synonyms` include 'immunodeficiency type 31C', 'immunodeficiency 31C, chronic mucocutaneous candidiasis, autosomal dominant', 'CANDF7', 'IMD31C'; `xrefs` `['Orphanet:391487','DOID:0111946','GARD:12314','MEDGEN:481620','UMLS:C3279990','OMIM:614162']`. It is a child of the CMC umbrella `MONDO_0015279`. (Test 1.)
- **STAT1 LoF = `MONDO_0013427`** immunodeficiency 31B (4 DIS-PHE; `exact_synonym` 'STAT1 deficiency'; AR; `xrefs = []`). AD LoF: `Orphanet_319543` "Autosomal dominant mendelian susceptibility to mycobacterial diseases due to a partial deficiency" (0 DIS-PHE; description mentions STAT1) and `Orphanet_748` Mendelian susceptibility to mycobacterial diseases (0). (Test 1.)
- **CMC umbrella `MONDO_0015279`**: 0 DIS-PHE, `xrefs = []`; parents `MONDO_0003778` 'inborn error of immunity', `MONDO_0100118` 'hereditary skin disorder'; children `MONDO_0013140` 'candidiasis, familial, 4' (3 DIS-PHE; OMIM:613108), `MONDO_0013500` 'immunodeficiency 51', `MONDO_0013599`.
- **JAK3 SCID = `MONDO_0010938`** (20 DIS-PHE; `xrefs` incl. OMIM:600802, Orphanet:35078, UMLS:C1833275 — the id DisGeNET keys on — MESH:C563440, SCTID:718107000, GARD:16632, MEDGEN:331474). Parents: `MONDO_0031520` 'familial severe combined immunodeficiency', `MONDO_0044200` 'T-B+ severe combined immunodeficiency'. (Test 2.)
- **X-SCID = `EFO_0005555`** 'gamma chain deficiency' (22 DIS-PHE; `xrefs = []`; description starts "Severe combined immunodeficiency (SCID) due to gamma chain deficiency, also called SCID-X1…"; `related_synonyms` `['X-linked SCID', 'severe combined immunodeficiency, X-linked', 'SCIDX', 'immunodeficiency 4', 'SCID, X-linked', 'X-SCID', …]`). **No disease node's text contains the string `IL2RG`** (0 hits over name, description, all synonym lists, concept_names, snomed_full_names), so IL2RG → X-SCID must be mapped via the gene's `symbol_synonyms` `IMD4` / `SCIDX` / `CIDX` ↔ the disease's `related_synonyms` 'immunodeficiency 4' / 'SCIDX', **not** via a gene-symbol text search. `EFO_0005555` shares exactly the same two parents as `MONDO_0010938`. (Test 2.)
- **HIES = `MONDO_0007818`** hyper-IgE recurrent infection syndrome 1, AD (53 DIS-PHE; STAT3 DN; `xrefs` incl. OMIM:146840, OMIM:147060, Orphanet:2314); parent `MONDO_0018037` hyper-IgE syndrome (0).
- **STAT3 GoF = `EFO_0010647`** (0 DIS-PHE; parents `EFO_0000508`, `EFO_0005140` only) — a node exists but phenotypes must come from elsewhere.
- **PV / ET / PMF = `EFO_0002429` (20) / `EFO_0000479` (10) / `EFO_0002430` (11)**; all three have `xrefs = []`, so OMIM/Orphanet cross-walks for them must come from another source. PV's parents are `EFO_0004251` myeloproliferative disorder (0 DIS-PHE) and `EFO_0002428` chronic myeloproliferative disorder (4); their `xrefs` were not recorded in the notes (UNVERIFIED). (Test 3.)
- **TYK2 = `MONDO_0012682`** immunodeficiency 35 (7 DIS-PHE; OMIM:611521).
- **JAK1: no disease node mentions JAK1** (0 hits). Only DIS-GEN associations exist (e.g. PV → JAK1 `evidence_score` 0.5391). Record as a coverage gap in `meta.coverage`.
- **Ruxolitinib drug node `CHEMBL1789941`**: `name 'RUXOLITINIB'`, `type 'Small molecule'`, `is_approved True`, `trade_names ['Jakavi']`, `synonyms ['Ruxolitinib','INC-424','INC424','INCB-018424','INCB-18424','INCB018424']`, `source_ids ['66919','DB08877']` (DrugCentral, DrugBank), `sources.direct ['DRUG_BANK','DRUG_CENTRAL','OPEN_TARGETS']`.
  - **DRG-GEN (3 edges):** → JAK2 `INHIBITOR` (`mechanisms_of_action ['Tyrosine-protein kinase JAK2 inhibitor']`), → JAK1 `INHIBITOR` (`['Tyrosine-protein kinase JAK1 inhibitor']`; per-edge values differ, second reviewer), both with `source_urls ['http://www.accessdata.fda.gov/drugsatfda_docs/label/2012/202192s001lbl.pdf']`, `sources {direct: ['DRUG_BANK','OPEN_TARGETS'], indirect: ['FDA']}`; → `ENSG00000160868` `ENZYME` (DrugBank only, lists null). **`drug_gene` has no STAT1 edges at all** (cluster-gene DRG-GEN edges: JAK2 39, JAK1 29, JAK3 25, TYK2 18, IL2RG 10, STAT3 3, STAT1 0). Ruxolitinib's targets in the backbone are JAK1/JAK2 only. (Tests 4, 5 and the `rules.py` direction-mismatch rule.)
  - **DRG-DIS (66 edges):** → `EFO_0000479` ET `INDICATION`, `highest_clinical_trial_phase 2.0`, `reference_ids ['NCT01392443','NCT02577926','NCT03878199']`; → `EFO_0002429` PV `INDICATION` phase 3.0; → `EFO_0002430` PMF `INDICATION` phase 4.0 (`sources.indirect ['DAILY_MED']`, `reference_ids` a DailyMed set-id GUID). **Nothing links ruxolitinib to STAT1 GoF / CMC** — expected; that edge is the literature layer's job (test 4).
- **DIS-GEN density from Open Targets — threshold required.** Open Targets' aggregated/indirect associations make DIS-GEN very dense: CMC `MONDO_0015279` has 468 gene edges (STAT1 0.7443 / `evidence_count` 737 / `disgenet_score` 0.5 / 1 pmid / 2011–2020, sources OPEN_TARGETS+DISGENET, indirect CTD; but also TYK2 0.003, STAT3 0.0209, JAK3 0.0374, IL2RG 0.0314); JAK3-SCID 701 edges (JAK3 0.8389 / 1172 / 0.6 / 6 pmids / 1995–2004, indirect CTD, GENOMICS_ENGLAND, ORPHANET, UNIPROT; IL2RG 0.0774 OT-only); PV 1,157 edges (JAK2 0.8015 / disgenet 1.0 / 12 pmids; JAK1 0.5391); HIES1 only 3 edges (STAT3 0.7044, OT-only). **Threshold on `evidence_score` and/or require `DISGENET` in `sources.direct` before treating an edge as a "known gene".** DisGeNET is v7.0 (January 2020), so `year_final` ≤ 2020.
- **Pathways / GO:** 174 PWY-GEN edges touch the 7 genes (e.g. `REACT:R-HSA-1059683` 'Interleukin-6 signaling' ↔ JAK1/JAK2/TYK2/STAT1/STAT3; `REACT:R-HSA-1169408` 'ISG15 antiviral mechanism' ↔ STAT1/JAK1). 378 BPO-GEN edges touch them (JAK2 111, STAT3 104, STAT1 51, JAK3 34, JAK1 28, TYK2 27, IL2RG 23).

---

## 5. Licensing

- **Data:** Dataverse `latestVersion.license = {"name": "CC BY-NC-SA 4.0", "uri": "http://creativecommons.org/licenses/by-nc-sa/4.0"}`; `termsOfUse: None`. VERIFIED. Our derived `graph.json` is therefore non-commercial + share-alike + attributed. For the README license table: OptimusKG data, version 3.2 (released 2026-09-09), retrieved 2026-10-03, CC BY-NC-SA 4.0.
- **Code:** MIT — repo `LICENSE`, `packages/optimuskg/LICENSE` and PyPI `license` all read "MIT License / Copyright (c) 2025 Zitnik Lab, Department of Biomedical Informatics, Harvard Medical School"; GitHub `license.spdx_id: MIT`. VERIFIED.
- **Official license page** (https://optimuskg.ai/docs/license, identical to `docs/content/docs/license.mdx`): the codebase is MIT; OptimusKG integrates primary resources each under its own license and terms, which "may impose restrictions on redistribution, commercial use, or downstream applications"; users are responsible for reviewing and complying with each primary dataset's terms; OptimusKG does not alter or override them.
- **DisGeNET** (`conf/base/catalog/landing/disgenet/disgenet.yml`): "DisGeNET v7.0, January 2020 … License: Creative Commons Attribution-NonCommercial-ShareAlike 4.0 (CC BY-NC-SA 4.0)". The DIS-GEN DisGeNET fields (`disgenet_score`, `number_of_pmids`, years, DSI/DPI) are NC-SA. VERIFIED.
- **DrugBank**: loaded by the pipeline from `data/landing/drugbank/full database.xml` (v5-1-13 per `vocabulary.yml`); appears in the released Parquet as `sources.direct` value `DRUG_BANK` (DRG-GEN, DRG-DIS, drug nodes) and `xrefs.source='DrugBank'` on genes. DrugBank's own license terms were not fetched: **UNVERIFIED**.
- **HPO**: embedded in every phenotype node, `properties.ontology.license = 'https://hpo.jax.org/app/license'`, version 2025-05-06. GO nodes embed an `ontology` struct too.
- Other `sources.direct`/`indirect` values seen: OPEN_TARGETS, BGEE, MONDO, DRUG_CENTRAL, ONSIDES, HPO, MEDDRA, GO, REACTOME, CTD, ORPHANET, GENOMICS_ENGLAND, UNIPROT, CGI, FDA, DAILY_MED, PUBMED, CLINICAL_TRIALS, ATC. Per-source terms UNVERIFIED (not fetched).
- **Citation** (BibTeX from the repo `README.md` lines 147–153, VERIFIED by the second reviewer):

```bibtex
@article{vittor2026optimuskg, title={OptimusKG: Unifying biomedical knowledge in a modern multimodal graph}, author={Vittor, Lucas and Noori, Ayush and Arango, Iñaki and Polonuer, Joaquín and Rodriques, Sam and White, Andrew and Clifton, David A. and Zitnik, Marinka}, journal={In review}, year={2026}}
```
  Dataset reference for the attribution line: Harvard Dataverse, doi:10.7910/DVN/IYNGEV, v3.2. The rendered https://optimuskg.ai/docs/citation page body was not fetched (status 200 only), so any citation format beyond this BibTeX is UNVERIFIED.

---

## 6. Open items / UNVERIFIED (carried over from the notes)

**UNVERIFIED after both passes:**
1. Whether the `/api/access/datafiles/<id,id,…>` zip bundle has a size cap for the full 158 MB set (only a 2-small-file bundle was tested).
2. DrugBank's own license terms, and every other upstream source's terms (not fetched).
3. Polars `scan_parquet` + filter performance on the 73 MB DIS-GEN file (not run; pyarrow `iter_batches` is the fallback).
4. The rendered content of https://optimuskg.ai/docs/citation (status 200 only); citation format beyond the BibTeX above.

**Open items for the pipeline (facts verified, work pending):**
5. Roll-up code for zero-DIS-PHE nodes (`MONDO_0015279` CMC, `EFO_0010647` STAT3 GoF, `MONDO_0018037`) via `DIS-DIS PARENT` with **`from` = parent, `to` = child**.
6. CURIE normalisation at load: underscore disease/phenotype/GO ids, bare Ensembl and ChEMBL ids, colon `REACT:` ids, and the three HP spellings inside `disease_phenotype.parquet` (`to` and `frequency` underscore; `onset`/`modifiers` colon).
7. Decide the DIS-GEN `evidence_score` threshold and/or the `DISGENET`-in-`sources.direct` requirement (range 0.0009095–0.9485794; CMC has 468 gene edges, PV 1,157).
8. IL2RG → `EFO_0005555` mapping through gene `symbol_synonyms` ↔ disease `related_synonyms`, not text search.
9. No JAK1 disease node: log as a `meta.coverage` gap.
10. `xrefs == []` on 28,061 disease nodes incl. PV/ET/PMF/CMC/X-SCID/STAT1 LoF — OMIM/Orphanet cross-walks for those need another source.
11. `pipeline/cache.py` HTTP client must send a non-default `User-Agent` (Dataverse 403s `python-requests/*` on `/api/datasets/*`); hard-code the 12–15 file ids (+ md5 from the dataset JSON) so the metadata call can be skipped.
12. Do not use `skills/optimuskg/reference/graph-schema.md` for counts (stale: PHE-PHE 24,862 / DIS-DIS 44,215 vs 26,121 / 47,892 in data); do not rely on docs' `parents`/`children` disease columns (absent in v3.2).
13. If the `optimuskg` client is ever used: `__version__` string mismatch (0.1.0 vs 1.0.0), `requires_python >=3.12`, pins `polars>=1.19` + `networkx>=3.0`; patch UA before import; never `load_graph`/`load_networkx`.
