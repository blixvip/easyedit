"""Browser regression checks against a running easyedit server.

Run: python -m tests.check_home [http://127.0.0.1:4331]
Requires the optional Playwright Python package and installed Chrome.
Only project API boundaries are simulated; no user projects are changed.
"""
import sys

from playwright.sync_api import sync_playwright


def check(url="http://127.0.0.1:4331"):
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.route("**/api/jobs", lambda route: route.fulfill(json={"jobs": []}))
        page.goto(url, wait_until="networkidle")
        assert page.locator("#empty").is_visible()
        assert page.evaluate("fmt(59.6)") == "1:00"
        assert page.evaluate("fmt(Infinity)") == "0:00"

        for width in (320, 390, 768, 1440):
            page.set_viewport_size({"width": width, "height": 900})
            for tab in ("projects", "footage", "setup", "bot", "poster"):
                page.evaluate("tab => {location.hash = tab; showPage();}", tab)
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (width, tab)

        page.evaluate("location.hash = 'projects'; showPage();")
        page.route("**/api/new", lambda route: route.fulfill(status=400, json={"error": "Try another source"}))
        page.locator("#movie").fill("Keep my title")
        page.locator("#go").click()
        page.wait_for_function("document.getElementById('createStatus').textContent === 'Try another source'")
        assert page.locator("#movie").input_value() == "Keep my title"
        assert page.locator("#go").is_enabled()

        page.unroute("**/api/new")
        page.route("**/api/new", lambda route: route.fulfill(status=409, json={
            "error": "That project already exists.", "exists": True, "slug": "existing-project"}))
        page.locator("#go").click()
        page.wait_for_function("!!document.querySelector('#createStatus a')")
        assert page.locator("#createStatus a").get_attribute("href") == "/editor/existing-project"

        page.unroute("**/api/new")
        page.route("**/api/new", lambda route: route.fulfill(json={"started": True, "slug": "test-project"}))
        page.locator("#go").click()
        page.wait_for_function("document.getElementById('movie').value === ''")
        assert "Project started" in page.locator("#createStatus").inner_text()

        page.unroute("**/api/jobs")
        page.route("**/api/jobs", lambda route: route.abort())
        page.evaluate("refresh()")
        assert "Connection lost" in page.locator("#connection").inner_text()
        page.unroute("**/api/jobs")
        page.route("**/api/jobs", lambda route: route.fulfill(json={"jobs": []}))
        page.evaluate("refresh()")
        assert page.locator("#connection").is_hidden()
        assert not errors, errors
        browser.close()
    print("Home browser checks passed: responsive tabs, empty state, create/retry, duplicate, reconnect, time formatting.")


if __name__ == "__main__":
    check(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:4331")
