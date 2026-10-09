# Devero Fleet

Secure local runtime for the Devero Chief, Writer, Verifier, and Copier.

See the [complete command-line reference](CLI.md) for every command, option, safety boundary, and example.

## Setup

From the repository root:

```bash
uv sync
```

Configure the Devero connection in the repository `.env` file:

```text
DEVERO_URL=https://your-agency.devero.com
DEVERO_USERNAME=your-login
DEVERO_PASSWORD=your-password
DEVERO_CDP_URL=http://127.0.0.1:9222
DEVERO_HEADLESS=true
```

The default case directory is owner-private and temporary. Override it only
with another owner-private directory using the global `--runtime-dir` option.

## Start a case

Create an interactive case. Patient and clinical prompts are hidden:

```bash
.venv/bin/python -m devero_fleet intake
```

The command prints an opaque `DV-...` case id. Send only that id to Hermes
Chief. Do not put patient names, dates, diagnoses, or note text in chat,
Kanban, shell arguments, or committed files.

For automation, use an owner-private JSON file (`chmod 600`):

```json
{
  "mode": "verify",
  "identity": {
    "patient": "LAST, FIRST",
    "caregiver": "CLINICIAN NAME (RN)",
    "doc_type": "OASIS-E2 Nurse Recert.",
    "source_doc_type": "OASIS-E1 Nurse Recert.",
    "source_date": "MM/DD/YYYY",
    "target_date": "MM/DD/YYYY"
  }
}
```

```bash
.venv/bin/python -m devero_fleet intake --from-file private-case.json
```

## Verify a document

The Verifier supports:

- OASIS-E2 Nurse Start of Care
- OASIS-E2 Resumption of Care
- OASIS-E2 Nurse Recertification
- OASIS-E2 Other Follow-Up
- OASIS-E2 Transfer, with or without agency discharge
- OASIS-E2 Death at Home
- OASIS-E2 Discharge
- Devero nursing/PT and visit/non-visit labels for those E2 time points
- Skilled Nurse Visit Note

Run the read-only deterministic scan:

```bash
.venv/bin/python -m devero_fleet verify-scan DV-XXXXXXXXXXXX --phase initial
```

The result is provisional: Python checks identity, extraction completeness,
required fields, dates, all CMS E2 time-point families, CMS v3.02.0 item
applicability and coded response domains exposed by Devero, E1-to-E2 evidence
mappings, stale copy-forward, and persistence. It reports `semantic_review_required` until
Hermes Verifier completes the bounded AI review.

For a human-readable, PHI-minimized status, filter the evidence down to the
audit outcome and correction codes:

```bash
.venv/bin/python -m devero_fleet verification-context DV-XXXXXXXXXXXX \
  --role verifier --phase initial \
  | jq '.audit | {
  run_status, verdict, document_kind, counts, semantic_review_required,
  correction_count: (.corrections | length),
  corrections: [.corrections[] |
    {id, issue_class, location, evidence_class, fact_status, route}]
}'
```

The unfiltered evidence command is for the local Hermes Verifier only. It
prints the complete PHI-bearing bundle and can be very large:

```bash
.venv/bin/python -m devero_fleet verification-context DV-XXXXXXXXXXXX \
  --role verifier --phase initial
```

Do not pipe the raw form to `more` for routine review, and never forward its
output to chat, Kanban, logs, or committed files. After semantic review, Hermes
records the final owner-private result with `record-verification`; the case
store rejects any result that removes or changes deterministic findings.

After an approved Writer Save, run the persistence phase:

```bash
.venv/bin/python -m devero_fleet verify-scan DV-XXXXXXXXXXXX --phase persistence
```

Completed E1 documents are historical evidence only. They may support copying
data into a new E2 document, but they never receive an E1 compliance verdict
and never prove that current E2 content is correct.

## Finish a case

Inspect a PHI-minimized status:

```bash
.venv/bin/python -m devero_fleet show DV-XXXXXXXXXXXX
```

Delete the case and its evidence when finished:

```bash
.venv/bin/python -m devero_fleet close DV-XXXXXXXXXXXX
```

Verification is read-only. It never edits, Saves, signs, submits, approves, or
finalizes a Devero clinical record.
