from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from devero_fleet.verification import OASIS_E2_ITEMS, OASIS_E2_SPEC, audit_bundle, document_rule
from devero_fleet.verifier_runner import EXTRACT_FRAME_JS, document_identity_matches


DEVERO_DOCUMENT_TYPES = {
    "OASIS-E2 Nurse Start of Care": ("01",),
    "NTST OASIS Nurse Start of Care": ("01",),
    "NTST OASIS PT SOC": ("01",),
    "OASIS-E2 Nurse Resumption of Care": ("03",),
    "NTST OASIS Nurse Resumption of Care": ("03",),
    "OASIS-E2 Nurse Recert.": ("04",),
    "NTST OASIS Nurse Recert": ("04",),
    "OASIS-E2 Nurse Other Follow Up": ("05",),
    "NTST OASIS Nurse Other Follow Up": ("05",),
    "NTST OASIS Nurse Resumption of Care/Recert": ("03", "04"),
    "NTST OASIS Transfer Non-Visit": ("06", "07"),
    "NTST (DeVero) OASIS Transfer/Death": ("06", "07", "08"),
    "OASIS-E2 Death at Home": ("08",),
    "OASIS-E2 Discharge": ("09",),
    "NTST OASIS Discharge": ("09",),
    "NTST OASIS Discharge Non-Visit": ("09",),
    "NTST OASIS Discharge Physical Therapy": ("09",),
}


def field(
    name: str,
    value: str,
    *,
    code: str = "",
    kind: str = "text",
    required: bool = False,
    visible: bool = True,
    valid: bool = True,
) -> dict:
    return {
        "name": name,
        "label": name,
        "kind": kind,
        "value": value,
        "code": code,
        "visible": visible,
        "required": required,
        "valid": valid,
    }


def bundle(doc_type: str, *fields: dict) -> dict:
    return {
        "document_type": doc_type,
        "target_pages": [
            {"page": 1, "extraction": {"ready": True}, "frames": [{"fields": list(fields)}]}
        ],
        "source_pages": [],
        "related_documents": [],
    }


def required_fields(rfa: str, *, event: date = date(2026, 5, 1), completed: date | None = None) -> list[dict]:
    completed = completed or event
    values = [
        field("M0090_INFO_COMPLETED_DT", completed.strftime("%m/%d/%Y")),
        field("M0100_ASSMT_REASON", rfa, code=rfa, kind="select"),
    ]
    if rfa == "01":
        values.append(field("M0030_START_CARE_DT", event.strftime("%m/%d/%Y")))
    elif rfa == "03":
        values.append(field("M0032_ROC_DT", event.strftime("%m/%d/%Y")))
    elif rfa in {"06", "07", "08", "09"}:
        values.append(field("M0906_DC_TRAN_DTH_DT", event.strftime("%m/%d/%Y")))
    return values


def test_document_types_cover_devero_inventory_and_all_official_rfas() -> None:
    assert {rfa for rfas in DEVERO_DOCUMENT_TYPES.values() for rfa in rfas} == {
        "01", "03", "04", "05", "06", "07", "08", "09"
    }
    for label, expected_rfas in DEVERO_DOCUMENT_TYPES.items():
        assert document_rule(label)["rfas"] == expected_rfas
    assert document_rule("OASIS-E2 Unknown Assessment") is None


def test_document_types_combined_devero_forms_resolve_from_m0100() -> None:
    roc = audit_bundle(
        bundle("NTST OASIS Nurse Resumption of Care/Recert", *required_fields("03"))
    )
    recert = audit_bundle(
        bundle(
            "NTST OASIS Nurse Resumption of Care/Recert",
            *required_fields("04"),
            field("M0030_START_CARE_DT", "04/01/2026"),
        )
    )
    death = audit_bundle(
        bundle("NTST (DeVero) OASIS Transfer/Death", *required_fields("08"))
    )
    assert roc["document_kind"] == "OASIS_E2_ROC"
    assert recert["document_kind"] == "OASIS_E2_RECERT"
    assert death["document_kind"] == "OASIS_E2_DEATH_AT_HOME"


def test_document_types_resolve_from_raw_m0100_code_when_label_has_no_number() -> None:
    fields = required_fields("03")
    fields[1]["value"] = "Resumption of care after inpatient stay"
    result = audit_bundle(bundle("NTST OASIS Nurse Resumption of Care/Recert", *fields))
    assert result["document_kind"] == "OASIS_E2_ROC"
    assert "DVV-TIMEPOINT-M0100" not in {item["id"] for item in result["corrections"]}


