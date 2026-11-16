"""Unit tests for the RTT pipeline (pipeline_rtt). Offline — synthetic CSV."""
import csv
import io
import os
import zipfile
import tempfile

import pytest

from pipeline_rtt import build, config


# Build a header matching the real Full CSV: 13 id cols, 104 'Gt NN To MM' bands,
# 'Gt 104 Weeks', then Total / unknown / Total All.
def make_header():
    ids = ["Period", "Provider Parent Org Code", "Provider Parent Name",
           "Provider Org Code", "Provider Org Name", "Commissioner Parent Org Code",
           "Commissioner Parent Name", "Commissioner Org Code", "Commissioner Org Name",
           "RTT Part Type", "RTT Part Description", "Treatment Function Code",
           "Treatment Function Name"]
    bands = [f"Gt {w:02d} To {w+1:02d} Weeks SUM 1" for w in range(0, 104)]
    bands.append("Gt 104 Weeks SUM 1")
    return ids + bands + ["Total", "Patients with unknown clock start date", "Total All"]


HEADER = make_header()


def row(part="Part_2", tf="C_999", prov="RAA", icb="QAA", comm="QAA",
        bandvals=None, comm_par_name="NHS TEST ICB"):
    """One CSV row. bandvals = dict {lower_week: count}."""
    r = ["RTT-March-2026", "RPAR", "PARENT", prov, prov + " TRUST",
         icb, comm_par_name, comm, comm + " SUB", part, "Incomplete Pathways", tf, "Total"]
    bands = [0] * 105
    for w, v in (bandvals or {}).items():
        bands[w if w < 104 else 104] = v
    return r + bands + ["", "", str(sum(bands))]


def write_zip(path, rows):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(HEADER)
    for r in rows:
        w.writerow(r)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("extract.csv", buf.getvalue())


def test_parse_period():
    assert build.parse_period("RTT-March-2026") == "2026-03"
    assert build.parse_period("RTT-April-2025") == "2025-04"
    with pytest.raises(ValueError):
        build.parse_period("not-a-period")


def test_band_resolution():
    cols = build.resolve_columns(HEADER)
    assert len(cols["bands"]) == 105
    assert len(cols["b18"]) == 18                  # weeks 0..17 => within 18
    # 52+ band set must exclude the single 104 catch-all double-count check
    assert HEADER[cols["w104"][0]] == "Gt 104 Weeks SUM 1"
    assert len(cols["w104"]) == 1


def _run(rows):
    from collections import defaultdict
    with tempfile.TemporaryDirectory() as d:
        zp = os.path.join(d, "Mar26.zip")
        write_zip(zp, rows)
        store = defaultdict(build._blank_metrics)
        bd_store = defaultdict(build._blank_metrics)
        names, tf_names = {}, {}
        build.process_zip(zp, store, bd_store, names, tf_names)
    return store, bd_store, tf_names


def test_derivation_and_nonc():
    # all-TF C_999 row: 10 within 18wk, plus one each at 52/65/78/104
    bands = {0: 5, 17: 5, 52: 3, 65: 2, 78: 1, 104: 4}
    rows = [
        row(tf="C_999", bandvals=bands),
        # a NONC row that must be excluded from every aggregate
        row(tf="C_999", prov="RAA", icb="NONC", comm="NONC", comm_par_name="NONC", bandvals={0: 999}),
    ]
    store, _, _ = _run(rows)
    nat = store[("national", "ENG", "2026-03")]
    assert nat["total"] == 5 + 5 + 3 + 2 + 1 + 4          # NONC's 999 excluded
    assert nat["within18"] == 10
    assert nat["w52"] == 3 + 2 + 1 + 4                    # all bands >=52
    assert nat["w65"] == 2 + 1 + 4
    assert nat["w78"] == 1 + 4
    assert nat["w104"] == 4                               # the single catch-all band


def test_breakdown_routing_and_tf_names():
    # C_999 feeds core; individual TFs feed the breakdown store
    rows = [
        row(tf="C_999", bandvals={0: 10}),
        row(tf="C_100", bandvals={0: 6}),
        row(tf="X02", bandvals={0: 4}),
    ]
    store, bd, tf_names = _run(rows)
    assert store[("national", "ENG", "2026-03")]["total"] == 10
    assert bd[("national", "ENG", "C_100", "2026-03")]["total"] == 6
    assert bd[("national", "ENG", "X02", "2026-03")]["total"] == 4
    assert ("C_999" not in tf_names) and "C_100" in tf_names    # C_999 isn't a selectable TF


