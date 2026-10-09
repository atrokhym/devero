import importlib.util
from pathlib import Path
from playwright.sync_api import sync_playwright

spec = importlib.util.spec_from_file_location(
    "copier",
    Path(__file__).resolve().parent.parent / "devero_copier_oasis.py",
)
copier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(copier)


def test_advance_directives_full_code_selection_and_insert():
    """Verify that source 'ADVANCE DIRECTIVES: Full code Start Effective Date: 01/10/2026'

    selects 'Full code' in the modal and inserts into the target note form without popups blocking.
    """
    html_page = """
    <html>
    <head>
        <style>
            .popup-modal { display: none; }
            .popup-modal.in { display: block; position: fixed; top: 0; left: 0; width: 500px; height: 400px; background: white; z-index: 1000; }
        </style>
    </head>
    <body>
        <cmwf-cop id="advance_directives">
            <a class="cop-title" data-section="advance_directives" href="javascript:void(0)" onclick="openModal()">Advance Directives</a>
            <div class="cop-items"></div>
        </cmwf-cop>

        <div id="formFramePopup" class="popup-modal">
            <div class="modal-content">
                <button class="close" onclick="closeModal()">×</button>
                <h4>Advance Directives</h4>
                <div class="ui-grid-render-container-body">
                    <table>
                        <tbody>
                            <tr class="ui-grid-row">
                                <td><input type="checkbox" ng-model="row.isSelected" id="cb_fullcode" /></td>
                                <td>Full code</td>
                                <td>Start Effective Date: 01/10/2026</td>
                            </tr>
                            <tr class="ui-grid-row">
                                <td><input type="checkbox" ng-model="row.isSelected" id="cb_proxy" /></td>
                                <td>Health care Proxy</td>
                                <td>Start Effective Date: 01/10/2026</td>
                            </tr>
                        </tbody>
                    </table>
                </div>
                <button data-automation-id="button-insert-to-form" class="btn" onclick="commitInsert()">Insert to form</button>
                <button class="btn" onclick="closeModal()">Cancel</button>
            </div>
        </div>

        <script>
            let selectedRows = [];
            function openModal() { document.getElementById('formFramePopup').classList.add('in'); }
            function closeModal() { document.getElementById('formFramePopup').classList.remove('in'); }
            function commitInsert() {
                const cbs = document.querySelectorAll('.ui-grid-row input[type="checkbox"]:checked');
                if (cbs.length === 0) {
                    alert("Please select one or more rows to insert into form");
                    return;
                }
                const texts = [];
                for (const cb of cbs) {
                    const row = cb.closest('tr');
                    texts.push(row.querySelectorAll('td')[1].innerText);
                }
                document.querySelector('#advance_directives .cop-items').innerText = texts.join('\\n\\n');
                closeModal();
            }
        </script>
    </body>
    </html>
    """

    source_fields = {
        "cop_section_advanced_directives": {
            "kind": "composite",
            "section": "advanced_directives",
            "title": "Advance Directives",
            "items": ["Full code"],
            "value": "ADVANCE DIRECTIVES: Full code Start Effective Date: 01/10/2026",
        }
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(html_page)

        copier.inject_composite_linked_forms(page, source_fields)

        result_text = page.evaluate("() => document.querySelector('#advance_directives .cop-items').innerText")
        is_modal_open = page.evaluate("() => document.getElementById('formFramePopup').classList.contains('in')")
        browser.close()

        assert "Full code" in result_text
        assert "Health care Proxy" not in result_text
        assert not is_modal_open


def test_update_medications_integrated_action_and_insert():
    """Verify that medications with integrated action (e.g. medications_action_1)

    are matched, selected, and inserted via 'Insert to form'.
    """
    html_page = """
    <html>
    <head>
        <style>
            .popup-modal { display: none; }
            .popup-modal.in { display: block; position: fixed; top: 0; left: 0; width: 500px; height: 400px; background: white; z-index: 1000; }
        </style>
    </head>
    <body>
        <cmwf-section class="cmwf-section">
            <cmwf-integrated-action id="medications_action_1">
                <span>Update Medications</span>
            </cmwf-integrated-action>
            <cmwf-text-display id="cp_medications_display_1">
            </cmwf-text-display>
        </cmwf-section>

        <div id="formFramePopup" class="popup-modal">
            <div class="modal-content">
                <button class="close" onclick="closeModal()">×</button>
                <h4>Medications</h4>
                <div class="ui-grid-render-container-body">
                    <table>
                        <tbody>
                            <tr class="ui-grid-row">
                                <td><input type="checkbox" ng-model="row.isSelected" id="cb_med1" /></td>
                                <td>Aspirin 81 mg oral daily</td>
                            </tr>
                            <tr class="ui-grid-row">
                                <td><input type="checkbox" ng-model="row.isSelected" id="cb_med2" /></td>
                                <td>Metformin 500 mg oral BID</td>
                            </tr>
                        </tbody>
                    </table>
                </div>
                <button data-automation-id="button-insert-to-form" class="btn" onclick="commitInsert()">Insert to form</button>
                <button class="btn" onclick="closeModal()">Cancel</button>
            </div>
        </div>

        <script>
            document.getElementById('medications_action_1').addEventListener('click', () => {
                document.getElementById('formFramePopup').classList.add('in');
            });
            function closeModal() { document.getElementById('formFramePopup').classList.remove('in'); }
            function commitInsert() {
                const cbs = document.querySelectorAll('.ui-grid-row input[type="checkbox"]:checked');
                const texts = [];
                for (const cb of cbs) {
                    texts.push(cb.closest('tr').querySelectorAll('td')[1].innerText);
                }
                document.getElementById('cp_medications_display_1').innerText = texts.join('\\n\\n');
                closeModal();
            }
        </script>
    </body>
    </html>
    """

    source_fields = {
        "linked_section_medications": {
            "kind": "integrated",
            "section": "medications",
            "action_id": "medications_action_1",
            "title": "Update Medications",
            "items": ["Aspirin 81 mg oral daily", "Metformin 500 mg oral BID"],
            "value": "Aspirin 81 mg oral daily\n\nMetformin 500 mg oral BID",
        }
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(html_page)

        copier.inject_composite_linked_forms(page, source_fields)

        result_text = page.evaluate("() => document.getElementById('cp_medications_display_1').innerText")
        is_modal_open = page.evaluate("() => document.getElementById('formFramePopup').classList.contains('in')")
        browser.close()

        assert "Aspirin 81 mg" in result_text
        assert "Metformin 500 mg" in result_text
        assert not is_modal_open


def test_interventions_goals_linked_form():
    """Verify Interventions/Goals linked form is discovered, selected, and inserted."""
    html_page = """
    <html>
    <head>
        <style>
            .popup-modal { display: none; }
            .popup-modal.in { display: block; position: fixed; top: 0; left: 0; width: 500px; height: 400px; background: white; z-index: 1000; }
        </style>
    </head>
    <body>
        <cmwf-cop id="interventions_goals">
            <a class="cop-title" data-section="interventions_goals" href="javascript:void(0)" onclick="openModal()">Interventions/Goals</a>
            <div class="cop-items"></div>
        </cmwf-cop>

        <div id="formFramePopup" class="popup-modal">
            <div class="modal-content">
                <button class="close" onclick="closeModal()">×</button>
                <h4>Care Plan / Interventions & Goals</h4>
                <div class="ui-grid-render-container-body">
                    <table>
                        <tbody>
                            <tr class="ui-grid-row">
                                <td><input type="checkbox" ng-model="row.isSelected" id="cb_ig1" /></td>
                                <td>SN will instruct patient and caregiver on diabetic diet and blood glucose monitoring.</td>
                            </tr>
                            <tr class="ui-grid-row">
                                <td><input type="checkbox" ng-model="row.isSelected" id="cb_ig2" /></td>
                                <td>Patient will maintain fasting blood sugar between 80-130 mg/dL without symptoms of hypoglycemia.</td>
                            </tr>
                        </tbody>
                    </table>
                </div>
                <button data-automation-id="button-insert-to-form" class="btn" onclick="commitInsert()">Insert to form</button>
            </div>
        </div>

        <script>
            function openModal() { document.getElementById('formFramePopup').classList.add('in'); }
            function closeModal() { document.getElementById('formFramePopup').classList.remove('in'); }
            function commitInsert() {
                const cbs = document.querySelectorAll('.ui-grid-row input[type="checkbox"]:checked');
                const texts = [];
                for (const cb of cbs) {
                    texts.push(cb.closest('tr').querySelectorAll('td')[1].innerText);
                }
                document.querySelector('#interventions_goals .cop-items').innerText = texts.join('\\n\\n');
                closeModal();
            }
        </script>
    </body>
    </html>
    """

    source_fields = {
        "cop_section_interventions_goals": {
            "kind": "composite",
            "section": "interventions_goals",
            "title": "Interventions/Goals",
            "items": [
                "SN will instruct patient and caregiver on diabetic diet and blood glucose monitoring.",
                "Patient will maintain fasting blood sugar between 80-130 mg/dL without symptoms of hypoglycemia.",
            ],
            "value": "SN will instruct patient and caregiver on diabetic diet and blood glucose monitoring.\n\nPatient will maintain fasting blood sugar between 80-130 mg/dL without symptoms of hypoglycemia.",
        }
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(html_page)

        copier.inject_composite_linked_forms(page, source_fields)

        result_text = page.evaluate("() => document.querySelector('#interventions_goals .cop-items').innerText")
        is_modal_open = page.evaluate("() => document.getElementById('formFramePopup').classList.contains('in')")
        browser.close()

        assert "SN will instruct patient" in result_text
        assert "Patient will maintain fasting blood sugar" in result_text
        assert not is_modal_open


def test_warning_popups_auto_dismissal():
    """Verify handle_warning_popups automatically clears blocking error/warning dialogs without closing form modal."""
    html_page = """
    <html>
    <body>
        <div id="formFramePopup" class="popup-modal in">
            <div class="modal-content">
                <h4>Main Form</h4>
            </div>
        </div>

        <div class="bootbox modal in" role="dialog" style="display: block;">
            <div class="modal-dialog">
                <div class="modal-content">
                    <div class="modal-header">
                        <h4 class="modal-title">Validation Warning</h4>
                    </div>
                    <div class="modal-body">
                        Please confirm this warning to proceed.
                    </div>
                    <div class="modal-footer">
                        <button class="btn btn-primary bootbox-accept" onclick="dismissPopup(this)">OK</button>
                    </div>
                </div>
            </div>
        </div>

        <script>
            function dismissPopup(btn) {
                btn.closest('.bootbox').remove();
            }
        </script>
    </body>
    </html>
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(html_page)

        copier.handle_warning_popups(page)

        bootbox_count = page.evaluate("() => document.querySelectorAll('.bootbox').length")
        is_form_open = page.evaluate("() => document.getElementById('formFramePopup').classList.contains('in')")
        browser.close()

        assert bootbox_count == 0
        assert is_form_open is True
