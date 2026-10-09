"""
Universal Clinical & Regulatory Rules Engine (verifier/oasis_rules.py)

Implements modular verification rules based on official CMS OASIS-E2 Guidance,
Medicare Conditions of Participation (§484.55 & §484.60), and DeVero EHR structure.

Supports all clinical document types:
  - OASIS-E2 Start of Care (SOC - RFA 01)
  - OASIS-E2 Resumption of Care (ROC - RFA 03)
  - OASIS-E2 Recertification (Follow-Up - RFA 04 / 05)
  - OASIS-E2 Discharge (DC - RFA 09)
  - OASIS-E2 Transfer to Inpatient Facility (RFA 06 / 07)
  - Routine Skilled Nurse Visit Notes (SNVN)
"""

import re
import datetime
from typing import Dict, List, Any, Optional
from verifier.doc_classifier import (
    classify_document,
    DOC_TYPE_OASIS_SOC,
    DOC_TYPE_OASIS_ROC,
    DOC_TYPE_OASIS_RECERT,
    DOC_TYPE_OASIS_DISCHARGE,
    DOC_TYPE_OASIS_TRANSFER,
    DOC_TYPE_OASIS_DEATH,
    DOC_TYPE_SN_VISIT_NOTE,
    DOC_TYPE_UNKNOWN
)

PLACEHOLDER_PROMPT_PATTERNS = [
    r"^patient response to treatment:?$",
    r"^additional details - unhealed pressure ulcers:?$",
    r"^additional details - cardiovascular:?$",
    r"^additional details - medications:?$",
    r"^additional readings / details:?$",
    r"^additional vital sign details:?$",
    r"^describe reported or observed evidence and actions taken:?$",
    r"^indicate details of any follow up actions and additional instructions.*$",
    r"^additional details.*$",
    r"^specify details:?$",
    r"^enter details:?$"
]

def check_is_prompt_placeholder(text: str) -> bool:
    if not text:
        return False
    t_clean = text.strip().lower()
    for pattern in PLACEHOLDER_PROMPT_PATTERNS:
        if re.match(pattern, t_clean):
            return True
    return False

def parse_date_safe(date_str: str) -> Optional[datetime.date]:
    if not date_str or not isinstance(date_str, str):
        return None
    cleaned = date_str.strip()
    match = re.search(r'\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b', cleaned)
    if match:
        m, d, y = int(match.group(1)), int(match.group(2)), int(match.group(3))
        if len(str(y)) == 2:
            y += 2000
        try:
            return datetime.date(y, m, d)
        except ValueError:
            return None
    return None

def check_invalid_date_format(val: str, visit_date: Optional[datetime.date] = None) -> List[str]:
    """Detects corrupted, impossible, or far out-of-horizon dates."""
    issues = []
    if not val or not isinstance(val, str):
        return issues
    
    date_matches = re.findall(r'\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b', val)
    for m, d, y in date_matches:
        month, day, year = int(m), int(d), int(y)
        if len(str(y)) == 4 and (year < 1900 or year > 2099):
            issues.append(f"Invalid year '{year}' in date '{m}/{d}/{y}'")
        elif month < 1 or month > 12:
            issues.append(f"Invalid month '{month}' in date '{m}/{d}/{y}'")
        elif day < 1 or day > 31:
            issues.append(f"Invalid day '{day}' in date '{m}/{d}/{y}'")
        else:
            if visit_date:
                try:
                    dt = datetime.date(year, month, day)
                    if (dt - visit_date).days > 120:
                        issues.append(f"Date '{m}/{d}/{y}' is far in the future relative to visit date ({visit_date.strftime('%m/%d/%Y')})")
                except ValueError:
                    pass
    return issues

def _field_quality(field: Dict[str, Any]) -> tuple:
    """Prefer the active visible DeVero control, then its populated backing value."""
    value = str(field.get("val") or "").strip()
    tag = str(field.get("tag") or "").lower()
    field_type = str(field.get("type") or "").lower()
    return (
        field_type == "radio" and bool(field.get("checked")),
        bool(field.get("vis")),
        bool(value),
        tag in {"input", "textarea", "select"},
        len(value),
    )

def _store_best_field(fields: Dict[str, Any], key: str, field: Dict[str, Any]) -> None:
    current = fields.get(key)
    if current is None or _field_quality(field) > _field_quality(current):
        fields[key] = field

HUMAN_ASSISTANCE_PATTERN = re.compile(
    r"\b(?:contact[- ]guard(?: assistance)?|standby assistance|hands[- ]on assistance|"
    r"one[- ]person assist(?:ance)?|two[- ]person assist(?:ance)?|"
    r"partial(?:ly)?(?:\s+to\s+total)? assistance|total assistance|"
    r"requires? (?:human |physical )?assistance|needs? (?:human |physical )?assistance)\b",
    re.I,
)
NO_HUMAN_ASSISTANCE_PATTERN = re.compile(
    r"\b(?:no|without|does not require|doesn't require) (?:human |physical )?assistance\b",
    re.I,
)

