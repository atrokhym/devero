---
name: devero-fleet
description: Coordinate secure Devero cases across Hermes Chief, Writer, Verifier, and Copier profiles. Use for every Devero browser task, opaque case handoff, Save authorization, or shared-Chrome ownership operation.
---

# Devero Fleet

Use `/app/workspaces/devero` as the working directory. Read
`references/case-protocol.md` before acting on a case.

Non-negotiable invariants:

- Put only the opaque `DV-...` case id and PHI-free status in Bot messages and Kanban.
- When Devero authentication is required, a browser-owning specialist may launch its own Chromium/Playwright session and log in with `DEVERO_URL`, `DEVERO_USERNAME`, and `DEVERO_PASSWORD` loaded locally from `/app/workspaces/devero/.env`, exactly like the repository copiers. Never print, quote, log, message, or copy credential values. If credentials are missing, rejected, or MFA/permission approval is required, stop and report an authentication blocker.
- Treat completed or signed source documents as read-only.
- Edit only the resolved pending target. Never sign, submit, approve, or finalize.
- Do not infer missing clinical facts. Report them as blocked or skipped.
- One role owns the shared Chrome session at a time. Release it in a `finally`-style cleanup even on failure.
- A Save needs exact live identity confirmation followed by Chief's one-use authorization.
- Do not create scheduled intake jobs; intake scheduling is intentionally deferred.

Return a bounded handoff: `CASE`, `STATUS`, `DONE`, `SKIPPED`, `BLOCKED`, and `NEXT`.
