"""
Export step: tidy DataFrame -> artefacts the static site consumes.

Outputs:
  site/data/index.json          - list of orgs (for the search/picker)
  site/data/org/<CODE>.json     - per-org time series for all standards
  site/data/national.json       - England series (used as comparison line)
  site/data/downloads/          - targeted CSV slices + gzipped full file
  site/data/meta.json           - build metadata + comparability note
"""
import gzip
import json
import os
import datetime as dt

import pandas as pd

from . import config
from . import cancer_groups
from pipeline_common import ods

# Non-real placeholder orgs (e.g. the 'UNKNOWN' unallocated-commissioner bucket).
# Hidden from the picker, but RETAINED in the tidy store and
# in national totals so sums still reconcile.
PLACEHOLDER_NAMES = {"UNKNOWN"}


def _is_placeholder(code, name):
    return (str(code).strip().upper() in PLACEHOLDER_NAMES
            or str(name).strip().upper() in PLACEHOLDER_NAMES)


def _first_months(df):
    """{code: earliest YYYY-MM present in the data} — used for young-org detection."""
    return (df[df["breakdown_type"] == "all"]
            .groupby("org_code")["month"].min().to_dict())




def _negligible_orgs(df, classification=None):
    """Codes to HIDE from the picker / default lists (selection-only: the per-org
    files are still written, the org stays in the store + downloads, and a direct
    ?org= link still resolves and renders with the usual low-reliability flags).

    YOUNG-ORG PROTECTION (2026-06-22): an org that is CURRENT per ODS and whose
    series first appears within config.YOUNG_WINDOW_MONTHS is "young" (thin because
    newly created, e.g. a just-merged ICB) — it is checked FIRST and never hidden,
    so it shows from month one. ODS status separates young (current) from dormant
    (former); the inactivity rule below still hides former-dormant orgs and the
    pre-existing current-but-defunct codes (the two rules do different jobs).

    Computed DYNAMICALLY from the current data each build — not a frozen list — so
    an org that crosses the bar (or a brand-new org) appears automatically and a
    dormant one drops. Rules (confirmed against the activity distribution, see
    DECISIONS.md 2026-06-12; the data separates cleanly):
      * PROVIDER hidden if NO standard clears the reliability threshold (n>=10) in a
        single month within the last config.PICKER_PROVIDER_WINDOW_MONTHS — i.e.
        negligible RECENT activity (incl. dormant / defunct-merged codes). The
        full-year window avoids the reporting-lag false positives a shorter window
        would wrongly hide;
      * COMMISSIONER hidden if its recent-3-finalised-month pooled all-cancers
        denominator (summed across standards) is below
        config.PICKER_MIN_COMMISSIONER_DENOM — isolates the commissioning hubs.
    Placeholders are already excluded elsewhere; skip them here too."""
    classification = classification or {}
    allrows = df[df["breakdown_type"] == "all"]
    recent = set(_recent_final_months(df))
    all_months = sorted(df["month"].unique())
    provider_window = set(all_months[-config.PICKER_PROVIDER_WINDOW_MONTHS:])
    young_cutoff = all_months[-config.YOUNG_WINDOW_MONTHS] if len(all_months) >= config.YOUNG_WINDOW_MONTHS else all_months[0]
    first_months = _first_months(df)
    hidden = set()
    for (code, name, level), g in allrows.groupby(["org_code", "org_name", "org_level"]):
        if _is_placeholder(code, name):
            continue
        # Young (current per ODS + recently first-appeared) → never hide. Checked first.
        if not ods.is_former(classification.get(code)) and first_months.get(code, "") >= young_cutoff:
            continue
        if level == "provider":
            recent_g = g.loc[g["month"].isin(provider_window), "total"]
            if recent_g.empty or recent_g.max() < config.RELIABILITY_THRESHOLD:
                hidden.add(code)
        elif level == "icb":
            if g.loc[g["month"].isin(recent), "total"].sum() < config.PICKER_MIN_COMMISSIONER_DENOM:
                hidden.add(code)
    return hidden


