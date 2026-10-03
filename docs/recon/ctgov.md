# ClinicalTrials.gov API v2 — reconnaissance notes

**Scope:** JAK-STAT MVP (README §5 "Studies" row; test-cluster-desc §4 "check ClinicalTrials.gov directly", §10 open item).
**Recon date:** 2026-10-03 (UTC ~19:20–19:30). All commands below were run live on that date; all URLs, parameter names, field names and enum values were observed in a response or in the fetched OpenAPI spec / docs pages. Anything not observed is marked **UNVERIFIED**.

```
$ curl -s https://clinicaltrials.gov/api/v2/version
{ "apiVersion" : "2.0.5", "dataTimestamp" : "2026-10-02T09:00:04" }
```

Raw captures saved next to this file: `oas_v2.yaml` (OpenAPI spec), `metadata.json`, `search_areas.json`, `enums.json`, `study_NCT05561985.json`, `study_NCT07261891.json`, `study_NCT07670156.json`, `study_NCT07262983.json`, `trimmed_NCT07670156.json`, `headers_single.txt`, `csv_page.csv`.

---

## 0. Verdict for the pipeline

Usable as planned, no key needed. The required searches work and give small, inspectable result sets. **The headline result for the cluster doc's open question: there is no ruxolitinib trial in STAT1 GoF (0 hits for `query.intr=ruxolitinib` + `query.term=STAT1`, and 0 for `query.term=ruxolitinib AND STAT1`), but there ARE three JAK-inhibitor studies touching STAT1 GoF**: NCT07670156 (upadacitinib, Phase 1/2 basket trial, NOT_YET_RECRUITING, conditions list literally `"STAT1 GOF"`, `"STAT3 GOF"`, `"JAK1 GOF"`, ...), NCT05561985 (itacitinib, single-patient EXPANDED_ACCESS, AVAILABLE), NCT07261891 (ex vivo JAK-inhibitor evaluation, RECRUITING). Plus NCT07262983 (baricitinib, Phase 1, STAT3 hyper-IgE / Job syndrome). So the README's "no controlled trial" framing must change to "one Phase 1/2 trial registered 2026, not yet recruiting; no completed/controlled trial with results".

Biggest gotcha: **the default search expansion is `Relaxation`**, so bare gene symbols are fuzzy-matched (`query.cond=STAT1` returned capillary potassium-test studies, apparently via "STAT"). Use `EXPANSION[None]` (case-sensitive exact) or `EXPANSION[Term]` for gene symbols, and keep quoted phrases for disease names (see §1.6).

---

## 1. Docs and the studies search endpoint

### 1.1 Where the real docs are

| What | URL | Observed |
| --- | --- | --- |
| OpenAPI 3.0.3 spec (YAML, the authoritative parameter reference) | `https://clinicaltrials.gov/api/oas/v2` | HTTP 200, `content-type: application/yaml`, 80,983 bytes, `info.version: "2.0.5"`, `servers[0].url: https://clinicaltrials.gov/api/v2` |
| API landing page | `https://clinicaltrials.gov/data-api/api` | Angular SPA; `curl` returns a 94,323-byte shell identical (same md5) for every docs URL. Rendered in a browser it shows: notice about modernized ingest (Aug 26 2025), data refresh schedule, links |
| About API index | `https://clinicaltrials.gov/data-api/about-api` | SPA; links to Migration Guide, Study Data Structure, Search Areas, CSV Download, RIS Download |
| Migration guide | `https://clinicaltrials.gov/data-api/about-api/api-migration` | SPA; endpoint mapping classic→v2, enum tables, date formats |
| Essie query syntax | `https://clinicaltrials.gov/find-studies/constructing-complex-search-queries` | SPA; operators AND/OR/NOT, quotes, parentheses, `AREA[]`, `SEARCH[]`, `COVERAGE[]`, `EXPANSION[]`, `MISSING`, `RANGE[]`, `DISTANCE[]`, `ALL`, `TILT[]` |
| Terms and Conditions | `https://clinicaltrials.gov/about-site/terms-conditions` | SPA; full text in §4 |
| Field-name reference (machine readable) | `https://clinicaltrials.gov/api/v2/studies/metadata` | HTTP 200, 175,633 bytes JSON (list of 6 top-level sections, nodes have `name`, `piece`, `title`, `sourceType`, `type`, `dedLink`, `children`) |
| Search areas (what each `query.*` searches) | `https://clinicaltrials.gov/api/v2/studies/search-areas` | HTTP 200, 25,359 bytes |
| Enum values | `https://clinicaltrials.gov/api/v2/studies/enums` | HTTP 200, 19,945 bytes (41 enums; items have `type`, `values[].value`, `pieces`) |

Non-OpenAPI bad-path probes (all 404): `/api/oas/v2.yaml`, `/api/oas/v2.json`, `/api/v2/openapi.json`.

### 1.2 All endpoints in the spec (`paths:` of `oas_v2.yaml`)

```
/studies              GET  operationId listStudies
/studies/{nctId}      GET  operationId fetchStudy        (200, 301, 400, 404)
/studies/metadata     GET  operationId studiesMetadata   (params includeIndexedOnly, includeHistoricOnly)
/studies/search-areas GET  operationId searchAreas
/studies/enums        GET  operationId enums
/stats/size           GET  operationId sizeStats
/stats/field/values   GET  operationId fieldValuesStats  (params types, fields)
/stats/field/sizes    GET
/version              GET  operationId version
```
The migration guide also mentions `/api/v2/studies/download?format=json.zip` (whole-database zip). Not in the spec's path list I parsed and **not tested** (too large) — **UNVERIFIED**, and not needed.

`/stats/size` (observed): `{"totalStudies":605599,"averageSizeBytes":17301,"percentiles":{...,"50%":9856,...,"99%":149539},...}` — a full study record is ~10 KB median, so fetching every hit in full is cheap.

### 1.3 `GET /studies` query parameters (from the spec; names exact)

| Param | Type / notes (spec text) |
| --- | --- |
| `format` | `csv` or `json` (default `json`) |
| `markupFormat` | `markdown` (default) or `legacy`; applies to `markup`-type fields such as `eligibilityCriteria` |
| `query.cond` | "Conditions or disease", Essie syntax → ConditionSearch area |
| `query.term` | "Other terms", Essie syntax → BasicSearch area (example in spec: `AREA[LastUpdatePostDate]RANGE[2023-01-15,MAX]`) |
| `query.intr` | "Intervention / treatment" → InterventionSearch area |
| `query.locn`, `query.titles`, `query.outc`, `query.spons`, `query.lead`, `query.id`, `query.patient` | other search areas (not needed for us) |
| `filter.overallStatus` | comma- or pipe-separated list of `Status` enum values, e.g. `RECRUITING,NOT_YET_RECRUITING` |
| `filter.ids` | comma/pipe list of NCT ids (pattern `^[Nn][Cc][Tt]0*[1-9]\d{0,7}$`), matched on `NCTId` and `NCTIdAlias` |
| `filter.advanced` | Essie expression, e.g. `AREA[StartDate]2022`, `AREA[MinimumAge]RANGE[MIN, 16 years]` |
| `filter.geo`, `filter.synonyms`, `postFilter.*`, `aggFilters`, `geoDecay` | not needed |
| `fields` | comma- or pipe-separated list; each item is an area name, **piece name** (short name, e.g. `NCTId`), full dotted field path, or special name; a branch node returns all descendants. Pattern `^([a-zA-Z][a-zA-Z0-9\-. ]*)|(@query)$` |
| `sort` | list like `LastUpdatePostDate`, `EnrollmentCount:desc`, or `@relevance`; only date/numeric fields sortable; **results are unsorted by default** |
| `countTotal` | boolean, default `false`; returns `totalCount` on the first page only (CSV: `x-total-count` header) |
| `pageSize` | integer, default 10, coerced down to **1000** max |
| `pageToken` | value of `nextPageToken` from the previous page; subsequent pages must repeat all other params except `countTotal`, `pageSize`, `pageToken` |

