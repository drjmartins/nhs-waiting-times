# Status

At-a-glance project state. For the reasoning behind anything here, and the full history, see `DECISIONS.md`.

_Last updated: 2026-11-20 (Claude session; RTT estimates and chart refinements deployed. Rewritten as a short current-state page on 2026-11-16. The earlier deploy-by-deploy log,
June to October 2026, was removed from this file: every entry is in `DECISIONS.md`, and the old text is in git history
at commit c5e23ba)._

## What this is
Two public dashboards on NHS England open data, hosted by the Surgical Data and Policy Lab.
- **Live:** https://waitingtimes.surgicaldatapolicy.org (landing page, `/cancer/`, `/rtt/`)
- **Repo:** https://github.com/drjmartins/nhs-waiting-times (single branch, `master`)
- **Host site the design follows:** https://surgicaldatapolicy.org
- **Working copy on João's Mac:** `~/Claude/NHS Waiting Times Dashboard/nhs-waiting-times` (keep it out of iCloud-synced
  folders; an earlier Desktop copy was damaged that way).

## Current state
- **Live and updating.** Data to September 2026 on both dashboards (refreshed 2026-11-12). 102 tests pass. Charts open on the full time range ("All").
- **Cancer** (`/cancer/`): 28-day Faster Diagnosis, 31-day and 62-day combined standards, by provider and commissioner,
  with cancer-group and referral-route breakdowns. FDS target is stepped: 75% to March 2026, 80% from April 2026,
  explained in a banner under the chart that shows only for that standard.
- **RTT** (`/rtt/`): % within 18 weeks, waiting-list size and long waiters, by provider, commissioner and treatment function.
  The England chart has a tick box to show or hide the 65% / 70% recovery milestones.
  England charts and cards also show NHS England's figures including estimates for providers that did not submit;
  trusts and ICBs affected by a missing submission carry a note; ICBs also get their own estimate and incomplete-month
  marks once the next scheduled refresh has run. DECISIONS 2026-11-20.
- **Design:** aligned with the host site (IBM Plex Sans, navy and magenta, lab header and footer). Charts and cards must
  stay on white panels for the colours to pass WCAG AA; see DECISIONS 2026-11-13.
- **Downloads:** each chart downloads as PNG or SVG, and its figures as a CSV spreadsheet (DECISIONS 2026-11-23).
- **Navigation:** landing page cards lead to each dashboard; each dashboard has a single "Home" link.
- **Visitor analytics:** GoatCounter (cookieless) on all three pages; stats at https://sdpl-waitingtimes.goatcounter.com.
  DECISIONS 2026-11-21.

## How updates run
NHS England's website blocks GitHub Actions' servers, so fetching and publishing are split:
1. **Fetch (local machine).** `scripts/scheduled_refresh.ps1` runs daily at 18:00 from Windows Task Scheduler on
   Obafunsho's (John's) laptop. It runs the tests and both pipelines with all gates, commits and pushes if the data
   changed, then starts the publish workflow. A failed run opens a GitHub issue titled "Scheduled refresh failed".
   The RTT run also downloads NHS England's overview workbook (estimates for missing trusts) and stops if it is missing
   or does not match our figures.
2. **Publish (GitHub).** `.github/workflows/update-data.yml`, started by hand or by the script, with `skip_fetch` on by
   default: tests, then `python -m pipeline.run --no-fetch` (cancer rebuilt from the committed store), then RTT's committed
   data as-is, then deploy to GitHub Pages. The `schedule:` trigger is commented out on purpose. `built_at` is preserved,
   so "Last updated" names the data refresh, not the deploy day.
3. **Staleness safeguards.** If the data falls more than 12 weeks behind the end of its latest month, an amber "Data may be
   out of date" note appears on all three pages (`site/assets/freshness.js`), and the weekly `freshness-check.yml` workflow
   opens an issue. Rule lives in `pipeline_common/freshness.py`; DECISIONS 2026-11-13.

**To publish what is already committed:** Actions tab, "Update CWT + RTT data and deploy", Run workflow (or
`gh workflow run update-data.yml`). Merging to `master` does not publish by itself.

**If GitHub-side fetching ever works again:** test with `gh workflow run update-data.yml -f skip_fetch=false` (green means
the block has cleared), then uncomment `schedule:` and flip the `skip_fetch` default to `false`. The parked AWS Lambda
relay design is in DECISIONS 2026-09-21.

## Making changes
Work on a branch, preview locally, merge, run the publish workflow, check the live site, then record it in `DECISIONS.md`.
Pull `master` first: the scheduled refresh adds data commits. For anything touching the data pipeline, reconciliation
gates or deploy, read the relevant `DECISIONS.md` entries before changing code.

## Open items
0. **Tell Obafunsho (John)** that the refresh now downloads NHS England's overview workbook and stops if it is missing
   or mismatched, and check that his first run after 2026-11-20 brings in the ICB estimates.
1. **Data cleaning** was listed as the next task in June and has not been scoped.
2. **Deferred:** a cancer sub-type breakdown control. The data keeps the sub-type level; only the control is missing.
3. **Not done by choice:** text inside charts (axes, legends, tooltips) is still monospace, and downloaded chart images
   name IBM Plex for the title without embedding it.
4. **Unconfirmed since June:** the 11 June 2026 decommission check of NHS's old provider flat files was handed to the user
   to run and never recorded as done. The pipeline has ingested data normally since, which suggests it is moot.

## Things to keep an eye on
- **The refresh depends on one laptop.** If it is off for weeks, the staleness safeguards above are what will show it.
- **NHS file format changes** fail the pipeline loudly by design (`pipeline/normalise.py`); the fix is usually a new
  column-name mapping.
- **Financial-year boundaries and re-published files** have caused problems before (DECISIONS 2026-06-25, 2026-06-29).
- **`pandas` is pinned below 3.0** in `requirements.txt` for a reason (DECISIONS 2026-06-25).

## Running locally
```bash
pip install -r requirements.txt
python -m pytest -q                            # tests (also run before every publish)
python -m pipeline.run --no-fetch              # rebuild cancer site data from the committed store (no network)
python -m http.server 8099 --directory site    # then open http://localhost:8099
python -m pipeline.run                         # real cancer run (needs access to england.nhs.uk)
python -m pipeline_rtt.run                     # real RTT run
```
