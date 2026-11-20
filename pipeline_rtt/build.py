"""
RTT site-data builder.

Reads every monthly "Full CSV data file" zip in RAW_DIR, derives the three
headline measures from the wait-band columns for the Incomplete pathway
(Part_2) / all-treatment-function (C_999) rows, and writes one JSON per org
(+ index/national/meta) into SITE_DATA_DIR. Finishes with the fail-loud
national reconciliation gate against the published SPN headline.

Aggregation (excl NONC):
  national  = sum of all non-NONC Part_2/C_999 rows
  provider  = grouped by Provider Org Code   (summed across commissioners)
  icb       = grouped by Commissioner Parent  (the ICB; summed across providers)
"""
import csv
import io
import json
import os
import re
import zipfile
import datetime as dt
from collections import defaultdict

from . import config
from . import estimates as nhs_estimates
from pipeline_common import ods
from pipeline_common import regions


# --- wait-band column resolution -------------------------------------------

def _band_lower(header_name):
    """Lower bound (weeks) of a 'Gt NN To MM Weeks' / 'Gt 104 Weeks' column."""
    m = re.match(r"Gt (\d+)", header_name)
    return int(m.group(1)) if m else None


def resolve_columns(header):
    """Return the index sets the builder needs, derived from the CSV header."""
    idx = {h: i for i, h in enumerate(header)}
    bands = [i for i, h in enumerate(header) if h.startswith("Gt ")]
    cols = {
        "period": idx["Period"],
        "prov_code": idx["Provider Org Code"],
        "prov_name": idx["Provider Org Name"],
        "comm_code": idx["Commissioner Org Code"],
        "comm_par_code": idx["Commissioner Parent Org Code"],
        "comm_par_name": idx["Commissioner Parent Name"],
        "part": idx["RTT Part Type"],
        "tf": idx["Treatment Function Code"],
        "bands": bands,
        "b18": [i for i in bands if _band_lower(header[i]) < 18],
    }
    for t in config.LONGWAIT_THRESHOLDS:
        cols[f"w{t}"] = [i for i in bands if _band_lower(header[i]) >= t]
    return cols


def parse_period(period):
    """'RTT-March-2026' -> '2026-03'."""
    m = re.search(r"([A-Za-z]+)-(\d{4})", period)
    if not m:
        raise ValueError(f"unrecognised Period: {period!r}")
    mon = config.MONTHS.get(m.group(1).lower())
    if not mon:
        raise ValueError(f"unrecognised month in Period: {period!r}")
    return f"{m.group(2)}-{mon:02d}"


def _sum(row, cols):
    s = 0
    for c in cols:
        v = row[c]
        if v:
            s += int(v)
    return s


def _is_nonc(comm_code, comm_par_code):
    return config.NONC_MARKER in comm_code or config.NONC_MARKER in comm_par_code


def _blank_metrics():
    return {"within18": 0, "total": 0, "w52": 0, "w65": 0, "w78": 0, "w104": 0}


def _zip_month(path):
    """Read just the first data row's Period -> 'YYYY-MM' (for dedup)."""
    z = zipfile.ZipFile(path)
    with z.open(z.namelist()[0]) as fh:
        reader = csv.reader(io.TextIOWrapper(fh, encoding="utf-8-sig"))
        period_i = next(reader).index("Period")
        for row in reader:
            return parse_period(row[period_i])
    return None


