# ClinVar via NCBI E-utilities — reconnaissance notes

Recon date: 2026-10-03 (all calls live, no API key). ClinVar build observed in esummary XML `<DbBuild>`: `Build260929-0200.1`; einfo `lastupdate` `2026/09/29 05:12`, einfo `count` 4,647,675 records.
Raw responses saved next to this file in `…/scratchpad/recon/` (`esearch_*.json`, `esummary_*.json|xml`, `efetch_vcv_*.xml`, `clinvar_tab_README.txt`, `variant_summary_head.tsv`, `einfo_clinvar.json`, `stat1_all_records.json`, `jak2_all_records.json`).

Everything below was observed in a live response or in an official NCBI page that was actually fetched. Anything not observed is marked **UNVERIFIED**.

---

## 0. TL;DR

- **Use esearch + esummary (JSON) with the history server, pages of ≤100 records, and do NOT use `single_gene[prop]`.** The full cluster is 6,924 Variation IDs across the 7 genes → ~70 esummary calls + 7 esearch calls ≈ 30 s at 3 req/s. Total payload a few tens of MB.
- esummary JSON gives everything we need except the full multi-transcript HGVS list and per-submission origin/citations: Variation ID, preferred name (HGVS c. + p. in `title`), single-letter protein change, germline classification + review status + last evaluated, condition names with MedGen/MONDO/OMIM/Orphanet/HPO/MeSH ids, molecular consequence (SO terms as text), dbSNP/ClinGen/OMIM-allelic/UniProt xrefs, gnomAD/TOPMed frequencies, SCV/RCV accessions.
- **Somatic classifications are exposed** as two separate blocks (`clinical_impact_classification`, `oncogenicity_classification`), but they are populated for only 3 of 817 JAK2 records. **JAK2 V617F (VCV000014662) carries only a *germline* block** ("Pathogenic/Likely pathogenic", conditions Thrombocythemia 3, Primary myelofibrosis, …), so germline-vs-somatic for the cluster must come from `somatic[orig]` searches / per-SCV `<Origin>` in VCV XML / `OriginSimple` in the bulk file, not from the classification type alone.
- Biggest gotchas: (1) esummary JSON hard-fails when a page's XML exceeds 10 MB — whole-chromosome CNV records carry hundreds of genes and blow a 317-record JAK2 page; (2) `single_gene[prop]` drops 807/817 JAK2 records because JAK2 SNVs are co-annotated with the overlapping gene INSL6; (3) `protein_change` is "R274W, R241W, …" (single-letter, one token per isoform, MANE isoform **not** first); the `p.Arg274Trp` form lives only in `title`; (4) **STAT1 N574I is not in ClinVar** (0 hits) although the README requires it; R274W, T419R and R321G are present.

---

## 1. esearch

### 1.1 Basic call (step 1 of the brief)

```sh
# NOTE: curl needs -g (globoff) or %5B/%5D, otherwise [gene] is eaten as a glob and curl returns nothing.
curl -sg 'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=clinvar&term=STAT1[gene]&retmax=5&retmode=json'
```
Response (verbatim):
```json
{"header":{"type":"esearch","version":"0.3"},"esearchresult":{"count":"863","retmax":"5","retstart":"0","idlist":["4959190","4959188","4959187","4959185","4959183"],"translationset":[],"translationstack":[{"term":"STAT1[gene]","field":"gene","count":"863","explode":"N"},"GROUP"],"querytranslation":"STAT1[gene]"}}
```
- `idlist` entries are ClinVar **Variation IDs** (uid == VariationID; `accession` in esummary is `VCV00` + zero-padded uid, e.g. 4959190 → `VCV004959190`).
- Order is descending uid (newest first): first id 4959190, last id 9043.

### 1.2 Counts per gene (live, retmax=0)

| term | count |
|---|---|
| `STAT1[gene]` | 863 |
| `STAT3[gene]` | 997 |
| `JAK1[gene]` | 922 |
| `JAK2[gene]` | 817 |
| `JAK3[gene]` | 1438 |
| `TYK2[gene]` | 1183 |
| `IL2RG[gene]` | 706 |
| `STAT1[gene] OR STAT3[gene] OR JAK1[gene] OR JAK2[gene] OR JAK3[gene] OR TYK2[gene] OR IL2RG[gene]` | **6924** (sum of singles = 6926 → 2 records touch two cluster genes) |

Cross-check from the FTP `gene_specific_summary.txt` (dated September 28, 2026; counts are *alleles*, not Variation IDs, hence slightly lower):
```
#Symbol GeneID Total_submissions Total_alleles Submissions_reporting_this_gene Alleles_reported_Pathogenic_Likely_pathogenic Gene_MIM_number Number_uncertain Number_with_conflicts
IL2RG 3561 961 697 822 170 308380 153 16
JAK1 3716 1135 903 1105 7 147795 401 4
JAK2 3717 1040 792 851 12 147796 353 17
JAK3 3718 1938 1422 1914 145 600173 396 42
STAT1 6772 1189 849 1159 105 600555 281 22
STAT3 6774 1355 970 1345 108 102582 341 40
TYK2 7297 1589 1152 1570 50 176941 506 43
```

### 1.3 Paging and retmax ceiling

- `retstart`/`retmax` work as expected: `…&term=STAT1[gene]&retstart=860&retmax=5` → `"retmax":"3","retstart":"860","idlist":["9045","9044","9043"]`.
- `retmax=10000` for STAT1 returned all 863 ids in one call; `retmax=100000` for TYK2 returned all 1183.
- Ceiling probe on a bigger gene: `term=BRCA1[gene]&retmax=100000` → `count 16094, retmax 16094, 16094 ids returned`, no `errorlist`/`warninglist`. So **no 10,000-id cap was observed** for ClinVar esearch. (The E-utilities book page NBK25499 that documents a retmax maximum is behind a reCAPTCHA challenge page and could not be fetched → that documented cap is **UNVERIFIED**; for our genes it is irrelevant since every gene is < 10,000.)

### 1.4 History server (recommended for esummary paging)

```sh
curl -sg 'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=clinvar&term=STAT1[gene]&usehistory=y&retmax=0&retmode=json'
# → {"esearchresult":{"count":"863","retmax":"0","retstart":"0","querykey":"1","webenv":"MCID_6ac156ed2b400ad4630bbacb","idlist":[], ...}}
```
Then page esummary with `query_key=1&WebEnv=MCID_…&retstart=N&retmax=M` (see §2.3). JSON keys are lower-case `querykey` / `webenv`; the parameters you send back are `query_key` / `WebEnv`.

### 1.5 Searchable fields (einfo, live)

```sh
curl -s 'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/einfo.fcgi?db=clinvar&retmode=json'
```
Field tags observed in `einforesult.dbinfo[0].fieldlist[].name` (with `fullname`): `ALL` All Fields · `UID` · `FILT` Filter · `TITL` Name of the ClinVar record · `WORD` Text Word · `ORGN` · `MDAT` Modification Date · `CHR` Chromosome · **`GENE`** Gene Name · **`MIM`** MIM number from OMIM · **`DIS`** Disease/Phenotype · `ACCN` ClinVar accession · `VRID` External allele ID · **`TRID`** Trait identifier (CUI, HPO) · **`PROP`** Properties · `CDAT` · `PMID` PubMed ID · **`GID`** Gene ID · `TID` · `CPOS` · `GFN` · `SBM` Submitter · **`VRNM`** Variant name · **`VRTP`** Type of variation · **`MCNS`** Molecular consequence · **`RVST`** Review status · **`ALID`** AlleleID · **`ORIG`** Origin · `SID` · `CYT` · `GTRT` · `STNM` · `CMPL` Complexity · `C37` · `VLEN` · `HGNC` · **`VID`** Variation ID · `IMOD` Last interpreted · `CLINSIG_LAST_CHANGED` · `VACC` Variant Accession · `RPLD` · `COMB` · `CSPDI` Canonical SPDI · `COMMONNAME` · `COLLMETHOD` · `FCNS` Functional consequence.
Link names observed in `linklist`: `clinvar_pubmed`, `clinvar_pmc`, `clinvar_medgen`, `clinvar_omim`, `clinvar_gene`, `clinvar_snp`, `clinvar_dbvar`, `clinvar_gtr`, `clinvar_orgtrack`, `clinvar_gene_specific`.

