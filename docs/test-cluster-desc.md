# JAK-STAT Cluster: Test Cluster for the AI Atlas for the World's Rare Diseases

**Project:** Hack-Nation 7th Global AI Hackathon, Challenge 05 (OpenAI x Buffalo Initiative)
**Purpose of this document:** describe the JAK-STAT disease cluster we will use to build and test the atlas, and say how it maps to the challenge brief.

**How to read the confidence tags.** Every factual claim below is tagged:

- **[Retrieved]**: seen in a source during our literature check (source listed in Section 11).
- **[Background]**: from general domain knowledge, *not yet verified against a source*. Check before it appears in a demo, the README, or the video.

---

## 1. Why this cluster

The brief says the atlas should cluster diseases by **mechanism and phenotype, not by name**, and that every link needs **evidence**. The JAK-STAT pathway is a compact place to test exactly that, for four reasons:

1. **One gene, opposite mechanisms, different diseases.** STAT1 loss-of-function and gain-of-function mutations produce different clinical pictures. A gene-name graph merges them; ours must not.
2. **Different genes, same disease.** JAK3 and IL2RG deficiency produce a similar severe immunodeficiency because they break the same signaling step. A good clustering should put them together despite different gene names.
3. **Germline versus somatic matters.** The best-known JAK2 diseases are somatic cancers-of-the-blood-forming-system, not inherited conditions. The graph must not cluster them with Mendelian immunodeficiencies for the wrong reasons.
4. **Real contradictory evidence exists.** JAK-inhibitor treatment of STAT1 gain-of-function disease has both reported successes and reported failures, which lets us test whether the atlas surfaces disagreement instead of hiding it.

It also has an answer key we can write down in advance (Section 8), which most rare-disease clusters do not.

---

## 2. The biology in brief

The Janus kinases (JAK1, JAK2, JAK3, TYK2) are non-receptor tyrosine kinases that sit under cytokine receptors. When a cytokine binds its receptor, the JAKs phosphorylate STAT transcription factors, which move to the nucleus and switch on target genes. **[Background]**

Disease can arise from:

- **Loss of function (LoF):** the pathway cannot signal (typically immunodeficiency).
- **Gain of function (GoF):** the pathway signals too much (typically immune dysregulation, autoimmunity, or, when somatic, blood cancers).
- **Dominant-negative effects:** a mutant protein interferes with the normal copy. This is a third mechanism class, distinct from simple LoF.

This is why the cluster is a good fit for the brief's "key insight": the same pathway connects many diseases, and the *direction* of the variant effect decides which disease and which treatment strategy applies.

---

## 3. Genes and diseases in the cluster

Gene identifiers are HGNC symbols only. Stable disease identifiers (MONDO, OMIM, Orphanet) should be resolved by the pipeline, not typed in by hand.

| Gene | Direction / mechanism | Disease(s) | Germline or somatic | Confidence |
|---|---|---|---|---|
| **STAT1** | Gain of function | Chronic mucocutaneous candidiasis with infection, autoimmunity and vascular features; broad phenotype | Germline, heterozygous | Retrieved |
| **STAT1** | Loss of function | Susceptibility to mycobacterial disease, osteomyelitis, respiratory and herpesvirus infections | Germline | Retrieved |
| **JAK3** | Loss of function | Autosomal recessive severe combined immunodeficiency (SCID), T-negative B-positive NK-negative | Germline, recessive | Background |
| **IL2RG** | Loss of function | X-linked SCID with a similar immunophenotype to JAK3 deficiency (JAK3 signals through the chain IL2RG encodes) | Germline, X-linked | Background |
| **JAK2** | Gain of function (V617F) | Polycythemia vera, essential thrombocythemia, primary myelofibrosis | **Somatic** | Background |
| **STAT3** | Dominant-negative / loss of function | Autosomal dominant hyper-IgE syndrome | Germline | Background |
| **STAT3** | Gain of function | Early-onset multi-organ autoimmunity and lymphoproliferation | Germline | Background (the Immune Deficiency Foundation has a handbook chapter on STAT1 and STAT3 gain of function, Retrieved) |
| **JAK1** | Loss of function; separately, gain of function | Rare immunodeficiency with mycobacterial susceptibility; rare immune dysregulation with eosinophilia | Germline | Background, **low confidence, very few reported patients** |
| **TYK2** | Loss of function (rare recessive); common variants modulate risk | Recessive infection susceptibility; common variants alter autoimmune and infection risk | Germline | Background, verify direction of common-variant effects |

