"""Pure, PHI-local verification rules for Devero documents.

The browser runner feeds this module normalized fields.  It deliberately does
not decide clinical truth: deterministic defects are findings, while ambiguous
cross-record differences are routed for clinician review.
"""

from __future__ import annotations

from datetime import date, datetime
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable


E2_EFFECTIVE = date(2026, 4, 1)
ITEM_RE = re.compile(
    r"(?<![A-Z0-9])((?:M|A|B|C|D|E|GG|H|I|J|K|L|N|O|Q)\d{4})(?:[A-Z]\d?)?(?![A-Z0-9])",
    re.I,
)
SAFE_CODE_RE = re.compile(r"^[A-Z0-9_.:/-]{1,160}$")

with Path(__file__).with_name("oasis_e2_v302.json").open(encoding="utf-8") as _spec_handle:
    OASIS_E2_SPEC = json.load(_spec_handle)
OASIS_E2_ITEMS: dict[str, dict[str, Any]] = OASIS_E2_SPEC["items"]
_SPEC_ITEM_IDS = tuple(sorted(OASIS_E2_ITEMS, key=len, reverse=True))
_SPEC_ITEM_PATTERNS = tuple(
    (item_id, re.compile(rf"(?<![A-Z0-9]){re.escape(item_id)}(?![A-Z0-9])"))
    for item_id in _SPEC_ITEM_IDS
)

RFA_RULES = {
    "01": {
        "kind": "OASIS_E2_SOC",
        "instrument": "SOC",
        "rfa": "01",
        "required_items": ("M0090", "M0100", "M0030"),
    },
    "03": {
        "kind": "OASIS_E2_ROC",
        "instrument": "ROC",
        "rfa": "03",
        "required_items": ("M0090", "M0100", "M0032"),
    },
    "04": {
        "kind": "OASIS_E2_RECERT",
        "instrument": "FU",
        "rfa": "04",
        "required_items": ("M0090", "M0100"),
    },
    "05": {
        "kind": "OASIS_E2_OTHER_FOLLOWUP",
        "instrument": "FU",
        "rfa": "05",
        "required_items": ("M0090", "M0100"),
    },
    "06": {
        "kind": "OASIS_E2_TRANSFER_NOT_DISCHARGED",
        "instrument": "TRN",
        "rfa": "06",
        "required_items": ("M0090", "M0100", "M0906"),
    },
    "07": {
        "kind": "OASIS_E2_TRANSFER_DISCHARGED",
        "instrument": "TRN",
        "rfa": "07",
        "required_items": ("M0090", "M0100", "M0906"),
    },
    "08": {
        "kind": "OASIS_E2_DEATH_AT_HOME",
        "instrument": "DAH",
        "rfa": "08",
        "required_items": ("M0090", "M0100", "M0906"),
    },
    "09": {
        "kind": "OASIS_E2_DISCHARGE",
        "instrument": "DC",
        "rfa": "09",
        "required_items": ("M0090", "M0100", "M0906"),
    },
}

