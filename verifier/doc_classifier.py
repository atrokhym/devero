"""
Document Classifier & Timepoint Router (verifier/doc_classifier.py)

Classifies DeVero clinical forms by document category, OASIS timepoint (RFA),
expected page count, and appropriate baseline comparison strategy.
"""

import re
from typing import Dict, Any, Optional

# Supported document categories
DOC_TYPE_OASIS_SOC = "OASIS_SOC"                # RFA 01: Start of Care
DOC_TYPE_OASIS_ROC = "OASIS_ROC"                # RFA 03: Resumption of Care
DOC_TYPE_OASIS_RECERT = "OASIS_RECERT"          # RFA 04 / 05: Recertification / Follow-up
DOC_TYPE_OASIS_DISCHARGE = "OASIS_DISCHARGE"    # RFA 09: Discharge from Agency
DOC_TYPE_OASIS_TRANSFER = "OASIS_TRANSFER"      # RFA 06 / 07: Transfer to Inpatient Facility
DOC_TYPE_OASIS_DEATH = "OASIS_DEATH"            # RFA 08: Death at Home
DOC_TYPE_SN_VISIT_NOTE = "SN_VISIT_NOTE"        # Routine Skilled Nurse Visit Note
DOC_TYPE_UNKNOWN = "UNKNOWN"

