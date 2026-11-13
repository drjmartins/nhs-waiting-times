"""
Tests for the publish path: `pipeline.run --no-fetch` (the workflow's skip_fetch run).

NHS's CDN/WAF serves empty pages to GitHub Actions' egress, so the NHS fetch runs on a
local machine (scripts/scheduled_refresh.ps1) and GitHub only publishes what is committed.
The publish run must therefore:
  1. never contact NHS or ODS (any network call would hit the blocked path),
  2. still run every fail-loud gate (it shares `_gate_and_build` with the fetch path),
  3. keep `built_at` so the footer's "Last updated" names the data refresh, not the
     day it was re-deployed,
  4. refuse to publish an empty dashboard if the committed store is missing.
"""
import json

import pytest

from pipeline import config, discover, run
from pipeline_common import ods


def _boom(*a, **k):
    raise AssertionError("network was contacted on the --no-fetch path")


def test_run_offline_contacts_no_network_and_preserves_built_at(monkeypatch, tmp_path):
    meta_dir = tmp_path / "data"
    meta_dir.mkdir()
    (meta_dir / "meta.json").write_text(json.dumps({"built_at": "2026-09-15T19:31:41.486173Z"}))
    monkeypatch.setattr(config, "SITE_DATA_DIR", str(meta_dir))

    monkeypatch.setattr(run, "_load_store", lambda path=None: [1])     # any non-empty store
    cached = {"orgs": {}, "nhs_trust_codes": ["RXX"], "as_of": "2026-09-10"}
    monkeypatch.setattr(ods, "load", lambda *a, **k: cached)
    seen = {}

    def fake_gate_and_build(store, ods_data=None):
        seen["ods_data"] = ods_data
        return {"built_at": "2026-10-07T12:00:00Z", "n_orgs": 3, "months": ["2026-06", "2026-07"]}

    monkeypatch.setattr(run, "_gate_and_build", fake_gate_and_build)
    # Any live fetch — NHS pages or ODS — must be unreachable from this path.
    for name in ("fetch_page_html", "scrape_all_links_with_retry", "scrape_all_links"):
        monkeypatch.setattr(discover, name, _boom)
    monkeypatch.setattr(ods, "refresh_or_cache", _boom)
    monkeypatch.setattr(ods, "refresh", _boom)

    run.run_offline()

    assert seen["ods_data"] is cached                       # committed ODS cache, not a live fetch
    written = json.loads((meta_dir / "meta.json").read_text())
    assert written["built_at"] == "2026-09-15T19:31:41.486173Z"   # data-refresh date kept
    assert written["n_orgs"] == 3                           # rest of the fresh meta is kept


def test_run_offline_refuses_a_missing_store(monkeypatch):
    monkeypatch.setattr(run, "_load_store", lambda path=None: None)
    with pytest.raises(RuntimeError, match="committed store"):
        run.run_offline()
