"""
Normalisation: NHS source CSV -> canonical tidy schema.

The combined CSV is long-thin, but its layout has changed more than once. The
Nov-2025 format change was STRUCTURAL, not just renamed headers: the breakdown
is no longer a single (type, value) pair but is spread across three columns
(cancer type, referral route/stage, treatment modality), there is no explicit
org level (rows are Provider/Commissioner 'basis', with the all-England total
shipping as a pseudo-org 'Total'), and the period is DD/MM/YYYY.

We therefore:
  * map a set of *candidate* source column names onto each scalar canonical
    field (add new candidates here, in one place, when headers change);
  * DERIVE breakdown_type/value from the three-column layout when present,
    RETAINING every breakdown row (the site build narrows to 'all'; the by-
    cancer / by-modality rows are kept as raw material for later views);
  * derive org_level from basis + the 'Total' pseudo-org.

If a required scalar field can't be mapped, we raise a clear error naming the
file and the missing field, so a format change fails loudly in the pipeline
(and in CI) rather than silently producing wrong numbers.
"""
import pandas as pd

from . import config

# Candidate source-column names for each required scalar field. Add to these
# lists when NHS changes the layout; order = priority.
#
# `month` is handled SEPARATELY (not in this required loop) because the per-month
# new-FY Combined CSV omits the Period column entirely — its month is implicit in
# the filename and injected via `period_hint`. See normalise() and MONTH_CANDIDATES.
COLUMN_CANDIDATES = {
    "org_level":       ["ORG_LEVEL", "LEVEL", "ORG TYPE", "ORG_TYPE", "Basis"],
    "org_code":        ["ORG_CODE", "ORG CODE", "CODE", "PROVIDER_CODE", "ICB_CODE", "Org_Code"],
    "org_name":        ["ORG_NAME", "ORG NAME", "NAME", "PROVIDER_NAME", "ICB_NAME", "Org_Name"],
    "standard":        ["STANDARD", "MEASURE", "CWT_STANDARD", "Standard_or_Item"],
    "within_target":   ["WITHIN_STANDARD", "WITHIN_TARGET", "TREATED_WITHIN", "SEEN_WITHIN_STANDARD", "Within"],
    "total":           ["TOTAL", "TOTAL_TREATED", "TOTAL_SEEN", "DENOMINATOR", "Total"],
}

# The period/month column, when the source ships one (the cumulative Combined CSV
# and older vintages). Absent in the per-month new-FY file -> month is injected
# from the filename via period_hint instead.
MONTH_CANDIDATES = ["MONTH", "PERIOD", "Period", "Month", "REPORTING_PERIOD"]

# Breakdown handling supports two source shapes:
#   (a) an explicit, pre-collapsed (type, value) pair -- the test fixtures
#       and any future source that ships breakdown columns directly; or
#   (b) NHS England's post-Nov-2025 three-column layout, derived below.
BREAKDOWN_TYPE_CANDIDATES = ["BREAKDOWN", "BREAKDOWN_TYPE", "CATEGORY_TYPE"]
BREAKDOWN_VALUE_CANDIDATES = ["BREAKDOWN_VALUE", "CATEGORY", "STAGE", "CANCER_TYPE", "MODALITY", "ROUTE"]

# NHS region for each provider. In the CWT file this is carried by Parent_Org
# (verified one-to-one with the 7 NHS England regions, no ODS lookup needed).
# Optional: sources without it (e.g. the test fixtures) yield region "".
REGION_CANDIDATES = ["Parent_Org", "PARENT_ORG", "REGION", "NHS_REGION", "Region"]

# NHS three-column breakdown layout: canonical dimension -> source column.
REAL_BREAKDOWN_COLS = {
    "cancer":   "Cancer_Type",
    "route":    "Referral_Route_or_Stage",
    "modality": "Treatment_Modality",
}

# Per-dimension values that mean "not broken down on this dimension" (i.e. the
# aggregate). NaN/blank also counts as aggregate (e.g. FDS carries no modality).
_ALL_MARKERS = {
    "cancer":   {"all cancers"},
    "route":    {"all routes", "all stages"},
    "modality": {"all modalities"},
}

