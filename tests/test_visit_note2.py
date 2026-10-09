"""The v2 writer fixes discrepancies without erasing sound or newer RN text."""

import json
import io
import os
from types import SimpleNamespace

import pytest

from writer import fix_visit_note2 as writer


TASK = {"patient": "Test Patient", "target_date": "01/01/2026", "doc_type": "Skilled Nurse Visit Note"}
CURRENT = {
    "int_IntervEducation_Txt": "SN taught fall precautions; patient verbalized full understanding.",
    "int_ResponseInterv_Txt": "Some",
    "osn_OngoingSkilledNeed_Txt": "Skilled nursing remains needed for mobility assessment and safety teaching.",
}
RESOLUTION = {
    "issue": "Full understanding conflicts with the limited response.",
    "field_ids": ["int_ResponseInterv_Txt"],
    "decision": "Use limited understanding and remove the full claim.",
    "basis": "The response field records only Some.",
    "needs_rn_confirmation": True,
}


def _output(interventions="SN reinforced fall precautions and reviewed safe mobility."):
    return {
        "interventions": interventions,
        "response": "Patient showed limited understanding of safety teaching.",
        "ongoing_need": CURRENT["osn_OngoingSkilledNeed_Txt"],
        "review_flags": [{"issue": "Degree of understanding needs confirmation.",
                          "field_ids": ["int_ResponseInterv_Txt"],
                          "recommended_action": "Confirm the patient's response to teaching."}],
        "resolutions": [RESOLUTION],
    }


def _audit():
    return {"target_pages": [{"page_num": 1, "extraction": {"ready": True}, "main": {"fields": [
        {"name": "cf_Temp_Txt", "val": "98.6", "type": "text", "tag": "input", "vis": True}
    ]}}]}


def _base(current=CURRENT, copied=("int_ResponseInterv_Txt",)):
    return {
        "task": TASK, "current_values": dict(current),
        "baseline_values": {**current, "int_IntervEducation_Txt": "Older teaching."},
        "copied_fields_requiring_confirmation": list(copied),
        "field_pages": {name: 2 for name in writer.NARRATIVE_FIELDS},
        "current_context": {"other_narratives": []},
        "audit_run_id": "test_run",
    }


def test_copied_response_and_full_understanding_are_reconciled(monkeypatch):
    monkeypatch.setattr(writer.legacy, "prepare_package", lambda *_: _base())
    calls = []

    def draft(prompt, **_):
        calls.append(prompt.copy())
        return _output(CURRENT["int_IntervEducation_Txt"] if len(calls) == 1 else
                       "SN reinforced fall precautions; patient needed further teaching.")

    monkeypatch.setattr(writer, "call_drafting_api", draft)
    monkeypatch.setattr(writer, "call_review_api", lambda *_, **__: {"ok": True, "issues": []})
    package = writer.create_draft(_audit(), "/tmp/test_audit.json")

    assert len(calls) == 2  # the deterministic conflict forces one revision
    assert package["updated_fields"] == ["int_IntervEducation_Txt", "int_ResponseInterv_Txt"]
    assert package["proposed_fields"]["osn_OngoingSkilledNeed_Txt"] == CURRENT["osn_OngoingSkilledNeed_Txt"]
    assert "RN to" not in package["proposed_fields"]["int_ResponseInterv_Txt"]
    assert package["status"] == "needs_rn_input"
    assert all(flag["issue"] != writer.OLD_COPY_WARNING for flag in package["review_flags"])
    assert "full understanding" not in package["proposed_fields"]["int_IntervEducation_Txt"].lower()
    assert package["resolutions"] == [RESOLUTION]
    assert len(package["review_flags"]) == 1
    assert package["review_flags"][0]["issue"] == "Degree of understanding needs confirmation."
    writer._validate_package(package, require_approval=False)
    writer._validate_package(package, require_approval=True)
    package["review_flags"].append({"issue": writer.OLD_COPY_WARNING, "field_ids": ["int_ResponseInterv_Txt"]})
    with pytest.raises(writer.VisitNote2Error, match="obsolete generic copied-text warnings"):
        writer._validate_package(package, require_approval=False)