def _org_payload(df_org):
    """Build the per-org JSON: one block per standard, 'All' breakdown only."""
    out = {"standards": {}}
    allrows = df_org[df_org["breakdown_type"] == "all"]
    for std in config.STANDARDS:
        s = allrows[allrows["standard"] == std].sort_values("month")
        if len(s) == 0:
            continue
        out["standards"][std] = {
            "label": config.STANDARDS[std]["label"],
            "target": config.STANDARDS[std]["target"],
            "months": s["month"].tolist(),
            "performance": s["performance"].tolist(),
            "within_target": s["within_target"].astype(int).tolist(),
            "total": s["total"].astype(int).tolist(),
            "data_status": s["data_status"].tolist(),
        }
    return out


# Breakdown dimensions exported in the load-on-demand per-org breakdown file.
# The file carries every dimension that exists in the source; which ones are
# *offered* per standard is a front-end concern (FDS28 surfaces cancer only).
BREAKDOWN_DIMS = ("cancer", "route", "modality", "combination")

# Fraction-of-group-activity bar below which a (cancer_group, route) slice is
# treated as not-applicable noise and omitted. The data separates cleanly: a
# route that genuinely applies to a group is 3-82% of that group's activity,
# whereas a cancer-specific route appearing under the wrong group (e.g. Breast
# Symptomatic under Lung, or Screening under Skin) is 0.0-0.1% — structural
# miscoding, ~1 patient/month. 1% sits squarely in that gap. See DECISIONS.md
# (2026-06-11 route-characterisation finding).
GROUP_ROUTE_ACTIVITY_BAR = 0.01


def _cancer_group_slices(cancer_rows):
    """Aggregate raw 'cancer' breakdown rows into NHS England's ten tumour-site
    groups for one (org, standard). Per (group, month) we SUM the numerators and
    SUM the denominators across the constituent cancer types, THEN compute
    performance from the totals — never average the per-type percentages
    (mis-weights types of different sizes). Returns slices keyed by group name,
    same series shape as the raw dims. Empty groups (no constituent rows for this
    org/standard) are omitted, so the front end can treat an absent group as
    'not published here' rather than a flat-zero line."""
    if cancer_rows.empty:
        return {}
    rows = cancer_rows.copy()
    rows["group"] = rows["breakdown_value"].map(cancer_groups.group_for)

    # Build-time double-count guard: a parent label and its children must never
    # share a (month) cell, or the group denominator would double. Cheap insurance
    # against a future NHS vintage shifting granularity mid-series.
    for _m, mg in rows.groupby("month"):
        cancer_groups.assert_no_double_count(mg["breakdown_value"].tolist())

    agg = (rows.groupby(["group", "month"], as_index=False)
               .agg(within=("within_target", "sum"),
                    total=("total", "sum"),
                    # within a (month, org, standard) all cancer rows come from one
                    # source file → one status; 'min' keeps provisional if ever mixed.
                    data_status=("data_status", "min")))
    agg["performance"] = (agg["within"] / agg["total"]).round(4)

    slices = {}
    for grp in cancer_groups.TEN_GROUPS:
        g = agg[agg["group"] == grp].sort_values("month")
        if g.empty:
            continue
        slices[grp] = {
            "months": g["month"].tolist(),
            "performance": g["performance"].tolist(),
            "within_target": [_num(v) for v in g["within"]],
            "total": [_num(v) for v in g["total"]],
            "data_status": g["data_status"].tolist(),
        }
    return slices


