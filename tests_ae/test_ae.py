"""Unit tests for the A&E pipeline (pipeline_ae). Offline: synthetic CSVs and workbooks."""
import json
import os

import pandas as pd
import pytest

from pipeline_ae import build, config, discover

HEADER = ["Period", "Org Code", "Parent Org", "Org name"] + list(config.COLS.values())


def raw(att_t1=0, att_t3=0, batt_t1=0, o4_t1=0, o4_t3=0, bo4_t1=0, dta_4_12=0, dta_12=0, adm_t1=0):
    r = {k: 0 for k in config.COLS}
    r.update(att_t1=att_t1, att_t3=att_t3, batt_t1=batt_t1, o4_t1=o4_t1, o4_t3=o4_t3,
             bo4_t1=bo4_t1, dta_4_12=dta_4_12, dta_12=dta_12, adm_t1=adm_t1)
    return r


def write_csv(path, period, rows, total_code="TOTAL", total_override=None):
    """rows: [(code, parent, name, raw dict)]. Adds a TOTAL row summing the rows."""
    lines = [",".join(HEADER)]
    tot = {k: 0 for k in config.COLS}
    for code, parent, name, r in rows:
        for k in config.COLS:
            tot[k] += r[k]
        nm = f'"{name}"' if "," in name else name
        lines.append(",".join([period, code, parent, nm] + [str(r[k]) for k in config.COLS]))
    tot.update(total_override or {})
    lines.append(",".join([total_code] * 4 + [str(tot[k]) for k in config.COLS]))
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write("﻿" + "\r\n".join(lines) + "\r\n")


# ---------------------------------------------------------------- parsing helpers

def test_parse_period():
    assert build.parse_period("MSitAE-OCTOBER-2026") == "2026-10"
    assert build.parse_period("MSitAE-April-2022 ") == "2022-04"
    with pytest.raises(ValueError):
        build.parse_period("MSitAE-SMARCH-2026")


def test_region_of_handles_trailing_spaces_and_unknowns():
    assert build.region_of("NHS ENGLAND NORTH WEST ") == "North West"
    assert build.region_of("NHS ENGLAND NORTH EAST AND YORKSHIRE") == "North East and Yorkshire"
    assert build.region_of("SOMEWHERE ELSE") == "England"


def test_metrics_include_booked_appointments():
    m = build.metrics(raw(att_t1=100, att_t3=50, batt_t1=10, o4_t1=40, o4_t3=1, bo4_t1=2,
                          dta_4_12=7, dta_12=3, adm_t1=20))
    assert m["att"] == 160 and m["att_t1"] == 110
    assert m["o4"] == 43 and m["o4_t1"] == 42
    assert m["dta4"] == 10 and m["dta12"] == 3 and m["adm"] == 20


def test_fy_of():
    assert build.fy_of("2026-03") == "2025-26"
    assert build.fy_of("2026-04") == "2026-27"


def test_icb_for_falls_back_to_nearest_year():
    maps = {"2023-24": {"RAP": ("QMJ", "NCL")}, "2024-25": {}, "2026-27": {"RAP": ("Z9B", "WNL")}}
    assert build.icb_for("RAP", "2024-07", maps) == ("QMJ", "NCL")      # 1 year away beats 2
    assert build.icb_for("XYZ", "2024-07", maps) is None


# ---------------------------------------------------------------- read_month + gate 1

def test_read_month_total_row_variants_and_quoted_names(tmp_path):
    p = tmp_path / "m.csv"
    write_csv(p, "MSitAE-MAY-2022", [
        ("RF4", "NHS ENGLAND LONDON", "BARKING, HAVERING AND REDBRIDGE NHS TRUST", raw(att_t1=10, o4_t1=4)),
        ("Y1", "NHS ENGLAND LONDON ", "A WALK-IN CENTRE", raw(att_t3=5)),
    ], total_code="Total ")
    month, rows, total = build.read_month(p)
    assert month == "2022-05"
    assert [r["code"] for r in rows] == ["RF4", "Y1"]
    assert rows[0]["name"] == "BARKING, HAVERING AND REDBRIDGE NHS TRUST"
    assert total["att_t1"] == 10
    build.check_total_row(month, rows, total)


