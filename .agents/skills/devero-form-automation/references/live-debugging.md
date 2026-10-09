# Live Devero debugging

Read this reference only for a user-authorized live inspection. Keep the run
read-only unless the user explicitly asks to mutate or save the record.

## No-save harness

Use a small temporary Playwright script under `/tmp` that imports the copier's
existing login, chart-navigation, and pagination helpers. It should:

1. authenticate using existing environment handling without printing secrets;
2. open only the exact in-scope activity through Patient Chart UI;
3. navigate to the relevant form page;
4. reproduce the linked-action interaction;
5. capture diagnostic state before selection, before Insert, after the popup,
   and after confirmation;
6. close the browser context without Save or Sign.

Do not add patient-specific data or credentials to repository files or tests.

## State to capture

Capture structure and counts, not clinical content:

- current page title and URL shape;
- all context pages and frame URLs;
- presence of the exact integrated-action ID;
- visible modal/overlay IDs and classes;
- visible checkbox count and checked count;
- relevant button labels plus `ng-click`, `ng-mousedown`, and
  `data-automation-id`;
- warning overlay class and a redacted/classified message;
- whether the module closed;
- whether committed target displays outside dialogs became nonempty.

Store screenshots in `/tmp` only when visual layout matters. Review them before
claiming a control was clicked or a section populated.

## Page and frame discovery

Do not assume the object used to open the action remains the active surface.
After a click, enumerate `context.pages`, then their frames, and locate the exact
expected control. Bring the correct page to front before trusted pointer clicks.

An integrated action can load slowly. Click it once, then poll for the module's
known controls. Avoid a second click while the first open is still resolving.

When a completed activity is hidden in a collapsed chart episode, reveal the
correct episode or use the copier's exact chart-row logic. A direct
`#/dynamicForm?activityId=...` navigation may redirect to Agent Menu because it
does not establish Devero's expected session state.

## Windows Chrome from WSL

When Chrome runs on Windows and the automation runs in WSL, first determine
which debugging protocol the Windows endpoint exposes. A listening
`127.0.0.1:9222` port is not by itself proof that Playwright can use
`connect_over_cdp`.

- Probe `/json/version` and `/json/list` through the Windows network path.
- If those endpoints return valid target metadata, connect through the provided
  CDP URL.
- If they return `404`, the Chrome setting may be exposing Chrome DevTools MCP
  rather than classic CDP. Use an already available trusted DevTools MCP
  connector when authorized; do not repeatedly retry Playwright CDP.
- If no compatible connector is available, use an authorized isolated
  persistent browser profile and normal Patient Chart navigation for the
  no-save diagnostic.

Do not install or reconfigure a debugging connector merely because the port is
present; that is a separate permission and environment change.

## Diagnose by postcondition

Use these interpretations:

- Checked DOM boxes + “Please select one or more rows” means the controller did
  not accept selection.
- `OK` disappeared but the module stayed open and target remained empty means
  only the warning was dismissed.
- A selection count followed by empty target content is a false-positive
  verifier.
- A timeout after Save does not prove linked content was committed; verification
  must happen before Save.

After finding the cause, encode it in a focused browser regression fixture and
run the local syntax/tests before another live attempt.
