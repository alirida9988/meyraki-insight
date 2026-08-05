"""HTML → PDF via Playwright driving the system Chrome (docs/03, reports row)."""

from playwright.sync_api import sync_playwright


def html_to_pdf(html: str) -> bytes:
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome", headless=True)
        except Exception:
            browser = p.chromium.launch(headless=True)  # bundled chromium fallback
        try:
            page = browser.new_page()
            page.set_content(html, wait_until="networkidle")  # let web fonts settle
            return page.pdf(
                format="A4",
                print_background=True,
                margin={"top": "0", "bottom": "0", "left": "0", "right": "0"},
            )
        finally:
            browser.close()
