from __future__ import annotations

from datetime import date, timedelta
import json
import stat
from pathlib import Path

import pytest

from devero_fleet.case_store import CaseStore, CaseStoreError
from devero_fleet.cli import _approve_record_supported, _intake_payload, _verification_report, build_parser
from devero_fleet.verification import (
    audit_bundle,
    compare_snapshots,
    cross_version_comparison,
    document_rule,
    flatten_fields,
    normalize_field,
    snapshot,
    validate_result,
)
from devero_fleet.verifier_runner import (
    EXTRACT_FRAME_JS,
    choose_source_row,
    document_identity_matches,
    related_classification,
    related_rows,
    row_matches,
    scan_case,
    _frame_signature,
    _open_row,
    _wait_for_document_identity,
    _wait_stable,
    VerificationBlocked,
)


IDENTITY = {
    "patient": "Fixture, Patient",
    "caregiver": "Fixture, Clinician",
    "doc_type": "OASIS-E2 Nurse Recert.",
    "source_doc_type": "OASIS-E1 Nurse Recert.",
    "source_date": "02/01/2026",
    "target_date": "05/26/2026",
}


def field(
    name: str,
    value: str,
    *,
    label: str = "",
    required: bool = False,
    visible: bool = True,
) -> dict:
    return {
        "name": name,
        "label": label,
        "kind": "text",
        "value": value,
        "visible": visible,
        "required": required,
    }


def page(*fields: dict, ready: bool = True) -> dict:
    return {"page": 1, "extraction": {"ready": ready}, "frames": [{"fields": list(fields)}]}


def bundle(doc_type: str, *fields: dict, source_fields: tuple[dict, ...] = ()) -> dict:
    return {
        "document_type": doc_type,
        "target_pages": [page(*fields)],
        "source_pages": [page(*source_fields)] if source_fields else [],
    }


def test_supported_document_aliases_and_item_codes() -> None:
    assert document_rule("OASIS-E2 Nurse Start of Care")["rfa"] == "01"
    assert document_rule("OASIS-E2 Nurse Recert.")["rfa"] == "04"
    assert document_rule("OASIS-E2 Discharge")["rfa"] == "09"
    assert document_rule("Skilled Nurse Visit Note")["kind"] == "SKILLED_NURSE_VISIT"
    assert document_rule("Unknown") is None
    normalized = normalize_field(field("M0090_INFO_COMPLETED_DT", "04/02/2026"))
    assert normalized["item"] == "M0090"
    assert normalized["location"] == "P1.F0.M0090"


def test_soc_timing_passes_and_target_date_is_not_forced_into_m0090() -> None:
    result = audit_bundle(
        bundle(
            "OASIS-E2 Nurse Start of Care",
            field("M0030_START_CARE_DT", "04/01/2026"),
            field("M0090_INFO_COMPLETED_DT", "04/05/2026"),
            field("M0100_ASSMT_REASON", "01 - Start of care"),
        )
    )
    assert result["run_status"] == "COMPLETE"
    assert result["verdict"] == "PASS"
    assert result["corrections"] == []


def test_version_and_timepoint_conflicts_fail_without_backdating_advice() -> None:
    result = audit_bundle(
        bundle(
            "OASIS-E2 Nurse Start of Care",
            field("M0030_START_CARE_DT", "03/25/2026"),
            field("M0090_INFO_COMPLETED_DT", "03/29/2026"),
            field("M0100_ASSMT_REASON", "04 - Recertification"),
        )
    )
    ids = {item["id"] for item in result["corrections"]}
    assert result["verdict"] == "FAIL"
    assert {"DVV-DATE-VERSION", "DVV-TIMEPOINT-M0100"} <= ids
    assert "backdate" in next(item for item in result["corrections"] if item["id"] == "DVV-DATE-VERSION")[
        "expected_correction"
    ].lower()


