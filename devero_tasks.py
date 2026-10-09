#!/usr/bin/env python3
"""
Devero Tasks Generator (devero_tasks.py)

Automates harvesting pending clinical forms (such as "Skilled Nurse Visit Note")
from the Devero Home / Agent Menu dashboard, discovers the most recent completed
source note date from each patient's chart, and outputs tasks_note.json for
devero_copier_note.py.
"""

import argparse
import datetime
import json
import os
import re
import sys
import time
from urllib.parse import urlparse
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

# Reconfigure stdout to handle UTF-8 cleanly on Windows console
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Load environment variables from .env file
load_dotenv()

DEVERO_URL = os.getenv("DEVERO_URL", "https://example.devero.com").rstrip("/")
DEVERO_USERNAME = os.getenv("DEVERO_USERNAME", "").strip()
DEVERO_PASSWORD = os.getenv("DEVERO_PASSWORD", "").strip()
DEVERO_HEADLESS = os.getenv("DEVERO_HEADLESS", "false").lower() in ("true", "1", "yes")
DEFAULT_CDP_URL = os.getenv("DEVERO_CDP_URL", "http://127.0.0.1:9222").strip()

PROFILE_DIR = os.path.join(os.path.expanduser("~"), ".devero_copier_profile")


def extract_base_url(url_str):
    parsed = urlparse(url_str)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return DEVERO_URL


def automated_login(page, base_url):
    current_url = page.url.lower()

    # Check if page is on login page or contains login form inputs
    is_login_page = "login" in current_url or page.evaluate("""() => {
        return !!(document.querySelector('input[type="password"]') ||
                  document.querySelector('input[name="uName"]') ||
                  document.querySelector('input[name="username"]'));
    }""")

    if not is_login_page:
        print("[OK] Already logged into Devero!")
        return True

    print("\n==================================================")
    if DEVERO_USERNAME and DEVERO_PASSWORD:
        print(f"Logging into Devero automatically as '{DEVERO_USERNAME}'...")
        try:
            user_selectors = ['#username', 'input[name="uName"]', 'input[name="username"]', 'input[name="j_username"]', 'input[type="text"]']
            user_filled = False
            for sel in user_selectors:
                try:
                    loc = page.locator(sel).first
                    if loc.is_visible(timeout=1500):
                        loc.fill(DEVERO_USERNAME)
                        user_filled = True
                        break
                except Exception:
                    continue

            pass_selectors = ['#password', 'input[name="password"]', 'input[name="j_password"]', 'input[type="password"]']
            pass_filled = False
            for sel in pass_selectors:
                try:
                    loc = page.locator(sel).first
                    if loc.is_visible(timeout=1500):
                        loc.fill(DEVERO_PASSWORD)
                        pass_filled = True
                        break
                except Exception:
                    continue

            if user_filled and pass_filled:
                submit_selectors = ['button[type="submit"]', 'input[type="submit"]', '#loginBtn', '#login-button', 'button:has-text("Log In")', 'button:has-text("Login")', 'input[value*="Log"]']
                submitted = False
                for sel in submit_selectors:
                    try:
                        loc = page.locator(sel).first
                        if loc.is_visible(timeout=1500):
                            loc.click()
                            submitted = True
                            break
                    except Exception:
                        continue

                if not submitted:
                    page.keyboard.press("Enter")

                print("--> Waiting for authentication response...")
                page.wait_for_timeout(3000)

                start = time.time()
                while time.time() - start < 15:
                    if "login" not in page.url.lower():
                        print("[OK] Automated login successful!")
                        return True
                    time.sleep(1)
        except Exception as e:
            print(f"Notice: Auto-login step: {e}")

    if "login" in page.url.lower():
        print("Credentials not set in .env or manual intervention required.")
        print("   Please log into Devero in Chrome to continue...")
        while "login" in page.url.lower():
            time.sleep(1)
        print("[OK] Login detected!")

    return True