def test_total_row_gate_fails_loud(tmp_path):
    p = tmp_path / "m.csv"
    write_csv(p, "MSitAE-MAY-2022", [("A", "NHS ENGLAND LONDON", "A", raw(att_t1=10))],
              total_override={"att_t1": 11})
    month, rows, total = build.read_month(p)
    with pytest.raises(AssertionError, match="TOTAL-ROW GATE"):
        build.check_total_row(month, rows, total)


def test_read_month_rejects_missing_column(tmp_path):
    p = tmp_path / "m.csv"
    p.write_text("Period,Org Code,Parent Org,Org name\nMSitAE-MAY-2022,A,B,C\n")
    with pytest.raises(ValueError, match="missing column"):
        build.read_month(p)


def test_read_month_rejects_no_total_row(tmp_path):
    p = tmp_path / "m.csv"
    write_csv(p, "MSitAE-MAY-2022", [("A", "X", "A", raw(att_t1=1))])
    text = p.read_text(encoding="utf-8-sig").splitlines()
    p.write_text("\n".join(text[:-1]) + "\n")
    with pytest.raises(ValueError, match="TOTAL row"):
        build.read_month(p)


# ---------------------------------------------------------------- discovery

FY_HTML = """
<a href="/statistics/wp-content/uploads/sites/2/2026/11/System-Mapping-Oct-26.xls">System Mapping File (XLS, 72KB)</a>
<a href="https://www.england.nhs.uk/statistics/wp-content/uploads/sites/2/2026/11/October-2026-CSV-WD35qg.csv">Monthly A&amp;E October 2026 (CSV, 28KB)</a>
<a href="/x/October-26-AE-by-provider-00Qp3.xls">Monthly A&amp;E October 2026 (XLS, 421KB)</a>
<a href="/x/Monthly-AE-Nov-25-CSV-revised.csv"><span>Monthly A&amp;E November 2025</span> (revised 14.05.26) (CSV, 29KB)</a>
<a href="/x/May-2026-Revised-12.11.26-CSV-28KB-Ea65dF.csv">Monthly A&amp;E May 2026 (Revised 12.11.26) (CSV 28KB)</a>
<a href="/x/Quarter-2.xls">2026-27 Quarter 2: -3 Months to September (XLS, 421KB)</a>
"""


def test_discover_months_reads_link_text_not_file_names():
    got = discover.discover_months([FY_HTML])
    assert sorted(got) == ["2025-11", "2026-05", "2026-10"]
    assert got["2026-10"].endswith("October-2026-CSV-WD35qg.csv")
    assert got["2025-11"] == "https://www.england.nhs.uk/x/Monthly-AE-Nov-25-CSV-revised.csv"


def test_discover_mapping_and_time_series():
    assert discover.discover_mapping(FY_HTML).endswith("System-Mapping-Oct-26.xls")
    idx = '<a href="/s/Monthly-AE-Time-Series-October-2026-Es49Qw.xls">Monthly A&amp;E Time Series October 2026 (XLS, 445KB)</a>'
    assert discover.discover_time_series(idx).endswith("Es49Qw.xls")
    assert discover.discover_time_series(FY_HTML) is None


def test_financial_years():
    import datetime as dt
    assert discover.financial_years(dt.date(2026, 3, 31))[-1] == "2025-26"
    assert discover.financial_years(dt.date(2026, 4, 1))[-1] == "2026-27"
    assert discover.financial_years(dt.date(2026, 4, 1))[0] == "2018-19"


def test_select_to_fetch(tmp_path):
    (tmp_path / "2026-09.csv").write_text("x")
    (tmp_path / "2026-08.csv").write_text("x")
    links = {"2026-08": "u8", "2026-09": "u9-revised", "2026-10": "u10"}
    todo = discover.select_to_fetch(links, {"2026-08": "u8", "2026-09": "u9"}, str(tmp_path), ".csv")
    assert sorted(todo) == ["2026-09", "2026-10"]


# ---------------------------------------------------------------- end to end

def write_mapping(path, rows):
    pad = pd.DataFrame([[None] * 6] * 7)
    body = pd.DataFrame([[None, c, c, i, i + " ICB", "Region"] for c, i in rows])
    head = pd.DataFrame([[None, "Code", "Name", "ICB Code", "ICB Name", "Region Name"]])
    pd.concat([pad, head, body]).to_excel(path, header=False, index=False)


