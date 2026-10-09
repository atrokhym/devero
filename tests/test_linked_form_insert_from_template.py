"""
Unit test for generic 'Insert From Template' workflow across Devero linked forms.
Tests that when a pending form opens a linked section modal with an empty table,
it clicks 'Insert From Template', expands accordions, selects source-matching items,
and inserts them into the target form.
"""
from playwright.sync_api import sync_playwright
import importlib.util

spec = importlib.util.spec_from_file_location("copier", "devero_copier_oasis.py")
copier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(copier)


def test_multiselect_checkbox_layer_lists_all_categories():
    """Live Devero uses a custom layer, not `.dropdown-menu a`."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content("""
        <html>
        <body>
            <div class="btn-group open">
                <div class="multiselect dropdown-toggle btn"
                     ng-click="toggleDropdown( $event );">(No Category)</div>
                <div class="checkboxLayer">
                    <div class="multiSelectItem"><span>(No Category)</span></div>
                    <div class="multiSelectItem"><span>Triage/Risk Code</span></div>
                    <div class="multiSelectItem"><span>Evacuation</span></div>
                    <div class="multiSelectItem"><span>SAFETY MEASURES:</span></div>
                    <div class="multiSelectItem"><span>PATIENT PERSONAL GOALS</span></div>
                </div>
            </div>
        </body>
        </html>
        """)
        listed = copier._list_open_category_options(page, include_placeholders=True)
        browser.close()
    assert "Triage/Risk Code" in listed, listed
    assert "SAFETY MEASURES:" in listed, listed
    assert "PATIENT PERSONAL GOALS" in listed, listed


def test_toggle_showing_no_category_still_lists_sibling_menu_rows():
    """After open, the toggle itself reads '(No Category)'.

    If we treat that toggle as the list, firstLine of the sibling <ul>
    is only '(No Category)' and Safety Measures is never seen.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content("""
        <html>
        <body>
            <div class="btn-group">
                <div class="multiselect dropdown-toggle btn btn-default"
                     ng-click="toggleDropdown( $event );">(No Category)</div>
                <ul class="dropdown-menu" style="display:block">
                    <li><a href="javascript:void(0)">(No Category)</a></li>
                    <li><a href="javascript:void(0)">Triage/Risk Code</a></li>
                    <li><a href="javascript:void(0)">Evacuation</a></li>
                    <li><a href="javascript:void(0)">SAFETY MEASURES:</a></li>
                    <li><a href="javascript:void(0)">PATIENT PERSONAL GOALS</a></li>
                </ul>
            </div>
        </body>
        </html>
        """)
        listed = copier._list_open_category_options(page, include_placeholders=True)
        browser.close()
    assert "Triage/Risk Code" in listed, listed
    assert "SAFETY MEASURES:" in listed, listed
    assert "PATIENT PERSONAL GOALS" in listed, listed


def test_nested_category_list_reads_rows_below_no_category():
    """Live menu wraps items in an inner <ul>. firstLine(ul) is '(No Category)'."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content("""
        <html>
        <body>
            <div class="dropdown-menu" style="display:block">
                <input placeholder="Search">
                <ul>
                    <li><a href="javascript:void(0)">(No Category)</a></li>
                    <li><a href="javascript:void(0)">Triage/Risk Code</a></li>
                    <li><a href="javascript:void(0)">Evacuation</a></li>
                    <li><a href="javascript:void(0)">SAFETY MEASURES:</a></li>
                    <li><a href="javascript:void(0)">PATIENT PERSONAL GOALS</a></li>
                </ul>
            </div>
        </body>
        </html>
        """)
        listed = copier._list_open_category_options(page, include_placeholders=True)
        browser.close()
    assert "(No Category)" in listed or "No Category" in listed
    assert "Triage/Risk Code" in listed
    assert "Evacuation" in listed
    assert "SAFETY MEASURES:" in listed
    assert "PATIENT PERSONAL GOALS" in listed
    selectable = [n for n in listed if not copier._is_category_placeholder(n)]
    assert selectable == [
        "Triage/Risk Code",
        "Evacuation",
        "SAFETY MEASURES:",
        "PATIENT PERSONAL GOALS",
    ]
    assert copier._matching_template_categories(
        selectable, "safety_measures", "Safety Measures", "clear pathways"
    ) == ["SAFETY MEASURES:"]


def test_empty_option_text_does_not_match_category():
    assert copier._option_text_matches("", "SAFETY MEASURES:") is False
    assert copier._option_text_matches("   ", "SAFETY MEASURES:") is False
    assert copier._option_text_matches("No Category", "SAFETY MEASURES:") is False
    assert copier._option_text_matches("SAFETY MEASURES:", "SAFETY MEASURES:") is True
    assert copier._option_text_matches("SAFETY MEASURES", "SAFETY MEASURES:") is True


def test_empty_dropdown_row_is_not_clicked_as_category():
    """Live list often has a blank first <a> above the real names.

    Clicking that empty row looks like success and selects 0 items.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content("""
        <html>
        <head>
            <style>
                .popup-modal { display: none; }
                .popup-modal.in { display: block; }
                .dropdown-menu { display: none; }
                .dropdown-menu.open { display: block; }
            </style>
        </head>
        <body>
            <cmwf-cop id="safety_measures">
                <a class="cop-title" data-section="safety_measures" href="javascript:void(0)" onclick="openModal()">Safety Measures</a>
                <div class="cop-items"></div>
            </cmwf-cop>
            <div id="formFramePopup" class="popup-modal">
                <div id="initialView">
                    <button class="btn" onclick="openTemplateView()">Insert From Template</button>
                    <table class="grid-table"><tbody>
                        <tr class="item-row"><td><input type="checkbox" /></td><td></td></tr>
                    </tbody></table>
                    <button class="btn btn-primary" onclick="commitToForm()">Insert to form</button>
                    <button class="btn" onclick="closeModal()">Cancel</button>
                </div>
                <div id="templateView" style="display:none;">
                    <div class="multiselect dropdown-toggle btn" id="catToggle"
                         ng-click="toggleDropdown( $event );"
                         onclick="toggleMenu(event)">(Select a category)</div>
                    <ul id="catMenu" class="dropdown-menu">
                        <li role="option"><a href="javascript:void(0)" onclick="pick(event, '')"></a></li>
                        <li role="option"><a href="javascript:void(0)" onclick="pick(event, 'No Category')">No Category</a></li>
                        <li role="option"><a href="javascript:void(0)" onclick="pick(event, 'SAFETY MEASURES:')">SAFETY MEASURES:</a></li>
                    </ul>
                    <p id="pleaseSelect">Please select a category</p>
                    <div id="categoryTableContainer"></div>
                    <button class="btn btn-success" onclick="insertTemplateItems()">Insert Template To SAFETY MEASURES</button>
                </div>
            </div>
            <script>
                window.__picked = [];
                function openModal() { document.getElementById('formFramePopup').classList.add('in'); }
                function closeModal() { document.getElementById('formFramePopup').classList.remove('in'); }
                function openTemplateView() {
                    document.getElementById('initialView').style.display = 'none';
                    document.getElementById('templateView').style.display = 'block';
                }
                function toggleMenu(event) {
                    if (!event || event.isTrusted !== true) return;
                    document.getElementById('catMenu').classList.toggle('open');
                }
                function pick(event, name) {
                    if (!event || event.isTrusted !== true) return;
                    window.__picked.push(name);
                    document.getElementById('catToggle').textContent = name || '(Select a category)';
                    document.getElementById('catMenu').classList.remove('open');
                    if (name === 'SAFETY MEASURES:') {
                        document.getElementById('pleaseSelect').style.display = 'none';
                        document.getElementById('categoryTableContainer').innerHTML =
                            '<table class="template-table"><tbody>' +
                            '<tr><td><input type="checkbox" class="template-cb" /></td><td>24 hr supervision</td></tr>' +
                            '<tr><td><input type="checkbox" class="template-cb" /></td><td>Clear pathways</td></tr>' +
                            '</tbody></table>';
                    }
                }
                function insertTemplateItems() {
                    const rows = [];
                    document.querySelectorAll('.template-cb:checked').forEach(cb => {
                        rows.push(cb.closest('tr').querySelectorAll('td')[1].innerText);
                    });
                    document.getElementById('templateView').style.display = 'none';
                    document.getElementById('initialView').style.display = 'block';
                    document.querySelector('.grid-table tbody').innerHTML = rows.map(t =>
                        '<tr class="item-row"><td><input type="checkbox" checked /></td><td>' + t + '</td></tr>'
                    ).join('');
                    window.__rows = rows;
                }
                function commitToForm() {
                    document.querySelector('#safety_measures .cop-items').innerHTML =
                        (window.__rows || []).map(s => '<div class="copElement">' + s + '</div>').join('');
                    closeModal();
                }
            </script>
        </body>
        </html>
        """)
        copier.inject_composite_linked_forms(page, {
            "cop_section_safety_measures": {
                "kind": "composite",
                "section": "safety_measures",
                "title": "Safety Measures",
                "items": ["24 hr supervision", "Clear pathways"],
                "value": "24 hr supervision Clear pathways",
            }
        })
        picked = page.evaluate("() => window.__picked")
        result = page.evaluate("""() => {
            const el = document.querySelector('#safety_measures .cop-items');
            return el ? el.innerText : '';
        }""")
        assert picked == ["SAFETY MEASURES:"], picked
        assert "24 hr supervision" in result
        browser.close()


