# Use the Devero Fleet command-line interface

This reference explains every `devero_fleet` command, its safety boundary, and its expected output. Run commands from WSL Bash in `/app/workspaces/devero`.

## Start in the repository

Change to the repository and display the top-level help:

```bash
cd /app/workspaces/devero
.venv/bin/python -m devero_fleet -h
```

Use this general syntax:

```text
.venv/bin/python -m devero_fleet [global_options] command [command_options]
```

The only global option is `--runtime-dir owner_private_path`. Place it before the command. Most runs should use the default runtime directory.

```bash
.venv/bin/python -m devero_fleet \
  --runtime-dir /path/to/owner_private_runtime \
  show DV-123456789012
```

Case envelopes expire after four hours. The runtime stores case data and verification artifacts with owner-only permissions.

## Choose a command

| Command | Use it to | Output contains PHI |
| --- | --- | --- |
| `intake` | Create a temporary case envelope | No, only the case ID |
| `show` | Read case status and counts | No |
| `work-context` | Load role-specific case details | Yes |
| `confirm-identity` | Attest the live pending target | No |
| `authorize-save` | Create a one-use Save grant | No |
| `consume-save` | Consume the grant before Save | No |
| `approve-corrections` | Approve specific correction IDs | No |
| `approve-record-supported` | Approve all record-supported Writer findings | No |
| `verify-scan` | Run deterministic read-only verification | No |
| `verification-context` | Read the full verification evidence | Yes |
| `verification-report` | Print a plain-language RN findings report | No |
| `record-verification` | Store the final semantic review | Input file may contain PHI |
| `cancel` | Delete an abandoned case | No |
| `close` | Delete a completed case | No |
| `sweep` | Delete all expired cases | No |
| `browser` | Manage shared browser ownership | No |

Protected Health Information (PHI) must stay in the owner-private runtime and local specialist context. Do not paste PHI-bearing output into Telegram, chat, Kanban, logs, or committed files.

## Create a case with `intake`

`intake` creates an owner-private case envelope and prints an opaque case ID such as `DV-123456789012`.

### Interactive intake

Use interactive intake when entering one case manually:

```bash
.venv/bin/python -m devero_fleet intake
```

The command asks for:

- `mode`: `copy`, `write`, `verify`, or `correct`
- Patient identity
- Caregiver
- Target document type
- Optional source document type and date
- Target date
- Optional current-status facts

Send only the returned case ID to Hermes Chief or another specialist.

### File intake

Use `--from-file` for an existing task file:

```bash
chmod 600 private_case.json
.venv/bin/python -m devero_fleet intake \
  --from-file private_case.json
```

The file must:

- Be a regular file, not a symbolic link
- Use owner-only permissions such as `chmod 600`
- Contain one case object or exactly one active task
- Include `patient`, `doc_type`, and `target_date`

The loader accepts the repository's commented JSON task-list format and trailing commas. It ignores tasks with `"enabled": false` or `"skip": true`.

## Inspect case status with `show`

`show` prints a PHI-minimized operational summary:

```bash
.venv/bin/python -m devero_fleet show DV-123456789012
```

The response includes:

- Case ID and mode
- Current lifecycle status
- Whether identity is resolved
- Whether Save is authorized
- Punch-list and approved-correction counts
- Expiration time

Use `show` in shell output, Chief coordination, and PHI-free troubleshooting.

## Load specialist data with `work-context`

`work-context` prints the minimum PHI-bearing data assigned to one specialist:

```bash
.venv/bin/python -m devero_fleet work-context \
  DV-123456789012 --role verifier
```

Choose one role:

- `writer`: identity, approved corrections, raw status, and punch list
- `verifier`: identity, raw status, transfer state, corrections, and verification state
- `copier`: identity and the role's permitted case context

This output is for local specialist processing. Never forward or quote it.

## Attest the target with `confirm-identity`

`confirm-identity` records that a specialist checked the exact live pending target:

```bash
.venv/bin/python -m devero_fleet confirm-identity \
  DV-123456789012 \
  --role verifier \
  --patient-match \
  --document-match \
  --date-match \
  --pending-target
```

All four flags are required:

- `--patient-match`: Patient identity matches the case
- `--document-match`: Document type matches the case
- `--date-match`: Target date matches the case
- `--pending-target`: The target remains Pending