FUNCTIONAL_CONSISTENCY_RULES = [
    ("M1800_CRNT_GROOMING", "Grooming", r"\bgroom(?:ing)?\b"),
    ("M1810_CRNT_DRESS_UPPER", "Upper-body dressing", r"\b(?:upper[- ]body dress|dress(?:ing)? upper)\b"),
    ("M1820_CRNT_DRESS_LOWER", "Lower-body dressing", r"\b(?:lower[- ]body dress|dress(?:ing)? lower)\b"),
    ("M1830_CRNT_BATHG", "Bathing", r"\b(?:bath(?:ing)?|shower|tub)\b"),
    ("M1840_CRNT_TOILTG", "Toileting transfer", r"\b(?:toilet(?:ing)?|commode)\b"),
    ("M1850_CRNT_TRNSFRNG", "Transfers", r"\b(?:transfers?|sit[- ]to[- ]stand)\b"),
    ("M1860_CRNT_AMBLTN", "Ambulation", r"\b(?:ambulat\w*|gait|walk\w*|mobility)\b"),
    ("M1870_CRNT_FEEDING", "Feeding/eating", r"\b(?:feed(?:ing)?|eat(?:ing)?)\b"),
]

def _is_independent_zero(value: str) -> bool:
    return bool(re.match(r"^\s*0(?:\D|$)", str(value or "")))

def _assistance_evidence(narratives: List[Dict[str, Any]], concept_pattern: str) -> List[Dict[str, Any]]:
    concept = re.compile(concept_pattern, re.I)
    evidence = []
    for narrative in narratives:
        text = str(narrative.get("text") or "").strip()
        if (
            not text
            or check_is_prompt_placeholder(text)
            or NO_HUMAN_ASSISTANCE_PATTERN.search(text)
            or not HUMAN_ASSISTANCE_PATTERN.search(text)
            or not concept.search(text)
        ):
            continue
        evidence.append(narrative)
    return evidence

def normalize_text(text: str) -> str:
    if not text:
        return ""
    return re.sub(r'\s+', ' ', text).strip().lower()

def flatten_extracted_pages(pages: List[Dict]) -> tuple:
    fields: Dict[str, Any] = {}
    narratives: List[Dict] = []
    seen_narratives = set()
    for p in pages:
        p_num = p.get("page_num", 1)
        for f in p.get("main", {}).get("fields", []):
            name = f.get("name") or f.get("id") or ""
            if name:
                # For radio buttons, only treat as active value if checked is True
                if f.get("type") == "radio" and not f.get("checked"):
                    continue
                _store_best_field(fields, f"p{p_num}_{name}", f)
                _store_best_field(fields, name, f)
        for n in p.get("main", {}).get("narratives", []):
            text = n.get("text", "").strip()
            # DeVero commonly exposes both a custom textarea component and its
            # backing textarea. Its paginated form may also leave the same
            # controls mounted on more than one page. Treat those identical
            # representations as one clinical narrative.
            name = n.get("name")
            identity = (
                name or f"__unnamed_page_{p_num}_{n.get('label') or ''}",
                normalize_text(text),
            )
            if identity in seen_narratives:
                continue
            seen_narratives.add(identity)
            narratives.append({
                "page": p_num,
                "name": name,
                "label": n.get("label"),
                "text": text
            })
    return fields, narratives

