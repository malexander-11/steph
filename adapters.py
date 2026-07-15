#!/usr/bin/env python3
"""Per-platform job fetchers for jobs-watch.

Each adapter takes the params declared for a watchlist entry and returns a list
of dicts: {"title", "url", "location", "employer"}. Adapters talk to the ATS
JSON APIs employers actually use (Workday, SmartRecruiters, Eightfold, BambooHR,
Amazon) or scrape genuinely server-rendered HTML (Grapevine aggregator, ITV
JSON-LD, and a few bespoke boards). No adapter guesses: if a source returns
nothing, the employer simply has no open roles that day.

All network access goes through requests with a shared honest User-Agent and a
sane timeout. Adapters raise on HTTP errors so check_jobs.py can record the
failure per-site and keep sweeping.
"""

import json
import os
import re
from urllib.parse import urljoin, quote_plus

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) jobs-watch/1.0 (personal job-alert script; low volume, one visit per day)"
}
TIMEOUT = 30
# Politeness / safety cap: never page a single source forever.
MAX_PAGES = 15


def _get(url, **kw):
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT, **kw)
    r.raise_for_status()
    return r


def _post(url, payload, **kw):
    headers = dict(HEADERS)
    headers["Content-Type"] = "application/json"
    r = requests.post(url, json=payload, headers=headers, timeout=TIMEOUT, **kw)
    r.raise_for_status()
    return r


def _clean(text):
    return " ".join((text or "").split())


# --------------------------------------------------------------------------- #
# JSON-API adapters
# --------------------------------------------------------------------------- #

def fetch_workday(employer, tenant, site, wd, host=None, search="London", **_):
    """Workday CXS API. Covers Sky, Disney, WBD, Sony, Banijay (EndemolShine).

    POST {tenant}.wd{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs
    Pages on offset (limit 20). externalPath builds the public apply URL.
    """
    base = host or f"https://{tenant}.wd{wd}.myworkdayjobs.com"
    api = f"{base}/wday/cxs/{tenant}/{site}/jobs"
    apply_root = f"{base}/{site}"
    jobs, offset, total = [], 0, None
    for _page in range(MAX_PAGES):
        payload = {"appliedFacets": {}, "limit": 20, "offset": offset,
                   "searchText": search or ""}
        data = _post(api, payload).json()
        if total is None:
            total = data.get("total", 0)
        postings = data.get("jobPostings", [])
        if not postings:
            break
        for p in postings:
            path = (p.get("externalPath") or "").lstrip("/")
            jobs.append({
                "title": _clean(p.get("title")),
                "url": f"{apply_root}/{path}" if path else apply_root,
                "location": _clean(p.get("locationsText")),
                "employer": employer,
            })
        offset += 20
        if offset >= (total or 0):
            break
    return jobs


def fetch_smartrecruiters(employer, company, country="gb", **_):
    """SmartRecruiters public postings API. Covers NBCUniversal (NBCUniversal3).

    The feed is global, so we page through all postings (limit 100) and filter
    client-side by ISO country code (default 'gb' — UK), which is more reliable
    than the server-side country param.
    """
    api = f"https://api.smartrecruiters.com/v1/companies/{company}/postings"
    jobs, offset = [], 0
    for _page in range(MAX_PAGES):
        data = _get(api, params={"limit": 100, "offset": offset}).json()
        content = data.get("content", [])
        if not content:
            break
        for p in content:
            loc = p.get("location", {}) or {}
            code = str(loc.get("country") or "").lower()
            if country and code != country.lower():
                continue
            city = loc.get("city") or ""
            jobs.append({
                "title": _clean(p.get("name")),
                "url": f"https://jobs.smartrecruiters.com/{company}/{p.get('id')}",
                "location": _clean(", ".join(x for x in (city, code.upper()) if x)),
                "employer": employer,
            })
        offset += 100
        if offset >= data.get("totalFound", 0):
            break
    return jobs


