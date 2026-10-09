---
name: devero-form-automation
description: Diagnose and modify Devero Playwright copiers for dynamic multi-page notes, linked/composite forms, and the integrated Plan of Care module. Use for Devero field transfer failures, changing E1/E2 forms, accordion template selection, popup handling, or live no-save browser debugging.
---

# Devero Form Automation

Work from observable Devero state. The application combines AngularJS, custom
`cmwf-*` elements, Bootstrap modals, SweetAlert overlays, multiple pages, and
occasionally nested frames. A DOM property that looks correct is not proof that
Devero's controller accepted it.

## Start here

1. Read the repository's `AGENTS.md` and the copier being changed.
2. Inspect relevant logs and existing tests before editing.
3. Preserve populated target fields and linked sections. Do not reopen a
   template chooser merely because source data exists.
4. Use `apply_patch` for edits. Keep unrelated user changes intact.
5. For a substantial linked-form issue, read
   [references/linked-forms.md](references/linked-forms.md).
6. For authorized live diagnosis, read
   [references/live-debugging.md](references/live-debugging.md).

## Core invariants

- Navigate notes through the Patient Chart when session initialization is
  required; direct dynamic-form URLs can be rejected or redirected.
- Iterate every note page and relevant frame. Wait for Angular page replacement
  after navigation.
- Never overwrite a target value or linked list that is already populated.
- Discover linked sections from the target's rendered
  `cmwf-integrated-action` elements; do not maintain a fixed list of clinical
  section names for templates that Devero versions independently.
- Treat source form IDs and target form IDs as versioned schema, not stable
  labels. Explicitly map known migrations.
- Treat `cmwf-text-display`, `cmwf-cop`, and their descendants as read-only
  linked projections during generic field injection. Populating their DOM text
  can make an empty target look committed even though Devero's controller has
  no selection to save.
- Use trusted Playwright pointer interaction for controls whose Angular handlers
  depend on real mouse events. Do not count `element.checked = true`, synthetic
  events, or selected-looking rows as successful controller state.
- Do not report a linked section as inserted until the module/list closes as
  expected and the target note itself shows committed content.
- Classify popup content before dismissing it. A validation error and a
  continuation confirmation can both have an `OK` button but require different
  recovery.

## Safety and privacy

- Never put credentials, patient names, chart numbers, activity IDs, clinical
  text, or screenshots containing PHI into this skill or committed fixtures.
- A request to diagnose or inspect does not authorize Save, Sign, submission,
  or other clinical-record mutation. Use a no-save live debugger unless the user
  explicitly requests the mutation.
- Store temporary screenshots and diagnostic files under `/tmp`; summarize
  counts and control state rather than clinical text.
- Stop before Save when the required target postcondition is not proven. A false
  success followed by Save is worse than a clear failed transfer.

## Verification

Use checks proportional to the change. For the resumption-of-care copier, the
usual local gates are:

```bash
.venv/bin/python -m py_compile devero_copier_resumption-of-care.py
DEVERO_LOG_FILE=/tmp/devero-roc-test.log .venv/bin/python tests/check_js_syntax.py
.venv/bin/pytest -q tests/test_interventions_goals_comprehensive.py
```

Run a live check only with the user's authorization and against the exact
in-scope note. A valid live result proves the target section is nonempty after
the final confirmation and before Save; selection counts and log messages alone
are insufficient.