def test_recert_days_56_to_60_and_discharge_two_day_window() -> None:
    soc = date(2026, 4, 1)
    day_56 = soc + timedelta(days=55)
    recert = audit_bundle(
        bundle(
            "OASIS-E2 Nurse Recert.",
            field("M0030_START_CARE_DT", soc.strftime("%m/%d/%Y")),
            field("M0090_INFO_COMPLETED_DT", day_56.strftime("%m/%d/%Y")),
            field("M0100_ASSMT_REASON", "04"),
        )
    )
    assert recert["verdict"] == "PASS"
    late = audit_bundle(
        bundle(
            "OASIS-E2 Nurse Recert.",
            field("M0030_START_CARE_DT", soc.strftime("%m/%d/%Y")),
            field("M0090_INFO_COMPLETED_DT", (soc + timedelta(days=50)).strftime("%m/%d/%Y")),
            field("M0100_ASSMT_REASON", "04"),
        )
    )
    assert "DVV-RFA04-TIMING" in {item["id"] for item in late["corrections"]}
    discharge = audit_bundle(
        bundle(
            "OASIS-E2 Discharge",
            field("M0906_DC_TRAN_DTH_DT", "06/01/2026"),
            field("M0090_INFO_COMPLETED_DT", "06/04/2026"),
            field("M0100_ASSMT_REASON", "09"),
        )
    )
    assert "DVV-DISCHARGE-TIMING" in {item["id"] for item in discharge["corrections"]}


def test_recert_can_use_historical_soc_date_as_timing_evidence() -> None:
    result = audit_bundle(
        bundle(
            "OASIS-E2 Nurse Recert.",
            field("M0090_INFO_COMPLETED_DT", "05/26/2026"),
            field("M0100_ASSMT_REASON", "04"),
            source_fields=(field("M0030_START_CARE_DT", "04/01/2026"),),
        )
    )
    assert result["verdict"] == "PASS"


def test_e1_is_evidence_only_and_exact_long_clone_requires_review() -> None:
    narrative = "Current assessment narrative. " * 30
    result = audit_bundle(
        bundle(
            "OASIS-E2 Nurse Recert.",
            field("M0090_INFO_COMPLETED_DT", "05/26/2026"),
            field("M0100_ASSMT_REASON", "04"),
            field("sum_AssessmentSum_Txt", narrative),
            source_fields=(field("sum_AssessmentSum_Txt", narrative),),
        )
    )
    clone = next(item for item in result["corrections"] if item["issue_class"] == "CURRENT_STATUS")
    assert clone["fact_status"] == "USER_OR_CLINICIAN_REQUIRED"
    comparisons = cross_version_comparison(
        [normalize_field(field("M0069_PAT_GENDER", "1"))],
        [normalize_field(field("A0810_GENDER_IDENTITY", "1"))],
    )
    assert comparisons == [
        {
            "source_item": "M0069",
            "target_item": "A0810",
            "classification": "RENAMED_EVIDENCE_ONLY",
            "target_present": True,
            "equal": True,
        }
    ]


def test_visible_required_prompt_and_invalid_dates_are_deterministic() -> None:
    result = audit_bundle(
        bundle(
            "OASIS-E2 Nurse Start of Care",
            field("M0030_START_CARE_DT", "04/01/2026"),
            field("M0090_INFO_COMPLETED_DT", "04/02/2026"),
            field("M0100_ASSMT_REASON", "01"),
            field("free_text", "Patient response to treatment:"),
            field("bad_date", "13/40/2026"),
            field("required_text", "", required=True),
            field("hidden_required", "", required=True, visible=False),
        )
    )
    classes = {item["issue_class"] for item in result["corrections"]}
    assert {"PROMPT_VALUE", "DATE_FORMAT", "VISIBLE_REQUIRED"} <= classes
    assert all(item["location"] != "P1.F0.HIDDEN_REQUIRED" for item in result["corrections"])


def test_incomplete_related_evidence_blocks_instead_of_guessing() -> None:
    evidence = bundle(
        "OASIS-E2 Nurse Recert.",
        field("M0090_INFO_COMPLETED_DT", "05/26/2026"),
        field("M0100_ASSMT_REASON", "04"),
    )
    evidence["related_documents"] = [{"classification": "ORDER", "pages": [page(ready=False)]}]
    result = audit_bundle(evidence)
    assert result["run_status"] == "BLOCKED"
    assert result["verdict"] is None