def select_patient_and_go_to_chart(page, patient_name, base_url=""):
    pat_clean = patient_name.split(',')[0].strip() if patient_name else ""
    if not pat_clean:
        return

    print(f"--> Selecting Patient '{pat_clean}' via top bar dropdown...")
    try:
        # Click patient dropdown toggle button in top nav
        page.evaluate("""() => {
            const btns = document.querySelectorAll('a, button, div, span, #searchFormSelect, [data-automation-id="patient-drop-down"]');
            for (let btn of btns) {
                const txt = (btn.innerText || btn.title || '').trim();
                const autoId = btn.getAttribute('data-automation-id') || '';
                if (txt.includes('Select a Patient') || autoId === 'patient-drop-down' || autoId === 'patient-drop-down-search-input') {
                    btn.click();
                    break;
                }
            }
        }""")
        page.wait_for_timeout(1500)

        # Type patient name into search input
        page.evaluate("""(name) => {
            const inputs = document.querySelectorAll('input');
            for (let inp of inputs) {
                const ngMod = inp.getAttribute('ng-model') || '';
                const autoId = inp.getAttribute('data-automation-id') || '';
                if (ngMod === 'search.text' || autoId === 'patient-drop-down-search-input' || (inp.placeholder && inp.placeholder.includes('Search'))) {
                    inp.value = name;
                    inp.dispatchEvent(new Event('input', { bubbles: true }));
                    inp.dispatchEvent(new Event('change', { bubbles: true }));
                    break;
                }
            }
        }""", pat_clean)
        page.wait_for_timeout(2000)

        # Click patient option in dropdown list
        page.evaluate("""(name) => {
            const listItems = document.querySelectorAll('li, a, tr, div');
            for (let item of listItems) {
                const txt = (item.innerText || '').trim();
                if (txt.toLowerCase().includes(name.toLowerCase()) && !txt.includes('Select a Patient')) {
                    item.click();
                    break;
                }
            }
        }""", pat_clean)
        page.wait_for_timeout(3000)
    except Exception as e:
        print(f"--> Patient selection notice: {e}")

    try:
        print("--> Navigating to Patient Chart...")
        if base_url:
            page.goto(f"{base_url}/app#/patientCharts", wait_until="domcontentloaded", timeout=20000)
        else:
            page.click('text=Patient Chart', timeout=3000)
        page.wait_for_timeout(3000)
    except Exception as e:
        print(f"--> Patient Chart navigation notice: {e}")


def wait_for_agent_menu_and_pending_table(page, timeout_sec=25):
    """
    Waits for Devero Agent Menu dashboard to load and for the Pending table rows to populate via AJAX.
    """
    print("--> Waiting for Devero Agent Menu & Pending table rows to load...")
    start_time = time.time()
    
    while time.time() - start_time < timeout_sec:
        status = page.evaluate("""() => {
            const panels = Array.from(document.querySelectorAll('.panel, .ibox, div[ng-repeat*="agentMenuFormStatus"]'));
            for (let p of panels) {
                const heading = p.querySelector('.panel-heading, .ibox-heading, h1, h2, h3, h4, h5, a');
                const title = heading ? (heading.innerText || '').trim() : '';
                if (/^\\^?\\s*pending/i.test(title)) {
                    // Check if collapsed
                    if (heading.classList.contains('collapsed') || heading.getAttribute('aria-expanded') === 'false') {
                        try { heading.click(); } catch(e){}
                    }
                    const trs = Array.from(p.querySelectorAll('tbody tr, table tr'));
                    const dataRows = trs.filter(r => r.querySelectorAll('td').length >= 3 && 
                                                    !r.classList.contains('dataTables_empty') && 
                                                    !r.innerText.includes('Loading...') &&
                                                    !r.innerText.includes('No data available'));
                    if (dataRows.length > 0) {
                        return { ready: true, count: dataRows.length };
                    }
                }
            }
            return { ready: false, count: 0 };
        }""")

        if status.get("ready", False):
            print(f"[OK] Devero Pending table loaded with {status['count']} active row(s)!")
            page.wait_for_timeout(1000)
            return True

        time.sleep(1)

    print("--> [Notice] Proceeding with scraping attempt...")
    return False


def set_pending_table_page_size_max(page):
    """
    Attempts to set the DataTables page length dropdown to 100 or max so all pending notes are visible.
    """
    try:
        page.evaluate("""() => {
            const selects = Array.from(document.querySelectorAll('.dataTables_length select, select[name*="length"], select[name*="showPending"]'));
            for (let s of selects) {
                const options = Array.from(s.options).map(o => parseInt(o.value, 10) || 0);
                const maxVal = Math.max(...options);
                if (maxVal > 10) {
                    s.value = String(maxVal);
                    s.dispatchEvent(new Event('change', { bubbles: true }));
                }
            }
        }""")
        page.wait_for_timeout(1000)
    except Exception:
        pass


