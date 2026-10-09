"""Read-only Devero evidence scanner used by the Hermes Verifier.

This module owns navigation and extraction only.  It has no form injection,
Save, signing, submission, approval, or finalization code paths.
"""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
import re
import time
from typing import Any

from dotenv import load_dotenv
from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

from .browser_lease import BrowserClaimManager, BrowserLease
from .case_store import CaseStore
from .verification import audit_bundle, compare_snapshots, document_rule


WORKSPACE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAX_PAGES = 100
MAX_RELATED_NOTES = 3


class VerificationBlocked(RuntimeError):
    pass


EXTRACT_FRAME_JS = r"""() => {
    const transient = '#formFramePopup, #insertToTemplatePopup, #templateView, [id*="templateView"], .modal, .popup-modal, [role="dialog"], .sweet-alert';
    const visible = el => !!el && (el.offsetWidth > 0 || el.offsetHeight > 0 || (el.getClientRects && el.getClientRects().length));
    const text = value => String(value || '').replace(/\s+/g, ' ').trim();
    const itemCode = value => {
        const match = String(value || '').match(/(?:^|[^A-Z0-9])((?:M|A|B|C|D|E|GG|H|I|J|K|L|N|O|Q)\d{4})(?:[A-Z]\d?)?(?![A-Z0-9])/i);
        return match ? match[1].toUpperCase() : '';
    };
    const labelFor = el => {
        if (el.id) {
            const label = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
            if (label) return text(label.innerText);
        }
        const wrapping = el.closest && el.closest('label');
        if (wrapping) return text(wrapping.innerText);
        const aria = el.getAttribute && (el.getAttribute('aria-label') || el.getAttribute('data-label'));
        if (aria) return text(aria);
        let parent = el.parentElement;
        for (let depth = 0; parent && depth < 5; depth++, parent = parent.parentElement) {
            const candidate = text(parent.innerText);
            if (candidate && candidate.length <= 240) return candidate;
        }
        return '';
    };
    const selectors = [
        'input:not([type="hidden"])', 'textarea', 'select', 'cmwf-checkbox',
        'cmwf-textbox', 'cmwf-textarea', 'cmwf-dropdown', 'ntst-select',
        'cmwf-text-display', 'cmwf-cop', '[data-field-id]', '[ng-model]'
    ].join(',');
    const fields = [];
    const seen = new Set();
    for (const el of document.querySelectorAll(selectors)) {
        if (el.closest && el.closest(transient)) continue;
        const tag = el.tagName.toLowerCase();
        const type = String(el.type || '').toLowerCase();
        const name = text(el.name || el.id || el.getAttribute('ng-model') || el.getAttribute('data-field-id') || `${tag}_${fields.length}`);
        const label = labelFor(el);
        const identity = `${tag}|${type}|${name}|${fields.length}`;
        if (seen.has(identity)) continue;
        seen.add(identity);
        let kind = type || tag;
        let value = el.value === undefined ? '' : text(el.value);
        let code = value;
        let checked = !!el.checked;
        if (tag === 'cmwf-checkbox') {
            const input = el.querySelector('input[type="checkbox"], input');
            checked = !!(input && input.checked) || el.getAttribute('aria-checked') === 'true';
            value = checked ? text((input && input.value) || 'checked') : '';
            code = checked ? text(input && input.value) : '';
            kind = 'checkbox';
        } else if (type === 'checkbox' || type === 'radio') {
            value = checked ? text(el.value || 'checked') : '';
            code = checked ? text(el.value) : '';
        } else if (tag === 'select') {
            const selected = el.options && el.selectedIndex >= 0 ? el.options[el.selectedIndex] : null;
            value = text((selected && (selected.text || selected.value)) || el.value);
            code = text((selected && selected.value) || el.value);
            kind = 'select';
        } else if (tag === 'cmwf-dropdown' || tag === 'ntst-select') {
            const selected = el.querySelector('ntst-option[selected], ntst-option[aria-selected="true"], ntst-option.selected, option:checked');
            value = text((selected && (selected.innerText || selected.getAttribute('value'))) || el.value);
            code = text((selected && (selected.getAttribute('value') || selected.value)) || el.value);
            kind = 'select';
        } else if (tag === 'cmwf-cop') {
            value = Array.from(el.querySelectorAll('.cop-items .copElement, .copElement'))
                .filter(node => visible(node) && !(node.closest && node.closest(transient)))
                .map(node => text(node.innerText)).filter(Boolean).join('\n');
            kind = 'linked';
        } else if (tag === 'cmwf-text-display') {
            value = visible(el) ? text(el.innerText) : '';
            kind = 'linked';
        }
        const nativeControl = tag === 'cmwf-checkbox' ? el.querySelector('input') : el;
        const valid = el.getAttribute('aria-invalid') !== 'true'
            && !(el.classList && el.classList.contains('ng-invalid'))
            && !(nativeControl && nativeControl.validity && !nativeControl.validity.valid);
        const nearby = `${name} ${label} ${text(el.closest && el.closest('tr, fieldset, .form-group, cmwf-section')?.innerText).slice(0, 500)}`;
        fields.push({
            name, label, item: itemCode(nearby), kind, value, code, checked, valid,
            visible: !!visible(el),
            required: !!(el.required || el.getAttribute('aria-required') === 'true' || el.classList.contains('required')),
            readonly: !!(el.readOnly || el.disabled || ['cmwf-text-display', 'cmwf-cop'].includes(tag))
        });
    }
    return {fields, body_length: text(document.body && document.body.innerText).length};
}"""


