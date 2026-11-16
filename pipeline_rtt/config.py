"""
Configuration for the RTT (Referral to Treatment) waiting-times pipeline.

Sibling to the cancer-waiting-times pipeline (pipeline/). Single source of truth
for: where the data lives, the standard/milestones, the series break, and the
national reconciliation gate that guards every build.

Data model (settled 2026-06-19, see DECISIONS.md):
  * Source = the monthly "Full CSV data file" (one CSV inside a ZIP, ~85MB),
    the RTT analogue of cancer's "Monthly Combined CSV". One file PER MONTH.
  * Grain = Provider x Commissioner x RTT-Part-Type x Treatment-Function, with
    105 one-week wait-band columns. We use ONLY the Incomplete pathway type
    (Part_2) and the all-treatment-function total row (C_999) this increment.
  * Every headline metric is derived from the wait-band columns; the Total /
    unknown-clock-start summary columns are blank in the Full CSV.
"""

# Per-financial-year sub-pages are scraped for the "Full CSV data file" zips.
SOURCE_INDEX = "https://www.england.nhs.uk/statistics/statistical-work-areas/rtt-waiting-times/"
SOURCE_FY_PAGES = [
    "https://www.england.nhs.uk/statistics/statistical-work-areas/rtt-waiting-times/rtt-data-{fy}/"
]

# The 18-week (92%) incomplete-pathway standard is still the live NHS
# Constitution standard. Recovery-trajectory milestones (planning guidance /
# elective-reform plan) are distinct from the flat 92% line and rendered as
# labelled reference lines on the percentage chart.
STANDARD_TARGET = 0.92
RECOVERY_MILESTONES = [
    {"target": 0.65, "by": "2026-03", "label": "65% by Mar 2026"},
    {"target": 0.70, "by": "2027-03", "label": "70% by Mar 2027"},
    {"target": 0.92, "by": "2029-03", "label": "92% standard by Mar 2029"},
]

# Series break: from February 2024 community-service pathways left RTT for the
# Community Health Services waiting-list collection. The analogue of cancer's
# Oct-2023 break -- pre/post are not strictly comparable. Front end shows the
# in-chart marker ONLY when the visible window includes pre-break data.
SERIES_BREAK = "2024-02"

# The three measures the dashboard exposes (the measure toggle). pct18 is a
# rate (0-1) on the percentage chart with the target line; the other two are
# absolute counts on the auto-scaling numeric chart with no target.
MEASURES = {
    "pct18":    {"label": "% within 18 weeks", "kind": "rate"},
    "waitlist": {"label": "Waiting-list size", "kind": "count"},
    "longwait": {"label": "Long waiters (52/65/78/104+ wks)", "kind": "count"},
}

# Long-waiter thresholds (weeks). w104 is the single open-ended ">104 weeks"
# band; the others are cumulative sums of every band at/above the threshold.
LONGWAIT_THRESHOLDS = [52, 65, 78, 104]

# --- National reconciliation gate (fail-loud) -------------------------------
# After building, the national (England, excl NONC) Part_2 aggregate for the
# gate month MUST match the published Statistical Press Notice headline within
# tolerance, else the build raises. Verified against the Apr-2025 SPN; the small
# count tolerance absorbs original-vs-revised-extract vintage drift.
RECON_GATE = {
    "month": "2025-04",
    "pct18": 0.597,          # SPN: 59.7%
    "pct18_tol": 0.002,      # +/- 0.2 percentage points
    "waitlist": 7_400_000,   # SPN: 7.4 million
    "waitlist_tol": 100_000,
    "w52": 190_068,
    "w65": 9_258,
    "w78": 1_361,
    "w104": 171,
    "count_rel_tol": 0.01,   # +/- 1% on the long-waiter counts
}

# Non-English-commissioned rows: present only in the Full CSV, excluded from
# every NHS England headline. Identified by commissioner org/parent code.
NONC_MARKER = "NONC"

# Reliability threshold for the percentage measure (shared with cancer): a
# month whose denominator is below this is low-reliability (flagged, not hidden).
RELIABILITY_THRESHOLD = 10

# Picker hiding (selection-only, like cancer): a provider whose maximum monthly
# waiting list over the last N months stays below this is negligible and hidden
# from the picker, but kept in the store and reachable by ?org=. ICBs never hide.
PICKER_PROVIDER_WINDOW_MONTHS = 12
PICKER_MIN_PROVIDER_WAITLIST = 100

# An org whose data series FIRST appears within this many months AND is CURRENT per
# ODS is "young" (newly created, e.g. a just-merged ICB) — checked first and
# protected from the hiding rule so it shows from month 1. See pipeline_common/ods.py.
YOUNG_WINDOW_MONTHS = 12

# Where things live.
RAW_DIR = "data_rtt/raw"
SITE_DATA_DIR = "site/rtt/data"
MANIFEST_PATH = "data_rtt/manifest.json"
# The RTT source has no provider region; we reuse the region the cancer dashboard
# already derived (Parent_Org) by reading its committed index, keyed by org code.
# Fail-open: absent/unreadable -> no regions populated this build (see regions.py).
CANCER_INDEX_PATH = "site/cancer/data/index.json"

# Full month name -> number, for parsing the "RTT-March-2026" Period field.
MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
}