Choose `writer`, `verifier`, or `copier` for `--role`. Run this command only after checking the live record. A new identity attestation revokes any earlier Save authorization.

## Authorize Save with `authorize-save`

`authorize-save` creates a one-use Save grant after identity confirmation:

```bash
.venv/bin/python -m devero_fleet authorize-save \
  DV-123456789012
```

Chief runs this command only after the owner explicitly authorizes Save for the current case. The grant authorizes Save only. It never authorizes signing, submission, approval, or finalization.

## Consume Save permission with `consume-save`

`consume-save` consumes the one-use grant immediately before a Save action:

```bash
.venv/bin/python -m devero_fleet consume-save \
  DV-123456789012 --role writer
```

Choose `writer` or `copier` for `--role`. The command changes the case to the saving state but does not click Save. A consumed grant cannot be reused.

The deterministic Copier may consume its grant inside its normal gated workflow. Do not consume a grant early.

## Approve findings with `approve-corrections`

`approve-corrections` marks named correction IDs as approved:

```bash
.venv/bin/python -m devero_fleet approve-corrections \
  DV-123456789012 \
  DVV-TIMEPOINT-M0100 \
  DVV-CURRENT-STATUS-NARRATIVE-01
```

Every ID must already exist on the case. Approval does not edit Devero, authorize Save, or approve unrelated findings.

To approve every finding that already has record support and is routed to Writer, use one command:

```bash
.venv/bin/python -m devero_fleet approve-record-supported \
  DV-123456789012 --phase initial
```

This excludes all findings that require user, RN, clinician, or clinical-policy review. It does not edit Devero or authorize Save.

## Run deterministic verification with `verify-scan`

`verify-scan` navigates Devero and extracts evidence without editing the record:

```bash
.venv/bin/python -m devero_fleet verify-scan \
  DV-123456789012 --phase initial
```

Choose one phase:

- `initial`: Verify the pending document before correction
- `persistence`: Verify the document after an approved Save and compare it with the initial snapshot

The scan:

- Claims and releases the shared browser automatically
- Confirms patient, document, date, caregiver, and Pending status
- Captures all target pages and bounded supporting documents
- Applies deterministic date, required-field, copy-forward, and persistence rules
- Covers E2 Start of Care, Resumption of Care, Recertification, Other Follow-Up, Transfer, Death at Home, and Discharge
- Validates CMS E2 v3.02.0 time-point item applicability and coded response domains exposed by Devero
- Stores an owner-private verification artifact
- Prints a PHI-free summary

Interpret `run_status` before `verdict`. The run status is:

- `COMPLETE`: Extraction finished and deterministic findings are available
- `BLOCKED`: Authentication, identity, navigation, or extraction did not complete

A complete run has one verdict:

- `PASS`: A complete deterministic scan found no deterministic corrections
- `MIXED`: A complete scan found one or more correctable or review-required issues
- `FAIL`: A complete scan found a material non-editable conflict

The deterministic verdict is provisional when `semantic_review_required` is `true`. Ask Hermes Chief to delegate semantic review to the Verifier.

Send this PHI-free Telegram message:

```text
Verify case DV-123456789012, initial phase.
Return an RN-friendly report with the page or section,
the issue, and what the RN should verify or correct.
Do not Save or Sign.
```

## Read verification evidence with `verification-context`

`verification-context` prints the complete PHI-bearing artifact:

```bash
.venv/bin/python -m devero_fleet verification-context \
  DV-123456789012 --role verifier --phase initial
```

Only the `verifier` role may use this command. Choose `initial` or `persistence` for `--phase`.

The raw output can contain thousands of fields. Do not use it as the RN-facing report, pipe it to `more`, paste it into chat, or save it outside the owner-private runtime.

### Display a PHI-minimized technical summary

Use `jq` to display status and correction codes:

```bash
.venv/bin/python -m devero_fleet verification-context \
  DV-123456789012 --role verifier --phase initial \
  | jq '.audit | {
    run_status, verdict, document_kind, counts,
    semantic_review_required,
    correction_count: (.corrections | length),
    corrections: [.corrections[] | {
      id, issue_class, location,
      evidence_class, fact_status, route
    }]
  }'
```

### Display deterministic findings for an RN

Use the dedicated plain-language command. It prints a numbered checklist and does not expose the complete field bundle or require JSON tools:

```bash
.venv/bin/python -m devero_fleet verification-report \
  DV-123456789012 --phase initial
```