def _cancer_group_route_slices(srows, route_values, cancer_values):
    """For one (org, standard): roll the cancer x route combination rows up into
    NHS's ten tumour-site groups x route. Per (group, route, month) SUM numerators
    and denominators across the group's constituent cancer types, THEN compute
    performance from the totals (never average per-type percentages). Returns
    {group: {route: <series>, ...}, ...} — the shape the group-aware route dropdown
    consumes (v6).

    Routes are PARTLY cancer-specific (Breast Symptomatic -> Breast only; Screening
    -> the screening-programme cancers), and the source ships near-zero noise cells
    for the impossible cross-tabs. So a (group, route) slice is emitted ONLY when
    its cumulative denominator clears GROUP_ROUTE_ACTIVITY_BAR of the group's total
    route activity, computed PER ORG — a small trust legitimately surfaces fewer
    routes than the national picture, and the front end just lists what is present.

    Reconciliation guard (fail-loud, protects the daily CI refresh): for every
    (group, month) the routes must sum to that group's cancer total — verified
    exact in the real source — so a future NHS vintage that breaks the route
    partition fails the run rather than shipping a wrong denominator."""
    if not route_values:
        return {}
    comb = srows[srows["breakdown_type"] == "combination"]
    if comb.empty:
        return {}
    split = comb["breakdown_value"].str.split("|", n=1, expand=True)
    if split.shape[1] < 2:
        return {}
    rows = comb.assign(_a=split[0].str.strip(), _b=split[1].str.strip())
    rows = rows[rows["_a"].isin(cancer_values) & rows["_b"].isin(route_values)]
    if rows.empty:
        return {}
    rows = rows.assign(group=rows["_a"].map(cancer_groups.group_for))

    agg = (rows.groupby(["group", "_b", "month"], as_index=False)
               .agg(within=("within_target", "sum"),
                    total=("total", "sum"),
                    data_status=("data_status", "min")))

    # Reconciliation guard (fail-loud, protects the daily refresh, which rebuilds
    # newly-fetched data AFTER the pre-fetch test gate). We assert the SAFETY-
    # CRITICAL direction: a group's routes must never EXCEED its cancer total
    # (inflation / double-counting → a wrong, overstated denominator). Real NHS
    # data partitions EXACTLY (routes == total; asserted in tests against the
    # store); an under-count (routes < total) only ever means a route is missing
    # and degrades gracefully (one fewer option), so it isn't a build-stopper and
    # keeps minimal unit fixtures — which carry partial combos — valid.
    canc = srows[srows["breakdown_type"] == "cancer"].copy()
    canc["group"] = canc["breakdown_value"].map(cancer_groups.group_for)
    grp_tot = canc.groupby(["group", "month"])["total"].sum()
    rte_tot = agg.groupby(["group", "month"])["total"].sum()
    over = rte_tot.subtract(grp_tot, fill_value=0)        # routes minus group total
    if (over > 0.6).any():
        bad = over[over > 0.6]
        raise AssertionError(
            "cancer_group x route EXCEEDS the group cancer total (double-count?) "
            f"(max over {over.max()}): {bad.head().to_dict()}")

    agg["performance"] = (agg["within"] / agg["total"]).round(4)
    out = {}
    for grp in cancer_groups.TEN_GROUPS:
        g = agg[agg["group"] == grp]
        denom = g["total"].sum()
        if denom <= 0:
            continue
        bar = GROUP_ROUTE_ACTIVITY_BAR * denom
        routes = {}
        for rv, rg in g.groupby("_b"):
            if rg["total"].sum() < bar:
                continue                       # cancer-specific route under the wrong group: drop the noise
            rg = rg.sort_values("month")
            routes[str(rv)] = {
                "months": rg["month"].tolist(),
                "performance": rg["performance"].tolist(),
                "within_target": [_num(v) for v in rg["within"]],
                "total": [_num(v) for v in rg["total"]],
                "data_status": rg["data_status"].tolist(),
            }
        if routes:
            out[grp] = routes
    return out


