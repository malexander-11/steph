# jobs-watch

A daily sweep of TV-industry careers pages that emails you only what's new.
Runs entirely on GitHub Actions — the cron fires at 07:30 UK each weekday,
diffs against `seen.json`, and opens a GitHub issue when new roles match.
GitHub emails issue notifications natively: no new roles, no email.

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
