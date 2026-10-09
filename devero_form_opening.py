"""Wait for DeVero's asynchronously opened and rendered form tabs."""

import json
import re
import time


FORM_SELECTOR = 'input, textarea, select, cmwf-checkbox, cmwf-textbox, cmwf-textarea, cmwf-text-display, cmwf-cop, cmwf-integrated-action, cmwf-dropdown, ntst-select, [data-field-id], [ng-model]'


def iter_chart_episodes(page):
    """Reveal each episode's clinical notes, yielding before opening the next."""
    panels = page.locator('.episode.epsd-pnl, .episode')
    for index in range(panels.count()):
        panel = panels.nth(index)
        if not panel.is_visible():
            # Historical episodes are hidden by their containing chart header.
            chart_header = panel.locator('..').locator(':scope > .chart-header:visible').first
            if chart_header.count() and not chart_header.evaluate(
                "el => el.classList.contains('open')"
            ):
                chart_header.click()
                page.wait_for_timeout(2500)
        if not panel.is_visible():
            continue
        header = panel.locator(
            '.episode-header:visible, div[ng-click*="toggleEpisode"]:visible'
        ).first
        if not header.count():
            continue
        if not header.evaluate(
            "el => el.classList.contains('open') || el.getAttribute('aria-expanded') === 'true'"
        ):
            header.click()
            page.wait_for_timeout(2500)

        notes = panel.locator(
            '[ng-click]:visible, [data-toggle="collapse"]:visible, '
            '.accordion-toggle:visible, .category-header:visible, button:visible, a:visible'
        ).filter(has_text=re.compile(r'^\s*(?:Nurses\s+)?Clinical Notes\s*$')).first
        if notes.count():
            needs_open = notes.evaluate("""el => {
                const expanded = el.getAttribute('aria-expanded');
                if (expanded !== null) return expanded === 'false';
                if (el.classList.contains('collapsed')) return true;
                if (el.classList.contains('open')) return false;
                if (el.classList.contains('category-header')) return true;
                return null;
            }""")
            if needs_open or (needs_open is None and not panel.locator('table:visible').count()):
                notes.click()
                page.wait_for_timeout(2500)
        yield index


def wait_for_form_inputs(page, min_inputs=15, timeout_sec=15):
    def count_inputs():
        total = 0
        for frame in page.frames:
            try:
                total += frame.evaluate(f"() => document.querySelectorAll({json.dumps(FORM_SELECTOR)}).length")
            except Exception:
                continue
        return total

    deadline = time.monotonic() + timeout_sec
    while True:
        count = count_inputs()
        if count >= min_inputs or time.monotonic() >= deadline:
            return count
        time.sleep(0.5)


def wait_for_opened_form_tab(context, chart_page, known_pages, chart_url, timeout_sec=20):
    deadline = time.monotonic() + timeout_sec
    while True:
        for tab in context.pages:
            if tab not in known_pages and not tab.is_closed() and tab.url != "about:blank":
                return tab
        if chart_page.url != chart_url:
            return chart_page
        if time.monotonic() >= deadline:
            return None
        chart_page.wait_for_timeout(500)