def assert_store_reconciles(df):
    """Fail-loud BIDIRECTIONAL reconciliation gate for the real accumulated store,
    run on the CI build path BEFORE the rebuilt data is committed (2026-06-29).

    It mirrors EXACTLY the two store reconciliation tests, so the build gate and
    the pre-fetch test gate now agree by construction: a vintage-mix (two source
    files disagreeing within one month-cell) fails THE BUILD, loudly, before
    commit — instead of slipping through a one-directional build check, getting
    committed, and wedging every subsequent run at the pre-fetch test gate (the
    2026-06-27/28 failure).

    Two identities, both checked in FULL (over- AND under-count):
      1. per (month, org, standard): ten groups + the Missing/Invalid residue ==
         the all-cancers headline (CMB31/CMB62 carry no residue, so == exactly);
      2. per (org, group, month) for CMB31/CMB62: the cancer x route combinations
         partition the group's cancer total exactly.
    Deliberately SEPARATE from the in-build `_group_route_section` guard, which
    stays one-directional (it must tolerate the partial-combo minimal unit
    fixtures and a genuinely-missing route degrades gracefully). This gate runs
    only on the real multi-source store, where the partition is exact."""
    cg = cancer_groups
    can = df[df["breakdown_type"] == "cancer"].copy()
    if can.empty:
        return
    can["group"] = can["breakdown_value"].map(cg.group_for)   # raises on any unmapped label
    rolled = can[can["group"].isin(cg.TEN_GROUPS)]
    keys = ["month", "org_code", "standard"]
    g = rolled.groupby(keys, as_index=False).agg(
        g_total=("total", "sum"), g_within=("within_target", "sum"))
    a = (df[df["breakdown_type"] == "all"].groupby(keys, as_index=False)
         .agg(a_total=("total", "sum"), a_within=("within_target", "sum")))
    excl = (can[can["group"] == cg.EXCLUDED_GROUP].groupby(keys, as_index=False)
            .agg(x_total=("total", "sum"), x_within=("within_target", "sum")))
    m = g.merge(a, on=keys, how="outer", indicator=True)
    if not (m["_merge"] == "both").all():
        raise AssertionError(
            f"store recon: groups/all cells don't align: {m['_merge'].value_counts().to_dict()}")
    m = m.merge(excl, on=keys, how="left").fillna({"x_total": 0.0, "x_within": 0.0})
    for std in ("FDS28", "CMB31", "CMB62"):
        s = m[m["standard"] == std]
        dt = (s["g_total"] + s["x_total"] - s["a_total"]).abs()
        dw = (s["g_within"] + s["x_within"] - s["a_within"]).abs()
        if dt.max() >= 0.6 or dw.max() >= 0.6:
            bad = s.loc[dt.idxmax()]
            raise AssertionError(
                f"store recon: ten groups + excluded != all-cancers for {std} "
                f"(max|Δtotal|={dt.max()}, max|Δwithin|={dw.max()}); e.g. "
                f"{bad['org_code']} {bad['month']}: g={bad['g_total']} x={bad['x_total']} "
                f"a={bad['a_total']} — likely two source files mixed in one month-cell.")
    for std in ("CMB31", "CMB62"):
        d = df[df["standard"] == std]
        routes = set(d.loc[d["breakdown_type"] == "route", "breakdown_value"])
        cancers = set(d.loc[d["breakdown_type"] == "cancer", "breakdown_value"])
        comb = d[d["breakdown_type"] == "combination"].copy()
        if comb.empty:
            continue
        parts = comb["breakdown_value"].str.split("|", n=1, expand=True)
        comb["_a"], comb["_b"] = parts[0].str.strip(), parts[1].str.strip()
        cxr = comb[comb["_a"].isin(cancers) & comb["_b"].isin(routes)].copy()
        cxr["group"] = cxr["_a"].map(cg.group_for)
        rt = cxr.groupby(["org_code", "group", "month"])["total"].sum()
        canc = d[d["breakdown_type"] == "cancer"].copy()
        canc["group"] = canc["breakdown_value"].map(cg.group_for)
        gt = canc.groupby(["org_code", "group", "month"])["total"].sum()
        j = gt.to_frame("g").join(rt.to_frame("r"), how="left")
        if not j["r"].notna().all():
            miss = j[j["r"].isna()]
            raise AssertionError(
                f"store recon: {std} — {len(miss)} group-cells have cancer data but no "
                f"route partition (e.g. {miss.index[0]}).")
        diff = (j["g"] - j["r"]).abs()
        if diff.max() >= 0.6:
            raise AssertionError(
                f"store recon: {std} — cancer x route partition != group cancer total "
                f"(max|Δ|={diff.max()}).")


