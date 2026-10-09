"""Offline regression checks for note closure and failed Save attempts."""

from unittest.mock import Mock

import pytest
from playwright.sync_api import Error, sync_playwright

import devero_copier_note as copier


@pytest.mark.parametrize("close_at", ["click", "wait", "popup"])
def test_note_closure_after_save_is_tolerated(monkeypatch, close_at):
    page = Mock()
    page.is_closed.return_value = False
    page.context.browser.is_connected.return_value = True
    chart = Mock()
    chart.is_closed.return_value = False
    page.context.pages = [chart, page]
    button = page.evaluate_handle.return_value.as_element.return_value

    def close(*args, **kwargs):
        page.is_closed.return_value = True
        raise Error("Target page, context or browser has been closed")

    popup = Mock()
    monkeypatch.setattr(copier, "handle_warning_popups", popup)
    {"click": button.click, "wait": page.wait_for_timeout, "popup": popup}[close_at].side_effect = close
    copier._save_target_note(page)
    button.click.assert_called_once()
    if close_at != "popup":
        popup.assert_not_called()


@pytest.mark.parametrize("failure", ["browser", "context", "click", "before_click", "missing"])
def test_save_failure_is_not_reported_as_success(monkeypatch, failure):
    page = Mock()
    page.is_closed.return_value = False
    page.context.browser.is_connected.return_value = True
    chart = Mock()
    chart.is_closed.return_value = False
    page.context.pages = [chart, page]
    button = page.evaluate_handle.return_value.as_element.return_value
    monkeypatch.setattr(copier, "handle_warning_popups", Mock())

    def fail(*args, **kwargs):
        if failure in ("browser", "context", "before_click"):
            page.is_closed.return_value = True
        if failure == "browser":
            page.context.browser.is_connected.return_value = False
        if failure == "context":
            page.context.pages = []
        raise Error("Target page, context or browser has been closed")

    if failure == "missing":
        page.evaluate_handle.return_value.as_element.return_value = None
    elif failure == "before_click":
        button.scroll_into_view_if_needed.side_effect = fail
    else:
        button.click.side_effect = fail
    with pytest.raises((RuntimeError, Error)):
        copier._save_target_note(page)
    assert button.click.call_count <= 1  # Never retry an ambiguous Save.
    page.close.assert_not_called()


def test_native_save_prefers_visible_note_button_and_handles_real_tab_close(monkeypatch):
    monkeypatch.setattr(copier, "handle_warning_popups", Mock())
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        chart = context.new_page()
        # A window opened by script may close itself during Save.
        with chart.expect_popup() as opened:
            chart.evaluate("window.open('about:blank')")
        page = opened.value
        page.set_content("""
            <input type="button" value="Save" style="display:none" onclick="throw Error('hidden')">
            <input type="button" value="Save" style="visibility:hidden" onclick="throw Error('hidden')">
            <div class="modal-dialog"><input type="button" value="Save" onclick="throw Error('modal')"></div>
            <input type="button" value="Save" disabled>
            <input type="button" value="Save & Close" onclick="throw Error('wrong button')">
            <button onclick="if (event.isTrusted) window.close()">Save</button>
        """)
        copier._save_target_note(page)
        assert page.is_closed()
        assert not chart.is_closed()
        assert browser.is_connected()
        browser.close()


def test_injection_propagates_save_failure_before_returning_counts(monkeypatch):
    page = Mock()
    page.frames = []
    page.evaluate.return_value = {"updated": 1, "skipped": 0}
    monkeypatch.setattr(copier, "wait_for_form_inputs", lambda *args, **kwargs: 200)
    monkeypatch.setattr(copier, "wait_for_page_extraction_ready", lambda *args, **kwargs: {"ready": True})
    monkeypatch.setattr(copier, "go_to_next_page", lambda *args: False)
    monkeypatch.setattr(copier, "_save_target_note", Mock(side_effect=RuntimeError("Save failed")))
    with pytest.raises(RuntimeError, match="Save failed"):
        copier.inject_all_pages_target(page, {})
