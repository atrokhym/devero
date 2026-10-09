from unittest.mock import Mock, patch

import pytest

from writer.fix_oasis_summary import (
    _assert_persisted_summary, _reopen_saved_target, _visible_summary_locator, _wait_for_summary_save,
    _verify_loaded_summary,
)
from writer.oasis_summary import OASISSummaryError


def test_saved_summary_reopens_from_chart_without_reloading_form():
    chart = Mock()
    chart.is_closed.return_value = False
    saved = Mock()
    saved.is_closed.return_value = False
    context = Mock()
    opened = object()
    package = {"task": {}}

    with patch("writer.fix_oasis_summary._open_exact_pending_target", return_value=opened) as open_target:
        assert _reopen_saved_target(context, chart, saved, package) is opened

    saved.close.assert_called_once_with()
    open_target.assert_called_once_with(context, chart, package)
    chart.reload.assert_not_called()


def test_saved_summary_reopens_with_new_chart_if_original_closed():
    chart = Mock()
    chart.is_closed.return_value = True
    saved = Mock()
    saved.is_closed.return_value = True
    context = Mock()
    package = {"task": {}}

    with patch("writer.fix_oasis_summary._open_exact_pending_target") as open_target:
        _reopen_saved_target(context, chart, saved, package)

    saved.close.assert_not_called()
    context.new_page.assert_called_once_with()
    open_target.assert_called_once_with(context, context.new_page.return_value, package)


def test_save_timeout_leaves_note_open_for_review():
    page = Mock()
    page.is_closed.return_value = False
    page.wait_for_load_state.side_effect = RuntimeError("Save still pending")
    with pytest.raises(OASISSummaryError, match="note was left open"):
        _wait_for_summary_save(page)
    page.close.assert_not_called()


def test_save_wait_accepts_note_closed_by_devero():
    page = Mock()
    page.is_closed.return_value = True
    _wait_for_summary_save(page)
    page.wait_for_load_state.assert_not_called()


def test_summary_native_entry_and_async_save_with_hidden_duplicate():
    import json
    import time
    from playwright.sync_api import sync_playwright
    from writer.fix_visit_note import _set_field

    text = "Interventions/Education Provided:\n\nSN assessed mobility and reinforced safe walker use."
    persisted = {"summary": ""}
    html = """<textarea name="sum_AssessmentSum_Txt" style="display:none"></textarea>
    <cmwf-textarea id="sum_AssessmentSum_Txt">
      <textarea name="sum_AssessmentSum_Txt" id="editor"></textarea>
    </cmwf-textarea><button id="save">Save</button>
    <script>
    const editor = document.querySelector('#editor');
    let model = '';
    editor.addEventListener('input', () => { model = editor.value; });
    document.querySelector('#save').onclick = async () => {
      await fetch('/save', {method:'POST', body:JSON.stringify({summary:model})});
    };
    </script>"""

    def route(request_route):
        if request_route.request.url.endswith("/save"):
            time.sleep(1)
            persisted.update(json.loads(request_route.request.post_data))
            request_route.fulfill(status=200, body="ok")
        else:
            page_html = html + "<script>setTimeout(() => {editor.value = " + json.dumps(persisted["summary"]) + ";}, 1200);</script>"
            request_route.fulfill(status=200, content_type="text/html", body=page_html)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.route("**/*", route)
            page.goto("http://summary.test/note")
            page.wait_for_timeout(1300)
            _set_field(page, "sum_AssessmentSum_Txt", text)
            assert _visible_summary_locator(page).input_value() == text
            assert page.locator('textarea[name="sum_AssessmentSum_Txt"]').first.input_value() == ""
            page.locator('#save').click()
            _wait_for_summary_save(page)
            page.reload()
            assert _visible_summary_locator(page).input_value() == ""
            _verify_loaded_summary(page, text, target_page_num=6)
        finally:
            browser.close()


def test_loaded_summary_waits_for_two_matching_reads(monkeypatch):
    from writer import fix_oasis_summary as summary
    page = Mock()
    control = Mock()
    control.input_value.side_effect = ["", "expected", "", "expected", "expected"]
    monkeypatch.setattr(summary, "_navigate_to_summary", lambda *_, **__: control)
    monkeypatch.setattr(summary, "_visible_summary_locator", lambda _: control)
    _verify_loaded_summary(page, "expected", 6)
    assert control.input_value.call_count == 5


@pytest.mark.parametrize("value", ["", "different saved narrative"])
def test_loaded_summary_still_rejects_persistent_wrong_value(monkeypatch, value):
    from writer import fix_oasis_summary as summary
    page = Mock()
    control = Mock()
    control.input_value.return_value = value
    monkeypatch.setattr(summary, "_navigate_to_summary", lambda *_, **__: control)
    monkeypatch.setattr(summary, "_visible_summary_locator", lambda _: control)
    clock = iter([0, 0, 21])
    monkeypatch.setattr(summary.time, "monotonic", lambda: next(clock))
    with pytest.raises(OASISSummaryError, match="Post-save verification failed"):
        _verify_loaded_summary(page, "expected", 6)