def test_tf_sum_gate_pass_and_fail():
    store = {("provider", "RAA", "2026-03"): {"within18": 10, "total": 10, "w52": 0, "w65": 0, "w78": 0, "w104": 0}}
    bd_ok = {("provider", "RAA"): {"C_100": {"2026-03": {"within18": 6, "total": 6, "w52": 0, "w65": 0, "w78": 0, "w104": 0}},
                                    "X02":   {"2026-03": {"within18": 4, "total": 4, "w52": 0, "w65": 0, "w78": 0, "w104": 0}}}}
    assert build.reconcile_tf(store, bd_ok)["max_abs_delta"] == 0
    bd_bad = {("provider", "RAA"): {"C_100": {"2026-03": {"within18": 6, "total": 5, "w52": 0, "w65": 0, "w78": 0, "w104": 0}}}}
    with pytest.raises(AssertionError):
        build.reconcile_tf(store, bd_bad)


def test_build_tf_payload():
    bm = {"2025-03": {"within18": 3, "total": 6, "w52": 2, "w65": 1, "w78": 0, "w104": 0}}
    p = build.build_tf_payload("General Surgery Service", bm)
    assert p["name"] == "General Surgery Service"
    assert p["measures"]["pct18"]["performance"] == [0.5]
    assert p["measures"]["waitlist"]["value"] == [6]
    assert p["measures"]["longwait"]["w52"] == [2]
    assert "data_status" not in p["measures"]["pct18"]   # omitted in the breakdown


def test_payload_shape():
    by_month = {"2025-03": {"within18": 6, "total": 10, "w52": 4, "w65": 2, "w78": 1, "w104": 0},
                "2025-04": {"within18": 7, "total": 10, "w52": 3, "w65": 1, "w78": 0, "w104": 0}}
    p = build.build_org_payload("RAA", "TEST TRUST", "provider", by_month)
    assert p["months"] == ["2025-03", "2025-04"]
    assert p["measures"]["pct18"]["performance"] == [0.6, 0.7]
    assert p["measures"]["waitlist"]["value"] == [10, 10]
    assert p["measures"]["longwait"]["w52"] == [4, 3]
    assert p["measures"]["pct18"]["target"] == config.STANDARD_TARGET
    assert p["region"] == "England"   # default when no region supplied


def test_payload_region_passthrough():
    by_month = {"2025-04": {"within18": 7, "total": 10, "w52": 3, "w65": 1, "w78": 0, "w104": 0}}
    p = build.build_org_payload("RCF", "AIREDALE", "provider", by_month, region="North East and Yorkshire")
    assert p["region"] == "North East and Yorkshire"


def test_region_map_reuses_cancer_index(tmp_path):
    # RTT has no provider region of its own; it reuses the cancer dashboard's
    # (Parent_Org-derived) region, keyed by org code. Real regions map through;
    # "England" / region-less / ICB-only codes do not.
    import json
    from pipeline_common import regions
    idx = tmp_path / "index.json"
    idx.write_text(json.dumps([
        {"code": "RCF", "level": "provider", "region": "North East and Yorkshire"},
        {"code": "RXX", "level": "provider", "region": "England"},   # region-less provider
        {"code": "QWO", "level": "icb", "region": "England"},        # ICB -> never mapped
    ]))
    m = regions.load_region_map(str(idx))
    assert m == {"RCF": "North East and Yorkshire"}
    # fail-open: missing file -> empty map (build defaults everything to "England")
    assert regions.load_region_map(str(tmp_path / "nope.json")) == {}


def _nat_payload(pct, wl, w52, w65, w78, w104):
    return {"months": ["2025-04"],
            "measures": {"pct18": {"performance": [pct]},
                         "waitlist": {"value": [wl]},
                         "longwait": {"w52": [w52], "w65": [w65], "w78": [w78], "w104": [w104]}}}


def test_reconcile_pass():
    # the real measured Apr-2025 values (revised extract) — must pass the gate
    r = build.reconcile(_nat_payload(0.5973, 7_389_065, 190_023, 9_244, 1_361, 171))
    assert r["checked"] is True


def test_reconcile_fail():
    with pytest.raises(AssertionError):
        build.reconcile(_nat_payload(0.40, 7_389_065, 190_023, 9_244, 1_361, 171))   # pct way off


def test_build_fails_loud_and_writes_nothing_on_bad_gate():
    # A C_999 total of 100 with treatment functions summing to only 50 violates the
    # TF-sum gate. The build MUST raise BEFORE writing any site file (no stale/partial
    # publish) — the gates run up-front in build.run().
    rows = [
        row(tf="C_999", bandvals={0: 100}),
        row(tf="C_100", bandvals={0: 50}),     # sum of TFs (50) != C_999 total (100)
    ]
    with tempfile.TemporaryDirectory() as d:
        raw = os.path.join(d, "raw"); out = os.path.join(d, "out")
        os.makedirs(raw)
        write_zip(os.path.join(raw, "2026-03.zip"), rows)
        with pytest.raises(AssertionError):
            build.run(raw_dir=raw, out_dir=out)
        # nothing published: no meta/index/national written
        assert not os.path.exists(os.path.join(out, "meta.json"))
        assert not os.path.exists(os.path.join(out, "index.json"))
        assert not os.path.exists(os.path.join(out, "national.json"))
