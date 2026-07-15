#!/usr/bin/env python3
"""One-off diagnostic: render the headless-target boards and report their DOM
structure so we can pick correct selectors. Runs in CI (real network); prints
to stdout for the Actions log. Safe to delete once selectors are tuned."""

import glob
import os

from playwright.sync_api import sync_playwright

TARGETS = {
    "Apple": {
        "url": "https://jobs.apple.com/en-gb/search?location=london-LONC",
        "selectors": ['a[href*="/details/"]', 'a.table--advanced-search__title',
                      'a[href*="/role/"]', 'ul[role="list"] a', 'a.link-inline'],
    },
    "Fremantle": {
        "url": "https://jobsearch.createyourowncareer.com/FREMANTLE/go/Fremantle_all_jobs/5381701/",
        "selectors": ['a[href*="/job/"]', 'a.jobTitle-link',
                      'a[data-careersite-propertyid="title"]', '.data-row a',
                      'span.jobTitle a'],
    },
}


def main():
    exe = None
    root = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")
    if root:
        found = sorted(glob.glob(f"{root}/chromium-*/chrome-linux/chrome"))
        exe = found[-1] if found else None
    proxy = os.environ.get("HTTPS_PROXY")
    launch = {"headless": True, "args": ["--no-sandbox", "--disable-dev-shm-usage"]}
    if exe:
        launch["executable_path"] = exe
    if proxy:
        launch["proxy"] = {"server": proxy, "bypass": "localhost,127.0.0.1"}

    with sync_playwright() as p:
        browser = p.chromium.launch(**launch)
        for name, cfg in TARGETS.items():
            print(f"\n===== {name}: {cfg['url']} =====")
            page = browser.new_context(ignore_https_errors=True).new_page()
            try:
                page.goto(cfg["url"], wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(7000)
                print("title:", page.title())
                print("total anchors:", page.eval_on_selector_all("a", "els => els.length"))
                for sel in cfg["selectors"]:
                    rows = page.eval_on_selector_all(sel, """els => els.slice(0,5).map(e => ({
                        t: (e.textContent||'').trim().slice(0,55),
                        h: (e.getAttribute('href')||'').slice(0,70)}))""")
                    total = page.eval_on_selector_all(sel, "els => els.length")
                    print(f"  [{total:>3}] {sel}")
                    for r in rows:
                        print(f"        {r['t']!r} -> {r['h']}")
            except Exception as exc:
                print("ERR:", type(exc).__name__, str(exc)[:120])
            finally:
                page.close()
        browser.close()


if __name__ == "__main__":
    main()
