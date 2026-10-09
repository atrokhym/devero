import importlib.util
import os
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright


os.environ.setdefault("DEVERO_LOG_FILE", "/tmp/devero-oasis-target-first-test.log")
SPEC = importlib.util.spec_from_file_location(
    "devero_copier_oasis",
    Path(__file__).resolve().parent.parent / "devero_copier_oasis.py",
)
copier = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(copier)


def _source_field(
    *,
    section="future_renal_therapy",
    action_id="future_renal_therapy_action_9",
    title="Legacy Renal Plan",
    item="Monitor fluid balance daily",
):
    return {
        "source_unknown_action": {
            "kind": "integrated",
            "section": section,
            "action_id": action_id,
            "title": title,
            "items": [item],
            "value": item,
            "keys": [action_id, section],
        }
    }


def test_generic_injector_skips_linked_displays():
    html = """
    <cmwf-section>
      <cmwf-integrated-action id="vitalsignparametershh_summary_of_problems_request">
        <span>Vital Signs</span>
      </cmwf-integrated-action>
      <cmwf-text-display id="cp_vitalsignparametershh_summary_of_problems"></cmwf-text-display>
      <cmwf-cop id="painhh"></cmwf-cop>
      <input id="ordinary_editable_field">
    </cmwf-section>
    """
    fields = {
        "cp_vitalsignparametershh_summary_of_problems": {
            "kind": "text",
            "value": "VITAL SIGN PARAMETERS Notify Physician if: copied source text",
        },
        "painhh": {
            "kind": "text",
            "value": "Pain Rating is greater than 6 on 0-10 scale",
        },
        "ordinary_editable_field": {
            "kind": "text",
            "value": "ordinary copied value",
        },
    }

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(html)
        stats = page.evaluate(copier.JS_INJECT_FIELDS, fields)
        result = page.evaluate(
            """() => ({
                vital: document.querySelector('#cp_vitalsignparametershh_summary_of_problems').textContent,
                pain: document.querySelector('#painhh').textContent,
                ordinary: document.querySelector('#ordinary_editable_field').value,
            })"""
        )
        browser.close()

    assert result == {
        "vital": "",
        "pain": "",
        "ordinary": "ordinary copied value",
    }
    assert stats["updated"] == 1


def test_source_extraction_catalogs_unknown_request_id_and_payload():
    html = """
    <cmwf-section>
      <cmwf-integrated-action id="futureRenalTherapyRequest">
        <span>Open New Plan</span>
      </cmwf-integrated-action>
      <cmwf-text-display id="cp_future_renal_therapy">
        Monitor fluid balance daily
      </cmwf-text-display>
    </cmwf-section>
    """
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(html)
        extracted = page.evaluate(copier.JS_EXTRACT_FIELDS)
        browser.close()

    records = copier._source_linked_records(extracted)
    assert any(
        record["section"] == "future_renal_therapy"
        and "Monitor fluid balance daily" in record["value"]
        for record in records
    )


def test_target_binding_resolves_renamed_source_action_suffix():
    fields = _source_field()
    records = copier._source_linked_records(fields)
    entry = {
        "kind": "integrated",
        "section": "future_renal_therapy",
        "actionId": "futureRenalTherapyRequest",
        "title": "Open New Plan",
    }

    resolved = copier._resolve_source_for_target(entry, records, {})

    assert resolved is not None
    assert resolved["items"] == ["Monitor fluid balance daily"]


def test_compact_id_matches_underscored_source_id():
    fields = _source_field(
        section="patient_centered_goal",
        action_id="patient_centered_goal_action_1",
        title="Legacy Workflow",
        item="Patient will state one personal recovery goal",
    )
    records = copier._source_linked_records(fields)
    entry = {
        "kind": "integrated",
        "section": "patientcenteredgoalhh",
        "actionId": "patientcenteredgoalhh_summary_of_problems_request",
        "title": "Patient Centered Goals",
    }

    resolved = copier._resolve_source_for_target(entry, records, {})

    assert resolved is not None
    assert resolved["items"] == ["Patient will state one personal recovery goal"]


