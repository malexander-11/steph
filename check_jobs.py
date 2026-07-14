#!/usr/bin/env python3
"""Daily jobs sweep: fetch each watchlist page, extract job-looking links,
diff against seen.json, and write new_jobs.md if anything new appeared."""

import json
import pathlib
import re
import sys
from urllib.parse import urljoin

import requests
import yaml
from bs4 import BeautifulSoup

ROOT = pathlib.Path(__file__).parent
SEEN_PATH = ROOT / "seen.json"
REPORT_PATH = ROOT / "new_jobs.md"

INCLUDE = re.compile(
    r"(commission|acquisition|development|distribut|sales|partnership"
    r"|publicit|communications|awards|talent|creative executive"
    r"|editor|producer|researcher|exec)",
    re.I,
)
EXCLUDE = re.compile(
    r"(software|engineer|\bdevops\b|data scientist|finance|accountant"
    r"|legal counsel|cyber|security|apprentice|internship|\bintern\b"
    r"|customer service|cleaner|chef|barista)",
    re.I,
)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) jobs-watch/1.0 (personal job-alert script; low volume, one visit per day)"
}


def load_watchlist():
    with open(ROOT / "watchlist.yaml") as f:
        data = yaml.safe_load(f)
    for category, sites in data.items():
        for site in sites or []:
            if site.get("strategy", "html") == "html":
                yield category, site["name"], site["url"]


def extract_jobs(base_url: str, html: str):
    """Return {title: url} for links that look like job postings."""
    soup = BeautifulSoup(html, "html.parser")
    jobs = {}
    for a in soup.find_all("a", href=True):
        title = " ".join(a.get_text(" ", strip=True).split())
        if not (10 <= len(title) <= 120):
            continue
        if not INCLUDE.search(title) or EXCLUDE.search(title):
            continue
        jobs[title] = urljoin(base_url, a["href"])
    return jobs


def main() -> int:
    seen = json.loads(SEEN_PATH.read_text()) if SEEN_PATH.exists() else {}
    first_run = not SEEN_PATH.exists()
    new_items, errors = [], []

    for category, name, url in load_watchlist():
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            jobs = extract_jobs(url, resp.text)
            if not jobs:
                errors.append(f"{name}: fetched OK but parsed 0 job links "
                              f"(likely JS-rendered — consider strategy: alert)")
            site_seen = set(seen.get(name, []))
            for title, link in jobs.items():
                if title not in site_seen:
                    new_items.append((category, name, title, link))
            seen[name] = sorted(site_seen | set(jobs))
        except Exception as exc:  # keep sweeping even if one site fails
            errors.append(f"{name}: {exc}")

    SEEN_PATH.write_text(json.dumps(seen, indent=1, sort_keys=True))

    if first_run:
        print(f"First run: baseline stored ({sum(len(v) for v in seen.values())} "
              f"roles across {len(seen)} sites). Diffs start tomorrow.")
        if errors:
            print("\nSites needing attention:\n- " + "\n- ".join(errors))
        return 0

    if new_items:
        lines = ["## New roles spotted\n"]
        current = None
        for category, name, title, link in sorted(new_items):
            if category != current:
                lines.append(f"\n### {category.replace('_', ' ').title()}\n")
                current = category
            lines.append(f"- **{title}** — {name} — {link}")
        if errors:
            lines.append("\n---\n_Sites that errored (check URLs):_ "
                         + "; ".join(errors))
        REPORT_PATH.write_text("\n".join(lines))
        print(f"{len(new_items)} new role(s) found — report written.")
    else:
        print("No new roles today.")
        if errors:
            print("Errors:\n- " + "\n- ".join(errors))

    return 0


if __name__ == "__main__":
    sys.exit(main())