Live field tests:
- `STAT1[gene] AND "chronic mucocutaneous candidiasis"[dis]` → count 2, ids `1264351`, `30084`.
- `JAK2[gene] AND somatic[orig]` → count 9, first ids `4688042, 4530519, 3390877, 2958894, 869413`.
- `STAT1[gene] AND (N574I OR Asn574Ile)` → **count 0** (translated to `N574I[All Fields] OR Asn574Ile[All Fields]`). `N574I` alone → 8 hits in other genes. Offline scan of all 863 STAT1 `title`/`protein_change` strings for "574" finds only `p.Gln594His` (isoform token Q574H). **→ STAT1 N574I is absent from ClinVar; log as a coverage gap, do not fake a node.**
- `STAT1[gene] AND single_gene[prop]` → 821 (equals the number of STAT1 records with exactly one entry in `genes[]`).
- **`JAK2[gene] AND single_gene[prop]` → 10 (!)**. Reason (from the 817 fetched records): 627 JAK2 records list 2 genes, and 625 of those are `('INSL6','JAK2')` — JAK2 SNVs including V617F (uid 14662, `genes` = `[{"symbol":"INSL6","geneid":"11172","strand":"-"},{"symbol":"JAK2","geneid":"3717","strand":"+"}]`) are co-annotated with the overlapping gene INSL6. **Never use `single_gene[prop]` for this cluster.** Combined 7-gene query with `single_gene[prop]` drops to 5654.
- The ClinVar docs' own example `FGFR3[gene]+AND+single_gene[prop]` is where this property comes from (https://www.ncbi.nlm.nih.gov/clinvar/docs/maintenance_use/, fetched).

---

## 2. esummary (JSON)

### 2.1 Call and full record (step 2 of the brief)

```sh
curl -s 'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?db=clinvar&id=4959190,4959188,4959187,4959185,4959183&retmode=json'
```
Top level: `{"header":{"type":"esummary","version":"0.3"},"result":{"uids":[...],"<uid>":{...},...}}`. Full record for the first id returned by the STAT1 esearch (verbatim, nothing edited):

```json
{
 "uid": "4959190",
 "obj_type": "single nucleotide variant",
 "accession": "VCV004959190",
 "accession_version": "VCV004959190.1",
 "title": "NM_007315.4(STAT1):c.-1-4A>G",
 "variation_set": [
  {
   "measure_id": "5070064",
   "variation_xrefs": [],
   "variation_name": "NM_007315.4(STAT1):c.-1-4A>G",
   "cdna_change": "NM_007315.4(STAT1):c.-1-4A>G",
   "aliases": [],
   "variation_loc": [
    {"status": "current", "assembly_name": "GRCh38", "chr": "2", "band": "2q32.2", "start": "191010008", "stop": "191010008",
     "inner_start": "", "inner_stop": "", "outer_start": "", "outer_stop": "", "display_start": "191010008", "display_stop": "191010008",
     "assembly_acc_ver": "GCF_000001405.38", "annotation_release": "", "alt": "", "ref": ""},
    {"status": "previous", "assembly_name": "GRCh37", "chr": "2", "band": "2q32.2", "start": "191874734", "stop": "191874734",
     "inner_start": "", "inner_stop": "", "outer_start": "", "outer_stop": "", "display_start": "191874734", "display_stop": "191874734",
     "assembly_acc_ver": "GCF_000001405.25", "annotation_release": "", "alt": "", "ref": ""}
   ],
   "allele_freq_set": [],
   "variant_type": "single nucleotide variant",
   "canonical_spdi": "NC_000002.12:191010007:T:C",
   "common_name": ""
  }
 ],
 "supporting_submissions": {"scv": ["SCV007922768"], "rcv": ["RCV006944197"]},
 "germline_classification": {
  "description": "Likely benign",
  "last_evaluated": "2020/08/13 00:00",
  "review_status": "criteria provided, single submitter",
  "fda_recognized_database": "",
  "trait_set": [{"trait_xrefs": [{"db_source": "MedGen", "db_id": "CN169374"}], "trait_name": "not specified"}]
 },
 "clinical_impact_classification": {"description": "", "last_evaluated": "1/01/01 00:00", "review_status": "", "fda_recognized_database": "", "trait_set": []},
 "oncogenicity_classification": {"description": "", "last_evaluated": "1/01/01 00:00", "review_status": "", "fda_recognized_database": "", "trait_set": []},
 "record_status": "",
 "gene_sort": "STAT1",
 "chr_sort": "02",
 "location_sort": "00000000000191010008",
 "variation_set_name": "",
 "variation_set_id": "",
 "genes": [{"symbol": "STAT1", "geneid": "6772", "strand": "-", "source": "submitted"}],
 "molecular_consequence_list": ["intron variant"],
 "protein_change": "",
 "fda_recognized_database": ""
}
```
Structure is stable: across all 863 STAT1 records there is exactly 1 distinct top-level key set, 1 distinct `germline_classification` key set, 1 distinct `variation_set[0]` key set, and `variation_set` always has length 1.

A second real example — first record in esearch order that has MONDO+OMIM xrefs and a protein change (uid 4884326):
```json
{"uid":"4884326","accession_version":"VCV004884326.1","title":"NM_007315.4(STAT1):c.265A>T (p.Asn89Tyr)","obj_type":"single nucleotide variant",
 "protein_change":"N101Y, N89Y, N91Y","molecular_consequence_list":["missense variant"],
 "genes":[{"symbol":"STAT1","geneid":"6772","strand":"-","source":"submitted"}],
 "germline_classification":{"description":"Likely pathogenic","last_evaluated":"2026/08/11 00:00","review_status":"criteria provided, single submitter","fda_recognized_database":"",
   "trait_set":[{"trait_xrefs":[{"db_source":"Orphanet","db_id":"391487"},{"db_source":"MedGen","db_id":"C3279990"},{"db_source":"MONDO","db_id":"MONDO:0013599"},{"db_source":"OMIM","db_id":"614162"}],
                 "trait_name":"Autoimmune enteropathy and endocrinopathy - susceptibility to chronic infections syndrome"}]},
 "supporting_submissions":{"scv":["SCV007664810"],"rcv":["RCV006748357"]},
 "variation_set[0]":{"variation_name":"NM_007315.4(STAT1):c.265A>T (p.Asn89Tyr)","cdna_change":"c.265A>T","canonical_spdi":"NC_000002.12:191008970:T:A","variation_xrefs":[],"aliases":[]}}
```

### 2.2 Field map for the pipeline (all paths observed)