Spec notes worth keeping: "If any query parameter contains only NCT IDs (comma- and/or space-separated), filters are ignored." "A page may have empty `studies` array." "The last page will not contain `nextPageToken`."

### 1.4 Observed pagination / error behaviour

```
# page 1
curl -sG 'https://clinicaltrials.gov/api/v2/studies' \
  --data-urlencode 'query.cond="hyper IgE syndrome"' --data-urlencode 'fields=NCTId' \
  --data-urlencode 'pageSize=5' --data-urlencode 'countTotal=true'
→ totalCount 14, 5 ids, nextPageToken "ZVNj7o2Elu8o3lpwTcito62tmpOQJJxuZvWs1PcckA"
# page 2 (same params + pageToken)
→ totalCount ABSENT (None), 5 ids, nextPageToken "ZVNj7o2Elu8o3lpwTcito62tmpOQJJxtYvSl3PcdkA"
```
- A `nextPageToken` was returned whenever the page was full (even `pageSize=1` with a single-NCT query), so loop until the token is absent and tolerate an empty final `studies` array.
- Query with 5 hits and `pageSize=50` → `nextPageToken` absent (correct last page).
- `filter.ids=NCT05561985,NCT07261891,NCT07670156` → `totalCount 3` (handy for re-fetching a known set in one call).
- `filter.overallStatus=RECRUITING,NOT_YET_RECRUITING` on the hyper-IgE query → 2 of 14.
- `filter.advanced=AREA[StudyType]INTERVENTIONAL` + `sort=@relevance` → 10 of 14.
- CSV: `format=csv&fields=NCT Number,Study Title,Study Status&pageSize=3&countTotal=true` → headers `content-type: text/csv`, `x-total-count: 14`, `x-next-page-token: ...`; body header row `NCT Number,Study Title,Study Status`. **CSV column names differ from JSON piece names.**
- Errors are **plain text, not JSON**:
  - `GET /studies?pageSize=abc` → HTTP 400 `Value provided in parameter `pageSize` cannot be converted to 32-bit integer`
  - `GET /studies/NCT07261891?fields=NotARealField` → HTTP 400 `Parameter 'fields' contains invalid field name: 'NotARealField'`
  - `GET /studies/NCT04409146` (nonexistent) → HTTP 404 `NCT number NCT04409146 not found`

### 1.5 The six required searches (exact commands, live totals, NCT ids)

Common tail used on every call: `--data-urlencode 'fields=NCTId,BriefTitle,OverallStatus' --data-urlencode 'countTotal=true' --data-urlencode 'pageSize=50'`. Status is `protocolSection.statusModule.overallStatus` as returned.

**Q1 — ruxolitinib + STAT1 → 0 (zero is the result)**
```
curl -sG 'https://clinicaltrials.gov/api/v2/studies' --data-urlencode 'query.intr=ruxolitinib' --data-urlencode 'query.term=STAT1' ...
→ {"totalCount":0,"studies":[]}
```
Also 0 for the single-expression form `--data-urlencode 'query.term=ruxolitinib AND STAT1'` and for `query.cond="primary immunodeficiency"` + `query.intr=ruxolitinib`.

**Q2 — "chronic mucocutaneous candidiasis" → 5**
```
curl -sG 'https://clinicaltrials.gov/api/v2/studies' --data-urlencode 'query.cond="chronic mucocutaneous candidiasis"' ...
NCT01842386 | COMPLETED   | Rituximab for Anti-cytokine Autoantibody-Associated Diseases
NCT03059992 | COMPLETED   | Study to Evaluate the Efficacy and Safety of Ibrexafungerp in Patients With Fungal Disease
NCT02629419 | COMPLETED   | CAMB/MAT2203 in Patients With Mucocutaneous Candidiasis
NCT03800056 | TERMINATED  | Characterization of the Fungal Origins in the Autoimmune Polyendocrinopathy of Type 1 ...
NCT01386437 | RECRUITING  | Natural History of Individuals With Immune System Problems That Lead to Fungal Infections
```

**Q3 — "STAT1 gain of function" → 3**
```
curl -sG 'https://clinicaltrials.gov/api/v2/studies' --data-urlencode 'query.term="STAT1 gain of function"' ...
NCT07261891 | RECRUITING          | Ex Vivo Evaluation of JAK-inhibitor and Gene Therapeutical Approach in JAK-STAT Related Disorders
NCT05561985 | AVAILABLE           | Expanded Access Use of Itacitinib to Treat a Single Patient With STAT1 Gain of Function (GOF) Disease
NCT07670156 | NOT_YET_RECRUITING  | Upadacitinib in Treatment of JAK/STAT Pathway Disorders With Activating Mutations
```

**Q4 — JAK inhibitor + "primary immunodeficiency" → 1**
```
curl -sG 'https://clinicaltrials.gov/api/v2/studies' --data-urlencode 'query.intr=JAK inhibitor' --data-urlencode 'query.cond="primary immunodeficiency"' ...
NCT07261891 | RECRUITING | Ex Vivo Evaluation of JAK-inhibitor and Gene Therapeutical Approach in JAK-STAT Related Disorders
```

**Q5 — "hyper IgE syndrome" → 14**
```
curl -sG 'https://clinicaltrials.gov/api/v2/studies' --data-urlencode 'query.cond="hyper IgE syndrome"' ...
NCT00005933 | COMPLETED               | Learning and Behavior Problems in Children With Chronic Granulomatous Disease and Related ...
NCT00006150 | RECRUITING              | Natural History, Management, and Genetics of the Hyperimmunoglobulin E Recurrent Infection Syndrome (HIES)
NCT00527878 | TERMINATED              | Effect of Ranitidine on Hyper-IgE Recurrent Infection (Job's) Syndrome
NCT00260702 | COMPLETED               | Omalizumab to Treat Hyper-IgE (Job's) Syndrome
NCT00033982 | COMPLETED               | Posaconazole to Treat Invasive Fungal Infections
NCT02996448 | TERMINATED              | Safety, Tolerability, and Immunogenicity of One Dose of NDV 3A Vaccine in People With STAT...
NCT01852370 | ENROLLING_BY_INVITATION | Sequential Cadaveric Lung and Bone Marrow Transplant for Immune Deficiency Diseases
NCT02228941 | UNKNOWN                 | NFIL3-induced Pathological Enhancement of IgE Class Switch Recombination in Hyper-IgE Synd...
NCT04897113 | UNKNOWN                 | Study of Efficacy and Safety of the Plasmapheresis Method With Albumin Compensation ...
NCT02629419 | COMPLETED               | CAMB/MAT2203 in Patients With Mucocutaneous Candidiasis
NCT01953016 | COMPLETED               | Participation in a Research Registry for Immune Disorders
NCT00001515 | COMPLETED               | Diagnostic Effectiveness of Virtual Bronchoscopy
NCT01176006 | ACTIVE_NOT_RECRUITING   | Pilot Study of Reduced-Intensity Hematopoietic Stem Cell Transplant of DOCK8 Deficiency
NCT07262983 | RECRUITING              | Evaluating the Safety and Tolerability of Baricitinib in Patients With Job Syndrome With L...
```

