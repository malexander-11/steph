# jobs-watch

A daily sweep that pulls **real** TV-industry vacancies straight from each
employer's applicant-tracking system (ATS), filters to relevant roles, diffs
against the previous run, and opens a GitHub issue when genuinely new roles
appear. GitHub emails issue notifications natively: no new roles, no email.

Runs entirely on GitHub Actions — the cron fires at 07:30 UK each weekday.

## How it works

`check_jobs.py` reads `watchlist.yaml`, and for each active employer calls the
matching **adapter** in `adapters.py`. Adapters talk to the real ATS JSON APIs
(Workday, SmartRecruiters, Eightfold/Netflix, amazon.jobs, Oracle Recruiting
Cloud, BambooHR) or scrape genuinely server-rendered HTML (the Grapevine media
aggregator; Cineflix). Each returns structured jobs — `title`, `location`,
apply `url` — so there is no keyword-guessing on page text.

Roles are then filtered:
- **INCLUDE / EXCLUDE** (top of `check_jobs.py`) keep relevant TV/creative roles
  and drop software/finance/etc. — now applied to real job titles.
- a **UK/London location gate** drops the non-UK roles that global feeds
  (Sky, WBD, NBCUniversal…) return.

State lives in `seen.json`: a cumulative set of job keys (for new-role
detection) plus the current live snapshot (for the dashboard).

### Watchlist format

```yaml
commissioning:
  - name: Sky
    platform: workday
    params: {tenant: sky, site: sky_careers, wd: 3}
acquisitions:
  - name: Netflix UK
    platform: netflix
    params: {}
aggregators:
  - name: Grapevine Jobs (UK media — indies + broadcasters)
    platform: grapevine
    params: {pages: 3}
no_feed:
  - {name: BBC, reason: "SuccessFactors search is JS-rendered"}
```

Platforms: `workday`, `smartrecruiters`, `netflix`, `amazon`, `oracle`,
`bamboohr`, `html`, `grapevine`, plus generic `greenhouse` / `lever` / `ashby`
for future additions. Add an employer by dropping in an entry with the right
`platform` + `params`; no code change needed.

### `no_feed` — employers with no machine-readable vacancies

The indie/PR/agency long-tail (KEO, Love Productions, Premier PR, Freuds,
Avalon…) plus a few JS/CSRF-gated ATSes (BBC, Channel 4, Apple, Fremantle,
The Talent Manager) publish no scrapable feed. They are **not** scraped — their
London roles surface via the **Grapevine aggregator**, which carries indie and
broadcaster roles alike. Set a native email alert for any you want directly.

## Dashboard (GitHub Pages)

A read-only dashboard lives in `docs/` — a single dependency-free, theme-aware
`index.html` that reads `docs/data.json` and lists every currently-open role by
category, with location and a clickable apply link, plus the `no_feed` list.
`build_site.py` regenerates `docs/data.json` from `seen.json` + `watchlist.yaml`;
the daily workflow runs and commits it automatically.

To publish: **Settings → Pages → Source: "Deploy from a branch"**, pick the
branch and folder `/docs`, then Save. The site appears at
`https://<user>.github.io/steph/`.
Preview locally: `python build_site.py && (cd docs && python -m http.server)`.

## Maintenance

- The daily issue's footer lists any sources that errored — usually an ATS
  moved; update its `params` in `watchlist.yaml`.
- Role keywords live in `INCLUDE`/`EXCLUDE`; the UK location gate is `UK_LOC`
  (all at the top of `check_jobs.py`).
- Cron schedule (UTC) lives in `.github/workflows/daily.yml`.
- To widen coverage to a JS/CSRF-gated site later, add a headless-browser
  (Playwright) adapter — the current design deliberately avoids that for CI
  reliability.
