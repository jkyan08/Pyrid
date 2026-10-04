# ClinicalTrials.gov API v2 fixtures

**Capture date: 2026-10-03** (UTC ~19:22–19:50); a second fetch at ~19:40–19:50 UTC returned the four study records,
`metadata.json` and `enums.json` md5-identical to the first capture. Server state at capture: `apiVersion` **2.0.5**, `dataTimestamp`
**2026-10-02T09:00:04**, every record's `derivedSection.miscInfoModule.versionHolder` = `"2026-10-02"`.
No API key exists or was used. Base URL `https://clinicaltrials.gov/api/v2`.

Every file is a **verbatim excerpt** of a real response. Values were never edited except for
(a) list truncation and (b) contact-data scrubbing, both itemised per file below.

## Files

| Fixture | Raw capture file | Exact command / URL | Truncated | Scrubbed |
| --- | --- | --- | --- | --- |
| `version.json` | `version.json` | `curl -s https://clinicaltrials.gov/api/v2/version` | nothing (71 B verbatim) | nothing |
| `search_page.json` | `page1.json` (byte-identical to `rerun/pg1.json`) | `curl -sG 'https://clinicaltrials.gov/api/v2/studies' --data-urlencode 'query.cond="hyper IgE syndrome"' --data-urlencode 'fields=NCTId' --data-urlencode 'pageSize=5' --data-urlencode 'countTotal=true'` | `studies[]` 5 → 2 (kept NCT00005933, NCT00006150). `totalCount` (14), `nextPageToken` and key order kept exactly as returned; raw line layout kept | nothing |
| `search_page_2.json` | `rerun/pg2.json` | `curl -sG 'https://clinicaltrials.gov/api/v2/studies' --data-urlencode 'query.cond="hyper IgE syndrome"' --data-urlencode 'fields=NCTId' --data-urlencode 'pageSize=5' --data-urlencode 'countTotal=true' --data-urlencode 'pageToken=ZVNj7o2Elu8o3lpwTcito62tmpOQJJxuZvWs1PcckA'` (page 1's `nextPageToken`) | nothing (425 B verbatim, 5 studies) | nothing |
| `search_page_last.json` | `rerun/pg3.json` | `curl -sG 'https://clinicaltrials.gov/api/v2/studies' --data-urlencode 'query.cond="hyper IgE syndrome"' --data-urlencode 'fields=NCTId' --data-urlencode 'pageSize=5' --data-urlencode 'countTotal=true' --data-urlencode 'pageToken=ZVNj7o2Elu8o3lpwTcito62tmpOQJJxtYvSl3PcdkA'` (page 2's `nextPageToken`) | nothing (294 B verbatim, 4 studies) | nothing |
| `search_empty.json` | `rerun/q1.json` | `curl -sG 'https://clinicaltrials.gov/api/v2/studies' --data-urlencode 'query.intr=ruxolitinib' --data-urlencode 'query.term=STAT1' --data-urlencode 'fields=NCTId,BriefTitle,OverallStatus' --data-urlencode 'countTotal=true' --data-urlencode 'pageSize=50'` | nothing (31 B verbatim) | nothing |
| `search_stat1_gof.json` | `rerun/q3.json` | `curl -sG 'https://clinicaltrials.gov/api/v2/studies' --data-urlencode 'query.term="STAT1 gain of function"' --data-urlencode 'fields=NCTId,BriefTitle,OverallStatus' --data-urlencode 'countTotal=true' --data-urlencode 'pageSize=50'` | nothing (711 B verbatim, 3 studies) | nothing |
| `study_NCT07670156.json` | `study_NCT07670156.json` | `curl -s https://clinicaltrials.gov/api/v2/studies/NCT07670156` | nothing (full record; 16,939 B raw → 16,866 B after scrubbing) | 12 values: 4 in `centralContacts[]`, 8 in `locations[].contacts[]` |
| `study_NCT07261891.json` | `study_NCT07261891.json` | `curl -s https://clinicaltrials.gov/api/v2/studies/NCT07261891` | nothing (full record; 10,221 B raw → 10,177 B after scrubbing) | 4 values: 2 in `centralContacts[]`, 2 in `locations[].contacts[]` (one is `phoneExt`) |
| `study_NCT07262983.json` | `study_NCT07262983.json` | `curl -s https://clinicaltrials.gov/api/v2/studies/NCT07262983` | nothing (full record; 22,962 B raw → 22,920 B after scrubbing) | 6 values: 2 in `centralContacts[]`, 4 in `locations[].contacts[]` |
| `study_NCT05561985.json` | `study_NCT05561985.json` | `curl -s https://clinicaltrials.gov/api/v2/studies/NCT05561985` | nothing (full record; 1,886 B raw → 1,874 B after scrubbing) | 2 values in `centralContacts[]` (record has no `locations`) |
| `error_404_NCT04409146.txt` | `study_full.json` | `curl -s https://clinicaltrials.gov/api/v2/studies/NCT04409146` → HTTP 404, `text/plain; charset=UTF-8` | nothing (32 B verbatim body) | nothing |
| `enums.json` | `enums.json` (byte-identical to `rerun/enums.json`) | `curl -s https://clinicaltrials.gov/api/v2/studies/enums` | top-level list 41 → 17 enums (the ones the pipeline validates against, see below); each kept enum object is complete (`type`, `values[].{value,legacyValue}`, `pieces`). Re-serialised with `json.dumps(indent=2)`, so whitespace differs from the server's `"key" : value` style | nothing |
| `metadata.json` | `metadata.json` (byte-identical to `rerun/metadata.json`) | `curl -s https://clinicaltrials.gov/api/v2/studies/metadata` | 175,633 B → ~38 KB: only `children` lists were truncated, to the nodes on the pipeline field paths listed below (plus `hasResults`). Dropped top-level nodes `resultsSection`, `annotationSection`, `documentSection`; dropped non-pipeline modules and the `centralContacts` / `locations` subtrees. Every kept node object is complete (`name`, `piece`, `title`, `sourceType`, `type`, `description`, `rules`, `altPieceNames`, `dedLink`, `maxChars`, …). Re-serialised with `json.dumps(indent=2)` | nothing |

`search_page.json` + `search_page_2.json` + `search_page_last.json` are pages 1–3 of the same query
(14 hits, `pageSize=5`): page 1 carries `totalCount` and `nextPageToken`; page 2 has **no**
`totalCount` but a `nextPageToken`; the last page has neither.

### Not included (and why)

- `search1.json` and `searches_3.json` among the raw captures are **NIH RePORTER** `/v2/projects/search`
  responses (`meta.search_id`, `results[].appl_id`), not ClinicalTrials.gov. They belong to the
  `reporter` fixtures, not here.
- `search_areas.json` (25,359 B, `GET /studies/search-areas`): not needed by the parser; what each
  `query.*` parameter searches is summarised under "Query areas" below.
- 301 alias redirect: `rerun/alias_hdr.txt` recorded `GET /studies/NCT05033340` → `HTTP/2 301`,
  `location: /api/v2/studies/NCT04173273`, `content-length: 0`, no body. The followed record is 701 KB,
  so it is not included; test redirect handling with a stub.
- `resultsSection`: `rerun/s00527878.json` (`GET /studies/NCT00527878`, 30,237 B, `hasResults: true`)
  was captured but is not included to stay inside the size budget. Shape: 4th top-level key
  `resultsSection` with modules `participantFlowModule, baselineCharacteristicsModule,
  outcomeMeasuresModule, adverseEventsModule, moreInfoModule`.

## Scrubbing

Every value of the keys `email`, `phone` and `phoneExt` in the four study records was replaced with
the string `"[scrubbed]"` (regex on the raw text, so the files are otherwise byte-identical to the
server response; a structural diff confirmed no other value changed). These keys occur only in
`protocolSection.contactsLocationsModule.centralContacts[]` and
`protocolSection.contactsLocationsModule.locations[].contacts[]`; `overallOfficials[]` carries no
contact data. `name`, `role`, `affiliation`, `facility`, `city`, `state`, `zip`, `country`,
`status` and `geoPoint` were kept. No other email address or phone number exists anywhere in the
records (the remaining digit runs are DOIs inside `referencesModule.references[].citation`).
Reason: the ClinicalTrials.gov Terms and Conditions (last updated 2023-01-31) forbid using email
addresses extracted from the database for marketing or promotional purposes, and we never want
contact data in the repo. Pipeline code must likewise never persist these two fields.

## Response envelopes

- `GET /studies` → `{"totalCount": <int, only when countTotal=true AND page 1>, "studies": [...], "nextPageToken": "<only when the page was full>"}`.
  `studies[]` items carry only the requested `fields`, in the nested record shape. A page may have
  an empty `studies` array (the server emits `[\n\n]` for it, see `search_empty.json`). Subsequent
  pages must repeat every parameter except `countTotal`, `pageSize`, `pageToken`.
- `GET /studies/{nctId}` → `{"protocolSection": {...}, "derivedSection": {...}, "hasResults": <bool>}`,
  plus `"resultsSection"` only when `hasResults` is true. Requested-but-absent fields are omitted (a
  module can come back as `{}`).
- Errors are **plain text, not JSON** (`error_404_NCT04409146.txt`; 400s such as
  `Parameter 'fields' contains invalid field name: 'NotARealField'` likewise).

## Field paths a parser should use (dotted path → `fields` piece name)

From `docs/source-apis.md` §2b; all verified live on these records.

- `protocolSection.identificationModule.nctId` → `NCTId`; `.briefTitle` → `BriefTitle`; `.officialTitle` → `OfficialTitle`; `.nctIdAliases[]` → `NCTIdAlias`
- `protocolSection.statusModule.overallStatus` → `OverallStatus` (enum `Status`); `.startDateStruct.{date,type}` → `StartDate`/`StartDateType`; `.primaryCompletionDateStruct.{date,type}` → `PrimaryCompletionDate`; `.lastUpdatePostDateStruct.date` → `LastUpdatePostDate`; `.studyFirstPostDateStruct.date` → `StudyFirstPostDate`
- `protocolSection.sponsorCollaboratorsModule.leadSponsor.{name,class}` → `LeadSponsor`/`LeadSponsorName`; `.collaborators[].name` → `CollaboratorName`; `.responsibleParty.type` → `ResponsiblePartyType`
- `protocolSection.conditionsModule.conditions[]` → `Condition` (free text, e.g. `"STAT1 GOF"`); `.keywords[]` → `Keyword`
- `protocolSection.designModule.studyType` → `StudyType`; `.phases[]` → `Phase`; `.enrollmentInfo.{count,type}` → `EnrollmentInfo`/`EnrollmentCount`; `.designInfo.allocation` → `DesignAllocation`; `.designInfo.primaryPurpose` → `DesignPrimaryPurpose`
- `protocolSection.armsInterventionsModule.interventions[].{type,name,otherNames[],description,armGroupLabels[]}` → `Intervention`/`InterventionType`/`InterventionName`
- `protocolSection.eligibilityModule.{eligibilityCriteria,sex,minimumAge,maximumAge,stdAges[],healthyVolunteers}` → `EligibilityModule` (branch)
- `protocolSection.contactsLocationsModule.overallOfficials[].{name,affiliation,role}` → `OverallOfficialName`; `.locations[].{facility,city,state,country,geoPoint}`; `.centralContacts[]` and `.locations[].contacts[]` have `name, role, phone|phoneExt, email` — **do not persist `phone`/`phoneExt`/`email`**
- `protocolSection.referencesModule.references[].{pmid,type,citation}` → `ReferencePMID`/`ReferenceType`/`ReferenceCitation` (present on NCT07261891 and NCT07262983, absent on NCT07670156 and NCT05561985)
- `derivedSection.conditionBrowseModule.meshes[].{id,term}`, `.ancestors[].{id,term}` (MeSH is the only coded disease id; absent on NCT05561985); `derivedSection.interventionBrowseModule.meshes[]`; `derivedSection.miscInfoModule.versionHolder` (data-version date to display)
- `hasResults` → `HasResults`; `resultsSection` only when true

Verified one-call pipeline `fields` set (HTTP 200 on NCT07670156):
`NCTId,BriefTitle,OfficialTitle,OverallStatus,StartDate,StartDateType,PrimaryCompletionDate,LastUpdatePostDate,StudyFirstPostDate,LeadSponsor,CollaboratorName,ResponsiblePartyType,Condition,Keyword,StudyType,Phase,EnrollmentInfo,DesignAllocation,DesignPrimaryPurpose,Intervention,EligibilityModule,OverallOfficialName,ReferenceType,HasResults`
+ `derivedSection.conditionBrowseModule,derivedSection.interventionBrowseModule,derivedSection.miscInfoModule`.
`metadata.json` here is trimmed to exactly these nodes (plus `nctIdAliases` and `references[].{pmid,citation}`).

### Record-shape gotchas the fixtures exercise

- `study_NCT05561985.json` is a sparse **expanded-access** record: no `startDateStruct`, `phases`,
  `enrollmentInfo`, `sex`, `minimumAge`, `locations`, `oversightModule`, `outcomesModule`,
  `referencesModule`, `conditionBrowseModule`; `designModule` is
  `{"studyType":"EXPANDED_ACCESS","nPtrsToThisExpAccNctId":2,"expandedAccessTypes":{"individual":true}}`;
  `overallStatus` is `AVAILABLE`. Every extractor field must be optional.
- `study_NCT07670156.json` has 11 protocol modules (no `referencesModule`/`ipdSharingStatementModule`);
  NCT07261891 and NCT07262983 have 13.
- Dates are partial ISO strings (`"2026-10"`); ages are strings with units (`"12 Years"`);
  `eligibilityCriteria` is markdown with escapes (`\\<`, `\\-`).
- `conditions[]` is free text; map to MONDO downstream via `derivedSection.conditionBrowseModule.meshes[].id`.

## Enums kept in `enums.json` (values as returned; `pieces` names the fields they apply to)

`Status` (`OverallStatus`, `LastKnownStatus`), `ExpandedAccessStatus`, `RecruitmentStatus` (`LocationStatus`),
`DateType`, `EnrollmentType`, `StudyType`, `Phase`, `DesignAllocation`, `PrimaryPurpose` (`DesignPrimaryPurpose`),
`InterventionType`, `Sex`, `StandardAge` (`StdAge`), `AgencyClass` (`OrgClass`, `LeadSponsorClass`, `CollaboratorClass`),
`ResponsiblePartyType`, `ContactRole`, `OfficialRole`, `ReferenceType`.

`Status` values: `ACTIVE_NOT_RECRUITING, COMPLETED, ENROLLING_BY_INVITATION, NOT_YET_RECRUITING, RECRUITING, SUSPENDED, TERMINATED, WITHDRAWN, AVAILABLE, NO_LONGER_AVAILABLE, TEMPORARILY_NOT_AVAILABLE, APPROVED_FOR_MARKETING, WITHHELD, UNKNOWN`.
`Phase`: `NA, EARLY_PHASE1, PHASE1, PHASE2, PHASE3, PHASE4`. `StudyType`: `EXPANDED_ACCESS, INTERVENTIONAL, OBSERVATIONAL`.

## Query areas (from `GET /studies/search-areas`, not included)

- `query.cond` → `ConditionSearch` (7 pieces: `Condition, BriefTitle, OfficialTitle, ConditionMeshTerm, ConditionAncestorTerm, Keyword, NCTId`)
- `query.intr` → `InterventionSearch` (12 pieces incl. `InterventionName, InterventionOtherName, InterventionMeshTerm, ArmGroupLabel, Keyword`)
- `query.term` → `BasicSearch` (57 pieces incl. `BriefSummary` and outcome measures, so bare gene symbols hit biomarker mentions)
- Default `EXPANSION[Relaxation]` fuzzy-matches gene symbols (`query.cond=STAT1` → 14 with Abbott i-STAT device noise; `query.cond=EXPANSION[None]STAT1` → 8 clean). `EXPANSION[None]` is case-sensitive, so never apply it to quoted disease phrases.

## Terms

ClinicalTrials.gov T&C (last updated 2023-01-31): attribute the source, keep data current, display
the date the data were processed (`versionHolder` / `dataTimestamp`), state modifications, and never
use extracted email addresses for marketing. Store `retrieved_at`, `dataTimestamp` and
`versionHolder` on every Study node. No rate limit is documented; throttle as a courtesy and cache.
