# ClinVar (NCBI E-utilities) fixtures

**Capture date: 2026-10-03** (recon pass, no API key). Every file is a verbatim excerpt of a real
response recorded under `scratchpad/recon/`; the only edits are the truncations listed below.
ClinVar build seen in the responses: einfo `dbbuild` `Build260929-0200.1`, `lastupdate` `2026/09/29 05:12`,
`count` `4647675`. Field semantics come from `docs/source-apis.md` §1 and `docs/recon/clinvar.md`.

Base URL for every E-utility below: `https://eutils.ncbi.nlm.nih.gov/entrez/eutils/`.
`curl` needs `-g` (globoff) or `%5B`/`%5D`, otherwise `[gene]` is eaten as a shell glob.

## Files

| File | Bytes | Raw capture | Exact command / URL |
|---|---|---|---|
| `esearch.json` | 322 | `esearch_stat1_history.json` | `curl -sg '…/esearch.fcgi?db=clinvar&term=STAT1[gene]&usehistory=y&retmax=0&retmode=json'` |
| `esummary_small.json` | 35,760 | 5 esummary responses (see table below) | `…/esummary.fcgi?db=clinvar&…&retmode=json` — composite, see below |
| `efetch_vcv.xml` | 18,576 | `efetch_vcv_4959183_4959185.xml` | `curl -s '…/efetch.fcgi?db=clinvar&rettype=vcv&is_variationid&id=4959183,4959185&from_esearch=true'` |
| `einfo.json` | 7,203 | `einfo_clinvar.json` | `curl -s '…/einfo.fcgi?db=clinvar&retmode=json'` |
| `variant_summary_head.tsv` | 3,359 | `variant_summary_head.tsv` | HTTP `GET https://ftp.ncbi.nlm.nih.gov/pub/clinvar/tab_delimited/variant_summary.txt.gz` with `Range: bytes=0-524287` (→ `206`), partial gunzip of the 512 KiB prefix, first lines kept |

### `esearch.json`
Byte-for-byte copy (trailing newline included). History-server form: JSON keys are lower-case
`querykey` / `webenv`; the parameters you send back to esummary are `query_key` / `WebEnv`. `idlist` is
empty because `retmax=0`. The WebEnv is session-specific and expired long ago — a parser must not
hard-code it. Count for `STAT1[gene]` was 863.

### `esummary_small.json`
Structure is exactly NCBI's: `{"header":{"type":"esummary","version":"0.3"},"result":{"uids":[…],"<uid>":{…}}}`.
Seven real records from five different real responses were merged under one `result`; `result.uids`
lists them in the order they appear. The file is pretty-printed (indent 1) — NCBI returns the same JSON
compact on one line; whitespace is the only difference. Every record is unmodified except the one noted.