def _normalize(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _parse_chart_date(text: str) -> datetime | None:
    match = re.search(r"\b\d{1,2}/\d{1,2}/\d{4}\b", text)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(), "%m/%d/%Y")
    except ValueError:
        return None


def _identity_name_tokens(value: str) -> list[str]:
    credentials = {"rn", "lpn", "lvn", "pt", "ot", "slp", "msw"}
    without_credentials = re.sub(r"\([^)]*\)", " ", value)
    return [token for token in _normalize(without_credentials).split() if len(token) > 1 and token not in credentials]


def _identity_phrase(value: str) -> str:
    return " ".join(_identity_name_tokens(value))


def _expand_chart_episodes(page: Page) -> None:
    opened = page.evaluate(
        """() => {
            let count = 0;
            for (const panel of document.querySelectorAll('.episode.epsd-pnl, .episode')) {
                const header = panel.querySelector('.episode-header, div[ng-click*="toggleEpisode"]');
                if (header && !header.classList.contains('open')) {
                    try {
                        header.click();
                        count++;
                    } catch (_) {}
                }
            }
            return count;
        }"""
    )
    page.wait_for_timeout(2_000 if opened else 500)


def document_identity_matches(
    visible_text: str,
    *,
    patient: str,
    doc_type: str,
    note_date: str,
    caregiver: str = "",
) -> bool:
    text = _normalize(visible_text)
    rule = document_rule(doc_type)
    if not rule:
        return False
    token_sets = rule["identity_token_sets"]
    if not any(all(token in text for token in tokens) for tokens in token_sets):
        return False
    if not _identity_phrase(patient) or _identity_phrase(patient) not in text:
        return False
    if caregiver and _identity_phrase(caregiver) not in text:
        return False
    expected_date = _parse_chart_date(note_date)
    visible_dates = {
        _parse_chart_date(value)
        for value in re.findall(r"\b\d{1,2}/\d{1,2}/\d{4}\b", visible_text)
    }
    return expected_date is not None and expected_date in visible_dates


def _wait_for_document_identity(
    page: Page,
    expected_identity: dict[str, str],
    timeout_seconds: int = 15,
) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            visible_text = page.locator("body").inner_text(timeout=2_000)
            control_values = page.evaluate(
                """() => Array.from(document.querySelectorAll('input, textarea, select'))
                    .map(el => String(el.value || '')).join(' ')"""
            )
            if document_identity_matches(f"{visible_text}\n{control_values}", **expected_identity):
                return True
        except Exception:
            pass
        page.wait_for_timeout(500)
    return False


def row_matches(row: dict[str, Any], *, doc_type: str, note_date: str, status: str, caregiver: str = "") -> bool:
    text = _normalize(row.get("text"))
    form = _normalize(row.get("form") or row.get("text"))
    row_date = _parse_chart_date(str(row.get("date") or row.get("text") or ""))
    expected_date = _parse_chart_date(note_date)
    row_status = _normalize(row.get("status") or row.get("text"))
    expected_doc = _normalize(doc_type)
    if expected_doc not in form or not row_date or not expected_date or row_date != expected_date:
        return False
    if caregiver and _normalize(caregiver) not in text:
        return False
    if status == "pending":
        return "pending" in row_status
    return ("completed" in row_status or "sent to office" in row_status) and "pending" not in row_status


