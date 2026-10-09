// ==UserScript==
// @name         Devero Note Copier (1-Click Copy & Paste)
// @namespace    http://tampermonkey.net/
// @version      1.1
// @description  Copy dynamic form fields from completed Devero note to pending note without overwriting pre-filled data
// @match        https://*.devero.com/*
// @grant        none
// @run-at       document-end
// ==UserScript==

(function() {
    'use strict';

    if (window.top !== window.self) return;

    const STORAGE_KEY = 'devero_copied_note_fields';

    function createUI() {
        if (document.getElementById('devero-copier-bar')) return;

        const bar = document.createElement('div');
        bar.id = 'devero-copier-bar';
        bar.style.cssText = `
            position: fixed;
            bottom: 20px;
            right: 20px;
            z-index: 999999;
            background: #1e293b;
            color: #f8fafc;
            padding: 10px 16px;
            border-radius: 12px;
            box-shadow: 0 10px 25px rgba(0,0,0,0.4);
            display: flex;
            gap: 10px;
            align-items: center;
            font-family: Arial, sans-serif;
            font-size: 13px;
        `;

        bar.innerHTML = `
            <span style="font-weight:bold; color:#38bdf8;">📋 Devero Copier:</span>
            <button id="btn-copy-note" style="background:#2563eb; color:white; border:none; padding:6px 12px; border-radius:6px; cursor:pointer; font-weight:bold;">Copy Note Data</button>
            <button id="btn-paste-note" style="background:#16a34a; color:white; border:none; padding:6px 12px; border-radius:6px; cursor:pointer; font-weight:bold;">Paste Note Data</button>
            <span id="copier-status" style="font-size:11px; color:#cbd5e1;"></span>
        `;

        document.body.appendChild(bar);

        document.getElementById('btn-copy-note').onclick = extractFields;
        document.getElementById('btn-paste-note').onclick = injectFields;
    }

    function cleanKey(str) {
        if (!str) return '';
        return str.toLowerCase().replace(/[^a-z0-9]/g, '');
    }

    function hasValue(el) {
        if (!el) return false;
        const tag = el.tagName.toLowerCase();
        const type = (el.type || 'text').toLowerCase();

        if (type === 'checkbox' || type === 'radio') return el.checked;
        if (tag === 'select') return el.value !== '' && el.value !== '?' && el.value !== null && el.value !== '0';
        return el.value && el.value.trim() !== '';
    }

    function formatTimeValue(val, type) {
        if (!val) return '';
        if (type === 'time') {
            const match = val.match(/(\d{1,2}):(\d{2})\s*(AM|PM)?/i);
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

    function extractFields() {
        const fields = {};
        let totalExtracted = 0;

        function scanDoc(doc) {
            if (!doc) return;
            const selectors = 'input, textarea, select, cmwf-textbox, cmwf-text-display, cmwf-cop, cmwf-integrated-action, [data-field-id], [ng-model]';
            const elements = doc.querySelectorAll(selectors);

            elements.forEach((el, index) => {
                const tag = el.tagName.toLowerCase();
                const type = (el.type || '').toLowerCase();

                const keys = [
                    el.name,
                    el.id,
                    el.getAttribute('ng-model'),
                    el.getAttribute('data-field-id'),
                    el.getAttribute('aria-label')
                ].filter(Boolean);

                let parentCw = el.closest ? el.closest('cmwf-textbox, cmwf-text-display, cmwf-cop, cmwf-integrated-action') : null;
                if (parentCw && parentCw !== el) {
                    if (parentCw.id) keys.push(parentCw.id);
                    if (parentCw.getAttribute('ng-model')) keys.push(parentCw.getAttribute('ng-model'));
                    if (parentCw.getAttribute('data-field-id')) keys.push(parentCw.getAttribute('data-field-id'));
                }

                const fallbackKey = `pos_${index}_${tag}_${type}`;
                const primaryKey = keys[0] || fallbackKey;

                let item = null;
                if (type === 'checkbox') {
                    item = { kind: 'checkbox', checked: el.checked, value: el.value, keys: keys, index: index };
                } else if (type === 'radio') {
                    if (el.checked) item = { kind: 'radio', value: el.value, keys: keys, index: index };
                } else if (tag === 'select') {
                    item = { kind: 'select', value: el.value, keys: keys, index: index };
                } else if (tag === 'cmwf-text-display' || tag === 'cmwf-cop') {
                    const val = (el.innerText || el.textContent || '').trim();
                    if (val) item = { kind: 'text', value: val, keys: keys, index: index };
                } else if (tag === 'cmwf-textbox') {
                    let val = el.value;
                    const innerInp = el.querySelector('input, textarea');
                    if (innerInp && innerInp.value) val = innerInp.value;
                    if (!val) val = (el.innerText || el.textContent || '').trim();
                    if (val) item = { kind: 'text', value: val, keys: keys, index: index };
                } else {
                    let val = el.value;
                    if ((val === undefined || val === null || val === '') && el.innerText) val = el.innerText.trim();
                    item = { kind: 'text', value: val, keys: keys, index: index };
                }

                if (item && item.value !== undefined && item.value !== null) {
                    totalExtracted++;
                    fields[primaryKey] = item;
                    fields[fallbackKey] = item;
                    keys.forEach(k => {
                        fields[k] = item;
                        const cleaned = cleanKey(k);
                        if (cleaned) fields[cleaned] = item;
                    });
                }
            });

            doc.querySelectorAll('iframe').forEach(iframe => {
                try { if (iframe.contentDocument) scanDoc(iframe.contentDocument); } catch(e){}
            });
        }

        scanDoc(document);
        localStorage.setItem(STORAGE_KEY, JSON.stringify(fields));

        const status = document.getElementById('copier-status');
        status.style.color = '#4ade80';
        status.textContent = `✔ Copied ${totalExtracted} fields!`;
        alert(`Copied ${totalExtracted} fields! Now open your Target Note and click 'Paste Note Data'.`);
    }

    function checkTemperatureFilled(doc) {
        if (!doc) doc = document;

        function scan(d) {
            if (!d) return null;
            const selectors = [
                'input[name="cf_Temp_Txt"]',
                '[name="cf_Temp_Txt"]',
                '#cf_Temp_Txt',
                '[data-field-id="cf_Temp_Txt"]',
                '[ng-model*="cf_Temp_Txt"]'
            ];
            for (let sel of selectors) {
                const elements = d.querySelectorAll(sel);
                for (let el of elements) {
                    let val = el.value;
                    if ((val === undefined || val === null || val === '') && el.getAttribute) {
                        val = el.getAttribute('value');
                    }
                    if (!val && el.innerText) val = el.innerText;
                    if (val && String(val).trim() !== '') return String(val).trim();
                }
            }

            const inputs = d.querySelectorAll('input, textarea');
            for (let el of inputs) {
                const nameAttr = (el.name || el.id || el.getAttribute('ng-model') || el.getAttribute('data-field-id') || '').toLowerCase();
                if (nameAttr.includes('temp_txt') || nameAttr === 'cf_temp_txt') {
                    let val = el.value;
                    if (val && String(val).trim() !== '') return String(val).trim();
                }
            }

            const iframes = d.querySelectorAll('iframe');
            for (let iframe of iframes) {
                try {
                    if (iframe.contentDocument) {
                        const res = scan(iframe.contentDocument);
                        if (res !== null) return res;
                    }
                } catch(e) {}
            }
            return null;
        }

        return scan(doc);
    }

    function injectFields() {
        const tempVal = checkTemperatureFilled(document);
        if (tempVal) {
            const status = document.getElementById('copier-status');
            if (status) {
                status.style.color = '#ef4444';
                status.textContent = `🚫 Cancelled: Temp set (${tempVal})`;
            }
            alert(`Task Cancelled: Temperature already set up (${tempVal}). Looks like it was filled early, no changes were made to the document.`);
            return;
        }

        const dataStr = localStorage.getItem(STORAGE_KEY);
        if (!dataStr) {
            alert('No copied note data found! Please click "Copy Note Data" on your Source Note first.');
            return;
        }

        const fieldMap = JSON.parse(dataStr);
        let updatedCount = 0;
        let skippedCount = 0;

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
                    if (k in scope) { scope[k] = val; updatedScope = true; }
                    if (scope.formData && (k in scope.formData)) { scope.formData[k] = val; updatedScope = true; }
                    const ngModel = el.getAttribute ? el.getAttribute('ng-model') : null;
                    if (ngModel && ngModel in scope) { scope[ngModel] = val; updatedScope = true; }
                });
                if (updatedScope && !scope.$$phase) { scope.$evalAsync(); }
            } catch(e) {}
        }

        function injectDoc(doc) {
            if (!doc) return;
            const selectors = 'input, textarea, select, cmwf-textbox, cmwf-text-display, cmwf-cop, cmwf-integrated-action, [data-field-id], [ng-model]';
            const elements = doc.querySelectorAll(selectors);

            elements.forEach((el, index) => {
                // RULE: Do NOT rewrite if target field already has data!
                if (hasValue(el)) {
                    skippedCount++;
                    return;
                }

                const tag = el.tagName.toLowerCase();
                const type = (el.type || 'text').toLowerCase();

                const candidateKeys = [
                    el.name,
                    el.id,
                    el.getAttribute('ng-model'),
                    el.getAttribute('data-field-id'),
                    el.getAttribute('aria-label'),
                    cleanKey(el.name),
                    cleanKey(el.id),
                    cleanKey(el.getAttribute('ng-model')),
                    `pos_${index}_${tag}_${type}`
                ].filter(Boolean);

                let parentCw = el.closest ? el.closest('cmwf-textbox, cmwf-text-display, cmwf-cop, cmwf-integrated-action') : null;
                if (parentCw && parentCw !== el) {
                    if (parentCw.id) candidateKeys.push(parentCw.id, cleanKey(parentCw.id));
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
                            updatedCount++;
                        }
                    } else if (type === 'radio') {
                        if (el.value === matchedItem.value) {
                            el.checked = true;
                            triggerEvents(el, doc);
                            syncAngularScope(el, doc, matchedItem.value, candidateKeys);
                            updatedCount++;
                        }
                    } else if (tag === 'select') {
                        if (matchedItem.value) {
                            el.value = matchedItem.value;
                            triggerEvents(el, doc);
                            syncAngularScope(el, doc, matchedItem.value, candidateKeys);
                            updatedCount++;
                        }
                    } else if (tag === 'cmwf-textbox') {
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
                            updatedCount++;
                        }
                    } else if (tag === 'cmwf-text-display' || tag === 'cmwf-cop') {
                        const finalVal = matchedItem.value || '';
                        if (finalVal) {
                            el.innerText = finalVal;
                            triggerEvents(el, doc);
                            syncAngularScope(el, doc, finalVal, candidateKeys);
                            updatedCount++;
                        }
                    } else {
                        const finalVal = formatTimeValue(matchedItem.value, type);
                        if (finalVal) {
                            el.value = finalVal;
                            triggerEvents(el, doc);
                            syncAngularScope(el, doc, finalVal, candidateKeys);
                            updatedCount++;
                        }
                    }
                } catch(err) {}
            });

            doc.querySelectorAll('iframe').forEach(iframe => {
                try { if (iframe.contentDocument) injectDoc(iframe.contentDocument); } catch(e){}
            });

            const win = doc.defaultView || window;
            if (win.angular) {
                try {
                    const rootScope = win.angular.element(doc.body).injector().get('$rootScope');
                    if (rootScope && !rootScope.$$phase) rootScope.$apply();
                } catch (e) {}
            }
        }

        injectDoc(document);

        const status = document.getElementById('copier-status');
        status.style.color = '#4ade80';
        status.textContent = `✔ Filled ${updatedCount} empty fields (${skippedCount} preserved)!`;
        alert(`Successfully filled ${updatedCount} empty fields!\n${skippedCount} pre-filled fields were preserved without being overwritten.`);
    }

    setTimeout(createUI, 1500);
})();