def test_no_category_list_clicks_item_below_placeholder():
    """Live picker opens with 'No Category' first, then real names.

    Items are labels in a multiselect container, not `.dropdown-menu a`.
    """
    options = [
        "No Category",
        "Triage/Risk Code",
        "Evacuation",
        "SAFETY MEASURES:",
        "PATIENT PERSONAL GOALS",
    ]
    assert copier._matching_template_categories(
        [o for o in options if o.lower() != "no category"],
        "safety_measures",
        "Safety Measures",
        "Triage/Risk Code Evacuation SAFETY MEASURES: 24 hr supervision PATIENT PERSONAL GOALS",
    ) == ["SAFETY MEASURES:"]


def test_matching_prefers_section_named_category():
    """A Safety Measures popup lists every agency category.

    Source notes often mention Triage/Evacuation/Goals too. Only the
    section-named category should be processed.
    """
    options = [
        "Triage/Risk Code",
        "Evacuation",
        "SAFETY MEASURES:",
        "PATIENT PERSONAL GOALS",
    ]
    source = (
        "Triage/Risk Code Evacuation SAFETY MEASURES: 24 hr supervision "
        "PATIENT PERSONAL GOALS"
    )
    matched = copier._matching_template_categories(
        options, "safety_measures", "Safety Measures", source
    )
    assert matched == ["SAFETY MEASURES:"]


def test_advance_directives_insert_from_template():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        # Set up a mock Devero target page with Advance Directives
        page.set_content("""
        <html>
        <head>
            <style>
                .popup-modal { display: none; }
                .popup-modal.in { display: block; }
                .accordion-body { display: none; }
                .accordion-body.open { display: block; }
            </style>
        </head>
        <body>
            <!-- Target Note Form with empty Advance Directives -->
            <div class="page-container">
                <h3>Page 4</h3>
                <cmwf-cop id="advanced_directives">
                    <a class="cop-title" data-section="advanced_directives" href="javascript:void(0)" onclick="openModal()">Advance Directives</a>
                    <div class="cop-items"></div>
                </cmwf-cop>
            </div>

            <!-- Modal #formFramePopup -->
            <div id="formFramePopup" class="popup-modal">
                <div class="modal-content">
                    <div class="modal-header">
                        <h4>ADVANCE DIRECTIVES</h4>
                        <button class="close" onclick="closeModal()">×</button>
                    </div>
                    <div class="modal-body" id="modalView">
                        <!-- Initial Table View (Empty items) -->
                        <div id="initialView">
                            <button id="btnInsertTemplate" class="btn" onclick="openTemplateView()">Insert From Template</button>
                            <table class="grid-table">
                                <thead>
                                    <tr><th></th><th>ADVANCE DIRECTIVES</th><th>Start Effective Date</th></tr>
                                </thead>
                                <tbody>
                                    <tr class="item-row">
                                        <td><input type="checkbox" id="emptyRowCb" /></td>
                                        <td></td>
                                        <td>08/18/2026</td>
                                    </tr>
                                </tbody>
                            </table>
                            <button id="btnInsertToFormMain" class="btn btn-primary" onclick="commitToForm()">Insert to form</button>
                            <button class="btn" onclick="closeModal()">Cancel</button>
                        </div>

                        <!-- Template Chooser View (Opens when clicking Insert From Template) -->
                        <div id="templateView" style="display:none;">
                            <div class="panel">
                                <div class="accordion-toggle panel-heading" onclick="toggleAccordion('acc1')">
                                    <i class="fa-chevron-right" id="accIcon1"></i> Advance Directives Templates
                                </div>
                                <div class="accordion-body" id="acc1">
                                    <div class="item-row">
                                        <label><input type="checkbox" id="cbFullCode" value="full_code" /> Full code</label>
                                    </div>
                                    <div class="item-row">
                                        <label><input type="checkbox" id="cbProxy" value="proxy" /> Health care Proxy</label>
                                    </div>
                                    <div class="item-row">
                                        <label><input type="checkbox" id="cbDnr" value="dnr" /> Do Not Resuscitate (DNR)</label>
                                    </div>
                                    <div class="item-row">
                                        <label><input type="checkbox" id="cbLivingWill" value="living_will" /> Living Will</label>
                                    </div>
                                </div>
                            </div>
                            <button id="btnConfirmTemplate" class="btn btn-success" onclick="insertTemplateItems()">Insert to form</button>
                        </div>
                    </div>
                </div>
            </div>

            <script>
                function openModal() {
                    document.getElementById('formFramePopup').classList.add('in');
                }
                function closeModal() {
                    document.getElementById('formFramePopup').classList.remove('in');
                }
                function openTemplateView() {
                    document.getElementById('initialView').style.display = 'none';
                    document.getElementById('templateView').style.display = 'block';
                }
                function toggleAccordion(id) {
                    const acc = document.getElementById(id);
                    acc.classList.toggle('open');
                    const icon = document.getElementById('accIcon1');
                    if (acc.classList.contains('open')) {
                        icon.className = 'fa-chevron-down';
                    } else {
                        icon.className = 'fa-chevron-right';
                    }
                }
                function insertTemplateItems() {
                    const items = [];
                    if (document.getElementById('cbFullCode').checked) items.push('Full code Start Effective Date: 08/18/2026');
                    if (document.getElementById('cbProxy').checked) items.push('Health care Proxy Start Effective Date: 08/18/2026');
                    if (document.getElementById('cbDnr').checked) items.push('Do Not Resuscitate (DNR) Start Effective Date: 08/18/2026');
                    if (document.getElementById('cbLivingWill').checked) items.push('Living Will Start Effective Date: 08/18/2026');

                    // Switch back to initial view with rows populated
                    document.getElementById('templateView').style.display = 'none';
                    document.getElementById('initialView').style.display = 'block';
                    
                    const tbody = document.querySelector('.grid-table tbody');
                    tbody.innerHTML = '';
                    items.forEach((itemText, i) => {
                        const tr = document.createElement('tr');
                        tr.className = 'item-row';
                        tr.innerHTML = '<td><input type="checkbox" checked id="rowCb' + i + '" /></td><td>' + itemText + '</td><td>08/18/2026</td>';
                        tbody.appendChild(tr);
                    });
                }
                function commitToForm() {
                    const rows = document.querySelectorAll('.grid-table tbody tr');
                    const selected = [];
                    rows.forEach(r => {
                        const cb = r.querySelector('input[type="checkbox"]');
                        if (cb && cb.checked) {
                            selected.push(r.innerText.trim());
                        }
                    });
                    const copItems = document.querySelector('#advanced_directives .cop-items');
                    copItems.innerHTML = selected.map(s => '<div class="copElement">' + s + '</div>').join('');
                    closeModal();
                }
            </script>
        </body>
        </html>
        """)

        # Mock extracted source fields
        extracted_fields = {
            "cop_section_advanced_directives": {
                "kind": "composite",
                "section": "advanced_directives",
                "title": "Advance Directives",
                "items": [
                    "Full code Start Effective Date: 09/25/2025",
                    "Health care Proxy Start Effective Date: 09/25/2025"
                ],
                "value": "Full code Start Effective Date: 09/25/2025\n\nHealth care Proxy Start Effective Date: 09/25/2025"
            }
        }

        # Run inject_composite_linked_forms
        copier.inject_composite_linked_forms(page, extracted_fields)

        # Verify that Advance Directives was populated on the target form
        result_text = page.evaluate("""() => {
            const cop = document.getElementById('advanced_directives');
            return cop ? cop.innerText : '';
        }""")

        assert "Full code" in result_text
        assert "Health care Proxy" in result_text
        assert "Do Not Resuscitate" not in result_text
        print("Advance Directives test passed!")

        browser.close()


