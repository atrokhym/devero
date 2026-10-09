import argparse
import json
import os
import sys
import time
import re
from datetime import datetime
from urllib.parse import urlparse
from dotenv import load_dotenv
from playwright.sync_api import Error as PlaywrightError, sync_playwright
from devero_form_opening import wait_for_form_inputs, wait_for_opened_form_tab
from verifier.verify_oasis import wait_for_page_extraction_ready

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
SOURCE_DOC_TYPE = "Skilled Nurse Visit Note"

PROFILE_DIR = os.path.join(os.path.expanduser("~"), ".devero_copier_profile")

JS_EXTRACT_FIELDS = """
() => {
    const fields = {};

    function cleanKey(str) {
        if (!str) return '';
        return str.toLowerCase().replace(/[^a-z0-9]/g, '');
    }

    function scanDoc(doc, prefix = '') {
        if (!doc) return;

        const selectors = 'input, textarea, select, cmwf-textbox, cmwf-textarea, cmwf-text-display, cmwf-cop, cmwf-integrated-action, cmwf-dropdown, ntst-select, [data-field-id], [ng-model]';
        const elements = doc.querySelectorAll(selectors);

        elements.forEach((el, index) => {
            // Visit-time widgets reuse outputModel.label; extract them separately.
            if (el.closest('.timeDropdown')) return;
            const tag = el.tagName.toLowerCase();
            const type = (el.type || '').toLowerCase();

            const keys = [
                el.name,
                el.id,
                el.getAttribute('name'),
                el.getAttribute('ng-model'),
                el.getAttribute('data-field-id'),
                el.getAttribute('aria-label')
            ].filter(Boolean);

            let parentCw = el.closest ? el.closest('cmwf-textbox, cmwf-textarea, cmwf-text-display, cmwf-cop, cmwf-integrated-action, cmwf-dropdown, ntst-select') : null;
            if (parentCw && parentCw !== el) {
                if (parentCw.id) keys.push(parentCw.id);
                if (parentCw.getAttribute('name')) keys.push(parentCw.getAttribute('name'));
                if (parentCw.getAttribute('ng-model')) keys.push(parentCw.getAttribute('ng-model'));
                if (parentCw.getAttribute('data-field-id')) keys.push(parentCw.getAttribute('data-field-id'));
            }

            const fallbackKey = `pos_${index}_${tag}_${type}`;
            const primaryKey = keys[0] || fallbackKey;

            let item = null;
            if (type === 'checkbox') {
                item = {
                    kind: 'checkbox',
                    checked: el.checked,
                    value: el.value,
                    keys: keys,
                    index: index
                };
            } else if (type === 'radio') {
                if (el.checked) {
                    item = {
                        kind: 'radio',
                        value: el.value,
                        keys: keys,
                        index: index
                    };
                }
            } else if (tag === 'select') {
                item = {
                    kind: 'select',
                    value: el.value,
                    keys: keys,
                    index: index
                };
            } else if (tag === 'cmwf-dropdown' || tag === 'ntst-select') {
                let val = el.value;
                const selOpt = el.querySelector('ntst-option[selected], ntst-option[aria-selected="true"], ntst-option.selected');
                if (selOpt) {
                    val = selOpt.getAttribute('value') || selOpt.getAttribute('display') || selOpt.innerText.trim();
                }
                if (!val) {
                    const span = el.querySelector('span.f-bold, span');
                    if (span && span.innerText.trim() && span.innerText.trim() !== '(Select)') {
                        val = span.innerText.trim();
                    }
                }
                if (val) {
                    item = {
                        kind: 'select',
                        value: val,
                        keys: keys,
                        index: index
                    };
                }
            } else if (tag === 'cmwf-text-display' || tag === 'cmwf-cop') {
                const val = (el.innerText || el.textContent || '').trim();
                if (val) {
                    item = {
                        kind: 'text',
                        value: val,
                        keys: keys,
                        index: index
                    };
                }
            } else if (tag === 'cmwf-textbox' || tag === 'cmwf-textarea') {
                let val = el.value;
                const innerInp = el.querySelector('input, textarea');
                if (innerInp && innerInp.value) val = innerInp.value;
                if (!val) val = (el.innerText || el.textContent || '').trim();
                if (val) {
                    item = {
                        kind: 'text',
                        value: val,
                        keys: keys,
                        index: index
                    };
                }
            } else {
                let val = el.value;
                if ((val === undefined || val === null || val === '') && el.innerText) {
                    val = el.innerText.trim();
                }
                item = {
                    kind: 'text',
                    value: val,
                    keys: keys,
                    index: index
                };
            }

            if (item && item.value !== undefined && item.value !== null) {
                fields[primaryKey] = item;
                fields[fallbackKey] = item;
                keys.forEach(k => {
                    fields[k] = item;
                    const cleaned = cleanKey(k);
                    if (cleaned) fields[cleaned] = item;
                });
            }
        });

        doc.querySelectorAll('.timeDropdown').forEach(el => {
            const label = (el.parentElement.querySelector('label')?.innerText || '').trim().toLowerCase();
            const key = label === 'time in:' ? 'TimeIn' : label === 'time out:' ? 'TimeOut' : null;
            const value = (el.querySelector('input')?.value || el.querySelector('.print-text')?.innerText || '').trim();
            if (key && value && !['(Unspecified)', '(Select)'].includes(value)) {
                fields[key] = {kind: 'text', value};
            }
        });

        const editables = doc.querySelectorAll('[contenteditable="true"]');
        editables.forEach((el, index) => {
            const key = el.id || el.getAttribute('ng-model') || (`editable_${index}`);
            fields[key] = {
                kind: 'contenteditable',
                value: el.innerHTML
            };
        });

        const iframes = doc.querySelectorAll('iframe');
        iframes.forEach((iframe, i) => {
            try {
                if (iframe.contentDocument) {
                    scanDoc(iframe.contentDocument, prefix + `iframe_${i}_`);
                }
            } catch (e) {}
        });
    }

    scanDoc(document);
    return fields;
}
"""