def test_sound_noncopy_text_stays_unchanged(monkeypatch):
    current = {**CURRENT, "int_ResponseInterv_Txt": "Patient restated fall precautions with verbal cueing.",
               "int_IntervEducation_Txt": "SN reinforced fall precautions and observed a safe transfer."}
    monkeypatch.setattr(writer.legacy, "prepare_package", lambda *_: _base(current, copied=()))
    monkeypatch.setattr(writer, "call_drafting_api", lambda *_, **__: {
        "interventions": current["int_IntervEducation_Txt"],
        "response": current["int_ResponseInterv_Txt"],
        "ongoing_need": current["osn_OngoingSkilledNeed_Txt"],
        "review_flags": [], "resolutions": [],
    })
    monkeypatch.setattr(writer, "call_review_api", lambda *_, **__: {"ok": True, "issues": []})
    package = writer.create_draft(_audit(), "/tmp/test_audit.json")
    assert package["status"] == "no_change"
    assert package["updated_fields"] == []
    writer._validate_package(package, require_approval=False)
    with pytest.raises(writer.VisitNote2Error, match="with changes"):
        writer._validate_package(package, require_approval=True)


def test_blank_response_with_rn_question_can_be_saved_pending_but_old_schema_is_rejected(monkeypatch):
    monkeypatch.setattr(writer.legacy, "prepare_package", lambda *_: _base())
    monkeypatch.setattr(writer, "call_drafting_api", lambda *_, **__: {**_output(), "response": ""})
    monkeypatch.setattr(writer, "call_review_api", lambda *_, **__: {"ok": True, "issues": []})
    package = writer.create_draft(_audit(), "/tmp/test_audit.json")
    assert package["status"] == "needs_rn_input"
    assert package["proposed_fields"]["int_ResponseInterv_Txt"] == ""
    writer._validate_package(package, require_approval=False)
    writer._validate_package(package, require_approval=True)
    package["status"] = "saved_pending_rn_review"
    writer._validate_package(package, require_approval=False)
    for old_schema in (1, 2):
        with pytest.raises(writer.VisitNote2Error, match="Unsupported"):
            writer._validate_package({"package_type": writer.PACKAGE_TYPE, "schema_version": old_schema}, require_approval=True)


def test_review_marker_cannot_hide_an_exact_copy():
    output = _output()
    output["response"] = "RN REVIEW - confirm for this visit: " + CURRENT["int_ResponseInterv_Txt"]
    with pytest.raises(writer.VisitNote2Error, match="chart-review commentary"):
        writer._validate_model_output(output, CURRENT, copied_fields={"int_ResponseInterv_Txt"})


def test_uncertain_changed_field_keeps_question_outside_prose():
    output = _output()
    output["review_flags"] = [{"issue": "Confirm visit teaching.", "field_ids": ["interventions"],
                               "recommended_action": "RN to confirm."}]
    output["resolutions"] = [{**RESOLUTION, "field_ids": ["interventions"],
                              "needs_rn_confirmation": True}]
    proposed, flags, resolutions = writer._validate_model_output(output, CURRENT)
    assert proposed["int_IntervEducation_Txt"] == output["interventions"]
    assert flags[0]["field_ids"] == ["int_IntervEducation_Txt"]
    assert resolutions[0]["field_ids"] == ["int_IntervEducation_Txt"]


def test_copied_supported_rewrite_has_no_generic_warning():
    output = _output("SN assessed mobility and reinforced fall precautions during the visit.")
    output["response"] = "Patient stated a preference for less discomfort and discussed the plan for mobility safety."
    output["review_flags"] = []
    output["resolutions"] = [{**RESOLUTION, "field_ids": ["int_IntervEducation_Txt", "int_ResponseInterv_Txt"],
                              "needs_rn_confirmation": False}]
    proposed, flags, _ = writer._validate_model_output(
        output, CURRENT, copied_fields={"int_IntervEducation_Txt", "int_ResponseInterv_Txt"})
    assert proposed["int_IntervEducation_Txt"] == output["interventions"]
    assert proposed["int_ResponseInterv_Txt"] == output["response"]
    assert flags == []


