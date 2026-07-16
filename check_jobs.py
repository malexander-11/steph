#!/usr/bin/env python3
"""Daily jobs sweep: pull REAL vacancies from each employer's ATS via the
platform adapters in adapters.py, filter to relevant TV/creative roles, diff
against the previous run, and write new_jobs.md when genuinely new roles appear.

State (seen.json) holds both a cumulative set of job keys (for new-role
detection) and the current live snapshot (for the dashboard), so the site always
shows open roles rather than a growing history.
"""

import datetime
import json
import pathlib
import re
import sys

import yaml

import adapters

ROOT = pathlib.Path(__file__).parent
SEEN_PATH = ROOT / "seen.json"
REPORT_PATH = ROOT / "new_jobs.md"

# Keep only relevant roles. These filter REAL job titles from the ATS feeds
# (e.g. dropping the software/finance roles that dominate broadcaster boards).
INCLUDE = re.compile(
    r"(commission|acquisition|development|distribut|sales|partnership"
    r"|publicit|communications|awards|talent|creative"
    r"|editor|producer|researcher|exec"
    # broader TV/editorial vocabulary so genuinely-relevant roles aren't dropped:
    r"|content|unscripted|scripted|factual|entertainment|drama|comedy"
    r"|documentary|\bformat|brand|marketing|head of|director of)",
    re.I,
)
EXCLUDE = re.compile(
    r"(software|engineer|\bdevops\b|data scientist|finance|accountant"
    r"|legal counsel|cyber|security|apprentice|internship|\bintern\b"
    r"|customer service|cleaner|chef|barista|moderator"
    # non-TV noise from diversified conglomerates (theme parks, games studios):
    r"|construction|\bresort\b|roadway|\brail\b|property development"
    r"|external development|gameplay"
    # back-office / finance / retail noise that keyword INCLUDE lets slip:
    r"|audit|statutory|ledger|royalty|payroll|treasury|\btax\b|procurement"
    r"|actuar|bookkeep|stylist|warehouse|\bdriver\b)",
    re.I,
)
# UK/London filter for the big conglomerate feeds (which are global). A job is
# kept if it has no location string (aggregator/HTML sources) or the location
# reads as UK. This is a location gate, separate from the INCLUDE/EXCLUDE role
# keywords — it drops Prague/Chicago/Shanghai roles from Sky/WBD/etc.
UK_LOC = re.compile(
    r"(united kingdom|\blondon\b|\buk\b|\bgb\b|england|scotland|wales"
    r"|northern ireland|manchester|salford|leeds|bristol|glasgow|cardiff"
    r"|birmingham|brentwood|leavesden|yorkshire|\bkent\b|surrey|hampshire)",
    re.I,
)

# Sections of the watchlist that carry fetchable job sources.
SKIP_SECTIONS = {"no_feed"}


def relevant(title: str) -> bool:
    return bool(INCLUDE.search(title) and not EXCLUDE.search(title))


def in_uk(location: str) -> bool:
    return not location or bool(UK_LOC.search(location))


def job_key(job: dict) -> str:
    """Stable identity for a job: its apply URL, or employer|title as a fallback."""
    return job.get("url") or f"{job.get('employer')}|{job.get('title')}"


# Aggregators pull the same role many other sources also carry, so they run
# LAST and their duplicates are dropped in favour of the direct-ATS entry.
AGGREGATOR_PLATFORMS = {"careerjet", "adzuna", "reed", "grapevine"}


def norm(text: str) -> str:
    """Loose key for cross-source dedup: lowercase alphanumerics only."""
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def load_sources():
    """Yield (category, name, platform, params, uk_scoped) for every active
    source, with aggregator platforms ordered last (for dedup precedence)."""
    data = yaml.safe_load((ROOT / "watchlist.yaml").read_text())
    rows = []
    for section, entries in data.items():
        if section in SKIP_SECTIONS:
            continue
        for entry in entries or []:
            if "platform" not in entry:
                continue
            params = dict(entry.get("params") or {})
            params["employer"] = entry["name"]
            rows.append((section, entry["name"], entry["platform"], params,
                         bool(entry.get("uk_scoped", False))))
    rows.sort(key=lambda r: r[2] in AGGREGATOR_PLATFORMS)
    return rows


def main() -> int:
    state = json.loads(SEEN_PATH.read_text()) if SEEN_PATH.exists() else {}
    first_run = not SEEN_PATH.exists()
    seen_keys = set(state.get("seen_keys", []))

    current, new_items, errors = {}, [], []
    global_seen = set()  # (employer, title) across all sources — first wins
    for category, name, platform, params, uk_scoped in load_sources():
        try:
            jobs = adapters.fetch(platform, params)
        except Exception as exc:  # keep sweeping even if one source fails
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
            continue

        kept = []
        for job in jobs:
            if not job.get("title") or not relevant(job["title"]):
                continue
            # uk_scoped sources (country-facet Workday, UK-native ATS) are
            # already UK-only; the location gate only applies to global feeds.
            if not uk_scoped and not in_uk(job.get("location", "")):
                continue
            employer = job.get("employer", name)
            gkey = (norm(employer), norm(job["title"]))
            if gkey in global_seen:
                continue
            global_seen.add(gkey)
            kept.append({"title": job["title"], "url": job.get("url", ""),
                         "location": job.get("location", ""),
                         "employer": employer, "salary": job.get("salary", "")})
            key = job_key(job)
            if key not in seen_keys:
                new_items.append((category, name, job["title"],
                                  job.get("location", ""), job.get("url", "")))
            seen_keys.add(key)

        current.setdefault(category, {})[name] = kept

    state = {
        "generated": datetime.datetime.now(datetime.timezone.utc)
        .strftime("%Y-%m-%d %H:%M UTC"),
        "seen_keys": sorted(seen_keys),
        "current": current,
        "errors": errors,
    }
    SEEN_PATH.write_text(json.dumps(state, indent=1, ensure_ascii=False))

    total = sum(len(js) for cat in current.values() for js in cat.values())
    sources = sum(len(cat) for cat in current.values())

    if first_run:
        print(f"First run: baseline stored ({total} live roles across {sources} "
              f"sources). Diffs start tomorrow.")
        if errors:
            print("\nSources that errored:\n- " + "\n- ".join(errors))
        return 0

    if new_items:
        lines = ["## New roles spotted\n"]
        cur = None
        for category, name, title, location, url in sorted(new_items):
            if category != cur:
                lines.append(f"\n### {category.replace('_', ' ').title()}\n")
                cur = category
            loc = f" — _{location}_" if location else ""
            link = f" — {url}" if url else ""
            lines.append(f"- **{title}** — {name}{loc}{link}")
        if errors:
            lines.append("\n---\n_Sources that errored:_ " + "; ".join(errors))
        REPORT_PATH.write_text("\n".join(lines))
        print(f"{len(new_items)} new role(s) found — report written.")
    else:
        print("No new roles today.")
        if errors:
            print("Errors:\n- " + "\n- ".join(errors))

    return 0


if __name__ == "__main__":
    sys.exit(main())