def test_advance_directives_concatenated_source_blob():
    """Production source notes store AD items as one line; still select those template rows."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        page.set_content("""
        <html>
        <head>
            <style>
                .popup-modal { display: none; }
                .popup-modal.in { display: block; }
                .accordion-body { display: none; }
                .accordion-body.open { display: block; }
            </style>
        </head>
        <body>
            <div class="page-container">
                <h3>Page 4</h3>
                <cmwf-cop id="advanced_directives">
                    <a class="cop-title" data-section="advanced_directives" href="javascript:void(0)" onclick="openModal()">Advance Directives</a>
                    <div class="cop-items"></div>
                </cmwf-cop>
            </div>

            <div id="formFramePopup" class="popup-modal">
                <div class="modal-content">
                    <div class="modal-header">
                        <h4>ADVANCE DIRECTIVES</h4>
                        <button class="close" onclick="closeModal()">×</button>
                    </div>
                    <div class="modal-body" id="modalView">
                        <div id="initialView">
                            <button id="btnInsertTemplate" class="btn" onclick="openTemplateView()">Insert From Template</button>
                            <table class="grid-table">
                                <thead>
                                    <tr><th></th><th>ADVANCE DIRECTIVES</th><th>Start Effective Date</th></tr>
                                </thead>
                                <tbody>
                                    <tr class="item-row">
                                        <td><input type="checkbox" id="emptyRowCb" /></td>
                                        <td></td>
                                        <td>08/18/2026</td>
                                    </tr>
                                </tbody>
                            </table>
                            <button id="btnInsertToFormMain" class="btn btn-primary" onclick="commitToForm()">Insert to form</button>
                            <button class="btn" onclick="closeModal()">Cancel</button>
                        </div>

                        <div id="templateView" style="display:none;">
                            <div class="panel">
                                <div class="accordion-toggle panel-heading" onclick="toggleAccordion('acc1')">
                                    <i class="fa-chevron-right" id="accIcon1"></i> Advance Directives Templates
                                </div>
                                <div class="accordion-body" id="acc1">
                                    <div class="item-row">
                                        <label><input type="checkbox" id="cbFullCode" value="full_code" /> Full code</label>
                                    </div>
                                    <div class="item-row">
                                        <label><input type="checkbox" id="cbProxy" value="proxy" /> Health care Proxy</label>
                                    </div>
                                    <div class="item-row">
                                        <label><input type="checkbox" id="cbDnr" value="dnr" /> Do Not Resuscitate (DNR)</label>
                                    </div>
                                    <div class="item-row">
                                        <label><input type="checkbox" id="cbLivingWill" value="living_will" /> Living Will</label>
                                    </div>
                                </div>
                            </div>
                            <button id="btnConfirmTemplate" class="btn btn-success" onclick="insertTemplateItems()">Insert to form</button>
                        </div>
                    </div>
                </div>
            </div>

            <script>
                function openModal() {
                    document.getElementById('formFramePopup').classList.add('in');
                }
                function closeModal() {
                    document.getElementById('formFramePopup').classList.remove('in');
                }
                function openTemplateView() {
                    document.getElementById('initialView').style.display = 'none';
                    document.getElementById('templateView').style.display = 'block';
                }
                function toggleAccordion(id) {
                    const acc = document.getElementById(id);
                    acc.classList.toggle('open');
                    const icon = document.getElementById('accIcon1');
                    if (acc.classList.contains('open')) {
                        icon.className = 'fa-chevron-down';
                    } else {
                        icon.className = 'fa-chevron-right';
                    }
                }
                function insertTemplateItems() {
                    const items = [];
                    if (document.getElementById('cbFullCode').checked) items.push('Full code Start Effective Date: 08/18/2026');
                    if (document.getElementById('cbProxy').checked) items.push('Health care Proxy Start Effective Date: 08/18/2026');
                    if (document.getElementById('cbDnr').checked) items.push('Do Not Resuscitate (DNR) Start Effective Date: 08/18/2026');
                    if (document.getElementById('cbLivingWill').checked) items.push('Living Will Start Effective Date: 08/18/2026');

                    document.getElementById('templateView').style.display = 'none';
                    document.getElementById('initialView').style.display = 'block';

                    const tbody = document.querySelector('.grid-table tbody');
                    tbody.innerHTML = '';
                    items.forEach((itemText, i) => {
                        const tr = document.createElement('tr');
                        tr.className = 'item-row';
                        tr.innerHTML = '<td><input type="checkbox" checked id="rowCb' + i + '" /></td><td>' + itemText + '</td><td>08/18/2026</td>';
                        tbody.appendChild(tr);
                    });
                }
                function commitToForm() {
                    const rows = document.querySelectorAll('.grid-table tbody tr');
                    const selected = [];
                    rows.forEach(r => {
                        const cb = r.querySelector('input[type="checkbox"]');
                        if (cb && cb.checked) {
                            selected.push(r.innerText.trim());
                        }
                    });
                    const copItems = document.querySelector('#advanced_directives .cop-items');
                    copItems.innerHTML = selected.map(s => '<div class="copElement">' + s + '</div>').join('');
                    closeModal();
                }
            </script>
        </body>
        </html>
        """)

        # Matches live extraction: one concatenated item, no newlines.
        extracted_fields = {
            "cop_section_advanced_directives": {
                "kind": "composite",
                "section": "advanced_directives",
                "title": "Advance Directives\nClear",
                "items": [
                    "ADVANCE DIRECTIVES: Full code Start Effective Date: 09/25/2025 "
                    "Health care Proxy Start Effective Date: 09/25/2025"
                ],
                "value": (
                    "ADVANCE DIRECTIVES: Full code Start Effective Date: 09/25/2025 "
                    "Health care Proxy Start Effective Date: 09/25/2025"
                ),
            }
        }

        copier.inject_composite_linked_forms(page, extracted_fields)

        result_text = page.evaluate("""() => {
            const cop = document.getElementById('advanced_directives');
            return cop ? cop.innerText : '';
        }""")

        assert "Full code" in result_text
        assert "Health care Proxy" in result_text
        assert "Do Not Resuscitate" not in result_text
        assert "Living Will" not in result_text

        browser.close()


def test_chart_placeholder_select_does_not_block_template_items():
    """A '(Select a chart)' dropdown is not a template category.

    Live Devero shows that control in the Insert From Template view.
    Treating it as a category leaves the linked form empty.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content("""
        <html>
        <head>
            <style>
                .popup-modal { display: none; }
                .popup-modal.in { display: block; }
                .accordion-body { display: none; }
                .accordion-body.open { display: block; }
            </style>
        </head>
        <body>
            <select id="pageChartSelect">
                <option>(Select a chart)</option>
                <option>FixtureFive, Patient</option>
            </select>
            <cmwf-cop id="advanced_directives">
                <a class="cop-title" data-section="advanced_directives" href="javascript:void(0)" onclick="openModal()">Advance Directives Clear</a>
                <div class="cop-items"></div>
            </cmwf-cop>
            <cmwf-cop id="safety_measures">
                <a class="cop-title" data-section="safety_measures" href="javascript:void(0)" onclick="openModal()">Safety Measures Clear</a>
                <div class="cop-items"></div>
            </cmwf-cop>

            <div id="formFramePopup" class="popup-modal">
                <h4 id="modalTitle"></h4>
                <div id="initialView">
                    <button id="btnInsertTemplate" class="btn" onclick="openTemplateView()">Insert From Template</button>
                    <table class="grid-table"><tbody>
                        <tr class="item-row"><td><input type="checkbox" /></td><td></td></tr>
                    </tbody></table>
                    <button class="btn btn-primary" onclick="commitToForm()">Insert to form</button>
                    <button class="btn" onclick="closeModal()">Cancel</button>
                </div>
                <div id="templateView" style="display:none;">
                    <select id="chartSelect" ng-model="carePlanModuleController.selectedChartId" style="display:none">
                        <option>(Select a chart)</option>
                    </select>
                    <button type="button" onclick="void(0)">ADVANCE DIRECTIVES:</button>
                    <div class="accordion-toggle panel-heading" onclick="toggleAccordion()">Templates</div>
                    <div class="accordion-body" id="acc">
                        <label><input type="checkbox" id="cbFullCode" ng-model="row.isSelected" /> Full code
ADVANCE DIRECTIVES:</label>
                        <label><input type="checkbox" id="cbProxy" ng-model="row.isSelected" /> Health care Proxy
ADVANCE DIRECTIVES:</label>
                        <label><input type="checkbox" id="cbDnr" ng-model="row.isSelected" /> Do Not Resuscitate (DNR)
ADVANCE DIRECTIVES:</label>
                        <label><input type="checkbox" id="cb24hr" ng-model="row.isSelected" /> 24 hr supervision
SAFETY MEASURES:</label>
                        <label><input type="checkbox" id="cbClear" ng-model="row.isSelected" /> Clear pathways
SAFETY MEASURES:</label>
                    </div>
                    <button class="btn btn-success" onclick="insertTemplateItems()">Insert to form</button>
                    <button class="btn" onclick="closeModal()">Cancel</button>
                </div>
            </div>
            <script>
                let currentSection = 'advanced_directives';
                function openModal() {
                    const src = event.target.closest('cmwf-cop');
                    currentSection = src ? src.id : 'advanced_directives';
                    document.getElementById('modalTitle').innerText = currentSection;
                    document.getElementById('formFramePopup').classList.add('in');
                    document.getElementById('initialView').style.display = 'block';
                    document.getElementById('templateView').style.display = 'none';
                }
                function closeModal() {
                    document.getElementById('formFramePopup').classList.remove('in');
                }
                function openTemplateView() {
                    document.getElementById('initialView').style.display = 'none';
                    document.getElementById('templateView').style.display = 'block';
                }
                function toggleAccordion() {
                    document.getElementById('acc').classList.toggle('open');
                }
                function insertTemplateItems() {
                    const map = {
                        cbFullCode: 'Full code',
                        cbProxy: 'Health care Proxy',
                        cbDnr: 'Do Not Resuscitate (DNR)',
                        cb24hr: '24 hr supervision',
                        cbClear: 'Clear pathways'
                    };
                    const items = Object.keys(map).filter(id => document.getElementById(id).checked)
                        .map(id => map[id] + ' Start Effective Date: 08/18/2026');
                    document.getElementById('templateView').style.display = 'none';
                    document.getElementById('initialView').style.display = 'block';
                    const tbody = document.querySelector('.grid-table tbody');
                    tbody.innerHTML = items.map((t,i) =>
                        '<tr class="item-row"><td><input type="checkbox" checked /></td><td>' + t + '</td></tr>'
                    ).join('');
                }
                function commitToForm() {
                    const selected = [];
                    document.querySelectorAll('.grid-table tbody tr').forEach(r => {
                        const cb = r.querySelector('input[type="checkbox"]');
                        if (cb && cb.checked) selected.push(r.innerText.trim());
                    });
                    document.querySelector('#' + currentSection + ' .cop-items').innerHTML =
                        selected.map(s => '<div class="copElement">' + s + '</div>').join('');
                    closeModal();
                }
            </script>
        </body>
        </html>
        """)

        extracted_fields = {
            "cop_section_advanced_directives": {
                "kind": "composite",
                "section": "advanced_directives",
                "title": "Advance Directives\\nClear",
                "items": [
                    "ADVANCE DIRECTIVES: Full code Start Effective Date: 09/25/2025 "
                    "Health care Proxy Start Effective Date: 09/25/2025"
                ],
                "value": (
                    "ADVANCE DIRECTIVES: Full code Start Effective Date: 09/25/2025 "
                    "Health care Proxy Start Effective Date: 09/25/2025"
                ),
            },
            "cop_section_safety_measures": {
                "kind": "composite",
                "section": "safety_measures",
                "title": "Safety Measures\\nClear",
                "items": [
                    "24 hr supervision Start Effective Date: 09/25/2025",
                    "Clear pathways Start Effective Date: 09/25/2025",
                ],
                "value": (
                    "24 hr supervision Start Effective Date: 09/25/2025\n"
                    "Clear pathways Start Effective Date: 09/25/2025"
                ),
            },
        }

        copier.inject_composite_linked_forms(page, extracted_fields)

        ad_text = page.evaluate("() => document.querySelector('#advanced_directives .cop-items').innerText")
        sm_text = page.evaluate("() => document.querySelector('#safety_measures .cop-items').innerText")

        assert "Full code" in ad_text
        assert "Health care Proxy" in ad_text
        assert "Do Not Resuscitate" not in ad_text
        assert "24 hr supervision" in sm_text
        assert "Clear pathways" in sm_text

        browser.close()