def fetch_netflix(employer, location="London", **_):
    """Netflix careers backend (Eightfold AI). Pages on start/num."""
    api = "https://explore.jobs.netflix.net/api/apply/v2/jobs"
    jobs, start, num = [], 0, 100
    for _page in range(MAX_PAGES):
        params = {"domain": "netflix.com", "query": "", "location": location,
                  "start": start, "num": num}
        data = _get(api, params=params).json()
        positions = data.get("positions", [])
        if not positions:
            break
        for p in positions:
            jobs.append({
                "title": _clean(p.get("name")),
                "url": p.get("canonicalPositionUrl") or p.get("job_url")
                or f"https://jobs.netflix.com/jobs/{p.get('id')}",
                "location": _clean(p.get("location")),
                "employer": employer,
            })
        start += num
        if start >= data.get("count", 0):
            break
    return jobs


def fetch_amazon(employer, base_query="", loc_query="London", **_):
    """amazon.jobs search.json. Covers Amazon MGM / Prime Video (by query)."""
    api = "https://www.amazon.jobs/en-gb/search.json"
    jobs, offset, limit = [], 0, 100
    for _page in range(MAX_PAGES):
        params = {"base_query": base_query, "loc_query": loc_query,
                  "country": "GBR", "result_limit": limit, "offset": offset}
        data = _get(api, params=params).json()
        batch = data.get("jobs", [])
        if not batch:
            break
        for j in batch:
            path = j.get("job_path") or ""
            city = j.get("city") or ""
            jobs.append({
                "title": _clean(j.get("title")),
                "url": urljoin("https://www.amazon.jobs", path) if path
                else "https://www.amazon.jobs",
                "location": _clean(city),
                "employer": employer,
            })
        offset += limit
        if offset >= data.get("hits", 0):
            break
    return jobs


def fetch_bamboohr(employer, subdomain, **_):
    """BambooHR careers JSON. Covers United Agents."""
    api = f"https://{subdomain}.bamboohr.com/careers/list"
    data = _get(api).json()
    jobs = []
    for j in (data.get("result") or []):
        loc = j.get("location") or {}
        city = loc.get("city") or ""
        jobs.append({
            "title": _clean(j.get("jobOpeningName")),
            "url": f"https://{subdomain}.bamboohr.com/careers/{j.get('id')}",
            "location": _clean(city),
            "employer": employer,
        })
    return jobs


def fetch_oracle(employer, host, site="CX_1", **_):
    """Oracle Recruiting Cloud (ORC) candidate-experience REST API. Covers ITV.

    GET {host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions with a
    findReqs finder. Pages via limit/offset in the finder string.
    """
    api = f"{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
    job_root = f"{host}/hcmUI/CandidateExperience/en/sites/{site}/job"
    jobs, offset, total = [], 0, None
    for _page in range(MAX_PAGES):
        finder = (f"findReqs;siteNumber={site},limit=25,offset={offset},"
                  f"sortBy=POSTING_DATES_DESC")
        params = {"onlyData": "true",
                  "expand": "requisitionList.secondaryLocations,flexFieldsFacet.values",
                  "finder": finder}
        data = _get(api, params=params).json()
        items = data.get("items", [])
        req_list = items[0].get("requisitionList", []) if items else []
        if total is None and items:
            total = items[0].get("TotalJobsCount", 0)
        if not req_list:
            break
        for j in req_list:
            jid = j.get("Id")
            jobs.append({
                "title": _clean(j.get("Title")),
                "url": f"{job_root}/{jid}" if jid else job_root,
                "location": _clean(j.get("PrimaryLocation")),
                "employer": employer,
            })
        offset += 25
        if total and offset >= total:
            break
    return jobs


def fetch_greenhouse(employer, token, **_):
    """Greenhouse boards API — generic, for future watchlist additions."""
    api = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
    data = _get(api).json()
    return [{
        "title": _clean(j.get("title")),
        "url": j.get("absolute_url"),
        "location": _clean((j.get("location") or {}).get("name")),
        "employer": employer,
    } for j in data.get("jobs", [])]


def fetch_lever(employer, company, **_):
    """Lever postings API — generic, for future watchlist additions."""
    api = f"https://api.lever.co/v0/postings/{company}?mode=json"
    data = _get(api).json()
    return [{
        "title": _clean(j.get("text")),
        "url": j.get("hostedUrl"),
        "location": _clean((j.get("categories") or {}).get("location")),
        "employer": employer,
    } for j in data]