def process_zip(path, store, bd_store, names, tf_names, contrib=None):
    """Fold one monthly zip into the core `store` (keyed (level, code, month)) and
    the per-treatment-function `bd_store` (keyed (level, code, tf, month)).

    The C_999 rows feed the core store (the published all-TF total); the 23
    individual treatment-function rows feed the breakdown store. The TF-sum gate
    (reconcile_tf) later asserts the breakdown sums back to the core total.

    `contrib` (optional) collects each provider's all-TF metrics per ICB per month,
    {(icb, month): {provider: metrics}}, so an ICB can say which of its providers did
    not submit (missing_submissions) and carry their figures forward (icb_estimates)."""
    z = zipfile.ZipFile(path)
    member = z.namelist()[0]
    with z.open(member) as fh:
        reader = csv.reader(io.TextIOWrapper(fh, encoding="utf-8-sig"))
        cols = resolve_columns(next(reader))
        month = None
        for row in reader:
            if row[cols["part"]] != "Part_2":
                continue
            if month is None:
                month = parse_period(row[cols["period"]])
            if _is_nonc(row[cols["comm_code"]], row[cols["comm_par_code"]]):
                continue
            tf = row[cols["tf"]]
            m = {
                "within18": _sum(row, cols["b18"]), "total": _sum(row, cols["bands"]),
                "w52": _sum(row, cols["w52"]), "w65": _sum(row, cols["w65"]),
                "w78": _sum(row, cols["w78"]), "w104": _sum(row, cols["w104"]),
            }
            pcode = row[cols["prov_code"]]
            icb = row[cols["comm_par_code"]]
            targets = [("national", "ENG"), ("provider", pcode)]
            if icb:                       # hub rows have a blank ICB parent
                targets.append(("icb", icb))
                if contrib is not None and tf == "C_999":
                    c = contrib.setdefault((icb, month), {}).setdefault(pcode, _blank_metrics())
                    for k, v in m.items():
                        c[k] += v
            for level, code in targets:
                if tf == "C_999":
                    acc = store[(level, code, month)]
                else:
                    acc = bd_store[(level, code, tf, month)]
                for k, v in m.items():
                    acc[k] += v
            if tf != "C_999":
                tf_names[tf] = row[cols["tf"] + 1]   # Treatment Function Name
            names[("provider", pcode)] = row[cols["prov_name"]]
            if icb:
                names[("icb", icb)] = row[cols["comm_par_name"]]
    return month


def build_org_payload(code, name, level, by_month, region="England"):
    """Assemble one org's JSON from its {month: metrics} dict."""
    months = sorted(by_month)
    pct_perf, pct_w, pct_t, status = [], [], [], []
    wl, w52, w65, w78, w104 = [], [], [], [], []
    for mo in months:
        m = by_month[mo]
        pct_w.append(m["within18"]); pct_t.append(m["total"])
        pct_perf.append(round(m["within18"] / m["total"], 4) if m["total"] else None)
        status.append("final")
        wl.append(m["total"])
        w52.append(m["w52"]); w65.append(m["w65"]); w78.append(m["w78"]); w104.append(m["w104"])
    return {
        "code": code, "name": name, "level": level, "region": region,
        "months": months,
        "measures": {
            "pct18": {"performance": pct_perf, "within_target": pct_w,
                       "total": pct_t, "data_status": status, "target": config.STANDARD_TARGET},
            "waitlist": {"value": wl, "data_status": status},
            "longwait": {"w52": w52, "w65": w65, "w78": w78, "w104": w104,
                          "total": wl, "data_status": status},
        },
    }


# Compact per-TF series for the breakdown file: parallel arrays per measure, no
# data_status (RTT has no provisional flag — the front end treats these as final)
# and no target (it reads config.STANDARD_TARGET). Mirrors core's measures shape
# so the front end can reuse the same series accessors.
def build_tf_payload(name, by_month):
    months = sorted(by_month)
    perf, w, t, wl, w52, w65, w78, w104 = [], [], [], [], [], [], [], []
    for mo in months:
        m = by_month[mo]
        w.append(m["within18"]); t.append(m["total"])
        perf.append(round(m["within18"] / m["total"], 4) if m["total"] else None)
        wl.append(m["total"])
        w52.append(m["w52"]); w65.append(m["w65"]); w78.append(m["w78"]); w104.append(m["w104"])
    return {"name": name, "months": months, "measures": {
        "pct18": {"performance": perf, "within_target": w, "total": t},
        "waitlist": {"value": wl},
        "longwait": {"w52": w52, "w65": w65, "w78": w78, "w104": w104},
    }}


