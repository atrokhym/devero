---
name: devero-verifier
description: Perform read-only E2 OASIS and Skilled Nurse Visit Note verification from an opaque Devero case using deterministic Python evidence plus bounded CMS-grounded review.
---

# Devero Verifier

Load `devero-fleet`, then read `references/cms-compliance-checklist.md`. Verification is read-only. Python is authoritative for identity, extracted values, timing, mappings, and persistence; model review is limited to semantic consistency in the secure evidence.

## Supported targets

- `OASIS-E2 Nurse Start of Care` (RFA 01)
- OASIS-E2 Resumption of Care (RFA 03)
- `OASIS-E2 Nurse Recert.` (RFA 04)
- OASIS-E2 Other Follow-Up (RFA 05)
- OASIS-E2 Transfer, patient retained or discharged (RFA 06/07)
- OASIS-E2 Death at Home (RFA 08)
- `OASIS-E2 Discharge` (RFA 09)
- Devero nursing/PT and visit/non-visit labels for those E2 time points
- `Skilled Nurse Visit Note`

Completed E1 records are historical source evidence only. Never issue an E1 compliance verdict and never treat an E1 value as proof of current E2 status.

## Procedure

1. Load only this assignment's local context with `.venv/bin/python -m devero_fleet work-context CASE --role verifier`. Never repeat its PHI in a response, memory, Bot message, Kanban record, command argument, or durable report.
2. Run `.venv/bin/python -m devero_fleet verify-scan CASE --phase initial` for a new review or `--phase persistence` after Writer Save. The command owns and releases the browser claim, performs exact live identity/status matching, and returns only a PHI-free scan summary.
3. Stop on `BLOCKED`. Do not reinterpret authentication, ambiguous identity, unsupported type, missing pages, or incomplete extraction as a clinical failure.
4. Read the local evidence with `.venv/bin/python -m devero_fleet verification-context CASE --role verifier --phase PHASE`. This output is PHI-bearing local tool context. Never quote or forward it.
5. Preserve every deterministic correction from `audit.corrections`. Add semantic findings only when directly supported by the extracted target and related evidence:
   - current E2 status versus stale E1 copy-forward;
   - structured responses versus narratives;
   - assessment versus plan of care, orders, disciplines, interventions, goals, frequencies, and durations;
   - skilled service, individualized skilled need, complexity, response/progress, objective outcomes, and next-visit planning.
6. Never invent or infer an assessment finding, diagnosis, date, measurement, intervention, response, or outcome. A missing or conflicting fact is routed for clinician/user input.
7. Write the combined PHI-local result to an owner-private temporary JSON file with `review_stage: FINAL` and `semantic_review_required: false`, then run `.venv/bin/python -m devero_fleet record-verification CASE --phase PHASE --from-file FILE`. The case store rejects any result that drops or changes a deterministic finding. Remove the temporary input after recording it.
8. Return only the case id, run status, verdict, correction ids with issue/evidence/source/fact-status/route, and whether Save/sign remained untouched.

## Owner documentation standard: interventions and education

When the target contains `Interventions/Education Provided` or a clear equivalent in a Skilled Nurse Visit Note or OASIS-related nursing document, apply this owner-supplied quality standard during initial and post-Save persistence review:

- Treat this as `USER_SUPPLIED`, not as an independently sourced CMS mandate. Do not describe a style-only finding as CMS noncompliance.
- Confirm the entry uses concise, objective, past-tense home-health skilled-nursing charting and does not add SOAP headings unless the whole form is explicitly SOAP-structured.
- Confirm supported content is organized as applicable: intervention or assessment actually performed; education or reinforcement actually provided; documented relevance to the condition or plan of care; then the observed patient/caregiver response.
- Flag vague filler, conversational wording, or a generic paragraph reused without visit-specific support. Do not require a clause for which the secure evidence contains no fact.
- Treat unsupported understanding, teach-back, return demonstration, cueing, barriers, provider notification, or reinforcement needs as an evidence problem, not merely a style problem.
- Use `DVV-NOTE-INTERVENTION-EDUCATION-STYLE` for a bounded style/structure correction and `DVV-NOTE-INTERVENTION-EDUCATION-SUPPORT` for an unsupported clinical statement. Use `evidence_class: DIRECT_RECORD` and include `source_class: USER_SUPPLIED`; add another source class only when that source actually supports the finding.
- Route to `WRITER` only when every replacement fact is already fully supported in the secure case evidence. Otherwise use `CHIEF_INPUT` or `CLINICAL_POLICY` as applicable.

## Correction records

Use stable ids and reuse them until the same issue passes:

- `DVV-DATE-VERSION`
- `DVV-RFA04-TIMING`
- `DVV-SOC-TIMING`
- `DVV-ROC-TIMING`
- `DVV-TRANSFER-TIMING`
- `DVV-DEATH-TIMING`
- `DVV-DISCHARGE-TIMING`
- `DVV-E2-RETIRED-<ITEM>`
- `DVV-RESPONSE-<ITEM>`
- `DVV-CURRENT-STATUS-<ITEM>`
- `DVV-TIMEPOINT-<ITEM>` or `DVV-SKIP-<ITEM>`
- `DVV-CONSISTENCY-<DOMAIN>`
- `DVV-NOTE-<REQUIREMENT>`
- `DVV-IDENTITY-<DIMENSION>`
- `DVV-PERSISTENCE-<LOCATION>`

Each record contains `id`, `issue_class`, PHI-free `location`, `observed_issue`, bounded `expected_correction`, `evidence_class`, `source_class`, `fact_status`, `route`, and `approved: false`.

- `evidence_class`: `DIRECT_RECORD`, `CROSS_RECORD`, `CALCULATED_TIMING`, `MISSING_REQUIRED`, or `POLICY_CONFLICT`
- `source_class`: `CMS_OASIS`, `CMS_MBPM_CH7`, `AGENCY_RECORD`, `USER_SUPPLIED`, or a comma-separated combination
- `fact_status`: `SUPPORTED`, `USER_OR_CLINICIAN_REQUIRED`, or `NOT_EDITABLE`
- `route`: `WRITER` only for a fully supported bounded edit; otherwise `CHIEF_INPUT` or `CLINICAL_POLICY`

Never place a patient name, caregiver, date, value, diagnosis, narrative excerpt, or activity id in a correction id or location.

## Verdict

- `PASS`: deterministic scan is complete, every in-scope semantic check passes, and no correction remains.
- `MIXED`: verified content and one or more bounded correctable issues or evidence gaps coexist.
- `FAIL`: a material unsupported clinical statement, non-editable timing/version conflict, or material cross-record conflict makes the target unreliable.
- `BLOCKED` is a run status, not a verdict.

Verifier never edits, types into, selects, toggles, Saves, signs, submits, approves, or finalizes a clinical record. Navigation and page controls are the only permitted Devero interactions.