**Q6 — "severe combined immunodeficiency" + JAK3 → 3**
```
curl -sG 'https://clinicaltrials.gov/api/v2/studies' --data-urlencode 'query.cond="severe combined immunodeficiency"' --data-urlencode 'query.term=JAK3' ...
NCT03597594 | ACTIVE_NOT_RECRUITING | Haplocompatible Transplant Using TCRα/β Depletion Followed by CD45RA-Depleted Donor Lympho...
NCT00006319 | UNKNOWN               | Molecular and Clinical Studies of Primary Immunodeficiency Diseases
NCT03619551 | ACTIVE_NOT_RECRUITING | Conditioning SCID Infants Diagnosed Early
```

**Extra variants run (same tail), for query design:**

| Query | totalCount | Note |
| --- | --- | --- |
| `query.intr=ruxolitinib` + `query.cond=candidiasis` | 1 | NCT05398809 (ruxolitinib for hair regrowth, candidiasis matched loosely) — noise |
| `query.term=AREA[InterventionName]ruxolitinib AND AREA[Condition]candidiasis` | 1 | same NCT05398809; shows `AREA[]` works inside `query.term` |
| `query.cond=STAT1` | 14 | includes unrelated capillary potassium/calcium test studies → Relaxation-expansion noise |
| `query.cond=EXPANSION[None]STAT1` | 8 | noise gone; keeps NCT07261891, NCT05561985, NCT07670156, NCT02629419, NCT00001467 ... |
| `query.term=STAT1` | 60 | STAT1 appears as a biomarker in many unrelated trials |
| `query.term=EXPANSION[None]STAT1` | 46 | still biomarker-heavy; `query.term` is the wrong area for gene-as-disease |
| `query.term=EXPANSION[Term]STAT1` | 48 | |
| `query.term="STAT3 gain of function"` | 1 | NCT07670156 |
| `query.term=JAK1 AND "gain of function"` | 0 | |
| `query.term=EXPANSION[None]JAK3` | 35 | mostly JAK3-inhibitor pharmacology, not JAK3 deficiency |
| `query.term=EXPANSION[None]IL2RG` | 10 | includes X-SCID gene-therapy trials NCT04286815, NCT03601286, NCT06851767, NCT03619551, NCT03597594 |
| `query.cond="severe combined immunodeficiency"` + `query.term=IL2RG` | 101 | identical count to the cond-only query (101) → `query.term=IL2RG` did not narrow; treat with suspicion |
| `query.term=TYK2` | 43 | TYK2-inhibitor psoriasis trials, not TYK2 deficiency |
| `query.intr="JAK inhibitor"` (no cond) | 297 | |
| `query.cond="myeloproliferative neoplasm"` + `query.intr=ruxolitinib` | 175 | README caps MPN literature; do the same here (keep counts only, or filter `phases`/status) |
| `query.cond="inborn errors of immunity" OR "primary immunodeficiency" OR "immune dysregulation"` + `query.intr="JAK inhibitor" OR ruxolitinib OR baricitinib OR tofacitinib OR upadacitinib OR itacitinib` | 4 | NCT06199427 (GVHD prophylaxis, noise), NCT07261891, NCT03922724 (lymphoma, noise), NCT07670156 |
| `query.cond=EXPANSION[None]"chronic mucocutaneous candidiasis"` | 1 | too strict: `None` is case-sensitive, drops "Chronic Mucocutaneous Candidiasis" records |

### 1.6 Essie syntax facts observed on the docs page (needed to write queries correctly)

- Precedence high→low: terms/source operators; `NOT` and context operators; `AND`; `OR`. Quotes `" "` force a phrase; parentheses group.
- Context operators go in square brackets with no space before the operand: `AREA[InterventionName]aspirin`, `EXPANSION[None]SLE`, `COVERAGE[FullMatch]pain`.
- `EXPANSION` choices: `None` (exact, case- and accent-sensitive), `Term` (plurals/case/hyphens), `Concept` (UMLS synonyms), `Relaxation` (**default**; partial-term matches), `Lossy`. A quoted phrase after `EXPANSION[Relaxation]` behaves like `EXPANSION[Concept]`.
- `COVERAGE` default `Contains`; `FullMatch`/`StartsWith`/`EndsWith` only with `EXPANSION[Concept|Term|None]`.
- The API landing page states: "the COVERAGE and EXPANSION operators are not fully implemented on the modernized ClinicalTrials.gov." Our `EXPANSION[None]` tests did change counts, so it is at least partly effective.
- `RANGE[min,max]`, `MISSING`, `ALL`, `TILT[field]` exist; the spec's own example is `AREA[LastUpdatePostDate]RANGE[2023-01-15,MAX]` in `query.term`.

### 1.7 What each `query.*` area actually searches (from `/studies/search-areas`, document `Study`)

- `query.cond` → area `ConditionSearch`, pieces: `Condition`, `BriefTitle`, `OfficialTitle`, `ConditionMeshTerm`, `ConditionAncestorTerm`, `Keyword`, `NCTId` (7).
- `query.intr` → area `InterventionSearch`, pieces: `InterventionName`, `InterventionType`, `ArmGroupType`, `InterventionOtherName`, `BriefTitle`, `OfficialTitle`, `ArmGroupLabel`, `InterventionMeshTerm`, `Keyword`, `InterventionAncestorTerm`, `InterventionDescription`, `ArmGroupDescription` (12).
- `query.term` → area `BasicSearch`, 57 pieces incl. `NCTId`, `Acronym`, `BriefTitle`, `OfficialTitle`, `Condition`, `InterventionName`, `InterventionOtherName`, `Phase`, `StdAge`, `PrimaryOutcomeMeasure`, `Keyword`, `BriefSummary`, `ArmGroupLabel`, `SecondaryOutcomeMeasure`, ... (this is why gene symbols in `query.term` hit biomarker mentions in summaries/outcomes).
- Other areas with a `param`: `TitleSearch`/`titles`, `LocationSearch`/`locn`, `OutcomeSearch`/`outc`, `SponsorSearch`/`spons`, `IdSearch`/`id`, `PatientSearch`/`patient`. Areas with no param (usable only via `AREA[...]`): `EligibilitySearch` (`EligibilityCriteria`, `StudyPopulation`), `ContactSearch`, `NCTIdSearch`, `InterventionNameSearch`, `ResponsiblePartySearch`, etc.

---

## 2. Response structure (exact JSON paths, from real records)

Top-level keys of a study object (both in `/studies` → `studies[]` and in `/studies/{nctId}`): `protocolSection`, `derivedSection`, `hasResults` (a `resultsSection` appears only when `hasResults` is true — **UNVERIFIED**, none of our four records had results).

`protocolSection` modules observed on interventional records: `identificationModule`, `statusModule`, `sponsorCollaboratorsModule`, `oversightModule`, `descriptionModule`, `conditionsModule`, `designModule`, `armsInterventionsModule`, `outcomesModule`, `eligibilityModule`, `contactsLocationsModule`, `referencesModule`, `ipdSharingStatementModule`. `derivedSection` modules observed: `miscInfoModule`, `conditionBrowseModule`, `interventionBrowseModule`.

Values below are from `GET /studies/NCT07670156`; the `piece` column is the short name accepted by `fields` (from `/studies/metadata`).

