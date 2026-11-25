"""
A&E source discovery + incremental fetch.

Scrapes the per-financial-year "A&E Attendances and Emergency Admissions" pages for the
monthly CSV links and the System Mapping File, and the index page for the Monthly A&E
Time Series workbook. Months are read from the LINK TEXT, because NHS England's file
names follow no pattern. A month whose URL changed since the last run (NHS re-uploads
revised months under a new name) is fetched again.

Network note: england.nhs.uk blocks some servers (GitHub Actions since 2026-09); the
parsing functions take HTML strings so they can be unit-tested offline.
"""
import datetime as dt
import html as htmllib
import json
import os
import re
import time
from urllib.parse import urljoin

from . import config
from pipeline_common import retry

UA = {"User-Agent": "ae-dashboard/1.0"}


def financial_years(today=None, start_year=config.FIRST_FY_START):
    """FY slugs ('2022-23' …) up to the current financial year."""
    today = today or dt.date.today()
    cur = today.year if today.month >= 4 else today.year - 1
    return [f"{y}-{str(y + 1)[-2:]}" for y in range(start_year, cur + 1)]


def fy_page_url(fy):
    return config.SOURCE_FY_PAGE.format(fy=fy)


def fetch(url, timeout=60):
    import requests
    r = requests.get(url, timeout=timeout, headers=UA)
    r.raise_for_status()
    return r


def _links(html):
    """[(link text, href)] for every <a href> in an HTML string (text de-tagged, spaces collapsed)."""
    out = []
    for href, inner in re.findall(r'<a\s[^>]*?href="([^"]+)"[^>]*>(.*?)</a>', html, re.I | re.S):
        text = re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", "", inner))).strip()
        out.append((text, htmllib.unescape(href)))
    return out


def discover_months(htmls, base_url=config.SOURCE_INDEX):
    """{month 'YYYY-MM': csv url} from FY page HTML. A later page wins for a month."""
    out = {}
    for html in htmls:
        for text, href in _links(html):
            m = re.match(config.MONTHLY_CSV_TEXT, text, re.I)
            if not m or not href.lower().split("?")[0].endswith(".csv"):
                continue
            mon = config.MONTHS.get(m.group(1).lower())
            if mon:
                out[f"{m.group(2)}-{mon:02d}"] = urljoin(base_url, href)
    return out


def discover_mapping(html, base_url=config.SOURCE_INDEX):
    """The System Mapping File link on one FY page, or None."""
    for text, href in _links(html):
        if re.search(config.SYSTEM_MAPPING_TEXT, text, re.I) and re.search(r"\.xlsx?$", href, re.I):
            return urljoin(base_url, href)
    return None


def discover_time_series(html, base_url=config.SOURCE_INDEX):
    """The Monthly A&E Time Series workbook link on the index page, or None."""
    for text, href in _links(html):
        if re.search(config.TIME_SERIES_TEXT, text, re.I) and re.search(r"\.xlsx?$", href, re.I):
            return urljoin(base_url, href)
    return None


def scrape(fys):
    """Fetch every FY page. A page that fails is WARNed and skipped."""
    pages = {}
    for fy in fys:
        try:
            pages[fy] = fetch(fy_page_url(fy)).text
        except Exception as e:
            print(f"  WARN: {fy} page fetch failed ({e})")
    return pages


def scrape_with_retry(fys, attempts=retry.DEFAULT_ATTEMPTS,
                      backoff_seconds=retry.DEFAULT_BACKOFF_SECONDS, sleep_fn=time.sleep):
    """`scrape`, retried while it finds no monthly CSV links at all (the transient
    empty-page response NHS's CDN gave on 2026-09-16/17, see the RTT pipeline)."""
    def attempt():
        pages = scrape(fys)
        return pages if discover_months(pages.values()) else {}
    return retry.retry_until_nonempty(attempt, attempts=attempts, backoff_seconds=backoff_seconds,
                                      sleep_fn=sleep_fn)


def load_manifest(path=config.MANIFEST_PATH):
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {"months": {}, "mapping": {}}


def save_manifest(manifest, path=config.MANIFEST_PATH):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)


def select_to_fetch(links, seen, folder, ext):
    """Keys whose file is missing locally OR whose URL changed (a revision)."""
    return {k: u for k, u in links.items()
            if not os.path.exists(os.path.join(folder, k + ext)) or seen.get(k) != u}
