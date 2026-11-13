"""
Stepped targets (2026-11-13): a standard's target can change over time. FDS28 is 75% to
March 2026 and 80% from April 2026. The schedule must be well-formed (fail loud) and must
reach the per-org payload so the front end can draw a stepped line.
"""
import pandas as pd
import pytest

from pipeline import build_site_data as b
from pipeline import config


def test_fds28_schedule_is_75_then_80_from_april_2026():
    std = config.STANDARDS["FDS28"]
    assert std["target"] == 0.80
    assert std["target_schedule"] == [[None, 0.75], ["2026-04", 0.80]]
    # The other two standards are flat.
    assert "target_schedule" not in config.STANDARDS["CMB31"]
    assert "target_schedule" not in config.STANDARDS["CMB62"]


def test_validate_rejects_schedule_whose_last_step_disagrees_with_target():
    bad = {"X": {"target": 0.80, "target_schedule": [[None, 0.75], ["2026-04", 0.77]]}}
    with pytest.raises(ValueError, match="must equal"):
        config.validate_standards(bad)


def test_validate_rejects_unordered_or_headless_schedule():
    with pytest.raises(ValueError, match="ascending"):
        config.validate_standards(
            {"X": {"target": 0.8, "target_schedule": [[None, 0.7], ["2026-04", 0.75], ["2025-04", 0.8]]}})
    with pytest.raises(ValueError, match="start with"):
        config.validate_standards({"X": {"target": 0.8, "target_schedule": [["2026-04", 0.8]]}})


def _rows(std):
    return [{"month": m, "org_code": "RXX", "org_name": "X", "org_level": "provider",
             "standard": std, "breakdown_type": "all", "breakdown_value": "All",
             "within_target": 80, "total": 100, "performance": 0.8, "data_status": "final"}
            for m in ("2026-03", "2026-04")]


def test_org_payload_carries_schedule_only_where_one_exists():
    out = b._org_payload(pd.DataFrame(_rows("FDS28") + _rows("CMB62")))
    fds, cmb = out["standards"]["FDS28"], out["standards"]["CMB62"]
    assert fds["target"] == 0.80 and fds["target_schedule"] == [[None, 0.75], ["2026-04", 0.80]]
    assert cmb["target"] == 0.85 and "target_schedule" not in cmb