| uid | Source response (raw file) | Why it is here |
|---|---|---|
| `4959183` | `esummary.fcgi?db=clinvar&id=4959190,4959188,4959187,4959185,4959183&retmode=json` (`esummary_stat1_first5.json`) | STAT1 missense SNV `NM_007315.4(STAT1):c.986C>T (p.Pro329Leu)`, `protein_change` `"P299L, P329L, P296L, P276L, P331L, P341L"` (single-letter, one token per isoform, MANE token **not** first), `molecular_consequence_list` with 2 terms, Likely pathogenic. Same variant as `efetch_vcv.xml`. |
| `30083` | STAT1 history page `esummary.fcgi?db=clinvar&query_key=1&WebEnv=MCID_6ac156ed2b400ad4630bbacb&retstart=500&retmax=500&retmode=json` (`esummary_stat1_p1.json`) | Hero variant R274W. **6 `trait_set` entries** incl. a duplicate trait and a no-xref trait (`STAT1-related disorder`), `trait_xrefs` with MONDO (`MONDO:0013427`, prefix included) / OMIM / Orphanet / MedGen; carries both the GoF (OMIM 613796) and LoF (OMIM 614892) conditions. `variation_xrefs`: ClinGen, UniProtKB, OMIM allelic `600555.0008`, dbSNP (no `rs` prefix). 10 SCV / 4 RCV. `accession_version` `.56`. |
| `2500092` | STAT1 history page `…retstart=0&retmax=500&retmode=json` (`esummary_stat1_p0.json`) | **Sentinel date on a classified record**: `germline_classification.description` = `Pathogenic` but `last_evaluated` = `1/01/01 00:00`. Also nonsense variant with stop as `*` in `protein_change` (`"Q11*, Q9*"`) and a gnomAD `allele_freq_set` entry with value `0.00000`. |
| `59158` | `esummary_stat1_p1.json` (same page as 30083) | Large CNV `GRCh38/hg38 2q32.2-37.3(chr2:188818195-242065208)x3`, `obj_type` `copy number gain`. **`genes` truncated: the original record lists 1705 genes (the maximum among all 863 STAT1 records); only the first 5 are kept.** Nothing else in the record was changed. Shows CNV-specific shapes: 3 `variation_loc` entries (GRCh38/GRCh37/NCBI36) with `inner_start`/`inner_stop` set, empty `canonical_spdi`, empty `molecular_consequence_list`, `cdna_change` holding the cytogenetic string, trait `See cases` with no xrefs, dbVar `variation_xrefs`, `gene_sort` = first gene alphabetically (`AAMP`). |
| `4530519` | JAK2 history page `esummary.fcgi?db=clinvar&query_key=1&WebEnv=MCID_6ac158f651f71c3fb00bbbb3&retstart=0&retmax=500&retmode=json` (`esummary_jak2_p0.json`) | **Somatic clinical-impact block populated** (`clinical_impact_classification.description` `Tier I - Strong`, `review_status` `criteria provided, single submitter`, trait with MONDO/Orphanet/MedGen/HPO xrefs) while the `germline_classification` block is entirely empty (`""`, sentinel date, `trait_set: []`). Two-gene record (INSL6 + JAK2 overlap). |
| `2958894` | `esummary_jak2_p0.json` | **Oncogenicity block populated** (`oncogenicity_classification.description` `Uncertain significance`, trait `Neoplasm` with MedGen/MeSH/MONDO/3×HPO) **and** a populated germline block (VUS, `criteria provided, multiple submitters, no conflicts`). `allele_freq_set` with 2 sources (ExAC, gnomAD). 3 SCV / 2 RCV. |
| `14662` | JAK2 history page `…WebEnv=MCID_6ac158f651f71c3fb00bbbb3&retstart=800&retmax=100&retmode=json` (`esummary_jak2_rs800.json`) | Hero variant JAK2 V617F. The canonical somatic MPN driver carries **only a germline block** (`Pathogenic/Likely pathogenic`); both somatic blocks empty — origin is not derivable from which block is populated. 13 traits incl. a MeSH xref and duplicates; 31 SCV / 12 RCV; 11 `variation_xrefs` incl. `Genetic Testing Registry (GTR)`; 4 `allele_freq_set` entries; `accession_version` `.142`. |

Records that were *not* chosen but exist in the raw pages, for reference: uid `3390877` (oncogenicity
only, `no assertion criteria provided`), the 45 STAT1 records with an empty `germline_classification.description`,
uid `4959187` (synonymous, `protein_change` `""`), uid `4684630` (alias `p.Asp575=`).

### `efetch_vcv.xml`
The two-record efetch response trimmed to its **first** `<VariationArchive>` (VariationID 4959183,
`VCV004959183.1`); the second record (4959185) was dropped and the `</ClinVarResult-Set>` closing tag
re-attached as in the single-record response. The kept record is byte-identical to the original slice
(XML declaration and the blank second line are preserved). It is the same variant as esummary record
`4959183`. Contents: 20 `<HGVS>` entries (16 `Type="coding"`, 2 `genomic`, 2 `genomic, top-level`),
MANE transcript `NM_007315.4` flagged `MANESelect="true"` with `ProteinExpression change="p.Pro329Leu"
singleLetterProteinChange="P329L"` and `MolecularConsequence ID="SO:0001583"`, one `ClinicalAssertion`
(`SCV007922732`, `SubmitterName="Blueprint Genetics"`, `OrgID="500188"`), `Citation/ID Source="PubMed"`
`25741868`, `ObservedIn/Sample/Origin` `germline`, only `GermlineClassification` under `Classifications`.
Somatic element names in VCV XML are UNVERIFIED (no somatic record was efetched).

