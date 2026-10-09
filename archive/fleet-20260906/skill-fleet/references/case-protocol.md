# Devero case protocol

All commands run from `/app/workspaces/devero`. Case envelopes default to an owner-only runtime directory and expire after four hours.

PHI-free operational Python commands, such as task harvesting, may be delegated directly to Copier without creating a clinical envelope. The exact command and Kanban task id form the operational handoff. If a command can edit or Save a clinical record, the clinical case lifecycle and Save gate below still apply.

The private owner Telegram DM (`OWNER_TELEGRAM_ID`) is an approved secure-intake source for commands and read-only verification requests. Chief may create the four-hour owner-private case envelope directly from case details already supplied there, but must immediately reduce every specialist/Kanban handoff to the opaque case id. This trusted-source rule removes redundant intake confirmation; it does not authorize a clinical write, Save, signing, submission, approval, or finalization.

## Lifecycle

1. Chief creates the case: `.venv/bin/python -m devero_fleet intake`.
2. Chief delegates only the returned opaque case id.
3. The assigned specialist loads the minimum local case data with `.venv/bin/python -m devero_fleet work-context CASE --role ROLE`. This output is PHI-bearing local tool context: never quote, summarize with identifiers, place in memory, or copy into Bot/Kanban messages.
4. Specialist claims Chrome: `.venv/bin/python -m devero_fleet browser claim CASE ROLE`.
5. Specialist checks the live record. Only if patient, document, date, and Pending status all match, run `confirm-identity` with all four required flags.
6. Chief asks the user for per-case Save confirmation, then runs `authorize-save`.
7. Writer consumes the grant immediately before Save. Copier runner consumes it automatically.
8. Specialist releases Chrome. Chief runs `close CASE` or `cancel CASE` when finished.

## Authentication

Specialists do not depend on an authenticated shared Chrome profile. Like the repository copiers, a browser-owning specialist may launch its own Chromium/Playwright session and log in with `DEVERO_URL`, `DEVERO_USERNAME`, and `DEVERO_PASSWORD` loaded locally from `/app/workspaces/devero/.env`. Load them locally without displaying them and do not include values in commands, stdout, screenshots, Bot/Kanban messages, memory, or reports. Keep the fleet browser-claim serialization even when using an own session. This standing authorization covers ordinary Devero login only; it does not cover MFA, permission prompts, password changes, signing, submission, approval, finalization, or Save. Stop if the credentials are absent or rejected, or if any additional authentication decision is required.

Use `show CASE` for an opaque PHI-free summary and `browser status` to see ownership. Never put the secure envelope contents in a Bot message, Kanban title/body/comment/result, logs outside its runtime directory, or durable documentation.

Bot Mode is the coordination plane. `message_agent` is preferred in canonical Bot Chats. The dedicated `devero` Kanban board is the durable fallback for Telegram and other sessions where direct Bot messaging is unavailable. No custom Devero MCP server is part of this design; browser access uses the already-configured Chrome DevTools server.

Conflicts and clinical-policy concerns go to Lora in Telegram group `REVIEW_TELEGRAM_GROUP_ID` as PHI-free escalations containing only the opaque case id, correction ids, issue class, and requested decision. Group discussion cannot authorize Save, signing, submission, approval, finalization, or expansion beyond user-approved corrections.

Clinical content necessarily enters the active local specialist model context while it reads Devero. Keep those profiles local and access-controlled, minimize repetition, disable external observability for them, and do not promote case details into memory.