# Map NHS standard labels (as they appear in the source) to our internal codes.
# Matched as case-insensitive substrings, so both the verbose pre-2025 labels
# and the terse post-2025 codes ('FDS', '31D', '62D') resolve. Anything not
# listed (e.g. the USC / breast-symptomatic referral items) maps to None and is
# dropped.
STANDARD_LABEL_MAP = {
    "28-day fds": "FDS28", "faster diagnosis": "FDS28", "fds": "FDS28",
    "31-day combined": "CMB31", "31 day combined": "CMB31", "31d": "CMB31",
    "62-day combined": "CMB62", "62 day combined": "CMB62", "62d": "CMB62",
}


def _find_column(df, candidates):
    for cand in candidates:
        if cand in df.columns:
            return cand
    return None


def _map_standard(value):
    v = str(value).strip().lower()
    for key, code in STANDARD_LABEL_MAP.items():
        if key in v:
            return code
    return None


def _is_aggregate(series, dim):
    # A dimension is "aggregate" if it is an ALL-marker OR blank/missing. Missing
    # MUST be caught via .isna() and not by stringifying to "nan": pandas 3.0
    # changed astype(str) to PRESERVE NaN (no longer the literal "nan"), so the
    # old `isin(["nan", …])` silently stopped matching blank cells under pandas 3
    # — which mis-classified every blank-modality FDS row as a real breakdown
    # (FDS carries no modality dimension). Works under both pandas 2.x and 3.0.
    isna = series.isna()
    low = series.astype(str).str.strip().str.lower()
    return isna | low.isin(_ALL_MARKERS[dim]) | low.isin(["nan", ""])


def _derive_breakdown(df):
    """
    Collapse NHS's three breakdown columns into (breakdown_type, breakdown_value)
    per row, keeping EVERY row. A dimension is 'aggregate' if its value is an
    ALL-marker or blank. Classify by how many dimensions are actually broken
    down:
        0  -> ('all', 'All')                       (the headline figure)
        1  -> (<that dimension>, <its value>)       e.g. ('cancer', 'Breast')
       >1  -> ('combination', 'a | b')              non-aggregate parts joined
    """
    dims = list(REAL_BREAKDOWN_COLS)
    raw = {dim: df[col].astype(str).str.strip() for dim, col in REAL_BREAKDOWN_COLS.items()}
    nonagg = pd.DataFrame({dim: ~_is_aggregate(df[REAL_BREAKDOWN_COLS[dim]], dim) for dim in dims})
    counts = nonagg.sum(axis=1)

    btype = pd.Series("combination", index=df.index)
    bvalue = pd.Series("", index=df.index)

    btype[counts == 0] = "all"
    bvalue[counts == 0] = "All"

    for dim in dims:
        mask = (counts == 1) & nonagg[dim]
        btype[mask] = dim
        bvalue[mask] = raw[dim][mask]

    combo_mask = counts >= 2
    if combo_mask.any():
        def _combo_row(r):
            parts = [str(r[col]).strip() for dim, col in REAL_BREAKDOWN_COLS.items()
                     if str(r[col]).strip().lower() not in _ALL_MARKERS[dim]
                     and str(r[col]).strip().lower() not in ("nan", "")]
            return " | ".join(parts)
        bvalue.loc[combo_mask] = df.loc[combo_mask].apply(_combo_row, axis=1)

    return btype, bvalue