def reconcile_tf(store, bd_orgs):
    """Fail-loud TF-sum gate: for every (level, code, month) the sum across the
    individual treatment functions MUST equal the published C_999 total (verified
    exact in source). Same sum-to-total discipline as cancer's cancer-group gate."""
    keys = ("within18", "total", "w52", "w65", "w78", "w104")
    checked = 0
    worst = 0
    for (level, code, month), tot in store.items():
        summed = {k: 0 for k in keys}
        for tf, by_month in bd_orgs.get((level, code), {}).items():
            if month in by_month:
                for k in keys:
                    summed[k] += by_month[month][k]
        for k in keys:
            d = abs(summed[k] - tot[k])
            worst = max(worst, d)
            if d != 0:
                raise AssertionError(
                    f"TF-SUM GATE FAILED at {level}/{code} {month} [{k}]: "
                    f"sum-of-TFs={summed[k]:,} vs C_999={tot[k]:,} (Δ={summed[k]-tot[k]})")
        checked += 1
    print(f"  TF-sum gate OK: {checked:,} org-months reconcile exactly (max|Δ|={worst})")
    return {"checked": checked, "max_abs_delta": worst}


def _negligible(by_month, all_months, code, classification):
    """A provider is hidden if its max monthly waiting list over the last
    PICKER_PROVIDER_WINDOW_MONTHS stays below PICKER_MIN_PROVIDER_WAITLIST.

    YOUNG-ORG PROTECTION (2026-06-22): an org that is CURRENT per ODS and whose
    series first appears within YOUNG_WINDOW_MONTHS is thin because newly created,
    not dormant — checked first and never hidden (so it shows from month one)."""
    months = sorted(all_months)
    young_cutoff = months[-config.YOUNG_WINDOW_MONTHS] if len(months) >= config.YOUNG_WINDOW_MONTHS else months[0]
    first_month = min(by_month) if by_month else ""
    if not ods.is_former(classification.get(code)) and first_month >= young_cutoff:
        return False
    recent = months[-config.PICKER_PROVIDER_WINDOW_MONTHS:]
    peak = max((by_month[mo]["total"] for mo in recent if mo in by_month), default=0)
    return peak < config.PICKER_MIN_PROVIDER_WAITLIST


def _runs(months):
    """Consecutive runs in a sorted list of month indices -> [(first_i, last_i), ...]."""
    out = []
    for i in months:
        if out and i == out[-1][1] + 1:
            out[-1][1] = i
        else:
            out.append([i, i])
    return [tuple(r) for r in out]