@pytest.mark.parametrize(
    ("label", "rfa", "kind"),
    (
        ("OASIS-E2 Nurse Start of Care", "01", "OASIS_E2_SOC"),
        ("OASIS-E2 Nurse Resumption of Care", "03", "OASIS_E2_ROC"),
        ("OASIS-E2 Nurse Recert.", "04", "OASIS_E2_RECERT"),
        ("OASIS-E2 Nurse Other Follow Up", "05", "OASIS_E2_OTHER_FOLLOWUP"),
        ("NTST OASIS Transfer Non-Visit", "06", "OASIS_E2_TRANSFER_NOT_DISCHARGED"),
        ("NTST OASIS Transfer Non-Visit", "07", "OASIS_E2_TRANSFER_DISCHARGED"),
        ("OASIS-E2 Death at Home", "08", "OASIS_E2_DEATH_AT_HOME"),
        ("OASIS-E2 Discharge", "09", "OASIS_E2_DISCHARGE"),
    ),
)
def test_document_types_all_rfas_complete(label: str, rfa: str, kind: str) -> None:
    result = audit_bundle(bundle(label, *required_fields(rfa)))
    assert result["run_status"] == "COMPLETE"
    assert result["document_kind"] == kind
    assert result["verdict"] == "PASS"


def test_identity_accepts_devero_soc_abbreviation_and_transfer_death_title() -> None:
    common = {
        "patient": "Fixture, Patient",
        "note_date": "05/01/2026",
        "caregiver": "Fixture, Clinician",
    }
    assert document_identity_matches(
        "NTST OASIS PT SOC 05/01/2026 Fixture Patient Fixture Clinician",
        doc_type="NTST OASIS PT SOC",
        **common,
    )
    assert document_identity_matches(
        "NTST DeVero OASIS Transfer Death 05/01/2026 Fixture Patient Fixture Clinician",
        doc_type="NTST (DeVero) OASIS Transfer/Death",
        **common,
    )
    assert document_identity_matches(
        "OASIS Death at Home 05/01/2026 Fixture Patient Fixture Clinician",
        doc_type="NTST (DeVero) OASIS Transfer/Death",
        **common,
    )
    assert document_identity_matches(
        "OASIS Recertification 05/01/2026 Fixture Patient Fixture Clinician",
        doc_type="NTST OASIS Nurse Resumption of Care/Recert",
        **common,
    )


def test_rules_use_official_v302_clinical_item_and_response_tables() -> None:
    assert OASIS_E2_SPEC["effective"] == "2026-04-01"
    assert "V3.02.0" in OASIS_E2_SPEC["source"]
    assert len(OASIS_E2_ITEMS) == 405
    assert OASIS_E2_ITEMS["A0810"]["v"] == ["1", "2"]
    assert OASIS_E2_ITEMS["A1255"]["r"] == ["01", "03"]
    assert "-" in OASIS_E2_ITEMS["D0150A2"]["v"]


def test_rules_flag_item_outside_selected_timepoint() -> None:
    result = audit_bundle(
        bundle(
            "OASIS-E2 Nurse Recert.",
            *required_fields("04"),
            field("A1255", "1 - No", code="1", kind="select"),
        )
    )
    finding = next(item for item in result["corrections"] if item["id"] == "DVV-TIMEPOINT-A1255")
    assert finding["issue_class"] == "TIMEPOINT_ITEM_SET"
    assert finding["fact_status"] == "NOT_EDITABLE"


def test_response_rules_reject_invalid_code_and_accept_e2_dash() -> None:
    invalid = audit_bundle(
        bundle(
            "OASIS-E2 Nurse Start of Care",
            *required_fields("01"),
            field("A0810", "Unknown", code="9", kind="select"),
        )
    )
    assert "DVV-RESPONSE-A0810" in {item["id"] for item in invalid["corrections"]}
    valid_dash = audit_bundle(
        bundle(
            "OASIS-E2 Discharge",
            *required_fields("09"),
            field("D0150A2", "-", code="-", kind="select"),
        )
    )
    assert "DVV-RESPONSE-D0150A2" not in {item["id"] for item in valid_dash["corrections"]}