def test_skilled_note_requires_all_documentation_domains() -> None:
    incomplete = audit_bundle(bundle("Skilled Nurse Visit Note", field("note", "Assessment completed.")))
    assert incomplete["verdict"] == "MIXED"
    assert "DVV-NOTE-NEXT_VISIT" in {item["id"] for item in incomplete["corrections"]}
    complete_text = (
        "Current assessment findings and vital status. Skilled wound care performed with education. "
        "Patient response and progress demonstrated. Plan for next visit includes follow-up monitoring. "
        "Requires skilled clinical judgment because of service complexity and risk."
    )
    complete = audit_bundle(bundle("Skilled Nurse Visit Note", field("note", complete_text)))
    assert complete["verdict"] == "PASS"


def test_snapshots_find_only_unapproved_changes() -> None:
    before_fields = flatten_fields([page(field("alpha", "one"), field("beta", "two"))])
    after_fields = flatten_fields([page(field("alpha", "changed"), field("beta", "changed"))])
    before = snapshot(before_fields)
    after = snapshot(after_fields)
    beta_location = next(item["location"] for item in after_fields if item["name"] == "beta")
    findings = compare_snapshots(before, after, [beta_location])
    assert len(findings) == 1
    assert findings[0]["location"].endswith("ALPHA")


def test_chart_matching_discovery_is_bounded_and_exact() -> None:
    rows = [
        {"index": 1, "activity_id": "fixture-a", "text": "OASIS-E1 Nurse Recert. 02/01/2026 Completed"},
        {"index": 2, "activity_id": "fixture-b", "text": "OASIS-E2 Nurse Recert. 05/26/2026 Pending Fixture, Clinician"},
        {"index": 3, "activity_id": "fixture-c", "text": "Plan of Care 05/20/2026 Completed"},
        {"index": 4, "activity_id": "fixture-d", "text": "Skilled Nurse Visit Note 05/22/2026 Sent To Office"},
        {"index": 5, "activity_id": "fixture-e", "text": "Physician Order 05/23/2026 Completed"},
        {"index": 6, "activity_id": "fixture-f", "text": "Skilled Nurse Visit Note 05/24/2026 Completed"},
    ]
    assert row_matches(
        rows[1],
        doc_type="OASIS-E2 Nurse Recert.",
        note_date="05/26/2026",
        status="pending",
        caregiver="Fixture, Clinician",
    )
    assert choose_source_row(rows, target_date="05/26/2026", doc_type="OASIS-E1 Nurse Recert.") == rows[0]
    selected = related_rows(rows, target_date="05/26/2026", excluded={"fixture-a", "fixture-b"})
    assert len(selected) == 4
    assert related_classification(rows[2]["text"]) == "PLAN_OF_CARE"
    assert related_classification(rows[3]["text"]) == "SKILLED_NOTE"


def test_chart_matching_uses_structured_columns_from_devero() -> None:
    row = {
        "index": 1,
        "activity_id": "fixture-a",
        "text": "Rendered row text without a parseable date separator",
        "form": "OASIS-E1 Nurse Start of Care",
        "date": "03/26/2026",
        "status": "Completed",
    }
    assert choose_source_row(
        [row],
        target_date="08/31/2026",
        doc_type="OASIS-E1 Nurse Start of Care",
        source_date="03/26/2026",
    ) == row


def test_open_document_identity_requires_patient_document_date_and_caregiver() -> None:
    visible = (
        "OASIS-E2 Nurse Recertification Patient: Fixture, Patient "
        "Date: 5/26/2026 Caregiver: Fixture, Clinician (RN)"
    )
    criteria = {
        "patient": "Fixture, Patient",
        "doc_type": "OASIS-E2 Nurse Recert.",
        "note_date": "05/26/2026",
        "caregiver": "Fixture, Clinician (RN)",
    }
    assert document_identity_matches(visible, **criteria)
    assert not document_identity_matches(visible.replace("Fixture, Patient", "Different, Patient"), **criteria)
    assert not document_identity_matches(visible.replace("5/26/2026", "5/25/2026"), **criteria)


