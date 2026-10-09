import importlib.util
from playwright.sync_api import sync_playwright

spec = importlib.util.spec_from_file_location("copier", "devero_copier_oasis.py")
copier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(copier)

JS_INJECT_FIELDS = copier.JS_INJECT_FIELDS
lines = JS_INJECT_FIELDS.split('\n')

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()

    for idx in range(len(lines)):
        test_lines = lines[:idx] + lines[idx+1:]
        test_code = "\n".join(test_lines)
        res = page.evaluate("""(code) => {
            try {
                new Function('fieldMap', code);
                return "OK";
            } catch(e) {
                return e.toString();
            }
        }""", test_code)
        if "Invalid regular expression" not in res:
            print(f"--> LINE {idx+1} IS THE CAUSE! Content: {lines[idx]}")
            print(f"     Error became: {res}")
            break