def choose_source_row(
    rows: list[dict[str, Any]], *, target_date: str, doc_type: str = "", source_date: str = ""
) -> dict[str, Any] | None:
    target = _parse_chart_date(target_date)
    expected_source_date = _parse_chart_date(source_date) if source_date else None
    if source_date and not expected_source_date:
        return None
    candidates = []
    for row in rows:
        form = _normalize(row.get("form") or row.get("text"))
        status = _normalize(row.get("status") or row.get("text"))
        when = _parse_chart_date(str(row.get("date") or row.get("text") or ""))
        if not when or target and when >= target:
            continue
        if not ("completed" in status or "sent to office" in status) or "pending" in status:
            continue
        if expected_source_date and when != expected_source_date:
            continue
        if doc_type:
            if _normalize(doc_type) not in form:
                continue
        elif "oasis" not in form:
            continue
        candidates.append((when, row))
    return max(candidates, key=lambda item: item[0])[1] if candidates else None


def related_rows(rows: list[dict[str, Any]], *, target_date: str, excluded: set[str]) -> list[dict[str, Any]]:
    target = _parse_chart_date(target_date)
    patterns = ("plan of care", "order", "skilled nurse visit note", "oasis")
    matches: dict[str, list[tuple[datetime, dict[str, Any]]]] = {
        "PLAN_OF_CARE": [], "ORDER": [], "OASIS": [], "SKILLED_NOTE": []
    }
    for row in rows:
        row_id = str(row.get("activity_id") or row.get("index"))
        form = _normalize(row.get("form") or row.get("text"))
        status = _normalize(row.get("status") or row.get("text"))
        when = _parse_chart_date(str(row.get("date") or row.get("text") or ""))
        if row_id in excluded or not when or target and when > target:
            continue
        if (
            not ("completed" in status or "sent to office" in status)
            or "pending" in status
            or not any(value in form for value in patterns)
        ):
            continue
        matches[related_classification(form)].append((when, row))
    for values in matches.values():
        values.sort(key=lambda item: item[0], reverse=True)
    selected = matches["PLAN_OF_CARE"][:1] + matches["ORDER"][:1] + matches["OASIS"][:1]
    selected += matches["SKILLED_NOTE"][:MAX_RELATED_NOTES]
    return [row for _when, row in selected]


def related_classification(text: str) -> str:
    normalized = _normalize(text)
    if "plan of care" in normalized:
        return "PLAN_OF_CARE"
    if "order" in normalized:
        return "ORDER"
    if "skilled nurse visit note" in normalized:
        return "SKILLED_NOTE"
    return "OASIS"


def _login(page: Page, base_url: str, username: str, password: str) -> None:
    page.goto(base_url, wait_until="domcontentloaded", timeout=45_000)
    page.wait_for_timeout(1_500)
    password_box = page.locator('input[type="password"]').first
    if not password_box.is_visible(timeout=1_000):
        return
    if not username or not password:
        raise VerificationBlocked("AUTHENTICATION_REQUIRED")
    user_box = page.locator(
        '#username, input[name="uName"], input[name="username"], input[name="j_username"], input[type="text"]'
    ).first
    if not user_box.is_visible(timeout=2_000):
        raise VerificationBlocked("LOGIN_FORM_UNRECOGNIZED")
    user_box.fill(username)
    password_box.fill(password)
    submit = page.locator('button[type="submit"], input[type="submit"], #loginBtn').first
    if submit.is_visible(timeout=1_000):
        submit.click()
    else:
        password_box.press("Enter")
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if not page.locator('input[type="password"]').first.is_visible(timeout=500):
            return
    raise VerificationBlocked("AUTHENTICATION_FAILED")


