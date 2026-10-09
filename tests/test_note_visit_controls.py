"""Copy distinct printed visit times and fill requested pedal-pulse defaults."""

import pytest
from playwright.sync_api import sync_playwright

import devero_copier_note as copier


def test_printed_visit_times_have_separate_keys_and_round_trip():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        source = browser.new_page()
        source.set_content("""
            <span><div><label>Time In:</label></div>
              <div class="timeDropdown"><span class="print-text">9:15 AM</span></div></span>
            <span><div><label>Time Out:</label></div>
              <div class="timeDropdown"><span class="print-text">10:00 AM</span></div></span>
            <span><label>Time In:</label><div class="timeDropdown"><input ng-model="outputModel.label"></div></span>
        """)
        fields = source.evaluate(copier.JS_EXTRACT_FIELDS)
        assert fields["TimeIn"]["value"] == "9:15 AM"
        assert fields["TimeOut"]["value"] == "10:00 AM"
        assert "outputModel.label" not in fields
        target = browser.new_page()
        target.set_content('<input name="TimeIn" type="time"><input name="TimeOut" type="time">')
        target.evaluate(copier.JS_INJECT_FIELDS, fields)
        assert target.locator('[name="TimeIn"]').input_value() == "09:15"
        assert target.locator('[name="TimeOut"]').input_value() == "10:00"
        browser.close()


@pytest.mark.parametrize("right", ["", "++"])
@pytest.mark.parametrize("layout", ["wrapped_hidden", "label_overlay", "plain"])
def test_pedal_pulses_use_native_checkmark_and_preserve_populated_values(right, layout):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content("""
            <cmwf-checkbox id="crd_PedalPulse_Chk"><label>Pedal pulses:
              <input hidden type="checkbox" name="crd_PedalPulse_Chk"
                onchange="window.pedalAccepted = this.checked">
            </label></cmwf-checkbox>
            <input name="crd_PulsesR_Txt">
            <input name="crd_PulsesL_Txt" onchange="window.leftAccepted = this.value">
        """)
        if layout != "wrapped_hidden":
            page.locator('cmwf-checkbox').evaluate("""(el, layout) => {
                el.innerHTML = `<input type="checkbox" id="crd_PedalPulse_Chk_key"
                    name="crd_PedalPulse_Chk" onchange="window.pedalAccepted = this.checked">`
                    + (layout === 'label_overlay' ? `<label class="checkbox_label color"
                        for="crd_PedalPulse_Chk_key">Pedal pulses:</label>` : '');
                if (layout === 'label_overlay') {
                    el.style.cssText = 'display:block;position:relative;width:140px;height:30px';
                    el.querySelector('input').style.cssText = 'position:absolute;left:0;top:0';
                    el.querySelector('label').style.cssText = 'position:absolute;inset:0;z-index:1';
                }
            }""", layout)
        if layout == "label_overlay":
            checkbox = page.locator('[name="crd_PedalPulse_Chk"]')
            assert checkbox.is_visible()
            assert checkbox.evaluate("""el => {
                const box = el.getBoundingClientRect();
                return document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2).tagName;
            }""") == "LABEL"
        page.locator('[name="crd_PulsesR_Txt"]').fill(right)
        assert copier._fill_pedal_pulses(page) == (2 if right else 3)
        assert page.locator('[name="crd_PedalPulse_Chk"]').is_checked()
        assert page.evaluate('window.pedalAccepted') is True
        assert page.locator('[name="crd_PulsesR_Txt"]').input_value() == (right or "+")
        assert page.locator('[name="crd_PulsesL_Txt"]').input_value() == "+"
        assert page.evaluate('window.leftAccepted') == "+"
        page.evaluate(copier.JS_INJECT_FIELDS, {
            "crd_PulsesL_Txt": {"kind": "text", "value": "-"},
            "crd_PedalPulse_Chk": {"kind": "checkbox", "checked": False, "value": "on"},
        })
        assert page.locator('[name="crd_PulsesL_Txt"]').input_value() == "+"
        assert copier._fill_pedal_pulses(page) == 0
        browser.close()


TIME_WIDGETS = """
<script>
window.committed = {};
function choose(anchor, name, event) {
    if (!event.isTrusted) throw Error('Trusted interaction required');
    const picker = anchor.closest('[time-picker]');
    picker.querySelector('input').value = anchor.innerText;
    picker.querySelector('ul').hidden = true;
    window.committed[name] = anchor.innerText;
}
function acceptInput(input, name, event) {
    if (!event.isTrusted) throw Error('Trusted input required');
    if (event.relatedTarget?.tagName === 'A') return;
    window.committed[name] = input.value;
    input.nextElementSibling.hidden = true;
}
</script>
""" + "".join(f"""
<span><label>Time {'In' if name == 'TimeIn' else 'Out'}:</label><div class="timeDropdown">
  <div time-picker output-model="dynamicFormController.timeObj['{name}']">
    <input ng-model="outputModel.label" onfocus="this.nextElementSibling.hidden=false"
      oninput="acceptInput(this, '{name}', event)"
      onblur="acceptInput(this, '{name}', event)">
    <ul hidden>
      <li class="list-of-options"><a href="#" onclick="choose(this, '{name}', event);return false">9:15 AM</a></li>
      <li class="list-of-options"><a href="#" onclick="choose(this, '{name}', event);return false">10:00 AM</a></li>
      <li class="list-of-options"><a href="#" onclick="choose(this, '{name}', event);return false">12:00 AM</a></li>
      <li class="list-of-options"><a href="#" onclick="choose(this, '{name}', event);return false">12:00 PM</a></li>
    </ul>
  </div>
</div></span>
""" for name in ("TimeIn", "TimeOut"))


@pytest.mark.parametrize("source_in,source_out,expected_in,expected_out", [
    ("09:15 AM", "10:00 AM", "9:15 AM", "10:00 AM"),
    ("09:17", "10:03", "9:17 AM", "10:03 AM"),
    ("00:00", "12:00", "12:00 AM", "12:00 PM"),
])
def test_visit_time_selection_commits_distinct_values(source_in, source_out, expected_in, expected_out):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(TIME_WIDGETS)
        fields = {"TimeIn": {"kind": "text", "value": source_in},
                  "TimeOut": {"kind": "text", "value": source_out},
                  "outputModel.label": {"kind": "text", "value": "wrong shared value"}}
        assert copier._copy_visit_times(page, fields) == 2
        page.evaluate(copier.JS_INJECT_FIELDS, fields)
        assert page.evaluate('window.committed') == {"TimeIn": expected_in, "TimeOut": expected_out}
        assert copier._copy_visit_times(page, fields) == 0  # Preserve existing target times.
        browser.close()


def test_missing_pedal_controls_and_invalid_source_time_stop_before_save():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(TIME_WIDGETS)
        with pytest.raises(RuntimeError, match="invalid format"):
            copier._copy_visit_times(page, {"TimeIn": {"value": "25:00"}})
        with pytest.raises(RuntimeError, match="Pedal-pulse controls were not found"):
            copier._fill_pedal_pulses(page)
        page.set_content('<input name="unrelated">')
        with pytest.raises(RuntimeError, match="Target TimeIn control was not found"):
            copier._copy_visit_times(page, {"TimeIn": {"value": "9:15 AM"}})
        browser.close()
