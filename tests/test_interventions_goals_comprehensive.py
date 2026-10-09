import importlib.util
from pathlib import Path
from playwright.sync_api import sync_playwright

spec = importlib.util.spec_from_file_location(
    "copier",
    Path(__file__).resolve().parent.parent / "devero_copier_oasis.py",
)
copier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(copier)


def test_extracts_separate_interventions_and_goals_integrated_actions():
    html_page = """
    <html><body>
        <cmwf-section>
            <cmwf-integrated-action id="interventions_action_1">
                <span>Interventions</span>
            </cmwf-integrated-action>
            <cmwf-text-display id="cp_interventions">
                Assess medication compliance Start Effective Date: 08/01/2026
            </cmwf-text-display>
        </cmwf-section>
        <cmwf-section>
            <cmwf-integrated-action id="goals_action_1">
                <span>Goals</span>
            </cmwf-integrated-action>
            <cmwf-text-display id="cp_goals">
                Patient will remain stable Start Effective Date: 08/01/2026
            </cmwf-text-display>
        </cmwf-section>
    </body></html>
    """

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(html_page)
        fields = page.evaluate(copier.JS_EXTRACT_FIELDS)
        browser.close()

    assert "Assess medication compliance" in fields["linked_section_interventions"]["value"]
    assert "Patient will remain stable" in fields["linked_section_goals"]["value"]


