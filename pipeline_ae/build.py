"""
A&E site-data builder.

Reads every monthly CSV in RAW_DIR, derives the three headline measures per provider,
ICB and England, runs the three fail-loud reconciliation gates (see config) BEFORE
writing anything, then writes one JSON per org (+ index/national/meta) into SITE_DATA_DIR.

Aggregation:
  national = sum of all provider rows (equals the file's TOTAL row; gate 1)
  provider = one row per Org Code
  icb      = providers summed by ICB, using NHS England's System Mapping File for the
             month's financial year; a provider missing from that year's file falls
             back to the nearest other year's file that has it.
"""
import csv
import datetime as dt
import json
import os
import re
from collections import defaultdict

from . import config
from pipeline_common import ods

# Per-row metrics derived from the source columns (all integer counts).
# patt / patt_t1 are the 4-hour denominators: attendances minus those at trusts in the CRS
# field test (config.CRS_*), which NHS England leaves out of every 4-hour percentage.
# crs_att counts the attendances so excluded.
KEYS = ("att", "att_t1", "o4", "o4_t1", "dta4", "dta12", "adm", "adm_other", "patt", "patt_t1", "crs_att")


def blank():
    return {k: 0 for k in KEYS}


def parse_period(period):
    """'MSitAE-OCTOBER-2026' -> '2026-10'."""
    m = re.search(r"([A-Za-z]+)-(\d{4})\s*$", period.strip())
    mon = config.MONTHS.get(m.group(1).lower()) if m else None
    if not mon:
        raise ValueError(f"unrecognised A&E Period: {period!r}")
    return f"{m.group(2)}-{mon:02d}"


def region_of(parent):
    """'NHS ENGLAND NORTH WEST ' -> 'North West'; unknown -> 'England'."""
    p = re.sub(r"\s+", " ", (parent or "").upper()).strip()
    p = re.sub(r"^NHS ENGLAND\s+", "", p)
    p = re.sub(r"\s*\(.*\)$", "", p)
    return config.REGIONS.get(p, "England")


def _int(v):
    v = (v or "").strip().replace(",", "")
    return int(float(v)) if v else 0


def in_crs(code, month):
    """True when a trust was in the CRS field test that month (no 4-hour data reported)."""
    return code in config.CRS_TRUSTS and config.CRS_FROM <= month <= config.CRS_TO


def metrics(raw, crs=False):
    """Source columns (already ints) -> the derived metrics in KEYS. `crs`: this row's
    attendances are left out of the 4-hour denominators (CRS field test)."""
    att = sum(raw[k] for k in ("att_t1", "att_t2", "att_t3", "batt_t1", "batt_t2", "batt_t3"))
    att_t1 = raw["att_t1"] + raw["batt_t1"]
    return {
        "att": att,
        "att_t1": att_t1,
        "patt": 0 if crs else att,
        "patt_t1": 0 if crs else att_t1,
        "crs_att": att if crs else 0,
        "o4": sum(raw[k] for k in ("o4_t1", "o4_t2", "o4_t3", "bo4_t1", "bo4_t2", "bo4_t3")),
        "o4_t1": raw["o4_t1"] + raw["bo4_t1"],
        "dta4": raw["dta_4_12"] + raw["dta_12"],
        "dta12": raw["dta_12"],
        "adm": raw["adm_t1"] + raw["adm_t2"] + raw["adm_t3"],
        "adm_other": raw["adm_other"],
    }