def test_safety_measures_insert_from_template():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        # Set up a mock Devero target page with Safety Measures
        page.set_content("""
        <html>
        <head>
            <style>
                .popup-modal { display: none; }
                .popup-modal.in { display: block; }
                .accordion-body { display: none; }
                .accordion-body.open { display: block; }
            </style>
        </head>
        <body>
            <div class="page-container">
                <cmwf-cop id="safety_measures">
                    <a class="cop-title" data-section="safety_measures" href="javascript:void(0)" onclick="openModal()">Safety Measures</a>
                    <div class="cop-items"></div>
                </cmwf-cop>
            </div>

            <div id="formFramePopup" class="popup-modal">
                <div class="modal-content">
                    <div class="modal-header">
                        <h4>SAFETY MEASURES</h4>
                        <button class="close" onclick="closeModal()">×</button>
                    </div>
                    <div class="modal-body" id="modalView">
                        <div id="initialView">
                            <button id="btnInsertTemplate" class="btn" onclick="openTemplateView()">Insert from template</button>
                            <table class="grid-table">
                                <thead><tr><th></th><th>SAFETY MEASURES</th></tr></thead>
                                <tbody>
                                    <tr class="item-row"><td><input type="checkbox" /></td><td></td></tr>
                                </tbody>
                            </table>
                            <button id="btnInsertToFormMain" class="btn btn-primary" onclick="commitToForm()">Insert to form</button>
                        </div>

                        <div id="templateView" style="display:none;">
                            <div class="panel">
                                <div class="accordion-toggle panel-heading" onclick="toggleAccordion('acc2')">
                                    <i class="fa-chevron-right"></i> Safety Measures List
                                </div>
                                <div class="accordion-body" id="acc2">
                                    <div class="item-row">
                                        <label><input type="checkbox" id="cb24hr" /> 24 hr supervision</label>
                                    </div>
                                    <div class="item-row">
                                        <label><input type="checkbox" id="cbClear" /> Clear pathways</label>
                                    </div>
                                    <div class="item-row">
                                        <label><input type="checkbox" id="cbLock" /> Lock wheelchair</label>
                                    </div>
                                </div>
                            </div>
                            <button id="btnConfirmTemplate" class="btn btn-success" onclick="insertTemplateItems()">Insert to form</button>
                        </div>
                    </div>
                </div>
            </div>

            <script>
                function openModal() { document.getElementById('formFramePopup').classList.add('in'); }
                function closeModal() { document.getElementById('formFramePopup').classList.remove('in'); }
                function openTemplateView() {
                    document.getElementById('initialView').style.display = 'none';
                    document.getElementById('templateView').style.display = 'block';
                }
                function toggleAccordion(id) {
                    const acc = document.getElementById(id);
                    acc.classList.toggle('open');
                }
                function insertTemplateItems() {
                    const items = [];
                    if (document.getElementById('cb24hr').checked) items.push('24 hr supervision Start Effective Date: 08/18/2026');
                    if (document.getElementById('cbClear').checked) items.push('Clear pathways Start Effective Date: 08/18/2026');

                    document.getElementById('templateView').style.display = 'none';
                    document.getElementById('initialView').style.display = 'block';
                    
                    const tbody = document.querySelector('.grid-table tbody');
                    tbody.innerHTML = '';
                    items.forEach((itemText, i) => {
                        const tr = document.createElement('tr');
                        tr.className = 'item-row';
                        tr.innerHTML = '<td><input type="checkbox" checked /></td><td>' + itemText + '</td>';
                        tbody.appendChild(tr);
                    });
                }
                function commitToForm() {
                    const rows = document.querySelectorAll('.grid-table tbody tr');
                    const selected = [];
                    rows.forEach(r => {
                        const cb = r.querySelector('input[type="checkbox"]');
                        if (cb && cb.checked) selected.push(r.innerText.trim());
                    });
                    document.querySelector('#safety_measures .cop-items').innerHTML = selected.map(s => '<div class="copElement">' + s + '</div>').join('');
                    closeModal();
                }
            </script>
        </body>
        </html>
        """)

        extracted_fields = {
            "cop_section_safety_measures": {
                "kind": "composite",
                "section": "safety_measures",
                "title": "Safety Measures",
                "items": [
                    "24 hr supervision Start Effective Date: 09/25/2025",
                    "Clear pathways Start Effective Date: 09/25/2025"
                ],
                "value": "24 hr supervision Start Effective Date: 09/25/2025\n\nClear pathways Start Effective Date: 09/25/2025"
            }
        }

        copier.inject_composite_linked_forms(page, extracted_fields)

        result_text = page.evaluate("""() => {
            const cop = document.getElementById('safety_measures');
            return cop ? cop.innerText : '';
        }""")

        assert "24 hr supervision" in result_text
        assert "Clear pathways" in result_text
        assert "Lock wheelchair" not in result_text
        print("Safety Measures test passed!")

def test_empty_selection_clicks_cancel_and_leaves_empty():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        # Target note with empty linked section
        page.set_content("""
        <html>
        <head>
            <style>
                .popup-modal { display: none; }
                .popup-modal.in { display: block; }
            </style>
        </head>
        <body>
            <div class="page-container">
                <cmwf-cop id="mental_status">
                    <a class="cop-title" data-section="mental_status" href="javascript:void(0)" onclick="openModal()">Mental Status</a>
                    <div class="cop-items"></div>
                </cmwf-cop>
            </div>

            <div id="formFramePopup" class="popup-modal">
                <div class="modal-content">
                    <button class="close" onclick="closeModal()">×</button>
                    <h4>MENTAL STATUS</h4>
                    <div id="initialView">
                        <button id="btnInsertTemplate" class="btn" onclick="openTemplateView()">Insert from template</button>
                        <button class="btn" onclick="closeModal()">Cancel</button>
                    </div>
                    <div id="templateView" style="display:none;">
                        <label><input type="checkbox" id="cbComatose" /> Comatose</label>
                        <button class="btn" onclick="closeTemplate()">Cancel</button>
                    </div>
                </div>
            </div>

            <script>
                function openModal() { document.getElementById('formFramePopup').classList.add('in'); }
                function closeModal() { document.getElementById('formFramePopup').classList.remove('in'); }
                function openTemplateView() {
                    document.getElementById('initialView').style.display = 'none';
                    document.getElementById('templateView').style.display = 'block';
                }
                function closeTemplate() {
                    document.getElementById('templateView').style.display = 'none';
                    document.getElementById('initialView').style.display = 'block';
                }
            </script>
        </body>
        </html>
        """)

        # Source note has no matching items for Mental Status (e.g. source is Alert/Oriented, but template has Comatose)
        extracted_fields = {
            "cop_section_mental_status": {
                "kind": "composite",
                "section": "mental_status",
                "title": "Mental Status",
                "items": ["Alert and oriented x4"],
                "value": "Alert and oriented x4"
            }
        }

        copier.inject_composite_linked_forms(page, extracted_fields)

        # Verify modal was closed via Cancel and form is left empty
        is_modal_open = page.evaluate("() => document.getElementById('formFramePopup').classList.contains('in')")
        assert not is_modal_open, "Modal should be closed after Cancel"

        cop_items_text = page.evaluate("() => document.querySelector('#mental_status .cop-items').innerText.trim()")
        assert cop_items_text == "", "Form should be left empty when nothing to select"
        print("Empty selection cancel test passed!")

