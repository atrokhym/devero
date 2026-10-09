# DeVero Universal Clinical Document Verifier

The **Universal Clinical Document Verifier** is an automated, read-only quality and compliance inspection tool for clinical documents in DeVero EHR. It automatically identifies the document category, assessment timepoint (RFA), and pagination, extracts live multi-page data from target pending documents and source baseline notes, and audits them against **CMS OASIS-E2 Guidance Manual rules**, **Medicare Conditions of Participation (§484.55 & §484.60)**, and **DeVero documentation standards**.

It generates structured, actionable punch lists categorized into **DO NOW** (critical errors), **CLINICAL JUDGMENT** (clinician review items), and **LEAVE** (verified items).

---

## 1. Supported Documents & Timepoints

1. **OASIS-E2 Start of Care (SOC - RFA 01)** (`OASIS-E2 Nurse Start of Care`)
   - Comprehensive admission baseline, 5-day referral window, M0030 Start Care Date, BIMS (C0100-C0500), PHQ-2/9, MAHC-10 Fall Risk, Admission GG0130 & GG0170 Prior & Admission performance, High-Risk Medications (N0415), and initial 485 orders.
2. **OASIS-E2 Resumption of Care (ROC - RFA 03)** (`OASIS-E2 Nurse Resumption of Care`)
   - 48-hour resumption window, M0032 Resumption Date, facility discharge date (M2410/M2420), post-hospitalization medication reconciliation (M2001, M2003, M2005), and functional reassessment.
3. **OASIS-E2 Recertification (Follow-Up - RFA 04 / 05)** (`OASIS-E2 Nurse Recert`)
   - 5-day assessment window (Days 56–60 of 60-day period), upcoming certification period date alignment, Plan of Care SN frequency and duration, 60-day progress toward goals % consistency, GG functional follow-up, and clone detection across all narrative sections.
4. **OASIS-E2 Discharge from Agency (DC - RFA 09)** (`OASIS-E2 Discharge`)
   - M0906 Discharge Date, M2420 Discharge Disposition, GG0130 & GG0170 Discharge Performance, discharge medication reconciliation (M2005), and comprehensive discharge summary narrative.
5. **OASIS-E2 Transfer to Inpatient Facility (RFA 06 / 07)** (`OASIS-E2 Transfer`)
   - M0906 Transfer Date, inpatient facility type (M2410), and transfer rationale.
6. **Routine Skilled Nurse Visit Notes** (`Skilled Nurse Visit Note`)
   - 1–2 page routine visit note audit: Time In/Time Out, vital signs against POC notification thresholds, systems assessment (Resp, Cardio, Neuro, GI Last BM, GU, Integumentary/Wounds), skilled interventions documented vs patient response & tolerance (preventing blank, swapped, or cloned notes), and plan for next visit.

---

## 2. Directory Structure

```
verifier/
├── README.md             # Complete user guide and CLI documentation (this file)
├── doc_classifier.py     # Document type, RFA timepoint, and pagination classifier
├── verify_oasis.py       # Main universal verifier CLI and multi-page extractor
├── audit_logging.py      # Atomic per-run and latest-snapshot persistence
└── oasis_rules.py        # Modular clinical rules engine (Universal, OASIS, Visit Notes)
```

Audit results and raw page dumps are saved to:
```
logs/
├── audit_<patient_name>_<date>_<UTC-run-id>.json  # Immutable history
└── audit_<patient_name>_<date>.json               # Latest snapshot
```

---

## 3. How to Run the Verifier

### Completed documents: insurance-audit readiness mode

Use the separate completed-document entry point when the document is already
Completed or Sent to Office. It strictly excludes Pending rows, remains
read-only, saves reports under `logs/insurance_audit_*.json`, and reports
retrospective audit risk instead of producing a Writer handoff:

```bash
uv run python verifier/verify_completed.py \
  --patient "FixtureOne, Patient" \
  --caregiver "Example, Clinician (RN)" \
  --doc-type "OASIS-E2 Nurse Recert" \
  --target-date "08/27/2026"
```