def normalise(df, source_file, data_status, period_hint=None):
    """Take a raw source DataFrame, return a DataFrame in CANONICAL_COLUMNS.

    `period_hint` ('YYYY-MM') supplies the month for the per-month new-FY Combined
    CSV, which omits the Period column. When a Period column IS present and a hint
    is also given, the two MUST agree (every row's month == hint) or we raise — a
    per-month file whose embedded period contradicts its filename is mislabelled
    and must stop the build, not be ingested."""
    rename = {}
    for field, candidates in COLUMN_CANDIDATES.items():
        col = _find_column(df, candidates)
        if col is None:
            raise ValueError(
                f"{source_file}: could not map required field '{field}'. "
                f"Add its source column name to COLUMN_CANDIDATES. "
                f"Columns present: {list(df.columns)[:30]}"
            )
        rename[col] = field
    out = df.rename(columns=rename)

    # Breakdown: prefer an explicit (type, value) pair; else derive from the NHS
    # three-column layout. Either way, every row is retained.
    bt_col = _find_column(out, BREAKDOWN_TYPE_CANDIDATES)
    bv_col = _find_column(out, BREAKDOWN_VALUE_CANDIDATES)
    if bt_col and bv_col:
        out["breakdown_type"] = out[bt_col].astype(str).str.strip().str.lower()
        out["breakdown_value"] = out[bv_col]
    elif all(c in df.columns for c in REAL_BREAKDOWN_COLS.values()):
        bt, bv = _derive_breakdown(df)
        out["breakdown_type"] = bt.values
        out["breakdown_value"] = bv.values
    else:
        raise ValueError(
            f"{source_file}: could not determine breakdown columns. Expected either "
            f"a type/value pair {BREAKDOWN_TYPE_CANDIDATES}/{BREAKDOWN_VALUE_CANDIDATES} "
            f"or the NHS three-column layout {list(REAL_BREAKDOWN_COLS.values())}. "
            f"Columns present: {list(df.columns)[:30]}"
        )

    # NHS region (optional). Captured from Parent_Org where present.
    region_col = _find_column(out, REGION_CANDIDATES)
    out["region"] = out[region_col].astype(str).str.strip() if region_col else ""

    out["standard"] = out["standard"].map(_map_standard)
    out = out[out["standard"].notna()]  # drop USC/breast-symptomatic referral rows

    # Coerce numerics; bad rows -> NaN then dropped.
    out["within_target"] = pd.to_numeric(out["within_target"], errors="coerce")
    out["total"] = pd.to_numeric(out["total"], errors="coerce")
    out = out[(out["total"].notna()) & (out["total"] > 0)]

    # Org level. Source ships a Provider/Commissioner 'basis' (or an explicit
    # level in the test fixtures); the all-England aggregate arrives as a
    # pseudo-org 'Total'. Normalise to our national/provider/icb vocabulary.
    out["org_level"] = out["org_level"].astype(str).str.strip().str.lower()
    out["org_level"] = out["org_level"].replace({"commissioner": "icb"})
    is_national = out["org_code"].astype(str).str.strip().str.lower() == "total"
    out.loc[is_national, "org_level"] = "national"
    out.loc[is_national, "org_code"] = "ENG"
    out.loc[is_national, "org_name"] = "England"
    out.loc[is_national, "region"] = "England"

    out["performance"] = (out["within_target"] / out["total"]).round(4)
    out["data_status"] = data_status
    out["source_file"] = source_file

    # Month. Prefer a Period column when the source ships one (cumulative + older
    # vintages); otherwise inject from the filename (period_hint) for the per-month
    # new-FY file, which omits Period. If neither is available, fail loud.
    month_col = _find_column(df, MONTH_CANDIDATES)
    if month_col is not None:
        # Normalise month to YYYY-MM. File vintages differ: older files use ISO
        # (YYYY-MM-DD), the 2025-26 files use DD/MM/YYYY. Parse ISO first, then fall
        # back to day-first for whatever didn't match -- a blanket dayfirst=True
        # would misread ISO dates as year-day-month and collapse every month to one.
        raw_month = df.loc[out.index, month_col]
        parsed = pd.to_datetime(raw_month, errors="coerce", format="%Y-%m-%d")
        fallback = parsed.isna()
        if fallback.any():
            parsed = parsed.copy()
            parsed[fallback] = pd.to_datetime(raw_month[fallback], errors="coerce", dayfirst=True)
        out["month"] = parsed.dt.strftime("%Y-%m").values
        out = out[out["month"].notna()]
        # Cross-check against the filename hint when both are present: a per-month
        # file that carries a Period column disagreeing with its filename month is
        # mislabelled -> stop the build (the recon gates are blind to month labels).
        if period_hint is not None:
            mism = sorted(set(out["month"].unique()) - {period_hint})
            if mism:
                raise ValueError(
                    f"{source_file}: Period column month(s) {mism} disagree with the "
                    f"filename month {period_hint!r} — mislabelled file, refusing to ingest.")
    elif period_hint is not None:
        out["month"] = period_hint
    else:
        raise ValueError(
            f"{source_file}: no Period/month column found (candidates {MONTH_CANDIDATES}) "
            f"AND no month derivable from the filename. A per-month file whose month "
            f"cannot be parsed must not be ingested (would mislabel the month). "
            f"Columns present: {list(df.columns)[:30]}")

    return out[config.CANONICAL_COLUMNS].reset_index(drop=True)


