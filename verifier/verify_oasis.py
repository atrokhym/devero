#!/usr/bin/env python3
"""
OASIS Document Verifier (verifier/verify_oasis.py)

Read-only verification tool for clinical OASIS documents in DeVero EHR.
Extracts target (pending) and source (completed baseline) forms, applies
CMS OASIS-E2 clinical and skip-logic rules, and outputs a Writer-ready punch list.

Usage:
  # Verify active task in tasks.jsonl:
  uv run python verifier/verify_oasis.py

  # Verify with custom task file:
  uv run python verifier/verify_oasis.py --config custom_tasks.json

  # Verify specific patient / document via CLI:
  uv run python verifier/verify_oasis.py \
    --patient "FixtureOne, Patient" \
    --caregiver "Example, Clinician (RN)" \
    --doc-type "OASIS-E2 Nurse Recert" \
    --source-doc-type "OASIS-E2 Nurse Recert" \
    --target-date "08/27/2026" \
    --source-date "06/30/2026"
"""

import argparse
import json
import os
import sys
import time
import re
from typing import Dict, List, Any
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

load_dotenv()

# Add workspace directory to path
WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_ROOT)

from devero_copier_oasis import (
    DEVERO_URL,
    DEVERO_USERNAME,
    DEVERO_PASSWORD,
    DEFAULT_CDP_URL,
    PROFILE_DIR,
    extract_base_url,
    select_patient_and_go_to_chart,
    open_note_by_ui_click,
    go_to_next_page,
    load_tasks_config
)
from verifier.doc_classifier import classify_document
from verifier.oasis_rules import audit_universal_document, audit_oasis_recert
from verifier.audit_logging import save_audit_run
from devero_form_opening import iter_chart_episodes

JS_PAGE_EXTRACT = """
() => {
    const data = {
        title: document.title,
        url: window.location.href,
        headings: [],
        fields: [],
        narratives: [],
        bodyTextSnippet: (document.body.innerText || '').slice(0, 5000),
        totalTextLength: (document.body.innerText || '').length
    };

    // Headings
    document.querySelectorAll('h1, h2, h3, h4, h5, .section-header, .module-title, legend, .panel-title, .form-title, .box-title, th, td.formSectionHeader').forEach(h => {
        const txt = (h.innerText || '').trim();
        if (txt && txt.length > 2 && txt.length < 150) data.headings.push(txt);
    });

    const selectors = 'input, textarea, select, cmwf-textbox, cmwf-textarea, cmwf-text-display, cmwf-date, cmwf-checkbox, cmwf-dropdown, cmwf-cop, cmwf-integrated-action, ntst-select, [data-field-id], [ng-model]';
    const elements = document.querySelectorAll(selectors);

    elements.forEach((el, idx) => {
        const tag = el.tagName.toLowerCase();
        const type = (el.type || '').toLowerCase();
        const name = el.name || el.id || el.getAttribute('ng-model') || el.getAttribute('data-field-id') || el.getAttribute('name') || '';

        let label = '';
        if (el.id) {
            const l = document.querySelector(`label[for="${el.id}"]`);
            if (l) label = (l.innerText || '').trim();
        }
        if (!label && el.closest('label')) label = (el.closest('label').innerText || '').trim();
        if (!label) {
            const parentTd = el.closest('td');
            if (parentTd && parentTd.previousElementSibling) {
                label = (parentTd.previousElementSibling.innerText || '').trim();
            }
        }
        if (!label && el.getAttribute('aria-label')) label = el.getAttribute('aria-label');
        if (!label && el.getAttribute('title')) label = el.getAttribute('title');
        if (!label && el.getAttribute('placeholder')) label = el.getAttribute('placeholder');

        let val = el.value !== undefined ? el.value : '';
        let isChecked = el.checked;

        if (tag === 'cmwf-checkbox') {
            const chk = el.querySelector('input[type="checkbox"]');
            isChecked = chk ? chk.checked : el.classList.contains('checked');
            val = isChecked ? 'checked' : 'unchecked';
        } else if (tag === 'cmwf-date') {
            // Completed/read-only DeVero forms render the clinical date in a
            // visible span while leaving the backing input hidden and empty.
            const display = el.querySelector('span:not(:has(button))');
            const backing = el.querySelector('input[type="date"], input');
            val = (display && (display.innerText || display.textContent || '').trim()) ||
                (backing && backing.value !== undefined ? backing.value : '') || '';
        } else if (tag === 'cmwf-dropdown' || tag === 'ntst-select') {
            const sel = el.querySelector('ntst-option[selected], ntst-option[aria-selected="true"], ntst-option.selected');
            if (sel) val = (sel.innerText || '').trim() || sel.getAttribute('value');
            if (!val) {
                const sp = el.querySelector('span.f-bold, span');
                if (sp) val = (sp.innerText || '').trim();
            }
        } else if (tag === 'cmwf-text-display' || tag === 'cmwf-cop' || tag === 'cmwf-integrated-action') {
            val = (el.innerText || '').trim();
        }

        const item = {
            idx: idx,
            tag: tag,
            type: type,
            name: name,
            id: el.id || '',
            label: label.slice(0, 150),
            val: typeof val === 'string' ? val.trim() : String(val),
            checked: isChecked,
            vis: el.offsetWidth > 0 || el.offsetHeight > 0
        };

        data.fields.push(item);

        if ((tag === 'textarea' || tag === 'cmwf-textarea') && val && String(val).trim().length > 5) {
            data.narratives.push({
                name: name,
                label: label,
                text: String(val).trim()
            });
        }
    });

    return data;
}
"""

