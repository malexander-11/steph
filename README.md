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
  (Netflix, NBCUniversal, aggregators…) return. Sources marked `uk_scoped: true`
  (country-facet Workday, UK-native ATSes) skip this gate, since they're already
  UK-only and their office locations (Leavesden, Knutsford…) aren't London.
- **cross-source dedup** on `(employer, title)` so a role carried by both an
  aggregator and a direct ATS feed is listed once (the direct feed wins).

### Aggregator APIs (broaden coverage)

`adzuna`, `reed` and `careerjet` are keyword job-search APIs that surface target
roles across the whole market — including indies with no ATS of their own. Each
takes a `queries` list (TV/creative phrases) and returns the real employer +
salary per job. All skip gracefully until their key is set, so the sweep stays
green meanwhile.
- **Adzuna** — free key at developer.adzuna.com → repo secrets `ADZUNA_APP_ID`
  and `ADZUNA_APP_KEY` (Settings → Secrets and variables → Actions). Pages 3 deep
  per query, most-recent first.
- **Reed** — free key at reed.co.uk/developers → secret `REED_API_KEY`.
- **Careerjet** — needs a partner `affid` (`CAREERJET_AFFID`); its keyless legacy
  endpoint was retired, so without a valid affid it returns nothing.

Salary (where a source provides it) shows as a tag on the dashboard.

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
`bamboohr`, `html`, `grapevine`, `adzuna`, `careerjet`, `headless`, plus generic
`greenhouse` / `lever` / `ashby` for future additions. Add an employer by
dropping in an entry with the right `platform` + `params`; no code change needed.

### Headless browser (`headless`)

Some boards render jobs only via JavaScript. The `headless` platform drives
real Chromium via Playwright to render the page, then extracts jobs either from
a CSS `selector` (each matched `<a>` is a job; the locator engine pierces open
shadow DOM) or from `application/ld+json` JobPosting blocks (`jsonld: true`).
Params: `url`, `selector` (or `jsonld`), optional `wait_for` selector,
`wait_ms`, `title_from_slug`. The workflow installs Chromium with
`playwright install --with-deps chromium`; the browser honours `HTTPS_PROXY`
when set (a no-op on GitHub Actions). If Playwright or the browser is missing,
that source is skipped and logged — the rest of the sweep is unaffected.

The adapter is verified end-to-end but currently has **no live target**: the
obvious candidates (Apple, Fremantle) turned out to serve no job data to a
datacenter IP — Apple returns only page chrome and Fremantle a nav-only shell
(confirmed against the real sites from GitHub Actions), so they sit in
`no_feed`. The capability is wired and ready for any JS board that *does* render
its listings to an automated browser; add a `platform: headless` entry to use it.

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