def fetch_ashby(employer, org, **_):
    """Ashby job-board API — generic, for future watchlist additions."""
    api = f"https://api.ashbyhq.com/posting-api/job-board/{org}"
    data = _get(api).json()
    return [{
        "title": _clean(j.get("title")),
        "url": j.get("jobUrl"),
        "location": _clean(j.get("location")),
        "employer": employer,
    } for j in data.get("jobs", [])]


# --------------------------------------------------------------------------- #
# HTML / JSON-LD adapters (genuinely server-rendered sources only)
# --------------------------------------------------------------------------- #

_GRAPEVINE_HREF = re.compile(
    r'href="(/(?:executive_)?mediajobs/(\d+),([^"]+?)\.html)"', re.I)


def fetch_grapevine(employer, pages=3, **_):
    """Grapevine Jobs — the UK media aggregator. Server-rendered, so the initial
    HTML carries every listing. Each href is self-describing:
    /(executive_)?mediajobs/{id},{Title_Words}_{Employer}.html
    We parse title + employer straight out of the slug. This is the backbone that
    covers indie prodcos + broadcasters that have no feed of their own.
    """
    base = "https://www.grapevinejobs.co.uk"
    listing = f"{base}/jobseeker/mediajobs.aspx"
    seen_ids, jobs = set(), []
    for page in range(1, min(pages, MAX_PAGES) + 1):
        url = listing if page == 1 else f"{listing}?page={page}&number_of_records=25"
        html = _get(url).text
        found_this_page = False
        for href, jid, slug in _GRAPEVINE_HREF.findall(html):
            if jid in seen_ids:
                continue
            seen_ids.add(jid)
            found_this_page = True
            words = slug.replace("_", " ").strip()
            # Slug is "Title Words Employer"; employer is the tail but we can't
            # split reliably, so surface the whole slug as the title (it reads as
            # "Role — Employer") and keep the aggregator as the employer bucket.
            jobs.append({
                "title": words,
                "url": urljoin(base, href),
                "location": "",
                "employer": employer,
            })
        if not found_this_page:
            break
    return jobs


def fetch_jsonld(employer, list_url, link_re, base=None, **_):
    """Follow job links from a server-rendered list page, then read the
    application/ld+json JobPosting block on each detail page. Covers ITV.
    """
    base = base or list_url
    html = _get(list_url).text
    soup = BeautifulSoup(html, "html.parser")
    pat = re.compile(link_re, re.I)
    links, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if pat.search(href):
            full = urljoin(base, href)
            if full not in seen:
                seen.add(full)
                links.append(full)
    jobs = []
    for link in links[:60]:  # cap detail fetches politely
        try:
            page = _get(link).text
        except Exception:
            continue
        for blob in BeautifulSoup(page, "html.parser").find_all(
                "script", type="application/ld+json"):
            try:
                obj = json.loads(blob.string or "{}")
            except (ValueError, TypeError):
                continue
            for node in obj if isinstance(obj, list) else [obj]:
                if not isinstance(node, dict) or node.get("@type") != "JobPosting":
                    continue
                loc = node.get("jobLocation") or {}
                if isinstance(loc, list):
                    loc = loc[0] if loc else {}
                addr = (loc.get("address") or {}) if isinstance(loc, dict) else {}
                jobs.append({
                    "title": _clean(node.get("title")),
                    "url": node.get("url") or link,
                    "location": _clean(addr.get("addressLocality")),
                    "employer": employer,
                })
    return jobs


def fetch_html(employer, url, selector, base=None, title_from_slug=False, **_):
    """Bespoke server-rendered scrape for boards that render job links in the
    initial HTML but have no JSON API. Covers Cineflix.
    `selector` is a CSS selector matching each job's <a>. When the anchor text
    bleeds into the job description, set title_from_slug to derive a clean title
    from the URL's last path segment instead.
    """
    base = base or url
    html = _get(url).text
    soup = BeautifulSoup(html, "html.parser")
    jobs, seen = [], set()
    for a in soup.select(selector):
        href = a.get("href")
        if not href:
            continue
        full = urljoin(base, href)
        if title_from_slug:
            slug = full.rstrip("/").rsplit("/", 1)[-1]
            title = _clean(slug.replace("-", " ")).title()
        else:
            title = _clean(a.get_text(" ", strip=True))
        if not title or full in seen:
            continue
        seen.add(full)
        jobs.append({"title": title, "url": full, "location": "",
                     "employer": employer})
    return jobs


