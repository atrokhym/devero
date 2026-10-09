"""Split concatenated COP source blobs into individual template items."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "copier",
    Path(__file__).resolve().parent.parent / "devero_copier_oasis.py",
)
copier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(copier)


def test_split_advance_directives_single_line_blob():
    blob = (
        "ADVANCE DIRECTIVES: Full code Start Effective Date: 09/25/2025 "
        "Health care Proxy Start Effective Date: 09/25/2025"
    )

    items = copier._split_composite_items(blob)

    names = [item.lower() for item in items]
    assert any("full code" == name for name in names)
    assert any("health care proxy" == name for name in names)
    assert not any("advance directives" == name for name in names)
    assert not any("dnr" in name for name in names)


def test_split_advance_directives_multiline_display_text():
    value = (
        "Advance Directives\n"
        "ADVANCE DIRECTIVES:\n"
        "Full code Start Effective Date: 09/25/2025\n"
        "Health care Proxy Start Effective Date: 09/25/2025"
    )

    items = copier._split_composite_items(value)

    names = [item.lower() for item in items]
    assert "full code" in names
    assert "health care proxy" in names


def test_split_uses_existing_items_and_strips_dates():
    items = copier._split_composite_items(
        value="ADVANCE DIRECTIVES: Full code Start Effective Date: 09/25/2025",
        items=["Full code Start Effective Date: 09/25/2025"],
    )

    assert items == ["Full code"]


def test_source_composite_sections_splits_blob_items():
    extracted = {
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

    sections = copier._source_composite_sections(extracted)
    items = [item.lower() for item in sections["advanced_directives"]["items"]]

    assert "full code" in items
    assert "health care proxy" in items
    assert len(items) == 2


def test_combined_interventions_goals_source_maps_to_separate_target_actions():
    extracted = {
        "cop_section_interventions_goals": {
            "kind": "composite",
            "section": "interventions_goals",
            "title": "Interventions/Goals",
            "items": ["Assess medication compliance", "Patient will remain stable"],
            "value": "Assess medication compliance\n\nPatient will remain stable",
        }
    }

    sections = copier._source_composite_sections(extracted)

    assert sections["interventions"]["items"] == sections["interventions_goals"]["items"]
    assert sections["goals"]["items"] == sections["interventions_goals"]["items"]


def test_plain_interventions_and_goals_displays_map_to_combined_target_cop():
    extracted = {
        "cp_interventions": {
            "kind": "text",
            "value": "Assess medication compliance",
            "keys": ["cp_interventions"],
        },
        "cp_goals": {
            "kind": "text",
            "value": "Patient will remain stable",
            "keys": ["cp_goals"],
        },
    }

    sections = copier._source_composite_sections(extracted)

    assert sections["interventions"]["items"] == ["Assess medication compliance"]
    assert sections["goals"]["items"] == ["Patient will remain stable"]
    assert sections["interventions_goals"]["items"] == [
        "Assess medication compliance",
        "Patient will remain stable",
    ]


def test_other_interventions_goals_merges_into_e2_combined_target_source():
    extracted = {
        "cop_section_interventions_goals": {
            "kind": "composite",
            "section": "interventions_goals",
            "title": "Interventions/Goals",
            "items": ["Interventions/Goals"],
            "value": "Interventions/Goals",
        },
        "cop_section_other_interventions_goals": {
            "kind": "composite",
            "section": "other_interventions_goals",
            "title": "Other Interventions/Goals",
            "items": [
                "VITAL SIGN PARAMETERS Notify Physician if:",
                (
                    "Blood Pressure; systolic is greater than 160 mm Hg or less "
                    "than 100 mm Hg Start Effective Date: 01/10/2026"
                ),
                (
                    "To have stable blood pressure and less pain "
                    "Start Effective Date: 01/10/2026"
                ),
            ],
            "value": (
                "Vital Sign Parameters INTERVENTIONS:\n"
                "VITAL SIGN PARAMETERS Notify Physician if:\n"
                "Blood Pressure; systolic is greater than 160 mm Hg or less than "
                "100 mm Hg Start Effective Date: 01/10/2026\n"
                "Patient Centered Goal GOALS:\n"
                "To have stable blood pressure and less pain "
                "Start Effective Date: 01/10/2026"
            ),
        },
    }

    sections = copier._source_composite_sections(extracted)
    combined = sections["interventions_goals"]
    combined_text = "\n".join(combined["items"]).lower()

    assert "blood pressure; systolic is greater than 160" in combined_text
    assert "to have stable blood pressure and less pain" in combined_text
    assert "interventions/goals" not in [
        item.lower() for item in combined["items"]
    ]
    assert sections["interventions"]["items"] == combined["items"]
    assert sections["goals"]["items"] == combined["items"]


def test_populated_interventions_goals_variants_are_deduplicated_and_merged():
    extracted = {
        "cop_section_interventions_goals": {
            "kind": "composite",
            "section": "interventions_goals",
            "title": "Interventions/Goals",
            "items": ["Assess medication compliance"],
            "value": "Assess medication compliance",
        },
        "cop_section_other_interventions_goals": {
            "kind": "composite",
            "section": "other_interventions_goals",
            "title": "Other Interventions/Goals",
            "items": [
                "Assess medication compliance",
                "Patient will remain stable",
            ],
            "value": "Assess medication compliance\nPatient will remain stable",
        },
    }

    combined = copier._source_composite_sections(extracted)["interventions_goals"]

    assert combined["items"] == [
        "Assess medication compliance",
        "Patient will remain stable",
    ]


def test_interventions_goals_blob_keeps_rows_and_discards_group_headings():
    blob = (
        "Vital Sign Parameters INTERVENTIONS:\n"
        "VITAL SIGN PARAMETERS Notify Physician if:\n"
        "Blood Pressure; systolic is greater than 160 mm Hg or less than 100 mm Hg; "
        "diastolic is greater than 90 mm Hg or less than 50 mm Hg "
        "Start Effective Date: 01/10/2026\n"
        "Pain Rating is greater than 6 on 0-10 scale "
        "Start Effective Date: 01/10/2026\n"
        "Pulse is greater than 100 or less than 50 "
        "Start Effective Date: 01/10/2026\n"
        "Respirations are greater than 26 or less than 12 "
        "Start Effective Date: 01/10/2026\n"
        "SPO2: is less than 88% at rest /with exertion "
        "Start Effective Date: 01/10/2026\n"
        "Medication Management GOALS:\n"
        "Patient Centered Goal GOALS:\n"
        "To have stable blood pressure and less pain "
        "Start Effective Date: 01/10/2026"
    )

    items = copier._split_composite_items(blob, [blob])
    joined = "\n".join(items).lower()

    assert "blood pressure; systolic is greater than 160" in joined
    assert "pain rating is greater than 6" in joined
    assert "pulse is greater than 100" in joined
    assert "respirations are greater than 26" in joined
    assert "spo2: is less than 88%" in joined
    assert "stable blood pressure and less pain" in joined
    assert "medication management goals" not in [item.lower() for item in items]


def test_e1_empty_interventions_and_populated_other_extract_as_one_e2_source():
    from playwright.sync_api import sync_playwright

    html = """
    <html><body>
      <cmwf-cop id="interventions_goals">
        <a class="cop-title" data-section="interventions_goals">Interventions/Goals</a>
        <div class="cop-items"></div>
      </cmwf-cop>
      <cmwf-cop id="other_interventions_goals_v1">
        <a class="cop-title" data-section="other_interventions_goals_v1">
          Other Interventions/Goals
        </a>
        <div class="cop-items">
          <div class="copElement">Blood Pressure; systolic is greater than 160 mm Hg or less than 100 mm Hg Start Effective Date: 01/10/2026</div>
          <div class="copElement">Pain Rating is greater than 6 on 0-10 scale Start Effective Date: 01/10/2026</div>
          <div class="copElement">To have stable blood pressure and less pain Start Effective Date: 01/10/2026 Progress Toward Goal: 80%</div>
        </div>
      </cmwf-cop>
    </body></html>
    """

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(html)
        fields = page.evaluate(copier.JS_EXTRACT_FIELDS)
        browser.close()

    combined = copier._source_composite_sections(fields)["interventions_goals"]
    combined_text = "\n".join(combined["items"]).lower()

    assert "blood pressure; systolic is greater than 160" in combined_text
    assert "pain rating is greater than 6" in combined_text
    assert "to have stable blood pressure and less pain" in combined_text


def test_split_does_not_invent_unrelated_template_choices():
    items = copier._split_composite_items(
        "Full code Start Effective Date: 09/25/2025 "
        "Health care Proxy Start Effective Date: 09/25/2025"
    )

    joined = " ".join(item.lower() for item in items)
    assert "living will" not in joined
    assert "do not resuscitate" not in joined


def test_underscored_template_number_matches_completed_source_number():
    assert copier._row_matches_source(
        "Pain Rating is greater than __7___ on 0-10 scale",
        "Pain Rating is greater than 6 on 0-10 scale",
        ["Pain Rating is greater than 6 on 0-10 scale"],
    )


def test_normal_numeric_values_are_not_wildcards():
    assert not copier._row_matches_source(
        "Medication dose is 10 mg daily",
        "Medication dose is 5 mg daily",
        ["Medication dose is 5 mg daily"],
    )


def test_split_preserves_emergency_contact_item():
    items = copier._split_composite_items(
        "Emergency contact: spouse Start Effective Date: 09/25/2025"
    )
    assert items == ["Emergency contact: spouse"]


def test_split_preserves_dme_item_name():
    items = copier._split_composite_items(
        "DME: walker Start Effective Date: 09/25/2025"
    )
    assert items == ["DME: walker"]


def test_split_ignores_clear_widget_chrome():
    value = (
        "Advance Directives\n"
        "Clear\n"
        "ADVANCE DIRECTIVES:\n"
        "Full code Start Effective Date: 09/25/2025\n"
        "Health care Proxy Start Effective Date: 09/25/2025"
    )
    names = [item.lower() for item in copier._split_composite_items(value)]
    assert "clear" not in names
    assert "full code" in names
    assert "health care proxy" in names


def test_split_keeps_long_items_without_date_boundaries():
    paragraph = (
        "I certify/recertify that the above stated patient is homebound "
        "and has a need/continued need for intermittent skilled nursing."
    )

    items = copier._split_composite_items(paragraph)

    assert len(items) == 1
    assert items[0] == paragraph


def test_extract_fields_splits_concatenated_cop_items():
    from playwright.sync_api import sync_playwright

    html = """
    <html><body>
      <cmwf-cop id="advanced_directives">
        <a class="cop-title" data-section="advanced_directives">Advance Directives</a>
        <div class="cop-items">ADVANCE DIRECTIVES: Full code Start Effective Date: 09/25/2025 Health care Proxy Start Effective Date: 09/25/2025</div>
      </cmwf-cop>
    </body></html>
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(html)
        fields = page.evaluate(copier.JS_EXTRACT_FIELDS)
        browser.close()

    section = fields["cop_section_advanced_directives"]
    names = [item.lower() for item in section["items"]]
    assert any("full code" == name or name.endswith("full code") for name in names)
    assert any("health care proxy" in name for name in names)
    assert len(section["items"]) >= 2


