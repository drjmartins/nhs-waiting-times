"""NHS England estimates for non-submitting trusts, and which trusts/ICBs are affected
(DECISIONS 2026-11-20). Offline: a small workbook in the NHS layout is generated here."""
import datetime as dt

import openpyxl
import pytest

from pipeline_rtt import build, discover, estimates

HEAD = ["", "Year", "Month", "Median wait (weeks)", "92nd percentile (weeks)",
        "No. within 18 weeks", "No. within 18 weeks with estimates for missing data",
        "% within 18 weeks", "% within 18 weeks with estimates for missing data",
        "No. > 18 weeks", "No. > 18 weeks with estimates for missing data",
        "No. > 52 weeks", "No. > 52 weeks with estimates for missing data",
        "% > 52 weeks", "% > 52 weeks with estimates for missing data",
        "No. > 65 weeks", "No. > 65 weeks with estimates for missing data",
        "No. > 78 weeks", "No. > 78 weeks with estimates for missing data",
        "No. > 104 weeks", "No. > 104 weeks with estimates for missing data",
        "Total waiting (mil)", "Total waiting (mil) with estimates for missing data"]


def _book(path, rows):
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Full Time Series"
    for _ in range(10):
        ws.append([])
    ws.append(["", "", "", "Incomplete RTT pathways"])
    ws.append(HEAD)
    for month, rep, est in rows:
        r = [""] * len(HEAD)
        r[2] = month
        for k, (a, b) in estimates.COLUMNS.items():
            r[HEAD.index(a)] = rep[k]; r[HEAD.index(b)] = est[k]
        ws.append(r)
    wb.save(path)


REP = {"waitlist": 1000, "pct18": 0.6, "w52": 50, "w65": 5, "w78": 1, "w104": 0}
EST = {"waitlist": 1100, "pct18": 0.59, "w52": 55, "w65": 6, "w78": 1, "w104": 0}


def _nat(months, wl=1000):
    n = len(months)
    return {"months": months, "measures": {
        "waitlist": {"value": [wl] * n}, "pct18": {"performance": [0.6] * n},
        "longwait": {"w52": [50] * n, "w65": [5] * n, "w78": [1] * n, "w104": [0] * n}}}


def test_parse_and_reconcile_publishes_estimates(tmp_path):
    p = tmp_path / "o.xlsx"
    _book(p, [(dt.datetime(2026, 8, 1), REP, EST), ("* Sep-26", REP, EST)])   # annotated month cell
    parsed = estimates.parse(p)
    assert set(parsed) == {"2026-08", "2026-09"}
    blk = estimates.reconcile(_nat(["2026-08", "2026-09"]), parsed)
    assert blk["waitlist"] == {"2026-08": 1100, "2026-09": 1100}
    assert blk["pct18"]["2026-09"] == 0.59 and blk["w52"]["2026-09"] == 55


def test_reconcile_fails_loud_when_our_figures_differ_from_nhs_reported(tmp_path):
    p = tmp_path / "o.xlsx"
    _book(p, [(dt.datetime(2026, 9, 1), REP, EST)])
    with pytest.raises(AssertionError, match="do not match"):
        estimates.reconcile(_nat(["2026-09"], wl=999), estimates.parse(p))


def test_reconcile_fails_loud_when_file_is_behind_our_data(tmp_path):
    p = tmp_path / "o.xlsx"
    _book(p, [(dt.datetime(2026, 8, 1), REP, EST)])
    with pytest.raises(AssertionError, match="BEHIND"):
        estimates.reconcile(_nat(["2026-08", "2026-09"]), estimates.parse(p))


def test_parse_fails_loud_on_changed_layout(tmp_path):
    wb = openpyxl.Workbook(); wb.active.append(["Year", "Month", "Something else"]); wb.save(tmp_path / "x.xlsx")
    with pytest.raises(ValueError, match="layout"):
        estimates.parse(tmp_path / "x.xlsx")