def _chromium_executable():
    """Prefer the pre-installed browser (PLAYWRIGHT_BROWSERS_PATH); fall back to
    Playwright's default resolution."""
    import glob
    root = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")
    if root:
        found = sorted(glob.glob(f"{root}/chromium-*/chrome-linux/chrome"))
        if found:
            return found[-1]
    return None


def fetch_headless(employer, url, wait_for=None, wait_ms=4000, selector=None,
                   link_attr="href", title_from_slug=False, jsonld=False,
                   location_selector=None, **_):
    """Render a JS/CSRF-gated board in headless Chromium and extract real jobs.

    Covers sites with no JSON API and no server-rendered HTML (Apple, Fremantle,
    The Talent Manager). Two extraction modes:
      * jsonld=True   -> read application/ld+json JobPosting blocks from the
                         rendered DOM.
      * selector=CSS  -> each matched <a> is a job; title from its text (or from
                         the URL slug when title_from_slug is set).

    The browser honours HTTPS_PROXY when present (a no-op in GitHub Actions,
    which has no proxy) and tolerates the proxy's re-signed TLS certs. Raises a
    clear error if Playwright or the browser is unavailable, so check_jobs.py
    records it per-source and keeps sweeping.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("playwright not installed (pip install playwright)") from exc

    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    launch = {"headless": True, "args": ["--no-sandbox", "--disable-dev-shm-usage"]}
    exe = _chromium_executable()
    if exe:
        launch["executable_path"] = exe
    if proxy:
        launch["proxy"] = {"server": proxy, "bypass": "localhost,127.0.0.1"}

    jobs = []
    with sync_playwright() as p:
        browser = p.chromium.launch(**launch)
        page = browser.new_context(ignore_https_errors=True,
                                    user_agent=HEADERS["User-Agent"]).new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            if wait_for:
                page.wait_for_selector(wait_for, timeout=20000)
            page.wait_for_timeout(wait_ms)

            if jsonld:
                blocks = page.eval_on_selector_all(
                    'script[type="application/ld+json"]',
                    "els => els.map(e => e.textContent)")
                for raw in blocks:
                    try:
                        obj = json.loads(raw or "{}")
                    except (ValueError, TypeError):
                        continue
                    for node in obj if isinstance(obj, list) else [obj]:
                        if not isinstance(node, dict) or node.get("@type") != "JobPosting":
                            continue
                        loc = node.get("jobLocation") or {}
                        if isinstance(loc, list):
                            loc = loc[0] if loc else {}
                        addr = (loc.get("address") or {}) if isinstance(loc, dict) else {}
                        jobs.append({
                            "title": _clean(node.get("title")),
                            "url": node.get("url") or url,
                            "location": _clean(addr.get("addressLocality")),
                            "employer": employer,
                        })
            elif selector:
                rows = page.eval_on_selector_all(selector, """els => els.map(e => ({
                    text: e.textContent.trim(),
                    href: e.getAttribute('href') || e.href || ''
                }))""")
                seen = set()
                for r in rows:
                    href = r.get("href") or ""
                    full = urljoin(url, href) if href else url
                    if title_from_slug and href:
                        slug = full.rstrip("/").rsplit("/", 1)[-1]
                        title = _clean(slug.replace("-", " ")).title()
                    else:
                        title = _clean(r.get("text"))
                    if not title or full in seen:
                        continue
                    seen.add(full)
                    jobs.append({"title": title, "url": full, "location": "",
                                 "employer": employer})
        finally:
            browser.close()
    return jobs


ADAPTERS = {
    "workday": fetch_workday,
    "headless": fetch_headless,
    "smartrecruiters": fetch_smartrecruiters,
    "netflix": fetch_netflix,
    "amazon": fetch_amazon,
    "bamboohr": fetch_bamboohr,
    "oracle": fetch_oracle,
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
    "grapevine": fetch_grapevine,
    "jsonld": fetch_jsonld,
    "html": fetch_html,
}


def fetch(platform, params):
    """Dispatch to the adapter for `platform`, passing the entry's params."""
    try:
        adapter = ADAPTERS[platform]
    except KeyError:
        raise ValueError(f"unknown platform: {platform!r}")
    return adapter(**params)
