# DeVero Universal OASIS Document Writer (`writer/fix_oasis.py`)

## Which OASIS writer handles which section?

| Script | Purpose | Field scope |
| :--- | :--- | :--- |
| `fix_oasis.py` | Automatically correct audit errors such as dates, RFA, diagnoses, and placeholders. | Fields selected by the deterministic fix plan. |
| `fix_oasis_summary.py` | Draft, approve, and apply the OASIS narrative headed **Interventions/Education Provided:**; fill blank Last BM on apply. | `sum_AssessmentSum_Txt` (Clinical Assessment Summary), plus blank `lbd_LastBm_Chk`. |

The summary narrative's **Interventions/Education Provided:** heading is distinct from the separate `int_IntervEducation_Txt` field. Use `fix_oasis_summary.py draft --style-examples FILE` for RN-example style in this narrative. The summary is number-free even without examples. `fix_oasis.py` performs deterministic error cleanup and does not call a model or block unrelated fixes because the separate intervention field contains numbers.

## Skilled Nurse Visit Note discrepancy fixer (version 2)

`fix_visit_note2.py` checks the three Skilled Nurse Visit Note narratives against the target visit, the source note, and each other. It rewrites copied fields and corrects discrepancies; sound RN text stays unchanged. Each run creates a new JSON draft in `drafts/`. `-t` selects exact visits from a task file, while the matching verifier audit supplies the evidence. The writer does not use the v1 clinical synthesizer.

Interventions/Education Provided is the exception to verbatim preservation: specific measurements, doses, counts, dates, frequencies, and written-out numbers are removed even from noncopied RN text. Drafting and review share this rule. Supported qualitative findings, their locations, documented assessments, and teaching remain; numeric omission alone is not an unresolved question. Exact values are not moved to another narrative just to retain them, and removing a number must not invent a severity or normal finding.

Use `--style-examples drafts/rn_style_examples.json` to guide Interventions/Education Provided with the RN's approved writing. The private file is a JSON array of 1–8 nonempty, deidentified intervention strings, at most 10,000 characters each and 40,000 characters total. It is excluded from Git under `drafts/`. The drafting and review calls receive the examples as style references, never as patient evidence. They guide paragraph structure, assessment-to-teaching order, and direct verbs; they do not impose a word count or change sound existing RN text. The package stores a fingerprint of the examples rather than their text. This option uses the existing model and does not train it.