def missing_submissions(orgs, contrib, all_months, names, classification=None, trust_codes=None,
                        nhs_gap=None):
    """Which providers did not submit, and which ICBs that affects (DECISIONS 2026-11-20).

    Candidate gap: a provider with data in an earlier month and none in month m, that is
    not a FORMER organisation per ODS (closed/merged orgs stop for a reason), whose last
    submission before the gap had at least MISSING_MIN_WAITLIST patients.

    `nhs_gap` ({month: NHS England's estimated minus reported waiting list}) then decides
    which candidates are real non-submissions, by accounting for NHS's own gap: candidates
    are taken largest first and kept only if NHS's still-unexplained gap covers the
    provider's last submission in EVERY month of its gap. That rules out providers that
    stopped for reasons NHS does not estimate for (a merger ODS has not caught up with,
    community services leaving RTT in Feb 2024, mental-health trusts and small
    independent clinics that stopped reporting). Checked on all 54 months to Sep 2026: the
    providers kept sum exactly to NHS's gap in every month except Apr 2022 (a provider
    already missing in the first month of the series, which a gap rule cannot see).
    Without `nhs_gap`, only NHS trusts are considered and nothing is cross-checked.

    Gaps are reported as runs; `to` is None while the gap runs to the latest month. An ICB
    lists a provider when its last contribution to that ICB before the gap reached
    MISSING_MIN_WAITLIST and MISSING_MIN_ICB_SHARE of the ICB's list in that month.
    Returns (by_provider, by_icb): {code: [ {from, to, last_waitlist} ]},
                                   {icb: [ {code, name, from, to, last_contribution} ]}."""
    classification = classification or {}
    trust_codes = trust_codes or set()
    pos = {m: i for i, m in enumerate(all_months)}
    last_i = len(all_months) - 1
    candidates = []                     # (last_waitlist, code, a, b)
    for (level, code), by_month in orgs.items():
        if level != "provider" or not by_month:
            continue
        if ods.is_former(classification.get(code)):
            continue
        if nhs_gap is None and trust_codes and code not in trust_codes:
            continue
        have = {pos[m] for m in by_month if m in pos}
        first = min(have)
        gaps = [i for i in range(first, last_i + 1) if i not in have]
        for a, b in _runs(gaps):
            last_wl = by_month[all_months[a - 1]]["total"]
            if last_wl >= config.MISSING_MIN_WAITLIST:
                candidates.append((last_wl, code, a, b))
    remaining = dict(nhs_gap) if nhs_gap is not None else None
    by_provider, by_icb = {}, {}
    for last_wl, code, a, b in sorted(candidates, key=lambda c: (-c[0], c[1], c[2])):
        span = [all_months[i] for i in range(a, b + 1)]
        if remaining is not None:
            if any(remaining.get(m, 0) < last_wl for m in span):
                continue
            for m in span:
                remaining[m] -= last_wl
        prev = all_months[a - 1]
        run = {"from": all_months[a], "to": None if b == last_i else all_months[b], "last_waitlist": last_wl}
        by_provider.setdefault(code, []).append(run)
        # Which ICBs relied on this provider just before the gap?
        for (icb, mo), provs in contrib.items():
            if mo != prev or code not in provs:
                continue
            share = provs[code]["total"]
            icb_total = sum(p["total"] for p in provs.values())
            if share >= config.MISSING_MIN_WAITLIST and share >= config.MISSING_MIN_ICB_SHARE * icb_total:
                by_icb.setdefault(icb, []).append({
                    "code": code, "name": names.get(("provider", code), code),
                    "from": run["from"], "to": run["to"], "last_contribution": share})
    for v in by_provider.values():
        v.sort(key=lambda r: r["from"])
    for v in by_icb.values():
        v.sort(key=lambda r: (r["from"], r["name"]))
    return by_provider, by_icb


def icb_estimates(orgs, contrib, by_provider, by_icb, all_months):
    """ICB figures with an estimate for providers that did not submit (DECISIONS 2026-11-20).

    NHS England publishes no ICB-level estimate, so this applies NHS England's national
    method at ICB level: for each month a provider missed, add the figures it last
    submitted for THAT ICB (its contribution in the month before the gap) to the ICB's
    reported figures. All missing providers' contributions are added, but only ICBs with
    a material exclusion (an entry in `by_icb`) get an estimate, so a few patients from
    a distant provider don't put an estimate on every ICB. Every month of the ICB's
    series is included (estimate == reported where nothing is missing), like England.
    Returns {icb: {"waitlist"|"pct18"|"w52"|"w65"|"w78"|"w104": {month: value}}}."""
    pos = {m: i for i, m in enumerate(all_months)}
    add = {}                                         # (icb, month) -> metrics added
    for code, runs in by_provider.items():
        for r in runs:
            a = pos[r["from"]]
            b = pos[r["to"]] if r["to"] else len(all_months) - 1
            prev = all_months[a - 1]
            for (icb, mo), provs in contrib.items():
                if mo != prev or code not in provs:
                    continue
                for i in range(a, b + 1):
                    acc = add.setdefault((icb, all_months[i]), _blank_metrics())
                    for k, v in provs[code].items():
                        acc[k] += v
    out = {}
    for icb in by_icb:
        base = orgs.get(("icb", icb), {})
        est = {"waitlist": {}, "pct18": {}, "w52": {}, "w65": {}, "w78": {}, "w104": {}}
        for mo in sorted(base):
            m = dict(base[mo])
            for k, v in add.get((icb, mo), {}).items():
                m[k] += v
            est["waitlist"][mo] = m["total"]
            est["pct18"][mo] = round(m["within18"] / m["total"], 4) if m["total"] else None
            for k in ("w52", "w65", "w78", "w104"):
                est[k][mo] = m[k]
        out[icb] = est
    return out, add


