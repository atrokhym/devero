"""Pure preparation and validation helpers for clinician-reviewed OASIS assessment summaries.

Supports all OASIS-E2 / EC2 document timepoints:
Start of Care (SOC), Resumption of Care (ROC), Recertification, Discharge, and Transfer.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import tempfile
from typing import Any, Dict, Iterable, List, Tuple

from verifier.oasis_rules import check_is_prompt_placeholder
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


SUMMARY_FIELD = "sum_AssessmentSum_Txt"
PACKAGE_SCHEMA_VERSION = 1


class OASISSummaryError(ValueError):
    pass

# Backwards-compatibility alias
RecertSummaryError = OASISSummaryError


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def text_sha256(value: str) -> str:
    return hashlib.sha256(normalize_text(value).encode("utf-8")).hexdigest()


def _is_ui_prompt(value: str) -> bool:
    text = str(value or "").strip()
    return check_is_prompt_placeholder(text) or (
        len(text) < 160 and text.endswith(":")
    )


def _field_quality(field: Dict[str, Any]) -> tuple:
    value = str(field.get("val") or "").strip()
    field_type = str(field.get("type") or "").lower()
    return (
        field_type == "radio" and bool(field.get("checked")),
        bool(field.get("vis")),
        bool(value),
        str(field.get("tag") or "").lower() in {"input", "textarea", "select"},
        len(value),
    )


def best_field_value(pages: Iterable[Dict[str, Any]], name: str) -> str:
    candidates = []
    for page in pages:
        for field in page.get("main", {}).get("fields", []):
            if field.get("name") == name or field.get("id") == name:
                candidates.append(field)
    if not candidates:
        return ""
    return str(max(candidates, key=_field_quality).get("val") or "").strip()


def _dedupe_current_narratives(pages: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    seen = set()
    for page in pages:
        page_num = page.get("page_num")
        for narrative in page.get("main", {}).get("narratives", []):
            name = narrative.get("name") or ""
            label = str(narrative.get("label") or "").strip()
            text = str(narrative.get("text") or "").strip()
            if (
                name == SUMMARY_FIELD
                or not text
                or _is_ui_prompt(text)
                or (label and normalize_text(text).lower() == normalize_text(label).lower())
            ):
                continue
            key = (name, normalize_text(text))
            if key in seen:
                continue
            seen.add(key)
            results.append({
                "page": page_num,
                "name": name,
                "label": label,
                "text": text,
            })
    return results


def _clinical_field_context(pages: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    prefixes = (
        "cf_", "M102", "m102", "M18", "m18", "M20", "m20",
        "nsc", "pain", "card_", "resp_", "nutr_", "elim_",
    )
    excluded = {SUMMARY_FIELD, "VisitDate", "effectiveDate"}
    best: Dict[Tuple[int, str], Dict[str, Any]] = {}
    for page in pages:
        page_num = int(page.get("page_num") or 0)
        for field in page.get("main", {}).get("fields", []):
            name = str(field.get("name") or field.get("id") or "")
            value = str(field.get("val") or "").strip()
            if (
                not name
                or name in excluded
                or not value
                or len(value) > 300
                or _is_ui_prompt(value)
                or not name.startswith(prefixes)
            ):
                continue
            key = (page_num, name)
            current = best.get(key)
            if current is None or _field_quality(field) > _field_quality(current):
                best[key] = field

    return [{
        "page": page_num,
        "name": name,
        "label": best[(page_num, name)].get("label") or "",
        "value": str(best[(page_num, name)].get("val") or "").strip(),
    } for page_num, name in sorted(best)]


def _clinical_review_flags(
    narratives: List[Dict[str, Any]], structured_fields: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    narrative_text = " ".join(item.get("text") or "" for item in narratives).lower()
    values = {item.get("name"): str(item.get("value") or "").strip() for item in structured_fields}
    flags: List[Dict[str, Any]] = []

    if values.get("M1860_CRNT_AMBLTN") == "0" and (
        "contact guard assistance" in narrative_text
        or "requires assistance" in narrative_text and "ambulation" in narrative_text
    ):
        flags.append({
            "id": "ambulation_independence_conflict",
            "issue": (
                "M1860 is coded 0 (independent ambulation), but current narratives "
                "state that contact-guard assistance and a walker are required."
            ),
            "required_action": "Clinician must identify the accurate current mobility status before drafting.",
            "resolved": False,
            "resolution": "",
            "resolved_by": "",
            "resolved_at_utc": "",
        })

    if values.get("M1850_CRNT_TRNSFRNG") == "0" and (
        "contact guard assistance" in narrative_text and "transfer" in narrative_text
    ):
        flags.append({
            "id": "transfer_independence_conflict",
            "issue": (
                "M1850 is coded 0 (independent transfer), but current narratives "
                "state that contact-guard assistance is required for transfers."
            ),
            "required_action": "Clinician must identify the accurate current transfer status before drafting.",
            "resolved": False,
            "resolution": "",
            "resolved_by": "",
            "resolved_at_utc": "",
        })
    return flags


def _require_complete_audit(audit: Dict[str, Any]) -> None:
    findings = audit.get("audit_results") or {}
    errors = findings.get("verification_error_items") or []
    if errors or findings.get("pass_fail_status") == "ERROR / INCOMPLETE":
        details = "; ".join(
            f"{item.get('section')}: {item.get('current')}" for item in errors
        ) or "audit status is ERROR / INCOMPLETE"
        raise OASISSummaryError(
            f"Cannot prepare an assessment summary from an incomplete audit: {details}"
        )

    task = audit.get("task") or {}
    doc_type = task.get("doc_type") or (findings.get("doc_classification") or {}).get("display_name", "")
    doc_info = classify_document(doc_type)
    category = doc_info.get("category")

    target_pages = audit.get("target_pages") or []
    source_pages = audit.get("source_pages") or []

    if not target_pages:
        raise OASISSummaryError("Target pages are required.")

    # Baseline notes are required for recert, ROC, discharge, etc., but optional for new SOC
    if not source_pages and category not in [DOC_TYPE_OASIS_SOC]:
        raise OASISSummaryError("Both target and completed baseline pages are required.")

    if source_pages and len(target_pages) != len(source_pages) and category == classify_document(task.get("source_doc_type", ""))["category"]:
        raise OASISSummaryError(
            f"Page-count mismatch: target={len(target_pages)}, baseline={len(source_pages)}."
        )

    pages_to_check = target_pages + source_pages
    if any(
        page.get("extraction") and not page["extraction"].get("ready", False)
        for page in pages_to_check
    ):
        raise OASISSummaryError("At least one page did not reach a stable extraction state.")


def prepare_package(audit: Dict[str, Any], audit_file: str) -> Dict[str, Any]:
    _require_complete_audit(audit)
    task = audit.get("task") or {}
    findings = audit.get("audit_results") or {}
    doc_type = task.get("doc_type") or (findings.get("doc_classification") or {}).get("display_name", "")
    doc_info = classify_document(doc_type)
    category = doc_info.get("category")

    target_pages = audit.get("target_pages") or []
    source_pages = audit.get("source_pages") or []

    if category == DOC_TYPE_SN_VISIT_NOTE:
        raise OASISSummaryError(
            "This is a routine Skilled Nurse Visit Note audit. Use "
            "'writer/fix_visit_note2.py draft -t <tasks-file>' instead."
        )
    if category not in {DOC_TYPE_OASIS_SOC, DOC_TYPE_OASIS_ROC, DOC_TYPE_OASIS_RECERT,
                        DOC_TYPE_OASIS_DISCHARGE, DOC_TYPE_OASIS_TRANSFER, DOC_TYPE_OASIS_DEATH}:
        raise OASISSummaryError("An OASIS document is required for assessment summary drafting.")

    current_summary = best_field_value(target_pages, SUMMARY_FIELD)
    baseline_summary = best_field_value(source_pages, SUMMARY_FIELD) if source_pages else ""

    if not current_summary and not baseline_summary:
        raise OASISSummaryError("No current or baseline assessment summary was captured.")
    if category != DOC_TYPE_OASIS_SOC and (not current_summary or not baseline_summary):
        raise OASISSummaryError("Both current and baseline assessment summaries must be captured.")

    current_narratives = _dedupe_current_narratives(target_pages)
    field_context = _clinical_field_context(target_pages)
    if not current_narratives and not field_context:
        raise OASISSummaryError(
            "No independent current-period facts were captured for AI-assisted drafting."
        )

    run_id = (audit.get("audit_run") or {}).get("run_id")
    if not run_id:
        raise OASISSummaryError("A versioned verifier audit with audit_run.run_id is required.")

    # Detect summary page
    summary_page_num = 6
    for page in target_pages:
        for field in page.get("main", {}).get("fields", []) + page.get("main", {}).get("narratives", []):
            if (field.get("name") or field.get("id")) == SUMMARY_FIELD:
                summary_page_num = page.get("page_num", 6)
                break

    display_name = doc_info.get("display_name", "OASIS Assessment")
    if category == DOC_TYPE_OASIS_SOC:
        prompt = (
            f"Draft a proposed replacement for the {display_name} Clinical Assessment "
            "Summary. Use only the verified current-period narratives and structured field "
            "context in this package. Use the agency's concise RN charting style: short paragraphs "
            "ordered by current status, safety/function, focused clinical findings, skilled intervention "
            "and education, documented response, and ongoing skilled need/coordination. Group relevant "
            "diagnoses instead of reproducing the entire problem list. "
            "Honor clinician resolutions in clinical_review_flags and do not draft while any flag is unresolved. "
            "Do not invent, reconcile, or infer missing clinical facts. If necessary facts are absent or contradictory, "
            "list them for clinician input instead of producing a final narrative. Write new visit-specific prose rather "
            "than copying sentences or distinctive clauses from an existing note. Output only the proposed narrative "
            "or the missing-facts list."
        )
    elif category == DOC_TYPE_OASIS_DISCHARGE:
        prompt = (
            f"Draft a proposed replacement for the {display_name} Clinical Assessment "
            "Summary. Use only the verified current-period narratives and structured field "
            "context in this package. Describe discharge status, summary of care provided, "
            "rehabilitation and clinical goals met, and discharge disposition. "
            "The baseline summary may be used only for established historical background that remains supported. "
            "Honor clinician resolutions in clinical_review_flags and do not draft while any flag is unresolved. "
            "Do not invent, reconcile, or infer missing clinical facts. Output only the proposed narrative or the missing-facts list."
        )
    elif category == DOC_TYPE_OASIS_ROC:
        prompt = (
            f"Draft a proposed replacement for the {display_name} Clinical Assessment "
            "Summary. Use only the verified current-period narratives and structured field "
            "context in this package. Describe the patient's post-inpatient status, reasons for resumption, "
            "current clinical findings, and continued skilled nursing need. "
            "The baseline summary may be used only for established historical diagnoses that remain supported. "
            "Honor clinician resolutions in clinical_review_flags and do not draft while any flag is unresolved. "
            "Do not invent, reconcile, or infer missing clinical facts. Output only the proposed narrative or the missing-facts list."
        )
    else:
        prompt = (
            f"Draft a proposed replacement for the {display_name} Clinical Assessment "
            "Summary. Use only the verified current-period narratives and structured field "
            "context in this package. The baseline summary may be used only for established "
            "historical diagnoses/background that remain supported; do not carry forward "
            "time-sensitive findings, medication changes, progress, dates, or skilled need "
            "unless current-period evidence supports them. Describe progress over the prior "
            "certification period and skilled need for the upcoming period only when those "
            "facts are explicit. Honor clinician resolutions in clinical_review_flags and "
            "do not draft while any flag is unresolved. Do not invent, reconcile, or infer missing clinical facts. "
            "If necessary facts are absent or contradictory, list them for clinician input "
            "instead of producing a final narrative. Output only the proposed narrative or "
            "the missing-facts list."
        )

    last_bm_page = next((int(page.get("page_num") or 1) for page in target_pages
                        if any((field.get("name") or field.get("id")) == "lbd_LastBm_Chk"
                               for field in page.get("main", {}).get("fields", []))), None)
    return {
        "schema_version": PACKAGE_SCHEMA_VERSION,
        "created_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "audit_file": os.path.basename(audit_file),
        "audit_run_id": run_id,
        "doc_classification": doc_info,
        "summary_page": summary_page_num,
        "task": {
            "patient": task.get("patient"),
            "caregiver": task.get("caregiver"),
            "doc_type": task.get("doc_type"),
            "target_date": task.get("target_date"),
            "target_required_status": "Pending",
            "source_doc_type": task.get("source_doc_type") or task.get("doc_type"),
            "source_date": task.get("source_date"),
            "source_required_status": "Completed" if source_pages else None,
        },
        "overwrite_scope": {
            "field": SUMMARY_FIELD,
            "permitted_fields": [SUMMARY_FIELD],
            "conditional_fill": {"lbd_LastBm_Chk": "Use target visit date only when blank or Clear; preserve existing values."},
            "final_action": "Save only; remain Pending and unsigned",
        },
        "source_precedence": {
            "current_target_facts": "Control current status, progress, and upcoming skilled need.",
            "completed_baseline": "Historical background only when still supported by current facts.",
            "user_or_clinician_facts": "May be added only after explicit clinician review.",
        },
        "integrity": {
            "expected_current_summary_sha256": text_sha256(current_summary) if current_summary else "",
            "baseline_summary_sha256": text_sha256(baseline_summary) if baseline_summary else "",
        },
        "ai_prompt": prompt,
        "baseline_summary": baseline_summary,
        "current_summary": current_summary,
        "last_bm_page": last_bm_page,
        "verified_current_context": {
            "narratives": current_narratives,
            "structured_fields": field_context,
        },
        "clinical_review_flags": _clinical_review_flags(
            current_narratives, field_context
        ),
        "proposed_summary": "",
        "clinician_review": {
            "approved": False,
            "approved_by": "",
            "approved_at_utc": "",
        },
    }


def validate_package(package: Dict[str, Any], require_approval: bool = True) -> List[str]:
    from writer.fix_visit_note import _contains_concrete_number
    from writer.fix_visit_note2 import REVIEW_LANGUAGE, SUBSCRIPT_DIGITS

    errors: List[str] = []
    if package.get("schema_version") != PACKAGE_SCHEMA_VERSION:
        errors.append("Unsupported package schema version.")
    task = package.get("task") or {}
    required_task_keys = ["patient", "caregiver", "doc_type", "target_date"]
    if (package.get("doc_classification") or {}).get("category") != DOC_TYPE_OASIS_SOC:
        required_task_keys.append("source_date")
    for key in required_task_keys:
        if not task.get(key):
            errors.append(f"Missing task.{key}.")
    scope = package.get("overwrite_scope") or {}
    if scope.get("permitted_fields") != [SUMMARY_FIELD]:
        errors.append("Overwrite scope must contain only sum_AssessmentSum_Txt.")

    proposed = str(package.get("proposed_summary") or "").strip()
    body = re.sub(r"^Interventions/Education Provided:\s*", "", proposed).strip()
    if not body:
        errors.append("Proposed summary must contain clinical prose.")
    if _contains_concrete_number(proposed.translate(SUBSCRIPT_DIGITS)):
        errors.append("Proposed OASIS education summary must not contain specific numbers.")
    if REVIEW_LANGUAGE.search(proposed):
        errors.append("RN confirmation questions must remain outside the clinical narrative.")

    clone_shas = set()
    current_sha = (package.get("integrity") or {}).get("expected_current_summary_sha256")
    baseline_sha = (package.get("integrity") or {}).get("baseline_summary_sha256")
    if current_sha:
        clone_shas.add(current_sha)
    if baseline_sha:
        clone_shas.add(baseline_sha)
    if proposed and text_sha256(proposed) in clone_shas:
        errors.append("Proposed summary is still an exact copy of the current/baseline summary.")
    if re.search(r"\b(?:TODO|TBD|INSERT\s+HERE|PLACEHOLDER)\b", proposed, re.I):
        errors.append("Proposed summary contains an unresolved placeholder.")

    unresolved_flags = [
        flag for flag in package.get("clinical_review_flags", [])
        if not flag.get("resolved") or not str(flag.get("resolution") or "").strip()
    ]
    for flag in unresolved_flags:
        errors.append(f"Unresolved clinical review flag: {flag.get('id')}.")

    review = package.get("clinician_review") or {}
    if require_approval and not review.get("approved"):
        errors.append("Clinician approval is required.")
    if require_approval and not str(review.get("approved_by") or "").strip():
        errors.append("clinician_review.approved_by is required.")
    return errors


def approve_package(
    package: Dict[str, Any], proposed_summary: str, approved_by: str
) -> Dict[str, Any]:
    updated = deepcopy(package)
    updated["proposed_summary"] = str(proposed_summary or "").strip()
    updated["clinician_review"] = {
        "approved": True,
        "approved_by": str(approved_by or "").strip(),
        "approved_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    errors = validate_package(updated, require_approval=True)
    if errors:
        raise OASISSummaryError(" ".join(errors))
    return updated


def resolve_review_flag(
    package: Dict[str, Any], flag_id: str, resolution: str, resolved_by: str
) -> Dict[str, Any]:
    updated = deepcopy(package)
    matches = [
        flag for flag in updated.get("clinical_review_flags", [])
        if flag.get("id") == flag_id
    ]
    if len(matches) != 1:
        raise OASISSummaryError(
            f"Expected one clinical review flag '{flag_id}'; found {len(matches)}."
        )
    if not str(resolution or "").strip() or not str(resolved_by or "").strip():
        raise OASISSummaryError("A clinician resolution and resolver name are required.")
    matches[0].update({
        "resolved": True,
        "resolution": str(resolution).strip(),
        "resolved_by": str(resolved_by).strip(),
        "resolved_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    })
    return updated


def approval_phrase(package: Dict[str, Any]) -> str:
    task = package.get("task") or {}
    return (
        f"SAVE ONLY | {task.get('patient')} | {task.get('target_date')} | "
        f"{SUMMARY_FIELD} | blank Last BM = visit date"
    )


def load_json(path: str) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def atomic_write_json(path: str, payload: Dict[str, Any]) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix=".oasis_", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        os.replace(temp_path, path)
    except Exception:
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass
        raise
