from pathlib import Path

import pytest

from writer.fix_oasis import build_fix_plan
from writer.fix_visit_note import _contains_concrete_number, _strip_numerical_vitals, _validate_narrative_draft
from writer import fix_oasis_summary as summary
from writer import fix_visit_note2 as visit_writer
from writer.oasis_summary import approve_package, prepare_package, validate_package


def test_numeric_vitals_are_removed_without_losing_other_education():
    text = (
        "Vital signs obtained with BP 144/68 sitting, pulse 60 and regular. "
        "SN reviewed fall precautions and safe walker use.\n\n"
        "SN reinforced medication precautions."
    )
    cleaned = _strip_numerical_vitals(text)
    assert cleaned == "SN reviewed fall precautions and safe walker use.\n\nSN reinforced medication precautions."
    assert _strip_numerical_vitals("BP 144/68; SN reinforced fall precautions.") == "BP 144/68; SN reinforced fall precautions."
    assert not _contains_concrete_number("SN reviewed SpO2 monitoring.")
    assert _contains_concrete_number("SN reviewed two falls.")


def test_visit_note_rejects_other_concrete_numbers():
    proposed = {
        "int_IntervEducation_Txt": "SN reviewed 2 medication doses and reinforced fall precautions with the patient and caregiver. " * 2,
        "int_ResponseInterv_Txt": "Patient verbalized understanding of the teaching.",
        "osn_OngoingSkilledNeed_Txt": "Skilled nursing remains needed to monitor symptoms and reinforce safe medication use with the caregiver.",
    }
    assert any("concrete number" in error for error in _validate_narrative_draft({}, proposed))
    proposed["int_IntervEducation_Txt"] = proposed["int_IntervEducation_Txt"].replace("2", "two")
    assert any("concrete number" in error for error in _validate_narrative_draft({}, proposed))


def test_oasis_automatic_fixes_preserve_numeric_education():
    def audit(response):
        return {
            "task": {"doc_type": "OASIS-E2 Start of Care"},
            "audit_results": {"do_now_items": [{"field": "int_IntervEducation_Txt"}]},
            "target_pages": [{"main": {"fields": [
                {"name": "int_IntervEducation_Txt", "val": "Patient verbalized partial understanding."},
                {"name": "int_ResponseInterv_Txt", "val": response},
            ]}}],
        }

    plan = build_fix_plan(audit("Vital signs obtained with BP 144/68 sitting, pulse 60 and regular. SN reinforced fall precautions."))
    assert plan["field_values"]["int_IntervEducation_Txt"].startswith("Vital signs obtained with BP 144/68")
    text = "SN taught 2 medication doses and reinforced fall precautions."
    assert build_fix_plan(audit(text))["field_values"]["int_IntervEducation_Txt"] == text


def summary_package():
    return {
        "schema_version": 1,
        "task": {"patient": "Synthetic Patient", "caregiver": "Test RN",
                 "doc_type": "OASIS-E2 Nurse Recert", "target_date": "10/01/2026",
                 "source_date": "08/01/2026"},
        "overwrite_scope": {"permitted_fields": ["sum_AssessmentSum_Txt"]},
        "verified_current_context": {
            "narratives": [{"name": "int_IntervEducation_Txt",
                           "text": "SN taught medication precautions and BP monitoring twice daily."}],
            "structured_fields": [{"name": "cf_SystolicBP_Txt", "value": "144"}],
        },
        "clinical_review_flags": [],
    }