def scrape_pending_table_current_page(page, target_doc_type="Skilled Nurse Visit Note", caregiver_filter=""):
    """
    Scrapes the visible rows from the Pending table on the current dashboard view.
    """
    js_extract = """(criteria) => {
        const targetDocLower = (criteria.docType || '').trim().toLowerCase();
        const cgFilterLower = (criteria.caregiver || '').trim().toLowerCase();

        // Locate Pending Panel
        const panels = Array.from(document.querySelectorAll('.panel, .ibox, div[ng-repeat*="agentMenuFormStatus"]'));
        let pendingPanel = null;
        for (let p of panels) {
            const heading = p.querySelector('.panel-heading, .ibox-heading, h1, h2, h3, h4, h5, a');
            const title = heading ? (heading.innerText || '').trim() : '';
            if (/^\\^?\\s*pending/i.test(title)) {
                pendingPanel = p;
                break;
            }
        }

        if (!pendingPanel) {
            return { error: "Pending panel not found", rows: [] };
        }

        const trs = Array.from(pendingPanel.querySelectorAll('tbody tr, table tr'));
        const results = [];
        let allPendingSummary = [];

        trs.forEach((tr, rIdx) => {
            const tds = Array.from(tr.querySelectorAll('td'));
            if (tds.length < 5) return; // Skip headers or empty rows

            const rawText = (tr.innerText || '').trim();
            if (rawText.includes('No data available') || rawText.includes('Loading...') || tr.classList.contains('dataTables_empty')) {
                return;
            }

            // Columns: [0: Patient, 1: MR#, 2: Form, 3: Form Date, 4: User, 5: Date Created, 6: Sent, 7: Modified, 8: Agency]
            const patName = (tds[0] ? tds[0].innerText : '').replace(/\\s+/g, ' ').trim();
            const mrNum = (tds[1] ? tds[1].innerText : '').trim();
            const formName = (tds[2] ? tds[2].innerText : '').replace(/\\s+/g, ' ').trim();
            const formDate = (tds[3] ? tds[3].innerText : '').trim();
            const userName = (tds[4] ? tds[4].innerText : '').replace(/\\s+/g, ' ').trim();
            const dateCreated = (tds[5] ? tds[5].innerText : '').trim();
            const dateModified = (tds[7] ? tds[7].innerText : '').trim();
            const agency = (tds[8] ? tds[8].innerText : '').trim();

            if (!patName || !formName || !formDate) return;

            allPendingSummary.push({
                patient: patName,
                form: formName,
                date: formDate,
                user: userName
            });

            const matchesDoc = !targetDocLower || formName.toLowerCase().includes(targetDocLower);
            const matchesCg = !cgFilterLower || userName.toLowerCase().includes(cgFilterLower);

            if (matchesDoc && matchesCg) {
                results.push({
                    patient: patName,
                    mr_num: mrNum,
                    form: formName,
                    target_date: formDate,
                    caregiver: userName,
                    date_created: dateCreated,
                    date_modified: dateModified,
                    agency: agency,
                    raw_row_idx: rIdx
                });
            }
        });

        // Pagination Info
        const nextBtns = Array.from(pendingPanel.querySelectorAll('.paginate_button.next, a.next, [data-dt-idx="next"], a, button'));
        let hasNext = false;
        for (let btn of nextBtns) {
            const txt = (btn.innerText || btn.textContent || '').trim();
            const isDis = btn.classList.contains('disabled') || btn.disabled || btn.getAttribute('aria-disabled') === 'true';
            if (!isDis && (txt.includes('Next') || txt === '>' || btn.classList.contains('next'))) {
                hasNext = true;
                break;
            }
        }

        return {
            rows: results,
            totalFound: results.length,
            allPendingCount: allPendingSummary.length,
            allPendingSample: allPendingSummary.slice(0, 5),
            pagination: { hasNext: hasNext }
        };
    }"""

    data = page.evaluate(js_extract, {
        "docType": target_doc_type,
        "caregiver": caregiver_filter
    })
    return data