def test_source_extraction_ignores_clear_only_summary_of_problems_action():
    html = """
    <cmwf-section>
      <cmwf-integrated-action
        id="painhh_summary_of_problems_request">
        <span>Pain</span>
      </cmwf-integrated-action>
      <cmwf-text-display id="cp_painhh">Pain Clear</cmwf-text-display>
      <cmwf-integrated-action id="clear_cp_section">
        <span>Clear</span>
      </cmwf-integrated-action>
    </cmwf-section>
    """
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(html)

        extracted = page.evaluate(copier.JS_EXTRACT_FIELDS)
        records = copier._source_linked_records(extracted)
        browser.close()

    assert records == []


def test_source_extraction_ignores_oasis_item_details_action():
    html = """
    <cmwf-section>
      <cmwf-integrated-action id="mDetails">
        <span>(A1005)</span>
      </cmwf-integrated-action>
      <cmwf-text-display id="a1005_item_text">
        Informational OASIS item text that must not become linked-form data
      </cmwf-text-display>
      <label><input type="checkbox"> Example response</label>
    </cmwf-section>
    """
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(html)

        extracted = page.evaluate(copier.JS_EXTRACT_FIELDS)
        records = copier._source_linked_records(extracted)
        sections = copier._source_composite_sections(extracted)
        browser.close()

    assert records == []
    assert "m_details" not in sections


def test_source_extraction_associates_items_inside_clear_output_cop():
    html = """
    <cmwf-section>
      <cmwf-integrated-action
        id="painhh_summary_of_problems_request">
        <span>Pain</span>
      </cmwf-integrated-action>
      <cmwf-cop id="clear_cp_section">
        <a class="cop-title">Clear</a>
        <div class="cop-items">
          <div class="copElement">Report pain above the ordered threshold</div>
        </div>
      </cmwf-cop>
    </cmwf-section>
    """
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(html)

        extracted = page.evaluate(copier.JS_EXTRACT_FIELDS)
        records = copier._source_linked_records(extracted)
        browser.close()

    assert len(records) == 1
    assert records[0]["section"] == "painhh"
    assert records[0]["items"] == ["Report pain above the ordered threshold"]


def test_source_catalog_defensively_rejects_clear_action_with_foreign_payload():
    fields = {
        "clear_action": {
            "kind": "integrated",
            "section": "clear_cp_section",
            "action_id": "clear_cp_section",
            "title": "Clear",
            "items": ["Foreign section content accidentally associated by the DOM"],
            "value": "Foreign section content accidentally associated by the DOM",
        }
    }

    assert copier._source_linked_records(fields) == []
    assert copier._source_composite_sections(fields) == {}


def test_target_modal_items_resolve_completely_renamed_source():
    fields = _source_field(
        section="heart_care",
        action_id="heartCareAction",
        title="Legacy Heart Workflow",
        item="Record resting pulse before medication",
    )
    records = copier._source_linked_records(fields)
    entry = {
        "kind": "integrated",
        "section": "cardiac_support",
        "actionId": "cardiacSupportRequest",
        "title": "Current Cardiac Workflow",
    }

    resolved = copier._resolve_source_for_target(
        entry,
        records,
        {},
        target_items=["Record resting pulse before medication"],
    )

    assert resolved is not None
    assert resolved["action_id"] == "heartCareAction"


def test_e1_split_actions_resolve_to_one_e2_target_and_mark_both_sources():
    fields = {
        "legacy_interventions": {
            "kind": "integrated",
            "section": "interventions_goals",
            "action_id": "interventionsGoalsRequest",
            "title": "Legacy first action",
            "items": ["Teach safe transfer technique"],
            "value": "Teach safe transfer technique",
        },
        "legacy_other_interventions": {
            "kind": "integrated",
            "section": "interventions_goals",
            "action_id": "otherInterventionsGoalsRequest",
            "title": "Legacy second action",
            "items": ["Patient will demonstrate safe transfer"],
            "value": "Patient will demonstrate safe transfer",
        },
    }
    records = copier._source_linked_records(fields)
    sections = copier._source_composite_sections(fields)
    entry = {
        "kind": "integrated",
        "section": "interventions_goals",
        "actionId": "combinedInterventionsGoalsRequest",
        "title": "Current combined action",
    }

    resolved = copier._resolve_source_for_target(entry, records, sections)

    assert resolved is not None
    assert set(resolved["items"]) == {
        "Teach safe transfer technique",
        "Patient will demonstrate safe transfer",
    }
    assert set(resolved["matched_record_keys"]) == {
        record["record_key"] for record in records
    }


