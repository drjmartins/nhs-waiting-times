"""
RTT source discovery + incremental fetch.

Scrapes the per-financial-year RTT sub-pages for the monthly "Full CSV data
file" zip links, and downloads any that are new or revised since the last run
(per the manifest). NHS re-uploads revised months with a new URL, so a changed
URL for a month means "re-fetch".

Network note: england.nhs.uk may be unreachable in some sandboxes; the parsing
functions take HTML strings so they can be unit-tested offline.
"""
import datetime as dt
import json
import os
import re
import time
from urllib.parse import urljoin

try:
    import requests
except ImportError:
    requests = None

from . import config
from pipeline_common import retry

_ABBR = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
         "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}


def financial_years(today=None, start_year=2022):
    """FY slugs ('2022-23' …) from start_year up to the current financial year.
    The FY beginning in April N is labelled 'N-(N+1)'."""
    today = today or dt.date.today()
    cur_start = today.year if today.month >= 4 else today.year - 1
    return [f"{y}-{str(y + 1)[-2:]}" for y in range(start_year, cur_start + 1)]


def fy_page_url(fy):
    return config.SOURCE_FY_PAGES[0].format(fy=fy)


def fetch_page_html(url):
    if requests is None:
        raise RuntimeError("requests not installed")
    r = requests.get(url, timeout=60, headers={"User-Agent": "rtt-dashboard/1.0"})
    r.raise_for_status()
    return r.text


def discover_links(htmls, base_url=config.SOURCE_INDEX):
    """Parse Full-CSV zip links from one or more page HTML strings.

    Returns {month 'YYYY-MM': url}. A later page (newer FY) wins for a month, but
    months don't overlap across FY pages in practice."""
    out = {}
    for html in htmls:
        for href in re.findall(r'href="([^"]*Full-CSV-data-file[^"]*\.zip)"', html, re.I):
            m = re.search(r"Full-CSV-data-file-([A-Za-z]{3})(\d{2})", href, re.I)
            if not m:
                continue
            mon = _ABBR.get(m.group(1).lower())
            if not mon:
                continue
            month = f"20{int(m.group(2)):02d}-{mon:02d}"
            out[month] = urljoin(base_url, href)
    return out


def discover_overview(htmls, base_url=config.SOURCE_INDEX):
    """Latest "RTT Overview Timeseries Including Estimates for Missing Trusts" link.
    Returns (month 'YYYY-MM', url) or None. Filenames carry a MonYY token, e.g.
    'RTT-Overview-Timeseries-Including-Estimates-for-Missing-Trusts-Sep26-XLS-140K-ojyW5x.xlsx'."""
    best = None
    for html in htmls:
        for href in re.findall(r'href="([^"]*' + config.OVERVIEW_MARKER + r'[^"]*\.xlsx?)"', html, re.I):
            m = re.search(r"Missing-Trusts-([A-Za-z]{3})(\d{2})", href, re.I)
            mon = _ABBR.get(m.group(1).lower()) if m else None
            if not mon:
                continue
            month = f"20{int(m.group(2)):02d}-{mon:02d}"
            if best is None or month > best[0]:
                best = (month, urljoin(base_url, href))
    return best


def scrape_all_links(fys):
    """Fetch every FY page and combine their discovered Full-CSV links.

    A page that fails to fetch (network error, non-2xx) is WARNed and
    skipped — it doesn't stop the other pages' links from counting."""
    htmls = []
    for fy in fys:
        try:
            htmls.append(fetch_page_html(fy_page_url(fy)))
        except Exception as e:
            print(f"  WARN: {fy} page fetch failed ({e})")
    # Remember the pages so the overview workbook can be found without re-fetching.
    scrape_all_links.last_htmls = htmls
    return discover_links(htmls)


def discover_links_with_retry(fys, attempts=retry.DEFAULT_ATTEMPTS,
                               backoff_seconds=retry.DEFAULT_BACKOFF_SECONDS,
                               sleep_fn=time.sleep):
    """`scrape_all_links`, retried a bounded number of times if it comes back
    with zero links across every page. Rescues a transient empty-content
    response from NHS/CloudFront (all 5 FY pages returned 200 with none of
    the expected links on 2026-09-16/17) without masking a genuine, lasting
    change — if every attempt is still empty, the empty dict is returned
    as-is so run.py's fail-loud guard raises on it."""
    def on_retry(attempt, backoff):
        print(f"  Discovered 0 links on attempt {attempt}/{attempts} across "
              f"all {len(fys)} FY pages; retrying in {backoff:.0f}s "
              f"(possible transient NHS/CDN glitch)...")

    return retry.retry_until_nonempty(
        lambda: scrape_all_links(fys), attempts=attempts,
        backoff_seconds=backoff_seconds, sleep_fn=sleep_fn, on_retry=on_retry)


def load_manifest(path=config.MANIFEST_PATH):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {"months": {}, "last_checked": None}


def save_manifest(manifest, path=config.MANIFEST_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    manifest["last_checked"] = dt.datetime.utcnow().isoformat() + "Z"
    with open(path, "w") as f:
        json.dump(manifest, f, indent=2)


def select_to_fetch(links, manifest, raw_dir=config.RAW_DIR):
    """A month is fetched if its zip is missing locally OR its URL changed
    (a revision). In CI the checkout has no raw zips, so every month is fetched;
    locally an unchanged month is skipped."""
    seen = manifest.get("months", {})
    todo = {}
    for month, url in links.items():
        path = os.path.join(raw_dir, month + ".zip")
        if (not os.path.exists(path)) or seen.get(month) != url:
            todo[month] = url
    return todo
