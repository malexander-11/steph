#!/usr/bin/env python3
"""One-off diagnostic: render the headless-target boards and report DOM structure
(light DOM, shadow-piercing locators, iframes) so we can pick correct selectors.
Runs in CI. Safe to delete once selectors are tuned."""

import glob
import os

from playwright.sync_api import sync_playwright

TARGETS = {
    "Apple": {
        "url": "https://jobs.apple.com/en-gb/search?location=london-LONC",
        "selectors": ['a[href*="/details/"]', 'a[href*="/role/"]', "a"],
    },
    "Fremantle": {
        "url": "https://jobsearch.createyourowncareer.com/FREMANTLE/go/Fremantle_all_jobs/5381701/",
        "selectors": ['a[href*="/job/"]', "a"],
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
                page.wait_for_timeout(8000)
                print("title:", repr(page.title()))
                print("iframes:", [f.url[:70] for f in page.frames if f != page.main_frame])
                # shadow-piercing locator counts + samples
                for sel in cfg["selectors"]:
                    loc = page.locator(sel)
                    n = loc.count()
                    print(f"  locator[{n:>3}] {sel}")
                    for i in range(min(n, 6)):
                        el = loc.nth(i)
                        try:
                            t = (el.inner_text(timeout=1000) or "").strip()[:50]
                            h = (el.get_attribute("href") or "")[:70]
                        except Exception:
                            t, h = "?", "?"
                        if "details" in h or "/job/" in h or (sel == "a" and h and "apple.com/uk" not in h):
                            print(f"        {t!r} -> {h}")
                # also scan every frame for job-detail links
                for fr in page.frames:
                    hrefs = fr.eval_on_selector_all(
                        "a", "els => els.map(e=>e.getAttribute('href')).filter(h=>h && (h.includes('/details/')||h.includes('/job/')))")
                    if hrefs:
                        print(f"  frame {fr.url[:50]} job hrefs:", hrefs[:5])
            except Exception as exc:
                print("ERR:", type(exc).__name__, str(exc)[:120])
            finally:
                page.close()
        browser.close()


if __name__ == "__main__":
    main()