def test_unknown_target_integrated_action_is_discovered_and_populated():
    html = """
    <style>
      #formFramePopup { display: none; }
      #formFramePopup.open { display: block; }
    </style>
    <cmwf-section>
      <cmwf-integrated-action id="futureRenalTherapyRequest">
        <span>Open New Plan</span>
      </cmwf-integrated-action>
      <cmwf-text-display id="cp_future_renal_therapy"></cmwf-text-display>
    </cmwf-section>
    <div id="formFramePopup">
      <table><tbody><tr class="ui-grid-row">
        <td><input type="checkbox" ng-model="row.isSelected"></td>
        <td>Monitor fluid balance daily</td>
      </tr></tbody></table>
      <button data-automation-id="button-insert-to-form">Insert to form</button>
      <button class="cancel">Cancel</button>
    </div>
    <script>
      const modal = document.getElementById('formFramePopup');
      document.getElementById('futureRenalTherapyRequest').addEventListener(
        'click', () => modal.classList.add('open')
      );
      modal.querySelector('.cancel').addEventListener(
        'click', () => modal.classList.remove('open')
      );
      modal.querySelector('[data-automation-id="button-insert-to-form"]')
        .addEventListener('mousedown', () => {
          const selected = modal.querySelector('input[type="checkbox"]').checked;
          if (!selected) return;
          document.getElementById('cp_future_renal_therapy').innerText =
            'Monitor fluid balance daily';
          modal.classList.remove('open');
        });
    </script>
    """
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(html)

        state = copier.inject_composite_linked_forms(page, _source_field())

        committed = page.locator("#cp_future_renal_therapy").inner_text()
        browser.close()

    assert committed == "Monitor fluid balance daily"
    assert state["discovered_targets"] == 1
    assert state["source_record_keys"] == state["matched_source_keys"]


def test_late_hydrating_page_one_action_is_discovered_clicked_and_populated():
    html = """
    <style>
      #formFramePopup { display: none; }
      #formFramePopup.open { display: block; }
    </style>
    <cmwf-section id="late-section">
      <cmwf-text-display id="cp_future_renal_therapy"></cmwf-text-display>
    </cmwf-section>
    <div id="formFramePopup">
      <table><tbody><tr class="ui-grid-row">
        <td><input type="checkbox" ng-model="row.isSelected"></td>
        <td>Monitor fluid balance daily</td>
      </tr></tbody></table>
      <button data-automation-id="button-insert-to-form">Insert to form</button>
      <button class="cancel">Cancel</button>
    </div>
    <script>
      const modal = document.getElementById('formFramePopup');
      window.openCount = 0;
      setTimeout(() => {
        const action = document.createElement('cmwf-integrated-action');
        action.id = 'futureRenalTherapyRequest';
        action.innerHTML = '<span>Open New Plan</span>';
        action.addEventListener('click', () => {
          window.openCount++;
          modal.classList.add('open');
        });
        document.getElementById('late-section').prepend(action);
      }, 650);
      modal.querySelector('.cancel').addEventListener(
        'click', () => modal.classList.remove('open')
      );
      modal.querySelector('[data-automation-id="button-insert-to-form"]')
        .addEventListener('mousedown', () => {
          if (!modal.querySelector('input[type="checkbox"]').checked) return;
          document.getElementById('cp_future_renal_therapy').innerText =
            'Monitor fluid balance daily';
          modal.classList.remove('open');
        });
    </script>
    """
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(html)

        state = copier.inject_composite_linked_forms(page, _source_field())

        open_count = page.evaluate("window.openCount")
        committed = page.locator("#cp_future_renal_therapy").inner_text()
        browser.close()

    assert open_count > 0
    assert committed == "Monitor fluid balance daily"
    assert state["discovered_targets"] == 1
    assert state["source_record_keys"] == state["matched_source_keys"]


