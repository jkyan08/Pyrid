# Source API cheat sheet — ClinVar, ClinicalTrials.gov, NIH RePORTER, HPO/MONDO/HGNC/Reactome/GO

Facts below were verified against the live APIs on 2026-10-03. Nothing here is from memory; anything that could not be checked is marked **UNVERIFIED**.

---

## 1. ClinVar (NCBI E-utilities)

Build observed: esummary `<DbBuild>Build260929-0200.1</DbBuild>`; einfo `lastupdate 2026/09/29 05:12`, `count` 4,647,675. No API key used.

### 1a. Endpoints verified live

Base `https://eutils.ncbi.nlm.nih.gov/entrez/eutils/`

| Endpoint | Parameters that worked | Notes |
|---|---|---|
| `esearch.fcgi` | `db=clinvar&term=STAT1[gene]&retmax=5&retmode=json`; `usehistory=y&retmax=0`; `retstart`/`retmax` paging | `idlist` = Variation IDs, descending (newest first). `retmax=10000` (STAT1: 863) and `retmax=100000` (TYK2: 1183; BRCA1: 16094, no `errorlist`/`warninglist`) returned every id → no 10,000 cap observed; documented cap UNVERIFIED (NBK25499 behind reCAPTCHA) |
| `esummary.fcgi` | `db=clinvar&id=<csv>&retmode=json`; `query_key=1&WebEnv=…&retstart=N&retmax=100&retmode=json`; POST with `--data-urlencode id=<500 ids>` | JSON: ≤500 UIDs/call and ≤10 MB intermediate XML (see gotchas). Same URL without `retmode=json` → XML, same element names, no 10 MB cap |
| `efetch.fcgi` | `db=clinvar&rettype=vcv&is_variationid&id=4959183,4959185&from_esearch=true` | 37,500 B for 2 ids; 14,200 B for single id 4959190. Also `rettype=vcv&id=VCV000014206[.1]`, `rettype=clinvarset&id=RCV000000606` (documented) |
| `einfo.fcgi` | `db=clinvar&retmode=json` | 47 `fieldlist` entries, 10 `linklist` names |
| `elink.fcgi` | `dbfrom=clinvar&db=pubmed&id=30083&retmode=json` | 297 B; `linkname clinvar_pubmed`, **12 PMIDs** for R274W (first five 39175777, 28492532, 28258222, 26604104, 26255980) |

```sh
# curl needs -g (globoff) or %5B/%5D, otherwise [gene] is eaten as a glob and curl returns nothing.
curl -sg 'https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=clinvar&term=STAT1[gene]&usehistory=y&retmax=0&retmode=json'
# → {"esearchresult":{"count":"863","retmax":"0","retstart":"0","querykey":"1","webenv":"MCID_6ac156ed2b400ad4630bbacb","idlist":[], ...}}
WEBENV=MCID_…; QK=1
curl -s "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?db=clinvar&query_key=${QK}&WebEnv=${WEBENV}&retstart=0&retmax=100&retmode=json"
```

Trimmed real esummary record (uid 4884326; top level is `{"header":…,"result":{"uids":[…],"<uid>":{…}}}`):

```json
{"uid":"4884326","accession_version":"VCV004884326.1","title":"NM_007315.4(STAT1):c.265A>T (p.Asn89Tyr)",
 "obj_type":"single nucleotide variant","protein_change":"N101Y, N89Y, N91Y","molecular_consequence_list":["missense variant"],
 "genes":[{"symbol":"STAT1","geneid":"6772","strand":"-","source":"submitted"}],
 "germline_classification":{"description":"Likely pathogenic","last_evaluated":"2026/08/11 00:00",
   "review_status":"criteria provided, single submitter","fda_recognized_database":"",
   "trait_set":[{"trait_xrefs":[{"db_source":"Orphanet","db_id":"391487"},{"db_source":"MedGen","db_id":"C3279990"},
                                {"db_source":"MONDO","db_id":"MONDO:0013599"},{"db_source":"OMIM","db_id":"614162"}],
                 "trait_name":"Autoimmune enteropathy and endocrinopathy - susceptibility to chronic infections syndrome"}]},
 "clinical_impact_classification":{"description":"","last_evaluated":"1/01/01 00:00","review_status":"","trait_set":[]},
 "oncogenicity_classification":{"description":"","last_evaluated":"1/01/01 00:00","review_status":"","trait_set":[]},
 "supporting_submissions":{"scv":["SCV007664810"],"rcv":["RCV006748357"]},
 "variation_set":[{"variation_name":"NM_007315.4(STAT1):c.265A>T (p.Asn89Tyr)","cdna_change":"c.265A>T",
   "canonical_spdi":"NC_000002.12:191008970:T:A","variation_xrefs":[],"aliases":[],"allele_freq_set":[…],"variation_loc":[…]}]}
```

Live counts (`<SYMBOL>[gene]`, retmax=0): STAT1 863 · STAT3 997 · JAK1 922 · JAK2 817 · JAK3 1438 · TYK2 1183 · IL2RG 706 · 7-gene OR **6924** (sum 6926 → 2 records touch two cluster genes).

