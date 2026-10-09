from playwright.sync_api import sync_playwright

JS_EXTRACT_FIELDS = """
() => {
    const fields = {};

    function cleanKey(str) {
        if (!str) return '';
        return str.toLowerCase().replace(/[^a-z0-9]/g, '');
    }

    function scanDoc(doc, prefix = '') {
        if (!doc) return;

        const selectors = 'input, textarea, select, cmwf-checkbox, cmwf-textbox, cmwf-text-display, cmwf-cop, cmwf-integrated-action, cmwf-dropdown, ntst-select, [data-field-id], [ng-model]';
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

            let parentCw = el.closest ? el.closest('cmwf-checkbox, cmwf-textbox, cmwf-text-display, cmwf-cop, cmwf-integrated-action, cmwf-dropdown, ntst-select') : null;
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
                if (el.checked) isChecked = true;
                if (el.hasAttribute && (el.hasAttribute('checked') || el.getAttribute('checked') === 'true' || el.getAttribute('checked') === 'checked')) isChecked = true;

                const innerInp = el.querySelector ? el.querySelector('input[type="checkbox"]') : null;
                if (innerInp) {
                    if (innerInp.checked || (innerInp.hasAttribute && (innerInp.hasAttribute('checked') || innerInp.getAttribute('checked') === 'true'))) isChecked = true;
                }

                const outerCw = el.closest ? el.closest('cmwf-checkbox') : null;
                if (outerCw) {
                    if (outerCw.checked || (outerCw.hasAttribute && (outerCw.hasAttribute('checked') || outerCw.getAttribute('checked') === 'true')) || outerCw.classList.contains('checked')) isChecked = true;
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
                item = {
                    kind: 'select',
                    value: el.value,
                    keys: specificKeys.concat(genericKeys),
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
                        keys: specificKeys.concat(genericKeys),
                        index: index
                    };
                }
            } else if (tag === 'cmwf-text-display' || tag === 'cmwf-cop') {
                const val = (el.innerText || el.textContent || '').trim();
                if (val) {
                    item = {
                        kind: 'text',
                        value: val,
                        keys: specificKeys.concat(genericKeys),
                        index: index
                    };
                }
            } else if (tag === 'cmwf-textbox') {
                let val = el.value;
                const innerInp = el.querySelector('input, textarea');
                if (innerInp && innerInp.value) val = innerInp.value;
                if (!val) val = (el.innerText || el.textContent || '').trim();
                if (val) {
                    item = {
                        kind: 'text',
                        value: val,
                        keys: specificKeys.concat(genericKeys),
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
                    keys: specificKeys.concat(genericKeys),
                    index: index
                };
            }

            if (item && item.value !== undefined && item.value !== null) {
                fields[primaryKey] = item;
                specificKeys.forEach(k => {
                    if (!k) return;
                    fields[k] = item;
                    const cleaned = cleanKey(k);
                    if (cleaned) fields[cleaned] = item;
                });
                genericKeys.forEach(k => {
                    if (!k) return;
                    if (!fields[k] || (item.kind === 'checkbox' && item.checked)) {
                        fields[k] = item;
                    }
                    const cleaned = cleanKey(k);
                    if (cleaned && (!fields[cleaned] || (item.kind === 'checkbox' && item.checked))) {
                        fields[cleaned] = item;
                    }
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
    let stats = { updated: 0, skipped: 0, total: Object.keys(fieldMap).length };

    function cleanKey(str) {
        if (!str) return '';
        return str.toLowerCase().replace(/[^a-z0-9]/g, '');
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
        if (tag === 'select') {
            return el.value !== '' && el.value !== '?' && el.value !== null && el.value !== '0';
        }
        if (tag === 'cmwf-text-display' || tag === 'cmwf-cop') {
            const txt = (el.innerText || el.textContent || '').trim();
            return txt !== '';
        }
        if (tag === 'cmwf-textbox') {
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

    function triggerEvents(el, doc) {
        try {
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
            el.dispatchEvent(new Event('blur', { bubbles: true }));
        } catch(e) {}
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

        const selectors = 'input, textarea, select, cmwf-checkbox, cmwf-textbox, cmwf-text-display, cmwf-cop, cmwf-integrated-action, cmwf-dropdown, ntst-select, [data-field-id], [ng-model]';
        const elements = doc.querySelectorAll(selectors);

        elements.forEach((el, index) => {
            if (hasValue(el)) {
                stats.skipped++;
                return;
            }

            const tag = el.tagName.toLowerCase();
            const type = (el.type || 'text').toLowerCase();

            const candidateKeys = [];

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

            const nameAttr = el.name || (el.getAttribute ? el.getAttribute('name') : null);
            if (nameAttr && el.value !== undefined && el.value !== null && el.value !== '') {
                candidateKeys.push(`${nameAttr}_val_${el.value}`, cleanKey(`${nameAttr}_val_${el.value}`));
                candidateKeys.push(`${nameAttr}_${el.value}`, cleanKey(`${nameAttr}_${el.value}`));
            }

            let parentCw = el.closest ? el.closest('cmwf-checkbox, cmwf-textbox, cmwf-text-display, cmwf-cop, cmwf-integrated-action, cmwf-dropdown, ntst-select') : null;
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

            const fallbackKey = `${prefix}pos_${index}_${tag}_${type}`;
            candidateKeys.push(fallbackKey);

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

            try {
                if (type === 'checkbox' || tag === 'cmwf-checkbox') {
                    if (matchedItem.checked) {
                        const inputEl = tag === 'cmwf-checkbox' ? el.querySelector('input[type="checkbox"]') : el;
                        const outerCw = tag === 'cmwf-checkbox' ? el : (parentCw && parentCw.tagName.toLowerCase() === 'cmwf-checkbox' ? parentCw : (el.closest ? el.closest('cmwf-checkbox') : null));

                        if (inputEl) {
                            inputEl.checked = true;
                            if (inputEl.setAttribute) inputEl.setAttribute('checked', 'checked');
                            triggerEvents(inputEl, doc);
                        }
                        if (outerCw) {
                            outerCw.checked = true;
                            if (outerCw.setAttribute) outerCw.setAttribute('checked', 'checked');
                            if (outerCw.classList) outerCw.classList.add('checked');
                            triggerEvents(outerCw, doc);
                        }
                        if (el !== inputEl && el !== outerCw) {
                            el.checked = true;
                            if (el.setAttribute) el.setAttribute('checked', 'checked');
                            triggerEvents(el, doc);
                        }

                        syncAngularScope(el, doc, true, candidateKeys);
                        if (inputEl) syncAngularScope(inputEl, doc, true, candidateKeys);
                        if (outerCw) syncAngularScope(outerCw, doc, true, candidateKeys);

                        stats.updated++;
                    }
                }
            } catch(err) {}
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

def test_checkbox_group_m0150_copying():
    """
    Test copying M0150 payment source checkboxes where all options share name="m0150",
    and Option 1 (Medicare) is checked in source note while Option 0 and Option 2..11 are unchecked.
    """
    html_source = """
    <!DOCTYPE html>
    <html>
    <body>
        <form id="sourceForm">
            <div id="m0150_container">
                <input type="checkbox" name="m0150" id="m0150_0" value="0" ng-model="formData.m0150_0"> None
                <input type="checkbox" name="m0150" id="m0150_1" value="1" ng-model="formData.m0150_1" checked="checked"> Medicare
                <input type="checkbox" name="m0150" id="m0150_2" value="2" ng-model="formData.m0150_2"> Medicare HMO
                <input type="checkbox" name="m0150" id="m0150_uk" value="uk" ng-model="formData.m0150_uk"> Unknown
            </div>
        </form>
    </body>
    </html>
    """

    html_target = """
    <!DOCTYPE html>
    <html>
    <body>
        <form id="targetForm">
            <div id="m0150_container">
                <input type="checkbox" name="m0150" id="m0150_0" value="0" ng-model="formData.m0150_0"> None
                <input type="checkbox" name="m0150" id="m0150_1" value="1" ng-model="formData.m0150_1"> Medicare
                <input type="checkbox" name="m0150" id="m0150_2" value="2" ng-model="formData.m0150_2"> Medicare HMO
                <input type="checkbox" name="m0150" id="m0150_uk" value="uk" ng-model="formData.m0150_uk"> Unknown
            </div>
        </form>
    </body>
    </html>
    """

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        # Source page extraction
        src_page = browser.new_page()
        src_page.set_content(html_source)

        extracted_fields = src_page.evaluate(JS_EXTRACT_FIELDS)
        assert extracted_fields is not None
        assert "m0150_1" in extracted_fields
        assert extracted_fields["m0150_1"]["checked"] is True
        assert extracted_fields["m0150_0"]["checked"] is False

        # Target page injection
        tgt_page = browser.new_page()
        tgt_page.set_content(html_target)

        stats = tgt_page.evaluate(JS_INJECT_FIELDS, extracted_fields)

        # Check target state
        m0_checked = tgt_page.evaluate("() => document.getElementById('m0150_0').checked")
        m1_checked = tgt_page.evaluate("() => document.getElementById('m0150_1').checked")
        m2_checked = tgt_page.evaluate("() => document.getElementById('m0150_2').checked")
        muk_checked = tgt_page.evaluate("() => document.getElementById('m0150_uk').checked")

        browser.close()

        assert m0_checked is False, "Option 0 should NOT be checked"
        assert m1_checked is True, "Option 1 (Medicare) MUST be checked!"
        assert m2_checked is False, "Option 2 should NOT be checked"
        assert muk_checked is False, "Option UK should NOT be checked"
        assert stats["updated"] == 1
        print("[SUCCESS] Checkbox group M0150 extraction & injection test passed!")

def test_user_m0150_cmwf_checkbox():
    import importlib.util
    spec = importlib.util.spec_from_file_location("devero_copier_roc", "devero_copier_oasis.py")
    roc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(roc)
    JS_EXTRACT_FIELDS, JS_INJECT_FIELDS = roc.JS_EXTRACT_FIELDS, roc.JS_INJECT_FIELDS

    html_source = """
    <!DOCTYPE html>
    <html>
    <body>
        <cmwf-checkbox class="col-12 pl-4 cmwf-checkbox cmwf-field hydrated" id="M0150_CPAY_MCARE_FFS" style="color: rgb(0, 0, 0); background-color: rgb(255, 255, 255);">
            <div class="checkbox-div background-color" style="color: rgb(0, 0, 0); background-color: rgb(255, 255, 255);">
                <input id="M0150_CPAY_MCARE_FFS_key" type="checkbox" name="M0150_CPAY_MCARE_FFS" disabled="" checked="" value="on" style="color: rgb(0, 0, 0); background-color: rgb(255, 255, 255);">
                <label class="checkbox_label color" for="M0150_CPAY_MCARE_FFS_key" style="color: rgb(0, 0, 0); background-color: rgb(255, 255, 255);">&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;1 &nbsp;&nbsp;&nbsp;- &nbsp;Medicare (traditional fee-for-service)</label>
            </div>
        </cmwf-checkbox>
    </body>
    </html>
    """

    html_target = """
    <!DOCTYPE html>
    <html>
    <body>
        <cmwf-checkbox class="col-12 pl-4 cmwf-checkbox cmwf-field hydrated" id="M0150_CPAY_MCARE_FFS">
            <div class="checkbox-div background-color">
                <input id="M0150_CPAY_MCARE_FFS_key" type="checkbox" name="M0150_CPAY_MCARE_FFS" value="on">
                <label class="checkbox_label color" for="M0150_CPAY_MCARE_FFS_key">&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;1 &nbsp;&nbsp;&nbsp;- &nbsp;Medicare (traditional fee-for-service)</label>
            </div>
        </cmwf-checkbox>
    </body>
    </html>
    """

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        src_page = browser.new_page()
        src_page.set_content(html_source)

        extracted = src_page.evaluate(JS_EXTRACT_FIELDS)
        assert extracted is not None
        assert "M0150_CPAY_MCARE_FFS" in extracted
        assert extracted["M0150_CPAY_MCARE_FFS"]["checked"] is True

        tgt_page = browser.new_page()
        tgt_page.set_content(html_target)

        stats = tgt_page.evaluate(JS_INJECT_FIELDS, extracted)

        is_checked = tgt_page.evaluate("() => document.getElementById('M0150_CPAY_MCARE_FFS_key').checked")
        browser.close()

        assert is_checked is True, "Target M0150_CPAY_MCARE_FFS_key MUST be checked!"
        print("[SUCCESS] Devero cmwf-checkbox M0150_CPAY_MCARE_FFS test passed!")

def test_full_m0150_group_with_cmwf_checkboxes():
    import importlib.util
    spec = importlib.util.spec_from_file_location("devero_copier_roc", "devero_copier_oasis.py")
    roc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(roc)
    JS_EXTRACT_FIELDS, JS_INJECT_FIELDS = roc.JS_EXTRACT_FIELDS, roc.JS_INJECT_FIELDS

    html_source = """
    <!DOCTYPE html>
    <html>
    <body>
        <cmwf-checkbox id="M0150_CPAY_NONE"><div class="checkbox-div"><input id="M0150_CPAY_NONE_key" type="checkbox" name="M0150_CPAY" disabled="" value="0"></div></cmwf-checkbox>
        <cmwf-checkbox id="M0150_CPAY_MCARE_FFS"><div class="checkbox-div"><input id="M0150_CPAY_MCARE_FFS_key" type="checkbox" name="M0150_CPAY" disabled="" checked="" value="1"></div></cmwf-checkbox>
        <cmwf-checkbox id="M0150_CPAY_MCARE_HMO"><div class="checkbox-div"><input id="M0150_CPAY_MCARE_HMO_key" type="checkbox" name="M0150_CPAY" disabled="" value="2"></div></cmwf-checkbox>
        <cmwf-checkbox id="M0150_CPAY_UK"><div class="checkbox-div"><input id="M0150_CPAY_UK_key" type="checkbox" name="M0150_CPAY" disabled="" value="uk"></div></cmwf-checkbox>
    </body>
    </html>
    """

    html_target = """
    <!DOCTYPE html>
    <html>
    <body>
        <cmwf-checkbox id="M0150_CPAY_NONE"><div class="checkbox-div"><input id="M0150_CPAY_NONE_key" type="checkbox" name="M0150_CPAY" value="0"></div></cmwf-checkbox>
        <cmwf-checkbox id="M0150_CPAY_MCARE_FFS"><div class="checkbox-div"><input id="M0150_CPAY_MCARE_FFS_key" type="checkbox" name="M0150_CPAY" value="1"></div></cmwf-checkbox>
        <cmwf-checkbox id="M0150_CPAY_MCARE_HMO"><div class="checkbox-div"><input id="M0150_CPAY_MCARE_HMO_key" type="checkbox" name="M0150_CPAY" value="2"></div></cmwf-checkbox>
        <cmwf-checkbox id="M0150_CPAY_UK"><div class="checkbox-div"><input id="M0150_CPAY_UK_key" type="checkbox" name="M0150_CPAY" value="uk"></div></cmwf-checkbox>
    </body>
    </html>
    """

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        src_page = browser.new_page()
        src_page.set_content(html_source)

        extracted = src_page.evaluate(JS_EXTRACT_FIELDS)
        assert extracted is not None
        assert "M0150_CPAY_MCARE_FFS" in extracted
        assert extracted["M0150_CPAY_MCARE_FFS"]["checked"] is True

        tgt_page = browser.new_page()
        tgt_page.set_content(html_target)

        stats = tgt_page.evaluate(JS_INJECT_FIELDS, extracted)

        c0 = tgt_page.evaluate("() => document.getElementById('M0150_CPAY_NONE_key').checked")
        c1 = tgt_page.evaluate("() => document.getElementById('M0150_CPAY_MCARE_FFS_key').checked")
        c2 = tgt_page.evaluate("() => document.getElementById('M0150_CPAY_MCARE_HMO_key').checked")
        cuk = tgt_page.evaluate("() => document.getElementById('M0150_CPAY_UK_key').checked")

        browser.close()

        assert c0 is False
        assert c1 is True, "Option 1 (Medicare FFS) MUST be checked on target!"
        assert c2 is False
        assert cuk is False
        print("[SUCCESS] Full M0150 cmwf-checkbox group copy test passed!")

if __name__ == "__main__":
    test_checkbox_group_m0150_copying()
    test_user_m0150_cmwf_checkbox()
    test_full_m0150_group_with_cmwf_checkboxes()


