"""Local secure-intake and case-lifecycle CLI."""

from __future__ import annotations

import argparse
import getpass
import json
import re
import stat
import sys
from pathlib import Path
from typing import Any

from .case_store import CaseStore, CaseStoreError
from .browser_lease import BrowserClaimManager


def _prompt(label: str, *, required: bool = False, secret: bool = False) -> str:
    while True:
        value = (getpass.getpass(f"{label}: ") if secret else input(f"{label}: ")).strip()
        if value or not required:
            return value


def _intake_payload(path: str | None) -> dict[str, Any]:
    if path:
        source = Path(path)
        if source.is_symlink():
            raise ValueError("Intake file must not be a symlink")
        if not source.is_file():
            raise ValueError("Intake path must be a regular file")
        if stat.S_IMODE(source.stat().st_mode) & 0o077:
            raise ValueError("Intake file must be owner-private (chmod 600)")
        content = source.read_text(encoding="utf-8")

        # Accept the repository's commented JSON task-list format without
        # treating comment markers inside quoted values as comments.
        def preserve_strings(match: re.Match[str]) -> str:
            token = match.group(0)
            return token if token.startswith('"') else ""

        clean = re.sub(
            r'//.*?$|/\*.*?\*/|"(?:\\.|[^\\"])*"',
            preserve_strings,
            content,
            flags=re.DOTALL | re.MULTILINE,
        )
        clean = re.sub(r",\s*([\]}])", r"\1", clean)
        payload = json.loads(clean)
        if isinstance(payload, list):
            active = [
                item for item in payload
                if isinstance(item, dict)
                and item.get("enabled", True) is not False
                and not item.get("skip", False)
            ]
            if len(active) != 1:
                raise ValueError("Intake file must contain exactly one active task")
            task = active[0]
            return {
                "mode": task.get("mode", "verify"),
                "identity": {
                    key: task.get(key, "")
                    for key in ("patient", "caregiver", "doc_type", "source_doc_type", "source_date", "target_date")
                },
                "raw_status": str(task.get("raw_status", "")),
            }
        if isinstance(payload, dict) and "identity" not in payload and "patient" in payload:
            return {
                "mode": payload.get("mode", "verify"),
                "identity": {
                    key: payload.get(key, "")
                    for key in ("patient", "caregiver", "doc_type", "source_doc_type", "source_date", "target_date")
                },
                "raw_status": str(payload.get("raw_status", "")),
            }
        if not isinstance(payload, dict):
            raise ValueError("Intake file must contain a JSON object or one active task")
        return payload
    mode = _prompt("Mode (copy/write/verify/correct)", required=True).lower()
    identity = {
        "patient": _prompt("Patient (Last, First)", required=True, secret=True),
        "caregiver": _prompt("Caregiver", secret=True),
        "doc_type": _prompt("Target document type", required=True),
        "source_doc_type": _prompt("Source document type (optional)"),
        "source_date": _prompt("Source date MM/DD/YYYY (optional)"),
        "target_date": _prompt("Target date MM/DD/YYYY", required=True),
    }
    raw_status = _prompt("Current-status facts (optional; hidden)", secret=True)
    return {"mode": mode, "identity": identity, "raw_status": raw_status}


def _owner_private_json(path: str) -> dict[str, Any]:
    source = Path(path)
    if source.is_symlink() or not source.is_file():
        raise ValueError("JSON path must be a regular non-symlink file")
    if stat.S_IMODE(source.stat().st_mode) & 0o077:
        raise ValueError("JSON file must be owner-private (chmod 600)")
    return json.loads(source.read_text(encoding="utf-8"))


def _record_supported_corrections(artifact: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        item
        for item in (artifact.get("audit") or {}).get("corrections") or []
        if item.get("fact_status") == "SUPPORTED" and item.get("route") == "WRITER"
    ]


def _approve_record_supported(store: CaseStore, case_id: str, phase: str) -> str:
    artifact = store.read_artifact(case_id, f"verification-{phase}")
    corrections = _record_supported_corrections(artifact)
    if not corrections:
        return "No record-supported Writer corrections are available for approval."
    store.approve_corrections(case_id, [str(item["id"]) for item in corrections])
    unique_fields = len({str(item.get("location") or "") for item in corrections})
    return (
        f"Approved {len(corrections)} record-supported findings across {unique_fields} fields for Writer.\n"
        "Clinician-required findings were not approved.\n"
        "Devero was not edited or saved."
    )


