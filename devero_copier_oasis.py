import argparse
import json
import logging
import os
import sys
import time
import re
from logging.handlers import RotatingFileHandler
from urllib.parse import urlparse
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright
from devero_form_opening import iter_chart_episodes, wait_for_form_inputs

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

# Keep detailed automation diagnostics server-side. Console output is reserved
# for concise job lifecycle messages suitable for a SaaS worker/email summary.
LOG_FILE = os.getenv(
    "DEVERO_LOG_FILE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "resumption-of-care.log"),
).strip()
os.makedirs(os.path.dirname(LOG_FILE) or ".", exist_ok=True)
LOGGER = logging.getLogger("devero.resumption_of_care")
LOGGER.setLevel(logging.DEBUG)
LOGGER.propagate = False
if not LOGGER.handlers:
    log_handler = RotatingFileHandler(
        LOG_FILE, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    log_handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(message)s"
    ))
    LOGGER.addHandler(log_handler)

_console_print = print


def _diagnostic_print(*values, sep=" ", end="\n", **_kwargs):
    """Compatibility sink for existing detailed messages: file log only."""
    message = sep.join(str(value) for value in values)
    if end and end != "\n":
        message += end
    LOGGER.debug(message.rstrip("\n"))


def job_status(message):
    """Emit a short, non-diagnostic status for the job runner and its log."""
    _console_print(message, flush=True)
    LOGGER.info(message)