def read_month(path):
    """Parse one monthly CSV. Returns (month, rows, total) where rows is a list of
    {code, name, region, raw: {col: int}} and total is the TOTAL row's raw dict.

    Fails loud on: a missing column, more than one month in the file, no TOTAL row
    or more than one, a duplicated org code, or a negative count."""
    with open(path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh)
        header = [config.COL_ALIASES.get(h.strip(), h.strip()) for h in next(reader)]
        idx = {h: i for i, h in enumerate(header)}
        missing = [c for c in list(config.ID_COLS.values()) + [v for k, v in config.COLS.items()
                   if k not in config.OPTIONAL_COLS] if c not in idx]
        if missing:
            raise ValueError(f"{os.path.basename(path)}: missing column(s) {missing} — NHS changed the format?")
        rows, totals, months, seen = [], [], set(), set()
        for r in reader:
            if not any(c.strip() for c in r):
                continue
            code = r[idx["Org Code"]].strip()
            raw = {k: (_int(r[idx[c]]) if c in idx else 0) for k, c in config.COLS.items()}
            if any(v < 0 for v in raw.values()):
                raise ValueError(f"{os.path.basename(path)}: negative count for {code}")
            # The TOTAL row: "TOTAL" in the code column, or (2019-20) blank codes and "Total" as the name.
            if any(r[idx[c]].strip().upper() == "TOTAL" for c in config.ID_COLS.values()):
                totals.append(raw)
                continue
            months.add(parse_period(r[idx["Period"]]))
            if code in seen:
                raise ValueError(f"{os.path.basename(path)}: org {code} appears twice")
            seen.add(code)
            rows.append({"code": code, "name": re.sub(r"\s+", " ", r[idx["Org name"]]).strip(),
                         "region": region_of(r[idx["Parent Org"]]), "raw": raw})
    if len(months) != 1:
        raise ValueError(f"{os.path.basename(path)}: expected one month, found {sorted(months)}")
    if len(totals) != 1:
        raise ValueError(f"{os.path.basename(path)}: expected one TOTAL row, found {len(totals)}")
    return months.pop(), rows, totals[0]


def check_total_row(month, rows, total):
    """Gate 1: provider rows sum exactly to the file's TOTAL row, column by column."""
    for k in config.COLS:
        s = sum(r["raw"][k] for r in rows)
        if s != total[k]:
            raise AssertionError(f"TOTAL-ROW GATE FAILED ({month}) [{config.COLS[k]}]: "
                                 f"rows sum to {s:,}, TOTAL row says {total[k]:,}")


# --- System Mapping File: provider -> ICB -----------------------------------------

def read_mapping(path):
    """NHS England's System Mapping File (.xls/.xlsx) -> {provider code: (icb code, icb name)}."""
    import pandas as pd
    df = pd.read_excel(path, header=None)
    hdr = None
    for i in range(min(len(df), 40)):
        vals = [str(v).strip() for v in df.iloc[i].tolist()]
        if "Code" in vals and "ICB Code" in vals:
            hdr = i
            break
    if hdr is None:
        raise ValueError(f"{path}: no 'Code' / 'ICB Code' header row")
    df = pd.read_excel(path, header=hdr)
    df.columns = [str(c).strip() for c in df.columns]
    out = {}
    for _, r in df.iterrows():
        code, icb = str(r.get("Code", "")).strip(), str(r.get("ICB Code", "")).strip()
        if code and code != "nan" and icb and icb != "nan":
            out[code] = (icb, str(r.get("ICB Name", icb)).strip())
    if len(out) < 50:
        raise ValueError(f"{path}: only {len(out)} providers mapped — wrong file?")
    return out


def fy_of(month):
    """'2026-03' -> '2025-26'."""
    y, m = map(int, month.split("-"))
    s = y if m >= 4 else y - 1
    return f"{s}-{str(s + 1)[-2:]}"


def icb_for(code, month, maps):
    """The ICB for a provider in a month: that FY's mapping, else the nearest FY's."""
    fy = fy_of(month)
    if code in maps.get(fy, {}):
        return maps[fy][code]
    for other in sorted(maps, key=lambda f: (abs(int(f[:4]) - int(fy[:4])), f)):
        if code in maps[other]:
            return maps[other][code]
    return None


# --- NHS England's Monthly A&E Time Series (gate 2) --------------------------------

