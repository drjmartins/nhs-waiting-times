"""
Configuration for the Cancer Waiting Times data pipeline.

Single source of truth for: where the data lives, the standards we track,
and the canonical schema we normalise every source file into.
"""

# NHS England publishes the combined CSVs (one per financial year) on this page.
# The pipeline scrapes it for links rather than hard-coding file URLs, because
# the URLs embed upload dates that change every release.
SOURCE_PAGE = "https://www.england.nhs.uk/statistics/statistical-work-areas/cancer-waiting-times/"

# We only want the "Monthly Combined CSV" links (provider + commissioner in one
# table). These substrings identify them in the page's anchor text.
COMBINED_CSV_MARKERS = ("Monthly Combined CSV",)

# The main SOURCE_PAGE hosts only the per-FY CUMULATIVE Combined CSVs. At a
# financial-year boundary the new year's data appears first as per-MONTH Combined
# CSVs on a separate FY sub-page (e.g. April 2026 lived here weeks before any
# 2026-27 cumulative file existed on the main page), so a main-page-only scrape
# trails until NHS posts the cumulative file. We also scrape the CURRENT FY's
# sub-page for those per-month files. The per-month file is column-for-column the
# same as the cumulative EXCEPT it omits the leading `Period` column (the month is
# implicit in the filename) — normalise injects it from the filename, guarded.
SOURCE_FY_PAGE_TEMPLATE = "https://www.england.nhs.uk/statistics/{fy}-monthly-cancer-waiting-times-statistics/"

# The October 2023 standards change means 31-day and 62-day figures before this
# month are NOT comparable to those after. The dashboard defaults to this start
# date and treats earlier data as a separate, clearly-labelled historic series.
COMPARABILITY_BREAK = "2023-10"

# The three headline standards plus the two "referral seen" measures, keyed by a
# short internal code. `target` is the operational standard (proportion) used as
# the default in the size-of-the-prize calculator; None where no % target applies.
STANDARDS = {
    "FDS28":  {"label": "28-day Faster Diagnosis Standard", "target": 0.75, "unit_days": 28},
    "CMB31":  {"label": "31-day combined (decision-to-treat to treatment)", "target": 0.96, "unit_days": 31},
    "CMB62":  {"label": "62-day combined (referral to treatment)", "target": 0.85, "unit_days": 62},
}

# Canonical tidy schema. Every source row, whatever its original layout, is
# normalised to exactly these columns. One row = one month x org x standard x
# breakdown-slice.
CANONICAL_COLUMNS = [
    "month",          # YYYY-MM (period the activity relates to)
    "org_level",      # 'national' | 'provider' | 'icb'
    "org_code",       # ODS code, e.g. 'RJ1'; 'ENG' for national
    "org_name",       # human-readable
    "region",         # NHS England region (from Parent_Org); 'England' if region-less
    "standard",       # one of STANDARDS keys
    "breakdown_type", # 'all' | 'cancer' | 'modality' | 'route'
    "breakdown_value",# e.g. 'All', 'Breast', 'Surgery', 'Screening'
    "within_target",  # count seen within the standard
    "total",          # denominator: total in scope
    "performance",    # within_target / total (0-1), computed
    "data_status",    # 'provisional' | 'final'
    "source_file",    # provenance: which CSV this row came from
]

# THE reliability threshold (single source of truth): a monthly figure with a
# denominator below this is "low reliability" (flagged on the charts), and it is the
# activity bar for the provider picker-hiding rule below.
RELIABILITY_THRESHOLD = 10

# "Recent finalised months" window used by the commissioner picker-hiding rule
# below: the most recent N FINALISED months, pooled by summing denominators.
RECENT_FINAL_WINDOW_MONTHS = 3

# --- Picker hiding (selection-only; see build_site_data._negligible_orgs) ------
# Negligible-activity orgs are hidden from the picker/default lists but kept in
# the store, the downloads, and reachable by direct ?org= link (and still render
# with the usual low-reliability flags). Computed DYNAMICALLY each build, so an
# org crossing the bar appears automatically and a dormant one drops. Rules,
# confirmed against the activity distribution (DECISIONS.md 2026-06-12):
#   * a PROVIDER is hidden if NO standard clears RELIABILITY_THRESHOLD (n>=10) in a
#     single month within the last PICKER_PROVIDER_WINDOW_MONTHS — i.e. negligible
#     RECENT activity, so it only produces empty/greyed charts now. The 12-month
#     window catches the genuinely dormant (incl. defunct/merged codes) while the
#     full-year requirement avoids reporting-lag false positives a 3-month window
#     would wrongly hide (an org active within the year but yet to report the latest
#     provisional months). Clean gap: dormant orgs sit at <=9 over the last year,
#     the next genuinely-active org at 22. Hides 58 providers.
#   * a COMMISSIONER is hidden if its recent-3-finalised-month pooled all-cancers
#     denominator (summed across standards) is below PICKER_MIN_COMMISSIONER_DENOM —
#     isolating the specialised/national commissioning hubs (<=869) from real ICBs
#     (>=11,737).
PICKER_PROVIDER_WINDOW_MONTHS = 12
PICKER_MIN_COMMISSIONER_DENOM = 2000

# --- Org lifecycle / ODS status (see pipeline_common/ods.py + DECISIONS 2026-06-22)
# An org whose data series FIRST appears within this many months AND is CURRENT per
# ODS is "young" — thin because it is newly created (e.g. a just-merged ICB), NOT
# dormant. Young orgs are checked FIRST and protected from the inactivity-hiding
# rule above, so a brand-new code shows from its first month (the existing
# "no year-ago data" note + n<10 flag handle the thin view). ODS status is what
# separates young (current) from dormant (former) — both can have minimal data.
YOUNG_WINDOW_MONTHS = 12

# Where things get written.
RAW_DIR = "data/raw"
PROCESSED_DIR = "data/processed"
SITE_DATA_DIR = "site/cancer/data"
MANIFEST_PATH = "data/manifest.json"
