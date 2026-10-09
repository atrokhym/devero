---
name: devero-runner
description: Command reference and workflow for all Devero Python automation scripts. Use for every Devero request.
---

# Devero Runner

Working directory: `/app/workspaces/devero`

Always read `writer/README.md` and `verifier/README.md` for detailed usage, field scope, safety rules, and implementation notes.

## Workflow

The standard workflow has 4 steps. The user tells you which doc type to process and you follow these steps in order.

### Step 1: Build task config file

Scrapes pending forms from the Devero dashboard and creates a tasks JSONL file used by all subsequent commands.

```bash
uv run python devero_tasks.py \
  -d "Skilled Nurse Visit Note" \
  -o tasks_note.json
```

Options:
- `-d, --doc-type` — Document type to filter for (default: `Skilled Nurse Visit Note`)
- `-o, --output FILE` — Output JSON file path (default: `tasks_note.json`)
- `-c, --caregiver NAME` — Optional caregiver filter
- `--no-lookup` — Skip chart navigation for source date lookup
- `--default-source-date DATE` — Default source date if lookup skipped
- `--cdp-url URL` — CDP endpoint (default: `http://127.0.0.1:9222`)
- `--headless` — Run browser headlessly

### Step 2: Copy document (source → target)

Copies fields from a completed source note into the pending target note. Choose the right copier based on doc type.

**For Skilled Nurse Visit Notes:**
```bash
uv run python devero_copier_note.py -t tasks_note.json
```

**For OASIS documents (SOC, ROC, Recert, Discharge, Transfer):**
```bash
uv run python devero_copier_oasis.py -t tasks.jsonl
```

Both copiers accept:
- `-t, --tasks, --config FILE` — Tasks configuration file (default: `tasks.jsonl`)
- Or positional: `uv run python devero_copier_note.py tasks_note.json`

### Step 3: Verify document (read-only)

Produces a structured punch list: **DO NOW** (must fix), **CLINICAL JUDGMENT** (clinician review), **LEAVE** (verified OK).

```bash
# From tasks file
uv run python verifier/verify_oasis.py -t tasks_note.json

# Or with explicit parameters
uv run python verifier/verify_oasis.py \
  --patient "Last, First" \
  --doc-type "OASIS-E2 Nurse Recert" \
  --target-date "MM/DD/YYYY"
```

Options:
- `-t, --tasks, --config FILE` — Tasks config file (default: `tasks.jsonl`)
- `--patient NAME` — Patient name
- `--caregiver NAME` — Caregiver name
- `--doc-type TYPE` — Target document type
- `--source-doc-type TYPE` — Source document type
- `--target-date DATE` — Target date (MM/DD/YYYY)
- `--source-date DATE` — Source date (MM/DD/YYYY)

Supported doc types: `OASIS-E2 Nurse Start of Care`, `OASIS-E2 Nurse Resumption of Care`, `OASIS-E2 Nurse Recert`, `OASIS-E2 Discharge`, `OASIS-E2 Transfer`, `Skilled Nurse Visit Note`

See `verifier/README.md` for full documentation.

### Step 4: Write fixes

Choose the writer command based on document type and what needs fixing.

**Deterministic DO NOW fixes (dates, RFAs, diagnoses, cleanups):**
```bash
uv run python writer/fix_oasis.py
uv run python writer/fix_oasis.py -t tasks.jsonl
uv run python writer/fix_oasis.py logs/audit_<name>_<date>.json
```

`fix_oasis.py -t FILE` processes every matching active task and selects each task's exact matching audit. `--patient` and `--target-date` can narrow the batch. Tasks with missing/incomplete audits fail without chart access; remaining tasks continue, and any failure produces exit status 2. An explicit audit file requires one selected task. There is no fallback to another visit's audit.

**OASIS narrative headed Interventions/Education Provided (Clinical Assessment Summary) — draft then apply:**
```bash
uv run python writer/fix_oasis_summary.py draft -t tasks_recert.jsonl --style-examples drafts/rn_style_examples.json --auto-approve
uv run python writer/fix_oasis_summary.py apply -t tasks_recert.jsonl --yes
```
Task selectors: `-t tasks_soc.jsonl`, `--patient "X" --target-date "MM/DD/YYYY"`

For the summary workflow, `-t FILE` iterates over all matching active tasks for prepare, draft, approve, validate, and apply. Each task uses its own matching files. Failures are reported and the remaining tasks continue; any failure produces exit status 2. Explicit input/output paths require a single selected task. `apply --yes -t FILE` writes each valid selected task after the user's write confirmation.

Writes `sum_AssessmentSum_Txt` and fills blank or `Clear` Last BM (`lbd_LastBm_Chk`) with the visit date on apply; existing Last BM values are preserved and filled dates are verified after Save. The separate `int_IntervEducation_Txt` field is not rewritten. `draft --style-examples FILE` optionally supplies private RN style references. Drafting and independent review use the visit-note v2 model settings. Specific numbers and RN comments are rejected in the narrative during draft, approval, and apply. Unresolved factual questions or clinical flags block drafting, and API failure stops the run without a synthesizer fallback. Obtain the user's write confirmation before apply. `fix_oasis.py` remains deterministic cleanup and no longer accepts `--style-examples`.

**Skilled Nurse Visit Note narrative — draft then apply:**
```bash
uv run python writer/fix_visit_note2.py draft -t tasks_svn.jsonl --style-examples drafts/rn_style_examples.json
uv run python writer/fix_visit_note2.py apply --yes -t tasks_svn.jsonl
```

Visit-note v2 saves clinical drafts as Pending for subsequent RN review. Specific unresolved RN questions remain outside clinical prose and do not block apply. The final apply output and private `drafts/rn_review_visit_note2_<timestamp>.md` report list review items by updated visit and distinguish failed updates. Preserve target identity, evidence, integrity, contradiction, and persistence checks. Save only: never sign, submit, or send to the office; the RN reviews/corrects the note and sends it herself.

See `writer/README.md` for full documentation, field scope, and all subcommands (prepare, draft, approve, resolve, validate, apply).

## Utility commands

**Reprint audit report from saved JSON:**
```bash
uv run python verifier/print_audit_log.py
uv run python verifier/print_audit_log.py logs/audit_<name>_<date>.json
```

## Safety invariants

- Scripts save but **never sign, submit, or finalize**.
- All notes remain Pending and unsigned after script execution.
- Completed/signed source notes are never modified.
- Always follow `writer/README.md` and `verifier/README.md`.