def test_eligibility_categorized_template_insert():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        page.set_content("""
        <html>
        <head>
            <style>
                .popup-modal { display: none; }
                .popup-modal.in { display: block; }
            </style>
        </head>
        <body>
            <div class="page-container">
                <cmwf-cop id="eligibility">
                    <a class="cop-title" data-section="eligibility" href="javascript:void(0)" onclick="openModal()">Eligibility</a>
                    <div class="cop-items"></div>
                </cmwf-cop>
            </div>

            <div id="formFramePopup" class="popup-modal">
                <div class="modal-content">
                    <button class="close" onclick="closeModal()">×</button>
                    <h4>ELIGIBILITY</h4>
                    <div id="mainView">
                        <table id="mainTable">
                            <tbody></tbody>
                        </table>
                        <button id="btnInsertTemplate" class="btn" onclick="openTemplateView()">Insert from template</button>
                        <button class="btn" onclick="commitMainModal()">Insert to form</button>
                        <button class="btn" onclick="closeModal()">Cancel</button>
                    </div>

                    <div id="templateView" style="display:none;">
                        <label>Category:</label>
                        <select id="categorySelect" onchange="onCategoryChange()">
                            <option value="">(Select a category)</option>
                            <option value="Physician Certification">Physician Certification</option>
                            <option value="HOMEBOUND STATUS:">HOMEBOUND STATUS:</option>
                            <option value="Emergency contact">Emergency contact</option>
                            <option value="FACE TO FACE ENCOUNTER">FACE TO FACE ENCOUNTER</option>
                        </select>

                        <div id="categoryTableContainer"></div>

                        <button id="btnInsertTemplateToSection" class="btn" onclick="insertSelectedTemplateItems()">Insert Template To ELIGIBILITY</button>
                        <button class="btn" onclick="closeTemplate()">Cancel</button>
                    </div>
                </div>
            </div>

            <script>
                const categoryItemsMap = {
                    "Physician Certification": [
                        "I certify/recertify that the above stated patient is homebound and has a need/continued need for intermittent skilled nursing, physical therapy and/or speech or occupational therapy services in their home for their current diagnosis as outlined in their initial plan of care. The patient is under my care, and I have authorized services on this plan of care and will periodically review the plan."
                    ],
                    "HOMEBOUND STATUS:": [
                        "All admission papers (including condition of admission, financial information form, patient's rights and responsibilities, notice of privacy practices, as well as name and phone number the Administrator of At Home Senior Care, Inc, and hotline DPH number) were explained to the patient/representative (if any) during the initial visit. Emergency preparedness discussed with pt/pcg, and emergency evacuation and management plan explained and created. Patient/representative (if any) indicated verbal understanding of the discussed paperwork, after which patient/representative signed and dated it.",
                        "Impact of patient's absence from home (physical, mental impact, recovery time etc.): _____",
                        "Pt/pcg received a written copy of proposed visit schedule, verbalized agreement. Medications reconciled with MD, updated written copy of medication list left at patient home. Treatment plan discussed with pt/cg, patient verbalizes understanding, written copy available upon pt/pcg request.",
                        "Requires transportation and assistance of another person to leave home safely.",
                        "Requires use of an assistive device and assist of one to leave home safely.",
                        "Has a medical contraindication that prevents patient from leaving home (describe): _____",
                        "Has functional or mental deficits impacting ability to leave home (describe): back pain, SOB",
                        "Patient with impaired mental status and cognition and requires supervision 24 hours a day and assistance of at least one person to leave the home.",
                        "Patient becomes exhausted after ambulating 30 feet with a walker and must sit down to catch his breath.",
                        "Patient with stairs in the home or exiting the home and due to fall risk, unsteady gait and poor endurance, is unable to negotiate the stairs safely and requires the assistance of another person to leave the home.",
                        "Reason(s) patient leaves home and duration of absence(s): 1-2/month for 1-2 hours for medical appointments and other medical necessities"
                    ],
                    "Emergency contact": [
                        "Emergency contact: spouse",
                        "Emergency contact: child"
                    ],
                    "FACE TO FACE ENCOUNTER": [
                        "The patient had face-to-face encounter on 09/23/2025 with MD Vaninov Eugene and the encounter was related to patient's needs for home health care."
                    ]
                };

                let accumulatedRows = [];

                function openModal() { document.getElementById('formFramePopup').classList.add('in'); }
                function closeModal() { document.getElementById('formFramePopup').classList.remove('in'); }
                function openTemplateView() {
                    document.getElementById('mainView').style.display = 'none';
                    document.getElementById('templateView').style.display = 'block';
                }
                function closeTemplate() {
                    document.getElementById('templateView').style.display = 'none';
                    document.getElementById('mainView').style.display = 'block';
                }

                function onCategoryChange() {
                    const cat = document.getElementById('categorySelect').value;
                    const items = categoryItemsMap[cat] || [];
                    let html = '<table class="template-table"><tbody>';
                    for (let i = 0; i < items.length; i++) {
                        html += `<tr><td><input type="checkbox" id="tmpl_cb_${i}" class="template-cb" /></td><td>${items[i]}</td></tr>`;
                    }
                    html += '</tbody></table>';
                    document.getElementById('categoryTableContainer').innerHTML = html;
                }

                function insertSelectedTemplateItems() {
                    const cbs = document.querySelectorAll('.template-cb:checked');
                    for (const cb of cbs) {
                        const txt = cb.closest('tr').querySelectorAll('td')[1].innerText;
                        accumulatedRows.push(txt);
                    }
                    renderMainTable();
                    closeTemplate();
                }

                function renderMainTable() {
                    const tbody = document.querySelector('#mainTable tbody');
                    let html = '';
                    for (let i = 0; i < accumulatedRows.length; i++) {
                        html += `<tr class="item-row"><td><input type="checkbox" checked /></td><td>${accumulatedRows[i]}</td></tr>`;
                    }
                    tbody.innerHTML = html;
                }

                function commitMainModal() {
                    const copItems = document.querySelector('#eligibility .cop-items');
                    copItems.innerText = accumulatedRows.join('\\n\\n');
                    closeModal();
                }
            </script>
        </body>
        </html>
        """)

        source_eligibility_text = """Eligibility
Physician Certification
I certify/recertify that the above stated patient is homebound and has a need/continued need for intermittent skilled nursing, physical therapy and/or speech or occupational therapy services in their home for their current diagnosis as outlined in their initial plan of care. The patient is under my care, and I have authorized services on this plan of care and will periodically review the plan. Start Effective Date: 09/25/2025
HOMEBOUND STATUS:
All admission papers (including condition of admission, financial information form, patient's rights and responsibilities, notice of privacy practices, as well as name and phone number the Administrator of At Home Senior Care, Inc, and hotline DPH number) were explained to the patient/representative (if any) during the initial visit. Emergency preparedness discussed with pt/pcg, and emergency evacuation and management plan explained and created. Patient/representative (if any) indicated verbal understanding of the discussed paperwork, after which patient/representative signed and dated it. Start Effective Date: 09/25/2025
Pt/pcg received a written copy of proposed visit schedule, verbalized agreement. Medications reconciled with MD, updated written copy of medication list left at patient home. Treatment plan discussed with pt/cg, patient verbalizes understanding, written copy available upon pt/pcg request. Start Effective Date: 09/25/2025
Requires use of an assistive device and assist of one to leave home safely. Start Effective Date: 09/25/2025
Has functional or mental deficits impacting ability to leave home (describe): back pain, SOB Start Effective Date: 09/25/2025
Patient becomes exhausted after ambulating 30 feet with a walker and must sit down to catch his breath. Start Effective Date: 09/25/2025
Patient with stairs in the home or exiting the home and due to fall risk, unsteady gait and poor endurance, is unable to negotiate the stairs safely and requires the assistance of another person to leave the home. Start Effective Date: 09/25/2025
Reason(s) patient leaves home and duration of absence(s): 1-2/month for 1-2 hours for medical appointments and other medical necessities Start Effective Date: 09/25/2025
Emergency contact
Emergency contact: spouse Start Effective Date: 09/25/2025
FACE TO FACE ENCOUNTER
The patient had face-to-face encounter on 09/23/2025 with MD Vaninov Eugene and the encounter was related to patient's needs for home health care. Start Effective Date: 09/25/2025"""

        extracted_fields = {
            "cop_section_eligibility": {
                "kind": "composite",
                "section": "eligibility",
                "title": "Eligibility",
                "items": [
                    "I certify/recertify that the above stated patient is homebound...",
                    "All admission papers (including condition of admission...",
                    "Pt/pcg received a written copy of proposed visit schedule...",
                    "Requires use of an assistive device and assist of one to leave home safely.",
                    "Has functional or mental deficits impacting ability to leave home (describe): back pain, SOB",
                    "Patient becomes exhausted after ambulating 30 feet with a walker and must sit down to catch his breath.",
                    "Patient with stairs in the home or exiting the home...",
                    "Reason(s) patient leaves home and duration of absence(s): 1-2/month for 1-2 hours...",
                    "Emergency contact: spouse",
                    "The patient had face-to-face encounter on 09/23/2025 with MD Vaninov Eugene..."
                ],
                "value": source_eligibility_text
            }
        }

        copier.inject_composite_linked_forms(page, extracted_fields)

        result_text = page.evaluate("() => document.querySelector('#eligibility .cop-items').innerText")
        print(f"Eligibility result text:\n{result_text}")

        # Assert all categories and their specific items were inserted
        assert "I certify/recertify that the above stated patient is homebound" in result_text
        assert "All admission papers" in result_text
        assert "Pt/pcg received a written copy" in result_text
        assert "Requires use of an assistive device" in result_text
        assert "Has functional or mental deficits" in result_text
        assert "Patient becomes exhausted" in result_text
        assert "Patient with stairs in the home" in result_text
        assert "Reason(s) patient leaves home" in result_text
        assert "Emergency contact: spouse" in result_text
        assert "The patient had face-to-face encounter on 09/23/2025" in result_text

        # Assert non-matching items were NOT selected
        assert "Emergency contact: child" not in result_text
        assert "Requires transportation and assistance" not in result_text

        print("Eligibility categorized template test passed!")
        browser.close()