| Need | JSON path | Notes / observed values |
|---|---|---|
| ClinVar variation id | `uid` (also `result.uids[]`) | string; `accession` = `VCV` + 9-digit zero-pad; `accession_version` e.g. `VCV004959190.1` |
| Preferred name / HGVS | `title` (== `variation_set[0].variation_name` in 863/863) | `NM_007315.4(STAT1):c.820C>T (p.Arg274Trp)` — MANE transcript, c. and **p. three-letter** in parentheses |
| cDNA change | `variation_set[0].cdna_change` | **Inconsistent**: 495 start with `c.` (e.g. `c.974T>C`), 338 start with `NM_…(STAT1):c.…`, CNVs carry `GRCh37/hg19 2q32.2-33.1(chr2:…)` strings. Parse from `title` instead. |
| Protein change | `protein_change` | Single-letter, comma-separated, one token per isoform, **not** ordered by MANE: `R274W, R241W, R244W, R276W, R286W`; stop = `*` (`Q444*`); synonymous → empty string with alias `p.Asp575=` in `variation_set[0].aliases`. Never contains `p.`. Non-empty in 321/863 STAT1 records. |
| Germline classification | `germline_classification.description` | STAT1 values: Likely benign 314, Uncertain significance 281, Pathogenic 82, Likely pathogenic 47, `""` 45, Benign 40, Conflicting classifications of pathogenicity 27, Benign/Likely benign 23, Pathogenic/Likely pathogenic 3, not provided 1 |
| Review status | `germline_classification.review_status` | criteria provided, single submitter 635 · criteria provided, multiple submitters, no conflicts 122 · `""` 45 · no assertion criteria provided 33 · criteria provided, conflicting classifications 27 · no classification provided 1. Full star mapping in §6. |
| Last evaluated | `germline_classification.last_evaluated` | `2026/08/11 00:00` (`YYYY/MM/DD HH:MM`); sentinel for empty is `1/01/01 00:00` |
| Condition names | `germline_classification.trait_set[].trait_name` | e.g. `Immunodeficiency 31B`, `Mendelian susceptibility to mycobacterial diseases due to partial STAT1 deficiency`, `not provided`, `not specified`, `STAT1-related disorder` (no xrefs) |
| Condition ids | `germline_classification.trait_set[].trait_xrefs[].{db_source,db_id}` | `db_source` ∈ {`MedGen`, `MONDO`, `Orphanet`, `OMIM`, `MeSH`, `Human Phenotype Ontology`} (STAT1 counts 2206/1954/1953/1953/33/4). `db_id` formats: MONDO `MONDO:0013427` (prefix included), OMIM `613796` or phenotypic series `PS114580`, Orphanet `391311`, MedGen `C3151088`/`CN169374`, HPO `HP:0002728`, MeSH `D030342` |
| Molecular consequence | `molecular_consequence_list[]` | SO term text, no SO id: intron variant 446, missense variant 291, synonymous variant 183, 3 prime UTR variant 38, frameshift variant 18, splice acceptor variant 13, nonsense 10, splice donor variant 10, 5 prime UTR variant 10, inframe_deletion 7, inframe_insertion 1, stop lost 1. Empty for 39 records (29 are copy number loss/gain). 203 records have >1 term (e.g. R274W = `["missense variant","intron variant"]` across transcripts). |
| Variant type | `obj_type` (== `variation_set[0].variant_type`) | single nucleotide variant 773, Deletion 35, copy number loss 16, Duplication 14, copy number gain 13, Microsatellite 6, Insertion 4, Indel 2 |
| Genes | `genes[].{symbol,geneid,strand,source}` | `source` always `submitted`. 821 STAT1 records have 1 gene; CNVs list up to 1216 genes |
| Location | `variation_set[0].variation_loc[]` (`assembly_name` GRCh38 `status:current` / GRCh37 `status:previous`, `chr`, `start`, `stop`, `band`) and `variation_set[0].canonical_spdi` | `ref`/`alt` were empty in observed SNV records; use `canonical_spdi` |
| Variant xrefs | `variation_set[0].variation_xrefs[].{db_source,db_id}` | `dbSNP` (rs number without "rs", e.g. `387906758`), `ClinGen` (`CA128924`), `OMIM` allelic variant (`600555.0008`), `UniProtKB` (`P42224#VAR_065943`), `dbVar` |
| Allele frequency | `variation_set[0].allele_freq_set[].{source,value,minor_allele}` | e.g. `The Genome Aggregation Database (gnomAD), exomes` / `Trans-Omics for Precision Medicine (TOPMed)` |
| Provenance | `supporting_submissions.scv[]`, `supporting_submissions.rcv[]` | SCV/RCV accessions |
| Somatic: clinical impact | `clinical_impact_classification.{description,last_evaluated,review_status,fda_recognized_database,trait_set[]}` | see §3 |
| Somatic: oncogenicity | `oncogenicity_classification.{…same keys…}` | see §3 |

Note: the ClinVar docs page (maintenance_use) refers to esummary fields `assembly_set`, `trait_refs`, `variant_type`, `gene/source`. In the live JSON the names are `variation_loc`, `trait_xrefs`, `variant_type`, `genes[].source` — the docs are stale; trust the response.

### 2.3 Paging esummary, limits (all observed live)

- **JSON cap on UIDs per call = 500.** `retmax=1000` via history → verbatim: `{"error":"Too many UIDs in request. Maximum number of UIDs is 500 for JSON format output."}` (returned inside the top-level object; `result` absent).
- **JSON cap on response size = 10 MB of intermediate XML.** `JAK2[gene]` history, `retstart=500&retmax=500` (317 records) → verbatim: `{ "header":{"type":"eutils", "version" : "0.3"}, "eutilsresult" : {"ERROR": "Input XML size is 10845566 bytes, and cannot be transformed to JSON. the max size is 10MB"} }`. Cause: whole-chromosome CNV records (e.g. uid 155409 `GRCh38/hg38 9p24.3-q34.3(chr9:203862-138125937)x3`) are ~500 KB each because `genes[]` lists hundreds of genes. Same slice without `retmode=json` (default XML) succeeded: 10,845,566 bytes, 317 `<DocumentSummary>`, DTD `esummary clinvar 20241209`. The XML uses the same element names as the JSON keys (`<germline_classification><description>…`).
- Pages of 100 all succeeded for JAK2: `retstart=500/600/700/800&retmax=100` → 100/100/100/17 records, 0.46–4.6 MB each, up to 7.5 s per call.
- History paging works: STAT1 `retstart=0&retmax=500` → 500 records (1.43 MB); `retstart=500&retmax=500` → 363 records (2.06 MB).
- POST with explicit ids works: `curl -X POST …/esummary.fcgi --data-urlencode db=clinvar --data-urlencode retmode=json --data-urlencode "id=<500 comma-separated ids>"` → 500 records.

Commands that worked:
```sh
WEBENV=…; QK=1
curl -s "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?db=clinvar&query_key=${QK}&WebEnv=${WEBENV}&retstart=0&retmax=100&retmode=json"
# fallback for a page that fails with the 10MB error: drop retmode=json and parse XML, or halve retmax
curl -s "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?db=clinvar&query_key=${QK}&WebEnv=${WEBENV}&retstart=500&retmax=500"
```

### 2.4 Protein-change format (step 3 of the brief) — real STAT1 examples

From the first five ids the STAT1 esearch returned (no hand-picking):
```
4959185 | NM_007315.4(STAT1):c.974T>C (p.Met325Thr)  | protein_change = "M292T, M337T, M272T, M295T, M325T, M327T" | Uncertain significance
4959183 | NM_007315.4(STAT1):c.986C>T (p.Pro329Leu)  | protein_change = "P299L, P329L, P296L, P276L, P331L, P341L" | Likely pathogenic
4959187 | NM_007315.4(STAT1):c.432T>C (p.Ser144=)     | protein_change = ""  (aliases carry the p.= form for synonymous)
```
So: `protein_change` = **single-letter "R274W" style**, comma-separated over isoforms, MANE isoform not necessarily first (M325T is 5th of 6). The **"p.Arg274Trp" style is only in `title`** (regex `\(p\.([A-Za-z0-9_=*]+)\)` on `title`). To get both forms per transcript with the accession, use VCV XML `<ProteinExpression change="p.Pro329Leu" singleLetterProteinChange="P329L">` under the `<HGVS Type="coding">` whose `<NucleotideExpression … MANESelect="true">` (§4).

README-required STAT1 variants (checked by exact token match in `protein_change` over all 863 records):