def test_response_rules_flag_retired_e1_items_and_preserve_subitem_locations() -> None:
    result = audit_bundle(
        bundle(
            "OASIS-E2 Nurse Start of Care",
            *required_fields("01"),
            field("M0069_PAT_GENDER", "1", code="1", kind="select"),
            field("D0150A1", "1", code="1", kind="select", required=True),
            field("D0150A2", "2", code="2", kind="select", required=True),
        )
    )
    ids = {item["id"] for item in result["corrections"]}
    assert "DVV-E2-RETIRED-M0069" in ids
    snapshot_locations = {item["location"] for item in result["snapshot"]["fields"].values()}
    assert {"P1.F0.D0150A1", "P1.F0.D0150A2"} <= snapshot_locations


def test_skip_rules_follow_devero_visibility_requiredness_and_validity() -> None:
    result = audit_bundle(
        bundle(
            "OASIS-E2 Nurse Start of Care",
            *required_fields("01"),
            field("M1005_INP_DISCHARGE_DT", "", required=True, visible=False, valid=False),
            field("A1255", "", required=True, visible=True),
            field("A0810", "9", code="9", kind="select", valid=False),
        )
    )
    ids = {item["id"] for item in result["corrections"]}
    assert "DVV-REQUIRED-A1255" in ids
    assert "DVV-REQUIRED-M1005" not in ids
    assert "DVV-RESPONSE-A0810" in ids


def test_timing_rules_cover_soc_roc_transfer_death_and_discharge() -> None:
    cases = (
        ("OASIS-E2 Nurse Start of Care", "01", 6, "DVV-SOC-TIMING"),
        ("OASIS-E2 Nurse Resumption of Care", "03", 3, "DVV-ROC-TIMING"),
        ("NTST OASIS Transfer Non-Visit", "06", 3, "DVV-TRANSFER-TIMING"),
        ("NTST (DeVero) OASIS Transfer/Death", "08", 3, "DVV-DEATH-TIMING"),
        ("OASIS-E2 Discharge", "09", 3, "DVV-DISCHARGE-TIMING"),
    )
    event = date(2026, 5, 1)
    for label, rfa, days, finding_id in cases:
        result = audit_bundle(
            bundle(label, *required_fields(rfa, event=event, completed=event + timedelta(days=days)))
        )
        assert finding_id in {item["id"] for item in result["corrections"]}


def test_timing_rules_support_other_followup_without_inventing_event_date() -> None:
    result = audit_bundle(
        bundle("OASIS-E2 Nurse Other Follow Up", *required_fields("05"))
    )
    assert result["document_kind"] == "OASIS_E2_OTHER_FOLLOWUP"
    assert result["verdict"] == "PASS"
    assert result["semantic_review_required"] is True


def test_unsupported_and_incomplete_documents_fail_closed() -> None:
    unsupported = audit_bundle(bundle("OASIS-E2 Unknown Assessment", field("M0100_ASSMT_REASON", "01")))
    incomplete = bundle("OASIS-E2 Nurse Start of Care", *required_fields("01"))
    incomplete["target_pages"][0]["extraction"]["ready"] = False
    assert unsupported["reason"] == "UNSUPPORTED_DOCUMENT"
    assert audit_bundle(incomplete)["reason"] == "INCOMPLETE_EXTRACTION"


def test_readonly_scanner_still_has_no_mutation_surface() -> None:
    source = Path("devero_fleet/verifier_runner.py").read_text(encoding="utf-8")
    assert "code, checked, valid" in EXTRACT_FRAME_JS
    for forbidden in ("JS_INJECT_FIELDS", "click_save_button", "sign_document"):
        assert forbidden not in source


def test_documentation_names_full_e2_scope_and_validation_boundary() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")
    skill = Path("hermes/skills/devero-verifier/SKILL.md").read_text(encoding="utf-8")
    checklist = Path(
        "hermes/skills/devero-verifier/references/cms-compliance-checklist.md"
    ).read_text(encoding="utf-8")
    for phrase in (
        "Resumption of Care", "Other Follow-Up", "Transfer", "Death at Home", "Discharge"
    ):
        assert phrase in readme
        assert phrase in skill
    assert "Data Specifications V3.02.0" in checklist
    assert "does not replace iQIES submission validation" in checklist
