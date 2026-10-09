from unittest.mock import patch

from devero_copier_note import open_note_by_ui_click, wait_for_form_inputs


class FormTab:
    url = "about:blank"

    def is_closed(self):
        return False


class ChartPage:
    url = "https://example.devero.com/app#/patientCharts"

    def __init__(self, row_caregiver):
        self.row_caregiver = row_caregiver
        self.attempts = []
        self.form_tab = FormTab()
        self.polls = 0

    def wait_for_timeout(self, milliseconds):
        if milliseconds == 500:
            self.polls += 1
            if self.polls == 1:
                self.context.pages.append(self.form_tab)
            elif self.polls == 2:
                self.form_tab.url = "https://example.devero.com/app#/printForm"

    def evaluate(self, script, criteria=None):
        if criteria is None:
            return None
        self.attempts.append(criteria.copy())
        return criteria["caregiver"] in ("", self.row_caregiver)


class ChartContext:
    def __init__(self, page):
        self.pages = [page]
        page.context = self


def test_dated_source_prefers_caregiver_then_accepts_other():
    with patch("devero_copier_note.select_patient_and_go_to_chart"):
        for row_caregiver, expected_attempts in [("Nurse", 1), ("Other", 3)]:
            page = ChartPage(row_caregiver)
            result = open_note_by_ui_click(
                ChartContext(page), page, "https://example.devero.com",
                note_date="09/10/2026", caregiver="Nurse", is_target=False,
            )
            assert result is page.form_tab
            assert page.polls == 2
            assert len(page.attempts) == expected_attempts
            assert page.attempts[-1]["caregiver"] == ("" if row_caregiver == "Other" else "Nurse")

        page = ChartPage("Other")
        result = open_note_by_ui_click(
            ChartContext(page), page, "https://example.devero.com",
            note_date="09/14/2026", caregiver="Nurse", is_target=True,
        )
        assert result is None
        assert all(attempt["caregiver"] == "Nurse" for attempt in page.attempts)


def test_wait_for_form_inputs_sees_delayed_frame_controls():
    class Frame:
        def __init__(self, counts):
            self.counts = iter(counts)

        def evaluate(self, _script):
            return next(self.counts)

    page = type("Page", (), {"frames": [Frame([0, 0]), Frame([0, 18])]})()
    with patch("devero_form_opening.time.sleep"):
        assert wait_for_form_inputs(page, min_inputs=15, timeout_sec=1) == 18