**Scale reference for STAT1 [Retrieved]:** a systematic review found 108 publications describing 442 unique patients with gain-of-function mutations, versus 21 publications describing 39 patients with loss-of-function mutations. The evidence base is uneven, and the atlas should show that imbalance rather than present both as equally well supported.

**Variant-level heterogeneity inside STAT1 gain of function [Retrieved].** Different gain-of-function variants appear to act through different molecular routes: R274W shows faster nuclear accumulation, while R321, N574I and T419R show reduced nuclear mobility, and transcriptional signatures are partly mutation-specific. This is direct support for having **variant nodes** and **mechanism nodes**, not only gene-to-disease edges.

---

## 4. Treatment landscape and the contradictory evidence

JAK inhibitors are the obvious mechanism-based hypothesis for *gain-of-function* diseases, since they dampen the overactive signal. Approved JAK inhibitors exist for other conditions, for example ruxolitinib for myelofibrosis and polycythemia vera **[Background]**. In STAT1 gain of function they are used off-label, and the evidence is mixed:

| Evidence | Finding | Confidence |
|---|---|---|
| Case report, child with a novel STAT1 linker-domain variant | Ruxolitinib reduced interferon hyperresponsiveness, normalized T-helper responses, cured mucocutaneous candidiasis, and maintained remission of immune cytopenias | Retrieved (PMID 28139313) |
| Case series, 2 STAT1 gain-of-function patients with severe fungal disease | Therapeutic failures despite clear inhibition of STAT1 phosphorylation in patient cells | Retrieved (PMC5714179) |
| NK-cell study, 16 STAT1 gain-of-function patients | Ruxolitinib partially restored perforin expression and NK cytotoxic function | Retrieved (bioRxiv 157271) |
| Single-patient study, 2024 | Autoimmune features improved and overexpressed interferon-stimulated genes fell, but epigenetic abnormality persisted; autoimmune features worsened when treatment was paused | Retrieved (J Clin Immunol 44(4):84) |

**What this means for the atlas.** The evidence is single-patient or small-series, and it conflicts. In the sources we retrieved we found no controlled trial; **check ClinicalTrials.gov directly** before claiming that none exists. The edge *JAK inhibitor, treats, STAT1 GoF* should therefore display as "promising, contested, small-n", with supporting and contradicting citations side by side. Loss-of-function diseases (for example JAK3 SCID) need replacement-type strategies rather than inhibition, so a JAK inhibitor edge to those would be an **unsupported link** that the atlas should flag **[inference from mechanism, Background]**.

---

## 5. Existing assets and communities

| Asset | What it is | Confidence |
|---|---|---|
| **Immune Deficiency Foundation (IDF)** | Patient organization; publishes a patient and family handbook chapter on STAT1 and STAT3 gain of function | Retrieved |
| **USIDNET** | Patient registry of primary immunodeficiencies, an IDF program funded by NIAID and NIH; has been used for retrospective cohort analyses | Retrieved |
| **Jeffrey Modell Foundation** | Supports diagnostic and research centers for primary immunodeficiency | Retrieved (named in sources; role needs checking) |
| **NIAID / NIH investigators** | The case reports above include authors from the NIH Clinical Center and NIAID (for example Holland, Freeman, Zimmerman), plus a Baylor / Texas Children's group (for example Forbes) | Retrieved (author lists; this is not a measure of who leads the field) |