def test_summary_style_rewrite_uses_examples_and_revises_numbers(monkeypatch):
    requests = []
    examples = ["SN assessed mobility and taught walker safety."]

    def api(data, **kwargs):
        requests.append((data.copy(), kwargs))
        if "proposed_summary" in data:
            return {"ok": True, "issues": []}
        text = "SN taught BP monitoring twice daily." if len(requests) == 1 else "SN reviewed prescribed medication precautions and blood pressure monitoring."
        return {"summary": text, "review_questions": []}

    monkeypatch.setattr(visit_writer, "_call_api", api)
    package = summary_package()
    draft, _ = summary.generate_draft_for_package(package, style_examples=examples, model="test-model", api_key="test-key")
    assert len(requests) == 3
    assert requests[0][0]["style_reference_interventions"] == examples
    assert requests[1][0]["revision_issues"]
    assert requests[1][0]["previous_draft"].endswith("twice daily.")
    assert requests[2][0]["evidence"]["style_reference_interventions"] == examples
    assert "NO specific measurements" in requests[0][1]["instructions"]
    assert summary.SUMMARY_DETAIL_POLICY in requests[0][1]["instructions"]
    assert summary.SUMMARY_DETAIL_POLICY in requests[2][1]["instructions"]
    assert requests[0][1]["api_key"] == "test-key"
    assert requests[0][1]["model"] == "test-model"
    assert draft.startswith("Interventions/Education Provided:\n\n")
    assert not _contains_concrete_number(draft)
    assert package["verified_current_context"]["structured_fields"][0]["value"] == "144"
    assert validate_package(approve_package(package, draft, "Test RN")) == []


@pytest.mark.parametrize("text", ["SN taught 2 doses.", "SN taught two doses.", "SN reviewed temperature ₉₈.₆.",
                                  "RN to confirm education occurred.", "", "Interventions/Education Provided:"])
def test_summary_invalid_model_prose_cannot_be_drafted(monkeypatch, text):
    monkeypatch.setattr(visit_writer, "_call_api", lambda *_, **__: {"summary": text, "review_questions": []})
    with pytest.raises(ValueError, match="needs review"):
        summary.generate_draft_for_package(summary_package())


def test_summary_missing_facts_block_auto_approval(tmp_path, monkeypatch):
    import json
    package = tmp_path / "package.json"
    examples = tmp_path / "style.json"
    previously_approved = approve_package(summary_package(), "SN reviewed medication precautions and blood pressure monitoring.", "Test RN")
    package.write_text(json.dumps(previously_approved))
    examples.write_text(json.dumps(["SN reviewed safety precautions."]))
    args = summary.build_parser().parse_args([
        "draft", str(package), "--auto-approve", "--style-examples", str(examples),
        "--output", str(tmp_path / "draft.txt"),
    ])
    monkeypatch.setattr(visit_writer, "_call_api", lambda *_, **__: {
        "summary": "SN assessed current symptoms.",
        "review_questions": ["Which education was performed during this assessment?"],
    })
    monkeypatch.setattr(summary, "approve_package", lambda *_, **__: pytest.fail("Blocked draft was approved"))
    with pytest.raises(ValueError, match="needs RN input"):
        summary.command_draft(args)
    assert not (tmp_path / "draft.txt").exists()
    assert not json.loads(package.read_text()).get("clinician_review", {}).get("approved")


def test_summary_review_rejects_borrowed_example_facts(monkeypatch):
    requests = []

    def api(data, **_):
        requests.append(data.copy())
        if "proposed_summary" in data:
            borrowed = "walker" in data["proposed_summary"]
            return {"ok": not borrowed, "issues": ["Walker came from the example, not target evidence."] if borrowed else []}
        return {"summary": "SN taught walker safety." if len(requests) == 1 else
                "SN reviewed medication precautions and blood pressure monitoring.", "review_questions": []}

    monkeypatch.setattr(visit_writer, "_call_api", api)
    draft, _ = summary.generate_draft_for_package(summary_package(), style_examples=["SN taught walker safety."])
    assert len(requests) == 4
    assert "walker" not in draft


def test_summary_unresolved_flags_and_api_failure_do_not_fall_back(monkeypatch):
    package = summary_package()
    package["clinical_review_flags"] = [{"id": "missing-teaching", "resolved": False}]
    monkeypatch.setattr(visit_writer, "_call_api", lambda *_, **__: pytest.fail("Unresolved flags reached model"))
    with pytest.raises(ValueError, match="Resolve clinical"):
        summary.generate_draft_for_package(package)
    package["clinical_review_flags"] = []

    def unavailable(*_, **__):
        raise visit_writer.VisitNote2Error("API unavailable")
    monkeypatch.setattr(visit_writer, "_call_api", unavailable)
    with pytest.raises(ValueError, match="API unavailable"):
        summary.generate_draft_for_package(package)