DOCUMENT_RULES = (
    {
        "aliases": (
            "oasis-e2 nurse start of care", "oasis e2 nurse start of care",
            "oasis-e2 start of care", "ntst oasis nurse start of care",
            "ntst oasis pt soc", "oasis-e2 pt start of care",
        ),
        "rfas": ("01",),
        "identity_token_sets": (("oasis", "start", "care"), ("oasis", "soc")),
    },
    {
        "aliases": (
            "oasis-e2 nurse resumption of care", "oasis e2 nurse resumption of care",
            "oasis-e2 resumption of care", "ntst oasis nurse resumption of care",
        ),
        "rfas": ("03",),
        "identity_token_sets": (("oasis", "resumption", "care"), ("oasis", "roc")),
    },
    {
        "aliases": (
            "oasis-e2 nurse recert", "oasis-e2 nurse recert.",
            "oasis e2 nurse recert", "oasis-e2 nurse recertification",
            "ntst oasis nurse recert", "oasis-e2 recertification",
        ),
        "rfas": ("04",),
        "identity_token_sets": (("oasis", "recert"),),
    },
    {
        "aliases": (
            "oasis-e2 nurse other follow up", "oasis-e2 nurse other follow-up",
            "oasis e2 other follow up", "ntst oasis nurse other follow up",
        ),
        "rfas": ("05",),
        "identity_token_sets": (("oasis", "other", "follow"),),
    },
    {
        "aliases": (
            "oasis-e2 nurse resumption of care/recert",
            "oasis-e2 nurse resumption of care recert",
            "ntst oasis nurse resumption of care/recert",
            "ntst oasis nurse resumption of care recert",
        ),
        "rfas": ("03", "04"),
        "identity_token_sets": (
            ("oasis", "resumption", "care"), ("oasis", "roc"), ("oasis", "recert")
        ),
    },
    {
        "aliases": (
            "oasis-e2 transfer", "oasis-e2 transfer non-visit",
            "ntst oasis transfer non-visit",
        ),
        "rfas": ("06", "07"),
        "identity_token_sets": (("oasis", "transfer"),),
    },
    {
        "aliases": (
            "oasis-e2 transfer/death", "oasis-e2 transfer death",
            "ntst devero oasis transfer/death", "ntst devero oasis transfer death",
        ),
        "rfas": ("06", "07", "08"),
        "identity_token_sets": (("oasis", "transfer"), ("oasis", "death")),
    },
    {
        "aliases": ("oasis-e2 death at home", "oasis e2 death at home"),
        "rfas": ("08",),
        "identity_token_sets": (("oasis", "death"),),
    },
    {
        "aliases": (
            "oasis-e2 discharge", "oasis e2 discharge", "oasis-e2 nurse discharge",
            "ntst oasis discharge", "ntst oasis discharge non-visit",
            "ntst oasis discharge physical therapy", "ntst oasis discharge phy",
        ),
        "rfas": ("09",),
        "identity_token_sets": (("oasis", "discharge"),),
    },
    {
        "kind": "SKILLED_NURSE_VISIT",
        "aliases": ("skilled nurse visit note",),
        "rfas": (),
        "rfa": None,
        "required_items": (),
        "identity_token_sets": (("skilled", "nurse", "visit", "note"),),
    },
)

# The E1 value is historical evidence.  These are schema relationships, not
# permission to copy current clinical status into a new E2 assessment.
E1_TO_E2 = {
    "M0069": ("A0810", "RENAMED_EVIDENCE_ONLY"),
    "A1250": ("A1255", "RENAMED_EVIDENCE_ONLY"),
}

PROMPT_PATTERNS = (
    re.compile(r"^patient response to treatment:?$", re.I),
    re.compile(r"^additional details\b.*:?$", re.I),
    re.compile(r"^indicate details\b.*", re.I),
    re.compile(r"^enter (?:details|comments|text)\b.*", re.I),
    re.compile(r"^select (?:one|all|an option)\b.*", re.I),
)

NOTE_REQUIREMENTS = {
    "CURRENT_FINDINGS": re.compile(r"\b(?:assessment|findings|vital|status|condition|exam)\b", re.I),
    "SKILLED_SERVICE": re.compile(r"\b(?:skilled|performed|wound care|teaching|education|monitor)\b", re.I),
    "RESPONSE_PROGRESS": re.compile(r"\b(?:response|progress|outcome|verbalized|demonstrated|tolerated)\b", re.I),
    "NEXT_VISIT": re.compile(r"\b(?:next visit|next skilled|follow[- ]?up|plan for next)\b", re.I),
    "SKILLED_NEED": re.compile(r"\b(?:skilled need|requires? skilled|clinical judgment|complexity|risk)\b", re.I),
}


def _compact(value: Any) -> str:
    return " ".join(str(value or "").split())