| Variant | uid | title | germline description | review status |
|---|---|---|---|---|
| R274W | **30083** | `NM_007315.4(STAT1):c.820C>T (p.Arg274Trp)` | Pathogenic | criteria provided, multiple submitters, no conflicts (10 SCVs, 4 RCVs; xrefs ClinGen CA128924, UniProtKB P42224#VAR_065943, OMIM 600555.0008, dbSNP 387906758) |
| T419R | **650110** | `NM_007315.4(STAT1):c.1256C>G (p.Thr419Arg)` | Uncertain significance | (see saved JSON) |
| R321 position | **2925337** | `NM_007315.4(STAT1):c.961A>G (p.Arg321Gly)` | Pathogenic | (only R321 variant found; the cluster doc gives no substituting residue) |
| N574I | — | **not in ClinVar** (esearch 0 hits; offline scan 0) | — | coverage gap |

Conditions attached to R274W (uid 30083) in `germline_classification.trait_set[]`: `STAT1-related disorder` (no xrefs), `Immunodeficiency 31B` (MONDO:0013427, OMIM 613796, Orphanet 391311, MedGen C3151088), `Mendelian susceptibility to mycobacterial diseases due to partial STAT1 deficiency` (MONDO:0013956, OMIM 614892, Orphanet 319595, MedGen C4013950), `Autoimmune enteropathy and endocrinopathy - susceptibility to chronic infections syndrome` (MONDO:0013599, OMIM 614162, Orphanet 391487, MedGen C3279990; appears twice), `not provided` (MedGen C3661900). **Gotcha for the GoF/LoF split:** ClinVar attaches both the GoF condition (Immunodeficiency 31B / OMIM 613796) *and* the LoF condition (MSMD partial STAT1 deficiency / OMIM 614892) to R274W because submitters test gene panels; condition ids alone do not give mechanism direction.

Distinct STAT1 condition names with xrefs (top): `Mendelian susceptibility to mycobacterial diseases due to partial STAT1 deficiency` ×697 [MONDO:0013956, OMIM 614892, Orphanet 319595, MedGen C4013950]; `Autoimmune enteropathy and endocrinopathy - susceptibility to chronic infections syndrome` ×637 [MONDO:0013599, OMIM 614162, Orphanet 391487, MedGen C3279990]; `Immunodeficiency 31B` ×616 [MONDO:0013427, OMIM 613796, Orphanet 391311, MedGen C3151088]; `not provided` ×133 [MedGen C3661900]; `not specified` ×87 [MedGen CN169374]; `Inborn genetic diseases` ×25 [MeSH D030342, MedGen C0950123]; `Chronic mucocutaneous candidiasis` ×2 [MONDO:0015279, OMIM PS114580, Orphanet 1334, MedGen C0006845, HP:0002728, HP:0005392]; `Chromosome 2q32-q33 deletion syndrome` ×1 [MONDO:0012864, OMIM 612313]. 459 records have 3 traits, 210 have 1, 45 have 0 (no classification).

---

## 3. Germline vs somatic (step 5 of the brief)

- esummary exposes three parallel classification blocks with identical shape: `germline_classification`, `clinical_impact_classification` (= ClinVar "somatic clinical impact"), `oncogenicity_classification`. Empty blocks have `description:""`, `review_status:""`, `last_evaluated:"1/01/01 00:00"`, `trait_set:[]`.
- STAT1: 0/863 records have a non-empty somatic block. JAK2: **3/817** do (JSON page 0):
  - uid 4530519 `NM_004972.4(JAK2):c.2049A>T (p.Arg683Ser)` — `clinical_impact_classification.description` = `Tier I - Strong`, `review_status` = `criteria provided, single submitter`, `last_evaluated` `2024/04/24 00:00`, trait `Precursor B-cell acute lymphoblastic leukemia` [MONDO:0020511, Orphanet 99860, MedGen C0349636, HP:0004812]; germline block empty.
  - uid 3390877 `(p.Arg795Ser)` — `oncogenicity_classification.description` = `Uncertain significance`, `review_status` = `no assertion criteria provided`, trait `Adrenal cortex carcinoma` [MONDO:0006639, …].
  - uid 2958894 `(p.Arg867Trp)` — germline `Uncertain significance` **and** `oncogenicity_classification.description` = `Uncertain significance` (`criteria provided, single submitter`), trait `Neoplasm` [MONDO:0005070, MedGen C0027651].
- **JAK2 V617F = uid 14662**, `NM_004972.4(JAK2):c.1849G>T (p.Val617Phe)`, `protein_change` `V617F, V212F, V468F`, `molecular_consequence_list` `["missense variant","non-coding transcript variant"]`: `germline_classification.description` = `Pathogenic/Likely pathogenic`, `review_status` = `criteria provided, multiple submitters, no conflicts`, `last_evaluated` `2026/07/07 00:00`; traits include `JAK2-related disorder` (no xrefs), `Thrombocythemia 3` [MONDO:0013794, OMIM 614521, MedGen C3281125], `Primary myelofibrosis` [MONDO:0009692, OMIM 254450, Orphanet 824, MedGen C0001815, MeSH D055728], `Primary familial polycythemia due to EPO receptor mutation` [MONDO:0007572, OMIM 133100, Orphanet 90042], … (trait_set 8.4 KB). Both somatic blocks are **empty**. I.e. the canonical somatic MPN driver is filed under ClinVar's *germline* classification type; our somatic flag cannot be derived from which block is populated.
- Where origin actually lives: (a) esearch field `[ORIG]` — `JAK2[gene] AND somatic[orig]` → 9 records (ids above incl. 4688042, 4530519, 3390877, 2958894, 869413); (b) VCV XML per submission: `<ClinicalAssertion>…<ObservedIn><Sample><Origin>germline</Origin>` (§4); (c) bulk `variant_summary.txt` columns `Origin` (e.g. `germline;unknown`) and `OriginSimple` (`germline`), plus `SomaticClinicalImpact`, `Oncogenicity` and their `*LastEvaluated`/`ReviewStatus*` columns (§5). esummary JSON has **no origin field**.
- Review-status vocabulary for somatic blocks is documented at https://www.ncbi.nlm.nih.gov/clinvar/docs/review_status/ (fetched; the somatic section exists but its table was not captured in my text dump → somatic star mapping **UNVERIFIED** here).

---

## 4. efetch (VCV XML) — step 4 of the brief

Documented on https://www.ncbi.nlm.nih.gov/clinvar/docs/maintenance_use/ (fetched): "Use efetch to retrieve the XML for one or more variation records (XML that is consistent with the VCV XML) based on their id(s). Note rettype=vcv&is_variationid", example `efetch.fcgi?db=clinvar&rettype=vcv&is_variationid&id=14206,41472&from_esearch=true`; by accession `rettype=vcv&id=VCV000014206[.1]`; RCV via `rettype=clinvarset&id=RCV000000606`. The page also lists `elink.fcgi?dbfrom=clinvar&db=pubmed&id=9` and `&db=medgen` (documented; **not executed** by me).

```sh
curl -s 'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=clinvar&rettype=vcv&is_variationid&id=4959183,4959185&from_esearch=true'
# → 37,500 bytes, 2 <VariationArchive>; single id 4959190 → 14,200 bytes
```
Root: `<ClinVarResult-Set><VariationArchive VariationID="4959190" VariationName="…" VariationType="single nucleotide variant" Accession="VCV004959190" Version="1" RecordType="classified" NumberOfSubmissions="1" NumberOfSubmitters="1" DateLastUpdated="2026-09-27" DateCreated="2026-09-22" MostRecentSubmission="2026-09-22">`.
Elements observed (counts in one record): `ClassifiedRecord`, `SimpleAllele AlleleID VariationID`, `GeneList/Gene Symbol FullName GeneID HGNC_ID="HGNC:11362" Source="calculated" RelationshipType="within single gene"` with child `<OMIM>600555</OMIM>`, `Haploinsufficiency`, `Triplosensitivity`; `Name`, `CanonicalSPDI`, `VariantType`, `Location/SequenceLocation Assembly Chr Accession start stop positionVCF referenceAlleleVCF alternateAlleleVCF`; **`HGVSlist/HGVS Type="coding"` → `NucleotideExpression sequenceAccessionVersion="NM_007315.4" change="c.986C>T" MANESelect="true"` + `ProteinExpression sequenceAccessionVersion="NP_009330.1" change="p.Pro329Leu" singleLetterProteinChange="P329L"` + `MolecularConsequence ID="SO:0001583" Type="missense variant" DB="SO"`** (19 HGVS entries per SNV incl. LRG, NG_, NC_ GRCh37/38); `Classifications/GermlineClassification DateLastEvaluated NumberOfSubmissions` → `ReviewStatus`, `Description`, `Explanation`, `ConditionList/TraitSet ID Type ContributesToAggregateClassification/Trait ID/Name/ElementValue Type="Preferred|Alternate"`, `XRef ID="CN169374" DB="MedGen"`; `RCVList/RCVAccession Title Accession Version/ClassifiedConditionList/ClassifiedCondition`, `RCVClassifications/GermlineClassification`; `ClinicalAssertionList/ClinicalAssertion ID SubmissionDate` → `ClinVarAccession Accession="SCV007922768" SubmitterName="Blueprint Genetics" OrgID OrganizationCategory="laboratory"`, `Classification/ReviewStatus + GermlineClassification`, `Assertion`, `AttributeSet/Attribute Type="AssertionMethod"` + `Citation/ID Source="PubMed"`, **`ObservedInList/ObservedIn/Sample/Origin`** (`germline`), `AffectedStatus`, `Method/MethodType` (`clinical testing`), `TraitSet/Trait/Name`. Only `GermlineClassification` appeared in these two records (no `SomaticClinicalImpact`/`OncogenicityClassification` elements; those element names are **UNVERIFIED** in XML).

Use efetch for: the full per-transcript HGVS/protein list with MANE flag and SO ids, per-SCV origin (germline/somatic), submitter names, and PubMed citations — i.e. for the handful of "hero" variants (R274W, T419R, R321G, V617F) rather than all 6.9k.

---

## 5. Bulk alternative (FTP over HTTPS)

Directory listing https://ftp.ncbi.nlm.nih.gov/pub/clinvar/tab_delimited/ (fetched 2026-10-03):

| File | Bytes | Last modified | Notes |
|---|---|---|---|
| `variant_summary.txt.gz` | **450,727,547** (HEAD `Content-Length`; `Accept-Ranges: bytes`, `Content-Type: application/x-gzip`) | 2026-09-29 02:10 UTC | one row per allele **per assembly** (GRCh37 + GRCh38) |
| `README` | 47,147 | 2026-01-13 | column docs (saved as `clinvar_tab_README.txt`) |
| `gene_specific_summary.txt` | 3,495,953 | 2026-09-28 | per-gene counts (fetched, §1.2) |
| `variation_allele.txt.gz` | 24,612,358 | 2026-09-28 | AlleleID↔VariationID map |
| `hgvs4variation.txt.gz` | 528,544,462 | 2026-09-28 | all HGVS expressions |
| `var_citations.txt` | 252,043,033 | 2026-09-28 | variant→PubMed |
| `cross_references.txt` | 111,555,929 | 2026-09-28 | |
| `submission_summary.txt.gz` | 394,172,714 | 2026-09-28 | per-SCV, has `SomaticClinicalImpact`, `Oncogenicity` columns (README lines 441–442) |
| `summary_of_conflicting_interpretations.txt` | 1,978,891,809 | 2026-09-28 | |
| `allele_gene.txt.gz` | 92,907,194 | 2026-09-28 | |

Other formats: `/pub/clinvar/vcf_GRCh38/clinvar.vcf.gz` 198,125,411 bytes (+ `.tbi` 597 K; weekly `clinvar_20260928.vcf.gz`); `/pub/clinvar/xml/ClinVarVCVRelease_00-latest.xml.gz` 5,984,688,352 bytes; `/pub/clinvar/disease_names` 5.4 M; `gene_condition_source_id` 1.2 M; `README_VCF.txt`.
**`variation_summary.txt.gz` does not exist** (HEAD → `HTTP/1.1 404 Not Found`); the file is `variant_summary.txt.gz`.

Header verified by a Range request (`Range: bytes=0-524287` → `206`, `Content-Range: bytes 0-524287/450727547`, partial gunzip gave 6,806 lines), 43 columns in this order:
`#AlleleID, Type, Name, GeneID, GeneSymbol, HGNC_ID, ClinicalSignificance, ClinSigSimple, LastEvaluated, RS# (dbSNP), nsv/esv (dbVar), RCVaccession, PhenotypeIDS, PhenotypeList, Origin, OriginSimple, Assembly, ChromosomeAccession, Chromosome, Start, Stop, ReferenceAllele, AlternateAllele, Cytogenetic, ReviewStatus, NumberSubmitters, Guidelines, TestedInGTR, OtherIDs, SubmitterCategories, VariationID, PositionVCF, ReferenceAlleleVCF, AlternateAlleleVCF, SomaticClinicalImpact, SomaticClinicalImpactLastEvaluated, ReviewStatusClinicalImpact, Oncogenicity, OncogenicityLastEvaluated, ReviewStatusOncogenicity, SCVsForAggregateGermlineClassification, SCVsForAggregateSomaticClinicalImpact, SCVsForAggregateOncogenicityClassification`
(The README documents the first 40; the last three `SCVsForAggregate*` columns are present in the file but not described in the README.)
First data row (GRCh37 line of VariationID 2): `Name = NM_014855.3(AP5Z1):c.80_83delinsTGCTGTAAACTGTAACTGTAAA (p.Arg27_Ile28delinsLeuLeuTer)`, `ClinicalSignificance = Pathogenic/Likely pathogenic`, `PhenotypeIDS = MONDO:MONDO:0013342,MedGen:C3150901,OMIM:613647,Orphanet:306511||MedGen:C3661900`, `PhenotypeList = Hereditary spastic paraplegia 48|Macular dystrophy with or without extraocular features|not provided`, `Origin = germline;unknown`, `OriginSimple = germline`, `ReviewStatus = criteria provided, multiple submitters, no conflicts`, `OtherIDs = ClinGen:CA215070,OMIM:613653.0001`, `SomaticClinicalImpact = -`.
README semantics worth knowing: `PhenotypeIDS` — RCVs separated by `|`, conditions within an RCV by `;`, ids of one condition by `,`; `ClinSigSimple` 0/1/-1; `GeneSymbol` is a comma-separated list and `GeneID` is `-1` when multiple genes. There is **no protein-change column** (parse `Name`) and **no molecular-consequence column** in `variant_summary.txt`.
ClinVar's own guidance (ftp_primer, fetched): "variant_summary.txt represents all variants in ClinVar that have a location on the genome. However, the file includes only selected metadata … To find all data for a variant, we recommend using the VCV XML instead."

---

## 6. Rate limits, api_key, review-status vocabulary

- Official NCBI statement (NCBI Insights, https://ncbiinsights.ncbi.nlm.nih.gov/2017/11/02/new-api-keys-for-the-e-utilities/, fetched): without a key "any computer (IP address) that submits more than three E-utility requests per second will receive an error message … applies to any combination of requests to EInfo, ESearch, ESummary, EFetch, ELink, EPost, ESpell, and EGquery"; "Use the key by adding the parameter `api_key` to any E-utilities request"; "your key will increase the limit to 10 requests/second for all activity from that key"; one key per NCBI account. Matches README §5 (`NCBI_API_KEY`). The E-utilities book chapters NBK25497/NBK25499 returned a reCAPTCHA challenge page to curl (not bypassed) → their exact wording is **UNVERIFIED**; the 429/error payload format on overrun was **not provoked** (we stayed ≤ 2.5 req/s).
- Observed latencies: esearch < 1 s; esummary 100-record JSON pages up to 7.5 s measured (`curl -w time_total` on the rs=700 JAK2 page: 4.58 MB in 7.46 s; CNV-heavy pages are slow); the shell script containing the 10.8 MB XML page plus four JSON pages and five esearch calls exceeded a 180 s timeout (the XML call was not timed on its own) — budget minutes, not seconds, for any 500-record page.
- Aggregate (VCV) germline/oncogenicity review-status vocabulary and stars (https://www.ncbi.nlm.nih.gov/clinvar/docs/review_status/, fetched): four `practice guideline`; three `reviewed by expert panel`; two `criteria provided, multiple submitters, no conflicts`; one `criteria provided, conflicting classifications`; one `criteria provided, single submitter`; none `no assertion criteria provided`; none `no classification provided`; none `no classification for the individual variant`. These strings match the esummary `review_status` values exactly.

---

## 7. Recommended approach for the pipeline

**Choose esearch + esummary JSON paging (history server), per gene, pages of ≤100.** Reasoning:
1. Volume is tiny: 6,924 Variation IDs total. At retmax=100 that is ≤ 76 esummary calls + 7 esearch calls ≈ 30–60 s with a 0.4 s sleep, and ~30–40 MB of JSON — versus a 430 MB gzip (≈ several GB uncompressed, 2 rows per allele) for the bulk file, which still lacks protein change and molecular consequence columns and would need `variation_allele.txt.gz` to map AlleleID→VariationID.
2. esummary already carries every field in the brief (id, HGVS name, protein change, germline classification, review status, last evaluated, condition names + MONDO/OMIM/Orphanet/MedGen/HPO ids, molecular consequence, dbSNP/ClinGen/OMIM-allelic xrefs, SCV/RCV provenance, both somatic blocks).
3. Weekly freshness is identical (esummary DbBuild 2026-09-29 vs file 2026-09-29).
4. Reproducibility: store each raw page as `clinvar/<gene>/esummary_p<N>.json` with `retrieved_at` and the `DbBuild`/einfo `lastupdate`, so the README "how to reproduce the dataset" step is `esearch → esummary` with recorded WebEnv/counts.

Implementation rules (each one is a verified gotcha):
- Query exactly `<SYMBOL>[gene]` per gene (7 calls) rather than one OR query, so each record is tagged with the gene it was pulled for; dedupe on `uid` (2 records appear under two genes).
- **Do not add `single_gene[prop]`** — it keeps 10/817 JAK2 records (INSL6 overlap). Instead drop CNV noise client-side: `obj_type in {"copy number loss","copy number gain"}` or `len(genes) > 3` (STAT1: 821 records have 1 gene, 12 have 2, 2 have 3, the remaining 28 list 19–1216 genes and are CNVs). Keep the 2-gene SNVs.
- Use `usehistory=y` then `esummary … query_key/WebEnv/retstart/retmax=100&retmode=json`. On a response whose top level has `"eutilsresult":{"ERROR":"Input XML size … max size is 10MB"}` or `"error":"Too many UIDs …"`, halve `retmax` and retry; last-resort fallback is the same URL without `retmode=json` (XML, same element names, no 10 MB cap — verified).
- Rate limit: ≥ 0.34 s between calls (3 req/s per IP, all E-utilities combined — PubMed/RePORTER-adjacent E-utilities calls from other pipeline stages share the same budget). Add `&api_key=` when `NCBI_API_KEY` is set.
- Parse names from `title` (`^(?P<tx>N[MR]_\d+\.\d+)\((?P<gene>[^)]+)\):(?P<c>c\.[^ ]+)(?: \((?P<p>p\.[^)]+)\))?$`); treat `cdna_change` as unreliable (mixed formats). Take single-letter protein change from `protein_change` split on `, ` and match the token whose residue number equals the `p.` number parsed from `title` (e.g. R274W ↔ p.Arg274Trp), because the MANE token is not first.
- Condition ids: iterate `germline_classification.trait_set[].trait_xrefs[]`; MONDO ids already carry the `MONDO:` prefix, OMIM ids may be phenotypic series (`PS114580`), traits like `not provided`/`not specified`/`STAT1-related disorder` carry only MedGen or nothing — don't create disease nodes from those.
- Germline vs somatic: record `germline_classification`, `clinical_impact_classification`, `oncogenicity_classification` descriptions separately; additionally run `<SYMBOL>[gene] AND somatic[orig]` per gene and flag those uids `origin_somatic_reported=true`. For the hero variants (R274W 30083, T419R 650110, R321G 2925337, V617F 14662) fetch VCV XML (`rettype=vcv&is_variationid`) and read `<ObservedIn><Sample><Origin>` per SCV plus `<Citation><ID Source="PubMed">` for claim provenance.
- **Coverage gap to log now:** STAT1 N574I is not in ClinVar (0 hits for `STAT1[gene] AND (N574I OR Asn574Ile)`); the variant node must come from literature (the transcriptional-profiling paper in cluster doc §3/§11) and be labelled as such.
- Mechanism direction (GoF vs LoF) is **not** derivable from ClinVar conditions alone — R274W is linked to both OMIM 613796 (IMD31B, GoF) and OMIM 614892 (MSMD partial STAT1 deficiency, LoF). Mechanism stays a curated/LLM-extracted node as the cluster doc already plans.

When to fall back to the bulk file: only if E-utilities are down during the hackathon window. Then stream `variant_summary.txt.gz` with Range/`zcat | awk -F'\t' '$5 ~ /(^|,)(STAT1|STAT3|JAK1|JAK2|JAK3|TYK2|IL2RG)(,|$)/ && $17=="GRCh38"'` (columns 5 = GeneSymbol, 17 = Assembly) and map ids via `variation_allele.txt.gz` (24.6 MB). Expect to lose protein-change/consequence fields.

---

## Re-run check (second reviewer)

Re-run date: 2026-10-03 (≈19:45 UTC per the FTP `Date:` header), no API key, ≤2.5 req/s to eutils (two streams with `sleep 0.8`, or one stream with `sleep 0.4`). Raw responses from this pass are in `…/scratchpad/recon/rerun_clinvar/` (same basenames as cited below). ClinVar build was unchanged: esummary XML `<DbBuild>Build260929-0200.1</DbBuild>`, einfo `lastupdate` `2026/09/29 05:12`, `count` 4647675.

**Verdict: every command listed under "verified" ran and returned what the notes say.** I found no wrong URL, no redirect, no field path that only works on one record among the paths the pipeline is told to use, and no copied-wrong count in the headline numbers. The corrections below are to secondary figures, and two of them (`ref`/`alt`, HGVS count) are one-sample generalisations.

### R1. Commands re-run, verbatim results

| # | Command (as listed) | Result this run | Matches notes? |
|---|---|---|---|
| 1 | `curl -sg '…/esearch.fcgi?db=clinvar&term=STAT1[gene]&retmax=5&retmode=json'` | HTTP 200, 315 B, `"count":"863"`, `idlist` `4959190,4959188,4959187,4959185,4959183` | yes, byte-identical |
| 2 | `…esearch.fcgi?db=clinvar&term=STAT1[gene]&usehistory=y&retmax=0&retmode=json` | `"count":"863","querykey":"1","webenv":"MCID_6ac15b21d23c84f0920d6217"` (WebEnv differs per session, as expected) | yes |
| 3 | `…esummary.fcgi?db=clinvar&query_key=1&WebEnv=<webenv>&retstart=0&retmax=100&retmode=json` (STAT1 history) | HTTP 200, 251,970 B, 0.57 s, `result.uids` length 100, first `4959190` | yes |
| 4 | `…esummary.fcgi?db=clinvar&query_key=1&WebEnv=<webenv>&retstart=500&retmax=500` (JAK2 history, XML) | HTTP 200, **10,845,566 B**, 1.0 s, `text/xml`, 317 `<DocumentSummary>`, DTD `esummary clinvar 20241209`, 317 `<germline_classification>` | yes |
| 4b | same URL **with** `&retmode=json` | 171 B: `{ "header":{"type":"eutils", "version" : "0.3"}, "eutilsresult" : {"ERROR": "Input XML size is 10845566 bytes, and cannot be transformed to JSON. the max size is 10MB"} }` | yes, verbatim |
| 5 | `…esummary.fcgi?db=clinvar&id=4959190,4959188,4959187,4959185,4959183&retmode=json` | 10,613 B; record `4959190` identical key-for-key to §2.1 (checked every key/value); `4959185` `protein_change` `"M292T, M337T, M272T, M295T, M325T, M327T"`, `4959183` `"P299L, P329L, P296L, P276L, P331L, P341L"`, `4959187` `""` | yes |
| 6 | `curl -s -X POST …/esummary.fcgi --data-urlencode db=clinvar --data-urlencode retmode=json --data-urlencode 'id=<500 ids>'` (first 500 STAT1 uids) | HTTP 200, 1,433,176 B, 6.4 s, 500 records | yes |
| 7 | `…efetch.fcgi?db=clinvar&rettype=vcv&is_variationid&id=4959183,4959185&from_esearch=true` | HTTP 200, **37,500 B**, `text/xml`, root `ClinVarResult-Set`, 2 `VariationArchive`; single id `4959190` → **14,200 B** | yes |
| 8 | `…einfo.fcgi?db=clinvar&retmode=json` | 10,627 B; 47 `fieldlist` entries, 10 `linklist` names, all as listed in §1.5 (two nits in R2.6) | yes |
| 9 | `…esearch.fcgi?db=clinvar&term=JAK2[gene]+AND+somatic[orig]&retmax=5&retmode=json` | `"count":"9"`, `idlist` `4688042,4530519,3390877,2958894,869413`; translationstack shows `somatic[orig]` alone = 10412 | yes |
| 10 | `…esearch.fcgi?db=clinvar&term=STAT1[gene]+AND+%22chronic+mucocutaneous+candidiasis%22[dis]&retmax=3&retmode=json` | `"count":"2"`, `idlist` `1264351,30084` (`[dis]` term alone = 8) | yes |
| 11 | `curl -sI 'https://ftp.ncbi.nlm.nih.gov/pub/clinvar/tab_delimited/variant_summary.txt.gz'` | `HTTP/1.1 200 OK`, `Last-Modified: Tue, 29 Sep 2026 02:10:09 GMT`, `Accept-Ranges: bytes`, `Content-Length: 450727547`, `Content-Type: application/x-gzip`; no redirect | yes |
| 12 | `curl -s 'https://ftp.ncbi.nlm.nih.gov/pub/clinvar/tab_delimited/README' -o clinvar_tab_README.txt` | 47,147 B, 630 lines, line 1 `Last updated January 13, 2026`; `SomaticClinicalImpact`/`Oncogenicity` at lines 316–322 and 441–442; `SCVsForAggregate` **absent** from README | yes |
| 13 | `curl -s '…/gene_specific_summary.txt' \| grep -P '^(STAT1\|STAT3\|JAK1\|JAK2\|JAK3\|TYK2\|IL2RG)\t'` | 3,495,953 B, header `#Overview of data in ClinVar by gene, dated September 28, 2026`; the 7 rows are identical to §1.2 (IL2RG 3561 961 697 … TYK2 7297 1589 1152 1570 50 176941 506 43) | yes |
| 14 | `/opt/anaconda3/bin/python3 -c "… Range: bytes=0-524287 … variant_summary.txt.gz …"` | `206`, `Content-Range: bytes 0-524287/450727547`, 6,806 lines, **43 columns** in exactly the order listed in §5; first data row = `#AlleleID 15041`, VariationID 2, `NM_014855.3(AP5Z1):c.80_83delinsTGCTGTAAACTGTAACTGTAAA (p.Arg27_Ile28delinsLeuLeuTer)`, `Origin germline;unknown`, `OriginSimple germline`, `SomaticClinicalImpact -` | yes |

Additional claims re-checked live (not in the command list but stated as verified):

- Per-gene counts: STAT3 997, JAK1 922, JAK2 817, JAK3 1438, TYK2 1183, IL2RG 706; 7-gene OR = **6924**; `STAT1[gene] AND single_gene[prop]` = 821; `JAK2[gene] AND single_gene[prop]` = **10**; 7-gene OR `AND single_gene[prop]` = 5654; `STAT1[gene] AND (N574I OR Asn574Ile)` = **0** (querytranslation `STAT1[gene] AND (N574I[All Fields] OR Asn574Ile[All Fields])`); `N574I` alone = 8. All match.
- Paging: `STAT1[gene]&retstart=860&retmax=5` → `"retmax":"3","retstart":"860","idlist":["9045","9044","9043"]`. `retmax=10000` → 863 ids (first 4959190, last 9043). `TYK2[gene]&retmax=100000` → 1183 ids. `BRCA1[gene]&retmax=100000` → `count 16094`, 16094 ids, no `errorlist`/`warninglist`. All match.
- `retmax=1000&retmode=json` via STAT1 history → 137 B `{"header":{"type":"esummary","version":"0.3"},"error":"Too many UIDs in request. Maximum number of UIDs is 500 for JSON format output."}` — verbatim match.
- STAT1 history `retstart=0&retmax=500` → 500 records, 1,433,176 B (7.4 s); `retstart=500&retmax=500` → 363 records, 2,062,557 B (6.2 s). JAK2 history `retstart=500/600/700/800&retmax=100&retmode=json` → 100/100/100/17 records, 460,932 / 661,335 / 4,581,406 / 684,425 B. JAK2 `retstart=0&retmax=500` → 500 records, 1,904,314 B (9.9 s). All match.
- `https://ftp.ncbi.nlm.nih.gov/pub/clinvar/tab_delimited/variation_summary.txt.gz` → `HTTP/1.1 404 Not Found` (confirms the filename gotcha). Directory listing sizes/dates for all 10 files in the §5 table match (`variant_summary.txt.gz` 450,727,547 / 2026-09-28 in the listing's local-time column = 02:10 UTC 09-29 in HEAD).
- Docs pages re-fetched (HTTP 200, no redirect): `https://www.ncbi.nlm.nih.gov/clinvar/docs/maintenance_use/` contains the `FGFR3[gene]+AND+single_gene[prop]` example, `rettype=vcv&is_variationid&id=14206,41472&from_esearch=true`, `rettype=clinvarset&id=RCV000000606`, the stale field names `assembly_set` / `trait_refs` / `variant_type` / `gene/source`, and `elink.fcgi?dbfrom=clinvar&cmd=acheck&id=9`. `…/docs/review_status/` contains all seven germline review-status strings. `…/docs/ftp_primer/` contains the quoted "To find all data for a variant, we recommend using the VCV XML instead." NCBI Insights api-key post contains the three quoted sentences verbatim ("more than three E-utility requests per second", "adding the parameter api_key", "increase the limit to 10 requests/second").

### R2. Offline re-verification of field paths and counts (863 STAT1 + 817 JAK2 records fetched this run)

All of the following hold exactly as written in §2–§3: 1 distinct top-level key set / 1 germline key set / 1 `variation_set[0]` key set; `variation_set` length always 1; `title == variation_set[0].variation_name` 863/863; `obj_type == variation_set[0].variant_type` 863/863; `protein_change` non-empty 321/863 and never contains `p.`; germline description tally (314/281/82/47/45/40/27/23/3/1); review-status tally (635/122/45/33/27/1); `trait_xrefs.db_source` tally (MedGen 2206, MONDO 1954, Orphanet 1953, OMIM 1953, MeSH 33, Human Phenotype Ontology 4); molecular-consequence tally (446/291/183/38/18/13/10/10/10/7/1/1), 39 empty of which 29 CNV, 203 with >1 term; `obj_type` tally (773/35/16/14/13/6/4/2); `genes[].source` always `submitted`; gene-count distribution 821×1, 12×2, 2×3, 28 with >3; 0/863 STAT1 somatic blocks populated; trait-count distribution 459×3, 210×1, 45×0; top trait names ×697/637/616/133/87/25/2/1 with the listed xrefs (CMC = MONDO:0015279, OMIM PS114580, Orphanet 1334, MedGen C0006845, HP:0002728, HP:0005392); R274W = uid **30083** (Pathogenic; `criteria provided, multiple submitters, no conflicts`; 10 SCV / 4 RCV; xrefs ClinGen CA128924, UniProtKB P42224#VAR_065943, OMIM 600555.0008, dbSNP 387906758; conditions exactly as §2.4 incl. both OMIM 613796 and 614892), T419R = **650110** (Uncertain significance, `criteria provided, multiple submitters, no conflicts`, 2022/08/09), R321G = **2925337** (Pathogenic, single submitter, 2024/06/17), N574I absent, "574" scan hits only uid 3755867 `p.Gln594His` (token Q574H); uid 4884326 is indeed the first record in esearch order with MONDO+OMIM xrefs and a protein change; synonymous alias example uid 4684630 `aliases ["p.Asp575="]`; `variation_xrefs.db_source` ∈ {dbSNP 626, ClinGen 625, OMIM 30, dbVar 26, UniProtKB 23}. JAK2: exactly 3/817 somatic-populated records (4530519 `Tier I - Strong` / `criteria provided, single submitter` / 2024/04/24 / trait Precursor B-cell ALL MONDO:0020511; 3390877 oncogenicity `Uncertain significance` / `no assertion criteria provided` / Adrenal cortex carcinoma MONDO:0006639; 2958894 germline VUS + oncogenicity VUS `criteria provided, single submitter` / Neoplasm MONDO:0005070); V617F = uid 14662 with `protein_change` `V617F, V212F, V468F`, consequences `["missense variant","non-coding transcript variant"]`, germline `Pathogenic/Likely pathogenic` / `criteria provided, multiple submitters, no conflicts` / `2026/07/07 00:00`, traits `JAK2-related disorder`, `Thrombocythemia 3` [MONDO:0013794, OMIM 614521, MedGen C3281125], `Primary myelofibrosis` [MONDO:0009692, OMIM 254450, Orphanet 824, MedGen C0001815, MeSH D055728], … (13 traits), both somatic blocks empty, `genes` = INSL6(11172,−) + JAK2(3717,+); 627 two-gene records of which 625 are (INSL6, JAK2) (the other two: JAK2+LOC130001497, JAK2+ZBTB20); 10 single-gene records (= the `single_gene[prop]` count). VCV XML (uids 4959183/4959185/4959190): every element and attribute named in §4 is present (`VariationArchive` attrs, `Gene` attrs incl. `HGNC_ID="HGNC:11362"` + child `<OMIM>600555</OMIM>`, `HGVS Type="coding"` → `NucleotideExpression sequenceAccessionVersion="NM_007315.4" change="c.986C>T" MANESelect="true"` + `ProteinExpression sequenceAccessionVersion="NP_009330.1" change="p.Pro329Leu" singleLetterProteinChange="P329L"` + `MolecularConsequence ID="SO:0001583" Type="missense variant" DB="SO"`, `SequenceLocation … positionVCF referenceAlleleVCF alternateAlleleVCF`, `ClinicalAssertion ID SubmissionDate`, `ClinVarAccession Accession="SCV007922768" SubmitterName="Blueprint Genetics" OrgID="500188" OrganizationCategory="laboratory"`, `Attribute Type="AssertionMethod"` = `ACMG Guidelines, 2015`, `Citation/ID Source="PubMed"` = 25741868, `ObservedIn/Sample/Origin` = `germline`, `Method/MethodType` = `clinical testing`, `XRef ID="CN169374" DB="MedGen"`, `ElementValue Type="Preferred|Alternate"`); only `GermlineClassification` under `Classifications`.

**Corrections (none affect the recommended approach):**

- **R2.1 `cdna_change` prefix tally (§2.2).** Notes say "495 start with `c.`, 338 start with `NM_…`". Actual: 495 `c.`, **334** `NM_`, 29 `GRCh37/hg19…`/`GRCh38/hg38…`, **4 `NC_000002.11:g.…`** (e.g. uid 2425749 `NC_000002.11:g.(?_191843562)_(191845415_?)del`), 1 `Single allele` (uid 635897, Duplication). Sum 863. The conclusion (parse from `title`, not `cdna_change`) is unchanged and if anything stronger.
- **R2.2 Max gene count on STAT1 CNVs (§2.2 "up to 1216 genes", §7 "19–1216").** Actual maximum is **1705** (uid 59158 `GRCh38/hg38 2q32.2-37.3(chr2:188818195-242065208)x…`), then 1689 (154206), 1667 (59159); 1216 is uid 442998, the 4th largest. The `len(genes) > 3` client-side CNV filter still partitions the 863 records exactly as described (28 records).
- **R2.3 "19 HGVS entries per SNV" (§4) is a one-record figure.** uid 4959190 has 19 `<HGVS>` (15 `Type="coding"`, 2 `genomic`, 2 `genomic, top-level`); uids 4959183 and 4959185 have **20** (16 coding + 2 + 2). Treat the count as variable. `HGVS` `Type` attribute values observed: `coding`, `genomic`, `genomic, top-level` (the last carries `Assembly="GRCh37"|"GRCh38"`). LRG_111 / LRG_111t1 / NG_008294.1 / NC_000002.11 / NC_000002.12 accessions confirmed present as claimed.
- **R2.4 `ref`/`alt` in `variation_loc[]` are not always empty (§2.2 "ref/alt were empty in observed SNV records").** 37 of 863 STAT1 records (all SNVs) carry non-empty `ref`/`alt`, always on the GRCh37 `status:"previous"` entry (e.g. uid 1174479 GRCh37 `ref "A"`, `alt "G"`). So the field is sparsely populated rather than unused; the recommendation to take alleles from `canonical_spdi` (populated on every SNV checked) or from VCV `referenceAlleleVCF`/`alternateAlleleVCF` stands.
- **R2.5 uid 155409 (§2.3) has 3,803 genes, not "hundreds"**, and is 331,867 B as compact JSON (NCBI's indented output is larger; "~500 KB" could not be confirmed exactly). 37 JAK2 records list >500 genes; the three largest (155409, 150050, 160862, all `9p24.3-q34.3 … x3`) are ~332 KB compact each. V617F's `trait_set` is 3,895 B compact (13 traits), not "8.4 KB" (presumably measured indented). Neither number matters for the design.
- **R2.6 einfo nits (§1.5).** `ACCN` appears **twice** in `fieldlist` (fullname `ClinVar accession`, description "Accession of the genotype/phenotype assertion"; and fullname `Nucleotide/Protein Accession`). `MIM`'s `fullname` is just `MIM`; "MIM number from OMIM" is its `description`. `TRID` description is "Public identifier for a trait (e.g. CUI, HPO)". Everything else in the field/link lists matches.
- **R2.7 Minor completeness in the condition-xref list (§2.4).** `Chromosome 2q32-q33 deletion syndrome` also carries MedGen C2676739 and Orphanet 251019; `not provided` carries MedGen **CN517202** in some records in addition to C3661900 (so don't hard-code C3661900 as the only "not provided" id). Trait names omitted from the "top" list: `See cases` ×17, `STAT1-related disorder` ×13, `Inherited Immunodeficiency Diseases` ×7.
- **R2.8 New observation:** 50 STAT1 germline blocks have the sentinel `last_evaluated` `1/01/01 00:00` but only 45 have an empty `description` — 5 records are classified yet undated. Do not assume `last_evaluated` is set whenever `description` is.
- **R2.9 Time estimate (§0 "≈ 30 s", verdict "≈30–60 s") is optimistic.** The limiter is server time, not the 3 req/s cap: measured `time_total` this run — 100-record JSON pages 0.57 s (STAT1 p0), 1.0 / 1.3 / **8.3** / 1.0 s (JAK2 rs500–800); 500-record pages 6.2–9.9 s; POST 500 ids 6.4 s; 10.8 MB XML page 1.0 s. For the ~73 pages of 100 (9+10+10+9+15+12+8) budget **~1–3 minutes** wall time, more if CNV-heavy pages recur. §6's "budget minutes, not seconds" is the right guidance; the TL;DR number should be read that way.

### R3. Items the original left UNVERIFIED or unexecuted, now checked

- **Somatic clinical-impact review-status/star table (§3 said UNVERIFIED)** — captured from `https://www.ncbi.nlm.nih.gov/clinvar/docs/review_status/` this run: four `practice guideline`; three `reviewed by expert panel`; two **`criteria provided, multiple submitters`** (no ", no conflicts" suffix — the page says consensus "is not considered" for somatic clinical impact); one `criteria provided, single submitter`; none `no assertion criteria provided`; none `no classification provided`; none `no classification for the individual variant`. Germline **and oncogenicity** share the germline table (page section "Germline classification and oncogenicity"). The live value on uid 4530519's `clinical_impact_classification.review_status` (`criteria provided, single submitter`) is in this vocabulary.
- **elink (documented in maintenance_use, not executed by the original)** — ran `curl -s 'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/elink.fcgi?dbfrom=clinvar&db=pubmed&id=30083&retmode=json'` → HTTP 200, 297 B: `linksets[0].dbfrom` `clinvar`, `ids ["30083"]`, `linksetdbs[0]` = `{"dbto":"pubmed","linkname":"clinvar_pubmed","links":[...]}` with **12 PMIDs** for R274W (first five `39175777, 28492532, 28258222, 26604104, 26255980`). This is the cheapest variant→PubMed provenance route (one call per hero variant) and shares the same 3 req/s budget.
- **Update cadence nuance:** the ftp_primer page says `variant_summary.txt` "is released on the first Thursday of the month", but the tab_delimited `README` (line 61–67) says "Data on the ftp site are updated monthly and weekly … Weekly: usually on Mondays", and the live file is dated Monday 2026-09-28 (02:10 UTC Tue 09-29) — i.e. weekly, matching the esummary `DbBuild260929`. The original's "same weekly freshness" claim holds; the primer sentence is stale.
- Still UNVERIFIED (unchanged): the E-utilities book chapters NBK25497/NBK25499 wording (not re-attempted); the 429/over-limit payload format (not provoked); XML element names for somatic classifications in VCV records (no somatic record was efetched).