def scrape_all_pending_pages(page, target_doc_type="Skilled Nurse Visit Note", caregiver_filter="", patient_filters=None):
    """
    Scrapes matching rows across all pages of the Pending table on Devero Home / Agent Menu.
    """
    # Maximize page size dropdown if present
    set_pending_table_page_size_max(page)
    patient_filters = [name.strip().casefold() for name in (patient_filters or [])]

    all_rows = []
    seen_keys = set()
    page_num = 1

    while True:
        print(f"--> Scraping Pending table (Page {page_num})...")
        res = scrape_pending_table_current_page(page, target_doc_type, caregiver_filter)
        rows = res.get("rows", [])
        if patient_filters:
            rows = [r for r in rows if any(name in r["patient"].casefold() for name in patient_filters)]
        total_in_table = res.get("allPendingCount", 0)
        
        new_count = 0
        for r in rows:
            key = (r["patient"], r["form"], r["target_date"], r["caregiver"])
            if key not in seen_keys:
                seen_keys.add(key)
                all_rows.append(r)
                new_count += 1

        print(f"    Page {page_num}: Table has {total_in_table} total pending note(s). Matched {len(rows)} '{target_doc_type}' ({new_count} new).")

        # Check if next page exists
        has_next = res.get("pagination", {}).get("hasNext", False)
        if not has_next:
            break

        # Click Next page
        clicked_next = page.evaluate("""() => {
            const panels = Array.from(document.querySelectorAll('.panel, .ibox, div[ng-repeat*="agentMenuFormStatus"]'));
            for (let p of panels) {
                const heading = p.querySelector('.panel-heading, .ibox-heading, h1, h2, h3, h4, h5, a');
                const title = heading ? (heading.innerText || '').trim() : '';
                if (/^\\^?\\s*pending/i.test(title)) {
                    const nextBtns = Array.from(p.querySelectorAll('.paginate_button.next, a.next, [data-dt-idx="next"], a, button'));
                    for (let btn of nextBtns) {
                        const txt = (btn.innerText || btn.textContent || '').trim();
                        const isDis = btn.classList.contains('disabled') || btn.disabled || btn.getAttribute('aria-disabled') === 'true';
                        if (!isDis && (txt.includes('Next') || txt === '>' || btn.classList.contains('next'))) {
                            btn.click();
                            return true;
                        }
                    }
                }
            }
            return false;
        }""")

        if not clicked_next:
            break

        page_num += 1
        page.wait_for_timeout(2000)

    print(f"[OK] Total matching pending forms scraped: {len(all_rows)}")
    return all_rows


def parse_date_safe(date_str):
    """
    Parses MM/DD/YYYY date string into datetime object. Returns None if parse fails.
    """
    if not date_str:
        return None
    cleaned = date_str.strip()
    match = re.search(r'(\d{1,2}/\d{1,2}/\d{4})', cleaned)
    if match:
        try:
            return datetime.datetime.strptime(match.group(1), "%m/%d/%Y")
        except Exception:
            pass
    return None


def select_newest_completed_note(rows, doc_type, before_date=None):
    """
    Return the newest completed/sent-to-office matching note from ``rows``.

    ``before_date`` is an exclusive upper bound.  It keeps a source note older
    than the earliest pending target, so a later clinical note is never copied
    backward into an earlier visit.
    """
    completed_notes = []
    doc_lower = doc_type.strip().lower()

    for row in rows:
        form = row.get("form", "") or ""
        row_text = row.get("rowText", "") or ""
        status = row.get("status", "") or ""
        status_lower = status.lower()
        row_lower = row_text.lower()

        is_target_doc = doc_lower in form.lower() or doc_lower in row_lower
        is_pending = "pending" in status_lower or "pending" in row_lower
        is_completed = (
            "completed" in status_lower
            or "sent to office" in status_lower
            or "completed" in row_lower
            or "sent to office" in row_lower
        )
        if not is_target_doc or not is_completed or is_pending:
            continue

        # Prefer the date column, but fall back to the complete row when the
        # table layout leaves that column empty or puts the date elsewhere.
        parsed_dt = parse_date_safe(row.get("date", "")) or parse_date_safe(row_text)
        if not parsed_dt or (before_date is not None and parsed_dt >= before_date):
            continue

        completed_notes.append({
            "date_str": parsed_dt.strftime("%m/%d/%Y"),
            "dt": parsed_dt,
            "user": row.get("user", "") or "",
            "rowText": row_text,
            "episode": row.get("episode", "") or "",
        })

    return max(completed_notes, key=lambda note: note["dt"], default=None)