def _norm_label(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def document_rule(label: str) -> dict[str, Any] | None:
    normalized = _norm_label(label)
    for document in DOCUMENT_RULES:
        if normalized not in {_norm_label(alias) for alias in document["aliases"]}:
            continue
        rfas = document.get("rfas") or ()
        if not rfas:
            return document
        if len(rfas) == 1:
            return {**document, **RFA_RULES[rfas[0]]}
        return {
            **document,
            "kind": "OASIS_E2_MULTI",
            "instrument": "MULTI",
            "rfa": None,
            "required_items": ("M0090", "M0100"),
        }
    return None


def spec_item_id(*values: Any) -> str:
    for value in values:
        text = str(value or "").upper()
        for item_id, pattern in _SPEC_ITEM_PATTERNS:
            if pattern.search(text):
                return item_id
    return ""


def item_code(*values: Any) -> str:
    for value in values:
        match = ITEM_RE.search(str(value or ""))
        if match:
            return match.group(1).upper()
    return ""


def parse_date(value: Any) -> date | None:
    text = _compact(value)
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None


def response_code(value: Any) -> str:
    match = re.search(r"(?:^|\D)(0?[1-9])(?:\D|$)", _compact(value))
    return match.group(1).zfill(2) if match else ""


def field_location(field: dict[str, Any]) -> str:
    base = field.get("item") or item_code(field.get("name"), field.get("label"))
    spec_item = field.get("spec_item") or ""
    code = spec_item if base and re.fullmatch(rf"{re.escape(base)}[A-Z]\d?", spec_item) else base
    name = code or re.sub(r"[^A-Za-z0-9_.-]+", "_", str(field.get("name") or "FIELD"))[:80]
    return f"P{int(field.get('page') or 1)}.F{int(field.get('frame') or 0)}.{name}".upper()


def normalize_field(field: dict[str, Any]) -> dict[str, Any]:
    kind = str(field.get("kind") or "text").lower()
    value = _compact(field.get("value"))
    code = _compact(field.get("code"))
    if kind in {"checkbox", "radio"}:
        value = value if field.get("checked") else ""
        code = code if field.get("checked") else ""
    normalized = {
        "page": int(field.get("page") or 1),
        "frame": int(field.get("frame") or 0),
        "name": _compact(field.get("name"))[:160],
        "label": _compact(field.get("label"))[:240],
        "item": item_code(field.get("item"), field.get("name"), field.get("label")),
        "spec_item": spec_item_id(field.get("name"), field.get("label"), field.get("item")),
        "kind": kind,
        "value": value,
        "code": code,
        "valid": bool(field.get("valid", True)),
        "checked": bool(field.get("checked")),
        "visible": bool(field.get("visible", True)),
        "required": bool(field.get("required")),
        "readonly": bool(field.get("readonly")),
    }
    normalized["location"] = field_location(normalized)
    return normalized


def flatten_fields(pages: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    fields: list[dict[str, Any]] = []
    for page in pages:
        page_num = int(page.get("page") or page.get("page_num") or 1)
        for frame_num, frame in enumerate(page.get("frames") or [page.get("main") or {}]):
            for raw in frame.get("fields") or []:
                fields.append(normalize_field({**raw, "page": page_num, "frame": frame_num}))
    return fields


def best_fields(fields: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    best: dict[str, dict[str, Any]] = {}
    for field in fields:
        key = field.get("item") or field.get("name") or field["location"]
        score = (
            bool(field.get("visible")),
            bool(field.get("value")),
            not bool(field.get("readonly")),
            len(str(field.get("value") or "")),
        )
        current = best.get(key)
        if current is None or score > current["_score"]:
            best[key] = {**field, "_score": score}
    return {key: {k: v for k, v in value.items() if k != "_score"} for key, value in best.items()}


def snapshot(fields: Iterable[dict[str, Any]]) -> dict[str, Any]:
    canonical: dict[str, dict[str, Any]] = {}
    for field in fields:
        key = ":".join(
            str(value)
            for value in (
                field.get("page"),
                field.get("frame"),
                field.get("item") or field.get("name") or "FIELD",
                field.get("name") or field.get("kind") or "VALUE",
            )
        )
        current = canonical.get(key)
        score = (bool(field.get("value")), bool(field.get("checked")), bool(field.get("visible")))
        if current is None or score > current["_score"]:
            canonical[key] = {**field, "_score": score}
    values = {
        key: {
            "location": field["location"],
            "item": field.get("item", ""),
            "kind": field.get("kind", ""),
            "value": field.get("value", ""),
        }
        for key, field in sorted(canonical.items())
    }
    payload = json.dumps(values, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return {"sha256": hashlib.sha256(payload.encode()).hexdigest(), "fields": values}


def compare_snapshots(
    before: dict[str, Any], after: dict[str, Any], allowed_locations: Iterable[str] = ()
) -> list[dict[str, Any]]:
    allowed = {value.upper() for value in allowed_locations}
    previous = before.get("fields") or {}
    current = after.get("fields") or {}
    unexpected = []
    for location in sorted(set(previous) | set(current)):
        previous_value = previous.get(location) or {}
        current_value = current.get(location) or {}
        display_location = str(current_value.get("location") or previous_value.get("location") or location)
        if display_location.upper() in allowed or previous_value == current_value:
            continue
        finding_suffix = hashlib.sha1(location.encode()).hexdigest()[:10].upper()
        unexpected.append(
            correction(
                f"DVV-PERSISTENCE-{finding_suffix}",
                "PERSISTENCE",
                display_location,
                "Target field changed outside the approved correction set.",
                "Restore or review the unapproved change before further action.",
                evidence_class="CROSS_RECORD",
                fact_status="USER_OR_CLINICIAN_REQUIRED",
                route="CHIEF_INPUT",
            )
        )
    return unexpected


def cross_version_comparison(
    source_fields: Iterable[dict[str, Any]], target_fields: Iterable[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Describe E1/E2 relationships without declaring current status correct."""
    source = best_fields(source_fields)
    target = best_fields(target_fields)
    comparisons = []
    for source_item, source_field in source.items():
        if not source_field.get("item"):
            continue
        target_item, classification = E1_TO_E2.get(
            source_item, (source_item, "SAME_ITEM_EVIDENCE_ONLY")
        )
        target_field = target.get(target_item)
        comparisons.append(
            {
                "source_item": source_item,
                "target_item": target_item,
                "classification": classification,
                "target_present": target_field is not None,
                "equal": bool(target_field) and source_field.get("value") == target_field.get("value"),
            }
        )
    return comparisons


def correction(
    finding_id: str,
    issue_class: str,
    location: str,
    observed_issue: str,
    expected_correction: str,
    *,
    evidence_class: str = "MISSING_REQUIRED",
    source_class: str = "CMS_OASIS",
    fact_status: str = "USER_OR_CLINICIAN_REQUIRED",
    route: str = "CHIEF_INPUT",
) -> dict[str, Any]:
    return {
        "id": finding_id,
        "issue_class": issue_class,
        "location": location.upper(),
        "observed_issue": observed_issue,
        "expected_correction": expected_correction,
        "evidence_class": evidence_class,
        "source_class": source_class,
        "fact_status": fact_status,
        "route": route,
        "approved": False,
    }


def _value_for(fields: dict[str, dict[str, Any]], code: str) -> str:
    field = fields.get(code)
    return str((field or {}).get("value") or "")


def _response_for(fields: dict[str, dict[str, Any]], code: str) -> str:
    field = fields.get(code) or {}
    return response_code(field.get("code")) or response_code(field.get("value"))


def _date_findings(fields: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    findings = []
    for field in fields:
        for raw in re.findall(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b", field.get("value", "")):
            if parse_date(raw):
                continue
            findings.append(
                correction(
                    f"DVV-DATE-FORMAT-{len(findings) + 1:02d}",
                    "DATE_FORMAT",
                    field["location"],
                    "A date value is not a valid calendar date.",
                    "Confirm the actual date from the clinical record; do not infer it.",
                )
            )
    return findings


def _prompt_findings(fields: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    findings = []
    for field in fields:
        value = field.get("value", "")
        if not value or not any(pattern.fullmatch(value) for pattern in PROMPT_PATTERNS):
            continue
        findings.append(
            correction(
                f"DVV-PROMPT-VALUE-{len(findings) + 1:02d}",
                "PROMPT_VALUE",
                field["location"],
                "A user-interface prompt appears to have been stored as clinical content.",
                "Clear it or replace it only with clinician-supported documentation.",
                fact_status="SUPPORTED",
                route="WRITER",
            )
        )
    return findings


def _resolve_oasis_rule(rule: dict[str, Any], fields: dict[str, dict[str, Any]]) -> dict[str, Any]:
    actual_rfa = _response_for(fields, "M0100")
    if actual_rfa in rule.get("rfas", ()):
        return {**rule, **RFA_RULES[actual_rfa]}
    return rule


def _required_findings(
    rule: dict[str, Any], fields: dict[str, dict[str, Any]], all_fields: Iterable[dict[str, Any]]
) -> list[dict[str, Any]]:
    findings = []
    for code in rule["required_items"]:
        if _value_for(fields, code):
            continue
        findings.append(
            correction(
                f"DVV-TIMEPOINT-{code}",
                "TIMEPOINT_REQUIRED",
                code,
                f"Required {code} content is missing or was not extracted.",
                "Re-extract the page; if still blank, obtain clinician-supported content.",
            )
        )
    for field in all_fields:
        if field.get("visible") and field.get("required") and not field.get("value"):
            field_id = (
                field.get("spec_item")
                or field.get("item")
                or hashlib.sha1(field["location"].encode()).hexdigest()[:8].upper()
            )
            finding_id = f"DVV-REQUIRED-{field_id}"
            if any(item["location"] == field["location"] for item in findings):
                continue
            findings.append(
                correction(
                    finding_id,
                    "VISIBLE_REQUIRED",
                    field["location"],
                    "A visible required control is blank.",
                    "Complete it only from supported current assessment evidence.",
                )
            )
    return findings


def _spec_findings(rule: dict[str, Any], fields: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Validate the CMS E2 rules that Devero exposes in its rendered controls."""
    rfa = rule.get("rfa")
    findings = []
    for field in fields:
        if not field.get("visible"):
            continue
        spec_item = field.get("spec_item")
        base_item = field.get("item")
        if base_item in {"M0069", "A1250", "O0350"} and field.get("value"):
            findings.append(
                correction(
                    f"DVV-E2-RETIRED-{base_item}",
                    "VERSION",
                    base_item,
                    f"Retired OASIS-E1 item {base_item} is populated on an E2 target.",
                    "Confirm the E2 form version and record the response only in the current E2 item.",
                    evidence_class="POLICY_CONFLICT",
                    fact_status="NOT_EDITABLE",
                    route="CLINICAL_POLICY",
                )
            )
            continue
        if not spec_item:
            continue
        spec = OASIS_E2_ITEMS[spec_item]
        if rfa and rfa not in spec["r"]:
            findings.append(
                correction(
                    f"DVV-TIMEPOINT-{spec_item}",
                    "TIMEPOINT_ITEM_SET",
                    spec_item,
                    f"Devero rendered {spec_item}, which is not active for OASIS RFA {rfa}.",
                    "Confirm the selected assessment time point and Devero form version.",
                    evidence_class="POLICY_CONFLICT",
                    fact_status="NOT_EDITABLE",
                    route="CLINICAL_POLICY",
                )
            )
            continue
        if field.get("value") and not field.get("valid", True):
            findings.append(
                correction(
                    f"DVV-RESPONSE-{spec_item}",
                    "RESPONSE_DOMAIN",
                    spec_item,
                    "Devero reports that the rendered OASIS response is invalid.",
                    "Select a valid response supported by the current assessment.",
                )
            )
            continue
        code = str(field.get("code") or "").strip()
        allowed = set(spec.get("v") or ())
        code_candidates = {code}
        leading_code = re.match(r"^([A-Za-z0-9^.-]+)", code)
        if leading_code:
            code_candidates.add(leading_code.group(1))
        if code.isdigit():
            code_candidates.add(code.zfill(2))
        if (
            code
            and allowed
            and spec.get("t") in {"Code", "Checklist"}
            and code_candidates.isdisjoint(allowed)
        ):
            findings.append(
                correction(
                    f"DVV-RESPONSE-{spec_item}",
                    "RESPONSE_DOMAIN",
                    spec_item,
                    f"The stored code for {spec_item} is outside the CMS E2 response domain.",
                    "Select a valid CMS E2 response supported by the current assessment.",
                )
            )
    return findings


def _timing_findings(rule: dict[str, Any], fields: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    findings = []
    m0090 = parse_date(_value_for(fields, "M0090"))
    if not m0090:
        return findings
    if m0090 < E2_EFFECTIVE:
        findings.append(
            correction(
                "DVV-DATE-VERSION",
                "VERSION",
                "M0090",
                "The E2 target has an M0090 before the E2 effective date.",
                "Confirm the document version and actual completion date; never backdate or force a version.",
                evidence_class="POLICY_CONFLICT",
                fact_status="NOT_EDITABLE",
                route="CLINICAL_POLICY",
            )
        )
    actual_rfa = _response_for(fields, "M0100")
    allowed_rfas = rule.get("rfas") or ((rule.get("rfa"),) if rule.get("rfa") else ())
    if actual_rfa and actual_rfa not in allowed_rfas:
        expected = "/".join(allowed_rfas)
        findings.append(
            correction(
                "DVV-TIMEPOINT-M0100",
                "TIMEPOINT",
                "M0100",
                f"M0100 does not match the target document time point {expected}.",
                "Confirm the correct assessment time point before changing any response.",
                evidence_class="POLICY_CONFLICT",
            )
        )
    if rule["kind"] == "OASIS_E2_SOC":
        soc = parse_date(_value_for(fields, "M0030"))
        if soc and not 0 <= (m0090 - soc).days <= 5:
            findings.append(
                correction(
                    "DVV-SOC-TIMING",
                    "TIMING",
                    "M0030/M0090",
                    "SOC assessment completion falls outside the five-day window.",
                    "Keep the actual dates and route the timing conflict for review.",
                    evidence_class="CALCULATED_TIMING",
                    source_class="CMS_OASIS",
                    fact_status="NOT_EDITABLE",
                    route="CLINICAL_POLICY",
                )
            )
    elif rule["kind"] == "OASIS_E2_ROC":
        roc = parse_date(_value_for(fields, "M0032"))
        if roc and not 0 <= (m0090 - roc).days <= 2:
            findings.append(
                correction(
                    "DVV-ROC-TIMING",
                    "TIMING",
                    "M0032/M0090",
                    "ROC assessment completion falls outside the two-day window.",
                    "Keep the actual dates and route the timing conflict for review.",
                    evidence_class="CALCULATED_TIMING",
                    fact_status="NOT_EDITABLE",
                    route="CLINICAL_POLICY",
                )
            )
    elif rule["kind"] == "OASIS_E2_RECERT":
        soc = parse_date(_value_for(fields, "M0030"))
        if soc:
            period_day = (m0090 - soc).days % 60 + 1
            if period_day not in range(56, 61):
                findings.append(
                    correction(
                        "DVV-RFA04-TIMING",
                        "TIMING",
                        "M0030/M0090",
                        f"Recertification calculates to certification-period day {period_day}, outside days 56-60.",
                        "Keep the actual dates and route the timing conflict for review.",
                        evidence_class="CALCULATED_TIMING",
                        fact_status="NOT_EDITABLE",
                        route="CLINICAL_POLICY",
                    )
                )
    elif rule["kind"] in {
        "OASIS_E2_TRANSFER_NOT_DISCHARGED",
        "OASIS_E2_TRANSFER_DISCHARGED",
        "OASIS_E2_DEATH_AT_HOME",
        "OASIS_E2_DISCHARGE",
    }:
        event_date = parse_date(_value_for(fields, "M0906"))
        if event_date and not 0 <= (m0090 - event_date).days <= 2:
            finding_id = {
                "OASIS_E2_TRANSFER_NOT_DISCHARGED": "DVV-TRANSFER-TIMING",
                "OASIS_E2_TRANSFER_DISCHARGED": "DVV-TRANSFER-TIMING",
                "OASIS_E2_DEATH_AT_HOME": "DVV-DEATH-TIMING",
                "OASIS_E2_DISCHARGE": "DVV-DISCHARGE-TIMING",
            }[rule["kind"]]
            findings.append(
                correction(
                    finding_id,
                    "TIMING",
                    "M0906/M0090",
                    "Transfer, death, or discharge assessment completion falls outside the two-day window.",
                    "Keep the actual dates and route the timing conflict for review.",
                    evidence_class="CALCULATED_TIMING",
                    fact_status="NOT_EDITABLE",
                    route="CLINICAL_POLICY",
                )
            )
    return findings


def _clone_findings(target: Iterable[dict[str, Any]], source: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    source_values = {
        field.get("name"): field.get("value")
        for field in source
        if len(field.get("value", "")) >= 500 and field.get("name")
    }
    findings = []
    for field in target:
        value = field.get("value", "")
        if len(value) < 500 or source_values.get(field.get("name")) != value:
            continue
        findings.append(
            correction(
                f"DVV-CURRENT-STATUS-{field.get('item') or 'NARRATIVE'}-{len(findings) + 1:02d}",
                "CURRENT_STATUS",
                field["location"],
                "A long target narrative exactly matches the completed E1/source narrative.",
                "Confirm that the target reflects the current assessment; do not auto-rewrite it.",
                evidence_class="CROSS_RECORD",
            )
        )
    return findings


def _note_findings(fields: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    corpus = "\n".join(
        f"{field.get('label', '')} {field.get('value', '')}" for field in fields if field.get("value")
    )
    findings = []
    for requirement, pattern in NOTE_REQUIREMENTS.items():
        if pattern.search(corpus):
            continue
        findings.append(
            correction(
                f"DVV-NOTE-{requirement}",
                "SKILLED_NOTE",
                requirement,
                f"The extracted visit note does not establish {requirement.lower().replace('_', ' ')}.",
                "Obtain specific clinician-supported documentation; do not add stock wording.",
                source_class="CMS_MBPM_CH7",
            )
        )
    return findings


def audit_bundle(bundle: dict[str, Any]) -> dict[str, Any]:
    rule = document_rule(str(bundle.get("document_type") or ""))
    pages = bundle.get("target_pages") or []
    source_pages = bundle.get("source_pages") or []
    related_pages = [
        page
        for related in bundle.get("related_documents") or []
        for page in related.get("pages") or []
    ]
    complete = (
        bool(pages)
        and all((page.get("extraction") or {}).get("ready") for page in pages)
        and all((page.get("extraction") or {}).get("ready") for page in source_pages + related_pages)
    )
    if not rule or not complete:
        return {
            "run_status": "BLOCKED",
            "verdict": None,
            "review_stage": "DETERMINISTIC",
            "semantic_review_required": False,
            "document_kind": rule["kind"] if rule else "UNSUPPORTED",
            "corrections": [],
            "reason": "UNSUPPORTED_DOCUMENT" if not rule else "INCOMPLETE_EXTRACTION",
        }

    target = flatten_fields(pages)
    source = flatten_fields(source_pages)
    indexed = best_fields(target)
    supporting = source[:]
    for related in bundle.get("related_documents") or []:
        supporting.extend(flatten_fields(related.get("pages") or []))
    supporting_index = best_fields(supporting)
    if "M0030" not in indexed and "M0030" in supporting_index:
        indexed["M0030"] = supporting_index["M0030"]
    if rule.get("rfas"):
        rule = _resolve_oasis_rule(rule, indexed)
    findings = _date_findings(target) + _prompt_findings(target)
    if rule["kind"].startswith("OASIS"):
        findings += _required_findings(rule, indexed, target)
        findings += _spec_findings(rule, target)
        findings += _timing_findings(rule, indexed)
        findings += _clone_findings(target, source)
    else:
        findings += _note_findings(target)

    deduped = {item["id"]: item for item in findings}
    findings = list(deduped.values())
    material = any(item["fact_status"] == "NOT_EDITABLE" for item in findings)
    return {
        "run_status": "COMPLETE",
        "verdict": "FAIL" if material else ("MIXED" if findings else "PASS"),
        "review_stage": "DETERMINISTIC",
        "semantic_review_required": True,
        "document_kind": rule["kind"],
        "rule_pack": "CMS_E2_V3.02.0_2026-04-01" if rule["kind"].startswith("OASIS") else "CMS_MBPM_CH7",
        "corrections": findings,
        "snapshot": snapshot(target),
        "counts": {"pages": len(pages), "fields": len(target), "source_fields": len(source)},
        "cross_version": cross_version_comparison(source, target),
    }


def validate_result(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("run_status") not in {"COMPLETE", "BLOCKED"}:
        raise ValueError("Invalid verification run_status")
    if payload.get("run_status") == "COMPLETE" and payload.get("verdict") not in {"PASS", "MIXED", "FAIL"}:
        raise ValueError("Invalid verification verdict")
    if payload.get("review_stage") not in {None, "DETERMINISTIC", "FINAL"}:
        raise ValueError("Invalid verification review_stage")
    if "semantic_review_required" in payload and not isinstance(payload["semantic_review_required"], bool):
        raise ValueError("semantic_review_required must be boolean")
    corrections = payload.get("corrections") or []
    if payload.get("run_status") == "BLOCKED":
        if payload.get("verdict") is not None or corrections:
            raise ValueError("Blocked verification cannot have a verdict or corrections")
        return payload
    if not corrections and payload.get("verdict") != "PASS":
        raise ValueError("A complete verification without corrections must pass")
    if corrections and payload.get("verdict") == "PASS":
        raise ValueError("A verification with corrections cannot pass")
    ids: set[str] = set()
    allowed_sources = {"CMS_OASIS", "CMS_MBPM_CH7", "AGENCY_RECORD", "USER_SUPPLIED"}
    for item in corrections:
        if not re.fullmatch(r"DVV-[A-Z0-9_-]{2,120}", str(item.get("id") or "")):
            raise ValueError("Invalid correction id")
        if item["id"] in ids:
            raise ValueError("Duplicate correction id")
        ids.add(item["id"])
        if not SAFE_CODE_RE.fullmatch(str(item.get("issue_class") or "")):
            raise ValueError("Invalid issue_class")
        if not SAFE_CODE_RE.fullmatch(str(item.get("location") or "")):
            raise ValueError("Correction location must be a PHI-free code")
        if item.get("evidence_class") not in {
            "DIRECT_RECORD", "CROSS_RECORD", "CALCULATED_TIMING", "MISSING_REQUIRED", "POLICY_CONFLICT"
        }:
            raise ValueError("Invalid evidence_class")
        if item.get("fact_status") not in {"SUPPORTED", "USER_OR_CLINICIAN_REQUIRED", "NOT_EDITABLE"}:
            raise ValueError("Invalid fact_status")
        if item.get("route") not in {"WRITER", "CHIEF_INPUT", "CLINICAL_POLICY"}:
            raise ValueError("Invalid correction route")
        sources = {part.strip() for part in str(item.get("source_class") or "").split(",") if part.strip()}
        if not sources or not sources <= allowed_sources:
            raise ValueError("Invalid source_class")
        for key in ("observed_issue", "expected_correction"):
            if len(str(item.get(key) or "")) > 1000:
                raise ValueError(f"{key} is too long")
        item.setdefault("approved", False)
    if any(item.get("fact_status") == "NOT_EDITABLE" for item in corrections) and payload.get("verdict") != "FAIL":
        raise ValueError("NOT_EDITABLE findings require a FAIL verdict")
    return payload