| Path | piece | Observed value / type |
| --- | --- | --- |
| `protocolSection.identificationModule.nctId` | `NCTId` | `"NCT07670156"` |
| `protocolSection.identificationModule.briefTitle` | `BriefTitle` | `"Upadacitinib in Treatment of JAK/STAT Pathway Disorders With Activating Mutations"` |
| `protocolSection.identificationModule.officialTitle` | `OfficialTitle` | string |
| `protocolSection.statusModule.overallStatus` | `OverallStatus` | `"NOT_YET_RECRUITING"` (enum `Status`) |
| `protocolSection.statusModule.startDateStruct` | `StartDateStruct` → `.date`=`StartDate`, `.type`=`StartDateType` | `{"date":"2026-10","type":"ESTIMATED"}` — **partial dates** (`yyyy`, `yyyy-MM`, `yyyy-MM-dd`) |
| `protocolSection.statusModule.primaryCompletionDateStruct` | `PrimaryCompletionDate` | `{"date":"2028-08","type":"ESTIMATED"}` |
| `protocolSection.statusModule.lastUpdatePostDateStruct` | `LastUpdatePostDateStruct` → `.date`=`LastUpdatePostDate` | `{"date":"2026-09-08","type":"ACTUAL"}` |
| `protocolSection.statusModule.lastUpdateSubmitDate` | — | `"2026-09-02"` |
| `protocolSection.statusModule.studyFirstPostDateStruct.date` | `StudyFirstPostDate` | `"2026-06-26"` |
| `protocolSection.sponsorCollaboratorsModule.leadSponsor` | `LeadSponsor` (`.name`=`LeadSponsorName`, `.class`) | `{"name":"Lisa Satter","class":"OTHER"}` (class enum `AgencyClass`) |
| `protocolSection.sponsorCollaboratorsModule.collaborators[]` | `.name`=`CollaboratorName` | `[{"name":"National Center for Advancing Translational Sciences (NCATS)","class":"NIH"},{"name":"AbbVie","class":"INDUSTRY"}]` |
| `protocolSection.sponsorCollaboratorsModule.responsibleParty` | `.type`=`ResponsiblePartyType` | `{"type":"SPONSOR_INVESTIGATOR","investigatorFullName":"Lisa Satter","investigatorTitle":"Associate Professor of Pediatrics","investigatorAffiliation":"Baylor College of Medicine"}` |
| `protocolSection.conditionsModule.conditions[]` | `Condition` (type `text[]`) | `["JAK1 GOF","STAT1 GOF","STAT3 GOF","STAT5B GOF","STAT6 GOF"]` — **free text, not coded** |
| `protocolSection.conditionsModule.keywords[]` | `Keyword` | `["Upadacitinib","JAK-STAT Pathway Disorders",...,"STAT1","STAT3",...]` |
| `protocolSection.designModule.studyType` | `StudyType` | `"INTERVENTIONAL"` (enum: `EXPANDED_ACCESS`, `INTERVENTIONAL`, `OBSERVATIONAL`) |
| `protocolSection.designModule.phases[]` | `Phase` (type `Phase[]`) | `["PHASE1","PHASE2"]` (enum: `NA`, `EARLY_PHASE1`, `PHASE1`..`PHASE4`) |
| `protocolSection.designModule.enrollmentInfo` | `EnrollmentInfo` → `.count`=`EnrollmentCount` (integer), `.type`=`EnrollmentType` | `{"count":30,"type":"ESTIMATED"}` |
| `protocolSection.designModule.designInfo.allocation` | `DesignAllocation` | `"RANDOMIZED"` |
| `protocolSection.designModule.designInfo.primaryPurpose` | `DesignPrimaryPurpose` | `"TREATMENT"` |
| `protocolSection.armsInterventionsModule.interventions[]` | `Intervention` → `.type`=`InterventionType`, `.name`=`InterventionName`, `.otherNames[]`, `.description`, `.armGroupLabels[]` | `[{"type":"DRUG","name":"Upadacitinib","otherNames":["Rinvoq"],...},{"type":"DRUG","name":"Placebo",...}]` |
| `protocolSection.eligibilityModule.eligibilityCriteria` | `EligibilityCriteria` (type `markup`, markdown by default) | `"Inclusion Criteria:\n\n* At least 30kg\n* Patients with a confirmed JAK/STATGOF mutation with evidence of immune dysregulation\n..."` |
| `protocolSection.eligibilityModule.sex` | `Sex` | `"ALL"` (enum `FEMALE`, `MALE`, `ALL`) |
| `protocolSection.eligibilityModule.minimumAge` | `MinimumAge` (type `NormalizedTime`) | `"12 Years"` (string with unit) |
| `protocolSection.eligibilityModule.maximumAge` | — | `"65 Years"` |
| `protocolSection.eligibilityModule.stdAges[]` | `StdAge` | `["CHILD","ADULT","OLDER_ADULT"]` |
| `protocolSection.eligibilityModule.healthyVolunteers` | — | `false` (boolean) |
| `protocolSection.contactsLocationsModule.overallOfficials[]` | `.name`=`OverallOfficialName`, `.affiliation`, `.role` | `[{"name":"Lisa F Satter, MD","affiliation":"Baylor College of Medicine","role":"STUDY_CHAIR"}]` |
| `protocolSection.contactsLocationsModule.locations[]` | `.facility`, `.city`, `.state`, `.zip`, `.country`, `.contacts[]`, `.geoPoint{lat,lon}` | first location facility `"Washington University in St.Louis"`; `contacts[]` carry names/phone/email — **do not harvest emails (T&C)** |
| `protocolSection.outcomesModule.primaryOutcomes[]` | `.measure`, `.description`, `.timeFrame` | present |
| `protocolSection.referencesModule.references[]` | `.pmid`, `.type`, `.citation` (type enum `ReferenceType`: `BACKGROUND`, `RESULT`, `DERIVED`) | absent on NCT07670156; module present on NCT07261891/NCT07262983 (**field names `pmid`/`citation` UNVERIFIED** — I only observed `ReferenceType` in metadata) |
| `derivedSection.conditionBrowseModule` | `.meshes[]{id,term}`, `.ancestors[]{id,term}` | `{"meshes":[{"id":"D007153","term":"Immunologic Deficiency Syndromes"}],"ancestors":[{"id":"D007154","term":"Immune System Diseases"}]}` — **MeSH ids for disease normalisation** |
| `derivedSection.interventionBrowseModule.meshes[]` | | `[{"id":"C000613732","term":"upadacitinib"}]` |
| `derivedSection.miscInfoModule.versionHolder` | | `"2026-10-02"` — the data-version date to display per T&C |
| `hasResults` | `HasResults` | `false` |

**Expanded-access records are sparse** (`GET /studies/NCT05561985`, 1,886 bytes): no `startDateStruct`, no `designModule.phases`, no `enrollmentInfo`; `designModule` = `{"studyType":"EXPANDED_ACCESS","nPtrsToThisExpAccNctId":2,"expandedAccessTypes":{"individual":true}}`; `eligibilityModule` = `{"eligibilityCriteria":"Inclusion Criteria:\n\n\\-\n\nExclusion Criteria:\n\n\\-","stdAges":[...]}` with **no `sex`, no `minimumAge`**; `statusModule.overallStatus` = `"AVAILABLE"` (an expanded-access status value). Every extractor field must be optional.

Trimmed real record (`trimmed_NCT07670156.json`; `eligibilityCriteria`, intervention and design descriptions truncated, location contacts removed):