def assert_month_label(out, expected_month, source_file):
    """Fail-loud month-label guard for the per-month ingest path (REQUIRED — the
    only safety net here, since cancer has no national-value reconciliation).

    Asserts the injected month parses as YYYY-MM AND every normalised row carries
    exactly that one month. A bad filename parse or a multi-month file on this path
    stops the build rather than silently mislabelling."""
    import re as _re
    if not expected_month or not _re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", str(expected_month)):
        raise ValueError(
            f"{source_file}: per-month file month {expected_month!r} did not parse as "
            f"YYYY-MM — refusing to ingest (cannot guess the month).")
    months = set(out["month"].unique())
    if months != {expected_month}:
        raise ValueError(
            f"{source_file}: expected a single-month file for {expected_month!r} but "
            f"normalised rows carry months {sorted(months)} — refusing to ingest.")


def assert_contiguous_national_months(store):
    """Fail-loud series guard: the national (ENG) all-slice series must have no
    duplicate and no missing month between its first and last. A gap would mean a
    month silently dropped at the cumulative<->per-month seam; a duplicate would
    mean a dedup/merge failure. Either stops the build."""
    nat = store[(store["org_code"] == "ENG")]
    months = sorted(nat["month"].dropna().unique())
    if not months:
        return  # no national rows (e.g. minimal fixtures) — nothing to check
    def _ix(m):
        y, mm = (int(x) for x in m.split("-"))
        return y * 12 + (mm - 1)
    expected = list(range(_ix(months[0]), _ix(months[-1]) + 1))
    actual = [_ix(m) for m in months]
    if actual != expected:
        missing = sorted(set(expected) - set(actual))
        def _m(i):
            return f"{i // 12}-{i % 12 + 1:02d}"
        raise AssertionError(
            f"national month series is not contiguous: {len(missing)} missing "
            f"month(s) between {months[0]} and {months[-1]}: {[_m(i) for i in missing][:6]}")


def _is_cumulative_source(source_file):
    """A cumulative Combined CSV is FY-prefixed ('2025-26-…'); a per-month file is
    month-name-prefixed ('April-2026-…'). Used only to break SAME-STATUS ties."""
    return source_file.astype(str).str.match(r"\d{4}-\d{2}-").fillna(False)


def merge_with_revisions(existing, new):
    """
    Combine existing tidy data with a newly-processed file, applying the
    source-precedence rules (confirmed 2026-06-25) for any
    (month, org_level, org_code, standard, breakdown_type, breakdown_value) key:
      (a) FINAL always beats PROVISIONAL, regardless of source shape;
      (b) at EQUAL status, the CUMULATIVE file wins over the per-month file (the
          consolidated re-publication; numerically identical in practice);
      (c) on a full tie, the newest-processed (this `new` frame) wins.

    CELL SINGLE-SOURCE (2026-06-29, defence-in-depth): precedence is applied at the
    granularity of a (month, org_level, org_code, standard) CELL, not a single
    breakdown row. The winning source FILE for a cell supplies ALL of that cell's
    rows; rows from any losing sibling file are dropped WHOLESALE. Row-by-row
    deduplication alone (the previous behaviour) only collapsed EXACT-key
    duplicates, so when two vintages of one month disagreed on WHICH breakdown rows
    exist (NHS's April-2026 ICB-merger re-publication dropped/restructured some
    rows), the leftover rows from the superseded file lingered and broke the exact
    ten-groups+excluded==all-cancers reconciliation. Picking one file per cell
    keeps each cell internally consistent even if `dedupe_per_month` is bypassed.

    Implemented as a stable sort on (final, cumulative, arrival-order) ascending so
    the highest-precedence row sorts last; the per-cell winning source is that
    cell's last row, and we keep only rows from that source.
    """
    if existing is None or len(existing) == 0:
        return new
    combined = pd.concat([existing, new], ignore_index=True)
    combined["_final"] = (combined["data_status"] == "final").astype(int)
    combined["_cumul"] = _is_cumulative_source(combined["source_file"]).astype(int)
    combined["_ord"] = range(len(combined))      # arrival order: `new` rows sort last
    combined = combined.sort_values(["_final", "_cumul", "_ord"], kind="stable")
    cell = ["month", "org_level", "org_code", "standard"]
    # The winning source FILE per cell = the source_file of that cell's last
    # (highest-precedence) row. Keep only rows from the winning source for each cell.
    win = combined.groupby(cell, sort=False)["source_file"].last().rename("_win_src")
    combined = combined.join(win, on=cell)
    combined = combined[combined["source_file"] == combined["_win_src"]]
    key = ["month", "org_level", "org_code", "standard", "breakdown_type", "breakdown_value"]
    combined = combined.drop_duplicates(subset=key, keep="last")
    return combined.drop(columns=["_final", "_cumul", "_ord", "_win_src"]).reset_index(drop=True)
