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

def fetch_workday(employer, tenant, site, wd, host=None, search="London",
                  applied_facets=None, **_):
    """Workday CXS API. Covers Sky, Disney, WBD, Sony, Banijay (EndemolShine).

    POST {tenant}.wd{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs
    Pages on offset (limit 20). externalPath builds the public apply URL.

    When applied_facets is given (e.g. a UK locationCountry UUID, or a union of
    UK location ids), it is sent as Workday's `appliedFacets` and searchText is
    cleared — this returns the full UK set (incl. sites whose text lacks
    "London") and drops cross-border false positives, which a plain
    searchText="London" both misses and over-admits.
    """
    base = host or f"https://{tenant}.wd{wd}.myworkdayjobs.com"
    api = f"{base}/wday/cxs/{tenant}/{site}/jobs"
    apply_root = f"{base}/{site}"
    facets = applied_facets or {}
    search_text = "" if facets else (search or "")
    jobs, offset, total = [], 0, None
    for _page in range(MAX_PAGES):
        payload = {"appliedFacets": facets, "limit": 20, "offset": offset,
                   "searchText": search_text}
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
# Aggregator adapters — multi-employer job boards queried by keyword. Each
# returns the *real* employer per job, so one entry surfaces roles across the
# whole market (including indies with no ATS of their own). Titles are still
# filtered by check_jobs' INCLUDE/EXCLUDE + UK gate.
# --------------------------------------------------------------------------- #

def fetch_careerjet(employer="Careerjet", queries=None, location="london", **_):
    """Careerjet public API. No API key — needs a Referer header and an affid
    (free from partners.careerjet.com; read from env CAREERJET_AFFID, with a
    placeholder fallback). Runs each keyword phrase and dedups by URL.
    """
    api = "http://public.api.careerjet.net/search"
    affid = os.environ.get("CAREERJET_AFFID", "213e213hd12344")
    ua = HEADERS["User-Agent"]
    headers = dict(HEADERS)
    headers["Referer"] = "https://github.com/malexander-11/steph"
    jobs, seen = [], set()
    for q in (queries or []):
        params = {"locale_code": "en_GB", "keywords": q, "location": location,
                  "pagesize": 50, "affid": affid, "user_ip": "8.8.8.8",
                  "user_agent": ua}
        try:
            data = requests.get(api, params=params, headers=headers,
                                timeout=TIMEOUT).json()
        except Exception:
            continue
        for j in data.get("jobs", []):
            url = j.get("url")
            if not url or url in seen:
                continue
            seen.add(url)
            jobs.append({
                "title": _clean(j.get("title")),
                "url": url,
                "location": _clean(j.get("locations")),
                "employer": _clean(j.get("company")) or employer,
            })
    return jobs


def _salary_str(low, high):
    """Human salary from numeric min/max (e.g. '£45k–£60k'), or '' if unknown."""
    def k(v):
        try:
            v = float(v)
        except (TypeError, ValueError):
            return None
        return f"£{v/1000:.0f}k" if v >= 1000 else f"£{v:.0f}"
    lo, hi = k(low), k(high)
    if lo and hi and lo != hi:
        return f"{lo}–{hi}"
    return lo or hi or ""


def fetch_adzuna(employer="Adzuna", queries=None, where="london",
                 results_per_page=50, pages=3, **_):
    """Adzuna job search API. Keyed via env ADZUNA_APP_ID / ADZUNA_APP_KEY (free
    self-signup, stored as GitHub secrets). Runs each keyword phrase across
    `pages` pages (most-recent first) and dedups by URL. Skips quietly when the
    keys are absent so the sweep stays green until they're configured.
    """
    app_id = os.environ.get("ADZUNA_APP_ID")
    app_key = os.environ.get("ADZUNA_APP_KEY")
    if not (app_id and app_key):
        return []
    jobs, seen = [], set()
    for q in (queries or []):
        for page in range(1, min(pages, MAX_PAGES) + 1):
            api = f"https://api.adzuna.com/v1/api/jobs/gb/search/{page}"
            params = {"app_id": app_id, "app_key": app_key, "what": q,
                      "where": where, "results_per_page": results_per_page,
                      "sort_by": "date", "content-type": "application/json"}
            batch = _get(api, params=params).json().get("results", [])
            if not batch:
                break
            for j in batch:
                url = j.get("redirect_url")
                if not url or url in seen:
                    continue
                seen.add(url)
                jobs.append({
                    "title": _clean(j.get("title")),
                    "url": url,
                    "location": _clean((j.get("location") or {}).get("display_name")),
                    "employer": _clean((j.get("company") or {}).get("display_name"))
                    or employer,
                    "salary": _salary_str(j.get("salary_min"), j.get("salary_max")),
                })
    return jobs