def test_discover_overview_picks_latest_month():
    base = "/statistics/wp-content/uploads/sites/2/2026/"
    html = (f'<a href="{base}10/RTT-Overview-Timeseries-Including-Estimates-for-Missing-Trusts-Aug26-XLS-139K-a1.xlsx">'
            f'<a href="{base}11/RTT-Overview-Timeseries-Including-Estimates-for-Missing-Trusts-Sep26-XLS-140K-ojyW5x.xlsx">')
    month, url = discover.discover_overview([html])
    assert month == "2026-09" and url.endswith("Sep26-XLS-140K-ojyW5x.xlsx") and url.startswith("https://")
    assert discover.discover_overview(["<p>nothing here</p>"]) is None


def _m(total):
    return {"within18": 0, "total": total, "w52": 0, "w65": 0, "w78": 0, "w104": 0}


def test_missing_submissions_flags_trust_gaps_and_affected_icbs():
    months = ["2026-06", "2026-07", "2026-08", "2026-09"]
    orgs = {
        ("provider", "RX1"): {"2026-06": _m(80000), "2026-07": _m(81000), "2026-08": _m(86000)},  # stops
        ("provider", "RK9"): {"2026-06": _m(47000), "2026-08": _m(47500), "2026-09": _m(47000)},  # one-month gap
        ("provider", "NTINY"): {"2026-06": _m(40)},                                              # tiny: ignored
        ("provider", "ROLD"): {"2026-06": _m(9000)},                                             # former: ignored
    }
    contrib = {("QT1", "2026-08"): {"RX1": 70000, "RZZ": 400000},
               ("QBIG", "2026-08"): {"RX1": 900, "RYY": 2000000},          # too small a share
               ("QK9", "2026-06"): {"RK9": 40000, "RAA": 100000}}
    names = {("provider", "RX1"): "NOTTINGHAM", ("provider", "RK9"): "PLYMOUTH"}
    cls = {"ROLD": {"status": "former"}}
    prov, icb = build.missing_submissions(orgs, contrib, months, names, cls)
    assert prov["RX1"] == [{"from": "2026-09", "to": None, "last_waitlist": 86000}]
    assert prov["RK9"] == [{"from": "2026-07", "to": "2026-07", "last_waitlist": 47000}]
    assert "NTINY" not in prov and "ROLD" not in prov
    assert icb["QT1"] == [{"code": "RX1", "name": "NOTTINGHAM", "from": "2026-09", "to": None,
                           "last_contribution": 70000}]
    assert "QBIG" not in icb
    assert icb["QK9"][0]["code"] == "RK9" and icb["QK9"][0]["to"] == "2026-07"


def test_process_zip_records_provider_contribution_per_icb(tmp_path):
    from collections import defaultdict
    from tests_rtt.test_build import row, write_zip
    zp = tmp_path / "Mar26.zip"
    write_zip(zp, [row(prov="RAA", icb="QAA", bandvals={0: 7}), row(prov="RBB", icb="QAA", bandvals={0: 3}),
                   row(prov="RAA", icb="QAA", tf="C_100", bandvals={0: 7})])
    contrib = {}
    build.process_zip(zp, defaultdict(build._blank_metrics), defaultdict(build._blank_metrics), {}, {}, contrib)
    assert contrib == {("QAA", "2026-03"): {"RAA": 7, "RBB": 3}}      # C_999 only


def test_missing_submissions_only_counts_gaps_nhs_england_also_estimated():
    months = ["2026-06", "2026-07", "2026-08", "2026-09"]
    orgs = {("provider", "RX1"): {"2026-06": _m(86000), "2026-07": _m(86000)},  # NHS estimated it from Aug
            ("provider", "RCOM"): {"2026-06": _m(5000)}}    # gone from Jul, when NHS added nothing:
    gap = {"2026-07": 0, "2026-08": 86000, "2026-09": 86000}   # stopped reporting RTT, not missing
    prov, _ = build.missing_submissions(orgs, {}, months, {}, nhs_gap=gap)
    assert set(prov) == {"RX1"}