def read_time_series(path):
    """{month: {att, att_t1, o4, o4_t1, dta4, dta12}} from the 'Activity' and 'Performance'
    sheets of the Monthly A&E Time Series workbook (England level)."""
    import pandas as pd

    def sheet(name):
        df = pd.read_excel(path, sheet_name=name, header=None)
        hdr = next(i for i in range(min(len(df), 40))
                   if any(str(v).strip() == "Period" for v in df.iloc[i].tolist()))
        df = pd.read_excel(path, sheet_name=name, header=hdr)
        df.columns = [str(c).strip() for c in df.columns]
        df = df[pd.to_datetime(df["Period"], errors="coerce").notna()].copy()
        df["month"] = pd.to_datetime(df["Period"]).dt.strftime("%Y-%m")
        return df.set_index("month")

    act, perf = sheet("Activity"), sheet("Performance")

    def col(df, *needles, nth=0):
        hits = [c for c in df.columns if all(n.lower() in c.lower() for n in needles)]
        if len(hits) <= nth:
            raise ValueError(f"time series: no column matching {needles}")
        return hits[nth]

    a_tot, a_t1 = col(act, "Total Attendances"), col(act, "Type 1")
    a_dta4, a_dta12 = col(act, ">4 hours from decision"), col(act, ">12 hours from decision")
    p_lt4, p_o4 = col(perf, "Total Attendances < 4"), col(perf, "Total Attendances > 4")
    p_t1 = [c for c in perf.columns if c.startswith("Type 1")]
    p_lt4_t1, p_o4_t1 = p_t1[0], p_t1[1]
    out = {}
    for m in act.index:
        if m not in perf.index:
            continue
        def iv(df, c):
            v = df.at[m, c]
            return None if pd.isna(v) else int(round(float(v)))
        def tot(a, b):
            x, y = iv(perf, a), iv(perf, b)
            return None if x is None or y is None else x + y
        # patt: the 4-hour denominator NHS England used (attendances under + over 4 hours).
        out[m] = {"att": iv(act, a_tot), "att_t1": iv(act, a_t1), "dta4": iv(act, a_dta4),
                  "dta12": iv(act, a_dta12), "o4": iv(perf, p_o4), "o4_t1": iv(perf, p_o4_t1),
                  "patt": tot(p_lt4, p_o4), "patt_t1": tot(p_lt4_t1, p_o4_t1)}
    return out


def reconcile_time_series(national, ts):
    """Gate 2: our England figures == NHS England's time series, exactly, every month in both.
    The latest month we hold must be in the time series (it is published the same day)."""
    months = sorted(national)
    if months[-1] not in ts:
        raise AssertionError(f"TIME-SERIES GATE FAILED: latest month {months[-1]} is not in "
                             f"NHS England's time series (latest {max(ts)}) — stale workbook?")
    checked = 0
    for m in months:
        if m not in ts:
            continue
        for k, v in ts[m].items():
            if v is not None and national[m][k] != v:
                raise AssertionError(f"TIME-SERIES GATE FAILED ({m}) [{k}]: ours {national[m][k]:,} "
                                     f"vs NHS England {v:,}")
        checked += 1
    print(f"  Time-series gate OK: {checked} months match NHS England exactly")
    return {"checked": checked, "first": months[0], "last": months[-1]}


# --- payloads ----------------------------------------------------------------------

def _rate(num_ok, total):
    return round(num_ok / total, 4) if total else None


def build_org_payload(code, name, level, by_month, region="England"):
    months = sorted(by_month)
    ms = [by_month[m] for m in months]
    def dept(att, o4):
        within = [m[att] - m[o4] for m in ms]
        total = [m[att] for m in ms]
        return {"performance": [_rate(w, t) for w, t in zip(within, total)],
                "within_target": within, "total": total}
    # crs: this provider reported no 4-hour data (CRS field test); excl_crs: the figure leaves
    # out CRS trusts' attendances (England / ICB during the field test); final otherwise.
    status = ["final" if not m["crs_att"] else ("crs" if not m["patt"] else "excl_crs") for m in ms]

    return {
        "code": code, "name": name, "level": level, "region": region, "months": months,
        "measures": {
            "pct4": {"all": dept("patt", "o4"), "t1": dept("patt_t1", "o4_t1"),
                     "data_status": status, "target": config.STANDARD_TARGET},
            "attend": {"all": [m["att"] for m in ms], "t1": [m["att_t1"] for m in ms]},
            "dta": {"w4": [m["dta4"] for m in ms], "w12": [m["dta12"] for m in ms],
                    "admissions": [m["adm"] for m in ms]},
        },
    }