def test_select_category_toggle_button_must_be_clicked():
    """Live Devero shows a '(Select a category)' toggleDropdown button.

    Template items are hidden until that button is pressed and a category
    is chosen. JS discovery that never clicks the button leaves the form empty.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content("""
        <html>
        <head>
            <style>
                .popup-modal { display: none; }
                .popup-modal.in { display: block; }
                .dropdown-menu { display: none; list-style: none; padding: 0; }
                .dropdown.open > .dropdown-menu { display: block; }
            </style>
        </head>
        <body>
            <cmwf-cop id="safety_measures">
                <a class="cop-title" data-section="safety_measures" href="javascript:void(0)" onclick="openModal()">Safety Measures</a>
                <div class="cop-items"></div>
            </cmwf-cop>

            <div id="formFramePopup" class="popup-modal">
                <h4>SAFETY MEASURES</h4>
                <div id="initialView">
                    <button class="btn" onclick="openTemplateView()">Insert From Template</button>
                    <table class="grid-table"><tbody>
                        <tr class="item-row"><td><input type="checkbox" /></td><td></td></tr>
                    </tbody></table>
                    <button class="btn btn-primary" onclick="commitToForm()">Insert to form</button>
                    <button class="btn" onclick="closeModal()">Cancel</button>
                </div>
                <div id="templateView" style="display:none;">
                    <label>Category:</label>
                    <div id="catDropdown" class="dropdown">
                    <button type="button" id="catToggle"
                            ng-click="toggleDropdown( $event );"
                            onclick="toggleCategoryDropdown(event)">(Select a category)</button>
                    <ul id="catMenu" class="dropdown-menu" role="listbox">
                        <li role="option"><a href="javascript:void(0)" onclick="pickCategory(event, 'ADVANCE DIRECTIVES:')">ADVANCE DIRECTIVES:</a></li>
                        <li role="option"><a href="javascript:void(0)" onclick="pickCategory(event, 'SAFETY MEASURES:')">SAFETY MEASURES:</a></li>
                    </ul>
                    </div>
                    <p id="pleaseSelect">Please select a category</p>
                    <div id="categoryTableContainer"></div>
                    <button class="btn btn-success" onclick="insertTemplateItems()">Insert Template To SAFETY MEASURES</button>
                </div>
            </div>
            <script>
                window.__categoryToggleClicks = 0;
                window.__categoryAnchorClicks = 0;
                const categoryItemsMap = {
                    "ADVANCE DIRECTIVES:": ["Full code", "Health care Proxy"],
                    "SAFETY MEASURES:": ["24 hr supervision", "Clear pathways", "Lock wheelchair"]
                };
                let currentCategory = "";
                let accumulatedRows = [];

                function openModal() { document.getElementById('formFramePopup').classList.add('in'); }
                function closeModal() { document.getElementById('formFramePopup').classList.remove('in'); }
                function openTemplateView() {
                    document.getElementById('initialView').style.display = 'none';
                    document.getElementById('templateView').style.display = 'block';
                }
                function toggleCategoryDropdown(event) {
                    // Live Angular toggleDropdown($event) ignores synthetic JS clicks.
                    if (!event || event.isTrusted !== true) return;
                    event.preventDefault();
                    event.stopPropagation();
                    window.__categoryToggleClicks += 1;
                    document.getElementById('catDropdown').classList.toggle('open');
                }
                function pickCategory(event, name) {
                    if (event && event.currentTarget && event.currentTarget.tagName === 'A') {
                        window.__categoryAnchorClicks += 1;
                    }
                    currentCategory = name;
                    document.getElementById('catToggle').textContent = name;
                    document.getElementById('catDropdown').classList.remove('open');
                    document.getElementById('pleaseSelect').style.display = 'none';
                    const items = categoryItemsMap[name] || [];
                    document.getElementById('categoryTableContainer').innerHTML =
                        '<table class="template-table"><tbody>' +
                        items.map((t, i) =>
                            '<tr><td><input type="checkbox" id="tmpl_cb_' + i +
                            '" class="template-cb" /></td><td>' + t + '</td></tr>'
                        ).join('') +
                        '</tbody></table>';
                }
                function insertTemplateItems() {
                    document.querySelectorAll('.template-cb:checked').forEach(cb => {
                        accumulatedRows.push(cb.closest('tr').querySelectorAll('td')[1].innerText);
                    });
                    document.getElementById('templateView').style.display = 'none';
                    document.getElementById('initialView').style.display = 'block';
                    const tbody = document.querySelector('.grid-table tbody');
                    tbody.innerHTML = accumulatedRows.map(t =>
                        '<tr class="item-row"><td><input type="checkbox" checked /></td><td>' + t + '</td></tr>'
                    ).join('');
                }
                function commitToForm() {
                    document.querySelector('#safety_measures .cop-items').innerHTML =
                        accumulatedRows.map(s => '<div class="copElement">' + s + '</div>').join('');
                    closeModal();
                }
            </script>
        </body>
        </html>
        """)

        extracted_fields = {
            "cop_section_safety_measures": {
                "kind": "composite",
                "section": "safety_measures",
                "title": "Safety Measures",
                "items": [
                    "24 hr supervision Start Effective Date: 09/25/2025",
                    "Clear pathways Start Effective Date: 09/25/2025",
                ],
                "value": (
                    "24 hr supervision Start Effective Date: 09/25/2025\n"
                    "Clear pathways Start Effective Date: 09/25/2025"
                ),
            }
        }

        copier.inject_composite_linked_forms(page, extracted_fields)

        clicks = page.evaluate("() => window.__categoryToggleClicks")
        anchor_clicks = page.evaluate("() => window.__categoryAnchorClicks")
        result_text = page.evaluate(
            "() => document.querySelector('#safety_measures .cop-items').innerText"
        )
        print(f"category toggle clicks={clicks} anchor clicks={anchor_clicks}")
        print(f"Safety result text:\n{result_text}")

        assert clicks >= 1, "Must press the (Select a category) toggleDropdown button"
        assert anchor_clicks >= 1, "Must click the category option <a>, not only the <li>"
        assert "24 hr supervision" in result_text
        assert "Clear pathways" in result_text
        assert "Lock wheelchair" not in result_text
        browser.close()


def test_portaled_category_menu_picks_section_option():
    """Live ui-bootstrap appends the category menu to document.body.

    After the toggle opens, the copier must click SAFETY MEASURES: in that
    detached menu. Clicking the toggle again closes it and selects nothing.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content("""
        <html>
        <head>
            <style>
                .popup-modal { display: none; }
                .popup-modal.in { display: block; }
                .dropdown-menu { display: none; list-style: none; padding: 4px; background: #fff; }
                body > .dropdown-menu.open-menu { display: block; }
            </style>
        </head>
        <body>
            <cmwf-cop id="safety_measures">
                <a class="cop-title" data-section="safety_measures" href="javascript:void(0)" onclick="openModal()">Safety Measures</a>
                <div class="cop-items"></div>
            </cmwf-cop>
            <div id="formFramePopup" class="popup-modal">
                <h4>SAFETY MEASURES</h4>
                <div id="initialView">
                    <button class="btn" onclick="openTemplateView()">Insert From Template</button>
                    <table class="grid-table"><tbody>
                        <tr class="item-row"><td><input type="checkbox" /></td><td></td></tr>
                    </tbody></table>
                    <button class="btn btn-primary" onclick="commitToForm()">Insert to form</button>
                    <button class="btn" onclick="closeModal()">Cancel</button>
                </div>
                <div id="templateView" style="display:none;">
                    <label>Category:</label>
                    <div id="catDropdown" class="dropdown">
                        <button type="button" id="catToggle"
                                ng-click="toggleDropdown( $event );"
                                onclick="toggleCategoryDropdown(event)">(Select a category)</button>
                    </div>
                    <p id="pleaseSelect">Please select a category</p>
                    <div id="categoryTableContainer"></div>
                    <button class="btn btn-success" onclick="insertTemplateItems()">Insert Template To SAFETY MEASURES</button>
                </div>
            </div>
            <ul id="catMenu" class="dropdown-menu" role="listbox">
                <li role="option"><a href="javascript:void(0)" onclick="pickCategory(event, 'Triage/Risk Code')">Triage/Risk Code</a></li>
                <li role="option"><a href="javascript:void(0)" onclick="pickCategory(event, 'Evacuation')">Evacuation</a></li>
                <li role="option"><a href="javascript:void(0)" onclick="pickCategory(event, 'SAFETY MEASURES:')">SAFETY MEASURES:</a></li>
                <li role="option"><a href="javascript:void(0)" onclick="pickCategory(event, 'PATIENT PERSONAL GOALS')">PATIENT PERSONAL GOALS</a></li>
            </ul>
            <script>
                window.__categoryToggleClicks = 0;
                window.__categoryAnchorClicks = 0;
                window.__picked = [];
                const categoryItemsMap = {
                    "Triage/Risk Code": ["Red"],
                    "Evacuation": ["Needs assistance"],
                    "SAFETY MEASURES:": ["24 hr supervision", "Clear pathways", "Lock wheelchair"],
                    "PATIENT PERSONAL GOALS": ["Stay at home"]
                };
                let currentCategory = "";
                let accumulatedRows = [];

                function openModal() { document.getElementById('formFramePopup').classList.add('in'); }
                function closeModal() { document.getElementById('formFramePopup').classList.remove('in'); }
                function openTemplateView() {
                    document.getElementById('initialView').style.display = 'none';
                    document.getElementById('templateView').style.display = 'block';
                    currentCategory = "";
                    document.getElementById('catToggle').textContent = '(Select a category)';
                    document.getElementById('pleaseSelect').style.display = 'block';
                    document.getElementById('categoryTableContainer').innerHTML = '';
                    closeMenu();
                }
                function closeMenu() {
                    const menu = document.getElementById('catMenu');
                    menu.classList.remove('open-menu');
                    menu.style.display = 'none';
                }
                function toggleCategoryDropdown(event) {
                    if (!event || event.isTrusted !== true) return;
                    window.__categoryToggleClicks += 1;
                    const menu = document.getElementById('catMenu');
                    const open = menu.classList.contains('open-menu');
                    if (open) { closeMenu(); return; }
                    document.body.appendChild(menu);
                    menu.classList.add('open-menu');
                    menu.style.display = 'block';
                }
                function pickCategory(event, name) {
                    if (!event || event.isTrusted !== true) return;
                    if (event.currentTarget && event.currentTarget.tagName === 'A') {
                        window.__categoryAnchorClicks += 1;
                    }
                    window.__picked.push(name);
                    currentCategory = name;
                    document.getElementById('catToggle').textContent = name;
                    closeMenu();
                    document.getElementById('pleaseSelect').style.display = 'none';
                    const items = categoryItemsMap[name] || [];
                    document.getElementById('categoryTableContainer').innerHTML =
                        '<table class="template-table"><tbody>' +
                        items.map((t, i) =>
                            '<tr><td><input type="checkbox" id="tmpl_cb_' + i +
                            '" class="template-cb" /></td><td>' + t + '</td></tr>'
                        ).join('') + '</tbody></table>';
                }
                function insertTemplateItems() {
                    document.querySelectorAll('.template-cb:checked').forEach(cb => {
                        accumulatedRows.push(cb.closest('tr').querySelectorAll('td')[1].innerText);
                    });
                    document.getElementById('templateView').style.display = 'none';
                    document.getElementById('initialView').style.display = 'block';
                    document.querySelector('.grid-table tbody').innerHTML = accumulatedRows.map(t =>
                        '<tr class="item-row"><td><input type="checkbox" checked /></td><td>' + t + '</td></tr>'
                    ).join('');
                }
                function commitToForm() {
                    document.querySelector('#safety_measures .cop-items').innerHTML =
                        accumulatedRows.map(s => '<div class="copElement">' + s + '</div>').join('');
                    closeModal();
                }
            </script>
        </body>
        </html>
        """)

        extracted_fields = {
            "cop_section_safety_measures": {
                "kind": "composite",
                "section": "safety_measures",
                "title": "Safety Measures",
                "items": [
                    "Triage/Risk Code",
                    "Evacuation",
                    "SAFETY MEASURES: 24 hr supervision Start Effective Date: 09/25/2025",
                    "Clear pathways Start Effective Date: 09/25/2025",
                    "PATIENT PERSONAL GOALS",
                ],
                "value": (
                    "Triage/Risk Code Evacuation SAFETY MEASURES: "
                    "24 hr supervision Start Effective Date: 09/25/2025 "
                    "Clear pathways Start Effective Date: 09/25/2025 "
                    "PATIENT PERSONAL GOALS"
                ),
            }
        }

        copier.inject_composite_linked_forms(page, extracted_fields)

        picked = page.evaluate("() => window.__picked")
        result_text = page.evaluate(
            "() => document.querySelector('#safety_measures .cop-items').innerText"
        )
        print(f"picked={picked}")
        print(f"Safety result text:\n{result_text}")

        assert picked == ["SAFETY MEASURES:"], picked
        assert "24 hr supervision" in result_text
        assert "Clear pathways" in result_text
        assert "Lock wheelchair" not in result_text
        assert "Needs assistance" not in result_text
        browser.close()


