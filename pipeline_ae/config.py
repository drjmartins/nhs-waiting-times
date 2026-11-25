"""
Configuration for the A&E (Accident and Emergency) waiting-times pipeline.

Sibling to the cancer (pipeline/) and RTT (pipeline_rtt/) pipelines. Single source of
truth for where the data lives, the 4-hour standard and planning milestones, and the
fail-loud reconciliation gates that guard every build.

Data model (DECISIONS 2026-11-25):
  * Source = NHS England's "Monthly A&E Attendances and Emergency Admissions" CSV, one
    file PER MONTH (~190 rows, ~30KB), listed on one page per financial year. File names
    are inconsistent (e.g. 'October-2026-CSV-WD35qg.csv', 'Monthly-AE-Nov-25-CSV-revised.csv'),
    so months are found from the LINK TEXT ("Monthly A&E October 2026 (CSV, 28KB)").
  * Grain = one row per reporting organisation (NHS trusts, plus GP practices, urgent
    treatment centres and other non-trust providers of Type 3 services), with a TOTAL row.
  * 4-hour performance = attendances (including booked appointments) NOT over 4 hours,
    divided by all attendances; "all types" and "Type 1" (major emergency departments),
    exactly as NHS England's Monthly A&E Time Series computes them.
  * ICB figures: providers summed by the ICB in NHS England's "System Mapping File" for
    that financial year (DECISIONS 2026-11-25).
"""

SOURCE_INDEX = "https://www.england.nhs.uk/statistics/statistical-work-areas/ae-waiting-times-and-activity/"
SOURCE_FY_PAGE = SOURCE_INDEX + "ae-attendances-and-emergency-admissions-{fy}/"
FIRST_FY_START = 2022            # April 2022 onwards, matching the RTT dashboard

# Link-text patterns (case-insensitive) on the FY pages / index page.
MONTHLY_CSV_TEXT = r"^Monthly A&E\s+([A-Za-z]+)\s+(\d{4})\b.*\bCSV\b"
SYSTEM_MAPPING_TEXT = r"System Mapping File"
TIME_SERIES_TEXT = r"^Monthly A&E Time Series\b"

# --- The 4-hour standard and milestones --------------------------------------------
# 95% is the NHS Constitution standard. Planning milestones are drawn as labelled lines
# on the England chart, like RTT's recovery milestones:
#   78% by March 2026 (2025/26 planning guidance), 82% by March 2027 and 85% in 2028/29
#   (Medium Term Planning Framework, NHS England, 24 October 2025).
STANDARD_TARGET = 0.95
MILESTONES = [
    {"target": 0.78, "by": "2026-03", "label": "78% by Mar 2026"},
    {"target": 0.82, "by": "2027-03", "label": "82% by Mar 2027"},
    {"target": 0.85, "by": "2029-03", "label": "85% by Mar 2029"},
]

# The three measures (cards). pct4 is a rate (0-1) with a target; the others are counts.
MEASURES = {
    "pct4":    {"label": "% within 4 hours", "kind": "rate"},
    "attend":  {"label": "Attendances", "kind": "count"},
    "dta":     {"label": "Waits from decision to admit (4+ / 12+ hours)", "kind": "count"},
}
# Department-type selector (the A&E analogue of RTT's treatment function).
DEPTS = {"all": "All A&E types", "t1": "Type 1 (major emergency departments)"}

# --- Source columns (fail loud if NHS renames any) ---------------------------------
COLS = {
    "att_t1": "A&E attendances Type 1",
    "att_t2": "A&E attendances Type 2",
    "att_t3": "A&E attendances Other A&E Department",
    "batt_t1": "A&E attendances Booked Appointments Type 1",
    "batt_t2": "A&E attendances Booked Appointments Type 2",
    "batt_t3": "A&E attendances Booked Appointments Other Department",
    "o4_t1": "Attendances over 4hrs Type 1",
    "o4_t2": "Attendances over 4hrs Type 2",
    "o4_t3": "Attendances over 4hrs Other Department",
    "bo4_t1": "Attendances over 4hrs Booked Appointments Type 1",
    "bo4_t2": "Attendances over 4hrs Booked Appointments Type 2",
    "bo4_t3": "Attendances over 4hrs Booked Appointments Other Department",
    "dta_4_12": "Patients who have waited 4-12 hs from DTA to admission",
    "dta_12": "Patients who have waited 12+ hrs from DTA to admission",
    "adm_t1": "Emergency admissions via A&E - Type 1",
    "adm_t2": "Emergency admissions via A&E - Type 2",
    "adm_t3": "Emergency admissions via A&E - Other A&E department",
    "adm_other": "Other emergency admissions",
}
ID_COLS = {"period": "Period", "code": "Org Code", "parent": "Parent Org", "name": "Org name"}

# NHS region from the CSV's Parent Org ("NHS ENGLAND LONDON" -> "London").
REGIONS = {
    "LONDON": "London", "SOUTH EAST": "South East", "SOUTH WEST": "South West",
    "MIDLANDS": "Midlands", "EAST OF ENGLAND": "East of England",
    "NORTH WEST": "North West", "NORTH EAST AND YORKSHIRE": "North East and Yorkshire",
}

# --- Reconciliation gates (fail-loud) ----------------------------------------------
# 1. Every month: the provider rows must sum EXACTLY to the file's own TOTAL row.
# 2. Every month in both: our England totals must EXACTLY equal NHS England's Monthly
#    A&E Time Series (attendances, over-4-hour counts, all types and Type 1, and both
#    decision-to-admit counts). The workbook is downloaded on every run.
# 3. Every month: ICBs + providers with no ICB in the mapping must sum EXACTLY to
#    England, and the unmapped share must stay below UNMAPPED_MAX_SHARE of attendances.
TIME_SERIES_PATH = "data_ae/raw/time_series.xls"
UNMAPPED_MAX_SHARE = 0.01

# Reliability threshold for the percentage (shared with cancer/RTT): a month whose
# denominator is below this is low-reliability (flagged, not hidden).
RELIABILITY_THRESHOLD = 10

# Picker hiding: a provider whose largest monthly attendances over the last N months
# stay below this is hidden from the picker (kept in the data, reachable by ?org=).
PICKER_PROVIDER_WINDOW_MONTHS = 12
PICKER_MIN_PROVIDER_ATTENDANCES = 100
YOUNG_WINDOW_MONTHS = 12

# Where things live.
RAW_DIR = "data_ae/raw"
MAPPING_DIR = "data_ae/raw/mapping"
SITE_DATA_DIR = "site/ae/data"
MANIFEST_PATH = "data_ae/manifest.json"

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
}
