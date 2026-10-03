# Recon: HPO / MONDO / HGNC / Reactome / GO raw-file fallback backbone

Date of recon: 2026-10-03. Method: live HEAD / Range / small GET requests with `curl` and `/opt/anaconda3/bin/python3` (requests). No file over ~20 MB was downloaded; the one JS-only page (HPO license) was rendered in the desktop app's built-in browser. Every URL, field name and value below was observed in a response or in the fetched official docs unless marked **UNVERIFIED**. Raw samples are saved under `samples/` next to this file (list at the end).

Scope: the raw-file fallback for the knowledge-graph backbone, plus the inputs needed to compute phenotype information content over **all** HPO-annotated diseases (which we need regardless of the OptimusKG decision).

---

## 0. One-table summary

| # | File | Download URL (verified) | Size (bytes) | Version observed | License (quoted in §8) | What we use it for |
|---|---|---|---|---|---|---|
| 1 | `phenotype.hpoa` | `https://purl.obolibrary.org/obo/hp/hpoa/phenotype.hpoa` → redirects to `https://github.com/obophenotype/human-phenotype-ontology/releases/download/v2026-09-01/phenotype.hpoa` | 35,816,037 (34.2 MiB) | `#version: 2026-09-02`, release tag `v2026-09-01` | HPO custom (free use; cite + show version + do not alter). IEA rows carry an extra OMIM-reuse caveat | disease–phenotype edges for every HPO-annotated disease (12,882 diseases: 8478 OMIM, 47 DECIPHER, 4357 ORPHANET per file header); phenotype IC; evidence code PCS/TAS/IEA → confidence |
| 2a | `genes_to_phenotype.txt` | `https://github.com/obophenotype/human-phenotype-ontology/releases/download/v2026-09-01/genes_to_phenotype.txt` | 20,821,704 (19.9 MiB) | same release | HPO custom | gene→phenotype (most specific terms only), carries `disease_id`; optional, derivable from 1 + 2c |
| 2b | `phenotype_to_genes.txt` | `https://github.com/obophenotype/human-phenotype-ontology/releases/download/v2026-09-01/phenotype_to_genes.txt` | 67,246,520 (64.1 MiB) | same release | HPO custom | phenotype→gene with ancestors propagated; big, probably skip |
| 2c | `genes_to_disease.txt` | `https://github.com/obophenotype/human-phenotype-ontology/releases/download/v2026-09-01/genes_to_disease.txt` | 1,492,039 (1.4 MiB) | same release | HPO custom | gene–disease edges (MENDELIAN / POLYGENIC / UNKNOWN) from mim2gene_medgen + Orphanet; small, useful |
| 3a | `hp.json` (OBO Graphs) | `https://purl.obolibrary.org/obo/hp.json` → `.../releases/download/v2026-09-01/hp.json`; pinned: `http://purl.obolibrary.org/obo/hp/releases/2026-09-01/hp.json` | 21,915,519 (20.9 MiB) | `2026-09-01` | HPO custom | HPO term labels, synonyms, is_a edges → ancestor closure for IC and semantic similarity |
| 3b | `hp.obo` | `https://purl.obolibrary.org/obo/hp.obo` → `.../v2026-09-01/hp.obo` | 10,863,613 (10.4 MiB) | `data-version: hp/releases/2026-09-01` | HPO custom | smaller alternative to 3a (same content, OBO flat format) |
| 4a | `mondo.json` | `https://purl.obolibrary.org/obo/mondo.json` → `https://github.com/monarch-initiative/mondo/releases/download/v2026-09-01/mondo.json` | 107,586,061 (102.6 MiB) | `v2026-09-01` | CC BY 4.0 | disease node labels/synonyms/xrefs; heavy |
| 4b | `mondo.obo` | `https://purl.obolibrary.org/obo/mondo.obo` → `.../v2026-09-01/mondo.obo` | 53,134,854 (50.7 MiB) | same | CC BY 4.0 | same content, half the size |
| 4c | **`mondo.sssom.tsv`** (recommended for ID mapping) | `https://purl.obolibrary.org/obo/mondo/mappings/mondo.sssom.tsv` → `https://raw.githubusercontent.com/monarch-initiative/mondo/master/src/ontology/mappings/mondo.sssom.tsv` | 13,113,756 (12.5 MiB) | master branch (not release-pinned) | CC BY 4.0 (part of mondo repo) | OMIM:/Orphanet: → MONDO mapping, 6 columns, 109,715 rows, all `skos:exactMatch` except 89 `skos:broadMatch` |
| 4d | `mondo-rare.json` / `mondo-rare.obo` | `.../v2026-09-01/mondo-rare.json`, `.../mondo-rare.obo` | 54,365,899 / 33,495,699 | same | CC BY 4.0 | rare-disease subset if we need labels/hierarchy but not the full ontology (content not inspected: **UNVERIFIED** that it keeps all xrefs) |
| 5 | `hgnc_complete_set.txt` | `https://storage.googleapis.com/public-download-files/hgnc/tsv/tsv/hgnc_complete_set.txt` (linked from `https://www.genenames.org/download/archive/`) | 16,963,116 (16.2 MiB) | Last-Modified 2026-10-02 | CC0 | gene nodes: `symbol`, `alias_symbol`, `prev_symbol`, `ensembl_gene_id`, `entrez_id`, `uniprot_ids`, `omim_id` |
| 6a | `ReactomePathways.txt` | `https://reactome.org/download/current/ReactomePathways.txt` or `https://download.reactome.org/97/ReactomePathways.txt` | 1,592,393 (1.5 MiB; 23,603 rows, 2,883 human) | Reactome v97 | CC0 (data) | pathway nodes (stable id, name, species) |
| 6b | `ReactomePathwaysRelation.txt` | `.../current/ReactomePathwaysRelation.txt` | 634,259 (0.6 MiB; 23,717 rows) | v97 | CC0 | pathway hierarchy (parent TAB child) |
| 6c | `Ensembl2Reactome.txt` | `https://download.reactome.org/97/Ensembl2Reactome.txt` (same content at `reactome.org/download/current/`) | **183,277,348 (174.8 MiB)**, all species | v97 | CC0 | gene→pathway (lowest level); too big to pull whole; use Range or the per-gene API instead |
| 6d | `UniProt2Reactome.txt` | `https://download.reactome.org/97/UniProt2Reactome.txt` | **43,041,375 (41.0 MiB)**, all species | v97 | CC0 | protein→pathway (lowest level) |
| 6e | per-gene API | `https://reactome.org/ContentService/data/mapping/UniProt/{acc}/pathways?species=9606` and `.../mapping/ENSEMBL/{ENSG}/pathways?species=9606` | n/a (31 pathways for STAT1) | v97 | CC0 | pathway–gene edges for the 7 cluster genes without the 175 MB file |
| 7 | `goa_human.gaf.gz` | `https://current.geneontology.org/annotations/goa_human.gaf.gz` (also reached via `http://geneontology.org/gene-associations/goa_human.gaf.gz` through 3 redirects) | 15,041,996 (14.3 MiB gz) | GAF 2.2; `date-generated: 2026-05-21`; file Last-Modified 2026-05-28; GO `release-date.json` says 2026-08-05 | CC BY 4.0 | GO process–gene edges; column 7 evidence code (experimental vs IEA) → confidence |

---

## 1. `phenotype.hpoa`

### 1.1 Resolution chain and size

```
$ curl -sIL https://purl.obolibrary.org/obo/hp/hpoa/phenotype.hpoa | grep -iE "^(HTTP|location|content-length|last-modified|accept-ranges)"
HTTP/2 302   location: https://github.com/obophenotype/human-phenotype-ontology/releases/latest/download/phenotype.hpoa
HTTP/2 302   location: https://github.com/obophenotype/human-phenotype-ontology/releases/download/v2026-09-01/phenotype.hpoa
HTTP/2 302   location: https://release-assets.githubusercontent.com/github-production-release-asset/41063438/30a413da-...   (signed, expires in ~1 h)
HTTP/2 200
last-modified: Wed, 02 Sep 2026 21:12:10 GMT
accept-ranges: bytes
content-length: 35816037
```

Range requests work end to end through the redirect (status 206, `Content-Range: bytes 8000000-8150000/35816037`), so a streaming parser is fine.

### 1.2 Latest release (GitHub API)

```
$ curl -s https://api.github.com/repos/obophenotype/human-phenotype-ontology/releases/latest
tag_name: v2026-09-01   name: 2026-09-01 Release   published_at: 2026-09-03T06:08:39Z
html_url: https://github.com/obophenotype/human-phenotype-ontology/releases/tag/v2026-09-01
```

Assets in that release (name, bytes): `genes_to_disease.txt` 1,492,039; `genes_to_phenotype.txt` 20,821,704; `phenotype_to_genes.txt` 67,246,520; `phenotype.hpoa` 35,816,037; `hp.json` 21,915,519; `hp.obo` 10,863,613; `hp.owl` 76,854,086; `hp-base.json` 22,122,839; `hp-base.obo` 11,424,063; `hp-full.json` 43,035,059; `hp-full.obo` 19,469,930; `hp-international.json` 49,442,540; `hp-simple-non-classified.json` 21,915,566; `hp-fr.json` 22,161,722; plus `.owl` variants and `hp-edit.owl-obo-report.tsv`. Pinned URL pattern: `https://github.com/obophenotype/human-phenotype-ontology/releases/download/v2026-09-01/<asset>`.

### 1.3 Header and first rows (Range 0-16383)

```
$ curl -sL -r 0-16383 https://purl.obolibrary.org/obo/hp/hpoa/phenotype.hpoa | head -8
#description: "HPO annotations for rare diseases [8478: OMIM; 47: DECIPHER; 4357 ORPHANET]"
#version: 2026-09-02
#tracker: https://github.com/obophenotype/human-phenotype-ontology/issues
#hpo-version: http://purl.obolibrary.org/obo/hp/releases/2026-09-01/hp.json
database_id	disease_name	qualifier	hpo_id	reference	evidence	onset	frequency	sex	modifier	aspect	biocuration
OMIM:619340	Developmental and epileptic encephalopathy 96		HP:0011097	PMID:31675180	PCS		1/2			P	HPO:probinson[2021-06-21]
OMIM:619340	Developmental and epileptic encephalopathy 96		HP:0002187	PMID:31675180	PCS		1/1			P	HPO:probinson[2021-06-21]
OMIM:619340	Developmental and epileptic encephalopathy 96		HP:0001518	PMID:31675180	PCS		1/2			P	HPO:probinson[2021-06-21]
```

Four `#` comment lines, then a header line (no `#`), then 12 tab-separated columns. Note the annotation file is dated 2026-09-02 while the ontology it references is 2026-09-01; store both.

### 1.4 Column meanings (official docs, `https://obophenotype.github.io/human-phenotype-ontology/annotations/phenotype_hpoa/`)