def lookup_patient_source_date(page, patient_name, base_url="", doc_type="Skilled Nurse Visit Note", target_dates=None):
    """
    Visit the patient's chart and search every episode for completed documents
    matching doc_type. Return the globally newest completed note older than the
    earliest pending target date. Episode display order is not trusted.
    """
    target_dates = target_dates or []
    target_dt_list = [parsed for d in target_dates if (parsed := parse_date_safe(d))]
    min_target_dt = min(target_dt_list) if target_dt_list else None

    print(f"\n--> Discovering Source Note for Patient: '{patient_name}'...")
    select_patient_and_go_to_chart(page, patient_name, base_url)
    page.wait_for_timeout(3500)

    # 1. Count all episode panels on the chart
    episode_count = page.evaluate("""() => {
        const panels = Array.from(document.querySelectorAll('.episode.epsd-pnl, .episode'));
        return panels.length;
    }""")

    print(f"    Found {episode_count} episode panel(s) on chart for '{patient_name}'.")

    all_chart_rows = []

    for ep_idx in range(episode_count):
        # Check and expand episode panel
        ep_info = page.evaluate("""(idx) => {
            const panels = Array.from(document.querySelectorAll('.episode.epsd-pnl, .episode'));
            const panel = panels[idx];
            if (!panel) return null;

            const header = panel.querySelector('.episode-header, div[ng-click*="toggleEpisode"]');
            const title = header ? (header.innerText || '').trim() : (panel.innerText || '').slice(0, 80);
            
            // Check if open
            const isOpen = header ? header.classList.contains('open') : !!panel.querySelector('table');
            
            // If not open, click header to expand
            if (!isOpen && header) {
                try {
                    header.scrollIntoView({ block: 'center' });
                    header.click();
                } catch(e){}
            }

            return {
                title: title.split('\\n')[0],
                wasOpen: isOpen
            };
        }""", ep_idx)

        if not ep_info:
            continue

        print(f"    Inspecting [{ep_idx+1}/{episode_count}]: {ep_info['title']}...")
        if not ep_info.get("wasOpen"):
            page.wait_for_timeout(2000)

        # Expand 'Nurses Clinical Notes' / 'Clinical Notes' inside this episode if collapsed
        page.evaluate("""(idx) => {
            const panels = Array.from(document.querySelectorAll('.episode.epsd-pnl, .episode'));
            const panel = panels[idx];
            if (!panel) return;
            
            const subAccordions = Array.from(panel.querySelectorAll('tr, div, a, span, button'));
            subAccordions.forEach(el => {
                const txt = (el.innerText || '').trim();
                if ((txt === 'Nurses Clinical Notes' || txt === 'Clinical Notes') && el.children.length < 3) {
                    try { el.click(); } catch(e){}
                }
            });
        }""", ep_idx)
        page.wait_for_timeout(800)

        # Extract rows from this episode panel
        ep_rows = page.evaluate("""(criteria) => {
            const panels = Array.from(document.querySelectorAll('.episode.epsd-pnl, .episode'));
            const panel = panels[criteria.idx];
            if (!panel) return [];

            const trs = Array.from(panel.querySelectorAll('tbody tr, table tr, .document-row'));
            const results = [];

            trs.forEach((tr, rIdx) => {
                const tds = Array.from(tr.querySelectorAll('td'));
                if (tds.length < 3) return;

                const formName = (tds[0] ? tds[0].innerText : '').trim();
                const formDate = (tds[1] ? tds[1].innerText : '').trim();
                const user = (tds[2] ? tds[2].innerText : '').trim();
                const status = (tds[3] ? tds[3].innerText : '').trim();
                const rowText = (tr.innerText || '').replace(/\\s+/g, ' ').trim();

                results.push({
                    form: formName,
                    date: formDate,
                    user: user,
                    status: status,
                    rowText: rowText
                });
            });

            return results;
        }""", {"idx": ep_idx})

        for row in ep_rows:
            row["episode"] = ep_info["title"]
        all_chart_rows.extend(ep_rows)

        episode_latest = select_newest_completed_note(ep_rows, doc_type, min_target_dt)
        if episode_latest:
            print(
                f"    Found eligible completed / sent-to-office '{doc_type}' "
                f"note(s); newest in this episode: {episode_latest['date_str']}."
            )
        else:
            print(f"    No eligible completed / sent-to-office '{doc_type}' in this episode.")

    latest = select_newest_completed_note(all_chart_rows, doc_type, min_target_dt)
    if latest:
        print(
            f"[OK] Selected globally newest eligible source across all {episode_count} "
            f"episode(s): {latest['date_str']} (User: {latest['user']}, "
            f"Episode: {latest['episode']})"
        )
        return latest["date_str"]

    print(f"--> [NOTICE] No completed / sent-to-office '{doc_type}' found in any episode for '{patient_name}'.")
    return ""