def test_hidden_module_rows_do_not_preserve_an_empty_target_action():
    html = """
    <style>
      #formFramePopup { display: none; }
      #formFramePopup.open { display: block; }
    </style>
    <cmwf-section>
      <cmwf-integrated-action id="futureRenalTherapyRequest">
        <span>Open New Plan</span>
      </cmwf-integrated-action>
      <cmwf-text-display id="cp_future_renal_therapy"></cmwf-text-display>
      <div id="formFramePopup">
        <cmwf-text-display id="future_renal_therapy_cached_module">
          Monitor fluid balance daily
        </cmwf-text-display>
        <table><tbody><tr class="ui-grid-row">
          <td><input type="checkbox" ng-model="row.isSelected"></td>
          <td>Monitor fluid balance daily</td>
        </tr></tbody></table>
        <button data-automation-id="button-insert-to-form">Insert to form</button>
        <button class="cancel">Cancel</button>
      </div>
    </cmwf-section>
    <script>
      const modal = document.getElementById('formFramePopup');
      window.openCount = 0;
      document.getElementById('futureRenalTherapyRequest').addEventListener(
        'click', () => {
          window.openCount++;
          modal.classList.add('open');
        }
      );
      modal.querySelector('.cancel').addEventListener(
        'click', () => modal.classList.remove('open')
      );
      modal.querySelector('[data-automation-id="button-insert-to-form"]')
        .addEventListener('mousedown', () => {
          if (!modal.querySelector('input[type="checkbox"]').checked) return;
          document.getElementById('cp_future_renal_therapy').innerText =
            'Monitor fluid balance daily';
          modal.classList.remove('open');
        });
    </script>
    """
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(html)

        state = copier.inject_composite_linked_forms(page, _source_field())

        open_count = page.evaluate("window.openCount")
        committed = page.locator("#cp_future_renal_therapy").inner_text()
        browser.close()

    assert open_count > 0
    assert committed == "Monitor fluid balance daily"
    assert state["source_record_keys"] == state["matched_source_keys"]


def test_structured_committed_cop_items_are_still_preserved():
    html = """
    <cmwf-section>
      <cmwf-integrated-action id="futureRenalTherapyRequest">
        <span>Open New Plan</span>
      </cmwf-integrated-action>
      <cmwf-cop id="clear_cp_section">
        <a class="cop-title">Clear</a>
        <div class="cop-items">
          <div class="copElement">Monitor fluid balance daily</div>
        </div>
      </cmwf-cop>
    </cmwf-section>
    <script>
      window.openCount = 0;
      document.getElementById('futureRenalTherapyRequest').addEventListener(
        'click', () => window.openCount++
      );
    </script>
    """
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(html)

        state = copier.inject_composite_linked_forms(page, _source_field())

        open_count = page.evaluate("window.openCount")
        browser.close()

    assert open_count == 0
    assert state["source_record_keys"] == state["matched_source_keys"]


def test_vital_sign_rows_with_target_dates_are_trusted_selected():
    first = "Notify when systolic pressure exceeds the ordered threshold"
    second = "Notify when pulse is outside the ordered range"
    html = f"""
    <div id="formFramePopup" style="display:block">
      <table><tbody>
        <tr class="ui-grid-row">
          <td><input type="checkbox" ng-model="row.isSelected"></td>
          <td>{first}<br>07/02/2026</td>
        </tr>
        <tr class="ui-grid-row">
          <td><input type="checkbox" ng-model="row.isSelected"></td>
          <td>{second}<br>07/02/2026</td>
        </tr>
      </tbody></table>
    </div>
    """
    source_items = [
        f"{first} Start Effective Date: 07/02/2026",
        f"{second} Start Effective Date: 07/02/2026",
    ]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(html)

        selection = copier._select_visible_template_matches(
            page, "\n".join(source_items), source_items
        )
        checked = page.locator(
            '#formFramePopup input[type="checkbox"]:checked'
        ).count()
        browser.close()

    assert selection == {"candidates": 2, "selected": 2}
    assert checked == 2


def test_populated_target_is_preserved_without_opening_linked_form():
    html = """
    <cmwf-section>
      <cmwf-integrated-action id="futureRenalTherapyRequest">
        <span>Open New Plan</span>
      </cmwf-integrated-action>
      <cmwf-text-display id="cp_future_renal_therapy">
        Existing target plan
      </cmwf-text-display>
    </cmwf-section>
    <script>
      window.openCount = 0;
      document.getElementById('futureRenalTherapyRequest').addEventListener(
        'click', () => window.openCount++
      );
    </script>
    """
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(html)

        state = copier.inject_composite_linked_forms(page, _source_field())

        open_count = page.evaluate("window.openCount")
        committed = page.locator("#cp_future_renal_therapy").inner_text()
        browser.close()

    assert open_count == 0
    assert committed.strip() == "Existing target plan"
    assert state["source_record_keys"] == state["matched_source_keys"]