def test_case_artifacts_are_private_expiring_and_results_are_validated(tmp_path: Path) -> None:
    store = CaseStore(root=tmp_path / "runtime", ttl_seconds=300)
    case_id = store.create(identity=IDENTITY, mode="verify")
    artifact = store.write_artifact(case_id, "verification-initial", {"clinical": "fixture"})
    assert stat.S_IMODE(artifact.stat().st_mode) == 0o600
    assert store.read_artifact(case_id, "verification-initial") == {"clinical": "fixture"}
    with pytest.raises(CaseStoreError, match="Invalid artifact"):
        store.write_artifact(case_id, "../unsafe", {})
    result = {
        "run_status": "COMPLETE",
        "verdict": "MIXED",
        "corrections": [
            {
                "id": "DVV-NOTE-NEXT_VISIT",
                "issue_class": "SKILLED_NOTE",
                "location": "NEXT_VISIT",
                "observed_issue": "Required support is missing.",
                "expected_correction": "Obtain clinician-supported documentation.",
                "evidence_class": "MISSING_REQUIRED",
                "source_class": "CMS_MBPM_CH7",
                "fact_status": "USER_OR_CLINICIAN_REQUIRED",
                "route": "CHIEF_INPUT",
            }
        ],
    }
    store.record_verification(case_id, result)
    store.approve_corrections(case_id, ["DVV-NOTE-NEXT_VISIT"])
    store.record_verification(case_id, result)
    assert store.load(case_id)["corrections"][0]["approved"] is True
    with pytest.raises(CaseStoreError, match="preserved unchanged"):
        store.record_verification(
            case_id,
            {"run_status": "COMPLETE", "verdict": "PASS", "corrections": []},
            required_corrections=result["corrections"],
        )
    with pytest.raises(CaseStoreError, match="explicitly FINAL"):
        store.record_verification(
            case_id,
            result,
            required_corrections=result["corrections"],
            require_final=True,
        )
    store.record_verification(
        case_id,
        {**result, "review_stage": "FINAL", "semantic_review_required": False},
        required_corrections=result["corrections"],
        require_final=True,
    )
    assert store.load(case_id)["status"] == "verified"
    store.close(case_id)
    assert not artifact.exists()


def test_result_rejects_unsafe_locations_and_cli_exposes_verifier_commands() -> None:
    with pytest.raises(ValueError, match="PHI-free"):
        validate_result(
            {
                "run_status": "COMPLETE",
                "verdict": "MIXED",
                "corrections": [
                    {
                        "id": "DVV-CONSISTENCY-TEST",
                        "issue_class": "CONSISTENCY",
                        "location": "patient name here",
                        "evidence_class": "CROSS_RECORD",
                        "source_class": "AGENCY_RECORD",
                        "fact_status": "USER_OR_CLINICIAN_REQUIRED",
                        "route": "CHIEF_INPUT",
                    }
                ],
            }
        )
    parser = build_parser()
    assert parser.parse_args(["verify-scan", "DV-AAAAAAAAAAAA"]).phase == "initial"
    assert parser.parse_args(
        ["verification-context", "DV-AAAAAAAAAAAA", "--role", "verifier", "--phase", "persistence"]
    ).phase == "persistence"
    assert parser.parse_args(["verification-report", "DV-AAAAAAAAAAAA"]).phase == "initial"
    assert parser.parse_args(["approve-record-supported", "DV-AAAAAAAAAAAA"]).phase == "initial"


