#!/usr/bin/env python3
"""Generate docs/data.json for the GitHub Pages dashboard from the live snapshot
in seen.json plus the no_feed reference list in watchlist.yaml. Run after
check_jobs.py. No new dependencies (reuses pyyaml)."""

import json
import pathlib

import yaml

ROOT = pathlib.Path(__file__).parent
DOCS = ROOT / "docs"


def main() -> int:
    state = json.loads((ROOT / "seen.json").read_text())
    watchlist = yaml.safe_load((ROOT / "watchlist.yaml").read_text())

    current = state.get("current", {})
    categories, total_roles, employers = [], 0, 0
    for category, emps in current.items():
        sites = []
        for name, jobs in emps.items():
            total_roles += len(jobs)
            employers += 1
            sites.append({"name": name, "jobs": jobs})
        # Show employers with roles first, then the empty ones.
        sites.sort(key=lambda s: (len(s["jobs"]) == 0, s["name"].lower()))
        categories.append({"name": category, "sites": sites})

    no_feed = [
        {"name": e["name"], "reason": e.get("reason", "")}
        for e in (watchlist.get("no_feed") or [])
    ]

    data = {
        "generated": state.get("generated", ""),
        "totals": {
            "roles": total_roles,
            "employers": employers,
            "sources": len(current),
        },
        "categories": categories,
        "no_feed": no_feed,
        "errors": state.get("errors", []),
    }

    DOCS.mkdir(exist_ok=True)
    (DOCS / "data.json").write_text(json.dumps(data, indent=1, ensure_ascii=False))
    print(f"Wrote docs/data.json — {total_roles} live roles across "
          f"{employers} employers; {len(no_feed)} no-feed entries.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
