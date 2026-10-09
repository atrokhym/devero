# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

### Execution & Development
- **Generate Tasks from Pending Dashboard**: `uv run python devero_tasks.py` (Harvests pending Skilled Nurse Visit Notes from Devero Home/Agent Menu and discovers source dates to generate `tasks_note.json`)
- **Run Note Copier**: `uv run python devero_copier_note.py tasks_note.json`
- **Run Fully Automated Copier**: `uv run python devero_copier.py` (Optionally pass custom JSON config: `uv run python devero_copier.py custom_tasks.jsonl`)
- **Run OASIS / Resumption of Care Copier**: `uv run python devero_copier_oasis.py`
- **Run Field Matching Debugger**: `uv run python devero_field_debugger.py`
- **Run Live Inspector & Router Debugger**: `uv run python devero_inspector.py`

### Environment Configuration
- Create a `.env` file (see `.env.example`) to configure credentials and settings:
  - `DEVERO_URL`: Devero instance URL (e.g. `https://example.devero.com`)
  - `DEVERO_USERNAME` & `DEVERO_PASSWORD`: Account credentials for automated login
  - `DEVERO_HEADLESS`: Set `true` to run browser headlessly or `false` (default) to watch execution
  - `DEVERO_CDP_URL`: Optional CDP endpoint (`http://127.0.0.1:9222`) to attach to an already running Chrome session

### Package Management
- Dependencies are managed with `uv` (`pyproject.toml` / `uv.lock`). Run `uv sync` to sync environment.

## Architecture & System Design

This repository automates copying dynamic clinical form notes (e.g., Skilled Nurse Visit Notes) in Devero from completed source notes to pending target notes without overwriting pre-existing target data.

### Core Architecture

1. **Automated Playwright Workflow (`devero_copier.py`)**
   - **Authentication**: Checks if current page is on `/login` and auto-fills `DEVERO_USERNAME` and `DEVERO_PASSWORD` from `.env`. Falls back to manual browser login if credentials are omitted.
   - **Patient Selection & Chart Navigation**: Automatically selects the patient in Devero (`text=(Select a Patient)` dropdown) and navigates to the Patient Chart (`/app#/patientCharts`).
   - **Form Navigation & Session Initialization**: Locates the note table row (`tr[data-activity-id="<id>"]`) on the Patient Chart and clicks it natively to open the form in a new tab, bypassing Devero's `"form cannot be accessed directly"` security guard.
   - **Multi-Page Support (`extract_all_pages_source` & `inject_all_pages_target`)**: Iterates through all form pages (`Page 1`, `Page 2`, `Next >`), extracting and populating form fields across multi-page forms, and returning to Page 1 on completion for user review.
   - **Field Extraction (`JS_EXTRACT_FIELDS`)**: Evaluates JavaScript in Playwright to harvest field values across the DOM and nested `iframe`s, indexing elements by ID, name, `ng-model`, `data-field-id`, `aria-label`, cleaned keys, and positional fallbacks.
   - **Field Injection (`JS_INJECT_FIELDS`)**: Populates target fields while strictly preserving existing target data (`hasValue` check). Dispatches native DOM events (`input`, `change`, `blur`), triggers Angular element handlers, and calls `$rootScope.$apply()` to sync scope.
   - **Browser Lifetime**: Retains the Chrome browser process open until the user presses Enter in the terminal, allowing manual review and saving before browser closure.

2. **Debugging Tools**
   - `devero_field_debugger.py`: Connects via CDP to inspect raw field metadata across active browser tabs.
   - `devero_inspector.py`: Inspects active tab AngularJS state (`$state`), checks for error banners, and tests navigating directly to target notes using Angular Router (`$state.go('dynamicForm', {activityId})`).

3. **Client-Side Userscript (`devero_userscript.js`)**
   - Standalone Tampermonkey/Greasemonkey script (`@match https://*.devero.com/*`).
   - Injects a floating UI bar (`#devero-copier-bar`) directly into Devero.
   - Uses `localStorage` to copy/paste form fields across tabs manually without running Python scripts.

4. **Hermes Devero Agent (`hermes/`)**
   - Single Telegram/Desktop agent profile (`devero`) that runs repository Python scripts on user request.
   - Uses `devero-runner` skill for command routing. Standard workflow: tasks → copy → verify → write.
   - Confirms with user before any command that writes to Devero. Read-only commands run immediately.
   - Never signs, submits, or finalizes. All notes remain Pending.
   - Always follows `writer/README.md` and `verifier/README.md` as authoritative documentation.
   - Old multi-agent fleet (Chief/Writer/Verifier/Copier) and `devero_fleet/` module archived to `archive/fleet-20260906/`.