def test_no_category_multiselect_clicks_safety_measures():
    """Live Devero: after (Select a category), a list appears with No Category first.

    The copier must click SAFETY MEASURES: below that row, not the toggle again.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content("""
        <html>
        <head>
            <style>
                .popup-modal { display: none; }
                .popup-modal.in { display: block; }
                .multiselect-container { display: none; background: #fff; padding: 4px; }
                .multiselect-container.open { display: block; }
            </style>
        </head>
        <body>
            <cmwf-cop id="safety_measures">
                <a class="cop-title" data-section="safety_measures" href="javascript:void(0)" onclick="openModal()">Safety Measures</a>
                <div class="cop-items"></div>
            </cmwf-cop>
            <div id="formFramePopup" class="popup-modal">
                <h4>SAFETY MEASURES</h4>
                <div id="initialView">
                    <button class="btn" onclick="openTemplateView()">Insert From Template</button>
                    <table class="grid-table"><tbody>
                        <tr class="item-row"><td><input type="checkbox" /></td><td></td></tr>
                    </tbody></table>
                    <button class="btn btn-primary" onclick="commitToForm()">Insert to form</button>
                    <button class="btn" onclick="closeModal()">Cancel</button>
                </div>
                <div id="templateView" style="display:none;">
                    <label>Category:</label>
                    <div class="multiselect dropdown-toggle btn btn-default" id="catToggle"
                         ng-click="toggleDropdown( $event );"
                         onclick="toggleCategoryDropdown(event)">(Select a category)</div>
                    <p id="pleaseSelect">Please select a category</p>
                    <div id="categoryTableContainer"></div>
                    <button class="btn btn-success" onclick="insertTemplateItems()">Insert Template To SAFETY MEASURES</button>
                </div>
            </div>
            <div id="catMenu" class="multiselect-container">
                <div class="item" onclick="pickCategory(event, 'No Category')"><label>No Category</label></div>
                <div class="item" onclick="pickCategory(event, 'Triage/Risk Code')"><label>Triage/Risk Code</label></div>
                <div class="item" onclick="pickCategory(event, 'Evacuation')"><label>Evacuation</label></div>
                <div class="item" onclick="pickCategory(event, 'SAFETY MEASURES:')"><label>SAFETY MEASURES:</label></div>
                <div class="item" onclick="pickCategory(event, 'PATIENT PERSONAL GOALS')"><label>PATIENT PERSONAL GOALS</label></div>
            </div>
            <script>
                window.__categoryToggleClicks = 0;
                window.__picked = [];
                const categoryItemsMap = {
                    "No Category": [],
                    "Triage/Risk Code": ["Red"],
                    "Evacuation": ["Needs assistance"],
                    "SAFETY MEASURES:": ["24 hr supervision", "Clear pathways", "Lock wheelchair"],
                    "PATIENT PERSONAL GOALS": ["Stay at home"]
                };
                let accumulatedRows = [];
                function openModal() { document.getElementById('formFramePopup').classList.add('in'); }
                function closeModal() { document.getElementById('formFramePopup').classList.remove('in'); }
                function openTemplateView() {
                    document.getElementById('initialView').style.display = 'none';
                    document.getElementById('templateView').style.display = 'block';
                    document.getElementById('catToggle').textContent = '(Select a category)';
                    document.getElementById('pleaseSelect').style.display = 'block';
                    document.getElementById('categoryTableContainer').innerHTML = '';
                    document.getElementById('catMenu').classList.remove('open');
                }
                function toggleCategoryDropdown(event) {
                    if (!event || event.isTrusted !== true) return;
                    window.__categoryToggleClicks += 1;
                    document.getElementById('catMenu').classList.toggle('open');
                }
                function pickCategory(event, name) {
                    if (!event || event.isTrusted !== true) return;
                    window.__picked.push(name);
                    document.getElementById('catToggle').textContent = name;
                    document.getElementById('catMenu').classList.remove('open');
                    document.getElementById('pleaseSelect').style.display = 'none';
                    const items = categoryItemsMap[name] || [];
                    document.getElementById('categoryTableContainer').innerHTML =
                        '<table class="template-table"><tbody>' +
                        items.map((t, i) =>
                            '<tr><td><input type="checkbox" class="template-cb" /></td><td>' + t + '</td></tr>'
                        ).join('') + '</tbody></table>';
                }
                function insertTemplateItems() {
                    document.querySelectorAll('.template-cb:checked').forEach(cb => {
                        accumulatedRows.push(cb.closest('tr').querySelectorAll('td')[1].innerText);
                    });
                    document.getElementById('templateView').style.display = 'none';
                    document.getElementById('initialView').style.display = 'block';
                    document.querySelector('.grid-table tbody').innerHTML = accumulatedRows.map(t =>
                        '<tr class="item-row"><td><input type="checkbox" checked /></td><td>' + t + '</td></tr>'
                    ).join('');
                }
                function commitToForm() {
                    document.querySelector('#safety_measures .cop-items').innerHTML =
                        accumulatedRows.map(s => '<div class="copElement">' + s + '</div>').join('');
                    closeModal();
                }
            </script>
        </body>
        </html>
        """)

        extracted_fields = {
            "cop_section_safety_measures": {
                "kind": "composite",
                "section": "safety_measures",
                "title": "Safety Measures",
                "items": [
                    "Triage/Risk Code",
                    "24 hr supervision Start Effective Date: 09/25/2025",
                    "Clear pathways Start Effective Date: 09/25/2025",
                    "PATIENT PERSONAL GOALS",
                ],
                "value": (
                    "Triage/Risk Code Evacuation SAFETY MEASURES: "
                    "24 hr supervision Clear pathways PATIENT PERSONAL GOALS"
                ),
            }
        }

        copier.inject_composite_linked_forms(page, extracted_fields)
        picked = page.evaluate("() => window.__picked")
        clicks = page.evaluate("() => window.__categoryToggleClicks")
        result_text = page.evaluate(
            "() => document.querySelector('#safety_measures .cop-items').innerText"
        )
        print(f"picked={picked} toggleClicks={clicks}")
        print(f"result={result_text}")
        assert "No Category" not in picked
        assert picked == ["SAFETY MEASURES:"], picked
        assert clicks == 1, clicks
        assert "24 hr supervision" in result_text
        assert "Clear pathways" in result_text
        assert "Lock wheelchair" not in result_text
        browser.close()


def test_preselected_category_toggle_is_not_clicked():
    """Advance Directives already shows ADVANCE DIRECTIVES: and the items."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content("""
        <html>
        <head>
            <style>
                .popup-modal { display: none; }
                .popup-modal.in { display: block; }
            </style>
        </head>
        <body>
            <cmwf-cop id="advanced_directives">
                <a class="cop-title" data-section="advanced_directives" href="javascript:void(0)" onclick="openModal()">Advance Directives</a>
                <div class="cop-items"></div>
            </cmwf-cop>
            <div id="formFramePopup" class="popup-modal">
                <div id="initialView">
                    <button class="btn" onclick="openTemplateView()">Insert From Template</button>
                    <table class="grid-table"><tbody>
                        <tr class="item-row"><td><input type="checkbox" /></td><td></td></tr>
                    </tbody></table>
                    <button class="btn btn-primary" onclick="commitToForm()">Insert to form</button>
                    <button class="btn" onclick="closeModal()">Cancel</button>
                </div>
                <div id="templateView" style="display:none;">
                    <label>Category:</label>
                    <button type="button" id="catToggle"
                            ng-click="toggleDropdown( $event );"
                            onclick="window.__categoryToggleClicks++">ADVANCE DIRECTIVES:</button>
                    <div class="item-row"><label><input type="checkbox" id="cbFull" /> Full code</label></div>
                    <div class="item-row"><label><input type="checkbox" id="cbProxy" /> Health care Proxy</label></div>
                    <div class="item-row"><label><input type="checkbox" id="cbDnr" /> DNR</label></div>
                    <button class="btn btn-success" onclick="insertTemplateItems()">Insert Template To ADVANCE DIRECTIVES</button>
                </div>
            </div>
            <script>
                window.__categoryToggleClicks = 0;
                let accumulatedRows = [];
                function openModal() { document.getElementById('formFramePopup').classList.add('in'); }
                function closeModal() { document.getElementById('formFramePopup').classList.remove('in'); }
                function openTemplateView() {
                    document.getElementById('initialView').style.display = 'none';
                    document.getElementById('templateView').style.display = 'block';
                }
                function insertTemplateItems() {
                    if (document.getElementById('cbFull').checked) accumulatedRows.push('Full code');
                    if (document.getElementById('cbProxy').checked) accumulatedRows.push('Health care Proxy');
                    if (document.getElementById('cbDnr').checked) accumulatedRows.push('DNR');
                    document.getElementById('templateView').style.display = 'none';
                    document.getElementById('initialView').style.display = 'block';
                    document.querySelector('.grid-table tbody').innerHTML = accumulatedRows.map(t =>
                        '<tr class="item-row"><td><input type="checkbox" checked /></td><td>' + t + '</td></tr>'
                    ).join('');
                }
                function commitToForm() {
                    document.querySelector('#advanced_directives .cop-items').innerHTML =
                        accumulatedRows.map(s => '<div class="copElement">' + s + '</div>').join('');
                    closeModal();
                }
            </script>
        </body>
        </html>
        """)

        extracted_fields = {
            "cop_section_advanced_directives": {
                "kind": "composite",
                "section": "advanced_directives",
                "title": "Advance Directives",
                "items": [
                    "ADVANCE DIRECTIVES: Full code Start Effective Date: 09/25/2025 "
                    "Health care Proxy Start Effective Date: 09/25/2025"
                ],
                "value": (
                    "ADVANCE DIRECTIVES: Full code Start Effective Date: 09/25/2025 "
                    "Health care Proxy Start Effective Date: 09/25/2025"
                ),
            }
        }

        copier.inject_composite_linked_forms(page, extracted_fields)
        clicks = page.evaluate("() => window.__categoryToggleClicks")
        result_text = page.evaluate(
            "() => document.querySelector('#advanced_directives .cop-items').innerText"
        )
        assert clicks == 0, "Pre-selected category toggle must not be clicked"
        assert "Full code" in result_text
        assert "Health care Proxy" in result_text
        assert "DNR" not in result_text
        browser.close()


def test_two_step_select_list_then_insert_to_form():
    """After Insert From Template fills the modal list, rows must be
    checked and Insert to form clicked or the page COP stays empty.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content("""
        <html>
        <head>
            <style>
                .popup-modal { display: none; }
                .popup-modal.in { display: block; }
            </style>
        </head>
        <body>
            <cmwf-cop id="safety_measures">
                <a class="cop-title" data-section="safety_measures" href="javascript:void(0)" onclick="openModal()">Safety Measures</a>
                <div class="cop-items"></div>
            </cmwf-cop>
            <div id="formFramePopup" class="popup-modal">
                <div id="initialView">
                    <button class="btn" onclick="openTemplateView()">Insert From Template</button>
                    <table class="grid-table"><tbody id="mainBody">
                        <tr class="item-row"><td><input type="checkbox" /></td><td></td></tr>
                    </tbody></table>
                    <button id="btnInsertToForm" class="btn btn-primary"
                            data-automation-id="button-insert-to-form"
                            onclick="commitToForm(event)">Insert to form</button>
                    <button class="btn" onclick="closeModal()">Cancel</button>
                </div>
                <div id="templateView" style="display:none;">
                    <label><input type="checkbox" id="cb24" /> 24 hr supervision</label>
                    <label><input type="checkbox" id="cbClear" /> Clear pathways</label>
                    <label><input type="checkbox" id="cbLock" /> Lock wheelchair</label>
                    <button class="btn" onclick="insertTemplateItems()">Insert Template To SAFETY MEASURES</button>
                </div>
            </div>
            <script>
                window.__insertToFormClicks = 0;
                function openModal() { document.getElementById('formFramePopup').classList.add('in'); }
                function closeModal() { document.getElementById('formFramePopup').classList.remove('in'); }
                function openTemplateView() {
                    document.getElementById('initialView').style.display = 'none';
                    document.getElementById('templateView').style.display = 'block';
                }
                function insertTemplateItems() {
                    const rows = [];
                    if (document.getElementById('cb24').checked) rows.push('24 hr supervision');
                    if (document.getElementById('cbClear').checked) rows.push('Clear pathways');
                    if (document.getElementById('cbLock').checked) rows.push('Lock wheelchair');
                    document.getElementById('templateView').style.display = 'none';
                    document.getElementById('initialView').style.display = 'block';
                    document.getElementById('mainBody').innerHTML = rows.map(t =>
                        '<tr class="item-row"><td><input type="checkbox" /></td><td>' + t + '</td></tr>'
                    ).join('');
                }
                function commitToForm(event) {
                    if (!event || event.isTrusted !== true) return;
                    window.__insertToFormClicks += 1;
                    const selected = [];
                    document.querySelectorAll('#mainBody tr').forEach(r => {
                        const cb = r.querySelector('input[type="checkbox"]');
                        if (cb && cb.checked) selected.push(r.innerText.trim());
                    });
                    document.querySelector('#safety_measures .cop-items').innerHTML =
                        selected.map(s => '<div class="copElement">' + s + '</div>').join('');
                    closeModal();
                }
            </script>
        </body>
        </html>
        """)
        copier.inject_composite_linked_forms(page, {
            "cop_section_safety_measures": {
                "kind": "composite",
                "section": "safety_measures",
                "title": "Safety Measures",
                "items": ["24 hr supervision", "Clear pathways"],
                "value": "24 hr supervision Clear pathways",
            }
        })
        clicks = page.evaluate("() => window.__insertToFormClicks")
        result = page.evaluate("""() => {
            const el = document.querySelector('#safety_measures .cop-items');
            return el ? el.innerText : '';
        }""")
        assert clicks == 1, "Must click Insert to form exactly once after the list is filled"
        assert "24 hr supervision" in result
        assert "Clear pathways" in result
        assert "Lock wheelchair" not in result
        browser.close()


def test_nested_template_popup_is_committed_before_outer_insert_button():
    """The live template dialog overlays the linked-form dialog.

    Clicking the outer Insert button while this surface is open is intercepted
    and prevents the remaining linked sections from being processed.
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content("""
        <html><head><style>
            #formFramePopup { display:block; position:fixed; inset:0; }
            #insertToTemplatePopup {
                display:block; position:fixed; inset:20px; z-index:2000;
                background:white;
            }
        </style></head><body>
            <div id="formFramePopup" class="popup-modal in">
                <button data-automation-id="button-insert-to-form"
                        onclick="outerClicks += 1">Insert to form</button>
                <div id="insertToTemplatePopup">
                    <button onclick="nestedClicks += 1; this.parentElement.style.display='none'">
                        Insert to form
                    </button>
                </div>
            </div>
            <script>let nestedClicks = 0; let outerClicks = 0;</script>
        </body></html>
        """)

        assert copier._template_surface_is_open(page)
        assert copier._click_template_insert(page)
        assert not copier._template_surface_is_open(page)
        assert copier._click_insert_to_form(page)
        result = page.evaluate("() => ({nestedClicks, outerClicks})")
        browser.close()

    assert result == {"nestedClicks": 1, "outerClicks": 1}


if __name__ == "__main__":
    test_advance_directives_insert_from_template()
    test_advance_directives_concatenated_source_blob()
    test_chart_placeholder_select_does_not_block_template_items()
    test_safety_measures_insert_from_template()
    test_empty_selection_clicks_cancel_and_leaves_empty()
    test_eligibility_categorized_template_insert()
    test_empty_option_text_does_not_match_category()
    test_empty_dropdown_row_is_not_clicked_as_category()
    test_matching_prefers_section_named_category()
    test_select_category_toggle_button_must_be_clicked()
    test_portaled_category_menu_picks_section_option()
    test_no_category_list_clicks_item_below_placeholder()
    test_no_category_multiselect_clicks_safety_measures()
    test_preselected_category_toggle_is_not_clicked()
    test_two_step_select_list_then_insert_to_form()
    print("All template tests passed successfully!")
