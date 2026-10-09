from unittest.mock import patch

from writer.fix_visit_note import LAST_BM_FIELD, approve_package


def test_visit_note_approval_defaults_empty_last_bm_to_visit_date():
    package = {
        "task": {"target_date": "09/14/2026"},
        "current_values": {LAST_BM_FIELD: "Clear"},
        "proposed_fields": {},
    }
    with patch("writer.fix_visit_note._validate_narrative_draft", return_value=[]):
        approved = approve_package(package, "RN")
        supplied = approve_package(package, "RN", last_bm="09/13/2026")

    assert approved["proposed_fields"][LAST_BM_FIELD] == "09/14/2026"
    assert approved["clinician_review"]["last_bm_source"] == "visit date default for missing Last BM"
    assert supplied["proposed_fields"][LAST_BM_FIELD] == "09/13/2026"
