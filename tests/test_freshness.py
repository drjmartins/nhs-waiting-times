"""Tests for the data-freshness rule (pipeline_common/freshness.py) and its JS mirror."""
import datetime as dt
import json
import os
import re

import pytest

from pipeline_common import freshness as fr

D = dt.date


def test_month_end_handles_month_lengths_and_december():
    assert fr.month_end("2026-09") == D(2026, 9, 30)
    assert fr.month_end("2026-12") == D(2026, 12, 31)
    assert fr.month_end("2028-02") == D(2028, 2, 29)   # leap year


def test_stale_from_is_twelve_weeks_after_month_end():
    # September 2026 data: month ends 30 Sep, 84 days later is 23 Dec -> stale from 24 Dec.
    assert fr.stale_from("2026-09") == D(2026, 12, 24)


def test_normal_publication_lag_is_not_stale():
    # Sep-2026 data was published 12 Nov 2026 (~6 weeks): still fresh into mid-December.
    assert not fr.is_stale("2026-09", D(2026, 11, 13))
    assert not fr.is_stale("2026-09", D(2026, 12, 23))


def test_missed_release_becomes_stale():
    assert fr.is_stale("2026-09", D(2026, 12, 24))
    assert fr.is_stale("2026-09", D(2027, 2, 1))


def test_check_reads_latest_month_from_each_meta(tmp_path):
    a, b = tmp_path / "a.json", tmp_path / "b.json"
    a.write_text(json.dumps({"months": ["2026-08", "2026-09"]}))
    b.write_text(json.dumps({"months": ["2026-06"]}))
    res = fr.check({"A": str(a), "B": str(b)}, today=D(2026, 11, 13))
    assert [(n, m, s) for n, m, _, s in res] == [("A", "2026-09", False), ("B", "2026-06", True)]


def test_check_refuses_meta_without_months(tmp_path):
    p = tmp_path / "m.json"
    p.write_text(json.dumps({"months": []}))
    with pytest.raises(ValueError):
        fr.check({"X": str(p)})


def test_committed_meta_files_exist_and_parse():
    # The watchdog runs against these committed files; they must be readable.
    res = fr.check(today=D(2000, 1, 1))
    assert {r[0] for r in res} == {"Cancer", "RTT"}


def test_js_mirror_uses_the_same_grace_period():
    js = open(os.path.join("site", "assets", "freshness.js"), encoding="utf-8").read()
    m = re.search(r"GRACE_DAYS\s*=\s*(\d+)", js)
    assert m and int(m.group(1)) == fr.GRACE_DAYS
