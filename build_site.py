#!/usr/bin/env python3
"""Generate docs/data.json for the GitHub Pages dashboard from watchlist.yaml
and seen.json. Run after check_jobs.py so the page reflects the latest baseline.
No new dependencies — reuses pyyaml, already required by the sweep."""

import datetime
import json
import pathlib

import yaml

ROOT = pathlib.Path(__file__).parent
DOCS = ROOT / "docs"


def main() -> int:
    watchlist = yaml.safe_load((ROOT / "watchlist.yaml").read_text())
    seen_path = ROOT / "seen.json"
    seen = json.loads(seen_path.read_text()) if seen_path.exists() else {}

    categories, live, alert, total_roles = [], 0, 0, 0
    for category, sites in watchlist.items():
        entries = []
        for site in sites or []:
            strategy = site.get("strategy", "html")
            roles = seen.get(site["name"], []) if strategy == "html" else []
            if strategy == "html":
                live += 1
                total_roles += len(roles)
            else:
                alert += 1
            entries.append({
                "name": site["name"],
                "url": site["url"],
                "strategy": strategy,
                "roles": roles,
            })
        categories.append({"name": category, "sites": entries})

    data = {
        "generated": datetime.datetime.now(datetime.timezone.utc)
        .strftime("%Y-%m-%d %H:%M UTC"),
        "totals": {"live": live, "alert": alert, "roles": total_roles},
        "categories": categories,
    }

    DOCS.mkdir(exist_ok=True)
    (DOCS / "data.json").write_text(json.dumps(data, indent=1, ensure_ascii=False))
    print(f"Wrote docs/data.json — {live} live sites, {alert} alert sites, "
          f"{total_roles} roles.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