@pytest.mark.parametrize("prose", ["SN taught two medication doses.", "SN reviewed BP 144/68.",
                                   "SN reviewed temperature ₉₈.₆.", "RN to confirm education occurred."])
def test_summary_approval_and_apply_reject_unsafe_prose(tmp_path, monkeypatch, prose):
    import json
    package = summary_package()
    with pytest.raises(ValueError):
        approve_package(package, prose, "Test RN")
    package["proposed_summary"] = prose
    package["clinician_review"] = {"approved": True, "approved_by": "Test RN"}
    file = tmp_path / "package.json"
    file.write_text(json.dumps(package))
    monkeypatch.setattr(summary, "sync_playwright", lambda: pytest.fail("Unsafe summary opened browser"))
    args = summary.build_parser().parse_args(["apply", str(file), "--yes"])
    with pytest.raises(ValueError):
        summary.command_apply(args)


def batch_tasks(tmp_path):
    import json
    tasks = [summary_package()["task"], {**summary_package()["task"],
                                       "patient": "Another Synthetic Patient", "target_date": "09/23/2026"}]
    config = tmp_path / "tasks.jsonl"
    config.write_text("\n".join(json.dumps(task) for task in [*tasks, {**tasks[0], "enabled": False}]))
    return config, tasks


def test_summary_batch_drafts_and_approves_each_matching_package(tmp_path, monkeypatch):
    import json
    config, tasks = batch_tasks(tmp_path)
    drafts = tmp_path / "drafts"
    drafts.mkdir()
    (drafts / "rn_style_examples.json").write_text(json.dumps(["SN reinforced safety precautions."]))
    (drafts / "unrelated.json").write_text("null")
    (drafts / "malformed_package.json").write_text(json.dumps({"overwrite_scope": [], "task": []}))
    for index, task in enumerate(tasks):
        package = summary_package()
        package["task"] = task
        (drafts / f"package_{index}.json").write_text(json.dumps(package))
    monkeypatch.setattr(summary, "WORKSPACE_ROOT", str(tmp_path))
    calls = []

    def generate(package, **kwargs):
        calls.append(package["task"])
        return "Interventions/Education Provided:\n\nSN reviewed medication precautions and blood pressure monitoring.", "test model"

    monkeypatch.setattr(summary, "generate_draft_for_package", generate)
    args = summary.build_parser().parse_args(["draft", "--auto-approve", "-t", str(config)])
    assert summary.run_selected_tasks(args) == 0
    assert calls == tasks
    for index, task in enumerate(tasks):
        package = json.loads((drafts / f"package_{index}.json").read_text())
        assert package["task"] == task
        assert package["clinician_review"]["approved"]
        assert summary.validate_package(package) == []
        assert Path(summary._default_draft_path(package)).exists()


@pytest.mark.parametrize("command", ["prepare", "draft", "approve", "validate", "apply"])
def test_summary_batch_continues_after_failure_and_reports_nonzero(tmp_path, command, capsys):
    config, tasks = batch_tasks(tmp_path)
    calls = []
    args = summary.build_parser().parse_args([command, "-t", str(config)])

    def run_task(selected):
        criteria = summary._task_criteria_from_args(selected)
        calls.append(criteria)
        if len(calls) == 1:
            raise summary.OASISSummaryError("Synthetic missing evidence")
        return 0

    args.func = run_task
    assert summary.run_selected_tasks(args) == 2
    assert calls == tasks
    output = capsys.readouterr().out
    assert "[FAILED]" in output and "[OK]" in output