An optional `--source-date` selects an earlier completed document for
longitudinal comparison. Without it, the verifier attempts to discover the
newest earlier completed baseline. A clean automated screen is not a guarantee
of payer approval. Findings on authenticated records must use the agency's
authorized correction, addendum, late-entry, or amendment process; the verifier
never modifies the chart.

### Option A: Run for Active Task in `tasks.jsonl` (Default)
When you have a task enabled in `tasks.jsonl`:
```bash
uv run python verifier/verify_oasis.py
```

### Option B: Run for Any Document via CLI
Pass patient and document parameters directly. If `--source-date` is omitted, the verifier automatically discovers the appropriate baseline note from the patient's chart:

```bash
# 1. OASIS-E2 Recertification (Auto-discovers prior completed Recert or SOC):
uv run python verifier/verify_oasis.py \
  --patient "FixtureOne, Patient" \
  --caregiver "Example, Clinician (RN)" \
  --doc-type "OASIS-E2 Nurse Recert" \
  --target-date "08/27/2026"

# 2. Skilled Nurse Visit Note (Auto-discovers previous completed visit note):
uv run python verifier/verify_oasis.py \
  --patient "FixtureSix, Patient" \
  --caregiver "Example, Clinician (RN)" \
  --doc-type "Skilled Nurse Visit Note" \
  --target-date "08/03/2026"

# 3. OASIS-E2 Start of Care (Single-document standalone audit if new patient):
uv run python verifier/verify_oasis.py \
  --patient "FixtureFive, Patient" \
  --caregiver "Example, Clinician (RN)" \
  --doc-type "OASIS-E2 Nurse Start of Care" \
  --target-date "08/18/2026"

# 4. OASIS-E2 Discharge:
uv run python verifier/verify_oasis.py \
  --patient "FixtureFour, Patient" \
  --caregiver "Example, Clinician (RN)" \
  --doc-type "OASIS-E2 Discharge" \
  --target-date "08/05/2026"
```

---

## 4. Verification Report Structure

The generated report uses a standardized 4-part punch list:

- **`### 0. VERIFICATION COMPLETENESS`**: Verifies that all expected pages reached DOM stability and were completely captured without timeouts.
- **`### 1. DO NOW`**: High-priority regulatory and structural failures that **must** be resolved before signing. Tagged with action types:
  - `[UPDATE FOR THIS TIMEPOINT]`: Dates, swapped fields, prompt placeholders, contradictory goal percentages.
  - `[COPY FROM SOURCE]`: Missing diagnoses or active Plan of Care orders.
- **`### 2. CLINICAL JUDGMENT / USER REVIEW`**: Clinical findings requiring human review rather than blind automated fixes:
  - `[LEAVE BECAUSE IT MUST DIFFER]`: Library intervention start effective dates from prior episodes, cloned narrative sections, or functional code cross-checks.
- **`### 3. LEAVE`**: Fully verified sections that meet all CMS and EHR standards.

---

## 5. Invariants & Safety Rules

- **Strictly Read-Only**: The verifier **never** writes, modifies, saves, or signs documents in DeVero EHR.
- **Target Unsigned Status**: The target document remains in its `Pending` / unsigned state.
- **Completed Baseline Safety**: Source baseline notes are never touched.


## 6. Print punch-list text from an audit JSON log

Verifier runs save a large JSON dump under `logs/`. To reprint the normal text report from that file:

```bash
# Newest audit_*.json in logs/
uv run python verifier/print_audit_log.py

# A specific run (timestamped file)
uv run python verifier/print_audit_log.py logs/audit_FixtureOne__Patient_08272026_20260902T221224155268Z.json

# Also write a .txt
uv run python verifier/print_audit_log.py logs/audit_FixtureOne__Patient_08272026.json -o logs/last_report.txt
```

The same command recognizes `insurance_audit_*.json` and automatically prints
the completed-document readiness format.
