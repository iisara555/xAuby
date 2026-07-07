#!/usr/bin/env python3
"""Render a book HTML source to a paginated PDF via Chromium print-to-pdf.

Usage: build_pdf.py [--html NAME.html] [--pdf NAME.pdf] [--footer TEXT]
Defaults to the Thai edition for backward compatibility.
"""
import argparse
import os
from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
CHROME = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"


def build(html_name: str, pdf_name: str, footer_label: str):
    html_path = os.path.join(HERE, html_name)
    pdf_path = os.path.join(HERE, pdf_name)
    footer = f"""
<div style="font-family:'Noto Sans Thai','Noto Sans',sans-serif; font-size:8px; width:100%;
            color:#888; padding:0 16mm; display:flex; justify-content:space-between;">
  <span>{footer_label}</span>
  <span><span class="pageNumber"></span> / <span class="totalPages"></span></span>
</div>
"""
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=CHROME)
        page = browser.new_page()
        page.goto(f"file://{html_path}")
        page.wait_for_timeout(300)
        page.pdf(
            path=pdf_path,
            format="A4",
            print_background=True,
            display_header_footer=True,
            header_template="<div></div>",
            footer_template=footer,
            margin={"top": "2.2cm", "bottom": "1.6cm", "left": "1.6cm", "right": "1.6cm"},
        )
        browser.close()
    print(f"wrote {pdf_path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--html", default="xauby_book.html")
    ap.add_argument("--pdf", default="xAuby_Book_TH.pdf")
    ap.add_argument("--footer", default="xAuby — คู่มือฉบับสมบูรณ์")
    args = ap.parse_args()
    build(args.html, args.pdf, args.footer)


if __name__ == "__main__":
    main()