# Existing detailed print calls stay available for troubleshooting without
# leaking browser/field diagnostics into worker stdout or user notifications.
print = _console_print

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

        const selectors = 'input, textarea, select, cmwf-checkbox, cmwf-textbox, cmwf-textarea, cmwf-text-display, cmwf-cop, cmwf-integrated-action, cmwf-dropdown, ntst-select, [data-field-id], [ng-model]';
        const elements = doc.querySelectorAll(selectors);

        elements.forEach((el, index) => {
            const tag = el.tagName.toLowerCase();
            const type = (el.type || '').toLowerCase();

            const specificKeys = [];
            const genericKeys = [];

            if (el.id) {
                specificKeys.push(el.id);
                if (el.id.endsWith('_key')) specificKeys.push(el.id.replace(/_key$/, ''));
            }

            const ngMod = el.getAttribute ? el.getAttribute('ng-model') : null;
            if (ngMod) {
                specificKeys.push(ngMod);
                if (ngMod.includes('.')) {
                    const prop = ngMod.split('.').pop();
                    if (prop) specificKeys.push(prop);
                }
            }

            const dataFieldId = el.getAttribute ? el.getAttribute('data-field-id') : null;
            if (dataFieldId) specificKeys.push(dataFieldId);

            const ariaLabel = el.getAttribute ? el.getAttribute('aria-label') : null;
            if (ariaLabel) specificKeys.push(ariaLabel);

            const nameAttr = el.name || (el.getAttribute ? el.getAttribute('name') : null);
            if (nameAttr && el.value !== undefined && el.value !== null && el.value !== '') {
                specificKeys.push(`${nameAttr}_val_${el.value}`);
                specificKeys.push(`${nameAttr}_${el.value}`);
            }

            if (nameAttr) {
                genericKeys.push(nameAttr);
            }

            let parentCw = el.closest ? el.closest('cmwf-checkbox, cmwf-textbox, cmwf-textarea, cmwf-text-display, cmwf-cop, cmwf-integrated-action, cmwf-dropdown, ntst-select') : null;
            if (parentCw && parentCw !== el) {
                if (parentCw.id) specificKeys.push(parentCw.id);
                const pNgMod = parentCw.getAttribute('ng-model');
                if (pNgMod) {
                    specificKeys.push(pNgMod);
                    if (pNgMod.includes('.')) {
                        const prop = pNgMod.split('.').pop();
                        if (prop) specificKeys.push(prop);
                    }
                }
                if (parentCw.getAttribute('data-field-id')) specificKeys.push(parentCw.getAttribute('data-field-id'));
                if (parentCw.getAttribute('name')) genericKeys.push(parentCw.getAttribute('name'));
            }

            const fallbackKey = `${prefix}pos_${index}_${tag}_${type}`;
            specificKeys.push(fallbackKey);

            const primaryKey = specificKeys[0] || genericKeys[0] || fallbackKey;

            let item = null;
            if (type === 'checkbox' || tag === 'cmwf-checkbox') {
                let isChecked = false;

                function checkSingleEl(e) {
                    if (!e) return false;
                    if (e.checked === true || e.checked === 'true' || e.checked === 1 || e.defaultChecked === true) return true;
                    if (e.hasAttribute) {
                        if (e.hasAttribute('checked')) {
                            const v = (e.getAttribute('checked') || '').toLowerCase();
                            if (v === '' || v === 'true' || v === 'checked' || v === '1') return true;
                        }
                        if (e.hasAttribute('aria-checked') && (e.getAttribute('aria-checked') === 'true' || e.getAttribute('aria-checked') === '1')) return true;
                        if (e.hasAttribute('ng-reflect-checked') && e.getAttribute('ng-reflect-checked') === 'true') return true;
                    }
                    if (e.classList && (e.classList.contains('checked') || e.classList.contains('is-checked') || e.classList.contains('active') || e.classList.contains('selected'))) return true;
                    return false;
                }

                function isValChecked(val, kName) {
                    if (val === undefined || val === null || val === false || val === 0 || val === '0' || val === 'false' || val === 'off' || val === 'OFF' || val === 'no' || val === 'NO') return false;
                    if (val === true || val === 1 || val === '1' || val === 'true' || val === 'TRUE' || val === 'on' || val === 'ON' || val === 'y' || val === 'Y' || val === 'yes' || val === 'YES' || val === 'checked' || val === 'CHECKED') return true;
                    if (Array.isArray(val)) {
                        if (val.includes(kName) || val.includes('1') || val.includes('on') || val.includes(true)) return true;
                    }
                    if (typeof val === 'string' && val.trim() !== '' && val.trim() !== '0' && val.trim().toLowerCase() !== 'false') return true;
                    return false;
                }

                if (checkSingleEl(el)) isChecked = true;

                const innerInp = el.querySelector ? el.querySelector('input[type="checkbox"], input') : null;
                if (innerInp && checkSingleEl(innerInp)) isChecked = true;

                const outerCw = el.closest ? el.closest('cmwf-checkbox') : null;
                if (outerCw && checkSingleEl(outerCw)) isChecked = true;

                if (el.shadowRoot) {
                    const shInp = el.shadowRoot.querySelector('input[type="checkbox"], input');
                    if (shInp && checkSingleEl(shInp)) isChecked = true;
                }

                try {
                    const cbDiv = (outerCw || el).querySelector ? (outerCw || el).querySelector('.checkbox-div, .checkbox_div') : null;
                    if (cbDiv && cbDiv.classList && (cbDiv.classList.contains('checked') || cbDiv.classList.contains('is-checked') || cbDiv.classList.contains('active'))) isChecked = true;
                } catch(e) {}

                if (!isChecked) {
                    try {
                        const win = (doc || document).defaultView || window;
                        if (win.angular) {
                            const targets = [el, innerInp, outerCw].filter(Boolean);
                            for (let t of targets) {
                                const ngEl = win.angular.element(t);
                                const scope = ngEl ? ngEl.scope() : null;
                                if (!scope) continue;
                                const ngMod = t.getAttribute ? t.getAttribute('ng-model') : null;
                                if (ngMod) {
                                    let val = null;
                                    if (ngMod in scope) val = scope[ngMod];
                                    if (val === null && scope.formData && (ngMod in scope.formData)) val = scope.formData[ngMod];
                                    if (val === null && ngMod.includes('.')) {
                                        const parts = ngMod.split('.');
                                        let obj = scope;
                                        for (let p of parts) {
                                            if (obj && p in obj) obj = obj[p];
                                            else { obj = null; break; }
                                        }
                                        val = obj;
                                    }
                                    if (isValChecked(val, ngMod)) { isChecked = true; break; }
                                }
                                const keyCandidates = [t.id, t.name, t.getAttribute ? t.getAttribute('name') : null].filter(Boolean);
                                if (t.id && t.id.endsWith('_key')) keyCandidates.push(t.id.replace(/_key$/, ''));
                                for (let k of keyCandidates) {
                                    if (k in scope && isValChecked(scope[k], k)) { isChecked = true; break; }
                                    if (scope.formData && k in scope.formData && isValChecked(scope.formData[k], k)) { isChecked = true; break; }
                                }
                                if (isChecked) break;
                            }
                        }
                    } catch(e) {}
                }

                item = {
                    kind: 'checkbox',
                    checked: isChecked,
                    value: el.value || (innerInp ? innerInp.value : '') || 'on',
                    keys: specificKeys.concat(genericKeys),
                    index: index
                };
            } else if (type === 'radio') {
                if (el.checked) {
                    item = {
                        kind: 'radio',
                        value: el.value,
                        keys: specificKeys.concat(genericKeys),
                        index: index
                    };
                }
            } else if (tag === 'select') {
                let val = el.value;
                if (!val && el.options && el.selectedIndex >= 0) {
                    const opt = el.options[el.selectedIndex];
                    val = opt ? (opt.innerText || opt.text || opt.value || '').trim() : '';
                }
                item = {
                    kind: 'select',
                    value: val || '',
                    keys: specificKeys.concat(genericKeys),
                    index: index
                };
            } else if (tag === 'cmwf-dropdown' || tag === 'ntst-select') {
                let val = el.value;
                const selOpt = el.querySelector('ntst-option[selected], ntst-option[aria-selected="true"], ntst-option.selected, option[selected]');
                if (selOpt) {
                    val = selOpt.getAttribute('display') || selOpt.innerText.trim() || selOpt.getAttribute('value');
                }
                if (!val || val === '(Select)' || val === 'Select') {
                    const span = el.querySelector('span.f-bold, span.n-label, span.textbox-value, span');
                    if (span && span.innerText.trim() && span.innerText.trim() !== '(Select)') {
                        val = span.innerText.trim();
                    }
                }
                item = {
                    kind: 'select',
                    value: val || '',
                    keys: specificKeys.concat(genericKeys),
                    index: index
                };
            } else if (tag === 'cmwf-integrated-action') {
                const actionId = (el.id || '').trim();
                const title = (el.innerText || el.textContent || '').trim();
                const transientSurfaceSelector = [
                    '#formFramePopup', '#insertToTemplatePopup', '#templateView',
                    '[id*="templateView"]', '[ng-if*="showTemplatePopup"]',
                    '.popup-modal', '.modal', '[role="dialog"]', '.sweet-alert'
                ].join(',');
                if (el.closest(transientSurfaceSelector)) return;
                const actionTitleKey = cleanKey(title);
                const actionChromeKeys = new Set([
                    'clear', 'select', 'add', 'delete', 'remove', 'edit',
                    'cancel', 'close', 'update', 'none', 'norecordsfound'
                ]);
                // Devero renders persistent controls such as the Clear button
                // with the same custom element as a real linked-form action.
                // They are UI chrome, not source linked records.
                if (actionChromeKeys.has(actionTitleKey)) return;
                const identity = (actionId + ' ' + title).toLowerCase();
                const actionStem = value => String(value || '')
                    .replace(/([a-z0-9])([A-Z])/g, '$1_$2')
                    .replace(/(?:_?summary_?of_?problems)?_?request$/i, '')
                    .replace(/_?action(?:_\\d+)?$/i, '')
                    .replace(/_?(?:link|button)$/i, '')
                    .replace(/[^a-z0-9]+/gi, '_')
                    .replace(/^_+|_+$/g, '')
                    .toLowerCase();
                // OASIS item-code links such as (A1005) use the shared
                // `mDetails` action to open reference/help text. They are not
                // clinical linked forms and must never be copied or clicked.
                if (actionStem(actionId) === 'm_details') return;
                // `update_diagnosis` is handled separately by sync_oasis_diagnoses().
                if (actionStem(actionId) === 'update_diagnosis' || cleanKey(title) === 'updatediagnosis') return;
                let section = actionStem(actionId) || actionStem(title) ||
                    ('linked_action_' + index);
                const requestId = actionId.replace(/[^a-z0-9]/gi, '').toLowerCase();
                const isInterventionsGoalsRequest = [
                    'interventionsgoalsrequest',
                    'otherinterventionsgoalsrequest',
                    'combinedinterventionsgoalsrequest'
                ].includes(requestId);
                if (isInterventionsGoalsRequest ||
                    (/intervention/.test(identity) && /goal/.test(identity))) {
                    section = 'interventions_goals';
                }
                if (section) {
                    const container = el.closest('cmwf-section') || el.parentElement;
                    const meaningfulText = node => {
                        const titleKey = cleanKey(title);
                        return String(node?.innerText || node?.textContent || '')
                            .split(/[\\n\\r]+/)
                            .map(line => line.replace(/\\s+/g, ' ').trim())
                            .filter(line => {
                                const key = cleanKey(line);
                                if (!key || key === titleKey ||
                                    actionChromeKeys.has(key)) return false;
                                // Hydrated components can flatten a link title
                                // and its always-visible Clear button onto one
                                // text line (for example, "Pain Clear").
                                const suffix = titleKey && key.startsWith(titleKey) ?
                                    key.slice(titleKey.length) : '';
                                const prefix = titleKey && key.endsWith(titleKey) ?
                                    key.slice(0, -titleKey.length) : '';
                                return !actionChromeKeys.has(suffix) &&
                                    !actionChromeKeys.has(prefix);
                            })
                            .join('\\n');
                    };
                    const committedDisplayText = node => {
                        if (node.tagName?.toLowerCase() !== 'cmwf-cop') {
                            return meaningfulText(node);
                        }
                        return Array.from(node.querySelectorAll(
                            '.cop-items .copElement, .copElement'
                        )).map(meaningfulText).filter(Boolean).join('\\n');
                    };
                    const allDisplays = Array.from(container.querySelectorAll(
                        'cmwf-text-display, cmwf-cop, [data-linked-display], ' +
                        '[data-action-output]'
                    )).filter(node =>
                        !/(?:^|_)module_items$/i.test(node.id || '') &&
                        !node.closest(transientSurfaceSelector)
                    );
                    const compactSection = section.replace(/[^a-z0-9]/g, '');
                    let displays = allDisplays.filter(node => {
                        const displayIdentity = [
                            node.id,
                            node.getAttribute('ng-model'),
                            node.getAttribute('data-field-id'),
                            node.getAttribute('data-linked-display'),
                            node.getAttribute('data-action-output')
                        ].filter(Boolean).join(' ')
                            .replace(/[^a-z0-9]/gi, '').toLowerCase();
                        return compactSection.length >= 4 &&
                            displayIdentity.includes(compactSection);
                    });
                    const siblingActions = Array.from(container.querySelectorAll(
                        'cmwf-integrated-action'
                    ));
                    if (!displays.length && siblingActions.length === 1) {
                        displays = allDisplays;
                    } else if (!displays.length && allDisplays.length) {
                        const ordered = Array.from(container.querySelectorAll(
                            'cmwf-integrated-action, cmwf-text-display, cmwf-cop, ' +
                            '[data-linked-display], [data-action-output]'
                        ));
                        displays = allDisplays.filter(node => {
                            const nodePosition = ordered.indexOf(node);
                            let nearest = null;
                            let distance = Number.POSITIVE_INFINITY;
                            for (const action of siblingActions) {
                                const candidateDistance = Math.abs(
                                    ordered.indexOf(action) - nodePosition
                                );
                                if (candidateDistance < distance) {
                                    nearest = action;
                                    distance = candidateDistance;
                                }
                            }
                            return nearest === el;
                        });
                    }
                    const payloads = displays
                        .map(committedDisplayText)
                        .filter(Boolean);
                    const val = payloads.join('\\n\\n');
                    specificKeys.push('linked_section_' + section);
                    item = {
                        kind: 'integrated',
                        section: section,
                        action_id: actionId,
                        action_stem: actionStem(actionId),
                        title: title || section,
                        items: payloads,
                        value: val,
                        keys: specificKeys.concat(genericKeys),
                        index: index
                    };
                }
            } else if (tag === 'cmwf-cop') {
                if (el.closest(
                    '#formFramePopup, #insertToTemplatePopup, #templateView, ' +
                    '[id*="templateView"], [ng-if*="showTemplatePopup"], ' +
                    '.popup-modal, .modal, [role="dialog"], .sweet-alert'
                )) return;
                const titleEl = el.querySelector('.cop-title[data-section], .cop-title');
                const section = (titleEl && titleEl.getAttribute('data-section')) || el.id || '';
                const title = ((titleEl && (titleEl.innerText || titleEl.textContent)) ||
                               (titleEl && titleEl.getAttribute('data-cop-title-go-composite')) || '').trim();
                if (!titleEl || [
                    'clear', 'select', 'add', 'delete', 'remove', 'edit',
                    'cancel', 'close', 'update', 'none'
                ].includes(cleanKey(title))) return;
                let items = Array.from(el.querySelectorAll('.cop-items .copElement, .copElement'))
                    .map(node => (node.innerText || node.textContent || '').trim())
                    .filter(text => text && ![
                        'clear', 'select', 'add', 'delete', 'remove', 'edit',
                        'cancel', 'close', 'update', 'none'
                    ].includes(cleanKey(text)));
                if (!items.length && el.querySelector('.cop-items')) {
                    items = (el.querySelector('.cop-items').innerText || '').split(/[\\n\\r]+/).map(s => s.trim()).filter(Boolean);
                }
                const splitByDate = (text) => String(text || '')
                    .split(/(?:Start Effective Date|Discontinued Date)\\s*:?\\s*\\d{1,2}\\/\\d{1,2}\\/\\d{2,4}/i)
                    .map(s => s.replace(/^\\s*(ADVANCE DIRECTIVES|SAFETY MEASURES|ELIGIBILITY|GOALS|INTERVENTIONS)\\s*:?\\s*/i, '').trim())
                    .filter(s => s.length >= 2);
                const expanded = [];
                for (const chunk of (items.length ? items : [(el.innerText || '').trim()])) {
                    const parts = splitByDate(chunk);
                    if (parts.length > 1 || (parts.length === 1 && parts[0] !== chunk)) {
                        expanded.push.apply(expanded, parts);
                    } else if (chunk) {
                        expanded.push(chunk);
                    }
                }
                items = expanded;
                const val = items.join('\\n\\n') || (el.innerText || '').trim();
                if (section) {
                    specificKeys.push(`cop_section_${section}`);
                }
                item = {
                    kind: 'composite',
                    section: section,
                    title: title || section,
                    items: items.length ? items : (val ? [val] : []),
                    value: val,
                    keys: specificKeys.concat(genericKeys),
                    index: index
                };
            } else if (tag === 'cmwf-text-display') {
                const val = (el.innerText || el.textContent || '').trim();
                item = {
                    kind: 'text',
                    value: val || '',
                    keys: specificKeys.concat(genericKeys),
                    index: index
                };
            } else if (tag === 'cmwf-textbox' || tag === 'cmwf-textarea') {
                let val = el.value;
                const innerInp = el.querySelector('input, textarea');
                if (innerInp && innerInp.value) val = innerInp.value;
                
                if (!val) {
                    const valDiv = el.querySelector('.textbox-value, .field-value, .cmwf-value, span.value, div.value, .display-value');
                    if (valDiv) val = valDiv.innerText.trim();
                }

                if (!val) {
                    val = (el.innerText || el.textContent || '').trim();
                }

                item = {
                    kind: 'text',
                    value: val || '',
                    keys: specificKeys.concat(genericKeys),
                    index: index
                };
            } else {
                let val = el.value;
                if ((val === undefined || val === null || val === '') && el.innerText) {
                    val = el.innerText.trim();
                }
                item = {
                    kind: 'text',
                    value: val || '',
                    keys: specificKeys.concat(genericKeys),
                    index: index
                };
            }

            if (item && item.value !== undefined && item.value !== null) {
                function setField(k, valItem) {
                    if (!k) return;
                    if (fields[k] && fields[k].kind === 'checkbox' && fields[k].checked && valItem.kind === 'checkbox' && !valItem.checked) {
                        return;
                    }
                    // Print/read-only forms can contain multiple copies of the
                    // same field. Keep a populated copy (for example
                    // cf_Temp_Txt=98.0) when a later hidden copy is empty.
                    const existing = fields[k];
                    const existingHasValue = existing && (
                        (existing.kind === 'checkbox' && existing.checked) ||
                        (existing.kind !== 'checkbox' && existing.value !== undefined &&
                         existing.value !== null && String(existing.value).trim() !== '')
                    );
                    const incomingHasValue = valItem && (
                        (valItem.kind === 'checkbox' && valItem.checked) ||
                        (valItem.kind !== 'checkbox' && valItem.value !== undefined &&
                         valItem.value !== null && String(valItem.value).trim() !== '')
                    );
                    if (existingHasValue && !incomingHasValue) return;
                    fields[k] = valItem;
                }

                setField(primaryKey, item);

                specificKeys.forEach(k => {
                    setField(k, item);
                    const cleaned = cleanKey(k);
                    if (cleaned) setField(cleaned, item);
                });

                genericKeys.forEach(k => {
                    setField(k, item);
                    const cleaned = cleanKey(k);
                    if (cleaned) setField(cleaned, item);
                });
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
    let stats = { updated: 0, skipped: 0, visited: 0, matched: 0, total: Object.keys(fieldMap).length };

    function asText(value) {
        if (value === undefined || value === null) return '';
        return String(value).trim();
    }

    function cleanKey(str) {
        if (!str) return '';
        return String(str).toLowerCase().replace(/[^a-z0-9]/g, '');
    }

    function hasValue(el) {
        if (!el) return false;
        const tag = el.tagName.toLowerCase();
        const type = (el.type || 'text').toLowerCase();

        if (type === 'checkbox' || tag === 'cmwf-checkbox' || type === 'radio') {
            if (el.checked) return true;
            if (el.hasAttribute && (el.hasAttribute('checked') || el.getAttribute('checked') === 'true')) return true;
            const innerInp = el.querySelector ? el.querySelector('input[type="checkbox"]') : null;
            if (innerInp && (innerInp.checked || (innerInp.hasAttribute && innerInp.hasAttribute('checked')))) return true;
            return false;
        }
        // Linked actions are populated separately by inject_composite_linked_forms().
        // Their `value` property can be an object, so treating them as text fields
        // used to abort the entire injector with "value.trim is not a function".
        if (tag === 'cmwf-integrated-action') return true;
        if (tag === 'select') {
            const value = asText(el.value);
            return value !== '' && value !== '?' && value !== '0';
        }
        if (tag === 'cmwf-text-display' || tag === 'cmwf-cop') {
            const txt = (el.innerText || el.textContent || '').trim();
            return txt !== '';
        }
        if (tag === 'cmwf-textbox' || tag === 'cmwf-textarea') {
            const innerInp = el.querySelector('input, textarea');
            if (innerInp && asText(innerInp.value) !== '') return true;
            return asText(el.value) !== '';
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
            val = asText(val);
            return val !== '' && val !== '(Select)' && val !== 'Select';
        }
        return asText(el.value) !== '';
    }

    function formatTimeValue(val, type) {
        val = asText(val);
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

            const ngModelAttr = el.getAttribute ? el.getAttribute('ng-model') : null;
            if (ngModelAttr) {
                try {
                    const $parse = ngEl.injector().get('$parse');
                    if ($parse) {
                        const assignFn = $parse(ngModelAttr).assign;
                        if (assignFn) {
                            assignFn(scope, val);
                            updatedScope = true;
                        }
                    }
                } catch(e) {}
            }

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
                if (k.includes('.')) {
                    const parts = k.split('.');
                    let obj = scope;
                    for (let i = 0; i < parts.length - 1; i++) {
                        if (obj && parts[i] in obj) {
                            obj = obj[parts[i]];
                        } else {
                            obj = null;
                            break;
                        }
                    }
                    if (obj && parts[parts.length - 1]) {
                        obj[parts[parts.length - 1]] = val;
                        updatedScope = true;
                    }
                }
            });
            if (updatedScope && !scope.$$phase) {
                scope.$evalAsync();
            }
        } catch(e) {}
    }

    function injectDoc(doc, prefix = '') {
        if (!doc) return;

        const selectors = 'input, textarea, select, cmwf-checkbox, cmwf-textbox, cmwf-textarea, cmwf-text-display, cmwf-cop, cmwf-integrated-action, cmwf-dropdown, ntst-select, [data-field-id], [ng-model]';
        const elements = doc.querySelectorAll(selectors);

        elements.forEach((el, index) => {
            stats.visited++;
            // Composite outputs are read-only projections of selections made in
            // Devero's linked-form modal. Writing their DOM text here creates a
            // transient false positive: discovery sees content and preserves an
            // otherwise empty target, but Devero has no committed selection to
            // save. Only inject_composite_linked_forms() may populate them.
            const linkedOutputOwner = el.closest ? el.closest(
                'cmwf-text-display, cmwf-cop, cmwf-integrated-action'
            ) : null;
            if (linkedOutputOwner) {
                stats.skipped++;
                return;
            }
            // RULE: Do NOT rewrite if target field already has data!
            if (hasValue(el)) {
                stats.skipped++;
                return;
            }

            const tag = el.tagName.toLowerCase();
            const type = (el.type || 'text').toLowerCase();

            const candidateKeys = [];

            // 1. Specific IDs & ng-model FIRST
            if (el.id) {
                candidateKeys.push(el.id, cleanKey(el.id));
                if (el.id.endsWith('_key')) {
                    candidateKeys.push(el.id.replace(/_key$/, ''), cleanKey(el.id.replace(/_key$/, '')));
                }
            }

            const ngMod = el.getAttribute ? el.getAttribute('ng-model') : null;
            if (ngMod) {
                candidateKeys.push(ngMod, cleanKey(ngMod));
                if (ngMod.includes('.')) {
                    const prop = ngMod.split('.').pop();
                    if (prop) candidateKeys.push(prop, cleanKey(prop));
                }
            }

            const dataFieldId = el.getAttribute ? el.getAttribute('data-field-id') : null;
            if (dataFieldId) {
                candidateKeys.push(dataFieldId, cleanKey(dataFieldId));
            }

            const ariaLabel = el.getAttribute ? el.getAttribute('aria-label') : null;
            if (ariaLabel) {
                candidateKeys.push(ariaLabel, cleanKey(ariaLabel));
            }

            // 2. Name + Value combination for checkboxes and radios
            const nameAttr = el.name || (el.getAttribute ? el.getAttribute('name') : null);
            if (nameAttr && el.value !== undefined && el.value !== null && el.value !== '') {
                candidateKeys.push(`${nameAttr}_val_${el.value}`, cleanKey(`${nameAttr}_val_${el.value}`));
                candidateKeys.push(`${nameAttr}_${el.value}`, cleanKey(`${nameAttr}_${el.value}`));
            }

            // 3. Parent element wrapper specific keys
            let parentCw = el.closest ? el.closest('cmwf-checkbox, cmwf-textbox, cmwf-textarea, cmwf-text-display, cmwf-cop, cmwf-integrated-action, cmwf-dropdown, ntst-select') : null;
            if (parentCw && parentCw !== el) {
                if (parentCw.id) candidateKeys.push(parentCw.id, cleanKey(parentCw.id));
                const pNgMod = parentCw.getAttribute('ng-model');
                if (pNgMod) {
                    candidateKeys.push(pNgMod, cleanKey(pNgMod));
                    if (pNgMod.includes('.')) {
                        const prop = pNgMod.split('.').pop();
                        if (prop) candidateKeys.push(prop, cleanKey(prop));
                    }
                }
                if (parentCw.getAttribute('data-field-id')) candidateKeys.push(parentCw.getAttribute('data-field-id'), cleanKey(parentCw.getAttribute('data-field-id')));
            }

            // 4. Positional fallback key (matching position across identical layout notes)
            const fallbackKey = `${prefix}pos_${index}_${tag}_${type}`;
            candidateKeys.push(fallbackKey);

            // 5. Generic shared name key LAST
            if (nameAttr) {
                candidateKeys.push(nameAttr, cleanKey(nameAttr));
            }
            if (parentCw && parentCw.getAttribute('name')) {
                candidateKeys.push(parentCw.getAttribute('name'), cleanKey(parentCw.getAttribute('name')));
            }

            let matchedItem = null;
            for (let k of candidateKeys) {
                if (k && k in fieldMap) {
                    matchedItem = fieldMap[k];
                    break;
                }
            }

            if (!matchedItem) return;
            stats.matched++;

            try {
                if (type === 'checkbox' || tag === 'cmwf-checkbox') {
                    if (matchedItem.checked) {
                        const inputEl = tag === 'cmwf-checkbox' ? el.querySelector('input[type="checkbox"]') : el;
                        const outerCw = tag === 'cmwf-checkbox' ? el : (parentCw && parentCw.tagName.toLowerCase() === 'cmwf-checkbox' ? parentCw : (el.closest ? el.closest('cmwf-checkbox') : null));
                        const targetEl = inputEl || el;

                        targetEl.checked = true;
                        if (targetEl.setAttribute) {
                            targetEl.setAttribute('checked', 'checked');
                            targetEl.setAttribute('aria-checked', 'true');
                        }

                        if (outerCw) {
                            outerCw.checked = true;
                            if (outerCw.setAttribute) {
                                outerCw.setAttribute('checked', 'checked');
                                outerCw.setAttribute('aria-checked', 'true');
                            }
                            if (outerCw.classList) outerCw.classList.add('checked');
                            if (outerCw.setChecked && typeof outerCw.setChecked === 'function') {
                                try { outerCw.setChecked(true); } catch(e) {}
                            }
                        }

                        if (el !== targetEl && el !== outerCw) {
                            el.checked = true;
                            if (el.setAttribute) {
                                el.setAttribute('checked', 'checked');
                                el.setAttribute('aria-checked', 'true');
                            }
                            if (el.setChecked && typeof el.setChecked === 'function') {
                                try { el.setChecked(true); } catch(e) {}
                            }
                        }

                        if (!targetEl.checked || (outerCw && !outerCw.checked && !outerCw.classList.contains('checked'))) {
                            const cbDiv = (outerCw || el).querySelector ? (outerCw || el).querySelector('.checkbox-div, .checkbox_div') : null;
                            if (cbDiv) {
                                try { cbDiv.click(); } catch(e) {}
                            }
                            const labelId = targetEl.id || el.id;
                            let labelEl = labelId ? doc.querySelector(`label[for="${labelId}"]`) : null;
                            if (labelEl) {
                                try { labelEl.click(); } catch(e) {}
                            }
                            if (!targetEl.checked) {
                                try { targetEl.click(); } catch(e) {}
                            }
                            targetEl.checked = true;
                            if (targetEl.setAttribute) targetEl.setAttribute('checked', 'checked');
                        }

                        triggerEvents(targetEl, doc);
                        if (outerCw) triggerEvents(outerCw, doc);
                        if (el !== targetEl && el !== outerCw) triggerEvents(el, doc);

                        syncAngularScope(el, doc, true, candidateKeys);
                        if (targetEl) syncAngularScope(targetEl, doc, true, candidateKeys);
                        if (outerCw) syncAngularScope(outerCw, doc, true, candidateKeys);

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
                    const finalVal = asText(matchedItem.value);
                    if (finalVal) {
                        const opts = Array.from(el.options || []);
                        const fLower = finalVal.toLowerCase();
                        const matchedOpt = opts.find(o => o.value === finalVal || o.innerText.trim() === finalVal || (o.value && o.value.toLowerCase() === fLower) || (o.innerText && o.innerText.trim().toLowerCase() === fLower));
                        if (matchedOpt) {
                            el.value = matchedOpt.value;
                            matchedOpt.selected = true;
                        } else {
                            el.value = finalVal;
                        }
                        triggerEvents(el, doc);
                        syncAngularScope(el, doc, el.value || finalVal, candidateKeys);
                        stats.updated++;
                    }
                } else if (tag === 'cmwf-dropdown' || tag === 'ntst-select') {
                    const finalVal = asText(matchedItem.value);
                    if (finalVal) {
                        const ntst = tag === 'ntst-select' ? el : (el.querySelector('ntst-select') || el);
                        let resolvedVal = finalVal;
                        let matchedText = finalVal;
                        let matchedOpt = null;

                        const options = Array.from(ntst.querySelectorAll('ntst-option, option'));
                        if (options.length > 0) {
                            const fLower = finalVal.toLowerCase();
                            matchedOpt = options.find(opt => {
                                const v = opt.getAttribute('value') || opt.value || '';
                                const d = opt.getAttribute('display') || '';
                                const t = (opt.innerText || opt.textContent || '').trim();
                                return v === finalVal || d === finalVal || t === finalVal ||
                                       v.toLowerCase() === fLower || d.toLowerCase() === fLower || t.toLowerCase() === fLower;
                            });

                            if (matchedOpt) {
                                resolvedVal = matchedOpt.getAttribute('value') || matchedOpt.value || matchedOpt.innerText.trim();
                                matchedText = matchedOpt.getAttribute('display') || matchedOpt.innerText.trim() || resolvedVal;
                            }
                        }

                        el.value = resolvedVal;
                        if (ntst) {
                            ntst.value = resolvedVal;
                            if (resolvedVal) ntst.setAttribute('aria-activedescendant', 'option-' + resolvedVal);
                            options.forEach(opt => {
                                if (opt === matchedOpt) {
                                    opt.selected = true;
                                    opt.setAttribute('selected', '');
                                    opt.setAttribute('aria-selected', 'true');
                                    opt.classList.add('selected');
                                } else {
                                    opt.selected = false;
                                    opt.removeAttribute('selected');
                                    opt.removeAttribute('aria-selected');
                                    opt.classList.remove('selected');
                                }
                            });
                            triggerEvents(ntst, doc);
                        }

                        const dropdown = tag === 'cmwf-dropdown' ? el : el.closest('cmwf-dropdown');
                        if (dropdown) {
                            dropdown.value = resolvedVal;
                            let span = dropdown.querySelector('span.f-bold, span.n-label, span.textbox-value, span');
                            if (!span) {
                                span = doc.createElement('span');
                                span.className = 'f-bold';
                                dropdown.appendChild(span);
                            }
                            span.innerText = matchedText;
                            triggerEvents(dropdown, doc);
                        }

                        triggerEvents(el, doc);
                        syncAngularScope(el, doc, resolvedVal, candidateKeys);
                        if (dropdown) syncAngularScope(dropdown, doc, resolvedVal, candidateKeys);
                        stats.updated++;
                    }
                } else if (tag === 'cmwf-textbox' || tag === 'cmwf-textarea') {
                    let finalVal = asText(matchedItem.value);
                    if (/[\\n\\r]+/.test(finalVal)) {
                        const lines = finalVal.split(/[\\n\\r]+/).map(l => l.trim()).filter(Boolean);
                        const cleanLines = lines.filter(l => !/^(Body Temperature|Temperature Method|Route|fahrenheit|degrees|VITAL SIGNS)/i.test(l));
                        if (cleanLines.length > 0) finalVal = cleanLines[0];
                    }
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
            if (el.closest && el.closest('cmwf-text-display, cmwf-cop, cmwf-integrated-action')) return;
            if (el.innerHTML && el.innerHTML.trim() !== '') return;
            const key = el.id || el.getAttribute('ng-model') || (`editable_${index}`);
            if (key in fieldMap && fieldMap[key].kind === 'contenteditable') {
                el.innerHTML = fieldMap[key].value;
                triggerEvents(el, doc);
                stats.updated++;
            }
        });

        const iframes = doc.querySelectorAll('iframe');
        iframes.forEach((iframe, i) => {
            try {
                if (iframe.contentDocument) {
                    injectDoc(iframe.contentDocument, prefix + `iframe_${i}_`);
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
                if ((val === undefined || val === null || val === '') && el.getAttribute) {
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
        job_status("[WAITING] Authentication required.")
        while "login" in page.url.lower():
            time.sleep(1)
        print("[OK] Login detected!")

    return True

def print_form_ready(page):
    if "printForm" not in page.url:
        return False
    try:
        return page.evaluate("() => (document.body?.innerText || '').trim().length") > 100
    except Exception:
        return False

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

    for f_idx, frame in enumerate(page.frames):
        try:
            candidates = frame.evaluate("""() => {
                const els = Array.from(document.querySelectorAll('a, button, input, span, div, li'));
                const found = [];
                els.forEach(el => {
                    const txt = (el.innerText || el.value || '').trim();
                    if (txt === 'Next >' || txt === 'Next' || txt === '>' || /^\\d+$/.test(txt)) {
                        found.push({
                            txt: txt,
                            tag: el.tagName,
                            id: el.id,
                            cls: el.className,
                            vis: el.offsetWidth > 0 || el.offsetHeight > 0
                        });
                    }
                });
                return found;
            }""")
            if candidates:
                print(f"    [Pagination Debug Frame {f_idx}] Found {len(candidates)} candidates: {candidates[:10]}")

            clicked = frame.evaluate("""(targetNum) => {
                const els = Array.from(document.querySelectorAll('a, button, input[type="button"], input[type="submit"], span, div, li, [ng-click]'));
                
                const numBtn = els.find(b => {
                    const txt = (b.innerText || b.value || '').trim();
                    const isVis = b.offsetWidth > 0 || b.offsetHeight > 0 || (b.getClientRects && b.getClientRects().length > 0);
                    const isDisabled = b.disabled || b.classList.contains('disabled') || b.classList.contains('activeNP') || (b.parentElement && (b.parentElement.classList.contains('disabled') || b.parentElement.classList.contains('activeNP')));
                    return txt === String(targetNum) && isVis && !isDisabled;
                });
                if (numBtn) {
                    try { numBtn.scrollIntoView({ block: 'center' }); } catch(e){}
                    numBtn.click();
                    return true;
                }

                const nextBtn = els.find(b => {
                    const txt = (b.innerText || b.value || '').trim().toLowerCase();
                    const isNext = txt === 'next >' || txt === 'next' || txt === '>';
                    const isVis = b.offsetWidth > 0 || b.offsetHeight > 0 || (b.getClientRects && b.getClientRects().length > 0);
                    const isDisabled = b.disabled || b.classList.contains('disabled') || b.classList.contains('activeNP') || (b.parentElement && (b.parentElement.classList.contains('disabled') || b.parentElement.classList.contains('activeNP')));
                    return isNext && isVis && !isDisabled;
                });
                if (nextBtn) {
                    try { nextBtn.scrollIntoView({ block: 'center' }); } catch(e){}
                    nextBtn.click();
                    return true;
                }
                return false;
            }""", target_next_num)
            if clicked:
                print(f"--> Successfully navigated to Page {target_next_num} via Frame {f_idx}!")
                return True
        except Exception:
            pass

    print(f"--> Notice: Could not find navigation button for Page {target_next_num}.")
    return False

def extract_all_pages_source(page):
    """Iterates through all pages of Source Note to extract form data across all pages and frames."""
    all_extracted = {}
    page_num = 1

    while True:
        if "printForm" in page.url:
            try:
                page.wait_for_function("() => (document.body?.innerText || '').trim().length > 100", timeout=15000)
            except Exception:
                pass
        else:
            wait_for_form_inputs(page, min_inputs=5)
        print(f"--> Extracting Source Note Page {page_num}...")
        for f_idx, frame in enumerate(page.frames):
            try:
                frame_fields = frame.evaluate(JS_EXTRACT_FIELDS)
                if frame_fields:
                    for key, value in frame_fields.items():
                        existing = all_extracted.get(key)
                        if (
                            isinstance(existing, dict)
                            and existing.get("kind") in {"composite", "integrated"}
                            and isinstance(value, dict)
                            and value.get("kind") in {"composite", "integrated"}
                            and len(str(existing.get("value") or "")) > len(str(value.get("value") or ""))
                        ):
                            continue
                        all_extracted[key] = value
                    cb_items = {k: v for k, v in frame_fields.items() if isinstance(v, dict) and v.get('kind') == 'checkbox'}
                    checked_cbs = {k: v for k, v in cb_items.items() if v.get('checked')}
                    print(f"    [Page {page_num} Frame {f_idx}] Extracted {len(frame_fields)} fields ({len(cb_items)} checkboxes, {len(checked_cbs)} checked).")
            except Exception as e:
                LOGGER.exception("Source extraction failed on page %s frame %s", page_num, f_idx)

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
    (e.g., 'Warning', 'The following warnings were found', 'Do you want to continue?', 'Error').
    Always clicks 'OK' / 'Yes' / 'Confirm' / 'Continue' button on the popup to commit and unblock the UI.
    Never closes or cancels warning confirmation dialogs.
    """
    for _ in range(5):
        try:
            # Handle the Plan of Care continuation warning first by clicking OK
            if _accept_post_insert_warning(page, timeout_ms=350):
                print("--> Auto-confirmed 1 Error/Warning/Confirmation popup(s) with 'OK'!")
                page.wait_for_timeout(500)
                continue
            handled = page.evaluate("""() => {
                let count = 0;
                const visible = el => !!el && (el.offsetWidth > 0 || el.offsetHeight > 0 || el.getClientRects().length > 0);
                
                // 1. SweetAlert dialogs (.sweet-alert) -> ALWAYS click confirm / OK
                const sweetAlerts = Array.from(document.querySelectorAll(
                    '.sweet-alert.visible, .sweet-alert.showSweetAlert, .sweet-alert'
                )).filter(visible);
                for (const sa of sweetAlerts) {
                    const confirmBtn = sa.querySelector(
                        'button.confirm, button.ok, .sa-confirm-button-container button, .sa-button-container button.confirm, button.btn-primary, button'
                    );
                    if (confirmBtn && visible(confirmBtn)) {
                        confirmBtn.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window, button: 0, buttons: 1 }));
                        confirmBtn.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window, button: 0 }));
                        confirmBtn.click();
                        count++;
                    }
                }

                // 2. Query all potential dialog / popup containers
                const dialogs = Array.from(document.querySelectorAll(
                    '.modal-dialog, .modal-content, .bootbox, .ui-dialog, [role="dialog"], [class*="alert-dialog"], [class*="error-dialog"]'
                )).filter(visible);

                dialogs.forEach(d => {
                    // Never close or target the main #formFramePopup form modal
                    if (d.id === 'formFramePopup' || (d.closest('#formFramePopup') && !d.classList.contains('bootbox') && !d.classList.contains('sweet-alert') && !d.classList.contains('alert-dialog') && !d.classList.contains('error-dialog'))) {
                        return;
                    }

                    const titleEl = d.querySelector('.modal-title, .title, h1, h2, h3, h4, h5, header, [class*="title"], [class*="header"]');
                    const titleText = (titleEl && (titleEl.innerText || titleEl.textContent) || '').toLowerCase().trim();
                    const fullText = (d.innerText || d.textContent || '').toLowerCase();

                    const isPopup = (titleText && /(error|warning|alert|notice|attention|validation)/i.test(titleText)) ||
                                    fullText.includes('the following errors were found') ||
                                    fullText.includes('errors were found') ||
                                    fullText.includes('do you want to continue') ||
                                    fullText.includes('warnings were found') ||
                                    fullText.includes('warning') ||
                                    fullText.includes('please select one or more') ||
                                    d.classList.contains('warning-popup') ||
                                    d.classList.contains('confirm-popup') ||
                                    d.classList.contains('error-popup') ||
                                    d.classList.contains('bootbox') ||
                                    d.classList.contains('bootbox-alert') ||
                                    d.classList.contains('bootbox-confirm');

                    if (isPopup) {
                        const btns = Array.from(d.querySelectorAll('button, a, input[type="button"], input[type="submit"], .btn')).filter(visible);
                        // Prioritize OK / Yes / Continue / Confirm
                        const okBtn = btns.find(b => {
                            const txt = (b.innerText || b.value || '').trim().toLowerCase();
                            const dataAuto = (b.getAttribute('data-automation-id') || '').toLowerCase();
                            return txt === 'ok' ||
                                   txt === 'yes' ||
                                   txt === 'continue' ||
                                   txt === 'confirm' ||
                                   txt === 'i understand' ||
                                   dataAuto.includes('button-ok') ||
                                   dataAuto.includes('button-confirm') ||
                                   b.classList.contains('confirm') ||
                                   b.classList.contains('bootbox-accept') ||
                                   b.classList.contains('confirm-btn-ok') ||
                                   b.classList.contains('btn-primary');
                        });

                        if (okBtn) {
                            okBtn.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window, button: 0, buttons: 1 }));
                            okBtn.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window, button: 0 }));
                            okBtn.click();
                            count++;
                        }
                    }
                });

                // 3. Dismiss any toastr error/warning notifications and clear controller error flags
                const toasts = Array.from(document.querySelectorAll('#toast-container .toast, .toast-error, .toast-warning, .toast, [class*="toast"]')).filter(visible);
                toasts.forEach(t => {
                    const closeBtn = t.querySelector('.toast-close-button, .close, [aria-label="Close"]');
                    if (closeBtn) closeBtn.click();
                    else t.remove();
                    count++;
                });

                // 4. Ensure related category popup is closed and controller flags are unblocked
                try {
                    const modal = document.querySelector('#formFramePopup') || document.querySelector('.popup-modal.in') || document;
                    const scope = window.angular && window.angular.element(modal).scope();
                    if (scope && scope.carePlanModuleController) {
                        if (typeof scope.carePlanModuleController.closeTemplatePopupRelatedCategory === 'function') {
                            scope.carePlanModuleController.closeTemplatePopupRelatedCategory();
                        }
                        scope.carePlanModuleController.duplicatesFound = false;
                        scope.carePlanModuleController.disableInsertFormButton = false;
                        if (scope.carePlanModuleController.section) {
                            scope.carePlanModuleController.section.isInsertIntoFormDisabled = false;
                        }
                    }
                } catch(e) {}

                // 5. Remove lingering sweet-overlay or modal backdrops if no popup is active
                const activeAlerts = Array.from(document.querySelectorAll(
                    '.sweet-alert.visible, .bootbox.in, .modal.in:not(#formFramePopup)'
                )).filter(visible);
                if (!activeAlerts.length) {
                    document.querySelectorAll('.sweet-overlay, .modal-backdrop.fade.in').forEach(overlay => {
                        if (!document.querySelector('#formFramePopup.in, .modal.in')) {
                            overlay.remove();
                        }
                    });
                }

                return count;
            }""")
            if handled > 0:
                print(f"--> Auto-handled {handled} popup(s) / notification(s)!")
                page.wait_for_timeout(1000)
            else:
                break
        except Exception as exc:
            LOGGER.debug("Warning popup handling failed: %s", exc)
            break

_DATE_BOUNDARY_RE = re.compile(
    r"(?:Start\s+Effective\s+Date|Discontinued\s+Date)\s*:?\s*\d{1,2}/\d{1,2}/\d{2,4}",
    re.IGNORECASE,
)

# Headers that are chrome only. Do not include names that also start real
# items (e.g. "Emergency contact: spouse", "DME: walker").
_SECTION_HEADER_PREFIX_RE = re.compile(
    r"^(?:"
    r"advance directives|safety measures|homebound(?: status)?|"
    r"eligibility|(?:other\s+)?interventions?\s*/?\s*goals?|"
    r"vital sign parameters\s+interventions?|patient centered goal\s+goals?|"
    r"(?:rehospitalization/?emergency room visit risk|neurological|respiratory|"
    r"cardiovascular|gastrointestinal|genitourinary|musculoskeletal|"
    r"endocrine|integumentary|pain|medication management|pt treatment plan)\s+"
    r"(?:interventions?|goals?)|"
    r"vital sign parameters notify physician if|"
    r"goals|interventions|orders|prognosis|"
    r"physician certification|face to face(?: encounter)?|"
    r"nutritional requirements|activities permitted|"
    r"functional limitations|"
    r"mental(?:/?\s*psychosocial/?\s*cognitive)? status|"
    r"discharge planning|rehab potential"
    r")\s*:?\s+",
    re.IGNORECASE,
)

_SECTION_TITLE_ONLY_RE = re.compile(
    r"^(?:"
    r"advance directives|safety measures|homebound(?: status)?|"
    r"eligibility|(?:other\s+)?interventions?\s*/?\s*goals?|"
    r"vital sign parameters\s+interventions?|patient centered goal\s+goals?|"
    r"(?:rehospitalization/?emergency room visit risk|neurological|respiratory|"
    r"cardiovascular|gastrointestinal|genitourinary|musculoskeletal|"
    r"endocrine|integumentary|pain|medication management|pt treatment plan)\s+"
    r"(?:interventions?|goals?)|"
    r"vital sign parameters notify physician if|"
    r"goals|interventions|orders|prognosis|"
    r"dme(?:/?\s*supplies)?|supplies|emergency contact|"
    r"physician certification|face to face(?: encounter)?|"
    r"nutritional requirements|activities permitted|"
    r"functional limitations|"
    r"mental(?:/?\s*psychosocial/?\s*cognitive)? status|"
    r"discharge planning|rehab potential"
    r")\s*:?\s*$",
    re.IGNORECASE,
)

_WIDGET_NOISE_KEYS = frozenset({"clear", "select", "add", "delete"})

_CARE_PLAN_SUBSYSTEM_SECTION_KEYS = (
    "vitalsignparametershh",
    "vital_sign_parameters",
    "vitalsignparameters",
    "vitalsigns",
    "patientcenteredgoalhh",
    "patient_centered_goal",
    "patientcenteredgoal",
    "rehospitalizationriskhh",
    "rehospitalization_risk",
    "rehospitalizationrisk",
    "rehospitalization_emergency_room_visit_risk",
    "neurologicalhh",
    "neurological",
    "respiratoryhh",
    "respiratory",
    "cardiovascularhh",
    "cardiovascular",
    "gastrointestinalhh",
    "gastrointestinal",
    "genitourinaryhh",
    "genitourinary",
    "musculoskeletalhh",
    "musculoskeletal",
    "endocrinehh",
    "endocrine",
    "integumentaryhh",
    "integumentary",
    "painhh",
    "pain",
    "medicationmanagement",
    "medication_management",
    "medications",
    "pttreatmentplan",
    "pt_treatment_plan",
    "interventions",
    "goals",
    "interventions_goals",
    "other_interventions_goals",
)


def _normalize_item_key(text):
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


_LINKED_ACTION_SUFFIX_RE = re.compile(
    r"(?:summary[\s_-]*of[\s_-]*problems[\s_-]*)?request$|"
    r"action(?:[\s_-]*\d+)?$|(?:link|button)$",
    re.IGNORECASE,
)
_LINKED_IDENTITY_NOISE = frozenset(
    {
        "action", "request", "button", "link", "open", "update", "select",
        "linked", "form", "plan", "workflow", "current", "legacy", "new",
    }
)
_LINKED_UI_CHROME_KEYS = frozenset({
    "clear", "select", "add", "delete", "remove", "edit", "cancel", "close",
    "update", "none", "no records found",
})
_NON_LINKED_ACTION_STEMS = frozenset({"m_details", "update_diagnosis"})


def _linked_action_stem(value):
    """Normalize a runtime action binding without knowing its clinical name."""
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(value or "")).strip()
    previous = None
    while text and text != previous:
        previous = text
        text = _LINKED_ACTION_SUFFIX_RE.sub("", text).strip(" _-")
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _linked_identity_tokens(*values):
    tokens = set()
    for value in values:
        stem = _linked_action_stem(value)
        tokens.update(
            token
            for token in re.findall(r"[a-z0-9]+", stem)
            if len(token) >= 3 and token not in _LINKED_IDENTITY_NOISE
        )
    return tokens


def _is_non_linked_action(*values):
    """Return True for informational controls that are not clinical linked forms."""
    return any(
        _linked_action_stem(value) in _NON_LINKED_ACTION_STEMS
        for value in values
        if value
    )


def _source_linked_records(extracted_fields):
    """Return de-duplicated populated linked records without collapsing their IDs."""
    records = []
    seen = set()
    for field_key, value in extracted_fields.items():
        if not isinstance(value, dict) or value.get("kind") not in {
            "composite", "integrated"
        }:
            continue
        if _is_non_linked_action(
            value.get("action_id"), value.get("action_stem"), value.get("section")
        ):
            continue
        if _normalize_item_key(value.get("title")) in _LINKED_UI_CHROME_KEYS:
            continue
        payload = str(value.get("value") or "").strip()
        items = [
            item
            for item in _split_composite_items(payload, value.get("items") or [])
            if _normalize_item_key(item) not in _LINKED_UI_CHROME_KEYS
        ]
        if not payload or not items:
            continue
        section = str(value.get("section") or "").strip().lower()
        action_id = str(value.get("action_id") or "").strip()
        title = str(value.get("title") or "").strip()
        identity = (
            _linked_action_stem(action_id)
            or _linked_action_stem(section)
            or _linked_action_stem(title)
            or _linked_action_stem(field_key)
        )
        payload_key = "\n".join(_normalize_item_key(item) for item in items)
        record_key = (value.get("kind"), identity, payload_key)
        if record_key in seen:
            continue
        seen.add(record_key)
        records.append({
            "record_key": "|".join(record_key),
            "section": section,
            "action_id": action_id,
            "action_stem": _linked_action_stem(action_id),
            "title": title or section or action_id,
            "items": items,
            "value": payload,
            "kind": value.get("kind"),
            "identity_tokens": sorted(_linked_identity_tokens(
                field_key, section, action_id, title, *(value.get("keys") or [])
            )),
        })
    return records


def _linked_item_overlap(target_items, source):
    target_items = [
        str(item or "").strip()
        for item in target_items or []
        if str(item or "").strip()
    ]
    source_items = source.get("items") or _split_composite_items(source.get("value"))
    if not target_items or not source_items:
        return 0
    return sum(
        _row_matches_source(target_item, source.get("value", ""), source_items)
        for target_item in target_items
    )


def _resolve_source_for_target(entry, source_records, source_sections=None, target_items=None):
    """Resolve one target action from runtime bindings and optional modal choices."""
    source_sections = source_sections or {}
    section = str(entry.get("section") or "").strip().lower()
    if section and section in source_sections:
        direct = dict(source_sections[section])
        if section in {"interventions_goals", "interventions", "goals"}:
            direct["matched_record_keys"] = [
                record["record_key"]
                for record in source_records
                if any(
                    stem in {str(record.get("section") or "").lower(), str(record.get("action_stem") or "").lower()}
                    for stem in _CARE_PLAN_SUBSYSTEM_SECTION_KEYS
                ) or "intervention" in str(record.get("section") or "").lower()
                   or "goal" in str(record.get("section") or "").lower()
            ]
        else:
            direct.setdefault("matched_record_keys", [
                record["record_key"]
                for record in source_records
                if section in {
                    str(record.get("section") or "").lower(),
                    str(record.get("action_stem") or "").lower(),
                }
            ])
        return direct

    target_tokens = _linked_identity_tokens(
        section,
        entry.get("actionId"),
        entry.get("actionStem"),
        entry.get("title"),
        entry.get("elementId"),
        entry.get("dataSection"),
    )
    target_stems = {
        stem for stem in (
            _linked_action_stem(entry.get("actionId")),
            _linked_action_stem(entry.get("actionStem")),
            _linked_action_stem(section),
            _linked_action_stem(entry.get("title")),
        ) if stem
    }
    target_compact_stems = {stem.replace("_", "") for stem in target_stems}

    ranked = []
    for record in source_records:
        record_stems = {
            stem for stem in (
                _linked_action_stem(record.get("action_id")),
                _linked_action_stem(record.get("action_stem")),
                _linked_action_stem(record.get("section")),
                _linked_action_stem(record.get("title")),
            ) if stem
        }
        record_tokens = set(record.get("identity_tokens") or [])
        record_compact_stems = {stem.replace("_", "") for stem in record_stems}
        score = 0
        if target_stems & record_stems:
            score += 120
        elif target_compact_stems & record_compact_stems:
            score += 120
        elif any(
            len(left) >= 6 and (left in right or right in left)
            for left in target_compact_stems
            for right in record_compact_stems
        ):
            score += 90
        elif any(
            len(left) >= 6 and (left in right or right in left)
            for left in target_stems
            for right in record_stems
        ):
            score += 80
        if target_tokens and record_tokens:
            intersection = target_tokens & record_tokens
            union = target_tokens | record_tokens
            score += int(60 * len(intersection) / len(union))
        overlap = _linked_item_overlap(target_items, record)
        score += overlap * 150
        ranked.append((score, overlap, record))

    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    if not ranked or ranked[0][0] < 60:
        return None
    if len(ranked) > 1 and ranked[1][0] == ranked[0][0] and ranked[0][1] == 0:
        return None

    best_score, best_overlap, best = ranked[0]
    related = [
        record for score, overlap, record in ranked
        if score == best_score or (best_overlap and overlap)
    ]
    merged_items = []
    seen_items = set()
    for record in related:
        for item in record.get("items") or []:
            key = _normalize_item_key(item)
            if key and key not in seen_items:
                seen_items.add(key)
                merged_items.append(item)
    resolved = dict(best)
    resolved["items"] = merged_items or list(best.get("items") or [])
    resolved["value"] = "\n\n".join(resolved["items"])
    resolved["matched_record_keys"] = [record["record_key"] for record in related]
    return resolved


def _strip_section_titles(text):
    cleaned = str(text or "").strip(" \t:-")
    return _SECTION_HEADER_PREFIX_RE.sub("", cleaned, count=1).strip(" \t:-")


def _split_composite_items(value, items=None):
    """Split a concatenated COP blob into individual template item names.

    Source notes often store Advance Directives as one line, e.g.
    ``ADVANCE DIRECTIVES: Full code Start Effective Date: 09/25/2025
    Health care Proxy Start Effective Date: 09/25/2025``. Template matching
    needs the discrete names ``Full code`` and ``Health care Proxy``.
    """
    chunks = []
    seen = set()

    def add_text(text):
        if not text:
            return
        for piece in _DATE_BOUNDARY_RE.split(str(text)):
            for line in re.split(r"[\n\r]+", piece):
                cleaned = re.sub(r"\s+", " ", _strip_section_titles(line)).strip(" \t:-")
                if len(cleaned) < 2:
                    continue
                key = _normalize_item_key(cleaned)
                if not key or key in seen or key in _WIDGET_NOISE_KEYS:
                    continue
                if _SECTION_TITLE_ONLY_RE.match(cleaned) or _SECTION_TITLE_ONLY_RE.match(key):
                    continue
                seen.add(key)
                chunks.append(cleaned)

    for item in items or []:
        add_text(item)
    add_text(value)
    return chunks


def _source_composite_sections(extracted_fields):
    """Return source data for every discovered COP or integrated linked section."""
    sections = {}
    for field_key, value in extracted_fields.items():
        if not isinstance(value, dict):
            continue

        kind = value.get("kind")
        section = ""
        if kind in {"composite", "integrated"}:
            section = str(value.get("section") or "").strip().lower()
            if _is_non_linked_action(
                value.get("action_id"), value.get("action_stem"), section
            ):
                continue
        elif kind == "text":
            # Interventions and goals can be rendered as plain text displays
            # beside integrated-action controls rather than as COP objects.
            identifiers = [field_key, *(value.get("keys") or [])]
            normalized_ids = {
                re.sub(r"[^a-z0-9]+", "", str(identifier).lower())
                for identifier in identifiers
                if identifier
            }
            if "cpinterventions" in normalized_ids:
                section = "interventions"
            elif "cpgoals" in normalized_ids:
                section = "goals"
            else:
                continue
        else:
            continue

        title = str(value.get("title") or section).strip()
        if _normalize_item_key(title) in _LINKED_UI_CHROME_KEYS:
            continue
        identity = _normalize_item_key(" ".join((field_key, section, title)))
        if "intervention" in identity and "goal" in identity:
            # Older E1 forms commonly store the actual rows under
            # "Other Interventions/Goals" while the sibling
            # "Interventions/Goals" COP is empty. E2 collapses both into one
            # target linked form, so treat every named variant as one source.
            section = "interventions_goals"

        text = str(value.get("value") or "").strip()
        if not section or not text:
            continue
        raw_items = value.get("items") or [text]
        split_items = _split_composite_items(text, raw_items)

        if section == "interventions_goals":
            # Do not let an empty heading-only COP hide the populated "Other"
            # COP, and merge populated variants without duplicate rows.
            if not split_items:
                continue
            existing = sections.get(section)
            merged_items = list((existing or {}).get("items") or [])
            seen_items = {_normalize_item_key(item) for item in merged_items}
            for item in split_items:
                item_key = _normalize_item_key(item)
                if item_key and item_key not in seen_items:
                    seen_items.add(item_key)
                    merged_items.append(item)
            sections[section] = {
                "section": section,
                "title": "Interventions/Goals",
                "items": merged_items,
                "value": "\n\n".join(merged_items),
                "kind": value.get("kind"),
                "action_id": value.get("action_id"),
            }
        elif section not in sections or len(text) > len(sections[section]["value"]):
            sections[section] = {
                "section": section,
                "title": title,
                "items": split_items or list(raw_items),
                "value": text,
                "kind": value.get("kind"),
                "action_id": value.get("action_id"),
            }

    # Source and target forms do not always use the same layout. One may expose
    # a combined Interventions/Goals COP while the other has two integrated
    # actions, or separate clinical subsystem COP sections (e.g. OASIS-E2 ROC/SOC/Recert
    # with vitalsignparametershh, cardiovascularhh, endocrinehh, etc.) while Discharge
    # exposes a single combinedInterventionsGoalsRequest action.
    # Index both representations so the row matcher can select only
    # the items that actually appear in each target modal.
    care_plan_components = []
    existing_combined = sections.get("interventions_goals")
    if existing_combined:
        care_plan_components.append(existing_combined)
    for name in _CARE_PLAN_SUBSYSTEM_SECTION_KEYS:
        if name in sections and sections[name] not in care_plan_components:
            care_plan_components.append(sections[name])
    for name, sec_data in sections.items():
        if sec_data in care_plan_components:
            continue
        sec_name = str(name).lower()
        sec_title = str(sec_data.get("title") or "").lower()
        if ("intervention" in sec_name or "goal" in sec_name or
            "intervention" in sec_title or "goal" in sec_title):
            care_plan_components.append(sec_data)

    if care_plan_components:
        combined_items = []
        combined_text = []
        seen_items = set()
        for component in care_plan_components:
            if component.get("value"):
                combined_text.append(component["value"])
            for item in component.get("items") or []:
                item_key = _normalize_item_key(item)
                if item_key and item_key not in seen_items:
                    seen_items.add(item_key)
                    combined_items.append(item)
        if combined_items:
            sections["interventions_goals"] = {
                "section": "interventions_goals",
                "title": "Interventions/Goals",
                "items": combined_items,
                "value": "\n\n".join(combined_text),
                "kind": "integrated",
                "action_id": None,
            }
            for section in ("interventions", "goals"):
                if section not in sections:
                    sections[section] = {**sections["interventions_goals"], "section": section}
    return sections


def _cancel_open_composite(page):
    """Close an open linked-form modal without committing changes, leaving form empty."""
    for _ in range(3):
        closed = page.evaluate("""() => {
            const visible = el => !!el && (el.offsetWidth > 0 || el.offsetHeight > 0 || el.getClientRects().length > 0);
            const modal = document.querySelector('#formFramePopup') ||
                          Array.from(document.querySelectorAll('.popup-modal.in, .modal-dialog, .popup-modal')).find(visible);
            if (!modal) return false;
            const buttons = Array.from(modal.querySelectorAll('button, a, input[type="button"], input[type="submit"], .btn')).filter(visible);
            const cancel = buttons.find(b => {
                const text = (b.innerText || b.value || '').trim().toLowerCase();
                return text === 'cancel' || text === 'close' || text === 'dismiss' || text === 'back';
            });
            if (cancel) {
                cancel.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window, button: 0, buttons: 1 }));
                cancel.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window, button: 0 }));
                cancel.click();
                return true;
            }
            const close = modal.querySelector('button.close, .close, [aria-label="Close"], [ng-click*="close"], [data-dismiss="modal"]');
            if (close && visible(close)) { close.click(); return true; }
            try {
                const tbody = modal.querySelector('#mainTable tbody, .grid-table tbody');
                if (tbody && !tbody.closest('#templateView')) {
                    tbody.innerHTML = '';
                }
            } catch(e) {}
            try {
                const scope = window.angular && window.angular.element(modal).scope();
                if (scope && scope.popupController && scope.popupController.close) {
                    scope.popupController.close();
                    return true;
                }
            } catch (e) {}
            return false;
        }""")
        if closed:
            page.wait_for_timeout(500)
            handle_warning_popups(page)
            return True
    return False


def _normalize_open_linked_form_modal(page):
    """Find the visible Care Plan dialog and normalize version-specific classes."""
    return bool(page.evaluate("""() => {
        const visible = el => !!el && (el.offsetWidth > 0 || el.offsetHeight > 0 ||
            (el.getClientRects && el.getClientRects().length > 0));
        const candidates = Array.from(document.querySelectorAll(
            '#formFramePopup, .popup-modal.in, .modal.in, [role="dialog"]'
        )).filter(modal => visible(modal) && modal.id !== 'insertToTemplatePopup');
        const modal = candidates.find(node => {
            const controls = Array.from(node.querySelectorAll(
                'button, a, input[type="button"], input[type="submit"], .btn'
            ));
            const hasCarePlanAction = controls.some(control =>
                /insert\\s*(?:from\\s*template|to\\s*form)|select\\s*active|update/i.test(
                    control.innerText || control.value || ''
                )
            );
            const hasCancelAction = controls.some(control =>
                /^(?:cancel|close|dismiss|back)$/i.test(
                    String(control.innerText || control.value || '').trim()
                )
            );
            // An E2 linked section may legitimately have no compatible rows.
            // Those dialogs omit Insert/Update and expose only Cancel, but they
            // are still the linked-form modal opened by the target action.
            const hasLinkedStructure = node.id === 'formFramePopup' || !!node.querySelector(
                'table, input[type="checkbox"], .ui-grid, .ui-grid-row, .item-row, ' +
                '[role="grid"], .no-data, .empty-state'
            );
            return (hasCarePlanAction || hasCancelAction) && hasLinkedStructure;
        });
        if (!modal) return false;
        // The rest of the copier consistently supports `.popup-modal.in`.
        // E2 sometimes renders the same dialog as a plain Bootstrap modal.
        modal.classList.add('popup-modal', 'in');
        return true;
    }"""))


def _click_integrated_action(page, entry, source):
    """Physically click a Stencil integrated action and confirm its dialog opened."""
    action_id = entry.get("actionId") or source.get("action_id")
    frame_index = int(entry.get("frameIndex") or 0)
    try:
        owner = page.frames[frame_index]
    except (IndexError, TypeError):
        owner = page
    if action_id:
        host = owner.locator(f'cmwf-integrated-action[id="{action_id}"]').first
    else:
        host = owner.locator("cmwf-integrated-action").nth(
            int(entry.get("actionIndex") or 0)
        )
    try:
        if host.count() == 0 or not host.is_visible():
            return False
        host.scroll_into_view_if_needed()
        click_targets = [host, host.locator("span").first]
        for target in click_targets:
            if target.count() == 0 or not target.is_visible():
                continue
            box = target.bounding_box()
            if not box:
                continue
            page.mouse.click(
                box["x"] + box["width"] / 2,
                box["y"] + box["height"] / 2,
            )
            page.wait_for_timeout(750)
            if _normalize_open_linked_form_modal(page):
                return True
        return False
    except Exception as exc:
        LOGGER.debug("Integrated linked-form click failed for %s: %s", action_id, exc)
        return False


def _cancel_empty_composite(page):
    """Dismiss the no-selection warning and cancel its still-open linked form."""
    result = page.evaluate("""() => {
        const visible = el => !!el && (el.offsetWidth > 0 || el.offsetHeight > 0 || el.getClientRects().length > 0);
        const dialogs = Array.from(document.querySelectorAll(
            '.modal-dialog, .modal-content, .bootbox, [role="dialog"], [class*="alert"], [class*="warning"]'
        )).filter(visible);
        const warning = dialogs.find(d => /please select one or more rows to insert into form/i.test(d.innerText || d.textContent || ''));
        if (!warning) return { warning: false, ok: false };
        const ok = Array.from(warning.querySelectorAll('button, a, input[type="button"], input[type="submit"]')).find(b =>
            (b.innerText || b.value || '').trim().toLowerCase() === 'ok'
        );
        if (ok) ok.click();
        return { warning: true, ok: !!ok };
    }""")
    if not result.get("warning"):
        return False

    page.wait_for_timeout(750)
    _cancel_open_composite(page)
    handle_warning_popups(page)
    print("    No matching source rows: dismissed warning and cancelled linked form.")
    return True


def _is_category_placeholder(text):
    normalized = _normalize_item_key(text)
    return (
        not normalized
        or normalized.startswith("select a ")
        or normalized in {"select", "no category", "category"}
    )


def _option_text_matches(text, category_name):
    """True only when the visible option text is this category.

    An empty string is a substring of every needle, so blank dropdown
    rows must never count as a match.
    """
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    if not cleaned or _is_category_placeholder(cleaned):
        return False
    option_n = _normalize_item_key(cleaned)
    needle = _normalize_item_key(category_name)
    if not option_n or not needle:
        return False
    return option_n == needle or option_n.rstrip(":") == needle.rstrip(":")


def _is_non_category_toggle_text(text):
    normalized = _normalize_item_key(text)
    return normalized.startswith("select a patient") or normalized.startswith("select a chart")


def _modal_root(page):
    return page.locator("#formFramePopup, .popup-modal.in").first


def _find_category_toggle(page):
    """Locate the Category dropdown toggle inside the linked-form popup."""
    modal = _modal_root(page)
    locators = [
        modal.get_by_role("button", name=re.compile(r"^\(?\s*select a category\s*\)?$", re.I)),
        modal.locator("button, a").filter(
            has_text=re.compile(r"^\(?\s*select a category\s*\)?$", re.I)
        ),
        modal.locator('[ng-click*="toggleDropdown"]:not([ng-click*="OnArrowClick"])'),
    ]
    for loc in locators:
        try:
            count = loc.count()
        except Exception as exc:
            LOGGER.debug("Category toggle locator failed: %s", exc)
            continue
        for index in range(count):
            item = loc.nth(index)
            try:
                if not item.is_visible():
                    continue
                text = (item.inner_text() or "").strip()
                if _is_non_category_toggle_text(text):
                    continue
                if _is_category_placeholder(text) or "category" in _normalize_item_key(text) or ":" in text:
                    return item
            except Exception as exc:
                LOGGER.debug("Category toggle candidate %s failed: %s", index, exc)
    return None


def _click_select_category_button(page):
    """Press the '(Select a category)' control. Angular toggleDropdown needs a real click."""
    if _list_open_category_options(page):
        return True
    toggle = _find_category_toggle(page)
    if toggle is None:
        return False
    label = (toggle.inner_text() or "").strip() or "(Select a category)"
    print(f"    Clicking category button '{label}'...")
    toggle.scroll_into_view_if_needed()
    toggle.click()
    for name in ("No Category", "SAFETY MEASURES:", "Triage/Risk Code"):
        try:
            page.get_by_text(name, exact=False).first.wait_for(state="visible", timeout=1500)
            break
        except Exception:
            continue
    else:
        page.wait_for_timeout(600)
    for _ in range(3):
        if _list_open_category_options(page):
            return True
        page.wait_for_timeout(400)
    return bool(_list_open_category_options(page))


def _list_open_category_options(page, include_placeholders=False):
    """Read names from the open category list, including rows under 'No Category'."""
    names = page.evaluate("""() => {
        const visible = el => !!el && (el.offsetWidth > 0 || el.offsetHeight > 0 ||
            (el.getClientRects && el.getClientRects().length > 0));
        const clean = txt => String(txt || '').replace(/\\s+/g, ' ').trim();
        const firstLine = txt => clean(String(txt || '').split(/[\\n\\r]+/)[0] || '');
        const isNoCategory = txt => /^\\(?\\s*no category\\s*\\)?$/i.test(clean(txt));
        const isToggle = el => /toggleDropdown/i.test(el.getAttribute('ng-click') || '') ||
            /dropdown-toggle|multiselect/.test(el.className || '');
        const isNoise = name => {
            if (!name || name.length > 80) return true;
            if (/^\\d{1,2}:\\d{2}\\s*(am|pm)$/i.test(name)) return true;
            if (/select a (patient|chart|category)/i.test(name)) return true;
            if (/https?:\\/\\//i.test(name) ||
                /switch to|log out|live assist|batch review|insert template|cancel|close|please select/i.test(name)) {
                return true;
            }
            return false;
        };
        const itemSelector = [
            'a', 'label', '[role="option"]', 'li', '.item', '.multiSelectItem',
            '.multiselect-option', '[ng-repeat]', 'span'
        ].join(', ');
        const collect = (root, into) => {
            if (!root) return;
            for (const node of root.querySelectorAll(itemSelector)) {
                if (!visible(node) || isToggle(node)) continue;
                if (node.querySelector('a, label, li, [role="option"], .item, .multiSelectItem')) {
                    continue;
                }
                const raw = String(node.innerText || node.textContent || '');
                if (raw.split(/[\\n\\r]+/).filter(line => clean(line)).length > 1) continue;
                const name = firstLine(raw);
                if (!name || isNoise(name)) continue;
                into.push(name);
            }
        };

        const names = [];
        const roots = new Set();
        const noCat = Array.from(document.querySelectorAll(
            'a, button, label, li, div, span, [role="option"]'
        )).find(el => visible(el) && !isToggle(el) &&
            isNoCategory(el.innerText || el.textContent) &&
            clean(el.innerText || '').length < 24);
        if (noCat) {
            const root = noCat.closest(
                'ul, ol, [role="listbox"], .dropdown-menu, .multiselect-container, ' +
                '.checkboxLayer, .checkBoxContainer, .btn-group, .multiselect-parent'
            ) || noCat.parentElement;
            if (root) roots.add(root);
        }
        for (const root of document.querySelectorAll(
            '.dropdown-menu, .multiselect-container, .checkboxLayer, .checkBoxContainer, ' +
            '[role="listbox"], .ui-select-choices, .select2-results, .btn-group.open'
        )) {
            if (visible(root)) roots.add(root);
        }
        for (const root of roots) collect(root, names);
        return Array.from(new Set(names));
    }""") or []
    if include_placeholders:
        return names
    return [name for name in names if not _is_category_placeholder(name)]


def _matching_template_categories(options, section, title, source_value):
    """Prefer the category named like this linked section.

    The live picker lists every agency category. Source notes often mention
    Triage/Evacuation/Goals as well; those must not steal Safety Measures.
    """
    section_n = _normalize_item_key(str(section or "").replace("_", " "))
    title_n = _normalize_item_key(title)
    source_n = _normalize_item_key(source_value)
    compact_sec = section_n.replace(" ", "")
    compact_title = title_n.replace(" ", "")

    strong = []
    for option in options:
        option_n = _normalize_item_key(option)
        compact_opt = option_n.replace(" ", "")
        if len(compact_opt) < 3:
            continue
        if compact_sec and compact_opt == compact_sec:
            strong.append(option)
            continue
        if compact_title and compact_opt == compact_title:
            strong.append(option)
            continue
        if compact_sec and len(compact_sec) >= 8 and (
            compact_opt.startswith(compact_sec) or compact_sec.startswith(compact_opt)
        ):
            strong.append(option)
            continue
        if compact_title and len(compact_title) >= 8 and (
            compact_opt.startswith(compact_title) or compact_title.startswith(compact_opt)
        ):
            strong.append(option)
            continue
    if strong:
        return strong

    loose = []
    for option in options:
        option_n = _normalize_item_key(option)
        if len(option_n) < 6:
            continue
        if f" {option_n} " in f" {source_n} ":
            loose.append(option)
    return loose


def _select_native_category(page, category_name):
    return bool(page.evaluate("""(categoryName) => {
        const modal = document.querySelector('#formFramePopup') ||
                      document.querySelector('.popup-modal.in') || document;
        const visible = el => !!el && (el.offsetWidth > 0 || el.offsetHeight > 0 ||
            (el.getClientRects && el.getClientRects().length > 0));
        const normalize = s => String(s || '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
        const targetNorm = normalize(categoryName);
        for (const sel of modal.querySelectorAll('select')) {
            const model = (sel.getAttribute('ng-model') || '').toLowerCase();
            if (!visible(sel) || model.includes('selectedchartid') || model.includes('chartid')) {
                continue;
            }
            const opt = Array.from(sel.options).find(o => {
                const txt = normalize(o.text || o.label);
                if (!txt || txt.startsWith('select a ') || txt === 'select' || txt === 'no category') {
                    return false;
                }
                return txt === targetNorm ||
                    (txt.length >= 3 && targetNorm.includes(txt)) ||
                    (targetNorm.length >= 3 && txt.includes(targetNorm));
            });
            if (!opt) continue;
            sel.value = opt.value;
            sel.dispatchEvent(new Event('input', { bubbles: true }));
            sel.dispatchEvent(new Event('change', { bubbles: true }));
            if (typeof sel.onchange === 'function') sel.onchange();
            try {
                const s = window.angular && window.angular.element(sel).scope();
                if (s) s.$apply();
            } catch (e) {}
            return true;
        }
        return false;
    }""", category_name))


def _category_selection_applied(page, category_name):
    current = _current_category_toggle_text(page)
    if _option_text_matches(current, category_name):
        return True
    return bool(page.evaluate("""() => {
        const modal = document.querySelector('#formFramePopup') ||
                      document.querySelector('.popup-modal.in') || document;
        const visible = el => !!el && (el.offsetWidth > 0 || el.offsetHeight > 0 ||
            (el.getClientRects && el.getClientRects().length > 0));
        if (/please select a category/i.test(modal.innerText || '') &&
            Array.from(modal.querySelectorAll('input[type="checkbox"]')).filter(visible).length === 0) {
            return false;
        }
        return Array.from(modal.querySelectorAll(
            'input[type="checkbox"], .template-table, .ui-grid-render-container-body'
        )).some(visible);
    }"""))


def _pick_open_category_option(page, category_name):
    """Click the row whose visible text is the category. Never click a blank row."""
    label = str(category_name or "").strip()
    if not label:
        return False
    patterns = [re.compile(r"^\s*" + re.escape(label) + r"\s*$", re.I)]
    if label.endswith(":"):
        patterns.append(re.compile(r"^\s*" + re.escape(label[:-1]) + r"\s*$", re.I))
    locators = []
    for pattern in patterns:
        locators.extend([
            page.locator(
                ".dropdown-menu, .multiselect-container, .checkboxLayer, "
                ".checkBoxContainer, .btn-group.open, [role='listbox'], .ui-select-choices"
            ).get_by_text(pattern),
            page.locator(".multiSelectItem, .multiselect-option").get_by_text(pattern),
            # Filtering an explicit role locator is safer than get_by_role here:
            # Devero category names commonly contain '/' and ':' characters.
            page.locator('[role="option"]').filter(has_text=pattern),
        ])
    seen = _list_open_category_options(page)
    for loc in locators:
        try:
            count = loc.count()
        except Exception as exc:
            LOGGER.debug("Category option locator failed: %s", exc)
            continue
        for index in range(count):
            item = loc.nth(index)
            try:
                if not item.is_visible():
                    continue
                text = re.sub(r"\s+", " ", item.inner_text() or "").strip()
                if not _option_text_matches(text, category_name):
                    continue
                if re.search(r"insert template", text, re.I):
                    continue
                print(f"    Clicking category option '{text}'...")
                item.scroll_into_view_if_needed()
                item.click(no_wait_after=True)
                page.wait_for_timeout(800)
                if _category_selection_applied(page, category_name):
                    return True
            except Exception as exc:
                LOGGER.debug("Category option click failed for %r: %s", category_name, exc)
    print(f"    Visible category options were {seen}; could not click '{category_name}'.")
    return False


def _current_category_toggle_text(page):
    return page.evaluate("""() => {
        const visible = el => !!el && (el.offsetWidth > 0 || el.offsetHeight > 0 ||
            (el.getClientRects && el.getClientRects().length > 0));
        const modal = document.querySelector('#formFramePopup') ||
                      document.querySelector('.popup-modal.in');
        if (!modal) return '';
        const toggle = Array.from(modal.querySelectorAll(
            '[ng-click*="toggleDropdown"]:not([ng-click*="OnArrowClick"])'
        )).find(el => {
            if (!visible(el)) return false;
            const txt = (el.innerText || '').trim();
            return !/select a (patient|chart)/i.test(txt);
        });
        return toggle ? (toggle.innerText || '').trim() : '';
    }""") or ""


def _select_template_category(page, category_name):
    """Select one template category via native <select> or the live toggle dropdown."""
    current = _current_category_toggle_text(page)
    if current and not _is_category_placeholder(current) and (
        _normalize_item_key(current) == _normalize_item_key(category_name)
    ):
        return True
    if _select_native_category(page, category_name):
        return True
    listed = _list_open_category_options(page)
    if not listed:
        _click_select_category_button(page)
        listed = _list_open_category_options(page)
    if _pick_open_category_option(page, category_name):
        return True
    print(f"    Visible category options were {listed}; could not click '{category_name}'.")
    return False


def _template_category_state(page):
    """Detect a Category picker without clicking it."""
    return page.evaluate("""() => {
        const modal = document.querySelector('#formFramePopup') ||
                      document.querySelector('.popup-modal.in');
        if (!modal) return { needsPick: false, nativeOptions: [] };
        const visible = el => !!el && (el.offsetWidth > 0 || el.offsetHeight > 0 ||
            (el.getClientRects && el.getClientRects().length > 0));
        const isPlaceholder = txt => {
            const n = String(txt || '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
            return !n || n.startsWith('select a ') || n === 'select' || n === 'no category';
        };
        const nativeOptions = [];
        let hasNativeSelect = false;
        for (const sel of modal.querySelectorAll('select')) {
            const model = (sel.getAttribute('ng-model') || '').toLowerCase();
            if (!visible(sel) || model.includes('selectedchartid') || model.includes('chartid')) {
                continue;
            }
            hasNativeSelect = true;
            for (const opt of sel.options) {
                const txt = (opt.text || opt.label || '').trim();
                if (txt && !isPlaceholder(txt)) nativeOptions.push(txt);
            }
        }
        const toggle = Array.from(modal.querySelectorAll(
            'button, a, [ng-click*="toggleDropdown"]'
        )).find(b => {
            if (!visible(b)) return false;
            const ngClick = b.getAttribute('ng-click') || '';
            const txt = (b.innerText || '').trim();
            return /toggleDropdown/i.test(ngClick) &&
                (/category/i.test(txt) || isPlaceholder(txt));
        });
        const toggleText = toggle ? (toggle.innerText || '').trim() : '';
        const toggleIsPlaceholder = !!(toggle && isPlaceholder(toggleText));
        const pleaseSelect = /please select a category/i.test(modal.innerText || '');
        return {
            needsPick: toggleIsPlaceholder || pleaseSelect ||
                (hasNativeSelect && nativeOptions.length > 0 && isPlaceholder(
                    (() => {
                        const sel = Array.from(modal.querySelectorAll('select')).find(s => {
                            const model = (s.getAttribute('ng-model') || '').toLowerCase();
                            return visible(s) && !model.includes('chart');
                        });
                        if (!sel || sel.selectedIndex < 0) return '';
                        return (sel.options[sel.selectedIndex].text || '');
                    })()
                )),
            nativeOptions,
            toggleIsPlaceholder,
            toggleText
        };
    }""") or {"needsPick": False, "nativeOptions": []}


def _template_chooser_is_open(page):
    if _list_open_category_options(page):
        return True
    if _find_category_toggle(page) is not None:
        return True
    return bool(page.evaluate("""() => {
        const modal = document.querySelector('#formFramePopup') ||
                      document.querySelector('.popup-modal.in');
        if (!modal) return false;
        const visible = el => !!el && (el.offsetWidth > 0 || el.offsetHeight > 0 ||
            (el.getClientRects && el.getClientRects().length > 0));
        if (Array.from(modal.querySelectorAll('select')).some(sel => {
            const model = (sel.getAttribute('ng-model') || '').toLowerCase();
            return visible(sel) && !model.includes('chart');
        })) return true;
        return Array.from(modal.querySelectorAll('button, a')).some(b =>
            visible(b) && /insert\\s*template\\s*to/i.test(b.innerText || b.value || '')
        );
    }"""))


def _ensure_template_chooser_open(page):
    """Re-open Insert From Template only if the picker is gone.

    Re-clicking while the category menu is open closes it.
    """
    if _template_chooser_is_open(page):
        return
    page.evaluate("""() => {
        const modal = document.querySelector('#formFramePopup') ||
                      document.querySelector('.popup-modal.in') || document;
        const visible = el => !!el && (el.offsetWidth > 0 || el.offsetHeight > 0 ||
            (el.getClientRects && el.getClientRects().length > 0));
        const buttons = Array.from(modal.querySelectorAll(
            'button, a, input[type="button"], .btn'
        )).filter(visible);
        const btn = buttons.find(b => /insert\\s*from\\s*template/i.test(b.innerText || b.value || ''));
        if (!btn) return false;
        btn.dispatchEvent(new MouseEvent('mousedown', {
            bubbles: true, cancelable: true, view: window, button: 0, buttons: 1
        }));
        btn.dispatchEvent(new MouseEvent('mouseup', {
            bubbles: true, cancelable: true, view: window, button: 0
        }));
        btn.click();
        return true;
    }""")
    page.wait_for_timeout(800)


def _template_surface_is_open(page):
    """Return whether the nested Insert From Template surface is still visible."""
    return bool(page.evaluate("""() => {
        const visible = el => !!el && (el.offsetWidth > 0 || el.offsetHeight > 0 ||
            (el.getClientRects && el.getClientRects().length > 0));
        return Array.from(document.querySelectorAll(
            '#insertToTemplatePopup, #templateView, [id*="templateView"], [ng-if*="showTemplatePopup"]'
        )).some(visible);
    }"""))


_POST_INSERT_WARNING_RE = re.compile(
    r"the following warnings? were found|do you want to continue", re.I
)
_POST_INSERT_ERROR_RE = re.compile(
    r"the following errors were found|errors were found", re.I
)


def _frame_has_post_insert_error(frame):
    try:
        matches = frame.get_by_text(_POST_INSERT_ERROR_RE)
        return any(
            matches.nth(index).is_visible() for index in range(matches.count())
        )
    except Exception:
        return False


def _frame_has_post_insert_warning(frame):
    try:
        if _frame_has_post_insert_error(frame):
            return False
        matches = frame.get_by_text(_POST_INSERT_WARNING_RE)
        return any(
            matches.nth(index).is_visible() for index in range(matches.count())
        )
    except Exception:
        return False


def _post_insert_warning_is_open(page):
    """Check every current browser page/frame for the continuation warning."""
    try:
        pages = list(page.context.pages)
    except Exception:
        pages = [page]
    for candidate_page in pages:
        try:
            if candidate_page.is_closed():
                continue
            for frame in candidate_page.frames:
                if _frame_has_post_insert_warning(frame):
                    return True
        except Exception:
            continue
    return False


def _dismiss_post_insert_error_and_cancel(page, timeout_ms=1500):
    """Dismiss a rejected Insert error, then cancel its linked-form selector."""
    attempts = max(1, int(timeout_ms / 250))
    button_pattern = re.compile(r"^(?:ok|okay|close|dismiss)$", re.I)
    detected = False

    for attempt_index in range(attempts):
        for frame in page.frames:
            try:
                dialogs = frame.locator(
                    '.modal-dialog, .modal-content, .bootbox, .ui-dialog, '
                    '.sweet-alert, [role="dialog"], [class*="error"]'
                )
                for dialog_index in range(dialogs.count() - 1, -1, -1):
                    dialog = dialogs.nth(dialog_index)
                    if not dialog.is_visible():
                        continue
                    dialog_text = re.sub(
                        r"\s+", " ", dialog.inner_text() or ""
                    ).strip()
                    error_match = _POST_INSERT_ERROR_RE.search(dialog_text)
                    if not error_match:
                        continue
                    if not detected:
                        reason = dialog_text[error_match.end():].strip(" :\n")[:300]
                        print(f"    Devero insert error: {reason or 'No reason shown'}")
                        LOGGER.warning("Devero rejected linked-form insert: %s", reason or "No reason shown")
                    detected = True
                    buttons = dialog.locator(
                        'button, a, input[type="button"], '
                        'input[type="submit"], .btn'
                    )
                    for button_index in range(buttons.count()):
                        button = buttons.nth(button_index)
                        if not button.is_visible() or button.is_disabled():
                            continue
                        label = (
                            button.inner_text()
                            or button.get_attribute("value")
                            or button.get_attribute("aria-label")
                            or ""
                        ).strip()
                        if not button_pattern.match(label):
                            continue
                        print(
                            f"    Post-insert error detected; clicking '{label}'..."
                        )
                        try:
                            button.click(no_wait_after=True, timeout=2500)
                        except Exception as exc:
                            LOGGER.debug("Post-insert error OK click failed: %s", exc)
                        page.wait_for_timeout(500)
                        break
            except Exception as exc:
                LOGGER.debug("Post-insert error scan failed: %s", exc)

        if detected and not any(
            _frame_has_post_insert_error(frame) for frame in page.frames
        ):
            print("    Post-insert error was dismissed; clicking Cancel...")
            if _template_surface_is_open(page):
                _cancel_template_surface(page)
            cancelled = _cancel_open_composite(page)
            if cancelled and not _linked_form_modal_is_open(page):
                print("    Linked form was cancelled after the rejected insert.")
            else:
                print(
                    "    Linked form did not close cleanly after the rejected insert."
                )
            try:
                page.bring_to_front()
            except Exception:
                pass
            return True

        if attempt_index + 1 < attempts:
            page.wait_for_timeout(250)

    if detected:
        print("    Blocking post-insert error remains open; insert was rejected.")
    return detected


def _accept_post_insert_warning(page, timeout_ms=3000):
    """Wait for and accept Devero's warning raised by Insert to form by clicking 'OK'.

    The Plan of Care module keeps the selected rows pending while it displays
    warnings such as missing ENDOCRINE GOALS/INTERVENTIONS. The insert is not
    completed until the warning's OK/Yes control receives a real pointer click.
    DO NOT just close or dismiss with X or cancel.
    """
    attempts = max(1, int(timeout_ms / 250))
    for attempt_index in range(attempts):
        try:
            context_pages = list(page.context.pages)
        except Exception:
            context_pages = [page]
        ordered_pages = [page] + [
            candidate for candidate in reversed(context_pages) if candidate != page
        ]
        for warning_page in ordered_pages:
            try:
                if warning_page.is_closed():
                    continue
                for frame in warning_page.frames:
                    selectors = [
                        '.sweet-alert button.confirm',
                        '.sweet-alert button.ok',
                        '.sa-confirm-button-container button',
                        '.sa-button-container button.confirm',
                        '.bootbox button.bootbox-accept',
                        '.bootbox button.btn-primary',
                        'button[data-automation-id*="button-ok"]',
                        'button[data-automation-id*="button-confirm"]',
                        '.confirm-btn-ok',
                        '.modal-dialog [role="dialog"] button',
                        '.modal-dialog button.btn-primary',
                        '.modal-dialog button',
                        '[role="dialog"] button.confirm',
                        '[role="dialog"] button.btn-primary',
                        '[role="dialog"] button',
                    ]
                    for sel in selectors:
                        loc = frame.locator(sel)
                        try:
                            count = loc.count()
                        except Exception:
                            count = 0
                        for i in range(count):
                            btn = loc.nth(i)
                            try:
                                if not btn.is_visible() or btn.is_disabled():
                                    continue
                                is_match = btn.evaluate("""node => {
                                    const text = (node.innerText || node.value || '').trim().toLowerCase();
                                    const isOkText = /^(?:ok|okay|yes|continue|confirm|i understand)$/i.test(text);
                                    const dialog = node.closest('.sweet-alert, .bootbox, .modal-dialog, [role="dialog"], .alert-dialog, .warning-popup, #blockingWarning');
                                    if (!dialog) return false;
                                    if (dialog.id === 'formFramePopup' && !dialog.classList.contains('bootbox') && !dialog.classList.contains('sweet-alert') && !node.closest('#blockingWarning, [role="dialog"]')) {
                                        return false;
                                    }
                                    const dText = (dialog.innerText || dialog.textContent || '').toLowerCase();
                                    const isWarning = /warning|warnings were found|do you want to continue|please confirm|notice|attention/i.test(dText);
                                    return (isOkText || node.classList.contains('confirm') || node.classList.contains('bootbox-accept')) && (isWarning || dialog.classList.contains('sweet-alert') || dialog.classList.contains('bootbox'));
                                }""")
                                if is_match:
                                    print(f"    Post-insert warning detected; clicking 'OK' on {btn}...")
                                    btn.scroll_into_view_if_needed()
                                    try:
                                        box = btn.bounding_box()
                                        if box:
                                            warning_page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                                        else:
                                            btn.click(no_wait_after=True, force=True)
                                    except Exception:
                                        btn.click(no_wait_after=True, force=True)
                                    warning_page.wait_for_timeout(500)
                                    return True
                            except Exception:
                                continue
            except Exception as exc:
                LOGGER.debug("Post-insert warning scan on page failed: %s", exc)

        if attempt_index + 1 < attempts:
            page.wait_for_timeout(250)
    return False


def _click_template_insert(page):
    """Commit selected template rows with one trusted click.

    Some Plan of Care versions render the accordion directly in
    ``#formFramePopup`` instead of creating a separately identifiable nested
    template popup. Include the active linked-form modal as the final fallback
    so its visible Insert to form button is not missed.
    """
    # A warning overlay intercepts pointer events even though the Insert button
    # remains visible underneath it.
    handle_warning_popups(page)
    page.wait_for_timeout(250)
    surfaces = page.locator(
        '#insertToTemplatePopup, #templateView, [id*="templateView"], '
        '[ng-if*="showTemplatePopup"], #formFramePopup, .popup-modal.in'
    )
    button_pattern = re.compile(
        r"^\s*(?:insert\s+template(?:\s+to.*)?|insert\s+to\s+form|add\s+to\s+form)\s*$",
        re.I,
    )
    try:
        surface_count = surfaces.count()
    except Exception:
        return False

    for surface_index in range(surface_count - 1, -1, -1):
        surface = surfaces.nth(surface_index)
        try:
            if not surface.is_visible():
                continue
            buttons = surface.locator(
                'button, a, input[type="button"], input[type="submit"], .btn'
            )
            for button_index in range(buttons.count()):
                button = buttons.nth(button_index)
                if not button.is_visible() or button.is_disabled():
                    continue
                label = (button.inner_text() or button.get_attribute("value") or "").strip()
                if not button_pattern.match(label):
                    continue
                print(f"    Clicking template popup button '{label}'...")
                native_warning = {"accepted": False}

                def accept_native_warning(dialog):
                    message = str(dialog.message or "")
                    if re.search(
                        r"warning|warnings were found|do you want to continue",
                        message,
                        re.I,
                    ):
                        print("    Post-insert browser warning detected; accepting it...")
                        dialog.accept()
                        native_warning["accepted"] = True

                page.on("dialog", accept_native_warning)
                button.scroll_into_view_if_needed()
                try:
                    box = button.bounding_box()
                    if box:
                        page.mouse.click(
                            box["x"] + box["width"] / 2,
                            box["y"] + box["height"] / 2,
                        )
                    else:
                        button.click(no_wait_after=True)
                    for _ in range(3):
                        if not _post_insert_warning_is_open(page):
                            break
                        if not _accept_post_insert_warning(page, timeout_ms=1500):
                            break
                        page.wait_for_timeout(500)
                finally:
                    try:
                        page.remove_listener("dialog", accept_native_warning)
                    except Exception:
                        pass
                if _post_insert_warning_is_open(page):
                    print(
                        "    Post-insert warning is still open; template commit "
                        "has not completed."
                    )
                    return False
                return True
        except Exception as exc:
            LOGGER.debug("Template insert click failed: %s", exc)
            return False
    return False


def _linked_form_modal_is_open(page):
    """Return whether the outer linked-form modal remains visible."""
    return bool(page.evaluate("""() => {
        const visible = el => !!el &&
            (el.offsetWidth > 0 || el.offsetHeight > 0 ||
             (el.getClientRects && el.getClientRects().length > 0)) &&
            getComputedStyle(el).display !== 'none' &&
            getComputedStyle(el).visibility !== 'hidden';
        return Array.from(document.querySelectorAll(
            '#formFramePopup, .popup-modal.in'
        )).some(visible);
    }"""))


def _cancel_template_surface(page):
    """Close only the nested template chooser so it cannot block later sections."""
    surfaces = page.locator(
        '#insertToTemplatePopup, #templateView, [id*="templateView"], '
        '[ng-if*="showTemplatePopup"]'
    )
    try:
        for surface_index in range(surfaces.count() - 1, -1, -1):
            surface = surfaces.nth(surface_index)
            if not surface.is_visible():
                continue
            controls = surface.locator(
                'button, a, input[type="button"], input[type="submit"], .btn, .close'
            )
            for control_index in range(controls.count()):
                control = controls.nth(control_index)
                if not control.is_visible():
                    continue
                label = (
                    control.inner_text()
                    or control.get_attribute("value")
                    or control.get_attribute("aria-label")
                    or ""
                ).strip()
                if not re.match(r"^\s*(?:cancel|close|back|×)\s*$", label, re.I):
                    continue
                control.click(no_wait_after=True, force=True)
                page.wait_for_timeout(500)
                return True
    except Exception as exc:
        LOGGER.debug("Template popup close failed: %s", exc)
    return False


def _select_visible_template_matches(page, source_value, items=None):
    """Select source-matching choices in the one currently open group."""
    phrases = _split_composite_items(source_value, items)
    checkboxes = page.locator(
        '#formFramePopup input[ng-model="row.isSelected"], '
        '.popup-modal.in input[ng-model="row.isSelected"], '
        '#formFramePopup tr input[type="checkbox"], '
        '#formFramePopup .item-row input[type="checkbox"], '
        '#formFramePopup .grid-row input[type="checkbox"], '
        '#formFramePopup label input[type="checkbox"], '
        '.popup-modal.in label input[type="checkbox"]'
    )
    candidates = 0
    selected = 0
    try:
        count = checkboxes.count()
    except Exception:
        return {"candidates": 0, "selected": 0}

    for index in range(count):
        checkbox = checkboxes.nth(index)
        try:
            if not checkbox.is_visible() or checkbox.is_disabled():
                continue
            if "selectall" in (checkbox.get_attribute("ng-model") or "").lower():
                continue
            candidates += 1
            raw = checkbox.evaluate("""cb => {
                const label = cb.closest('label');
                if (label) return label.innerText || label.textContent || '';
                const row = cb.closest(
                    'tr, .ui-grid-row, .item-row, .grid-row, .template-item, li, .checkbox'
                );
                if (row && row.querySelectorAll('input[type="checkbox"]').length === 1) {
                    return row.innerText || row.textContent || '';
                }
                if (cb.parentElement &&
                    cb.parentElement.querySelectorAll('input[type="checkbox"]').length === 1) {
                    return cb.parentElement.innerText || cb.parentElement.textContent || '';
                }
                let node = cb.nextSibling;
                let text = '';
                while (node) {
                    if (node.nodeType === 3) text += node.textContent + ' ';
                    else if (node.nodeType === 1) {
                        if (node.tagName === 'INPUT' && node.type === 'checkbox') break;
                        text += (node.innerText || node.textContent || '') + ' ';
                    }
                    node = node.nextSibling;
                }
                return text;
            }""")
            # Target rows may show a target-date value that is intentionally
            # different from the completed source note's effective date.
            comparable = _DATE_BOUNDARY_RE.sub(" ", raw or "")
            comparable = re.sub(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b", " ", comparable)
            comparable = re.sub(r"\s+", " ", comparable).strip()
            if not comparable or not _row_matches_source(
                comparable, source_value, phrases
            ):
                continue

            if not checkbox.is_checked():
                checkbox.click(no_wait_after=True)
                page.wait_for_timeout(150)
            if not checkbox.is_checked():
                LOGGER.debug(
                    "Trusted linked-row click did not select candidate %d; "
                    "leaving it uncounted.",
                    index,
                )
                continue
            if checkbox.is_checked():
                selected += 1
        except Exception as exc:
            LOGGER.debug("Visible template item selection failed: %s", exc)
    return {"candidates": candidates, "selected": selected}


def _expand_template_groups(page, on_group_open=None):
    """Open accordion groups, processing each before opening the next one."""
    surfaces = page.locator(
        '#insertToTemplatePopup, #templateView, [id*="templateView"], '
        '[ng-if*="showTemplatePopup"], #formFramePopup, .popup-modal.in'
    )
    scope = None
    try:
        for index in range(surfaces.count() - 1, -1, -1):
            candidate_surface = surfaces.nth(index)
            if candidate_surface.is_visible():
                scope = candidate_surface
                break
    except Exception:
        return 0
    if scope is None:
        return 0

    candidate_selector = (
        '[aria-expanded="false"], .accordion-toggle, .accordion-button.collapsed, '
        '.panel-heading, .panel-title, .accordion-header, .group-header, .group-title, '
        '[data-toggle="collapse"], [data-bs-toggle="collapse"], '
        '[ng-click*="toggle"], [ng-click*="Toggle"], [ng-click*="expand"], '
        '[ng-click*="collapse"], [ng-click*="isOpen"]'
    )
    candidates = scope.locator(candidate_selector)
    clicked_labels = set()
    expanded = 0

    def visible_choice_count():
        """Count choices currently exposed by the accordion surface."""
        try:
            return scope.evaluate("""root => {
                const visible = el => !!el &&
                    (el.offsetWidth > 0 || el.offsetHeight > 0 ||
                     (el.getClientRects && el.getClientRects().length > 0));
                return Array.from(root.querySelectorAll(
                    'input[type="checkbox"], tbody tr, .ui-grid-row, .item-row, '
                    '.grid-row, .template-item'
                )).filter(visible).length;
            }""")
        except Exception:
            return 0

    def group_is_open(target, choices_before):
        """Verify the click changed this header or exposed item choices."""
        try:
            opened = target.evaluate("""node => {
                const visible = el => !!el &&
                    (el.offsetWidth > 0 || el.offsetHeight > 0 ||
                     (el.getClientRects && el.getClientRects().length > 0)) &&
                    getComputedStyle(el).display !== 'none' &&
                    getComputedStyle(el).visibility !== 'hidden';
                if (node.getAttribute('aria-expanded') === 'true') return true;
                if (node.classList.contains('open') ||
                    node.classList.contains('expanded')) return true;

                const selectors = [
                    node.getAttribute('data-target'),
                    node.getAttribute('data-bs-target'),
                    (node.getAttribute('href') || '').startsWith('#') ?
                        node.getAttribute('href') : ''
                ].filter(Boolean);
                for (const selector of selectors) {
                    try {
                        const body = document.querySelector(selector);
                        if (visible(body) &&
                            (!body.classList.contains('collapse') ||
                             body.classList.contains('in') ||
                             body.classList.contains('show'))) return true;
                    } catch (e) {}
                }

                const itemSelector =
                    'input[type="checkbox"], tbody tr, .ui-grid-row, .item-row, ' +
                    '.grid-row, .template-item';
                const possibleBodies = [
                    node.nextElementSibling,
                    node.parentElement && node.parentElement.nextElementSibling
                ].filter(Boolean);
                const container = node.closest(
                    '.panel, .card, .accordion-item, .group'
                );
                if (container && container.querySelectorAll(
                    '[aria-expanded], .accordion-toggle, .panel-heading, .group-header'
                ).length <= 1) possibleBodies.push(container);
                return possibleBodies.some(body => visible(body) &&
                    Array.from(body.querySelectorAll(itemSelector)).some(visible));
            }""")
            if opened:
                return True
        except Exception:
            pass
        return visible_choice_count() > choices_before

    def physically_expand(target):
        """Use the right-hand disclosure control and confirm it really opened."""
        try:
            if group_is_open(target, visible_choice_count()):
                return True
            target.scroll_into_view_if_needed()
            choices_before = visible_choice_count()

            # First, check if there is an explicit chevron/disclosure icon inside the target
            icons = target.locator(
                'i, svg, span[class*="chevron"], [class*="chevron"], [class*="arrow"], '
                '[class*="caret"], [class*="angle"], [class*="toggle"], [class*="collapse-icon"]'
            )
            visible_icons = []
            try:
                icon_count = icons.count()
            except Exception:
                icon_count = 0
            for icon_index in range(icon_count):
                icon = icons.nth(icon_index)
                if icon.is_visible():
                    icon_box = icon.bounding_box()
                    if icon_box:
                        visible_icons.append((icon_box["x"], icon))
            if visible_icons:
                visible_icons.sort(key=lambda pair: pair[0], reverse=True)
                visible_icons[0][1].click(no_wait_after=True, force=True)
                page.wait_for_timeout(600)
                if group_is_open(target, choices_before):
                    return True

            box = target.bounding_box()
            if box:
                # Devero's Plan of Care rows expand only from the chevron at
                # the far right. Clicking the label/centre can instead invoke
                # related-category validation.
                inset = min(18, max(6, box["width"] / 5))
                page.mouse.click(
                    box["x"] + box["width"] - inset,
                    box["y"] + box["height"] / 2,
                )
            else:
                target.click(no_wait_after=True)
            page.wait_for_timeout(600)
            if group_is_open(target, choices_before):
                return True
        except Exception as exc:
            LOGGER.debug("Physical template group expansion failed: %s", exc)
        return False

    try:
        count = candidates.count()
    except Exception:
        count = 0
    for index in range(count):
        candidate = candidates.nth(index)
        try:
            if not candidate.is_visible():
                continue
            label = re.sub(r"\s+", " ", candidate.inner_text() or "").strip()
            if not label or len(label) > 160 or re.search(
                r"insert|cancel|close|related\s*category|select\s+(?:all|active|a category|options?|option\(s\)|category)",
                label,
                re.I,
            ):
                continue
            # Avoid clicking related category sub-buttons
            is_related_cat = candidate.evaluate("""node => {
                const click = (node.getAttribute('ng-click') || '').toLowerCase();
                const cls = (node.className || '').toLowerCase();
                return click.includes('relatedcategory') || cls.includes('related-category');
            }""")
            if is_related_cat:
                continue

            key = label.lower()
            if key in clicked_labels:
                continue
            is_closed = candidate.evaluate("""node => {
                if (node.getAttribute('aria-expanded') === 'false') return true;
                if (node.classList.contains('collapsed')) return true;
                const targetSelector = node.getAttribute('data-target') ||
                    node.getAttribute('data-bs-target') ||
                    ((node.getAttribute('href') || '').startsWith('#') ?
                        node.getAttribute('href') : '');
                if (targetSelector) {
                    try {
                        const body = document.querySelector(targetSelector);
                        if (body && (body.offsetHeight === 0 ||
                            getComputedStyle(body).display === 'none' ||
                            (!body.classList.contains('in') &&
                             !body.classList.contains('show') &&
                             body.classList.contains('collapse')))) return true;
                    } catch (e) {}
                }
                const body = node.nextElementSibling;
                return !!body && (body.offsetHeight === 0 ||
                    getComputedStyle(body).display === 'none');
            }""")
            if not is_closed:
                if group_is_open(candidate, visible_choice_count()):
                    clicked_labels.add(key)
                    if on_group_open:
                        on_group_open(label)
                continue
            if physically_expand(candidate):
                clicked_labels.add(key)
                expanded += 1
                print(f"    Expanded template group '{label}' (verified).")
                if on_group_open:
                    on_group_open(label)
                # Quick check to ensure no related-category modal or toast lingered
                handle_warning_popups(page)
        except Exception as exc:
            LOGGER.debug("Template group expansion failed: %s", exc)

    # The structural fallback is only for variants where the primary
    # accordion selectors found nothing. Scanning it after real groups were
    # processed is slow and can mistake controls such as Batch Update Dates
    # for a clinical group.
    if clicked_labels:
        return expanded

    # Some E2 variants render plain buttons/links with no accordion class.
    fallback_candidates = scope.locator(
        'button, a, [role="button"], [ng-click], .panel > :first-child, '
        '.card > :first-child, .accordion-item > :first-child, .group > :first-child'
    )
    try:
        fallback_count = fallback_candidates.count()
    except Exception:
        fallback_count = 0
    for index in range(fallback_count):
        target = fallback_candidates.nth(index)
        try:
            if not target.is_visible():
                continue
            label = re.sub(r"\s+", " ", target.inner_text() or "").strip()
            key = label.lower()
            if (
                not label
                or len(label) > 160
                or key in clicked_labels
                or re.search(
                    r"insert|cancel|close|related\s*category|select\s+(?:all|active|a category|options?|option\(s\)|category)",
                    label,
                    re.I,
                )
            ):
                continue
            is_disclosure = target.evaluate("""node => {
                if (node.querySelector('input[type="checkbox"]')) return false;
                const handler = [
                    node.getAttribute('ng-click'),
                    node.getAttribute('onclick')
                ].filter(Boolean).join(' ');
                if (/relatedcategory/i.test(handler)) return false;
                if (/toggle|expand|collapse|isOpen/i.test(handler)) return true;
                if (node.matches(
                    '[aria-expanded], [data-target], [data-bs-target], [data-toggle="collapse"]'
                )) return true;
                if (node.querySelector(
                    'i, svg, [class*="chevron"], [class*="arrow"], [class*="caret"]'
                )) return true;
                const choices = selectorRoot => !!selectorRoot &&
                    selectorRoot.querySelector(
                        'input[type="checkbox"], tbody tr, .ui-grid-row, ' +
                        '.item-row, .grid-row, .template-item'
                    );
                if (choices(node.nextElementSibling)) return true;
                const container = node.closest(
                    '.panel, .card, .accordion-item, .group'
                );
                return choices(container);
            }""")
            if not is_disclosure:
                continue
            if group_is_open(target, visible_choice_count()):
                clicked_labels.add(key)
                if on_group_open:
                    on_group_open(label)
                continue
            if physically_expand(target):
                clicked_labels.add(key)
                expanded += 1
                print(f"    Expanded template group '{label}' (verified).")
                if on_group_open:
                    on_group_open(label)
                handle_warning_popups(page)
        except Exception as exc:
            LOGGER.debug("Structural template group discovery failed: %s", exc)
    return expanded


def _categories_to_process(page, source_value, section, title):
    """Open the category picker if needed and return categories to populate."""
    state = _template_category_state(page)
    if not state.get("needsPick"):
        return []
    options = list(state.get("nativeOptions") or [])
    if state.get("toggleIsPlaceholder") or not options:
        _click_select_category_button(page)
        opened = _list_open_category_options(page, include_placeholders=True)
        if opened:
            options = opened
    print(f"    Discovered {len(options)} template category/categories to populate: {options}")
    selectable = [name for name in options if not _is_category_placeholder(name)]
    matching = _matching_template_categories(selectable, section, title, source_value)
    if matching:
        print(f"    Will select matching categor(y/ies) for this section: {matching}")
        return matching
    if len(selectable) == 1:
        return selectable
    fallback = _section_category_fallback(section, title)
    if fallback:
        print(f"    Category list incomplete; trying section category {fallback}")
        return fallback
    return []


def _section_category_fallback(section, title):
    """If the open list was not scraped, still try the section's own category name."""
    aliases = {
        "safety_measures": ["SAFETY MEASURES:", "SAFETY MEASURES"],
        "advanced_directives": ["ADVANCE DIRECTIVES:", "ADVANCE DIRECTIVES"],
        "eligibility": ["ELIGIBILITY:", "ELIGIBILITY"],
        "mental_status": ["MENTAL STATUS:", "MENTAL STATUS"],
        "goals": ["GOALS:", "GOALS"],
        "interventions": ["INTERVENTIONS:", "INTERVENTIONS"],
    }
    guessed = list(aliases.get(str(section or "").strip().lower(), []))
    title_n = str(title or "").splitlines()[0].strip()
    if title_n:
        guessed.append(title_n)
        if not title_n.endswith(":"):
            guessed.append(title_n.upper() + ":")
    return guessed[:2]


def _main_modal_data_rows(page):
    """Visible data rows in the linked-form list (after Insert From Template)."""
    return page.evaluate("""() => {
        const modal = document.querySelector('#formFramePopup') ||
                      document.querySelector('.popup-modal.in');
        if (!modal) return [];
        const visible = el => !!el && (el.offsetWidth > 0 || el.offsetHeight > 0 ||
            (el.getClientRects && el.getClientRects().length > 0));
        const rows = Array.from(modal.querySelectorAll(
            'tbody tr, .ui-grid-row, .item-row, .grid-row'
        )).filter(row => {
            if (!visible(row) || row.querySelector('th') || row.closest('thead')) return false;
            if (row.closest(
                '#insertToTemplatePopup, #templateView, [id*="templateView"], [ng-if*="showTemplatePopup"]'
            )) return false;
            const text = (row.innerText || '').replace(/\\s+/g, ' ').trim();
            const meaningful = text
                .replace(/start effective date\\s*:?\\s*\\d{1,2}\\/\\d{1,2}\\/\\d{2,4}/gi, ' ')
                .replace(/discontinued date\\s*:?\\s*\\d{1,2}\\/\\d{1,2}\\/\\d{2,4}/gi, ' ')
                .replace(/\\d{1,2}\\/\\d{1,2}\\/\\d{2,4}/g, ' ')
                .replace(/\\b(?:delete|remove|edit)\\b/gi, ' ')
                .replace(/\\s+/g, ' ')
                .trim();
            return meaningful && !/no records found/i.test(meaningful);
        });
        return rows.map(row => (row.innerText || '').replace(/\\s+/g, ' ').trim());
    }""") or []


def _row_matches_source(row_text, source_value, phrases):
    row_n = _normalize_item_key(row_text)
    if len(row_n) < 3:
        return False
    source_n = _normalize_item_key(source_value)
    if source_n and f" {row_n} " in f" {source_n} ":
        return True
    for phrase in phrases or []:
        phrase_n = _normalize_item_key(phrase)
        if len(phrase_n) < 3:
            continue
        if phrase_n in row_n or row_n in phrase_n:
            return True
        if "__" in str(row_text) or "__" in str(phrase):
            row_template = _normalize_item_key(
                re.sub(r"_*\d+(?:\.\d+)?_*|_+", " ", str(row_text))
            )
            phrase_template = _normalize_item_key(
                re.sub(r"_*\d+(?:\.\d+)?_*|_+", " ", str(phrase))
            )
            if len(row_template) >= 12 and row_template == phrase_template:
                return True
    return False


def _select_main_modal_rows(page, source_value, items=None):
    """Check source-matching rows in the now-populated linked-form list."""
    phrases = _split_composite_items(source_value, items)
    loc = page.locator(
        "#formFramePopup tbody tr, #formFramePopup .ui-grid-row, "
        "#formFramePopup .item-row, .popup-modal.in tbody tr, .popup-modal.in .item-row"
    )
    selected = 0
    try:
        count = loc.count()
    except Exception:
        return 0
    for index in range(count):
        row = loc.nth(index)
        try:
            if not row.is_visible():
                continue
            if row.evaluate("""node => !!node.closest(
                '#insertToTemplatePopup, #templateView, [id*="templateView"], [ng-if*="showTemplatePopup"]'
            )"""):
                continue
            text = re.sub(r"\s+", " ", row.inner_text() or "").strip()
            if not text or re.search(r"no records found", text, re.I):
                continue
            checkbox = row.locator('input[type="checkbox"]').first
            if checkbox.count() == 0:
                continue
            if not checkbox.is_checked():
                checkbox.click()
            selected += 1
        except Exception as exc:
            LOGGER.debug("Main modal row select failed: %s", exc)
    if selected:
        print(f"    Selected {selected} row(s) in the linked-form list.")
    return selected


def _click_insert_to_form(page):
    """Press the linked-form commit control once with a trusted pointer event."""
    modal = page.locator("#formFramePopup, .popup-modal.in").first
    commit_label = re.compile(r"^\s*insert to form\s*$", re.I)
    locators = [
        modal.get_by_role("button", name=commit_label),
        modal.locator('[data-automation-id="button-insert-to-form"]'),
        modal.locator("button, a, .btn").filter(
            has_text=commit_label
        ),
    ]
    for loc in locators:
        try:
            if loc.count() == 0 or not loc.first.is_visible():
                continue
            label = (loc.first.inner_text() or "Insert to form").strip()
            print(f"    Clicking '{label}'...")
            loc.first.scroll_into_view_if_needed()
            box = loc.first.bounding_box()
            if box:
                page.mouse.click(
                    box["x"] + box["width"] / 2,
                    box["y"] + box["height"] / 2,
                )
            else:
                loc.first.click(no_wait_after=True)
            if _dismiss_post_insert_error_and_cancel(page):
                return "blocked_error"
            _accept_post_insert_warning(page)
            if _post_insert_warning_is_open(page):
                print(
                    "    Post-insert warning is still open; Insert to form "
                    "has not completed."
                )
            return "attempted"
        except Exception as exc:
            LOGGER.debug("Insert to form click failed: %s", exc)
            # The modal can disappear while Playwright completes a successful
            # click. Do not try an overlapping selector and double-submit.
            return "attempted"
    return False


def _target_linked_section_text(page, entry):
    """Read committed linked-section text only from the target page/frames."""
    read_script = """(entry) => {
        const cleanKey = value => String(value || '')
            .toLowerCase().replace(/[^a-z0-9]/g, '');
        const chromeKeys = new Set([
            'clear', 'select', 'add', 'delete', 'remove', 'edit', 'cancel',
            'close', 'update', 'none', 'norecordsfound'
        ]);
        const meaningfulText = (node, title) => {
            const titleKey = cleanKey(title);
            return String(node?.innerText || node?.textContent || '')
                .split(/[\\n\\r]+/)
                .map(line => line.replace(/\\s+/g, ' ').trim())
                .filter(line => {
                    const key = cleanKey(line);
                    if (!key || key === titleKey || chromeKeys.has(key)) return false;
                    const suffix = titleKey && key.startsWith(titleKey) ?
                        key.slice(titleKey.length) : '';
                    const prefix = titleKey && key.endsWith(titleKey) ?
                        key.slice(0, -titleKey.length) : '';
                    return !chromeKeys.has(suffix) && !chromeKeys.has(prefix);
                })
                .join('\\n');
        };
        const committedDisplayText = (node, title) => {
            if (node.tagName?.toLowerCase() !== 'cmwf-cop') {
                return meaningfulText(node, title);
            }
            return Array.from(node.querySelectorAll(
                '.cop-items .copElement, .copElement'
            )).map(item => meaningfulText(item, title)).filter(Boolean).join('\\n');
        };
        const section = entry.section || '';
        const transientSurfaceSelector = [
            '#formFramePopup', '#insertToTemplatePopup', '#templateView',
            '[id*="templateView"]', '[ng-if*="showTemplatePopup"]',
            '.popup-modal', '.modal', '[role="dialog"]', '.sweet-alert'
        ].join(',');
        if (entry.kind === 'integrated') {
            const actions = Array.from(document.querySelectorAll(
                'cmwf-integrated-action'
            ));
            const action = (entry.actionId && document.getElementById(entry.actionId)) ||
                actions[Number(entry.actionIndex || 0)];
            const container = (action && action.closest('cmwf-section')) ||
                              (action && action.parentElement);
            if (!container || !action) return '';
            const compact = String(
                entry.actionStem || entry.actionId || section
            ).replace(/[^a-z0-9]/gi, '').toLowerCase();
            const displays = Array.from(container.querySelectorAll(
                'cmwf-text-display, cmwf-cop, [data-linked-display], ' +
                '[data-action-output]'
            )).filter(node =>
                !/(?:^|_)module_items$/i.test(node.id || '') &&
                !node.closest(transientSurfaceSelector)
            );
            let owned = displays.filter(node => {
                const identity = [
                    node.id,
                    node.getAttribute('ng-model'),
                    node.getAttribute('data-field-id'),
                    node.getAttribute('data-linked-display'),
                    node.getAttribute('data-action-output')
                ].filter(Boolean).join(' ')
                    .replace(/[^a-z0-9]/gi, '').toLowerCase();
                return compact.length >= 4 && identity.includes(compact);
            });
            const siblings = Array.from(container.querySelectorAll(
                'cmwf-integrated-action'
            ));
            if (!owned.length && siblings.length === 1) owned = displays;
            if (!owned.length && displays.length) {
                const ordered = Array.from(container.querySelectorAll(
                    'cmwf-integrated-action, cmwf-text-display, cmwf-cop, ' +
                    '[data-linked-display], [data-action-output]'
                ));
                owned = displays.filter(node => {
                    const nodePosition = ordered.indexOf(node);
                    let nearest = null;
                    let distance = Number.POSITIVE_INFINITY;
                    for (const sibling of siblings) {
                        const candidateDistance = Math.abs(
                            ordered.indexOf(sibling) - nodePosition
                        );
                        if (candidateDistance < distance) {
                            nearest = sibling;
                            distance = candidateDistance;
                        }
                    }
                    return nearest === action;
                });
            }
            return owned
                .map(node => committedDisplayText(node, entry.title || section))
                .filter(Boolean)
                .join('\\n\\n');
        }
        const title = Array.from(document.querySelectorAll(
            'a.cop-title, [data-cop-title-go-composite], [data-section]'
        )).find(node => {
            if (node.closest(transientSurfaceSelector)) return false;
            const dataSection = node.getAttribute('data-section') || '';
            const cop = node.closest('cmwf-cop');
            if (entry.dataSection && dataSection === entry.dataSection) return true;
            return !!(entry.elementId && cop?.id === entry.elementId);
        });
        const cop = (title && title.closest('cmwf-cop')) ||
            (entry.elementId && document.getElementById(entry.elementId)) ||
            document.getElementById(section);
        const items = cop ? Array.from(cop.querySelectorAll(
            '.cop-items .copElement, .copElement'
        )) : [];
        return items
            .map(node => meaningfulText(node, entry.title || section))
            .filter(Boolean)
            .join('\\n\\n');
    }"""
    try:
        frames = list(page.frames)
        frame_index = int(entry.get("frameIndex") or 0)
        if 0 <= frame_index < len(frames):
            frames = [frames[frame_index]] + [
                frame for index, frame in enumerate(frames)
                if index != frame_index
            ]
        for frame in frames:
            text = frame.evaluate(read_script, entry)
            if str(text or "").strip():
                return str(text).strip()
    except Exception as exc:
        LOGGER.debug("Target linked-section read failed: %s", exc)
    return ""


def _wait_for_target_linked_section(page, entry, timeout_ms=12000):
    """Wait for the post-warning Angular update to reach the target note."""
    attempts = max(1, int(timeout_ms / 500))
    for attempt_index in range(attempts):
        text = _target_linked_section_text(page, entry)
        if text:
            return text
        if attempt_index + 1 < attempts:
            page.wait_for_timeout(500)
    return ""


def _wait_for_template_commit_result(page, entry, timeout_ms=10000):
    """Wait until template confirmation updates target or yields an outer list."""
    attempts = max(1, int(timeout_ms / 500))
    for attempt_index in range(attempts):
        if _main_modal_data_rows(page):
            return "list"
        if not _linked_form_modal_is_open(page) and _target_linked_section_text(page, entry):
            return "target"
        if _post_insert_warning_is_open(page):
            _accept_post_insert_warning(page, timeout_ms=1000)
        if attempt_index + 1 < attempts:
            page.wait_for_timeout(500)
    return "pending"


def _post_commit_diagnostics(page):
    """Summarize pending Plan of Care state without exposing clinical text."""
    pages = []
    try:
        context_pages = list(page.context.pages)
    except Exception:
        context_pages = [page]
    for candidate_page in context_pages:
        try:
            if candidate_page.is_closed():
                continue
            state = candidate_page.evaluate("""() => {
                const visible = el => !!el &&
                    (el.offsetWidth > 0 || el.offsetHeight > 0 ||
                     (el.getClientRects && el.getClientRects().length > 0));
                const modal = Array.from(document.querySelectorAll(
                    '#formFramePopup, .popup-modal.in, .modal.in, [role="dialog"]'
                )).find(visible);
                return {
                    title: document.title || '',
                    modalOpen: !!modal,
                    selected: Array.from(document.querySelectorAll(
                        'input[type="checkbox"]:checked'
                    )).filter(visible).length,
                    insertButtons: Array.from(document.querySelectorAll(
                        'button, a, input[type="button"], input[type="submit"], .btn'
                    )).filter(el => visible(el) && /insert\\s+to\\s+form/i.test(
                        el.innerText || el.value || ''
                    )).length
                };
            }""")
            pages.append(state)
        except Exception:
            continue
    return pages


def _click_linked_entry(page, entry, source=None):
    """Open a discovered target linked action using its runtime frame location."""
    source = source or {}
    title = entry.get("title") or source.get("title") or entry.get("section") or "(untitled)"
    section = entry.get("section") or source.get("section") or "unknown"
    action_ref = (
        entry.get("actionId") or entry.get("elementId") or
        entry.get("dataSection") or section
    )
    message = (
        f"--> Trying to open linked form: {title} "
        f"[section={section}, kind={entry.get('kind') or 'unknown'}, action={action_ref}]"
    )
    print(message)
    LOGGER.debug(message)
    if entry.get("kind") == "integrated":
        return _click_integrated_action(page, entry, source)
    frame_index = int(entry.get("frameIndex") or 0)
    try:
        owner = page.frames[frame_index]
    except (IndexError, TypeError):
        owner = page
    clicked = owner.evaluate("""(entry) => {
        const candidates = Array.from(document.querySelectorAll(
            'a.cop-title, a[data-cop-title-go-composite], ' +
            'button[data-cop-title-go-composite], cmwf-cop .cop-title, cmwf-cop[id]'
        ));
        const el = candidates.find(node => {
            const cop = node.closest('cmwf-cop') ||
                (node.tagName.toLowerCase() === 'cmwf-cop' ? node : null);
            const elementId = cop?.id || node.id || '';
            const dataSection = node.getAttribute('data-section') || '';
            if (entry.elementId && elementId === entry.elementId) return true;
            if (entry.dataSection && dataSection === entry.dataSection) return true;
            return Number(entry.actionIndex || -1) === candidates.indexOf(node);
        });
        if (!el) return false;
        const clickTarget = el.querySelector('a, button, span') || el;
        clickTarget.scrollIntoView({ block: 'center', inline: 'center' });
        clickTarget.click();
        return true;
    }""", entry)
    if not clicked:
        return False
    page.wait_for_timeout(750)
    return _normalize_open_linked_form_modal(page)


def _visible_template_choice_texts(page):
    """Read currently visible template choices without changing selection state."""
    return page.evaluate("""() => {
        const roots = Array.from(document.querySelectorAll(
            '#insertToTemplatePopup, #templateView, [id*="templateView"], ' +
            '[ng-if*="showTemplatePopup"], #formFramePopup, .popup-modal.in'
        )).filter(root => root.offsetWidth > 0 || root.offsetHeight > 0);
        const root = roots[roots.length - 1];
        if (!root) return [];
        const visible = el => !!el && (el.offsetWidth > 0 || el.offsetHeight > 0 ||
            (el.getClientRects && el.getClientRects().length > 0));
        const values = [];
        for (const checkbox of root.querySelectorAll('input[type="checkbox"]')) {
            if (!visible(checkbox) ||
                /selectall/i.test(checkbox.getAttribute('ng-model') || '')) continue;
            const row = checkbox.closest(
                'tr, .ui-grid-row, .item-row, .grid-row, .template-item, li, label, .checkbox'
            ) || checkbox.parentElement;
            const text = (row?.innerText || row?.textContent || '')
                .replace(/\\s+/g, ' ').trim();
            if (text && !/^(?:select all|active|inactive)$/i.test(text)) values.push(text);
        }
        return Array.from(new Set(values));
    }""") or []


def _inventory_open_target_linked_form(page):
    """Inventory outer-list or template choices in an already open empty target."""
    outer_rows = _main_modal_data_rows(page)
    if outer_rows:
        return outer_rows

    if not _ensure_template_chooser_open(page):
        return []
    page.wait_for_timeout(750)
    items = []
    state = _template_category_state(page)
    options = list(state.get("nativeOptions") or [])
    if state.get("toggleIsPlaceholder") or not options:
        _click_select_category_button(page)
        options = _list_open_category_options(page)
    options = [name for name in options if not _is_category_placeholder(name)]

    if options:
        for category in options:
            _ensure_template_chooser_open(page)
            if not _select_template_category(page, category):
                continue
            page.wait_for_timeout(500)
            items.extend(_visible_template_choice_texts(page))
    else:
        items.extend(_visible_template_choice_texts(page))

        def collect_open_group(_label):
            items.extend(_visible_template_choice_texts(page))

        _expand_template_groups(page, collect_open_group)
    return list(dict.fromkeys(item for item in items if item))


def _probe_target_linked_items(page, entry):
    """Safely inspect a target linked form and restore the empty target state."""
    if not _click_linked_entry(page, entry):
        return []
    page.wait_for_timeout(500)
    handle_warning_popups(page)
    try:
        return _inventory_open_target_linked_form(page)
    finally:
        if _template_surface_is_open(page):
            _cancel_template_surface(page)
        _cancel_open_composite(page)


def _report_unresolved_linked_sources(linked_state):
    """Return and report populated linked records not resolved on the target."""
    if "applicable_source_keys" in linked_state:
        candidates = set(linked_state.get("applicable_source_keys") or set())
        if not candidates and not linked_state.get("discovered_targets"):
            candidates = set(linked_state.get("source_record_keys") or set())
    else:
        candidates = set(linked_state.get("source_record_keys") or set())
    unmatched = (
        candidates
        - set(linked_state.get("matched_source_keys") or set())
        - set(linked_state.get("intentionally_skipped_source_keys") or set())
    )
    if not unmatched:
        return set()
    source_sections = linked_state.get("source_record_sections") or {}
    unresolved_sections = sorted({
        source_sections.get(key, "unknown") for key in unmatched
    })
    LOGGER.warning(
        "Continuing to Save for RN review: %d populated source linked "
        "record(s) in %s were not resolved after discovering %d target linked "
        "action(s).",
        len(unmatched),
        ", ".join(unresolved_sections),
        int(linked_state.get("discovered_targets") or 0),
    )
    print(
        f"--> WARNING: {len(unmatched)} populated source linked form(s) were "
        f"not transferred ({', '.join(unresolved_sections)}); saving draft for RN review."
    )
    return unmatched


def inject_composite_linked_forms(page, extracted_fields, linked_state=None):
    """Discover linked forms on this page and select rows present in the source section, using templates if needed."""
    source_sections = _source_composite_sections(extracted_fields)
    source_records = _source_linked_records(extracted_fields)
    if not source_sections and not source_records:
        return linked_state or {}
    if linked_state is None:
        linked_state = {
            "source_record_keys": {
                record["record_key"] for record in source_records
            },
            "source_record_sections": {
                record["record_key"]: record.get("section") or record.get("action_stem") or "unknown"
                for record in source_records
            },
            "matched_source_keys": set(),
            "applicable_source_keys": set(),
            "intentionally_skipped_source_keys": set(),
            "discovered_targets": 0,
            "unresolved_targets": [],
            "inserted_target_entries": [],
        }
    linked_state.setdefault("intentionally_skipped_source_keys", set())
    linked_state.setdefault("applicable_source_keys", set())
    linked_state.setdefault("inserted_target_entries", [])
    interventions_goals_source = source_sections.get("interventions_goals")
    if interventions_goals_source:
        LOGGER.debug(
            "Interventions/Goals merged source items (%d): %s",
            len(interventions_goals_source.get("items") or []),
            (interventions_goals_source.get("items") or [])[:40],
        )

    discover_script = """() => {
        const found = new Map();
        const cleanKey = value => String(value || '')
            .toLowerCase().replace(/[^a-z0-9]/g, '');
        const transientSurfaceSelector = [
            '#formFramePopup', '#insertToTemplatePopup', '#templateView',
            '[id*="templateView"]', '[ng-if*="showTemplatePopup"]',
            '.popup-modal', '.modal', '[role="dialog"]', '.sweet-alert'
        ].join(',');
        const chromeKeys = new Set([
            'clear', 'select', 'add', 'delete', 'remove', 'edit', 'cancel',
            'close', 'update', 'none', 'norecordsfound'
        ]);
        const meaningfulText = (node, title) => {
            const titleKey = cleanKey(title);
            return String(node?.innerText || node?.textContent || '')
                .split(/[\\n\\r]+/)
                .map(line => line.replace(/\\s+/g, ' ').trim())
                .filter(line => {
                    const key = cleanKey(line);
                    if (!key || key === titleKey || chromeKeys.has(key)) return false;
                    const suffix = titleKey && key.startsWith(titleKey) ?
                        key.slice(titleKey.length) : '';
                    const prefix = titleKey && key.endsWith(titleKey) ?
                        key.slice(0, -titleKey.length) : '';
                    return !chromeKeys.has(suffix) && !chromeKeys.has(prefix);
                })
                .join('\\n');
        };
        const committedDisplayText = (node, title) => {
            if (node.tagName?.toLowerCase() !== 'cmwf-cop') {
                return meaningfulText(node, title);
            }
            return Array.from(node.querySelectorAll(
                '.cop-items .copElement, .copElement'
            )).map(item => meaningfulText(item, title)).filter(Boolean).join('\\n');
        };
        const actionStem = value => String(value || '')
            .replace(/([a-z0-9])([A-Z])/g, '$1_$2')
            .replace(/(?:_?summary_?of_?problems)?_?request$/i, '')
            .replace(/_?action(?:_\\d+)?$/i, '')
            .replace(/_?(?:link|button)$/i, '')
            .replace(/[^a-z0-9]+/gi, '_')
            .replace(/^_+|_+$/g, '')
            .toLowerCase();
        const canonicalSection = (raw, title) => {
            const identity = String((raw || '') + ' ' + (title || '')).toLowerCase();
            const requestId = String(raw || '').replace(/[^a-z0-9]/gi, '').toLowerCase();
            if ([
                'interventionsgoalsrequest',
                'otherinterventionsgoalsrequest',
                'combinedinterventionsgoalsrequest'
            ].includes(requestId) ||
                (/intervention/.test(identity) && /goal/.test(identity))) {
                return 'interventions_goals';
            }
            return actionStem(raw) || actionStem(title);
        };
        const selectors = [
            'a.cop-title',
            'a[data-cop-title-go-composite]',
            'button[data-cop-title-go-composite]',
            'cmwf-cop .cop-title',
            'cmwf-cop[id]'
        ];
        for (const el of document.querySelectorAll(selectors.join(','))) {
            const cop = el.closest('cmwf-cop') || (el.tagName.toLowerCase() === 'cmwf-cop' ? el : null);
            if (cop?.closest(transientSurfaceSelector)) continue;
            const titleNode = el.matches?.('.cop-title, [data-cop-title-go-composite]') ?
                el : cop?.querySelector('.cop-title, [data-cop-title-go-composite]');
            if (!cop || !titleNode) continue;
            const rawSection = (el.getAttribute('data-section') || cop?.id || el.id || '').trim();
            const rawTitle = (titleNode.getAttribute('data-cop-title-go-composite') ||
                titleNode.innerText || titleNode.textContent || rawSection).trim();
            const title = rawTitle.split(/[\\n\\r]+/).map(line => line.trim()).find(Boolean) ||
                rawSection;
            const section = canonicalSection(rawSection, title);
            if (!section || chromeKeys.has(cleanKey(title)) || found.has(section)) continue;
            const hasItems = Array.from(cop.querySelectorAll(
                '.cop-items .copElement, .copElement'
            )).some(node => !!meaningfulText(node, title));
            found.set(section, {
                section,
                kind: 'composite',
                title,
                elementId: cop?.id || el.id || '',
                dataSection: el.getAttribute('data-section') || '',
                hasItems
            });
        }
        const actions = Array.from(document.querySelectorAll('cmwf-integrated-action'));
        for (let actionIndex = 0; actionIndex < actions.length; actionIndex++) {
            const el = actions[actionIndex];
            if (el.closest(transientSurfaceSelector)) continue;
            const actionId = (el.id || '').trim();
            const title = (el.innerText || el.textContent || '').trim();
            const actionBindingStem = actionStem(actionId);
            // `mDetails` opens OASIS item instructions (for example A1005),
            // not a linked clinical selector.
            if (actionBindingStem === 'm_details') continue;
            // `update_diagnosis` is handled separately by sync_oasis_diagnoses().
            if (actionBindingStem === 'update_diagnosis' || cleanKey(title) === 'updatediagnosis') continue;
            const rawSection = actionBindingStem || actionStem(title) ||
                ('linked_action_' + actionIndex);
            const section = canonicalSection(rawSection, title);
            const entryKey = 'integrated:' + (actionId || section) + ':' + actionIndex;
            // Clear/Delete/etc. use cmwf-integrated-action too, but do not
            // navigate to a linked clinical form.
            if (!section || chromeKeys.has(cleanKey(title)) || found.has(entryKey)) continue;
            const container = el.closest('cmwf-section') || el.parentElement;
            const displays = container ? Array.from(container.querySelectorAll(
                'cmwf-text-display, cmwf-cop, [data-linked-display], ' +
                '[data-action-output]'
            )).filter(node =>
                !/(?:^|_)module_items$/i.test(node.id || '') &&
                !node.closest(transientSurfaceSelector)
            ) : [];
            const compactSection = section.replace(/[^a-z0-9]/g, '');
            let ownedDisplays = displays.filter(node => {
                const displayIdentity = [
                    node.id,
                    node.getAttribute('ng-model'),
                    node.getAttribute('data-field-id'),
                    node.getAttribute('data-linked-display'),
                    node.getAttribute('data-action-output')
                ].filter(Boolean).join(' ')
                    .replace(/[^a-z0-9]/gi, '').toLowerCase();
                return compactSection.length >= 4 &&
                    displayIdentity.includes(compactSection);
            });
            const siblingActions = container ? Array.from(
                container.querySelectorAll('cmwf-integrated-action')
            ) : [];
            if (!ownedDisplays.length && siblingActions.length === 1) {
                ownedDisplays = displays;
            }
            if (!ownedDisplays.length && displays.length) {
                const ordered = Array.from(container.querySelectorAll(
                    'cmwf-integrated-action, cmwf-text-display, cmwf-cop, ' +
                    '[data-linked-display], [data-action-output]'
                ));
                ownedDisplays = displays.filter(node => {
                    const nodePosition = ordered.indexOf(node);
                    let nearest = null;
                    let distance = Number.POSITIVE_INFINITY;
                    for (const sibling of siblingActions) {
                        const candidateDistance = Math.abs(
                            ordered.indexOf(sibling) - nodePosition
                        );
                        if (candidateDistance < distance) {
                            nearest = sibling;
                            distance = candidateDistance;
                        }
                    }
                    return nearest === el;
                });
            }
            const hasItems = ownedDisplays.some(
                node => !!committedDisplayText(node, title)
            );
            found.set(entryKey, {
                section,
                kind: 'integrated',
                actionId,
                actionStem: actionStem(actionId),
                actionIndex,
                title: title || section,
                hasItems
            });
        }
        return Array.from(found.values());
    }"""

    # Stencil/Angular can hydrate the page shell before its linked actions.
    # Rebuild the inventory until it is nonempty and stable across consecutive
    # observations; a single eager query caused page-one actions to be missed.
    entry_points = []
    previous_fingerprint = None
    stable_passes = 0
    discovery_attempts = 8
    for attempt_index in range(discovery_attempts):
        current_entries = []
        for frame_index, frame in enumerate(page.frames):
            try:
                discovered = frame.evaluate(discover_script) or []
            except Exception as exc:
                LOGGER.debug(
                    "Linked-form discovery failed in frame %s: %s", frame_index, exc
                )
                continue
            for entry in discovered:
                entry["frameIndex"] = frame_index
                current_entries.append(entry)

        fingerprint = tuple(sorted(
            (
                int(entry.get("frameIndex") or 0),
                str(entry.get("kind") or ""),
                str(entry.get("actionId") or entry.get("elementId") or ""),
                str(entry.get("section") or ""),
            )
            for entry in current_entries
        ))
        if fingerprint and fingerprint == previous_fingerprint:
            stable_passes += 1
        else:
            stable_passes = 0
        previous_fingerprint = fingerprint
        entry_points = current_entries

        # Observe for at least ~1.6 seconds even when some COPs appeared early;
        # integrated actions often hydrate shortly after those static sections.
        if attempt_index >= 4 and fingerprint and stable_passes >= 2:
            break
        if attempt_index + 1 < discovery_attempts:
            page.wait_for_timeout(400)

    if discovery_attempts > 1:
        LOGGER.debug(
            "Linked-form inventory stabilized with %d target(s) after %d pass(es).",
            len(entry_points),
            attempt_index + 1,
        )

    LOGGER.debug(
        "Linked-form discovery: source=%s target=%s",
        {name: len(data.get("items") or []) for name, data in source_sections.items()},
        entry_points,
    )

    for entry in entry_points:
        linked_state["discovered_targets"] += 1
        section = entry.get("section", "").lower()
        if entry.get("hasItems"):
            source = _resolve_source_for_target(
                entry, source_records, source_sections
            )
            if source:
                linked_state["applicable_source_keys"].update(
                    source.get("matched_record_keys") or []
                )
                linked_state["matched_source_keys"].update(
                    source.get("matched_record_keys") or []
                )
            title = entry.get("title") or (source or {}).get("title") or section
            print(
                f"--> Preserving linked section: {title} [{section}] "
                "(target list is already populated)."
            )
            continue

        source = _resolve_source_for_target(entry, source_records, source_sections)
        if not source:
            target_items = _probe_target_linked_items(page, entry)
            source = _resolve_source_for_target(
                entry,
                source_records,
                source_sections,
                target_items=target_items,
            )
        title = entry.get("title") or (source or {}).get("title") or section
        if not source:
            print(
                f"--> Skipping linked section: {title} [{section}] "
                "(no populated source match)."
            )
            linked_state["unresolved_targets"].append({
                "section": section,
                "title": title,
                "actionId": entry.get("actionId") or "",
            })
            continue
        linked_state["applicable_source_keys"].update(
            source.get("matched_record_keys") or []
        )
        print(f"--> Auto-populating discovered linked section: {title} [{section}]...")
        try:
            clicked = _click_linked_entry(page, entry, source)
            if not clicked:
                print(f"    Clicking {title} did not open its linked-form modal; skipping section.")
                continue

            page.wait_for_timeout(2000)
            handle_warning_popups(page)

            # Expand accordions in initial modal
            page.evaluate("""() => {
                const modal = document.querySelector('#formFramePopup') || document.querySelector('.popup-modal.in');
                if (!modal) return;
                for (const header of modal.querySelectorAll('.accordion-toggle, [ng-click*="isOpen"], .panel-heading')) {
                    const body = header.nextElementSibling;
                    if (header.querySelector('.fa-chevron-right, .fa-angle-right, .fa-caret-right') ||
                        (body && body.offsetHeight === 0)) {
                        try { header.click(); } catch (e) {}
                    }
                }
            }""")
            page.wait_for_timeout(1000)

            # Use a trusted Playwright click for each matching checkbox. A
            # JavaScript `cb.click()` can look selected without updating the
            # Devero controller and was leaving Vital Signs/Pain uncommitted.
            selection = {
                "found": _linked_form_modal_is_open(page),
                **_select_visible_template_matches(
                    page, source["value"], source.get("items")
                ),
            }

            if not selection.get("found"):
                print("    Linked-form modal did not open; skipping section.")
                continue

            # Insert From Template is only for a genuinely empty linked-form
            # list. If rows already exist, use that list and never enter the
            # category chooser merely because none of its rows matched.
            # Devero may render a blank placeholder row with a checkbox. Count
            # only rows containing actual display text as an existing list.
            list_has_rows = bool(_main_modal_data_rows(page))
            template_committed_directly = False

            if selection.get("selected", 0) == 0 and not list_has_rows:
                print(f"    No matching rows in table. Opening 'Insert From Template' for {title}...")
                template_opened = page.evaluate("""() => {
                    const modal = document.querySelector('#formFramePopup') || document.querySelector('.popup-modal.in') || document;
                    const visible = el => !!el && (el.offsetWidth > 0 || el.offsetHeight > 0 || el.getClientRects().length > 0);
                    const buttons = Array.from(modal.querySelectorAll('button, a, input[type="button"], .btn, span')).filter(visible);
                    const btn = buttons.find(b => {
                        const txt = (b.innerText || b.value || '').trim().toLowerCase();
                        const ngClick = (b.getAttribute('ng-click') || '').toLowerCase();
                        const dataAuto = (b.getAttribute('data-automation-id') || '').toLowerCase();
                        return /insert\\s*from\\s*template/i.test(txt) ||
                               /from\\s*template/i.test(txt) ||
                               ngClick.includes('template') ||
                               dataAuto.includes('template');
                    });
                    if (!btn) return false;
                    btn.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window, button: 0, buttons: 1 }));
                    btn.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window, button: 0 }));
                    btn.click();
                    return true;
                }""")

                if template_opened:
                    page.wait_for_timeout(2000)
                    handle_warning_popups(page)

                    categories_to_process = _categories_to_process(
                        page, source["value"], section, title
                    )
                    total_template_selected = 0

                    if categories_to_process:
                        
                        for cat_idx, cat_name in enumerate(categories_to_process):
                            _ensure_template_chooser_open(page)
                            handle_warning_popups(page)

                            print(f"    Selecting category '{cat_name}'...")
                            category_selected = _select_template_category(page, cat_name)
                            if not category_selected:
                                print(f"    Could not select category '{cat_name}'.")
                                # Do not reuse checkboxes left over from the
                                # previously selected category.
                                continue
                            page.wait_for_timeout(2000)
                            handle_warning_popups(page)

                            # Select matching items for this category
                            cat_item_sel = page.evaluate("""(sourceData) => {
                                const modal = document.querySelector('#formFramePopup') || document.querySelector('.popup-modal.in') || document;
                                const visible = el => !!el && !el.disabled &&
                                    (el.offsetWidth > 0 || el.offsetHeight > 0 || el.getClientRects().length > 0);
                                const normalize = text => String(text || '')
                                    .replace(/start effective date\\s*:?\\s*\\d{1,2}\\/\\d{1,2}\\/\\d{2,4}/gi, ' ')
                                    .replace(/discontinued date\\s*:?\\s*\\d{1,2}\\/\\d{1,2}\\/\\d{2,4}/gi, ' ')
                                    .split(/[\\n\\r]+/)
                                    .map(line => line.toLowerCase()
                                        .replace(/\\d{1,2}\\/\\d{1,2}\\/\\d{2,4}/g, ' ')
                                        .replace(/[^a-z0-9]+/g, ' ')
                                        .replace(/\\s+/g, ' ')
                                        .trim())
                                    .filter(line => line.length >= 2 &&
                                        !/^(advance directives|safety measures|homebound|homebound status|eligibility|goals|interventions|orders|prognosis|dme|supplies|emergency contact|physician certification|face to face|face to face encounter|nutritional requirements|activities permitted|functional limitations|mental status|discharge planning|rehab potential)$/i.test(line))
                                    .join(' ')
                                    .replace(/\\s+/g, ' ')
                                    .trim();

                                const rawItems = (sourceData.items || []).concat(
                                    String(sourceData.value || '').split(/[\\n\\r]+/)
                                );

                                const cleanItems = rawItems.map(normalize).filter(p => {
                                    if (p.length < 2) return false;
                                    if (/^(advance directives|safety measures|homebound|homebound status|eligibility|goals|interventions|orders|prognosis|dme|supplies|emergency contact|physician certification|face to face|face to face encounter)$/i.test(p)) return false;
                                    return true;
                                });

                                const getCheckboxText = (cb) => {
                                    const label = cb.closest('label');
                                    if (label) return label.innerText || label.textContent || '';
                                    const rowEl = cb.closest('tr, .ui-grid-row, .item-row, .grid-row, .template-item, li, .checkbox');
                                    if (rowEl && rowEl.querySelectorAll('input[type="checkbox"]').length === 1) {
                                        return rowEl.innerText || rowEl.textContent || '';
                                    }
                                    if (cb.parentElement && cb.parentElement.querySelectorAll('input[type="checkbox"]').length === 1) {
                                        return cb.parentElement.innerText || cb.parentElement.textContent || '';
                                    }
                                    let node = cb.nextSibling;
                                    let txt = '';
                                    while (node) {
                                        if (node.nodeType === 3) txt += node.textContent + ' ';
                                        else if (node.nodeType === 1) {
                                            if (node.tagName === 'INPUT' && node.type === 'checkbox') break;
                                            txt += (node.innerText || node.textContent || '') + ' ';
                                        }
                                        node = node.nextSibling;
                                    }
                                    return txt;
                                };

                                const sourceBlob = ' ' + normalize(sourceData.value) + ' ';
                                const isMatch = (itemText) => {
                                    const norm = normalize(itemText);
                                    if (norm.length < 2) return false;
                                    const variableKey = text => normalize(text)
                                        .replace(/\\b\\d+(?:\\.\\d+)?\\b/g, ' ')
                                        .replace(/\\s+/g, ' ')
                                        .trim();
                                    if (/_{2,}/.test(String(itemText || '')) && cleanItems.some(target =>
                                        variableKey(itemText) === variableKey(target) &&
                                        variableKey(itemText).length >= 12
                                    )) return true;
                                    if (/^(select all|select active|active|inactive|all|none|yes|no|other|delete|close|cancel)$/i.test(norm)) {
                                        return false;
                                    }
                                    if (/^(advance directives|safety measures|homebound|homebound status|eligibility|goals|interventions|orders|prognosis|dme|supplies|emergency contact|physician certification|face to face|face to face encounter)$/i.test(norm)) {
                                        return false;
                                    }
                                    // Concatenated source blobs (one line, many items) still
                                    // contain the discrete template label as a whole phrase.
                                    if (norm.length >= 6 && sourceBlob.includes(' ' + norm + ' ')) return true;
                                    for (const target of cleanItems) {
                                        if (target.length < 2) continue;
                                        if (norm === target) return true;
                                        if (target.length >= 6 && (' ' + norm + ' ').includes(' ' + target + ' ')) return true;
                                        if (norm.length >= 6 && (' ' + target + ' ').includes(' ' + norm + ' ')) return true;
                                        if (norm.length >= 6 && target.length >= 6) {
                                            if (norm.includes(target) && target.length >= norm.length * 0.6) return true;
                                            if (target.includes(norm) && norm.length >= target.length * 0.6) return true;
                                        }
                                        if (norm.startsWith(target + ' ') && target.length >= 15) return true;
                                        if (target.startsWith(norm + ' ') && norm.length >= 15) return true;
                                    }
                                    return false;
                                };

                                let selected = 0;
                                let candidates = 0;
                                let checkboxes = Array.from(modal.querySelectorAll(
                                    '.ui-grid-render-container-body input[ng-model="row.isSelected"], input[ng-model="row.isSelected"], tr input[type="checkbox"], .item-row input[type="checkbox"], .grid-row input[type="checkbox"], label input[type="checkbox"], input[type="checkbox"]'
                                )).filter(visible);

                                for (const cb of checkboxes) {
                                    if ((cb.getAttribute('ng-model') || '').toLowerCase().includes('selectall')) continue;
                                    candidates++;
                                    const raw = getCheckboxText(cb);
                                    if (!raw || !isMatch(raw)) {
                                        if (cb.checked) {
                                            cb.click();
                                            cb.checked = false;
                                        }
                                        continue;
                                    }

                                    if (!cb.checked) {
                                        cb.click();
                                        if (!cb.checked) {
                                            cb.checked = true;
                                            cb.dispatchEvent(new Event('change', { bubbles: true }));
                                        }
                                    }

                                    try {
                                        const s = window.angular && window.angular.element(cb).scope();
                                        if (s) {
                                            if (s.row && typeof s.row === 'object') s.row.isSelected = true;
                                            for (let k in s) {
                                                if (s[k] && typeof s[k] === 'object') {
                                                    if ('selected' in s[k]) s[k].selected = true;
                                                    if ('isSelected' in s[k]) s[k].isSelected = true;
                                                    if ('isChecked' in s[k]) s[k].isChecked = true;
                                                }
                                            }
                                        }
                                    } catch(e) {}

                                    selected++;
                                }

                                return { selected, candidates };
                            }""", {
                                "value": source["value"],
                                "items": source.get("items") or [],
                                "title": title,
                                "section": section
                            })

                            cat_sel_count = cat_item_sel.get("selected", 0)
                            print(f"      Category '{cat_name}': selected {cat_sel_count} item(s).")
                            if cat_sel_count > 0:
                                total_template_selected += cat_sel_count
                                _click_template_insert(page)
                                page.wait_for_timeout(1000)
                                handle_warning_popups(page)

                        if total_template_selected > 0:
                            selection["selected"] = total_template_selected
                            print(
                                f"    Inserted {total_template_selected} template item(s) into Care Plan module table for {title}."
                            )

                            if _main_modal_data_rows(page):
                                selected_rows = _select_main_modal_rows(
                                    page, source["value"], source.get("items")
                                )
                                if selected_rows:
                                    selection["selected"] = selected_rows
                                    total_template_selected = max(
                                        total_template_selected, selected_rows
                                    )
                                elif total_template_selected > 0:
                                    selection["selected"] = total_template_selected
                    else:
                        # Goals/Interventions templates are grouped in nested
                        # accordions. Opening one group closes the previous
                        # one, so match and select its rows immediately before
                        # moving to the next group.
                        group_selection = {"candidates": 0, "selected": 0}

                        def select_open_group(group_name):
                            result = _select_visible_template_matches(
                                page, source["value"], source.get("items")
                            )
                            group_selection["candidates"] += result.get(
                                "candidates", 0
                            )
                            group_selection["selected"] += result.get(
                                "selected", 0
                            )
                            print(
                                f"      Group '{group_name}': selected "
                                f"{result.get('selected', 0)} matching item(s)."
                            )

                        _expand_template_groups(page, select_open_group)
                        page.wait_for_timeout(1000)

                        # Select matching template items
                        template_selection = page.evaluate("""(sourceData) => {
                            const modal = document.querySelector('#formFramePopup') || document.querySelector('.popup-modal.in') || document;
                            const visible = el => !!el && !el.disabled &&
                                (el.offsetWidth > 0 || el.offsetHeight > 0 || el.getClientRects().length > 0);
                            const normalize = text => String(text || '')
                                .replace(/start effective date\\s*:?\\s*\\d{1,2}\\/\\d{1,2}\\/\\d{2,4}/gi, ' ')
                                .replace(/discontinued date\\s*:?\\s*\\d{1,2}\\/\\d{1,2}\\/\\d{2,4}/gi, ' ')
                                .split(/[\\n\\r]+/)
                                .map(line => line.toLowerCase()
                                    .replace(/\\d{1,2}\\/\\d{1,2}\\/\\d{2,4}/g, ' ')
                                    .replace(/[^a-z0-9]+/g, ' ')
                                    .replace(/\\s+/g, ' ')
                                    .trim())
                                .filter(line => line.length >= 2 &&
                                    !/^(advance directives|safety measures|homebound|homebound status|eligibility|goals|interventions|orders|prognosis|dme|supplies|emergency contact|physician certification|face to face|face to face encounter|nutritional requirements|activities permitted|functional limitations|mental status|discharge planning|rehab potential)$/i.test(line))
                                .join(' ')
                                .replace(/\\s+/g, ' ')
                                .trim();

                            const rawItems = (sourceData.items || []).concat(
                                String(sourceData.value || '').split(/[\\n\\r]+/)
                            );

                            const cleanItems = rawItems.map(normalize).filter(p => {
                                if (p.length < 2) return false;
                                if (/^(advance directives|safety measures|homebound|homebound status|eligibility|goals|interventions|orders|prognosis|dme|supplies|emergency contact|physician certification|face to face|face to face encounter)$/i.test(p)) return false;
                                return true;
                            });

                            const getCheckboxText = (cb) => {
                                const label = cb.closest('label');
                                if (label) return label.innerText || label.textContent || '';
                                const rowEl = cb.closest('tr, .ui-grid-row, .item-row, .grid-row, .template-item, li, .checkbox');
                                if (rowEl && rowEl.querySelectorAll('input[type="checkbox"]').length === 1) {
                                    return rowEl.innerText || rowEl.textContent || '';
                                }
                                if (cb.parentElement && cb.parentElement.querySelectorAll('input[type="checkbox"]').length === 1) {
                                    return cb.parentElement.innerText || cb.parentElement.textContent || '';
                                }
                                let node = cb.nextSibling;
                                let txt = '';
                                while (node) {
                                    if (node.nodeType === 3) txt += node.textContent + ' ';
                                    else if (node.nodeType === 1) {
                                        if (node.tagName === 'INPUT' && node.type === 'checkbox') break;
                                        txt += (node.innerText || node.textContent || '') + ' ';
                                    }
                                    node = node.nextSibling;
                                }
                                return txt;
                            };

                            const sourceBlob = ' ' + normalize(sourceData.value) + ' ';
                            const isMatch = (itemText) => {
                                const norm = normalize(itemText);
                                if (norm.length < 2) return false;
                                const variableKey = text => normalize(text)
                                    .replace(/\\b\\d+(?:\\.\\d+)?\\b/g, ' ')
                                    .replace(/\\s+/g, ' ')
                                    .trim();
                                if (/_{2,}/.test(String(itemText || '')) && cleanItems.some(target =>
                                    variableKey(itemText) === variableKey(target) &&
                                    variableKey(itemText).length >= 12
                                )) return true;
                                if (/^(select all|select active|active|inactive|all|none|yes|no|other|delete|close|cancel)$/i.test(norm)) {
                                    return false;
                                }
                                if (/^(advance directives|safety measures|homebound|homebound status|eligibility|goals|interventions|orders|prognosis|dme|supplies|emergency contact|physician certification|face to face|face to face encounter)$/i.test(norm)) {
                                    return false;
                                }
                                if (norm.length >= 6 && sourceBlob.includes(' ' + norm + ' ')) return true;
                                for (const target of cleanItems) {
                                    if (target.length < 2) continue;
                                    if (norm === target) return true;
                                    if (target.length >= 6 && (' ' + norm + ' ').includes(' ' + target + ' ')) return true;
                                    if (norm.length >= 6 && (' ' + target + ' ').includes(' ' + norm + ' ')) return true;
                                    if (norm.length >= 6 && target.length >= 6) {
                                        if (norm.includes(target) && target.length >= norm.length * 0.6) return true;
                                        if (target.includes(norm) && norm.length >= target.length * 0.6) return true;
                                    }
                                    if (norm.startsWith(target + ' ') && target.length >= 15) return true;
                                    if (target.startsWith(norm + ' ') && norm.length >= 15) return true;
                                }
                                return false;
                            };

                            let selected = 0;
                            let candidates = 0;
                            let checkboxes = Array.from(modal.querySelectorAll(
                                '.ui-grid-render-container-body input[ng-model="row.isSelected"], input[ng-model="row.isSelected"], tr input[type="checkbox"], .item-row input[type="checkbox"], .grid-row input[type="checkbox"], label input[type="checkbox"], input[type="checkbox"]'
                            )).filter(visible);

                            for (const cb of checkboxes) {
                                if ((cb.getAttribute('ng-model') || '').toLowerCase().includes('selectall')) continue;
                                candidates++;
                                const raw = getCheckboxText(cb);
                                if (!raw || !isMatch(raw)) {
                                    if (cb.checked) {
                                        cb.click();
                                        cb.checked = false;
                                    }
                                    continue;
                                }

                                if (!cb.checked) {
                                    cb.click();
                                    if (!cb.checked) {
                                        cb.checked = true;
                                        cb.dispatchEvent(new Event('change', { bubbles: true }));
                                    }
                                }

                                try {
                                    const s = window.angular && window.angular.element(cb).scope();
                                    if (s) {
                                        if (s.row && typeof s.row === 'object') s.row.isSelected = true;
                                        for (let k in s) {
                                            if (s[k] && typeof s[k] === 'object') {
                                                if ('selected' in s[k]) s[k].selected = true;
                                                if ('isSelected' in s[k]) s[k].isSelected = true;
                                                if ('isChecked' in s[k]) s[k].isChecked = true;
                                            }
                                        }
                                    }
                                } catch(e) {}

                                selected++;
                            }

                            return { selected, candidates, cleanItems };
                        }""", {
                            "value": source["value"],
                            "items": source.get("items") or [],
                            "title": title,
                            "section": section
                        })

                        # The evaluate pass above covers whichever group is
                        # currently visible. The callback totals retain rows
                        # selected from earlier groups that the accordion has
                        # since collapsed.
                        template_selection["candidates"] = max(
                            group_selection["candidates"],
                            template_selection.get("candidates", 0),
                        )
                        total_template_selected = max(
                            group_selection["selected"],
                            template_selection.get("selected", 0),
                        )
                        print(f"    Template choices: {template_selection.get('candidates', 0)}, selected {total_template_selected} matching item(s) for targets: {template_selection.get('cleanItems', [])}")

                        if total_template_selected > 0:
                            # Clear any warning/confirmation overlay before
                            # trying the commit button beneath it.
                            handle_warning_popups(page)
                            page.wait_for_timeout(300)
                            template_inserted = _click_template_insert(page)
                            if not template_inserted:
                                print(
                                    f"    Template insert button was not available for {title}."
                                )
                            else:
                                page.wait_for_timeout(1500)
                                handle_warning_popups(page)
                                commit_result = _wait_for_template_commit_result(
                                    page, entry, timeout_ms=10000
                                )
                                if commit_result == "target":
                                    template_committed_directly = True
                                    selection["selected"] = total_template_selected
                                    print(
                                        f"    Template selection for {title} was "
                                        "inserted directly into the form."
                                    )
                                elif commit_result == "pending":
                                    print(
                                        f"    Template confirmation for {title} "
                                        "closed, but no target update or linked-form "
                                        "list appeared yet."
                                    )
                        else:
                            print(f"    Nothing to select in template for {title}; clicking Cancel button (leaving form empty)...")
                            page.evaluate("""() => {
                                const modal = document.querySelector('#formFramePopup') || document.querySelector('.popup-modal.in') || document;
                                const buttons = Array.from(modal.querySelectorAll('button, a, input[type="button"], .btn'));
                                const cancelBtn = buttons.find(b => {
                                    const txt = (b.innerText || b.value || '').trim().toLowerCase();
                                    return txt === 'cancel' || txt === 'close' || txt === 'back';
                                });
                                if (cancelBtn) cancelBtn.click();
                                const closeX = modal.querySelector('button.close, .close, [aria-label="Close"]');
                                if (closeX) closeX.click();
                            }""")
                            page.wait_for_timeout(500)
                            handle_warning_popups(page)

                    # Step 2: the template popup filled the linked-form list.
                    # Select those rows and Insert to form, or the page COP stays empty.
                    page.wait_for_timeout(800)
                    if _main_modal_data_rows(page):
                        selected_rows = _select_main_modal_rows(
                            page, source["value"], source.get("items")
                        )
                        if selected_rows:
                            selection["selected"] = selected_rows
                            total_template_selected = max(
                                total_template_selected, selected_rows
                            )
                        elif total_template_selected > 0:
                            selection["selected"] = total_template_selected
                    if total_template_selected > 0:
                        selection["selected"] = max(
                            selection.get("selected", 0), total_template_selected
                        )
                    # Checked rows inside the template chooser do not count as
                    # committed. Only rows that have returned to the main list
                    # are eligible for the final Insert to form action.

            if selection.get("selected", 0) == 0 and list_has_rows:
                print(
                    f"    Existing linked-form list for {title} had no source-matching rows; "
                    "Insert From Template was not opened."
                )

            if selection.get("selected", 0) == 0:
                print(f"    Nothing selected for {title}; clicking Cancel button (leaving form empty).")
                if _template_surface_is_open(page):
                    _cancel_template_surface(page)
                cancelled = _cancel_open_composite(page)
                if not cancelled:
                    _cancel_empty_composite(page)
                handle_warning_popups(page)
                page.wait_for_timeout(750)
                if not _linked_form_modal_is_open(page) and not _template_surface_is_open(page):
                    # E1 and E2 do not always offer the same linked rows. Once
                    # the target selector was inspected and Cancel closed it,
                    # an unavailable source row is an intentional omission,
                    # not a silently missed transfer.
                    skipped_keys = source.get("matched_record_keys") or []
                    linked_state["intentionally_skipped_source_keys"].update(
                        skipped_keys
                    )
                    print(
                        f"    No compatible target row for {title}; "
                        "popup closed with Cancel and section was left empty."
                    )
                else:
                    print(
                        f"    Could not confirm that {title} closed after Cancel; "
                        "section remains unresolved."
                    )
                continue

            if _template_surface_is_open(page):
                _cancel_template_surface(page)
                page.wait_for_timeout(500)
                handle_warning_popups(page)

            # A template insert can return to the outer linked-form list while
            # rendering selected rows behind it. If that list is still open,
            # its trusted Insert to form click is always required.
            if _linked_form_modal_is_open(page):
                insert_result = _click_insert_to_form(page)
            elif template_committed_directly:
                insert_result = "committed"
            else:
                insert_result = False

            if not insert_result:
                print("    Insert button not found; cancelling linked form so it cannot block the page.")
                _cancel_open_composite(page)
                continue

            if insert_result == "blocked_error":
                print(
                    f"    Insert for {title} was rejected; section remains unresolved."
                )
                continue

            # Actively watch for and confirm any remaining popups (Error / Warning / Alert) with OK
            for _ in range(3):
                page.wait_for_timeout(500)
                handle_warning_popups(page)

            if _cancel_empty_composite(page):
                continue
            handle_warning_popups(page)

            target_text = _target_linked_section_text(page, entry)
            if not target_text:
                print(
                    "    Warning cleared; waiting for selected items to appear "
                    "in the target section..."
                )
                target_text = _wait_for_target_linked_section(
                    page, entry, timeout_ms=12000
                )
            if not target_text:
                print(
                    "    Post-confirmation state: target still empty; "
                    f"open page state={_post_commit_diagnostics(page)}"
                )
                # A stale/disabled row can still leave Devero's chooser open.
                # Never claim success, and never allow that popup to block the
                # next linked section.
                if not _cancel_empty_composite(page):
                    _cancel_open_composite(page)
                print("    Insert did not populate the target section; cancelled linked form.")
                continue

            # Resolution and click attempts do not make a source safe to Save.
            # Mark it handled only after the committed target is visible
            # outside the transient linked-form/module surface.
            linked_state["matched_source_keys"].update(
                source.get("matched_record_keys") or []
            )
            persisted_entry = {
                key: entry.get(key)
                for key in (
                    "section", "kind", "actionId", "actionStem", "actionIndex",
                    "title", "elementId", "dataSection", "frameIndex",
                )
            }
            persisted_entry["pageNum"] = linked_state.get("current_page_num", 1)
            linked_state["inserted_target_entries"].append(persisted_entry)
            print(f"    Inserted {selection.get('selected', 0)} source-matching row(s) into target.")
            page.wait_for_timeout(1000)
        except Exception as e:
            print(f"--> Composite section {section} notice: {e}")
            if "has been closed" in str(e).lower():
                return
            try:
                if _template_surface_is_open(page):
                    _cancel_template_surface(page)
                if not _cancel_empty_composite(page):
                    _cancel_open_composite(page)
            except Exception as cleanup_error:
                LOGGER.debug(
                    "Composite cleanup failed for %s; continuing: %s",
                    section,
                    cleanup_error,
                )
                continue

    return linked_state


def sync_oasis_diagnoses(page, page_num):
    """
    Checks if the current OASIS page contains the M1021/M1023 Diagnosis Grid.
    If 'Update Diagnosis' action is present and the primary diagnosis grid is empty,
    clicks 'Update Diagnosis' to synchronize active diagnoses from the patient chart/episode
    into the assessment, confirms any dialogs, and waits for DOM hydration.
    """
    probe_script = """() => {
        const primDiag = document.querySelector('#prim_diag, [name="prim_diag"]');
        const primCode = document.querySelector('#prim_diag_code, [name="prim_diag_code"]');
        const text = (primDiag?.innerText || primDiag?.textContent || primDiag?.value || '').trim();
        const code = (primCode?.innerText || primCode?.textContent || primCode?.value || '').trim();

        const actions = Array.from(document.querySelectorAll(
            'cmwf-integrated-action, button, a, input[type="button"], .link'
        ));
        let updateAction = null;
        for (const el of actions) {
            const txt = (el.innerText || el.textContent || el.value || '').trim().toLowerCase();
            if (txt === 'update diagnosis' || txt.includes('update diagnosis')) {
                updateAction = {
                    id: el.id || '',
                    tag: el.tagName.toLowerCase(),
                    text: (el.innerText || el.textContent || el.value || '').trim(),
                    visible: !!(el.offsetWidth > 0 || el.offsetHeight > 0 || (el.getClientRects && el.getClientRects().length > 0))
                };
                break;
            }
        }

        return {
            hasDiagGrid: !!(primDiag || primCode || updateAction),
            isPopulated: !!(text || code),
            currentText: text,
            currentCode: code,
            updateAction: updateAction
        };
    }"""

    diag_info = None
    target_frame = None

    for frame in page.frames:
        try:
            info = frame.evaluate(probe_script)
            if info and info.get("hasDiagGrid"):
                diag_info = info
                target_frame = frame
                break
        except Exception:
            continue

    if not diag_info or not diag_info.get("hasDiagGrid"):
        return False

    if diag_info.get("isPopulated"):
        print(
            f"    [Page {page_num}] Diagnosis grid already populated "
            f"({diag_info.get('currentCode')}: {diag_info.get('currentText')[:40]}...). "
            "Preserving existing diagnoses."
        )
        return False

    update_action = diag_info.get("updateAction")
    if not update_action:
        print(f"    [Page {page_num}] Diagnosis grid detected but no 'Update Diagnosis' action found.")
        return False

    print(f"--> [Page {page_num}] Diagnosis grid empty. Triggering 'Update Diagnosis' from patient chart...")

    action_id = update_action.get("id")
    clicked = False
    frame_owner = target_frame or page

    try:
        locators = []
        if action_id:
            locators.append(frame_owner.locator(f'#{action_id}').first)
        locators.extend([
            frame_owner.locator('cmwf-integrated-action:has-text("Update Diagnosis")').first,
            frame_owner.locator('button:has-text("Update Diagnosis")').first,
            frame_owner.locator('a:has-text("Update Diagnosis")').first,
        ])
        for loc in locators:
            if loc and loc.count() > 0 and loc.is_visible():
                loc.scroll_into_view_if_needed()
                box = loc.bounding_box()
                if box:
                    page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                    clicked = True
                    break
                else:
                    loc.click()
                    clicked = True
                    break
    except Exception as exc:
        LOGGER.debug("Playwright click on Update Diagnosis notice: %s", exc)

    if not clicked:
        try:
            clicked = frame_owner.evaluate("""(actionId) => {
                const el = actionId ? document.getElementById(actionId) : null;
                const target = el || Array.from(document.querySelectorAll(
                    'cmwf-integrated-action, button, a, .link'
                )).find(e => (e.innerText || e.textContent || e.value || '').trim().toLowerCase().includes('update diagnosis'));
                if (!target) return false;
                target.scrollIntoView({ block: 'center', inline: 'center' });
                target.click();
                target.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
                if (target.shadowRoot) {
                    const inner = target.shadowRoot.querySelector('a, button, div, span, .link');
                    if (inner) {
                        inner.click();
                        inner.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
                    }
                }
                return true;
            }""", action_id)
        except Exception as exc:
            LOGGER.debug("JS click on Update Diagnosis notice: %s", exc)

    page.wait_for_timeout(2000)
    handle_warning_popups(page)

    # In DeVero, clicking 'Update Diagnosis' opens a Diagnosis Modal (.popup-modal, .modal.in).
    # Active diagnoses must be selected (their checkboxes checked) and committed via 'Insert to form' (ng-mousedown).
    modal_committed = page.evaluate("""() => {
        const visible = el => !!el && (el.offsetWidth > 0 || el.offsetHeight > 0 || (el.getClientRects && el.getClientRects().length > 0));
        const modal = document.querySelector('.popup-modal, .modal.in, [data-automation-id="button-insert-to-form"]')?.closest('.modal, .popup-modal') ||
                      Array.from(document.querySelectorAll('.modal, .popup-modal, .modal-dialog')).find(visible);
        if (!modal) return { handled: false, reason: 'no modal' };

        // 1. Check all diagnosis row checkboxes inside the modal
        const checkboxes = Array.from(modal.querySelectorAll('input[type="checkbox"]'));
        checkboxes.forEach(cb => {
            if (!cb.checked) {
                cb.scrollIntoView({ block: 'center' });
                cb.click();
                cb.dispatchEvent(new Event('change', { bubbles: true }));
                if (window.angular) {
                    try {
                        const scope = window.angular.element(cb).scope();
                        if (scope && !scope.$$phase) scope.$apply();
                    } catch(e) {}
                }
            }
        });

        // 2. Click 'Insert to form' button (uses ng-mousedown)
        const insertBtn = document.querySelector('[data-automation-id="button-insert-to-form"], button.confirm-btn-ok') ||
                          Array.from(modal.querySelectorAll('button, a, input[type="button"]')).find(b => {
                              const t = (b.innerText || b.textContent || b.value || '').trim().toLowerCase();
                              return t === 'insert to form' || t.includes('insert to form');
                          });

        if (insertBtn) {
            insertBtn.scrollIntoView({ block: 'center' });
            insertBtn.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true }));
            insertBtn.click();
            return { handled: true, action: 'insert_to_form', count: checkboxes.length };
        }

        // Fallback: Confirm any generic modal dialog that asks to confirm or update diagnoses
        const btns = Array.from(modal.querySelectorAll('button, a, input[type="button"], input[type="submit"], .btn')).filter(visible);
        const okBtn = btns.find(b => {
            const bTxt = (b.innerText || b.value || '').trim().toLowerCase();
            return ['ok', 'yes', 'update', 'continue', 'confirm'].includes(bTxt) || b.classList.contains('btn-primary') || b.classList.contains('bootbox-accept');
        });
        if (okBtn) {
            try { okBtn.click(); return { handled: true, action: 'confirm_btn' }; } catch(e) {}
        }

        return { handled: false, reason: 'no action button' };
    }""")

    LOGGER.debug("Diagnosis modal commit result: %s", modal_committed)
    page.wait_for_timeout(3000)
    handle_warning_popups(page)

    # Confirm any secondary overwrite/confirmation alert dialogs if they appear
    page.evaluate("""() => {
        const visible = el => !!el && (el.offsetWidth > 0 || el.offsetHeight > 0 || (el.getClientRects && el.getClientRects().length > 0));
        const dialogs = Array.from(document.querySelectorAll(
            '.modal-dialog, .modal-content, .bootbox, .ui-dialog, .sweet-alert, [role="dialog"], .popup-modal'
        )).filter(visible);
        dialogs.forEach(d => {
            const txt = (d.innerText || d.textContent || '').toLowerCase();
            if (txt.includes('diagnos') || txt.includes('update') || txt.includes('continue') || txt.includes('overwrite')) {
                const btns = Array.from(d.querySelectorAll('button, a, input[type="button"], input[type="submit"], .btn')).filter(visible);
                const okBtn = btns.find(b => {
                    const bTxt = (b.innerText || b.value || '').trim().toLowerCase();
                    return ['ok', 'yes', 'update', 'continue', 'confirm'].includes(bTxt) || b.classList.contains('btn-primary') || b.classList.contains('bootbox-accept');
                });
                if (okBtn) {
                    try { okBtn.click(); } catch(e) {}
                }
            }
        });
    }""")
    page.wait_for_timeout(2000)

    try:
        post_info = frame_owner.evaluate(probe_script)
        if post_info and post_info.get("isPopulated"):
            code = post_info.get("currentCode", "")
            desc = post_info.get("currentText", "")
            print(f"    [OK] Diagnosis grid populated from patient chart: Primary = {code} ({desc[:50]}).")
            return True
        else:
            print("    [NOTE] 'Update Diagnosis' clicked, but grid remains blank (chart profile may be empty for this patient).")
            return False
    except Exception as exc:
        LOGGER.debug("Post-update diagnosis check notice: %s", exc)
        return False


def open_note_by_ui_click(context, active_page, base_url, patient_name="", doc_type="", act_id="", note_date="", caregiver="", is_target=False, source_doc_type="", required_status=None):
    """
    Finds and opens a note link using Devero Patient Chart UI (/app#/patientCharts).

    Target Note (is_target=True):
    Strictly matches data rows by:
    1. Document Type (e.g. 'OASIS-E2 Nurse Resumption of Care')
    2. Target Note Date (e.g. '08/11/2026')
    3. Caregiver MUST match tasks.jsonl (e.g. 'Example, Clinician')
    4. Status defaults to 'Pending'; completed-document audits can require completed statuses

    Source Note (is_target=False):
    Selected by source_date (e.g. '07/16/2026'). Document type and caregiver can differ
    from target note unless source_doc_type is explicitly specified.
    """
    # Always select patient & open Patient Chart (/app#/patientCharts)
    select_patient_and_go_to_chart(active_page, patient_name, base_url)
    active_page.wait_for_timeout(3000)

    chart_note_count = active_page.evaluate("""() =>
        Array.from(document.querySelectorAll('table tr, .document-row')).filter(tr =>
            tr.querySelectorAll('td').length >= 3
        ).length
    """)
    LOGGER.debug("Patient Chart contains %s note rows", chart_note_count)

    status_filters = (("pending",) if is_target else ()) if required_status is None else (
        (required_status,) if isinstance(required_status, str) else tuple(required_status)
    )
    status_filters = tuple(str(status).strip().lower() for status in status_filters if str(status).strip())
    if is_target and not status_filters:
        raise ValueError("Target note status filter cannot be empty.")
    allow_print_form = not is_target or status_filters != ("pending",)
    known_pages = list(context.pages)
    chart_url = active_page.url

    # Helper JS function to search for matching note row link
    find_and_click_row_js = """(criteria) => {
        const docLower = (criteria.docType || '').trim().toLowerCase();
        const srcDocLower = (criteria.sourceDocType || '').trim().toLowerCase();
        const dateLower = (criteria.noteDate || '').trim().toLowerCase();
        const cgLower = (criteria.caregiver || '').split(',')[0].trim().toLowerCase();
        const statuses = (criteria.requiredStatuses || []).map(s => String(s).trim().toLowerCase()).filter(Boolean);
        const actId = criteria.actId ? String(criteria.actId).trim() : '';
        const isTarget = criteria.isTarget;

        const rows = Array.from(document.querySelectorAll('table tr, .document-row'));
        const excludedStatuses = new Set();

        for (let tr of rows) {
            const cells = Array.from(tr.querySelectorAll('td')).map(c => (c.innerText || '').trim().toLowerCase());
            if (cells.length < 3) continue;

            const fullRowText = cells.join(' ');

            // Target Note requires matching target doc_type.
            // Source Note matches sourceDocType if specified; otherwise if target is an OASIS note, source MUST contain 'oasis'.
            let matchesDocType = true;
            if (isTarget) {
                matchesDocType = !docLower || fullRowText.includes(docLower);
            } else if (srcDocLower) {
                matchesDocType = fullRowText.includes(srcDocLower);
            } else if (docLower && docLower.includes('oasis')) {
                matchesDocType = fullRowText.includes('oasis');
            }

            const matchesDate = dateLower ? fullRowText.includes(dateLower) : true;
            // Target Note strictly matches caregiver; explicit statuses also filter completed targets and sources.
            const matchesCaregiver = (isTarget && cgLower) ? fullRowText.includes(cgLower) : true;
            const matchesStatus = !statuses.length || statuses.some(status => fullRowText.includes(status));
            const matchesId = !actId || fullRowText.includes(actId);

            if (matchesDocType && matchesDate && matchesCaregiver && matchesId && !matchesStatus) {
                for (const status of ['pending', 'completed', 'sent to office']) {
                    if (fullRowText.includes(status)) excludedStatuses.add(status);
                }
            }
            if (matchesDocType && matchesDate && matchesCaregiver && matchesStatus && matchesId) {
                const link = tr.querySelector('.dbDrivenForm-new-window, .dbDrivenForm-inline-window, .standardform-new-window, .standardform-inline-window, a');
                if (link) {
                    link.scrollIntoView({ block: 'center' });
                    link.click();
                    return { clicked: true, text: cells.join(' | ') };
                }
            }
        }

        return { clicked: false, text: '', excludedStatuses: [...excludedStatuses] };
    }"""

    search_args = {
        'docType': doc_type,
        'sourceDocType': source_doc_type,
        'actId': act_id,
        'noteDate': note_date,
        'caregiver': caregiver,
        'requiredStatuses': status_filters,
        'isTarget': is_target
    }

    # Attempt 1: Search in currently visible episode
    res = active_page.evaluate(find_and_click_row_js, search_args)
    clicked = res.get('clicked') if isinstance(res, dict) else False
    row_text = res.get('text') if isinstance(res, dict) else ''
    excluded_statuses = set(res.get('excludedStatuses', [])) if isinstance(res, dict) else set()

    # Search each episode before opening the next (some charts use an accordion).
    if not clicked:
        print(f"--> Note link for date '{note_date}' not found in current episode. Searching all episodes...")
        for episode_index in iter_chart_episodes(active_page):
            LOGGER.debug("Searching chart episode %s", episode_index + 1)
            res = active_page.evaluate(find_and_click_row_js, search_args)
            clicked = res.get('clicked') if isinstance(res, dict) else False
            row_text = res.get('text') if isinstance(res, dict) else ''
            if isinstance(res, dict):
                excluded_statuses.update(res.get('excludedStatuses', []))
            if clicked:
                break

    if clicked:
        status_tag = "/".join(status.upper() for status in status_filters) or "SOURCE"
        print(f"--> Clicked note row '{row_text}' [{status_tag}] on Patient Chart!")
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            for p_tab in context.pages:
                if (p_tab not in known_pages and not p_tab.is_closed()
                        and p_tab.url != "about:blank"
                        and ((allow_print_form and print_form_ready(p_tab))
                             or wait_for_form_inputs(p_tab, min_inputs=5, timeout_sec=1) >= 5)):
                    return p_tab
            if active_page.url != chart_url and ((allow_print_form and print_form_ready(active_page))
                    or wait_for_form_inputs(active_page, min_inputs=5, timeout_sec=1) >= 5):
                return active_page
            active_page.wait_for_timeout(500)
        LOGGER.warning("Clicked note row did not render form controls within 20 seconds")
        for p_tab in context.pages:
            if p_tab not in known_pages and not p_tab.is_closed() and p_tab.url != "about:blank":
                return p_tab
        return active_page if active_page.url != chart_url else None

    status_tag = "/".join(status.upper() for status in status_filters) or "SOURCE"
    doc_desc = f"'{doc_type}'" if is_target else (f"'{source_doc_type}'" if source_doc_type else "Note")
    cg_desc = f" by '{caregiver}'" if (is_target and caregiver) else ""
    if excluded_statuses:
        print(f"--> [NOTICE] Matching document/date/caregiver row exists with status "
              f"{' / '.join(sorted(excluded_statuses)).upper()}; required status: {status_tag}.")
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

def _find_target_save_button(page):
    """Return the visible enabled note-level Save control."""
    candidates = page.locator(
        'button, a, input[type="button"], input[type="submit"]'
    )
    for label in ("Save", "Save & Close"):
        for index in range(candidates.count()):
            candidate = candidates.nth(index)
            try:
                text = (
                    candidate.inner_text() or candidate.get_attribute("value") or ""
                ).strip()
                ng_click = candidate.get_attribute("ng-click") or ""
                in_popup = candidate.evaluate(
                    "el => !!el.closest('.modal-dialog, #formFramePopup')"
                )
                if (
                    text == label
                    and candidate.is_visible()
                    and candidate.is_enabled()
                    and not in_popup
                    and "ReasonCodes" not in ng_click
                    and "CPTCode" not in ng_click
                ):
                    return candidate
            except Exception:
                continue
    raise RuntimeError("Save button was not found or is not enabled.")


def _click_target_save(page, page_num):
    """Click Save through Playwright so Devero receives trusted pointer events."""
    print(f"--> Clicking SAVE button on Page {page_num}...")
    save_button = _find_target_save_button(page)
    save_button.scroll_into_view_if_needed()
    try:
        save_button.click()
    except Exception:
        # Some Devero note windows close during the trusted click. Persistence
        # is still proven below by reopening the target from the chart.
        if not page.is_closed():
            raise
    print("--> Save click sent; waiting for Devero to commit the note...")
    try:
        page.wait_for_timeout(4000)
    except Exception:
        if not page.is_closed():
            raise


def _reopen_saved_target(page, save_context):
    """Reload or reopen the exact Pending target after Save."""
    context = save_context["context"]
    if page.is_closed():
        print("--> Target note closed after Save; reopening it from Patient Chart...")
        active_page = save_context["active_page"]
        if active_page.is_closed():
            surviving = [candidate for candidate in context.pages if not candidate.is_closed()]
            active_page = surviving[-1] if surviving else context.new_page()
        reopened = open_note_by_ui_click(
            context,
            active_page,
            save_context["base_url"],
            save_context.get("patient_name", ""),
            save_context.get("doc_type", ""),
            parse_activity_id(save_context.get("target_ref", "")),
            save_context.get("note_date", ""),
            save_context.get("caregiver", ""),
            True,
        )
        if not reopened:
            raise RuntimeError(
                "Save could not be verified because the exact Pending target did not reopen."
            )
        return reopened

    print("--> Reloading Target note to verify server-persisted data...")
    page.reload(wait_until="domcontentloaded", timeout=30000)
    return page


def _verify_persisted_linked_sections(page, entries):
    """Require each newly inserted linked section to remain nonempty after reload."""
    remaining = list(entries or [])
    if not remaining:
        return
    try:
        returned_to_first = page.evaluate("""() => {
            const visible = el => !!el &&
                (el.offsetWidth > 0 || el.offsetHeight > 0 ||
                 (el.getClientRects && el.getClientRects().length > 0));
            const first = Array.from(document.querySelectorAll('a, button')).find(
                el => visible(el) && (el.innerText || '').trim() === '1'
            );
            if (!first) return false;
            first.click();
            return true;
        }""")
        if returned_to_first:
            page.wait_for_timeout(2500)
    except Exception:
        pass
    page_num = 1
    while remaining:
        wait_for_form_inputs(page, min_inputs=5)
        for entry in list(remaining):
            if int(entry.get("pageNum") or 1) != page_num:
                continue
            if _wait_for_target_linked_section(page, entry, timeout_ms=5000):
                remaining.remove(entry)
        if not remaining or not go_to_next_page(page, page_num):
            break
        page_num += 1
        page.wait_for_timeout(2500)

    if remaining:
        names = ", ".join(
            str(entry.get("title") or entry.get("section") or "linked section")
            for entry in remaining
        )
        raise RuntimeError(
            f"Post-save verification failed: linked content was not persisted for {names}."
        )
    if entries:
        print(
            f"[OK] Verified {len(entries)} linked section(s) after server reload."
        )


def inject_all_pages_target(page, extracted_fields, save_context=None):
    """Iterates through all pages of Target Note, injecting fields across all pages and frames."""
    total_updated = 0
    total_skipped = 0
    total_visited = 0
    total_matched = 0
    injection_errors = []
    page_num = 1
    source_linked_records = _source_linked_records(extracted_fields)
    linked_state = {
        "source_record_keys": {
            record["record_key"] for record in source_linked_records
        },
        "source_record_sections": {
            record["record_key"]: record.get("section") or record.get("action_stem") or "unknown"
            for record in source_linked_records
        },
        "matched_source_keys": set(),
        "applicable_source_keys": set(),
        "intentionally_skipped_source_keys": set(),
        "discovered_targets": 0,
        "unresolved_targets": [],
        "inserted_target_entries": [],
    }

    extracted_cbs = {k: v for k, v in extracted_fields.items() if isinstance(v, dict) and v.get('kind') == 'checkbox' and v.get('checked')}
    print(f"--> Total CHECKED checkboxes available in extracted fields: {len(extracted_cbs)}")

    while True:
        wait_for_form_inputs(page, min_inputs=5)
        print(f"--> Injecting Target Note Page {page_num}...")

        for f_idx, frame in enumerate(page.frames):
            try:
                stats = frame.evaluate(JS_INJECT_FIELDS, extracted_fields)
                if stats:
                    up = stats.get('updated', 0)
                    sk = stats.get('skipped', 0)
                    total_updated += up
                    total_skipped += sk
                    visited = stats.get('visited', 0)
                    matched = stats.get('matched', 0)
                    total_visited += visited
                    total_matched += matched
                    print(
                        f"    [Page {page_num} Frame {f_idx}] Result: {up} updated, "
                        f"{sk} preserved, {matched}/{visited} elements matched."
                    )

            except Exception as e:
                error = f"Page {page_num} Frame {f_idx} ({frame.url}): {e}"
                injection_errors.append(error)
                print(f"    [INJECTION ERROR] {error}")

        linked_state["current_page_num"] = page_num
        page_linked_state = inject_composite_linked_forms(
            page, extracted_fields, linked_state=linked_state
        )
        if page_linked_state:
            linked_state = page_linked_state

        # Synchronize M1021/M1023 Diagnosis Grid from patient chart if present on this page
        sync_oasis_diagnoses(page, page_num)

        has_next = go_to_next_page(page, page_num)
        if not has_next:
            print(f"--> Reached final page ({page_num}). Injection complete!")
            break
        page_num += 1
        print(f"--> Navigating to Target Note Page {page_num}...")
        # Devero replaces the active page asynchronously. Without this wait the
        # next pagination lookup can run while Angular is still tearing down the
        # prior page, causing a 15-page ROC form to stop after page 2.
        page.wait_for_timeout(3000)

    # Never save or report success when every target frame failed before it could
    # inspect a field. This previously produced a misleading 0/0 SUCCESS result.
    if total_visited == 0:
        details = injection_errors[0] if injection_errors else "no target form elements were visited"
        raise RuntimeError(f"Target injection failed: {details}")
    if total_matched == 0:
        raise RuntimeError(
            f"Target injection found {total_visited} form elements but none matched the source fields"
        )
    unresolved = _report_unresolved_linked_sources(linked_state)

    if save_context is None:
        raise RuntimeError(
            "Save was blocked because post-save target verification was not configured."
        )
    page.wait_for_timeout(2000)
    _click_target_save(page, page_num)
    verified_page = _reopen_saved_target(page, save_context)
    if wait_for_form_inputs(verified_page, min_inputs=15) == 0:
        raise RuntimeError(
            "Save could not be verified because the reopened target form did not load."
        )
    _verify_persisted_linked_sections(
        verified_page, linked_state.get("inserted_target_entries") or []
    )
    print("[OK] Target note Save verified from server-persisted data.")

    review_sections = sorted({
        (linked_state.get("source_record_sections") or {}).get(key, "unknown")
        for key in unresolved
    })
    return total_updated, total_skipped, review_sections

def navigate_to_note(context, active_page, base_url, url_or_activity_id, is_target=False, patient_name="", doc_type="", note_date="", caregiver="", source_doc_type=""):
    val_str = str(url_or_activity_id).strip()
    activity_id = parse_activity_id(val_str)
    note_label = "Target" if is_target else "Source"

    # Option A: If full URL provided, try page.goto directly
    if val_str.startswith("http://") or val_str.startswith("https://"):
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
    status_label = "PENDING" if is_target else "SOURCE"
    print(f"--> Opening {note_label} Note ({status_label}) via Devero Patient Chart UI...")
    navigated_page = open_note_by_ui_click(context, active_page, base_url, patient_name, doc_type, activity_id, note_date, caregiver, is_target, source_doc_type)

    if navigated_page:
        return navigated_page

    return None

def load_tasks_config(config_file):
    """
    Loads tasks configuration with support for:
    - JSON array format: [ {...}, {...} ]
    - JSON Lines (JSONL) format: newline-delimited {...} objects
    - Single JSON object: {...}
    - Single-line (//) and block (/* ... */) comments
    - Trailing commas
    - Filtering by enabled: false or skip: true
    """
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

    clean_content = remove_comments(content).strip()
    raw_tasks = []

    # Attempt 1: Standard JSON Array format
    if clean_content.startswith("["):
        try:
            clean_arr = re.sub(r',\s*([\]}])', r'\1', clean_content)
            parsed = json.loads(clean_arr)
            if isinstance(parsed, list):
                raw_tasks = parsed
            elif isinstance(parsed, dict):
                raw_tasks = [parsed]
        except Exception:
            raw_tasks = []

    # Attempt 2: Single JSON object format
    if not raw_tasks and clean_content.startswith("{") and clean_content.endswith("}"):
        try:
            clean_obj = re.sub(r',\s*([\]}])', r'\1', clean_content)
            parsed = json.loads(clean_obj)
            if isinstance(parsed, dict):
                raw_tasks = [parsed]
        except Exception:
            raw_tasks = []

    # Attempt 3: Line-by-line JSON Lines (.jsonl) format
    if not raw_tasks:
        for line in clean_content.splitlines():
            line = line.strip()
            if not line or line in ("[", "]", ","):
                continue
            if line.endswith(","):
                line = line[:-1].strip()
            try:
                item = json.loads(line)
                if isinstance(item, dict):
                    raw_tasks.append(item)
                elif isinstance(item, list):
                    raw_tasks.extend(item)
            except Exception:
                continue

    active_tasks = []
    for t in raw_tasks:
        if isinstance(t, dict):
            if t.get("enabled", True) is False or t.get("skip", False) is True:
                continue
            active_tasks.append(t)
    return active_tasks

def main():
    parser = argparse.ArgumentParser(
        description="Devero Copier Resumption of Care - Automate copying resumption of care documents in Devero EHR."
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
    args = parser.parse_args()

    config_file = args.config_file or args.tasks_file or "tasks.jsonl"

    if not os.path.exists(config_file):
        job_status("[FAILED] Job configuration was not found.")
        LOGGER.error("Config file not found: %s", config_file)
        sys.exit(1)

    tasks = load_tasks_config(config_file)
    job_status(f"[STARTED] Resumption-of-care copy job with {len(tasks)} task(s).")
    succeeded_tasks = 0
    failed_tasks = 0
    skipped_tasks = 0

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
            launch_args = ["--no-first-run", "--no-default-browser-check", "--remote-debugging-port=9222"]
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
            source_doc_type = task.get("source_doc_type", "")
            source_date = task.get("source_date", "")
            target_date = task.get("target_date", "")
            source_val = task.get("source_url") or task.get("source_activity_id") or ""
            target_val = task.get("target_url") or task.get("target_activity_id") or ""

            if not patient or not doc_type or not source_date or not target_date:
                print(f"[SKIP Task #{idx}/{total_tasks}]: Missing patient, doc_type, source_date, or target_date.")
                skipped_tasks += 1
                job_status(f"[SKIPPED] Task {idx}/{total_tasks}: incomplete configuration.")
                continue

            print("\n==================================================")
            print(f"Processing Task #{idx}/{total_tasks}: {doc_type} (Target)")
            print(f"   Patient:     {patient}")
            if source_doc_type:
                print(f"   Source Doc:  {source_doc_type} ({source_date})")
            else:
                print(f"   Source Date: {source_date} (Selected by date only)")
            print(f"   Target Doc:  {doc_type} ({target_date}, Pending, Caregiver: {caregiver})")
            print("==================================================")
            job_status(f"[RUNNING] Task {idx}/{total_tasks}: {doc_type}.")

            # Step 1: Navigate to Source Note & Extract Fields
            source_page = navigate_to_note(context, main_page, base_url, source_val, is_target=False, patient_name=patient, doc_type=doc_type, note_date=source_date, caregiver=caregiver, source_doc_type=source_doc_type)

            if not source_page:
                print(f"\n==================================================")
                print(f"[ERROR Task #{idx}/{total_tasks}]: SOURCE NOTE DOES NOT EXIST!")
                print(f"   Patient:     '{patient}'")
                if source_doc_type:
                    print(f"   Source Doc:  '{source_doc_type}'")
                print(f"   Source Date: '{source_date}'")
                print(f"   Reason:      No matching source note row found on Patient Chart for date '{source_date}'!")
                print(f"==================================================")
                failed_tasks += 1
                job_status(f"[FAILED] Task {idx}/{total_tasks}: source note not found.")
                continue

            src_inputs = wait_for_form_inputs(source_page, min_inputs=15)
            if src_inputs == 0 and not print_form_ready(source_page):
                print(f"\n==================================================")
                print(f"[ERROR Task #{idx}/{total_tasks}]: SOURCE NOTE FORM FAILED TO LOAD!")
                print(f"   Patient:     '{patient}'")
                print(f"   Source Date: '{source_date}'")
                print(f"   Reason:      No source form controls appeared in the selected page or frames.")
                print(f"==================================================")
                failed_tasks += 1
                job_status(f"[FAILED] Task {idx}/{total_tasks}: source form did not load.")
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
                failed_tasks += 1
                job_status(f"[FAILED] Task {idx}/{total_tasks}: source form contained no transferable fields.")
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
                failed_tasks += 1
                job_status(f"[FAILED] Task {idx}/{total_tasks}: pending target note not found.")
                if source_page != main_page:
                    try: source_page.close()
                    except Exception: pass
                continue

            tgt_inputs = wait_for_form_inputs(target_page, min_inputs=15)
            if tgt_inputs == 0:
                print(f"\n==================================================")
                print(f"[ERROR Task #{idx}/{total_tasks}]: TARGET NOTE FORM FAILED TO LOAD!")
                print(f"   Patient:     '{patient}'")
                print(f"   Doc Type:    '{doc_type}'")
                print(f"   Target Date: '{target_date}'")
                print(f"   Reason:      0 form inputs loaded in Target Note tab!")
                print(f"==================================================")
                failed_tasks += 1
                job_status(f"[FAILED] Task {idx}/{total_tasks}: target form did not load.")
                if source_page != main_page:
                    try: source_page.close()
                    except Exception: pass
                if target_page != main_page:
                    try: target_page.close()
                    except Exception: pass
                continue

            # Check if temperature field is already filled in Target Note (indicates it was processed earlier)
            temp_val = target_page.evaluate(JS_CHECK_TEMPERATURE)
            if temp_val:
                print(f"\n==================================================")
                print(f"[CANCEL Task #{idx}/{total_tasks}]: TARGET NOTE ALREADY PROCESSED!")
                print(f"   Patient:     '{patient}'")
                print(f"   Doc Type:    '{doc_type}'")
                print(f"   Temperature: '{temp_val}' (Field: cf_Temp_Txt)")
                print(f"   Notice:      Temperature already set up, looks like it was filled early. No changes made to the document.")
                print(f"==================================================")
                skipped_tasks += 1
                job_status(f"[SKIPPED] Task {idx}/{total_tasks}: target was already processed.")
                if source_page and source_page != main_page:
                    try: source_page.close()
                    except Exception: pass
                if target_page and target_page != main_page:
                    try: target_page.close()
                    except Exception: pass
                for tab in list(context.pages):
                    if tab != main_page and tab.url != "about:blank":
                        try: tab.close()
                        except Exception: pass
                continue

            print("--> Injecting fields across all pages of Target Note (Preserving pre-filled data)...")
            updated_count, skipped_count, review_sections = inject_all_pages_target(
                target_page,
                extracted_fields,
                save_context={
                    "context": context,
                    "active_page": main_page,
                    "base_url": base_url,
                    "target_ref": target_val,
                    "patient_name": patient,
                    "doc_type": doc_type,
                    "note_date": target_date,
                    "caregiver": caregiver,
                },
            )

            # Close the completed source tab now; the remaining browser is
            # closed automatically after the final task finishes.
            if source_page and source_page != main_page and source_page != target_page:
                try:
                    print("--> Closing Completed Source Note tab...")
                    source_page.close()
                except Exception as e:
                    print(f"--> Source tab close notice: {e}")

            print(f"\n==================================================")
            print(f"[SUCCESS] Transfer completed for Task #{idx}/{total_tasks} ({patient})!")
            print(f"   - Target Fields Populated: {updated_count}")
            print(f"   - Pre-filled Fields Preserved: {skipped_count}")
            if review_sections:
                print(f"   - RN Review Required: {', '.join(review_sections)}")
            print(f"==================================================")
            succeeded_tasks += 1
            review_status = f" RN review required: {', '.join(review_sections)}." if review_sections else ""
            job_status(f"[SUCCEEDED] Task {idx}/{total_tasks} saved.{review_status}")

        keep_browser_open = os.getenv("DEVERO_KEEP_BROWSER_OPEN", "false").lower() in (
            "true", "1", "yes"
        )
        if keep_browser_open:
            print("\nAll tasks processed. Leaving shared Chrome open for Hermes review.")
        else:
            print("\nAll tasks processed. Closing browser...")
            try:
                active_browser = context.browser
                if active_browser:
                    # This also closes Chrome when the script attached over CDP.
                    active_browser.close()
                else:
                    # Persistent contexts may not expose a Browser object.
                    context.close()
                print("[OK] Browser closed.")
            except Exception as e:
                print(f"--> Browser close notice: {e}")
        job_status(
            f"[FINISHED] Job complete: {succeeded_tasks} succeeded, "
            f"{failed_tasks} failed, {skipped_tasks} skipped."
        )

if __name__ == "__main__":
    try:
        main()
    except Exception:
        LOGGER.exception("Unhandled resumption-of-care copier failure")
        job_status("[FAILED] Job terminated unexpectedly. See server log for details.")
        raise SystemExit(1)