```json
{
  "protocolSection": {
    "identificationModule": {
      "nctId": "NCT07670156",
      "orgStudyIdInfo": {
        "id": "H-52366"
      },
      "organization": {
        "fullName": "Baylor College of Medicine",
        "class": "OTHER"
      },
      "briefTitle": "Upadacitinib in Treatment of JAK/STAT Pathway Disorders With Activating Mutations",
      "officialTitle": "Safety and Efficacy of Upadacitinib in Treatment of JAK/STAT Pathway Disorders With Activating Mutations"
    },
    "statusModule": {
      "statusVerifiedDate": "2026-09",
      "overallStatus": "NOT_YET_RECRUITING",
      "startDateStruct": {
        "date": "2026-10",
        "type": "ESTIMATED"
      },
      "primaryCompletionDateStruct": {
        "date": "2028-08",
        "type": "ESTIMATED"
      },
      "completionDateStruct": {
        "date": "2028-08",
        "type": "ESTIMATED"
      },
      "studyFirstPostDateStruct": {
        "date": "2026-06-26",
        "type": "ACTUAL"
      },
      "lastUpdateSubmitDate": "2026-09-02",
      "lastUpdatePostDateStruct": {
        "date": "2026-09-08",
        "type": "ACTUAL"
      }
    },
    "sponsorCollaboratorsModule": {
      "responsibleParty": {
        "type": "SPONSOR_INVESTIGATOR",
        "investigatorFullName": "Lisa Satter",
        "investigatorTitle": "Associate Professor of Pediatrics",
        "investigatorAffiliation": "Baylor College of Medicine"
      },
      "leadSponsor": {
        "name": "Lisa Satter",
        "class": "OTHER"
      },
      "collaborators": [
        {
          "name": "National Center for Advancing Translational Sciences (NCATS)",
          "class": "NIH"
        },
        {
          "name": "AbbVie",
          "class": "INDUSTRY"
        }
      ]
    },
    "conditionsModule": {
      "conditions": [
        "JAK1 GOF",
        "STAT1 GOF",
        "STAT3 GOF",
        "STAT5B GOF",
        "STAT6 GOF"
      ],
      "keywords": [
        "Upadacitinib",
        "JAK-STAT Pathway Disorders",
        "Immunodeficiency",
        "Primary Immune Regulatory Disorder (PIRD)",
        "Inborn Error of Immunity (IEI)",
        "early onset autoimmunity",
        "autoimmune cytopenia",
        "immune dysregulation",
        "STAT1",
        "STAT3",
        "lymphoproliferation",
        "STAT5b",
        "STAT6",
        "JAK1"
      ]
    },
    "designModule": {
      "studyType": "INTERVENTIONAL",
      "phases": [
        "PHASE1",
        "PHASE2"
      ],
      "designInfo": {
        "allocation": "RANDOMIZED",
        "interventionModel": "PARALLEL",
        "primaryPurpose": "TREATMENT",
        "maskingInfo": {
          "masking": "TRIPLE",
          "maskingDescription": "Double Blinded",
          "whoMasked": [
            "PARTICIPANT",
            "INVESTIGATOR",
            "OUTCOMES_ASSESSOR"
          ]
        }
      },
      "enrollmentInfo": {
        "count": 30,
        "type": "ESTIMATED"
      }
    },
    "armsInterventionsModule": {
      "interventions": [
        {
          "type": "DRUG",
          "name": "Upadacitinib",
          "armGroupLabels": [
            "Upadacitinib (Drug)"
          ],
          "otherNames": [
            "Rinvoq"
          ]
        },
        {
          "type": "DRUG",
          "name": "Placebo",
          "armGroupLabels": [
            "Matching Placebo"
          ]
        }
      ]
    },
    "eligibilityModule": {
      "eligibilityCriteria": "Inclusion Criteria:\n\n* At least 30kg\n* Patients with a confirmed JAK/STATGOF mutation with evidence of immune dysregulation\n* Current disease status meeting criteria as defined in the disease scoring module\n* Expected survival of more than 12 months\n* Willingness to allow storage of biological samples for future research\n* Agreement to use highly effective contraception (for female participants)\n\n ...[truncated]",
      "healthyVolunteers": false,
      "sex": "ALL",
      "minimumAge": "12 Years",
      "maximumAge": "65 Years",
      "stdAges": [
        "CHILD",
        "ADULT",
        "OLDER_ADULT"
      ]
    },
    "contactsLocationsModule": {
      "overallOfficials": [
        {
          "name": "Lisa F Satter, MD",
          "affiliation": "Baylor College of Medicine",
          "role": "STUDY_CHAIR"
        }
      ],
      "locations": [
        {
          "facility": "Washington University in St.Louis",
          "city": "St Louis",
          "state": "Missouri",
          "country": "United States",
          "geoPoint": {
            "lat": 38.62727,
            "lon": -90.19789
          }
        }
      ]
    }
  },
  "derivedSection": {
    "miscInfoModule": {
      "versionHolder": "2026-10-02"
    },
    "conditionBrowseModule": {
      "meshes": [
        {
          "id": "D007153",
          "term": "Immunologic Deficiency Syndromes"
        }
      ],
      "ancestors": [
        {
          "id": "D007154",
          "term": "Immune System Diseases"
        }
      ]
    },
    "interventionBrowseModule": {
      "meshes": [
        {
          "id": "C000613732",
          "term": "upadacitinib"
        }
      ]
    }
  },
  "hasResults": false
}
```

---

## 3. Fetch one study by NCT id

```
GET https://clinicaltrials.gov/api/v2/studies/{nctId}
  params: format (csv | json | json.zip | fhir.json | ris; default json), markupFormat (markdown | legacy), fields
  responses: 200, 301 (nctId found in NCTIdAlias → redirect to actual study; UNVERIFIED, from spec only), 400, 404
```
Observed:
```
curl -s https://clinicaltrials.gov/api/v2/studies/NCT05561985        → HTTP 200, 1886 bytes, application/json (full record)
curl -s https://clinicaltrials.gov/api/v2/studies/NCT07670156        → HTTP 200, 16939 bytes
curl -s https://clinicaltrials.gov/api/v2/studies/NCT04409146        → HTTP 404, text/plain "NCT number NCT04409146 not found"
```
`fields` accepts three spellings, mixable, comma- or pipe-separated (all three observed to work):
```
# short piece names
.../studies?query.term=NCT05561985&fields=NCTId,BriefTitle,OverallStatus,Phase,Condition,InterventionName,InterventionType,StartDate,LastUpdatePostDate,LeadSponsorName,StudyType,EnrollmentCount,MinimumAge,Sex
# full dotted paths
.../studies/NCT07261891?fields=protocolSection.identificationModule.nctId,protocolSection.identificationModule.briefTitle,protocolSection.statusModule.overallStatus,protocolSection.designModule.phases,protocolSection.conditionsModule.conditions
→ {"protocolSection":{"identificationModule":{"nctId":"NCT07261891","briefTitle":"Ex Vivo Evaluation of JAK-inhibitor and Gene Therapeutical Approach in JAK-STAT Related Disorders"},"statusModule":{"overallStatus":"RECRUITING"},"conditionsModule":{"conditions":["Primary Immunodeficiency Diseases (PID)"]},"designModule":{"phases":["NA"]}}}
# module (branch) names → all descendants
.../studies/NCT07261891?fields=EligibilityModule
→ {"protocolSection":{"eligibilityModule":{"eligibilityCriteria":"Inclusion Criteria:\n\n* Cases (A): adult patients ...","healthyVolunteers":true,"sex":"ALL","minimumAge":"18 Years","stdAges":["ADULT","OLDER_ADULT"]}}}
```
The response keeps the nested shape; requested-but-absent fields are simply omitted (a module can come back as `{}`). Unknown names → HTTP 400 plain text. Suggested `fields` set for the pipeline (all names verified): `NCTId,BriefTitle,OfficialTitle,OverallStatus,StartDate,StartDateType,PrimaryCompletionDate,LastUpdatePostDate,StudyFirstPostDate,LeadSponsor,CollaboratorName,ResponsiblePartyType,Condition,Keyword,StudyType,Phase,EnrollmentInfo,DesignAllocation,DesignPrimaryPurpose,Intervention,EligibilityModule,OverallOfficialName,ReferenceType,HasResults` plus `derivedSection.conditionBrowseModule,derivedSection.interventionBrowseModule,derivedSection.miscInfoModule` (branch paths; the `Intervention` and `EligibilityModule` branches were observed to expand).

