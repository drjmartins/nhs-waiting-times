"""
A&E pipeline entry point.

  python -m pipeline_ae.run             # scrape NHS England's A&E pages, fetch new or revised
                                        # months, mapping files and the time series, then
                                        # rebuild site/ae/data
  python -m pipeline_ae.run --no-fetch  # rebuild from the files already in data_ae/raw

The build runs all three fail-loud gates (TOTAL row, NHS England time series, ICB sum)
before it writes a single file. The raw files are small (~1.6MB for four years) but are
not committed, like RTT's; the derived site/ae/data IS committed, so the publish
workflow ships it as-is.
"""
import json
import os
import sys

from . import config, discover, build
from pipeline_common import ods


def _save(url, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(discover.fetch(url, timeout=120).content)


def fetch_all():
    fys = discover.financial_years()
    print(f"Scraping {len(fys)} A&E FY pages: {', '.join(fys)}")
    pages = discover.scrape_with_retry(fys)
    links = discover.discover_months(pages.values())
    if not links:
        raise RuntimeError("discovered no monthly A&E CSV links — refusing to build")
    print(f"Discovered {len(links)} monthly CSVs ({min(links)}..{max(links)})")
    manifest = discover.load_manifest()
    before = json.loads(json.dumps(manifest))

    todo = discover.select_to_fetch(links, manifest.get("months", {}), config.RAW_DIR, ".csv")
    print(f"{len(todo)} month(s) to fetch ({len(links) - len(todo)} already current)")
    for month in sorted(todo):
        print(f"  fetching {month}: {todo[month]}")
        _save(todo[month], os.path.join(config.RAW_DIR, month + ".csv"))
        manifest.setdefault("months", {})[month] = todo[month]

    maps = {fy: u for fy, u in ((fy, discover.discover_mapping(h)) for fy, h in pages.items()) if u}
    # System Mapping Files exist from 2022-23 (ICBs began July 2022); earlier pages have none.
    missing = [fy for fy in fys if fy in pages and fy >= config.FIRST_ICB_FY and fy not in maps]
    if missing:
        raise RuntimeError(f"no System Mapping File link on the {missing} page(s) — refusing to build")
    for fy, url in sorted(maps.items()):
        ext = os.path.splitext(url)[1].lower()
        path = os.path.join(config.MAPPING_DIR, fy + ext)
        if not os.path.exists(path) or manifest.get("mapping", {}).get(fy) != url:
            print(f"  fetching mapping {fy}: {url}")
            _save(url, path)
            manifest.setdefault("mapping", {})[fy] = url

    # NHS England's England-level time series: always re-fetched (it is revised with the
    # monthly release); the build reconciles every month against it.
    ts = discover.discover_time_series(discover.fetch(config.SOURCE_INDEX).text)
    if not ts:
        raise RuntimeError("no 'Monthly A&E Time Series' link on the A&E index page — refusing to build")
    print(f"  fetching time series: {ts}")
    _save(ts, config.TIME_SERIES_PATH)
    # Persist the manifest only when something new was fetched, so a no-change run
    # makes no commit (same rule as RTT).
    if manifest != before:
        discover.save_manifest(manifest)


def run(fetch=True):
    if fetch:
        fetch_all()
    ods_data = ods.refresh_or_cache() if fetch else ods.load()
    if not ods_data.get("nhs_trust_codes"):
        raise RuntimeError("ODS classification has no NHS-trust codes — refusing to build a "
                           "provider-type split that would mis-show every provider.")
    meta = build.run(classification=ods_data["orgs"],
                     trust_codes=set(ods_data.get("nhs_trust_codes") or []))
    print(f"Rebuilt A&E site data: {meta['counts']} | time-series gate {meta['reconciliation']}")
    return meta


if __name__ == "__main__":
    try:
        run(fetch="--no-fetch" not in sys.argv[1:])
    except Exception as e:
        print(f"A&E run failed ({e}).", file=sys.stderr)
        sys.exit(1)