def test_populated_target_inside_frame_is_discovered_and_preserved():
    source = _source_field(
        section="future_infusion",
        action_id="future_infusion_action_2",
        title="Legacy Infusion",
        item="Existing infusion instructions",
    )
    frame_html = """
      <cmwf-section>
        <cmwf-integrated-action id="futureInfusionRequest">
          <span>Current Infusion Workflow</span>
        </cmwf-integrated-action>
        <cmwf-text-display id="cp_future_infusion">
          Existing infusion instructions
        </cmwf-text-display>
      </cmwf-section>
      <script>
        window.openCount = 0;
        document.getElementById('futureInfusionRequest').addEventListener(
          'click', () => window.openCount++
        );
      </script>
    """
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content("<iframe></iframe>")
        frame = page.frames[1]
        frame.set_content(frame_html)

        state = copier.inject_composite_linked_forms(page, source)

        open_count = frame.evaluate("window.openCount")
        browser.close()

    assert open_count == 0
    assert state["discovered_targets"] == 1
    assert state["source_record_keys"] == state["matched_source_keys"]


def test_structural_group_discovery_opens_unknown_future_group():
    html = """
    <style>
      #future-body { display: none; }
      #future-body.open { display: block; }
    </style>
    <div id="formFramePopup" class="popup-modal in">
      <button id="future-header">
        Completely New OASIS Group <span class="chevron-right">&gt;</span>
      </button>
      <div id="future-body">
        <label><input type="checkbox">A future template choice</label>
      </div>
    </div>
    <script>
      document.getElementById('future-header').addEventListener('click', () => {
        document.getElementById('future-body').classList.add('open');
      });
    </script>
    """
    opened_labels = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(html)

        expanded = copier._expand_template_groups(
            page, lambda label: opened_labels.append(label)
        )
        visible = page.locator("#future-body").is_visible()
        browser.close()

    assert expanded == 1
    assert visible
    assert opened_labels == ["Completely New OASIS Group >"]


def test_summary_of_problems_flattened_clear_chrome_is_empty_and_not_pseudo_linked_form():
    action_specs = [
        (
            "patientcenteredgoalhh_summary_of_problems_request",
            "Patient Centered Goals",
            "patient_centered_goal",
            "Patient will state one personal recovery goal",
        ),
        (
            "vitalsignparametershh_summary_of_problems_request",
            "Vital Signs",
            "vital_sign_parameters",
            "Report readings outside ordered parameters",
        ),
        (
            "painhh_summary_of_problems_request",
            "Pain",
            "pain",
            "Report pain above the ordered threshold",
        ),
    ]
    target_sections = []
    source_fields = {}
    for index, (target_id, title, source_section, source_item) in enumerate(
        action_specs
    ):
        target_stem = copier._linked_action_stem(target_id)
        target_sections.append(
            f"""
            <cmwf-section>
              <cmwf-integrated-action id="{target_id}">
                <span>{title}</span>
              </cmwf-integrated-action>
              <cmwf-text-display id="cp_{target_stem}">{title} Clear</cmwf-text-display>
              <cmwf-integrated-action id="clear_cp_section">
                <span>Clear</span>
              </cmwf-integrated-action>
            </cmwf-section>
            """
        )
        source_fields[f"source_{index}"] = {
            "kind": "integrated",
            "section": source_section,
            "action_id": f"{source_section}_action_1",
            "title": f"Legacy {title}",
            "items": [source_item],
            "value": source_item,
            "keys": [source_section],
        }

    html = (
        "".join(target_sections)
        + """
        <script>
          window.openCounts = {};
          document.querySelectorAll('cmwf-integrated-action').forEach(action => {
            window.openCounts[action.id] = 0;
            action.addEventListener('click', () => window.openCounts[action.id]++);
          });
        </script>
        """
    )
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(html)

        state = copier.inject_composite_linked_forms(page, source_fields)

        open_counts = page.evaluate("window.openCounts")
        browser.close()

    assert state["discovered_targets"] == len(action_specs)
    # These fixtures deliberately count clicks without opening/committing a
    # modal, so their populated source records must remain unresolved.
    assert state["source_record_keys"] != state["matched_source_keys"]
    assert all(open_counts[target_id] > 0 for target_id, *_ in action_specs)
    assert open_counts["clear_cp_section"] == 0


