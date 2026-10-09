# CMS compliance quick reference

Use this checklist for read-only E2 target verification, not as permission to diagnose, change a record, determine coverage, or Save. Completed E1 documents are historical source evidence only. Apply item-specific CMS guidance when it is more specific than this summary.

## OASIS date, version, and time point

1. **Confirm the E2 target from the real completion date.** OASIS-E2 is effective April 1, 2026. An E2 target with M0090 before that date is a version/date conflict; never alter or backdate M0090 to force a version. E1 sources remain evidence and are mapped into the E2 schema without receiving a separate compliance verdict. [CMS-E2, title/effective date, Ch. 3 M0090]
2. **Use every E2 time point.** The verifier covers Start of Care (RFA 01), Resumption of Care (03), Recertification (04), Other Follow-Up (05), Transfer without and with agency discharge (06/07), Death at Home (08), and Discharge (09), including Devero's combined and discipline-specific labels. [CMS-E2, Ch. 1 Table 1.2]
3. **Apply official timing.** SOC is due within five calendar days after SOC (Day 0); ROC, significant-change Other Follow-Up, Transfer, Death at Home, and Discharge are due within two calendar days of the applicable event or knowledge date; Recertification is due in days 56-60 of the current 60-day period. Preserve actual dates and route lateness instead of backdating. Deterministic calculation covers event dates represented by M0030, M0032, or M0906. Other Follow-Up timing remains semantic review because Devero does not expose its significant-change identification date as a standardized OASIS event-date item. [CMS-E2, Ch. 1 Table 1.2; Ch. 3 M0032 and M0100] [CMS-BP7, §10.3]
4. **M0090 means actual completion.** M0090 is the last date information used to complete the comprehensive assessment and determine OASIS coding was gathered by the assessing clinician and the specific responses were documented. It may differ from the visit date. [CMS-E2, Ch. 3 M0090]
5. **Current status is newly assessed.** Current-status responses must reflect observation and permitted reports about the patient's condition and ability at the E2 assessment time. A completed E1 value may support review but is not a substitute for the current assessment. Each time point requires a unique patient assessment, not simple carry-forward. [CMS-E2, Ch. 1 §§1.5.5 and 1.5.8.1 conventions 7-9]
6. **Use the correct time-point item set and skip logic.** Confirm M0100/RFA, then use the final instrument or Appendix B matrix for that time point. The deterministic scanner uses the official Data Specifications V3.02.0 for item applicability and coded response domains, and Devero's rendered visibility, requiredness, and validity state for UI-implemented skip behavior. Item-specific clinical coding and look-back judgments remain semantic review. [CMS-E2, Ch. 1 §§1.5.4-1.5.5; Ch. 3 M0100; App. B] [CMS-SPEC]

## Assessment, plan of care, and orders

7. **Require congruence, not copied wording.** OASIS is part of the comprehensive assessment, not the whole assessment. The comprehensive assessment is the basis of the physician/allowed-practitioner ordered plan of care, so current findings, problems, goals, interventions, disciplines, frequencies, and durations must not materially conflict across those records. [CMS-E2, Ch. 1 §1.5.3] [CMS-BP7, §§20.1.2 and 30.2.2]
8. **Check order specificity and service-date fit.** The plan of care must address patient-specific needs identified by the comprehensive assessment. Orders must identify the professional, nature of service, and frequency; PRN orders require triggering signs/symptoms and a visit limit. Care reviewed for a service date must be within the applicable plan/orders. [CMS-BP7, §§30.2.2-30.2.3]

## Skilled home-health note requirements

9. **The record must show why the visit was skilled.** Coverage rests on the individual patient's objective clinical need for specialized nursing judgment/skills, not diagnosis alone, boilerplate, or whether improvement is expected. [CMS-BP7, §§20.1.2 and 40.1.1]
10. **For each visit, look for:** pertinent current history/exam; skilled services performed; patient/caregiver response and progress; next-visit plan based on prior results; individualized rationale for skilled need; service complexity; pertinent home/beneficiary characteristics; and objective outcomes or clearly described behavior change when applicable. The notes as a whole should show progress toward plan-of-care goals and the continued need for skilled care. [CMS-BP7, §40.1.1]
11. **Flag vague assertions.** Phrases such as “tolerated treatment well,” “caregiver instructed,” or “continue POC” do not by themselves demonstrate skilled need, response, progress, or next steps. Do not invent the missing detail; classify it as missing required support. [CMS-BP7, §40.1.1]

## Read-only decision rules

- Treat the completed source, pending target, linked forms, plan of care, orders, and current notes as evidence classes. No single record is automatically authoritative when records conflict.
- A calculated timing defect, unsupported clinical statement, or conflict is a finding. It is never permission to change dates, select a clinically plausible answer, or compose facts.
- Route a correction to Writer only when the replacement is fully supported in the secure case evidence and the user later approves that stable correction id. Otherwise route it to Chief for user/clinician input or clinical-policy review.
- Verifier never edits, Saves, signs, submits, approves, or finalizes.
- This read-only record review does not replace iQIES submission validation, the CMS Validation Utility Tool, or clinician application of item-specific guidance.

## Official CMS sources

- **CMS-E2:** Centers for Medicare & Medicaid Services, *Final OASIS-E2 Guidance Manual*, effective April 1, 2026: https://www.cms.gov/files/document/final-oasis-e2-manual-04-01-2026.pdf
- **CMS-SPEC:** Centers for Medicare & Medicaid Services, *OASIS-E2 Data Specifications V3.02.0 Final*: https://www.cms.gov/medicare/quality/home-health/data-specifications
- **CMS-E1 source reference:** Centers for Medicare & Medicaid Services, *Final OASIS-E1 Guidance Manual*, effective January 1, 2025: https://www.cms.gov/files/document/oasis-e1-manualfinal12-9-2024.pdf
- **CMS-BP7:** Centers for Medicare & Medicaid Services, *Medicare Benefit Policy Manual, Pub. 100-02, Chapter 7 — Home Health Services*: https://www.cms.gov/regulations-and-guidance/guidance/manuals/downloads/bp102c07.pdf
- Confirm the current final instrument and guidance at the official CMS indexes before each review: https://www.cms.gov/medicare/quality/home-health/oasis-data-sets and https://www.cms.gov/medicare/quality/home-health/oasis-user-manuals
