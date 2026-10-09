"""Print the human punch-list from a verifier audit JSON log.

Usage (from the devero project root):

    uv run python verifier/print_audit_log.py
    uv run python verifier/print_audit_log.py logs/audit_FixtureOne__Patient_08272026_20260902T221224155268Z.json
    uv run python verifier/print_audit_log.py logs/audit_FixtureOne__Patient_08272026.json -o report.txt
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Optional


def print_audit_report(findings: Dict[str, Any], meta: Dict[str, Any]) -> None:
    doc_info = findings.get("doc_classification", {})
    display_title = doc_info.get("display_name") or meta.get("doc_type") or "Clinical Document"

    print("\n" + "=" * 70)
    print(f"CLINICAL DOCUMENT VERIFICATION REPORT: {display_title.upper()}")
    print(f"Patient: {meta.get('patient')}")
    print(f"Caregiver: {meta.get('caregiver')}")
    print(f"Document Type: {meta.get('doc_type')} ({doc_info.get('description', '')})")
    print(
        f"Target Date: {meta.get('target_date')} | "
        f"Baseline Date: {meta.get('source_date') or 'None (Standalone Audit)'}"
    )
    print("=" * 70)

    status = findings.get("pass_fail_status", "UNKNOWN")
    print(f"\nAUDIT STATUS: {status}\n")

    verification_errors = findings.get("verification_error_items", [])
    print(f"### 0. VERIFICATION COMPLETENESS ({len(verification_errors)} error(s)):")
    if not verification_errors:
        print("  - Complete. All required audit data was captured.")
    else:
        for i, item in enumerate(verification_errors, 1):
            print(f"  {i}. [{item.get('section')}] Field: {item.get('field')}")
            print(f"     Issue:   {item.get('current')}")
            print(f"     Action:  {item.get('fix')}")

    do_now = findings.get("do_now_items", [])
    print(f"### 1. DO NOW ({len(do_now)} issue(s) to fix):")
    if not do_now:
        print("  - None! No automated errors detected.")
    else:
        for i, item in enumerate(do_now, 1):
            fix_type_str = f" [{item.get('fix_type').upper()}]" if item.get("fix_type") else ""
            print(f"  {i}. [{item.get('section')}] Field: {item.get('field')}{fix_type_str}")
            if item.get("current"):
                print(f"     Current: {item.get('current')}")
            print(f"     Action:  {item.get('fix')}")

    decisions = findings.get("clinical_decision_items", [])
    print(f"\n### 2. CLINICAL JUDGMENT / USER REVIEW ({len(decisions)} item(s)):")
    if not decisions:
        print("  - None.")
    else:
        for i, item in enumerate(decisions, 1):
            fix_type_str = f" [{item.get('fix_type').upper()}]" if item.get("fix_type") else ""
            print(f"  {i}. [{item.get('section')}] Field: {item.get('field')}{fix_type_str}")
            print(f"     Issue:   {item.get('current')}")
            print(f"     Action:  {item.get('fix')}")

    leaves = findings.get("leave_items", [])
    print(f"\n### 3. LEAVE ({len(leaves)} section(s) verified correct):")
    for item in leaves:
        val_str = f" ({item.get('val')})" if item.get("val") else ""
        print(f"  - [{item.get('section')}] {item.get('field')}{val_str}: {item.get('note', 'PASS')}")

    print("\n" + "=" * 70)
    print("Verification completed (Read-only; target remains Pending and unsigned).")
    print("=" * 70 + "\n")


def load_audit(path: Path) -> tuple[Dict[str, Any], Dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} is not a JSON object")
    findings = payload.get("audit_results")
    meta = payload.get("task")
    if not isinstance(findings, dict):
        raise ValueError(f"{path} is missing audit_results")
    if not isinstance(meta, dict):
        meta = {}
    return findings, meta


def latest_audit_json(logs_dir: Path) -> Optional[Path]:
    snapshots = sorted(
        [
            path
            for pattern in ("audit_*.json", "insurance_audit_*.json")
            for path in logs_dir.glob(pattern)
        ],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    # Prefer timestamped run files; skip none if only the latest snapshot exists.
    for path in snapshots:
        if path.is_file():
            return path
    return None


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Print the verifier punch-list text from an audit JSON log."
    )
    parser.add_argument(
        "json_path",
        nargs="?",
        help="Path to audit_*.json (default: newest file in logs/)",
    )
    parser.add_argument(
        "-o",
        "--output",
        help="Write the report to this text file instead of only stdout",
    )
    args = parser.parse_args(argv)

    root = project_root()
    if args.json_path:
        path = Path(args.json_path)
        if not path.is_absolute():
            path = (Path.cwd() / path).resolve()
    else:
        found = latest_audit_json(root / "logs")
        if found is None:
            print("No audit_*.json files found in logs/", file=sys.stderr)
            return 1
        path = found
        print(f"(using {path})", file=sys.stderr)

    findings, meta = load_audit(path)
    report_printer = print_audit_report
    if meta.get("review_mode") == "completed_insurance_audit":
        from verifier.verify_completed import print_completed_audit_report

        report_printer = print_completed_audit_report

    if args.output:
        out_path = Path(args.output)
        if not out_path.is_absolute():
            out_path = (Path.cwd() / out_path).resolve()
        from contextlib import redirect_stdout
        from io import StringIO

        sink = StringIO()
        with redirect_stdout(sink):
            report_printer(findings, meta)
        text = sink.getvalue()
        out_path.write_text(text, encoding="utf-8")
        print(text, end="")
        print(f"(wrote {out_path})", file=sys.stderr)
    else:
        report_printer(findings, meta)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