JS_INJECT_FIELDS = """
(fieldMap) => {
    let stats = { updated: 0, skipped: 0, total: Object.keys(fieldMap).length };

    function cleanKey(str) {
        if (!str) return '';
        return str.toLowerCase().replace(/[^a-z0-9]/g, '');
    }

    function hasValue(el) {
        if (!el) return false;
        const tag = el.tagName.toLowerCase();
        const type = (el.type || 'text').toLowerCase();

        if (type === 'checkbox' || type === 'radio') {
            return el.checked;
        }
        if (tag === 'select') {
            return el.value !== '' && el.value !== '?' && el.value !== null && el.value !== '0';
        }
        if (tag === 'cmwf-text-display' || tag === 'cmwf-cop') {
            const txt = (el.innerText || el.textContent || '').trim();
            return txt !== '';
        }
        if (tag === 'cmwf-textbox' || tag === 'cmwf-textarea') {
            const innerInp = el.querySelector('input, textarea');
            if (innerInp && innerInp.value && innerInp.value.trim() !== '') return true;
            return el.value && el.value.trim() !== '';
        }
        if (tag === 'cmwf-dropdown' || tag === 'ntst-select') {
            let val = el.value;
            const selOpt = el.querySelector('ntst-option[selected], ntst-option[aria-selected="true"], ntst-option.selected');
            if (selOpt) {
                val = selOpt.getAttribute('value') || selOpt.getAttribute('display') || selOpt.innerText.trim();
            }
            if (!val) {
                const span = el.querySelector('span.f-bold, span');
                if (span && span.innerText.trim() && span.innerText.trim() !== '(Select)') {
                    val = span.innerText.trim();
                }
            }
            return !!(val && val.trim() !== '' && val.trim() !== '(Select)');
        }
        return el.value && el.value.trim() !== '';
    }

    function formatTimeValue(val, type) {
        if (!val) return '';
        if (type === 'time') {
            const match = val.match(/(\\d{1,2}):(\\d{2})\\s*(AM|PM)?/i);
            if (match) {
                let hh = parseInt(match[1], 10);
                const mm = match[2];
                const ampm = match[3] ? match[3].toUpperCase() : null;
                if (ampm === 'PM' && hh < 12) hh += 12;
                if (ampm === 'AM' && hh === 12) hh = 0;
                const hhStr = hh < 10 ? '0' + hh : '' + hh;
                return `${hhStr}:${mm}`;
            }
        }
        return val;
    }

    function triggerEvents(el, doc) {
        try {
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
            el.dispatchEvent(new Event('blur', { bubbles: true }));
        } catch(e) {}

        const win = doc.defaultView || window;
        if (win.angular) {
            try {
                const ngEl = win.angular.element(el);
                ngEl.triggerHandler('input');
                ngEl.triggerHandler('change');
                ngEl.triggerHandler('blur');
            } catch (e) {}
        }
    }

    function syncAngularScope(el, doc, val, keys) {
        const win = doc.defaultView || window;
        if (!win.angular) return;
        try {
            const ngEl = win.angular.element(el);
            const scope = ngEl.scope();
            if (!scope) return;

            let updatedScope = false;
            keys.forEach(k => {
                if (!k) return;
                if (k in scope) {
                    scope[k] = val;
                    updatedScope = true;
                }
                if (scope.formData && (k in scope.formData)) {
                    scope.formData[k] = val;
                    updatedScope = true;
                }
                const ngModel = el.getAttribute ? el.getAttribute('ng-model') : null;
                if (ngModel && ngModel in scope) {
                    scope[ngModel] = val;
                    updatedScope = true;
                }
            });
            if (updatedScope && !scope.$$phase) {
                scope.$evalAsync();
            }
        } catch(e) {}
    }

    function injectDoc(doc) {
        if (!doc) return;

        const selectors = 'input, textarea, select, cmwf-textbox, cmwf-textarea, cmwf-text-display, cmwf-cop, cmwf-integrated-action, cmwf-dropdown, ntst-select, [data-field-id], [ng-model]';
        const elements = doc.querySelectorAll(selectors);

        elements.forEach((el, index) => {
            if (el.closest('.timeDropdown')) return;  // Native visit-time selection below.
            // RULE: Do NOT rewrite if target field already has data!
            if (hasValue(el)) {
                stats.skipped++;
                return;
            }

            const tag = el.tagName.toLowerCase();
            const type = (el.type || 'text').toLowerCase();

            const candidateKeys = [
                el.name,
                el.id,
                el.getAttribute('name'),
                el.getAttribute('ng-model'),
                el.getAttribute('data-field-id'),
                el.getAttribute('aria-label'),
                cleanKey(el.name),
                cleanKey(el.id),
                cleanKey(el.getAttribute('ng-model')),
                `pos_${index}_${tag}_${type}`
            ].filter(Boolean);

            let parentCw = el.closest ? el.closest('cmwf-textbox, cmwf-textarea, cmwf-text-display, cmwf-cop, cmwf-integrated-action, cmwf-dropdown, ntst-select') : null;
            if (parentCw && parentCw !== el) {
                if (parentCw.id) candidateKeys.push(parentCw.id, cleanKey(parentCw.id));
                if (parentCw.getAttribute('name')) candidateKeys.push(parentCw.getAttribute('name'), cleanKey(parentCw.getAttribute('name')));
                if (parentCw.getAttribute('ng-model')) candidateKeys.push(parentCw.getAttribute('ng-model'), cleanKey(parentCw.getAttribute('ng-model')));
                if (parentCw.getAttribute('data-field-id')) candidateKeys.push(parentCw.getAttribute('data-field-id'));
            }

            let matchedItem = null;
            for (let k of candidateKeys) {
                if (k in fieldMap) {
                    matchedItem = fieldMap[k];
                    break;
                }
            }

            if (!matchedItem) return;

            try {
                if (type === 'checkbox') {
                    if (matchedItem.checked) {
                        el.checked = true;
                        triggerEvents(el, doc);
                        syncAngularScope(el, doc, true, candidateKeys);
                        stats.updated++;
                    }
                } else if (type === 'radio') {
                    if (el.value === matchedItem.value) {
                        el.checked = true;
                        triggerEvents(el, doc);
                        syncAngularScope(el, doc, matchedItem.value, candidateKeys);
                        stats.updated++;
                    }
                } else if (tag === 'select') {
                    if (matchedItem.value) {
                        el.value = matchedItem.value;
                        triggerEvents(el, doc);
                        syncAngularScope(el, doc, matchedItem.value, candidateKeys);
                        stats.updated++;
                    }
                } else if (tag === 'cmwf-dropdown' || tag === 'ntst-select') {
                    const finalVal = matchedItem.value || '';
                    if (finalVal) {
                        el.value = finalVal;
                        
                        const ntst = tag === 'ntst-select' ? el : el.querySelector('ntst-select');
                        if (ntst) {
                            ntst.value = finalVal;
                            ntst.setAttribute('aria-activedescendant', 'option-' + finalVal);
                            const options = ntst.querySelectorAll('ntst-option');
                            options.forEach(opt => {
                                const optVal = opt.getAttribute('value') || opt.getAttribute('display') || opt.innerText.trim();
                                if (optVal === finalVal || optVal.toLowerCase() === finalVal.toLowerCase()) {
                                    opt.selected = true;
                                    opt.setAttribute('selected', '');
                                    opt.setAttribute('aria-selected', 'true');
                                    opt.classList.add('selected');
                                } else {
                                    opt.selected = false;
                                    opt.removeAttribute('selected');
                                    opt.setAttribute('aria-selected', 'false');
                                    opt.classList.remove('selected');
                                }
                            });
                            triggerEvents(ntst, doc);
                        }

                        const dropdown = tag === 'cmwf-dropdown' ? el : el.closest('cmwf-dropdown');
                        if (dropdown) {
                            let span = dropdown.querySelector('span.f-bold, span');
                            if (!span) {
                                span = doc.createElement('span');
                                span.className = 'f-bold';
                                dropdown.appendChild(span);
                            }
                            span.innerText = finalVal;
                            triggerEvents(dropdown, doc);
                        }

                        triggerEvents(el, doc);
                        syncAngularScope(el, doc, finalVal, candidateKeys);
                        stats.updated++;
                    }
                } else if (tag === 'cmwf-textbox' || tag === 'cmwf-textarea') {
                    const finalVal = matchedItem.value || '';
                    if (finalVal) {
                        el.value = finalVal;
                        const innerInp = el.querySelector('input, textarea');
                        if (innerInp) {
                            innerInp.value = finalVal;
                            triggerEvents(innerInp, doc);
                        }
                        triggerEvents(el, doc);
                        syncAngularScope(el, doc, finalVal, candidateKeys);
                        stats.updated++;
                    }
                } else if (tag === 'cmwf-text-display' || tag === 'cmwf-cop') {
                    const finalVal = matchedItem.value || '';
                    if (finalVal) {
                        el.innerText = finalVal;
                        triggerEvents(el, doc);
                        syncAngularScope(el, doc, finalVal, candidateKeys);
                        stats.updated++;
                    }
                } else {
                    const finalVal = formatTimeValue(matchedItem.value, type);
                    if (finalVal) {
                        el.value = finalVal;
                        triggerEvents(el, doc);
                        syncAngularScope(el, doc, finalVal, candidateKeys);
                        stats.updated++;
                    }
                }
            } catch(err) {
                console.error('Error populating field:', err);
            }
        });

        const editables = doc.querySelectorAll('[contenteditable="true"]');
        editables.forEach((el, index) => {
            if (el.innerHTML && el.innerHTML.trim() !== '') return;
            const key = el.id || el.getAttribute('ng-model') || (`editable_${index}`);
            if (key in fieldMap && fieldMap[key].kind === 'contenteditable') {
                el.innerHTML = fieldMap[key].value;
                triggerEvents(el, doc);
                stats.updated++;
            }
        });

        const iframes = doc.querySelectorAll('iframe');
        iframes.forEach((iframe) => {
            try {
                if (iframe.contentDocument) {
                    injectDoc(iframe.contentDocument);
                }
            } catch (e) {}
        });

        const win = doc.defaultView || window;
        if (win.angular) {
            try {
                const rootScope = win.angular.element(doc.body).injector().get('$rootScope');
                if (rootScope && !rootScope.$$phase) {
                    rootScope.$apply();
                }
            } catch (e) {}
        }
    }

    injectDoc(document);
    return stats;
}
"""

JS_CHECK_TEMPERATURE = """
() => {
    function checkDoc(doc) {
        if (!doc) return null;

        const selectors = [
            'input[name="cf_Temp_Txt"]',
            '[name="cf_Temp_Txt"]',
            '#cf_Temp_Txt',
            '[data-field-id="cf_Temp_Txt"]',
            '[ng-model*="cf_Temp_Txt"]'
        ];

        for (let sel of selectors) {
            const elements = doc.querySelectorAll(sel);
            for (let el of elements) {
                let val = el.value;
                if ((val === undefined || val === null) && el.getAttribute) {
                    val = el.getAttribute('value');
                }
                if (!val && el.innerText) {
                    val = el.innerText;
                }
                if (val && String(val).trim() !== '') {
                    return String(val).trim();
                }
            }
        }

        const inputs = doc.querySelectorAll('input, textarea');
        for (let el of inputs) {
            const nameAttr = (el.name || el.id || el.getAttribute('ng-model') || el.getAttribute('data-field-id') || '').toLowerCase();
            if (nameAttr.includes('temp_txt') || nameAttr === 'cf_temp_txt') {
                let val = el.value;
                if (val && String(val).trim() !== '') {
                    return String(val).trim();
                }
            }
        }

        const iframes = doc.querySelectorAll('iframe');
        for (let iframe of iframes) {
            try {
                if (iframe.contentDocument) {
                    const res = checkDoc(iframe.contentDocument);
                    if (res !== null) return res;
                }
            } catch (e) {}
        }
        return null;
    }
    return checkDoc(document);
}
"""


def extract_base_url(url_str):
    parsed = urlparse(url_str)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}"
    return DEVERO_URL

def parse_activity_id(value):
    if not value:
        return ""
    val_str = str(value).strip()
    match = re.search(r'activityId=(\d+)', val_str)
    if match:
        return match.group(1)
    if val_str.isdigit():
        return val_str
    return val_str

