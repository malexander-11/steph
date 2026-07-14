# jobs-watch

A daily sweep of TV-industry careers pages that emails you only what's new.
Runs entirely on GitHub Actions — the cron fires at 07:30 UK each weekday,
diffs against `seen.json`, and opens a GitHub issue when new roles match.
GitHub emails issue notifications natively: no new roles, no email.

## Dashboard (GitHub Pages)

A read-only dashboard lives in `docs/` — it lists every employer by category,
shows which sites are scraped live vs. need an email alert, and displays the
current baseline of roles per site. It's a single static `index.html` (no
build step, no external libraries) that reads `docs/data.json`.

`docs/data.json` is regenerated from `watchlist.yaml` + `seen.json` by
`build_site.py`, which the daily workflow runs and commits automatically, so
the page stays current without any manual step.

To publish it: **Settings → Pages → Build and deployment → Source:
"Deploy from a branch"**, pick the branch this lives on and folder `/docs`,
then Save. The site appears at `https://<user>.github.io/steph/`.
To preview locally: `python build_site.py && (cd docs && python -m http.server)`.

## Maintenance

- The daily issue's error footer lists sites that failed or parsed zero
  links — fix the URL in `watchlist.yaml` or change its strategy to `alert`.
- Keywords live in `INCLUDE`/`EXCLUDE` at the top of `check_jobs.py`.
- Cron schedule (UTC) lives in `.github/workflows/daily.yml`.

## Sites this can't scrape (set native email alerts instead)

- **LinkedIn** — blocks all automation. Save searches for "commissioning",
  "acquisitions", "content partnerships" (London) with daily alerts on.
- Anything marked `strategy: alert` in the watchlist (Workday and
  SuccessFactors boards, Netflix, TikTok, Meta, Google) — each careers
  site offers its own job-alert email signup.
