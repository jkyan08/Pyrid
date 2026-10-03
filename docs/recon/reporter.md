# NIH RePORTER API v2 — reconnaissance notes

Retrieved 2026-10-03 (UTC ~19:22–19:30). All endpoints, parameter names, field names and
values below were observed live in HTTP responses or in the official docs fetched during this
session, unless marked **UNVERIFIED**. Raw captures live next to this file
(`docs_root.html/.txt`, `swagger.json`, `search1.json`, `searches_3.json`, `data_elements_v2.pdf`).

Purpose in the project: `pipeline/sources/reporter.py` — "Grants / investigators" layer
(README §5), "funding–researcher–asset" edges (README §15), and the open item in
`test-cluster-desc.md` §10: "Check RePORTER for current funded programs on STAT1 GoF and
inborn errors of immunity."

---

## 0. Verdict

Usable as planned, with zero auth. Two endpoints only (`POST /v2/projects/search`,
`POST /v2/publications/search`), JSON in / JSON out, no key. Searches for all four cluster
topics return results (289 / 545 / 76 / 192 project records). Project → PMID linking works via
`core_project_num` → `/v2/publications/search`. The biggest gotchas:

1. **Field-name casing is inconsistent across three layers.** Request body keys are
   `snake_case` (`include_fields`, `advanced_text_search.search_text`); the swagger JSON
   documents them as `camelCase` (`includeFields`, `searchText`) which is misleading; and the
   *values* of `include_fields` / `exclude_fields` must be `PascalCase` (`ProjectNum`,
   `PrincipalInvestigators`). Passing snake_case values in `include_fields` is **silently
   accepted and returns `{}` for every record** (observed).
2. **Totals count project-year records, not grants.** The same grant appears once per fiscal
   year / type (e.g. `5R01AI127564-10` and `7R01AI127564-11` are both the Casanova CMC grant).
   Dedupe on `core_project_num`.
3. **No ORCID anywhere.** PI identity is `profile_id` (int). Confirmed across swagger, docs,
   and 76 PI objects.
4. `criteria.publications_search` inside project search is accepted but **does not filter**
   (returned total 2,982,395 = whole database). Use `/v2/publications/search` + `appl_ids` instead.
5. Rate limit is 200 requests per 1-minute window (header-observed) but the docs ask for
   ≤1 request/second. Sleep ≥1 s between calls.

---

## 1. Documentation and spec (fetched)

| What | URL | Observed |
| --- | --- | --- |
| HTML docs (swagger-ui page) | `https://api.reporter.nih.gov/` | 200, `text/html`, 34,935 bytes, `last-modified: Fri, 25 Sep 2026 12:45:14 GMT` |
| OpenAPI JSON | `https://api.reporter.nih.gov/swagger/v2/swagger.json` (path found in `js/main.min.js`) | 200, `openapi: "3.0.1"`, `info.title: "NIH RePORTER API"`, `info.version: "2.0"`, 34,158 bytes |
| Data Elements PDF | `https://api.reporter.nih.gov/documents/Data%20Elements%20for%20RePORTER%20Project%20API_V2.pdf` | HEAD 200, `application/pdf`, `content-length: 690439`, 51 pages. Downloaded (690 KB); text recovered by raw zlib stream decode (no PDF tooling on this machine) — name mapping in §10. |
| Sample request PDF | `documents/Sample RePORTER API Request Matching ExPORTER Project Files.pdf` (link seen on docs page) | not fetched — UNVERIFIED |
| Bulk download alternative | `https://reporter.nih.gov/exporter/` (linked from docs footer) | not fetched |
| Support contact (from docs) | `RePORT@mail.nih.gov` | — |

Response header on every API call: `api-supported-versions: 2.0`.

Only two paths exist in the swagger:

```
POST /v2/projects/search      summary: "Retrieves Projects against specified criteria payload."
POST /v2/publications/search  summary: "Retrieves publications against specified criteria payload."
```

Both document responses `200` (SearchResponse), `400` "Bad Request (Parameter value(s) are
either of invalid data types or No value(s) has been supplied)", `404` "Not found (no matching
project found)", `500`. **In practice a zero-hit search returns 200 with `total: 0`, not 404**
(observed, see §3.6).

Docs-page statements worth keeping:
- "NIH RePORTER APIs do not support fuzzy, proximity, or range based searches except where specified."
- "it is recommended that users post no more than one URL request per second and limit large
  jobs to either weekends or weekdays between 9:00 PM and 5:00 AM EST. Failure to comply with
  this policy may require administrators to block your IP address"
