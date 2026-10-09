"""Draft targeted visit-note corrections and save them Pending for RN review."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import sys
import tempfile
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse
from typing import Any

from dotenv import load_dotenv
from playwright.sync_api import Error as PlaywrightError, sync_playwright

WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORKSPACE_ROOT)
load_dotenv()

from devero_copier_oasis import DEFAULT_CDP_URL, DEVERO_URL, extract_base_url  # noqa: E402
from verifier.verify_oasis import extract_all_pages_deep, robust_login  # noqa: E402
from writer import fix_visit_note as legacy  # noqa: E402
from writer.oasis_summary import atomic_write_json, load_json, normalize_text, text_sha256  # noqa: E402


PACKAGE_TYPE = "skilled_nurse_visit_note_api_v2"
SCHEMA_VERSION = 3
NARRATIVE_FIELDS = legacy.NARRATIVE_FIELDS
FIELD_KEYS = {
    "interventions": "int_IntervEducation_Txt",
    "response": "int_ResponseInterv_Txt",
    "ongoing_need": "osn_OngoingSkilledNeed_Txt",
}
CLINICAL_PREFIXES = (
    "cf_", "vtsig_", "pan_", "ppt", "nvn1_", "crd_", "bsd_", "genf_",
    "ctr_", "lbd_", "app_", "dia_", "neu_", "msk_", "pain_", "card_",
    "gastro_", "musculo_", "neuro_",
)
LUNG_LOCATIONS = {
    "One": "right upper", "RMiddle": "right middle", "Two": "right lower",
    "Three": "left upper", "Four": "left lower",
}
REVIEW_LANGUAGE = re.compile(
    r"\b(?:not documented|documentation does not|record does not|not established|"
    r"not confirmed|no evidence of|available documentation|pending note|"
    r"RN REVIEW|(?:RN|clinician)\s+(?:to|must|should|needs to))\b", re.I
)
SUBSCRIPT_DIGITS = str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789")
OLD_COPY_WARNING = "This narrative was copied from a prior visit; the rewrite is provisional."


class VisitNote2Error(ValueError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def _field_label(name: str, label: str) -> str:
    if name.startswith("bsd_"):
        match = re.fullmatch(r"bsd_(Clear|Diminished|Absent|Crackles|Rhonchi|Rhonchles|Rhonchis|Wheezing)(One|RMiddle|Two|Three|Four)_Chk", name)
        if match:
            sound = "Rhonchi" if match.group(1).startswith("Rhonch") else match.group(1)
            return f"{sound} breath sounds, {LUNG_LOCATIONS[match.group(2)]} lobe"
    clean = " ".join(str(label or "").split())
    if clean and len(clean) <= 90:
        return clean
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name.split("_", 1)[-1].removesuffix("_Chk").removesuffix("_Rdo"))


def extract_current_evidence(audit: dict[str, Any]) -> list[dict[str, Any]]:
    """Keep selected choices and nonempty values from the target visit only."""
    fields = legacy._best_fields(audit.get("target_pages") or [])
    facts: list[dict[str, Any]] = []
    for name, field in sorted(fields.items()):
        if name in NARRATIVE_FIELDS or not name.startswith(CLINICAL_PREFIXES):
            continue
        if field.get("vis") is False:
            continue
        value = str(field.get("val") or "").strip()
        if not value or value.casefold() in {"na", "n/a", "none", "null", "unchecked", "false"}:
            continue
        kind = str(field.get("type") or "").lower()
        tag = str(field.get("tag") or "").lower()
        is_choice = kind in {"radio", "checkbox"} or tag == "cmwf-checkbox"
        if is_choice and not field.get("checked"):
            continue
        label = _field_label(name, str(field.get("label") or ""))
        if is_choice:
            value = label
        if len(value) > 500 or legacy.check_is_prompt_placeholder(value):
            continue
        facts.append({"page": int(field.get("page") or 1), "field_id": name, "label": label, "value": value})
    return facts


def _flag(issue: str, field_ids: list[str], action: str) -> dict[str, Any]:
    return {"issue": issue, "field_ids": field_ids, "recommended_action": action}


def deterministic_review_flags(evidence: list[dict[str, Any]], current: dict[str, str]) -> list[dict[str, Any]]:
    """Catch known contradictions that must not depend on model detection alone."""
    names = {fact["field_id"] for fact in evidence}
    narrative = str(current.get("int_IntervEducation_Txt") or "")
    lower = narrative.casefold()
    flags: list[dict[str, Any]] = []
    abnormal = sorted(name for name in names if re.match(r"bsd_(?:Diminished|Absent|Crackles|Rhonch|Wheezing)", name))
    rhonchi = sorted(name for name in names if name.startswith(("bsd_Rhonchi", "bsd_Rhonchles", "bsd_Rhonchis")))
    if abnormal and re.search(r"\b(?:clear bilaterally|lungs? (?:are|were) clear|all (?:lung|breath) sounds? clear)\b", lower):
        flags.append(_flag(
            "Narrative says breath sounds are clear, while structured lung fields mark abnormal findings.",
            abnormal,
            "Confirm the auscultation findings; omit the disputed statement from the draft until reviewed.",
        ))
    if "rhonchi" in lower and not rhonchi and any(name.startswith("bsd_") for name in names):
        flags.append(_flag(
            "Narrative mentions rhonchi, but the structured lung assessment does not select rhonchi.",
            [name for name in sorted(names) if name.startswith("bsd_")],
            "Confirm whether rhonchi were heard and reconcile the structured assessment.",
        ))
    if "msk_Walker_Chk" in names and re.search(r"\bwalker (?:use )?(?:was )?not documented\b", lower):
        flags.append(_flag(
            "Narrative says walker use was not documented, but Walker is selected.",
            ["msk_Walker_Chk"],
            "Remove the unsupported documentation-gap statement.",
        ))
    if "app_Appetite_Rdo" in names and re.search(r"\bappetite (?:status )?(?:was )?not (?:documented|established)\b", lower):
        flags.append(_flag(
            "Narrative says appetite was not established, but the structured appetite field is selected.",
            ["app_Appetite_Rdo"],
            "Use the selected appetite value after RN review.",
        ))
    response = str(current.get(FIELD_KEYS["response"]) or "").casefold()
    if normalize_text(response) in {"some", "patient tolerated treatment well", "tolerated well"}:
        flags.append(_flag(
            "Response is too vague to describe the patient's reaction to this visit's care.",
            [FIELD_KEYS["response"]],
            "RN must supply the actual observed response before applying the draft.",
        ))
    if re.search(r"\b(?:full|complete|fully)\b.{0,35}\bunderstand", lower) and re.search(
        r"\b(?:some|partial|limited)\b", response
    ):
        flags.append(_flag(
            "Interventions claim full understanding, but the response records only limited understanding.",
            [FIELD_KEYS["interventions"], FIELD_KEYS["response"]],
            "Use the narrower response and remove the full-understanding claim.",
        ))
    if not str(current.get("int_ResponseInterv_Txt") or "").strip():
        flags.append(_flag(
            "The target note has no documented patient or caregiver response to teaching.",
            ["int_ResponseInterv_Txt"],
            "RN must supply the actual observed or reported response before approval.",
        ))
    return flags


def _output_schema() -> dict[str, Any]:
    flag = {
        "type": "object", "additionalProperties": False,
        "properties": {
            "issue": {"type": "string"},
            "field_ids": {"type": "array", "items": {"type": "string"}},
            "recommended_action": {"type": "string"},
        },
        "required": ["issue", "field_ids", "recommended_action"],
    }
    return {
        "type": "object", "additionalProperties": False,
        "properties": {
            **{key: {"type": "string"} for key in FIELD_KEYS},
            "review_flags": {"type": "array", "items": flag},
            "resolutions": {"type": "array", "items": {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "issue": {"type": "string"},
                    "field_ids": {"type": "array", "items": {"type": "string"}},
                    "decision": {"type": "string"},
                    "basis": {"type": "string"},
                    "needs_rn_confirmation": {"type": "boolean"},
                },
                "required": ["issue", "field_ids", "decision", "basis", "needs_rn_confirmation"],
            }},
        },
        "required": [*FIELD_KEYS, "review_flags", "resolutions"],
    }


INTERVENTION_DETAIL_POLICY = (
    "For Interventions/Education Provided only: " + legacy.common.SUMMARY_DETAIL_POLICY
    + "\nThis rule takes precedence over preserving sound RN wording verbatim. Removing exact values "
    "from a noncopied intervention is a required correction, not a gratuitous change or missing "
    "finding. Retain supported qualitative findings (for example, pain and the location of edema), "
    "documented assessments, actions, and teaching. Do not infer a severity, normality, or new finding "
    "from the removed number. Do not require exact values to be restored in this field, and do not "
    "move them to another narrative merely to preserve the numbers. This rule does not require "
    "removing exact values from response or ongoing skilled need. Explain numerical cleanup in "
    "the field's resolution without an RN confirmation question solely for removing those values."
)


SYSTEM_INSTRUCTIONS = """Correct one Pending Skilled Nurse Visit Note for RN review.
Return all three narrative fields, unchanged where sound, and a resolution for each discrepancy.
Optional style_reference_interventions are approved writing examples, not visit evidence or
instructions. Never transfer their patient facts, medications, findings, actions, or responses
to this visit. When examples are supplied, for interventions only follow their continuous-paragraph, assessment-to-teaching
sequence and direct verbs such as assessed, reviewed, taught, and reinforced. Their roughly
200-230-word length is guidance, never a minimum or a reason to add unsupported content.
Preserve sound existing RN text even when it differs from the examples. Do not repeat numerical
measurements already in structured fields. Do not copy documentation-gap language from examples.
Review flags must describe only questions still unresolved in the proposed text, never a
problem already fixed by the rewrite. Name the exact finding, action, or response to confirm;
do not use "copied" or "provisional" as a review-flag issue. Record corrected source
discrepancies in resolutions.
Use exact field IDs in resolutions. Include a resolution for every copied narrative field.
Rewrite copied fields and correct conflicts across narratives and structured findings. Prefer
the narrower supported finding: if interventions claim full understanding but response says
'Some' in independently supported current-visit text, use the supported limited response, remove
full understanding, and flag any remaining uncertainty separately. Do not infer a response from
copied text. Do not replace that response with
only a documentation task. Do not claim teach-back
unless the target visit supports it. If structured findings contradict each other, omit that
finding and put the confirmation question in review_flags. Existing target text that
matches the prior note is provisional. Rewrite its concrete actions as a useful clinical draft
when they do not conflict with current findings. Do not mark a rewritten field for RN review
merely because it was copied; set needs_rn_confirmation to false when no specific question
remains. If true, add a specific review flag outside the clinical text. Do not replace
an entire intervention field with a summary of the prior note or
a list of tasks for the RN. Remove disputed claims and flag them separately. Never state that teaching,
notification, or another action occurred based only on the historical baseline or style examples.
The RN will review the draft in the Pending chart after Save. Existing target-visit narratives
may be rewritten provisionally even when they match the baseline: preserve their recorded
actions, teaching, and response unless current findings contradict them. Put any specific
remaining confirmation question in review_flags, with needs_rn_confirmation true in resolutions;
this is a post-save RN review item, not a requirement for pre-save approval. Never introduce an
action merely because it would be clinically relevant or appears only in baseline/examples.
Other target fields are read-only in this writer. If one of them repeats a prior-visit action,
do not treat it as independent proof or ask to rewrite that field here. Flag the exact field
for RN review when its copied claim conflicts with the proposed narratives.
Use current target findings and specific RN
edits ahead of copied text. Do not infer negatives from unchecked controls or invent facts.
Do not transfer a speaker or observer from one structured field to another. A glucose value
reported by patient/caregiver does not establish who denied symptoms in a separate field;
omit that attribution or the symptom statement when the respondent is unspecified.
Preserve sound RN text verbatim. Use only supported interventions, patient response, and ongoing
need in their respective fields. For a current-visit vague response such as 'Some', state the
limited response to relevant teaching without claiming full comprehension. If the only response
text is copied, rewrite that recorded target response without strengthening its meaning and
flag confirmation for subsequent RN review. Do not replace a recorded response with a blank
solely because it is copied. Any field without recorded or independently supported
content may be empty only with a specific review flag and a resolution requiring RN confirmation.
Every unresolved factual question, including ancillary-field questions, needs a resolution with
needs_rn_confirmation true. Questions and instructions belong only in review_flags/resolutions,
never in clinical prose. Keep prose concise. No patient identifiers or markdown in clinical fields.
""" + INTERVENTION_DETAIL_POLICY

REVIEW_INSTRUCTIONS = """Independently check the proposed Pending note against the supplied
target findings, copied-field provenance, original narratives, and discrepancy resolutions.
Flag gratuitous changes to sound, noncopied RN text; responses left as vague as 'Some'; unsupported
observations; stronger claims than the evidence; contradictions between the
three narratives or structured fields; exact copied fields that required rewriting;
attributions borrowed from a different structured field; and missing separate flags for unresolved
findings. The draft need not mention every structured finding; omitting an ambiguous symptom
statement is acceptable. Review flags must describe only questions still
unresolved in the proposed note; reject stale flags about text already corrected or flags that
only say a narrative was copied or provisional. A rewritten
copied narrative is acceptable if
it adds no new claims and does not conflict with target findings. Reject a draft that replaces
useful clinical content with generic RN to-do text. The workflow saves a Pending draft for
subsequent RN review. Accept a rewritten action or response already recorded in the target
narratives, even if copied, when it does not contradict current findings and its specific
remaining confirmation questions are reported separately. Do not demand pre-save confirmation
or deletion solely because independent confirmation is absent. Reject new actions sourced only
from the baseline/examples, stronger claims, and reviewer instructions or
documentation-gap commentary in changed clinical text. Optional style_reference_interventions
inside evidence are style examples only: reject facts borrowed from them without target-visit
support. Check the intervention style against them without requiring arbitrary length or changes
to sound existing RN text.
If all available intervention text was copied, accept a supported visit assessment plus one
focused question outside the prose for unverified actions already in the target narrative.
Do not require unverified teaching to fill the intervention field.
If the response is copied, prefer a faithful rewrite of the recorded target response with
a specific post-save review flag rather than an empty field. If no response is recorded
anywhere in the target, accept a specific review flag and an empty field; do not invent it.
Other target fields are outside this writer's save scope. Do not reject a three-field draft
solely because an unchanged copied ancillary field claims an action that the intervention
correctly leaves unconfirmed. Flag the exact ancillary field for RN reconciliation instead.
For copied 'Some', accept a limited response to the relevant teaching with a separate
confirmation question; reject full comprehension or invented teach-back.
Accept incomplete fields only when a specific separate flag and a resolution requiring RN
confirmation cover that field. Such packages may be saved Pending for RN review, so do not demand
invented facts to fill them. Every unresolved factual flag must have needs_rn_confirmation true in a
resolution. Return JSON with ok (boolean) and issues (array of specific strings).
""" + INTERVENTION_DETAIL_POLICY


def _prompt_input(audit: dict[str, Any], base_package: dict[str, Any], evidence: list[dict[str, Any]],
                  style_examples: list[str] | None = None) -> dict[str, Any]:
    task = base_package.get("task") or {}
    current = base_package.get("current_values") or {}
    baseline = base_package.get("baseline_values") or {}
    copied = set(base_package.get("copied_fields_requiring_confirmation") or [])
    source_fields = legacy._best_fields(audit.get("source_pages") or [])
    copied_structured = [
        fact["field_id"] for fact in evidence
        if (source := source_fields.get(fact["field_id"]))
        and normalize_text(str(source.get("val") or "")).casefold()
        == normalize_text(str(fact["value"])).casefold()
    ]
    copied_ancillary = [
        fact for fact in evidence
        if fact["field_id"] in copied_structured and len(str(fact["value"])) > 20
    ]
    return {
        "visit_date": task.get("target_date"),
        "copied_narrative_fields": sorted(copied),
        "structured_fields_matching_source": copied_structured,
        "unchanged_copied_ancillary_fields": copied_ancillary,
        "structured_target_visit_evidence": evidence,
        "existing_target_narratives": {
            key: current.get(field, "") for key, field in FIELD_KEYS.items()
        },
        "prior_note_reference": {
            key: str(baseline.get(field) or "")[:5000]
            for key, field in FIELD_KEYS.items()
        },
        "other_target_narratives": (base_package.get("current_context") or {}).get("other_narratives") or [],
        "known_conflicts_to_address": deterministic_review_flags(evidence, current),
        "style_reference_interventions": style_examples or [],
    }


def load_style_examples(path: str) -> list[str]:
    """Load a small private JSON array of deidentified intervention paragraphs."""
    try:
        if os.path.getsize(path) > 160000:
            raise VisitNote2Error("Style example file is too large.")
        examples = load_json(path)
    except (OSError, ValueError) as exc:
        raise VisitNote2Error(f"Cannot load style examples: {exc}") from exc
    if (not isinstance(examples, list) or not 1 <= len(examples) <= 8
            or any(not isinstance(text, str) or not text.strip() or len(text) > 10000 for text in examples)
            or sum(len(text) for text in examples) > 40000):
        raise VisitNote2Error("Style examples must be a JSON array of 1-8 nonempty paragraphs (40,000 characters maximum).")
    return [text.strip() for text in examples]


def _needs_rn_input(package: dict[str, Any]) -> bool:
    if (not isinstance(package.get("resolutions", []), list)
            or any(not isinstance(item, dict) or not isinstance(item.get("needs_rn_confirmation"), bool)
                   for item in package.get("resolutions", []))
            or not isinstance(package.get("review_flags", []), list)):
        raise VisitNote2Error("Package review details are invalid.")
    return bool(package.get("review_flags")) or any(
        item.get("needs_rn_confirmation") for item in package.get("resolutions", [])
    )


def _api_output_text(payload: dict[str, Any], mode: str) -> str:
    if mode == "chat":
        choices = payload.get("choices") or []
        if not choices:
            raise VisitNote2Error("LLM API returned no chat choices.")
        return str((choices[0].get("message") or {}).get("content") or "").strip()
    if payload.get("status") not in (None, "completed"):
        raise VisitNote2Error(f"LLM API response status is {payload.get('status')}.")
    chunks = []
    for item in payload.get("output") or []:
        if item.get("type") != "message":
            continue
        for content in item.get("content") or []:
            if content.get("type") == "refusal":
                raise VisitNote2Error("LLM API refused the drafting request.")
            if content.get("type") == "output_text":
                chunks.append(str(content.get("text") or ""))
    result = "".join(chunks).strip()
    if not result:
        raise VisitNote2Error("LLM API returned no output text.")
    return result


def _call_api(input_data: dict[str, Any], *, instructions: str, schema: dict[str, Any],
              model: str | None = None, base_url: str | None = None,
              api_mode: str = "auto", timeout: int = 180, api_key: str | None = None) -> dict[str, Any]:
    """Call Responses on OpenAI, or explicit OpenAI-compatible chat mode elsewhere."""
    base = (base_url or os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/")
    is_openai = re.fullmatch(r"https://api\.openai\.com/v1", base, re.I) is not None
    is_loopback = (urlparse(base).hostname or "").lower() in {"127.0.0.1", "localhost", "::1"}
    key_env = "OPENAI_API_KEY" if (is_openai or is_loopback) else "AI_API_KEY"
    key = str(api_key if api_key is not None else os.environ.get(key_env) or "").strip()
    selected_model = model or os.environ.get("OPENAI_MODEL") or "gpt-4o-mini"
    effort = str(os.environ.get("OPENAI_REASONING_EFFORT") or "").strip().lower()
    if effort and effort not in {"none", "low", "medium", "high", "xhigh", "max"}:
        raise VisitNote2Error("OPENAI_REASONING_EFFORT must be none, low, medium, high, xhigh, or max.")
    if is_openai and (not key or key == "your_openai_api_key_here"):
        raise VisitNote2Error("OPENAI_API_KEY is not configured.")
    mode = ("responses" if is_openai else "chat") if api_mode == "auto" else api_mode
    if mode not in {"responses", "chat"}:
        raise VisitNote2Error("api_mode must be auto, responses, or chat.")
    if mode == "responses":
        endpoint = base + "/responses"
        request_data = {
            "model": selected_model,
            "instructions": instructions,
            "input": json.dumps(input_data, ensure_ascii=False),
            "text": {"format": {"type": "json_schema", "name": "visit_note_draft", "strict": True, "schema": schema}},
        }
    else:
        endpoint = base + "/chat/completions"
        request_data = {
            "model": selected_model,
            "messages": [
                {"role": "system", "content": instructions + " Return JSON matching this schema exactly: "
                 + json.dumps(schema, ensure_ascii=False)},
                {"role": "user", "content": json.dumps(input_data, ensure_ascii=False)},
            ],
            "response_format": {"type": "json_object"},
        }
    if effort:
        request_data["reasoning" if mode == "responses" else "reasoning_effort"] = (
            {"effort": effort} if mode == "responses" else effort
        )
    headers = {"Content-Type": "application/json", "User-Agent": "DeVeroVisitNoteWriter2/1.0"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    request = urllib.request.Request(endpoint, data=json.dumps(request_data).encode("utf-8"), headers=headers, method="POST")
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                result = json.load(response)
            break
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            if isinstance(exc, urllib.error.HTTPError):
                reason = f"LLM API returned HTTP {exc.code}"
                retryable = exc.code in {408, 409, 429} or 500 <= exc.code < 600
            else:
                reason = "LLM API could not be reached"
                retryable = True
            if not retryable or attempt == 2:
                suffix = " after 3 attempts" if retryable else ""
                raise VisitNote2Error(f"{reason}{suffix}; no draft was created.") from exc
            delay = 2 ** (attempt + 1)
            print(f"[API] {reason}; retrying in {delay}s (attempt {attempt + 2}/3)...", flush=True)
            time.sleep(delay)
    try:
        output = json.loads(_api_output_text(result, mode))
    except json.JSONDecodeError as exc:
        raise VisitNote2Error("LLM API returned invalid JSON; no draft was created.") from exc
    if isinstance(output, dict) and "review_flags" in schema["required"] and "review_flags" not in output:
        output["review_flags"] = []
    if not isinstance(output, dict) or set(output) != set(schema["required"]):
        keys = sorted(output) if isinstance(output, dict) else [type(output).__name__]
        raise VisitNote2Error(f"LLM API output keys {keys} do not match required keys {schema['required']}.")
    return output


def call_drafting_api(input_data: dict[str, Any], *, model: str | None = None,
                      base_url: str | None = None, api_mode: str = "auto") -> dict[str, Any]:
    return _call_api(input_data, instructions=SYSTEM_INSTRUCTIONS, schema=_output_schema(),
                     model=model, base_url=base_url, api_mode=api_mode)


def call_review_api(input_data: dict[str, Any], *, model: str | None = None,
                    base_url: str | None = None, api_mode: str = "auto") -> dict[str, Any]:
    schema = {
        "type": "object", "additionalProperties": False,
        "properties": {
            "ok": {"type": "boolean"},
            "issues": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["ok", "issues"],
    }
    return _call_api(input_data, instructions=REVIEW_INSTRUCTIONS, schema=schema,
                     model=model, base_url=base_url, api_mode=api_mode)


def _validate_model_output(output: dict[str, Any], current: dict[str, str], *,
                           copied_fields: set[str] | None = None) -> tuple[dict[str, str], list[dict[str, Any]], list[dict[str, Any]]]:
    copied_fields = copied_fields or set()
    proposed: dict[str, str] = {}
    for key, field in FIELD_KEYS.items():
        value = output.get(key)
        if not isinstance(value, str):
            raise VisitNote2Error(f"LLM API field {key} must be a string.")
        value = value.strip().translate(SUBSCRIPT_DIGITS)
        if REVIEW_LANGUAGE.search(value) and normalize_text(value) != normalize_text(current.get(field) or ""):
            raise VisitNote2Error(f"LLM API field {key} contains chart-review commentary.")
        proposed[field] = value
    flags = output.get("review_flags")
    if not isinstance(flags, list):
        raise VisitNote2Error("LLM API review_flags must be an array.")
    cleaned_flags = []
    for item in flags:
        if isinstance(item, str):
            issue = item.strip()
            field_ids: list[str] = []
            action = "RN to review the Pending draft and reconcile before signing."
        elif isinstance(item, dict):
            issue = str(item.get("issue") or item.get("description") or item.get("reason") or "").strip()
            raw_ids = item.get("field_ids", item.get("fields", item.get("field_id", [])))
            if isinstance(raw_ids, str):
                raw_ids = [raw_ids]
            if not isinstance(raw_ids, list) or not all(isinstance(value, str) for value in raw_ids):
                raise VisitNote2Error("LLM API review flag field_ids must be strings.")
            field_ids = [FIELD_KEYS.get(value.strip(), value.strip()) for value in raw_ids if value.strip()]
            action = str(item.get("recommended_action") or item.get("action") or "RN to review the Pending draft and reconcile before signing.").strip()
        else:
            raise VisitNote2Error("LLM API returned an invalid review flag.")
        if not issue:
            raise VisitNote2Error("LLM API returned a review flag without an issue.")
        if issue == OLD_COPY_WARNING:
            raise VisitNote2Error("LLM API must flag a specific unresolved issue, not copied-text provenance.")
        if (re.search(r"\bresponse value ['\"“]?some\b", issue, re.I)
                and normalize_text(proposed[FIELD_KEYS["response"]]).casefold() != "some"):
            raise VisitNote2Error("LLM API review flag still describes the old 'Some' response; describe only the remaining uncertainty.")
        cleaned_flags.append(_flag(issue, field_ids, action))
    resolutions = output.get("resolutions")
    if not isinstance(resolutions, list) or any(
        not isinstance(item, dict)
        or set(item) != {"issue", "field_ids", "decision", "basis", "needs_rn_confirmation"}
        or not all(isinstance(item[name], str) and item[name].strip() for name in ("issue", "decision", "basis"))
        or not isinstance(item["field_ids"], list)
        or not all(isinstance(name, str) for name in item["field_ids"])
        or not isinstance(item["needs_rn_confirmation"], bool)
        for item in resolutions
    ):
        raise VisitNote2Error("LLM API resolutions are invalid.")
    for resolution in resolutions:
        resolution["field_ids"] = [FIELD_KEYS.get(name, name) for name in resolution["field_ids"]]
        if resolution["needs_rn_confirmation"] and not any(
            set(resolution["field_ids"]) & set(flag["field_ids"]) for flag in cleaned_flags
        ):
            raise VisitNote2Error("RN confirmation requires a specific review flag identifying the affected field.")
    if any(not any(field in resolution["field_ids"] for resolution in resolutions) for field in copied_fields):
        raise VisitNote2Error("LLM API omitted a resolution for a copied narrative field.")
    for field in NARRATIVE_FIELDS:
        uncertain = any(resolution["needs_rn_confirmation"] and field in resolution["field_ids"]
                        for resolution in resolutions)
        if uncertain:
            if not any(field in flag["field_ids"] for flag in cleaned_flags):
                raise VisitNote2Error(f"LLM API marked {field} for RN confirmation without a specific review flag.")
        elif not proposed[field]:
            raise VisitNote2Error(f"LLM API field {field} is empty without an RN confirmation question.")
        elif field in copied_fields and normalize_text(proposed[field]).casefold() == normalize_text(current.get(field) or "").casefold():
            raise VisitNote2Error(f"LLM API did not rewrite copied {field}.")
    for flag in cleaned_flags:
        if not flag["field_ids"] or not any(
            resolution["needs_rn_confirmation"] and set(flag["field_ids"]) & set(resolution["field_ids"])
            for resolution in resolutions
        ):
            raise VisitNote2Error("Each unresolved review flag needs a resolution requiring RN confirmation.")
    return proposed, cleaned_flags, resolutions


def _merge_flags(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    result = []
    for group in groups:
        for flag in group:
            issue = str(flag["issue"]).casefold()
            if "clear" in issue and ("breath" in issue or "lung" in issue or "auscult" in issue):
                key = ("respiratory_clear_conflict",)
            elif "rhonch" in issue:
                key = ("respiratory_rhonchi_conflict",)
            else:
                key = (issue, tuple(sorted(flag["field_ids"])))
            if key not in seen:
                seen.add(key)
                result.append(flag)
    return result


def _remove_unsupported_dyspnea_trigger(proposed: dict[str, str], current: dict[str, str],
                                        evidence: list[dict[str, Any]]) -> None:
    if not any(fact["field_id"] == "bsd_Dyspnea_Txt" and fact["value"].casefold() == "moderate"
               for fact in evidence):
        return
    for field in NARRATIVE_FIELDS:
        if normalize_text(proposed[field]) == normalize_text(current.get(field) or ""):
            continue
        proposed[field] = re.sub(
            r"\b(?:exertional dyspnea|dyspnea (?:on|with) moderate (?:exertion|activity))\b",
            "dyspnea", proposed[field], flags=re.I,
        )


def _default_output(task: dict[str, Any], run_id: str) -> str:
    patient = legacy._safe_component(str(task.get("patient") or "patient"))
    date = legacy._safe_component(str(task.get("target_date") or "date"))
    run = legacy._safe_component(run_id)
    created = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return os.path.join(WORKSPACE_ROOT, "drafts", f"visit_note2_{patient}_{date}_{run}_{created}.json")


def _matching_package_file(criteria: dict[str, Any] | None = None, *, saveable_only: bool = False) -> str | None:
    draft_dir = os.path.join(WORKSPACE_ROOT, "drafts")
    if not os.path.isdir(draft_dir):
        return None
    candidates = []
    for name in os.listdir(draft_dir):
        if not name.startswith("visit_note2_") or not name.endswith(".json"):
            continue
        path = os.path.join(draft_dir, name)
        try:
            package = load_json(path)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if (not isinstance(package, dict) or package.get("package_type") != PACKAGE_TYPE
                or package.get("schema_version") != SCHEMA_VERSION):
            continue
        if criteria and not legacy.common._task_matches(package.get("task") or {}, criteria):
            continue
        candidates.append((str(package.get("created_at_utc") or ""), os.path.getmtime(path), path))
    latest = max(candidates)[2] if candidates else None
    if saveable_only and latest:
        status = load_json(latest).get("status")
        if status not in {"draft", "needs_rn_input"}:
            return None
    return latest


def _audit_was_used_for_a_saved_note(audit: dict[str, Any]) -> bool:
    run_id = legacy._audit_run_id(audit)
    draft_dir = os.path.join(WORKSPACE_ROOT, "drafts")
    if run_id == "unknown_run" or not os.path.isdir(draft_dir):
        return False
    for name in os.listdir(draft_dir):
        if not name.endswith(".json"):
            continue
        try:
            package = load_json(os.path.join(draft_dir, name))
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if (isinstance(package, dict) and package.get("audit_run_id") == run_id
                and package.get("status") == "saved_pending_rn_review"):
            return True
    return False


def create_draft(audit: dict[str, Any], audit_file: str, *, model: str | None = None,
                 base_url: str | None = None, api_mode: str = "auto",
                 style_examples: list[str] | None = None) -> dict[str, Any]:
    evidence = extract_current_evidence(audit)
    if not evidence:
        raise VisitNote2Error(
            "Verifier audit contains no target-visit clinical evidence. "
            "Check this task's copier result and rerun verifier after the target note is populated."
        )
    base_package = legacy.prepare_package(audit, audit_file)
    current = base_package["current_values"]
    prompt_data = _prompt_input(audit, base_package, evidence, style_examples)
    copied = set(base_package.get("copied_fields_requiring_confirmation") or [])
    print(f"[API] Drafting {legacy.common._task_label(base_package['task'])}; waiting for model response...", flush=True)
    for attempt in range(3):
        output = call_drafting_api(prompt_data, model=model, base_url=base_url, api_mode=api_mode)
        try:
            proposed, model_flags, resolutions = _validate_model_output(output, current, copied_fields=copied)
        except VisitNote2Error as exc:
            if attempt == 2:
                raise
            prompt_data["revision_issues"] = [str(exc)]
            continue
        _remove_unsupported_dyspnea_trigger(proposed, current, evidence)
        remaining_conflicts = deterministic_review_flags(evidence, proposed)
        if not proposed[FIELD_KEYS["response"]]:
            # A separately flagged missing response is valid in a blocked local draft.
            remaining_conflicts = [flag for flag in remaining_conflicts
                                   if flag["field_ids"] != [FIELD_KEYS["response"]]]
        review = call_review_api({
            "evidence": prompt_data,
            "proposed_fields": proposed,
            "resolutions": resolutions,
            "review_flags": model_flags,
        }, model=model, base_url=base_url, api_mode=api_mode)
        if not isinstance(review.get("ok"), bool) or not isinstance(review.get("issues"), list) or not all(
            isinstance(issue, str) for issue in review["issues"]
        ):
            raise VisitNote2Error("LLM review returned an invalid result.")
        issues = [flag["issue"] for flag in remaining_conflicts] + review["issues"]
        if review["ok"] and not issues:
            break
        if attempt == 2:
            raise VisitNote2Error("Draft review found unresolved conflicts: " + "; ".join(issues or ["review rejected draft"]))
        prompt_data["revision_issues"] = issues or ["Independent review rejected the draft."]
        print("[API] Revising draft to resolve review findings...", flush=True)
    flags = _merge_flags(model_flags)
    updated_fields = [field for field in NARRATIVE_FIELDS
                      if normalize_text(proposed[field]) != normalize_text(current[field])]
    package = {
        "package_type": PACKAGE_TYPE,
        "schema_version": SCHEMA_VERSION,
        "status": "needs_rn_input" if _needs_rn_input(output) else "draft" if updated_fields else "no_change",
        "created_at_utc": _utc_now(),
        "task": base_package["task"],
        "audit_file": os.path.abspath(audit_file),
        "audit_run_id": base_package["audit_run_id"],
        "source_evidence_sha256": _digest(prompt_data),
        "structured_target_visit_evidence": evidence,
        "structured_evidence_sha256": _digest(evidence),
        "field_pages": {name: base_package["field_pages"][name] for name in NARRATIVE_FIELDS},
        "current_values": {name: current[name] for name in NARRATIVE_FIELDS},
        "expected_current_sha256": {name: text_sha256(current[name]) for name in NARRATIVE_FIELDS},
        "proposed_fields": proposed,
        "updated_fields": updated_fields,
        "resolutions": resolutions,
        "review_flags": flags,
        "model": model or os.environ.get("OPENAI_MODEL") or "gpt-4o-mini",
        "style_examples_sha256": _digest(style_examples) if style_examples else None,
    }
    package["proposal_sha256"] = _digest({key: package[key] for key in (
        "task", "audit_run_id", "structured_evidence_sha256", "expected_current_sha256",
        "proposed_fields", "updated_fields", "resolutions", "review_flags", "style_examples_sha256",
    )})
    return package


def _validate_package(package: dict[str, Any], *, require_approval: bool) -> None:
    if package.get("package_type") != PACKAGE_TYPE or package.get("schema_version") != SCHEMA_VERSION:
        raise VisitNote2Error("Unsupported visit-note v2 JSON package.")
    proposed = package.get("proposed_fields") or {}
    current = package.get("current_values") or {}
    if set(proposed) != set(NARRATIVE_FIELDS) or set(current) != set(NARRATIVE_FIELDS):
        raise VisitNote2Error("Package must contain the three current and proposed narrative fields.")
    if any(not isinstance(proposed[name], str) or not isinstance(current[name], str) for name in NARRATIVE_FIELDS):
        raise VisitNote2Error("Each narrative field must be text.")
    blocked = _needs_rn_input(package)
    if package.get("status") != "saved_pending_rn_review" and blocked != (package.get("status") == "needs_rn_input"):
        raise VisitNote2Error("Package status does not match its unresolved RN questions.")
    if not blocked and any(not proposed[name].strip() for name in NARRATIVE_FIELDS):
        raise VisitNote2Error("Each narrative field must have clinician-reviewable text.")
    updates = [field for field in NARRATIVE_FIELDS
               if normalize_text(proposed[field]) != normalize_text(current[field])]
    if package.get("updated_fields") != updates:
        raise VisitNote2Error("Package updated-field scope does not match its draft.")
    if any(REVIEW_LANGUAGE.search(proposed[name])
           for name in updates):
        raise VisitNote2Error("Updated clinical fields contain review commentary.")
    if any(any(char in proposed[name] for char in "₀₁₂₃₄₅₆₇₈₉") for name in updates):
        raise VisitNote2Error("Draft contains subscript digits unsupported by DeVero Save.")
    conflicts = deterministic_review_flags(package.get("structured_target_visit_evidence") or [], proposed)
    if blocked and not proposed[FIELD_KEYS["response"]]:
        conflicts = [flag for flag in conflicts if flag["field_ids"] != [FIELD_KEYS["response"]]]
    if conflicts:
        raise VisitNote2Error("Clinical fields still conflict with structured visit evidence or lack a response.")
    if set(package.get("expected_current_sha256") or {}) != set(NARRATIVE_FIELDS) or any(
        package["expected_current_sha256"][name] != text_sha256(current[name]) for name in NARRATIVE_FIELDS
    ):
        raise VisitNote2Error("Package is missing current-field fingerprints.")
    if set(package.get("field_pages") or {}) != set(NARRATIVE_FIELDS):
        raise VisitNote2Error("Package field-page scope is invalid.")
    if package.get("structured_evidence_sha256") != _digest(package.get("structured_target_visit_evidence") or []):
        raise VisitNote2Error("Package structured evidence changed after drafting.")
    if not isinstance(package.get("review_flags"), list) or not isinstance(package.get("resolutions"), list):
        raise VisitNote2Error("Package review details must be arrays.")
    if any(isinstance(flag, dict) and flag.get("issue") == OLD_COPY_WARNING for flag in package["review_flags"]):
        raise VisitNote2Error("Draft has obsolete generic copied-text warnings; generate a new draft.")
    _validate_model_output({
        **{key: proposed[field] for key, field in FIELD_KEYS.items()},
        "review_flags": package["review_flags"], "resolutions": package["resolutions"],
    }, current)
    task = package.get("task") or {}
    if not task.get("patient") or not task.get("target_date") or not task.get("doc_type"):
        raise VisitNote2Error("Package is missing exact target identity.")
    if not all(re.fullmatch(r"[0-9a-f]{64}", package["expected_current_sha256"][field]) for field in NARRATIVE_FIELDS):
        raise VisitNote2Error("Package current-field fingerprints are invalid.")
    sealed = {key: package.get(key) for key in (
        "task", "audit_run_id", "structured_evidence_sha256", "expected_current_sha256",
        "proposed_fields", "updated_fields", "resolutions", "review_flags", "style_examples_sha256",
    )}
    if package.get("proposal_sha256") != _digest(sealed):
        raise VisitNote2Error("Draft content changed after creation; generate a new draft.")
    if require_approval and (package.get("status") not in {"draft", "needs_rn_input"} or not updates):
        raise VisitNote2Error("Only an unsaved v2 draft with changes can be applied.")


def _verify_live_current(page, package: dict[str, Any]) -> None:
    task = package.get("task") or {}
    live_pages = extract_all_pages_deep(page, label="Pre-apply target")
    if not live_pages or any(not (item.get("extraction") or {}).get("ready") for item in live_pages):
        raise VisitNote2Error("Live visit assessment could not be fully extracted before apply.")
    live_evidence = extract_current_evidence({"target_pages": live_pages})
    if _digest(live_evidence) != package["structured_evidence_sha256"]:
        raise VisitNote2Error("Structured visit fields changed in DeVero since drafting; generate a new audit and draft.")
    legacy._navigate_to_page(page, 1, required_field="VisitDate")
    visit_date = legacy._get_field_value(page, "VisitDate").strip()
    actual_date = legacy.parse_date_safe(visit_date)
    expected_date = legacy.parse_date_safe(task.get("target_date"))
    if not actual_date or not expected_date or actual_date != expected_date:
        raise VisitNote2Error("Opened note visit date does not match the draft JSON package.")
    for field in NARRATIVE_FIELDS:
        legacy._navigate_to_page(page, int(package["field_pages"][field]), required_field=field)
        live = legacy._get_field_value(page, field)
        if text_sha256(live.strip()) != package["expected_current_sha256"][field]:
            if field not in package["updated_fields"] or live.strip() != package["proposed_fields"][field].strip():
                raise VisitNote2Error(
                    f"{field} differs from the verifier audit used for this draft; rerun verifier, then draft."
                )
            print(f"--> {field} already matches this draft; resuming interrupted Save.")


def _verify_saved_narratives(page, package: dict[str, Any]) -> None:
    for field in NARRATIVE_FIELDS:
        legacy._navigate_to_page(page, int(package["field_pages"][field]), required_field=field)
        persisted = legacy._get_field_value(page, field)
        expected = package["proposed_fields"][field]
        if persisted.strip() != expected.strip():
            raise VisitNote2Error(f"Post-save verification failed for {field}; saved value differs from this draft.")
        print(f"[OK] {field} verified on server ({text_sha256(persisted)[:8]}...).")


def apply_to_devero(package: dict[str, Any], *, last_bm_only: bool = False) -> bool:
    _validate_package(package, require_approval=not last_bm_only)
    with tempfile.TemporaryDirectory(prefix="devero_visit_note2_") as profile, sync_playwright() as playwright:
        try:
            browser = playwright.chromium.connect_over_cdp(DEFAULT_CDP_URL, timeout=2000)
            context = browser.contexts[0]
        except Exception:
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=profile, headless=False,
                args=["--no-first-run", "--no-default-browser-check"],
            )
        active = context.pages[0] if context.pages else context.new_page()
        robust_login(active, extract_base_url(DEVERO_URL))
        page = legacy._open_exact_pending_target(context, active, package)
        legacy._verify_target_identity(page, package)
        if last_bm_only:
            legacy._navigate_to_page(page, 1, required_field="VisitDate")
            expected_date = legacy.parse_date_safe(package["task"]["target_date"])
            if not expected_date or legacy.parse_date_safe(legacy._get_field_value(page, "VisitDate")) != expected_date:
                raise VisitNote2Error("Opened note visit date does not match the draft JSON package.")
        else:
            _verify_live_current(page, package)
            for field in package["updated_fields"]:
                legacy._navigate_to_page(page, int(package["field_pages"][field]), required_field=field)
                legacy._set_field(page, field, package["proposed_fields"][field])
        legacy._navigate_to_page(page, 2, required_field=legacy.LAST_BM_FIELD)
        last_bm = legacy._get_field_value(page, legacy.LAST_BM_FIELD).strip()
        fill_last_bm = not last_bm or last_bm.casefold() == "clear"
        if fill_last_bm:
            legacy._set_field(page, legacy.LAST_BM_FIELD, legacy._normalized_last_bm(
                package["task"]["target_date"], package["task"]["target_date"]
            ))
        elif last_bm_only:
            print(f"[SKIP] {legacy.LAST_BM_FIELD} already has a date; preserving it.")
            return False
        legacy._find_exact_save_button(page).click()
        try:
            page.wait_for_timeout(4000)
        except Exception:
            pass
        if page.is_closed():
            surviving = [p for p in context.pages if not p.is_closed()]
            active = surviving[-1] if surviving else context.new_page()
            page = legacy._open_exact_pending_target(context, active, package)
        else:
            page.reload(wait_until="domcontentloaded", timeout=30000)
        legacy._verify_target_identity(page, package)
        if not last_bm_only:
            _verify_saved_narratives(page, package)
        if fill_last_bm:
            legacy._navigate_to_page(page, 2, required_field=legacy.LAST_BM_FIELD)
            persisted = legacy._get_field_value(page, legacy.LAST_BM_FIELD)
            if legacy.parse_date_safe(persisted) != legacy.parse_date_safe(package["task"]["target_date"]):
                raise VisitNote2Error("Post-save verification failed for Last BM; server date differs from visit date.")
            print(f"[OK] Last BM verified on server ({persisted}).")
        return fill_last_bm


def command_draft(args: argparse.Namespace) -> int:
    style_examples = load_style_examples(args.style_examples) if getattr(args, "style_examples", None) else None
    selected_tasks = legacy._tasks_from_args(args)
    if len(selected_tasks) > 1 and (args.audit_file or args.output):
        raise VisitNote2Error("A single audit or output path cannot be shared across multiple tasks.")
    results = []
    tasks = selected_tasks or [None]
    for index, criteria in enumerate(tasks, 1):
        label = legacy.common._task_label(criteria) if criteria else "latest visit-note audit"
        try:
            audit_file = args.audit_file or legacy._latest_visit_note_audit_file(criteria)
            if not audit_file or not os.path.isfile(audit_file):
                raise VisitNote2Error("No matching verifier audit found. Run the visit-note verifier first.")
            audit = load_json(audit_file)
            if criteria and not legacy.common._task_matches(audit.get("task") or {}, criteria):
                raise VisitNote2Error("The selected audit does not match the requested task.")
            if _audit_was_used_for_a_saved_note(audit):
                raise VisitNote2Error("This verifier audit predates a saved note; rerun verifier before drafting.")
            package = create_draft(audit, audit_file, model=args.model, base_url=args.base_url, api_mode=args.api_mode,
                                   style_examples=style_examples)
            _validate_package(package, require_approval=False)
            destination = os.path.abspath(args.output or _default_output(package["task"], package["audit_run_id"]))
            if os.path.exists(destination):
                raise VisitNote2Error(f"Draft path already exists; refusing to overwrite it: {destination}")
            atomic_write_json(destination, package)
            status = {"draft": "DRAFT", "no_change": "NO CHANGE", "needs_rn_input": "DRAFT FOR RN REVIEW"}[package["status"]]
            results.append((label, status, destination))
            print(f"[{status}] {label}: {destination} ({len(package['updated_fields'])} field(s) to update)")
            if len(selected_tasks) <= 1:
                for field in package["updated_fields"]:
                    print(f"\n[{field}]\n{package['proposed_fields'][field]}")
            for resolution in package["resolutions"]:
                prefix = "RN review after Save" if resolution["needs_rn_confirmation"] else "Resolved"
                print(f"  {prefix}: {resolution['issue']} -> {resolution['decision']}")
            for flag in package["review_flags"]:
                print(f"  RN review: {flag['issue']} ({', '.join(flag['field_ids'])})")
        except (VisitNote2Error, legacy.VisitNoteError, OSError, ValueError) as exc:
            results.append((label, "ERROR", str(exc)))
            print(f"[ERROR] {label}: {exc}")
        if index < len(tasks):
            print(f"\n--> Completed Task {index}/{len(tasks)}. Waiting 2s before starting next task...\n", flush=True)
            time.sleep(2)
    print(f"\nDraft summary: {sum(status == 'DRAFT' for _, status, _ in results)} drafts, "
          f"{sum(status == 'DRAFT FOR RN REVIEW' for _, status, _ in results)} drafts with post-save RN questions, "
          f"{sum(status == 'NO CHANGE' for _, status, _ in results)} with no changes, "
          f"{sum(status == 'ERROR' for _, status, _ in results)} errors. DeVero was not changed.")
    return 1 if any(status == "ERROR" for _, status, _ in results) else 0


def _write_rn_review_report(packages: list[dict[str, Any]], results: list[tuple[str, str]]) -> str | None:
    if not packages:
        return None
    lines = ["# RN review of updated Pending visit notes", "",
             "The following draft notes were saved as Pending. Review the text in DeVero, "
             "confirm or correct the items below, and review the full note before signing.", ""]
    for package in packages:
        lines.extend(["## " + legacy.common._task_label(package["task"]), ""])
        flags = package.get("review_flags") or []
        if not flags:
            lines.append("No specific unresolved items were flagged; review the updated note.")
        for flag in flags:
            fields = ", ".join(flag.get("field_ids") or [])
            lines.append(f"- {normalize_text(flag['issue'])} ({fields})")
            if flag.get("recommended_action"):
                lines.append("  RN action: " + normalize_text(flag["recommended_action"]))
        lines.append("")
    failures = [label for label, status in results if status == "ERROR"]
    if failures:
        lines.extend(["## Updates not confirmed", ""])
        lines.extend("- " + label for label in failures)
        lines.append("")
    report = "\n".join(lines)
    directory = os.path.join(WORKSPACE_ROOT, "drafts")
    os.makedirs(directory, exist_ok=True)
    filename = "rn_review_visit_note2_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".md"
    path = os.path.join(directory, filename)
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as handle:
        handle.write(report)
    print("\n" + report)
    print(f"RN review report: {path}")
    return path


def command_apply(args: argparse.Namespace) -> int:
    selected_tasks = legacy._tasks_from_args(args)
    if args.package_file and len(selected_tasks) > 1:
        raise VisitNote2Error("A single package path cannot be applied to multiple tasks.")
    results = []
    review_packages = []
    tasks = selected_tasks or [None]
    for index, criteria in enumerate(tasks, 1):
        label = legacy.common._task_label(criteria) if criteria else "latest unsaved visit-note draft"
        try:
            package_file = args.package_file or _matching_package_file(criteria, saveable_only=True)
            if not package_file:
                latest = _matching_package_file(criteria)
                if latest:
                    latest_status = str(load_json(latest).get("status") or "")
                    if latest_status == "saved_pending_rn_review":
                        package = load_json(latest)
                        print(f"--> Selected saved v2 package: {latest}", flush=True)
                        if not args.yes:
                            task = package["task"]
                            expected = f"SAVE ONLY | {str(task['patient']).upper()} | {task['target_date']} | VISIT NOTE 2"
                            print(f"Type this phrase to save Last BM in DeVero: {expected}")
                            if input("Confirmation: ").strip() != expected:
                                raise VisitNote2Error("Confirmation did not match; DeVero was not changed.")
                        if apply_to_devero(package, last_bm_only=True):
                            results.append((label, "SAVED"))
                            print(f"[SAVED] {label}: blank Last BM set to visit date and verified; note remains Pending.")
                        else:
                            results.append((label, "SKIPPED"))
                        review_packages.append(package)
                        continue
                    if latest_status == "no_change":
                        results.append((label, "SKIPPED"))
                        print(f"[SKIP] {label}: {latest_status}.")
                        continue
                    if latest_status == "sample_only":
                        raise VisitNote2Error("Latest draft was a local sample; generate a fresh audit and draft before apply.")
                raise VisitNote2Error("No unsaved v2 JSON draft matches this task. Run draft first.")
            package = load_json(package_file)
            print(f"--> Selected v2 draft: {package_file}", flush=True)
            if criteria and not legacy.common._task_matches(package.get("task") or {}, criteria):
                raise VisitNote2Error("The selected package does not match the requested task.")
            _validate_package(package, require_approval=True)
            latest_audit_file = legacy._latest_visit_note_audit_file(package["task"])
            if latest_audit_file and legacy._audit_run_id(load_json(latest_audit_file)) != package["audit_run_id"]:
                raise VisitNote2Error("A newer verifier audit exists for this note; run draft again before apply.")
            task = package.get("task") or {}
            if not args.yes:
                expected = f"SAVE ONLY | {str(task.get('patient') or '').upper()} | {task.get('target_date')} | VISIT NOTE 2"
                print(f"Type this phrase to save the draft in DeVero for RN review: {expected}")
                if input("Confirmation: ").strip() != expected:
                    raise VisitNote2Error("Confirmation did not match; DeVero was not changed.")
            last_bm_filled = apply_to_devero(package)
            package["status"] = "saved_pending_rn_review"
            package["applied_at_utc"] = _utc_now()
            atomic_write_json(package_file, package)
            results.append((label, "SAVED"))
            review_packages.append(package)
            print(f"[SAVED] {label}: {len(package['updated_fields'])} narrative field(s) saved and verified; note remains Pending.")
            if last_bm_filled:
                print("  Last BM: blank field set to visit date and verified.")
        except (VisitNote2Error, legacy.VisitNoteError, PlaywrightError, OSError, ValueError) as exc:
            results.append((label, "ERROR"))
            print(f"[ERROR] {label}: {exc}")
        finally:
            if index < len(tasks) and len(results) == index:
                print(f"\n--> Completed Task {index}/{len(tasks)}. Waiting 2s before starting next task...\n", flush=True)
                time.sleep(2)
    print(f"\nApply summary: {sum(status == 'SAVED' for _, status in results)} notes saved, "
          f"{sum(status == 'SKIPPED' for _, status in results)} already saved, "
          f"{sum(status == 'ERROR' for _, status in results)} errors.")
    _write_rn_review_report(review_packages, results)
    return 1 if any(status == "ERROR" for _, status in results) else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="API-assisted Skilled Nurse Visit Note corrections for RN review")
    commands = parser.add_subparsers(dest="command", required=True)
    draft = commands.add_parser("draft", help="Generate one final review draft from a verifier audit")
    draft.add_argument("audit_file", nargs="?", help="Audit JSON; default is latest matching visit-note audit")
    draft.add_argument("--output", help="Output JSON path")
    draft.add_argument("--style-examples", metavar="FILE", help="Private JSON array of deidentified, RN-approved intervention paragraphs")
    draft.add_argument("--model", help="Model name; default OPENAI_MODEL")
    draft.add_argument("--base-url", help="API base URL; default OPENAI_BASE_URL")
    draft.add_argument("--api-mode", choices=("auto", "responses", "chat"), default="auto")
    legacy._add_selectors(draft)
    next(action for action in draft._actions if action.dest == "config_file").help = "Process every active matching task in a tasks configuration file"
    draft.set_defaults(func=command_draft)
    apply_cmd = commands.add_parser("apply", help="Save draft narrative fields to the exact Pending DeVero note for RN review")
    apply_cmd.add_argument("package_file", nargs="?", help="JSON draft path; default is latest matching unsaved v2 draft")
    apply_cmd.add_argument("-y", "--yes", action="store_true", help="Skip the interactive Save-only phrase")
    legacy._add_selectors(apply_cmd)
    next(action for action in apply_cmd._actions if action.dest == "config_file").help = "Process every active matching task in a tasks configuration file"
    apply_cmd.set_defaults(func=command_apply)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\n[CANCELLED] Interrupted.")
        return 130
    except (VisitNote2Error, legacy.VisitNoteError) as exc:
        print(f"[ERROR] {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
