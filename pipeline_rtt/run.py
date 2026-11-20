"""
RTT pipeline entry point.

  python -m pipeline_rtt.run            # scrape NHS RTT FY pages, fetch any new/
                                        # revised months, then rebuild site/rtt/data

The build is the source of truth for correctness: it runs BOTH fail-loud gates
(national SPN headline + sum-of-treatment-functions == C_999 total) before it
writes a single file, so a bad fetch or a definitional change fails the run
rather than publishing stale/partial data.

The raw monthly zips are gitignored and not persisted between CI runs, so a CI
checkout re-fetches every month (always picking up NHS's latest revisions);
local re-runs skip months whose URL is unchanged.
"""
import os
import sys

from . import config, discover, build
from pipeline_common import ods


def run_real():
    import requests
    fys = discover.financial_years()
    print(f"Scraping {len(fys)} FY pages: {', '.join(fys)}")
    links = discover.discover_links_with_retry(fys)
    if not links:
        raise RuntimeError("discovered no Full-CSV links — refusing to build")
    print(f"Discovered {len(links)} monthly Full-CSV files "
          f"({min(links)}..{max(links)})")

    manifest = discover.load_manifest()
    todo = discover.select_to_fetch(links, manifest)
    os.makedirs(config.RAW_DIR, exist_ok=True)
    print(f"{len(todo)} month(s) to fetch ({len(links) - len(todo)} already current)")
    for month in sorted(todo):
        url = todo[month]
        path = os.path.join(config.RAW_DIR, month + ".zip")
        print(f"  fetching {month}: {url}")
        with requests.get(url, stream=True, timeout=300,
                          headers={"User-Agent": "rtt-dashboard/1.0"}) as r:
            r.raise_for_status()
            with open(path, "wb") as f:
                for chunk in r.iter_content(1 << 16):
                    f.write(chunk)
        manifest.setdefault("months", {})[month] = url
    # Only persist the manifest when we actually fetched something. save_manifest
    # stamps last_checked, so writing it every run would churn data_rtt/manifest.json
    # and reintroduce the daily no-op "Auto update CWT data" commit the CI hardening
    # removed (the no-op-commit guard only excludes the meta.json files).
    if todo:
        discover.save_manifest(manifest)

    # NHS England's England-level figures WITH estimates for non-submitting trusts.
    # Required: the build reconciles our reported England figures against NHS's
    # reported columns in this file and publishes the estimated ones beside them.
    ov = discover.discover_overview(getattr(discover.scrape_all_links, "last_htmls", []))
    if not ov:
        raise RuntimeError("no 'RTT Overview Timeseries Including Estimates for Missing Trusts' "
                           "link found on the RTT FY pages — refusing to build without it")
    print(f"  fetching overview (estimates) {ov[0]}: {ov[1]}")
    os.makedirs(os.path.dirname(config.OVERVIEW_PATH), exist_ok=True)
    r = requests.get(ov[1], timeout=120, headers={"User-Agent": "rtt-dashboard/1.0"})
    r.raise_for_status()
    with open(config.OVERVIEW_PATH, "wb") as f:
        f.write(r.content)

    # Shared ODS org-status classification (current/former + succession links) +
    # NHS-trust code set for the provider-type filter; fail-soft to the last-known
    # committed cache on any ODS outage (never raises).
    ods_data = ods.refresh_or_cache()
    if not ods_data.get("nhs_trust_codes"):
        raise RuntimeError("ODS classification returned no NHS-trust codes (live fetch AND committed "
                           "cache both empty) — refusing to build a provider-type split that would "
                           "mis-show every provider under the NHS-Trusts default.")
    meta = build.run(classification=ods_data["orgs"],          # runs all gates, then writes site/rtt/data
                     trust_codes=set(ods_data.get("nhs_trust_codes") or []),
                     estimates_path=config.OVERVIEW_PATH)
    print(f"Rebuilt RTT site data: {meta['counts']} | "
          f"recon={meta['reconciliation'].get('checked')} "
          f"tf_recon_maxΔ={meta['tf_reconciliation']['max_abs_delta']}")
    return meta


if __name__ == "__main__":
    try:
        run_real()
    except Exception as e:
        print(f"RTT run failed ({e}).", file=sys.stderr)
        sys.exit(1)
