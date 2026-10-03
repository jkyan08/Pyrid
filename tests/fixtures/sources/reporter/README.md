# NIH RePORTER API v2 — recorded fixtures

**Capture date: 2026-10-03** (re-run session 19:44–19:50 UTC documented in
`docs/recon/reporter.md` → "Re-run check (second reviewer)"; the raw bodies sat in the recon
scratchpad as `rerun_reporter/<tag>.json` with response headers in `hdr_<tag>.txt`).
Cheat sheet: `docs/source-apis.md` §3.

Every file here is a **verbatim excerpt of a real API response**. The only edits allowed were
(a) truncating long arrays to a few representative items and (b) scrubbing e-mail addresses and
phone numbers. No value inside any record was changed. Nothing was fetched from the network to
build this directory. The fixtures come from the *re-run*, so `meta.search_id` values differ from
the ones quoted for the original session in the cheat sheet (e.g. `MAcLaHzK70mk5XV6Nt2gUQ` here vs
`hsvM8Uq3M06EHdOGjeKNCA` in §3a) — totals, record identities and field paths are identical.

Base URL `https://api.reporter.nih.gov`. Both endpoints are `POST`, JSON in / JSON out, no auth.
Every request below carried `-H "Content-Type: application/json"`.

## Files

| File | Raw capture | HTTP | Bytes | Trimmed? |
|---|---|---|---|---|
| `projects_search_page.json` | `rerun_reporter/04_stat1_gof.json` | 200 | 37,569 | no — byte-identical to the capture |
| `projects_search_empty.json` | `rerun_reporter/x5_zero_hits.json` | 200 | 241 | no — byte-identical |
| `publications_search.json` | `rerun_reporter/17_pubs_3grants.json` | 200 | 504 | **yes** — `results` 124 → 5 rows (capture was 8,120 B) |
| `error_400.json` | `rerun_reporter/x1_proj_limit501.json` | 400 | 98 | no — byte-identical |
| `error_400_model_binding.json` | `rerun_reporter/x7_pubsearch_pmid_string.json` | 400 | 194 | no — byte-identical |

All bodies were served as `content-type: application/json; charset=utf-8`, single-line, no
trailing newline (the byte-identical copies keep that, so `content-length` in the header capture
equals the file size).

### `projects_search_page.json` — `POST /v2/projects/search`, 3 full records

```bash
curl -sS -X POST "https://api.reporter.nih.gov/v2/projects/search" -H "accept: application/json" -H "Content-Type: application/json" \
  -d '{"criteria":{"advanced_text_search":{"operator":"and","search_field":"projecttitle,terms,abstracttext","search_text":"STAT1 gain of function"}},"offset":0,"limit":3}'
```

Response headers seen: `HTTP/2 200`, `date: Sat, 03 Oct 2026 19:45:17 GMT`, `content-length: 37569`,
`api-supported-versions: 2.0`, `x-rate-limit-limit: 1m`, `x-rate-limit-remaining: 199`,
`x-rate-limit-reset: 2026-10-03T19:46:17.2773585Z`.

`meta` is exactly as returned: `{"search_id":"MAcLaHzK70mk5XV6Nt2gUQ","total":289,"offset":0,"limit":3,"sort_field":null,"sort_order":"ASC","sorted_by_relevance":true,"properties":{"URL":"https:/reporter.nih.gov/search/MAcLaHzK70mk5XV6Nt2gUQ/projects"}}`
(note the malformed single-slash `properties.URL` — real, not a typo).

The live response had exactly three records (`limit: 3`), so **nothing was truncated**: each
record has all 45 keys in server order, and the long strings (`terms` ≈5.5 KB angle-bracket
delimited, `pref_terms` ≈1.7 KB `;`-delimited, `abstract_text`, `phr_text`) are whole. Every
array in these records has ≤ 2 items (`principal_investigators`, `program_officers`,
`agency_ic_fundings`, `organization.org_duns`, `organization.org_ueis`), so none was cut.

| `appl_id` | `project_num` | `core_project_num` | FY | IC | `principal_investigators[0]` | `contact_pi_name` |
|---|---|---|---|---|---|---|
| 11458029 | `4R00AI177758-02` | `R00AI177758` | 2026 | NIAID | Philips, `profile_id` 14185972, `is_contact_pi: true` | `"PHILIPS, RACHAEL LAURA"` |
| 11449228 | `2R01AR073938-06A1` | `R01AR073938` | 2026 | NIAMS | Jackson, `profile_id` 11998787 | `"JACKSON, SHAUN WILLIAM"` |
| 11291853 | `5R01AI191107-02` | `R01AI191107` | 2026 | NIAID | Cingolani, `profile_id` 8029719, `middle_name: ""`, `full_name: "Gino  Cingolani"` (double space) | `"CINGOLANI, GINO "` (trailing space) |