def write_time_series(path, months):
    """months: {month: (att, att_t1, o4, o4_t1, dta4, dta12)}"""
    pad = [[None] * 13] * 13
    act = [[None, "Period", "Type 1 Departments - Major A&E", "Type 2", "Type 3", "Total Attendances",
            "a", "b", "c", "d", "e", "f",
            "Number of patients spending >4 hours from decision to admit to admission",
            "Number of patients spending >12 hours from decision to admit to admission"]]
    perf = [[None, "Period", "Type 1 Departments - Major A&E", "Type 2", "Type 3", "Total Attendances < 4 hours",
             "Type 1 Departments - Major A&E.1", "Type 2.1", "Type 3.1", "Total Attendances > 4 hours"]]
    for m, (att, t1, o4, o4t1, d4, d12) in months.items():
        ts = pd.Timestamp(m + "-01")
        act.append([None, ts, t1, 0, att - t1, att, 0, 0, 0, 0, 0, 0, d4, d12])
        perf.append([None, ts, t1 - o4t1, 0, 0, att - o4, o4t1, 0, 0, o4])
    with pd.ExcelWriter(path) as xw:
        pd.DataFrame([r[:14] for r in pad] + act).to_excel(xw, sheet_name="Activity", header=False, index=False)
        pd.DataFrame([r[:10] for r in pad] + perf).to_excel(xw, sheet_name="Performance", header=False, index=False)


@pytest.fixture
def dataset(tmp_path):
    raw_dir, map_dir, out = tmp_path / "raw", tmp_path / "map", tmp_path / "out"
    raw_dir.mkdir(); map_dir.mkdir()
    write_csv(raw_dir / "2026-03.csv", "MSitAE-MARCH-2026", [
        ("RAA", "NHS ENGLAND LONDON", "A TRUST", raw(att_t1=100, o4_t1=30, dta_4_12=5, dta_12=2)),
        ("Y01", "NHS ENGLAND LONDON", "A WALK-IN CENTRE", raw(att_t3=50, o4_t3=1)),
        ("ZZZ", "NHS ENGLAND LONDON", "UNMAPPED UTC", raw(att_t3=1)),
    ])
    write_csv(raw_dir / "2026-04.csv", "MSitAE-APRIL-2026", [
        ("RAA", "NHS ENGLAND LONDON", "A TRUST", raw(att_t1=110, batt_t1=10, o4_t1=20, bo4_t1=1, dta_12=4)),
        ("Y01", "NHS ENGLAND LONDON", "A WALK-IN CENTRE", raw(att_t3=60)),
    ])
    write_mapping(map_dir / "2025-26.xlsx", [("RAA", "QOLD"), ("Y01", "QOLD")] + [(f"P{i}", "QX") for i in range(60)])
    write_mapping(map_dir / "2026-27.xlsx", [("RAA", "QNEW"), ("Y01", "QNEW")] + [(f"P{i}", "QX") for i in range(60)])
    write_time_series(tmp_path / "ts.xlsx", {"2026-03": (151, 100, 31, 30, 7, 2),
                                             "2026-04": (180, 120, 21, 21, 4, 4)})
    return raw_dir, map_dir, out, tmp_path / "ts.xlsx"


def test_end_to_end_build(dataset, monkeypatch):
    raw_dir, map_dir, out, ts = dataset
    monkeypatch.setattr(config, "UNMAPPED_MAX_SHARE", 0.05)
    meta = build.run(raw_dir=str(raw_dir), mapping_dir=str(map_dir), out_dir=str(out),
                     classification={}, trust_codes={"RAA"}, time_series_path=str(ts))
    assert meta["months"] == ["2026-03", "2026-04"]
    assert meta["reconciliation"]["checked"] == 2
    assert meta["unmapped"] == {"2026-03": {"ZZZ": 1}}
    nat = json.load(open(out / "national.json"))
    assert nat["measures"]["pct4"]["all"]["performance"] == [round(120 / 151, 4), round(159 / 180, 4)]
    assert nat["measures"]["pct4"]["t1"]["within_target"] == [70, 99]
    assert nat["measures"]["dta"]["w12"] == [2, 4]
    # ICBs follow each financial year's mapping file
    assert json.load(open(out / "org" / "QOLD.json"))["months"] == ["2026-03"]
    assert json.load(open(out / "org" / "QNEW.json"))["measures"]["attend"]["all"] == [180]
    idx = {e["code"]: e for e in json.load(open(out / "index.json"))}
    assert idx["Y01"]["ptype"] == "independent" and "ptype" not in idx["RAA"]
    assert idx["RAA"]["region"] == "London"
    # a walk-in centre with no Type 1 department has no Type 1 percentage
    y = json.load(open(out / "org" / "Y01.json"))
    assert y["measures"]["pct4"]["t1"]["performance"] == [None, None]