def test_verification_report_is_plain_language_and_excludes_raw_evidence() -> None:
    artifact = {
        "bundle": {"patient": "Fixture, Private", "clinical_text": "sensitive fixture"},
        "audit": {
            "run_status": "COMPLETE",
            "verdict": "MIXED",
            "document_kind": "OASIS_E2_SOC",
            "counts": {"pages": 16},
            "corrections": [
                {
                    "id": "DVV-RESPONSE-A1005",
                    "issue_class": "RESPONSE_DOMAIN",
                    "location": "A1005",
                    "observed_issue": "The recorded response needs review.",
                    "expected_correction": "Select a supported response.",
                    "fact_status": "USER_OR_CLINICIAN_REQUIRED",
                    "route": "CHIEF_INPUT",
                },
                {
                    "id": "DVV-RESPONSE-A1005-DUPLICATE",
                    "issue_class": "RESPONSE_DOMAIN",
                    "location": "A1005",
                    "observed_issue": "The recorded response needs review.",
                    "expected_correction": "Select a supported response.",
                    "fact_status": "USER_OR_CLINICIAN_REQUIRED",
                    "route": "CHIEF_INPUT",
                },
            ],
        },
    }
    report = _verification_report(artifact, "DV-AAAAAAAAAAAA", "initial")
    assert "DEVERO VERIFICATION REPORT" in report
    assert "1. Response Domain" in report
    assert "What to do: Select a supported response." in report
    assert "RN/clinician confirmation required" in report
    assert "Findings flagged: 2" in report
    assert "Unique items to review: 1" in report
    assert report.count("1. Response Domain") == 1
    assert "Finding IDs: DVV-RESPONSE-A1005, DVV-RESPONSE-A1005-DUPLICATE" in report
    assert "Fixture, Private" not in report
    assert "sensitive fixture" not in report
    supported = _verification_report(
        artifact, "DV-AAAAAAAAAAAA", "initial", record_supported_only=True
    )
    assert "Unique items to review: 0" in supported
    assert "Response Domain" not in supported


def test_batch_approval_selects_only_record_supported_writer_findings(tmp_path: Path) -> None:
    store = CaseStore(root=tmp_path / "runtime", ttl_seconds=300)
    case_id = store.create(identity=IDENTITY, mode="correct")
    corrections = [
        {"id": "DVV-SUPPORTED-ONE", "location": "P1.F1", "fact_status": "SUPPORTED", "route": "WRITER"},
        {
            "id": "DVV-CLINICIAN-TWO",
            "location": "A1005",
            "fact_status": "USER_OR_CLINICIAN_REQUIRED",
            "route": "CHIEF_INPUT",
        },
    ]
    store.update_section(case_id, "corrections", [{**item, "approved": False} for item in corrections])
    store.write_artifact(case_id, "verification-initial", {"audit": {"corrections": corrections}})
    message = _approve_record_supported(store, case_id, "initial")
    by_id = {item["id"]: item for item in store.load(case_id)["corrections"]}
    assert by_id["DVV-SUPPORTED-ONE"]["approved"] is True
    assert by_id["DVV-CLINICIAN-TWO"]["approved"] is False
    assert "Devero was not edited or saved" in message


def test_intake_accepts_one_commented_task_list(tmp_path: Path) -> None:
    source = tmp_path / "tasks.jsonl"
    source.write_text(
        '[\n  // disabled example\n  {"enabled": false, "patient": "Old, Person"},\n'
        '  {"patient": "Fixture, Patient", "doc_type": "Skilled Nurse Visit Note", "target_date": "MM/DD/YYYY"},\n]\n',
        encoding="utf-8",
    )
    source.chmod(0o600)
    payload = _intake_payload(str(source))
    assert payload["mode"] == "verify"
    assert payload["identity"]["doc_type"] == "Skilled Nurse Visit Note"


def test_scan_case_records_initial_and_persistence_evidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = CaseStore(root=tmp_path / "runtime", ttl_seconds=300)
    case_id = store.create(identity=IDENTITY, mode="verify")
    current = {"value": "one"}

    def fake_scan(_case_id: str, _record: dict) -> dict:
        return bundle(
            "OASIS-E2 Nurse Recert.",
            field("M0030_START_CARE_DT", "04/01/2026"),
            field("M0090_INFO_COMPLETED_DT", "05/26/2026"),
            field("M0100_ASSMT_REASON", "04"),
            field("stable", current["value"]),
        )

    monkeypatch.setattr("devero_fleet.verifier_runner._scan_live", fake_scan)
    initial = scan_case(case_id, "initial", store)
    assert initial["verdict"] == "PASS"
    assert initial["semantic_review_required"] is True
    assert store.load(case_id)["status"] == "verification_pending"
    assert store.read_artifact(case_id, "verification-initial")["audit"]["snapshot"]
    current["value"] = "changed"
    persistence = scan_case(case_id, "persistence", store)
    assert persistence["verdict"] == "MIXED"
    assert any(
        item["id"].startswith("DVV-PERSISTENCE-")
        for item in store.load(case_id)["corrections"]
    )


