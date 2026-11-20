"""
NHS England's national RTT figures WITH estimates for trusts that did not submit data.

Source: the "RTT Overview Timeseries Including Estimates for Missing Trusts" workbook that
NHS England publishes each month alongside the RTT data. It holds, for England only, each
headline measure twice: as reported (submitted data only) and "with estimates for missing
data" (NHS adds each non-submitting trust's latest submission). NHS's own headline figures
(for example "7.4 million" for Sep 2026) are the estimated ones; this dashboard's own totals
are the reported ones.

Two jobs:
  * parse()      -> {month: {measure: (reported, estimated)}}
  * reconcile()  -> fail-loud check that OUR national figures equal NHS's REPORTED columns for
                    every month both cover, then returns the estimated block to publish.
                    If our sum ever drifts from NHS's reported total, the build stops.
"""
import datetime as dt
import re

# Column headings in the "Full Time Series" sheet (header row is the one containing them).
# measure key -> (reported heading, estimated heading)
COLUMNS = {
    "waitlist": ("Total waiting (mil)", "Total waiting (mil) with estimates for missing data"),
    "pct18":    ("% within 18 weeks",   "% within 18 weeks with estimates for missing data"),
    "w52":      ("No. > 52 weeks",      "No. > 52 weeks with estimates for missing data"),
    "w65":      ("No. > 65 weeks",      "No. > 65 weeks with estimates for missing data"),
    "w78":      ("No. > 78 weeks",      "No. > 78 weeks with estimates for missing data"),
    "w104":     ("No. > 104 weeks",     "No. > 104 weeks with estimates for missing data"),
}
PCT_TOL = 0.0006          # our 4-dp performance vs NHS's full-precision proportion
_MON = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def _norm(h):
    return re.sub(r"\s+", " ", str(h or "")).strip()


def _month(v):
    """Month cell -> 'YYYY-MM'. Cells are datetimes, except annotated ones like '* Feb-24'."""
    if isinstance(v, (dt.datetime, dt.date)):
        return v.strftime("%Y-%m")
    if isinstance(v, str):
        m = re.search(r"([A-Za-z]{3})[- ](\d{2,4})", v)
        if m and m.group(1).lower() in _MON:
            y = int(m.group(2)); y = y + 2000 if y < 100 else y
            return f"{y}-{_MON[m.group(1).lower()]:02d}"
    return None


def _num(v):
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def parse(path):
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    ws = wb["Full Time Series"] if "Full Time Series" in wb.sheetnames else wb.worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    hdr_i = next((i for i, r in enumerate(rows)
                  if any(_norm(c) == COLUMNS["waitlist"][1] for c in r)), None)
    if hdr_i is None:
        raise ValueError(f"{path}: no '{COLUMNS['waitlist'][1]}' column; the NHS layout has changed")
    hdr = [_norm(c) for c in rows[hdr_i]]
    idx = {}
    for key, (rep, est) in COLUMNS.items():
        for h in (rep, est):
            if hdr.count(h) < 1:
                raise ValueError(f"{path}: column '{h}' not found; the NHS layout has changed")
        idx[key] = (hdr.index(rep), hdr.index(est))   # first match = the incomplete-pathways block
    month_col = 2
    out = {}
    for r in rows[hdr_i + 1:]:
        if len(r) <= month_col:
            continue
        m = _month(r[month_col])
        if not m:
            continue
        vals = {k: (_num(r[a]), _num(r[b])) for k, (a, b) in idx.items()}
        if any(v[1] is not None for v in vals.values()):
            out[m] = vals
    if not out:
        raise ValueError(f"{path}: parsed no months")
    return out


def reconcile(national_payload, parsed):
    """Our reported England figures must equal NHS's reported columns, month by month.
    Returns the estimates block for national.json: {measure: {month: estimated}}."""
    months = national_payload["months"]
    me = national_payload["measures"]
    ours = {
        "waitlist": me["waitlist"]["value"],
        "pct18": me["pct18"]["performance"],
        **{k: me["longwait"][k] for k in ("w52", "w65", "w78", "w104")},
    }
    fails, checked = [], 0
    block = {k: {} for k in COLUMNS}
    for i, m in enumerate(months):
        row = parsed.get(m)
        if row is None:
            continue
        for k, (rep, est) in row.items():
            if rep is None or est is None:
                continue
            o = ours[k][i]
            if o is None:
                continue
            tol = PCT_TOL if k == "pct18" else 0.5
            if abs(float(o) - rep) > tol:
                fails.append(f"{m} {k}: ours {o} vs NHS reported {rep}")
            block[k][m] = round(est, 4) if k == "pct18" else int(round(est))
        checked += 1
    if fails:
        raise AssertionError("ESTIMATES RECONCILIATION FAILED: our national figures do not match "
                             "NHS England's reported figures: " + "; ".join(fails[:10]))
    last = months[-1]
    if last not in parsed:
        raise AssertionError(f"ESTIMATES FILE IS BEHIND THE DATA: our latest month {last} is not in "
                             f"the NHS overview file (latest {max(parsed)}); refusing to publish a "
                             f"stale estimate line")
    print(f"  Estimates reconciliation OK: {checked} months, our reported England figures == NHS "
          f"reported; latest {last} waitlist est {block['waitlist'].get(last):,}")
    return block