---

## 4. Rate limits and terms of use (as stated in the docs)

**Rate limit: none stated anywhere I could find.** Checked: the OpenAPI spec (grep for rate/limit/throttle/429 → only unrelated schema names), the API landing page, About API, the Migration Guide, the Terms and Conditions, and the main JS bundle (`main-NRLW7DCI.js`, 114 KB — no "rate limit"/"requests per" strings; the only API path string in it is `/api/int/access-check`). Response headers carry **no** `x-ratelimit*`/`retry-after` (observed full header set: `date`, `server: istio-envoy`, `strict-transport-security`, `x-frame-options`, `content-type`, `etag`, `x-envoy-upstream-service-time`, `set-cookie: ncbi_sid=...`, `vary`, `x-ua-compatible`, `x-xss-protection`, `via: 1.1 google`, `alt-svc`). No API key exists or is accepted. Practical implication: there is no documented budget; the pipeline should still throttle itself as a courtesy (our choice, not a rule — e.g. reuse the NCBI 3 req/s limiter already in `pipeline/cache.py`, since the host is NLM infrastructure) and cache every response.

**Data refresh (API landing page, last updated Aug 26 2025):** "Data on ClinicalTrials.gov is refreshed daily Monday through Friday, generally by 9 a.m. ET (14:00 UTC)... check the `dataTimestamp` field available at https://clinicaltrials.gov/api/v2/version". Observed `dataTimestamp: 2026-10-02T09:00:04`; each record's `derivedSection.miscInfoModule.versionHolder` was `"2026-10-02"`.