def main():
    parser = argparse.ArgumentParser(
        description="Devero Tasks Generator - Scrape pending forms from Devero Home / Agent Menu and create tasks_note.json."
    )
    parser.add_argument(
        "-o", "--output",
        default="tasks_note.json",
        metavar="FILE",
        help="Path to output JSON file (default: tasks_note.json)"
    )
    parser.add_argument(
        "-d", "--doc-type",
        default="Skilled Nurse Visit Note",
        help="Document type form name to filter for (default: 'Skilled Nurse Visit Note')"
    )
    parser.add_argument(
        "-p", "--patient",
        action="append",
        default=[],
        metavar="NAME",
        help="Patient name filter (case-insensitive partial match); repeat to match any name"
    )
    parser.add_argument(
        "-c", "--caregiver",
        default="",
        help="Optional caregiver / user name filter"
    )
    parser.add_argument(
        "--no-lookup",
        action="store_true",
        help="Skip navigating to patient charts to auto-lookup source note dates"
    )
    parser.add_argument(
        "--default-source-date",
        default="",
        help="Default source date to use if chart lookup is skipped or not found"
    )
    parser.add_argument(
        "--cdp-url",
        default=DEFAULT_CDP_URL,
        help=f"CDP endpoint URL (default: {DEFAULT_CDP_URL})"
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        default=DEVERO_HEADLESS,
        help="Run browser in headless mode"
    )
    args = parser.parse_args()

    output_file = args.output
    doc_type_filter = args.doc_type
    caregiver_filter = args.caregiver
    lookup_source = not args.no_lookup
    default_source_date = args.default_source_date
    cdp_url = args.cdp_url
    headless = args.headless

    print("==================================================")
    print("Devero Task Generator (devero_tasks.py)")
    print(f"Target Doc Type: '{doc_type_filter}'")
    if caregiver_filter:
        print(f"Caregiver Filter: '{caregiver_filter}'")
    if args.patient:
        print(f"Patient Filters (OR): {args.patient}")
    print(f"Output File:     '{output_file}'")
    print(f"Auto-Lookup:     {lookup_source}")
    print("==================================================")

    with sync_playwright() as p:
        context = None

        if cdp_url:
            try:
                print(f"Connecting to existing Chrome on {cdp_url}...")
                browser = p.chromium.connect_over_cdp(cdp_url)
                context = browser.contexts[0]
                print("Connected to active Chrome session!")
            except Exception as e:
                print(f"CDP connection notice: {e}")
                context = None

        if not context:
            print(f"Auto-launching persistent Chrome context (Headless={headless})...")
            launch_args = ["--no-first-run", "--no-default-browser-check"]
            try:
                context = p.chromium.launch_persistent_context(
                    user_data_dir=PROFILE_DIR,
                    headless=headless,
                    channel="chrome",
                    args=launch_args
                )
            except Exception:
                time.sleep(1.5)
                try:
                    context = p.chromium.launch_persistent_context(
                        user_data_dir=PROFILE_DIR,
                        headless=headless,
                        args=launch_args
                    )
                except Exception:
                    context = p.chromium.launch_persistent_context(
                        user_data_dir=PROFILE_DIR + "_run",
                        headless=headless,
                        args=launch_args
                    )
            print(f"Chrome context launched! Session profile: {PROFILE_DIR}")

        main_page = context.pages[0] if context.pages else context.new_page()

        if "devero.com" not in main_page.url:
            print(f"--> Navigating to Devero base URL: {DEVERO_URL}")
            try:
                main_page.goto(DEVERO_URL, wait_until="domcontentloaded", timeout=30000)
            except Exception as e:
                print(f"--> Navigation notice: {e}")
            main_page.wait_for_timeout(2000)

        base_url = extract_base_url(main_page.url)

        # Step 0: Handle Authentication
        automated_login(main_page, base_url)

        # Step 1: Wait for Agent Menu and Pending table to populate
        wait_for_agent_menu_and_pending_table(main_page, timeout_sec=25)

        # Step 2: Scrape Pending table rows matching doc_type
        print(f"\n--> Harvesting '{doc_type_filter}' forms from Pending table...")
        pending_rows = scrape_all_pending_pages(main_page, target_doc_type=doc_type_filter, caregiver_filter=caregiver_filter, patient_filters=args.patient)

        if not pending_rows:
            print(f"\n[WARNING] No pending forms matching '{doc_type_filter}' were found on the dashboard.")
            print(f"Writing empty task list to '{output_file}'...")
            with open(output_file, "w", encoding="utf-8") as f:
                json.dump([], f, indent=2)
            return

        print(f"\n[OK] Successfully scraped {len(pending_rows)} pending form(s):")
        for idx, row in enumerate(pending_rows, 1):
            print(f"  [{idx:02d}] {row['patient']} | {row['target_date']} | {row['caregiver']}")

        # Step 4: Group target dates by patient and lookup source note dates
        patient_source_dates = {}
        if lookup_source:
            # Group rows by patient
            patient_targets = {}
            for row in pending_rows:
                pat = row["patient"]
                if pat not in patient_targets:
                    patient_targets[pat] = []
                patient_targets[pat].append(row["target_date"])

            print(f"\n==================================================")
            print(f"Looking up Source Note dates for {len(patient_targets)} unique patient(s)...")
            print(f"==================================================")

            for pat, dates in patient_targets.items():
                src_date = lookup_patient_source_date(
                    main_page,
                    patient_name=pat,
                    base_url=base_url,
                    doc_type=doc_type_filter,
                    target_dates=dates
                )
                patient_source_dates[pat] = src_date or default_source_date

            # Return to Home / Agent Menu
            print("\n--> Returning to Home / Agent Menu...")
            try:
                main_page.goto(f"{base_url}/app#/agentMenu", wait_until="domcontentloaded", timeout=20000)
            except Exception:
                pass
            main_page.wait_for_timeout(2000)
        else:
            for row in pending_rows:
                pat = row["patient"]
                patient_source_dates[pat] = default_source_date

        # Step 5: Build final task items list
        final_tasks = []
        for row in pending_rows:
            pat = row["patient"]
            src_date = patient_source_dates.get(pat, default_source_date)
            task_item = {
                "patient": pat,
                "caregiver": row["caregiver"],
                "doc_type": row["form"] or doc_type_filter,
                "source_date": src_date,
                "target_date": row["target_date"]
            }
            final_tasks.append(task_item)

        # Sort tasks chronologically by patient, then by target_date
        def task_sort_key(t):
            dt = parse_date_safe(t["target_date"])
            ts = dt.timestamp() if dt else 0
            return (t["patient"], ts)

        final_tasks.sort(key=task_sort_key)

        # Step 6: Write to JSON file
        print(f"\n--> Saving {len(final_tasks)} task items to '{output_file}'...")
        with open(output_file, "w", encoding="utf-8") as f:
            f.write("[\n")
            for i, task in enumerate(final_tasks):
                comma = "," if i < len(final_tasks) - 1 else ""
                f.write(f"  {json.dumps(task)}{comma}\n")
            f.write("]\n")

        print("\n==================================================")
        print(f"[SUCCESS] Generated '{output_file}' with {len(final_tasks)} task(s)!")
        print("==================================================")
        for idx, t in enumerate(final_tasks, 1):
            src_display = t['source_date'] if t['source_date'] else "[NOT FOUND - PLEASE FILL]"
            print(f"Task #{idx:02d}: {t['patient']}")
            print(f"   Caregiver:   {t['caregiver']}")
            print(f"   Doc Type:    {t['doc_type']}")
            print(f"   Source Date: {src_display}")
            print(f"   Target Date: {t['target_date']}")
            print("--------------------------------------------------")


if __name__ == "__main__":
    main()