CMS expects each home-health visit note to describe the services provided, the patient/caregiver response, and why skilled care remains needed; it does not prescribe different wording in every field. See the [Medicare Benefit Policy Manual, Chapter 7](https://www.cms.gov/manuals/Downloads/bp102c07.pdf). Copied text is rewritten because it needs current-visit confirmation, not merely to vary wording.

The Responses API with a strict JSON schema is used for `https://api.openai.com/v1`. Other OpenAI-compatible base URLs use Chat Completions JSON mode. Before drafting, configure the API in `.env`: `OPENAI_API_KEY` for OpenAI or the existing loopback API, and `OPENAI_BASE_URL` if using a different endpoint. `OPENAI_MODEL` is optional. Set `OPENAI_REASONING_EFFORT=high` to request high effort for both drafting and review; leave it unset to use the API's default. The local gateway must support the requested value. For a different remote provider, set `AI_API_KEY`; the OpenAI key is not sent to that provider.

```powershell
# First produce the verifier audit JSON for each active task.
uv run python verifier/verify_oasis.py -t tasks_svn.jsonl

# Each active task gets an immutable JSON draft and an independent model review.
uv run python writer/fix_visit_note2.py draft -t tasks_svn.jsonl

# Optional RN style references (private; included in the configured model requests).
uv run python writer/fix_visit_note2.py draft -t tasks_svn.jsonl --style-examples drafts/rn_style_examples.json

# Save changed narratives and fill a blank Last BM with the visit date.
uv run python writer/fix_visit_note2.py apply --yes -t tasks_svn.jsonl
```

`-t` selects patients and dates for both commands. `draft` requires a complete matching verifier audit in `logs/`; `apply` selects the newest v2 draft for that visit. `--yes` skips the Save confirmation. RN approval and signing happen only in DeVero.

Copied fields are rewritten without a blanket `RN REVIEW` marker. The RN reviews the updated Pending note after Save. Existing target narratives may be rewritten provisionally, including copied teaching and recorded responses, without strengthening their meaning or adding actions from baseline notes or style examples. Contradictions with current findings still require correction; genuinely absent facts are not invented.

Confirmation questions stay in `review_flags` and `resolutions`, outside clinical prose. Packages retain the internal status `needs_rn_input` when such questions exist, but that status no longer blocks Save. `draft` prints these as DRAFT FOR RN REVIEW and exits successfully when only RN questions remain. `apply --yes` saves the proposed clinical text as Pending, retains all review questions, and prints a consolidated report at the end of the job. The report is saved privately as `drafts/rn_review_visit_note2_<timestamp>.md` with owner-only permissions; pass this report to the RN to review alongside the updated notes. Failed/unconfirmed updates are listed separately. A field with no recorded or independently supported content may remain blank with a specific RN question; no response is invented to fill it. Model review still checks the proposed narratives and allows up to two revisions.
Copied text in other form fields can be flagged for RN reconciliation when relevant; this writer saves the three main narrative fields and fills a blank Last BM with the visit date.

`apply` checks patient, visit date, structured evidence, and all three original field fingerprints. If DeVero changed since drafting, that visit is skipped for a fresh audit and draft. Otherwise it writes changed narrative fields, fills Last BM only when blank, reopens the note, and verifies Save. Running `apply` again on an already saved v2 package fills Last BM if it remains blank without rewriting narratives. Only a button labeled exactly Save is accepted. The note remains Pending and is not signed, submitted, or sent to the office; only the RN sends it after reviewing and correcting the note. This workflow uses package schema 3; older packages must be redrafted and are never selected automatically. The newest matching draft is used, including one with post-save RN questions, rather than falling back to an older draft.

The older `fix_visit_note.py` workflow below remains available for existing users.

The **OASIS Document Writer** takes the structured audit report generated by `verifier/verify_oasis.py` and applies safe, deterministic **`DO NOW`** fixes directly to the pending target note in DeVero EHR.

It supports all official OASIS-E2 / EC2 document timepoints:
- **Start of Care (SOC - RFA 01)**
- **Resumption of Care (ROC - RFA 03)**
- **Recertification / Follow-Up (RFA 04 / 05)**
- **Discharge from Agency (DC - RFA 09)**
- **Transfer to Inpatient Facility (RFA 06 / 07)**
- **Death at Home (RFA 08)**

---

## Quick Start: Fast-Track Execution

### 1. Deterministic DO NOW Fixes (Dates, RFAs, Diagnoses, Cleanups)
```bash
uv run python writer/fix_oasis.py
```

### 2. Clinical Assessment Summary (AI Draft & Apply)
```bash
uv run python writer/fix_oasis_summary.py draft -t tasks_recert.jsonl --style-examples drafts/rn_style_examples.json --auto-approve
uv run python writer/fix_oasis_summary.py apply -t tasks_recert.jsonl --yes
```

### 3. Skilled Nurse Visit Note (one API draft, Save for RN review)
```bash
uv run python writer/fix_visit_note2.py draft -t tasks_svn.jsonl
uv run python writer/fix_visit_note2.py apply --yes -t tasks_svn.jsonl
```

---

## 1. Directory Structure

```
writer/
├── README.md            # Documentation and execution guide (this file)
├── fix_oasis.py         # Automated deterministic DO NOW fixes (all OASIS documents)
├── fix_oasis_summary.py # Universal clinician-reviewed assessment summary workflow
├── fix_oasis_recert.py  # Backward-compatible CLI for assessment summary workflow
├── fix_visit_note.py    # Skilled Nurse Visit Note narrative workflow (interventions, response, need, last BM)
└── recert_summary.py    # Package preparation, schema, and validation helpers
```

---

## 2. Core Workflow: Verifier → Writer

```
 +------------------------+        +---------------------------+        +--------------------------+
 |  1. Verifier           | -----> | 2. Audit JSON Log         | -----> | 3. Writer                |
 |  (verify_oasis.py)     |        | (latest + per-run JSON)   |        | (fix_oasis.py)           |
 |  Read-Only Inspection  |        | Punch List & Findings     |        | Applies Safe 'DO NOW' Fix|
 +------------------------+        +---------------------------+        +--------------------------+
                                                                                  │
                                                                                  ▼
                                                                        +--------------------------+
                                                                        | 4. DeVero Pending Note   |
                                                                        | SAVED (Pending, Unsigned)|
                                                                        +--------------------------+
```

---

## 3. What the Writer Automatically Fixes Across All OASIS Documents

| Issue Category | What the Writer Does | Fields Corrected |
| :--- | :--- | :--- |
| **Reason for Assessment (M0100)** | Aligns M0100 RFA radio/value to match document classification (01 SOC, 03 ROC, 04 Recert, 09 DC, 06 Transfer). | `M0100_ASSMT_REASON` |
| **Assessment Completed Date (M0090)** | Synchronizes M0090 with target assessment date. | `M0090_INFO_COMPLETED_DT` |
| **Visit Date Alignment** | Corrects visit header date to target assessment date. | `VisitDate` |
| **Timepoint Specific Dates** | Sets missing required timepoint dates to target date when flagged. | `M0030_START_CARE_DT` (SOC), `M0032_ROC_DT` (ROC), `M0906_DC_TRAN_DTH_DT` (DC / Transfer) |
| **Inverted Narrative Fields** | Swaps skilled teaching content into the Interventions box and patient findings into the Response box on any page. | `int_IntervEducation_Txt` ↔ `int_ResponseInterv_Txt` |
| **Pasted UI Prompts / Placeholders** | Clears erroneous UI label strings pasted as values across all pages. | `cf_AddedBPdetails_Txt`, `vtsig_AddVitalSign_Txt`, `eane_DescribeReported_Txt`, `card_Comments_Txt`, `nsc7_M1306Comments_Txt`, `sup_IndicateDet_Txt`, `nvn1_*` |
| **Malformed / Impossible Dates** | Clears out-of-range or future date typos (e.g. `05/01/2027` or `01/07/0456`). | `lbd_LastBm_Chk`, `imm_OtrVaccinationDate_Txt` |
| **Goal Progress % Reconciliation** | Aligns narrative goal progress percentage in `sum_AssessmentSum_Txt` with active library module progress (e.g. updating `30%` to `40%`). | `sum_AssessmentSum_Txt` |
| **Narrative Date Typo Cleanup** | Cleans impossible date references (e.g. `(05/01/2027 is not treated as a real BM date)`) and restores clean clinical prose across all notes. | `sum_AssessmentSum_Txt` |
| **Diagnoses (M1021 / M1023)** | Triggers "Update Diagnosis" action on diagnosis page when primary grid is empty, confirming alerts and populating active chart diagnoses. | `prim_diag`, `prim_diag_code`, `other_diag`, `other_diag_code` |
| **DOM & Angular Scope Sync** | Dispatches `input`, `change`, and `blur` events and triggers `$rootScope.$apply()` / `$evalAsync()`. | All modified fields across all pages |
| **Save-Only Rule** | Clicks the `Save` button on the final page to persist edits in DeVero. | **Status remains Pending; NEVER signed or sent.** |

---

## 4. What the Writer Automatically Fixes Across Skilled Nurse Visit Notes

| Issue Category | What the Writer Does | Fields Corrected |
| :--- | :--- | :--- |
| **Interventions & Education Narrative** | Replaces verbatim copy-forward narrative from prior visits with visit-specific assessment, vitals, fall report, pain management, and PT/OT coordination. | `int_IntervEducation_Txt` |
| **Patient / Caregiver Response** | Resolves missing or copied patient response with visit-grounded observations on teaching acceptance and adherence. | `int_ResponseInterv_Txt` |
| **Ongoing Skilled Need Justification** | Formulates clear, audit-proof justification for continued skilled nursing care based on current-visit clinical findings. | `osn_OngoingSkilledNeed_Txt` |
| **Date of Last Bowel Movement** | Identifies and documents the patient's last BM date (defaults to visit date under `--auto-approve` or accepts `--last-bm`). | `lbd_LastBm_Chk` |
| **Web Component Control Resolution** | Automatically extracts inner `<textarea>` / `<input>` controls from DeVero's custom `<cmwf-textarea>` containers. | All narrative controls on Page 2 |
| **Save-Only Server Verification** | Clicks `Save`, handles note window auto-closure, re-opens from Patient Chart, and verifies SHA-256 persistence. | Note remains **Pending and unsigned**. |

---

## 5. How to Run the Writer

### Option A: Auto-detect the Latest Audit Report (Recommended)
```bash
uv run python writer/fix_oasis.py
```

The verifier maintains a stable latest snapshot for this mode and creates an immutable timestamped audit for every run. The writer reads the audit report, plans safe fixes, applies them to the open DeVero document, and clicks Save.

### Option B: Run on a Specific Audit Report File
```bash
uv run python writer/fix_oasis.py logs/audit_example.json
```

### OASIS Writer Parameters (`writer/fix_oasis.py`)

| Parameter | Purpose |
| :--- | :--- |
| `[audit_file]` | Use an explicit verifier audit JSON. Recommended when selecting the exact note to update. If omitted, discover a recent audit under `logs/`. |
| `-t FILE`, `--tasks FILE`, `--config FILE` | Iterate over every matching active task in file order. Select an audit matching that task's patient, visit date, document type, and other supplied selectors. |
| `--patient NAME`, `--target-date DATE`, `--caregiver NAME`, `--doc-type TYPE`, `--source-doc-type TYPE`, `--source-date DATE` | Narrow task selection or audit discovery. |
| `-d CODE`, `--disposition CODE` | Supply the M2420 discharge disposition for fix planning, such as `01` for community or `02` for formal services. |
| `-h`, `--help` | Show command usage. |

`fix_oasis.py` applies and saves automatic fixes in one run. It does not accept `--style-examples` or `--yes`. A note without DO NOW items is left unchanged. Use the summary workflow below for the RN-style education narrative.

With `-t FILE`, disabled/skipped tasks are ignored. A missing or incomplete audit fails that task without opening its chart, and the remaining tasks continue. There is no fallback to another patient's or another visit's audit. The final report lists each task; exit status 2 indicates at least one failure. An explicit audit file can be combined with a task file only when selection matches one task. Without a task file or selectors, the newest audit is used.

```bash
uv run python writer/fix_oasis.py -t tasks_recert.jsonl

# Retry one task if needed.
uv run python writer/fix_oasis.py -t tasks_recert.jsonl --patient "Example Patient" --target-date 10/01/2026
```

### Option C: Clinician-Reviewed Assessment Summary Workflow (AI-Assisted & OpenAI-Compatible)
For all OASIS documents requiring a new Clinical Assessment Summary draft (`sum_AssessmentSum_Txt` across SOC, ROC, Recert, DC, Transfer):

> This workflow writes the narrative headed **Interventions/Education Provided:** into `sum_AssessmentSum_Txt`. On apply, it also fills blank or `Clear` Last BM (`lbd_LastBm_Chk`) with the target visit date. Existing Last BM values are preserved. The separate intervention, response, and skilled-need narratives remain unchanged.

Last BM is located using the target audit's field page, rather than a fixed OASIS page number. Existing approved packages can use their referenced audit without redrafting. If the field is absent from the audited form, it is skipped. When filled, it is saved with the summary and verified against the visit date after reopening. Reapplying an approved package also performs this blank-field backfill.

Shared page navigation waits for a usable pager and retries up to three times if a click is ignored or the page has not activated. It waits up to 15 seconds per attempt by default and confirms the requested editable control again after Angular settles. Field entry remains blocked if the requested page never becomes ready.

Drafting and independent model review enforce no specific measurements, dates, doses, counts, percentages, frequencies, or written-out numbers. Exact values remain in structured form fields. RN questions stay outside the clinical prose and block drafting/approval. The same checks also run during approval and before chart access on apply, including with `--yes`; older numeric drafts must be redrafted. A redraft clears the selected package's previous approval before contacting the model, so failure cannot leave an old draft approved.

An explicit `prepare` or `draft` request can rewrite a summary even when the verifier has no Clinical Assessment Summary review item. A complete versioned OASIS audit, captured summary context, and current-period evidence are still required; missing review items alone do not block drafting.

The narrative uses the supported facts available. Undocumented optional details (such as medication reconciliation, mental status, teaching response, incident outcomes, or therapy service status) are omitted rather than turned into required RN questions. Documented incidents and recommendations retain only their supported meaning. Contradictions that prevent accurate prose, claims needing confirmation, unresolved clinical review flags, or lack of any supported content still block drafting.

Revisions receive the previous draft and reviewer issues so documented teaching and recommendations can be retained. If review still fails after three attempts, the writer stores an unapproved `<draft-path>.blocked.txt` and matching `.review.json` with the remaining issues. It prints both paths and does not auto-approve that text. The RN can review/edit the blocked text against the evidence and explicitly attach it with `approve <package.json> <blocked-text-file> "Reviewer, RN"`; approval validation still applies. For missing visit facts, update the clinical evidence and rerun the verifier before drafting again.

Drafting and review share the same rule for exact values and frequencies. Supported scheduled medication administration can be described as scheduled or as prescribed; review must not require the omitted frequency to be restored. Required ongoing skilled administration must not be described as performed at this visit without evidence. To retry only a failed task and retain another task's approved draft, add `--patient` (and `--target-date` if needed) to the same task-file command. Rerunning the whole batch redrafts and clears previous approvals for every selected task.

Optional `--style-examples FILE` guides paragraph structure and assessment-to-teaching flow without imposing a word count. It accepts the same private, deidentified JSON array as the visit-note v2 writer: 1–8 nonempty strings, at most 10,000 characters each and 40,000 characters total. Keep it in ignored `drafts/`; its contents are sent to the configured model as style references, never as evidence.

#### 1. Configuration in `.env`
Set up your model and API endpoint in `.env`:
```env
# AI Assessment Summary Configuration (OpenAI-Compatible API)
# Supports OpenAI, OpenRouter, Groq, DeepSeek, Local Ollama, LM Studio, vLLM, etc.
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_API_KEY=your_api_key_here
OPENAI_MODEL=gpt-4o-mini
```

* **Zero Extra Dependencies:** Uses standard library `urllib` — no additional pip packages required.
* **Model configuration:** Uses the same structured model client as `fix_visit_note2.py`: `OPENAI_API_KEY` for OpenAI or loopback endpoints, `AI_API_KEY` for a different remote provider, and optional `OPENAI_REASONING_EFFORT` for drafting and review. Existing `--model`, `--base-url`, and explicit `--api-key` overrides remain supported.
* **Failed drafting:** API failures, invalid drafts, unresolved clinical review flags, or unresolved RN questions stop drafting. There is no automatic clinical synthesizer fallback.

#### 2. Fast-Track Execution (2 Commands)
Generate an AI draft with automatic clinician approval and apply directly to DeVero:
```bash
uv run python writer/fix_oasis_summary.py draft -t tasks_soc.jsonl --style-examples drafts/rn_style_examples.json --auto-approve
uv run python writer/fix_oasis_summary.py apply -t tasks_soc.jsonl --yes
```

`-t FILE` processes every matching active task in file order for prepare, draft, approve, validate, and apply. Disabled or skipped tasks are ignored. Each task selects its own matching audit/package and draft path. A failed or blocked task is reported while the remaining tasks continue; the command exits with status 2 if any task fails. Direct selectors such as `--patient "Example Patient" --target-date 10/01/2026` narrow the batch. Without a task file, the existing single-note discovery remains available.

Explicit input files and `--output` may be used only when selection matches one task. For a batch, use the default per-task paths. Approval requires that task's associated draft rather than falling back to another task's file. Apply and validate check the newest matching package; a newer blocked/unapproved package prevents selecting an older approved draft. `apply --yes -t FILE` saves each valid selected task and leaves notes Pending and unsigned.

#### 3. Standard 3-Step Execution (Zero Arguments Needed)
All commands automatically discover the newest audit log, review package, draft text, and clinician credentials:

```bash
# Step 1: Draft the Clinical Summary
uv run python writer/fix_oasis_summary.py draft
```
* Prepares the review package from the latest verifier audit log.
* Drafts and independently reviews against verified current-period evidence, resolved review flags, and historical baseline provenance. Numeric evidence is available to the model but specific numbers are excluded from the output.
* Saves the formatted narrative to `drafts/draft_<category>_<patient>_<date>.txt` and displays a character/word preview.

```bash
# Step 2: Approve the Draft
uv run python writer/fix_oasis_summary.py approve
```
* Attaches the draft text to the package.
* Automatically records the reviewing clinician from the note task (`caregiver`).
* Computes and verifies SHA-256 integrity hashes for data safety.

```bash
# Step 3: Apply to DeVero (Save-Only)
uv run python writer/fix_oasis_summary.py apply --yes
```
* Navigates directly to the summary page in DeVero.
* Populates `sum_AssessmentSum_Txt` and triggers Angular scope synchronization.
* Clicks `Save` and verifies persistence.
* Enters text through a unique visible editable control, verifies it survives blur, and waits for Save traffic to settle before reopening. A save timeout leaves the note open for review rather than closing a tab with a pending request. Server-reloaded text must still match the approved draft.
* After reopening, waits up to 20 seconds for Angular to load the saved value and requires matching reads separated by a short wait. An initially blank textarea is not treated as a final server value; a persistently blank or different value still fails verification.
* **Status remains Pending; NEVER signed or sent.**

#### 4. Command Options Reference (`writer/fix_oasis_summary.py`)
- **`draft`**:
  - `uv run python writer/fix_oasis_summary.py draft [package_or_audit.json]` (Explicit file)
  - `-t, --tasks, --config <file>`: Iterate over all matching active tasks in a JSONL file; also available for prepare, approve, validate, and apply
  - `--patient <name>`, `--target-date <date>`: Direct task selection filters
  - `--model <name>`: Override `OPENAI_MODEL` (e.g. `--model gpt-5.6-luna`)
  - `--base-url <url>`: Override `OPENAI_BASE_URL`
  - `--api-key <key>`: Override `OPENAI_API_KEY`
  - `--style-examples <file>`: Private JSON array of RN-approved examples for style only; optional. The narrative remains number-free without this flag.
  - `--output <path>`: Custom output path for draft `.txt`
  - `--auto-approve`: Automatically attach draft to package upon generation
  - `--approved-by <name>`: Specify clinician credentials when auto-approving
- **`approve`**:
  - `uv run python writer/fix_oasis_summary.py approve` (Auto-detects latest package & draft)
  - `uv run python writer/fix_oasis_summary.py approve <pkg.json> <draft.txt> "Clinician Name, RN"` (Explicit positional syntax)
  - `--approved-by <name>`: Flag syntax for reviewing clinician
- **`apply`**:
  - `uv run python writer/fix_oasis_summary.py apply --yes` (Recommended: non-blocking apply)
  - `uv run python writer/fix_oasis_summary.py apply` (Prompts for typed confirmation phrase)
  - `uv run python writer/fix_oasis_summary.py apply <package.json> -y` (Explicit package path)

---

### Option D: RN-Reviewed Skilled Nurse Visit Note Workflow (`writer/fix_visit_note.py`)

For routine Skilled Nurse Visit Notes requiring visit-specific narrative updates and Date of Last BM. Supports the exact same streamlined CLI ergonomics as the OASIS summary workflow (`draft --auto-approve` / `apply --yes` / `-t`).

#### 1. Permitted Field Scope
This workflow writes **only** to the following 4 routine visit note fields across Pages 1 and 2:
- `int_IntervEducation_Txt`: Interventions & Education Provided
- `int_ResponseInterv_Txt`: Patient / Caregiver Response to Interventions
- `osn_OngoingSkilledNeed_Txt`: Ongoing Skilled Nursing Need Justification
- `lbd_LastBm_Chk`: Date of Last Bowel Movement (MM/DD/YYYY)

#### 2. Fast-Track Execution (2 Commands)
Generate grounded visit-note narratives with auto-approval and apply directly to DeVero:
```bash
uv run python writer/fix_visit_note.py draft --auto-approve
uv run python writer/fix_visit_note.py apply --yes -t tasks_svn.jsonl
```

- **`draft --auto-approve`**:
  - Automatically identifies the latest visit-note audit log (strictly separating visit notes from OASIS audits).
  - Drafts visit-specific narratives using the configured OpenAI-compatible model, with automatic fallback to the built-in clinical synthesizer (`synthesize_visit_note`) if offline.
  - Automatically defaults `lbd_LastBm_Chk` to the target visit date if empty, synthesizes a grounded response to teaching, attaches the draft, and validates package integrity.
- **`apply --yes`**:
  - Connects to DeVero via CDP or Chrome persistent profile and opens the target Pending note.
  - Resolves DeVero's custom web components (`<cmwf-textarea>`, `<cmwf-textbox>`) to the underlying editable controls.
  - Avoids redundant page clicks if the target page is already active.
  - Synchronizes form controls and Angular scopes (`$rootScope.$applyAsync()`).
  - Clicks `Save` (Save-only; remains Pending and unsigned).
  - Automatically recovers and verifies persistence from the Patient Chart if the note popup closes after Save.

#### 3. Standard 4-Step RN Review Workflow (Manual Entry)
When the reviewing nurse wishes to inspect drafts and provide explicit clinical observations:

```bash
# Step 1: Draft narratives (inserts clinical review markers)
uv run python writer/fix_visit_note.py draft -t tasks_svn.jsonl

# Step 2: RN Review & Explicit Approval
uv run python writer/fix_visit_note.py approve -t tasks_svn.jsonl \
  --last-bm 09/02/2026 \
  --response "Patient verbalized understanding of low-sodium diet and fall precautions." \
  --approved-by "Example, Clinician (RN)"

# Step 3: Validate Review Package Integrity
uv run python writer/fix_visit_note.py validate -t tasks_svn.jsonl

# Step 4: Save-Only Apply to DeVero
uv run python writer/fix_visit_note.py apply -t tasks_svn.jsonl --yes
```

#### 4. Command Options Reference (`writer/fix_visit_note.py`)
- **`draft`**:
  - `uv run python writer/fix_visit_note.py draft [package_or_audit.json]`
  - `-t, --tasks, --config <file>`: Match against target note in tasks file
  - `--patient <name>`, `--target-date <date>`: Direct task selection filters
  - `--model <name>`, `--base-url <url>`, `--api-key <key>`: Override LLM settings
  - `--output <path>`: Custom output path for draft `.txt`
  - `--auto-approve`: Automatically attach generated draft and default Last BM
  - `--approved-by <name>`: Reviewing clinician credentials
  - `--last-bm <date>`: RN-supplied Last BM date (MM/DD/YYYY)
  - `--response <text>`: RN-supplied teaching response narrative
- **`approve`**:
  - `uv run python writer/fix_visit_note.py approve [package.json] [draft.txt] [approved_by]`
  - `-t, --tasks, --config <file>`: Task selector filter
  - `--approved-by <name>`: Reviewing clinician credentials
  - `--last-bm <date>`: RN-supplied Last BM date
  - `--response <text>`: RN-supplied teaching response narrative
  - `--output <path>`: Output package JSON path
- **`validate`**:
  - `uv run python writer/fix_visit_note.py validate [package.json]`
  - `-t, --tasks, --config <file>`: Task selector filter
- **`apply`**:
  - `uv run python writer/fix_visit_note.py apply [package.json] --yes`
  - `-t, --tasks, --config <file>`: Task selector filter
  - `-y, --yes`: Skip typing confirmation phrase

#### 5. Technical Implementation & Resilience Features
The Skilled Nurse Visit Note writer incorporates four key engineering solutions to guarantee reliable DeVero form automation:

1. **Custom Web Component (`<cmwf-textarea>`) Resolution:**
   DeVero encloses multiline text controls inside custom angular Web Components (`<cmwf-textarea id="int_IntervEducation_Txt">`). Direct Playwright calls (`locator.fill()`) fail if targeted at custom elements. `_visible_field` and `_extract_editable_control` drill down to the inner `<textarea>` element. Furthermore, `_set_field` injects the value via `HTMLTextAreaElement.prototype` descriptor setter and dispatches `input`, `change`, `blur`, and `$rootScope.$applyAsync()` to ensure full Angular scope binding.

2. **Intelligent Single-Page Navigation (No Redundant Clicks):**
   All 4 routine visit note fields reside on Page 2. Re-clicking an already active pagination button in DeVero can trigger a controller reload, wiping out in-progress DOM modifications. `_navigate_to_page` inspects pagination button classes (`.active`) and skips redundant clicks when already positioned on the target page.

3. **Post-Save Window Recovery & Direct Chart Re-verification:**
   When the `Save` button is clicked on a visit note popup, DeVero commits the record and immediately closes the popup tab. The writer wraps the post-save delay to catch `TargetClosedError`, detects if the note window closed, automatically re-opens the target note from the Patient Chart, and navigates to verify that each field's server-persisted value matches the approved SHA-256 hash.

4. **Deterministic Clinical Synthesizer Fallback (`synthesize_visit_note`):**
   If `OPENAI_API_KEY` is not configured or the OpenAI-compatible endpoint is unreachable, drafting falls back automatically to the built-in clinical synthesizer. It extracts grounded EHR data (vitals, GI/bowel, skin, fall reports, PT/OT therapy, pain management, medications) to generate authentic, compliant clinical narratives without hallucination.

---

## 6. Port Invariants & Safety Rules

1. **Target Note Only:** The writer only opens and modifies the exact pending target document.
2. **Immutable Baseline:** Completed/signed source notes are strictly read-only and never modified.
3. **Save-Only Authority:** The writer will **never** click Sign, Mark as Signed, or Send to Office. All notes remain Pending and unsigned.
4. **Clinical Judgment Separation:** Findings marked under `CLINICAL JUDGMENT / USER REVIEW` (such as reviewing library effective dates from prior episodes or clinical assessment narratives) are not blindly overwritten; they require clinician review before signing.
5. **Grounded AI Synthesis:** The OpenAI-compatible generator strictly uses verified, audited EHR context (vitals, diagnoses, systems assessment, interventions, patient goals) and prohibits clinical hallucinations.