**Terms and Conditions (https://clinicaltrials.gov/about-site/terms-conditions, "Last updated on January 31, 2023"), paraphrased from the rendered page:**
- Apply to all ClinicalTrials.gov data regardless of format/method; downloading = acceptance.
- Data available to all requesters, inside and outside the US, at no charge.
- No warranties; no liability for use.
- In any publication or distribution you should: (1) attribute the source as ClinicalTrials.gov; (2) keep the data current; (3) clearly display the date the data were processed by ClinicalTrials.gov; (4) state any modifications made, with a complete description.
- You shall not assert proprietary rights to any portion of the database, nor represent it as other than a US Government database.
- **You shall not use any email addresses extracted from the database for marketing or other promotional purposes** (relevant to the README's outreach-draft feature: do not pull `locations[].contacts[].email` / `centralContacts[].email` into contact routes).
- Data carry an international copyright outside the US; some data may be subject to third-party copyright.
- Terms remain in effect as long as the user retains any data.

Pipeline mapping: store `retrieved_at`, `/version.dataTimestamp` and `versionHolder` on every Study node; show "Source: ClinicalTrials.gov, data as of <versionHolder>" in the UI; mark any LLM-extracted "mechanism" or eligibility summary as a modification.

---

## 5. Gotchas summary for the engineer

1. Default `EXPANSION[Relaxation]` makes bare gene symbols fuzzy; `query.cond=STAT1` → 14 with potassium-test noise, `query.cond=EXPANSION[None]STAT1` → 8 clean. `EXPANSION[None]` is case-sensitive, so **do not** apply it to quoted disease phrases (`"chronic mucocutaneous candidiasis"` drops from 5 to 1).
2. `query.term` searches 57 pieces including `BriefSummary` and outcome measures → gene symbols there mostly hit biomarker mentions (STAT1: 60). Use `query.cond` (+ `Keyword` is included) for gene-as-disease, `query.intr` for drugs.
3. Results are **unsorted** unless `sort` is given; `sort` only accepts date/numeric fields or `@relevance`.
4. `totalCount` is only on page 1; `nextPageToken` appears whenever a page is full, and a last page may be empty.
5. Errors (400/404) are plain text, not JSON; `fields` typos are 400s.
6. Expanded-access records lack `phases`, `enrollmentInfo`, `startDateStruct`, `sex`, `minimumAge`; `overallStatus` uses extra values (`AVAILABLE`, `NO_LONGER_AVAILABLE`, `TEMPORARILY_NOT_AVAILABLE`, `APPROVED_FOR_MARKETING`).
7. Dates are partial ISO strings (`"2026-10"`); ages are strings with units (`"12 Years"`); `eligibilityCriteria` is markdown with escaped characters (`\\<`, `\\-`).
8. `conditions[]` is free text (`"STAT1 GOF"`); the only coded disease ids are MeSH in `derivedSection.conditionBrowseModule.meshes[].id` (e.g. `D007153`) — no MONDO/Orphanet; map via MONDO xrefs downstream.
9. CSV column names (`NCT Number`, `Study Title`, `Study Status`) differ from JSON piece names; prefer JSON.
10. Email addresses in contact fields must not be used for outreach (T&C).

## 6. Enum values observed (`/studies/enums`)

- `Status` (pieces `OverallStatus`, `LastKnownStatus`): `ACTIVE_NOT_RECRUITING`, `COMPLETED`, `ENROLLING_BY_INVITATION`, `NOT_YET_RECRUITING`, `RECRUITING`, `SUSPENDED`, `TERMINATED`, `WITHDRAWN`, `AVAILABLE`, `NO_LONGER_AVAILABLE`, `TEMPORARILY_NOT_AVAILABLE`, `APPROVED_FOR_MARKETING`, `WITHHELD`, `UNKNOWN`
- `Phase`: `NA`, `EARLY_PHASE1`, `PHASE1`, `PHASE2`, `PHASE3`, `PHASE4` (`/stats/field/values?fields=Phase`: NA 238,061; PHASE2 90,442; PHASE1 65,890; PHASE3 49,967; PHASE4 35,897; EARLY_PHASE1 6,494; missing 143,573)
- `StudyType`: `EXPANDED_ACCESS`, `INTERVENTIONAL`, `OBSERVATIONAL`
- `InterventionType`: `BEHAVIORAL`, `BIOLOGICAL`, `COMBINATION_PRODUCT`, `DEVICE`, `DIAGNOSTIC_TEST`, `DIETARY_SUPPLEMENT`, `DRUG`, `GENETIC`, `PROCEDURE`, `RADIATION`, `OTHER`
- `Sex`: `FEMALE`, `MALE`, `ALL`; `StandardAge`: `CHILD`, `ADULT`, `OLDER_ADULT`
- `DateType` / `EnrollmentType`: `ACTUAL`, `ESTIMATED`
- `AgencyClass`: `NIH`, `FED`, `OTHER_GOV`, `INDIV`, `INDUSTRY`, `NETWORK`, `AMBIG`, `OTHER`, `UNKNOWN`
- `DesignAllocation`: `RANDOMIZED`, `NON_RANDOMIZED`, `NA`; `PrimaryPurpose`: `TREATMENT`, `PREVENTION`, `DIAGNOSTIC`, `ECT`, `SUPPORTIVE_CARE`, `SCREENING`, `HEALTH_SERVICES_RESEARCH`, `BASIC_SCIENCE`, `DEVICE_FEASIBILITY`, `OTHER`

---

## Re-run check (second reviewer)

**Re-run date:** 2026-10-03 (UTC ~19:40–19:50), same data version as the original (`/version` → `{"apiVersion":"2.0.5","dataTimestamp":"2026-10-02T09:00:04"}`; every record's `derivedSection.miscInfoModule.versionHolder` = `"2026-10-02"`). Raw re-run captures are in `rerun/` next to this file (`q1.json`…`q6.json`, `exp_none.json`, `pg1-3.json`, `s*.json`, `oas_v2_rerun.yaml`, `metadata.json`, `search_areas.json`, `enums.json`, `spa_*.html`, `main_bundle.js`, `alias_hdr.txt`, …).

**Verdict: every command listed under "verified" ran and matched.** No wrong counts, no wrong field paths, no redirected URLs, nothing that looks copied from memory. The four cached study records (`study_NCT05561985.json`, `study_NCT07261891.json`, `study_NCT07262983.json`, `study_NCT07670156.json`), `oas_v2.yaml` and `main.js` are byte-identical (md5) to what the server returns today. The SPA doc pages were rendered in a real browser this time, and the quoted/paraphrased text (T&C, landing-page notice, Essie operators) matches.

### R.1 Command-by-command result

| # | Command (as listed) | Original claim | Re-run result | Match |
| --- | --- | --- | --- | --- |
| 1 | `curl -s https://clinicaltrials.gov/api/v2/version` | 2.0.5 / 2026-10-02T09:00:04 | identical; HTTP 200 `application/json` | yes |
| 2 | `curl -s -o oas_v2.yaml https://clinicaltrials.gov/api/oas/v2` | 200, `application/yaml`, 80,983 B, `info.version "2.0.5"`, `servers[0].url https://clinicaltrials.gov/api/v2` | identical, 0 redirects, md5 `1ebdcf44…` same as cached | yes |
| 3 | Q1 `query.intr=ruxolitinib` + `query.term=STAT1` | 0 | `{"totalCount":0,"studies":[]}` | yes |
| 4 | Q2 `query.cond="chronic mucocutaneous candidiasis"` | 5 | 5: NCT01842386 COMPLETED, NCT03059992 COMPLETED, NCT02629419 COMPLETED, NCT03800056 TERMINATED, NCT01386437 RECRUITING (same order) | yes |
| 5 | Q3 `query.term="STAT1 gain of function"` | 3 | 3: NCT07261891 RECRUITING, NCT05561985 AVAILABLE, NCT07670156 NOT_YET_RECRUITING | yes |
| 6 | Q4 `query.intr=JAK inhibitor` + `query.cond="primary immunodeficiency"` | 1 | 1: NCT07261891 RECRUITING | yes |
| 7 | Q5 `query.cond="hyper IgE syndrome"` | 14 | 14, same ids/statuses/order; NCT07262983 RECRUITING present | yes |
| 8 | Q6 `query.cond="severe combined immunodeficiency"` + `query.term=JAK3` | 3 | 3: NCT03597594 ACTIVE_NOT_RECRUITING, NCT00006319 UNKNOWN, NCT03619551 ACTIVE_NOT_RECRUITING | yes |
| 9 | `query.cond=EXPANSION[None]STAT1` | 8 | 8: NCT07261891, NCT05561985, NCT05781841, NCT07670156, NCT02629419, NCT01568996, NCT02171286, NCT00001467 | yes |
| 10 | hyper-IgE `pageSize=5` + `pageToken` | totalCount 14 on p1 only; tokens `…xuZvWs1PcckA` / `…xtYvSl3PcdkA` | p1 totalCount 14 + same token; p2 no totalCount, same second token; p3 (4 ids) has **no** `nextPageToken` | yes |
| 11 | `filter.overallStatus=RECRUITING,NOT_YET_RECRUITING` | 2 of 14 | 2: NCT00006150, NCT07262983 (both RECRUITING) | yes |
| 12 | `filter.ids=NCT05561985,NCT07261891,NCT07670156` | 3 | 3 | yes |
| 13 | `GET /studies/NCT07670156` | 16,939 B; all §2 paths/values | 16,939 B, md5 identical; every path in the §2 table re-checked programmatically (values identical) | yes |
| 14 | `GET /studies/NCT07261891?fields=<dotted paths>` | shown JSON | byte-identical JSON | yes |
| 15 | `GET /studies/NCT07261891?fields=EligibilityModule` | module expands | identical (`healthyVolunteers` true, `minimumAge "18 Years"`, no `maximumAge`) | yes |
| 16 | `query.term=NCT05561985` + 14 short piece names, `pageSize=1` | works; `nextPageToken` even on 1 hit | 200; `eligibilityModule: {}`; `nextPageToken` present | yes |
| 17 | `GET /studies/metadata` | 175,633 B, 6 top-level sections, node keys | identical; sections `protocolSection, resultsSection, annotationSection, documentSection, derivedSection, hasResults`; keys `children, dedLink, name, piece, sourceType, title, type` | yes |
| 18 | `GET /studies/search-areas` | 25,359 B; cond 7 / intr 12 / term 57 pieces | identical; BasicSearch = 39 parts carrying **57** pieces, ConditionSearch 7, InterventionSearch 12; same param list | yes |
| 19 | `GET /studies/enums` | 19,945 B, 41 enums, item keys `type, values[].value, pieces` | identical; all §6 enum value lists match. Addition: each `values[]` item also has `legacyValue` | yes |
| 20 | `GET /stats/size` | totalStudies 605,599, avg 17,301, p50 9,856, p99 149,539 | identical | yes |
| 21 | `GET /stats/field/values?fields=Phase` | NA 238,061; PHASE2 90,442; PHASE1 65,890; PHASE3 49,967; PHASE4 35,897; EARLY_PHASE1 6,494; missing 143,573 | identical (`field: protocolSection.designModule.phases`) | yes |

### R.2 Everything else in the notes that I re-ran (not in the "verified" list but stated as observed)

All 19 rows of the §1.5 "extra variants" table reproduce exactly: `ruxolitinib AND STAT1` 0; ruxolitinib + `"primary immunodeficiency"` 0; ruxolitinib + candidiasis 1 (NCT05398809); `AREA[]` inside `query.term` 1 (same); `query.cond=STAT1` 14 (noise = Abbott **i-STAT** point-of-care potassium/calcium device studies, e.g. NCT07382076 "i-STAT ACTpro Cartridge" — consistent with the "STAT" partial-match explanation); `query.term=STAT1` 60; `EXPANSION[None]STAT1` 46; `EXPANSION[Term]STAT1` 48; `"STAT3 gain of function"` 1; `JAK1 AND "gain of function"` 0; `EXPANSION[None]JAK3` 35; `EXPANSION[None]IL2RG` 10 (all five X-SCID ids present); SCID + IL2RG 101 = SCID alone 101; TYK2 43; `"JAK inhibitor"` 297; MPN + ruxolitinib 175; big OR query 4 (same ids); `EXPANSION[None]"chronic mucocutaneous candidiasis"` 1 (NCT03800056); hyper-IgE + `AREA[StudyType]INTERVENTIONAL` + `sort=@relevance` 10.

Error behaviour reproduces exactly (all `text/plain; charset=UTF-8`): `pageSize=abc` → 400 ``Value provided in parameter `pageSize` cannot be converted to 32-bit integer``; `fields=NotARealField` → 400 `Parameter 'fields' contains invalid field name: 'NotARealField'`; `/studies/NCT04409146` → 404 `NCT number NCT04409146 not found`. CSV call reproduces (`content-type: text/csv`, `x-total-count: 14`, `x-next-page-token`, header row `NCT Number,Study Title,Study Status`). Bad-path probes `/api/oas/v2.yaml`, `/api/oas/v2.json`, `/api/v2/openapi.json` → 404. Response-header set identical (no rate-limit headers). SPA shell: all five docs URLs return the same 94,323-byte HTML (md5 `a7f0876c…`, identical to the cached `api_docs.html`/`terms.html`); bundle is `main-NRLW7DCI.js`, `content-length: 114456`, md5 identical to cached `main.js`, and the only `/api/...` string in it is `/api/int/access-check`; no "rate limit"/"requests per" strings.

NCT05561985 sparse-record claims reproduce (1,886 B; no `startDateStruct`, `phases`, `enrollmentInfo`, `sex`, `minimumAge`; `designModule = {"studyType":"EXPANDED_ACCESS","nPtrsToThisExpAccNctId":2,"expandedAccessTypes":{"individual":true}}`; `overallStatus "AVAILABLE"`).

Spec wording claims in §1.3 all found verbatim in `oas_v2_rerun.yaml`: `query.lead` exists (line 132); `filter.advanced` examples `AREA[StartDate]2022` and `AREA[MinimumAge]RANGE[MIN, 16 years]…`; `sort` "not sorted by default for a performance reason", "only date and numeric fields are allowed for sorting", examples `@relevance`, `LastUpdatePostDate`, `EnrollmentCount:desc`; `countTotal` default `false`, "ignored for the subsequent pages", CSV `x-total-count`; `pageSize` default 10, "coerced down to 1,000"; `pageToken` "Do not specify it for first page"; `fields` pattern `^([a-zA-Z][a-zA-Z0-9\-. ]*)|(@query)$`; `filter.ids` pattern `^[Nn][Cc][Tt]0*[1-9]\d{0,7}$`; fetchStudy `format` enum `csv, json, json.zip, fhir.json, ris`; "Request for each subsequent page **must** have the same parameters as for the first page, except `countTotal`, `pageSize`, and `pageToken`".

Rendered docs pages (built-in browser, 2026-10-03): T&C text matches the §4 paraphrase point for point, including the email clause and "Last updated on January 31, 2023". API landing page (`/data-api/api`, "Last updated on August 26, 2025") contains the modernized-ingest notice, the refresh-schedule sentence quoted in §4, and "Please note that the COVERAGE and EXPANSION operators are not fully implemented on the modernized ClinicalTrials.gov." Essie page (`/find-studies/constructing-complex-search-queries`, "Last updated on February 09, 2026") matches §1.6: precedence list, `EXPANSION` choices with `None` = "Case and accent marks are significant" and "Defaults to Relaxation", the quoted-phrase-after-Relaxation ⇒ Concept note, `COVERAGE` defaults to `Contains` and `FullMatch/StartsWith/EndsWith` only with `Concept|Term|None`, plus `AREA`, `SEARCH`, `MISSING`, `RANGE`, `DISTANCE`, `ALL`, `TILT`. About-API index (`/data-api/about-api`) lists exactly: API Migration Guide, Study Data Structure, Search Areas, CSV Download, RIS Download. Migration guide ("Last updated on August 27, 2024") does name `/api/v2/studies/download?format=json.zip` and says pagination default 10 / maximum 1000.

### R.3 Corrections and upgrades (nothing in the original was wrong; these sharpen it)

1. **`resultsSection` — UNVERIFIED → verified.** `filter.advanced=AREA[HasResults]true` on the hyper-IgE query returns 3 (NCT00527878, NCT02996448, NCT02629419).
   ```
   curl -s https://clinicaltrials.gov/api/v2/studies/NCT00527878        → HTTP 200, 30,237 B
   top-level keys: ["protocolSection","resultsSection","derivedSection","hasResults"]   hasResults: true
   resultsSection modules: participantFlowModule, baselineCharacteristicsModule, outcomeMeasuresModule, adverseEventsModule, moreInfoModule
   ```
   So `resultsSection` is a 4th top-level key that appears only when `hasResults` is true (metadata lists it as piece `ResultsSection`).
2. **`references[].pmid / .type / .citation` — UNVERIFIED → verified** on NCT07261891 (3 refs) and NCT07262983 (3 refs): first ref keys exactly `["pmid","type","citation"]`, e.g. `{"pmid":"37020552","type":"BACKGROUND","citation":"Staels F, …"}`. Metadata pieces: `protocolSection.referencesModule.references` → `Reference` (`Reference[]`), `.pmid` → `ReferencePMID` (text), `.type` → `ReferenceType`, `.citation` → `ReferenceCitation`. Use `ReferencePMID` in `fields` to link studies to PubMed nodes.
3. **301 on `NCTIdAlias` — UNVERIFIED → verified.** Aliased studies can be listed with `query.term=NOT AREA[NCTIdAlias]MISSING` (3,162 studies; field path `protocolSection.identificationModule.nctIdAliases`, an array; piece `NCTIdAlias`). Then:
   ```
   curl -s -D - https://clinicaltrials.gov/api/v2/studies/NCT05033340
   HTTP/2 301
   location: /api/v2/studies/NCT04173273          (relative path, empty body, no content-type)
   curl -sL …/NCT05033340 → 200, nctId "NCT04173273", nctIdAliases ["NCT05033340"]
   ```
   Pipeline note: follow redirects (`requests` does by default; plain `curl` needs `-L`) and store the canonical `nctId` from the body, not the id you asked for. (That record happens to be 701 KB — well above the 149 KB p99 — so size outliers exist.)
4. **`/studies/download?format=json.zip` — UNVERIFIED → exists.** `curl -sI` → HTTP 200, `content-type: application/zip`, `content-disposition: attachment; filename=ctg-studies.json.zip`, **no `content-length`** header (size unknown without downloading; not downloaded). Still not in the spec's `paths:` and still not needed.
5. **`pageSize` coercion verified live:** `query.cond=cancer&fields=NCTId&pageSize=1500` → exactly 1,000 studies + `nextPageToken` (70 KB).
6. **`filter.advanced` parse errors are also plain-text 400**, e.g. `filter.advanced=AREA[NCTIdAlias]RANGE[NCT00000001,NCT99999999]` → `Error parsing query in advanced filter: RANGE is not supported for nct field protocolSection.identificationModule.nctIdAliases`. Add to gotcha 5.
7. **Precision on §2 module list:** `referencesModule` and `ipdSharingStatementModule` are present on NCT07261891 (`{"ipdSharing":"NO"}`) and NCT07262983 (`{"ipdSharing":"UNDECIDED"}`) but **absent** on NCT07670156 (11 protocol modules). The §2 sentence is correct as a union; just don't expect them on every interventional record.
8. **Contact sub-fields verified** (NCT07670156): `contactsLocationsModule` keys `centralContacts, overallOfficials, locations`; both `centralContacts[]` and `locations[].contacts[]` items have keys `name, role, phone, email` — these are the fields the T&C email clause applies to.
9. `/stats/field/sizes` has `operationId: listFieldSizesStats` (left blank in §1.2).
10. The suggested pipeline `fields` list in §3 (24 piece names + 3 `derivedSection.*` branch paths) was sent in one call against NCT07670156 → HTTP 200, 3,608 B, 8 protocol modules + 3 derived modules; no 400, so every name is valid.
11. The `query.cond=STAT1` noise is specifically Abbott **i-STAT** device studies (titles contain "i-STAT"), which is why `EXPANSION[None]` (exact, case-sensitive) removes it.

Nothing in the original needs to be deleted; the "UNVERIFIED" tags in §1.2 (download endpoint), §2 (`resultsSection`, `pmid`/`citation`) and §3 (301) can now be read as verified per R.3.