def test_browser_scanner_has_no_clinical_mutation_surface() -> None:
    source = Path("devero_fleet/verifier_runner.py").read_text(encoding="utf-8")
    for forbidden in (
        "JS_INJECT_FIELDS",
        "consume_save_authorization",
        "click_save_button",
        "insertIntoForm",
        "sign_document",
    ):
        assert forbidden not in source
    assert "cmwf-integrated-action" not in EXTRACT_FRAME_JS
    assert "#formFramePopup" in EXTRACT_FRAME_JS
    assert "txt.includes('Select a Patient')" in source
    assert "classes.includes('activenp')" in source
    assert "!ngClick.includes('printFormController')" in source


def test_frame_signature_carries_body_length_for_stability_check() -> None:
    class Frame:
        def evaluate(self, _script: str) -> dict:
            return {"fields": [], "body_length": 123}

    class Page:
        frames = [Frame()]

        def wait_for_timeout(self, _milliseconds: int) -> None:
            pass

    page = Page()
    assert _frame_signature(page).startswith("123:")
    assert _wait_stable(page, timeout_seconds=1)


def test_open_row_waits_for_new_form_page_instead_of_accepting_chart_controls() -> None:
    class Locator:
        def count(self) -> int:
            return 1

    class FormPage:
        def locator(self, _selector: str) -> Locator:
            return Locator()

        def evaluate(self, _script: str) -> int:
            return 100

    class Context:
        pages: list[object]

    form = FormPage()
    context = Context()

    class ChartPage:
        def evaluate(self, _script: str, _criteria: dict) -> bool:
            return True

        def wait_for_timeout(self, _milliseconds: int) -> None:
            if form not in context.pages:
                context.pages.append(form)

    chart = ChartPage()
    context.pages = [chart]
    row = {"index": 1, "activity_id": "fixture"}
    assert _open_row(chart, context, row) is form


def test_document_identity_wait_includes_control_values() -> None:
    class Body:
        def inner_text(self, timeout: int) -> str:
            assert timeout == 2_000
            return "OASIS E2 Nurse Recert Patient Fixture Patient Caregiver Fixture Clinician"

    class Page:
        def locator(self, selector: str) -> Body:
            assert selector == "body"
            return Body()

        def evaluate(self, _script: str) -> str:
            return "05/26/2026"

        def wait_for_timeout(self, _milliseconds: int) -> None:
            pass

    assert _wait_for_document_identity(
        Page(),
        {
            "patient": "Fixture, Patient",
            "doc_type": "OASIS-E2 Nurse Recert.",
            "note_date": "05/26/2026",
            "caregiver": "Fixture, Clinician",
        },
        timeout_seconds=1,
    )


def test_blocked_scan_replaces_stale_verification_artifact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = CaseStore(root=tmp_path / "runtime", ttl_seconds=300)
    case_id = store.create(identity=IDENTITY, mode="verify")
    store.write_artifact(case_id, "verification-initial", {"bundle": {"stale": True}})

    def blocked_scan(_case_id: str, _record: dict) -> dict:
        raise VerificationBlocked("TEST_BLOCK")

    monkeypatch.setattr("devero_fleet.verifier_runner._scan_live", blocked_scan)
    result = scan_case(case_id, "initial", store)
    assert result["run_status"] == "BLOCKED"
    assert store.read_artifact(case_id, "verification-initial") == {
        "audit": {
            "run_status": "BLOCKED",
            "verdict": None,
            "review_stage": "DETERMINISTIC",
            "semantic_review_required": False,
            "corrections": [],
            "reason": "TEST_BLOCK",
        }
    }