Hero variants (exact-token match on `protein_change`): R274W = uid **30083** (Pathogenic; multiple submitters, no conflicts; 10 SCV/4 RCV; ClinGen CA128924, UniProtKB P42224#VAR_065943, OMIM 600555.0008, dbSNP 387906758) · T419R = **650110** (Uncertain significance) · R321G = **2925337** (Pathogenic, single submitter) · **N574I absent** (0 hits) · JAK2 V617F = **14662**.

### 1b. Field paths the pipeline needs (esummary JSON)

| Need | Path | Observed |
|---|---|---|
| Variation ID / accession | `uid`, `accession` (`VCV`+9-digit zero-pad), `accession_version` | `4959190` → `VCV004959190.1` |
| Preferred HGVS name | `title` (== `variation_set[0].variation_name` 863/863) | `NM_007315.4(STAT1):c.820C>T (p.Arg274Trp)`; parse with `^(?P<tx>N[MR]_\d+\.\d+)\((?P<gene>[^)]+)\):(?P<c>c\.[^ ]+)(?: \((?P<p>p\.[^)]+)\))?$` |
| Protein change (1-letter) | `protein_change` | `R274W, R241W, R244W, R276W, R286W` — comma-separated per isoform, MANE **not** first, never contains `p.`, stop = `*`; non-empty 321/863; synonymous → `""` with alias `p.Asp575=` in `variation_set[0].aliases` |
| Germline classification | `germline_classification.{description,review_status,last_evaluated}` | dates `YYYY/MM/DD HH:MM`; empty sentinel `1/01/01 00:00` (5 STAT1 records are classified yet undated — don't assume `last_evaluated` is set when `description` is) |
| Conditions | `germline_classification.trait_set[].trait_name`, `.trait_xrefs[].{db_source,db_id}` | `db_source` ∈ MedGen, MONDO (`MONDO:0013427`, prefix included), Orphanet (`391311`), OMIM (`613796` or `PS114580`), MeSH (`D030342`), Human Phenotype Ontology (`HP:0002728`) |
| Molecular consequence | `molecular_consequence_list[]` | SO term text, no id; 203/863 have >1 term; empty for 39 (29 CNV) |
| Variant type | `obj_type` (== `variation_set[0].variant_type`) | SNV 773, Deletion 35, copy number loss 16, Duplication 14, copy number gain 13, … |
| Genes | `genes[].{symbol,geneid,strand,source}` | `source` always `submitted`; STAT1: 821×1 gene, 12×2, 2×3, 28 with >3 (CNVs, max **1705** genes, uid 59158) |
| Location / alleles | `variation_set[0].variation_loc[]` (GRCh38 `status:current`, GRCh37 `status:previous`), `variation_set[0].canonical_spdi` | `ref`/`alt` sparsely populated (37/863, GRCh37 entry only) → use `canonical_spdi` or VCV `referenceAlleleVCF`/`alternateAlleleVCF` |
| Variant xrefs | `variation_set[0].variation_xrefs[].{db_source,db_id}` | dbSNP (no `rs`, e.g. `387906758`), ClinGen, OMIM allelic (`600555.0008`), UniProtKB, dbVar |
| Allele frequency | `variation_set[0].allele_freq_set[].{source,value,minor_allele}` | gnomAD / TOPMed |
| Provenance | `supporting_submissions.scv[]`, `.rcv[]` | SCV/RCV accessions |
| Somatic blocks | `clinical_impact_classification.*`, `oncogenicity_classification.*` | same shape as germline; populated in 3/817 JAK2, 0/863 STAT1 |

VCV XML (efetch) adds per-transcript `HGVSlist/HGVS Type="coding"` → `NucleotideExpression sequenceAccessionVersion="NM_007315.4" change="c.986C>T" MANESelect="true"` + `ProteinExpression change="p.Pro329Leu" singleLetterProteinChange="P329L"` + `MolecularConsequence ID="SO:0001583"`; per-SCV `ClinicalAssertion/…/ObservedIn/Sample/Origin` (`germline`), `ClinVarAccession SubmitterName`, `Citation/ID Source="PubMed"`. HGVS entries per SNV vary (19–20 observed). Only `GermlineClassification` seen; somatic element names in XML UNVERIFIED.

### 1c. Gotchas

1. **esummary JSON pages ≤100 records.** JSON caps: 500 UIDs/call (`{"error":"Too many UIDs in request. Maximum number of UIDs is 500 for JSON format output."}`) and 10 MB intermediate XML (`{"eutilsresult":{"ERROR":"Input XML size is 10845566 bytes, and cannot be transformed to JSON. the max size is 10MB"}}` on JAK2 `retstart=500&retmax=500`, 317 records). Whole-chromosome CNVs (uid 155409: 3,803 genes, ~332 KB compact) blow pages. On either error halve `retmax`; last resort drop `retmode=json` and parse XML.
2. **Never use `single_gene[prop]`**: `JAK2[gene] AND single_gene[prop]` → 10 of 817 because 625 JAK2 records (incl. the V617F SNV) are co-annotated with overlapping INSL6. Filter CNVs client-side instead (`obj_type` in copy number loss/gain, or `len(genes) > 3`). Keep 2-gene SNVs.
3. **STAT1 N574I is not in ClinVar** (`STAT1[gene] AND (N574I OR Asn574Ile)` → 0; offline scan of 863 titles finds only `p.Gln594His`, token Q574H). Log as coverage gap; node must come from literature.
4. **GoF/LoF not derivable from conditions**: R274W carries both OMIM 613796 (IMD31B, GoF) and OMIM 614892 (MSMD partial STAT1 deficiency, LoF) because submitters test panels. Mechanism stays curated/LLM-extracted.
5. **Germline vs somatic**: V617F (uid 14662) has only a *germline* block (`Pathogenic/Likely pathogenic`); both somatic blocks empty. esummary has **no origin field**. Origin lives in (a) esearch `[orig]` (`JAK2[gene] AND somatic[orig]` → 9: 4688042, 4530519, 3390877, 2958894, 869413, …), (b) VCV XML `<ObservedIn><Sample><Origin>`, (c) bulk `variant_summary.txt` `Origin`/`OriginSimple`.
6. **elink to PubMed** works (`dbfrom=clinvar&db=pubmed`); one call per hero variant; shares the 3 req/s budget.
7. `cdna_change` is mixed (495 `c.`, 334 `NM_…`, 29 `GRCh37/hg19…`/`GRCh38/hg38…`, 4 `NC_000002.11:g.…`, 1 `Single allele`) → parse from `title`.
8. Traits `not provided` / `not specified` / `STAT1-related disorder` carry only MedGen or nothing — don't create disease nodes (other low-information trait names seen: `See cases` ×17, `Inherited Immunodeficiency Diseases` ×7). `not provided` uses MedGen C3661900 **and** CN517202.
9. Docs field names (`assembly_set`, `trait_refs`, `gene/source`) are stale; live JSON uses `variation_loc`, `trait_xrefs`, `genes[].source`.
10. Review-status strings match the docs exactly: 4★ `practice guideline`; 3★ `reviewed by expert panel`; 2★ `criteria provided, multiple submitters, no conflicts`; 1★ `criteria provided, conflicting classifications` / `criteria provided, single submitter`; 0★ `no assertion criteria provided` / `no classification provided` / `no classification for the individual variant`. Oncogenicity shares this table; somatic clinical impact uses 2★ **`criteria provided, multiple submitters`** (no ", no conflicts").
11. Query `<SYMBOL>[gene]` per gene (7 calls), dedupe on `uid`. Budget **~1–3 min** wall time for ~73 pages of 100 (server time: 0.57–8.3 s per 100-record page; 500-record pages 6.2–9.9 s).
12. Bulk fallback: `https://ftp.ncbi.nlm.nih.gov/pub/clinvar/tab_delimited/variant_summary.txt.gz` (450,727,547 B, 2026-09-29, `Accept-Ranges: bytes`, 43 columns; one row per allele **per assembly**). `variation_summary.txt.gz` does **not** exist (404). No protein-change or molecular-consequence columns; needs `variation_allele.txt.gz` (24,612,358 B) for AlleleID↔VariationID.

### 1d. Rate limits / terms

- Without key: >3 E-utility requests/s per IP → error (all E-utilities combined — PubMed calls share the budget). `api_key` parameter → 10 req/s per key (NCBI Insights post, fetched). README `NCBI_API_KEY`. 429/over-limit payload format not provoked (UNVERIFIED). Use ≥0.34 s between calls.
- License/terms: not captured — fill in before the README license table is finalised.

---

## 2. ClinicalTrials.gov API v2

`GET https://clinicaltrials.gov/api/v2/version` → `{ "apiVersion" : "2.0.5", "dataTimestamp" : "2026-10-02T09:00:04" }`. No key exists or is accepted.

### 2a. Endpoints verified live

Base `https://clinicaltrials.gov/api/v2`. Spec: `https://clinicaltrials.gov/api/oas/v2` (YAML, 80,983 B). Also `/studies/metadata` (175,633 B; field/piece names), `/studies/search-areas` (25,359 B), `/studies/enums` (19,945 B, 41 enums), `/stats/size`, `/stats/field/values?fields=Phase`, `/stats/field/sizes`. `/studies/download?format=json.zip` exists (HEAD 200, `application/zip`, no `content-length`) but is not in the spec and not needed.

`GET /studies` params: `query.cond`, `query.term`, `query.intr` (Essie syntax; also `query.locn/titles/outc/spons/lead/id/patient`), `filter.overallStatus`, `filter.ids`, `filter.advanced`, `fields` (piece names, dotted paths or module names; comma/pipe), `sort` (date/numeric fields or `@relevance`; **unsorted by default**), `countTotal` (default false), `pageSize` (default 10, coerced to **1000**), `pageToken`, `format` (`json`|`csv`), `markupFormat`.
`GET /studies/{nctId}` params: `format` (`csv, json, json.zip, fhir.json, ris`), `markupFormat`, `fields`. Responses 200 / 301 / 400 / 404.

```sh
curl -sG 'https://clinicaltrials.gov/api/v2/studies' --data-urlencode 'query.term="STAT1 gain of function"' \
  --data-urlencode 'fields=NCTId,BriefTitle,OverallStatus' --data-urlencode 'countTotal=true' --data-urlencode 'pageSize=50'
# totalCount 3:
# NCT07261891 | RECRUITING          | Ex Vivo Evaluation of JAK-inhibitor and Gene Therapeutical Approach in JAK-STAT Related Disorders
# NCT05561985 | AVAILABLE           | Expanded Access Use of Itacitinib to Treat a Single Patient With STAT1 Gain of Function (GOF) Disease
# NCT07670156 | NOT_YET_RECRUITING  | Upadacitinib in Treatment of JAK/STAT Pathway Disorders With Activating Mutations
curl -s 'https://clinicaltrials.gov/api/v2/studies/NCT07261891?fields=protocolSection.identificationModule.nctId,protocolSection.identificationModule.briefTitle,protocolSection.statusModule.overallStatus,protocolSection.designModule.phases,protocolSection.conditionsModule.conditions'
# → {"protocolSection":{"identificationModule":{"nctId":"NCT07261891","briefTitle":"Ex Vivo Evaluation of JAK-inhibitor and Gene Therapeutical Approach in JAK-STAT Related Disorders"},
#    "statusModule":{"overallStatus":"RECRUITING"},"conditionsModule":{"conditions":["Primary Immunodeficiency Diseases (PID)"]},"designModule":{"phases":["NA"]}}}
```

Trimmed real record (`GET /studies/NCT07670156`, 16,939 B full):

```json
{"protocolSection":{
  "identificationModule":{"nctId":"NCT07670156","organization":{"fullName":"Baylor College of Medicine","class":"OTHER"},
    "briefTitle":"Upadacitinib in Treatment of JAK/STAT Pathway Disorders With Activating Mutations"},
  "statusModule":{"overallStatus":"NOT_YET_RECRUITING","startDateStruct":{"date":"2026-10","type":"ESTIMATED"},
    "primaryCompletionDateStruct":{"date":"2028-08","type":"ESTIMATED"},"studyFirstPostDateStruct":{"date":"2026-06-26","type":"ACTUAL"},
    "lastUpdatePostDateStruct":{"date":"2026-09-08","type":"ACTUAL"}},
  "sponsorCollaboratorsModule":{"leadSponsor":{"name":"Lisa Satter","class":"OTHER"},
    "collaborators":[{"name":"National Center for Advancing Translational Sciences (NCATS)","class":"NIH"},{"name":"AbbVie","class":"INDUSTRY"}]},
  "conditionsModule":{"conditions":["JAK1 GOF","STAT1 GOF","STAT3 GOF","STAT5B GOF","STAT6 GOF"],"keywords":["Upadacitinib","JAK-STAT Pathway Disorders","STAT1","STAT3", "..."]},
  "designModule":{"studyType":"INTERVENTIONAL","phases":["PHASE1","PHASE2"],"designInfo":{"allocation":"RANDOMIZED","primaryPurpose":"TREATMENT"},
    "enrollmentInfo":{"count":30,"type":"ESTIMATED"}},
  "armsInterventionsModule":{"interventions":[{"type":"DRUG","name":"Upadacitinib","otherNames":["Rinvoq"]},{"type":"DRUG","name":"Placebo"}]},
  "eligibilityModule":{"sex":"ALL","minimumAge":"12 Years","maximumAge":"65 Years","stdAges":["CHILD","ADULT","OLDER_ADULT"],"healthyVolunteers":false}},
 "derivedSection":{"miscInfoModule":{"versionHolder":"2026-10-02"},
  "conditionBrowseModule":{"meshes":[{"id":"D007153","term":"Immunologic Deficiency Syndromes"}],"ancestors":[{"id":"D007154","term":"Immune System Diseases"}]},
  "interventionBrowseModule":{"meshes":[{"id":"C000613732","term":"upadacitinib"}]}},
 "hasResults":false}
```

**The six required searches (live totals):**

| # | Query | totalCount | NCT ids (status) |
|---|---|---|---|
| Q1 | `query.intr=ruxolitinib` + `query.term=STAT1` | **0** | — (also 0 for `query.term=ruxolitinib AND STAT1` and for `query.cond="primary immunodeficiency"` + `query.intr=ruxolitinib`) |
| Q2 | `query.cond="chronic mucocutaneous candidiasis"` | 5 | NCT01842386 COMPLETED, NCT03059992 COMPLETED, NCT02629419 COMPLETED, NCT03800056 TERMINATED, NCT01386437 RECRUITING |
| Q3 | `query.term="STAT1 gain of function"` | **3** | NCT07261891 RECRUITING, NCT05561985 AVAILABLE, NCT07670156 NOT_YET_RECRUITING |
| Q4 | `query.intr=JAK inhibitor` + `query.cond="primary immunodeficiency"` | 1 | NCT07261891 RECRUITING |
| Q5 | `query.cond="hyper IgE syndrome"` | 14 | incl. NCT00006150 RECRUITING, NCT07262983 RECRUITING (baricitinib, Job syndrome), NCT02629419, NCT01176006 ACTIVE_NOT_RECRUITING |
| Q6 | `query.cond="severe combined immunodeficiency"` + `query.term=JAK3` | 3 | NCT03597594 ACTIVE_NOT_RECRUITING, NCT00006319 UNKNOWN, NCT03619551 ACTIVE_NOT_RECRUITING |

Headline: no ruxolitinib trial in STAT1 GoF, but three JAK-inhibitor studies touch STAT1 GoF (upadacitinib Phase 1/2 basket trial not yet recruiting; itacitinib single-patient expanded access; ex vivo JAK-inhibitor evaluation) plus NCT07262983 (baricitinib, STAT3 hyper-IgE). Also `query.cond=EXPANSION[None]STAT1` → 8 (NCT07261891, NCT05561985, NCT05781841, NCT07670156, NCT02629419, NCT01568996, NCT02171286, NCT00001467); `query.term="STAT3 gain of function"` → 1 (NCT07670156); `query.term=EXPANSION[None]IL2RG` → 10 incl. X-SCID gene-therapy NCT04286815, NCT03601286, NCT06851767, NCT03619551, NCT03597594; MPN + ruxolitinib → 175 (cap it like the README caps MPN literature).

### 2b. Field paths the pipeline needs (dotted path → `fields` piece name)

- `protocolSection.identificationModule.nctId` → `NCTId`; `.briefTitle` → `BriefTitle`; `.officialTitle` → `OfficialTitle`; `.nctIdAliases[]` → `NCTIdAlias`
- `protocolSection.statusModule.overallStatus` → `OverallStatus` (enum `Status`); `.startDateStruct.{date,type}` → `StartDate`/`StartDateType`; `.primaryCompletionDateStruct` → `PrimaryCompletionDate`; `.lastUpdatePostDateStruct.date` → `LastUpdatePostDate`; `.studyFirstPostDateStruct.date` → `StudyFirstPostDate`
- `protocolSection.sponsorCollaboratorsModule.leadSponsor.{name,class}` → `LeadSponsor`/`LeadSponsorName`; `.collaborators[].name` → `CollaboratorName`; `.responsibleParty.type` → `ResponsiblePartyType`
- `protocolSection.conditionsModule.conditions[]` → `Condition` (free text); `.keywords[]` → `Keyword`
- `protocolSection.designModule.studyType` → `StudyType`; `.phases[]` → `Phase`; `.enrollmentInfo.{count,type}` → `EnrollmentInfo`/`EnrollmentCount`; `.designInfo.allocation` → `DesignAllocation`; `.designInfo.primaryPurpose` → `DesignPrimaryPurpose`
- `protocolSection.armsInterventionsModule.interventions[].{type,name,otherNames[],description,armGroupLabels[]}` → `Intervention`/`InterventionType`/`InterventionName`
- `protocolSection.eligibilityModule.{eligibilityCriteria,sex,minimumAge,maximumAge,stdAges[],healthyVolunteers}` → `EligibilityModule` (branch)
- `protocolSection.contactsLocationsModule.overallOfficials[].{name,affiliation,role}` → `OverallOfficialName`; `.locations[].{facility,city,state,country,geoPoint}`; `.centralContacts[]` and `.locations[].contacts[]` have `name, role, phone, email` (T&C applies)
- `protocolSection.referencesModule.references[].{pmid,type,citation}` → `ReferencePMID`/`ReferenceType`/`ReferenceCitation` (verified on NCT07261891, NCT07262983; e.g. `{"pmid":"37020552","type":"BACKGROUND",…}`) — link studies to PubMed nodes
- `derivedSection.conditionBrowseModule.meshes[]{id,term}`, `.ancestors[]` — only coded disease ids (MeSH); `derivedSection.interventionBrowseModule.meshes[]`; `derivedSection.miscInfoModule.versionHolder` (data-version date to display)
- `hasResults` → `HasResults`; `resultsSection` (4th top-level key, only when `hasResults` true; modules `participantFlowModule, baselineCharacteristicsModule, outcomeMeasuresModule, adverseEventsModule, moreInfoModule`; verified on NCT00527878)

Verified pipeline `fields` set (one call, HTTP 200): `NCTId,BriefTitle,OfficialTitle,OverallStatus,StartDate,StartDateType,PrimaryCompletionDate,LastUpdatePostDate,StudyFirstPostDate,LeadSponsor,CollaboratorName,ResponsiblePartyType,Condition,Keyword,StudyType,Phase,EnrollmentInfo,DesignAllocation,DesignPrimaryPurpose,Intervention,EligibilityModule,OverallOfficialName,ReferenceType,HasResults` + `derivedSection.conditionBrowseModule,derivedSection.interventionBrowseModule,derivedSection.miscInfoModule`.

### 2c. Gotchas

1. **Default `EXPANSION[Relaxation]` fuzzy-matches gene symbols**: `query.cond=STAT1` → 14 with Abbott **i-STAT** device noise; `query.cond=EXPANSION[None]STAT1` → 8 clean. `None` is case- and accent-sensitive, so **do not** apply it to quoted disease phrases (`EXPANSION[None]"chronic mucocutaneous candidiasis"` drops 5 → 1). Landing page: COVERAGE/EXPANSION "not fully implemented"; our tests show it is at least partly effective.
2. `query.term` searches 57 pieces (incl. `BriefSummary`, outcomes) → gene symbols hit biomarker mentions (STAT1: 60). Use `query.cond` (7 pieces incl. `Keyword`) for gene-as-disease, `query.intr` for drugs. `query.term=IL2RG` did not narrow SCID (101 = 101) — treat with suspicion.
3. **`totalCount` only on page 1**; `nextPageToken` appears whenever a page is full (even `pageSize=1`); last page may have empty `studies`. Subsequent pages must repeat all params except `countTotal`, `pageSize`, `pageToken`.
4. **301 alias redirects**: `GET /studies/NCT05033340` → `HTTP/2 301`, `location: /api/v2/studies/NCT04173273` (relative, empty body). Follow redirects (`curl -L`; `requests` does by default) and store the canonical `nctId` from the body, not the id asked for. Aliased studies: `query.term=NOT AREA[NCTIdAlias]MISSING` (3,162). That record is 701 KB — size outliers exist (p50 9,856 B, p99 149,539 B).
5. Errors are **plain text, not JSON** (400 `Parameter 'fields' contains invalid field name: 'NotARealField'`; 404 `NCT number NCT04409146 not found`; `filter.advanced` parse errors likewise 400 text).
6. Expanded-access records are sparse (NCT05561985, 1,886 B): no `startDateStruct`, `phases`, `enrollmentInfo`, `sex`, `minimumAge`; `overallStatus` extra values `AVAILABLE`, `NO_LONGER_AVAILABLE`, `TEMPORARILY_NOT_AVAILABLE`, `APPROVED_FOR_MARKETING`. Every extractor field must be optional; `referencesModule`/`ipdSharingStatementModule` absent on some interventional records too.
7. Dates are partial ISO (`"2026-10"`); ages are strings with units (`"12 Years"`); `eligibilityCriteria` is markdown with escapes (`\\<`, `\\-`).
8. `conditions[]` is free text (`"STAT1 GOF"`); only MeSH ids are coded — map to MONDO downstream.
9. CSV column names (`NCT Number`, `Study Title`, `Study Status`) differ from JSON piece names; prefer JSON. CSV total is in `x-total-count` header.
10. **T&C: do not use email addresses** (`centralContacts[].email`, `locations[].contacts[].email`) for marketing/promotional purposes — keep them out of outreach routes. **Display the data-processed date** (`versionHolder` / `dataTimestamp`), attribute ClinicalTrials.gov, keep data current, state modifications (mark LLM-extracted summaries as modifications).
11. Status enum: `ACTIVE_NOT_RECRUITING, COMPLETED, ENROLLING_BY_INVITATION, NOT_YET_RECRUITING, RECRUITING, SUSPENDED, TERMINATED, WITHDRAWN, WITHHELD, UNKNOWN` + the expanded-access values. Phase: `NA, EARLY_PHASE1, PHASE1..PHASE4`. StudyType: `EXPANDED_ACCESS, INTERVENTIONAL, OBSERVATIONAL`.

### 2d. Rate limits / terms

- **No rate limit stated anywhere** (spec, docs pages, JS bundle); no `x-ratelimit*`/`retry-after` headers. Throttle as a courtesy (reuse the NCBI 3 req/s limiter in `pipeline/cache.py`) and cache every response.
- Data refresh: daily Mon–Fri, generally by 9 a.m. ET; check `/version` `dataTimestamp`.
- Terms and Conditions (`https://clinicaltrials.gov/about-site/terms-conditions`, last updated January 31, 2023): free to all requesters; no warranties; attribute source, keep current, display processing date, state modifications; no proprietary claims; **no email use for marketing**; international copyright outside the US. Store `retrieved_at`, `dataTimestamp`, `versionHolder` on every Study node.

---

## 3. NIH RePORTER API v2

Docs `https://api.reporter.nih.gov/` (swagger-ui page); spec `https://api.reporter.nih.gov/swagger/v2/swagger.json` (OpenAPI 3.0.1, version "2.0"); Data Elements PDF `https://api.reporter.nih.gov/documents/Data%20Elements%20for%20RePORTER%20Project%20API_V2.pdf` (690,439 B, 51 pages). No auth. Header `api-supported-versions: 2.0` on 200s and cap-400s (absent on model-binding 400s).

### 3a. Endpoints verified live (only two exist)

`POST /v2/projects/search` and `POST /v2/publications/search`, JSON in / JSON out, `Content-Type: application/json`.

```bash
curl -sS -X POST "https://api.reporter.nih.gov/v2/projects/search" -H "accept: application/json" -H "Content-Type: application/json" \
  -d '{"criteria":{"advanced_text_search":{"operator":"and","search_field":"projecttitle,terms,abstracttext","search_text":"STAT1 gain of function"}},"offset":0,"limit":3}'
# meta: {"search_id":"hsvM8Uq3M06EHdOGjeKNCA","total":289,"offset":0,"limit":3,"sort_field":null,"sort_order":"ASC","sorted_by_relevance":true,
#        "properties":{"URL":"https:/reporter.nih.gov/search/hsvM8Uq3M06EHdOGjeKNCA/projects"}}
# 4R00AI177758-02   FY2026 NIAID  PHILIPS, RACHAEL LAURA  Genomic regulation of immune response by a STAT1 gain of function mutation
# 2R01AR073938-06A1 FY2026 NIAMS  Mechanistic insights into systemic lupus erythematosus (SLE) pathogenesis from inborn errors of immunity
# 5R01AI191107-02   FY2026 NIAID  Mechanisms of STAT1 and STAT3 canonical and persistent activation in physiology and disease
curl -sS -X POST "https://api.reporter.nih.gov/v2/publications/search" -H "Content-Type: application/json" \
  -d '{"criteria":{"core_project_nums":["R00AI177758"]},"offset":0,"limit":5}'
# → {"meta":{"search_id":null,"total":1,"offset":0,"limit":5,"sort_field":"coreproject","sort_order":"desc","sorted_by_relevance":false,"properties":{}},
#    "results":[{"coreproject":"R00AI177758","pmid":40446018,"applid":11458029}],"facet_results":[]}
```

Trimmed real project record (`appl_id` 11458029; 45 keys total):

```json
{"appl_id":11458029,"fiscal_year":2026,"project_num":"4R00AI177758-02","project_serial_num":"AI177758",
 "core_project_num":"R00AI177758","award_type":"4N","activity_code":"R00","award_amount":249000,"is_active":true,
 "project_title":"Genomic regulation of immune response by a STAT1 gain of function mutation",
 "contact_pi_name":"PHILIPS, RACHAEL LAURA",
 "principal_investigators":[{"profile_id":14185972,"first_name":"Rachael","middle_name":"Laura","last_name":"Philips","is_contact_pi":true,"full_name":"Rachael Laura Philips","title":"ASSISTANT PROFESSOR"}],
 "agency_ic_admin":{"code":"AI","abbreviation":"NIAID","name":"National Institute of Allergy and Infectious Diseases","admin_org_id":"12500003","admin_funding_url":""},
 "agency_ic_fundings":[{"fy":2026,"code":"AI","name":"...","abbreviation":"NIAID","total_cost":249000.0,"direct_cost_ic":158095.0,"indirect_cost_ic":90905.0}],
 "organization":{"org_name":"OHIO STATE UNIVERSITY","city":null,"org_city":"Columbus","org_country":"UNITED STATES","org_state":"OH","dept_type":"MICROBIOLOGY/IMMUN/VIROLOGY", "...":"..."},
 "project_start_date":"2023-09-01T00:00:00","project_end_date":"2028-03-31T00:00:00","opportunity_number":"PAR-21-271",
 "project_detail_url":"https://reporter.nih.gov/project-details/11458029","date_added":"2026-04-04T17:04:38","cong_dist":"OH-03"}
```

Live totals for the four cluster topics (`operator:"and"`, `projecttitle,terms,abstracttext`): "STAT1 gain of function" **289** (advanced `STAT1 AND ("gain of function" OR "gain-of-function" OR GOF)`, `search_field:"all"` → 237; `operator:"and"` "STAT1 gain of function" with `search_field:"all"` + `fiscal_years:[2025,2026]` + `include_active_projects:true` → 62; bare `STAT1`, `search_field:"all"` → 3390) · "inborn errors of immunity" **545** (top: 7R01AI088364-17, 2R01AI088364-16A1, 7R01AI127564-11, all CASANOVA) · "chronic mucocutaneous candidiasis" **76** (exact phrase, `all` → 70; 7R01AI127564-11, 5R01AI127564-10, 1R21TR006398-01) · "JAK3 severe combined immunodeficiency" **192** (5U01AI184132-03, 3U01AI184132-03S1 AULETTA; 1ZIABC012039-05 PAI).

Request body keys (snake_case): `criteria`, `offset`, `limit`, `sort_field`, `sort_order`, `include_fields`, `exclude_fields`, `search_id` (re-fetch a previous search; resolved ≥25 min later; expiry UNVERIFIED). Verified criteria: `advanced_text_search{operator,search_field,search_text}`, `fiscal_years`, `include_active_projects`, `project_nums`, `appl_ids`, `exclude_subprojects`. Documented but UNVERIFIED live: `pi_profile_ids`, `pi_names`, `org_names`, date ranges, `project_nums` wildcard `*`, camelCase body keys.
Publications `criteria` accepts exactly `pmids` (`[int]`), `appl_ids` (`[int]`), `core_project_nums` (`[string]`); result rows `{coreproject, pmid, applid}` (no underscores; no title/journal — fetch from PubMed). PMID → grants: `{"criteria":{"pmids":[28139313]}}` → 5 rows (ZIACL010304/11411917, ZIAAI001222/11403365, ZIAAI001122/11403323, R56AI115699/9108510, R01AI085090/9984930). Three grants `["R01AI127564","R00AI177758","U01AI184132"]` → 124 rows (95/1/28).

### 3b. Field paths the pipeline needs

| Concept | Field | Notes |
|---|---|---|
| Record key | `appl_id` (int) | **not** `project_num` — P01 subprojects share `project_num` (487 distinct in a 500-row page) |
| Grant identity | `core_project_num` | dedupe key across fiscal years/types (`5R01AI127564-10` and `7R01AI127564-11` are one grant) |
| Title / abstract / relevance | `project_title`, `abstract_text`, `phr_text` | strings with `\n` |
| PIs | `principal_investigators[].{profile_id,first_name,middle_name,last_name,is_contact_pi,full_name,title}` | **no ORCID**; `profile_id` is the only stable id; build display names from first/middle/last with whitespace collapsing (`full_name` has double/triple/trailing spaces) |
| Contact PI (flat) | `contact_pi_name` | `LAST, FIRST MIDDLE` uppercase; dirty (trailing space in 20/50 records, `" PAPPAS , IOANNIS "`) — prefer `principal_investigators[is_contact_pi]` |
| Funder | `agency_ic_admin.{code,abbreviation,name}`, `agency_ic_fundings[].{fy,abbreviation,total_cost}`, `agency_code` | |
| Organization | `organization.{org_name,org_city,org_state,org_country,dept_type,primary_uei}` | bare `city`/`country` were `null` |
| Money / dates / flags | `award_amount`, `direct_cost_amt`, `indirect_cost_amt`, `fiscal_year`, `project_start_date`, `project_end_date`, `award_notice_date`, `date_added`, `is_active`, `is_new` | ISO datetimes without TZ |
| Mechanism | `award_type`, `activity_code`, `funding_mechanism`, `mechanism_code_dc`, `opportunity_number` (RFA/PAR signal) | |
| Terms | `terms` (angle-bracket delimited), `pref_terms` (`;` delimited) | |
| Source link | `project_detail_url` | well-formed; `meta.properties.URL` is malformed (`https:/…`) |
| Publications | none on project record | hop via `core_project_num` → `/v2/publications/search` |

`include_fields`/`exclude_fields` values must be **PascalCase**: verified `ApplId, ProjectNum, ProjectTitle, FiscalYear, CoreProjectNum, ContactPiName, PrincipalInvestigators, AgencyIcAdmin, IsActive, AwardAmount, SubprojectId, AbstractText, PhrText, Terms, PrefTerms, Organization, AgencyIcFundings, FullStudySection, ProjectNumSplit, GeoLatLon, ProgramOfficers`. The full field-name mapping is in the Data Elements PDF linked above.

### 3c. Gotchas

1. **snake_case body keys, PascalCase `include_fields` values.** Swagger documents camelCase keys (`includeFields`) — misleading. snake_case values in `include_fields` are silently accepted and return `{}` for every record.
2. **`appl_id` vs `core_project_num`**: totals count project-year (and subproject) records, not grants. Use `"exclude_subprojects": true` in text searches (verified: 23 → 3 for three P01 numbers). `applid` in publications is the *latest* application of the grant.
3. **No ORCID** anywhere (swagger, docs, 76 PI objects). Investigators = name + affiliation, flag `low` per README unless matched elsewhere.
4. **≤1 req/s** per docs (and large jobs weekends or 9 PM–5 AM EST; IP block possible). Headers `x-rate-limit-limit: 1m`, `x-rate-limit-remaining` (199 after one call ⇒ ~200/min per bucket; several load-balanced buckets observed). Budget 200/min, sleep ≥1 s.
5. **Caps**: projects `limit` ≤500 (501 → 400 `{"message":"System doesn't support limit value greater than 500. Please reduce your limit value."}`), `offset` ≤14,999 (→ at most 15,499 records per query; split by `fiscal_years`); publications `limit` ≤500, `offset` ≤9,999. Default `limit` 50 (swagger's "System imposed limit is 50" is stale).
6. `criteria.publications_search` inside project search is accepted but **ignored** (total 2,982,395 = whole DB). Use `/v2/publications/search`.
7. Zero hits → HTTP 200 with `meta.total: 0`, not 404. Error bodies: cap errors `{"message":…}`; type errors a JSON **array of strings**, HTML-entity-escaped (`&quot;`, `&#39;`) → `json.loads` then `html.unescape`.
8. `"and"` is bag-of-words across title+abstract+terms; use quoted phrases (`"\"chronic mucocutaneous candidiasis\""`) or `operator:"advanced"` for precision. Results are relevance-ranked (`sorted_by_relevance: true`) even without `use_relevance`.

### 3d. Rate limits / terms

- Docs: "no more than one URL request per second"; failure may lead to IP block. Observed per-bucket 200 requests/min. Separate RePORTER limiter at 1 req/s in `pipeline/cache.py`.
- License: docs say "The RePORTER database is available to all public users" and link a "Data Access Policy" whose `href` is filled by JS (not followed — UNVERIFIED). README row: "NIH RePORTER API v2, retrieved 2026-10-03, public/NIH terms"; link the policy once read. Support `RePORT@mail.nih.gov`.

---

## 4. HPO / MONDO / HGNC / Reactome / GO files

### 4a. Files, URLs, versions, sizes (all HEAD/Range-verified)

| File | URL (start here; GitHub assets redirect via a signed `release-assets.githubusercontent.com` URL that expires in ~1 h — never cache it) | Bytes | Version observed |
|---|---|---|---|
| `phenotype.hpoa` | `https://purl.obolibrary.org/obo/hp/hpoa/phenotype.hpoa` → `https://github.com/obophenotype/human-phenotype-ontology/releases/download/v2026-09-01/phenotype.hpoa` | 35,816,037 | `#version: 2026-09-02`, release `v2026-09-01` |
| `genes_to_phenotype.txt` | `.../releases/download/v2026-09-01/genes_to_phenotype.txt` | 20,821,704 | same release |
| `phenotype_to_genes.txt` | `.../releases/download/v2026-09-01/phenotype_to_genes.txt` | 67,246,520 | same (skip; recomputable) |
| `genes_to_disease.txt` | `.../releases/download/v2026-09-01/genes_to_disease.txt` | 1,492,039 | same |
| `hp.json` | `https://purl.obolibrary.org/obo/hp.json`; pinned `http://purl.obolibrary.org/obo/hp/releases/2026-09-01/hp.json` | 21,915,519 | `2026-09-01` |
| `hp.obo` | `https://purl.obolibrary.org/obo/hp.obo` | 10,863,613 | `data-version: hp/releases/2026-09-01` |
| `mondo.json` / `mondo.obo` | `https://purl.obolibrary.org/obo/mondo.json` / `.obo` → `https://github.com/monarch-initiative/mondo/releases/download/v2026-09-01/…` | 107,586,061 / 53,134,854 | `v2026-09-01` (OMIM source 2026-08-12, ORDO 4.7, DOID 2026-07-31) |
| `mondo.sssom.tsv` | `https://purl.obolibrary.org/obo/mondo/mappings/mondo.sssom.tsv` → `https://raw.githubusercontent.com/monarch-initiative/mondo/master/src/ontology/mappings/mondo.sssom.tsv` | 13,113,756 | **master, not release-pinned**; sha256 `317ac4bf93be1deff4406b4d7d18bb730ead3c4f5c7c814f75d24be7f6ef302f` (2026-10-03) |
| `hgnc_complete_set.txt` | `https://storage.googleapis.com/public-download-files/hgnc/tsv/tsv/hgnc_complete_set.txt` | 16,963,116 | `Last-Modified` 2026-10-02 (no version string) |
| `ReactomePathways.txt` / `ReactomePathwaysRelation.txt` | `https://reactome.org/download/current/<file>` or `https://download.reactome.org/97/<file>` | 1,592,393 / 634,259 | Reactome v97 (`ContentService/data/database/version` → `97`) |
| `Ensembl2Reactome.txt` / `UniProt2Reactome.txt` | `https://download.reactome.org/97/<file>` (supports Range; `reactome.org/download/current/` ignores Range and has no Content-Length) | 183,277,348 / 43,041,375 | v97 |
| Reactome per-gene API | `https://reactome.org/ContentService/data/mapping/UniProt/{acc}/pathways?species=9606`, `.../mapping/ENSEMBL/{ENSG}/pathways?species=9606` | — | 31 pathways for STAT1 (P42224 / ENSG00000115415) |
| `goa_human.gaf.gz` | `https://current.geneontology.org/annotations/goa_human.gaf.gz` | 15,041,996 | GAF 2.2; `!date-generated: 2026-05-21T08:10`; Last-Modified 2026-05-28; GO `metadata/release-date.json` → 2026-08-05 |

```sh
curl -sL -r 0-16383 https://purl.obolibrary.org/obo/hp/hpoa/phenotype.hpoa | head -8
#description: "HPO annotations for rare diseases [8478: OMIM; 47: DECIPHER; 4357 ORPHANET]"
#version: 2026-09-02
#tracker: https://github.com/obophenotype/human-phenotype-ontology/issues
#hpo-version: http://purl.obolibrary.org/obo/hp/releases/2026-09-01/hp.json
database_id	disease_name	qualifier	hpo_id	reference	evidence	onset	frequency	sex	modifier	aspect	biocuration
OMIM:619340	Developmental and epileptic encephalopathy 96		HP:0011097	PMID:31675180	PCS		1/2			P	HPO:probinson[2021-06-21]
OMIM:619340	Developmental and epileptic encephalopathy 96		HP:0002187	PMID:31675180	PCS		1/1			P	HPO:probinson[2021-06-21]
```

Other verified one-liners: `curl -sL -r 21817215-21915518 https://purl.obolibrary.org/obo/hp.json` (edges live at the end; suffix ranges → 501) · `curl -sL -o mondo.sssom.tsv https://purl.obolibrary.org/obo/mondo/mappings/mondo.sssom.tsv` · `curl -s -H "Accept: application/json" https://rest.genenames.org/fetch/symbol/STAT1` (→ `HGNC:11362`, `ENSG00000115415`, `entrez_id 6772`, `uniprot_ids ['P42224']`, `omim_id ['600555']`) · `curl -sL -r 0-131071 -o goa_head.gz https://current.geneontology.org/annotations/goa_human.gaf.gz` then `zlib.decompressobj(16+zlib.MAX_WBITS)` · `curl -s "https://ontology.jax.org/api/network/annotation/OMIM:600802"` (per-disease QA; returns `disease.mondoId` `MONDO:0010938`; undocumented, terms/rate limits UNVERIFIED).

### 4b. Columns / field paths

- **`phenotype.hpoa`**: 4 `#` lines, header, 12 tab columns: `database_id, disease_name, qualifier, hpo_id, reference, evidence, onset, frequency, sex, modifier, aspect, biocuration`. `database_id` prefixes `OMIM:`, `ORPHA:`, `DECIPHER:` (docs' `MIM:`/`MONDO:` not seen in 18,294 rows). `evidence` ∈ `PCS` (published clinical study, PMID ref), `TAS` (traceable author statement), `IEA` (parsed from OMIM Clinical Features; OMIM reuse caveat). `aspect` ∈ `P` (HP:0000118), `C` (HP:0031797), `I` (HP:0000005), `M` (HP:0012823), `H` (HP:0032443). `qualifier` empty or `NOT`. `sex` empty, `MALE` or `FEMALE` (8 + 1 in sample). `biocuration` e.g. `HPO:probinson[2021-06-21]`, `ORPHA:orphadata[2026-09-02]`, `;`-separated history.
- **`frequency` — three formats, all present in the current file**: HP terms `HP:0040285` Excluded 0% · `HP:0040284` Very rare 1–4% · `HP:0040283` Occasional 5–29% · `HP:0040282` Frequent 30–79% · `HP:0040281` Very frequent 80–99% · `HP:0040280` Obligate 100%; counts `n/m` (`7/13`; `0/2` = looked for and absent); **percentages** `25%`, `90%` (10 rows, OMIM:194190 Wolf-Hirschhorn; docs call them deprecated) → parser must accept `^\d+%$`.
- **`reference`**: `PMID:`, `OMIM:`, `ORPHA:`, `DECIPHER:`, plus `http://…` URLs (42) and `ISBN-13:` (17), and `;`-separated lists (`ISBN-13:978-3-7945-2657-4;OMIM:194190`). Split on `;`; don't `split(':')[0]` blindly.
- `genes_to_phenotype.txt`: `ncbi_gene_id, gene_symbol, hpo_id, hpo_name, frequency, disease_id` — `ncbi_gene_id` bare integer, `frequency` `-` when absent. `genes_to_disease.txt`: `ncbi_gene_id, gene_symbol, association_type, disease_id, source` — `ncbi_gene_id` **has** `NCBIGene:` prefix; `association_type` ∈ `MENDELIAN, POLYGENIC, UNKNOWN`. `phenotype_to_genes.txt`: `hpo_id, hpo_name, ncbi_gene_id, gene_symbol, disease_id` (ancestors propagated; P-aspect only).
- **`hp.json`** (OBO Graphs): `graphs[0].{nodes[],edges[],meta}`; node `id` is IRI `http://purl.obolibrary.org/obo/HP_0000003` (→ `HP:` CURIE), `lbl`, `meta.definition.val`, `meta.synonyms[].{pred,val,synonymType}`, `meta.xrefs[].val` (`MEDDRA:`, `Fyler:`, `NCIT:` seen), `meta.basicPropertyValues[]` (`hasAlternativeId`; replaced-by `IAO_0100001`), `meta.deprecated: true` on obsolete terms. **Edge keys `sub` / `pred` / `obj`** (658 tail edges all `is_a`; other predicates UNVERIFIED). `hp.obo` stanza: `id:`, `name:`, `def:`, `synonym: "…" EXACT layperson []`, `is_a: HP:0001507 ! Growth abnormality`.
- **`mondo.sssom.tsv`**: 6 columns `subject_id, subject_label, predicate_id, object_id, object_label, mapping_justification`; YAML `curie_map` in `#` lines. 109,715 rows, 24,549 MONDO subjects; `skos:exactMatch` 109,626 / `skos:broadMatch` 89; OMIM 10,045 rows (unique target), Orphanet 9,785 (unique target), OMIMPS 615, **DECIPHER 0**. `object_label` may be empty. Cluster: MONDO:0013599 ↔ OMIM:614162 / Orphanet:391487 (STAT1 GoF); MONDO:0013956 ↔ OMIM:614892 / Orphanet:319595; MONDO:0013427 ↔ OMIM:613796 / Orphanet:391311; MONDO:0015279 ↔ Orphanet:1334 (CMC); MONDO:0010938 ↔ OMIM:600802 / Orphanet:35078 (JAK3 SCID); MONDO:0010315 ↔ OMIM:300400 / Orphanet:276 (X-SCID); MONDO:0007818 ↔ OMIM:147060 / Orphanet:2314; MONDO:0014414 ↔ OMIM:615952 / Orphanet:438159 (STAT3 GoF); MONDO:0009891 ↔ OMIM:263300 / Orphanet:729; MONDO:0005029 ↔ Orphanet:3318; MONDO:0009692 ↔ OMIM:254450 / Orphanet:824.
- `mondo.json` mappings live in `meta.xrefs[].val` (CURIEs; exactness qualifiers dropped), `meta.basicPropertyValues` with `pred = http://www.w3.org/2004/02/skos/core#exactMatch` (full IRIs; the exact ones), and `synonyms[].xrefs` (provenance only). First ~130 KB of nodes are HGNC stubs (`http://identifiers.org/hgnc/<n>`) — skip non-`MONDO_` ids. `mondo.obo` has `relationship: has_material_basis_in_germline_mutation_in http://identifiers.org/hgnc/5261` (second gene→disease source).
- **HGNC TSV**: 53 columns; key ones `1 hgnc_id, 2 symbol, 8 alias_symbol, 10 prev_symbol, 18 entrez_id, 19 ensembl_gene_id, 25 uniprot_ids, 31 omim_id, 36 orphanet, 52 mane_select`. Multi-values are pipe-separated **and double-quoted** (`"ACF|ASP|ACF64|ACF65|APOBEC1CF"`) → CSV reader with `quotechar='"'`. `entrez_id` = HPO `ncbi_gene_id`; `uniprot_ids` → GO col 2 / Reactome UniProt; `ensembl_gene_id` → Reactome Ensembl.
- **Reactome**: `ReactomePathways.txt` 3 cols (stable id, name, species; 23,603 rows, 2,883 human); `ReactomePathwaysRelation.txt` 2 cols (parent TAB child; 23,717 rows); `Ensembl2Reactome.txt` 6 cols no header (source id, pathway stable id, browser URL, pathway name, evidence `TAS`/`IEA`, species). API response keys: `className, dbId, displayName, hasDiagram, hasEHLD, isInDisease, isInferred, lastUpdatedDate, maxDepth, name, releaseDate, schemaClass, speciesName, stId, stIdVersion` (no evidence code).
- **GAF 2.2** (17 cols, `!` comments): `1 DB, 2 DB Object ID (UniProt acc), 3 Symbol, 4 Relation (NOT|…), 5 GO ID, 6 Reference, 7 Evidence Code, 8 With/From, 9 Aspect (P/F/C), 10 Name, 11 Synonym, 12 Type, 13 Taxon, 14 Date, 15 Assigned By, 16 Extension, 17 Gene Product Form ID`. Evidence: experimental `EXP, IDA, IPI, IMP, IGI, IEP` → high; `HTP, HDA, HMP, HGI, HEP, IBA, ISS, TAS` → medium; `IEA` → low. Filter col 9 == `P`, drop col 4 starting `NOT|`.

### 4c. Gotchas

1. **HPO license is custom, not CC BY**: free use on three conditions — cite the HPO Consortium, display the file version(s), do not alter content. Add "This service/product uses the Human Phenotype Ontology (version information). Find out more at http://www.human-phenotype-ontology.org" + logo. The license URL `https://hpo.jax.org/license` (and `/app/license`) returns **HTTP 404 that renders client-side** — cite it, don't `raise_for_status()` on it.
2. **IEA rows carry an OMIM reuse caveat** ("you need to contact OMIM in order to reuse these annotations in other software products"). IEA is ~15 % of OMIM-sourced rows (0 on ORPHA rows, 5/5 on DECIPHER). Decide explicitly: display only PCS/TAS edges, or drop IEA; record it in the README license table.
3. **`ORPHA:` vs `Orphanet:`**: hpoa writes `ORPHA:1334`, MONDO/SSSOM write `Orphanet:1334`. Normalise before joining. `OMIM:` matches. `DECIPHER:` (47 diseases) has no MONDO mapping.
4. **Use `mondo.sssom.tsv` for OMIM/Orphanet→MONDO** (12.5 MB), not `mondo.json` (103 MB). Both maps are functions (unique target). It lives on `master` — pin by sha256 + retrieval date.
5. **`hp.json` edge key is `sub`, not `subj`** (the obographs README writes `subj`). Filter `meta.deprecated == true`; follow `IAO_0100001` for replacements. `hp.obo` (10.4 MB) suffices for labels + `is_a`.
6. **hpoa layout**: `OMIM:` rows (with DECIPHER near 16 MB) fill bytes 0 → ~21 MB; `ORPHA:` rows ~22 MB → end. Range requests work (206) through the redirect chain.
7. **Reactome human block**: `Ensembl2Reactome.txt` is sorted by col 1, so human rows are one contiguous block at **bytes 40,875,195–49,383,298** (8,508,104 B) of `https://download.reactome.org/97/Ensembl2Reactome.txt` — one `curl -r 40875195-49383298`. Offsets are v97-specific (`last-modified: Wed, 24 Jun 2026`); recompute per release. Or use the per-gene API (7 calls for the cluster).
8. **No literal "JAK-STAT signaling" human pathway in Reactome v97.** Closest: `R-HSA-8950505` Gene and protein expression by JAK-STAT signaling after Interleukin-12 stimulation (← `R-HSA-9020591` ← `R-HSA-447115` ← `R-HSA-449147` Signaling by Interleukins ← `R-HSA-1280215` ← `R-HSA-168256`). Also `R-HSA-913531` Interferon Signaling (4 children incl. `R-HSA-877300`, `R-HSA-909733`), `R-HSA-451927`, `R-HSA-6783589`, `R-HSA-1266695`, `R-HSA-1059683`, `R-HSA-9645135`, `R-HSA-198745`, `R-HSA-8875791`. Model our own `Pathway` node linking to these with provenance.
9. IC over all diseases: 12,882 diseases; use `aspect == P`, propagate `is_a`, treat `qualifier == NOT`, `0/n` and `HP:0040285` as excluded; collapse OMIM/Orphanet duplicates via SSSOM if it matters.
10. GO dates disagree (release 2026-08-05 vs `goa_human` generated 2026-05-21, Last-Modified 2026-05-28) — record all three. `HUMAN-uniprot.gaf.gz` (12,109,254 B, 2026-08-08) is fresher; relationship between the two UNVERIFIED. `goa_human.gaf.gz` is not listed in the `annotations/` index — reach it by direct URL.
11. Still UNVERIFIED: full node/edge counts of `hp.json`/`mondo.json`; non-`is_a` predicates in `hp.json`; `mondo-rare.*` xref completeness; human evidence distribution across all of `Ensembl2Reactome.txt` (27 sampled: 19 TAS / 8 IEA); JAX API terms.

### 4d. Rate limits / licenses (quoted pages fetched 2026-10-03)

- No rate limits stated for any of these downloads; only HTTP etiquette. GitHub asset CDN rejects suffix ranges (501).
- HPO: custom license (above). MONDO: CC BY 4.0 (`LICENSE` "Attribution 4.0 International"; cite Vasilevsky et al., Genetics 2026, iyaf215). HGNC: CC0 ("any form of reuse of the content is permitted"; attribution recommended). Reactome: data CC0 ("Attribution is encouraged but not required"; illustrations CC BY 4.0, software Apache 2.0). GO: CC BY 4.0 ("the particular release is mentioned"; cite Ashburner 2000 + GO Consortium NAR `gkaf1292`, dated 2025 Dec 18 on the citation page).

---

## 5. Summary

| Source | Version / date observed (2026-10-03) | License / terms | What we use it for |
|---|---|---|---|
| ClinVar (E-utilities) | `Build260929-0200.1`; einfo lastupdate 2026/09/29; 6,924 cluster Variation IDs | Not captured; 3 req/s without key, 10 req/s with `api_key` | Variant nodes (id, HGVS, protein change, germline/somatic classification, review status, conditions with MONDO/OMIM/Orphanet/MedGen/HPO ids, xrefs, SCV/RCV); elink → PubMed |
| ClinicalTrials.gov API v2 | `apiVersion 2.0.5`, `dataTimestamp 2026-10-02T09:00:04`, `versionHolder 2026-10-02` | US Gov T&C (2023-01-31): attribute, keep current, show processed date, state modifications, **no email marketing**; no rate limit stated | Study nodes for the six cluster queries (incl. NCT07670156, NCT05561985, NCT07261891, NCT07262983); MeSH ids; PMID references |
| NIH RePORTER API v2 | API 2.0 (`api-supported-versions: 2.0`); docs last-modified 2026-09-25 | "available to all public users"; Data Access Policy UNVERIFIED; ≤1 req/s requested, ~200/min per bucket | Grant / investigator / funder nodes (`appl_id`, `core_project_num`, `profile_id`, `agency_ic_admin`), grant↔PMID edges via `/v2/publications/search` |
| HPO (`phenotype.hpoa`, gene files, `hp.json`/`hp.obo`) | release `v2026-09-01`; hpoa `#version: 2026-09-02` | HPO custom (cite, show version, don't alter; IEA rows: OMIM reuse caveat) | Disease–phenotype edges (12,882 diseases), phenotype IC, gene–disease edges, HPO labels/hierarchy |
| MONDO (`mondo.sssom.tsv`, `mondo.obo/.json`) | `v2026-09-01`; SSSOM from `master`, sha256 `317ac4bf…ef302f` | CC BY 4.0 | OMIM/Orphanet → MONDO disease-id resolution; optional labels/hierarchy |
| HGNC (`hgnc_complete_set.txt`) | Last-Modified 2026-10-02 | CC0 | Gene nodes; join key across NCBI/Ensembl/UniProt/OMIM ids |
| Reactome (v97 files + ContentService) | v97 | CC0 (data) | Pathway nodes/hierarchy; gene→pathway edges (per-gene API or human byte range) |
| GO (`goa_human.gaf.gz`) | GAF 2.2, generated 2026-05-21; GO release 2026-08-05 | CC BY 4.0 | GO process–gene edges; evidence code → confidence |
