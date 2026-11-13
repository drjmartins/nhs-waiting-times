"""Data-freshness check for both dashboards (the "is the refresh still running?" watchdog).

Since 2026-11-12 the NHS fetch runs on a local Windows machine (scripts/scheduled_refresh.ps1).
That script reports its own failures, but if the machine stops running it altogether nothing
fails, so nothing is reported. This check closes that gap from the DATA side: it only reads the
committed meta.json files, never contacts NHS, so it runs fine on GitHub Actions.

Rule (one place; site/assets/freshness.js mirrors it for the on-page note):
  NHS England publishes a month's cancer and RTT figures roughly 6 weeks after that month
  ends (September 2026 landed on 12 Nov 2026). A dashboard is STALE once more than
  GRACE_DAYS (12 weeks) have passed since the end of its latest data month, i.e. a monthly
  release has very likely been missed. e.g. latest = 2026-09 -> stale from 2026-12-24.

Usage:  python -m pipeline_common.freshness     # exit 1 if any dashboard is stale
"""
import datetime as dt
import json
import os
import sys

GRACE_DAYS = 84   # 12 weeks; keep in step with GRACE_DAYS in site/assets/freshness.js

META_PATHS = {
    "Cancer": "site/cancer/data/meta.json",
    "RTT": "site/rtt/data/meta.json",
}


def month_end(ym):
    """'2026-09' -> date(2026, 9, 30)."""
    y, m = (int(x) for x in ym.split("-"))
    first_of_next = dt.date(y + (m == 12), m % 12 + 1, 1)
    return first_of_next - dt.timedelta(days=1)


def stale_from(ym):
    """First date on which data whose latest month is `ym` counts as stale."""
    return month_end(ym) + dt.timedelta(days=GRACE_DAYS + 1)


def is_stale(ym, today=None):
    return (today or dt.date.today()) >= stale_from(ym)


def check(meta_paths=META_PATHS, today=None):
    """[(name, latest_month, stale_from_date, stale), ...] for each dashboard."""
    out = []
    for name, path in meta_paths.items():
        with open(path) as f:
            months = json.load(f).get("months") or []
        if not months:
            raise ValueError(f"{path} has no months — cannot judge freshness")
        latest = months[-1]
        out.append((name, latest, stale_from(latest), is_stale(latest, today)))
    return out


def main():
    results = check()
    for name, latest, due, stale in results:
        state = "STALE" if stale else "ok"
        print(f"{name}: latest data month {latest}; stale from {due.isoformat()} -> {state}")
    if any(r[3] for r in results):
        print("\nAt least one dashboard looks out of date: the local scheduled refresh "
              "(scripts/scheduled_refresh.ps1) has probably stopped running.")
        # Machine-readable summary for the workflow's issue body.
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a") as f:
                for name, latest, due, stale in results:
                    f.write(f"- **{name}**: latest month {latest}, stale from {due} "
                            f"({'STALE' if stale else 'ok'})\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