def _select_patient_and_chart(page: Page, base_url: str, patient: str) -> None:
    pat_clean = patient.split(",", 1)[0].strip()
    if not pat_clean:
        raise VerificationBlocked("PATIENT_NOT_FOUND")
    # Same evaluate-based selection proven in devero_copier.select_patient_and_go_to_chart:
    # locator visibility checks are unreliable for the top-nav dropdown in fresh sessions.
    # Click the innermost matching element: the first querySelectorAll hit is often a
    # wrapper div whose click triggers Angular navigation and destroys the context.
    try:
        clicked = page.evaluate(
            """() => {
                let best = null;
                for (const btn of document.querySelectorAll('a, button, div, span')) {
                    const txt = (btn.innerText || btn.title || '').trim();
                    if (!txt.includes('Select a Patient') && btn.getAttribute('data-automation-id') !== 'patient-drop-down-search-input') continue;
                    if (!best || (btn.innerText || '').length < (best.innerText || '').length) best = btn;
                }
                if (!best) return false;
                best.click();
                return true;
            }"""
        )
    except Exception:
        # Navigation destroyed the context; the click itself may still have landed.
        clicked = True
    if not clicked:
        # No "Select a Patient" toggle: the patient is likely already selected
        # (top bar shows their name) on repeat calls for the same patient.
        already = page.evaluate(
            """name => {
                const bar = document.querySelector('.patient-info, [data-automation-id="patient-drop-down-search-input"], .navbar, header');
                return !!(bar && (bar.innerText || '').toLowerCase().includes(name.toLowerCase()));
            }""",
            pat_clean,
        )
        if not already:
            raise VerificationBlocked("PATIENT_DROPDOWN_NOT_FOUND")
        page.wait_for_timeout(1_000)
    else:
        page.wait_for_timeout(1_500)
    typed = page.evaluate(
        """name => {
            for (const inp of document.querySelectorAll('input')) {
                const ngMod = inp.getAttribute('ng-model') || '';
                const autoId = inp.getAttribute('data-automation-id') || '';
                if (ngMod === 'search.text' || autoId === 'patient-drop-down-search-input' || (inp.placeholder || '').includes('Search')) {
                    inp.value = name;
                    inp.dispatchEvent(new Event('input', { bubbles: true }));
                    inp.dispatchEvent(new Event('change', { bubbles: true }));
                    return true;
                }
            }
            return false;
        }""",
        pat_clean,
    )
    if not typed:
        raise VerificationBlocked("PATIENT_SEARCH_BOX_NOT_FOUND")
    page.wait_for_timeout(2_000)
    selected = page.evaluate(
        """name => {
            for (const item of document.querySelectorAll('li, a, tr, div')) {
                const txt = (item.innerText || '').trim();
                if (txt.toLowerCase().includes(name.toLowerCase()) && !txt.includes('Select a Patient')) {
                    item.click();
                    return true;
                }
            }
            return false;
        }""",
        pat_clean,
    )
    if not selected:
        raise VerificationBlocked("PATIENT_NOT_FOUND")
    page.wait_for_timeout(1_500)
    page.goto(f"{base_url}/app#/patientCharts", wait_until="domcontentloaded", timeout=45_000)
    page.wait_for_timeout(2_000)
    _expand_chart_episodes(page)
    patient_phrase = _identity_phrase(patient)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        visible_text = _normalize(page.locator("body").inner_text(timeout=2_000))
        if patient_phrase and patient_phrase in visible_text:
            return
        page.wait_for_timeout(500)
    raise VerificationBlocked("PATIENT_IDENTITY_UNCONFIRMED")


def _chart_rows(page: Page) -> list[dict[str, Any]]:
    return page.evaluate(
        r"""() => Array.from(document.querySelectorAll('table tr, tr[data-activity-id], .document-row')).map((row, index) => {
            const cells = Array.from(row.querySelectorAll(':scope > td'));
            return {
                index,
                activity_id: row.getAttribute('data-activity-id') || '',
                text: (row.innerText || '').replace(/\s+/g, ' ').trim(),
                form: (cells[0]?.innerText || '').trim(),
                date: (cells[1]?.innerText || '').trim(),
                status: (cells[3]?.innerText || '').trim(),
                has_link: !!row.querySelector('a, .dbDrivenForm-new-window, .dbDrivenForm-inline-window, .standardform-new-window, .standardform-inline-window')
            };
        }).filter(row => row.text && row.has_link)"""
    )