**Gap we found:** we did **not** find a patient group dedicated to STAT1 gain of function alone, only umbrella immunodeficiency organizations. If that holds after checking NORD, Global Genes, Orphanet and EURODIS, it is a valuable output: "no dedicated group exists; the closest are X and Y; here is who could help build one." That is the "honest gap" behavior the brief explicitly asks for (Devon's persona).

Patient groups for JAK2-driven blood cancers and for SCID exist but were **not verified**; resolve them from the directories listed in the brief.

---

## 6. How this cluster maps to the project brief

### 6.1 The three questions that drive the atlas

| Brief question | JAK-STAT answer we want the atlas to produce |
|---|---|
| **Who shares our disease characteristics?** | STAT1 GoF neighbors: STAT3 GoF (shared pathway and immune-dysregulation phenotype), and not STAT1 LoF despite the same gene |
| **What useful work already exists?** | USIDNET registry, IDF patient materials, published case series and the mutation-level functional studies, plus the existing JAK-inhibitor experience |
| **What should we do together next?** | Pool variant-level treatment responses across STAT1 GoF patients and test whether response tracks variant mechanism; reuse registry infrastructure (see Section 9) |

### 6.2 Personas

| Persona (from brief) | Scenario in this cluster |
|---|---|
| **Maria**, patient group leader | Her child has a STAT1 GoF variant. The atlas takes her from the variant, to its mechanism class, to other patients with variants acting similarly, to the mixed ruxolitinib evidence (with citations), to the registry and the investigators already studying it, to a concrete next step. |
| **Devon**, newly diagnosed caregiver | Searches in plain language for recurring candida infections plus autoimmune thyroid disease. A systematic review flags that combination as a prompt to consider STAT1 GoF **[Retrieved]**. The atlas resolves the symptoms to the disease, explains it without jargon, and, if no dedicated group exists, says so and points to the closest. |
| **Priya**, biotech scout | Holds a pathway-dampening mechanism. Gets a ranked list: STAT1 GoF, STAT3 GoF and others, each with evidence level, patient communities, and the **explicit exclusion** of loss-of-function diseases where inhibition would be the wrong direction. |
| **Dr. Osei**, academic researcher | Works on one gene. The atlas shows that colleagues working on *other* genes in the same pathway are chasing a mechanistically similar problem, and who they are. |

### 6.3 Modules

| Module | What JAK-STAT exercises |
|---|---|
| **1. Connect the evidence** | Gene-variant-mechanism (including direction and germline vs somatic); disease-phenotype (HPO) to separate broad from distinguishing symptoms; publication-claim-investigator for the ruxolitinib papers; study-intervention from ClinicalTrials.gov; patient organization-disease-registry for IDF and USIDNET |
| **2. Make each connection trustworthy** | The ruxolitinib contradiction; the 442 vs 39 patient imbalance; observation vs inference (for example JAK inhibitor to LoF disease is inferred-unsupported) |
| **3. Turn the network into action** | *Mechanistic overlap*: JAK3 with IL2RG; STAT1 GoF with STAT3 GoF. *Find what can be shared*: registry and case-series designs. *Network overlap*: whether the same investigators appear across JAK-STAT subclusters and other disease communities (to be tested; we have not yet shown this) |

### 6.4 Judging criteria

| Criterion | Where this cluster helps |
|---|---|
| **Graph quality** | Built-in counterexamples (STAT1 LoF vs GoF; somatic vs germline); defensible clustering with checkable ground truth |
| **Evidence integrity** | Real conflicting papers; clear split between data, hypothesis, and clinical proof |
| **Patient progress** | A concrete path: variant, then mechanism, then evidence, then registry, then investigators, then next step; or an honest gap |
| **10x impact** | See Section 9 |
| **Product craft** | A family-readable explanation of why STAT1 GoF is different from STAT1 LoF despite the same gene name |

---

## 7. Proposed graph design for this cluster

### 7.1 Node types

| Node | Examples | Source layer |
|---|---|---|
| Gene | STAT1, JAK3, IL2RG | Existing KGs (HGNC, Ensembl IDs) |
| **Variant** | STAT1 R274W, T419R, N574I | **New:** ClinVar, OMIM, literature |
| **Mechanism** | GoF, LoF, dominant-negative, somatic-driver | **New:** curated and LLM-extracted, always marked as inferred or observed |
| Disease | STAT1 GoF syndrome, SCID, myeloproliferative neoplasms | MONDO / Orphanet via existing KGs |
| Phenotype | Candidiasis, autoimmune thyroid disease | HPO |
| Pathway | JAK-STAT signaling | Reactome |
| Drug | Ruxolitinib | Existing KGs |
| **Paper / claim** | Case reports in Section 4 | **New:** LLM extraction tied to PMIDs |
| **Investigator** | Authors of the key papers | **New:** PubMed / PMC |
| **Patient group / registry** | IDF, USIDNET | **New:** NORD, Global Genes, Orphanet, group sites |
| **Study** | Any controlled trials found | **New:** ClinicalTrials.gov |

### 7.2 Evidence fields on every edge

`source` (database or PMID) · `date` · `relationship type` · `evidence class` (observed / extracted / inferred) · `confidence` · `supporting citations` · `contradicting citations`

### 7.3 Starting resources

- **OptimusKG** (arXiv 2604.27269): preferred backbone because every node and edge carries provenance fields and the pipeline is reproducible. Disease-gene edges carry evidence scores, PMID counts and first/last year **[Retrieved]**. It has **no** variant, mechanism, patient-group, study or investigator nodes, so these are ours to add.
- **PrimeKG-Plus** (bioRxiv 2026.07.14.738415): useful as a second reference and for its literature-curation workflow (entity normalization to UMLS, expert review). Its literature layer covers four other rare diseases, not JAK-STAT.
- Open question: confirm that the STAT, JAK and relevant disease nodes exist as single, well-connected nodes in the OptimusKG release before committing.

---

## 8. Ground-truth tests (write these before building)

These are the pass/fail checks for the clustering and evidence layers.

| # | Test | Pass condition |
|---|---|---|
| 1 | STAT1 gain of function vs loss of function | Land in **different** clusters with different phenotype profiles |
| 2 | JAK3 and IL2RG SCID | Cluster **together** despite different gene names |
| 3 | JAK2 somatic myeloproliferative disease | **Not** clustered with germline immunodeficiencies |
| 4 | JAK inhibitor edge to STAT1 GoF | Shows **mixed** evidence with both supporting and contradicting citations |
| 5 | JAK inhibitor edge to a LoF disease (for example JAK3 SCID) | Flagged as **unsupported or inferred**, not presented as a lead |
| 6 | Obscure gene with no connections | Returns **"no supported route"** with stated search coverage and a next question to test |
| 7 | Within-STAT1-GoF variants | Mutation-specific mechanism differences (Section 3) are representable, not collapsed into one node |

---

## 9. 10x moonshot hypothesis (to be validated, not yet established)

The brief asks for a meaningful milestone, the existing timeline versus our route, and the assumptions behind the 10x claim. **We have not established a 10x figure and should not state one until we have real timelines.** Here is a framing to test.

**Candidate milestone:** decide whether a JAK inhibitor is a credible candidate for a specific STAT1 gain-of-function variant, or design a shared natural history and treatment-response study across variants.

**Existing route (what the retrieved sources show):** conflicting single-patient and small-series reports published separately, with the successes and failures from 2017 and a further case study in 2024 **[Retrieved]**. We did not find a pooled analysis or controlled trial in our check (verify).

**Proposed route:** one atlas view that aligns each reported treatment response to the patient's variant and its mechanism class, grades the evidence, flags contradictions, and links to the registry and the investigators who could pool data.

**Assumptions that must be validated:**

1. Treatment response actually depends on variant mechanism (nothing retrieved shows this; it is the hypothesis).
2. Enough treated patients are reported with enough variant detail to compare. Single-patient reports may not be sufficient.
3. Registry owners and investigators would share data, which needs outreach, not just a graph.
4. Time saved comes from evidence-gathering and partner-finding, not from regulatory or trial steps that the atlas cannot shorten.

The honest claim is that the atlas could shorten the *evidence-assembly and collaborator-finding* step. Whether that is 10x depends on measuring how long that step takes today.

---

## 10. Open items to verify before building

- [ ] Resolve MONDO / OMIM / Orphanet IDs for every disease in Section 3 (let the pipeline do it, then review).
- [ ] Verify all **[Background]** rows, especially JAK1 and TYK2, which have the thinnest evidence.
- [ ] Search ClinicalTrials.gov for JAK inhibitor studies in STAT1 GoF and related conditions.
- [ ] Check NORD, Global Genes, Orphanet and EURORDIS for a dedicated STAT1 GoF patient group.
- [ ] Check RePORTER for current funded programs on STAT1 GoF and inborn errors of immunity.
- [ ] Confirm the cluster's genes and diseases exist as single nodes in the chosen KG release.
- [ ] Decide how to represent germline vs somatic (an attribute on the disease-gene edge is the simplest option).
- [ ] Check licensing of the source databases (some, for example DisGeNET and DrugBank, restrict commercial use).

---

## 11. Sources

**Retrieved during the literature check**

- Zimmerman O et al. *Risks of Ruxolitinib in STAT1 Gain-of-Function-Associated Severe Fungal Disease.* Open Forum Infect Dis 2017. PMC5714179.
- *Ruxolitinib reverses dysregulated T helper cell responses and controls autoimmunity caused by a novel STAT1 gain-of-function mutation.* PMID 28139313.
- Vargas-Hernandez A et al. *Ruxolitinib partially reverses functional NK cell deficiency in patients with STAT1 gain-of-function mutations.* bioRxiv, doi 10.1101/157271.
- *Ruxolitinib Improves Immune-Dysregulation Features but not Epigenetic Abnormality in a Patient with STAT1 GOF.* J Clin Immunol 44(4):84, 2024.
- *Clinical Relevance of Gain- and Loss-of-Function Germline Mutations in STAT1: A Systematic Review.* (abstract seen via the Immune Deficiency Foundation research digest.)
- *Transcriptional Profiling of STAT1 Gain-of-Function Reveals Common and Mutation-Specific Fingerprints.* (abstract seen via the same digest.)
- Immune Deficiency Foundation. *Patient & Family Handbook, Chapter 15: Diseases of Immune Dysregulation: STAT1 and STAT3 Gain of Function.* https://primaryimmune.org/resources/print-material/chapter-15-diseases-of-immune-dysregulation
- USIDNET registry: http://www.usidnet.org/ ; *Inborn Errors of Immunity Associated With Type 2 Inflammation in the USIDNET Registry.* Front Immunol 2022, PMC8902297.

**Methods and knowledge-graph background**

- Goh K-I et al. *The human disease network.* PNAS 2007.
- Menche J et al. *Uncovering disease-disease relationships through the incomplete interactome.* Science 347, 2015.
- Nguyen TTD et al. *PrimeKG-Plus.* bioRxiv, doi 10.64898/2026.07.14.738415.
- Vittor L, Noori A, Arango I et al. *OptimusKG: Unifying biomedical knowledge in a modern multimodal graph.* arXiv 2604.27269.
- Hack-Nation x OpenAI x Buffalo Initiative. *Challenge 05 brief: AI Atlas for the World's Rare Diseases.*