- `offset`: "Maximum allowed value is 14,999." (projects) / "Maximum allowed value is 9,999." (publications)
- `limit`: "The default value is 50, if no value is specified. Maximum allowed value is 500."
  (Swagger's description text says "System imposed limit is 50" — stale; live cap is 500.)

---

## 2. `POST /v2/projects/search` — request body

### 2.1 Top-level body (snake_case, all verified live)

| Key | Type | Notes |
| --- | --- | --- |
| `criteria` | object | see 2.2 |
| `offset` | int | default 0; max **14,999** (live error: `{"message":"Requested limit exceeded. Maximum offset is 14,999."}`) |
| `limit` | int | default 50 (docs); max **500** (live error on 501: `{"message":"System doesn't support limit value greater than 500. Please reduce your limit value."}`) |
| `sort_field` | string | tested `"fiscal_year"` — echoed back in `meta.sort_field` |
| `sort_order` | string | `"asc"` / `"desc"` — tested `"desc"`, echoed in `meta.sort_order` |
| `include_fields` | array of **PascalCase** names | verified: `ProjectNum`, `ProjectTitle`, `FiscalYear`, `CoreProjectNum`, `ApplId`, `ContactPiName`, `PrincipalInvestigators`, `AgencyIcAdmin`, `IsActive`, `AwardAmount`. Docs example also lists `SubprojectId`, `OrgName`, `OrgCity`, `OrgState`, `OrgStateName`, `DeptType`, `OrgCountry` (UNVERIFIED live). |
| `exclude_fields` | array of PascalCase names | verified: `AbstractText`, `PhrText`, `Terms`, `PrefTerms`, `Organization`, `AgencyIcFundings`, `FullStudySection`, `ProjectNumSplit`, `GeoLatLon`, `PrincipalInvestigators`, `ProgramOfficers`. Docs example: `ProjectStartDate`, `ProjectEndDate`. |
| `search_id` | string | re-fetch a previous search (no `criteria` allowed alongside). Verified: `{"search_id":"hsvM8Uq3M06EHdOGjeKNCA","offset":3,"limit":3}` returned page 2 of the same 289-hit search. |

Swagger `ProjectSearchRequest` lists the same things in camelCase (`searchId, criteria,
includeFields, excludeFields, offset, limit, sortField, sortOrder, isInitialSearch`). Whether the
API also accepts camelCase body keys is **UNVERIFIED** (only snake_case was sent). Swagger note on
`includeFields`: "If null the all fields are included by default. If not null but empty, then no
fields are included."

### 2.2 `criteria` — verified live

| Criterion | Shape | Verified example |
| --- | --- | --- |
| `advanced_text_search` | `{"operator": "and"|"or"|"advanced", "search_field": "projecttitle,terms,abstracttext" or "all"/"", "search_text": "..."}` | all four cluster queries; exact phrase with `\"...\"`; `operator: "advanced"` with `AND`/`OR`/parentheses |
| `fiscal_years` | `[2025, 2026]` (`[]` = all) | combined with text search → total 62 |
| `include_active_projects` | bool | `true` combined above |
| `project_nums` | `["4R00AI177758-02"]` (wildcard `*` per docs) | total 1 |
| `appl_ids` | `[11458029, 9984930]` | total 2 |
| `publications_search` | `{"pm_id": ["28139313"], "appl_id": [11458029], "filter_appl_ids": true}` | **accepted but ignored**: total 2,982,395 both times. `pm_id` must be an array of strings (sending a bare string → 400 `["Error converting value \"28139313\" to type 'System.Collections.Generic.IEnumerable`1[System.String]'. Path 'criteria.publications_search.pm_id' ..."]`). Do not rely on this. |

Docs text-search notes: `"and"` is default; stop words dropped ("a, an, and, are, as, at, be, of,
on, or, into, is, it, etc."); `"or"` = any term; quotes for exact phrase; `"advanced"` = boolean
syntax. `search_field` values: `"projecttitle"`, `"terms"`, `"abstracttext"` (comma-separated), or
`"all"` / `""` for everything.

### 2.3 `criteria` — documented on the docs page, NOT tested (UNVERIFIED live)

`use_relevance` (bool), `pi_names` / `po_names` (`[{"any_name":..},{"first_name":..},{"last_name":..}]`,
wildcard by default), `org_names`, `org_names_exact_match`, `pi_profile_ids` (`[int]`), `org_cities`,
`org_states`, `org_countries`, `project_num_split` (`{appl_type_code, activity_code, ic_code,
serial_num, support_year, full_support_year, suffix_code}`), `spending_categories`
(`{"Values":[27,92],"match_all":"false"}`), `funding_mechanism`, `agencies` (e.g. `["NIGMS","AHRQ"]`),
`is_agency_admin`, `is_agency_funding`, `activity_codes`, `award_types` (`'4C'`/`'4N'` for type 4),
`dept_types`, `cong_dists`, `opportunity_numbers`, `project_start_date` / `project_end_date` /
`award_notice_date` / `date_added` (`{"from_date":"YYYY-MM-DD","to_date":"YYYY-MM-DD"}`, either
side may be `""`), `organization_type`, `award_amount_range` (`{"min_amount","max_amount"}`),
`exclude_subprojects`, `multi_pi_only`, `newly_added_projects_only`, `sub_project_only`,
`covid_response`. Swagger additionally lists `cooperativeAgreementCodes`, `foa`, `fullStudySections`,
`outcomesOnly`, `arraType` (snake_case form UNVERIFIED).

---

## 3. Live searches (the four requested topics)

Command template (all four used this; only `search_text` changed):

```bash
curl -sS -X POST "https://api.reporter.nih.gov/v2/projects/search" \
  -H "accept: application/json" -H "Content-Type: application/json" \
  -d '{"criteria":{"advanced_text_search":{"operator":"and","search_field":"projecttitle,terms,abstracttext","search_text":"STAT1 gain of function"}},"offset":0,"limit":3}'
```

### 3.1 "STAT1 gain of function" — total **289**
```
meta: {"search_id":"hsvM8Uq3M06EHdOGjeKNCA","total":289,"offset":0,"limit":3,"sort_field":null,"sort_order":"ASC","sorted_by_relevance":true,"properties":{"URL":"https:/reporter.nih.gov/search/hsvM8Uq3M06EHdOGjeKNCA/projects"}}
4R00AI177758-02   FY2026 NIAID  PHILIPS, RACHAEL LAURA  Genomic regulation of immune response by a STAT1 gain of function mutation
2R01AR073938-06A1 FY2026 NIAMS  (…)                     Mechanistic insights into systemic lupus erythematosus (SLE) pathogenesis from inborn errors of immunity
5R01AI191107-02   FY2026 NIAID  (…)                     Mechanisms of STAT1 and STAT3 canonical and persistent activation in physiology and disease
```
Variants: `operator:"advanced"`, `search_text:"STAT1 AND (\"gain of function\" OR \"gain-of-function\" OR GOF)"`, `search_field:"all"` → total **237** (same top 3).
`fiscal_years:[2025,2026]` + `include_active_projects:true` → total **62** (award_amount 249000 / 756756 / 646763 for the top 3; all `is_active: true`).
`search_text:"STAT1"` alone, `search_field:"all"` → total **3390**.

### 3.2 "inborn errors of immunity" — total **545**
```
meta: {"search_id":"IfDRuai8rUacEMIznUNk6A","total":545,...}
7R01AI088364-17   FY2026 NIAID CASANOVA, JEAN-LAURENT  Inborn errors of immunity underlying herpes simplex encephalitis
2R01AI088364-16A1 FY2026 NIAID CASANOVA, JEAN-LAURENT  Inborn errors of immunity underlying herpes simplex encephalitis
7R01AI127564-11   FY2026 NIAID CASANOVA, JEAN-LAURENT  Genome-wide search for inborn errors of IL-17 immunity underlying chronic mucocutaneous candidiasis
```

### 3.3 "chronic mucocutaneous candidiasis" — total **76** (exact phrase `"\"chronic mucocutaneous candidiasis\""` with `search_field:"all"` → **70**)
```
meta: {"search_id":"MmhzCDZJ_UCdCq5t3Ig_lw","total":76,...}
7R01AI127564-11 FY2026 NIAID CASANOVA, JEAN-LAURENT  Genome-wide search for inborn errors of IL-17 immunity underlying chronic mucocutaneous candidiasis
5R01AI127564-10 FY2026 NIAID CASANOVA, JEAN-LAURENT  Genome-wide search for inborn errors of IL-17 immunity underlying chronic mucocutaneous candidiasis
1R21TR006398-01 FY2026 NCATS WANG, JINGXIN           Development of splicing modulators and their combinations to target undruggable genes
```

### 3.4 "JAK3 severe combined immunodeficiency" — total **192**
```
meta: {"search_id":"Euqn77T3mUGchQTt6yu8UA","total":192,...}
5U01AI184132-03   FY2026 NIAID AULETTA, JEFFERY J  Clinical and mechanistic studies defining optimal preparative approaches to infants with IL2RG/JAK3/RAG1/RAG2 SCID: a randomized trial of busulfan dosage
3U01AI184132-03S1 FY2026 NIAID AULETTA, JEFFERY J  (same title; -03S1 is a supplement)
1ZIABC012039-05   FY2025 NCI   PAI, SUNG-YUN       Pathobiology and treatment of patients with severe combined immunodeficiency
```

### 3.5 Observations about search semantics
- Results come back relevance-ranked (`meta.sorted_by_relevance: true`) even without `use_relevance`.
- `"and"` is bag-of-words across title+abstract+terms, so "STAT1 gain of function" also matches an
  SLE project that mentions STAT1 and gain-of-function separately. Use quoted phrases or
  `operator:"advanced"` for precision.
- `meta.properties.URL` is malformed as returned (`https:/reporter.nih.gov/...`, single slash) —
  fix the scheme before storing it as a source link. `project_detail_url` on each record is well-formed.

### 3.6 Zero hits
`search_text:"qzxv9876nonexistentterm"` → HTTP 200, `{"meta":{"search_id":"30fZZworKkCsJhuXAoXDyg","total":0,...},"results":[]}`.
So "log exact query + date even on zero hits" is straightforward: check `meta.total == 0`.

---

## 4. `POST /v2/projects/search` — response structure

Top level: `{"meta": {...}, "results": [...]}`.

`meta` keys (observed): `search_id`, `total`, `offset`, `limit`, `sort_field`, `sort_order`,
`sorted_by_relevance`, `properties` (`{"URL": "..."}`).

Each `results[]` record has these 45 keys (observed, in response order):
`appl_id, subproject_id, fiscal_year, project_num, project_serial_num, organization, award_type,
activity_code, award_amount, is_active, project_num_split, principal_investigators, contact_pi_name,
program_officers, agency_ic_admin, agency_ic_fundings, cong_dist, spending_categories,
project_start_date, project_end_date, organization_type, geo_lat_lon, opportunity_number,
full_study_section, award_notice_date, is_new, mechanism_code_dc, core_project_num, terms,
pref_terms, abstract_text, project_title, phr_text, spending_categories_desc, agency_code,
covid_response, arra_funded, budget_start, budget_end, cfda_code, funding_mechanism,
direct_cost_amt, indirect_cost_amt, project_detail_url, date_added`

### 4.1 The fields the task asked about — exact names and observed values (record `appl_id` 11458029)

| Concept | Exact field | Observed value / shape |
| --- | --- | --- |
| project number (full, per year) | `project_num` | `"4R00AI177758-02"` |
| grant identity across years | `core_project_num` | `"R00AI177758"` — **dedupe key** |
| application id (per record) | `appl_id` | `11458029` (int) — also the key in `project_detail_url` and in publications `applid` |
| serial | `project_serial_num` | `"AI177758"` |
| title | `project_title` | `"Genomic regulation of immune response by a STAT1 gain of function mutation"` |
| abstract | `abstract_text` | string, starts `"Project Summary/Abstract\nThe coordinated effort..."` (contains `\n`) |
| public health relevance | `phr_text` | string, starts `"Project Narrative\n..."` |
| fiscal year | `fiscal_year` | `2026` (int) |
| award amount | `award_amount` | `249000` (int); also `direct_cost_amt` `158095`, `indirect_cost_amt` `90905` |
| admin IC | `agency_ic_admin` | `{"code":"AI","abbreviation":"NIAID","name":"National Institute of Allergy and Infectious Diseases","admin_org_id":"12500003","admin_funding_url":""}` |
| funding ICs (per FY) | `agency_ic_fundings` | `[{"fy":2026,"code":"AI","name":"...","abbreviation":"NIAID","total_cost":249000.0,"direct_cost_ic":158095.0,"indirect_cost_ic":90905.0}]` |
| agency | `agency_code` | `"NIH"` |
| organization | `organization` | `{"org_name":"OHIO STATE UNIVERSITY","city":null,"country":null,"org_city":"Columbus","org_country":"UNITED STATES","org_state":"OH","org_state_name":null,"dept_type":"MICROBIOLOGY/IMMUN/VIROLOGY","fips_country_code":null,"org_duns":["832127323"],"org_ueis":["DLWBSLWAJWR1"],"primary_duns":"832127323","primary_uei":"DLWBSLWAJWR1","org_fips":"US","org_ipf_code":"6218701","org_zipcode":"43210","external_org_id":6218701}` — use `org_name`, `org_city` (the bare `city`/`country` keys were `null`) |
| PIs | `principal_investigators` | `[{"profile_id":14185972,"first_name":"Rachael","middle_name":"Laura","last_name":"Philips","is_contact_pi":true,"full_name":"Rachael Laura Philips","title":"ASSISTANT PROFESSOR"}]` — keys across 76 PI objects: exactly `profile_id, first_name, middle_name, last_name, is_contact_pi, full_name, title`. Multi-PI example: `7R01AI088364-17` → Casanova (`is_contact_pi: true`, profile_id 9889423) + Shen-Ying Zhang (`false`, 10812226); `title` and `middle_name` can be `""`. |
| contact PI (flat) | `contact_pi_name` | `"PHILIPS, RACHAEL LAURA"` — uppercase `LAST, FIRST MIDDLE`. **Dirty**: observed `"CASANOVA, JEAN-LAURENT "` (trailing space), `" PAPPAS , IOANNIS "`, `"'GERARD, ROBE…"` (leading apostrophe). Prefer `principal_investigators[is_contact_pi]`. |
| program officers | `program_officers` | `[{"first_name":"TIMOTHY","middle_name":"A","last_name":"GONDRE-LEWIS","full_name":"TIMOTHY A GONDRE-LEWIS"}]` (no profile_id) |
| dates | `project_start_date`, `project_end_date` | `"2023-09-01T00:00:00"`, `"2028-03-31T00:00:00"` (ISO, no TZ); also `budget_start`, `budget_end`, `award_notice_date`, `date_added` (`"2026-04-04T17:04:38"`) |
| active flag | `is_active` | `true`; `is_new` `false` |
| mechanism / type | `award_type` `"4N"`, `activity_code` `"R00"`, `funding_mechanism` `"Non-SBIR/STTR"`, `mechanism_code_dc` `"RP"`, `cfda_code` `"93.855"`, `arra_funded` `"N"`, `covid_response` `null` | |
| opportunity | `opportunity_number` | `"PAR-21-271"` (RFA/PAR signal for the Act tab) |
| study section | `full_study_section` | `{"srg_code":"AITC","srg_flex":null,"sra_designator_code":null,"sra_flex_code":null,"group_code":null,"name":"Allergy, Immunology, and Transplantation Research Committee[AITC]"}` |
| RCDC terms | `terms` | one string, angle-bracket delimited: `"<(IFN) α><(IFN)-α><ATAC><Autoimmune Diseases>..."` |
| preferred terms | `pref_terms` | one string, semicolon delimited: `"Affect;Anti-viral Response;Autoimmune Diseases;..."` |
| org type | `organization_type` | `{"name":"SCHOOLS OF MEDICINE","code":"10","is_other":false}` |
| geo | `geo_lat_lon` | `{"lon":-83.033131,"lat":39.999598}` |
| web link | `project_detail_url` | `"https://reporter.nih.gov/project-details/11458029"` — use as the human-readable source URL |
| **publications link** | **none** | There is no publications field on a project record. Link via `core_project_num` (or `appl_id`) → `/v2/publications/search` (§5). |
| spending categories | `spending_categories`, `spending_categories_desc` | both `null` on this record |
| split number | `project_num_split` | `{"appl_type_code":"4","activity_code":"R00","ic_code":"AI","serial_num":"177758","support_year":"02","full_support_year":"02","suffix_code":""}` |

---

## 5. `POST /v2/publications/search` — linking projects to PMIDs

### 5.1 Request
Body keys: `criteria`, `offset`, `limit`, `sort_field`, `sort_order` (swagger: `criteria` required).
`criteria` accepts exactly three keys (swagger `PublicationSearchCriteriaDto` + docs):
`pmids` (`[int]`), `appl_ids` (`[int]`), `core_project_nums` (`[string]`, wildcard `*` allowed).
Caps (live): `limit` ≤ 500 (`{"message":"System doesn't support limit value greater than 500. Please reduce your limit value."}`),
`offset` ≤ 9,999 (`{"message":"System doesn't support offset value greater than 9,999. Please narrow down your search criteria."}`).

### 5.2 Response
```
{"meta":{"search_id":null,"total":1,"offset":0,"limit":5,"sort_field":"coreproject","sort_order":"desc","sorted_by_relevance":false,"properties":{}},
 "results":[{"coreproject":"R00AI177758","pmid":40446018,"applid":11458029}],
 "facet_results":[]}
```
Result row keys: **`coreproject`, `pmid`, `applid`** (note: different naming style from the project
endpoint — no underscores). Docs call these "Core Project Number", "PMID", "Latest Application ID".
No title/journal/authors — fetch those from PubMed by PMID.
No-match → 200 with `total: 0`, `results: []` (tested `pmids:[1]`).

### 5.3 Verified commands

Project → PMIDs:
```bash
curl -sS -X POST "https://api.reporter.nih.gov/v2/publications/search" \
  -H "Content-Type: application/json" \
  -d '{"criteria":{"core_project_nums":["R00AI177758"]},"offset":0,"limit":5}'
# → total 1: coreproject R00AI177758, pmid 40446018, applid 11458029
```
Three cluster grants at once:
```bash
curl -sS -X POST "https://api.reporter.nih.gov/v2/publications/search" \
  -H "Content-Type: application/json" \
  -d '{"criteria":{"core_project_nums":["R01AI127564","R00AI177758","U01AI184132"]},"offset":0,"limit":500}'
# → total 124 rows, e.g. {"coreproject":"U01AI184132","pmid":40544999,"applid":11555650}
```
PMID → grants (cluster paper PMID 28139313, the ruxolitinib STAT1 GoF Th-cell paper):
```bash
curl -sS -X POST "https://api.reporter.nih.gov/v2/publications/search" \
  -H "Content-Type: application/json" \
  -d '{"criteria":{"pmids":[28139313]},"offset":0,"limit":5}'
# → total 5: ZIACL010304/applid 11411917, ZIAAI001222/11403365, ZIAAI001122/11403323,
#            R56AI115699/9108510, R01AI085090/9984930
```
Then grants → full project records (reverse hop):
```bash
curl -sS -X POST "https://api.reporter.nih.gov/v2/projects/search" \
  -H "Content-Type: application/json" \
  -d '{"criteria":{"appl_ids":[11458029,9984930]},"offset":0,"limit":5,"include_fields":["ApplId","ProjectNum","CoreProjectNum","FiscalYear","ProjectTitle","ContactPiName","AgencyIcAdmin"]}'
# → total 2: 9984930 = 5R01AI085090-10 FY2020 CHATILA, TALAL AMINE "Mechanisms of Disease Pathogenesis in Regulatory T cell Deficiency" (NIAID)
#            11458029 = 4R00AI177758-02 FY2026 PHILIPS, RACHAEL LAURA (NIAID)
```
Gotcha: `applid` in the publications table is the *latest* application of the grant, so the
`appl_ids` hop returns that one fiscal-year record. To get every year of a grant use
`project_nums` with a wildcard (documented, e.g. `["5UG1HD0784*"]`) — UNVERIFIED live.

### 5.4 Pipeline recipe (project ↔ paper ↔ investigator edges)
1. `projects/search` with `advanced_text_search` (+ `fiscal_years`, `include_active_projects`) and
   `include_fields` to keep payloads small; page with `offset` in steps of ≤500; dedupe by `core_project_num`.
2. `publications/search` with `core_project_nums` (batch; ≤500 rows per page, offset ≤9,999) → PMIDs.
3. Intersect with the Section 11 PMIDs already in the paper layer; for new PMIDs fetch via PubMed
   E-utilities (NCBI limit 3 req/s).
4. Investigator node = `principal_investigators[].profile_id` + names + `organization.org_name` /
   `org_city`; `is_contact_pi` for the contact. No ORCID (§7) → flag `low` per README §5 unless
   matched to a PubMed author by name + affiliation.
5. Funder node = `agency_ic_admin.abbreviation` (+ `agency_ic_fundings[]` for co-funding IC and
   `total_cost`); RFA/PAR signal = `opportunity_number`.
6. Source link = `project_detail_url`; record `date_added` and the retrieval date.

---

## 6. Rate limits and paging caps (verified)

| Item | Docs | Observed live |
| --- | --- | --- |
| Request rate | "no more than one URL request per second"; big jobs weekends or 9 PM–5 AM EST; IP block possible | Headers on every response: `x-rate-limit-limit: 1m`, `x-rate-limit-remaining: 199` after a first call, then `196` → `195` on two back-to-back calls with the same `x-rate-limit-reset: 2026-10-03T19:27:14.2756625Z`. ⇒ **200 requests per 1-minute window** (the number 200 is inferred from 199-after-one-call; the header never prints the ceiling). |
| Projects `limit` | default 50, max 500 | 501 → HTTP 400 `{"message":"System doesn't support limit value greater than 500. Please reduce your limit value."}`; 500 → 200 with 500 rows |
| Projects `offset` | max 14,999 | 15000 → HTTP 400 `{"message":"Requested limit exceeded. Maximum offset is 14,999."}` ⇒ at most 15,499 records reachable per query; split big queries by `fiscal_years`. |
| Publications `limit` | max 500 | 501 → 400 (same message) |
| Publications `offset` | max 9,999 | 10000 → 400 `{"message":"System doesn't support offset value greater than 9,999. Please narrow down your search criteria."}` |
| Auth | none mentioned | none required; no key accepted/needed |
| Error body formats | — | cap errors: JSON object `{"message": "..."}`; type errors: JSON **array of strings** (`["Error converting value ..."]`). Handle both. |
| Content-Type | — | `application/json; charset=utf-8` |

Recommendation for `pipeline/cache.py`'s HTTP client: a separate RePORTER limiter at 1 req/s
(docs' request), which is well under the 200/min header limit.

---

## 7. ORCID — confirmed absent

- `grep -i orcid swagger.json` → no match.
- docs page text → no match.
- all PI objects across the STAT1 search (3 records) and a 50-record "inborn errors of immunity"
  page (76 PI objects, 17 multi-PI projects): key set is exactly
  `['first_name','full_name','is_contact_pi','last_name','middle_name','profile_id','title']`.
- `program_officers[]` has only `first_name, middle_name, last_name, full_name`.
- The only stable person identifier is **`profile_id`** (int; RePORTER's own PI profile id, also a
  search criterion `pi_profile_ids` per docs — UNVERIFIED live). Per README §5 this means RePORTER
  investigators get "name + affiliation, flagged `low`" unless matched elsewhere.

---

## 8. Everything that worked, as exact commands

```bash
# docs + spec
curl -sS -L -D - -o docs_root.html "https://api.reporter.nih.gov/"
curl -sS -o swagger.json "https://api.reporter.nih.gov/swagger/v2/swagger.json"
curl -sS -I "https://api.reporter.nih.gov/documents/Data%20Elements%20for%20RePORTER%20Project%20API_V2.pdf"

# project text searches (swap search_text)
curl -sS -X POST "https://api.reporter.nih.gov/v2/projects/search" -H "accept: application/json" -H "Content-Type: application/json" \
  -d '{"criteria":{"advanced_text_search":{"operator":"and","search_field":"projecttitle,terms,abstracttext","search_text":"STAT1 gain of function"}},"offset":0,"limit":3}'
curl -sS -X POST "https://api.reporter.nih.gov/v2/projects/search" -H "Content-Type: application/json" \
  -d '{"criteria":{"advanced_text_search":{"operator":"and","search_field":"all","search_text":"\"chronic mucocutaneous candidiasis\""}},"offset":0,"limit":3}'
curl -sS -X POST "https://api.reporter.nih.gov/v2/projects/search" -H "Content-Type: application/json" \
  -d '{"criteria":{"advanced_text_search":{"operator":"advanced","search_field":"all","search_text":"STAT1 AND (\"gain of function\" OR \"gain-of-function\" OR GOF)"}},"offset":0,"limit":3,"include_fields":["ProjectNum","FiscalYear","ProjectTitle"]}'
curl -sS -X POST "https://api.reporter.nih.gov/v2/projects/search" -H "Content-Type: application/json" \
  -d '{"criteria":{"advanced_text_search":{"operator":"and","search_field":"all","search_text":"STAT1 gain of function"},"fiscal_years":[2025,2026],"include_active_projects":true},"offset":0,"limit":3,"include_fields":["ProjectNum","FiscalYear","IsActive","ProjectTitle","AwardAmount","AgencyIcAdmin"]}'

# paging / lookups
curl -sS -X POST "https://api.reporter.nih.gov/v2/projects/search" -H "Content-Type: application/json" \
  -d '{"search_id":"hsvM8Uq3M06EHdOGjeKNCA","offset":3,"limit":3,"include_fields":["ProjectNum","FiscalYear","ProjectTitle"]}'
curl -sS -X POST "https://api.reporter.nih.gov/v2/projects/search" -H "Content-Type: application/json" \
  -d '{"criteria":{"project_nums":["4R00AI177758-02"]},"limit":1,"exclude_fields":["AbstractText","PhrText","Terms","PrefTerms"]}'
curl -sS -X POST "https://api.reporter.nih.gov/v2/projects/search" -H "Content-Type: application/json" \
  -d '{"criteria":{"appl_ids":[11458029,9984930]},"offset":0,"limit":5,"include_fields":["ApplId","ProjectNum","CoreProjectNum","FiscalYear","ProjectTitle","ContactPiName","AgencyIcAdmin"]}'

# publications
curl -sS -X POST "https://api.reporter.nih.gov/v2/publications/search" -H "Content-Type: application/json" \
  -d '{"criteria":{"core_project_nums":["R00AI177758"]},"offset":0,"limit":5}'
curl -sS -X POST "https://api.reporter.nih.gov/v2/publications/search" -H "Content-Type: application/json" \
  -d '{"criteria":{"pmids":[28139313]},"offset":0,"limit":5}'
```

---

## 9. Open / unverified

- camelCase request-body keys (as in swagger) — untested; snake_case is what works.
- Full list of legal `include_fields` names — see §10 (extracted from the Data Elements PDF); names in §2.1 were additionally exercised live.
- `pi_profile_ids`, `pi_names`, `org_names`, date-range and other criteria in §2.3 — documented, not exercised.
- Whether `search_id` expires (docs do not say).
- License wording: docs page says "The RePORTER database is available to all public users" and
  links a "Data Access Policy" (not fetched). Fill the README §5 licenses row with "NIH RePORTER
  API v2, retrieved 2026-10-03, public/NIH terms" and link the policy once read.

---

## 10. Data Elements PDF — authoritative name mapping (extracted 2026-10-03 from the 51-page PDF via raw zlib stream decode; a few names may be mangled by PDF text spacing, verify against §4 before relying on an entry marked `?`)

The PDF defines each element with: business Name, Definition, Type, `PayloadCriteriaName` (request criteria key), `APIResponseName` (response key), `APIIncludeFieldName` (value for `include_fields`/`exclude_fields`), `ExPORTERColumn`.

| Element (PDF) | PayloadCriteriaName | APIResponseName | APIIncludeFieldName |
| --- | --- | --- | --- |
| Application ID | `appl_ids` | `appl_id` | `ApplId` |
| Activity | `activity_codes` | `activity_code` | `ActivityCode` |
| Administering IC | `is_agency_admin` | `agency_ic_admin` | `AgencyIcAdmin` |
| IC Code | `NA` | `code` | `AgencyIcAdmin` |
| IC Name Abbreviation | `agencies` | `abbreviation` | `AgencyIcAdmin` |
| IC Name | `NA` | `name` | `AgencyIcAdmin` |
| Application Type | `award_types` | `award_type` | `AwardType` |
| ARRA Indicator | `NA` | `arra_funded` | `ArraFunded` |
| Award Notice D ate | `award` (truncated in PDF text, UNVERIFIED) | `award_notice_date` | `AwardNoticeDate` |
| Budget Start Date | `NA` | `budget_start` | `BudgetStart` |
| Budget End Date | `NA` | `budget_end` | `BudgetEnd` |
| CFDA Code | `NA` | `cfda_code` | `CfdaCode` |
| Core Project Number | `project_nums` | `core_project_num` | `CoreProjectNum` |
| Organization Type | `organization_type` | `organization_type` | `OrganizationType` |
| Gene ric Name for Institution Type | `NA` | `name` | `OrganizationType` |
| Generic Name for Institution Type Code | `NA` | `code` | `OrganizationType` |
| Other Generic Institution Type | `NA` | `is_other` | `OrganizationType` |
| Funding Opportunity Announcement | `opportunity_number` | `opportunity_number` | `OpportunityNumber` |
| Full Project Number | `project_nums` | `project_num` | `ProjectNum` |
| Funding ICs | `is_agency_funding` | `agency_ic_fundings` | `AgencyIcFundings` |
| Fiscal Year | `NA` | `fy` | `AgencyIcFundings` |
| IC Code | `agencies` | `code` | `AgencyIcFundings` |
|  | `NA` | `name` | `AgencyIcFundings` |
| IC Name Abbreviation | `NA` | `abbreviation` | `AgencyIcFundings` |
| IC Funding | `agencies` | `total_cost` | `AgencyIcFundings` |
| Funding Mechanism | `NA` | `funding_mechanism` | `FundingMechanism` |
| Funding Mechanism Code | `funding_mechanism` | `mechanism_code_dc` | `MechanismCodeDc` |
| Fiscal Year | `fiscal_years` | `fiscal_year` | `FiscalYear` |
| NIH Spending Categories | `spending_categories` | `spending_categories_desc` | `SpendingCategoriesDesc` |
| NIH Spending Categories Code | `NA` | `spending_categories` | `SpendingCategories` |
| Organization | `NA` | `organization` | `Organization` |
| Organization City | `org_cities` | `org_city` | `Organization` |
| Organization County | `org_countries` | `org_country` | `Organization` |
| Department | `dept_types` | `dept_type` | `Organization` |
| DUNS | `NA` | `org_duns` | `Organization` |
| U nique E ntity I dentifier | `NA` | `org_ueis` | `Organization` |
| Primary DUNS | `NA` | `primary_duns` | `Organization` |
| Primary UEI | `NA` | `primary_uei` | `Organization` |
| FIPS | `NA` | `org_fips` | `Organization` |
| Institution Profile (IPF) Number | `NA` | `org_ipf_code` | `Organization` |
| Organization Name | `org_names , org_names_exact_match` | `org_name` | `Organization` |
| Organization State | `org_states` | `org_state` | `Organization` |
| Organization ZIP Code | `NA` | `org_zipcode` | `Organization` |
| External Organization ID | `NA` | `external_org_id` | `Organization` |
| Congressional District | `c` (truncated in PDF text, UNVERIFIED) | `cong_dist` | `CongDist` |
| Public Health Relevance | `NA` | `phr_text` | `PhrText` |
| Principal Investigator | `pi_names , multi_pi_only` | `principal_investigators` | `PrincipalInvestigators` |
| Principal Investigators IDs | `pi_profile_ids` | `profile_id` | `PrincipalInvestigators` |
| Principal Investigators Full Name | `pi_` (truncated in PDF text, UNVERIFIED) | `full_name` | `PrincipalInvestigators` |
| First Name of Principal Investigator | `first` (truncated in PDF text, UNVERIFIED) | `first_name` | `PrincipalInvestigators` |
| Middle Name of Principal Investigator | `any` (truncated in PDF text, UNVERIFIED) | `middle_name` | `PrincipalInvestigators` |
| Last Name of Principal Investigator | `last` (truncated in PDF text, UNVERIFIED) | `last_name` | `PrincipalInvestigators` |
| Principal Investigator of Contact | `NA` | `is_contact_pi` | `PrincipalInvestigators` |
| Title of Principal Investigator | `` | `title` | `PrincipalInvestigators` |
| Program Officer | `po_names` | `program_officers` | `ProgramOfficers` |
| Program Officer Name | `po_names` | `full_name` | `ProgramOfficers` |
| Program Officer First Name | `first_name` | `first_name` | `ProgramOfficers` |
| Program Officer Middle Name | `middle` (truncated in PDF text, UNVERIFIED) | `middle_name` | `ProgramOfficers` |
| Program Officer Last Name | `last_name` | `last_name` | `ProgramOfficers` |
| Project Start Date | `p` (truncated in PDF text, UNVERIFIED) | `project_start_date` | `ProjectStartDate` |
| Project End Date | `project_end_date` | `project_end_date` | `ProjectEndDate` |
| Project Terms | `terms` | `pref_terms` | `PrefTerms` |
| Project Title | `projecttitle` | `project_title` | `ProjectTitle` |
| Project Serial Number | `serial_num` | `project_serial_num` | `ProjectSerialNum` |
| Study Section| `NA` | `full_study_section` | `FullStudySection` |
| Scientific Review Group (SR G ) | `NA` | `srg_code` | `FullStudySection` |
| Study Section Name | `NA` | `name` | `FullStudySection` |
| Scientific Review Group Flex Code | `NA` | `srg_flex` | `FullStudySection` |
| Scientific Review Administrator Designator Code | `NA` | `sra_designator_code` | `FullStudySection` |
| Scientific Review Administrator Flex Code | `NA` | `sra_flex_code` | `FullStudySection` |
| Study Section Group| `NA` | `group` | `FullStudySection` |
| Sub Project ID | `exclude_subprojects,` | `subproject_id` | `SubprojectId` |
| Project Number Components Split | `project_n` | `project_num_split` | `ProjectNumSplit` |
| Suffix| `suffix_code` | `suffix_code` | `ProjectNumSplit` |
| Support Year | `support_year` | `support_year` | `ProjectNumSplit` |
| Full Support Year | `full_support_year` | `full_support_year` | `ProjectNumSplit` |
| Application Type | `award_types` | `appl_type_code` | `ProjectNumSplit` |
| Activity Code | `activity_codes , activity_code` | `activity_code` | `ProjectNumSplit` |
| IC Code | `ic_code` | `ic_code` | `ProjectNumSplit` |
| A Serial Number| `serial_num` | `serial_num` | `ProjectNumSplit` |
| Total of Direct Cost Funding | `` | `direct_cost_amt` | `DirectCostAmt` |
| Total of Indirect Cost Funding | `NA` | `indirect_cost_amt` | `IndirectCostAmt` |
| Total Project Funding | `award_amount_range` | `award_amount` | `AwardAmount` |
| Active project Indicator | `include_active_projects` | `is_active` | `IsActive` |
| Newly Added Project Indicator | `newly_added_projects_only` | `is_new` | `IsNew` |
| Name of PI Contact | `pi_names` | `contact_pi_name` | `ContactPiName` |
| NIH COVID - 19 Response | `covid_response` | `covid_response` | `CovidResponse` |
| RCDC T hesaurus Terms | `NA` | `terms` | `Terms` |
| Abstract | `abstracttext` | `abstract_text` | `AbstractText` |
| Agency Code | `a` (truncated in PDF text, UNVERIFIED) | `agency_code` | `AgencyCode` |
| Project Details Page URL | `NA` | `project_detail_url` | `ProjectDetailUrl` |
| Date Added | `date_added` | `date_added` | `DateAdded` |

Names from this table that were ALSO exercised live in this session (so they are certain): `ApplId`, `ProjectNum`, `ProjectTitle`, `FiscalYear`, `CoreProjectNum`, `ContactPiName`, `PrincipalInvestigators`, `AgencyIcAdmin`, `IsActive`, `AwardAmount`, `AbstractText`, `PhrText`, `Terms`, `PrefTerms`, `Organization`, `AgencyIcFundings`, `FullStudySection`, `ProjectNumSplit`, `GeoLatLon`, `ProgramOfficers`.

---

## Re-run check (second reviewer)

Re-run 2026-10-03 19:44–19:50 UTC (≈20 min after the original session), from the same machine, no API
key, ≥1.1 s sleep between RePORTER calls. Every command in §8 plus every command the task listed as
"verified" was re-executed; raw bodies and response headers are in `rerun_reporter/` next to this file
(`NN_<tag>.json` + `hdr_NN_<tag>.txt`; the `x*_` files are the extra edge-case checks).
**Result: all 17 listed commands ran and matched the notes (status, totals, record identities, field
paths, error messages). No count was copied wrong, no URL redirected, nothing marked verified turned
out to be from memory.** Corrections below are nuances and additions, not reversals.

### R.1 Command-by-command result

| # | Command (abridged) | Claimed | Observed now | Match |
| --- | --- | --- | --- | --- |
| 1 | `curl -sS -L -D - -o docs_root.html https://api.reporter.nih.gov/` | 200, text/html, 34,935 B, last-modified 25 Sep 2026 12:45:14 | single `HTTP/2 200` (no 3xx hop), `content-type: text/html`, `content-length: 34935`, same last-modified | yes |
| 2 | `curl -sS -o swagger.json .../swagger/v2/swagger.json` | OpenAPI 3.0.1, title "NIH RePORTER API", version "2.0", 34,158 B, two paths | identical; `content-type: application/json;charset=utf-8`; no `orcid` in file; `swagger/v2/swagger.json` string confirmed inside `js/main.min.js` (7,286 B, re-fetched) | yes |
| 3 | `curl -sS -I .../documents/Data%20Elements%20for%20RePORTER%20Project%20API_V2.pdf` | 200, application/pdf, 690,439 B | identical (`last-modified: Fri, 25 Sep 2026 12:44:16 GMT`) | yes |
| 4 | "STAT1 gain of function", `projecttitle,terms,abstracttext`, limit 3 | total 289; 4R00AI177758-02 / 2R01AR073938-06A1 / 5R01AI191107-02 | total **289**, same three in same order; `meta.properties.URL` = `"https:/reporter.nih.gov/search/MAcLaHzK70mk5XV6Nt2gUQ/projects"` (single slash reproduced) | yes |
| 5 | "inborn errors of immunity" | total 545; 7R01AI088364-17 / 2R01AI088364-16A1 / 7R01AI127564-11 | total **545**, same three; `contact_pi_name` = `"CASANOVA, JEAN-LAURENT "` (trailing space reproduced) | yes |
| 6 | "chronic mucocutaneous candidiasis" | total 76; 7R01AI127564-11 / 5R01AI127564-10 / 1R21TR006398-01 | total **76**, same three | yes |
| 7 | "JAK3 severe combined immunodeficiency" | total 192; 5U01AI184132-03 / 3U01AI184132-03S1 / 1ZIABC012039-05 (FY2025, NCI, PAI) | total **192**, same three | yes |
| 8 | exact phrase `"\"chronic mucocutaneous candidiasis\""`, `search_field:"all"` | total 70 | total **70**; same top 3 as #6 | yes |
| 9 | `operator:"advanced"`, `STAT1 AND ("gain of function" OR "gain-of-function" OR GOF)` + `include_fields` | total 237, "same top 3" | total **237**; top 3 is the same *set* as #4 but order differs (2nd/3rd swapped: 5R01AI191107-02 then 2R01AR073938-06A1). `include_fields` honoured: records have exactly `fiscal_year, project_num, project_title` | yes (order nuance) |
| 10 | + `fiscal_years:[2025,2026]`, `include_active_projects:true` | total 62; award_amount 249000 / 756756 / 646763; all `is_active: true` | total **62**, identical amounts, `is_active: true` ×3, `agency_ic_admin` objects as documented | yes |
| 11 | `search_text:"STAT1"`, `search_field:"all"`, `limit:500` | total 3390, 500 rows | total **3390**, **500** rows (`include_fields` → only `project_num, project_title`) | yes |
| 12 | `{"search_id":"hsvM8Uq3M06EHdOGjeKNCA","offset":3,"limit":3,...}` | page 2 of the 289-hit search | 200, `total 289, offset 3`, rows 5P01AI179570-02 / 5R01AI127564-10 / 7R01AI127564-11. **The original session's `search_id` still resolved ~25 min later**, and today's fresh id `MAcLaHzK70mk5XV6Nt2gUQ` returned the identical page → `search_id` lifetime is ≥25 min (expiry still unknown beyond that) | yes |
| 13 | `project_nums:["4R00AI177758-02"]`, `limit:1`, 11-name `exclude_fields` | total 1 | total **1**; record has **34 keys = 45 − 11**; exactly the 11 excluded keys are absent. A second call with no `exclude_fields` returned the **45 keys in exactly the order listed in §4** | yes |
| 14 | `appl_ids:[11458029,9984930]` + 7 `include_fields` | total 2; 9984930 = 5R01AI085090-10 FY2020 CHATILA; 11458029 = 4R00AI177758-02 FY2026 PHILIPS | identical (`core_project_num` R01AI085090 / R00AI177758; `meta.sorted_by_relevance: false` here) | yes |
| 15 | IEI, `search_field:"all"`, `limit:50`, `include_fields:[ProjectNum,PrincipalInvestigators,ContactPiName]` | 76 PI objects, 17 multi-PI projects, PI key set exactly 7 names | **76** PI objects, **17** multi-PI, key set exactly `first_name, full_name, is_contact_pi, last_name, middle_name, profile_id, title` on all 76; 7R01AI088364-17 → Casanova (`true`, 9889423) + Zhang (`false`, 10812226); no `orcid` | yes |
| 16 | pubs `core_project_nums:["R00AI177758"]` | total 1: R00AI177758 / 40446018 / 11458029 | identical; `meta` = `{"search_id":null,"total":1,...,"sort_field":"coreproject","sort_order":"desc","sorted_by_relevance":false,"properties":{}}`, `facet_results: []` | yes |
| 17 | pubs 3 core project nums, `limit:500` | total 124; e.g. U01AI184132 / 40544999 / 11555650 | total **124**, 124 rows, example row present; split R01AI127564 = 95, U01AI184132 = 28, R00AI177758 = 1; one `applid` per grant (11577355 / 11555650 / 11458029) | yes |
| 18 | pubs `pmids:[28139313]` | total 5: ZIACL010304/11411917, ZIAAI001222/11403365, ZIAAI001122/11403323, R56AI115699/9108510, R01AI085090/9984930 | identical five rows, same order | yes |

### R.2 §6 / §2.2 / §3.6 "verified" claims, re-exercised (not in the command list but marked verified)

| Claim | Command (body only; all `POST`, `Content-Type: application/json`) | Observed |
| --- | --- | --- |
| projects `limit` 501 → 400 | `{"criteria":{"advanced_text_search":{"operator":"and","search_field":"all","search_text":"STAT1"}},"offset":0,"limit":501,"include_fields":["ProjectNum"]}` | `HTTP/2 400` `{"message":"System doesn't support limit value greater than 500. Please reduce your limit value."}` |
| projects `offset` 15000 → 400 | `{"criteria":{"advanced_text_search":{...,"search_text":"immunity"}},"offset":15000,"limit":1,...}` | `400` `{"message":"Requested limit exceeded. Maximum offset is 14,999."}` |
| publications `limit` 501 → 400 | `{"criteria":{"core_project_nums":["R01AI127564"]},"offset":0,"limit":501}` | `400`, same limit message |
| publications `offset` 10000 → 400 | `{"criteria":{"core_project_nums":["R01AI127564"]},"offset":10000,"limit":1}` | `400` `{"message":"System doesn't support offset value greater than 9,999. Please narrow down your search criteria."}` |
| zero hits → 200, total 0 | `search_text:"qzxv9876nonexistentterm"` | `200`, `{"meta":{"search_id":"PiQnbBAT4kGsm7I_uP7UFw","total":0,...},"results":[]}` |
| `publications_search` criterion ignored | `{"criteria":{"publications_search":{"pm_id":["28139313"],"appl_id":[11458029],"filter_appl_ids":true}},"offset":0,"limit":2,"include_fields":["ProjectNum","ApplId"]}` | `200`, **total 2,982,395** (same number as the original), first rows `261201500036I-0-26100009-2`, `5P01AG052350-08` — whole DB |
| `pm_id` as bare string → 400 array-of-strings | `{"criteria":{"publications_search":{"pm_id":"28139313"}},...}` | `400`; body is a JSON array of one string — **but see R.3(c): the string is HTML-entity-escaped** |
| snake_case `include_fields` → `{}` | `{"criteria":{"project_nums":["4R00AI177758-02"]},"limit":1,"include_fields":["project_num","project_title"]}` | `200`, `total 1`, `results: [{}]` — reproduced |
| pubs no-match → total 0 | `{"criteria":{"pmids":[1]},"offset":0,"limit":5}` | `200`, `total 0`, `results: []`, `facet_results: []` |
| `sort_field` / `sort_order` echoed | `{"criteria":{"project_nums":["5R01AI127564-10","7R01AI127564-11"]},"offset":0,"limit":2,"sort_field":"fiscal_year","sort_order":"desc",...}` | `meta.sort_field: "fiscal_year"`, `meta.sort_order: "desc"` |
| Sample-request PDF (was UNVERIFIED in §1) | `curl -sS -I "https://api.reporter.nih.gov/documents/Sample%20RePORTER%20API%20Request%20Matching%20ExPORTER%20Project%20Files.pdf"` | `HTTP/2 200`, `application/pdf`, `content-length: 348134` — now verified to exist |

### R.3 Corrections and additions (none reverse a verdict)

(a) **`project_num` is not a unique record key — P01 subprojects share it.** The 500-row page from
command 11 has only **487 distinct `project_num`** values (12 numbers appear 2–3×). Follow-up:

```bash
curl -sS -X POST "https://api.reporter.nih.gov/v2/projects/search" -H "Content-Type: application/json" \
  -d '{"criteria":{"project_nums":["5P01AI127335-10","5P01AI179570-02","5P01CA272161-05"]},"offset":0,"limit":20,"include_fields":["ApplId","SubprojectId","ProjectNum","CoreProjectNum","ProjectTitle"]}'
# → total 23 for 3 project numbers. e.g. 5P01AI127335-10 appears as appl_id 11389488 (subproject_id null,
#   "Human Cytomegalovirus dysregulation of host hematopoietic ...") AND appl_id 11389489 (subproject_id 6542,
#   "The Administrative Core"), 11389491 (6543, "Humanized Mouse Core"), ... 11389504 (6549)
```

So: record key = **`appl_id`**; grant key = `core_project_num` (as §0 says); and `project_nums` lookups
return subprojects too. The documented `exclude_subprojects` criterion (UNVERIFIED in §2.3) **works**:

```bash
curl -sS -X POST "https://api.reporter.nih.gov/v2/projects/search" -H "Content-Type: application/json" \
  -d '{"criteria":{"project_nums":["5P01AI127335-10","5P01AI179570-02","5P01CA272161-05"],"exclude_subprojects":true},"offset":0,"limit":20,"include_fields":["ApplId","SubprojectId","ProjectNum"]}'
# → total 3: (5P01AI127335-10, 11389488, null) (5P01CA272161-05, 11415565, null) (5P01AI179570-02, 11377982, null)
```

Recommend `"exclude_subprojects": true` in the pipeline's text searches unless subproject titles are wanted
(the "Illuminating pediatric immunology through genetics" hit in §3.1 page 2 is itself a subproject,
appl_id 11377999, subproject_id 6344).

(b) **PI `full_name` is dirty too, not just `contact_pi_name`.** On the 50-record IEI page, 39/76 PI
objects have a double space in `full_name` (`"Jean-Laurent  Casanova"` — produced when `middle_name` is
`""`), 42/76 have `title: ""`. On whole-DB rows `full_name` has triple spaces and trailing spaces
(`"Ioannis   Pappas "`, `"Clinton   Grubbs"`). §4.1's advice to prefer `principal_investigators` is right
for `profile_id`, but build display names from `first_name`/`middle_name`/`last_name` with whitespace
collapsing rather than trusting `full_name`. Of the two dirty `contact_pi_name` examples quoted in §4.1,
`" PAPPAS , IOANNIS "` is reproduced (row 2 of the whole-DB query with `include_fields:["ProjectNum","ContactPiName","PrincipalInvestigators"]`, project `5P01AG052350-08`); `"'GERARD, ROBE…"` was **not**
reproduced in this re-run (not in the first 5 whole-DB rows) — treat that specific example as UNVERIFIED,
the leading-space / space-before-comma class is confirmed. 20 of 50 IEI records had a trailing space in
`contact_pi_name`.

(c) **Type-error 400 bodies are HTML-entity-escaped inside the JSON string.** §2.2 quotes the error with
plain quotes; the raw bytes are:
`["Error converting value &quot;28139313&quot; to type &#39;System.Collections.Generic.IEnumerable`1[System.String]&#39;. Path &#39;criteria.publications_search.pm_id&#39;, line 1, position 54."]`
— so `json.loads` then `html.unescape` if the message is logged. Also, the `api-supported-versions: 2.0`
header (§1: "on every API call") was **absent on this model-binding 400** but present on the cap 400s and
all 200s.

(d) **Rate-limit headers are not a single global counter.** Across 30 calls in ~5 min at ≤1 req/s,
`x-rate-limit-remaining` did not decrement monotonically (sequence incl. 199,198,199,198,197,197,196,196,
199,198,197,196,199,...) and **11 distinct `x-rate-limit-reset` timestamps** appeared, with consecutive
calls often landing on different buckets (e.g. #4 and #5 shared reset `19:46:17.277`; #6/#7/#8 shared
`19:46:20.023`; #9 was back on `19:46:17.277`). This is consistent with several load-balanced backends each
keeping its own 200/min bucket. §6's "200 requests per 1-minute window" is the per-bucket ceiling as
inferred; the effective global allowance may be higher, but the client should still budget 200/min and
follow the docs' 1 req/s request — §6's recommendation stands.

(e) Trivia: §4.1's `terms` sample drops one token — the string actually begins
`"<(IFN) α><(IFN)-α><(IFN)α><ATAC><Aberrant Chromosome><Affect><Alferon>"`. §3.1's variant line "same top 3"
for the `advanced` search is true as a set but not as an order (R.1 #9). `cong_dist` on the Philips record
is `"OH-03"` (not listed in §4.1; the field is in the 45-key list).

### R.4 Still unverified after this pass

camelCase request-body keys; `pi_profile_ids`, `pi_names`, `org_names`, date-range and the other §2.3
criteria (only `exclude_subprojects` was added above); `project_nums` wildcard `*`; `search_id` expiry
beyond ~25 min; the `"'GERARD, ROBE…"` example; `Data Access Policy` link target (its `href` is empty in the
static HTML — `<a href="" id="dapUrl">` — and is filled by JS, so it was not followed).
