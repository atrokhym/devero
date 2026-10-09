from unittest.mock import patch

import pytest

import devero_copier
import devero_copier_note
import devero_copier_note_any
import devero_copier_note_completed
import devero_copier_oasis
from devero_form_opening import wait_for_form_inputs, wait_for_opened_form_tab


class FormTab:
    url = "about:blank"

    def is_closed(self):
        return False

    def evaluate(self, script):
        return 200 if "innerText" in script else 0


class ChartPage:
    url = "https://example.devero.com/app#/patientCharts"

    def __init__(self, row_status="completed", oasis=False, row_caregiver=None):
        self.context = type("Context", (), {"pages": [self]})()
        self.form_tab = FormTab()
        self.row_status = row_status
        self.oasis = oasis
        self.row_caregiver = row_caregiver
        self.polls = 0
        self.criteria = []

    def evaluate(self, _script, criteria=None):
        if criteria is None:
            return 1
        self.criteria.append(criteria)
        if self.oasis:
            allowed = criteria["requiredStatuses"]
            clicked = not allowed or self.row_status in allowed
            return {"clicked": clicked, "text": "matching row" if clicked else ""}
        if self.row_caregiver is not None:
            return criteria["caregiver"] in ("", self.row_caregiver)
        return True

    def wait_for_timeout(self, milliseconds):
        if milliseconds == 500:
            self.polls += 1
            if self.polls == 1:
                self.context.pages.append(self.form_tab)
            elif self.polls == 2:
                self.form_tab.url = "https://example.devero.com/app#/printForm"


@pytest.mark.parametrize("module", [
    devero_copier, devero_copier_note, devero_copier_note_any,
    devero_copier_note_completed,
])
def test_legacy_copiers_wait_for_delayed_completed_tab(module):
    chart = ChartPage()
    with patch.object(module, "select_patient_and_go_to_chart"):
        opened = module.open_note_by_ui_click(
            chart.context, chart, "https://example.devero.com",
            note_date="09/10/2026", is_target=False,
        )
    assert opened is chart.form_tab
    assert chart.polls == 2


def test_auto_detected_source_waits_for_delayed_tab():
    chart = ChartPage()
    candidate = {"rowIndex": 1, "dateStr": "09/10/2026", "tier": 1,
                 "formName": "Skilled Nurse Visit Note", "tier_label": "Sent To Office", "user": "Other"}
    with (patch.object(devero_copier_note, "select_patient_and_go_to_chart"),
          patch.object(devero_copier_note, "expand_all_chart_episodes"),
          patch.object(devero_copier_note, "extract_chart_rows", return_value=[{}]),
          patch.object(devero_copier_note, "select_source_candidate", return_value=candidate)):
        found = devero_copier_note.auto_detect_source_note(
            chart.context, chart, "https://example.devero.com", target_date="09/14/2026"
        )
    assert found["page"] is chart.form_tab


def test_completed_note_copier_accepts_different_source_caregiver():
    chart = ChartPage(row_caregiver="Other")
    with patch.object(devero_copier_note_completed, "select_patient_and_go_to_chart"):
        opened = devero_copier_note_completed.open_note_by_ui_click(
            chart.context, chart, "https://example.devero.com",
            note_date="09/10/2026", caregiver="Nurse", is_target=False,
        )
    assert opened is chart.form_tab
    assert len(chart.criteria) == 3
    assert chart.criteria[-1]["caregiver"] == ""


def test_form_controls_can_appear_in_delayed_frame():
    class Frame:
        def __init__(self, counts):
            self.counts = iter(counts)

        def evaluate(self, _script):
            return next(self.counts)

    page = type("Page", (), {"frames": [Frame([0, 0]), Frame([0, 18])]})()
    with patch("devero_form_opening.time.sleep"):
        assert wait_for_form_inputs(page, min_inputs=15, timeout_sec=1) == 18


def test_chart_page_is_not_mistaken_for_unopened_form():
    chart = ChartPage()
    assert wait_for_opened_form_tab(chart.context, chart, [chart], chart.url, timeout_sec=0) is None


def test_completed_target_accepts_print_form_and_excludes_pending_row():
    for status, expected in (("completed", True), ("pending", False)):
        chart = ChartPage(row_status=status, oasis=True)
        with (patch.object(devero_copier_oasis, "select_patient_and_go_to_chart"),
              patch.object(devero_copier_oasis, "iter_chart_episodes", return_value=iter(())),
              patch.object(devero_copier_oasis, "wait_for_form_inputs", return_value=0)):
            opened = devero_copier_oasis.open_note_by_ui_click(
                chart.context, chart, "https://example.devero.com",
                note_date="09/10/2026", is_target=True,
                required_status=("completed", "sent to office"),
            )
        assert (opened is chart.form_tab) is expected
        assert chart.criteria[0]["requiredStatuses"] == ("completed", "sent to office")