def automated_login(page, base_url):
    current_url = page.url.lower()

    # Check if page is on login page or contains login form inputs
    is_login_page = "login" in current_url or page.evaluate("""() => {
        return !!(document.querySelector('input[type="password"]') || document.querySelector('input[name="uName"]') || document.querySelector('input[name="username"]'));
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
            const btns = document.querySelectorAll('a, button, div, span');
            for (let btn of btns) {
                const txt = (btn.innerText || btn.title || '').trim();
                if (txt.includes('Select a Patient') || btn.getAttribute('data-automation-id') === 'patient-drop-down-search-input') {
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
                if (ngMod === 'search.text' || autoId === 'patient-drop-down-search-input' || inp.placeholder.includes('Search')) {
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



def go_to_next_page(page, current_page_num):
    target_next_num = current_page_num + 1

    # Check if page has numeric pagination controls (1, 2, 3...)
    nav_info = page.evaluate("""(targetNum) => {
        const btns = document.querySelectorAll('a, button');
        const numPageLinks = [];
        let targetLink = null;
        let anyNextDisabled = false;

        for (let btn of btns) {
            const txt = (btn.innerText || '').trim();
            // Collect all standalone page number links (e.g., 1, 2, 3)
            if (/^\\d+$/.test(txt) && btn.offsetWidth > 0) {
                const numVal = parseInt(txt, 10);
                if (!numPageLinks.includes(numVal)) {
                    numPageLinks.push(numVal);
                }
                if (numVal === targetNum) {
                    targetLink = btn;
                }
            }

            // Check if any Next link is marked disabled/activeNP
            if ((txt === 'Next >' || txt === 'Next') && btn.offsetWidth > 0) {
                const isDisabled = btn.disabled ||
                                   btn.classList.contains('disabled') ||
                                   btn.classList.contains('activeNP') ||
                                   (btn.parentElement && (btn.parentElement.classList.contains('disabled') || btn.parentElement.classList.contains('activeNP')));
                if (isDisabled) {
                    anyNextDisabled = true;
                }
            }
        }

        return {
            numPageLinks: numPageLinks,
            hasTargetNumLink: !!targetLink,
            anyNextDisabled: anyNextDisabled
        };
    }""", target_next_num)

    # 1. If numeric page links exist on the page:
    if nav_info and nav_info.get('numPageLinks'):
        num_links = nav_info['numPageLinks']
        max_page_found = max(num_links)
        if current_page_num >= max_page_found or not nav_info.get('hasTargetNumLink'):
            return False

        # Click the target page number link
        clicked = page.evaluate("""(targetNum) => {
            const btns = document.querySelectorAll('a, button');
            for (let btn of btns) {
                const txt = (btn.innerText || '').trim();
                if (txt === String(targetNum) && btn.offsetWidth > 0) {
                    btn.click();
                    return true;
                }
            }
            return false;
        }""", target_next_num)
        return clicked

    # 2. If no numeric page links, check if any Next link is disabled
    if nav_info and nav_info.get('anyNextDisabled'):
        return False

    # 3. Fallback: try clicking Next >
    clicked_next = page.evaluate("""() => {
        const nextBtns = Array.from(document.querySelectorAll('a, button')).filter(btn => {
            const txt = (btn.innerText || '').trim();
            return (txt === 'Next >' || txt === 'Next') && btn.offsetWidth > 0;
        });

        for (let btn of nextBtns) {
            const isDisabled = btn.disabled ||
                               btn.classList.contains('disabled') ||
                               btn.classList.contains('activeNP') ||
                               (btn.parentElement && (btn.parentElement.classList.contains('disabled') || btn.parentElement.classList.contains('activeNP')));
            if (!isDisabled) {
                btn.click();
                return true;
            }
        }
        return false;
    }""")

    return clicked_next

def extract_all_pages_source(page):
    """Iterates through all pages of Source Note to extract form data across all pages."""
    all_extracted = {}
    page_num = 1

    while True:
        wait_for_form_inputs(page, min_inputs=5)
        print(f"--> Extracting Source Note Page {page_num}...")
        page_fields = page.evaluate(JS_EXTRACT_FIELDS)
        if page_fields:
            all_extracted.update(page_fields)
            print(f"    [Page {page_num}] Harvested {len(page_fields)} items.")

        try:
            clicked_next = go_to_next_page(page, page_num)
            if clicked_next:
                print(f"--> Navigating to Source Note Page {page_num + 1}...")
                page.wait_for_timeout(3000)
                page_num += 1
                continue
        except Exception as e:
            print(f"--> Source next page check notice: {e}")

        break

    print(f"[OK] Completed Source Note extraction across {page_num} page(s). Total fields: {len(all_extracted)}")
    return all_extracted

def handle_warning_popups(page):
    """
    Detects and auto-confirms any actual error/warning/confirmation dialogs in Devero
    (e.g., 'Error', 'The following errors were found', 'Please select one or more', 'Warning', 'Do you want to continue?').
    Clicks 'OK' / 'Yes' / 'Close' / 'Dismiss' button on the popup to unblock the UI.
    """
    for _ in range(5):
        try:
            handled = page.evaluate("""() => {
                let count = 0;
                const visible = el => !!el && (el.offsetWidth > 0 || el.offsetHeight > 0 || el.getClientRects().length > 0);
                
                // Query all potential dialog / popup containers
                const dialogs = Array.from(document.querySelectorAll(
                    '.modal-dialog, .modal-content, .bootbox, .ui-dialog, .sweet-alert, [role="dialog"], [class*="alert-dialog"], [class*="error-dialog"]'
                )).filter(visible);

                dialogs.forEach(d => {
                    // Never close or target the main #formFramePopup form modal
                    if (d.id === 'formFramePopup' || d.closest('#formFramePopup')) {
                        const isNestedAlert = d.classList.contains('bootbox') ||
                                              d.classList.contains('sweet-alert') ||
                                              d.classList.contains('alert-dialog') ||
                                              d.classList.contains('error-dialog');
                        if (!isNestedAlert) return;
                    }

                    const titleEl = d.querySelector('.modal-title, .title, h1, h2, h3, h4, h5, header, [class*="title"], [class*="header"]');
                    const titleText = (titleEl && (titleEl.innerText || titleEl.textContent) || '').toLowerCase().trim();
                    const fullText = (d.innerText || d.textContent || '').toLowerCase();

                    const isPopup = (titleText && /^(error|warning|alert|notice|attention|validation error)/i.test(titleText)) ||
                                    fullText.includes('the following errors were found') ||
                                    fullText.includes('errors were found') ||
                                    fullText.includes('do you want to continue') ||
                                    fullText.includes('please select one or more') ||
                                    d.classList.contains('warning-popup') ||
                                    d.classList.contains('confirm-popup') ||
                                    d.classList.contains('error-popup') ||
                                    d.classList.contains('bootbox-alert') ||
                                    d.classList.contains('bootbox-confirm');

                    if (isPopup) {
                        const btns = Array.from(d.querySelectorAll('button, a, input[type="button"], input[type="submit"], .btn')).filter(visible);
                        const okBtn = btns.find(b => {
                            const txt = (b.innerText || b.value || '').trim().toLowerCase();
                            const dataAuto = (b.getAttribute('data-automation-id') || '').toLowerCase();
                            return txt === 'ok' ||
                                   txt === 'yes' ||
                                   txt === 'close' ||
                                   txt === 'dismiss' ||
                                   txt === 'i understand' ||
                                   txt === 'continue' ||
                                   dataAuto.includes('button-ok') ||
                                   dataAuto.includes('button-confirm') ||
                                   b.classList.contains('bootbox-accept') ||
                                   b.classList.contains('confirm-btn-ok') ||
                                   b.classList.contains('btn-primary');
                        });

                        if (okBtn) {
                            okBtn.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window, button: 0, buttons: 1 }));
                            okBtn.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window, button: 0 }));
                            okBtn.click();
                            count++;
                        } else {
                            const closeBtn = d.querySelector('button.close, .close, [data-dismiss="modal"], a.close-reveal-modal');
                            if (closeBtn && visible(closeBtn)) {
                                closeBtn.click();
                                count++;
                            }
                        }
                    }
                });
                return count;
            }""")
            if handled > 0:
                print(f"--> Auto-dismissed {handled} Error/Warning/Confirmation popup(s)!")
                page.wait_for_timeout(1000)
            else:
                break
        except Exception:
            break

def inject_composite_linked_forms(page):
    """
    On target note Page 2 (or any page containing composite sections):
    For each composite section ('eligibility', 'interventions', 'goals', 'discharge planning'):
    1. Locate and click section link on page 2.
    2. Wait for #formFramePopup modal to open.
    3. Expand category accordions and check all item row checkboxes.
    4. Click 'Insert to form' button in modal.
    5. Watch for and auto-confirm any warning popups ('FACE TO FACE ENCOUNTER', 'OK').
    6. Click 'Update' button in modal / call carePlanModuleController.updateModuleItems and close modal cleanly.
    """
    sections = ['eligibility', 'interventions', 'goals', 'discharge planning']
    for sec in sections:
        try:
            print(f"--> Auto-populating composite linked section: {sec.upper()}...")
            # Step 1: Scroll into view & click section link (handles cmwf-integrated-action elements)
            clicked = page.evaluate("""(secName) => {
                const secLower = secName.toLowerCase();
                const els = document.querySelectorAll('cmwf-integrated-action, .cmwf-integrated-action, a, button, span, .link, [onclick], [ng-click]');
                for (let el of els) {
                    const txt = (el.innerText || el.textContent || '').trim().toLowerCase();
                    const secAttr = (el.getAttribute('data-section') || '').toLowerCase();
                    const idAttr = (el.id || '').toLowerCase();
                    if (txt === secLower || secAttr === secLower || idAttr.includes(secLower)) {
                        try {
                            el.scrollIntoView({ block: 'center', inline: 'center' });
                        } catch(e){}
                        const targetEl = el.querySelector('span') || el;
                        targetEl.click();
                        targetEl.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
                        return true;
                    }
                }
                return false;
            }""", sec)

            if not clicked:
                continue

            page.wait_for_timeout(3000)
            handle_warning_popups(page)

            # Step 2: Expand accordions, check checkboxes, AND sync Angular scope properties
            page.evaluate("""() => {
                const modal = document.querySelector('#formFramePopup') || document.querySelector('.popup-modal.in');
                if (!modal) return;

                // Expand accordions
                const accordions = modal.querySelectorAll('.accordion-toggle, [ng-click*="isOpen"], .panel-heading');
                accordions.forEach(acc => {
                    if (acc.querySelector('.fa-chevron-right, .fa-angle-right, .fa-caret-right') || !acc.nextElementSibling || acc.nextElementSibling.offsetHeight === 0) {
                        try { acc.click(); } catch(e){}
                    }
                });

                // Click 'Select Active' if present
                const selBtns = Array.from(modal.querySelectorAll('button, a, .btn')).filter(b => {
                    const txt = (b.innerText || b.value || '').trim().toLowerCase();
                    return txt === 'select active' || (b.getAttribute('ng-click') || '').includes('selectActiveItems');
                });
                selBtns.forEach(btn => btn.click());

                // Select ONLY the first checkbox per category block/panel to avoid 3-time item repetitions
                const panels = modal.querySelectorAll('.panel, .accordion-group, [class*="category"], table');
                let checkedCount = 0;

                if (panels.length > 0) {
                    panels.forEach(p => {
                        const cbs = p.querySelectorAll('input[type="checkbox"]');
                        let alreadyChecked = false;
                        cbs.forEach(cb => { if (cb.checked) alreadyChecked = true; });

                        if (!alreadyChecked && cbs.length > 0) {
                            const firstCb = cbs[0];
                            firstCb.checked = true;
                            try {
                                const s = angular.element(firstCb).scope();
                                if (s) {
                                    for (let k in s) {
                                        if (s[k] && typeof s[k] === 'object') {
                                            if ('selected' in s[k]) s[k].selected = true;
                                            if ('isChecked' in s[k]) s[k].isChecked = true;
                                        }
                                    }
                                }
                            } catch(e){}
                            firstCb.dispatchEvent(new Event('click', { bubbles: true }));
                            firstCb.dispatchEvent(new Event('change', { bubbles: true }));
                            checkedCount++;
                        }
                    });
                }

                // Fallback: If 0 checkboxes were checked, check ONLY the first visible checkbox in modal
                if (checkedCount === 0) {
                    const cbs = modal.querySelectorAll('input[type="checkbox"]');
                    for (let cb of cbs) {
                        if (cb.offsetWidth > 0 || cb.offsetHeight > 0) {
                            cb.checked = true;
                            try {
                                const s = angular.element(cb).scope();
                                if (s) {
                                    for (let k in s) {
                                        if (s[k] && typeof s[k] === 'object') {
                                            if ('selected' in s[k]) s[k].selected = true;
                                            if ('isChecked' in s[k]) s[k].isChecked = true;
                                        }
                                    }
                                }
                            } catch(e){}
                            cb.dispatchEvent(new Event('click', { bubbles: true }));
                            cb.dispatchEvent(new Event('change', { bubbles: true }));
                            break;
                        }
                    }
                }

                // Trigger Angular Digest
                try {
                    const modalScope = angular.element(modal).scope();
                    if (modalScope && !modalScope.$$phase && modalScope.$root && !modalScope.$root.$$phase) {
                        modalScope.$root.$apply();
                    }
                } catch(e){}
            }""")
            page.wait_for_timeout(2000)

            # Step 3: Click 'Insert to form' button explicitly
            try:
                insert_loc = page.locator('#formFramePopup button:has-text("Insert to form"), .confirm-btn-ok').first
                if insert_loc.is_visible(timeout=3000):
                    insert_loc.click(force=True)
                else:
                    page.evaluate("""() => {
                        const modal = document.querySelector('#formFramePopup') || document.querySelector('.popup-modal.in');
                        if (!modal) return;
                        const btns = Array.from(modal.querySelectorAll('button, a, input[type="button"], .btn'));
                        const insertBtn = btns.find(b => (b.innerText || b.value || '').trim().toLowerCase() === 'insert to form');
                        if (insertBtn) insertBtn.click();
                    }""")
            except Exception:
                page.evaluate("""() => {
                    const modal = document.querySelector('#formFramePopup') || document.querySelector('.popup-modal.in');
                    if (!modal) return;
                    const btns = Array.from(modal.querySelectorAll('button, a, input[type="button"], .btn'));
                    const insertBtn = btns.find(b => (b.innerText || b.value || '').trim().toLowerCase() === 'insert to form');
                    if (insertBtn) insertBtn.click();
                }""")

            page.wait_for_timeout(2500)

            # Step 4: Handle Warning Popup (e.g. FACE TO FACE ENCOUNTER) by clicking OK
            handle_warning_popups(page)

            # Step 5: If modal is still open, click Update and close modal cleanly
            page.evaluate("""() => {
                const modal = document.querySelector('#formFramePopup') || document.querySelector('.popup-modal.in');
                if (!modal || modal.offsetWidth === 0 || modal.offsetHeight === 0) return;

                // Invoke Angular controller updateModuleItems
                try {
                    const scope = angular.element(modal).scope();
                    if (scope && scope.carePlanModuleController) {
                        scope.carePlanModuleController.updateModuleItems(null, false);
                    }
                    if (scope && scope.popupController && scope.popupController.close) {
                        scope.popupController.close();
                    }
                } catch(e){}

                const updateBtn = Array.from(modal.querySelectorAll('button, a, input[type="button"]')).find(b => {
                    const txt = (b.innerText || b.value || '').trim().toLowerCase();
                    return txt === 'update' || txt === 'save';
                });
                if (updateBtn) {
                    try { updateBtn.click(); } catch(e){}
                }

                const closeBtn = modal.querySelector('button.close, .close, [ng-click*="close"]');
                if (closeBtn) {
                    try { closeBtn.click(); } catch(e){}
                }
            }""")
            page.wait_for_timeout(2000)
            handle_warning_popups(page)
        except Exception as e:
            print(f"--> Composite section {sec} notice: {e}")

def parse_date_safe(date_str):
    """
    Parses MM/DD/YYYY date string into a datetime object. Returns None if parse fails.
    """
    if not date_str:
        return None
    cleaned = str(date_str).strip()
    match = re.search(r'\b(\d{1,2}/\d{1,2}/\d{4})\b', cleaned)
    if match:
        try:
            return datetime.strptime(match.group(1), "%m/%d/%Y")
        except Exception:
            pass
    return None

def expand_all_chart_episodes(page):
    """
    Expands all episode panels and clinical notes accordions on the Patient Chart
    so that all historical rows are available in the DOM.
    """
    try:
        page.evaluate("""() => {
            const panels = Array.from(document.querySelectorAll('.episode.epsd-pnl, .episode, [data-toggle="collapse"]'));
            for (let panel of panels) {
                const header = panel.querySelector('.episode-header, div[ng-click*="toggleEpisode"]');
                const isOpen = header ? header.classList.contains('open') : !!panel.querySelector('table');
                if (!isOpen && header) {
                    try { header.click(); } catch(e){}
                }
            }

            const elements = Array.from(document.querySelectorAll('tr, div, td, th, a, span, button'));
            for (let el of elements) {
                const txt = (el.innerText || el.textContent || '').trim();
                if (txt.includes('Episode #') && el.children.length < 6) {
                    try {
                        el.scrollIntoView({ block: 'center' });
                        el.click();
                    } catch(e){}
                }
                if ((txt === 'Nurses Clinical Notes' || txt === 'Clinical Notes') && el.children.length < 3) {
                    try { el.click(); } catch(e){}
                }
            }
        }""")
        page.wait_for_timeout(2000)
    except Exception as e:
        print(f"--> Episode expansion notice: {e}")

def extract_chart_rows(page):
    """
    Extracts all table rows from the Patient Chart that contain links to documents.
    """
    return page.evaluate("""() => {
        const rows = Array.from(document.querySelectorAll('table tr, .document-row'));
        const results = [];
        for (let i = 0; i < rows.length; i++) {
            const tr = rows[i];
            const tds = Array.from(tr.querySelectorAll('td'));
            if (tds.length < 3) continue;

            const link = tr.querySelector('.dbDrivenForm-new-window, .dbDrivenForm-inline-window, .standardform-new-window, .standardform-inline-window, a');
            if (!link) continue;

            const cellsText = tds.map(c => (c.innerText || '').trim());
            const fullRowText = cellsText.join(' ');

            const dateMatch = fullRowText.match(/\\b(\\d{1,2}\\/\\d{1,2}\\/\\d{4})\\b/);
            const dateStr = dateMatch ? dateMatch[1] : (tds[1] ? tds[1].innerText.trim() : '');

            results.push({
                rowIndex: i,
                formName: tds[0] ? tds[0].innerText.trim() : '',
                dateStr: dateStr,
                user: tds[2] ? tds[2].innerText.trim() : '',
                status: tds[3] ? tds[3].innerText.trim() : '',
                rowText: fullRowText,
                linkText: (link.innerText || '').trim()
            });
        }
        return results;
    }""")

def select_source_candidate(rows, doc_type, target_date, caregiver="", source_doc_type=""):
    """
    Selects the best source note candidate from scraped chart rows:
    - If no source mentioned in task file:
      1. Matches exactly 'Skilled Nurse Visit Note'.
      2. Matches caregiver (last name) if caregiver is specified.
      3. Strictly precedes target_date (date < target_date).
      4. Must NOT be 'pending'.
      5. Ranking rule:
         - Tier 1: Last 'Send-to-office' (status contains 'sent to office' or 'send to office')
         - Tier 2: If no 'Send-to-office', last 'complete' note (status contains 'completed' or 'complete')
    """
    target_dt = parse_date_safe(target_date)
    expected_doc = SOURCE_DOC_TYPE.lower()
    cg_last = (caregiver.split(",")[0].strip().lower()) if caregiver else ""

    send_to_office_candidates = []
    completed_candidates = []

    for row in rows:
        form_name = row.get("formName", "") or ""
        row_text = row.get("rowText", "") or ""
        row_text_lower = row_text.lower()
        form_name_lower = form_name.lower()
        status_text = (row.get("status", "") or "") + " " + row_text
        status_lower = status_text.lower().replace("-", " ")

        if form_name_lower.strip() != expected_doc:
            continue

        # Check caregiver match (last name)
        if cg_last and cg_last not in row_text_lower:
            continue

        # Exclude pending rows
        if "pending" in status_lower:
            continue

        # Parse date and enforce row_dt < target_dt
        date_str = row.get("dateStr", "") or ""
        row_dt = parse_date_safe(date_str) or parse_date_safe(row_text)
        if not row_dt:
            continue

        if target_dt and row_dt >= target_dt:
            continue

        cand = {
            **row,
            "row_dt": row_dt,
            "dateStr": row_dt.strftime("%m/%d/%Y"),
        }

        # Status check
        if "sent to office" in status_lower or "send to office" in status_lower:
            cand["tier"] = 1
            cand["tier_label"] = "Sent To Office"
            send_to_office_candidates.append(cand)
        elif "completed" in status_lower or "complete" in status_lower:
            cand["tier"] = 2
            cand["tier_label"] = "Completed"
            completed_candidates.append(cand)

    # Pick last (latest date) Send-to-office, if not, last complete note
    if send_to_office_candidates:
        return max(send_to_office_candidates, key=lambda c: (c["row_dt"], c["rowIndex"]))
    if completed_candidates:
        return max(completed_candidates, key=lambda c: (c["row_dt"], c["rowIndex"]))

    return None

def auto_detect_source_note(context, active_page, base_url, patient_name="", doc_type="", target_date="", caregiver="", source_doc_type=""):
    """
    Navigates to Patient Chart, scans all rows across expanded episodes,
    and opens the last 'Send-to-office' note (or if none, the last 'completed' note)
    prior to target_date.

    Returns dict with {page, dateStr, formName, status, user} or None.
    """
    select_patient_and_go_to_chart(active_page, patient_name, base_url)
    active_page.wait_for_timeout(3000)

    # Expand all collapsed episodes
    expand_all_chart_episodes(active_page)

    rows = extract_chart_rows(active_page)
    if not rows:
        expand_all_chart_episodes(active_page)
        rows = extract_chart_rows(active_page)

    best = select_source_candidate(rows, doc_type=doc_type, target_date=target_date, caregiver=caregiver, source_doc_type=source_doc_type)
    if not best:
        return None

    print(f"--> Auto-selected Source Note: '{best['formName']}' ({best['dateStr']}) [{best['tier_label']}] by '{best['user']}'")

    known_pages = list(context.pages)
    chart_url = active_page.url

    click_js = """(args) => {
        const rows = Array.from(document.querySelectorAll('table tr, .document-row'));
        const matchesSourceType = row =>
            (row.querySelector('td')?.innerText || '').trim().toLowerCase() === args.sourceDocType.toLowerCase();
        let targetRow = rows[args.rowIndex];

        if (targetRow) {
            const fullText = (targetRow.innerText || '').toLowerCase();
            if (!matchesSourceType(targetRow) || !fullText.includes(args.dateStr.toLowerCase())) {
                targetRow = null;
            }
        }

        if (!targetRow) {
            for (let tr of rows) {
                const fullText = (tr.innerText || '').toLowerCase();
                if (matchesSourceType(tr) && fullText.includes(args.dateStr.toLowerCase()) &&
                    (args.tier === 1 ? (fullText.includes('sent to office') || fullText.includes('send to office')) :
                                       (fullText.includes('completed') || fullText.includes('complete')))) {
                    targetRow = tr;
                    break;
                }
            }
        }

        if (targetRow) {
            const link = targetRow.querySelector('.dbDrivenForm-new-window, .dbDrivenForm-inline-window, .standardform-new-window, .standardform-inline-window, a');
            if (link) {
                link.scrollIntoView({ block: 'center' });
                link.click();
                return true;
            }
        }
        return false;
    }"""

    clicked = active_page.evaluate(click_js, {"rowIndex": best["rowIndex"], "dateStr": best["dateStr"], "tier": best["tier"], "sourceDocType": SOURCE_DOC_TYPE})
    if not clicked:
        return None

    source_page = wait_for_opened_form_tab(context, active_page, known_pages, chart_url)
    if not source_page:
        print("--> [NOTICE] Source row clicked, but no form tab opened within 20 seconds.")
        return None

    return {
        "page": source_page,
        "dateStr": best["dateStr"],
        "formName": best["formName"],
        "status": best["tier_label"],
        "user": best["user"],
    }

def open_note_by_ui_click(context, active_page, base_url, patient_name="", doc_type="", act_id="", note_date="", caregiver="", is_target=False, source_doc_type="", target_date=""):
    """
    Finds and opens a note link using Devero Patient Chart UI (/app#/patientCharts).

    Target Note (is_target=True):
    Strictly matches data rows by:
    1. Document Type (e.g. 'OASIS-E2 Nurse Resumption of Care')
    2. Target Note Date (e.g. '08/11/2026')
    3. Caregiver MUST match tasks.jsonl (e.g. 'Example, Clinician')
    4. Status MUST match: 'Pending'

    Source Note (is_target=False):
    If note_date is not specified:
      Auto-detects last 'Send-to-office' note (or if none, last 'completed' note) prior to target_date.
    If note_date is specified:
      Matches data rows by:
      1. Source Note Date (e.g. '07/16/2026')
      2. Prefers the caregiver in tasks.jsonl, but accepts another if none match
      3. Document Type MUST be 'Skilled Nurse Visit Note'
      4. Status MUST match: 'Completed' or 'Sent To Office'
    """
    source_doc_type = SOURCE_DOC_TYPE
    if not is_target and not note_date and not act_id:
        res = auto_detect_source_note(
            context=context,
            active_page=active_page,
            base_url=base_url,
            patient_name=patient_name,
            doc_type=doc_type,
            target_date=target_date,
            caregiver=caregiver,
            source_doc_type=source_doc_type,
        )
        if res and res.get("page"):
            return res["page"]
        return None

    # Always select patient & open Patient Chart (/app#/patientCharts)
    select_patient_and_go_to_chart(active_page, patient_name, base_url)
    active_page.wait_for_timeout(3000)

    required_status = "pending" if is_target else "completed | sent to office"
    known_pages = list(context.pages)
    chart_url = active_page.url

    # Helper JS function to search for matching note row link
    find_and_click_row_js = """(criteria) => {
        const docLower = (criteria.docType || '').trim().toLowerCase();
        const srcDocLower = (criteria.sourceDocType || '').trim().toLowerCase();
        const dateLower = (criteria.noteDate || '').trim().toLowerCase();
        const cgLower = (criteria.caregiver || '').split(',')[0].trim().toLowerCase();
        const reqStatus = (criteria.requiredStatus || '').trim().toLowerCase();
        const actId = criteria.actId ? String(criteria.actId).trim() : '';
        const isTarget = criteria.isTarget;

        const rows = Array.from(document.querySelectorAll('table tr, .document-row'));

        for (let tr of rows) {
            const cells = Array.from(tr.querySelectorAll('td')).map(c => (c.innerText || '').trim().toLowerCase());
            if (cells.length < 3) continue;

            const fullRowText = cells.join(' ');

            // Target Note requires matching target doc_type.
            // Source Note always requires the exact source document type.
            let matchesDocType = true;
            if (isTarget) {
                matchesDocType = !docLower || fullRowText.includes(docLower);
            } else {
                matchesDocType = cells[0] === srcDocLower;
            }

            const matchesDate = dateLower ? fullRowText.includes(dateLower) : true;
            // Caregiver is required for target notes and preferred for dated source notes.
            const matchesCaregiver = cgLower ? fullRowText.includes(cgLower) : true;

            // Target Note strictly matches 'pending' status.
            // Source Note strictly matches 'completed' or 'sent to office' status.
            let matchesStatus = true;
            if (isTarget) {
                matchesStatus = reqStatus ? fullRowText.includes(reqStatus) : fullRowText.includes('pending');
            } else {
                matchesStatus = fullRowText.includes('completed') || fullRowText.includes('sent to office');
            }

            const matchesId = !actId || fullRowText.includes(actId);

            if (matchesDocType && matchesDate && matchesCaregiver && matchesStatus && matchesId) {
                const link = tr.querySelector('.dbDrivenForm-new-window, .dbDrivenForm-inline-window, .standardform-new-window, .standardform-inline-window, a');
                if (link) {
                    link.scrollIntoView({ block: 'center' });
                    link.click();
                    return true;
                }
            }
        }

        return false;
    }"""

    search_args = {
        'docType': doc_type,
        'sourceDocType': source_doc_type,
        'actId': act_id,
        'noteDate': note_date,
        'caregiver': caregiver,
        'requiredStatus': required_status,
        'isTarget': is_target
    }

    # Attempt 1: Search in currently visible episode
    clicked = active_page.evaluate(find_and_click_row_js, search_args)

    # Attempt 2: If not found, expand all collapsed episodes on Patient Chart and retry
    if not clicked:
        print(f"--> Note link for date '{note_date}' not found in default episode. Expanding all collapsed episodes on Patient Chart...")
        active_page.evaluate("""() => {
            const elements = Array.from(document.querySelectorAll('tr, div, td, th, a, span, button, [data-toggle="collapse"]'));
            for (let el of elements) {
                const txt = (el.innerText || el.textContent || '').trim();
                if (txt.includes('Episode #') && el.children.length < 6) {
                    try {
                        el.scrollIntoView({ block: 'center' });
                        el.click();
                    } catch(e){}
                }
            }
        }""")
        active_page.wait_for_timeout(2500)
        clicked = active_page.evaluate(find_and_click_row_js, search_args)

    # A dated source may have been completed by a different caregiver.
    if not clicked and not is_target and note_date and caregiver:
        search_args['caregiver'] = ''
        clicked = active_page.evaluate(find_and_click_row_js, search_args)
        if clicked:
            print(f"--> Source note for '{note_date}' found with a different caregiver.")

    if clicked:
        status_tag = "PENDING" if is_target else "COMPLETED / SENT TO OFFICE"
        doc_desc = f"'{doc_type}'" if is_target else (f"'{source_doc_type}'" if source_doc_type else "Note")
        cg_desc = f" by '{search_args['caregiver']}'" if search_args['caregiver'] else ""
        print(f"--> Clicked note link for {doc_desc} ({note_date}) [{status_tag}]{cg_desc} on Patient Chart!")
        opened = wait_for_opened_form_tab(context, active_page, known_pages, chart_url)
        if opened:
            return opened
        print("--> [NOTICE] Clicked note row, but no form tab opened within 20 seconds.")
        return None

    status_tag = "PENDING" if is_target else "COMPLETED / SENT TO OFFICE"
    doc_desc = f"'{doc_type}'" if is_target else (f"'{source_doc_type}'" if source_doc_type else "Note")
    cg_desc = f" by '{search_args['caregiver']}'" if search_args['caregiver'] else ""
    print(f"--> [NOTICE] No matching note row found on Patient Chart for {doc_desc} ({note_date}) [{status_tag}]{cg_desc}.")
    return None

def go_to_next_page(page, current_page_num):
    target_next_num = current_page_num + 1

    # Check if page has numeric pagination controls (1, 2, 3...)
    nav_info = page.evaluate("""(targetNum) => {
        const btns = document.querySelectorAll('a, button');
        const numPageLinks = [];
        let targetLink = null;
        let anyNextDisabled = false;

        for (let btn of btns) {
            const txt = (btn.innerText || '').trim();
            // Collect all standalone page number links (e.g., 1, 2, 3)
            if (/^\\d+$/.test(txt) && btn.offsetWidth > 0) {
                const numVal = parseInt(txt, 10);
                if (!numPageLinks.includes(numVal)) {
                    numPageLinks.push(numVal);
                }
                if (numVal === targetNum) {
                    targetLink = btn;
                }
            }

            // Check if any Next link is marked disabled/activeNP
            if ((txt === 'Next >' || txt === 'Next') && btn.offsetWidth > 0) {
                const isDisabled = btn.disabled ||
                                   btn.classList.contains('disabled') ||
                                   btn.classList.contains('activeNP') ||
                                   (btn.parentElement && (btn.parentElement.classList.contains('disabled') || btn.parentElement.classList.contains('activeNP')));
                if (isDisabled) anyNextDisabled = true;
            }
        }

        return {
            hasNumericControls: numPageLinks.length > 0,
            hasTargetPage: targetLink !== null,
            nextDisabled: anyNextDisabled
        };
    }""", target_next_num)

    if nav_info and nav_info.get("hasNumericControls"):
        if not nav_info.get("hasTargetPage"):
            return False
        return page.evaluate("""(targetNum) => {
            const btns = document.querySelectorAll('a, button');
            for (let btn of btns) {
                const txt = (btn.innerText || '').trim();
                if (txt === String(targetNum) && btn.offsetWidth > 0) {
                    btn.click();
                    return true;
                }
            }
            return false;
        }""", target_next_num)

    if nav_info and nav_info.get("nextDisabled"):
        return False

    return page.evaluate("""() => {
        const btns = Array.from(document.querySelectorAll('a, button'));
        const nextBtn = btns.find(b => {
            const txt = (b.innerText || '').trim();
            const isDisabled = b.disabled || b.classList.contains('disabled') || b.classList.contains('activeNP') || (b.parentElement && (b.parentElement.classList.contains('disabled') || b.parentElement.classList.contains('activeNP')));
            return (txt === 'Next >' || txt === 'Next') && b.offsetWidth > 0 && !isDisabled;
        });
        if (nextBtn) {
            nextBtn.click();
            return true;
        }
        return false;
    }""")

def _save_target_note(page):
    """Send Save once; tolerate note closure only while the session survives."""
    context = page.context
    browser = context.browser

    def note_closed_only():
        return (page.is_closed()
                and (browser is None or browser.is_connected())
                and any(not tab.is_closed() for tab in context.pages))

    if page.is_closed():
        raise RuntimeError("Target note closed before Save; nothing was saved.")

    print("--> Clicking the note SAVE button...")
    handle = page.evaluate_handle("""() => {
        const btns = Array.from(document.querySelectorAll(
            'input[type="button"], input[type="submit"], button, a, .btn'
        ));
        for (const label of ['Save', 'Save & Close']) {
            const btn = btns.find(b => {
                const text = (b.value || b.innerText || '').trim();
                const ngClick = b.getAttribute('ng-click') || '';
                return text === label && !b.matches(':disabled') && b.getAttribute('aria-disabled') !== 'true'
                    && b.getClientRects().length > 0
                    && getComputedStyle(b).visibility !== 'hidden'
                    && !ngClick.includes('ReasonCodes') && !ngClick.includes('CPTCode')
                    && !b.closest('.modal-dialog, #formFramePopup');
            });
            if (btn) return btn;
        }
        return null;
    }""")
    button = handle.as_element()
    if button is None:
        handle.dispose()
        raise RuntimeError("Note Save button was not found or is not enabled; review the open note.")

    try:
        # Scroll before the click so a closure here cannot count as a Save.
        button.scroll_into_view_if_needed()
        try:
            button.click(timeout=10000)
            page.wait_for_timeout(3000)
            if not page.is_closed():
                handle_warning_popups(page)
        except PlaywrightError:
            if not note_closed_only():
                raise RuntimeError(
                    "Save outcome is unknown; the note or browser became unavailable. "
                    "Review the target before retrying."
                ) from None
        if (browser is not None and not browser.is_connected()) or not context.pages:
            raise RuntimeError("Browser/context closed during Save; save outcome is unknown.")
        if page.is_closed():
            print("--> Note tab closed after Save click; server persistence has not been verified.")
        else:
            print("--> Save click sent; note remains open.")
    finally:
        try:
            handle.dispose()
        except PlaywrightError:
            pass  # The note may have closed during Save.


def _canonical_visit_time(value):
    for fmt in ("%I:%M %p", "%I:%M%p", "%H:%M"):
        try:
            return datetime.strptime(str(value).strip().upper(), fmt).strftime("%I:%M %p").lstrip("0")
        except ValueError:
            pass
    raise RuntimeError("Source visit time has an invalid format; stopping before Save.")


def _copy_visit_times(page, fields):
    """Use the time picker's native selection or valid-input handler."""
    updated = 0
    found = set()
    for frame in page.frames:
        for name in ("TimeIn", "TimeOut"):
            value = (fields.get(name) or {}).get("value")
            if not value:
                continue
            picker = frame.locator(
                f'[time-picker][output-model*="timeObj"][output-model*="{name}"]:visible'
            ).first
            if not picker.count():
                continue
            found.add(name)
            field = picker.locator('input[ng-model="outputModel.label"]').first
            if field.input_value().strip() not in ("", "(Unspecified)", "(Select)"):
                continue
            label = _canonical_visit_time(value)
            option = picker.locator('li.list-of-options a').filter(
                has_text=re.compile(r'^\s*0?' + re.escape(label) + r'\s*$', re.I)
            ).first
            if option.count():
                field.click()
                option.click()
            else:
                # The directive's on-valid-input handler supports times between options.
                field.fill(label)
                field.press("Tab")
            valid = field.evaluate("""el => {
                const model = window.angular && angular.element(el).controller('ngModel');
                return !model || model.$valid !== false;
            }""")
            try:
                accepted = _canonical_visit_time(field.input_value())
            except RuntimeError:
                accepted = None
            if not valid or accepted != label:
                raise RuntimeError(f"Devero did not accept {name}; stopping before Save.")
            updated += 1
    for name in ("TimeIn", "TimeOut"):
        if (fields.get(name) or {}).get("value") and name not in found:
            if not any(frame.locator(f'input[name="{name}"], input[id="{name}"]').count()
                       for frame in page.frames):
                raise RuntimeError(f"Target {name} control was not found; stopping before Save.")
    return updated


def _fill_pedal_pulses(page):
    """Check pedal pulses and fill blank R/L values with the requested '+'."""
    updated = 0
    found = False
    for frame in page.frames:
        checkbox = frame.locator('input[name="crd_PedalPulse_Chk"]').first
        if not checkbox.count():
            continue
        found = True
        if not checkbox.is_checked():
            checkbox_id = checkbox.get_attribute("id")
            label = frame.locator(f'label[for="{checkbox_id}"]:visible').first if checkbox_id else None
            if label is not None and label.count():
                label.click()
            elif checkbox.is_visible():
                checkbox.set_checked(True)
            else:
                frame.locator('cmwf-checkbox[id="crd_PedalPulse_Chk"]').click()
            if not checkbox.is_checked():
                raise RuntimeError("Devero did not accept the pedal-pulses checkmark; stopping before Save.")
            updated += 1
        for name in ("crd_PulsesR_Txt", "crd_PulsesL_Txt"):
            field = frame.locator(f'input[name="{name}"]').first
            if not field.input_value().strip():
                field.fill("+")
                field.blur()
                updated += 1
    if not found:
        raise RuntimeError("Pedal-pulse controls were not found on Target Page 2; stopping before Save.")
    return updated


def inject_all_pages_target(page, extracted_fields, *, skip_if_temperature=True):
    """Copy all target pages; return None without writes when temperature is filled."""
    total_updated = 0
    total_skipped = 0
    page_num = 1
    narrative_names = (
        "int_IntervEducation_Txt", "int_ResponseInterv_Txt", "osn_OngoingSkilledNeed_Txt"
    )
    expected = [name for name in narrative_names
                if str((extracted_fields.get(name) or {}).get("value") or "").strip()]

    while True:
        if page_num == 2 and expected:
            # The Page 1 controls can remain in the DOM while Page 2 loads.
            selector = ", ".join(f'cmwf-textarea[id="{name}"]' for name in expected)
            page.wait_for_selector(selector, state="visible", timeout=30000)
        controls = wait_for_form_inputs(page, min_inputs=100, timeout_sec=30)
        ready = wait_for_page_extraction_ready(page, page_num, timeout_sec=30)
        if controls < 100 or not ready.get("ready"):
            raise RuntimeError(
                f"Target Page {page_num} did not finish loading before injection "
                f"({ready.get('control_count', controls)} controls; "
                f"pager active: {ready.get('pager_active', False)})."
            )
        if page_num == 1 and skip_if_temperature:
            for frame in page.frames:
                temp_val = frame.evaluate(JS_CHECK_TEMPERATURE)
                if temp_val:
                    print(f"[SKIP]: Target temperature is already filled ({temp_val}); no fields copied.")
                    return None
        print(f"--> Injecting Target Note Page {page_num}...")
        if page_num == 1:
            total_updated += _copy_visit_times(page, extracted_fields)
        if page_num == 2:
            total_updated += _fill_pedal_pulses(page)
        stats = page.evaluate(JS_INJECT_FIELDS, extracted_fields)
        up = stats.get('updated', 0) if stats else 0
        sk = stats.get('skipped', 0) if stats else 0
        total_updated += up
        total_skipped += sk
        print(f"    [Page {page_num}] Result: {up} updated, {sk} preserved.")

        # If on page 2 or higher, populate composite linked sections (Eligibility, Interventions, Goals)
        if page_num >= 2:
            inject_composite_linked_forms(page)

        try:
            clicked_next = go_to_next_page(page, page_num)
            if clicked_next:
                print(f"--> Navigating to Target Note Page {page_num + 1}...")
                page_num += 1
                continue
        except Exception as e:
            print(f"--> Target next page check notice: {e}")

        break

    # If only 1 page was processed, check if Page 2 exists for composite sections
    if page_num == 1:
        if go_to_next_page(page, 1):
            page.wait_for_timeout(3000)
            total_updated += _fill_pedal_pulses(page)
            inject_composite_linked_forms(page)

    # A populated source narrative must be present on the target before Save.
    if expected:
        present = set()
        for frame in page.frames:
            try:
                present.update(frame.evaluate("""(names) => names.filter(name => {
                    const field = document.querySelector(`textarea[name="${name}"], cmwf-textarea[id="${name}"]`);
                    return !!(field && String(field.value || field.querySelector?.('textarea')?.value || '').trim());
                })""", expected))
            except Exception:
                continue
        missing = sorted(set(expected) - present)
        if missing:
            raise RuntimeError(f"Target narrative fields still blank before Save: {', '.join(missing)}")

    # Save errors must stop the run before cleanup can discard unsaved fields.
    _save_target_note(page)

    return total_updated, total_skipped

def navigate_to_note(context, active_page, base_url, url_or_activity_id, is_target=False, patient_name="", doc_type="", note_date="", caregiver="", source_doc_type="", target_date=""):
    val_str = str(url_or_activity_id).strip()
    activity_id = parse_activity_id(val_str)
    note_label = "Target" if is_target else "Source"

    # Source URLs must resolve through the chart so document type is checked.
    if is_target and (val_str.startswith("http://") or val_str.startswith("https://")):
        print(f"--> Navigating directly to {note_label} Note URL: {val_str[:90]}...")
        try:
            active_page.goto(val_str, wait_until="domcontentloaded", timeout=30000)
            active_page.wait_for_timeout(2000)
            count = wait_for_form_inputs(active_page, min_inputs=15, timeout_sec=30)
            has_error = active_page.evaluate("""() => {
                const text = document.body ? document.body.innerText || '' : '';
                return text.includes('cannot be accessed directly') || text.includes('Access Denied');
            }""")
            if count >= 15 and not has_error:
                return active_page
        except Exception as e:
            print(f"--> Direct URL notice: {e}")

    # Option B: UI click via Patient Chart / Dashboard
    status_label = "PENDING" if is_target else "COMPLETED / SENT TO OFFICE"
    print(f"--> Opening {note_label} Note ({status_label}) via Devero Patient Chart UI...")
    navigated_page = open_note_by_ui_click(context, active_page, base_url, patient_name, doc_type, activity_id, note_date, caregiver, is_target, source_doc_type, target_date=target_date)

    if navigated_page:
        return navigated_page

    return None

def load_tasks_config(config_file):
    """Loads tasks.jsonl with support for // single-line comments, /* block comments */, trailing commas, and enabled/skip flags."""
    with open(config_file, "r", encoding="utf-8") as f:
        content = f.read()

    def remove_comments(text):
        def replacer(match):
            s = match.group(0)
            if s.startswith('/'):
                return ""
            else:
                return s
        pattern = re.compile(
            r'//.*?$|/\*.*?\*/|"(?:\\.|[^\\"])*"',
            re.DOTALL | re.MULTILINE
        )
        return re.sub(pattern, replacer, text)

    clean_content = remove_comments(content)
    clean_content = re.sub(r',\s*([\]}])', r'\1', clean_content)

    raw_tasks = json.loads(clean_content)
    active_tasks = []
    for t in raw_tasks:
        if t.get("enabled", True) is False or t.get("skip", False) is True:
            continue
        active_tasks.append(t)
    return active_tasks

def build_parser():
    parser = argparse.ArgumentParser(
        description="Devero Copier Note - Automate copying patient notes in Devero EHR."
    )
    parser.add_argument(
        "-t", "--tasks", "--config",
        dest="config_file",
        default=None,
        metavar="FILE",
        help="Path to the JSON tasks configuration file (default: tasks.jsonl)"
    )
    parser.add_argument(
        "tasks_file",
        nargs="?",
        default=None,
        metavar="FILE",
        help="Optional positional path to JSON tasks configuration file"
    )
    parser.add_argument(
        "--skip-if-temperature",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Skip a target note when its temperature is already filled (default: enabled)",
    )
    return parser


def main():
    args = build_parser().parse_args()

    config_file = args.config_file or args.tasks_file or "tasks.jsonl"

    if not os.path.exists(config_file):
        print(f"Error: Config file '{config_file}' not found.")
        sys.exit(1)

    tasks = load_tasks_config(config_file)

    with sync_playwright() as p:
        context = None

        if DEFAULT_CDP_URL:
            try:
                print(f"Connecting to existing Chrome on {DEFAULT_CDP_URL}...")
                browser = p.chromium.connect_over_cdp(DEFAULT_CDP_URL)
                context = browser.contexts[0]
                print("Connected to active Chrome session!")
            except Exception:
                context = None

        if not context:
            print(f"Auto-launching persistent Chrome context (Headless={DEVERO_HEADLESS})...")
            launch_args = ["--no-first-run", "--no-default-browser-check"]
            try:
                context = p.chromium.launch_persistent_context(
                    user_data_dir=PROFILE_DIR,
                    headless=DEVERO_HEADLESS,
                    channel="chrome",
                    args=launch_args
                )
            except Exception:
                time.sleep(1.5)
                try:
                    context = p.chromium.launch_persistent_context(
                        user_data_dir=PROFILE_DIR,
                        headless=DEVERO_HEADLESS,
                        args=launch_args
                    )
                except Exception:
                    context = p.chromium.launch_persistent_context(
                        user_data_dir=PROFILE_DIR + "_run",
                        headless=DEVERO_HEADLESS,
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

        # Process Tasks
        total_tasks = len(tasks)
        for idx, task in enumerate(tasks, 1):
            patient = task.get("patient", "")
            caregiver = task.get("caregiver", "")
            doc_type = task.get("doc_type", "")
            source_doc_type = SOURCE_DOC_TYPE
            source_date = (task.get("source_date") or "").strip()
            target_date = (task.get("target_date") or "").strip()
            source_val = (task.get("source_url") or task.get("source_activity_id") or "").strip()
            target_val = (task.get("target_url") or task.get("target_activity_id") or "").strip()

            if not patient or not doc_type or not target_date:
                print(f"[SKIP Task #{idx}/{total_tasks}]: Missing patient, doc_type, or target_date.")
                continue

            print("\n==================================================")
            print(f"Processing Task #{idx}/{total_tasks}: {doc_type} (Target)")
            print(f"   Patient:     {patient}")
            if source_date:
                if source_doc_type:
                    print(f"   Source Doc:  {source_doc_type} ({source_date}, Completed/Sent To Office)")
                else:
                    print(f"   Source Date: {source_date} (Completed/Sent To Office)")
            else:
                print(f"   Source Note: [Auto-detect: most recent Send-to-office, fallback most recent Complete] (prior to {target_date})")
            print(f"   Target Doc:  {doc_type} ({target_date}, Pending, Caregiver: {caregiver})")
            print("==================================================")

            # Step 1: Navigate to Source Note & Extract Fields
            if not source_date and not source_val:
                print(f"--> No source mentioned in task file. Auto-detecting source note on Patient Chart (prior to {target_date})...")
                discovered = auto_detect_source_note(
                    context=context,
                    active_page=main_page,
                    base_url=base_url,
                    patient_name=patient,
                    doc_type=doc_type,
                    target_date=target_date,
                    caregiver=caregiver,
                    source_doc_type=source_doc_type,
                )
                if not discovered or not discovered.get("page"):
                    print(f"\n==================================================")
                    print(f"[ERROR Task #{idx}/{total_tasks}]: NO COMPLETED OR SENT-TO-OFFICE SOURCE NOTE FOUND!")
                    print(f"   Patient:     '{patient}'")
                    print(f"   Target Date: '{target_date}'")
                    print(f"   Reason:      Could not find any 'Send-to-office' or 'Completed' source note prior to '{target_date}'" + (f" by '{caregiver}'!" if caregiver else "!"))
                    print(f"==================================================")
                    continue

                source_date = discovered["dateStr"]
                task["source_date"] = source_date
                source_page = discovered["page"]
            else:
                source_page = navigate_to_note(
                    context,
                    main_page,
                    base_url,
                    source_val,
                    is_target=False,
                    patient_name=patient,
                    doc_type=doc_type,
                    note_date=source_date,
                    caregiver=caregiver,
                    source_doc_type=source_doc_type,
                    target_date=target_date,
                )

            if not source_page:
                print(f"\n==================================================")
                print(f"[ERROR Task #{idx}/{total_tasks}]: SOURCE NOTE DOES NOT EXIST!")
                print(f"   Patient:     '{patient}'")
                if source_doc_type:
                    print(f"   Source Doc:  '{source_doc_type}'")
                print(f"   Source Date: '{source_date}' [COMPLETED / SENT TO OFFICE]")
                print(f"   Reason:      No matching Completed or Sent To Office source note row found on Patient Chart for date '{source_date}'!")
                print(f"==================================================")
                continue

            src_inputs = wait_for_form_inputs(source_page, min_inputs=15, timeout_sec=30)
            if src_inputs < 15:
                print(f"\n==================================================")
                print(f"[ERROR Task #{idx}/{total_tasks}]: SOURCE NOTE FORM FAILED TO LOAD!")
                print(f"   Patient:     '{patient}'")
                print(f"   Source Date: '{source_date}'")
                print(f"   Reason:      Only {src_inputs} form controls loaded in Source Note tab!")
                print(f"==================================================")
                if source_page != main_page:
                    try: source_page.close()
                    except Exception: pass
                continue

            print("--> Extracting fields across all pages of Source Note...")
            extracted_fields = extract_all_pages_source(source_page)
            field_count = len(extracted_fields) if extracted_fields else 0
            print(f"[OK] Extracted {field_count} total form fields across all Source Note pages!")

            if field_count == 0:
                print(f"\n==================================================")
                print(f"[ERROR Task #{idx}/{total_tasks}]: 0 FIELDS EXTRACTED FROM SOURCE NOTE!")
                print(f"   Patient:     '{patient}'")
                print(f"   Source Date: '{source_date}'")
                print(f"   Reason:      No valid form fields were extracted from Source Note!")
                print(f"==================================================")
                if source_page != main_page:
                    try: source_page.close()
                    except Exception: pass
                continue

            # Step 2: Navigate to Target Note & Inject Fields across all pages
            target_page = navigate_to_note(context, main_page, base_url, target_val, is_target=True, patient_name=patient, doc_type=doc_type, note_date=target_date, caregiver=caregiver)

            if not target_page:
                print(f"\n==================================================")
                print(f"[ERROR Task #{idx}/{total_tasks}]: TARGET NOTE DOES NOT EXIST!")
                print(f"   Patient:     '{patient}'")
                print(f"   Doc Type:    '{doc_type}'")
                print(f"   Target Date: '{target_date}' [PENDING]")
                print(f"   Caregiver:   '{caregiver}'")
                print(f"   Reason:      No matching PENDING note row found on Patient Chart!")
                print(f"==================================================")
                if source_page != main_page:
                    try: source_page.close()
                    except Exception: pass
                continue

            tgt_inputs = wait_for_form_inputs(target_page, min_inputs=15, timeout_sec=30)
            if tgt_inputs < 15:
                print(f"\n==================================================")
                print(f"[ERROR Task #{idx}/{total_tasks}]: TARGET NOTE FORM FAILED TO LOAD!")
                print(f"   Patient:     '{patient}'")
                print(f"   Doc Type:    '{doc_type}'")
                print(f"   Target Date: '{target_date}'")
                print(f"   Reason:      Only {tgt_inputs} form controls loaded in Target Note tab!")
                print(f"==================================================")
                if source_page != main_page:
                    try: source_page.close()
                    except Exception: pass
                if target_page != main_page:
                    try: target_page.close()
                    except Exception: pass
                continue

            print("--> Checking loaded Target Note before copying blank fields...")
            result = inject_all_pages_target(target_page, extracted_fields,
                                             skip_if_temperature=args.skip_if_temperature)

            # Close Source Note tab (Completed / Sent To Office doc) and any leftover tabs before proceeding
            if source_page and source_page != main_page:
                try:
                    print("--> Closing Completed / Sent To Office Source Note tab...")
                    source_page.close()
                except Exception as e:
                    print(f"--> Source tab close notice: {e}")

            for tab in list(context.pages):
                if tab != main_page and tab.url != "about:blank":
                    try:
                        tab.close()
                    except Exception:
                        pass

            if result is None:
                continue
            updated_count, skipped_count = result
            print(f"\n==================================================")
            print(f"[SUCCESS] Transfer completed for Task #{idx}/{total_tasks} ({patient})!")
            print(f"   - Target Fields Populated: {updated_count}")
            print(f"   - Pre-filled Fields Preserved: {skipped_count}")
            print(f"==================================================")

        print("\nAll tasks processed successfully! Execution complete.")

if __name__ == "__main__":
    main()
