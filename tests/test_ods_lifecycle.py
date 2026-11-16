"""
Tests for the shared ODS org-status lifecycle feature (pipeline_common/ods.py +
its use in both builds). No network: these exercise the pure classification
helpers and the young-org / former-org logic against synthetic inputs.

Guards:
  1. annotate_entry stamps the right fields for former vs newly-formed orgs, and
     is a no-op for an unclassified (current, no-succession) org — so the lean
     index files stay lean and absent-from-cache => plain current org.
  2. YOUNG PROTECTION: a current-per-ODS org whose series first appears within the
     window is NEVER hidden (shown from month one) — in BOTH builds — while a
     former+dormant org and the pre-existing current-but-defunct safety net still
     hide. This is the bug fix: thin-because-new must not be auto-hidden.
"""
import pandas as pd
import pytest

from pipeline import build_site_data as b
from pipeline import config as ccfg
from pipeline_rtt import build as rb
from pipeline_common import ods


def test_is_former():
    assert ods.is_former({"status": "former"}) is True
    assert ods.is_former({"status": "current"}) is False
    assert ods.is_former(None) is False


def test_annotate_entry_former_formed_and_noop():
    # Former -> former/closed/related(successors)/reltype=superseded
    e = {"code": "QNQ"}
    ods.annotate_entry(e, {"status": "former", "closed": "2026-04-01",
                           "successors": [{"code": "S0E4D", "name": "Thames Valley"}],
                           "predecessors": []})
    assert e["former"] is True and e["closed"] == "2026-04-01"
    assert e["reltype"] == "superseded" and e["related"][0]["code"] == "S0E4D"

    # Newly-formed current org -> related(predecessors)/reltype=formed/opened, NOT former
    e = {"code": "S1Y5D"}
    ods.annotate_entry(e, {"status": "current", "opened": "2026-04-01", "successors": [],
                           "predecessors": [{"code": "QHG", "name": "BLMK"}]})
    assert "former" not in e and e["reltype"] == "formed" and e["opened"] == "2026-04-01"
    assert e["related"][0]["code"] == "QHG"

    # Unclassified / ordinary current org -> untouched (lean entry)
    e = {"code": "RJ1"}
    ods.annotate_entry(e, None)
    assert e == {"code": "RJ1"}


def test_tag_provider_type():
    trust = {"RJ1", "RCF"}
    # NHS trust -> untagged (absent => trust default view)
    e = {"code": "RJ1"}; ods.tag_provider_type(e, trust); assert "ptype" not in e
    # Non-trust (IS, or the RO157 residue, or anything future) -> independent
    e = {"code": "NT1"}; ods.tag_provider_type(e, trust); assert e["ptype"] == "independent"
    # Empty trust set (cold ODS + no cache) -> leave untagged (fail-open to trust view)
    e = {"code": "NT1"}; ods.tag_provider_type(e, set()); assert "ptype" not in e


def test_series_truncated_and_formed_note_gate():
    months = [f"2022-{m:02d}" for m in range(4, 13)] + [f"2023-{m:02d}" for m in range(1, 13)]
    months += [f"2024-{m:02d}" for m in range(1, 13)] + [f"2025-{m:02d}" for m in range(1, 5)]
    floor = months[0]  # 2022-04
    # Full-history org (starts at floor) -> NOT truncated (Oxford case)
    assert ods.series_truncated(floor, months) is False
    # Within the 2-month margin -> still NOT truncated
    assert ods.series_truncated("2022-06", months) is False
    # Genuinely short series -> truncated
    assert ods.series_truncated("2024-11", months) is True

    ce = {"status": "current", "predecessors": [{"code": "RHV", "name": "x"}], "opened": None}
    # Long-established org with predecessors but full history -> NO 'Formed' note
    e = {"code": "RTH"}
    ods.annotate_entry(e, ce, formed_ok=ods.series_truncated(floor, months))
    assert "reltype" not in e and "related" not in e
    # Genuinely recent org with predecessors -> 'Formed' note shows
    e = {"code": "G6V2S"}
    ods.annotate_entry(e, ce, formed_ok=ods.series_truncated("2024-11", months))
    assert e["reltype"] == "formed"

    # formed_recently: needs a predecessor that itself closed within the data window.
    classn = {"NEW": {"status": "current", "predecessors": [{"code": "OLD", "name": "o"}]},
              "OLD": {"status": "former", "closed": "2024-11-01"},
              "MH":  {"status": "current", "predecessors": [{"code": "GONE", "name": "g"}]},
              "GONE": {"status": "former", "closed": "2017-04-01"},  # old merger, before floor
              "GAIN": {"status": "current", "predecessors": [{"code": "OLD", "name": "o"}]}}
    assert ods.formed_recently(classn["NEW"], classn, floor) is True    # recent handoff
    assert ods.formed_recently(classn["MH"], classn, floor) is False    # old merger, pre-floor close
    # QRL-type boundary gainer: recent handoff BUT full history → truncation gate blocks the note
    e = {"code": "GAIN"}
    formed_ok = ods.series_truncated(floor, months) and ods.formed_recently(classn["GAIN"], classn, floor)
    ods.annotate_entry(e, classn["GAIN"], formed_ok=formed_ok)
    assert "reltype" not in e   # not truncated → no note despite a recent predecessor
    # ASYMMETRY: a FORMER org is annotated regardless of formed_ok
    e = {"code": "QNQ"}
    ods.annotate_entry(e, {"status": "former", "closed": "2026-04-01",
                           "successors": [{"code": "S0E4D", "name": "x"}]}, formed_ok=False)
    assert e["former"] is True and e["reltype"] == "superseded"