| # | Field | Required | Docs say | Observed in file |
|---|---|---|---|---|
| 1 | `database_id` | yes | "MIM:154700, ORPHA:558 or MONDO:0007947" | prefixes seen: `OMIM:`, `ORPHA:`, `DECIPHER:` (the docs' `MIM:` spelling and `MONDO:` were **not** seen in 7,001 sampled rows; header counts only OMIM/DECIPHER/ORPHANET) |
| 2 | `disease_name` | yes | label in the source database | free text |
| 3 | `qualifier` | no | `NOT` or empty; "We plan to obsolete this column and replace all such annotations with the precise frequencies" | empty 6,982 / `NOT` 19 in sample |
| 4 | `hpo_id` | yes | HPO id (label must be looked up in hp.json) | `HP:nnnnnnn` |
| 5 | `reference` | yes | "usually PMID or OMIM" | prefixes seen: `PMID:`, `OMIM:`, `ORPHA:`, `DECIPHER:` |
| 6 | `evidence` | yes | `IEA` = "extracted by parsing the Clinical Features sections of the omim.txt file ... Please note that you need to contact OMIM in order to reuse these annotations in other software products"; `PCS` = "published clinical study ... will include the PubMed id"; `TAS` = "traceable author statement, usually reviews or disease entries (e.g. OMIM) that only refers to the original publication" | sample: TAS 4,935 / PCS 1,705 / IEA 361. Orphanet rows are all TAS; HPO-team OMIM rows are PCS (PMID reference) or TAS/IEA (OMIM reference) |
| 7 | `onset` | no | term from the Onset branch | `HP:` term or empty (39 / 6,962 in sample) |
| 8 | `frequency` | no | three formats (see 1.5) | `n/m` 1,500; `HP:004028x` term 4,687; empty 814; **no percent values in sample** |
| 9 | `sex` | no | `MALE` or `FEMALE` | always empty in sample |
| 10 | `modifier` | no | Clinical modifier terms, `;`-separated | mostly empty |
| 11 | `aspect` | yes | `P` Phenotypic abnormality (HP:0000118), `C` Clinical course (HP:0031797), `I` Mode of inheritance (HP:0000005), `M` Clinical modifier (HP:0012823), `H` Past medical history (HP:0032443) | P 6,756 / I 128 / C 112 / H 4 / M 1 |
| 12 | `biocuration` | yes | `HPO:skoehler[YYYY-MM-DD]`, `;`-separated history | e.g. `HPO:probinson[2021-06-21]`, `ORPHA:orphadata[2026-09-02]` |

Docs also say: "For use in semantic similarity analysis, some approaches use only terms that descend from Phenotypic abnormality and filter other annotations out" — i.e. filter `aspect == "P"` before computing IC.

### 1.5 Frequency column formats (official docs, `https://obophenotype.github.io/human-phenotype-ontology/annotations/frequency/`)

1. Frequency terms (sub-ontology under "Frequency"): `HP:0040285` Excluded (0%), `HP:0040284` Very rare (1–4%), `HP:0040283` Occasional (5–29%), `HP:0040282` Frequent (30–79%), `HP:0040281` Very frequent (80–99%), `HP:0040280` Obligate (100%).
2. Counts: "7/13 would indicate that 7 of the 13 patients ... were found to have the phenotypic abnormality". Zero counts occur (`0/2` observed) and mean the feature was looked for and absent.
3. Percentages: "A percentage value such as 17% ... (Note: We are deprecating the use of percentages, and will disallow this type of annotation in the future)". None seen in 7,001 sampled rows.

### 1.6 Sampled value distribution (6 × 150 KB Range windows, 7,001 complete rows)

```
database_id prefixes: {'OMIM': 2337, 'DECIPHER': 5, 'ORPHA': 4659}
evidence: {'PCS': 1705, 'IEA': 361, 'TAS': 4935}
aspect: {'P': 6756, 'I': 128, 'C': 112, 'M': 1, 'H': 4}
qualifier: {'<empty>': 6982, 'NOT': 19}
frequency format: {'n/m': 1500, '<empty>': 814, 'HP term': 4687}
reference prefixes: {'PMID': 1703, 'OMIM': 634, 'DECIPHER': 5, 'ORPHA': 4659}
```

One real row per (prefix, evidence) combination:

```
OMIM:604416	Pyogenic sterile arthritis, pyoderma gangrenosum, and acne		HP:0025452	PMID:9212761	PCS	HP:0003581	4/10			P	HPO:skoehler[2017-07-13];HPO:probinson[2020-09-28]
OMIM:604416	Pyogenic sterile arthritis, pyoderma gangrenosum, and acne		HP:0001876	OMIM:604416	IEA					P	HPO:skoehler[2019-09-07]
OMIM:187550	Thalassemia, beta+, silent allele		HP:0011906	OMIM:187550	TAS					P	HPO:probinson[2009-02-17]
DECIPHER:44	2q37 monosomy		HP:0001156	DECIPHER:44	IEA					P	HPO:skoehler[2013-05-29]
ORPHA:1525	Cranio-osteoarthropathy		HP:0001386	ORPHA:1525	TAS		HP:0040282			P	ORPHA:orphadata[2026-09-02]
```

The file is grouped by disease but **not** sorted by prefix (OMIM rows appear at the start and at 30 MB; ORPHA rows dominate 8–24 MB).

### 1.7 Per-disease spot check without downloading (JAX API, found by probing; docs not fetched)

```
$ curl -s "https://ontology.jax.org/api/network/annotation/OMIM:600802" | head -c 600
{"disease":{"id":"OMIM:600802","name":"Scid, autosomal recessive, T-Negative/b-Positive type","mondoId":"MONDO:0010938","description":"Severe combined immunodeficiency (SCID) T-B+ due to JAK3 deficiency ..."},"categories":{"Clinical course":[{"id":"HP:0003593","name":"Infantile onset","metadata":{"sex":"","onset":"","frequency":"21/24","sources":["PMID:11668610"]},"category":"Clinical course"}],"Immunology":[{"id":"HP:0001287","name":"Meningitis","metadata":{"sex":"","onset":"","frequency":"","sources":["OMIM:600802"]}, ...
```

Returns `disease.mondoId` plus annotations grouped by organ-system category. Good for QA of our parsed edges for the cluster diseases; not a bulk source (rate limits and terms **UNVERIFIED**).

---

## 2. `genes_to_phenotype.txt`, `phenotype_to_genes.txt`, `genes_to_disease.txt`

All in the same release; all have a header line and no `#` comments.

```
$ curl -sL -r 0-4095 https://github.com/obophenotype/human-phenotype-ontology/releases/download/v2026-09-01/genes_to_phenotype.txt | head -5
ncbi_gene_id	gene_symbol	hpo_id	hpo_name	frequency	disease_id
10	NAT2	HP:0000007	Autosomal recessive inheritance	-	OMIM:243400
10	NAT2	HP:0001939	Abnormality of metabolism/homeostasis	-	OMIM:243400
16	AARS1	HP:0002460	Distal muscle weakness	15/15	OMIM:613287
16	AARS1	HP:0008619	Bilateral sensorineural hearing impairment	HP:0040283	ORPHA:33364
```

- 20,821,704 bytes. `ncbi_gene_id` is a bare integer here (no `NCBIGene:` prefix), `frequency` is `-` when absent, otherwise `n/m` or an `HP:004028x` term. Docs: "we list for each gene the most specific HPO-classes (and not all the ancestors)"; HPO-team (OMIM) and Orphanet (ORPHA) annotations appear as separate rows.

```
$ curl -sL -r 0-4095 .../v2026-09-01/phenotype_to_genes.txt | head -3
hpo_id	hpo_name	ncbi_gene_id	gene_symbol	disease_id
HP:0009830	Peripheral neuropathy	773	CACNA1A	OMIM:183086
HP:0009830	Peripheral neuropathy	87178	PNPT1	ORPHA:101111
```

- 67,246,520 bytes. Docs: "this file includes the ancestor classes of each associated phenotype" and "only contains annotations to HPO terms that are descendants of Phenotypic abnormality". We can recompute this from 1 + 3a, so skip the download.

```
$ curl -sL -r 0-2047 .../v2026-09-01/genes_to_disease.txt | head -4
ncbi_gene_id	gene_symbol	association_type	disease_id	source
NCBIGene:64170	CARD9	MENDELIAN	OMIM:212050	ftp://ftp.ncbi.nlm.nih.gov/gene/DATA/mim2gene_medgen
NCBIGene:51256	TBC1D7	MENDELIAN	OMIM:248000	ftp://ftp.ncbi.nlm.nih.gov/gene/DATA/mim2gene_medgen
NCBIGene:6505	SLC1A1	POLYGENIC	OMIM:615232	ftp://ftp.ncbi.nlm.nih.gov/gene/DATA/mim2gene_medgen
```

- 1,492,039 bytes. Here `ncbi_gene_id` **does** carry the `NCBIGene:` prefix (inconsistent with genes_to_phenotype.txt). Docs: "This file provides the disease to gene mappings that are used to create the phenotype.hpoa file. The association types are: MENDELIAN POLYGENIC UNKNOWN". This is the cheapest gene–disease edge source in the fallback stack.

---

## 3. HPO ontology: `hp.json` and `hp.obo`

### 3.1 URLs and sizes

```
$ curl -sIL https://purl.obolibrary.org/obo/hp.json | grep -iE "^(HTTP|location|content-length|last-modified)"
302 → https://github.com/obophenotype/human-phenotype-ontology/releases/latest/download/hp.json
302 → https://github.com/obophenotype/human-phenotype-ontology/releases/download/v2026-09-01/hp.json
302 → https://release-assets.githubusercontent.com/...
200  last-modified: Wed, 02 Sep 2026 21:01:12 GMT  content-length: 21915519  accept-ranges: bytes

$ curl -sIL https://purl.obolibrary.org/obo/hp.obo | ...
200  content-length: 10863613

$ curl -sIL http://purl.obolibrary.org/obo/hp/releases/2026-09-01/hp.json | ...   # dated PURL for pinning
302 → https://github.com/obophenotype/human-phenotype-ontology/releases/download/v2026-09-01/hp.json → 200 content-length: 21915519
```

GitHub's asset CDN rejects suffix ranges (`-r -98304` → Varnish "501 Unsupported client range"); use absolute offsets.

### 3.2 `hp.json` structure (OBO Graphs JSON) — observed

Top level: `{"graphs": [ { "id", "meta", "nodes": [...], "edges": [...], "propertyChainAxioms": [...] } ]}`. Graph `meta.version` = `http://purl.obolibrary.org/obo/hp/releases/2026-09-01/hp.json`; `meta.basicPropertyValues` includes `{"pred": "http://purl.org/dc/terms/license", "val": "https://hpo.jax.org/app/license"}` and `{"pred": "http://www.w3.org/2002/07/owl#versionInfo", "val": "2026-09-01"}`.

Node (first 64 KB):

```json
{
  "id" : "http://purl.obolibrary.org/obo/HP_0000003",
  "lbl" : "Multicystic kidney dysplasia",
  "type" : "CLASS",
  "meta" : {
    "definition" : { "val" : "Multicystic dysplasia of the kidney is characterized by ...", "xrefs" : [ "https://orcid.org/0000-0002-0736-9199" ] },
    "comments" : [ "Multicystic kidney dysplasia is the result of ..." ],
    "synonyms" : [ { "pred" : "hasExactSynonym", "val" : "Multicystic dysplastic kidney" },
                   { "pred" : "hasExactSynonym", "val" : "Multicystic kidneys" } ],
    "basicPropertyValues" : [ { "pred" : "http://www.geneontology.org/formats/oboInOwl#hasAlternativeId", "val" : "HP:0004715" } ]
  }
}
```

- `id` is the full IRI `http://purl.obolibrary.org/obo/HP_0000003`; convert to CURIE by replacing `http://purl.obolibrary.org/obo/HP_` with `HP:`.
- Synonym objects have `pred` (`hasExactSynonym` observed), `val`, optional `synonymType` (e.g. `http://purl.obolibrary.org/obo/hp#layperson`) and optional `xrefs`.
- Node-level cross-references are objects: `"xrefs" : [ { "val" : "MEDDRA:10022016" } ]`, `{ "val" : "Fyler:4493" }` (UMLS/SNOMED values likely further in the file — **UNVERIFIED** in the 64 KB sample).
- Alternate ids are in `basicPropertyValues` with pred `...oboInOwl#hasAlternativeId`.
- Obsolete terms and `deprecated` flag: not in sample — **UNVERIFIED**.

Edges (last 98 KB of the file, bytes 21817215-21915518):

```json
"edges" : [ {
  "sub" : "http://purl.obolibrary.org/obo/HP_6000901",
  "pred" : "is_a",
  "obj" : "http://purl.obolibrary.org/obo/HP_0430150"
}, ... ],
"propertyChainAxioms" : [ { "predicateId" : "http://purl.obolibrary.org/obo/RO_0002314", "chainPredicateIds" : [ "http://purl.obolibrary.org/obo/RO_0000052", "http://purl.obolibrary.org/obo/BFO_0000050" ] } ]
```

- **Gotcha:** the key is `sub`, not `subj`. The obographs README example (`https://raw.githubusercontent.com/geneontology/obographs/master/README.md`) writes `"subj"`, but the real hp.json writes `"sub"`. Code against `sub`.
- All 658 edges in the tail sample have `pred: "is_a"`. README: edges correspond to "`C SubClassOf D` (aka `is_a` in obo-format)" or "`C SubClassOf P some D` (aka `relationship` in obo-format)", so other predicates may appear elsewhere (**UNVERIFIED** for the full file).

`hp.obo` equivalents (first 8 KB): header has `format-version: 1.2`, `data-version: hp/releases/2026-09-01`, `synonymtypedef: layperson "layperson term"`, `property_value: terms:license https://hpo.jax.org/app/license`; a term stanza is

```
[Term]
id: HP:0000002
name: Abnormality of body height
def: "Deviation from the norm of height ..." [https://orcid.org/0000-0002-0736-9199]
synonym: "Abnormality of body height" EXACT layperson []
is_a: HP:0001507 ! Growth abnormality
```

If we only need labels + is_a, `hp.obo` is half the size and trivially parseable (`pronto`/`obonet` or a 20-line parser).

---

## 4. MONDO

### 4.1 URLs, sizes, release

```
$ curl -sIL https://purl.obolibrary.org/obo/mondo.json | ...
302 → https://github.com/monarch-initiative/mondo/releases/latest/download/mondo.json
302 → https://github.com/monarch-initiative/mondo/releases/download/v2026-09-01/mondo.json
200  last-modified: Tue, 01 Sep 2026 17:32:41 GMT  content-length: 107586061  accept-ranges: bytes
$ curl -sIL https://purl.obolibrary.org/obo/mondo.obo | ...
200  content-length: 53134854
$ curl -s https://api.github.com/repos/monarch-initiative/mondo/releases/latest
tag_name: v2026-09-01   published_at: 2026-09-01T17:41:30Z
```

Release assets (bytes): `mondo.json` 107,586,061; `mondo.obo` 53,134,854; `mondo.owl` 254,230,660; `mondo-rare.json` 54,365,899; `mondo-rare.obo` 33,495,699; `mondo-rare_nodes.tsv` 15,436,291; `mondo-rare_edges.tsv` 3,803,629; `mondo_nodes.tsv` 24,664,837; `mondo_edges.tsv` 22,115,136; `mondo-base.*`, `mondo-simple.*`, `mondo-clingen.*`, `mondo-international.*`; `source-versions.tsv` 865; `README.md` 4,845.

`source-versions.tsv` (what this MONDO release was built against):

```
source	ontology	versionIRI	versionInfo
omim	<http://purl.obolibrary.org/obo/mondo/omim.owl>	<http://purl.obolibrary.org/obo/mondo/releases/2026-08-12/omim.owl>	"2026-08-12"
ordo	<https://www.orphadata.com/data/ontologies/ordo/last_version/ORDO_en_4.7.owl>	...	"4.7"
doid	...	<http://purl.obolibrary.org/obo/doid/releases/2026-07-31/doid.owl>	"2026-07-31"
```

`mondo_nodes.tsv` (KGX) header: `id category name description xref provided_by synonym broad_synonyms deprecated exact_synonyms iri narrow_synonyms related_synonyms same_as subsets` — has an `xref` column but is 24.7 MB; not sampled further.

### 4.2 How MONDO cross-references OMIM / Orphanet in `mondo.json` (chunk at byte 60,000,000)

```json
{
  "id" : "http://purl.obolibrary.org/obo/MONDO_0032814",
  "lbl" : "microangiopathy and leukoencephalopathy, pontine, autosomal dominant",
  "type" : "CLASS",
  "meta" : {
    "subsets" : [ "http://purl.obolibrary.org/obo/mondo#gard_rare", "http://purl.obolibrary.org/obo/mondo#orphanet_rare", "http://purl.obolibrary.org/obo/mondo#rare", ... ],
    "synonyms" : [ { "pred" : "hasExactSynonym", "val" : "PADMAL", "synonymType" : "http://purl.obolibrary.org/obo/mondo#ABBREVIATION", "xrefs" : [ "OMIM:618564", "Orphanet:477749" ] }, ... ],
    "xrefs" : [ { "val" : "GARD:0017855" }, { "val" : "MEDGEN:1684781" }, { "val" : "OMIM:618564" }, { "val" : "Orphanet:477749" }, { "val" : "UMLS:C5231411" } ],
    "basicPropertyValues" : [
      { "pred" : "http://www.w3.org/2004/02/skos/core#exactMatch", "val" : "http://www.orpha.net/ORDO/Orphanet_477749" },
      { "pred" : "http://www.w3.org/2004/02/skos/core#exactMatch", "val" : "https://omim.org/entry/618564" },
      { "pred" : "http://www.w3.org/2004/02/skos/core#exactMatch", "val" : "http://identifiers.org/medgen/1684781" },
      { "pred" : "http://purl.obolibrary.org/obo/IAO_0000233", "val" : "https://github.com/monarch-initiative/mondo/issues/6368" } ]
  }
}
```

Three places carry the mapping:
1. `meta.xrefs[].val` as CURIEs: `OMIM:618564`, `Orphanet:477749` (also `DOID:`, `GARD:`, `MEDGEN:`, `UMLS:`, `NCIT:`, `SCTID:`, `ICD10CM:`, `icd11.foundation:`, `OMIMPS:`, `NORD:` seen). Xrefs are **not all exact**: in `mondo.obo` the same xrefs carry `{source="MONDO:equivalentTo"}` vs `{source="MONDO:relatedTo", ...}` qualifiers, which the JSON `xrefs` array drops.
2. `meta.basicPropertyValues` with `pred = http://www.w3.org/2004/02/skos/core#exactMatch` and full IRIs: `https://omim.org/entry/618564`, `http://www.orpha.net/ORDO/Orphanet_477749` — these are the *exact* ones.
3. `synonyms[].xrefs` (provenance of a synonym, not a disease mapping).

The first ~130 KB of `mondo.json` nodes are HGNC gene stubs (`"id" : "http://identifiers.org/hgnc/10001", "lbl" : "RGS5"`), so a streaming parser must skip non-`MONDO_` ids. Note the HGNC IRI form `http://identifiers.org/hgnc/<numeric id>`, used by `has_material_basis_in_germline_mutation_in` relationships (see 4.3) — a second gene→disease source.

### 4.3 Same term in `mondo.obo` (chunk at byte 25,000,000)

```
[Term]
id: MONDO:0012824
name: leukodystrophy, hypomyelinating, 4
subset: ordo_subtype_of_a_disorder {source="Orphanet:280288"}
synonym: "HLD4" EXACT ABBREVIATION [DOID:0060789, OMIM:612233]
xref: DOID:0060789 {source="MONDO:equivalentTo"}
xref: OMIM:612233 {source="Orphanet:280288/e", source="MONDO:equivalentTo", source="DOID:0060789", source="Orphanet:280288"}
xref: Orphanet:280288 {source="OMIM:612233", source="MONDO:equivalentTo", source="DOID:0060789"}
xref: UMLS:C2677109 {source="MONDO:equivalentTo", source="MONDO:MEDGEN", source="MEDGEN:383026"}
is_a: MONDO:0017226 {source="Orphanet:280288"} ! Pelizaeus-Merzbacher-like disease
relationship: has_material_basis_in_germline_mutation_in http://identifiers.org/hgnc/5261 {source="OMIM:612233"} ! HSPD1
property_value: skos:exactMatch OMIM:612233
property_value: skos:exactMatch Orphanet:280288
```

Header `idspace` lines (first 16 KB) define the prefixes: `idspace: OMIM https://omim.org/entry/`, `idspace: OMIMPS https://omim.org/phenotypicSeries/PS`, `idspace: Orphanet http://www.orpha.net/ORDO/Orphanet_`, `idspace: UMLS http://linkedlifedata.com/resource/umls/id/`, `idspace: MEDGEN http://identifiers.org/medgen/`, `idspace: MESH http://identifiers.org/mesh/`, `idspace: SCTID http://identifiers.org/snomedct/`, `idspace: ICD10CM ...`, `idspace: ICD11 ...`. No `DECIPHER` idspace appears in the header.

### 4.4 Recommended mapping file: `mondo.sssom.tsv` (13.1 MB, downloaded in full)

```
$ curl -sIL https://purl.obolibrary.org/obo/mondo/mappings/mondo.sssom.tsv | grep -iE "^(HTTP|location|content-length)"
HTTP/2 302  location: https://raw.githubusercontent.com/monarch-initiative/mondo/master/src/ontology/mappings/mondo.sssom.tsv
HTTP/2 200  content-length: 13113756

$ grep -v '^#' mondo.sssom.tsv | head -3
subject_id	subject_label	predicate_id	object_id	object_label	mapping_justification
MONDO:0000001	disease	skos:exactMatch	DOID:4	disease	semapv:UnspecifiedMatching
MONDO:0000001	disease	skos:exactMatch	Orphanet:377788	disease	semapv:UnspecifiedMatching
```

A YAML `curie_map` lives in `#` comment lines at the top (`OMIM: https://omim.org/entry/`, `Orphanet: http://www.orpha.net/ORDO/Orphanet_`, `OMIMPS: https://omim.org/phenotypicSeries/PS`, `DECIPHER: http://purl.obolibrary.org/obo/mondo/mappings/unknown_prefix/DECIPHER/`, ...).

Full-file tallies (109,715 data rows, 24,549 distinct MONDO subjects):

```
predicate_id: {'skos:exactMatch': 109626, 'skos:broadMatch': 89}
mapping_justification: {'semapv:UnspecifiedMatching': 109715}
object prefixes: MEDGEN 21661, UMLS 21661, DOID 11965, OMIM 10045, Orphanet 9785, SCTID 9124, mesh 8183, NCIT 7312, icd11.foundation 4635, EFO 2400, ICD10CM 2117, OMIMPS 615, ICD10WHO 209, MedDRA 3
OMIM:     10045 rows, all skos:exactMatch; 10045 distinct OMIM ids; 0 OMIM ids map to >1 MONDO; 8 MONDO ids have 2 OMIM objects
Orphanet:  9785 rows, all skos:exactMatch;  9785 distinct ids; 0 map to >1 MONDO; 43 MONDO ids have >1 Orphanet object
DECIPHER:     0 rows   <-- the 47 DECIPHER diseases in phenotype.hpoa cannot be mapped to MONDO with this file
OMIMPS:     615 rows (phenotypic series)
```

So OMIM→MONDO and Orphanet→MONDO are functions (unique target), which is what the pipeline needs. **Prefix gotcha:** phenotype.hpoa writes `ORPHA:1334`; MONDO writes `Orphanet:1334`. Normalise `ORPHA:` → `Orphanet:` before joining. This file is served from `master`, not a tagged release; record its `content-length`/sha256 and retrieval date because it can change between pipeline runs. It is the cheapest correct way to resolve §3 disease IDs; `mondo.json` is only needed if we want MONDO labels/hierarchy/synonyms (then prefer `mondo.obo` or `mondo-rare.obo`).

### 4.5 Cluster diseases resolved from the SSSOM file (subject_label search; verifies the join works)

| MONDO | MONDO label | OMIM | Orphanet |
|---|---|---|---|
| MONDO:0013599 | autoimmune enteropathy and endocrinopathy - susceptibility to chronic infections syndrome (= STAT1 GoF / IMD31C) | OMIM:614162 ("immunodeficiency 31c") | Orphanet:391487 ("STAT1-related autoimmune enteropathy and endocrinopathy-susceptibility to chronic infections syndrome") |
| MONDO:0013956 | Mendelian susceptibility to mycobacterial diseases due to partial STAT1 deficiency | OMIM:614892 (31a) | Orphanet:319595 |
| MONDO:0013427 | immunodeficiency 31B | OMIM:613796 | Orphanet:391311 ("Susceptibility to viral and mycobacterial infections due to STAT1 deficiency") |
| MONDO:0015279 | chronic mucocutaneous candidiasis (grouping) | — | Orphanet:1334 |
| MONDO:0010938 | T-B+ severe combined immunodeficiency due to JAK3 deficiency | OMIM:600802 | Orphanet:35078 |
| MONDO:0010315 | T-B+ severe combined immunodeficiency due to gamma chain deficiency (IL2RG, X-SCID) | OMIM:300400 | Orphanet:276 |
| MONDO:0007818 | hyper-IgE recurrent infection syndrome 1, autosomal dominant (STAT3 LoF/DN) | OMIM:147060 | Orphanet:2314 |
| MONDO:0014414 | STAT3-related early-onset multisystem autoimmune disease (STAT3 GoF) | OMIM:615952 | Orphanet:438159 |
| MONDO:0009891 | acquired polycythemia vera | OMIM:263300 | Orphanet:729 |
| MONDO:0005029 | essential thrombocythemia | — | Orphanet:3318 |
| MONDO:0009692 | primary myelofibrosis | OMIM:254450 | Orphanet:824 |

(JAK1, JAK2 germline and TYK2 entries were not searched; let the pipeline resolve them from genes_to_disease.txt + SSSOM.)

---

## 5. HGNC complete set

```
$ curl -sIL https://storage.googleapis.com/public-download-files/hgnc/tsv/tsv/hgnc_complete_set.txt | ...
HTTP/2 200  content-type: text/plain;charset=utf-8  last-modified: Fri, 02 Oct 2026 13:45:08 GMT  accept-ranges: bytes  content-length: 16963116
```

The HGNC archive page (`https://www.genenames.org/download/archive/`) links exactly `//storage.googleapis.com/public-download-files/hgnc/tsv/tsv/hgnc_complete_set.txt` and `//storage.googleapis.com/public-download-files/hgnc/json/json/hgnc_complete_set.json`; the license page states "All download files including the archive files are now in a publicly accessible Google Storage Bucket." No release version string is exposed; use `Last-Modified`.

53 tab-separated columns (header line, Range 0-8191), numbered:

```
1 hgnc_id  2 symbol  3 name  4 locus_group  5 locus_type  6 status  7 location  8 alias_symbol  9 alias_name  10 prev_symbol  11 prev_name
12 gene_group  13 gene_group_id  14 date_approved_reserved  15 date_symbol_changed  16 date_name_changed  17 date_modified
18 entrez_id  19 ensembl_gene_id  20 vega_id  21 ucsc_id  22 ena  23 refseq_accession  24 ccds_id  25 uniprot_ids  26 pubmed_id
27 mgd_id  28 rgd_id  29 lsdb  30 cosmic  31 omim_id  32 mirbase  33 homeodb  34 snornabase  35 bioparadigms_slc  36 orphanet
37 pseudogene.org  38 horde_id  39 merops  40 imgt  41 iuphar  42 kznf_gene_catalog  43 mamit-trnadb  44 cd  45 lncrnadb  46 enzyme_id
47 intermediate_filament_db  48 rna_central_id  49 lncipedia  50 gtrnadb  51 agr  52 mane_select  53 gencc
```

Multi-valued cells are pipe-separated **and double-quoted** (so use a CSV reader with `quotechar='"'`, not a naive split):

```
symbol='A1CF'      alias_symbol='"ACF|ASP|ACF64|ACF65|APOBEC1CF"'  prev_symbol=''                       ensembl_gene_id='ENSG00000148584'
symbol='A1BG-AS1'  alias_symbol='FLJ23569'                         prev_symbol='"NCRNA00181|A1BGAS|A1BG-AS"' ensembl_gene_id='ENSG00000268895'
mane_select='"ENST00000263100.8|NM_130786.4"'
```

Live check of a cluster gene via the REST API (JSON, no key):

```
$ curl -s -H "Accept: application/json" https://rest.genenames.org/fetch/symbol/STAT1
numFound: 1
hgnc_id = HGNC:11362   symbol = STAT1   name = signal transducer and activator of transcription 1
alias_symbol = ['STAT91', 'ISGF-3']   prev_symbol = None   ensembl_gene_id = ENSG00000115415
entrez_id = 6772   uniprot_ids = ['P42224']   omim_id = ['600555']   orphanet = 119879   locus_type = gene with protein product   status = Approved
```

`entrez_id` here = `ncbi_gene_id` in the HPO gene files (6772), so HGNC is the join key between HPO gene files (NCBI ids), Reactome (Ensembl/UniProt) and GO (UniProt).

---

## 6. Reactome (release 97)

```
$ curl -s https://reactome.org/ContentService/data/database/version
97
```

`https://reactome.org/download/current/<file>` returns HTTP 200 with **no Content-Length** and ignores Range (returns 200, full body). The download page (`https://reactome.org/download-data`) actually links `https://download.reactome.org/version/<file>` where `version` is substituted client-side; the literal `/version/` and `/current/` paths on that host return 403, but the numbered path works and gives sizes and byte ranges:

```
$ curl -sIL https://download.reactome.org/97/Ensembl2Reactome.txt
HTTP/2 200  content-length: 183277348  last-modified: Wed, 24 Jun 2026 19:18:15 GMT  accept-ranges: bytes
UniProt2Reactome.txt            43041375
ReactomePathways.txt             1592393
Ensembl2Reactome_All_Levels.txt 504035190
UniProt2Reactome_All_Levels.txt 117823518
```

Files are all-species. Columns (6, tab-separated, no header), observed:

```
$ curl -s -r 0-1500 https://reactome.org/download/current/Ensembl2Reactome.txt | head -2
2RSSE.1b.1	R-CEL-8980692	https://reactome.org/PathwayBrowser/#/R-CEL-8980692	RHOA GTPase cycle	IEA	Caenorhabditis elegans
# human block, located by a 2-request Range binary search at ~byte 45,819,336 of download.reactome.org/97/Ensembl2Reactome.txt:
ENSG00000167513	R-HSA-68867	https://reactome.org/PathwayBrowser/#/R-HSA-68867	Assembly of the pre-replicative complex	TAS	Homo sapiens
ENSG00000167513	R-HSA-68867	https://reactome.org/PathwayBrowser/#/R-HSA-68867	Assembly of the pre-replicative complex	IEA	Homo sapiens
```

col1 source id (Ensembl gene / UniProt accession), col2 pathway stable id, col3 browser URL, col4 pathway name, col5 evidence (`TAS` or `IEA`; human sample 19 TAS / 8 IEA), col6 species. Rows are sorted by col1, so human `ENSG...` rows form one contiguous block that can be pulled with a handful of Range requests (no need for the 175 MB file).

`ReactomePathways.txt` (3 cols: stable id, name, species) and `ReactomePathwaysRelation.txt` (2 cols: parent, child) were downloaded in full. Human JAK-STAT-relevant entries in v97 — there is **no human pathway literally named "JAK-STAT signaling"**; the closest and its ancestors (from the relation file):

```
R-HSA-8950505 | Gene and protein expression by JAK-STAT signaling after Interleukin-12 stimulation
  ↑ R-HSA-9020591 | Interleukin-12 signaling
  ↑ R-HSA-447115  | Interleukin-12 family signaling
  ↑ R-HSA-449147  | Signaling by Interleukins
  ↑ R-HSA-1280215 | Cytokine Signaling in Immune system
  ↑ R-HSA-168256  | Immune System
Other relevant human pathways: R-HSA-913531 Interferon Signaling (children R-HSA-877300 Interferon gamma signaling, R-HSA-909733 Interferon alpha/beta signaling),
R-HSA-451927 Interleukin-2 family signaling, R-HSA-6783589 Interleukin-6 family signaling, R-HSA-1266695 Interleukin-7 signaling, R-HSA-1059683 Interleukin-6 signaling,
R-HSA-9645135 STAT5 Activation, R-HSA-198745 Signalling to STAT3, R-HSA-8875791 MET activates STAT3
```

Per-gene API (verified, no key; both id types):

```
$ curl -s "https://reactome.org/ContentService/data/mapping/UniProt/P42224/pathways?species=9606"   # STAT1
count: 31   e.g. R-HSA-1059683 Interleukin-6 signaling | R-HSA-877300 Interferon gamma signaling | R-HSA-6785807 Interleukin-4 and Interleukin-13 signaling | R-HSA-8984722 Interleukin-35 Signalling
keys: className, dbId, displayName, hasDiagram, hasEHLD, isInDisease, isInferred, lastUpdatedDate, maxDepth, name, releaseDate, schemaClass, speciesName, stId, stIdVersion
$ curl -s "https://reactome.org/ContentService/data/mapping/ENSEMBL/ENSG00000115415/pathways?species=9606"   # same 31
```

For 7 cluster genes this is 7 calls; for all HGNC genes use the Range-block approach on `Ensembl2Reactome.txt` instead. The API response has no evidence code; the flat file does.

---

## 7. GO annotations: `goa_human.gaf.gz`

```
$ curl -sIL https://current.geneontology.org/annotations/goa_human.gaf.gz | ...
HTTP/2 200  content-type: application/gzip  content-length: 15041996  last-modified: Thu, 28 May 2026 21:18:27 GMT  accept-ranges: bytes
$ curl -sIL http://geneontology.org/gene-associations/goa_human.gaf.gz        # legacy URL
301 → https://geneontology.org/gene-associations/goa_human.gaf.gz → 302 → http://current.geneontology.org/annotations/goa_human.gaf.gz → 301 → https://current.geneontology.org/annotations/goa_human.gaf.gz → 200 (same 15041996 bytes)
$ curl -s https://current.geneontology.org/metadata/release-date.json
{date: 2026-08-05}
```

Header (first 128 KB of the gzip, decompressed with zlib) and first rows:

```
!gaf-version: 2.2
!generated-by: GOC
!date-generated: 2026-05-21T08:10
!Header from goa_human source association file:
!generated-by: UniProt
!date-generated: 2026-04-30 09:29
!go-version: http://purl.obolibrary.org/obo/go/releases/2026-04-27/extensions/go-plus.ofn
!Header copied from paint_goa_human_valid.gaf   ... PANTHER version: v.19.0.  GO version: 2025-10-05.
UniProtKB	A0A024RBG1	NUDT4B	enables	GO:0005515	PMID:33961781	IPI	UniProtKB:Q8NFP7	F	Diphosphoinositol polyphosphate phosphohydrolase NUDT4B	NUDT4B	protein	taxon:9606	20260425	IntAct		UniProtKB:A0A024RBG1
UniProtKB	A0A024RBG1	NUDT4B	enables	GO:0008486	GO_REF:0000003	IEA	EC:3.6.1.52	F	Diphosphoinositol polyphosphate phosphohydrolase NUDT4B	NUDT4B	protein	taxon:9606	20260428	UniProt		UniProtKB:A0A024RBG1
UniProtKB	A0A075B6H7	IGKV3-7	located_in	GO:0005576	PMID:20176268	NAS		C	Probable non-functional immunoglobulin kappa variable 3-7	IGKV3-7	protein	taxon:9606	20240313	UniProt		UniProtKB:A0A075B6H7
```

Note the release-date metadata (2026-08-05) is later than the annotation file's own `date-generated` (2026-05-21) and Last-Modified (2026-05-28); record all three. Comment lines start with `!`.

GAF 2.2 columns (official spec `https://geneontology.org/docs/go-annotation-file-gaf-format-2.2/`): "comprised of 17 tab-delimited fields":

```
1 DB (UniProtKB)  2 DB Object ID (P12345)  3 DB Object Symbol (GOT2)  4 Relation (involved_in; negation as "NOT|involved_in")  5 GO ID
6 Reference (PMID:... or GO_REF:...)  7 Evidence Code (TAS / IEA / IDA ...)  8 With (or) From  9 Aspect (P / F / C)  10 DB Object Name
11 DB Object Synonym  12 DB Object Type (protein)  13 Taxon (taxon:9606)  14 Date (YYYYMMDD)  15 Assigned By  16 Annotation Extension  17 Gene Product Form ID
```

Evidence codes (official guide `https://geneontology.org/docs/guide-go-evidence-codes/`): experimental = `EXP, IDA, IPI, IMP, IGI, IEP`; high-throughput experimental = `HTP, HDA, HMP, HGI, HEP`; phylogenetic = `IBA, IBD, IKR, IRD`; computational = `ISS, ISO, ISA, ISM, IGC, RCA`; author statements = `TAS, NAS`; curator = `IC, ND`; automatic = `IEA` ("Inferred from Electronic Annotation"). Spec: With/From "Cardinality must be 0 for evidence codes IDA, TAS, NAS, or ND" and "must be 1, >1 for IEA, IC, IGI, IPI, ISS". For the README confidence rubric ("experimental high; IEA low"): map col 7 ∈ {EXP, IDA, IPI, IMP, IGI, IEP} → high, {HTP..HEP, IBA, ISS, TAS} → medium, IEA → low; filter col 9 == `P` for "GO process–gene" edges and drop rows whose col 4 starts with `NOT|`. Join to HGNC on col 2 (UniProt accession) via `uniprot_ids`.

The `current.geneontology.org/annotations/` directory index lists a `gaf/` sub-directory of per-species `*-uniprot.gaf.gz` files (194 links), including `https://current.geneontology.org/annotations/gaf/HUMAN-uniprot.gaf.gz` (HEAD: 200, `content-length: 12109254`, `last-modified: Sat, 08 Aug 2026 22:15:52 GMT` — i.e. dated with the 2026-08-05 GO release, unlike `goa_human.gaf.gz` whose header says 2026-05-21). `goa_human.gaf.gz` itself sits one level up at `/annotations/goa_human.gaf.gz` (verified by HEAD above). The relationship between the two human files (which is the superset, and why `goa_human` lags the release) is **UNVERIFIED**; the README names `goa_human.gaf.gz`, so use that, but record the dates and consider `HUMAN-uniprot.gaf.gz` if freshness matters.

---

## 8. Licenses (quoted from the pages fetched 2026-10-03)

| Source | Page | Quote | Implication |
|---|---|---|---|
| **HPO** (ontology + annotations + gene files) | `https://hpo.jax.org/license` (JS app; rendered in the built-in browser; the repo `LICENSE.md` only says "Please read the license explanation at the HPO website"; GitHub API `spdx_id: NOASSERTION`) | "The HPO vocabularies, annotation files, tools and documentation are freely available. The HPO is copyrighted to protect the integrity of the vocabularies ... anyone can download the HPO and use the ontologies or other HPO files under three conditions: That the Human Phenotype Ontology Consortium is acknowledged and cited properly. That any HPO Consortium file(s) displayed publicly include the date(s) and/or version number(s) of the relevant HPO file(s). That neither the content of the HPO file(s) nor the logical relationships embedded within the HPO file(s) be altered in any way." Also: "Users of the HPO should add the following statement to their online presence. This service/product uses the Human Phenotype Ontology (version information). Find out more at http://www.human-phenotype-ontology.org We request that the HPO logo be included as well." | **Not** CC BY. Free to use; must cite, show version (we have it: 2026-09-01 / hpoa 2026-09-02), not alter term content, add the statement + logo to the site. Separately, the hpoa docs warn for `IEA` rows: "you need to contact OMIM in order to reuse these annotations in other software products" — see §9 |
| **MONDO** | repo `LICENSE` = "Attribution 4.0 International"; release `README.md` "## License [CC-BY 4.0](https://creativecommons.org/licenses/by/4.0/)"; `mondo.json` meta `dc:terms/license` = `http://creativecommons.org/licenses/by/4.0/` | CC BY 4.0 | attribute Mondo (cite Vasilevsky et al., Genetics 2026, iyaf215, per README) |
| **HGNC** | `https://www.genenames.org/about/license/` | "all data is released under the Creative Commons Public Domain (CC0) License. This means that any form of reuse of the content is permitted." "Although stating the source is not mandatory, the HGNC recommends attribution" | CC0 |
| **Reactome** | `https://reactome.org/license` | "c) Data: All data in the Reactome database and files derived from that data are licensed under the Creative Commons Public Domain Dedication (CC0). User may copy, modify, and distribute these data, even for commercial purposes, without asking for permission. Attribution is encouraged but not required." (Illustrations/icons are CC BY 4.0; software Apache 2.0) | CC0 for the mapping files |
| **GO** | `https://geneontology.org/docs/go-citation-policy/` | "Gene Ontology Consortium data and data products are licensed under the Creative Commons Attribution 4.0 Unported License." and "We ask that when using or citing GO data that the particular release is mentioned." | CC BY 4.0; cite Ashburner 2000 + GO Consortium NAR 2026 (gkaf1292) and the release date |

---

## 9. Gotchas and recommendations for `pipeline/sources/`

1. **IEA rows in phenotype.hpoa are OMIM-derived and have a reuse caveat** (docs: "contact OMIM in order to reuse these annotations in other software products"). They are ~5% of rows in the sample and they come with `reference = OMIM:<id>`. Options: compute IC over everything but only *display* PCS/TAS edges; or drop IEA entirely (the README rubric already ranks PCS/TAS above IEA). Decide explicitly and record it in the README license table.
2. **Prefix mismatch:** hpoa `ORPHA:` ↔ MONDO `Orphanet:`; hpoa `OMIM:` ↔ MONDO `OMIM:` (same). Normalise before joining. `DECIPHER:` (47 diseases) has no MONDO mapping in `mondo.sssom.tsv`; keep them as their own nodes or drop them.
3. **IC over all diseases:** phenotype.hpoa has 12,882 diseases; IC(t) = −log(|diseases annotated to t or a descendant| / |all diseases|). Use `aspect == P`, propagate over `is_a` from `hp.json`/`hp.obo`, treat `qualifier == NOT` and frequency `0/n` / `HP:0040285` as *excluded* (do not count as annotated). Orphanet rows duplicate many OMIM diseases under a different id; if that matters for IC, collapse via SSSOM to MONDO first (both OMIM→MONDO and Orphanet→MONDO are unique).
4. `hp.json` edge keys are `sub`/`pred`/`obj` (not `subj`); ids are full IRIs. `hp.obo` (10.4 MB) is the lighter choice if only labels, synonyms and `is_a` are needed.
5. Don't fetch `mondo.json` (103 MB) just to map ids: `mondo.sssom.tsv` (12.5 MB, 6 columns) does it; it lives on `master`, so pin by sha256 + retrieval date.
6. Reactome mapping files: don't download `Ensembl2Reactome.txt` (175 MB). Either (a) call `ContentService/data/mapping/{UniProt|ENSEMBL}/{id}/pathways?species=9606` per gene (31 pathways for STAT1), or (b) Range-fetch the contiguous `ENSG` block from `https://download.reactome.org/97/Ensembl2Reactome.txt` (supports `Range`; GitHub does too, Varnish rejects suffix ranges). Pin the release number `97` in the URL.
7. There is no single "JAK-STAT signaling" pathway node in Reactome human; model the pathway as the set {R-HSA-913531 Interferon Signaling, R-HSA-449147 Signaling by Interleukins (or its IL-2/IL-6/IL-7/IL-12 family children), R-HSA-8950505} or define our own `Pathway` node that links to these Reactome ids with provenance.
8. HGNC TSV: quoted pipe-separated multi-values; `entrez_id` joins to HPO `ncbi_gene_id` (bare integer in genes_to_phenotype.txt, `NCBIGene:`-prefixed in genes_to_disease.txt); `uniprot_ids` joins to GO col 2 and Reactome UniProt files; `ensembl_gene_id` joins to Reactome Ensembl files.
9. Versions to record in the README license table: HPO release `v2026-09-01` (hpoa `#version: 2026-09-02`), MONDO `v2026-09-01` (OMIM source 2026-08-12, ORDO 4.7), HGNC Last-Modified 2026-10-02, Reactome 97, GO goa_human `date-generated 2026-05-21` (GO release metadata 2026-08-05).
10. All GitHub release assets redirect through a signed `release-assets.githubusercontent.com` URL that expires in about an hour; always start from the PURL or the `releases/download/<tag>/<asset>` URL, never cache the signed one.

---

## 10. Verified commands (copy/paste)

```bash
# HPO
curl -sIL https://purl.obolibrary.org/obo/hp/hpoa/phenotype.hpoa
curl -s https://api.github.com/repos/obophenotype/human-phenotype-ontology/releases/latest
curl -sL -r 0-16383 https://purl.obolibrary.org/obo/hp/hpoa/phenotype.hpoa
curl -sL -r 0-4095 https://github.com/obophenotype/human-phenotype-ontology/releases/download/v2026-09-01/genes_to_phenotype.txt
curl -sL -r 0-4095 https://github.com/obophenotype/human-phenotype-ontology/releases/download/v2026-09-01/phenotype_to_genes.txt
curl -sL -r 0-2047 https://github.com/obophenotype/human-phenotype-ontology/releases/download/v2026-09-01/genes_to_disease.txt
curl -sIL https://purl.obolibrary.org/obo/hp.json
curl -sL -r 0-65535 https://purl.obolibrary.org/obo/hp.json
curl -sL -r 21817215-21915518 https://purl.obolibrary.org/obo/hp.json      # edges live at the end; suffix ranges are rejected
curl -sIL https://purl.obolibrary.org/obo/hp.obo
curl -sL -r 0-8191 https://purl.obolibrary.org/obo/hp.obo
curl -sIL http://purl.obolibrary.org/obo/hp/releases/2026-09-01/hp.json
curl -s "https://ontology.jax.org/api/network/annotation/OMIM:600802"
# MONDO
curl -sIL https://purl.obolibrary.org/obo/mondo.json
curl -sIL https://purl.obolibrary.org/obo/mondo.obo
curl -s https://api.github.com/repos/monarch-initiative/mondo/releases/latest
curl -sL -r 0-131071 https://purl.obolibrary.org/obo/mondo.json
curl -sL -r 60000000-60300000 https://purl.obolibrary.org/obo/mondo.json
curl -sL -r 0-16383 https://purl.obolibrary.org/obo/mondo.obo
curl -sL -r 25000000-25060000 https://purl.obolibrary.org/obo/mondo.obo
curl -sIL https://purl.obolibrary.org/obo/mondo/mappings/mondo.sssom.tsv
curl -sL -o mondo.sssom.tsv https://purl.obolibrary.org/obo/mondo/mappings/mondo.sssom.tsv
curl -sL https://github.com/monarch-initiative/mondo/releases/download/v2026-09-01/source-versions.tsv
curl -sL https://github.com/monarch-initiative/mondo/releases/download/v2026-09-01/README.md
# HGNC
curl -sIL https://storage.googleapis.com/public-download-files/hgnc/tsv/tsv/hgnc_complete_set.txt
curl -sL -r 0-8191 https://storage.googleapis.com/public-download-files/hgnc/tsv/tsv/hgnc_complete_set.txt
curl -s -H "Accept: application/json" https://rest.genenames.org/fetch/symbol/STAT1
# Reactome
curl -s https://reactome.org/ContentService/data/database/version
curl -sIL https://download.reactome.org/97/Ensembl2Reactome.txt
curl -sIL https://download.reactome.org/97/UniProt2Reactome.txt
curl -s https://reactome.org/download/current/ReactomePathways.txt
curl -s https://reactome.org/download/current/ReactomePathwaysRelation.txt
curl -s -r 0-1500 https://reactome.org/download/current/Ensembl2Reactome.txt
curl -s "https://reactome.org/ContentService/data/mapping/UniProt/P42224/pathways?species=9606"
curl -s "https://reactome.org/ContentService/data/mapping/ENSEMBL/ENSG00000115415/pathways?species=9606"
# GO
curl -sIL https://current.geneontology.org/annotations/goa_human.gaf.gz
curl -sL -r 0-131071 -o goa_head.gz https://current.geneontology.org/annotations/goa_human.gaf.gz   # then zlib.decompressobj(16+zlib.MAX_WBITS)
curl -s https://current.geneontology.org/metadata/release-date.json
# Licenses / docs
curl -sL https://www.genenames.org/about/license/
curl -sL https://reactome.org/license
curl -sL https://geneontology.org/docs/go-citation-policy/
curl -sL https://geneontology.org/docs/go-annotation-file-gaf-format-2.2/
curl -sL https://geneontology.org/docs/guide-go-evidence-codes/
curl -sL https://obophenotype.github.io/human-phenotype-ontology/annotations/phenotype_hpoa/
curl -sL https://obophenotype.github.io/human-phenotype-ontology/annotations/frequency/
curl -sL https://obophenotype.github.io/human-phenotype-ontology/annotations/genes_to_phenotype/
curl -sL https://obophenotype.github.io/human-phenotype-ontology/annotations/phenotype_to_genes/
curl -sL https://obophenotype.github.io/human-phenotype-ontology/annotations/genes_to_disease/
curl -sL https://raw.githubusercontent.com/monarch-initiative/mondo/master/LICENSE
curl -s https://api.github.com/repos/obophenotype/human-phenotype-ontology/license
# HPO license page is a JS app: curl returns only the shell; render https://hpo.jax.org/license in a browser.
```

## 11. UNVERIFIED / not checked

- Exact node and edge counts of `hp.json` and `mondo.json` (only head/tail/mid chunks were read).
- Whether percent-format frequencies (`22%`) or `MONDO:` database_ids occur anywhere in the current `phenotype.hpoa` (docs allow both; none in 7,001 sampled rows; header counts only OMIM/DECIPHER/ORPHANET).
- Whether `mondo.json` `meta.xrefs` ever contain `DECIPHER:` ids (none in the 300 KB chunk; `mondo.sssom.tsv` has 0).
- Whether `hp.json` contains any non-`is_a` edge predicates (658 tail edges were all `is_a`).
- Node-level `UMLS:`/`SNOMEDCT_US:` xrefs in `hp.json` (only `MEDDRA:` and `Fyler:` seen in the first 64 KB).
- Human evidence-code distribution across all of `Ensembl2Reactome.txt` (27 human rows sampled: 19 TAS, 8 IEA).
- Reason for the GO release-date (2026-08-05) vs goa_human `date-generated` (2026-05-21) gap.
- Terms of use / rate limits of `https://ontology.jax.org/api/network/annotation/{id}` (undocumented endpoint found by probing; response verified).
- `mondo-rare.json` content (whether it preserves all OMIM/Orphanet xrefs).
- HPO docs `MIM:` prefix spelling vs file `OMIM:` — file wins.

## 12. Saved samples (`samples/`)

`hpoa_head.txt`, `hp_json_head.txt`, `hp_json_tail.txt`, `hp_obo_head.txt`, `mondo_json_head.txt`, `mondo_json_mid.txt`, `mondo_obo_head.txt`, `mondo_obo_head2.txt`, `mondo_obo_mid.txt`, `mondo_sssom_head.txt`, `mondo.sssom.tsv` (full, 13.1 MB), `hgnc_head.txt`, `hgnc_stat1.json`, `ReactomePathways.txt` (full), `ReactomePathwaysRelation.txt` (full), `reactome_stat1.json`, `reactome_stat1_ensg.json`, `goa_head.gz`, `jax_omim600802.json`, plus the fetched HTML of each license/docs page (`hgnc_license.html`, `reactome_license.html`, `go_license.html`, `gaf22.html`, `go_evidence.html`, `hpoa_docs.html`, `docs_frequency.html`, `docs_genes_to_phenotype.html`, `docs_phenotype_to_genes.html`, `docs_genes_to_disease.html`, `obographs_readme.md`).

---

## Re-run check (second reviewer)

Re-run date: 2026-10-03 (same day as the original). Method: every command in §10 was re-executed with `curl` / `/opt/anaconda3/bin/python3`; raw outputs are in `rerun_hpo/` next to this file (114 files, 21 MB; the only full downloads were `mondo.sssom.tsv` 13.1 MB, `ReactomePathways.txt` 1.5 MB, `ReactomePathwaysRelation.txt` 0.6 MB). Nothing in `/Users/jonathanyan/Desktop/projects/Pyrid` was touched. Verdict: **every command ran and every URL, size, version, redirect chain, field path and count that is checkable reproduced**, with the corrections below. The corrections are about (a) the *description* of `phenotype.hpoa` contents in §1.4–1.6 (four claims contradicted by a larger sample), (b) the Reactome human-block offset in §6, (c) the HPO license URL status, and (d) several "head -N" samples that are curated rows rather than literal `head -N` output.

### R.1 Pass table (commands from §10, re-run)

| § | Command (abridged) | Re-run result | Matches original? |
|---|---|---|---|
| 1.1 | `curl -sIL .../hp/hpoa/phenotype.hpoa` | 302 → `releases/latest/download/phenotype.hpoa` → 302 → `releases/download/v2026-09-01/phenotype.hpoa` → 302 → `release-assets.githubusercontent.com/...` → 200, `content-length: 35816037`, `last-modified: Wed, 02 Sep 2026 21:12:10 GMT`, `accept-ranges: bytes` | yes |
| 1.1 | `curl -sL -r 8000000-8150000 ...phenotype.hpoa` | 206, `content-range: bytes 8000000-8150000/35816037` | yes |
| 1.2 | `curl -s api.github.com/.../human-phenotype-ontology/releases/latest` | `tag_name v2026-09-01`, `name 2026-09-01 Release`, `published_at 2026-09-03T06:08:39Z`; all 14 asset sizes quoted in §1.2 match (23 assets total) | yes |
| 1.3 | `curl -sL -r 0-16383 ...phenotype.hpoa` | 206; 4 `#` lines (`#description: "HPO annotations for rare diseases [8478: OMIM; 47: DECIPHER; 4357 ORPHANET]"`, `#version: 2026-09-02`, `#tracker`, `#hpo-version: .../hp/releases/2026-09-01/hp.json`), 12-column header, first 3 rows identical | yes |
| 2 | `curl -sL -r 0-4095 .../genes_to_phenotype.txt` | 206, `content-range: bytes 0-4095/20821704`; header `ncbi_gene_id gene_symbol hpo_id hpo_name frequency disease_id` | yes (sample rows: see R.2.9) |
| 2 | `curl -sL -r 0-4095 .../phenotype_to_genes.txt` | 206, `/67246520`; header + first 2 rows identical | yes |
| 2 | `curl -sL -r 0-2047 .../genes_to_disease.txt` | 206, `/1492039`; header identical, `NCBIGene:` prefix confirmed | yes (sample rows: see R.2.9) |
| 3.1 | `curl -sIL .../hp.json` | same 3-hop chain → 200, `21915519`, `last-modified: Wed, 02 Sep 2026 21:01:12 GMT` | yes |
| 3.2 | `curl -sL -r 0-65535 .../hp.json` | 206; `meta.version` = `http://purl.obolibrary.org/obo/hp/releases/2026-09-01/hp.json`; `owl#versionInfo` = `2026-09-01`; `dc/terms/license` = `https://hpo.jax.org/app/license`; node `HP_0000003` as quoted (`definition.val/xrefs`, `comments`, `synonyms[].pred/val`, `basicPropertyValues` `hasAlternativeId` → `HP:0004715`); `hp#layperson` present; `"xrefs" : [ { "val" : "MEDDRA:10022016" } ]` and `Fyler:4493` present | yes (+ see R.3.1) |
| 3.2 | `curl -sL -r 21817215-21915518 .../hp.json` | 206, `bytes 21817215-21915518/21915519`; 658 × `"sub"`, 0 × `"subj"`, 658 × `"pred" : "is_a"`; `propertyChainAxioms` with `RO_0002314` ← [`RO_0000052`, `BFO_0000050`] | yes |
| 3.1 | suffix range `curl -sL -r -1024 .../hp.json` | HTTP 501 | yes |
| 3.1 | `curl -sIL .../hp.obo` | 200, `10863613`, `last-modified: Wed, 02 Sep 2026 20:59:18 GMT` | yes |
| 3.2 | `curl -sL -r 0-8191 .../hp.obo` | `format-version: 1.2`, `data-version: hp/releases/2026-09-01`, `synonymtypedef: layperson "layperson term"`, `property_value: terms:license https://hpo.jax.org/app/license`; `HP:0000002` stanza identical | yes |
| 3.1 | `curl -sIL http://purl.obolibrary.org/obo/hp/releases/2026-09-01/hp.json` | `HTTP/1.1 302 Found` → `releases/download/v2026-09-01/hp.json` → 302 → 200 `21915519` | yes |
| 1.7 | `curl -s "https://ontology.jax.org/api/network/annotation/OMIM:600802"` | 200 `application/json`; `disease.id/name/mondoId (MONDO:0010938)/description` identical; `categories` keyed by organ system (10 keys) | yes (+ see R.3.3) |
| 4.1 | `curl -sIL .../mondo.json` | 3-hop chain → 200, `107586061`, `last-modified: Tue, 01 Sep 2026 17:32:41 GMT` | yes |
| 4.1 | `curl -sIL .../mondo.obo` | → 200, `53134854`, `last-modified: Tue, 01 Sep 2026 17:32:21 GMT` | yes |
| 4.1 | `curl -s api.github.com/.../mondo/releases/latest` | `tag_name v2026-09-01`, `published_at 2026-09-01T17:41:30Z`; all 11 asset sizes quoted match (29 assets total) | yes |
| 4.2 | `curl -sL -r 0-131071 .../mondo.json` | 206; first node id `http://purl.obolibrary.org/obo/mondo.owl`, then 1,222 `http://identifiers.org/hgnc/<n>` stubs; `dc/terms/license` val `http://creativecommons.org/licenses/by/4.0/`; graph `version` `http://purl.obolibrary.org/obo/mondo/releases/2026-09-01/mondo.owl` | yes |
| 4.2 | `curl -sL -r 60000000-60300000 .../mondo.json` | 206; `MONDO_0032814` present with exactly the quoted `meta.xrefs` (`GARD:0017855, MEDGEN:1684781, OMIM:618564, Orphanet:477749, UMLS:C5231411`), the `PADMAL` synonym object (`synonymType ...mondo#ABBREVIATION`, `xrefs [OMIM:618564, Orphanet:477749]`) and `skos/core#exactMatch` to `https://omim.org/entry/618564` / `http://www.orpha.net/ORDO/Orphanet_477749` / `http://identifiers.org/medgen/1684781`; xref prefixes in chunk: DOID, GARD, MEDGEN, OMIM, UMLS, Orphanet, SCTID, NORD, icd11.foundation, NCIT, ICD10CM, OMIMPS; 0 × `DECIPHER` | yes |
| 4.3 | `curl -sL -r 0-16383 .../mondo.obo` | `format-version: 1.2`, `data-version: releases/2026-09-01`; all 9 quoted `idspace:` lines present verbatim; 0 `idspace: DECIPHER` | yes |
| 4.3 | `curl -sL -r 25000000-25060000 .../mondo.obo` | `MONDO:0012824` stanza present; every quoted line is verbatim (the real stanza has ~20 more lines: `def:`, more `subset:`/`synonym:`/`xref:` incl. `NANDO:`, `intersection_of:`) | yes (trimmed sample) |
| 4.4 | `curl -sIL .../mondo/mappings/mondo.sssom.tsv` | 302 → `raw.githubusercontent.com/monarch-initiative/mondo/master/src/ontology/mappings/mondo.sssom.tsv` → 200, `13113756`, `accept-ranges: bytes` | yes |
| 4.4 | `curl -sL -o mondo.sssom.tsv ...` + recount | 13,113,756 bytes, **sha256 `317ac4bf93be1deff4406b4d7d18bb730ead3c4f5c7c814f75d24be7f6ef302f`**; 6 columns; 109,715 data rows; 24,549 distinct subjects; `skos:exactMatch` 109,626 / `skos:broadMatch` 89; `semapv:UnspecifiedMatching` 109,715; prefix tallies identical (MEDGEN 21661, UMLS 21661, DOID 11965, OMIM 10045, Orphanet 9785, SCTID 9124, mesh 8183, NCIT 7312, icd11.foundation 4635, EFO 2400, ICD10CM 2117, OMIMPS 615, ICD10WHO 209, MedDRA 3); OMIM 10045 rows all exactMatch, 10045 distinct, 0 → >1 MONDO, 8 MONDO with 2 OMIM; Orphanet 9785/9785/0/43 MONDO with >1 Orphanet (41 with exactly 2); DECIPHER 0; OMIMPS 615 | yes (every number) — sample rows: see R.2.8 |
| 4.5 | cluster table | all 11 MONDO ids, labels, OMIM and Orphanet objects reproduced from the file | yes |
| 4.1 | `source-versions.tsv` | 865 bytes; `omim ... releases/2026-08-12/omim.owl "2026-08-12"`, `ordo ... ORDO_en_4.7.owl "4.7"`, `doid ... releases/2026-07-31/doid.owl "2026-07-31"` (+ icd10cm, icd10who, icd11foundation, ncit rows) | yes |
| 8 | mondo release `README.md` / repo `LICENSE` | 4,845 bytes, `## License` → `[CC-BY 4.0](https://creativecommons.org/licenses/by/4.0/)`, citation Vasilevsky et al., Genetics 232(4) 2026, iyaf215; LICENSE first line `Attribution 4.0 International` | yes |
| 4.1 | (extra) `curl -sL -r 0-600 .../v2026-09-01/mondo_nodes.tsv` | header exactly as quoted (`id category name description xref provided_by synonym broad_synonyms deprecated exact_synonyms iri narrow_synonyms related_synonyms same_as subsets`) | yes |
| 5 | `curl -sIL storage.googleapis.com/.../hgnc_complete_set.txt` | 200, `text/plain;charset=utf-8`, `last-modified: Fri, 02 Oct 2026 13:45:08 GMT`, `accept-ranges: bytes`, `content-length: 16963116` | yes |
| 5 | `curl -sL -r 0-8191 ...hgnc_complete_set.txt` | 206; 53 columns in exactly the quoted order; quoted `A1CF`/`A1BG-AS1` quoted-pipe cells and `mane_select` value identical | yes |
| 5 | `curl -s -H "Accept: application/json" rest.genenames.org/fetch/symbol/STAT1` | `response.numFound 1`; `HGNC:11362`, aliases `['STAT91','ISGF-3']`, `ENSG00000115415`, `entrez_id 6772`, `uniprot_ids ['P42224']`, `omim_id ['600555']`, `orphanet 119879` | yes |
| 8 | `genenames.org/about/license/` | 200; "all data is released under the Creative Commons Public Domain (CC0) License", "Although stating the source is not mandatory, the HGNC recommends attribution", "All download files including the archive files are now in a publicly accessible Google Storage Bucket" | yes |
| 5 | (extra) `genenames.org/download/archive/` | 200; links exactly `//storage.googleapis.com/public-download-files/hgnc/tsv/tsv/hgnc_complete_set.txt` and `.../hgnc/json/json/hgnc_complete_set.json` | yes |
| 6 | `reactome.org/ContentService/data/database/version` | `97` | yes |
| 6 | `curl -sIL download.reactome.org/97/Ensembl2Reactome.txt` | 200, `183277348`, `last-modified: Wed, 24 Jun 2026 19:18:15 GMT`, `accept-ranges: bytes`; `UniProt2Reactome.txt` 43041375; `ReactomePathways.txt` 1592393; `ReactomePathwaysRelation.txt` 634259; `Ensembl2Reactome_All_Levels.txt` 504035190; `UniProt2Reactome_All_Levels.txt` 117823518; `/version/...` and `/current/...` on this host → 403 | yes |
| 6 | `curl -s reactome.org/download/current/ReactomePathways.txt` | 200, no `content-length`; 23,603 rows / 2,883 `Homo sapiens` / 3 cols; **byte-identical** to `samples/ReactomePathways.txt` | yes |
| 6 | `curl -s .../ReactomePathwaysRelation.txt` | 23,717 rows / 2 cols; byte-identical to the original sample | yes |
| 6 | `curl -s -r 0-1500 reactome.org/download/current/Ensembl2Reactome.txt` | HTTP 200 (Range ignored, no `content-range`/`content-length`); first row `2RSSE.1b.1	R-CEL-8980692	...	RHOA GTPase cycle	IEA	Caenorhabditis elegans` (piped through `head -c 1501` so the full 175 MB body is not pulled) | yes |
| 6 | pathway names / hierarchy | all 19 stable ids named in §6 resolve to the quoted names in v97; chain `R-HSA-8950505 ← 9020591 ← 447115 ← 449147 ← 1280215 ← 168256` reproduced; no human row with name `JAK-STAT signaling` (only `R-HSA-8950505 Gene and protein expression by JAK-STAT signaling after Interleukin-12 stimulation`) | yes (+ see R.3.4) |
| 6 | `.../mapping/UniProt/P42224/pathways?species=9606` | 200, 31 pathways; keys exactly `className, dbId, displayName, hasDiagram, hasEHLD, isInDisease, isInferred, lastUpdatedDate, maxDepth, name, releaseDate, schemaClass, speciesName, stId, stIdVersion`; the 4 quoted examples present | yes |
| 6 | `.../mapping/ENSEMBL/ENSG00000115415/pathways?species=9606` | 31 pathways, identical `stId` set to the UniProt call | yes |
| 8 | `reactome.org/license` | 200; "c) Data : All data in the Reactome database and files derived from that data are licensed under the Creative Commons Public Domain Dedication (CC0) . User may copy, modify, and distribute these data, even for commercial purposes, without asking for permission. Attribution is encouraged but not required." (rendered text has a space before the colon); illustrations CC BY 4.0, software Apache License 2.0 | yes |
| 6 | (extra) `reactome.org/download-data` | 200; links are `https://download.reactome.org/version/<file>` (literal `version`) | yes |
| 7 | `curl -sIL current.geneontology.org/annotations/goa_human.gaf.gz` | 200, `application/gzip`, `15041996`, `last-modified: Thu, 28 May 2026 21:18:27 GMT`, `accept-ranges: bytes` | yes |
| 7 | `curl -sIL http://geneontology.org/gene-associations/goa_human.gaf.gz` | 301 → `https://geneontology.org/gene-associations/goa_human.gaf.gz` → 302 → `http://current.geneontology.org/annotations/goa_human.gaf.gz` → 301 → `https://current.geneontology.org/annotations/goa_human.gaf.gz` → 200 `15041996` | yes |
| 7 | `curl -sL -r 0-131071 -o goa_head.gz ...` + `zlib.decompressobj(16+zlib.MAX_WBITS)` | 206; decodes to 32 `!` header lines + 10,028 complete rows, all 17 columns; `!gaf-version: 2.2`, `!date-generated: 2026-05-21T08:10`, `!generated-by: UniProt` / `!date-generated: 2026-04-30 09:29` / `!go-version: .../go/releases/2026-04-27/extensions/go-plus.ofn`, `PANTHER version: v.19.0.`, `GO version: 2025-10-05.` all present | yes (header quote is trimmed, see R.2.10) |
| 7 | `current.geneontology.org/metadata/release-date.json` | `{date: 2026-08-05}` (unquoted key, as in original) | yes |
| 7 | `curl -sIL http://current.geneontology.org/annotations/gaf/HUMAN-uniprot.gaf.gz` | 301 → https → 200, `12109254`, `last-modified: Sat, 08 Aug 2026 22:15:52 GMT` | yes |
| 7 | (extra) `current.geneontology.org/annotations/gaf/` index | 194 `href`s = 1 parent-index link + 193 `.gaf.gz` files, of which **172** are `*-uniprot.gaf.gz` and 21 are `*-mod.gaf.gz`; `annotations/` index (947 bytes) links only `gaf/index.html`, not `goa_human.gaf.gz` | partly (see R.2.11) |
| 8 | `geneontology.org/docs/go-citation-policy/` | 200; "Gene Ontology Consortium data and data products are licensed under the Creative Commons Attribution 4.0 Unported License", "We ask that when using or citing GO data that the particular release is mentioned", Ashburner 2000 + NAR 2025 Dec 18 `gkaf1292` | yes |
| 7 | `.../go-annotation-file-gaf-format-2.2/` | 200; "comprised of 17 tab-delimited fields"; "Cardinality must be 0 for evidence codes IDA, TAS, NAS, or ND. Cardinality must be 1, >1 for IEA, IC, IGI, IPI, ISS & child terms of ISS"; relation `involved_in or NOT|involved_in`; col 17 `Gene Product Form ID` | yes |
| 7 | `.../guide-go-evidence-codes/` | 200; all 26 codes listed in §7 appear (`EXP IDA IPI IMP IGI IEP HTP HDA HMP HGI HEP IBA IBD IKR IRD ISS ISO ISA ISM IGC RCA TAS NAS IC ND IEA`); "Inferred from Electronic Annotation (IEA)" | yes |
| 1.4 | `obophenotype.github.io/.../annotations/phenotype_hpoa/` | 200; every quote in §1.4 found verbatim (`MIM:154700, ORPHA:558 or MONDO:0007947`; "We plan to obsolete this column"; "parsing the Clinical Features sections of the omim.txt file"; "you need to contact OMIM in order to reuse these annotations in other software products"; "traceable author statement"; aspect ids `HP:0000118/HP:0031797/HP:0000005/HP:0012823/HP:0032443`; "For use in semantic similarity analysis ..."; `HPO:skoehler[YYYY-MM-DD]`; `MALE or FEMALE`) | yes |
| 1.5 | `.../annotations/frequency/` | 200; `HP:0040285` Excluded 0% … `HP:0040280` Obligate 100%; "7/13 would indicate that 7 of the 13 patients"; "A percentage value such as 17%"; "We are deprecating the use of percentages" | yes |
| 2 | `.../annotations/genes_to_phenotype/`, `.../phenotype_to_genes/`, `.../genes_to_disease/` | 200 each; "we list for each gene the most specific HPO-classes (and not all the ancestors)"; "this file includes the ancestor classes of each associated phenotype"; "only contains annotations to HPO terms that are descendants of Phenotypic abnormality"; "This file provides the disease to gene mappings that are used to create the phenotype.hpoa file. ... MENDELIAN POLYGENIC UNKNOWN" | yes |
| 8 | `api.github.com/repos/obophenotype/human-phenotype-ontology/license` | `license.spdx_id NOASSERTION` (`key other`); `LICENSE.md` content "Please read the license explanation at the [HPO website](https://hpo.jax.org/app/license)" | yes |
| 3.2 | `raw.githubusercontent.com/geneontology/obographs/master/README.md` | 200; edges written with `"subj"` (lines 26, 115–127); "`C SubClassOf D` (aka `is_a` in obo-format)" / "`C SubClassOf P some D`(aka `relationship` in obo-format)" | yes |
| 8 | HPO license text (browser-rendered in the original) | verified without a browser: the text lives in the live Angular chunk `https://hpo.jax.org/chunk-BTDEGCNK.js` (44,480 bytes; chunk name taken from `https://hpo.jax.org/main-PGR2ZGBJ.js`); every sentence quoted in §8 is present verbatim ("freely available", "under three conditions", "acknowledged … and … cited … properly", "include the date(s) and/or version number(s)", "be altered in any way", "Users of the HPO should add the following statement to their online presence. This service/product uses the Human Phenotype Ontology (version information). Find out more at http://www.human-phenotype-ontology.org We request that the HPO logo be included as well.") | yes, but the URL returns 404 — see R.2.7 |

### R.2 Corrections

**R.2.1 — §1.6/§9: the OMIM/ORPHA layout statement is backwards.** Original: "OMIM rows appear at the start and at 30 MB; ORPHA rows dominate 8–24 MB". Sixteen 150 KB Range windows (18,294 complete rows) show the opposite: `OMIM:` rows (with a few `DECIPHER:` rows interleaved near 16 MB) fill bytes 0 → ~21 MB and `ORPHA:` rows fill ~22 MB → end of file. Within the sampled windows the split is clean (no window mixed OMIM and ORPHA).

```
$ for off in 0 4000000 8000000 12000000 16000000 20000000 21000000 22000000 23000000 24000000 26000000 28000000 30000000 32000000 34000000 35600000; do
    curl -sL -r $off-$((off+150000)) https://purl.obolibrary.org/obo/hp/hpoa/phenotype.hpoa -o hpoa_w$off.txt; done
offset         0: rows  135 prefixes {'OMIM': 135}
offset  4,000,000: rows 1141 prefixes {'OMIM': 1141}
offset  8,000,000: rows 1218 prefixes {'OMIM': 1218}
offset 12,000,000: rows 1103 prefixes {'OMIM': 1103}
offset 16,000,000: rows 1124 prefixes {'OMIM': 1119, 'DECIPHER': 5}
offset 20,000,000: rows 1180 prefixes {'OMIM': 1180}
offset 21,000,000: rows 1220 prefixes {'OMIM': 1220}
offset 22,000,000: rows 1217 prefixes {'ORPHA': 1217}
offset 23,000,000 … 35,600,000 (8 windows): 100 % 'ORPHA'
```

Implication: a streaming parser that only wants HPO-team (OMIM) rows can stop at the first `ORPHA:` row; one that wants ORPHA rows can start at ~22 MB. (Exact boundary not located; whole-file sortedness not proven.)

**R.2.2 — §1.4/§1.5/§11: percent-format frequencies DO occur** (original: "no percent values in sample" and listed as UNVERIFIED). 10 rows in the 18,294-row sample, all `OMIM:194190 Wolf-Hirschhorn syndrome`, evidence `TAS`, biocuration `HPO:iea[...]`:

```
OMIM:194190	Wolf-Hirschhorn syndrome		HP:0000119	http://www.ncbi.nlm.nih.gov/bookshelf/br.fcgi?book=gene&part=whs	TAS		25%			P	HPO:iea[2010-09-10]
OMIM:194190	Wolf-Hirschhorn syndrome		HP:0002353	http://www.ncbi.nlm.nih.gov/bookshelf/br.fcgi?book=gene&part=whs	TAS		90%			P	HPO:iea[2010-09-10]
```

The frequency parser must accept `^\d+%$` as a third format (docs call it deprecated but it is in the current file). `MONDO:` and `MIM:` database_ids: still 0 in 18,294 rows.

**R.2.3 — §1.4: `sex` is not "always empty".** 8 `MALE` + 1 `FEMALE` in the sample, e.g.

```
OMIM:619435	Ritscher-Schinzel syndrome 4		HP:0000054	PMID:33894126	PCS		2/3	MALE		P	HPO:probinson[2021-08-21]
OMIM:612567	Inflammatory bowel disease 25, early onset, autosomal recessive		HP:0000143	PMID:19890111	PCS	HP:0003593	1/1	FEMALE		P	HPO:probinson[2013-03-12];HPO:probinson
```

**R.2.4 — §1.4: `reference` is not always a CURIE and can hold several values.** Besides `PMID:`, `OMIM:`, `ORPHA:`, `DECIPHER:` the sample has 42 `http://...` URLs and 17 `ISBN-13:` values, and `;`-separated lists:

```
OMIM:194190	Wolf-Hirschhorn syndrome		HP:0000486	ISBN-13:978-3-7945-2657-4;OMIM:194190	TAS		HP:0040282			P	HPO:iea[2009-02-17]
OMIM:194190	Wolf-Hirschhorn syndrome		HP:0004322	http://www.ncbi.nlm.nih.gov/bookshelf/br.fcgi?book=gene&part=whs;ISBN-13:978-3-7945-2657-4	TAS		HP:0040282			P	HPO:iea[2010-09-10]
```

Split on `;` and do not `split(':')[0]` blindly (an `http:` "prefix" would result). `PMID` 5129 / `ORPHA` 11173 / `OMIM` 1928 / `http` 42 / `ISBN-13` 17 / `DECIPHER` 5 in the sample.

**R.2.5 — §1.4/§9: "IEA ≈ 5 % of rows" understates it in the OMIM part.** In the sample: overall IEA 1,095 / 18,294 = 6.0 %, but IEA occurs only on OMIM/DECIPHER rows (ORPHA rows: 2,339 TAS, 0 IEA; DECIPHER: 5/5 IEA), and within OMIM rows IEA is 681 / 4,620 = **14.7 %** (PCS 3,363, TAS 576). The whole-file share therefore depends on the OMIM:ORPHA row ratio; plan for ~15 % of OMIM-sourced edges being IEA. The §1.6 distribution (7,001 rows) cannot be reproduced because its window offsets were not recorded; the 18,294-row tallies are: evidence `PCS 5135 / TAS 12064 / IEA 1095`; aspect `P 17583 / I 365 / C 330 / H 14 / M 2`; qualifier `NOT` 64; frequency `HP term 11285 / n/m 4510 / empty 2489 / percent 10`; `onset` non-empty 105; `modifier` non-empty 42; `0/n` counts 102. All five "one real row per (prefix, evidence)" example rows in §1.6 were found verbatim.

**R.2.6 — §6: "human block … at ~byte 45,819,336" names a point *inside* the block, not its start.** Binary search over `download.reactome.org/97/Ensembl2Reactome.txt` (35 Range requests of 4–8 KB, script in `rerun_hpo/` output):

```
FIRST human row at byte 40,875,195 : ENSG00000000419	R-HSA-162699	https://reactome.org/PathwayBrowser/#/R-HSA-162699	Synthesis of dolichy...
previous row                       : ENSDART00000193975	R-DRE-8866907	...   (zebrafish)
first NON-human row at byte 49,383,299 : ENSGALG00000004231	R-GGA-449836	...   (chicken)
last human row                     : ENSG00000310517	R-HSA-8862803	...
human block = bytes 40875195-49383298 = 8,508,104 bytes (8.1 MiB); 3 probe windows inside are 100 % Homo sapiens
```

So the whole human block is one `curl -r 40875195-49383298` (8.1 MiB, under the 20 MB limit) — no need for "a handful" of requests. Offsets are specific to the v97 file (`last-modified: Wed, 24 Jun 2026 19:18:15 GMT`); recompute with the same binary search each release. Sort order is consistent with plain byte order (`ENSDART…` < `ENSG0…` < `ENSGALG…`).

**R.2.7 — §8: the HPO license URL returns HTTP 404.** `curl -sIL https://hpo.jax.org/license` → `404` (the server answers the Angular shell, 60,203 bytes, with a 404 status), and so does `https://hpo.jax.org/app/license`, the URL embedded in `hp.json` / `hp.obo` (`dc:terms/license`) and in the repo `LICENSE.md`. The page renders only client-side. Link checkers and `requests.get(...).raise_for_status()` will fail on it; cite it as the HPO's stated license URL but do not depend on its status code. `http://www.human-phenotype-ontology.org` → 200 (redirects to `https://hpo.jax.org/`). The license text itself was verified in the live bundle (R.1 last row).

**R.2.8 — §4.4: the `head -3` sample is not what `head -3` prints.** Actual:

```
$ grep -v '^#' mondo.sssom.tsv | head -3
subject_id	subject_label	predicate_id	object_id	object_label	mapping_justification
MONDO:0000001	disease	skos:exactMatch	DOID:4	disease	semapv:UnspecifiedMatching
MONDO:0000001	disease	skos:exactMatch	MEDGEN:4347		semapv:UnspecifiedMatching
```

`Orphanet:377788` is the 4th data row of `MONDO:0000001` (7 rows: DOID, MEDGEN, NCIT, Orphanet, SCTID, UMLS, mesh). Note the empty `object_label` on the MEDGEN row — the column is optional.

**R.2.9 — §2: the `head -5` / `head -4` samples are curated, not literal.** `genes_to_phenotype.txt` row 5 is `16	AARS1	HP:0002451	Limb dystonia	3/3	OMIM:616339`; the quoted `HP:0008619 … HP:0040283	ORPHA:33364` row is line 6. `genes_to_disease.txt` row 4 is `NCBIGene:28981	IFT81	MENDELIAN	OMIM:617895	ftp://...mim2gene_medgen`; the quoted `NCBIGene:6505	SLC1A1	POLYGENIC	OMIM:615232` row is line 6 (2 KB holds 19 MENDELIAN + 2 POLYGENIC rows). All quoted rows are real.

**R.2.10 — §7: the GAF header quote silently skips 7 lines and row 3 is not row 3.** The real header is 32 `!` lines; between `!date-generated: 2026-05-21T08:10` and `!Header from goa_human source association file:` there is a `!Header from source association file:` block (`!generated-by: GOC`, `!date-generated: 2026-05-20T11:45`). The third data row is `UniProtKB	A0A024RBG1	NUDT4B	enables	GO:0008486	GO_REF:0000024	ISS	UniProtKB:Q9NZJ9	F …`; the quoted `IGKV3-7 … PMID:20176268	NAS` row is data row 11 (0-based 10). All quoted lines exist verbatim.

**R.2.11 — §7: "194 links" of `*-uniprot.gaf.gz`** → the `gaf/` index has 194 `href`s in total: 1 link back to `annotations/index.html`, 172 `*-uniprot.gaf.gz`, 21 `*-mod.gaf.gz` (e.g. `ARATH-mod.gaf.gz`). The claim "`current.geneontology.org/annotations/` directory index lists a `gaf/` sub-directory" holds (it links `gaf/index.html`), but that index does not list `goa_human.gaf.gz`; that file is reachable only by direct URL (verified by HEAD).

### R.3 Additions observed while re-running (not errors)

- **R.3.1 `hp.json` obsolete terms are visible in the first 64 KB** (original §3.2/§11: "not in sample — UNVERIFIED"): node `HP_0000057` `"lbl" : "obsolete Clitoromegaly"` carries `"deprecated" : true` inside `meta` and a `basicPropertyValues` entry `{"pred": "http://purl.obolibrary.org/obo/IAO_0100001", "val": "HP:0008665"}` (term replaced by). Filter `meta.deprecated == true`; follow `IAO_0100001` for the replacement. Node-level xref prefixes in the head are `MEDDRA:`, `Fyler:` **and `NCIT:`** (`NCIT:C3754`).
- **R.3.2 `mondo.sssom.tsv` fingerprint for pinning:** sha256 `317ac4bf93be1deff4406b4d7d18bb730ead3c4f5c7c814f75d24be7f6ef302f`, 13,113,756 bytes, retrieved 2026-10-03 (identical size to the original fetch the same day).
- **R.3.3 JAX annotation API** top-level keys are `disease`, `categories`, `genes`, `medicalActions` (the original mentioned only the first two); still undocumented, terms UNVERIFIED.
- **R.3.4 `R-HSA-913531 Interferon Signaling` has 4 children in v97**, not the 2 listed: `R-HSA-1169410 Antimicrobial mechanism of IFN-stimulated genes`, `R-HSA-877300 Interferon gamma signaling`, `R-HSA-909733 Interferon alpha/beta signaling`, `R-HSA-9909505 Modulation of host responses by IFN-stimulated genes`. Parents confirmed: `R-HSA-451927/6783589/1266695 ← R-HSA-449147`, `R-HSA-1059683 ← R-HSA-6783589`, `R-HSA-913531 ← R-HSA-1280215`.
- **R.3.5** `mondo.json` graph `"version"` is `http://purl.obolibrary.org/obo/mondo/releases/2026-09-01/mondo.owl`; `mondo.obo` `data-version: releases/2026-09-01` (useful version strings to record).

### R.4 Reviewer's bottom line

The source stack is usable as the original concluded; nothing is broken or mis-sized, and the SSSOM-based OMIM/Orphanet→MONDO join is confirmed to be a clean function. Before coding `pipeline/sources/hpo.py`, fold in R.2.1–R.2.5 (hpoa file layout, `%` frequencies, `sex`, non-CURIE / multi-valued `reference`, ~15 % IEA among OMIM rows) and R.2.6 (single 8.1 MiB Range for the Reactome human block). Treat `https://hpo.jax.org/license` as a 404-that-renders when writing the README license table.