def _verification_report(
    artifact: dict[str, Any], case_id: str, phase: str, *, record_supported_only: bool = False
) -> str:
    audit = artifact.get("audit") or {}
    all_corrections = audit.get("corrections") or []
    corrections = _record_supported_corrections(artifact) if record_supported_only else all_corrections
    grouped: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for item in corrections:
        key = tuple(
            str(item.get(field) or "")
            for field in ("issue_class", "location", "observed_issue", "expected_correction", "fact_status")
        )
        grouped.setdefault(key, []).append(item)
    counts = audit.get("counts") or {}
    verdict = str(audit.get("verdict") or "NOT AVAILABLE")
    lines = [
        "DEVERO VERIFICATION REPORT",
        f"Case: {case_id}",
        f"Phase: {phase.title()}",
        f"Status: {audit.get('run_status') or 'UNKNOWN'}",
        f"Result: {verdict}",
        f"Document: {str(audit.get('document_kind') or 'Unknown').replace('_', ' ')}",
        f"Pages reviewed: {counts.get('pages', 0)}",
        f"Findings flagged: {len(corrections)}",
        f"Unique items to review: {len(grouped)}",
        "",
    ]
    if record_supported_only:
        lines.insert(-1, "Report filter: Record-supported Writer corrections only")
    if audit.get("run_status") == "BLOCKED":
        lines.append(f"The scan did not finish: {str(audit.get('reason') or 'Unknown reason').replace('_', ' ')}")
        return "\n".join(lines)
    if not corrections:
        lines.append(
            "No record-supported Writer corrections were found."
            if record_supported_only
            else "No deterministic issues were found."
        )
        return "\n".join(lines)

    lines.append("Review each item below. Do not change an answer unless the current clinical record supports it.")
    for number, items in enumerate(grouped.values(), 1):
        item = items[0]
        confirmation = {
            "SUPPORTED": "Record-supported correction",
            "USER_OR_CLINICIAN_REQUIRED": "RN/clinician confirmation required",
            "NOT_EDITABLE": "Do not edit; escalate for clinical-policy review",
        }.get(str(item.get("fact_status") or ""), "Review required")
        lines.extend(
            [
                "",
                f"{number}. {str(item.get('issue_class') or 'Finding').replace('_', ' ').title()}",
                f"   Page/field: {item.get('location') or 'Unknown'}",
                f"   Issue: {item.get('observed_issue') or 'Review required.'}",
                f"   What to do: {item.get('expected_correction') or 'Review the current record.'}",
                f"   Decision needed: {confirmation}",
                f"   Finding ID{'s' if len(items) > 1 else ''}: "
                + ", ".join(str(value.get("id") or "Unknown") for value in items),
            ]
        )
    lines.extend(["", "This is a deterministic report. Complete semantic/clinical review before approving changes."])
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="devero-fleet", description="Secure local intake for the Hermes Devero fleet")
    parser.add_argument("--runtime-dir", help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)
    intake = sub.add_parser("intake", help="Create a TTL-limited case envelope")
    intake.add_argument("--from-file", metavar="JSON", help="Read intake from an owner-private JSON file")
    show = sub.add_parser("show", help="Show a PHI-minimized case summary")
    show.add_argument("case_id")
    context = sub.add_parser("work-context", help="Read minimum PHI-bearing context locally as a specialist")
    context.add_argument("case_id")
    context.add_argument("--role", required=True, choices=("writer", "verifier", "copier"))
    resolve = sub.add_parser("confirm-identity", help="Attest the exact live pending target identity")
    resolve.add_argument("case_id")
    resolve.add_argument("--role", required=True, choices=("writer", "verifier", "copier"))
    resolve.add_argument("--patient-match", action="store_true", required=True)
    resolve.add_argument("--document-match", action="store_true", required=True)
    resolve.add_argument("--date-match", action="store_true", required=True)
    resolve.add_argument("--pending-target", action="store_true", required=True)
    auth = sub.add_parser("authorize-save", help="Authorize Save-only after live identity resolution")
    auth.add_argument("case_id")
    consume = sub.add_parser("consume-save", help="Consume a one-use Save grant immediately before Save")
    consume.add_argument("case_id")
    consume.add_argument("--role", required=True, choices=("writer", "copier"))
    approve = sub.add_parser("approve-corrections", help="Approve enumerated correction ids")
    approve.add_argument("case_id")
    approve.add_argument("correction_ids", nargs="+")
    approve_supported = sub.add_parser(
        "approve-record-supported", help="Approve all record-supported Writer corrections"
    )
    approve_supported.add_argument("case_id")
    approve_supported.add_argument("--phase", choices=("initial", "persistence"), default="initial")
    scan = sub.add_parser("verify-scan", help="Run the read-only deterministic Devero verifier scan")
    scan.add_argument("case_id")
    scan.add_argument("--phase", choices=("initial", "persistence"), default="initial")
    evidence = sub.add_parser(
        "verification-context", help="Read PHI-bearing verifier evidence locally; never forward its output"
    )
    evidence.add_argument("case_id")
    evidence.add_argument("--role", required=True, choices=("verifier",))
    evidence.add_argument("--phase", choices=("initial", "persistence"), default="initial")
    report = sub.add_parser("verification-report", help="Print a plain-language RN verification report")
    report.add_argument("case_id")
    report.add_argument("--phase", choices=("initial", "persistence"), default="initial")
    report.add_argument(
        "--record-supported-only",
        action="store_true",
        help="Show only findings that already have record support and route to Writer",
    )
    record = sub.add_parser("record-verification", help="Store a validated verifier result")
    record.add_argument("case_id")
    record.add_argument("--from-file", required=True, metavar="JSON")
    record.add_argument("--phase", choices=("initial", "persistence"), default="initial")
    for name in ("cancel", "close"):
        cmd = sub.add_parser(name, help=f"{name.title()} and delete the temporary case envelope")
        cmd.add_argument("case_id")
    sub.add_parser("sweep", help="Delete expired case envelopes")
    browser = sub.add_parser("browser", help="Manage serialized Chrome ownership")
    browser_sub = browser.add_subparsers(dest="browser_command", required=True)
    for name in ("claim", "heartbeat", "release"):
        cmd = browser_sub.add_parser(name)
        cmd.add_argument("case_id")
        cmd.add_argument("role", choices=("chief", "writer", "verifier", "copier"))
    browser_sub.add_parser("status")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    store = CaseStore(root=args.runtime_dir)
    try:
        if args.command == "intake":
            payload = _intake_payload(args.from_file)
            case_id = store.create(
                identity=payload["identity"],
                mode=payload["mode"],
                raw_status=str(payload.get("raw_status", "")),
            )
            print(case_id)
        elif args.command == "show":
            print(json.dumps(store.summary(args.case_id), sort_keys=True))
        elif args.command == "work-context":
            print(json.dumps(store.work_context(args.case_id, args.role), sort_keys=True))
        elif args.command == "confirm-identity":
            record = store.load(args.case_id)
            facts = {
                "attested_by": args.role,
                "patient_match": args.patient_match,
                "document_match": args.document_match,
                "date_match": args.date_match,
                "pending_target": args.pending_target,
            }
            store.mark_resolved(
                args.case_id,
                live_fingerprint=record["identity_fingerprint"],
                facts=facts,
            )
            print(json.dumps({"case_id": args.case_id, "identity_confirmed": True}, sort_keys=True))
        elif args.command == "authorize-save":
            store.authorize_save(args.case_id)
            print(json.dumps({"case_id": args.case_id, "save_authorized": True}, sort_keys=True))
        elif args.command == "consume-save":
            record = store.load(args.case_id)
            store.consume_save_authorization(args.case_id, record["identity_fingerprint"])
            print(json.dumps({"case_id": args.case_id, "save_grant_consumed": True}, sort_keys=True))
        elif args.command == "approve-corrections":
            store.approve_corrections(args.case_id, args.correction_ids)
            print(json.dumps({"case_id": args.case_id, "approved": len(args.correction_ids)}, sort_keys=True))
        elif args.command == "approve-record-supported":
            print(_approve_record_supported(store, args.case_id, args.phase))
        elif args.command == "verify-scan":
            from .verifier_runner import scan_case

            print(json.dumps(scan_case(args.case_id, args.phase, store=store), sort_keys=True))
        elif args.command == "verification-context":
            print(json.dumps(store.read_artifact(args.case_id, f"verification-{args.phase}"), sort_keys=True))
        elif args.command == "verification-report":
            artifact = store.read_artifact(args.case_id, f"verification-{args.phase}")
            print(
                _verification_report(
                    artifact,
                    args.case_id,
                    args.phase,
                    record_supported_only=args.record_supported_only,
                )
            )
        elif args.command == "record-verification":
            result = _owner_private_json(args.from_file)
            evidence = store.read_artifact(args.case_id, f"verification-{args.phase}")
            deterministic = (evidence.get("audit") or {}).get("corrections") or []
            store.record_verification(
                args.case_id,
                result,
                required_corrections=deterministic,
                require_final=True,
            )
            print(
                json.dumps(
                    {
                        "case_id": args.case_id,
                        "recorded": True,
                        "verdict": result.get("verdict"),
                        "correction_count": len(result.get("corrections") or []),
                    },
                    sort_keys=True,
                )
            )
        elif args.command == "cancel":
            store.cancel(args.case_id)
            print(json.dumps({"case_id": args.case_id, "cancelled": True}, sort_keys=True))
        elif args.command == "close":
            store.close(args.case_id)
            print(json.dumps({"case_id": args.case_id, "closed": True}, sort_keys=True))
        elif args.command == "sweep":
            print(json.dumps({"expired_removed": store.sweep_expired()}, sort_keys=True))
        elif args.command == "browser":
            claims = BrowserClaimManager(store)
            if args.browser_command == "claim":
                result = claims.claim(args.case_id, args.role)
            elif args.browser_command == "heartbeat":
                result = claims.heartbeat(args.case_id, args.role)
            elif args.browser_command == "release":
                claims.release(args.case_id, args.role)
                result = {"case_id": args.case_id, "role": args.role, "released": True}
            else:
                result = claims.status()
            print(json.dumps(result, sort_keys=True))
        return 0
    except (CaseStoreError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
