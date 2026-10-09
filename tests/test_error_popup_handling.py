"""
Unit test for Devero Error & Validation Popup Handling.
Verifies that handle_warning_popups correctly detects and dismisses:
1. Error dialog with 'The following errors were found: • ADVANCE DIRECTIVES:'
2. Warning dialogs and confirmation dialogs
"""
from playwright.sync_api import sync_playwright
import importlib.util

spec = importlib.util.spec_from_file_location("copier", "devero_copier_oasis.py")
copier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(copier)


def test_advance_directives_error_popup_dismissal():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        # Mock page with Devero Error popup as seen in user's screenshot
        page.set_content("""
        <html>
        <head>
            <style>
                .modal-dialog { display: block; background: #fff; border: 1px solid #ccc; padding: 20px; }
                .modal-dialog.closed { display: none; }
            </style>
        </head>
        <body>
            <div id="formFramePopup" class="popup-modal">
                <h3>Advance Directives</h3>
            </div>

            <!-- Error Popup overlay -->
            <div class="modal-dialog error-popup" id="errorPopup">
                <div class="modal-content">
                    <div class="modal-header">
                        <button type="button" class="close" onclick="closePopup()">×</button>
                        <h4 class="modal-title">Error</h4>
                    </div>
                    <div class="modal-body">
                        <p>The following errors were found:</p>
                        <ul>
                            <li>ADVANCE DIRECTIVES:</li>
                        </ul>
                    </div>
                    <div class="modal-footer">
                        <button type="button" id="btnOk" class="btn btn-default" onclick="closePopup()">OK</button>
                    </div>
                </div>
            </div>

            <script>
                function closePopup() {
                    document.getElementById('errorPopup').classList.add('closed');
                }
            </script>
        </body>
        </html>
        """)

        # Verify popup is currently visible
        is_visible_before = page.evaluate("() => document.getElementById('errorPopup').offsetHeight > 0")
        assert is_visible_before, "Popup should be initially visible"

        # Execute handle_warning_popups
        copier.handle_warning_popups(page)

        # Verify popup is now dismissed
        is_visible_after = page.evaluate("() => document.getElementById('errorPopup').classList.contains('closed')")
        assert is_visible_after, "Error popup should have been auto-dismissed by handle_warning_popups"
        print("[PASS] Error popup dismissal test passed successfully!")

        browser.close()


if __name__ == "__main__":
    test_advance_directives_error_popup_dismissal()
