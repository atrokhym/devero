import sys
import os
from playwright.sync_api import sync_playwright

# Import JS_CHECK_TEMPERATURE from devero_copier
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__) + '/..'))
from devero_copier import JS_CHECK_TEMPERATURE

def test_temperature_check_empty_and_filled():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        # Test Case 1: Target document with empty temperature field
        html_empty = """
        <!DOCTYPE html>
        <html>
        <body>
            <input type="text" class="text-field background-color" name="cf_Temp_Txt" autocomplete="on" placeholder="">
        </body>
        </html>
        """
        page.set_content(html_empty)
        val = page.evaluate(JS_CHECK_TEMPERATURE)
        assert val is None, f"Expected None for empty temperature field, got '{val}'"
        print("✔ Test Case 1 Passed: Empty temperature field returns None (proceed with task)")

        # Test Case 2: Target document with filled temperature field
        html_filled = """
        <!DOCTYPE html>
        <html>
        <body>
            <input type="text" class="text-field background-color" name="cf_Temp_Txt" autocomplete="on" placeholder="" value="98.6">
        </body>
        </html>
        """
        page.set_content(html_filled)
        val = page.evaluate(JS_CHECK_TEMPERATURE)
        assert val == "98.6", f"Expected '98.6' for filled temperature field, got '{val}'"
        print("✔ Test Case 2 Passed: Filled temperature field returns '98.6' (cancel task)")

        # Test Case 3: Temperature field inside iframe with value
        html_iframe = """
        <!DOCTYPE html>
        <html>
        <body>
            <iframe id="f1" srcdoc='<input type="text" name="cf_Temp_Txt" value="97.7">'></iframe>
        </body>
        </html>
        """
        page.set_content(html_iframe)
        page.wait_for_timeout(500)
        val = page.evaluate(JS_CHECK_TEMPERATURE)
        assert val == "97.7", f"Expected '97.7' for iframe temperature field, got '{val}'"
        print("✔ Test Case 3 Passed: Temperature field inside iframe detected (cancel task)")

        browser.close()

if __name__ == "__main__":
    test_temperature_check_empty_and_filled()
    print("All temperature cancellation unit tests passed!")