def _open_row(page: Page, context: BrowserContext, row: dict[str, Any]) -> Page:
    before = set(context.pages)
    activity_id = str(row.get("activity_id") or "")
    clicked = page.evaluate(
        """criteria => {
            const rows = Array.from(document.querySelectorAll('table tr, tr[data-activity-id], .document-row'));
            const row = criteria.activityId
                ? document.querySelector(`tr[data-activity-id="${CSS.escape(criteria.activityId)}"]`)
                : rows[criteria.index];
            if (!row) return false;
            const link = row.querySelector('.dbDrivenForm-new-window, .dbDrivenForm-inline-window, .standardform-new-window, .standardform-inline-window, a');
            if (!link) return false;
            link.click();
            return true;
        }""",
        {"activityId": activity_id, "index": int(row["index"])},
    )
    if not clicked:
        raise VerificationBlocked("DOCUMENT_OPEN_FAILED")
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        new_pages = [candidate for candidate in context.pages if candidate not in before]
        if not new_pages:
            page.wait_for_timeout(500)
            continue
        candidate = new_pages[-1]
        try:
            controls = candidate.locator("input, textarea, select, cmwf-textbox, cmwf-checkbox").count()
            body_length = candidate.evaluate("() => (document.body && document.body.innerText || '').trim().length")
            if controls >= 1 or body_length >= 100:
                return candidate
        except Exception:
            pass
        page.wait_for_timeout(500)
    raise VerificationBlocked("DOCUMENT_LOAD_TIMEOUT")