def test_separate_interventions_and_goals_actions_on_target():
    """Verify target with separate interventions_action_1 and goals_action_1

    correctly extracts and inserts items from combined or separate source data.
    """
    html_page = """
    <html>
    <head>
        <style>
            .popup-modal { display: none; }
            .popup-modal.in { display: block; position: fixed; top: 0; left: 0; width: 600px; height: 500px; background: white; z-index: 1000; }
        </style>
    </head>
    <body>
        <cmwf-section class="cmwf-section">
            <cmwf-integrated-action id="interventions_action_1">
                <span>Interventions</span>
            </cmwf-integrated-action>
            <cmwf-text-display id="cp_interventions"></cmwf-text-display>
        </cmwf-section>

        <cmwf-section class="cmwf-section">
            <cmwf-integrated-action id="goals_action_1">
                <span>Goals</span>
            </cmwf-integrated-action>
            <cmwf-text-display id="cp_goals"></cmwf-text-display>
        </cmwf-section>

        <div id="formFramePopup" class="popup-modal">
            <div class="modal-content">
                <button class="close" onclick="closeModal()">×</button>
                <h4 id="modalTitle">Care Plan Module</h4>
                <div class="ui-grid-render-container-body">
                    <table id="modalTable">
                        <tbody>
                            <!-- populated dynamically based on section -->
                        </tbody>
                    </table>
                </div>
                <button data-automation-id="button-insert-to-form" class="btn" onclick="commitInsert()">Insert to form</button>
                <button class="btn" onclick="closeModal()">Cancel</button>
            </div>
        </div>

        <script>
            let currentSection = '';
            const insertCalls = { interventions: 0, goals: 0 };
            document.getElementById('interventions_action_1').addEventListener('click', () => {
                currentSection = 'interventions';
                document.getElementById('modalTitle').innerText = 'Interventions';
                document.querySelector('#modalTable tbody').innerHTML = `
                    <tr class="ui-grid-row">
                        <td><input type="checkbox" ng-model="row.isSelected" id="cb_i1" /></td>
                        <td>VITAL SIGN PARAMETERS Notify Physician if: Blood Pressure systolic > 160 or < 100</td>
                    </tr>
                    <tr class="ui-grid-row">
                        <td><input type="checkbox" ng-model="row.isSelected" id="cb_i2" /></td>
                        <td>Assess for disease exacerbation, meds compliance and home safety</td>
                    </tr>
                `;
                document.getElementById('formFramePopup').classList.add('in');
            });

            document.getElementById('goals_action_1').addEventListener('click', () => {
                currentSection = 'goals';
                document.getElementById('modalTitle').innerText = 'Goals';
                document.querySelector('#modalTable tbody').innerHTML = `
                    <tr class="ui-grid-row">
                        <td><input type="checkbox" ng-model="row.isSelected" id="cb_g1" /></td>
                        <td>Patient will have no acute care hospitalizations or ER visits during episode</td>
                    </tr>
                    <tr class="ui-grid-row">
                        <td><input type="checkbox" ng-model="row.isSelected" id="cb_g2" /></td>
                        <td>Patient/Caregiver will demonstrate understanding of emergency plan</td>
                    </tr>
                `;
                document.getElementById('formFramePopup').classList.add('in');
            });

            function closeModal() { document.getElementById('formFramePopup').classList.remove('in'); }
            function commitInsert() {
                insertCalls[currentSection] += 1;
                const cbs = document.querySelectorAll('#modalTable tbody input[type="checkbox"]:checked');
                const texts = [];
                for (const cb of cbs) {
                    texts.push(cb.closest('tr').querySelectorAll('td')[1].innerText);
                }
                if (currentSection === 'interventions') {
                    document.getElementById('cp_interventions').innerText = texts.join('\\n\\n');
                } else if (currentSection === 'goals') {
                    document.getElementById('cp_goals').innerText = texts.join('\\n\\n');
                }
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
                "VITAL SIGN PARAMETERS Notify Physician if: Blood Pressure systolic > 160 or < 100",
                "Assess for disease exacerbation, meds compliance and home safety",
                "Patient will have no acute care hospitalizations or ER visits during episode",
                "Patient/Caregiver will demonstrate understanding of emergency plan",
            ],
            "value": "VITAL SIGN PARAMETERS Notify Physician if: Blood Pressure systolic > 160 or < 100\n\nAssess for disease exacerbation, meds compliance and home safety\n\nPatient will have no acute care hospitalizations or ER visits during episode\n\nPatient/Caregiver will demonstrate understanding of emergency plan",
        }
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(html_page)

        copier.inject_composite_linked_forms(page, source_fields)

        int_text = page.evaluate("() => document.getElementById('cp_interventions').innerText")
        goals_text = page.evaluate("() => document.getElementById('cp_goals').innerText")
        is_modal_open = page.evaluate("() => document.getElementById('formFramePopup').classList.contains('in')")
        insert_calls = page.evaluate("() => insertCalls")
        browser.close()

        assert "Blood Pressure systolic > 160" in int_text
        assert "Assess for disease exacerbation" in int_text
        assert "no acute care hospitalizations" in goals_text
        assert "understanding of emergency plan" in goals_text
        assert insert_calls == {"interventions": 1, "goals": 1}
        assert not is_modal_open


def test_existing_linked_form_list_never_opens_insert_from_template():
    html_page = """
    <html>
    <head>
        <style>
            .popup-modal { display: none; }
            .popup-modal.in { display: block; position: fixed; width: 600px; height: 400px; background: white; }
        </style>
    </head>
    <body>
        <cmwf-cop id="mental_status">
            <a class="cop-title" data-section="mental_status" onclick="openModal()">Mental Status</a>
            <div class="cop-items"></div>
        </cmwf-cop>
        <div id="formFramePopup" class="popup-modal">
            <table><tbody>
                <tr class="ui-grid-row">
                    <td><input type="checkbox" ng-model="row.isSelected" /></td>
                    <td>Patient strengths: motivated learner</td>
                </tr>
            </tbody></table>
            <button id="templateButton" onclick="templateClicks += 1">Insert From Template</button>
            <button data-automation-id="button-insert-to-form" onclick="commitInsert()">Insert to form</button>
        </div>
        <script>
            let templateClicks = 0;
            let insertCalls = 0;
            function openModal() { document.getElementById('formFramePopup').classList.add('in'); }
            function commitInsert() {
                insertCalls += 1;
                const selected = document.querySelector('.ui-grid-row input').checked;
                if (selected) {
                    document.querySelector('#mental_status .cop-items').innerText =
                        'Patient strengths: motivated learner';
                }
                document.getElementById('formFramePopup').classList.remove('in');
            }
        </script>
    </body>
    </html>
    """
    source_fields = {
        "cop_section_mental_status": {
            "kind": "composite",
            "section": "mental_status",
            "title": "Mental Status",
            "items": ["Patient strengths: motivated learner"],
            "value": "Patient strengths: motivated learner",
        }
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(html_page)
        copier.inject_composite_linked_forms(page, source_fields)
        result = page.evaluate("""() => ({
            text: document.querySelector('#mental_status .cop-items').innerText,
            templateClicks,
            insertCalls
        })""")
        browser.close()

    assert "motivated learner" in result["text"]
    assert result["templateClicks"] == 0
    assert result["insertCalls"] == 1


def test_update_button_is_not_used_as_insert_to_form():
    """Only Insert to form may commit; an Update-only modal is cancelled."""
    html_page = """
    <html>
    <head>
        <style>
            .popup-modal { display: none; }
            .popup-modal.in { display: block; position: fixed; top: 0; left: 0; width: 600px; height: 500px; background: white; z-index: 1000; }
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
                <h4>Care Plan Module - Interventions & Goals</h4>
                <button class="btn" ng-click="selectActiveItems($event);">Select Active</button>
                <div class="ui-grid-render-container-body">
                    <table>
                        <tbody>
                            <tr class="ui-grid-row">
                                <td><input type="checkbox" ng-model="row.isSelected" id="cb1" /></td>
                                <td>Instruct patient and family on medication management</td>
                            </tr>
                            <tr class="ui-grid-row">
                                <td><input type="checkbox" ng-model="row.isSelected" id="cb2" /></td>
                                <td>Patient will demonstrate safe transfer technique with walker</td>
                            </tr>
                        </tbody>
                    </table>
                </div>
                <!-- Note: 'Update' button instead of 'Insert to form' -->
                <button class="btn btn-primary" data-automation-id="button-update" onclick="commitUpdate()">Update</button>
                <button class="btn" onclick="closeModal()">Cancel</button>
            </div>
        </div>

        <script>
            function openModal() { document.getElementById('formFramePopup').classList.add('in'); }
            function closeModal() { document.getElementById('formFramePopup').classList.remove('in'); }
            function commitUpdate() {
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
        "cp_interventions": {
            "kind": "text",
            "value": "Instruct patient and family on medication management",
        },
        "cp_goals": {
            "kind": "text",
            "value": "Patient will demonstrate safe transfer technique with walker",
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

        assert result_text == ""
        assert not is_modal_open


def test_e2_interventions_goals_title_with_unrelated_internal_section_id():
    """E2 labels the linked form Interventions/Goals even when its DOM ID changes."""
    html_page = """
    <html><head><style>
        .popup-modal { display:none; }
        .popup-modal.in { display:block; position:fixed; inset:0; background:white; }
    </style></head><body>
        <cmwf-cop id="patient_outcomes_plan_of_care_e2">
            <a class="cop-title" data-section="care_plan_outcomes_481"
               onclick="openModal()">Interventions/Goals</a>
            <div class="cop-items"></div>
        </cmwf-cop>
        <div id="formFramePopup" class="popup-modal">
            <table><tbody>
                <tr class="ui-grid-row"><td><input type="checkbox" ng-model="row.isSelected"></td>
                    <td>Blood Pressure; systolic is greater than 160 mm Hg or less than 100 mm Hg</td></tr>
                <tr class="ui-grid-row"><td><input type="checkbox" ng-model="row.isSelected"></td>
                    <td>To have stable blood pressure and less pain</td></tr>
            </tbody></table>
            <button data-automation-id="button-insert-to-form" onclick="commit()">Insert to form</button>
            <button onclick="closeModal()">Cancel</button>
        </div>
        <script>
            function openModal() { document.getElementById('formFramePopup').classList.add('in'); }
            function closeModal() { document.getElementById('formFramePopup').classList.remove('in'); }
            function commit() {
                const selected = Array.from(document.querySelectorAll('.ui-grid-row input:checked'))
                    .map(cb => cb.closest('tr').querySelectorAll('td')[1].innerText);
                document.querySelector('#patient_outcomes_plan_of_care_e2 .cop-items').innerText =
                    selected.join('\\n\\n');
                closeModal();
            }
        </script>
    </body></html>
    """
    source_fields = {
        "cop_section_other_interventions_goals_v1": {
            "kind": "composite",
            "section": "other_interventions_goals_v1",
            "title": "Other Interventions/Goals",
            "items": [
                "Blood Pressure; systolic is greater than 160 mm Hg or less than 100 mm Hg",
                "To have stable blood pressure and less pain",
            ],
            "value": (
                "Blood Pressure; systolic is greater than 160 mm Hg or less than 100 mm Hg\n"
                "To have stable blood pressure and less pain"
            ),
        }
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(html_page)
        copier.inject_composite_linked_forms(page, source_fields)
        result = page.locator(
            "#patient_outcomes_plan_of_care_e2 .cop-items"
        ).inner_text()
        browser.close()

    assert "systolic is greater than 160" in result
    assert "stable blood pressure and less pain" in result


def test_exact_e1_request_ids_merge_into_exact_e2_combined_request_id():
    source_html = """
    <html><body>
      <cmwf-section>
        <cmwf-integrated-action id="interventionsGoalsRequest">
          <span class="disabled">Interventions/Goals</span>
        </cmwf-integrated-action>
        <cmwf-text-display id="cp_renderedCarePlanRowsA">
          Pain Rating is greater than 6 on 0-10 scale
        </cmwf-text-display>
        <cmwf-integrated-action id="otherInterventionsGoalsRequest">
          <span class="disabled">Other Interventions/Goals</span>
        </cmwf-integrated-action>
        <cmwf-text-display id="cp_renderedCarePlanRowsB">
          Neurological INTERVENTIONS:
          Blood Pressure; systolic is greater than 160 mm Hg or less than 100 mm Hg
          To have stable blood pressure and less pain
        </cmwf-text-display>
      </cmwf-section>
    </body></html>
    """
    target_html = """
    <html><head><style>
      .modal { display:none; }
      .modal.in { display:block; position:fixed; inset:0; background:white; }
    </style></head><body>
      <cmwf-section>
        <cmwf-integrated-action id="combinedInterventionsGoalsRequest">
          <span>Interventions/Goals</span>
        </cmwf-integrated-action>
        <cmwf-text-display id="cp_combinedInterventionsGoals"></cmwf-text-display>
      </cmwf-section>
      <div id="combinedCarePlanDialog" class="modal fade" role="dialog">
        <table><tbody>
          <tr class="ui-grid-row"><td><input type="checkbox" ng-model="row.isSelected"></td>
            <td>Pain Rating is greater than 6 on 0-10 scale</td></tr>
          <tr class="ui-grid-row"><td><input type="checkbox" ng-model="row.isSelected"></td>
            <td>Blood Pressure; systolic is greater than 160 mm Hg or less than 100 mm Hg</td></tr>
          <tr class="ui-grid-row"><td><input type="checkbox" ng-model="row.isSelected"></td>
            <td>To have stable blood pressure and less pain</td></tr>
        </tbody></table>
        <button data-automation-id="button-insert-to-form" onclick="commit()">Insert to form</button>
        <button onclick="closeModal()">Cancel</button>
      </div>
      <script>
        document.getElementById('combinedInterventionsGoalsRequest').addEventListener(
          'click', () => document.getElementById('combinedCarePlanDialog').classList.add('in')
        );
        function closeModal() { document.getElementById('combinedCarePlanDialog').classList.remove('in'); }
        function commit() {
          const selected = Array.from(document.querySelectorAll('.ui-grid-row input:checked'))
            .map(cb => cb.closest('tr').querySelectorAll('td')[1].innerText);
          document.getElementById('cp_combinedInterventionsGoals').innerText =
            selected.join('\\n\\n');
          closeModal();
        }
      </script>
    </body></html>
    """

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        source_page = browser.new_page()
        source_page.set_content(source_html)
        extracted = source_page.evaluate(copier.JS_EXTRACT_FIELDS)

        target_page = browser.new_page()
        target_page.set_content(target_html)
        copier.inject_composite_linked_forms(target_page, extracted)
        result = target_page.locator("#cp_combinedInterventionsGoals").inner_text()
        browser.close()

    assert "Pain Rating is greater than 6" in result
    assert "systolic is greater than 160" in result
    assert "stable blood pressure and less pain" in result


def test_combined_interventions_goals_expands_all_template_groups_before_matching():
    html_page = """
    <html><head><style>
      .popup-modal { display:none; }
      .popup-modal.in { display:block; position:fixed; inset:0; background:white; }
      #insertToTemplatePopup { display:none; position:fixed; inset:20px; background:white; z-index:20; }
      .group-body { display:none; }
      .group-body.open { display:block; }
    </style></head><body>
      <cmwf-section>
        <cmwf-integrated-action id="combinedInterventionsGoalsRequest">
          <span>Interventions/Goals</span>
        </cmwf-integrated-action>
        <cmwf-text-display id="cp_combinedInterventionsGoals"></cmwf-text-display>
      </cmwf-section>
      <div id="formFramePopup" class="popup-modal">
        <button onclick="openTemplate()">Insert From Template</button>
        <table><tbody id="mainRows"><tr class="item-row"><td><input type="checkbox"></td><td></td></tr></tbody></table>
        <button data-automation-id="button-insert-to-form" onclick="commitMain()">Insert to form</button>
        <button onclick="closeMain()">Cancel</button>
        <div id="insertToTemplatePopup" role="dialog">
          <div class="accordion">
          <button class="group-header" aria-expanded="false"
                  onclick="toggleGroup(event, 'vitals', this)">Vital Sign Parameters</button>
          <div id="vitals" class="group-body">
            <label><input type="checkbox"> Blood Pressure; systolic is greater than 160 mm Hg or less than 100 mm Hg</label>
            <label><input type="checkbox"> Pain Rating is greater than __7___ on 0-10 scale</label>
            <label><input type="checkbox"> Pulse is greater than 100 or less than 50</label>
          </div>
          <button class="group-header" aria-expanded="false"
                  onclick="toggleGroup(event, 'goals', this)">Patient Centered Goal</button>
          <div id="goals" class="group-body">
            <label><input type="checkbox"> To have stable blood pressure and less pain</label>
          </div>
          <button class="group-header" aria-expanded="false"
                  onclick="toggleGroup(event, 'respiratory', this)">Respiratory</button>
          <div id="respiratory" class="group-body">
            <label><input type="checkbox"> Assess respiratory status each visit</label>
          </div>
          </div>
          <button onclick="insertTemplate()">Insert to form</button>
          <button onclick="closeTemplate()">Cancel</button>
        </div>
      </div>
      <script>
        document.getElementById('combinedInterventionsGoalsRequest').addEventListener('click', () => {
          document.getElementById('formFramePopup').classList.add('in');
        });
        function openTemplate() { document.getElementById('insertToTemplatePopup').style.display = 'block'; }
        function closeTemplate() { document.getElementById('insertToTemplatePopup').style.display = 'none'; }
        function closeMain() { document.getElementById('formFramePopup').classList.remove('in'); }
        function toggleGroup(event, id, header) {
          if (!event || event.isTrusted !== true) return;
          const rect = header.getBoundingClientRect();
          // Match the live Plan of Care control: only its right-side chevron
          // expands the group; clicking the category label does nothing.
          if (event.clientX < rect.right - 35) return;
          document.querySelectorAll('.group-body').forEach(body => body.classList.remove('open'));
          document.querySelectorAll('.group-header').forEach(node =>
            node.setAttribute('aria-expanded', 'false'));
          document.getElementById(id).classList.add('open');
          header.setAttribute('aria-expanded', 'true');
          window.openedGroups = window.openedGroups || [];
          if (!window.openedGroups.includes(id)) window.openedGroups.push(id);
        }
        function insertTemplate() {
          const selected = Array.from(document.querySelectorAll('#insertToTemplatePopup input:checked'))
            .map(cb => cb.closest('label').innerText.trim());
          document.getElementById('mainRows').innerHTML = selected.map(text =>
            '<tr class="item-row"><td><input type="checkbox"></td><td>' + text + '</td></tr>'
          ).join('');
          closeTemplate();
        }
        function commitMain() {
          const selected = Array.from(document.querySelectorAll('#mainRows input:checked'))
            .map(cb => cb.closest('tr').querySelectorAll('td')[1].innerText);
          document.getElementById('cp_combinedInterventionsGoals').innerText = selected.join('\\n\\n');
          closeMain();
        }
      </script>
    </body></html>
    """
    source_fields = {
        "otherInterventionsGoalsRequest": {
            "kind": "integrated",
            "section": "interventions_goals",
            "title": "Other Interventions/Goals",
            "items": [
                "Blood Pressure; systolic is greater than 160 mm Hg or less than 100 mm Hg",
                "Pain Rating is greater than 6 on 0-10 scale",
                "Pulse is greater than 100 or less than 50",
                "To have stable blood pressure and less pain",
            ],
            "value": (
                "Blood Pressure; systolic is greater than 160 mm Hg or less than 100 mm Hg\n"
                "Pain Rating is greater than 6 on 0-10 scale\n"
                "Pulse is greater than 100 or less than 50\n"
                "To have stable blood pressure and less pain"
            ),
        }
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(html_page)
        copier.inject_composite_linked_forms(page, source_fields)
        result = page.locator("#cp_combinedInterventionsGoals").inner_text()
        opened_groups = page.evaluate("window.openedGroups || []")
        browser.close()

    assert opened_groups == ["vitals", "goals", "respiratory"]
    assert "systolic is greater than 160" in result
    assert "Pain Rating is greater than __7___" in result
    assert "Pulse is greater than 100" in result
    assert "stable blood pressure and less pain" in result
    assert "Assess respiratory status" not in result


def test_combined_interventions_goals_commits_single_modal_template_directly():
    """E2 can render its template accordion directly in formFramePopup."""
    html_page = """
    <html><head><style>
      #formFramePopup { display:none; position:fixed; inset:0; background:white; }
      #formFramePopup.open { display:block; }
      #chooser { display:none; }
      .group-body { display:none; }
      .group-body.open { display:block; }
      #blockingWarning { display:none; position:fixed; inset:0; z-index:100; background:#fff; }
      #blockingWarning.open { display:block; }
    </style></head><body>
      <cmwf-section>
        <cmwf-integrated-action id="combinedInterventionsGoalsRequest">
          <span>Interventions/Goals</span>
        </cmwf-integrated-action>
        <cmwf-text-display id="cp_combinedInterventionsGoals"></cmwf-text-display>
      </cmwf-section>
      <div id="formFramePopup">
        <div id="emptyList"><button onclick="openTemplate()">Insert From Template</button></div>
        <div id="chooser">
          <button class="group-header" aria-expanded="false"
                  onclick="toggleGroup(event, this)">Vital Sign Parameters</button>
          <div class="group-body">
            <label><input type="checkbox"> Pulse is greater than 100 or less than 50</label>
            <label><input type="checkbox"> Unrelated template choice</label>
          </div>
          <button id="directInsert" onclick="requestCommit()">Insert to form</button>
          <button>Cancel</button>
        </div>
        <div id="blockingWarning" class="modal-dialog">
          <div class="modal-content">
            <div role="dialog">
              <p>Warning: the following warnings were found.</p>
              <p>ENDOCRINE GOALS</p>
              <p>ENDOCRINE INTERVENTIONS</p>
              <p>Do you want to continue?</p>
              <button onclick="dismissWarning(event)">OK</button>
            </div>
          </div>
        </div>
      </div>
      <script>
        document.getElementById('combinedInterventionsGoalsRequest').addEventListener('click', () => {
          document.getElementById('formFramePopup').classList.add('open');
        });
        function openTemplate() {
          document.getElementById('emptyList').style.display = 'none';
          document.getElementById('chooser').style.display = 'block';
        }
        function toggleGroup(event, header) {
          if (!event || event.isTrusted !== true) return;
          const rect = header.getBoundingClientRect();
          if (event.clientX < rect.right - 35) return;
          const body = header.nextElementSibling;
          body.classList.add('open');
          header.setAttribute('aria-expanded', 'true');
        }
        function commitDirectly() {
          const values = Array.from(document.querySelectorAll('#chooser input:checked'))
            .map(cb => cb.closest('label').innerText.trim());
          document.getElementById('cp_combinedInterventionsGoals').innerText = values.join('\\n');
          document.getElementById('formFramePopup').classList.remove('open');
        }
        function requestCommit() {
          document.getElementById('blockingWarning').classList.add('open');
        }
        function dismissWarning(event) {
          if (!event || event.isTrusted !== true) return;
          document.getElementById('blockingWarning').classList.remove('open');
          window.warningDismissed = true;
          // Live Devero applies the confirmed Plan of Care selection through
          // a delayed Angular update after the warning has disappeared.
          setTimeout(commitDirectly, 6500);
        }
      </script>
    </body></html>
    """
    source_fields = {
        "otherInterventionsGoalsRequest": {
            "kind": "integrated",
            "section": "interventions_goals",
            "title": "Other Interventions/Goals",
            "items": ["Pulse is greater than 100 or less than 50"],
            "value": "Pulse is greater than 100 or less than 50",
        }
    }

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(html_page)
        copier.inject_composite_linked_forms(page, source_fields)
        result = page.locator("#cp_combinedInterventionsGoals").inner_text()
        modal_open = page.locator("#formFramePopup").evaluate(
            "node => node.classList.contains('open')"
        )
        warning_dismissed = page.evaluate("window.warningDismissed === true")
        browser.close()

    assert result == "Pulse is greater than 100 or less than 50"
    assert modal_open is False
    assert warning_dismissed is True


def test_post_insert_warning_is_accepted_on_plan_of_care_page():
    """The warning may live on a different context page than the caller."""
    warning_html = """
    <div class="modal-dialog">
      <h3>Warning</h3>
      <p>The following warnings were found:</p>
      <ul><li>ENDOCRINE GOALS</li><li>ENDOCRINE INTERVENTIONS</li></ul>
      <p>Do you want to continue?</p>
      <button onclick="acceptWarning(event)">OK</button>
    </div>
    <script>
      function acceptWarning(event) {
        if (!event || event.isTrusted !== true) return;
        document.querySelector('.modal-dialog').remove();
        window.warningAccepted = true;
      }
    </script>
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        target_page = context.new_page()
        target_page.set_content("<p>Target note</p>")
        plan_page = context.new_page()
        plan_page.set_content(warning_html)

        accepted = copier._accept_post_insert_warning(target_page, timeout_ms=1500)
        warning_accepted = plan_page.evaluate("window.warningAccepted === true")
        browser.close()

    assert accepted is True
    assert warning_accepted is True
