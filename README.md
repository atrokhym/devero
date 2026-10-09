# Devero Document Copier Automation

Automated tool to copy dynamic form clinical notes in Devero (e.g. Skilled Nurse Visit Notes) from a completed source note to a pending target note.

## Setup & Instructions

### Step 1: Launch Chrome in Debug Mode
To allow the script to operate inside your existing logged-in Devero browser session:
1. Close your running Chrome windows (or start the debug instance).
2. Double-click `launch_chrome_debug.bat` (or run in Command Prompt/PowerShell):
   ```cmd
   launch_chrome_debug.bat
   ```
3. Log into **Devero** in the Chrome window that opens.

### Step 2: Configure `tasks.jsonl`
Create a local, Git-ignored `tasks.jsonl` with your list of patient notes to copy. Keep real identifiers out of committed files:

```json
[
  {
    "patient": "LAST, FIRST",
    "caregiver": "CLINICIAN NAME (RN)",
    "doc_type": "Skilled Nurse Visit Note",
    "source_date": "MM/DD/YYYY",
    "target_date": "MM/DD/YYYY"
  }
]
```

### Step 3: Run the Copier Script
Run the Python script:
```powershell
py devero_copier.py
```

The script will automatically navigate your Chrome browser to each source note, extract all form field values, navigate to the target note, populate the fields, and trigger AngularJS update events.

## Hermes specialist fleet

The repository includes a native Hermes Bot Mode fleet matching the Grokbot roles:

- `devero` — Chief of Staff and the only user-facing Telegram/Desktop coordinator
- `writer` — scoped pending-note corrections and current-status documentation
- `verifier` — read-only source/target and persistence verification
- `copier` — deterministic note and OASIS transfer

Bot Mode (`agent.bot_mode_protocol: true`) is the coordination layer. Canonical Bot Chats use `message_agent`; the dedicated `devero` Kanban board is the durable fallback for Telegram sessions. The existing Chrome DevTools MCP remains only as the browser connection for specialists—there is no custom Devero MCP server.

Install or reconcile the profiles:

```bash
.venv/bin/python scripts/setup_hermes_fleet.py verify-package
.venv/bin/python scripts/setup_hermes_fleet.py install
.venv/bin/python tests/verify_hermes_live.py
```

Before messaging Chief, create secure local intake in the WSL workspace. The prompts that can contain identity or clinical facts are hidden from terminal history:

```bash
.venv/bin/python -m devero_fleet intake
```

Send only the returned `DV-...` id to Chief in Telegram or Desktop. Only that opaque id belongs in Bot messages and Kanban. Identifiers and clinical facts live in owner-only, four-hour case envelopes. A specialist must attest the exact live pending target, the user must confirm Save for that case, and Chief then issues a one-use Save grant. Save never authorizes signing, submission, approval, or finalization. No scheduled intake is installed in v1.

Specialist sessions necessarily process the clinical content visible in local Devero/Chrome. Keep Hermes local and access-controlled, do not enable external observability for these profiles, and close or cancel cases promptly so temporary envelopes and diagnostics are erased.

### Read-only document verification

The verifier is a new Python-plus-AI workflow, independent of the older Recert-only verifier. Python performs exact pending-target matching, full-page read-only extraction, timing and required-field rules, E1/E2 comparisons, and before/after persistence checks. The Hermes Verifier (`gpt-5.6-sol`) reviews only semantic consistency and cannot remove deterministic findings.

Supported E2 time points are Start of Care, Resumption of Care, Recertification, Other Follow-Up, Transfer (with or without agency discharge), Death at Home, and Discharge, including the nursing/PT and visit/non-visit labels exposed by Devero. Skilled Nurse Visit Note is also supported. The deterministic scanner validates the official CMS E2 v3.02.0 item set and coded response domains when those controls are exposed by Devero, plus Devero's rendered required/skip/validity state. It is not an iQIES submission validator and does not replace clinician review. A completed E1 document may be used as historical copy evidence, but the verifier never gives E1 a compliance verdict or treats E1 content as proof of current E2 status.

After secure intake, run the scanner directly when needed:

```bash
.venv/bin/python -m devero_fleet verify-scan DV-XXXXXXXXXXXX --phase initial
```

`verify-scan` is the deterministic stage and explicitly reports that semantic review is still required; it is not the final hybrid verdict by itself. To inspect its PHI-minimized status and correction codes without dumping the full evidence bundle, run:

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

Running `verification-context` without the `jq` filter prints the complete PHI-bearing evidence bundle. That raw form is for the local Hermes Verifier only and must not be copied into chat, Kanban, logs, or committed files. Verification never edits, Saves, signs, submits, approves, or finalizes a Devero record.

See the [Devero Fleet command-line reference](devero_fleet/CLI.md) for every subcommand, option, lifecycle step, and PHI boundary.