To show only findings that already have record support and can be routed to Writer after explicit approval:

```bash
.venv/bin/python -m devero_fleet verification-report \
  DV-123456789012 --phase initial --record-supported-only
```

This report contains deterministic findings only. Hermes Verifier must complete semantic review before you treat it as the final verification result.

## Store semantic review with `record-verification`

`record-verification` stores a final, validated Verifier result:

```bash
chmod 600 final_verification.json
.venv/bin/python -m devero_fleet record-verification \
  DV-123456789012 \
  --phase initial \
  --from-file final_verification.json
```

The input must be a regular, non-symbolic-link JSON file with owner-only permissions. The result must:

- Set `run_status` to `COMPLETE` or `BLOCKED`
- Set `review_stage` to `FINAL`
- Set `semantic_review_required` to `false`
- Use `PASS`, `MIXED`, or `FAIL` for a complete verdict
- Preserve every deterministic correction without changing or removing it
- Use PHI-free correction IDs and locations

Hermes Verifier normally creates and records this file. An RN should review the RN-facing report instead of writing this JSON manually.

## Delete an abandoned case with `cancel`

`cancel` deletes an abandoned case and all of its artifacts:

```bash
.venv/bin/python -m devero_fleet cancel \
  DV-123456789012
```

The deletion also removes a browser claim owned by that case. The operation cannot be undone.

## Delete a completed case with `close`

`close` deletes a completed case and all of its artifacts:

```bash
.venv/bin/python -m devero_fleet close \
  DV-123456789012
```

Use `close` after verification, correction, and required persistence checks finish. The operation cannot be undone.

`cancel` and `close` delete the same files. Their names record your operational intent before deletion.

## Delete expired cases with `sweep`

`sweep` removes every case whose four-hour lifetime has expired:

```bash
.venv/bin/python -m devero_fleet sweep
```

The response reports `expired_removed`. The sweep also deletes each expired case's artifacts and browser claim.

## Manage browser ownership with `browser`

The `browser` command serializes access to the shared Devero browser. Claims expire after 15 minutes unless the owner sends a heartbeat.

### Claim the browser

Use `claim` before a multi-step browser task:

```bash
.venv/bin/python -m devero_fleet browser claim \
  DV-123456789012 verifier
```

Choose `chief`, `writer`, `verifier`, or `copier`. Another case or role cannot claim the browser until the current owner releases it or the claim expires.

### Extend a claim

Use `heartbeat` during a browser task that lasts longer than the claim interval:

```bash
.venv/bin/python -m devero_fleet browser heartbeat \
  DV-123456789012 verifier
```

Only the current case and role can extend the claim.

### Release the browser

Use `release` when the browser task finishes:

```bash
.venv/bin/python -m devero_fleet browser release \
  DV-123456789012 verifier
```

Releasing an absent claim succeeds. A different case or role cannot release an active claim.

### Check browser status

Use `status` to see whether the shared browser is owned:

```bash
.venv/bin/python -m devero_fleet browser status
```

The response returns `busy: false` or the active case ID, role, and expiration time.

`verify-scan` manages its claim automatically. Manual claims support specialist workflows that span multiple browser calls.

## Follow the verification workflow

Use this sequence for read-only verification:

1. Run `intake` and retain the opaque case ID.
2. Run `verify-scan --phase initial`.
3. Stop if the run status is `BLOCKED`.
4. Ask Chief in Telegram to delegate the case to Verifier.
5. Review the RN-facing final report.
6. Run `close` after the case is finished.

Use this sequence after approved corrections:

1. Confirm the live identity with `confirm-identity`.
2. Approve named corrections with `approve-corrections`.
3. Obtain explicit Save authorization from the owner.
4. Run `authorize-save`.
5. Consume the grant immediately before Save.
6. Save without signing, submitting, approving, or finalizing.
7. Run `verify-scan --phase persistence`.
8. Complete semantic review and then run `close`.

## Interpret errors and exit codes

Successful commands exit with code `0`. Invalid arguments, missing or expired cases, unsafe file permissions, unknown correction IDs, invalid verification results, and authorization failures exit with code `2`.

Use command-specific help when an argument is unclear:

```bash
.venv/bin/python -m devero_fleet verify-scan -h
.venv/bin/python -m devero_fleet browser -h
.venv/bin/python -m devero_fleet browser claim -h
```