def test_assert_independents_tagged_guard():
    # Populated trust set + providers but ZERO independents tagged → fail loud
    # (this is the silent-match-failure / empty-tag case fail-open used to hide).
    idx = [{"level": "provider", "code": "RJ1"}, {"level": "provider", "code": "RCF"}]
    with pytest.raises(RuntimeError):
        ods.assert_independents_tagged(idx, "X", {"RJ1", "RCF"})
    # At least one independent present → OK, returns the count.
    idx2 = idx + [{"level": "provider", "code": "NT1", "ptype": "independent"}]
    assert ods.assert_independents_tagged(idx2, "X", {"RJ1", "RCF"}) == 1
    # Empty trust set (synthetic/dev/tests — typing not attempted) → skipped, no raise.
    assert ods.assert_independents_tagged(idx, "X", set()) == 0


def _cancer_df(code, level, months, total):
    """Minimal all-slice tidy frame: one org, constant monthly total."""
    return pd.DataFrame([{
        "month": m, "org_level": level, "org_code": code, "org_name": code,
        "region": "England", "standard": "CMB62", "breakdown_type": "all",
        "breakdown_value": "All", "within_target": total, "total": total,
        "performance": 1.0, "data_status": "final", "source_file": "t",
    } for m in months])


def _months(n, end=2026):
    """n consecutive months ending YYYY-12, oldest first."""
    out = []
    y, m = end, 12
    for _ in range(n):
        out.append(f"{y}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return sorted(out)


def test_cancer_young_provider_not_hidden():
    full = _months(40)                       # 40 months of history available
    young_first = full[-(ccfg.YOUNG_WINDOW_MONTHS - 1):]   # appears within the window
    # A young provider with negligible volume (total < threshold) that WOULD be hidden
    # by the inactivity rule, but is current per ODS + recently first-appeared.
    df = _cancer_df("NEW1", "provider", young_first, total=1)
    # pad the frame's month axis so the window maths sees the full timeline
    df = pd.concat([df, _cancer_df("OLDBIG", "provider", full, total=9999)], ignore_index=True)
    hidden = b._negligible_orgs(df, {})      # empty classification => current => young protected
    assert "NEW1" not in hidden              # protected, shown from month one
    assert "OLDBIG" not in hidden            # genuinely active, never hidden


def test_cancer_former_dormant_still_hidden():
    full = _months(40)
    old_first = full[:5]                     # first appeared long ago (not young)
    df = _cancer_df("DEAD", "provider", old_first, total=1)   # dormant: no recent activity
    df = pd.concat([df, _cancer_df("OLDBIG", "provider", full, total=9999)], ignore_index=True)
    hidden = b._negligible_orgs(df, {"DEAD": {"status": "former"}})
    assert "DEAD" in hidden                  # former + dormant => hidden


def _by_month(months, total):
    return {m: {"total": total} for m in months}


def test_rtt_young_provider_not_hidden_but_dormant_is():
    full = _months(40)
    young = _by_month(full[-3:], total=1)            # 3 recent months, tiny volume
    dormant = _by_month(full[:5], total=1)           # old, tiny volume
    # Young current org protected; same thin profile but former+old is hidden.
    assert rb._negligible(young, full, "NEWX", {}) is False
    assert rb._negligible(dormant, full, "DEADX", {"DEADX": {"status": "former"}}) is True