def test_oasis_item_details_action_is_not_discovered_or_clicked(capsys):
    html = """
    <cmwf-section>
      <cmwf-integrated-action id="mDetails"><span>(A1005)</span></cmwf-integrated-action>
      <label><input type="checkbox"> Example response</label>
    </cmwf-section>
    <cmwf-section>
      <cmwf-integrated-action id="futureRenalTherapyRequest">
        <span>Open New Plan</span>
      </cmwf-integrated-action>
      <cmwf-text-display id="cp_future_renal_therapy"></cmwf-text-display>
    </cmwf-section>
    <script>
      window.openCounts = { details: 0, linked: 0 };
      document.getElementById('mDetails').addEventListener(
        'click', () => window.openCounts.details++
      );
      document.getElementById('futureRenalTherapyRequest').addEventListener(
        'click', () => window.openCounts.linked++
      );
    </script>
    """
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(html)

        state = copier.inject_composite_linked_forms(page, _source_field())
        open_counts = page.evaluate("window.openCounts")
        browser.close()

    output = capsys.readouterr().out
    attempt_lines = [line for line in output.splitlines() if "Trying to open linked form" in line]
    assert state["discovered_targets"] == 1
    assert open_counts == {"details": 0, "linked": 2}
    assert any("Open New Plan" in line for line in attempt_lines)
    assert all("A1005" not in line and "mDetails" not in line for line in attempt_lines)


def test_post_insert_error_clicks_ok_then_cancel_and_never_reports_success(capsys):
    html = """
    <style>
      #formFramePopup { display: none; }
      #formFramePopup.in { display: block; }
      #insertError { display: none; }
      #insertError.open { display: block; }
    </style>
    <cmwf-cop id="safety_measures">
      <a class="cop-title" data-section="safety_measures" onclick="openLinked()">
        Safety Measures
      </a>
      <div class="cop-items"></div>
    </cmwf-cop>
    <div id="formFramePopup">
      <table><tbody>
        <tr class="item-row">
          <td><input type="checkbox"></td><td>Clear Pathways</td>
        </tr>
      </tbody></table>
      <button onclick="showInsertError()">Insert to form</button>
      <button onclick="cancelLinked()">Cancel</button>
      <div id="insertError" class="modal-dialog error-popup" role="dialog">
        <h4>Error</h4>
        <p>The following errors were found:</p>
        <ul><li>Required related section</li></ul>
        <p>The following warnings were found:</p>
        <ul><li>Related warning</li></ul>
        <button onclick="dismissInsertError()">OK</button>
      </div>
    </div>
    <script>
      window.errorOkClicks = 0;
      window.cancelClicks = 0;
      function openLinked() {
        document.getElementById('formFramePopup').classList.add('in');
      }
      function showInsertError() {
        document.getElementById('insertError').classList.add('open');
      }
      function dismissInsertError() {
        window.errorOkClicks++;
        document.getElementById('insertError').classList.remove('open');
      }
      function cancelLinked() {
        window.cancelClicks++;
        document.getElementById('formFramePopup').classList.remove('in');
      }
    </script>
    """
    source = {
        "source_safety": {
            "kind": "composite",
            "section": "safety_measures",
            "title": "Safety Measures",
            "items": ["Clear Pathways"],
            "value": "Clear Pathways",
        }
    }
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(html)

        state = copier.inject_composite_linked_forms(page, source)
        clicks = page.evaluate(
            "() => ({errorOk: window.errorOkClicks, cancel: window.cancelClicks})"
        )
        modal_open = copier._linked_form_modal_is_open(page)
        target_text = page.locator("#safety_measures .cop-items").inner_text()
        browser.close()

    output = capsys.readouterr().out
    assert clicks == {"errorOk": 1, "cancel": 1}
    assert not modal_open
    assert target_text == ""
    assert state["matched_source_keys"] == set()
    assert state["intentionally_skipped_source_keys"] == set()
    assert "Post-insert error detected" in output
    assert "Devero insert error: Required related section" in output
    assert "Linked form was cancelled after the rejected insert" in output
    assert "Inserted 1 source-matching row" not in output
    assert copier._report_unresolved_linked_sources(state) == state["source_record_keys"]