def test_uncertain_rewrite_requires_specific_review_work():
    output = _output()
    output["review_flags"] = []
    with pytest.raises(writer.VisitNote2Error, match="specific review flag"):
        writer._validate_model_output(output, CURRENT, copied_fields={"int_ResponseInterv_Txt"})
    output["review_flags"] = [{"issue": "Confirm understanding.",
                               "field_ids": ["int_ResponseInterv_Txt"],
                               "recommended_action": "Ask RN to confirm."}]
    output["response"] = "Patient showed limited understanding and needed further safety teaching."
    proposed, _, _ = writer._validate_model_output(output, CURRENT, copied_fields={"int_ResponseInterv_Txt"})
    assert proposed["int_ResponseInterv_Txt"] == output["response"]


def test_review_flag_cannot_describe_old_some_response():
    output = _output()
    output["review_flags"][0]["issue"] = "The response value Some does not identify who responded."
    with pytest.raises(writer.VisitNote2Error, match="old 'Some' response"):
        writer._validate_model_output(output, CURRENT, copied_fields={"int_ResponseInterv_Txt"})


def test_rewritten_response_drops_original_vague_response_warning(monkeypatch):
    monkeypatch.setattr(writer.legacy, "prepare_package", lambda *_: _base())
    monkeypatch.setattr(writer, "call_drafting_api", lambda *_, **__: _output())
    monkeypatch.setattr(writer, "call_review_api", lambda *_, **__: {"ok": True, "issues": []})
    package = writer.create_draft(_audit(), "/tmp/test_audit.json")
    assert [flag["issue"] for flag in package["review_flags"]] == [
        "Degree of understanding needs confirmation."
    ]


def test_post_save_verification_requires_exact_proposed_text(monkeypatch):
    monkeypatch.setattr(writer.legacy, "_navigate_to_page", lambda *_, **__: None)
    persisted = dict(CURRENT)
    monkeypatch.setattr(writer.legacy, "_get_field_value", lambda _, field: persisted[field])
    package = {"field_pages": {field: 2 for field in writer.NARRATIVE_FIELDS},
               "proposed_fields": dict(CURRENT)}
    writer._verify_saved_narratives(None, package)
    persisted["int_ResponseInterv_Txt"] = "Some additional response"
    with pytest.raises(writer.VisitNote2Error, match="saved value differs"):
        writer._verify_saved_narratives(None, package)


def test_ambiguous_dyspnea_value_does_not_become_exertion():
    proposed = {**CURRENT,
                "int_IntervEducation_Txt": "SN assessed dyspnea on moderate exertion.",
                "osn_OngoingSkilledNeed_Txt": "Continued monitoring for exertional dyspnea is needed."}
    evidence = [{"field_id": "bsd_Dyspnea_Txt", "value": "moderate"}]
    writer._remove_unsupported_dyspnea_trigger(proposed, CURRENT, evidence)
    assert proposed["int_IntervEducation_Txt"] == "SN assessed dyspnea."
    assert proposed["osn_OngoingSkilledNeed_Txt"] == "Continued monitoring for dyspnea is needed."


def test_prompt_marks_copied_ancillary_text_as_provisional():
    copied = "Skin is intact; skin-care instructions explained."
    audit = {"source_pages": [{"page_num": 1, "main": {"fields": [
        {"name": "dia_FootInsp_Txt", "val": copied, "type": "text", "tag": "input", "vis": True}
    ]}}]}
    evidence = [{"page": 1, "field_id": "dia_FootInsp_Txt", "label": "Foot inspection", "value": copied}]
    prompt = writer._prompt_input(audit, _base(), evidence)
    assert prompt["unchanged_copied_ancillary_fields"] == evidence


