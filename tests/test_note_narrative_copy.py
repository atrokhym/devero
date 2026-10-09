"""The note copier resumes a partly populated note and fills custom textareas."""

import os
import sys

from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import devero_copier_note as copier


def test_temperature_skip_flag_defaults_on():
    parser = copier.build_parser()
    assert parser.parse_args([]).skip_if_temperature is True
    assert parser.parse_args(["--no-skip-if-temperature"]).skip_if_temperature is False


def test_note_temperature_check_reads_target_value():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content('<input name="cf_Temp_Txt" value="98.6">')
        assert page.evaluate(copier.JS_CHECK_TEMPERATURE) == "98.6"
        page.locator('[name="cf_Temp_Txt"]').fill("")
        assert page.evaluate(copier.JS_CHECK_TEMPERATURE) is None
        browser.close()


def test_narrative_copy_preserves_existing_temperature(monkeypatch):
    monkeypatch.setattr(copier, "_save_target_note", lambda page: None)
    monkeypatch.setattr(copier, "wait_for_form_inputs", lambda *args, **kwargs: 200)
    monkeypatch.setattr(copier, "wait_for_page_extraction_ready", lambda *args, **kwargs: {"ready": True})
    monkeypatch.setattr(copier, "go_to_next_page", lambda *args: False)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        source = browser.new_page()
        source.set_content("""
            <cmwf-textarea id="int_IntervEducation_Txt">
              <textarea name="int_IntervEducation_Txt">Source teaching</textarea>
            </cmwf-textarea>
            <cmwf-textarea id="int_ResponseInterv_Txt">
              <textarea name="int_ResponseInterv_Txt">Source response</textarea>
            </cmwf-textarea>
        """)
        fields = source.evaluate(copier.JS_EXTRACT_FIELDS)

        target = browser.new_page()
        target.set_content("""
            <input name="cf_Temp_Txt" value="98.6">
            <cmwf-textarea id="int_IntervEducation_Txt">
              <textarea name="int_IntervEducation_Txt"></textarea>
            </cmwf-textarea>
            <cmwf-textarea id="int_ResponseInterv_Txt">
              <textarea name="int_ResponseInterv_Txt"></textarea>
            </cmwf-textarea>
        """)
        copier.inject_all_pages_target(target, fields, skip_if_temperature=False)

        assert target.locator('[name="cf_Temp_Txt"]').input_value() == "98.6"
        assert target.locator('[name="int_IntervEducation_Txt"]').input_value() == "Source teaching"
        assert target.locator('[name="int_ResponseInterv_Txt"]').input_value() == "Source response"
        browser.close()


def test_delayed_page_two_is_loaded_before_injection(monkeypatch):
    monkeypatch.setattr(copier, "_save_target_note", lambda page: None)
    monkeypatch.setattr(copier, "_fill_pedal_pulses", lambda page: 0)
    monkeypatch.setattr(copier, "wait_for_form_inputs", lambda *args, **kwargs: 200)
    monkeypatch.setattr(copier, "wait_for_page_extraction_ready", lambda *args, **kwargs: {"ready": True})

    def next_page(page, page_num):
        if page_num != 1:
            return False
        page.evaluate("""() => setTimeout(() => {
            document.body.innerHTML = `<cmwf-textarea id="int_IntervEducation_Txt">
                <textarea name="int_IntervEducation_Txt"></textarea>
            </cmwf-textarea>`;
        }, 3500)""")
        return True

    monkeypatch.setattr(copier, "go_to_next_page", next_page)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        target = browser.new_page()
        target.set_content('<input name="cf_Temp_Txt" value="98.6">')
        copier.inject_all_pages_target(target, {
            "int_IntervEducation_Txt": {"kind": "text", "value": "Source teaching"}
        }, skip_if_temperature=False)
        assert target.locator('[name="int_IntervEducation_Txt"]').input_value() == "Source teaching"
        browser.close()


def test_default_skip_waits_for_temperature_in_all_playwright_frames(monkeypatch):
    from unittest.mock import Mock

    target = Mock()
    target.frames = [Mock(), Mock()]
    target.frames[0].evaluate.return_value = None
    monkeypatch.setattr(copier, "wait_for_form_inputs", lambda *args, **kwargs: 200)

    def ready(*args, **kwargs):
        # Temperature arrives after the initial form-control count succeeds.
        target.frames[1].evaluate.return_value = "98.6"
        return {"ready": True}

    monkeypatch.setattr(copier, "wait_for_page_extraction_ready", ready)
    target.evaluate.side_effect = AssertionError("Skip must happen before any injection or Save")
    assert copier.inject_all_pages_target(target, {"needed": {"kind": "text", "value": "Copied"}}) is None
    for frame in target.frames:
        frame.evaluate.assert_called_once_with(copier.JS_CHECK_TEMPERATURE)
    target.evaluate.assert_not_called()
    target.wait_for_selector.assert_not_called()


def test_initial_form_controls_finish_loading_before_injection(monkeypatch):
    monkeypatch.setattr(copier, "_save_target_note", lambda page: None)
    monkeypatch.setattr(copier, "go_to_next_page", lambda *args: False)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        target = browser.new_page()
        target.set_content("<input><input><input><input><input><input><input>")
        target.evaluate("""() => setTimeout(() => {
            document.body.innerHTML = '<button class="activeNP">1</button>'
                + Array.from({length: 100}, (_, i) => `<input name="field${i}">`).join('')
                + '<input name="needed">';
        }, 1200)""")
        copier.inject_all_pages_target(target, {"needed": {"kind": "text", "value": "Copied value"}})
        assert target.locator('[name="needed"]').input_value() == "Copied value"
        browser.close()