def test_insert_to_form_uses_one_trusted_click_without_forcing_modal_closed():
    html = """
    <div id="formFramePopup" class="popup-modal in">
      <button data-automation-id="button-insert-to-form"
              ng-mousedown="carePlanModuleController.insertIntoForm($event, false)">
        Insert to form
      </button>
    </div>
    <script>
      window.pointerCommits = 0;
      window.directControllerCommits = 0;
      document.querySelector('[data-automation-id="button-insert-to-form"]')
        .addEventListener('mousedown', event => {
          if (event.isTrusted) window.pointerCommits++;
        });
      window.angular = {
        element: () => ({
          scope: () => ({
            carePlanModuleController: {
              insertIntoForm: () => window.directControllerCommits++
            }
          })
        })
      };
    </script>
    """
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(html)

        result = copier._click_insert_to_form(page)
        clicks = page.evaluate(
            "() => ({pointer: window.pointerCommits, direct: window.directControllerCommits})"
        )
        modal_open = copier._linked_form_modal_is_open(page)
        browser.close()

    assert result == "attempted"
    assert clicks == {"pointer": 1, "direct": 0}
    assert modal_open


def test_target_save_uses_a_trusted_playwright_click():
    html = """
    <button id="save">Save</button>
    <script>
      window.saveClicks = 0;
      window.trustedSaveClicks = 0;
      document.getElementById('save').addEventListener('click', event => {
        window.saveClicks++;
        if (event.isTrusted) window.trustedSaveClicks++;
      });
    </script>
    """
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(html)

        copier._click_target_save(page, 1)
        clicks = page.evaluate(
            "() => ({all: window.saveClicks, trusted: window.trustedSaveClicks})"
        )
        browser.close()

    assert clicks == {"all": 1, "trusted": 1}


def test_post_save_verification_rejects_an_empty_linked_section(monkeypatch):
    class FakePage:
        pass

    monkeypatch.setattr(copier, "wait_for_form_inputs", lambda *_args, **_kwargs: 1)
    monkeypatch.setattr(
        copier, "_wait_for_target_linked_section", lambda *_args, **_kwargs: ""
    )
    monkeypatch.setattr(copier, "go_to_next_page", lambda *_args: False)

    with pytest.raises(RuntimeError, match="linked content was not persisted"):
        copier._verify_persisted_linked_sections(
            FakePage(),
            [{"pageNum": 1, "title": "Goals/Interventions"}],
        )


def test_template_insert_retries_a_continuation_warning_until_it_closes():
    html = """
    <style>
      #formFramePopup { display: block; }
      #continueWarning { display: none; }
      #continueWarning.open { display: block; }
    </style>
    <div id="formFramePopup" class="popup-modal in">
      <button onclick="showWarning()">Insert to form</button>
    </div>
    <div id="continueWarning" class="sweet-alert">
      <p>The following warnings were found. Do you want to continue?</p>
      <button class="confirm" onclick="confirmWarning()">OK</button>
    </div>
    <script>
      window.confirmClicks = 0;
      function showWarning() {
        document.getElementById('continueWarning').classList.add('open');
      }
      function confirmWarning() {
        window.confirmClicks++;
        if (window.confirmClicks === 2) {
          document.getElementById('continueWarning').classList.remove('open');
        }
      }
    </script>
    """
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(html)

        inserted = copier._click_template_insert(page)
        confirm_clicks = page.evaluate("window.confirmClicks")
        browser.close()

    assert inserted
    assert confirm_clicks == 2


def test_target_commit_verification_never_reads_populated_source_tab():
    entry = {
        "kind": "composite",
        "section": "safety_measures",
        "elementId": "safety_measures",
        "dataSection": "safety_measures",
        "title": "Safety Measures",
        "frameIndex": 0,
    }
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        source_page = context.new_page()
        source_page.set_content("""
          <cmwf-cop id="safety_measures">
            <a class="cop-title" data-section="safety_measures">Safety Measures</a>
            <div class="cop-items"><div class="copElement">Populated source row</div></div>
          </cmwf-cop>
        """)
        target_page = context.new_page()
        target_page.set_content("""
          <cmwf-cop id="safety_measures">
            <a class="cop-title" data-section="safety_measures">Safety Measures</a>
            <div class="cop-items"></div>
          </cmwf-cop>
        """)

        result = copier._target_linked_section_text(target_page, entry)
        browser.close()

    assert result == ""


def test_unresolved_populated_source_is_reported_for_review(capsys):
    state = {
        "source_record_keys": {"integrated|painhh|private clinical text"},
        "source_record_sections": {"integrated|painhh|private clinical text": "painhh"},
        "matched_source_keys": set(),
        "discovered_targets": 3,
    }

    assert copier._report_unresolved_linked_sources(state) == state["source_record_keys"]

    output = capsys.readouterr().out
    assert "painhh" in output
    assert "private clinical text" not in output
    assert "saving draft for RN review" in output