def classify_document(doc_type_str: str, form_title: str = "", m0100_val: Optional[str] = None) -> Dict[str, Any]:
    """
    Classifies a clinical document and returns metadata including:
    - category: The normalized document category
    - is_oasis: Boolean indicating if it's an OASIS assessment
    - expected_rfa: Expected M0100 Reason for Assessment code
    - expected_pages: Expected page count range (min_pages, max_pages)
    - baseline_strategy: How to select or compare against baseline notes
    - display_name: Clean human-readable document label
    """
    combined = f"{doc_type_str} {form_title}".strip().lower()
    
    # 1. Skilled Nurse Visit Note (Routine)
    if "skilled nurse visit note" in combined or "sn visit note" in combined or "skilled nursing visit" in combined:
        return {
            "category": DOC_TYPE_SN_VISIT_NOTE,
            "is_oasis": False,
            "expected_rfa": None,
            "min_pages": 1,
            "max_pages": 3,
            "baseline_strategy": "PREVIOUS_VISIT_NOTE",
            "display_name": "Skilled Nurse Visit Note (Routine)",
            "description": "Routine 1-2 page skilled nursing visit note documenting interventions, vitals, and progress."
        }

    # 2. OASIS Recertification (RFA 04 / 05) - Check explicit recert title first!
    if "recert" in combined or "follow-up" in combined or "follow up" in combined:
        return {
            "category": DOC_TYPE_OASIS_RECERT,
            "is_oasis": True,
            "expected_rfa": "04",
            "min_pages": 5,
            "max_pages": 7,
            "baseline_strategy": "PRIOR_COMPLETED_RECERT_OR_SOC",
            "display_name": "OASIS-E2 Recertification (Follow-Up)",
            "description": "60-day recertification assessment during days 56-60 of the certification period."
        }

    # 3. OASIS Resumption of Care (RFA 03)
    if "resumption of care" in combined or "roc" in combined.split():
        return {
            "category": DOC_TYPE_OASIS_ROC,
            "is_oasis": True,
            "expected_rfa": "03",
            "min_pages": 5,
            "max_pages": 7,
            "baseline_strategy": "PRE_HOSPITALIZATION_NOTE",
            "display_name": "OASIS-E2 Resumption of Care (ROC)",
            "description": "Post-inpatient facility reassessment within 48 hours of return home."
        }

    # 4. OASIS Discharge (RFA 09)
    if "discharge" in combined:
        return {
            "category": DOC_TYPE_OASIS_DISCHARGE,
            "is_oasis": True,
            "expected_rfa": "09",
            "min_pages": 4,
            "max_pages": 7,
            "baseline_strategy": "ADMISSION_SOC_OR_RECENT_RECERT",
            "display_name": "OASIS-E2 Discharge (DC)",
            "description": "Comprehensive discharge assessment evaluating goals met and discharge disposition."
        }

    # 5. OASIS Transfer to Inpatient Facility (RFA 06 / 07)
    if "transfer" in combined:
        return {
            "category": DOC_TYPE_OASIS_TRANSFER,
            "is_oasis": True,
            "expected_rfa": "06_OR_07",
            "min_pages": 3,
            "max_pages": 6,
            "baseline_strategy": "MOST_RECENT_CLINICAL_NOTE",
            "display_name": "OASIS-E2 Transfer to Inpatient Facility",
            "description": "Assessment of transfer to inpatient facility with or without discharge from agency."
        }

    # 6. OASIS Death at Home (RFA 08)
    if "death" in combined:
        return {
            "category": DOC_TYPE_OASIS_DEATH,
            "is_oasis": True,
            "expected_rfa": "08",
            "min_pages": 1,
            "max_pages": 4,
            "baseline_strategy": "MOST_RECENT_CLINICAL_NOTE",
            "display_name": "OASIS-E2 Death at Home",
            "description": "Assessment following patient death at home."
        }

    # 7. OASIS Start of Care (RFA 01)
    if "start of care" in combined or "soc" in combined.split() or (m0100_val and str(m0100_val) in ["1", "01"]):
        return {
            "category": DOC_TYPE_OASIS_SOC,
            "is_oasis": True,
            "expected_rfa": "01",
            "min_pages": 5,
            "max_pages": 18,
            "baseline_strategy": "STANDALONE_OR_PRIOR_EPISODE",
            "display_name": "OASIS-E2 Start of Care (SOC)",
            "description": "Initial comprehensive assessment within 5 days of referral or hospital discharge."
        }

    # 8. M0100-based fallback if document title was generic
    if m0100_val:
        m_str = str(m0100_val).strip()
        if m_str in ["3", "03"]:
            return {"category": DOC_TYPE_OASIS_ROC, "is_oasis": True, "expected_rfa": "03", "min_pages": 5, "max_pages": 7, "baseline_strategy": "PRE_HOSPITALIZATION_NOTE", "display_name": "OASIS-E2 Resumption of Care (ROC)", "description": "Post-inpatient facility reassessment."}
        if m_str in ["4", "04", "5", "05"]:
            return {"category": DOC_TYPE_OASIS_RECERT, "is_oasis": True, "expected_rfa": "04", "min_pages": 5, "max_pages": 7, "baseline_strategy": "PRIOR_COMPLETED_RECERT_OR_SOC", "display_name": "OASIS-E2 Recertification (Follow-Up)", "description": "60-day recertification assessment."}
        if m_str in ["9", "09"]:
            return {"category": DOC_TYPE_OASIS_DISCHARGE, "is_oasis": True, "expected_rfa": "09", "min_pages": 4, "max_pages": 7, "baseline_strategy": "ADMISSION_SOC_OR_RECENT_RECERT", "display_name": "OASIS-E2 Discharge (DC)", "description": "Comprehensive discharge assessment."}

    # Fallback to general OASIS Recert if OASIS keyword present or if doc_type is empty/default
    if "oasis" in combined or not combined:
        return {
            "category": DOC_TYPE_OASIS_RECERT,
            "is_oasis": True,
            "expected_rfa": "04",
            "min_pages": 5,
            "max_pages": 7,
            "baseline_strategy": "PRIOR_COMPLETED_RECERT_OR_SOC",
            "display_name": "OASIS-E2 Recertification (Follow-Up)",
            "description": "Standard OASIS assessment form."
        }

    return {
        "category": DOC_TYPE_UNKNOWN,
        "is_oasis": False,
        "expected_rfa": None,
        "min_pages": 1,
        "max_pages": 10,
        "baseline_strategy": "MOST_RECENT_CLINICAL_NOTE",
        "display_name": doc_type_str or "Clinical Document",
        "description": "Generic clinical form."
    }