The third record is deliberately useful: it carries the real whitespace dirt described in the
cheat sheet (`full_name` double space when `middle_name` is `""`, trailing space in
`contact_pi_name`), so a name-normalisation test has a genuine case. Record 2's `program_officers[0].full_name` is `"MARIE  MANCINI"` (same double-space pattern).

### `projects_search_empty.json` — zero hits are HTTP 200, not 404

```bash
# Endpoint + body as recorded in the recon note (§3.6 / R.2). The note recorded only
# search_text; offset/limit are confirmed by the response meta; operator/search_field were NOT
# recorded for this call and are the §3 template values (the only ones used in that session).
curl -sS -X POST "https://api.reporter.nih.gov/v2/projects/search" -H "Content-Type: application/json" \
  -d '{"criteria":{"advanced_text_search":{"operator":"and","search_field":"projecttitle,terms,abstracttext","search_text":"qzxv9876nonexistentterm"}},"offset":0,"limit":3}'
```

Headers: `HTTP/2 200`, `date: Sat, 03 Oct 2026 19:47:56 GMT`, `content-length: 241`,
`api-supported-versions: 2.0`, `x-rate-limit-remaining: 198`.
Body: `meta.total` is `0`, `results` is `[]`, and a fresh `search_id` is still issued. A parser must
test `meta.total == 0`, never rely on a non-200 status.

### `publications_search.json` — `POST /v2/publications/search`, trimmed to 5 rows

```bash
curl -sS -X POST "https://api.reporter.nih.gov/v2/publications/search" -H "Content-Type: application/json" \
  -d '{"criteria":{"core_project_nums":["R01AI127564","R00AI177758","U01AI184132"]},"offset":0,"limit":500}'
```

Headers: `HTTP/2 200`, `date: Sat, 03 Oct 2026 19:47:48 GMT`, `content-length: 8120` (the full
124-row body), `api-supported-versions: 2.0`, `x-rate-limit-remaining: 198`.

**Truncation:** the live `results` array had 124 rows (95 × `R01AI127564`, 28 × `U01AI184132`,
1 × `R00AI177758`, sorted `coreproject desc`). Kept rows are original indices 0, 1, 28, 29 and
123 in their original order: two for `U01AI184132` (`applid` 11555650), two for `R01AI127564`
(`applid` 11577355) and the single `R00AI177758` row (`pmid` 40446018, `applid` 11458029), so
every requested grant and every distinct `applid` is represented. `meta` (`"total":124`,
`"limit":500`, `"sort_field":"coreproject"`, `"sort_order":"desc"`, `"search_id":null`) and
`facet_results` (`[]`) are untouched. **`meta.total` (124) therefore ≠ `len(results)` (5) on
purpose** — read the count from `meta.total`. Serialisation uses the server's compact separators
(verified to reproduce the full capture byte-for-byte before trimming), so the file is the wire
format minus 119 rows.

Row shape is `{"coreproject":"U01AI184132","pmid":40544999,"applid":11555650}` — keys have **no
underscores** (unlike the projects endpoint); no title/journal/authors (fetch from PubMed by PMID);
`applid` is the *latest* application of that grant, one per `coreproject`.

### `error_400.json` — paging-cap error (`{"message": …}` shape)

```bash
curl -sS -X POST "https://api.reporter.nih.gov/v2/projects/search" -H "Content-Type: application/json" \
  -d '{"criteria":{"advanced_text_search":{"operator":"and","search_field":"all","search_text":"STAT1"}},"offset":0,"limit":501,"include_fields":["ProjectNum"]}'
```

Headers: `HTTP/2 400`, `date: Sat, 03 Oct 2026 19:47:50 GMT`, `content-type: application/json; charset=utf-8`,
`api-supported-versions: 2.0` (present on cap-400s), `x-rate-limit-remaining: 199`.
Body: `{"message":"System doesn't support limit value greater than 500. Please reduce your limit value."}`.
The same shape was captured (not included here) for `offset` 15000 on projects
(`"Requested limit exceeded. Maximum offset is 14,999."`), `limit` 501 on publications (same
limit message) and `offset` 10000 on publications
(`"System doesn't support offset value greater than 9,999. Please narrow down your search criteria."`).

### `error_400_model_binding.json` — type error (JSON array of strings, HTML-entity-escaped)

```bash
# Endpoint + body exactly as recorded in the recon note (R.2): pm_id sent as a bare string
# instead of ["28139313"]. The note wrote the trailing top-level keys literally as `,...}` (not
# recorded), so this is the recorded prefix, not a complete replayable body.
curl -sS -X POST "https://api.reporter.nih.gov/v2/projects/search" -H "Content-Type: application/json" \
  -d '{"criteria":{"publications_search":{"pm_id":"28139313"}},...}'
```

The error's `line 1, position 54` is exactly the closing quote of that `"28139313"` value in the
body above (byte 54 of the recorded prefix), which pins the request shape even without the tail.

