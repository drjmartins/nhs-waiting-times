# NHS Waiting Times Dashboards

Two public dashboards built on NHS England open data, hosted by the
[Surgical Data and Policy Lab](https://surgicaldatapolicy.org):

- **Cancer Waiting Times** (`/cancer/`): 28-day Faster Diagnosis Standard and the 31- and 62-day
  combined standards, by provider and commissioner.
- **RTT (Referral to Treatment)** (`/rtt/`): % within 18 weeks, waiting-list size and long waiters,
  by provider, commissioner and treatment function.

Live site: https://waitingtimes.surgicaldatapolicy.org

**Read `STATUS.md` (current state, how updates run) and `DECISIONS.md` (full decision history,
data quirks, accessibility choices) before changing anything, especially the data pipeline.**

## How it works

A static site (`site/`) fed by build-time Python pipelines. No backend.

```
pipeline/        cancer: discover NHS files -> normalise -> data/processed/tidy.parquet -> site/cancer/data
pipeline_rtt/    RTT: discover + fetch monthly Full CSVs -> site/rtt/data
pipeline_common/ shared: ODS organisation status, regions, retry
site/            landing page, /cancer/, /rtt/, shared assets (fonts, seal, sdpl.css)
tests/ tests_rtt/  pytest suites (run before every build and deploy)
scripts/         the scheduled refresh (Windows Task Scheduler)
```

Both pipelines run fail-loud reconciliation gates before writing anything.

## How updates run

NHS England's website blocks GitHub Actions' servers, so the fetch runs on a local machine and
GitHub only publishes:

1. `scripts/scheduled_refresh.ps1` (daily, Windows Task Scheduler) runs the tests and both
   pipelines, then commits and pushes if the data changed.
2. It then starts `.github/workflows/update-data.yml`, which rebuilds the cancer site from the
   committed store (`python -m pipeline.run --no-fetch`), publishes the committed RTT data, and
   deploys `site/` to GitHub Pages.

To publish what is already committed: `gh workflow run update-data.yml`.

## Run it locally

```bash
pip install -r requirements.txt
python -m pytest -q                            # tests
python -m pipeline.run --no-fetch              # rebuild cancer site data from the committed store
python -m http.server 8099 --directory site    # then open http://localhost:8099
python -m pipeline.run                         # real cancer run (needs access to england.nhs.uk)
python -m pipeline_rtt.run                     # real RTT run
```

## Data and licence

Source: NHS England Cancer Waiting Times and RTT statistics, used under the Open Government
Licence v3.0. These are independent tools, not affiliated with or endorsed by NHS England.
IBM Plex Sans is bundled under the SIL Open Font License (`site/assets/fonts/`).

Developed by Dr João Martins.