### `einfo.json`
Same top-level shape (`einforesult.dbinfo[0]`); all scalar fields kept. **`fieldlist` truncated from 47
to 22 entries** (ALL, UID, FILT, TITL, WORD, MDAT, GENE, MIM, DIS, ACCN ×2, VRID, TRID, PROP, VRTP, MCNS,
RVST, ORIG, HGNC, VID, VACC, CSPDI — both `ACCN` entries are kept deliberately: NCBI really returns the
tag twice with different `fullname`s) and **`linklist` truncated from 10 to 5** (clinvar_gene,
clinvar_medgen, clinvar_omim, clinvar_pubmed, clinvar_snp). Pretty-printed; NCBI returns compact JSON.

### `variant_summary_head.tsv`
Header line plus the first **5 data rows**, verbatim (the raw capture holds 49 lines; the file itself
is 450,727,547 B gzipped, dated 2026-09-29). 43 tab-separated columns in this order:
`#AlleleID, Type, Name, GeneID, GeneSymbol, HGNC_ID, ClinicalSignificance, ClinSigSimple, LastEvaluated,
RS# (dbSNP), nsv/esv (dbVar), RCVaccession, PhenotypeIDS, PhenotypeList, Origin, OriginSimple, Assembly,
ChromosomeAccession, Chromosome, Start, Stop, ReferenceAllele, AlternateAllele, Cytogenetic, ReviewStatus,
NumberSubmitters, Guidelines, TestedInGTR, OtherIDs, SubmitterCategories, VariationID, PositionVCF,
ReferenceAlleleVCF, AlternateAlleleVCF, SomaticClinicalImpact, SomaticClinicalImpactLastEvaluated,
ReviewStatusClinicalImpact, Oncogenicity, OncogenicityLastEvaluated, ReviewStatusOncogenicity,
SCVsForAggregateGermlineClassification, SCVsForAggregateSomaticClinicalImpact,
SCVsForAggregateOncogenicityClassification` (the last three are absent from NCBI's README).
Rows come in GRCh37/GRCh38 pairs per allele (AlleleID 15041 → VariationID 2; 15042 → 3; 15043 → 4).
`PhenotypeIDS`: RCVs separated by `|`, conditions within an RCV by `;`, ids of one condition by `,`.
No protein-change or molecular-consequence columns exist in this file.

## Truncations (complete list)
1. `esummary_small.json` → record `59158`: `genes` array 1705 → 5 entries.
2. `esummary_small.json`: 7 records selected out of 5 responses holding 5 + 500 + 363 + 500 + 17 records.
3. `efetch_vcv.xml`: 2 `<VariationArchive>` → 1.
4. `einfo.json`: `fieldlist` 47 → 22, `linklist` 10 → 5.
5. `variant_summary_head.tsv`: 49 lines → 6 (header + 5 rows).

No value inside any record was changed. JSON files are re-serialised with indentation only.

## Scrubbing
Policy for every fixture dir: every email address and phone number is replaced with `[scrubbed]`
(ClinicalTrials.gov's terms forbid reusing extracted emails, and no contact data belongs in the repo).
The five files were scanned (regex for the `local-part AT domain DOT tld` email shape and for
`(nnn) nnn-nnnn` / `nnn-nnn-nnnn` / `+cc …` phone shapes): **0 email addresses and 0 phone numbers
found, so nothing needed replacing** —
ClinVar E-utility responses carry submitter *organisation* names (`SubmitterName="Blueprint Genetics"`)
but no personal contact data.

## Field paths a parser should use (from `docs/source-apis.md` §1b)

esummary JSON (`result[uid]`):

| Need | Path | Notes |
|---|---|---|
| Variation ID / accession | `uid`, `accession` (`VCV` + 9-digit zero-pad), `accession_version` | `4959183` → `VCV004959183.1` |
| Preferred HGVS name | `title` (== `variation_set[0].variation_name`) | parse with `^(?P<tx>N[MR]_\d+\.\d+)\((?P<gene>[^)]+)\):(?P<c>c\.[^ ]+)(?: \((?P<p>p\.[^)]+)\))?$`; do **not** rely on `variation_set[0].cdna_change` (mixed formats) |
| Protein change (1-letter) | `protein_change` | comma-separated per isoform, MANE token not first, stop = `*`, never contains `p.`; match the token whose residue number equals the `p.` number from `title` |
| Germline classification | `germline_classification.{description,review_status,last_evaluated}` | dates `YYYY/MM/DD HH:MM`; **sentinel `1/01/01 00:00`** can appear on a classified record (uid 2500092) |
| Conditions | `germline_classification.trait_set[].trait_name`, `.trait_xrefs[].{db_source,db_id}` | `db_source` ∈ MedGen, MONDO (`MONDO:` prefix included), Orphanet, OMIM (`613796` or `PS…`), MeSH, `Human Phenotype Ontology`; traits may repeat; `not provided`/`not specified`/`<GENE>-related disorder`/`See cases` carry only MedGen or nothing |
| Molecular consequence | `molecular_consequence_list[]` | SO term text, no id; may have >1 term; empty for CNVs |
| Variant type | `obj_type` (== `variation_set[0].variant_type`) | CNV filter: `obj_type` in {copy number loss, copy number gain} or `len(genes) > 3` |
| Genes | `genes[].{symbol,geneid,strand,source}` | `source` always `submitted`; 2-gene SNVs (INSL6+JAK2) are normal — never use `single_gene[prop]` |
| Location / alleles | `variation_set[0].variation_loc[]` (`assembly_name`, `status` current/previous, `chr`, `start`, `stop`, `band`), `variation_set[0].canonical_spdi` | `ref`/`alt` sparsely populated; CNVs: empty `canonical_spdi`, `inner_*` set |
| Variant xrefs | `variation_set[0].variation_xrefs[].{db_source,db_id}` | dbSNP (no `rs`), ClinGen, OMIM allelic, UniProtKB, dbVar, GTR |
| Allele frequency | `variation_set[0].allele_freq_set[].{source,value,minor_allele}` | gnomAD / TOPMed / ExAC |
| Provenance | `supporting_submissions.scv[]`, `.rcv[]` | |
| Somatic blocks | `clinical_impact_classification.*`, `oncogenicity_classification.*` | same shape as germline; populated independently of the germline block (uids 4530519, 2958894); V617F (14662) is germline-only |

esearch JSON: `esearchresult.{count,retmax,retstart,idlist[],querykey,webenv,querytranslation}`;
`idlist` entries are Variation IDs (== esummary `uid`), descending.

einfo JSON: `einforesult.dbinfo[0].{dbname,dbbuild,count,lastupdate,fieldlist[].{name,fullname,description,…},linklist[].{name,menu,description,dbto}}`.

VCV XML (`ClinVarResult-Set/VariationArchive`): attributes `VariationID`, `Accession`, `Version`,
`VariationType`, `RecordType`, `DateLastUpdated`; `ClassifiedRecord/SimpleAllele/HGVSlist/HGVS[@Type="coding"]`
→ `NucleotideExpression[@sequenceAccessionVersion,@change,@MANESelect]`,
`ProteinExpression[@sequenceAccessionVersion,@change,@singleLetterProteinChange]`,
`MolecularConsequence[@ID,@Type]`; `SimpleAllele/Location/SequenceLocation[@Assembly,@positionVCF,@referenceAlleleVCF,@alternateAlleleVCF]`;
`ClassifiedRecord/Classifications/GermlineClassification/{ReviewStatus,Description}`;
`ClassifiedRecord/ClinicalAssertionList/ClinicalAssertion/ClinVarAccession[@Accession,@SubmitterName]`,
`…/ObservedInList/ObservedIn/Sample/Origin`, `…/Citation/ID[@Source="PubMed"]`.

`variant_summary.txt`: `GeneSymbol` (col 5, comma-separated list; `GeneID` is `-1` when multiple),
`Assembly` (col 17, filter `GRCh38`), `VariationID` (col 31), `Origin`/`OriginSimple` (cols 15–16),
`SomaticClinicalImpact`/`Oncogenicity` (cols 35, 38); `-` means empty.

## Known limits / gotchas captured here
- esummary JSON: ≤500 UIDs per call and ≤10 MB intermediate XML; CNV-heavy pages fail with
  `{"eutilsresult":{"ERROR":"Input XML size is … max size is 10MB"}}` — page ≤100 and halve on error.
- Rate limit 3 req/s per IP without `api_key` (10 with).
- STAT1 N574I is **not** in ClinVar (0 hits) — coverage gap, not a parser bug.