Headers: `HTTP/2 400`, `date: Sat, 03 Oct 2026 19:47:59 GMT`, `content-length: 194`,
**no `api-supported-versions` header** (absent on model-binding 400s), `x-rate-limit-remaining: 196`.
Body is a JSON **array of one string** whose quotes are HTML-escaped (`&quot;`, `&#39;`) — parse
with `json.loads` then `html.unescape` before logging. A client must therefore accept both 400
shapes: `dict` with `message`, and `list[str]`.

## What was scrubbed

**Nothing needed scrubbing.** All five bodies were scanned for e-mail addresses
(`[\w.%+-]+@[\w.-]+\.\w{2,}`) and phone numbers (`\(?\d{3}\)?[-. ]\d{3}[-. ]\d{4}`), and for `@`
at all: zero matches. RePORTER project records have no contact fields among their 45 keys (no
e-mail, phone or address field exists; `organization` carries only name, city, state, country,
ZIP, DUNS/UEI and dept codes). `principal_investigators[]` objects and `program_officers[]`
objects are kept intact — PI and program-officer names are public NIH grant records. The header
captures contain AWS load-balancer session cookies (`set-cookie: AWSALB…`); header files were not
copied into the repo.

## Field paths a parser should use (from `docs/source-apis.md` §3b)

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
| Publications | none on project record | hop via `core_project_num` → `/v2/publications/search`; rows `{coreproject, pmid, applid}` |
| Paging | `meta.{total,offset,limit,search_id}` | `search_id` re-fetches the same result set (`{"search_id":…,"offset":3,"limit":3}`); lifetime ≥ 25 min, expiry unverified |

## Gotchas the parser and client must encode

1. **snake_case request-body keys, PascalCase `include_fields` / `exclude_fields` values.**
   Body keys are `criteria`, `offset`, `limit`, `sort_field`, `sort_order`, `include_fields`,
   `exclude_fields`, `search_id`. The *values* of `include_fields`/`exclude_fields` are PascalCase
   data-element names: `ApplId, ProjectNum, ProjectTitle, FiscalYear, CoreProjectNum, ContactPiName,
   PrincipalInvestigators, AgencyIcAdmin, IsActive, AwardAmount, SubprojectId, AbstractText, PhrText,
   Terms, PrefTerms, Organization, AgencyIcFundings, FullStudySection, ProjectNumSplit, GeoLatLon,
   ProgramOfficers` (all verified live). snake_case values such as `["project_num","project_title"]`
   are **silently accepted and return `{}` for every record** (HTTP 200, `total` correct,
   `results: [{}]`). The swagger file documents camelCase body keys (`includeFields`) — misleading;
   only snake_case keys were verified. Response keys, by contrast, are snake_case
   (`appl_id`, `core_project_num`, …) — see `projects_search_page.json`.
2. **`appl_id` vs `core_project_num` (vs `project_num`).** `appl_id` is the per-record key: one
   application = one fiscal year of one grant (or one P01 subproject). `project_num`
   (`4R00AI177758-02`) encodes type/activity/IC/serial/support-year and is **not unique**:
   subprojects share it. `core_project_num` (`R00AI177758`) is the grant identity across years and
   award types — the dedupe key for Grant nodes. `meta.total` counts applications, not grants.
   Use `"exclude_subprojects": true` in text searches (verified: 23 → 3 for three P01 numbers).
   In `publications_search.json`, `applid` is the grant's *latest* application, so the reverse hop
   `criteria.appl_ids` returns a single fiscal-year record per grant.
3. **Zero hits → HTTP 200 with `meta.total: 0`** (`projects_search_empty.json`); a `search_id`
   is still issued.
4. **Two 400 body shapes**: cap errors are `{"message": …}` (`error_400.json`); model-binding
   errors are a JSON array of HTML-entity-escaped strings (`error_400_model_binding.json`) and
   lack the `api-supported-versions` header.
5. **Caps**: projects `limit` ≤ 500, `offset` ≤ 14,999 (≤ 15,499 records per query — split by
   `fiscal_years`); publications `limit` ≤ 500, `offset` ≤ 9,999. Default `limit` is 50.
6. **Rate limit**: docs ask for ≤ 1 request/s (IP block possible); headers show
   `x-rate-limit-limit: 1m` with `x-rate-limit-remaining` starting at 199 — ~200/min per
   load-balanced bucket (several buckets observed, so the counter is not monotonic). Budget
   200/min and sleep ≥ 1 s.
7. `meta.properties.URL` is malformed (`https:/reporter.nih.gov/…`, single slash) — use
   `project_detail_url` on the record as the source link.
8. `"operator":"and"` is bag-of-words over title + abstract + terms (the SLE record in
   `projects_search_page.json` matched "STAT1" and "gain of function" separately); results are
   relevance-ranked (`sorted_by_relevance: true`) even without `use_relevance`.
9. `criteria.publications_search` inside a project search is accepted but ignored (returns the
   whole DB, total 2,982,395) — link projects to PMIDs only via `/v2/publications/search`.