def test_oasis_roc_care_plan_subsystems_merge_into_interventions_goals():
    extracted = {
        "cop_section_vitalsignparametershh": {
            "kind": "composite",
            "section": "vitalsignparametershh",
            "title": "Vital Signs",
            "items": [
                "Blood Pressure; systolic is greater than 160 mm Hg or less than 100 mm Hg; diastolic is greater than 90 mm Hg or less than 50 mm Hg Start Effective Date: 08/11/2026",
                "Fasting Blood Glucose is greater than 126 mg/dl or less than 70 mg/dl or Random Blood Glucose is greater than 200 mg/dl or less than 70 mg/dl Start Effective Date: 08/11/2026",
                "Pain Rating is greater than 7 on 0-10 scale Start Effective Date: 08/11/2026",
            ],
            "value": "Blood Pressure; systolic is greater than 160 mm Hg or less than 100 mm Hg; diastolic is greater than 90 mm Hg or less than 50 mm Hg Start Effective Date: 08/11/2026\n\nFasting Blood Glucose is greater than 126 mg/dl or less than 70 mg/dl or Random Blood Glucose is greater than 200 mg/dl or less than 70 mg/dl Start Effective Date: 08/11/2026\n\nPain Rating is greater than 7 on 0-10 scale Start Effective Date: 08/11/2026",
        },
        "cop_section_cardiovascularhh": {
            "kind": "composite",
            "section": "cardiovascularhh",
            "title": "Cardiovascular",
            "items": [
                "Cardiovascular INTERVENTIONS: Assess cardiovascular status Start Effective Date: 08/11/2026",
                "Cardiovascular GOALS: Patient will maintain adequate cardiac output Start Effective Date: 08/11/2026",
            ],
            "value": "Cardiovascular INTERVENTIONS: Assess cardiovascular status Start Effective Date: 08/11/2026\n\nCardiovascular GOALS: Patient will maintain adequate cardiac output Start Effective Date: 08/11/2026",
        },
        "cop_section_endocrinehh": {
            "kind": "composite",
            "section": "endocrinehh",
            "title": "Endocrine",
            "items": [
                "Endocrine INTERVENTIONS: Start Effective Date: 08/11/2026",
                "Endocrine GOALS: Start Effective Date: 08/11/2026",
                "SN to assess blood glucose monitoring technique Start Effective Date: 08/11/2026",
            ],
            "value": "Endocrine INTERVENTIONS:\nEndocrine GOALS:\nSN to assess blood glucose monitoring technique Start Effective Date: 08/11/2026",
        },
        "cop_section_interventions_goals": {
            "kind": "composite",
            "section": "interventions_goals",
            "title": "Interventions/Goals",
            "items": [
                "Endocrine INTERVENTIONS",
                "Endocrine GOALS",
            ],
            "value": "Endocrine INTERVENTIONS\nEndocrine GOALS",
        },
    }

    sections = copier._source_composite_sections(extracted)
    assert "interventions_goals" in sections
    combined = sections["interventions_goals"]
    items = combined["items"]
    joined = "\n".join(items).lower()

    assert "blood pressure; systolic is greater than 160" in joined
    assert "fasting blood glucose is greater than 126" in joined
    assert "pain rating is greater than 7" in joined
    assert "assess cardiovascular status" in joined
    assert "patient will maintain adequate cardiac output" in joined
    assert "assess blood glucose monitoring technique" in joined
    assert "endocrine interventions" not in [i.lower() for i in items]
    assert "endocrine goals" not in [i.lower() for i in items]