def run(raw_dir=config.RAW_DIR, out_dir=config.SITE_DATA_DIR, classification=None, trust_codes=None,
        estimates_path=None):
    classification = classification or {}   # ODS org-status; absent → current (fail-open)
    trust_codes = trust_codes or set()      # NHS-trust code set for the provider-type filter
    # Provider NHS region, reused from the cancer dashboard (the RTT source has none).
    # Fail-open: {} if the cancer index is missing → providers default to "England".
    region_map = regions.load_region_map(config.CANCER_INDEX_PATH)
    zips = sorted(f for f in os.listdir(raw_dir) if f.endswith(".zip"))
    if not zips:
        raise RuntimeError(f"no zips in {raw_dir}")
    store = defaultdict(_blank_metrics)
    bd_store = defaultdict(_blank_metrics)
    names = {}
    tf_names = {}
    contrib = {}
    months_seen = set()
    for zf in zips:
        # Peek the Period without folding, so two files for the SAME month (e.g. a
        # stale Mon-token zip beside a YYYY-MM one) can't be double-counted.
        peek = _zip_month(os.path.join(raw_dir, zf))
        if peek in months_seen:
            print(f"  SKIP {zf}: month {peek} already processed (duplicate file)")
            continue
        mo = process_zip(os.path.join(raw_dir, zf), store, bd_store, names, tf_names, contrib)
        months_seen.add(mo)
        print(f"  processed {zf} -> {mo}")
    all_months = sorted(months_seen)

    # regroup core store -> {(level,code): {month: metrics}}
    orgs = defaultdict(dict)
    for (level, code, month), m in store.items():
        orgs[(level, code)][month] = m
    # regroup breakdown store -> {(level,code): {tf: {month: metrics}}}
    bd_orgs = defaultdict(lambda: defaultdict(dict))
    for (level, code, tf, month), m in bd_store.items():
        bd_orgs[(level, code)][tf][month] = m

    # BOTH gates fire BEFORE any file is written, so a failed gate fails the build
    # loudly and never publishes stale/partial data (the SPN headline gate needs the
    # national payload, built up-front here).
    if ("national", "ENG") not in orgs:
        raise RuntimeError("no national (ENG) aggregate built — refusing to publish")
    national_payload = build_org_payload("ENG", "England", "national", orgs[("national", "ENG")])
    recon = reconcile(national_payload)            # national SPN headline gate
    tf_recon = reconcile_tf(store, bd_orgs)        # sum-of-TFs == C_999 gate
    # Third gate: our England figures must equal NHS England's REPORTED figures in the
    # overview workbook, month by month; then publish NHS's ESTIMATED figures beside ours.
    if estimates_path:
        national_payload["estimates"] = nhs_estimates.reconcile(
            national_payload, nhs_estimates.parse(estimates_path))
    nhs_gap = None
    if "estimates" in national_payload:
        rep = dict(zip(national_payload["months"], national_payload["measures"]["waitlist"]["value"]))
        nhs_gap = {m: v - rep[m] for m, v in national_payload["estimates"]["waitlist"].items() if m in rep}
    not_submitted, icb_excludes = missing_submissions(
        orgs, contrib, all_months, names, classification, trust_codes, nhs_gap)
    if nhs_gap is not None:      # report (not a gate): do the flagged trusts explain NHS's gap?
        last = all_months[-1]
        ours = sum(r["last_waitlist"] for runs in not_submitted.values() for r in runs if r["to"] is None)
        print(f"  Missing trusts {last}: {sorted(c for c, rs in not_submitted.items() if any(r['to'] is None for r in rs))}"
              f" last submissions sum {ours:,} vs NHS estimate gap {nhs_gap.get(last, 0):,}"
              + ("" if ours == nhs_gap.get(last, 0) else "  (WARN: not an exact match; notes may be incomplete)"))
    icb_est, icb_add = icb_estimates(orgs, contrib, not_submitted, icb_excludes, all_months)
    # Fail-loud arithmetic check: what the ICBs add can never exceed what the missing providers
    # last submitted in total (the difference is their hub / blank-ICB rows, normally small).
    for mo in all_months:
        added = sum(v["total"] for (icb, m), v in icb_add.items() if m == mo)
        missing = sum(r["last_waitlist"] for rs in not_submitted.values() for r in rs
                      if r["from"] <= mo <= (r["to"] or all_months[-1]))
        if added > missing:
            raise AssertionError(f"ICB ESTIMATES FAILED ({mo}): ICBs add {added:,} but missing providers "
                                 f"last submitted only {missing:,} — refusing to publish")
    if icb_est:
        print(f"  ICB estimates for {len(icb_est)} ICB(s): {sorted(icb_est)}")

    org_dir = os.path.join(out_dir, "org")
    os.makedirs(org_dir, exist_ok=True)
    index = []
    national_bd = None
    for (level, code), by_month in sorted(orgs.items()):
        bd = bd_orgs.get((level, code), {})
        bd_payload = {"code": code, "level": level,
                      "tfs": {tf: build_tf_payload(tf_names.get(tf, tf), bm)
                              for tf, bm in sorted(bd.items())}}
        if level == "national":
            national_bd = bd_payload
            continue
        name = names.get((level, code), code)
        # Region only for providers (matches cancer, where ICBs stay "England");
        # unknown providers fail-open to "England" → front end shows no region.
        region = region_map.get(code, "England") if level == "provider" else "England"
        payload = build_org_payload(code, name, level, by_month, region=region)
        if level == "provider" and code in not_submitted:
            payload["not_submitted"] = not_submitted[code]
        if level == "icb" and code in icb_excludes:
            payload["excludes"] = icb_excludes[code]
        if level == "icb" and code in icb_est:
            payload["estimates"] = icb_est[code]
        with open(os.path.join(org_dir, f"{code}.json"), "w") as f:
            json.dump(payload, f, separators=(",", ":"))
        with open(os.path.join(org_dir, f"{code}.breakdown.json"), "w") as f:
            json.dump(bd_payload, f, separators=(",", ":"))
        entry = {"code": code, "name": name, "level": level, "region": region}
        if level == "provider" and _negligible(by_month, all_months, code, classification):
            entry["hidden"] = True
        # 'Formed' note only when the DATA series is genuinely truncated AND a
        # predecessor recently closed (a real handoff, not an old merger).
        ce = classification.get(code)
        formed_ok = (ods.series_truncated(min(by_month) if by_month else "", all_months)
                     and ods.formed_recently(ce, classification, all_months[0]))
        ods.annotate_entry(entry, ce, formed_ok=formed_ok)
        if level == "provider":
            ods.tag_provider_type(entry, trust_codes)
        index.append(entry)

    index.sort(key=lambda e: (e["level"], e["name"]))
    # Fail-loud paired check: a populated ODS trust set must tag >0 independents.
    ods.assert_independents_tagged(index, "RTT", trust_codes)
    with open(os.path.join(out_dir, "index.json"), "w") as f:
        json.dump(index, f, separators=(",", ":"))
    with open(os.path.join(out_dir, "national.json"), "w") as f:
        json.dump(national_payload, f, separators=(",", ":"))
    with open(os.path.join(out_dir, "national.breakdown.json"), "w") as f:
        json.dump(national_bd, f, separators=(",", ":"))

    n_prov = sum(1 for e in index if e["level"] == "provider")
    n_icb = sum(1 for e in index if e["level"] == "icb")
    n_hidden = sum(1 for e in index if e.get("hidden"))
    meta = {
        "built_at": dt.datetime.utcnow().isoformat() + "Z",
        "months": all_months,
        "month_range": [all_months[0], all_months[-1]],
        "source": config.SOURCE_INDEX,
        "standard_target": config.STANDARD_TARGET,
        "recovery_milestones": config.RECOVERY_MILESTONES,
        "series_break": config.SERIES_BREAK,
        "measures": config.MEASURES,
        "counts": {"providers": n_prov, "icbs": n_icb, "hidden": n_hidden},
        # Treatment-function taxonomy for the breakdown selector: named specialties
        # (code C_NNN) first, then the five X0x "Other –" buckets, each tagged with a
        # group so the front end can mirror cancer's grouped picker.
        "treatment_functions": [
            {"code": c, "name": tf_names[c], "group": "Other" if c.startswith("X") else "Specialty"}
            for c in sorted(tf_names, key=lambda c: (c.startswith("X"), c))
        ],
        "tf_reconciliation": tf_recon,
        "reconciliation": recon,
    }
    with open(os.path.join(out_dir, "meta.json"), "w") as f:
        json.dump(meta, f, indent=2)
    print(f"\nWrote {len(index)} orgs ({n_prov} providers [{n_hidden} hidden], "
          f"{n_icb} ICBs) + national, months {all_months[0]}..{all_months[-1]}")
    return meta