@pytest.mark.parametrize("mode,base,key,value", [
    ("chat", "http://127.0.0.1:8317/v1", "reasoning_effort", "high"),
    ("responses", "https://api.openai.com/v1", "reasoning", {"effort": "high"}),
])
def test_reasoning_effort_env_is_sent_in_api_request(monkeypatch, mode, base, key, value):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_REASONING_EFFORT", "high")
    sent = []

    def fake_urlopen(request, timeout):
        sent.append(json.loads(request.data))
        output = '{"ok": true, "issues": []}'
        payload = ({"choices": [{"message": {"content": output}}]} if mode == "chat" else
                   {"output": [{"type": "message", "content": [{"type": "output_text", "text": output}]}]})
        return io.BytesIO(json.dumps(payload).encode())

    monkeypatch.setattr(writer.urllib.request, "urlopen", fake_urlopen)
    assert writer.call_review_api({}, base_url=base, api_mode=mode) == {"ok": True, "issues": []}
    assert sent[0][key] == value


def test_reasoning_effort_env_rejects_invalid_value(monkeypatch):
    monkeypatch.setenv("OPENAI_REASONING_EFFORT", "ultra")
    with pytest.raises(writer.VisitNote2Error, match="OPENAI_REASONING_EFFORT"):
        writer.call_review_api({}, base_url="http://127.0.0.1:8317/v1")


