"""Episode navigation must reveal clinical notes once and search every episode."""

import pytest
from playwright.sync_api import sync_playwright

import devero_copier_oasis as copier
from devero_form_opening import iter_chart_episodes
from verifier import verify_oasis as verifier


CHART_HTML = """
<script>
window.clicks = [0, 0, 0]; window.noteClicks = [0, 0, 0];
function toggleEpisode(index) {
    window.clicks[index]++;
    document.querySelectorAll('.episode').forEach((panel, i) => {
        const open = i === index && !panel.querySelector('.episode-header').classList.contains('open');
        panel.querySelector('.episode-header').classList.toggle('open', open);
        panel.querySelector('.content').style.display = open ? 'block' : 'none';
    });
}
function showNotes(index, button) {
    window.noteClicks[index]++;
    button.setAttribute('aria-expanded', 'true');
    setTimeout(() => {
        button.nextElementSibling.innerHTML = `<table><tr>
            <td>Skilled Nurse Visit Note</td><td>${['09/01/2026', '09/15/2026', '09/10/2026'][index]}</td>
            <td>Example Nurse</td><td>${index === 1 && window.pending ? 'Pending' : 'Completed'}</td>
            <td><a href="#opened" onclick="window.selected=${index}">Open</a></td>
        </tr></table>`;
    }, 30);
}
</script>
<div class="episode epsd-pnl">
  <div class="episode-header open" onclick="toggleEpisode(0)"><span>Episode #3</span></div>
  <div class="content">
    <button aria-expanded="false" onclick="showNotes(0, this)"><span>Clinical Notes</span></button><div></div>
  </div>
</div>
<div class="episode epsd-pnl">
  <div class="episode-header" onclick="toggleEpisode(1)"><span>Episode #2</span></div>
  <div class="content" style="display:none">
    <table><tr><td>Unrelated section</td></tr></table>
    <button aria-expanded="false" onclick="showNotes(1, this)"><span>Nurses Clinical Notes</span></button><div></div>
  </div>
</div>
<div class="episode epsd-pnl">
  <div class="episode-header" onclick="toggleEpisode(2)"><span>Episode #1</span></div>
  <div class="content" style="display:none">
    <button aria-expanded="false" onclick="showNotes(2, this)"><span>Clinical Notes</span></button><div></div>
  </div>
</div>
"""


def test_episode_iterator_opens_headers_and_nested_notes_once(monkeypatch):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(CHART_HTML)
        wait = page.wait_for_timeout
        monkeypatch.setattr(page, "wait_for_timeout", lambda milliseconds: wait(80))
        visited = []
        for index in iter_chart_episodes(page):
            visited.append(index)
            assert page.locator('.episode').nth(index).locator('a').is_visible()
        assert visited == [0, 1, 2]
        assert page.evaluate('window.clicks') == [0, 1, 1]
        assert page.evaluate('window.noteClicks') == [1, 1, 1]
        browser.close()


@pytest.mark.parametrize("caregiver,pending,found", [
    ("Example Nurse", True, True),
    ("Other Nurse", True, False),
    ("Example Nurse", False, False),
])
def test_verifier_target_in_other_episode_keeps_strict_filters(monkeypatch, capsys, caregiver, pending, found):
    monkeypatch.setattr(copier, "select_patient_and_go_to_chart", lambda *args: None)
    monkeypatch.setattr(copier, "wait_for_form_inputs", lambda *args, **kwargs: 20)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(CHART_HTML)
        page.evaluate('pending => window.pending = pending', pending)
        wait = page.wait_for_timeout
        monkeypatch.setattr(page, "wait_for_timeout", lambda milliseconds: wait(80))
        opened = verifier.open_note_by_ui_click(
            page.context, page, "https://example.devero.com",
            doc_type="Skilled Nurse Visit Note", note_date="09/15/2026",
            caregiver=caregiver, is_target=True,
        )
        assert opened is (page if found else None)
        assert page.evaluate('window.selected') == (1 if found else None)
        assert page.evaluate('window.clicks') == ([0, 1, 0] if found else [0, 1, 1])
        if caregiver == "Example Nurse" and not pending:
            output = capsys.readouterr().out
            assert "row exists with status COMPLETED; required status: PENDING" in output
        browser.close()