def reconcile(national_payload):
    """Fail-loud national gate against the published SPN headline."""
    g = config.RECON_GATE
    months = national_payload["months"]
    if g["month"] not in months:
        print(f"  recon: gate month {g['month']} not in data, skipped")
        return {"checked": False, "reason": "gate month absent"}
    i = months.index(g["month"])
    pct = national_payload["measures"]["pct18"]["performance"][i]
    wl = national_payload["measures"]["waitlist"]["value"][i]
    lw = national_payload["measures"]["longwait"]
    got = {"pct18": pct, "waitlist": wl, "w52": lw["w52"][i], "w65": lw["w65"][i],
           "w78": lw["w78"][i], "w104": lw["w104"][i]}
    fails = []
    if abs(got["pct18"] - g["pct18"]) > g["pct18_tol"]:
        fails.append(f"pct18 {got['pct18']:.4f} vs {g['pct18']} (>{g['pct18_tol']})")
    if abs(got["waitlist"] - g["waitlist"]) > g["waitlist_tol"]:
        fails.append(f"waitlist {got['waitlist']:,} vs ~{g['waitlist']:,}")
    for k in ("w52", "w65", "w78", "w104"):
        if abs(got[k] - g[k]) > max(1, g[k] * g["count_rel_tol"]):
            fails.append(f"{k} {got[k]:,} vs {g[k]:,} (>{g['count_rel_tol']:.0%})")
    if fails:
        raise AssertionError("RECONCILIATION GATE FAILED (" + g["month"] + "): " + "; ".join(fails))
    print(f"  recon OK @ {g['month']}: pct18={got['pct18']:.4f} waitlist={got['waitlist']:,} "
          f"w52={got['w52']:,} w65={got['w65']:,} w78={got['w78']:,} w104={got['w104']:,}")
    return {"checked": True, "month": g["month"], **got}


if __name__ == "__main__":
    run()