def _breakdown_payload(df_org):
    """Per-org breakdown series for every standard. Carries the raw granular
    dimensions (cancer / route / modality + published pairwise combinations) AND
    a new 'cancer_group' dimension: NHS England's ten tumour-site groups, summed
    up from the raw cancer types (kept ALONGSIDE the granular cancer dim, not as
    a replacement). Same series shape throughout so the front end renders any of
    them with no new chart code. Shipped as a separate org/<CODE>.breakdown.json
    fetched ONLY when the user opens a filter / picks a group."""
    out = {"standards": {}}
    for std in config.STANDARDS:
        srows = df_org[df_org["standard"] == std]
        dims = {}
        for bt in BREAKDOWN_DIMS:
            rows = srows[srows["breakdown_type"] == bt]
            if rows.empty:
                continue
            slices = {}
            for bv, g in rows.groupby("breakdown_value"):
                g = g.sort_values("month")
                slices[str(bv)] = {
                    "months": g["month"].tolist(),
                    "performance": g["performance"].tolist(),
                    "within_target": [_num(v) for v in g["within_target"]],
                    "total": [_num(v) for v in g["total"]],
                    "data_status": g["data_status"].tolist(),
                }
            if slices:
                dims[bt] = slices
        # NHS ten-group rollup, derived from the raw cancer rows.
        group_slices = _cancer_group_slices(srows[srows["breakdown_type"] == "cancer"])
        if group_slices:
            dims["cancer_group"] = group_slices
        # Group-aware routes (v6): ten groups x route, activity-filtered so a group
        # only offers the routes that genuinely apply to it. Built from the
        # cancer x route combos; absent for FDS28 (no route dim).
        group_route = _cancer_group_route_slices(
            srows,
            set(dims.get("route", {}).keys()),
            set(dims.get("cancer", {}).keys()))
        if group_route:
            dims["cancer_group_route"] = group_route
        if dims:
            out["standards"][std] = dims
    return out


def _financial_year(month):
    """'2025-07' -> '2025-26' (NHS financial year, Apr-Mar)."""
    y, m = (int(x) for x in month.split("-"))
    start = y if m >= 4 else y - 1
    return f"{start}-{str(start + 1)[-2:]}"


def _recent_final_months(df, k=config.RECENT_FINAL_WINDOW_MONTHS):
    """The k most recent FINALISED months (provisional months excluded)."""
    fin = sorted(df.loc[df["data_status"] == "final", "month"].unique())
    return fin[-k:]


def _num(v):
    """NHS small-number rounding leaves fractional values (e.g. 0.5); keep them
    truthful rather than truncating 0.5 -> 0, but show whole counts as ints."""
    v = float(v)
    return int(v) if v.is_integer() else round(v, 1)


def _build_downloads(df, out_dir):
    """Targeted CSV slices + gzipped full file (replaces the single 200MB CSV)."""
    dl_dir = os.path.join(out_dir, "downloads")
    os.makedirs(dl_dir, exist_ok=True)
    full = df.sort_values(["org_code", "standard", "month"])
    files = []

    for label, g in full.groupby(full["month"].map(_financial_year)):
        name = f"cwt_tidy_{label}.csv"
        g.to_csv(os.path.join(dl_dir, name), index=False)
        files.append(name)

    full[full["breakdown_type"] == "all"].to_csv(
        os.path.join(dl_dir, "cwt_headline_all_cancers.csv"), index=False)
    files.append("cwt_headline_all_cancers.csv")

    with gzip.open(os.path.join(dl_dir, "cwt_tidy_full.csv.gz"), "wt", newline="") as f:
        full.to_csv(f, index=False)
    files.append("cwt_tidy_full.csv.gz")

    with open(os.path.join(dl_dir, "index.json"), "w") as f:
        json.dump({"files": sorted(files)}, f, indent=2)
    return files