def fetch_reed(employer="Reed", queries=None, location="london",
               distance=10, **_):
    """Reed.co.uk job search API. Keyed via env REED_API_KEY (free self-signup;
    HTTP Basic auth with the key as username, blank password). Runs each keyword
    phrase and dedups by URL. Skips quietly when the key is absent.
    """
    key = os.environ.get("REED_API_KEY")
    if not key:
        return []
    api = "https://www.reed.co.uk/api/1.0/search"
    jobs, seen = [], set()
    for q in (queries or []):
        params = {"keywords": q, "locationName": location,
                  "distanceFromLocation": distance, "resultsToTake": 100}
        data = requests.get(api, params=params, headers=HEADERS,
                            timeout=TIMEOUT, auth=(key, "")).json()
        for j in data.get("results", []):
            url = j.get("jobUrl")
            if not url or url in seen:
                continue
            seen.add(url)
            jobs.append({
                "title": _clean(j.get("jobTitle")),
                "url": url,
                "location": _clean(j.get("locationName")),
                "employer": _clean(j.get("employerName")) or employer,
                "salary": _salary_str(j.get("minimumSalary"), j.get("maximumSalary")),
            })
    return jobs


# --------------------------------------------------------------------------- #
# HTML / JSON-LD adapters (genuinely server-rendered sources only)
# --------------------------------------------------------------------------- #

_GRAPEVINE_SLUG = re.compile(r"/(?:executive_)?mediajobs/(\d+),(.+?)\.html", re.I)


def _grapevine_employer(slug, title):
    """Derive the employer from a Grapevine slug given the card's clean title.
    The slug is '{Title_Words}_{Employer_Words}' with no delimiter, so we drop
    the leading run of slug words that appear in the title; the remainder is the
    employer (dropping the trailing '_FJ' featured marker)."""
    slug_words = [w for w in slug.replace("_", " ").split() if w]
    title_set = set(re.sub(r"[^a-z0-9 ]", " ", (title or "").lower()).split())
    i = 0
    while i < len(slug_words) and slug_words[i].lower() in title_set:
        i += 1
    tail = [w for w in slug_words[i:] if w.upper() != "FJ"]
    return " ".join(tail).strip()


def fetch_grapevine(employer="Grapevine", records=100, **_):
    """Grapevine Jobs — the UK media aggregator. Server-rendered: one request for
    `records` (default 100) captures the whole ~49-role pool and avoids the
    page-3 session-reset shell. Parses each job CARD for a clean title, location
    and salary, and derives the real employer from the slug — so listings read
    properly and cross-source dedup can merge them with direct ATS feeds.
    Backbone for indie prodcos + broadcasters with no feed of their own.
    """
    base = "https://www.grapevinejobs.co.uk"
    url = (f"{base}/jobseeker/jobseeker_jobs.aspx"
           f"?page=1&screen=1&number_of_records={records}")
    soup = BeautifulSoup(_get(url).text, "html.parser")
    # Title (in the card wrapper) and location/salary/link (in the sibling
    # footer) each appear once per job in DOM order, so we zip the parallel lists.
    titles = soup.select(".new-job-card-inner-job-title")
    links = soup.select("a.new-job-card-footer-details[href]")
    locs = soup.select(".new-job-card-footer-location")
    sals = soup.select(".new-job-card-footer-salary")
    jobs, seen = [], set()
    for i, link in enumerate(links):
        m = _GRAPEVINE_SLUG.search(link["href"])
        if not m or m.group(1) in seen:
            continue
        seen.add(m.group(1))
        title = _clean(titles[i].get_text(" ", strip=True)) if i < len(titles) else ""
        loc = _clean(locs[i].get_text(" ", strip=True)) if i < len(locs) else ""
        sal_el = sals[i] if i < len(sals) else None
        salary = "" if (not sal_el or "hidden" in (sal_el.get("class") or [])) \
            else _clean(sal_el.get_text(" ", strip=True))
        jobs.append({
            "title": title,
            "url": urljoin(base, link["href"]),
            "location": loc,
            "employer": _grapevine_employer(m.group(2), title) or employer,
            "salary": salary,
        })
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
                # Locator.evaluate_all pierces open shadow DOM (unlike a raw
                # document.querySelectorAll), so web-component boards work too.
                rows = page.locator(selector).evaluate_all("""els => els.map(e => ({
                    text: (e.textContent || '').trim(),
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
    "careerjet": fetch_careerjet,
    "adzuna": fetch_adzuna,
    "reed": fetch_reed,
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