def test_api_retries_connection_failure_then_succeeds(monkeypatch, capsys):
    calls = []
    delays = []

    def fake_urlopen(request, timeout):
        calls.append(timeout)
        if len(calls) == 1:
            raise writer.urllib.error.URLError("temporary disconnect")
        return io.BytesIO(b'{"choices": [{"message": {"content": "{\\"ok\\": true, \\"issues\\": []}"}}]}')

    monkeypatch.setattr(writer.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(writer.time, "sleep", delays.append)
    assert writer.call_review_api({}, base_url="http://127.0.0.1:8317/v1") == {"ok": True, "issues": []}
    assert calls == [180, 180]
    assert delays == [2]
    assert "retrying in 2s (attempt 2/3)" in capsys.readouterr().out


def test_api_does_not_retry_bad_request(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout):
        calls.append(timeout)
        raise writer.urllib.error.HTTPError(request.full_url, 400, "bad request", None, None)

    monkeypatch.setattr(writer.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(writer.VisitNote2Error, match="HTTP 400"):
        writer.call_review_api({}, base_url="http://127.0.0.1:8317/v1")
    assert calls == [180]


def test_independent_review_forces_one_revision(monkeypatch):
    current = {**CURRENT, "int_ResponseInterv_Txt": "Patient restated the teaching with cueing."}
    monkeypatch.setattr(writer.legacy, "prepare_package", lambda *_: _base(current, copied=()))
    drafts = []

    def draft(prompt, **_):
        drafts.append(prompt.copy())
        return {
            "interventions": "SN reviewed fall prevention and observed a safe transfer.",
            "response": current["int_ResponseInterv_Txt"],
            "ongoing_need": current["osn_OngoingSkilledNeed_Txt"],
            "review_flags": [], "resolutions": [],
        }

    reviews = iter([{"ok": False, "issues": ["Confirm the transfer description."]},
                    {"ok": True, "issues": []}])
    monkeypatch.setattr(writer, "call_drafting_api", draft)
    monkeypatch.setattr(writer, "call_review_api", lambda *_, **__: next(reviews))
    package = writer.create_draft(_audit(), "/tmp/test_audit.json")
    assert len(drafts) == 2
    assert drafts[1]["revision_issues"] == ["Confirm the transfer description."]
    assert package["updated_fields"] == ["int_IntervEducation_Txt"]


def test_stale_live_narrative_blocks_apply(monkeypatch):
    pages = _audit()["target_pages"]
    monkeypatch.setattr(writer, "extract_all_pages_deep", lambda *_, **__: pages)
    monkeypatch.setattr(writer.legacy, "_navigate_to_page", lambda *_, **__: None)
    values = {**CURRENT, "VisitDate": TASK["target_date"]}
    values["int_IntervEducation_Txt"] = "RN entered newer teaching after the draft."
    monkeypatch.setattr(writer.legacy, "_get_field_value", lambda _, field: values[field])
    package = {
        "task": TASK,
        "structured_evidence_sha256": writer._digest(writer.extract_current_evidence({"target_pages": pages})),
        "field_pages": {field: 2 for field in writer.NARRATIVE_FIELDS},
        "expected_current_sha256": {field: writer.text_sha256(CURRENT[field]) for field in writer.NARRATIVE_FIELDS},
        "proposed_fields": {**CURRENT, "int_IntervEducation_Txt": "A replacement intervention."},
        "updated_fields": ["int_IntervEducation_Txt"],
    }
    with pytest.raises(writer.VisitNote2Error, match="rerun verifier, then draft"):
        writer._verify_live_current(None, package)


def test_apply_fills_blank_last_bm_and_backfill_preserves_narratives(monkeypatch):
    class Page:
        def is_closed(self):
            return False

        def wait_for_timeout(self, *_):
            pass

        def reload(self, **_):
            pass

    page = Page()
    context = type("Context", (), {"pages": [page]})()
    profiles = []

    def unavailable_cdp(*_, **__):
        raise RuntimeError("CDP unavailable")

    def launch(*_, **kwargs):
        profiles.append(kwargs["user_data_dir"])
        assert os.path.isdir(profiles[-1])
        return context

    chromium = type("Chromium", (), {"connect_over_cdp": unavailable_cdp,
                                      "launch_persistent_context": launch})()
    playwright = type("Playwright", (), {"chromium": chromium})()

    class Manager:
        def __enter__(self):
            return playwright

        def __exit__(self, *_):
            pass

    monkeypatch.setattr(writer, "sync_playwright", Manager)
    monkeypatch.setattr(writer, "_validate_package", lambda *_, **__: None)
    monkeypatch.setattr(writer, "robust_login", lambda *_, **__: None)
    monkeypatch.setattr(writer, "_verify_live_current", lambda *_, **__: None)
    monkeypatch.setattr(writer, "_verify_saved_narratives", lambda *_, **__: None)
    monkeypatch.setattr(writer.legacy, "_open_exact_pending_target", lambda *_, **__: page)
    monkeypatch.setattr(writer.legacy, "_verify_target_identity", lambda *_, **__: None)
    monkeypatch.setattr(writer.legacy, "_navigate_to_page", lambda *_, **__: None)
    saves = []
    monkeypatch.setattr(writer.legacy, "_find_exact_save_button", lambda *_, **__: type(
        "Button", (), {"click": lambda *_: saves.append(True)})())
    written = []
    values = {"VisitDate": TASK["target_date"], writer.legacy.LAST_BM_FIELD: ""}
    monkeypatch.setattr(writer.legacy, "_get_field_value", lambda _, field: values[field])

    def set_field(_, field, value):
        written.append((field, value))
        values[field] = value

    monkeypatch.setattr(writer.legacy, "_set_field", set_field)
    package = {
        "task": TASK,
        "field_pages": {field: 2 for field in writer.NARRATIVE_FIELDS},
        "updated_fields": ["int_ResponseInterv_Txt"],
        "proposed_fields": {**CURRENT, "int_ResponseInterv_Txt": "Limited understanding."},
    }
    assert writer.apply_to_devero(package)
    assert written == [("int_ResponseInterv_Txt", "Limited understanding."),
                       (writer.legacy.LAST_BM_FIELD, TASK["target_date"])]
    values[writer.legacy.LAST_BM_FIELD] = ""
    assert writer.apply_to_devero(package, last_bm_only=True)
    assert written[-1] == (writer.legacy.LAST_BM_FIELD, TASK["target_date"])
    assert not writer.apply_to_devero(package, last_bm_only=True)
    assert len(saves) == 2
    assert len(profiles) == 3 and all(not os.path.exists(profile) for profile in profiles)


def test_latest_selector_ignores_old_schema_and_saved_packages(tmp_path, monkeypatch):
    drafts = tmp_path / "drafts"
    drafts.mkdir()
    for name, value in (("array", ["style example"]), ("null", None), ("scalar", "unrelated")):
        (drafts / f"visit_note2_{name}.json").write_text(json.dumps(value))
    old = drafts / "visit_note2_old.json"
    prior = drafts / "visit_note2_prior_draft.json"
    saved = drafts / "visit_note2_saved.json"
    for path, schema, status, created, mtime in (
        (old, 1, "draft", "2026-09-28T00:00:00Z", 3),
        (prior, writer.SCHEMA_VERSION, "draft", "2026-09-26T00:00:00Z", 4),
        (saved, writer.SCHEMA_VERSION, "saved_pending_rn_review", "2026-09-27T00:00:00Z", 2),
    ):
        path.write_text(json.dumps({"package_type": writer.PACKAGE_TYPE, "schema_version": schema,
                                    "task": TASK, "status": status, "created_at_utc": created}))
        os.utime(path, (mtime, mtime))
    monkeypatch.setattr(writer, "WORKSPACE_ROOT", str(tmp_path))
    assert writer._matching_package_file(TASK) == str(saved)
    assert writer._matching_package_file(TASK, saveable_only=True) is None
    assert writer._default_output(TASK, "run") != writer._default_output(TASK, "run")


def test_draft_detects_audit_reused_after_save(tmp_path, monkeypatch):
    drafts = tmp_path / "drafts"
    drafts.mkdir()
    for name, value in (("rn_style_examples", ["SN reviewed safety precautions."]),
                        ("null", None), ("scalar", "unrelated")):
        (drafts / f"{name}.json").write_text(json.dumps(value))
    (drafts / "earlier_saved.json").write_text(json.dumps({
        "audit_run_id": "run-1", "status": "saved_pending_rn_review", "schema_version": 1,
    }))
    monkeypatch.setattr(writer, "WORKSPACE_ROOT", str(tmp_path))
    assert writer._audit_was_used_for_a_saved_note({"audit_run": {"run_id": "run-1"}})
    assert not writer._audit_was_used_for_a_saved_note({"audit_run": {"run_id": "run-2"}})


def test_drafting_and_review_receive_same_numeric_cleanup_policy(monkeypatch):
    calls = []

    def api(data, **kwargs):
        calls.append(kwargs["instructions"])
        return {}

    monkeypatch.setattr(writer, "_call_api", api)
    writer.call_drafting_api({})
    writer.call_review_api({})
    assert len(calls) == 2
    assert all(writer.INTERVENTION_DETAIL_POLICY in instructions for instructions in calls)
    assert "noncopied intervention is a required correction" in writer.INTERVENTION_DETAIL_POLICY


def test_final_rn_report_groups_updated_visits_and_distinguishes_failures(tmp_path, monkeypatch):
    monkeypatch.setattr(writer, "WORKSPACE_ROOT", str(tmp_path))
    packages = [{"task": TASK, "review_flags": [{"issue": "Confirm the recorded teaching response.",
                  "field_ids": ["int_ResponseInterv_Txt"], "recommended_action": "Review and correct the Pending note."}]},
                {"task": {**TASK, "target_date": "01/02/2026"}, "review_flags": []}]
    path = writer._write_rn_review_report(packages, [("first", "SAVED"), ("second", "SAVED"), ("third", "ERROR")])
    text = open(path).read()
    assert "01/01/2026" in text and "01/02/2026" in text
    assert "Confirm the recorded teaching response." in text
    assert "Review and correct the Pending note." in text
    assert "No specific unresolved items" in text
    assert "## Updates not confirmed\n\n- third" in text


def test_apply_rejects_draft_from_older_audit(tmp_path, monkeypatch, capsys):
    draft = tmp_path / "draft.json"
    audit = tmp_path / "audit.json"
    draft.write_text(json.dumps({"task": TASK, "audit_run_id": "old-run", "status": "draft"}))
    audit.write_text(json.dumps({"audit_run": {"run_id": "new-run"}}))
    monkeypatch.setattr(writer.legacy, "_tasks_from_args", lambda _: [])
    monkeypatch.setattr(writer.legacy, "_latest_visit_note_audit_file", lambda _: str(audit))
    monkeypatch.setattr(writer, "_validate_package", lambda *_, **__: None)
    monkeypatch.setattr(writer, "apply_to_devero", lambda *_: pytest.fail("stale draft reached DeVero"))
    assert writer.command_apply(SimpleNamespace(package_file=str(draft), yes=True)) == 1
    assert "newer verifier audit exists" in capsys.readouterr().out


def test_apply_shows_progress_between_tasks(monkeypatch, capsys):
    tasks = [TASK, {**TASK, "target_date": "01/02/2026"}]
    delays = []
    monkeypatch.setattr(writer.legacy, "_tasks_from_args", lambda _: tasks)
    monkeypatch.setattr(writer, "_matching_package_file", lambda *_, **__: None)
    monkeypatch.setattr(writer.time, "sleep", delays.append)
    assert writer.command_apply(SimpleNamespace(package_file=None, yes=True)) == 1
    output = capsys.readouterr().out
    assert "Completed Task 1/2. Waiting 2s before starting next task..." in output
    assert "Completed Task 2/2" not in output
    assert delays == [2]


def test_style_examples_reach_draft_and_review_without_becoming_visit_evidence(tmp_path, monkeypatch):
    path = tmp_path / "style.json"
    examples = ["SN assessed wheelchair positioning and taught pressure relief."]
    path.write_text(json.dumps(examples))
    current = {**CURRENT, "int_ResponseInterv_Txt": "Patient restated fall precautions."}
    monkeypatch.setattr(writer.legacy, "prepare_package", lambda *_: _base(current, copied=()))
    requests, reviews = [], []

    def draft(prompt, **_):
        requests.append(prompt.copy())
        return {"interventions": "SN assessed wheelchair positioning." if len(requests) == 1 else
                "SN assessed mobility and taught fall precautions.",
                "response": current["int_ResponseInterv_Txt"], "ongoing_need": current["osn_OngoingSkilledNeed_Txt"],
                "review_flags": [], "resolutions": []}

    def review(prompt, **_):
        reviews.append(prompt)
        borrowed = "wheelchair" in prompt["proposed_fields"]["int_IntervEducation_Txt"]
        return {"ok": not borrowed, "issues": ["Wheelchair was borrowed from the style example."] if borrowed else []}

    monkeypatch.setattr(writer, "call_drafting_api", draft)
    monkeypatch.setattr(writer, "call_review_api", review)
    package = writer.create_draft(_audit(), "audit.json", style_examples=writer.load_style_examples(str(path)))
    assert len(requests) == 2
    assert all(prompt["style_reference_interventions"] == examples for prompt in requests)
    assert reviews[0]["evidence"]["style_reference_interventions"] == examples
    assert "wheelchair" not in json.dumps(package["structured_target_visit_evidence"])
    assert "wheelchair" not in package["proposed_fields"]["int_IntervEducation_Txt"]
    assert package["style_examples_sha256"] == writer._digest(examples)
    writer._validate_package(package, require_approval=True)
    package["style_examples_sha256"] = "changed"
    with pytest.raises(writer.VisitNote2Error, match="content changed"):
        writer._validate_package(package, require_approval=True)
    assert writer.build_parser().parse_args(["draft", "--style-examples", str(path)]).style_examples == str(path)


@pytest.mark.parametrize("examples", [[], [""], [42], {"patient": "Synthetic name"}, ["x"] * 9,
                                     ["x" * 10001], ["x" * 9000] * 5])
def test_invalid_style_files_fail_before_model_request(tmp_path, examples):
    path = tmp_path / "style.json"
    path.write_text(json.dumps(examples))
    with pytest.raises(writer.VisitNote2Error, match="Style examples"):
        writer.load_style_examples(str(path))


def test_unreadable_style_files_have_clear_errors(tmp_path):
    path = tmp_path / "style.json"
    for content in (None, "invalid JSON", " " * 160001):
        if content is not None:
            path.write_text(content)
        with pytest.raises(writer.VisitNote2Error, match="Cannot load style examples"):
            writer.load_style_examples(str(path))


@pytest.mark.parametrize("last_bm_only", [False, True])
def test_invalid_package_cannot_open_browser_even_for_backfill(monkeypatch, last_bm_only):
    monkeypatch.setattr(writer, "sync_playwright", lambda: pytest.fail("invalid draft opened browser"))
    package = {"status": "needs_rn_input", "resolutions": [RESOLUTION], "review_flags": []}
    with pytest.raises(writer.VisitNote2Error, match="Unsupported"):
        writer.apply_to_devero(package, last_bm_only=last_bm_only)
    package["status"] = "draft"
    with pytest.raises(writer.VisitNote2Error, match="Unsupported"):
        writer.apply_to_devero(package, last_bm_only=last_bm_only)


def test_review_questions_are_retained_when_yes_saves_pending_draft(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(writer, "WORKSPACE_ROOT", str(tmp_path))
    current = {**CURRENT, "int_IntervEducation_Txt": "SN assessed mobility and reinforced fall precautions."}
    monkeypatch.setattr(writer.legacy, "prepare_package", lambda *_: _base(current))
    monkeypatch.setattr(writer, "call_drafting_api", lambda *_, **__: {**_output(current["int_IntervEducation_Txt"]), "response": ""})
    monkeypatch.setattr(writer, "call_review_api", lambda *_, **__: {"ok": True, "issues": []})
    audit = tmp_path / "audit.json"
    audit.write_text(json.dumps(_audit()))
    output = tmp_path / "blocked.json"
    monkeypatch.setattr(writer.legacy, "_tasks_from_args", lambda _: [])
    args = writer.build_parser().parse_args(["draft", str(audit), "--output", str(output)])
    assert writer.command_draft(args) == 0
    assert json.loads(output.read_text())["status"] == "needs_rn_input"
    saved = []
    monkeypatch.setattr(writer, "apply_to_devero", lambda package: saved.append(package.copy()) or False)
    monkeypatch.setattr(writer.legacy, "_latest_visit_note_audit_file", lambda *_: None)
    assert writer.command_apply(SimpleNamespace(package_file=str(output), yes=True)) == 0
    assert len(saved) == 1
    result = json.loads(output.read_text())
    assert result["status"] == "saved_pending_rn_review"
    assert result["review_flags"]
    writer._validate_package(result, require_approval=False)
    reports = list((tmp_path / "drafts").glob("rn_review_visit_note2_*.md"))
    assert len(reports) == 1
    assert result["review_flags"][0]["issue"] in reports[0].read_text()
    assert reports[0].stat().st_mode & 0o777 == 0o600


def test_newest_draft_with_questions_is_selected_instead_of_older_draft(tmp_path, monkeypatch, capsys):
    directory = tmp_path / "drafts"
    directory.mkdir()
    for status, created in (("draft", "2026-01-01"), ("needs_rn_input", "2026-01-02")):
        (directory / f"visit_note2_{status}.json").write_text(json.dumps({
            "package_type": writer.PACKAGE_TYPE, "schema_version": writer.SCHEMA_VERSION,
            "task": TASK, "status": status, "created_at_utc": created,
        }))
    monkeypatch.setattr(writer, "WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setattr(writer.legacy, "_tasks_from_args", lambda _: [TASK])
    assert writer._matching_package_file(TASK, saveable_only=True).endswith("visit_note2_needs_rn_input.json")
    assert writer.command_apply(SimpleNamespace(package_file=None, yes=True)) == 1
    assert "Package must contain the three" in capsys.readouterr().out


def test_empty_field_without_confirmation_is_rejected_and_review_instructions_are_rejected():
    output = {**_output(), "response": "", "resolutions": [], "review_flags": []}
    with pytest.raises(writer.VisitNote2Error, match="empty without"):
        writer._validate_model_output(output, CURRENT)
    for text in ("RN to confirm the patient's response.", "RN REVIEW - check teaching.", "This action was not confirmed."):
        with pytest.raises(writer.VisitNote2Error, match="chart-review commentary"):
            writer._validate_model_output({**_output(), "response": text}, CURRENT)