def _frame_signature(page: Page) -> str:
    parts = []
    for frame in page.frames:
        try:
            state = frame.evaluate(EXTRACT_FRAME_JS)
            encoded = json.dumps(state, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            parts.append(f"{int(state.get('body_length') or 0)}:{hashlib.sha256(encoded.encode()).hexdigest()}")
        except Exception:
            parts.append("unavailable")
    return "|".join(parts)


def _wait_stable(page: Page, timeout_seconds: int = 60) -> bool:
    deadline = time.monotonic() + timeout_seconds
    previous = ""
    stable = 0
    while time.monotonic() < deadline:
        signature = _frame_signature(page)
        if signature and signature == previous and any(part.split(":", 1)[0].isdigit() and int(part.split(":", 1)[0]) for part in signature.split("|")):
            stable += 1
            if stable >= 3:
                return True
        else:
            stable = 0
        previous = signature
        page.wait_for_timeout(400)
    return False


def _go_to_page_one(page: Page) -> None:
    before = _frame_signature(page)
    for frame in page.frames:
        try:
            state = frame.evaluate(
                """() => {
                    const visible = el => el && (el.offsetWidth > 0 || el.offsetHeight > 0 || (el.getClientRects && el.getClientRects().length));
                    const text = el => String(el.innerText || el.value || '').trim();
                    const candidates = Array.from(document.querySelectorAll('a, button, input')).filter(el => visible(el) && text(el) === '1');
                    const pageOne = candidates.find(el => {
                        const container = el.closest('.pagination, .pager, nav, [class*="pagin"]') || el.parentElement;
                        const context = String(container && container.innerText || '').toLowerCase();
                        return context.includes('next') || context.includes('previous');
                    });
                    if (!pageOne) return 'absent';
                    const classes = `${pageOne.className || ''} ${(pageOne.parentElement && pageOne.parentElement.className) || ''}`.toLowerCase();
                    if (classes.includes('active') || pageOne.getAttribute('aria-current') === 'page') return 'already';
                    pageOne.click();
                    return 'clicked';
                }"""
            )
            if state == "already":
                return
            if state == "clicked":
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline and _frame_signature(page) == before:
                    page.wait_for_timeout(300)
                if _frame_signature(page) == before:
                    raise VerificationBlocked("PAGE_ONE_DID_NOT_LOAD")
                return
        except VerificationBlocked:
            raise
        except Exception:
            pass


def _capture_page(page: Page, page_number: int) -> dict[str, Any]:
    ready = _wait_stable(page)
    frames = []
    for index, frame in enumerate(page.frames):
        try:
            extracted = frame.evaluate(EXTRACT_FRAME_JS)
            frames.append({"index": index, "fields": extracted["fields"]})
        except Exception:
            frames.append({"index": index, "fields": [], "unavailable": True})
            ready = False
    return {"page": page_number, "extraction": {"ready": ready}, "frames": frames}


def _click_next(page: Page) -> bool:
    for frame in page.frames:
        try:
            clicked = frame.evaluate(
                """() => {
                    const visible = el => el && (el.offsetWidth > 0 || el.offsetHeight > 0 || (el.getClientRects && el.getClientRects().length));
                    const buttons = Array.from(document.querySelectorAll('a, button, input'));
                    const next = buttons.find(el => {
                        const text = String(el.innerText || el.value || '').trim().toLowerCase();
                        const classes = `${el.className || ''} ${(el.parentElement && el.parentElement.className) || ''}`.toLowerCase();
                        const ngClick = el.getAttribute('ng-click') || '';
                        return visible(el) && (text === 'next >' || text === 'next') && !el.disabled && !classes.includes('disabled') && !classes.includes('activenp') && !ngClick.includes('printFormController');
                    });
                    if (!next) return false;
                    next.click();
                    return true;
                }"""
            )
            if clicked:
                return True
        except Exception:
            pass
    return False


def capture_document(page: Page) -> list[dict[str, Any]]:
    _go_to_page_one(page)
    pages = []
    seen = set()
    for page_number in range(1, MAX_PAGES + 1):
        captured = _capture_page(page, page_number)
        signature = _frame_signature(page)
        if signature in seen:
            captured["extraction"] = {"ready": False, "reason": "PAGER_LOOP"}
            pages.append(captured)
            break
        seen.add(signature)
        pages.append(captured)
        if not captured["extraction"]["ready"] or not _click_next(page):
            break
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and _frame_signature(page) == signature:
            page.wait_for_timeout(300)
        if _frame_signature(page) == signature:
            pages[-1]["extraction"] = {"ready": False, "reason": "PAGE_DID_NOT_CHANGE"}
            break
    else:
        pages[-1]["extraction"] = {"ready": False, "reason": "PAGE_LIMIT"}
    return pages


def _open_capture_from_chart(
    chart: Page,
    context: BrowserContext,
    row: dict[str, Any],
    *,
    expected_identity: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    row_id = str(row.get("activity_id") or "")
    matches = []
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        _expand_chart_episodes(chart)
        refreshed = _chart_rows(chart)
        if row_id:
            matches = [candidate for candidate in refreshed if str(candidate.get("activity_id")) == row_id]
        else:
            matches = [candidate for candidate in refreshed if candidate.get("text") == row.get("text")]
        if len(matches) == 1:
            break
        chart.wait_for_timeout(500)
    if len(matches) != 1:
        raise VerificationBlocked("DOCUMENT_IDENTITY_CHANGED")
    form = _open_row(chart, context, matches[0])
    try:
        if expected_identity and not _wait_for_document_identity(form, expected_identity):
            raise VerificationBlocked("OPEN_DOCUMENT_IDENTITY_UNCONFIRMED")
        return capture_document(form)
    finally:
        if form is not chart:
            form.close()


def _connect(playwright, cdp_url: str, headless: bool) -> tuple[Browser | None, BrowserContext, bool]:
    if cdp_url:
        try:
            browser = playwright.chromium.connect_over_cdp(cdp_url)
            if browser.contexts:
                return browser, browser.contexts[0], False
        except Exception:
            pass
    browser = playwright.chromium.launch(headless=headless)
    return browser, browser.new_context(), True


def _scan_live(case_id: str, record: dict[str, Any]) -> dict[str, Any]:
    load_dotenv(os.path.join(WORKSPACE, ".env"))
    base_url = os.getenv("DEVERO_URL", "").rstrip("/")
    if not base_url:
        raise VerificationBlocked("DEVERO_URL_MISSING")
    identity = record["identity"]
    if not document_rule(str(identity.get("doc_type") or "")):
        raise VerificationBlocked("UNSUPPORTED_DOCUMENT")
    with sync_playwright() as playwright:
        browser, context, owns_browser = _connect(
            playwright,
            os.getenv("DEVERO_CDP_URL", "http://127.0.0.1:9222").strip(),
            os.getenv("DEVERO_HEADLESS", "true").lower() in {"1", "true", "yes"},
        )
        chart = context.pages[0] if context.pages else context.new_page()
        try:
            _login(chart, base_url, os.getenv("DEVERO_USERNAME", ""), os.getenv("DEVERO_PASSWORD", ""))
            _select_patient_and_chart(chart, base_url, identity["patient"])
            rows = _chart_rows(chart)
            target_matches = [
                row
                for row in rows
                if row_matches(
                    row,
                    doc_type=identity["doc_type"],
                    note_date=identity["target_date"],
                    status="pending",
                    caregiver=str(identity.get("caregiver") or ""),
                )
            ]
            if len(target_matches) != 1:
                raise VerificationBlocked("TARGET_IDENTITY_AMBIGUOUS")
            target_row = target_matches[0]
            target_pages = _open_capture_from_chart(
                chart,
                context,
                target_row,
                expected_identity={
                    "patient": identity["patient"],
                    "doc_type": identity["doc_type"],
                    "note_date": identity["target_date"],
                    "caregiver": str(identity.get("caregiver") or ""),
                },
            )

            source_row = choose_source_row(
                rows,
                target_date=identity["target_date"],
                doc_type=str(identity.get("source_doc_type") or ""),
                source_date=str(identity.get("source_date") or ""),
            )
            if (identity.get("source_doc_type") or identity.get("source_date")) and not source_row:
                raise VerificationBlocked("SOURCE_NOT_FOUND")
            source_pages = (
                _open_capture_from_chart(chart, context, source_row)
                if source_row
                else []
            )
            excluded = {
                str(target_row.get("activity_id") or target_row.get("index")),
                str((source_row or {}).get("activity_id") or (source_row or {}).get("index")),
            }
            related = []
            for row in related_rows(rows, target_date=identity["target_date"], excluded=excluded):
                related.append(
                    {
                        "classification": related_classification(row.get("form") or row["text"]),
                        "pages": _open_capture_from_chart(chart, context, row),
                    }
                )
            return {
                "case_id": case_id,
                "document_type": identity["doc_type"],
                "target_date": identity["target_date"],
                "source_document_type": identity.get("source_doc_type", ""),
                "target_pages": target_pages,
                "source_pages": source_pages,
                "related_documents": related,
            }
        finally:
            if owns_browser and browser:
                browser.close()


def scan_case(case_id: str, phase: str = "initial", store: CaseStore | None = None) -> dict[str, Any]:
    if phase not in {"initial", "persistence"}:
        raise ValueError("phase must be initial or persistence")
    store = store or CaseStore()
    record = store.load(case_id)
    claims = BrowserClaimManager(store)
    claims.claim(case_id, "verifier")
    try:
        with BrowserLease(case_id, "verifier", store=store, timeout_seconds=1):
            bundle = _scan_live(case_id, record)
        store.mark_resolved(
            case_id,
            live_fingerprint=record["identity_fingerprint"],
            facts={
                "attested_by": "verifier",
                "patient_match": True,
                "document_match": True,
                "date_match": True,
                "pending_target": True,
            },
        )
        result = audit_bundle(bundle)
        if phase == "persistence":
            initial = store.read_artifact(case_id, "verification-initial")
            allowed = [
                item.get("location", "")
                for item in record.get("corrections") or []
                if item.get("approved")
            ]
            result["corrections"] += compare_snapshots(
                (initial.get("audit") or {}).get("snapshot") or {},
                result.get("snapshot") or {},
                allowed,
            )
            if result["corrections"] and result.get("verdict") == "PASS":
                result["verdict"] = "MIXED"
        store.write_artifact(case_id, f"verification-{phase}", {"bundle": bundle, "audit": result})
        store.record_verification(case_id, result)
        return {
            "case_id": case_id,
            "phase": phase,
            "run_status": result["run_status"],
            "verdict": result.get("verdict"),
            "review_stage": result.get("review_stage"),
            "semantic_review_required": bool(result.get("semantic_review_required")),
            "document_kind": result.get("document_kind"),
            "correction_count": len(result.get("corrections") or []),
            "counts": result.get("counts", {}),
        }
    except VerificationBlocked as exc:
        blocked = {
            "run_status": "BLOCKED",
            "verdict": None,
            "review_stage": "DETERMINISTIC",
            "semantic_review_required": False,
            "corrections": [],
            "reason": str(exc),
        }
        store.write_artifact(case_id, f"verification-{phase}", {"audit": blocked})
        store.record_verification(case_id, blocked)
        return {"case_id": case_id, "phase": phase, **blocked}
    finally:
        claims.release(case_id, "verifier")
