"""Source notes must always be exactly Skilled Nurse Visit Note."""

from unittest.mock import Mock

import pytest
from playwright.sync_api import sync_playwright

import devero_copier_note as copier


@pytest.mark.parametrize("override", ["", "Other Note", "Skilled Nursing Visit Note"])
def test_auto_selection_requires_exact_source_type(override):
    rows = [
        {"formName": name, "rowIndex": index, "dateStr": "09/10/2026",
         "status": "Completed", "rowText": name}
        for index, name in enumerate([
            copier.SOURCE_DOC_TYPE, "Other Note", "Skilled Nursing Visit Note",
            "Skilled Nurse Visit Note Addendum",
        ])
    ]
    selected = copier.select_source_candidate(
        rows, "Other Target Note", "09/20/2026", source_doc_type=override,
    )
    assert selected["formName"] == copier.SOURCE_DOC_TYPE
    assert copier.select_source_candidate(rows[1:], "Other Target Note", "09/20/2026") is None


def test_dated_source_ignores_override_and_rejects_similar_types(monkeypatch):
    monkeypatch.setattr(copier, "select_patient_and_go_to_chart", lambda *args: None)
    monkeypatch.setattr(copier, "wait_for_opened_form_tab", lambda context, page, *args: page)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        monkeypatch.setattr(page, "wait_for_timeout", lambda *args: None)
        page.set_content("""
            <table>
              <tr><td>Other Note</td><td>09/10/2026</td><td>Nurse</td><td>Completed</td>
                <td><a href="#" onclick="window.selected='wrong'">Open</a></td></tr>
              <tr><td>Skilled Nurse Visit Note Addendum</td><td>09/10/2026</td><td>Nurse</td><td>Completed</td>
                <td><a href="#" onclick="window.selected='wrong'">Open</a></td></tr>
              <tr><td>Skilled Nurse Visit Note</td><td>09/10/2026</td><td>Nurse</td><td>Completed</td>
                <td><a href="#" onclick="window.selected='correct'">Open</a></td></tr>
            </table>
        """)
        assert copier.open_note_by_ui_click(
            page.context, page, "https://example.devero.com",
            note_date="09/10/2026", caregiver="Nurse", source_doc_type="Other Note",
        ) is page
        assert page.evaluate("window.selected") == "correct"
        page.locator("tr").last.evaluate("el => el.remove()")
        page.evaluate("window.selected = null")
        assert copier.open_note_by_ui_click(
            page.context, page, "https://example.devero.com",
            note_date="09/10/2026", caregiver="Nurse",
        ) is None
        assert page.evaluate("window.selected") is None
        browser.close()


def test_source_url_cannot_bypass_chart_type_check(monkeypatch):
    page = Mock()
    open_from_chart = Mock(return_value=page)
    monkeypatch.setattr(copier, "open_note_by_ui_click", open_from_chart)
    assert copier.navigate_to_note(
        Mock(), page, "https://example.devero.com",
        "https://example.devero.com/app#/dynamicForm?activityId=123", is_target=False,
    ) is page
    page.goto.assert_not_called()
    assert open_from_chart.call_args.args[5] == "123"