def test_time_series_gate_fails_loud(dataset, monkeypatch):
    raw_dir, map_dir, out, ts = dataset
    monkeypatch.setattr(config, "UNMAPPED_MAX_SHARE", 0.05)
    write_time_series(ts, {"2026-03": (151, 100, 31, 30, 7, 2), "2026-04": (180, 120, 22, 21, 4, 4)})
    with pytest.raises(AssertionError, match=r"TIME-SERIES GATE FAILED \(2026-04\) \[o4\]"):
        build.run(raw_dir=str(raw_dir), mapping_dir=str(map_dir), out_dir=str(out),
                  classification={}, trust_codes={"RAA"}, time_series_path=str(ts))
    assert not os.path.exists(out / "index.json")          # nothing written


def test_unmapped_share_gate(dataset):
    raw_dir, map_dir, out, ts = dataset                     # ZZZ is 1 of 151 = 0.7% > 0.5%
    import pipeline_ae.config as c
    old = c.UNMAPPED_MAX_SHARE
    c.UNMAPPED_MAX_SHARE = 0.005
    try:
        with pytest.raises(AssertionError, match="ICB GATE FAILED"):
            build.run(raw_dir=str(raw_dir), mapping_dir=str(map_dir), out_dir=str(out),
                      classification={}, trust_codes={"RAA"}, time_series_path=str(ts))
    finally:
        c.UNMAPPED_MAX_SHARE = old


def test_time_series_must_include_latest_month(dataset, monkeypatch):
    raw_dir, map_dir, out, ts = dataset
    monkeypatch.setattr(config, "UNMAPPED_MAX_SHARE", 0.05)
    write_time_series(ts, {"2026-03": (151, 100, 31, 30, 7, 2)})
    with pytest.raises(AssertionError, match="stale workbook"):
        build.run(raw_dir=str(raw_dir), mapping_dir=str(map_dir), out_dir=str(out),
                  classification={}, trust_codes={"RAA"}, time_series_path=str(ts))


# ---------------------------------------------------------------- older formats (2018-2021)

OLD_HEADER = ["Period", "Org Code", "Parent Org", "Org name",
              "Number of A&E attendances Type 1", "Number of A&E attendances Type 2",
              "Number of A&E attendances Other A&E Department", "Number of attendances over 4hrs Type 1",
              "Number of attendances over 4hrs Type 2", "Number of attendances over 4hrs Other A&E Department",
              "Patients who have waited 4-12 hs from DTA to admission", "Patients who have waited 12+ hrs from DTA to admission",
              "Emergency admissions via A&E - Type 1", "Emergency admissions via A&E - Type 2",
              "Emergency admissions via A&E - Other A&E department", "Other emergency admissions"]


def test_read_month_2019_format_without_booked_columns_and_blank_code_total(tmp_path):
    p = tmp_path / "m.csv"
    p.write_text(",".join(OLD_HEADER) + "\n"
                 "MSitAE-MAY-2019,RBD,NHS ENGLAND SOUTH WEST (SOUTH WEST SOUTH),DORSET COUNTY,10,0,5,3,0,1,2,1,4,0,0,1\n"
                 ",,,Total,10,0,5,3,0,1,2,1,4,0,0,1\n")
    month, rows, total = build.read_month(p)
    assert month == "2019-05" and len(rows) == 1
    assert rows[0]["region"] == "South West"
    assert rows[0]["raw"]["att_t1"] == 10 and rows[0]["raw"]["batt_t1"] == 0
    assert rows[0]["raw"]["o4_t3"] == 1
    build.check_total_row(month, rows, total)


def test_discover_months_accepts_link_text_without_ae():
    html = '<a href="/x/July-2021-revised-120522-CBA304.csv">Monthly July 2021 (Revised 120522) (CSV, 30K)</a>'
    assert list(discover.discover_months([html])) == ["2021-07"]


