import sys, os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from verifier.oasis_rules import audit_universal_document
from verifier.doc_classifier import (
    DOC_TYPE_OASIS_SOC,
    DOC_TYPE_OASIS_ROC,
    DOC_TYPE_OASIS_RECERT,
    DOC_TYPE_OASIS_DISCHARGE,
    DOC_TYPE_OASIS_TRANSFER,
    DOC_TYPE_OASIS_DEATH
)

def test_discharge_does_not_flag_missing_assessment_summary_or_m1021():
    """OASIS Discharge should not flag verification errors for sum_AssessmentSum_Txt or M1021/M1023."""
    target_pages = [
        {
            "page_num": 1,
            "extraction": {"ready": True},
            "main": {
                "title": "OASIS-E2 Discharge",
                "fields": [
                    {"name": "M0090_INFO_COMPLETED_DT", "val": "09/09/2026", "vis": True},
                    {"name": "M0100_ASSMT_REASON", "val": "09", "vis": True},
                    {"name": "M0906_DC_TRAN_DTH_DT", "val": "09/09/2026", "vis": True},
                    {"name": "M2420_DSCHRG_DISP", "val": "01", "vis": True},
                    {"name": "cf_SystolicBP_Txt", "val": "120", "vis": True},
                    {"name": "cf_DiastolicBP_Txt", "val": "80", "vis": True},
                    {"name": "cf_Pulse_Txt", "val": "72", "vis": True}
                ],
                "narratives": []
            }
        },
        {
            "page_num": 2,
            "extraction": {"ready": True},
            "main": {
                "title": "OASIS-E2 Discharge Page 2",
                "fields": [],
                "narratives": []
            }
        }
    ]
    source_pages = [
        {
            "page_num": 6,
            "extraction": {"ready": True},
            "main": {
                "title": "OASIS-E2 Nurse Resumption of Care",
                "fields": [
                    {"name": "sum_AssessmentSum_Txt", "val": "Patient is an 83yo male...", "vis": True},
                    {"name": "prim_diag_code", "val": "I10", "vis": True}
                ],
                "narratives": []
            }
        }
    ]
    meta = {
        "doc_type": "OASIS-E2 Discharge",
        "target_date": "09/09/2026",
        "source_doc_type": "OASIS-E2 Nurse Resumption of Care"
    }

    findings = audit_universal_document(target_pages, source_pages, meta)
    assert findings["verification_error_items"] == []
    assert not any("M1021" in item.get("field", "") for item in findings["do_now_items"])
    assert findings["pass_fail_status"] == "PASS"

def test_discharge_unselected_m2420_in_clinical_decision_items():
    """OASIS Discharge with unselected M2420 should place it in clinical_decision_items, not do_now_items."""
    target_pages = [
        {
            "page_num": 1,
            "extraction": {"ready": True},
            "main": {
                "title": "OASIS-E2 Discharge",
                "fields": [
                    {"name": "M0090_INFO_COMPLETED_DT", "val": "09/09/2026", "vis": True},
                    {"name": "M0100_ASSMT_REASON", "val": "09", "vis": True},
                    {"name": "M0906_DC_TRAN_DTH_DT", "val": "09/09/2026", "vis": True}
                ],
                "narratives": []
            }
        }
    ]
    meta = {
        "doc_type": "OASIS-E2 Discharge",
        "target_date": "09/09/2026"
    }

    findings = audit_universal_document(target_pages, [], meta)
    assert any("M2420_DSCHRG_DISP" in item.get("field", "") for item in findings["clinical_decision_items"])
    assert not any("M2420_DSCHRG_DISP" in item.get("field", "") for item in findings["do_now_items"])

def test_recert_flags_missing_assessment_summary_and_m1021():
    """OASIS Recert should flag verification error when sum_AssessmentSum_Txt is missing and DO NOW when M1021 is empty."""
    target_pages = [
        {
            "page_num": 1,
            "extraction": {"ready": True},
            "main": {
                "title": "OASIS-E2 Nurse Recert",
                "fields": [
                    {"name": "M0090_INFO_COMPLETED_DT", "val": "08/27/2026", "vis": True},
                    {"name": "M0100_ASSMT_REASON", "val": "04", "vis": True}
                ],
                "narratives": []
            }
        }
    ]
    source_pages = [
        {
            "page_num": 6,
            "extraction": {"ready": True},
            "main": {
                "title": "OASIS-E2 Nurse Recert",
                "fields": [
                    {"name": "sum_AssessmentSum_Txt", "val": "Baseline summary narrative...", "vis": True}
                ],
                "narratives": []
            }
        }
    ]
    meta = {
        "doc_type": "OASIS-E2 Nurse Recert",
        "target_date": "08/27/2026",
        "source_doc_type": "OASIS-E2 Nurse Recert"
    }

    findings = audit_universal_document(target_pages, source_pages, meta)
    assert any("Clinical Assessment Summary Extraction" in item.get("section", "") for item in findings["verification_error_items"])
    assert any("M1021" in item.get("field", "") for item in findings["do_now_items"])


def test_missing_last_bm_is_reported_for_nursing_and_oasis():
    for doc_type in ("Skilled Nurse Visit Note", "OASIS-E2 Nurse Start of Care"):
        for value in ("", "Clear", "09/10/2026"):
            pages = [{
                "page_num": 1,
                "main": {
                    "title": doc_type,
                    "fields": [{"name": "lbd_LastBm_Chk", "tag": "cmwf-date", "val": value, "vis": True}],
                    "narratives": [],
                },
            }]
            findings = audit_universal_document(pages, [], {"doc_type": doc_type, "target_date": "09/14/2026"})
            missing = [item for item in findings["clinical_decision_items"]
                       if item.get("field") == "lbd_LastBm_Chk" and item.get("current") == "Field is empty"]
            assert bool(missing) == (value != "09/10/2026")