def test_baseline_discovery_picks_newest_across_episodes(monkeypatch):
    monkeypatch.setattr(verifier, "select_patient_and_go_to_chart", lambda *args: None)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(CHART_HTML)
        wait = page.wait_for_timeout
        monkeypatch.setattr(page, "wait_for_timeout", lambda milliseconds: wait(80))
        assert verifier.discover_source_note_date(
            page, "Example Patient", "Skilled Nurse Visit Note", "09/20/2026",
        ) == "09/15/2026"
        assert page.evaluate('window.noteClicks') == [1, 1, 1]
        browser.close()


def test_episode_iterator_skips_hidden_panels_and_duplicate_controls(monkeypatch):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_default_timeout(500)
        page.set_content("""
            <script>window.clicks = {header: 0, notes: 0, hidden: 0};</script>
            <div class="episode">
              <div class="episode-header open">Episode #4</div>
              <button hidden onclick="window.clicks.hidden++">Clinical Notes</button>
              <button aria-expanded="false" onclick="window.clicks.notes++;
                this.setAttribute('aria-expanded', 'true')">Clinical Notes</button>
            </div>
            <div class="episode" hidden>
              <div class="episode-header" onclick="window.clicks.hidden++">Episode #3</div>
            </div>
            <div class="episode">
              <div class="episode-header" hidden onclick="window.clicks.hidden++">Episode #2</div>
              <span>Hidden header's placeholder</span>
            </div>
            <div class="episode">
              <div class="episode-header" hidden onclick="window.clicks.hidden++">Episode #1 copy</div>
              <div class="episode-header" onclick="window.clicks.header++;
                this.classList.add('open'); this.nextElementSibling.hidden=false">Episode #1</div>
              <div hidden>
                <button aria-expanded="false" onclick="window.clicks.notes++;
                  this.setAttribute('aria-expanded', 'true')">Nurses Clinical Notes</button>
              </div>
            </div>
        """)
        monkeypatch.setattr(page, "wait_for_timeout", lambda milliseconds: None)
        assert list(iter_chart_episodes(page)) == [0, 3]
        assert page.evaluate('window.clicks') == {"header": 1, "notes": 2, "hidden": 0}
        browser.close()


def test_closed_chart_headers_reveal_historical_episodes(monkeypatch):
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(CHART_HTML)
        page.evaluate("""() => {
            window.chartClicks = 0;
            const panels = [...document.querySelectorAll('.episode')];
            for (const indexes of [[0], [1, 2]]) {
                const chart = document.createElement('div');
                chart.className = 'chart';
                const header = document.createElement('div');
                header.className = 'chart-header' + (indexes[0] === 0 ? ' open' : '');
                header.textContent = 'Chart';
                header.onclick = () => { window.chartClicks++; header.classList.add('open'); };
                chart.append(header);
                document.body.append(chart);
                indexes.forEach(i => chart.append(panels[i]));
            }
            const style = document.createElement('style');
            style.textContent = '.chart-header ~ .episode {display:none}'
                + '.chart-header.open ~ .episode {display:block}';
            document.head.append(style);
            document.querySelectorAll('button').forEach(button => {
                const header = document.createElement('div');
                header.className = 'category-header';
                header.textContent = '  Nurses Clinical Notes  ';
                header.onclick = () => {
                    header.classList.add('open');
                    showNotes([...document.querySelectorAll('.episode')].indexOf(header.closest('.episode')), header);
                };
                button.replaceWith(header);
            });
        }""")
        wait = page.wait_for_timeout
        monkeypatch.setattr(page, "wait_for_timeout", lambda milliseconds: wait(80))
        assert list(iter_chart_episodes(page)) == [0, 1, 2]
        assert page.evaluate('window.chartClicks') == 1
        assert page.evaluate('window.clicks') == [0, 1, 1]
        assert page.evaluate('window.noteClicks') == [1, 1, 1]
        browser.close()