@pytest.mark.parametrize("current,filled", [("", True), ("Clear", True), ("09/20/2026", False)])
def test_oasis_last_bm_defaults_only_missing_value(monkeypatch, current, filled):
    from writer import fix_oasis_summary as summary
    from writer import fix_visit_note as legacy
    page = Mock()
    control = Mock()
    control.input_value.return_value = current
    navigate = Mock()
    setter = Mock()
    monkeypatch.setattr(legacy, "_navigate_to_page", navigate)
    monkeypatch.setattr(legacy, "_visible_field", lambda *_, **__: control)
    monkeypatch.setattr(legacy, "_set_field", setter)
    package = {"last_bm_page": 4, "task": {"target_date": "9/23/2026"}}
    result = summary._fill_missing_last_bm(page, package)
    navigate.assert_called_once_with(page, 4, required_field="lbd_LastBm_Chk")
    if filled:
        assert result == (4, "09/23/2026")
        setter.assert_called_once_with(page, "lbd_LastBm_Chk", "09/23/2026")
    else:
        assert result is None
        setter.assert_not_called()


def test_oasis_last_bm_page_is_read_from_original_audit_for_existing_packages(tmp_path, monkeypatch):
    import json
    from writer import fix_oasis_summary as summary
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "audit.json").write_text(json.dumps({"target_pages": [
        {"page_num": 4, "main": {"fields": [{"name": "lbd_LastBm_Chk", "val": ""}]}}
    ]}))
    monkeypatch.setattr(summary, "WORKSPACE_ROOT", str(tmp_path))
    assert summary._last_bm_page({"audit_file": "audit.json"}) == 4
    assert summary._last_bm_page({"last_bm_page": None}) is None


def test_oasis_last_bm_verification_waits_for_loaded_date(monkeypatch):
    from writer import fix_oasis_summary as summary
    from writer import fix_visit_note as legacy
    page = Mock()
    control = Mock()
    control.input_value.side_effect = ["", "9/23/2026", "09/23/2026"]
    monkeypatch.setattr(legacy, "_navigate_to_page", Mock())
    monkeypatch.setattr(legacy, "_visible_field", lambda *_, **__: control)
    summary._verify_saved_last_bm(page, (4, "09/23/2026"))
    assert control.input_value.call_count == 3


@pytest.mark.parametrize("ignored_click", [True, False])
def test_shared_navigation_retries_and_waits_for_late_pager(ignored_click):
    from playwright.sync_api import sync_playwright
    from writer.fix_visit_note import _navigate_to_page
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_content("""<div id="pager"></div><div id="form"></div>
            <script>
            window.clicks = 0;
            </script>""")
            page.evaluate("""(ignoreFirst) => {
                setTimeout(() => {
                    const button = document.createElement('button');
                    button.textContent = '3';
                    button.onclick = () => {
                        window.clicks++;
                        if (ignoreFirst && window.clicks === 1) return;
                        button.className = 'active loading';
                        setTimeout(() => {
                            document.querySelector('#form').innerHTML =
                                '<input name="lbd_LastBm_Chk">';
                            button.className = 'active';
                        }, 300);
                    };
                    document.querySelector('#pager').append(button);
                }, 200);
            }""", ignored_click)
            _navigate_to_page(page, 3, timeout_sec=1, required_field="lbd_LastBm_Chk")
            assert page.evaluate("window.clicks") == (2 if ignored_click else 1)
            assert page.locator('input[name="lbd_LastBm_Chk"]').is_visible()
        finally:
            browser.close()


def test_shared_navigation_still_blocks_when_page_never_activates():
    from playwright.sync_api import sync_playwright
    from writer.fix_visit_note import _navigate_to_page, VisitNoteError
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_content('<button>3</button><input name="lbd_LastBm_Chk" value="existing">')
            with pytest.raises(VisitNoteError, match="after 3 attempts"):
                _navigate_to_page(page, 3, timeout_sec=0.2, required_field="lbd_LastBm_Chk")
            assert page.locator('input[name="lbd_LastBm_Chk"]').input_value() == "existing"
        finally:
            browser.close()


def test_writers_click_only_exact_save_and_refuse_send_actions():
    from playwright.sync_api import sync_playwright
    from writer import fix_visit_note as legacy, fix_oasis_summary as summary
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_content('<button>Save and Send</button><button>Sign</button><button>Save</button>')
            for writer_module in (legacy, summary):
                assert writer_module._find_exact_save_button(page).inner_text() == "Save"
            page.set_content('<button>Save and Send</button><button>Sign</button>')
            for writer_module in (legacy, summary):
                with pytest.raises(ValueError, match="exactly Save"):
                    writer_module._find_exact_save_button(page)
        finally:
            browser.close()
