#!/usr/bin/env python3
"""Render xauby_book.html to a paginated PDF via Chromium print-to-pdf."""
import os
from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
HTML_PATH = os.path.join(HERE, "xauby_book.html")
PDF_PATH = os.path.join(HERE, "xAuby_Book_TH.pdf")
CHROME = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"

FOOTER = """
<div style="font-family:'Noto Sans Thai',sans-serif; font-size:8px; width:100%;
            color:#888; padding:0 16mm; display:flex; justify-content:space-between;">
  <span>xAuby — คู่มือฉบับสมบูรณ์</span>
  <span><span class="pageNumber"></span> / <span class="totalPages"></span></span>
</div>
"""

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROME)
        page = browser.new_page()
        page.goto(f"file://{HTML_PATH}")
        page.wait_for_timeout(300)
        page.pdf(
            path=PDF_PATH,
            format="A4",
            print_background=True,
            display_header_footer=True,
            header_template="<div></div>",
            footer_template=FOOTER,
            margin={"top": "2.2cm", "bottom": "1.6cm", "left": "1.6cm", "right": "1.6cm"},
        )
        browser.close()
    print(f"wrote {PDF_PATH}")

if __name__ == "__main__":
    main()