def test_source_only_linked_sections_do_not_block_a_resolved_target_schema():
    state = {
        "source_record_keys": {"mapped-source", "source-only"},
        "applicable_source_keys": {"mapped-source"},
        "matched_source_keys": {"mapped-source"},
        "intentionally_skipped_source_keys": set(),
        "discovered_targets": 1,
    }

    assert copier._report_unresolved_linked_sources(state) == set()


def test_failed_applicable_linked_section_is_reported():
    state = {
        "source_record_keys": {"mapped-source", "source-only"},
        "applicable_source_keys": {"mapped-source"},
        "matched_source_keys": set(),
        "intentionally_skipped_source_keys": set(),
        "discovered_targets": 1,
    }

    assert copier._report_unresolved_linked_sources(state) == {"mapped-source"}


def test_final_save_continues_with_unresolved_linked_source(monkeypatch, capsys):
    class FakeFrame:
        url = "https://example.test/target-frame"

        def evaluate(self, _script, _fields):
            return {"updated": 1, "skipped": 0, "visited": 2, "matched": 1}

    class FakePage:
        def __init__(self):
            self.frames = [FakeFrame()]
            self.save_clicked = False

        def wait_for_timeout(self, _milliseconds):
            pass

        def evaluate(self, _script):
            self.save_clicked = True
            return True

    page = FakePage()
    monkeypatch.setattr(copier, "wait_for_form_inputs", lambda *_args, **_kwargs: 15)
    monkeypatch.setattr(
        copier,
        "_source_linked_records",
        lambda _fields: [{"record_key": "unresolved-source-record"}],
    )
    monkeypatch.setattr(
        copier,
        "inject_composite_linked_forms",
        lambda _page, _fields, linked_state: linked_state,
    )
    monkeypatch.setattr(copier, "go_to_next_page", lambda *_args: False)
    monkeypatch.setattr(copier, "handle_warning_popups", lambda _page: None)
    monkeypatch.setattr(copier, "_click_target_save", lambda *_args: setattr(page, "save_clicked", True))
    monkeypatch.setattr(copier, "_reopen_saved_target", lambda *_args: page)
    monkeypatch.setattr(copier, "_verify_persisted_linked_sections", lambda *_args: None)

    _, _, review_sections = copier.inject_all_pages_target(page, {}, save_context={})

    output = capsys.readouterr().out
    assert page.save_clicked
    assert review_sections == ["unknown"]
    assert "saving draft for RN review" in output
    assert "Target note Save verified" in output


def test_unresolved_source_is_reported_when_target_action_does_not_open():
    html = """
    <cmwf-section>
      <cmwf-integrated-action id="futureRenalTherapyRequest">
        <span>Open New Plan</span>
      </cmwf-integrated-action>
      <cmwf-text-display id="cp_future_renal_therapy"></cmwf-text-display>
    </cmwf-section>
    """
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(html)

        state = copier.inject_composite_linked_forms(page, _source_field())

        browser.close()

    assert state["source_record_keys"] != state["matched_source_keys"]
    assert copier._report_unresolved_linked_sources(state) == state["source_record_keys"]


def test_cancel_only_empty_e2_linked_form_is_an_intentional_omission():
    html = """
    <style>
      #formFramePopup { display: none; }
      #formFramePopup.in { display: block; }
    </style>
    <cmwf-section>
      <cmwf-integrated-action id="futureRenalTherapyRequest">
        <span>Open New Plan</span>
      </cmwf-integrated-action>
      <cmwf-text-display id="cp_future_renal_therapy"></cmwf-text-display>
    </cmwf-section>
    <div id="formFramePopup">
      <div class="empty-state">No compatible rows</div>
      <button onclick="closeLinkedForm()">Cancel</button>
    </div>
    <script>
      document.getElementById('futureRenalTherapyRequest').addEventListener(
        'click',
        () => document.getElementById('formFramePopup').classList.add('in')
      );
      function closeLinkedForm() {
        document.getElementById('formFramePopup').classList.remove('in');
      }
    </script>
    """
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(html)

        state = copier.inject_composite_linked_forms(page, _source_field())
        modal_open = copier._linked_form_modal_is_open(page)

        browser.close()

    assert not modal_open
    assert state["matched_source_keys"] == set()
    assert state["intentionally_skipped_source_keys"] == state["source_record_keys"]
    assert copier._report_unresolved_linked_sources(state) == set()