def build(df, out_dir=config.SITE_DATA_DIR, classification=None, trust_codes=None):
    # Fail loudly if a composite group's composition description has drifted from
    # the actual cancer_groups mapping before we emit it into meta.json (v7).
    cancer_groups.assert_composition_consistent()
    os.makedirs(os.path.join(out_dir, "org"), exist_ok=True)

    # Shared ODS org-status classification (current/former + succession links) +
    # NHS-trust code set (for the provider-type filter). Absent/empty → every org
    # treated as current and untyped (fail-open); annotation only.
    classification = classification or {}
    trust_codes = trust_codes or set()

    # Negligible-activity orgs to hide from the picker (computed each build). They
    # still get per-org files written and stay in index.json flagged hidden=true,
    # so direct ?org= links resolve; the front end just filters them out of the
    # picker / default lists. Young current-per-ODS orgs are protected (see above).
    hidden = _negligible_orgs(df, classification)

    # Per-org files + index
    all_months = sorted(df["month"].unique())
    first_months = _first_months(df)        # for the data-keyed 'Formed' note gate
    index = []
    for (code, name, level), g in df.groupby(["org_code", "org_name", "org_level"]):
        if _is_placeholder(code, name):
            continue  # kept in the store/national totals, just not selectable
        region = g["region"].iloc[0]
        payload = _org_payload(g)
        payload.update({"code": code, "name": name, "level": level, "region": region})
        with open(os.path.join(out_dir, "org", f"{code}.json"), "w") as f:
            json.dump(payload, f, separators=(",", ":"))
        if level != "national":
            # Load-on-demand breakdown file (gitignored, rebuilt every run).
            with open(os.path.join(out_dir, "org", f"{code}.breakdown.json"), "w") as f:
                json.dump(_breakdown_payload(g), f, separators=(",", ":"))
            entry = {"code": code, "name": name, "level": level, "region": region}
            if code in hidden:
                entry["hidden"] = True   # negligible activity: out of the picker, still direct-linkable
            # 'Formed' note only when the DATA series is genuinely truncated AND a
            # predecessor recently closed (a real handoff, not an old merger).
            ce = classification.get(code)
            formed_ok = (ods.series_truncated(first_months.get(code, ""), all_months)
                         and ods.formed_recently(ce, classification, all_months[0]))
            ods.annotate_entry(entry, ce, formed_ok=formed_ok)
            if level == "provider":
                ods.tag_provider_type(entry, trust_codes)
            index.append(entry)
    index.sort(key=lambda r: r["name"])
    # Fail-loud paired check: a populated ODS trust set must tag >0 independents.
    ods.assert_independents_tagged(index, "CWT", trust_codes)
    with open(os.path.join(out_dir, "index.json"), "w") as f:
        json.dump(index, f, separators=(",", ":"))
    n_hidden = {
        "providers": sum(1 for e in index if e.get("hidden") and e["level"] == "provider"),
        "commissioners": sum(1 for e in index if e.get("hidden") and e["level"] == "icb"),
    }
    n_selectable = sum(1 for e in index if not e.get("hidden"))

    # National series as its own file (comparison overlay)
    nat = df[df["org_level"] == "national"]
    if len(nat):
        code = nat["org_code"].iloc[0]
        national_payload = _org_payload(nat)
        with open(os.path.join(out_dir, "national.json"), "w") as f:
            json.dump(national_payload, f, separators=(",", ":"))
        # National breakdown (for the slice-matched comparison line, on demand).
        with open(os.path.join(out_dir, "national.breakdown.json"), "w") as f:
            json.dump(_breakdown_payload(nat), f, separators=(",", ":"))

    # Targeted downloads
    downloads = _build_downloads(df, out_dir)

    # Metadata
    meta = {
        "built_at": dt.datetime.utcnow().isoformat() + "Z",
        "comparability_break": config.COMPARABILITY_BREAK,
        "n_orgs": n_selectable,                       # selectable in the picker
        "n_orgs_total": len(index),                   # incl. hidden (direct-linkable)
        "hidden_from_picker": n_hidden,
        "months": sorted(df["month"].unique().tolist()),
        "standards": {k: v["label"] for k, v in config.STANDARDS.items()},
        "downloads": downloads,
        # Composite-group make-up, sourced from cancer_groups so the front-end
        # description can't drift from the actual mapping (v7). Composite groups
        # only; the six 1:1 groups are self-explanatory and absent here.
        "group_composition": {g: cancer_groups.composition_text(g)
                              for g in cancer_groups.COMPOSITE_GROUPS},
        # Precise per-standard caveat for 'Other' (sourced from cancer_groups so
        # the front-end copy can't drift from the mapping). Drives the group hint
        # + the 'Other' dropdown tooltip.
        "group_caveat": {"Other": cancer_groups.OTHER_GROUP_NOTE},
        "note": ("31-day and 62-day standards changed in October 2023; figures "
                 "before that month are not directly comparable."),
    }
    with open(os.path.join(out_dir, "meta.json"), "w") as f:
        json.dump(meta, f, indent=2)
    return meta