def robust_login(page, base_url: str):
    page.goto(DEVERO_URL, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(3000)

    is_login = "login" in page.url.lower() or page.evaluate("""() => {
        return !!(document.querySelector('input[type="password"]') || document.querySelector('input[name="uName"]') || document.querySelector('input[name="username"]'));
    }""")

    if is_login:
        print(f"--> Logging into DeVero as '{DEVERO_USERNAME}'...")
        user_selectors = ['#username', 'input[name="uName"]', 'input[name="username"]', 'input[name="j_username"]', 'input[type="text"]']
        for sel in user_selectors:
            try:
                loc = page.locator(sel).first
                if loc.is_visible(timeout=2000):
                    loc.fill(DEVERO_USERNAME)
                    break
            except Exception: pass

        pass_selectors = ['#password', 'input[name="password"]', 'input[name="j_password"]', 'input[type="password"]']
        for sel in pass_selectors:
            try:
                loc = page.locator(sel).first
                if loc.is_visible(timeout=2000):
                    loc.fill(DEVERO_PASSWORD)
                    break
            except Exception: pass

        submit_selectors = ['button[type="submit"]', 'input[type="submit"]', '#loginBtn', '#login-button', 'button:has-text("Log In")', 'button:has-text("Login")']
        submitted = False
        for sel in submit_selectors:
            try:
                loc = page.locator(sel).first
                if loc.is_visible(timeout=2000):
                    loc.click()
                    submitted = True
                    break
            except Exception: pass

        if not submitted:
            page.keyboard.press("Enter")

        page.wait_for_timeout(4000)
        start = time.time()
        while time.time() - start < 20:
            if "login" not in page.url.lower():
                print("[OK] Login successful!")
                return True
            time.sleep(1)

    return True

def wait_for_page_extraction_ready(page, page_num: int, timeout_sec=15) -> Dict[str, Any]:
    """Wait for a stable active page, including any assessment summary narrative if present on this page."""
    deadline = time.time() + timeout_sec
    previous_signature = None
    stable_samples = 0
    last_state: Dict[str, Any] = {}

    while time.time() < deadline:
        last_state = page.evaluate("""(expectedPage) => {
            const visible = el => !!el && (
                el.offsetWidth > 0 || el.offsetHeight > 0 ||
                (el.getClientRects && el.getClientRects().length > 0)
            );
            const selectors = 'input, textarea, select, cmwf-textbox, cmwf-textarea, cmwf-text-display, cmwf-date, cmwf-checkbox, cmwf-dropdown, cmwf-cop, cmwf-integrated-action, ntst-select';
            const controls = Array.from(document.querySelectorAll(selectors));
            const visibleControls = controls.filter(visible);
            const pager = Array.from(document.querySelectorAll('a, button')).filter(el =>
                visible(el) && (el.innerText || el.value || '').trim() === String(expectedPage)
            );
            const classText = el => `${el.className || ''} ${(el.parentElement && el.parentElement.className) || ''}`.toLowerCase();
            const loading = pager.some(el => /(^|\\s)disable(\\s|$)/.test(classText(el)));
            const active = pager.some(el => /(^|\\s)(activenp|active|selected)(\\s|$)/.test(classText(el)));

            const summaryControls = Array.from(document.querySelectorAll(
                '[name="sum_AssessmentSum_Txt"], #sum_AssessmentSum_Txt'
            ));
            const summaryLengths = summaryControls.map(el => {
                let value = el.value;
                if (value === undefined) {
                    const backing = el.querySelector && el.querySelector('textarea, input');
                    value = backing && backing.value !== undefined ? backing.value : '';
                }
                return String(value || '').trim().length;
            });
            const summaryLength = summaryLengths.length ? Math.max(...summaryLengths) : 0;
            const valueLength = visibleControls.reduce((total, el) => {
                let value = el.value !== undefined ? el.value : '';
                if (el.tagName && el.tagName.toLowerCase() === 'cmwf-date') {
                    const display = el.querySelector('span.f-bold, span');
                    value = display ? (display.innerText || display.textContent || '') : value;
                }
                return total + String(value || '').length;
            }, 0);

            return {
                control_count: controls.length,
                visible_control_count: visibleControls.length,
                pager_found: pager.length > 0,
                pager_active: active,
                pager_loading: loading,
                summary_present: summaryControls.length > 0,
                summary_length: summaryLength,
                signature: `${controls.length}:${visibleControls.length}:${valueLength}:${summaryLength}`
            };
        }""", page_num)

        base_ready = (
            last_state.get("control_count", 0) > 25
            and last_state.get("visible_control_count", 0) > 25
            and last_state.get("pager_found", False)
            and last_state.get("pager_active", False)
            and not last_state.get("pager_loading", False)
        )
        # If an assessment summary field is present on this page, wait for it to load non-empty
        summary_ready = (
            not last_state.get("summary_present", False)
            or last_state.get("summary_length", 0) > 0
        )
        signature = last_state.get("signature")
        if base_ready and summary_ready and signature == previous_signature:
            stable_samples += 1
        elif base_ready and summary_ready:
            stable_samples = 1
        else:
            stable_samples = 0

        if stable_samples >= 3:
            return {
                "ready": True,
                "reason": "Page controls and values stabilized before extraction.",
                **last_state,
            }

        previous_signature = signature
        page.wait_for_timeout(500)

    if last_state.get("summary_present", False) and not last_state.get("summary_length", 0):
        reason = f"Page {page_num} assessment summary remained blank or uncaptured through the readiness timeout."
    else:
        reason = "Page controls did not reach a stable extraction state before timeout."
    return {"ready": False, "reason": reason, **last_state}

def extract_all_pages_deep(page, label="Document") -> List[Dict]:
    all_pages = []
    page_num = 1

    # Attempt to reset to page 1
    page.evaluate("""() => {
        const p1 = Array.from(document.querySelectorAll('a, button')).find(b => (b.innerText || '').trim() === '1' && (b.offsetWidth > 0 || b.offsetHeight > 0));
        if (p1) try { p1.click(); } catch(e){}
    }""")
    page.wait_for_timeout(2000)

    while True:
        extraction_state = wait_for_page_extraction_ready(page, page_num, timeout_sec=15)
        print(f"--> Extracting {label} Page {page_num}...")

        page_doc = {
            "page_num": page_num,
            "url": page.url,
            "extraction": extraction_state,
            "main": page.evaluate(JS_PAGE_EXTRACT)
        }

        main_fields_count = len(page_doc["main"].get("fields", []))
        narrative_count = len(page_doc["main"].get("narratives", []))
        print(f"    [Page {page_num}] {main_fields_count} fields, {narrative_count} narrative blocks.")
        if not extraction_state.get("ready"):
            print(f"    [WARN] {extraction_state.get('reason')}")
        all_pages.append(page_doc)

        clicked = go_to_next_page(page, page_num)
        if clicked:
            print(f"--> Moving to {label} Page {page_num + 1}...")
            page.wait_for_timeout(500)
            page_num += 1
            if page_num > 15:
                break
        else:
            print(f"[OK] Completed all pages for {label} (Total: {page_num} page(s)).")
            break

    return all_pages

def print_audit_report(findings: Dict, meta: Dict):
    doc_info = findings.get("doc_classification", {})
    display_title = doc_info.get("display_name") or meta.get("doc_type") or "Clinical Document"

    print("\n" + "=" * 70)
    print(f"CLINICAL DOCUMENT VERIFICATION REPORT: {display_title.upper()}")
    print(f"Patient: {meta.get('patient')}")
    print(f"Caregiver: {meta.get('caregiver')}")
    print(f"Document Type: {meta.get('doc_type')} ({doc_info.get('description', '')})")
    print(f"Target Date: {meta.get('target_date')} | Baseline Date: {meta.get('source_date') or 'None (Standalone Audit)'}")
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

    # 1. DO NOW (Fixes required before signing)
    do_now = findings.get("do_now_items", [])
    print(f"### 1. DO NOW ({len(do_now)} issue(s) to fix):")
    if not do_now:
        print("  - None! No automated errors detected.")
    else:
        for i, item in enumerate(do_now, 1):
            fix_type_str = f" [{item.get('fix_type').upper()}]" if item.get('fix_type') else ""
            print(f"  {i}. [{item.get('section')}] Field: {item.get('field')}{fix_type_str}")
            if item.get('current'):
                print(f"     Current: {item.get('current')}")
            print(f"     Action:  {item.get('fix')}")

    # 2. CLINICAL DECISION / USER REVIEW
    decisions = findings.get("clinical_decision_items", [])
    print(f"\n### 2. CLINICAL JUDGMENT / USER REVIEW ({len(decisions)} item(s)):")
    if not decisions:
        print("  - None.")
    else:
        for i, item in enumerate(decisions, 1):
            fix_type_str = f" [{item.get('fix_type').upper()}]" if item.get('fix_type') else ""
            print(f"  {i}. [{item.get('section')}] Field: {item.get('field')}{fix_type_str}")
            print(f"     Issue:   {item.get('current')}")
            print(f"     Action:  {item.get('fix')}")

    # 3. LEAVE (Verified Correct)
    leaves = findings.get("leave_items", [])
    print(f"\n### 3. LEAVE ({len(leaves)} section(s) verified correct):")
    for item in leaves:
        val_str = f" ({item.get('val')})" if item.get('val') else ""
        print(f"  - [{item.get('section')}] {item.get('field')}{val_str}: {item.get('note', 'PASS')}")

    print("\n" + "=" * 70)
    print("Verification completed (Read-only; target remains Pending and unsigned).")
    if findings.get("audit_latest_path"):
        print(f"Writer input file: {findings['audit_latest_path']}")
    print("=" * 70 + "\n")

def discover_source_note_date(page, patient_name: str, doc_type: str, target_date: str, base_url: str = "") -> str:
    """
    Automatically scans patient chart across all episodes to find the newest completed
    source note prior to the target date, matching the appropriate baseline strategy.
    """
    doc_info = classify_document(doc_type)
    print(f"--> [Auto-Discovery] Searching Patient Chart for baseline note ({doc_info['display_name']})...")
    select_patient_and_go_to_chart(page, patient_name, base_url)
    page.wait_for_timeout(3000)

    find_baseline_js = """(criteria) => {
        const docLower = (criteria.docType || '').trim().toLowerCase();
        const category = criteria.category || '';
        const tgtDate = criteria.targetDate || '';
        const rows = Array.from(document.querySelectorAll('table tr, .document-row'));
        const completedNotes = [];

        function parseDate(str) {
            const m = str.match(/(\\d{1,2})\\/(\\d{1,2})\\/(\\d{4})/);
            if (m) return new Date(parseInt(m[3], 10), parseInt(m[1], 10) - 1, parseInt(m[2], 10));
            return null;
        }

        const tgtDt = parseDate(tgtDate);

        for (let tr of rows) {
            const cells = Array.from(tr.querySelectorAll('td')).map(c => (c.innerText || '').trim());
            if (cells.length < 3) continue;
            const fullText = cells.join(' ').toLowerCase();

            let matchesDoc = false;
            if (category === 'SN_VISIT_NOTE') {
                matchesDoc = fullText.includes('visit note') || fullText.includes('skilled nurse');
            } else {
                matchesDoc = fullText.includes('oasis');
            }

            const isCompleted = fullText.includes('completed') || fullText.includes('sent to office');
            const isPending = fullText.includes('pending');

            if (matchesDoc && isCompleted && !isPending) {
                const dateMatch = cells.join(' ').match(/\\d{1,2}\\/\\d{1,2}\\/\\d{4}/);
                if (dateMatch) {
                    const dt = parseDate(dateMatch[0]);
                    if (dt && (!tgtDt || dt < tgtDt)) {
                        completedNotes.push({
                            dateStr: dateMatch[0],
                            timestamp: dt.getTime(),
                            rowText: cells.join(' | ')
                        });
                    }
                }
            }
        }

        completedNotes.sort((a, b) => b.timestamp - a.timestamp);
        return completedNotes.length > 0 ? completedNotes[0] : null;
    }"""
    criteria = {"docType": doc_type, "category": doc_info["category"], "targetDate": target_date}
    res = page.evaluate(find_baseline_js, criteria)
    for _ in iter_chart_episodes(page):
        candidate = page.evaluate(find_baseline_js, criteria)
        if candidate and (not res or candidate["timestamp"] > res["timestamp"]):
            res = candidate

    if res and res.get("dateStr"):
        print(f"--> [Auto-Discovery] Found newest completed baseline note on date '{res['dateStr']}' ({res.get('rowText', '')[:80]})")
        return res["dateStr"]

    print("--> [Auto-Discovery] No completed baseline note found prior to target date.")
    return ""

def run_verification_for_task(context, page, base_url: str, task: Dict) -> Dict:
    patient = task.get("patient", "")
    caregiver = task.get("caregiver", "")
    doc_type = task.get("doc_type", "OASIS-E2 Nurse Recert")
    source_doc_type = task.get("source_doc_type", doc_type)
    target_date = task.get("target_date", "")
    source_date = task.get("source_date", "")

    doc_info = classify_document(doc_type)

    # If source date not provided, auto-discover it from the chart
    if not source_date:
        source_date = discover_source_note_date(page, patient, doc_type, target_date, base_url)
        task["source_date"] = source_date

    print("\n" + "=" * 60)
    print(f"Starting Read-Only Clinical Verification for '{patient}'")
    print(f"Document: {doc_info['display_name']}")
    print(f"Target: {doc_type} ({target_date}, Pending, {caregiver})")
    print(f"Source: {source_doc_type} ({source_date or 'None (Standalone Audit)'}, Completed)")
    print("=" * 60)

    # 1. Open Target Note
    print(f"\n[Step 1] Opening TARGET note ({target_date} Pending)...")
    target_page = open_note_by_ui_click(
        context=context,
        active_page=page,
        base_url=base_url,
        patient_name=patient,
        doc_type=doc_type,
        note_date=target_date,
        caregiver=caregiver,
        is_target=True
    )

    target_pages = []
    if target_page:
        print(f"[OK] Target form opened!")
        try:
            target_pages = extract_all_pages_deep(target_page, label=f"Target ({target_date})")
        finally:
            if target_page != page and not target_page.is_closed():
                try:
                    target_page.close()
                except Exception:
                    pass
    else:
        print(f"[ERROR] Could not locate target note row on Patient Chart for {target_date}!")
        return {"error": f"Target note not found on Patient Chart for {target_date}"}

    # 2. Open Source Note (if available)
    source_pages = []
    if source_date:
        print(f"\n[Step 2] Opening SOURCE baseline note ({source_date} Completed)...")
        source_page = open_note_by_ui_click(
            context=context,
            active_page=page,
            base_url=base_url,
            patient_name=patient,
            doc_type=doc_type,
            source_doc_type=source_doc_type,
            note_date=source_date,
            caregiver=caregiver,
            is_target=False
        )

        if source_page:
            print(f"[OK] Source baseline form opened!")
            try:
                source_pages = extract_all_pages_deep(source_page, label=f"Source ({source_date})")
            finally:
                if source_page != page and not source_page.is_closed():
                    try:
                        source_page.close()
                    except Exception:
                        pass
        else:
            print(f"[WARN] Could not open source baseline note for {source_date}. Proceeding with single-note audit.")
    else:
        print("\n[Step 2] No source baseline note found/provided. Performing standalone target document audit...")

    # 3. Apply Audit Rules (Universal multi-document router)
    print(f"\n[Step 3] Running clinical and regulatory audit rules for {doc_info['display_name']}...")
    audit_results = audit_universal_document(target_pages, source_pages, task)

    # 4. Save one immutable run plus the backward-compatible latest snapshot.
    log_dir = os.path.join(WORKSPACE_ROOT, "logs")
    audit_payload = {
        "task": task,
        "audit_results": audit_results,
        "target_pages": target_pages,
        "source_pages": source_pages,
    }
    audit_run_path, audit_latest_path = save_audit_run(
        log_dir, patient, target_date, audit_payload
    )
    audit_results["audit_run_path"] = audit_run_path
    audit_results["audit_latest_path"] = audit_latest_path
    print(f"[OK] Immutable audit run saved to {audit_run_path}")
    print(f"[OK] Latest audit snapshot updated at {audit_latest_path}")
    print(f"[WRITER INPUT] Verification audit file saved for Writer:")
    print(f"               --> Latest snapshot: {audit_latest_path}")
    print(f"               --> Versioned run:   {audit_run_path}")

    # 5. Output Report
    print_audit_report(audit_results, task)
    return audit_results

def main():
    parser = argparse.ArgumentParser(description="OASIS Document Verifier for DeVero EHR")
    parser.add_argument("-t", "--tasks", "--config", dest="config_file", default=None, help="Path to tasks configuration file (default: tasks.jsonl)")
    parser.add_argument("--patient", dest="patient", help="Patient name (e.g. 'FixtureOne, Patient')")
    parser.add_argument("--caregiver", dest="caregiver", help="Caregiver name (e.g. 'Example, Clinician (RN)')")
    parser.add_argument("--doc-type", dest="doc_type", default="OASIS-E2 Nurse Recert", help="Target document type")
    parser.add_argument("--source-doc-type", dest="source_doc_type", default=None, help="Source document type")
    parser.add_argument("--target-date", dest="target_date", help="Target assessment date (MM/DD/YYYY)")
    parser.add_argument("--source-date", dest="source_date", help="Source assessment date (MM/DD/YYYY)")
    args = parser.parse_args()

    # Determine tasks to run
    tasks_to_run = []
    if args.patient and args.target_date:
        tasks_to_run.append({
            "patient": args.patient,
            "caregiver": args.caregiver or "",
            "doc_type": args.doc_type,
            "source_doc_type": args.source_doc_type or args.doc_type,
            "target_date": args.target_date,
            "source_date": args.source_date or ""
        })
    else:
        config_path = args.config_file or os.path.join(WORKSPACE_ROOT, "tasks.jsonl")
        if not os.path.exists(config_path):
            print(f"[ERROR] Task configuration file not found: {config_path}")
            sys.exit(1)
        tasks_to_run = load_tasks_config(config_path)
        if args.patient:
            pat_filter = args.patient.lower()
            tasks_to_run = [t for t in tasks_to_run if pat_filter in (t.get("patient") or "").lower()]

    total_tasks = len(tasks_to_run)
    if total_tasks == 0:
        print("[NOTICE] No active tasks found to verify.")
        return

    print(f"\n======================================================================")
    print(f"Loaded {total_tasks} task(s) for sequential verification.")
    for idx, t in enumerate(tasks_to_run, start=1):
        print(f"  {idx}. {t.get('patient')} | {t.get('doc_type')} | {t.get('target_date')}")
    print("======================================================================\n")

    with sync_playwright() as p:
        try:
            browser = p.chromium.connect_over_cdp(DEFAULT_CDP_URL)
            context = browser.contexts[0]
            print("Connected via CDP on", DEFAULT_CDP_URL)
        except Exception:
            context = p.chromium.launch_persistent_context(
                user_data_dir=PROFILE_DIR,
                headless=False,
                args=["--no-first-run", "--no-default-browser-check"]
            )

        page = context.pages[0] if context.pages else context.new_page()
        base_url = extract_base_url(DEVERO_URL)
        robust_login(page, base_url)

        results_summary = []

        for idx, task in enumerate(tasks_to_run, start=1):
            patient = task.get("patient", "Unknown")
            doc_type = task.get("doc_type", "Unknown")
            target_date = task.get("target_date", "Unknown")

            print("\n" + "#" * 70)
            print(f"TASK {idx} OF {total_tasks}: {patient} | {doc_type} | {target_date}")
            print("#" * 70)

            # Ensure auxiliary pages/popups are closed before starting task
            for extra_page in list(context.pages):
                if extra_page != page and not extra_page.is_closed():
                    try:
                        extra_page.close()
                    except Exception:
                        pass

            # Ensure main page is still alive
            if page.is_closed():
                page = context.pages[0] if context.pages else context.new_page()
                robust_login(page, base_url)

            task_start_time = time.time()
            try:
                res = run_verification_for_task(context, page, base_url, task)
                duration = time.time() - task_start_time

                if isinstance(res, dict) and "error" in res:
                    results_summary.append({
                        "task": task,
                        "status": "FAILED",
                        "detail": res.get("error") or "Unknown error",
                        "duration": duration,
                        "writer_file": None
                    })
                else:
                    audit_status = res.get("pass_fail_status", "PASS") if isinstance(res, dict) else "PASS"
                    do_now_count = len(res.get("do_now_items", [])) if isinstance(res, dict) else 0
                    writer_file = res.get("audit_latest_path") or res.get("audit_run_path") if isinstance(res, dict) else None
                    results_summary.append({
                        "task": task,
                        "status": "SUCCESS",
                        "audit_status": audit_status,
                        "detail": f"{audit_status} ({do_now_count} DO NOW items)",
                        "duration": duration,
                        "writer_file": writer_file
                    })
            except Exception as exc:
                duration = time.time() - task_start_time
                print(f"[ERROR] Task {idx} failed with unhandled exception: {exc}")
                results_summary.append({
                    "task": task,
                    "status": "ERROR",
                    "detail": str(exc),
                    "duration": duration,
                    "writer_file": None
                })

            # Clean up auxiliary pages after task
            for extra_page in list(context.pages):
                if extra_page != page and not extra_page.is_closed():
                    try:
                        extra_page.close()
                    except Exception:
                        pass

            if idx < total_tasks:
                print(f"\n--> Completed Task {idx}/{total_tasks}. Waiting 2s before starting next task...\n")
                time.sleep(2)

        # Batch summary report
        print("\n" + "=" * 70)
        print(f"BATCH VERIFICATION SUMMARY ({total_tasks} task(s) processed)")
        print("=" * 70)
        success_count = sum(1 for r in results_summary if r["status"] == "SUCCESS")
        for idx, item in enumerate(results_summary, start=1):
            t = item["task"]
            status = item["status"]
            detail = item.get("detail", "")
            dur_str = f"{item.get('duration', 0):.1f}s"
            print(f"  {idx}. [{status}] {t.get('patient')} | {t.get('doc_type')} | {t.get('target_date')} - {detail} ({dur_str})")
            if item.get("writer_file"):
                print(f"     --> Writer Input File: {item['writer_file']}")
        print("=" * 70)
        print(f"Total: {total_tasks} | Succeeded: {success_count} | Failed/Errors: {total_tasks - success_count}\n")

        if any(r.get("writer_file") for r in results_summary):
            print("Writer workflow ready. Use the generated audit file(s) with Writer:")
            print("  - Skilled Nurse Visit Note: uv run python writer/fix_visit_note.py draft -t <tasks.jsonl> --auto-approve")
            print("  - OASIS Assessment Summary:  uv run python writer/fix_oasis_summary.py draft -t <tasks.jsonl> --auto-approve")
            print("  - OASIS Fields Fixer:        uv run python writer/fix_oasis.py --config <tasks.jsonl>\n")

if __name__ == "__main__":
    main()