def _negligible(by_month, all_months, code, classification):
    """Hide a provider whose attendances never reach PICKER_MIN_PROVIDER_ATTENDANCES in any
    month. Unlike RTT, a provider that has stopped reporting stays in the picker (under
    "Stopped reporting"), because the A&E series goes back to 2018 and many trusts merged
    since (DECISIONS 2026-11-26). A newly created, current org is never hidden."""
    months = sorted(all_months)
    young_cutoff = months[-config.YOUNG_WINDOW_MONTHS] if len(months) >= config.YOUNG_WINDOW_MONTHS else months[0]
    first = min(by_month) if by_month else ""
    if not ods.is_former(classification.get(code)) and first >= young_cutoff:
        return False
    peak = max((m["att"] for m in by_month.values()), default=0)
    return peak < config.PICKER_MIN_PROVIDER_ATTENDANCES


def run(raw_dir=config.RAW_DIR, mapping_dir=config.MAPPING_DIR, out_dir=config.SITE_DATA_DIR,
        classification=None, trust_codes=None, time_series_path=config.TIME_SERIES_PATH):
    classification = classification or {}
    trust_codes = trust_codes or set()
    files = sorted(f for f in os.listdir(raw_dir) if f.endswith(".csv"))
    if not files:
        raise RuntimeError(f"no monthly CSVs in {raw_dir}")
    maps = {}
    for f in sorted(os.listdir(mapping_dir)):
        m = re.match(r"(\d{4}-\d{2})\.xlsx?$", f)
        if m:
            maps[m.group(1)] = read_mapping(os.path.join(mapping_dir, f))
    if not maps:
        raise RuntimeError(f"no System Mapping Files in {mapping_dir}")
    first_icb_month = min(maps)[:4] + "-04"       # ICB figures start with the first mapping year

    orgs = defaultdict(dict)            # (level, code) -> {month: metrics}
    names, regions, icb_names = {}, {}, {}
    unmapped = {}                       # month -> {code: attendances}
    months = set()
    for f in files:
        month, rows, total = read_month(os.path.join(raw_dir, f))
        if month in months:
            raise RuntimeError(f"two files for {month} in {raw_dir}")
        months.add(month)
        check_total_row(month, rows, total)                      # gate 1
        nat = orgs[("national", "ENG")].setdefault(month, blank())
        with_icbs = month >= first_icb_month
        for r in rows:
            crs = in_crs(r["code"], month)
            if crs and (r["raw"]["o4_t1"] or r["raw"]["o4_t2"] or r["raw"]["o4_t3"]
                        or r["raw"]["bo4_t1"] or r["raw"]["bo4_t2"] or r["raw"]["bo4_t3"]):
                raise AssertionError(f"CRS GATE FAILED ({month}): {r['code']} is listed as a CRS field-test "
                                     f"trust but reported 4-hour breaches — check config.CRS_TRUSTS")
            mt = metrics(r["raw"], crs)
            orgs[("provider", r["code"])][month] = mt
            names[r["code"]] = r["name"]
            if r["region"] != "England":
                regions[r["code"]] = r["region"]
            for k in KEYS:
                nat[k] += mt[k]
            if not with_icbs:
                continue
            icb = icb_for(r["code"], month, maps)
            if icb is None:
                unmapped.setdefault(month, {})[r["code"]] = mt["att"]
                continue
            icb_names[icb[0]] = icb[1].upper()
            acc = orgs[("icb", icb[0])].setdefault(month, blank())
            for k in KEYS:
                acc[k] += mt[k]
    all_months = sorted(months)
    print(f"  Total-row gate OK: {len(all_months)} months ({all_months[0]}..{all_months[-1]})")

    national = orgs[("national", "ENG")]
    # Gate 3: ICBs + unmapped == England, unmapped share small.
    for m in (m for m in all_months if m >= first_icb_month):
        icb_att = sum(v[m]["att"] for (lv, _), v in orgs.items() if lv == "icb" and m in v)
        um = sum(unmapped.get(m, {}).values())
        if icb_att + um != national[m]["att"]:
            raise AssertionError(f"ICB GATE FAILED ({m}): ICBs {icb_att:,} + unmapped {um:,} "
                                 f"!= England {national[m]['att']:,}")
        if national[m]["att"] and um / national[m]["att"] > config.UNMAPPED_MAX_SHARE:
            raise AssertionError(f"ICB GATE FAILED ({m}): {um:,} attendances at providers with no ICB "
                                 f"({sorted(unmapped[m])}) exceed {config.UNMAPPED_MAX_SHARE:.0%}")
    print(f"  ICB gate OK: {sum(1 for k in orgs if k[0]=='icb')} ICBs; providers with no ICB: "
          f"{sorted({c for d in unmapped.values() for c in d}) or 'none'}")
    # Gate 2: NHS England's own England figures.
    ts_recon = None
    if time_series_path:
        ts_recon = reconcile_time_series(national, read_time_series(time_series_path))

    # ---- write ----
    org_dir = os.path.join(out_dir, "org")
    os.makedirs(org_dir, exist_ok=True)
    for f in os.listdir(org_dir):                    # drop orgs from a previous build
        if f.endswith(".json"):
            os.remove(os.path.join(org_dir, f))
    index = []
    for (level, code), by_month in sorted(orgs.items()):
        if level == "national":
            continue
        name = names.get(code) if level == "provider" else icb_names.get(code, code)
        region = regions.get(code, "England") if level == "provider" else "England"
        payload = build_org_payload(code, name, level, by_month, region)
        with open(os.path.join(org_dir, f"{code}.json"), "w") as fh:
            json.dump(payload, fh, separators=(",", ":"))
        entry = {"code": code, "name": name, "level": level, "region": region}
        if level == "provider" and _negligible(by_month, all_months, code, classification):
            entry["hidden"] = True
        ce = classification.get(code)
        formed_ok = (ods.series_truncated(min(by_month), all_months)
                     and ods.formed_recently(ce, classification, all_months[0]))
        ods.annotate_entry(entry, ce, formed_ok=formed_ok)
        # A provider whose figures end before the last STOPPED_GAP_MONTHS months has stopped
        # reporting (usually a merger ODS no longer flags). The picker lists it separately.
        last_seen = max(by_month)
        if level == "provider" and not entry.get("former") and len(all_months) > config.STOPPED_GAP_MONTHS \
                and last_seen < all_months[-1 - config.STOPPED_GAP_MONTHS]:
            entry["stopped"] = last_seen
        if level == "provider":
            ods.tag_provider_type(entry, trust_codes)
        index.append(entry)
    index.sort(key=lambda e: (e["level"], e["name"]))
    ods.assert_independents_tagged(index, "A&E", trust_codes)
    national_payload = build_org_payload("ENG", "England", "national", national)
    with open(os.path.join(out_dir, "index.json"), "w") as fh:
        json.dump(index, fh, separators=(",", ":"))
    with open(os.path.join(out_dir, "national.json"), "w") as fh:
        json.dump(national_payload, fh, separators=(",", ":"))

    n_prov = sum(1 for e in index if e["level"] == "provider")
    n_icb = sum(1 for e in index if e["level"] == "icb")
    n_hidden = sum(1 for e in index if e.get("hidden"))
    meta = {
        "built_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "months": all_months,
        "month_range": [all_months[0], all_months[-1]],
        "source": config.SOURCE_INDEX,
        "standard_target": config.STANDARD_TARGET,
        "milestones": config.MILESTONES,
        "measures": config.MEASURES,
        "depts": config.DEPTS,
        "counts": {"providers": n_prov, "icbs": n_icb, "hidden": n_hidden},
        "unmapped": {m: d for m, d in sorted(unmapped.items())},
        "icb_from": max(first_icb_month, all_months[0]),
        "crs": {"from": config.CRS_FROM, "to": config.CRS_TO, "trusts": sorted(config.CRS_TRUSTS)},
        "reconciliation": ts_recon,
    }
    with open(os.path.join(out_dir, "meta.json"), "w") as fh:
        json.dump(meta, fh, indent=2)
    print(f"\nWrote {len(index)} orgs ({n_prov} providers [{n_hidden} hidden], {n_icb} ICBs) "
          f"+ national, months {all_months[0]}..{all_months[-1]}")
    return meta
