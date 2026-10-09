from unittest.mock import patch

from devero_copier_oasis import open_note_by_ui_click, wait_for_form_inputs


class FormTab:
    url = "about:blank"

    def __init__(self):
        self.frames = [self]
        self.controls = 0
        self.body_length = 0

    def evaluate(self, script):
        return self.body_length if "innerText" in script else self.controls

    def is_closed(self):
        return False


class ChartPage:
    url = "https://example.devero.com/app#/patientCharts"

    def __init__(self, context, form_tab, print_view=False):
        self.context = context
        self.form_tab = form_tab
        self.print_view = print_view
        self.polls = 0

    def evaluate(self, _script, criteria=None):
        if criteria is None:
            return 1
        return {"clicked": True, "text": "source row"}

    def wait_for_timeout(self, milliseconds):
        if milliseconds == 500:
            self.polls += 1
            if self.polls == 1:
                self.context.pages.append(self.form_tab)
            elif self.polls == 2:
                kind = "printForm" if self.print_view else "dynamicForm"
                self.form_tab.url = f"https://example.devero.com/app#/{kind}"
            elif self.polls == 3:
                if self.print_view:
                    self.form_tab.body_length = 200
                else:
                    self.form_tab.controls = 20


def test_open_note_waits_for_delayed_form_tab():
    context = type("Context", (), {"pages": []})()
    form_tab = FormTab()
    chart = ChartPage(context, form_tab)
    context.pages.append(chart)

    with patch("devero_copier_oasis.select_patient_and_go_to_chart"):
        opened = open_note_by_ui_click(
            context, chart, "https://example.devero.com",
            note_date="04/09/2026", is_target=False,
        )

    assert opened is form_tab
    assert chart.polls == 3


def test_form_input_count_uses_playwright_frames():
    class Frame:
        def __init__(self, count):
            self.count = count

        def evaluate(self, _script):
            return self.count

    page = type("Page", (), {"frames": [Frame(0), Frame(18)]})()
    assert wait_for_form_inputs(page, min_inputs=15, timeout_sec=0) == 18


def test_open_note_accepts_rendered_read_only_print_form():
    context = type("Context", (), {"pages": []})()
    form_tab = FormTab()
    chart = ChartPage(context, form_tab, print_view=True)
    context.pages.append(chart)

    with patch("devero_copier_oasis.select_patient_and_go_to_chart"):
        opened = open_note_by_ui_click(
            context, chart, "https://example.devero.com",
            note_date="04/09/2026", is_target=False,
        )

    assert opened is form_tab
    assert chart.polls == 3
    assert form_tab.controls == 0