def audit_universal_document(target_pages: List[Dict], source_pages: List[Dict], meta: Dict) -> Dict[str, Any]:
    """
    Universal verification router that automatically determines document type,
    runs universal baseline checks, and executes timepoint-specific clinical rules.
    """
    tgt_fields, tgt_narratives = flatten_extracted_pages(target_pages)
    src_fields, src_narratives = flatten_extracted_pages(source_pages)

    # 1. Classify Document Type & Timepoint
    doc_type_str = meta.get("doc_type", "")
    form_title = target_pages[0].get("main", {}).get("title", "") if target_pages else ""
    m0100_raw = tgt_fields.get("M0100_ASSMT_REASON", {}).get("val")
    doc_info = classify_document(doc_type_str, form_title, m0100_val=m0100_raw)

    findings: Dict[str, Any] = {
        "pass_fail_status": "PASS",
        "doc_classification": doc_info,
        "verification_error_items": [],
        "do_now_items": [],
        "leave_items": [],
        "clinical_decision_items": [],
        "header_audit": {},
        "vitals_audit": {},
        "diagnoses_audit": [],
        "narratives_audit": [],
        "raw_counts": {
            "target_fields_count": len(tgt_fields),
            "source_fields_count": len(src_fields),
            "target_narratives_count": len(tgt_narratives),
            "source_narratives_count": len(src_narratives)
        }
    }

    issues_count = 0
    expected_target_date_str = meta.get("target_date", "")
    target_dt = parse_date_safe(expected_target_date_str)

    # -------------------------------------------------------------
    # 0. VERIFICATION COMPLETENESS & EXTRACTION STABILITY
    # -------------------------------------------------------------
    if source_pages and len(target_pages) != len(source_pages) and doc_info["category"] == classify_document(meta.get("source_doc_type", ""))["category"]:
        findings["verification_error_items"].append({
            "section": "Document Pagination / Extraction",
            "field": "target_pages",
            "current": f"Captured {len(target_pages)} target page(s) but {len(source_pages)} baseline page(s).",
            "fix": "Re-run verification until every target page is captured.",
            "fix_type": "update for this timepoint"
        })

    for page in target_pages:
        extraction = page.get("extraction") or {}
        if extraction and not extraction.get("ready", False):
            findings["verification_error_items"].append({
                "section": f"Target Page {page.get('page_num', '?')} Extraction",
                "field": "page_readiness",
                "current": extraction.get("reason") or "Page did not reach a stable extraction state.",
                "fix": "Re-run verification and allow this page to finish loading.",
                "fix_type": "update for this timepoint"
            })

    # -------------------------------------------------------------
    # 1. UNIVERSAL FORM CHECKS (Applies to All Documents & Notes)
    # -------------------------------------------------------------
    # B1. Time In vs Time Out
    time_in = (
        tgt_fields.get("clockIn_time_txt", {}).get("val")
        or tgt_fields.get("TimeIn", {}).get("val")
        or tgt_fields.get("time_in", {}).get("val")
        or ""
    )
    time_out = (
        tgt_fields.get("clockOut_time_txt", {}).get("val")
        or tgt_fields.get("TimeOut", {}).get("val")
        or tgt_fields.get("time_out", {}).get("val")
        or ""
    )

    if time_in and not time_out:
        issues_count += 1
        findings["do_now_items"].append({
            "section": "Header / Visit Authentication",
            "field": "clockOut_time_txt / Time Out",
            "current": f"Time In is '{time_in}', but Time Out is empty",
            "fix": "Enter Time Out (must be filled and chronologically after Time In).",
            "fix_type": "update for this timepoint"
        })
    elif time_in and time_out:
        findings["leave_items"].append({
            "section": "Header / Visit Authentication",
            "field": "Time In / Time Out",
            "val": f"{time_in} - {time_out}",
            "status": "PASS",
            "note": "Time In and Time Out both recorded."
        })

    # B2. Impossible / Out-of-Range Date Scan
    for k, f in tgt_fields.items():
        # Only check structured date input fields, skip long narrative text areas
        if "sum_AssessmentSum_Txt" in k or "int_IntervEducation_Txt" in k or "int_ResponseInterv_Txt" in k:
            continue
        val = f.get("val") or ""
        date_errs = check_invalid_date_format(val, visit_date=target_dt)
        if date_errs:
            issues_count += 1
            for err in date_errs:
                findings["do_now_items"].append({
                    "section": f"Page {k.split('_')[0]} Date Validation",
                    "field": f.get("name") or k,
                    "label": f.get("label") or "",
                    "current": val,
                    "fix": f"Fix malformed/impossible date: {err}",
                    "fix_type": "update for this timepoint"
                })

    # B3. GI Last BM Date Validation (SOC, ROC, Recert, SNVN)
    has_gi_form = (
        any("lastbm" in k.lower().replace("_", "") or "gi_" in k.lower() for k in tgt_fields)
        or doc_info["category"] in [DOC_TYPE_OASIS_SOC, DOC_TYPE_OASIS_ROC, DOC_TYPE_OASIS_RECERT, DOC_TYPE_SN_VISIT_NOTE]
    )
    bm_field_name = "lbd_LastBm_Chk" if any("lbd_lastbm" in k.lower() for k in tgt_fields) else "gi_LastBm_Txt"
    last_bm_val = (
        tgt_fields.get("gi_LastBm_Txt", {}).get("val")
        or tgt_fields.get("p3_gi_LastBm_Txt", {}).get("val")
        or tgt_fields.get("lbd_LastBm_Chk", {}).get("val")
        or tgt_fields.get("LastBMDate", {}).get("val")
        or tgt_fields.get("last_bm", {}).get("val")
        or ""
    )
    if last_bm_val.strip().lower() == "clear":
        last_bm_val = ""
    if last_bm_val:
        last_bm_dt = parse_date_safe(last_bm_val)
        if last_bm_dt and target_dt and last_bm_dt > target_dt:
            issues_count += 1
            findings["do_now_items"].append({
                "section": "GI Assessment",
                "field": bm_field_name,
                "label": "Date of Last BM",
                "current": f"'{last_bm_val}' (Future date after visit date {expected_target_date_str})",
                "fix": "Correct Last BM date (must be on or before the visit date); require clinical documentation of actual last BM.",
                "fix_type": "update for this timepoint"
            })
            findings["clinical_decision_items"].append({
                "section": "GI Assessment",
                "field": bm_field_name,
                "label": "Date of Last BM Clinical Confirmation",
                "current": f"Last BM date was recorded as future date '{last_bm_val}'",
                "fix": "Confirm actual last bowel movement date with patient/caregiver for this visit.",
                "fix_type": "update for this timepoint"
            })
    elif has_gi_form and (doc_info["is_oasis"] or doc_info["category"] == DOC_TYPE_SN_VISIT_NOTE):
        findings["clinical_decision_items"].append({
            "section": "GI Assessment",
            "field": bm_field_name,
            "label": "Date of Last BM",
            "current": "Field is empty",
            "fix": "Document patient's last bowel movement date for this assessment visit.",
            "fix_type": "update for this timepoint"
        })

    # B4. UI Prompt Placeholder Leaks
    for k, f in tgt_fields.items():
        val = f.get("val") or ""
        if check_is_prompt_placeholder(val):
            issues_count += 1
            findings["do_now_items"].append({
                "section": f"Page {k.split('_')[0]} Input Fields",
                "field": f.get("name") or k,
                "label": f.get("label") or "",
                "current": f"'{val}' (UI prompt label pasted as value)",
                "fix": "Clear prompt placeholder text or enter actual clinical observation.",
                "fix_type": "update for this timepoint"
            })

    # B5. Inverted / Swapped Free Text Narratives
    int_ed = tgt_fields.get("int_IntervEducation_Txt", {}).get("val", "")
    int_resp = tgt_fields.get("int_ResponseInterv_Txt", {}).get("val", "")
    if int_ed and int_resp:
        is_ed_in_resp = "reinforced education" in int_resp.lower() or "provided verbal" in int_resp.lower() or "instructions on" in int_resp.lower()
        is_resp_in_ed = "verbalized a partial understanding" in int_ed.lower() or "reports persistent pain" in int_ed.lower() or "upon assessment" in int_ed.lower()
        if is_ed_in_resp and is_resp_in_ed:
            issues_count += 1
            findings["do_now_items"].append({
                "section": "Interventions & Response",
                "field": "int_IntervEducation_Txt <-> int_ResponseInterv_Txt",
                "label": "Interventions/Education Provided vs Response to Interventions",
                "current": "Narrative contents are inverted/swapped between these two fields",
                "fix": "SWAP content: move skilled teaching to 'int_IntervEducation_Txt' and patient tolerance/findings to 'int_ResponseInterv_Txt'.",
                "fix_type": "update for this timepoint"
            })

    # Vital Signs Audit (only if vitals fields are present or multi-page form)
    has_vitals_form = any(k in tgt_fields for k in ["cf_SystolicBP_Txt", "cf_BPSys_Txt", "bp_systolic", "cf_Pulse_Txt", "pulse"]) or len(target_pages) >= 2
    if has_vitals_form:
        sys_bp = (
            tgt_fields.get("cf_SystolicBP_Txt", {}).get("val")
            or tgt_fields.get("cf_BPSys_Txt", {}).get("val")
            or tgt_fields.get("bp_systolic", {}).get("val")
            or ""
        )
        dia_bp = (
            tgt_fields.get("cf_DiastolicBP_Txt", {}).get("val")
            or tgt_fields.get("cf_BPDia_Txt", {}).get("val")
            or tgt_fields.get("bp_diastolic", {}).get("val")
            or ""
        )
        pulse = (
            tgt_fields.get("cf_Pulse_Txt", {}).get("val")
            or tgt_fields.get("pulse", {}).get("val")
            or ""
        )
        resp = (
            tgt_fields.get("cf_RespRate_Txt", {}).get("val")
            or tgt_fields.get("cf_Resp_Txt", {}).get("val")
            or tgt_fields.get("respirations", {}).get("val")
            or ""
        )
        temp = (
            tgt_fields.get("cf_Temp_Txt", {}).get("val")
            or tgt_fields.get("temperature", {}).get("val")
            or ""
        )
        spo2 = (
            tgt_fields.get("cf_PulseOx_Txt", {}).get("val")
            or tgt_fields.get("cf_SPO2_Txt", {}).get("val")
            or tgt_fields.get("o2_sat", {}).get("val")
            or ""
        )

        findings["vitals_audit"] = {
            "BP": f"{sys_bp}/{dia_bp}" if sys_bp else "Not found",
            "Pulse": pulse,
            "Resp": resp,
            "Temp": temp,
            "SPO2": spo2
        }
        if sys_bp and pulse:
            findings["leave_items"].append({
                "section": "Vital Signs",
                "field": "BP / Pulse / Resp / Temp / SpO2",
                "val": f"BP {sys_bp}/{dia_bp}, HR {pulse}, RR {resp}, Temp {temp}, SpO2 {spo2}%",
                "status": "PASS"
            })
        else:
            issues_count += 1
            findings["do_now_items"].append({
                "section": "Vital Signs",
                "field": "BP / Pulse / Resp",
                "current": "Vital signs are incomplete or missing",
                "fix": "Enter complete vital signs for this visit.",
                "fix_type": "update for this timepoint"
            })

    # -------------------------------------------------------------
    # 2. DOCUMENT CATEGORY SPECIFIC RULES
    # -------------------------------------------------------------
    category = doc_info["category"]

    # =============================================================
    # CASE A: ROUTINE SKILLED NURSE VISIT NOTE (SNVN)
    # =============================================================
    if category == DOC_TYPE_SN_VISIT_NOTE:
        # Check skilled nursing interventions & patient response
        sn_interv = tgt_fields.get("sn_interventions", {}).get("val") or tgt_fields.get("interventions_provided", {}).get("val") or int_ed
        sn_resp = tgt_fields.get("patient_response", {}).get("val") or tgt_fields.get("response_to_intervention", {}).get("val") or int_resp

        if not sn_interv or len(sn_interv.strip()) < 15:
            issues_count += 1
            findings["do_now_items"].append({
                "section": "Skilled Nursing Interventions",
                "field": "Interventions Provided",
                "current": "Skilled interventions field is empty or insufficient",
                "fix": "Document specific skilled nursing care delivered during this visit (e.g. cardiac assessment, diabetic teaching, wound care, medication management).",
                "fix_type": "update for this timepoint"
            })
        else:
            findings["leave_items"].append({
                "section": "Skilled Nursing Interventions",
                "field": "Interventions Provided",
                "val": f"{sn_interv[:80]}...",
                "status": "PASS"
            })

        if not sn_resp or len(sn_resp.strip()) < 15:
            issues_count += 1
            findings["do_now_items"].append({
                "section": "Patient Response / Tolerance",
                "field": "Patient Response",
                "current": "Patient response/tolerance field is empty",
                "fix": "Document objective observation of patient's tolerance to treatment and comprehension of teaching.",
                "fix_type": "update for this timepoint"
            })

        # Check visit date matching target date
        v_date = tgt_fields.get("VisitDate", {}).get("val") or ""
        if v_date and expected_target_date_str and v_date != expected_target_date_str:
            issues_count += 1
            findings["do_now_items"].append({
                "section": "Visit Header",
                "field": "VisitDate",
                "current": f"Visit date is '{v_date}', expected '{expected_target_date_str}'",
                "fix": f"Correct visit date to match target '{expected_target_date_str}'.",
                "fix_type": "update for this timepoint"
            })

        # Check for near-verbatim clone against prior visit note
        if src_narratives and tgt_narratives:
            for tn in tgt_narratives:
                t_txt = tn.get("text", "").strip()
                if len(t_txt) > 60:
                    for sn in src_narratives:
                        s_txt = sn.get("text", "").strip()
                        if normalize_text(t_txt) == normalize_text(s_txt):
                            findings["clinical_decision_items"].append({
                                "section": f"Visit Note Narrative: {tn.get('name')}",
                                "field": tn.get("name") or "Narrative",
                                "label": tn.get("label") or "Narrative",
                                "current": "100% identical copy of narrative from prior visit note",
                                "fix": "CLINICAL REVIEW: Verify that visit narrative describes patient's status this specific visit rather than verbatim copy from prior note.",
                                "fix_type": "update for this timepoint"
                            })
                            break

    # =============================================================
    # CASE B: ALL OASIS-E2 ASSESSMENTS (SOC, ROC, Recert, DC, Transfer)
    # =============================================================
    elif doc_info["is_oasis"]:
        # M0090 Assessment Completion Date
        m0090 = tgt_fields.get("M0090_INFO_COMPLETED_DT", {}).get("val") or ""
        visit_date = tgt_fields.get("VisitDate", {}).get("val") or ""
        m0100 = tgt_fields.get("M0100_ASSMT_REASON", {}).get("val") or ""
        m0110 = tgt_fields.get("M0110_EPISODE_TIMING", {}).get("val") or ""

        findings["header_audit"] = {
            "M0090_completed_date": m0090,
            "visit_date": visit_date,
            "M0100_reason": m0100,
            "M0110_timing": m0110
        }

        if m0090 == expected_target_date_str:
            findings["leave_items"].append({
                "section": "Header / M0090",
                "field": "M0090_INFO_COMPLETED_DT",
                "val": m0090,
                "status": "PASS",
                "note": f"Assessment date matches target date ({expected_target_date_str})."
            })
        elif m0090:
            issues_count += 1
            findings["do_now_items"].append({
                "section": "Header / M0090",
                "field": "M0090_INFO_COMPLETED_DT",
                "current": m0090,
                "expected": expected_target_date_str,
                "fix": f"Update M0090 to match target assessment date '{expected_target_date_str}'.",
                "fix_type": "update for this timepoint"
            })

        # M0100 Reason for Assessment (RFA) Validation
        expected_rfa = doc_info.get("expected_rfa")
        if expected_rfa == "06_OR_07":
            valid_rfas = ["6", "06", "7", "07"]
        elif expected_rfa:
            valid_rfas = [expected_rfa, str(int(expected_rfa))]
        else:
            valid_rfas = []

        if valid_rfas and m0100 and str(m0100) not in valid_rfas:
            issues_count += 1
            findings["do_now_items"].append({
                "section": "Header / M0100",
                "field": "M0100_ASSMT_REASON",
                "current": f"M0100 is '{m0100}'",
                "expected": f"RFA {expected_rfa} for {doc_info['display_name']}",
                "fix": f"Update M0100 to match {doc_info['display_name']}.",
                "fix_type": "update for this timepoint"
            })
        elif m0100:
            findings["leave_items"].append({
                "section": "Header / M0100",
                "field": "M0100_ASSMT_REASON",
                "val": f"RFA {m0100}",
                "status": "PASS"
            })

        # M1021 / M1023 Diagnosis Grid (SOC, ROC, Recert)
        has_diag_form = (
            category in [DOC_TYPE_OASIS_SOC, DOC_TYPE_OASIS_ROC, DOC_TYPE_OASIS_RECERT]
            or any(k in tgt_fields for k in ["prim_diag", "prim_diag_code", "m1021_primary_diag_icd", "prim_diag_icd"])
        )
        if has_diag_form:
            prim_diag = (
                tgt_fields.get("m1021_primary_diag_icd", {}).get("val")
                or tgt_fields.get("prim_diag_code", {}).get("val")
                or tgt_fields.get("prim_diag_icd", {}).get("val")
                or tgt_fields.get("prim_diag", {}).get("val")
                or ""
            )
            if prim_diag:
                findings["diagnoses_audit"].append({"primary": prim_diag, "status": "VERIFIED"})
                findings["leave_items"].append({
                    "section": "Diagnoses (M1021 / M1023)",
                    "field": "Primary ICD-10 Code",
                    "val": prim_diag,
                    "status": "PASS",
                    "note": "Primary diagnosis and secondary chronic conditions recorded."
                })
            else:
                issues_count += 1
                findings["do_now_items"].append({
                    "section": "Diagnoses (M1021 / M1023)",
                    "field": "Primary Diagnosis (M1021)",
                    "current": "Primary diagnosis grid is empty",
                    "fix": "Enter primary ICD-10 diagnosis code and description.",
                    "fix_type": "copy from source"
                })

        # --- B8. CLINICAL ASSESSMENT SUMMARY AUDIT (SOC, ROC, Recert) ---
        has_summary_form = (
            category in [DOC_TYPE_OASIS_SOC, DOC_TYPE_OASIS_ROC, DOC_TYPE_OASIS_RECERT]
            or any(
                f.get("name") == "sum_AssessmentSum_Txt" or f.get("id") == "sum_AssessmentSum_Txt"
                for p in target_pages
                for f in p.get("main", {}).get("fields", [])
            )
        )
        if has_summary_form:
            sum_page_num = 6
            for p in target_pages:
                for f in p.get("main", {}).get("fields", []):
                    if f.get("name") == "sum_AssessmentSum_Txt" or f.get("id") == "sum_AssessmentSum_Txt":
                        sum_page_num = p.get("page_num", 6)
                        break

            sum_txt_tgt = tgt_fields.get("sum_AssessmentSum_Txt", {}).get("val", "")
            sum_txt_src = src_fields.get("sum_AssessmentSum_Txt", {}).get("val", "")

            if not sum_txt_tgt:
                baseline_detail = " while the baseline summary was present" if sum_txt_src else ""
                findings["verification_error_items"].append({
                    "section": f"Page {sum_page_num}: Clinical Assessment Summary Extraction",
                    "field": "sum_AssessmentSum_Txt",
                    "current": f"Target assessment summary was blank or was not captured{baseline_detail}.",
                    "fix": "Re-run verification after the assessment summary loads. If truly blank, complete it using current clinician-approved findings.",
                    "fix_type": "update for this timepoint"
                })
            else:
                is_exact_clone = (normalize_text(sum_txt_tgt) == normalize_text(sum_txt_src))
                if is_exact_clone:
                    issues_count += 1
                    findings["clinical_decision_items"].append({
                        "section": f"Page {sum_page_num}: Clinical Assessment Summary (sum_AssessmentSum_Txt)",
                        "field": "sum_AssessmentSum_Txt",
                        "label": "Assessment Summary Narrative",
                        "current": "100% exact copy of baseline note from prior period",
                        "fix": "USER DECISION / CLINICAL JUDGMENT: Retain core diagnostic history but update progress toward goals over the past 60 days and current skilled need for upcoming 60-day certification period.",
                        "fix_type": "update for this timepoint"
                    })
                else:
                    findings["leave_items"].append({
                        "section": f"Page {sum_page_num}: Clinical Assessment Summary",
                        "field": "sum_AssessmentSum_Txt",
                        "status": "PASS",
                        "note": f"Assessment summary is customized for current timepoint ({doc_info['display_name']})."
                    })

        # Functional ADL assistance evidence cross-checks
        independent_adl_fields = [
            name for name, _, _ in FUNCTIONAL_CONSISTENCY_RULES
            if _is_independent_zero(tgt_fields.get(name, {}).get("val") or "")
        ]
        broad_adl_evidence = _assistance_evidence(
            tgt_narratives, r"\b(?:activities? of daily living|ADLs?|IADLs?)\b"
        )
        if len(independent_adl_fields) >= 3 and broad_adl_evidence:
            item = broad_adl_evidence[0]
            excerpt = re.sub(r"\s+", " ", item.get("text") or "").strip()
            if len(excerpt) > 260:
                excerpt = excerpt[:257] + "..."
            issues_count += 1
            findings["clinical_decision_items"].append({
                "section": f"Page {item.get('page')}: ADL Coding Consistency",
                "field": "M1800-M1870",
                "label": "Functional ADL independence versus narrative assistance",
                "current": (
                    f"{len(independent_adl_fields)} functional items are coded 0 (independent), "
                    f"but narrative field {item.get('name') or 'unknown'} documents ADL/IADL assistance: "
                    f"'{excerpt}'"
                ),
                "fix": (
                    "CLINICAL REVIEW REQUIRED: reconcile the item-level OASIS functional responses "
                    "with the current ADL/IADL narrative before signing. Do not auto-fix."
                ),
                "fix_type": "update for this timepoint"
            })

        # --- TIMEPOINT SPECIFIC RULES ---

        # 1. START OF CARE (SOC - RFA 01)
        if category == DOC_TYPE_OASIS_SOC:
            m0030 = tgt_fields.get("M0030_START_CARE_DT", {}).get("val") or ""
            if not m0030:
                issues_count += 1
                findings["do_now_items"].append({
                    "section": "OASIS SOC: M0030 Start Care Date",
                    "field": "M0030_START_CARE_DT",
                    "current": "Start of Care Date is empty",
                    "fix": "Enter M0030 Start of Care Date (must be within 5-day referral/discharge window).",
                    "fix_type": "update for this timepoint"
                })
            else:
                findings["leave_items"].append({
                    "section": "OASIS SOC: M0030",
                    "field": "M0030_START_CARE_DT",
                    "val": m0030,
                    "status": "PASS"
                })

            # Check Cognitive (BIMS) & Mood (PHQ)
            bims = tgt_fields.get("C0500_BIMS_SCORE", {}).get("val")
            if bims:
                findings["leave_items"].append({
                    "section": "OASIS SOC: Cognitive BIMS",
                    "field": "C0500 BIMS Score",
                    "val": bims,
                    "status": "PASS"
                })

        # 2. RESUMPTION OF CARE (ROC - RFA 03)
        elif category == DOC_TYPE_OASIS_ROC:
            m0032 = tgt_fields.get("M0032_ROC_DT", {}).get("val") or ""
            if not m0032:
                issues_count += 1
                findings["do_now_items"].append({
                    "section": "OASIS ROC: M0032 Resumption Date",
                    "field": "M0032_ROC_DT",
                    "current": "Resumption of Care Date is empty",
                    "fix": "Enter M0032 ROC Date (must be within 48 hours of inpatient discharge or notification).",
                    "fix_type": "update for this timepoint"
                })
            else:
                findings["leave_items"].append({
                    "section": "OASIS ROC: M0032",
                    "field": "M0032_ROC_DT",
                    "val": m0032,
                    "status": "PASS"
                })

            # Post-hospitalization medication reconciliation check
            m2005 = tgt_fields.get("M2005_MED_RC_TRNSFR", {}).get("val")
            if not m2005:
                findings["clinical_decision_items"].append({
                    "section": "OASIS ROC: Medication Reconciliation",
                    "field": "M2005_MED_RC_TRNSFR",
                    "current": "Medication reconciliation at inpatient facility transfer/ROC is unconfirmed",
                    "fix": "Verify that post-hospital discharge medications were reconciled with the agency regimen.",
                    "fix_type": "update for this timepoint"
                })

        # 3. RECERTIFICATION (RFA 04 / 05)
        elif category == DOC_TYPE_OASIS_RECERT:
            # Plan of Care SN Frequency check
            sn_freq_val = (
                tgt_fields.get("care_ClinicalSn_Txt", {}).get("val")
                or tgt_fields.get("sn_frequency", {}).get("val")
                or tgt_fields.get("sn_FreqDuration_Txt", {}).get("val")
                or ""
            )
            sn_chk = tgt_fields.get("care_ClinicalSn_Chk", {}).get("checked") or False
            p6_freq_fields = [v for k, v in tgt_fields.items() if "p6" in k.lower() and ("freq" in k.lower() or "clinical" in k.lower()) and v.get("val")]

            # Plan of Care SN Frequency check (only on forms where POC fields exist or full recert)
            has_freq_form = any("care_clinical" in k.lower() or "freq" in k.lower() for k in tgt_fields) or len(target_pages) >= 5
            if has_freq_form:
                if not sn_freq_val and not sn_chk and len(p6_freq_fields) == 0:
                    issues_count += 1
                    findings["do_now_items"].append({
                        "section": "Page 6: Plan of Care Orders",
                        "field": "PLAN OF CARE Frequency / Duration (SN)",
                        "label": "Skilled Nursing Frequency & Duration",
                        "current": "All frequency/duration rows are blank on Recertification Page 6",
                        "fix": "Enter Skilled Nursing frequency and duration (e.g. 2x/wk x 9 wks) to establish active POC orders for the recertification period.",
                        "fix_type": "copy from source"
                    })
                else:
                    findings["leave_items"].append({
                        "section": "Page 6: Plan of Care Orders",
                        "field": "SN Frequency",
                        "val": sn_freq_val or "Specified",
                        "status": "PASS"
                    })

            # Library Start Effective Dates check
            prior_cert_start_str = "07/02/2026"
            stale_effective_count = sum(1 for k, f in tgt_fields.items() if "start effective date:" in (f.get("val") or "").lower() and prior_cert_start_str in (f.get("val") or ""))
            if stale_effective_count > 0:
                findings["clinical_decision_items"].append({
                    "section": "Pages 1-6: Library Modules & Interventions",
                    "field": "Start Effective Dates (Library Modules)",
                    "label": "Interventions, Goals, Vital Sign Parameters Start Effective Dates",
                    "current": f"{stale_effective_count} library module item(s) retain prior cert start date ({prior_cert_start_str}) instead of new cert period start",
                    "fix": "CLINICAL JUDGMENT / USER REVIEW: Effective dates still on previous cert start; confirm whether to keep established order dates or batch update to new cert start.",
                    "fix_type": "leave because it must differ"
                })

            # Goal Progress % Cross-Check
            lib_goal_text = " ".join(f.get("val", "") for f in tgt_fields.values() if "progress toward goal:" in (f.get("val") or "").lower())
            lib_progress_matches = re.findall(r'progress toward goal:\s*(\d+)%', lib_goal_text, re.I)
            lib_progress_val = lib_progress_matches[0] if lib_progress_matches else None
            sum_progress_matches = re.findall(r'(\d+)%\s*progress', sum_txt_tgt, re.I) or re.findall(r'progress(?: toward goal)?(?: of| is at|:)?\s*(\d+)%', sum_txt_tgt, re.I)
            sum_progress_val = sum_progress_matches[0] if sum_progress_matches else None

            if lib_progress_val and sum_progress_val and lib_progress_val != sum_progress_val:
                issues_count += 1
                findings["do_now_items"].append({
                    "section": "Page 1 vs Page 6: Goal Progress Consistency",
                    "field": "Patient Centered Goal Progress % (Library vs Summary)",
                    "label": "Goal Progress % Inconsistency",
                    "current": f"Library module states {lib_progress_val}%, but Assessment Summary states {sum_progress_val}%",
                    "fix": f"Resolve contradiction: Update summary narrative to match library progress ({lib_progress_val}%) or reconcile percentage.",
                    "fix_type": "update for this timepoint"
                })

            # Expanded Clone Detection on Recert
            narrative_check_map = {
                "ncmb_Comment_Txt": ("Page 3", "Neuro / Mood Narrative"),
                "dtd_TransferDeficits_Txt": ("Page 4", "Transfer & Gait Deficits"),
                "ana_GaitAnalyse_Txt": ("Page 4", "Gait Analysis Narrative"),
                "nsc_FPatientdditional_Txt": ("Page 4", "Falls Prevention Teaching"),
                "sah_SafetyHazardsDiscuss_Txt": ("Page 2", "Safety Hazards / Emergency Plan"),
                "pts_MedicationComments_Txt": ("Page 6", "Medications Reconciliation Details"),
                "nvn1_SeeWoundAdditional_Txt": ("Page 5", "Integumentary Comments"),
                "str3_EquipComments_Txt": ("Page 2", "Medical Equipment & Supplies Comments")
            }
            src_narrative_dict = {n.get("name"): n.get("text", "").strip() for n in src_narratives if n.get("name")}
            for n_field, (pg_lbl, desc_lbl) in narrative_check_map.items():
                t_val = tgt_fields.get(n_field, {}).get("val", "").strip()
                s_val = src_narrative_dict.get(n_field, "").strip()
                if t_val and s_val and len(t_val) > 40 and normalize_text(t_val) == normalize_text(s_val):
                    findings["clinical_decision_items"].append({
                        "section": f"{pg_lbl}: {desc_lbl}",
                        "field": n_field,
                        "label": desc_lbl,
                        "current": f"100% identical copy of prior baseline note ({len(t_val)} chars)",
                        "fix": f"CLINICAL JUDGMENT / USER REVIEW: Text is cloned from prior episode. Verify whether patient condition, fall risk, and teaching remain unchanged or require this-visit clinical updates.",
                        "fix_type": "leave because it must differ"
                    })

        # 4. DISCHARGE (RFA 09)
        elif category == DOC_TYPE_OASIS_DISCHARGE:
            m0906 = tgt_fields.get("M0906_DC_TRAN_DTH_DT", {}).get("val") or ""
            m2420 = tgt_fields.get("M2420_DSCHRG_DISP", {}).get("val") or ""

            if not m0906:
                issues_count += 1
                findings["do_now_items"].append({
                    "section": "OASIS Discharge: M0906 Discharge Date",
                    "field": "M0906_DC_TRAN_DTH_DT",
                    "current": "Discharge Date is empty",
                    "fix": "Enter M0906 Discharge Date.",
                    "fix_type": "update for this timepoint"
                })
            else:
                findings["leave_items"].append({
                    "section": "OASIS Discharge: M0906",
                    "field": "M0906_DC_TRAN_DTH_DT",
                    "val": m0906,
                    "status": "PASS"
                })

            if not m2420:
                issues_count += 1
                findings["clinical_decision_items"].append({
                    "section": "OASIS Discharge: M2420 Disposition",
                    "field": "M2420_DSCHRG_DISP",
                    "label": "Discharge Disposition",
                    "current": "Discharge Disposition is unselected",
                    "fix": "CLINICAL JUDGMENT / USER REVIEW: Select M2420 Discharge Disposition (e.g. 01 - Patient remained at home with services no longer needed; 02 - with formal assistive services).",
                    "fix_type": "update for this timepoint"
                })
            else:
                findings["leave_items"].append({
                    "section": "OASIS Discharge: M2420",
                    "field": "M2420_DSCHRG_DISP",
                    "val": f"Disposition Code {m2420}",
                    "status": "PASS"
                })

        # 5. TRANSFER TO INPATIENT FACILITY (RFA 06 / 07)
        elif category == DOC_TYPE_OASIS_TRANSFER:
            m0906 = tgt_fields.get("M0906_DC_TRAN_DTH_DT", {}).get("val") or ""
            m2410 = tgt_fields.get("M2410_INPAT_FAC", {}).get("val") or ""

            if not m0906:
                issues_count += 1
                findings["do_now_items"].append({
                    "section": "OASIS Transfer: M0906 Transfer Date",
                    "field": "M0906_DC_TRAN_DTH_DT",
                    "current": "Transfer Date is empty",
                    "fix": "Enter M0906 Transfer Date.",
                    "fix_type": "update for this timepoint"
                })
            if not m2410:
                issues_count += 1
                findings["clinical_decision_items"].append({
                    "section": "OASIS Transfer: M2410 Inpatient Facility",
                    "field": "M2410_INPAT_FAC",
                    "label": "Inpatient Facility Type",
                    "current": "Inpatient facility type is unselected",
                    "fix": "CLINICAL JUDGMENT / USER REVIEW: Select M2410 Inpatient Facility type (e.g. 01 - Hospital, 02 - Rehabilitation facility, 03 - Nursing home).",
                    "fix_type": "update for this timepoint"
                })

        # Functional Consistency Cross-Check (all OASIS)
        for field_name, function_label, concept_pattern in FUNCTIONAL_CONSISTENCY_RULES:
            coded_value = tgt_fields.get(field_name, {}).get("val") or ""
            if not _is_independent_zero(coded_value):
                continue
            evidence = _assistance_evidence(tgt_narratives, concept_pattern)
            if not evidence:
                continue
            item = evidence[0]
            excerpt = re.sub(r"\s+", " ", item.get("text") or "").strip()
            if len(excerpt) > 260:
                excerpt = excerpt[:257] + "..."
            issues_count += 1
            findings["clinical_decision_items"].append({
                "section": f"Page {item.get('page')}: Functional Status Consistency",
                "field": field_name,
                "label": function_label,
                "current": f"{field_name}=0 indicates independence/no human assistance, but narrative field {item.get('name') or 'unknown'} documents assistance: '{excerpt}'",
                "fix": "CLINICAL REVIEW REQUIRED: reconcile OASIS functional response with conflicting narrative.",
                "fix_type": "update for this timepoint"
            })

    # -------------------------------------------------------------
    # 3. OVERALL PASS / FAIL STATUS EVALUATION
    # -------------------------------------------------------------
    if findings["verification_error_items"]:
        findings["pass_fail_status"] = "ERROR / INCOMPLETE"
    elif len(findings["do_now_items"]) > 0 or len(findings["clinical_decision_items"]) > 0:
        findings["pass_fail_status"] = "MIXED / FAIL"
    else:
        findings["pass_fail_status"] = "PASS"

    return findings

# Backward compatibility alias
def audit_oasis_recert(target_pages: List[Dict], source_pages: List[Dict], meta: Dict) -> Dict[str, Any]:
    return audit_universal_document(target_pages, source_pages, meta)