def test_crs_trusts_left_out_of_four_hour_denominator():
    m = build.metrics(raw(att_t1=100, att_t3=20), crs=True)
    assert m["att"] == 120 and m["patt"] == 0 and m["patt_t1"] == 0 and m["crs_att"] == 120
    assert build.in_crs("RGT", "2019-05") and build.in_crs("RGT", "2023-05")
    assert not build.in_crs("RGT", "2023-06") and not build.in_crs("RAA", "2020-01")


def test_crs_end_to_end(tmp_path, monkeypatch):
    raw_dir, map_dir, out = tmp_path / "raw", tmp_path / "map", tmp_path / "out"
    raw_dir.mkdir(); map_dir.mkdir()
    # 2021-06: before ICB mapping; RGT is in the field test and reports no breaches.
    write_csv(raw_dir / "2021-06.csv", "MSitAE-JUNE-2021", [
        ("RAA", "NHS ENGLAND LONDON", "A TRUST", raw(att_t1=100, o4_t1=30)),
        ("RGT", "NHS ENGLAND EAST OF ENGLAND", "CAMBRIDGE", raw(att_t1=50)),
    ])
    write_csv(raw_dir / "2023-06.csv", "MSitAE-JUNE-2023", [
        ("RAA", "NHS ENGLAND LONDON", "A TRUST", raw(att_t1=100, o4_t1=20)),
        ("RGT", "NHS ENGLAND EAST OF ENGLAND", "CAMBRIDGE", raw(att_t1=50, o4_t1=10)),
    ])
    write_mapping(map_dir / "2023-24.xlsx", [("RAA", "QA"), ("RGT", "QB")] + [(f"P{i}", "QX") for i in range(60)])
    # time series: in 2021-06 NHS England's 4-hour denominator leaves out RGT's 50 attendances
    ts = tmp_path / "ts.xlsx"
    write_time_series(ts, {"2021-06": (150, 150, 30, 30, 0, 0), "2023-06": (150, 150, 30, 30, 0, 0)})
    import pandas as pd
    perf = pd.read_excel(ts, sheet_name="Performance", header=None)
    act = pd.read_excel(ts, sheet_name="Activity", header=None)
    perf.iloc[14, 2] = 70; perf.iloc[14, 5] = 70          # 2021-06: <4 h = 100 - 30 (RGT excluded)
    with pd.ExcelWriter(ts) as xw:
        act.to_excel(xw, sheet_name="Activity", header=False, index=False)
        perf.to_excel(xw, sheet_name="Performance", header=False, index=False)
    meta = build.run(raw_dir=str(raw_dir), mapping_dir=str(map_dir), out_dir=str(out),
                     classification={}, trust_codes={"RAA"}, time_series_path=str(ts))
    assert meta["icb_from"] == "2023-04"
    nat = json.load(open(out / "national.json"))
    assert nat["measures"]["pct4"]["all"]["performance"] == [0.7, 0.8]
    assert nat["measures"]["pct4"]["data_status"] == ["excl_crs", "final"]
    assert nat["measures"]["attend"]["all"] == [150, 150]
    rgt = json.load(open(out / "org" / "RGT.json"))
    assert rgt["measures"]["pct4"]["all"]["performance"] == [None, 0.8]
    assert rgt["measures"]["pct4"]["data_status"] == ["crs", "final"]
    assert json.load(open(out / "org" / "QB.json"))["months"] == ["2023-06"]   # no ICB before mapping


def test_crs_trust_reporting_breaches_fails_loud(tmp_path):
    raw_dir, map_dir = tmp_path / "raw", tmp_path / "map"
    raw_dir.mkdir(); map_dir.mkdir()
    write_csv(raw_dir / "2021-06.csv", "MSitAE-JUNE-2021", [("RGT", "X", "CAMBRIDGE", raw(att_t1=50, o4_t1=1))])
    write_mapping(map_dir / "2023-24.xlsx", [(f"P{i}", "QX") for i in range(60)])
    with pytest.raises(AssertionError, match="CRS GATE"):
        build.run(raw_dir=str(raw_dir), mapping_dir=str(map_dir), out_dir=str(tmp_path / "o"),
                  classification={}, trust_codes=set(), time_series_path=None)