def test_summary_batch_filters_and_rejects_shared_output_before_work(tmp_path):
    config, tasks = batch_tasks(tmp_path)
    calls = []
    args = summary.build_parser().parse_args(["draft", "-t", str(config), "--output", str(tmp_path / "shared.txt")])
    args.func = lambda selected: calls.append(summary._task_criteria_from_args(selected)) or 0
    with pytest.raises(ValueError, match="cannot be shared"):
        summary.run_selected_tasks(args)
    assert calls == []
    args.patient = tasks[1]["patient"]
    assert summary.run_selected_tasks(args) == 0
    assert calls == [tasks[1]]


def test_summary_approve_never_uses_another_tasks_draft(tmp_path, monkeypatch):
    import json
    package = tmp_path / "package.json"
    package.write_text(json.dumps(summary_package()))
    monkeypatch.setattr(summary, "WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setattr(summary, "_latest_draft_file", lambda: pytest.fail("Selected task used unrelated draft"))
    args = summary.build_parser().parse_args(["approve", str(package), "--patient", summary_package()["task"]["patient"]])
    with pytest.raises(ValueError, match="No draft text"):
        summary.command_approve(args)


def test_summary_apply_does_not_fall_back_to_older_approved_package(tmp_path, monkeypatch):
    import json
    package = tmp_path / "blocked.json"
    data = summary_package()
    package.write_text(json.dumps(data))
    lookups = []
    monkeypatch.setattr(summary, "_matching_package_file", lambda criteria, **kwargs: lookups.append(kwargs) or str(package))
    monkeypatch.setattr(summary, "sync_playwright", lambda: pytest.fail("Blocked package opened browser"))
    args = summary.build_parser().parse_args(["apply", "--patient", data["task"]["patient"], "--yes"])
    with pytest.raises(ValueError, match="Clinician approval"):
        summary.command_apply(args)
    assert lookups == [{}]


def test_summary_audit_discovery_skips_non_object_json(tmp_path, monkeypatch):
    import json
    logs = tmp_path / "logs"
    logs.mkdir()
    task = summary_package()["task"]
    audit = logs / "audit_valid.json"
    audit.write_text(json.dumps({"task": task}))
    (logs / "audit_array.json").write_text("[]")
    (logs / "audit_null.json").write_text("null")
    (logs / "audit_bad_task.json").write_text(json.dumps({"task": [task]}))
    monkeypatch.setattr(summary, "WORKSPACE_ROOT", str(tmp_path))
    assert summary._matching_audit_file(task) == str(audit)


def complete_summary_audit():
    return {
        "task": {**summary_package()["task"], "source_doc_type": "OASIS-E2 Nurse Recert"},
        "audit_run": {"run_id": "synthetic-run"},
        "audit_results": {"clinical_decision_items": [], "verification_error_items": []},
        "target_pages": [{"page_num": 1, "extraction": {"ready": True}, "main": {
            "fields": [{"name": "sum_AssessmentSum_Txt", "val": "Current assessment narrative."},
                       {"name": "cf_SystolicBP_Txt", "val": "144"}], "narratives": []}}],
        "source_pages": [{"page_num": 1, "extraction": {"ready": True}, "main": {
            "fields": [{"name": "sum_AssessmentSum_Txt", "val": "Baseline assessment narrative."}],
            "narratives": []}}],
    }


def test_summary_explicit_rewrite_does_not_require_review_item():
    package = prepare_package(complete_summary_audit(), "audit.json")
    assert package["audit_run_id"] == "synthetic-run"
    assert package["overwrite_scope"]["permitted_fields"] == ["sum_AssessmentSum_Txt"]
    assert package["verified_current_context"]["structured_fields"]


@pytest.mark.parametrize("missing", ["stable-extraction", "current-evidence", "baseline", "run-id", "oasis-document"])
def test_summary_no_review_item_still_requires_valid_evidence(missing):
    audit = complete_summary_audit()
    if missing == "stable-extraction":
        audit["target_pages"][0]["extraction"]["ready"] = False
    elif missing == "current-evidence":
        audit["target_pages"][0]["main"]["fields"].pop()
    elif missing == "baseline":
        audit["source_pages"] = []
    elif missing == "run-id":
        audit["audit_run"] = {}
    else:
        audit["task"]["doc_type"] = "Skilled Nurse Visit Note"
    with pytest.raises(ValueError):
        prepare_package(audit, "audit.json")


def test_summary_exhausted_review_preserves_unapproved_text_and_issues(tmp_path, monkeypatch):
    import json
    package = tmp_path / "package.json"
    package.write_text(json.dumps(summary_package()))
    output = tmp_path / "draft.txt"
    requests = []
    issue = "Preserve documented energy conservation teaching and the recommendation for PT evaluation."

    def api(data, **kwargs):
        requests.append(data.copy())
        if "proposed_summary" in data:
            return {"ok": False, "issues": [issue]}
        return {"summary": "SN reviewed medication precautions.", "review_questions": []}

    monkeypatch.setattr(visit_writer, "_call_api", api)
    monkeypatch.setattr(summary, "approve_package", lambda *_, **__: pytest.fail("Rejected draft was approved"))
    args = summary.build_parser().parse_args(["draft", str(package), "--output", str(output), "--auto-approve"])
    with pytest.raises(summary.SummaryReviewError):
        summary.command_draft(args)
    assert len(requests) == 6
    assert requests[2]["previous_draft"] == requests[1]["proposed_summary"]
    assert requests[2]["revision_issues"] == [issue]
    assert not output.exists()
    assert Path(str(output) + ".blocked.txt").read_text().endswith("medication precautions.")
    assert json.loads(Path(str(output) + ".blocked.txt.review.json").read_text())["issues"] == [issue]
    assert not json.loads(package.read_text())["clinician_review"]["approved"]


@pytest.mark.parametrize("first_failure", [None, "missing-audit", "incomplete-audit"])
def test_automatic_oasis_writer_iterates_exact_tasks(tmp_path, monkeypatch, capsys, first_failure):
    import json
    import sys
    from writer import fix_oasis as automatic
    config, tasks = batch_tasks(tmp_path)
    logs = tmp_path / "logs"
    logs.mkdir()
    for index, task in enumerate(tasks):
        if index == 0 and first_failure == "missing-audit":
            continue
        data = {"task": task, "audit_results": {"do_now_items": []}}
        if index == 0 and first_failure == "incomplete-audit":
            data["audit_results"]["pass_fail_status"] = "ERROR / INCOMPLETE"
        (logs / f"audit_task_{index}.json").write_text(json.dumps(data))
    # This newer same-patient audit must never substitute for the requested visit.
    unrelated = {"task": {**tasks[0], "target_date": "08/01/2026"},
                 "audit_results": {"do_now_items": [{"field": "VisitDate"}]}}
    (logs / "audit_unrelated_visit.json").write_text(json.dumps(unrelated))
    monkeypatch.setattr(automatic, "WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setattr(automatic, "sync_playwright", lambda: pytest.fail("No-fix or unrelated task opened chart"))
    monkeypatch.setattr(sys, "argv", ["fix_oasis.py", "-t", str(config)])
    assert automatic.main() == (2 if first_failure else 0)
    output = capsys.readouterr().out
    assert "[1/2]" in output and "[2/2]" in output
    assert str(logs / "audit_task_1.json") in output
    assert str(logs / "audit_unrelated_visit.json") not in output
    assert "[OK] " + summary._task_label(tasks[1]) in output


def test_automatic_oasis_writer_rejects_shared_explicit_audit(tmp_path, monkeypatch, capsys):
    import sys
    from writer import fix_oasis as automatic
    config, _ = batch_tasks(tmp_path)
    monkeypatch.setattr(sys, "argv", ["fix_oasis.py", "-t", str(config), str(tmp_path / "audit.json")])
    monkeypatch.setattr(automatic, "command_apply", lambda _: pytest.fail("Ambiguous audit was processed"))
    assert automatic.main() == 2
    assert "cannot be shared" in capsys.readouterr().out
