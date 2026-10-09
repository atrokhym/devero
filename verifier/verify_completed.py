#!/usr/bin/env python3
"""Read-only payer-audit readiness review for completed DeVero documents.

This is intentionally separate from ``verify_oasis.py``.  The existing verifier
selects a Pending target and produces writer-ready corrections.  This command
selects only a Completed/Sent to Office target, never saves or signs it, and
frames findings as retrospective audit risk requiring the agency's authorized
post-signature correction process.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Any, Dict, Iterable, Optional

from playwright.sync_api import sync_playwright

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_ROOT)

from devero_copier_oasis import (  # noqa: E402
    DEFAULT_CDP_URL,
    DEVERO_URL,
    PROFILE_DIR,
    extract_base_url,
    load_tasks_config,
    open_note_by_ui_click,
)
from verifier.audit_logging import save_audit_run  # noqa: E402
from verifier.doc_classifier import classify_document  # noqa: E402
from verifier.oasis_rules import audit_universal_document  # noqa: E402
from verifier.verify_oasis import (  # noqa: E402
    discover_source_note_date,
    extract_all_pages_deep,
    robust_login,
)


COMPLETED_STATUSES = ("completed", "sent to office")


def build_insurance_audit_readiness(findings: Dict[str, Any]) -> Dict[str, Any]:
    """Convert verifier findings into a conservative payer-audit readiness rating."""
    extraction_errors = findings.get("verification_error_items", [])
    deterministic_risks = findings.get("do_now_items", [])
    clinical_reviews = findings.get("clinical_decision_items", [])

    if extraction_errors:
        rating = "INCOMPLETE - NOT READY TO RATE"
        rationale = "The completed record was not captured completely enough for a reliable audit screen."
    elif deterministic_risks:
        rating = "NOT AUDIT READY"
        rationale = "One or more deterministic documentation or structural risks were detected."
    elif clinical_reviews:
        rating = "CLINICAL REVIEW REQUIRED"
        rationale = "No deterministic failure was found, but clinician review is required before treating the record as audit-ready."
    else:
        rating = "AUDIT READY - AUTOMATED CHECKS"
        rationale = "The record passed the implemented automated checks; this does not guarantee payer approval."

    return {
        "rating": rating,
        "rationale": rationale,
        "completed_record": True,
        "read_only_review": True,
        "counts": {
            "extraction_errors": len(extraction_errors),
            "deterministic_risks": len(deterministic_risks),
            "clinical_review_items": len(clinical_reviews),
            "verified_checks": len(findings.get("leave_items", [])),
        },
        "remediation_boundary": (
            "Do not silently edit or overwrite authenticated content. Route any correction, "
            "addendum, late entry, or amended record through the agency's authorized policy "
            "with attribution, date/time, reason, and preserved original content."
        ),
    }


def print_completed_audit_report(findings: Dict[str, Any], meta: Dict[str, Any]) -> None:
    doc_info = findings.get("doc_classification", {})
    readiness = findings.get("insurance_audit_readiness", {})
    title = doc_info.get("display_name") or meta.get("doc_type") or "Clinical Document"

    print("\n" + "=" * 76)
    print(f"COMPLETED DOCUMENT INSURANCE-AUDIT READINESS: {title.upper()}")
    print(f"Patient: {meta.get('patient')}")
    print(f"Caregiver: {meta.get('caregiver') or 'Not specified'}")
    print(f"Completed document date: {meta.get('target_date')}")
    print(f"Comparison baseline: {meta.get('source_date') or 'None (standalone review)'}")
    print(f"READINESS RATING: {readiness.get('rating', 'UNKNOWN')}")
    print(f"Basis: {readiness.get('rationale', '')}")
    print("=" * 76)

    sections = (
        ("0. VERIFICATION COMPLETENESS", "verification_error_items", "No extraction or page-completeness errors."),
        ("1. DETERMINISTIC AUDIT RISKS", "do_now_items", "No deterministic documentation risks detected."),
        ("2. CLINICAL / COMPLIANCE REVIEW", "clinical_decision_items", "No additional clinician-review signals detected."),
        ("3. VERIFIED CHECKS", "leave_items", "No affirmative checks were recorded."),
    )
    for heading, key, empty_message in sections:
        items = findings.get(key, [])
        print(f"\n### {heading} ({len(items)})")
        if not items:
            print(f"  - {empty_message}")
            continue
        for index, item in enumerate(items, 1):
            print(f"  {index}. [{item.get('section')}] Field: {item.get('field')}")
            evidence = item.get("current") or item.get("val") or item.get("note")
            if evidence:
                print(f"     Evidence: {evidence}")
            action = item.get("fix")
            if action:
                print(f"     Review:   {action}")

    print("\nPOST-SIGNATURE SAFETY BOUNDARY")
    print(f"  {readiness.get('remediation_boundary', '')}")
    print("  This is a screening report, not a coverage determination or guarantee of payment.")
    print("=" * 76 + "\n")


def run_completed_verification_for_task(context, page, base_url: str, task: Dict[str, Any]) -> Dict[str, Any]:
    task = dict(task)
    patient = task.get("patient", "")
    caregiver = task.get("caregiver", "")
    doc_type = task.get("doc_type", "OASIS-E2 Nurse Recert")
    source_doc_type = task.get("source_doc_type", doc_type)
    target_date = task.get("target_date", "")
    source_date = task.get("source_date", "")

    task.update({
        "review_mode": "completed_insurance_audit",
        "document_status": "Completed",
        "record_access": "read_only",
    })
    doc_info = classify_document(doc_type)

    if not source_date:
        source_date = discover_source_note_date(page, patient, doc_type, target_date, base_url)
        task["source_date"] = source_date

    print("\n" + "=" * 64)
    print(f"Starting completed-document audit-readiness review for '{patient}'")
    print(f"Document: {doc_info['display_name']} ({target_date})")
    print("Target status: Completed/Sent to Office (strict; Pending rows excluded)")
    print("=" * 64)

    print(f"\n[Step 1] Opening completed target document ({target_date}) read-only...")
    target_page = open_note_by_ui_click(
        context=context,
        active_page=page,
        base_url=base_url,
        patient_name=patient,
        doc_type=doc_type,
        note_date=target_date,
        caregiver=caregiver,
        is_target=True,
        required_status=COMPLETED_STATUSES,
    )
    if not target_page:
        return {"error": f"Completed target document not found for {target_date}; Pending rows were not accepted."}

    try:
        target_pages = extract_all_pages_deep(target_page, label=f"Completed target ({target_date})")
    finally:
        if target_page != page and not target_page.is_closed():
            target_page.close()

    source_pages = []
    if source_date:
        print(f"\n[Step 2] Opening prior completed comparison document ({source_date})...")
        source_page = open_note_by_ui_click(
            context=context,
            active_page=page,
            base_url=base_url,
            patient_name=patient,
            doc_type=doc_type,
            source_doc_type=source_doc_type,
            note_date=source_date,
            is_target=False,
            required_status=COMPLETED_STATUSES,
        )
        if source_page:
            try:
                source_pages = extract_all_pages_deep(source_page, label=f"Completed baseline ({source_date})")
            finally:
                if source_page != page and not source_page.is_closed():
                    source_page.close()
        else:
            print("[WARN] Prior completed comparison document could not be opened; continuing standalone.")
    else:
        print("\n[Step 2] No earlier completed comparison document found; continuing standalone.")

    print("\n[Step 3] Running read-only clinical, structural, and longitudinal checks...")
    findings = audit_universal_document(target_pages, source_pages, task)
    findings["insurance_audit_readiness"] = build_insurance_audit_readiness(findings)

    payload = {
        "task": task,
        "audit_results": findings,
        "target_pages": target_pages,
        "source_pages": source_pages,
    }
    run_path, latest_path = save_audit_run(
        os.path.join(WORKSPACE_ROOT, "logs"),
        patient,
        target_date,
        payload,
        filename_prefix="insurance_audit",
    )
    findings["audit_run_path"] = run_path
    findings["audit_latest_path"] = latest_path
    print(f"[OK] Immutable insurance-audit screen saved to {run_path}")
    print(f"[OK] Latest completed-document snapshot saved to {latest_path}")
    print_completed_audit_report(findings, task)
    return findings


def _tasks_from_args(args: argparse.Namespace) -> list[Dict[str, Any]]:
    if args.patient and args.target_date:
        return [{
            "patient": args.patient,
            "caregiver": args.caregiver or "",
            "doc_type": args.doc_type,
            "source_doc_type": args.source_doc_type or args.doc_type,
            "target_date": args.target_date,
            "source_date": args.source_date or "",
        }]

    config_path = args.config_file or os.path.join(WORKSPACE_ROOT, "tasks.jsonl")
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Task configuration file not found: {config_path}")
    return load_tasks_config(config_path)


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Read-only insurance-audit readiness review of completed DeVero documents."
    )
    parser.add_argument("-t", "--tasks", "--config", dest="config_file")
    parser.add_argument("--patient")
    parser.add_argument("--caregiver")
    parser.add_argument("--doc-type", default="OASIS-E2 Nurse Recert")
    parser.add_argument("--source-doc-type")
    parser.add_argument("--target-date", help="Completed document date (MM/DD/YYYY)")
    parser.add_argument("--source-date", help="Optional prior completed comparison date")
    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        tasks = _tasks_from_args(args)
    except (FileNotFoundError, ValueError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    if not tasks:
        print("[NOTICE] No active tasks found to review.")
        return 0

    print(f"Loaded {len(tasks)} completed-document audit task(s).")
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.connect_over_cdp(DEFAULT_CDP_URL)
            context = browser.contexts[0]
            print("Connected via CDP on", DEFAULT_CDP_URL)
        except Exception:
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=PROFILE_DIR,
                headless=False,
                args=["--no-first-run", "--no-default-browser-check"],
            )

        page = context.pages[0] if context.pages else context.new_page()
        base_url = extract_base_url(DEVERO_URL)
        robust_login(page, base_url)

        failures = 0
        for index, task in enumerate(tasks, 1):
            print(f"\nTASK {index}/{len(tasks)}: {task.get('patient')} | {task.get('doc_type')} | {task.get('target_date')}")
            try:
                result = run_completed_verification_for_task(context, page, base_url, task)
                if result.get("error"):
                    failures += 1
                    print(f"[FAILED] {result['error']}")
            except Exception as exc:
                failures += 1
                print(f"[ERROR] Completed-document review failed: {exc}")

            for extra_page in list(context.pages):
                if extra_page != page and not extra_page.is_closed():
                    extra_page.close()
            if index < len(tasks):
                time.sleep(2)

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
