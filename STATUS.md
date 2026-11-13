# Status

At-a-glance project state. For the full decision history see `DECISIONS.md`.

_Last updated: 2026-11-13 (Claude session; un-mothball clean-up + host-site visual alignment BUILT on branch `unmothball-and-align`, NOT yet pushed or deployed)._

## Current state (2026-11-13): LIVE and updating again; no longer mothballed
- **Repo:** https://github.com/drjmartins/nhs-waiting-times · **Live:** https://waitingtimes.surgicaldatapolicy.org
  (`/cancer/`, `/rtt/`, landing). Host organisation site: https://surgicaldatapolicy.org (Surgical Data and Policy Lab).
  Older entries below still name the previous repo (`drjmartins/waiting-times`) and `*.github.io` URL; those are historical.
- **Update route (since 2026-11-12, Obafunsho):** NHS England's CDN/WAF still blocks GitHub Actions' egress, so the NHS
  fetch runs on a local Windows machine (`scripts/scheduled_refresh.ps1`, Task Scheduler, daily 18:00). It runs the tests
  and both pipelines (all gates), commits + pushes when data changed, then starts the publish workflow.
- **GitHub side = publish only.** `update-data.yml` via `workflow_dispatch`, `skip_fetch` default **true**: tests →
  `python -m pipeline.run --no-fetch` (cancer rebuilt from the committed store, all gates, committed ODS cache) → RTT's
  committed `site/rtt/data` published as-is → deploy. The `schedule:` cron stays commented out on purpose. `built_at` is
  preserved, so "Last updated" names the data refresh, not the deploy day.
- **Data:** to September 2026 on both dashboards (refreshed 2026-11-12).
- **Mothball artefacts REMOVED (this branch):** the `updates_paused` flag, `pipeline_common/freeze_note.py`, the workflow's
  `paused_note` input + stamp step, the footer conditionals on cancer/RTT/compare, the landing page's `#paused` line and
  script, and the two stamp tests (`tests/test_mothball.py` → `tests/test_publish_path.py`). 89 tests pass. Footer
  "Data to {month}. Last updated {date}." was always dynamic from `meta.json` and is unchanged.
- **The live pages still show "Automatic updates are currently paused" until the next publish run**: the live `meta.json`
  files carry a stale `updates_paused: true` stamped by the 12 Nov publish (made before the stamp became opt-in). Any
  publish run clears it (`gh workflow run update-data.yml`).
- **Visual alignment with surgicaldatapolicy.org (this branch):** page chrome fully aligned (option 2) AND, by explicit user
  decision, the chart palette re-aligned ("option B": navy series, magenta target). All ratios pass AA on the white panels;
  compare.html keeps amber limits. See DECISIONS 2026-11-13 (×2).
- **Lab seal:** `site/assets/sdpl-seal.png` (176px, transparent corners) made from the image João supplied 2026-11-13.

**Known risk (open):** the scheduled refresh runs only while that Windows user is logged on. If it stops, the site goes
stale with nothing on the page to say so (the manual "paused" note is gone by decision). Failures open a GitHub issue,
but a machine that is simply off raises nothing. Option if wanted: an automatic on-page note when the data month falls
more than N months behind today.

**If GitHub-side fetching ever works again:** test with `gh workflow run update-data.yml -f skip_fetch=false` (green =
block cleared), then uncomment `schedule:` and flip the `skip_fetch` default to `false`. The parked AWS Lambda relay
design is in DECISIONS 2026-09-21.

**Housekeeping flagged, not done:** `HANDOFF.md` is obsolete (describes the synthetic-data scaffold); stray git ref
`.git/refs/heads/master 2` on the original laptop clone.

## (Historical) 2026-10-07 to 2026-11-12: site was MOTHBALLED, frozen at data to July 2026
Resolved. Full detail in DECISIONS 2026-10-07 (×2); the un-mothball is DECISIONS 2026-11-13.

## ✅ DEPLOYED + LIVE-VERIFIED 2026-06-29 (run 28362120515, build+deploy GREEN; commit 15acb64) — cron-wedge fix (layered C+B+A+D); 76 tests pass
**Cron UNWEDGED** (14m6s build + 14s deploy — a real full run, vs the ~30s test-gate failures June-27/28). **Live checks
all pass** (cache-busted curl on the deployed site): cancer meta **49 months → 2026-04**, built_at today; England FDS28
2026-04 **status=provisional**; CI ingested exactly the **"April 2026 … New ICB Structure"** file (single vintage,
B's dedup choice); all **6 new ICBs present + reltype=formed** (D7T5G "NHS Essex…", S0E4D "NHS Thames Valley…", S1Y5D/
S9B9J/T6Y0W/Z9B2Z) while the **12 old merged ICBs are former=True/superseded** (QHG/QUE/QH8…) — so the vintage choice
and the ODS lifecycle AGREE on the live site. CI: 76 tests, strict bidirectional `assert_store_reconciles` passed
(build completed past it), RTT recon OK @2025-04 (pct18 0.5973 / waitlist 7,389,065), TF-sum max|Δ|=0, ODS LIVE fetch
both pipelines (428 classified / 354 former / 556 trust codes, as_of 2026-06-25 — not the fallback), provider-type
guards intact. Detail below.

### What was wrong + the layered fix (BUILT 2026-06-29, now DEPLOYED)
The daily cron FAILED June-27 + June-28 (runs 28295888701/28329562744, ~30s, exit 1) at the pytest gate — NOT a fetch/
ODS/dependency error. Root cause: on June-26 NHS published April-2026 provisional under THREE filenames (old-ICB-
structure `April-2026-…`, FY-prefixed re-publication `2026-27-Apr-…`, and `…-New-ICB-Structure.csv`); the per-month
merge UNIONED breakdown rows from two disagreeing vintages (the re-publication dropped rows the original kept), so the
ten cancer groups over-counted the all-cancers headline by 2 (FDS28) / 15 (CMB62) for the UNKNOWN unattributed-ICB cell.
The one-directional BUILD gate let it through and committed it (run 28255139256); the bidirectional pre-fetch TEST gate
then wedged every later run. Live site UNAFFECTED (deploy needs:build; June-26's data is live, the 2/15-patient skew is
invisible). FIX (all four, layered, verified end-to-end on the real corrupt store + real NHS files):
- **C recovery:** purged corrupt 2026-04 rows (store→2026-03) + dropped the 3 April per-month URLs from manifest → CI
  re-ingests April cleanly. Recovered store passes the pre-fetch test gate.
- **B discovery:** `dedupe_per_month` keeps ONE vintage per (month, status), preferring the current org structure
  (New-ICB-Structure marker > FY-prefixed re-publication > plain); cumulatives untouched. Each file is individually
  self-consistent, so picking one = exact recon. April picks the New-ICB-Structure file (correct: old ICBs are Former
  from Apr-2026). `_classify_file` now resolves single-month FY-prefixed names too.
- **A merge (defence-in-depth):** `merge_with_revisions` makes a (month,org,standard) cell single-source — the winning
  file supplies all rows, a losing sibling's rows drop wholesale. Proven to clean up even all-3-ingested.
- **D hardening:** new bidirectional `assert_store_reconciles` runs on the CI build path before commit (mirrors the
  store tests exactly → build gate == test gate). Pre-flight: the ENTIRE clean history passes the strict gate with 0
  violations / 0 shortfalls — adopting it surfaces nothing pre-existing. Confirmed it catches the corrupt store.
- **Verified:** re-ingest → April single-vintage, all 6 new ICBs present, no old merged ICBs for April, contiguity +
  bidirectional recon PASS. 76 tests (71+5). Deliverables ready to commit on say-so. See DECISIONS 2026-06-29 (×2).

## ✅ DEPLOYED + LIVE-VERIFIED 2026-06-25 (run 28163463431, build+deploy GREEN; commit a914ec2) — FY-boundary fix + pandas-3.0 fix
**Live checks pass** (cache-busted curl + headless on the deployed site): all three standards reach **2026-04, flagged
provisional** (FDS28 0.7591 / CMB31 0.915 / CMB62 0.6995); meta 49 months → 2026-04, built_at 10:27; provider org files
(e.g. RCF) reach April; footer renders **"Data to April 2026. Last updated 25 June 2026. Figures for the most recent
months are provisional and subject to revision."** CI: **pandas 2.3.3** (pin held, not 3.x), 71 tests, ODS live both
pipelines (427/353/556, as_of 2026-05-07), RTT recon OK @2025-04 (pct18 0.5973 / waitlist 7,389,065), TF-sum max|Δ|=0.
The first deploy (run 28160596681) ingested April but pandas 3.0.3 (unpinned) silently mis-classified FDS → caught at
live-verify → fixed + pinned + corrupt month purged → re-deployed clean. See DECISIONS 2026-06-25 (×3).

## ⚠️→✅ 2026-06-25 — pandas 3.0 silently broke FDS classification (caught at live-verify), FIXED + pinned
First deploy (run 28160596681) ingested April-2026 but live-verify found **FDS28 stuck at March** while CMB31/CMB62
reached April. Cause: unpinned `requirements.txt` → CI installed **pandas 3.0.3** (dev was 2.3.3); pandas 3.0 makes
`astype(str)` preserve NaN (not `"nan"`), so `normalise._is_aggregate` stopped treating BLANK modality (FDS has no
modality dim) as aggregate → FDS rows mis-classified, `all` slice dropped. April was the first file normalised under 3.0
(historical months cached from 2.x runs); tests passed because fixtures used explicit `"ALL MODALITIES"`. Fix: `.isna()`
in `_is_aggregate` (version-robust) + pin `pandas>=2.2,<3` + 3 blank-modality regression tests; purged corrupt 2026-04
rows + dropped April from manifest so CI re-ingests cleanly. 71 tests pass; recovery verified locally (FDS28 April `all`
perf 0.7591, 49 months, recon green). See DECISIONS 2026-06-25.

## ✅ DEPLOYED 2026-06-25 — CWT financial-year-boundary staleness fix
Cancer pipeline now ALSO scrapes the current-FY sub-page (`{fy}-monthly-cancer-waiting-times-statistics/`) for the
per-MONTH Combined CSVs that appear there before the cumulative file lands on the main page — closing the trail at an
FY boundary (the April-2026 case). Per-month file is column-identical to the cumulative MINUS the `Period` column;
`normalise` injects the month from the filename, guarded. Precedence (user-confirmed): final>provisional, then
cumulative>per-month at equal status, newest-on-tie. **REQUIRED fail-loud month-label guard** (3 layers: refuse
monthly+unparseable filename; refuse no-Period+no-hint; assert single parseable injected month) + national contiguity
guard — the safety net here since cancer has no national-value reconciliation. **68 tests pass** (55 + 13 new).
Demonstrated on REAL data: April-2026 ingested Provisional (store 48→49 months, to 2026-04); guard fires on bad parse;
ten-groups + route recon gates GREEN across the seam. PAUSED before deploy. See DECISIONS 2026-06-25 (×2).

## ✅ DEPLOYED + LIVE-VERIFIED 2026-06-23 (run 28040528379, build+deploy GREEN; CI commit 97049ef) — WCAG 2.1 AA accessibility pass (all 4 pages)
**Live checks all pass** (headless on the deployed site, cache-busted). Per-criterion re-confirmed LIVE: 1.1.1
chart `role="img"`+`aria-label` + visually-hidden data tables present AND view-accurate (cancer table 36 rows at
3y → **12 rows at 12mo**, proving it tracks the on-screen window; RTT rate+count 36; compare funnel 157 /
percentile 139; tables render as a 1×1px clipped box, invisible to sighted users); 2.1.1 focus tooltip shows on
keyboard focus (display:block); 4.1.2 segmented `aria-pressed`="true,false,false", modal `role=dialog`+
`aria-modal`, `.wrap` inert while open, focus→close on open + **returned to Expand on close**; 4.1.3 `#live`
present all pages; 1.4.10 **0px** horizontal overflow at 320px (cancer/rtt/compare); 1.4.3 text `--target-text
#9a6600` + `--milestone-text #75632f` live; 1.4.11 lines `--org-muted #4f9aa6` / `--nat #838f95` / `--milestone
#a08a52` live (clean 3:1 pass). Landing + all nav links underlined. Charts still render correctly for sighted
users (verified screenshot: strong org line, provisional dashed tail, England grey present-but-secondary, dark
amber target label). CI: 55 tests pass; RTT recon OK @2025-04 (pct18 0.5973 / waitlist 7,389,065); TF-sum gate
max|Δ|=0 (24,776 org-months); ODS LIVE fetch both pipelines (427 orgs / 353 former / 556 trust codes, as_of
2026-05-07 — not the fallback); provider-type guards intact (build didn't fail). Audience stays professional
(NHS managers/analysts/clinicians) — accessibility only, NO plain-language changes. Audit → fixes → one
trade-off resolved (full line nudge), all in this session (see DECISIONS 2026-06-23 ×3). Shipped across cancer,
rtt, compare.html + landing:
- **1.1.1** every chart `<svg>` → `role="img"` + `<title>`/`<desc>` + summary `aria-label`, PLUS a visually-hidden
  data `<table>` per big chart / funnel / percentile built from the SAME on-screen series (view-accurate:
  measure/group/TF/route/range/England overlay; provisional + low-reliability flags as TEXT). Sparklines get a
  trend `aria-label`.
- **2.1.1** SVG data points keyboard-focusable (`tabindex=0` + per-point `aria-label`); the hover tooltip also
  fires on focus, with a visible focus ring.
- **4.1.2** `aria-pressed` on segmented toggles + cards, `aria-selected` on listbox options, illegal
  search-in-listbox fixed (role moved to options container), expand modal → `role="dialog"`+`aria-modal` + focus
  trap + background `inert` + return-focus-on-close.
- **4.1.3** polite `#live` region announces chart updates + empty/fallback states.
- **1.4.10** no horizontal scroll at 320px (relaxed fixed min-widths, capped native selects, wrap long-waiter
  control). **1.4.1** nav links underlined.
- **1.4.3 text** decoupled: amber "below target" + target-% labels → `--target-text #9a6600` (4.9:1), RTT
  milestone labels → `--milestone-text #75632f` (5.9:1), cancer low-reliability figure → dark ink.
- **1.4.11 lines (full nudge, chosen by user):** low-reliability/provisional `--org-muted #6db0ba→#4f9aa6`
  (3.23:1), England grey `--nat #9aa7ad→#838f95` (3.32:1), milestone `--milestone #b8a06a→#a08a52` (3.36:1) —
  all now clear 3:1, so the earlier "rely on shape+dash redundancy" 1.4.11 stance is MOOT (clean pass). Primary
  solid org line untouched, so the visual hierarchy holds (render-verified headless before deploy).
- Pre-deploy: 55 tests pass; headless confirms tables present + view-accurate, focus tooltips, dialog/inert +
  focus return, `aria` state, live region, 0px 320px overflow, charts still render correctly for sighted users.

## ✅ DEPLOYED + LIVE-VERIFIED 2026-06-23 (run 28019834569, build+deploy GREEN; CI commit 9dbac27) — consistency-cluster round (4 changes)
Bundled into one watched workflow_dispatch. **Live checks all pass** (curl + headless on the live site):
RTT picker shows "North East and Yorkshire" beside Airedale (RCF), independent NPR01 + ICBs + National show
none; live data 162/594 providers with a real region, ICBs all England. Cancer header nav = "**Cancer** ·
RTT · All dashboards" with NO visible Compare link; compare.html still 200s. Per-org "Compare this trust"
link present in markup but hidden (href `compare.html?org=RCF&std=CMB62`). Nav consistent across all three
(landing current "All dashboards", cancer "Cancer", rtt "RTT"; same order). compare.html `?org=RCF` live →
scope auto-set to NE&Y + 2 highlight rings. CI: 55 tests, RTT recon OK @2025-04 (pct18 0.5973 / waitlist
7,389,065), TF-sum max|Δ|=0, ODS LIVE fetch for BOTH pipelines (427 orgs / 556 trust codes, as_of
2026-05-07 — not the fallback), provider-type guards intact (build didn't fail). Detail below:
- **RTT region in the provider picker.** The RTT source has no provider region (it wrote "England" for all);
  now it REUSES the cancer dashboard's authoritative (Parent_Org-derived) region, keyed by org code, via new
  shared `pipeline_common/regions.py` reading committed `site/cancer/data/index.json`. So an RTT trust's
  region == the same trust's cancer region by construction. Providers only (ICBs stay "England", matching
  cancer); fail-open to "England" if the cancer index is absent. Front-end byline reuses cancer's exact
  logic. Rebuilt: both gates green (SPN pct18 0.5973 / waitlist 7.39M; TF-sum max|Δ|=0); 162/594 providers
  got a real region, **0 mismatches** across 163 shared codes; independents/ICBs/National show none.
- **CWT comparator soft-hidden.** One CSS rule `.compare-soft-hidden{display:none!important}` hides the header
  "Compare Providers (Beta)" link; compare.html stays live at its URL. Reversal = delete that one rule.
- **Compare-this-trust link (built, soft-hidden).** Per-org cancer link `compare.html?org=<code>&std=<std>`,
  provider-only (JS), hidden via the SAME class so it un-hides with the comparator (no rework). compare.html
  consumes `?org=`: scopes the funnel to the trust's region + rings/labels its point (funnel + percentile).
- **Navigation — Option A across all 3 pages.** Consistent `.dashnav` strip `Cancer · RTT · All dashboards`
  on landing/cancer/rtt, current page bold non-link (`aria-current`), unified `·` separators. Upgrade path to
  a segmented switcher (Option B) noted for when a 3rd dashboard lands. 55 tests pass.

## ✅ DEPLOYED + LIVE-VERIFIED 2026-06-22 (run 27966254999, build+deploy GREEN)

## ✅ DEPLOYED + LIVE-VERIFIED 2026-06-22 (run 27966254999, build+deploy GREEN)
- **Footer accuracy reword, BOTH dashboards.** Hiding is org-type-agnostic (the inactivity rule keys on
  recent activity, not provider type — dormant NHS trusts are hidden alongside independents: cancer 34
  trusts + 17 independent hidden, RTT 22 + 42), so the RTT footer's "independent-sector providers … are
  hidden" was misleading. Reworded: RTT → "Providers with very little recent activity (no month reaching 100
  on the waiting list in the past year) are hidden. Non-English-commissioned activity is also excluded.";
  cancer → "Organisations with very little recent activity (for providers, no standard reaching at least 10
  patients in a single month over the last year; for commissioners, very low recent total volume) are
  hidden." Provider-type-filter mention deliberately dropped (toggle is self-evident in the UI). Live-
  verified: both footers render with the new copy, clean punctuation, dynamic dates intact.

## ✅ DEPLOYED + LIVE-VERIFIED 2026-06-22 (run 27964831412, build+deploy GREEN)
- **"Formed / new organisation" note scoped to genuine recent formations, BOTH dashboards.** It previously
  fired on any current org with predecessor links (wrongly on e.g. Oxford RTH, formed 2011). Now shows only
  when BOTH: the DATA series is truncated (starts > Apr-2022 floor + ~2mo) AND a predecessor itself closed
  within the data window (a real handoff — uses reliable succession close-dates, not the sparse ODS
  formation date). Both needed: truncation alone over-fires on late-reporting MH/ambulance trusts (which
  carry old-merger predecessor links); the handoff alone fires on a boundary gainer (QRL). Closed/Former
  note unchanged (asymmetric). Live: RTT RTH/R1L/QRL no note, Z9B2Z Formed note, QNQ Former unchanged;
  cancer RTH no note, QNQ Former unchanged. Formed notes now = RTT 6 (the 2026 ICBs) / cancer 0 (until April
  CWT lands). 53 tests. See DECISIONS 2026-06-22.
  (Footer-accuracy reword that this entry flagged as pending is now DEPLOYED — see the top entry.)

## ✅ DEPLOYED + LIVE-VERIFIED 2026-06-22 (run 27962238975, build+deploy GREEN; CI commit a11c663)
- **Provider-type hardening + cache-bust, BOTH dashboards.** Follow-up to a reported "RTT Independent Sector
  empty" — root cause was a stale browser/CDN cache skew (new index.html + cached pre-`ptype` index.json);
  the deployed data was correct (423 RTT independents tagged, all matched). Shipped two fail-loud guards +
  the real recurrence-fix:
  - **Guards:** `ods.assert_independents_tagged` makes a dashboard with providers fail the build if it tags
    ZERO independents while an ODS trust set is present (silent match failure); both real `run.py`
    entrypoints refuse to build if `nhs_trust_codes` is empty (transient ODS failure). Synthetic/tests skip.
  - **Cache-bust:** data files (except meta.json) fetched via `durl()` with `?v=<token>`, token = dynamic
    `meta.built_at` digits; meta.json fetched `no-store`. `?v=` is part of the cache key, so a new
    index.html can never pair with a stale index.json. 52 tests.
- **Live checks:** RTT Independent Sector populated (423 tagged / 381 visible), cancer unchanged (28 / 11
  visible); data requests carry `?v=<built_at token>` (meta.json stays bare/no-store); CI ODS live fetch ran
  (556 trust codes), both RTT gates passed, no guard tripped. recon + ODS fail-soft intact.

## ✅ DEPLOYED + LIVE-VERIFIED 2026-06-22 (run 27958596878, build+deploy GREEN; CI commit f7adeee)
- **Provider-TYPE picker filter, BOTH dashboards.** A segmented [NHS Trusts | Independent Sector] control
  scopes the org dropdown on the Providers view only (England/Commissioners unaffected), **default NHS
  Trusts** (independent-sector opt-in, so the ~32 young IS clinics drop off the default RTT list). Split is
  by ODS PRIMARY role — NHS trust (RO197/RO107) vs non-NHS-trust (everything else; the lone RO157 "non-NHS
  org" and any future odd role fold into Independent Sector → no "Other" bucket). Both dashboards a clean
  two-way split: cancer 173/28, RTT 171/423. Deep-link safety: a `?org=` link follows the org's type so the
  filter never hides it. `pipeline_common/ods.py` emits `nhs_trust_codes` (556) into the shared cache; builds
  tag provider index entries `ptype:"independent"` (absent => NHS trust, fail-open). 51 tests.
- **Live checks:** ptype tags present (cancer 173/28, RTT 171/423, no ICB tagged); default Providers = NHS
  Trusts (Airedale RCF) on each; switch → Independent re-scopes (RTT NPR01 / cancer NYT); deep-link
  ?org=NPR01 / ?org=NYT → filter auto-switches to Independent and shows the org. CI: ODS live fetch ran for
  both pipelines (not the fallback); both RTT gates intact (recon OK, TF-sum max|Δ|=0); ODS fail-soft path
  unchanged.

## ✅ DEPLOYED + LIVE-VERIFIED 2026-06-22 (run 27948177203, build+deploy GREEN; CI commit aa3c52b)
- **Self-updating ODS org-status feature, BOTH dashboards** + **Part A RTT copy ×3** shipped together in one
  watched workflow_dispatch. Three lifecycle states (current / former-but-selectable / hidden) from ODS ORD
  succession links via shared `pipeline_common/ods.py` (FORMER = succession-link-passed, NOT Status); young
  orgs shown from month one; fail-soft to committed `ods_classification.json`. 50 tests pass.
- **Live checks (all explicit, headless + curl):** both dashboards 200; ODS fetch ran in CI for BOTH
  pipelines ("classified 416 orgs, 344 former, as_of 2026-05-07" — live path, not fallback); both RTT
  fail-loud gates intact (recon OK, TF-sum max|Δ|=0); QNQ/Frimley in Former group, selectable, 48mo history
  to Mar-2026, related-orgs note (→ S0E4D/S9B9J/QRL); Z9B2Z young, shown from its single Apr-2026 month, NOT
  hidden, "Formed" note; "Former organisations" picker group renders on each; Part A banner/footer/subtitle
  live with dynamic dates ("Data to April 2026. Last updated 22 June 2026"); un-hide-only confirmed
  (RTT 96→64, cancer 66→59, live_hidden ⊆ before_hidden, 0 wrongly-dropped); fail-soft re-confirmed
  (simulated outage → committed-cache fallback, no crash). Classification cache is a committed BUILD INPUT
  at repo root (not in the Pages artefact — it reaches users baked into index.json).
- Note: cancer data ends Mar-2026 (publication lag), so its 6 new ICB codes appear automatically when April
  CWT publishes; RTT already carries them. YOUNG_WINDOW_MONTHS=12 left simple per user (new IS clinics
  accepted as honest current providers).

## RTT dashboard (second dashboard, /rtt/) — increments 1 & 2 BUILT, NOT deployed ⏸
Parallel stack to the cancer one: `pipeline_rtt/` (config + build), `tests_rtt/` (9 tests),
`site/rtt/index.html` + `site/rtt/data/`. Source = the monthly RTT "Full CSV data file" (Incomplete
pathways, Part_2), 49 months Apr-2022→Apr-2026, NONC excluded, 6 metrics derived from the 105
wait-bands. 642 orgs (594 providers [96 hidden], 48 ICBs) + national. **TWO fail-loud gates pass**:
national vs Apr-2025 SPN (pct18 59.73%, waitlist 7.39M, w78/w104 exact, w52/w65 within 1%) AND the
TF-sum gate (24,776 org-months: sum of 23 TFs == C_999 total, max|Δ|=0). Front end reuses cancer
CSS/picker/range/expand/export/England mechanics; three cards = the measure toggle [% within 18wk ·
Waiting-list · Long waiters]; % chart = cancer CMB model + 65/70/92% lines; NEW count chart
(auto-scaling numeric axis); **treatment-function breakdown selector** (grouped Specialty/Other, 23
TFs) drives all three measures, with low-reliability (n<10) markers + matched England overlay + empty-TF
state; long-waiters [52·65·78·104] sub-control. Feb-2024 break marker+banner on all measures. 41 tests
pass (32 cancer + 9 RTT). **Functionally complete** (picker + 3 measures + 2 chart types + TF
breakdown + reuse mechanics). REMAINING: polish, deploy + CI/cron — awaiting the user. See DECISIONS.md
2026-06-19 (increments 1 & 2).

## Deployed and live ✅

- **Live site (post-restructure 2026-06-20):** https://drjmartins.github.io/waiting-times/
  — root landing → **/cancer/** (relocated Cancer dashboard + compare) and **/rtt/** (RTT dashboard).
  Old URL https://drjmartins.github.io/cancer-waiting-times/ now a stub repo doing a query/path-preserving
  redirect to …/waiting-times/cancer/.
- **Repo:** https://github.com/drjmartins/waiting-times (renamed from cancer-waiting-times; public)
- **Deploy:** GitHub Pages via `.github/workflows/update-data.yml` (daily cron
  `0 16 * * *` + manual `workflow_dispatch`). Build runs tests → fetches any new
  NHS files → rebuilds `site/data` → commits data back (only when something other
  than `meta.json`'s timestamp changed) → deploys the Pages artefact. Source is
  genuine NHS England CWT data (OGL v3.0), 2022-23 → 2025-26.
- **CI hardening (2026-06-10):** actions pinned to Node 24-capable majors
  (checkout v6, setup-python v6, upload-pages-artifact v5, deploy-pages v5) ahead
  of the 16 Jun Node 20 removal; the commit step no longer makes a daily no-op
  "Auto update CWT data" commit (skips when only `meta.json` built_at changed).

### Per-org page — v2 → v4, all shipped & verified live
- **v2 redesign:** National default, grouped picker, ONE big time-series chart +
  three summary cards (latest % + sparkline) that switch the standard, hover
  tooltips, target-aware card cues.
- **v3 breakdown filtering:** load-on-demand per-org breakdown files
  (`org/<CODE>.breakdown.json` + `national.breakdown.json`, gitignored, rebuilt
  each run); single-dim + published pairwise slices (no Cartesian cross), UI
  reshapes per standard (FDS28 = cancer only), slice-matched national line,
  sub-threshold months shown-but-flagged.
- **v4 visual refinements (live as of run 27281517441):** (1) National/Providers/
  Commissioners type buttons + dependent dropdown (defaults to first org so the
  chart never blanks); (2) both org + breakdown dropdowns searchable (type-to-
  filter); (3) Size-of-the-prize follows the active standard — selector removed,
  slider kept; (4) legend shows marker dot + line matching the chart; (5) low-
  reliability (n<10) recoloured grey → muted org teal (`--org-muted`), distinct
  from the grey England line, still de-emphasised.

### Verified on the first watched deploy (all four checks) — still holding
1. Actions run green and the Pages site loads (200s across pages + data).
2. The checkout-without-`data/raw` re-fetch path works (clean-runner re-fetch +
   committed manifest+store back).
3. Download slices resolve on the live site (per-FY CSVs, headline extract,
   gzipped full file). `downloads/` + the breakdown files are artefact-only
   (gitignored, rebuilt every run into the Pages artefact).
4. Daily cron registered; the no-op path rebuilds + deploys cleanly (and, since
   the CI fix, without making a commit).

### Cancer-type aggregation (NHS's ten groups) + shared group filter (v5) — SHIPPED
- **PART A pipeline:** `pipeline/cancer_groups.py` rolls raw Cancer_Type labels into
  NHS England's ten tumour-site groups (sourced from NHS's own label hierarchy + CWT
  Monitoring Dataset Guidance v10.0; the only inferred mappings are the four
  FDS28-only sites with no dashboard group → Other). Added as a `cancer_group` dim in
  the per-org breakdown files ALONGSIDE the raw dims. Gate PASSED: the ten groups
  reconcile EXACTLY to the all-cancers total for all 27,816 org-month-standard cells
  across FDS28/CMB31/CMB62 (max |Δ|=0).
- **PART B front end:** shared searchable "Cancer group" selector above the three
  cards drives all three cards + big chart + size-of-the-prize together; persists
  across standard/org switches; default All cancers; muted-teal low-reliability
  (n<10) treatment on thin group sparklines + card caveat. Shared-group and
  big-chart granular filter are mutually-exclusive lenses (CONFIRMED at review).
- **Polish shipped same cycle:** Other-group cross-standard caveat (hint + tooltip);
  accurate "latest month, same rate as the card" basis label on the prize (the
  pooled-rate concern was a premise error — the per-org prize uses the latest single
  month and already matches the card; see `DECISIONS.md`). 25 tests pass.

### Item 1 — "Size of the prize" removed (2026-06-11) — DEPLOYED ✅
User wanted it gone, not hidden. Section + CSS + JS + call sites removed; no data
change (it read only core series fields). Deployed standalone (run 27339479076,
green) and verified live (zero prize refs).

### Items 2 & 3 — v6 context-aware breakdown dropdowns — DEPLOYED ✅
Group-aware **Referral route** dropdown + all-cancers-only **Treatment modality**
dropdown, built on the ≥1% activity bar (route characterisation showed routes are
partly cancer-specific — see `DECISIONS.md`). New `cancer_group_route` pipeline dim
(ten groups × route, composite groups aggregated, ≥1% per-org filter, fail-loud
reconciliation guard + store test). Front end uses a composite GROUP/ROUTE/MOD
state; spec's hide-and-reset transitions implemented. 30 tests pass; all 5 review
cases + transition + invalid-route reset rendered (screenshots/v6_a..f). Two
interpretation calls were accepted by the user as-is: raw cancer-subtype +
bare-modality breakdown options dropped (the ten-groups + route model is enough —
see deferred item below); FDS28 keeps its "no breakdowns published" line rather than
gaining a stage dropdown.

### v7 — labelling/copy + data items + org-hiding — DEPLOYED ✅ (2026-06-12, run 27406241866)
Full set live + verified. Part 1: new title/subtitle, grouped provider/commissioner
pickers (Trust/ICB first; data classifies cleanly 173/28 + 42/8), byline prefixes
dropped, group helper text removed, Oct-2023 banner moved below the chart, redundant
third footer line removed. Part 2: Missing/Invalid excluded from "Other" (sentinel
EXCLUDED_GROUP; gap is FDS28-only ~0.28%, CMB31/CMB62 still exact; reconciliation
test one-directional); composite-group composition descriptions sourced from
cancer_groups -> meta.json (drift-guarded); Oct-2023 dashed "standards changed"
marker on CMB31/CMB62 charts only. Part 3: negligible-activity orgs hidden from the
picker, computed DYNAMICALLY each build, SELECTION-ONLY (files written, org in
store/downloads + reachable by ?org=) — commissioners with recent-3-month pooled
denom < 2000 (8 hubs). **v7.1 (same day):** provider rule TIGHTENED to a recent
window — hide if no standard clears n>=10 in a single month over the LAST 12 MONTHS
(config.PICKER_PROVIDER_WINDOW_MONTHS), catching dormant/defunct-merged codes that a
historical-only rule missed (now 58 providers hidden, 185 selectable); banner made
CMB31/CMB62-only (hidden on FDS28); banner's "defaults to 2023-10 onward" trailer
removed; footer gained a transparency note about the hiding. Live: CSH Surrey + the
dormant codes hidden, all reachable by ?org=. 32 tests pass.

### Chart-polish series (v8–v14) — ALL SHIPPED ✅ (2026-06-15)
A run of front-end-only (site/index.html, no pipeline/data change) visual passes on the
per-org page, deployed and verified live. v8/v9 (run 27547584946 / 27548485696): label/legend
tidy-up, "National"→"England", provider-dropdown layout fix, index gap fix, "Beta" relabels +
compare-page header. v10–v14 bundled into ONE deploy (run 27560316331, build+deploy GREEN,
verified live) — thirteen changes to the big breakdown chart + cards:
- **Title/hint:** group hint removed; chart title = `standard — cancer group` always (incl.
  All cancers); route/modality dropped from the title (read from the dropdowns).
- **Legend:** one line, organisation-only labels —
  "this organisation · low reliability (n<10) · provisional | England · target".
- **England line:** faint SOLID grey (dashed reserved for the org's own uncertain states) so
  it never collides with a provisional org line.
- **Annotations clear of data:** "standards changed" parked in a header strip (BG.P.t); the
  "target NN.N%" label moved into the right margin (BG.P.r).
- **Markers + precedence:** uncertain states shape-distinct — provisional = open circle +
  dashed, low-reliability (n<10) = open square + dotted — both in the SAME lighter teal, so
  shape+dash carry the distinction; final = strong solid teal + filled circle. Per-point state
  mutually exclusive, PROVISIONAL WINS (a both-states point renders provisional; square =
  final-but-low only).
- **Card sparklines:** brought in line with the chart's colour logic (lighter teal for
  provisional/low-n segments, strong solid teal for final); latest-point amber/teal target-cue
  dot deliberately kept (the at-a-glance "meeting target now?" signal).

### v15 — per-org chart: time-range + expand + image export — DEPLOYED ✅ (2026-06-16, run 27619479528)
Live + verified (build+deploy green; headless live render confirms 3y/12mo/All + FDS28 marker
behaviour). Three front-end-only additions to the per-org big chart (site/index.html), built to compose
(chart renders in the modal first, then export reads what's on screen, all respecting the range):
- **Time range** — fixed ROLLING window, segmented presets [3 years · 12 months · All],
  default 3 years (36mo), applied to ALL standards incl. FDS28. Clips the big chart's org +
  England series (cards' sparklines untouched). Adaptive x-axis labels (~8–10). The Oct-2023
  in-chart marker shows ONLY when the visible window includes pre-break data, and NEVER on
  FDS28 (verified clean in 3y/12mo/All); once the rolling window clears Oct-2023 (~2027) it
  drops out on its own, banner-below still carries the note.
- **Expand** — toolbar Expand button opens the chart in a fullscreen modal by MOVING the live
  panel into it (route/modality dropdowns, tooltips, export all keep working); ✕ / Esc /
  backdrop close. `?expand=open` hook.
- **Download** — PNG + SVG of a self-contained export (title + organisation + legend + the
  on-screen chart, CSS vars resolved, fonts inlined). Captures the on-screen slice AND window;
  filename + subtitle title-cased (e.g. Airedale-NHS-Foundation-Trust-CMB62-Haematology.png).

### v16 — per-org chart: show/hide England comparison line — DEPLOYED ✅ (2026-06-16, run 27620549350)
Live + verified (build+deploy green; headless live render confirms ON/OFF + England-tab behaviour).
A "Show England" checkbox in the chart filter row (default ON), providers + commissioners only.
OFF hides the grey England series, its legend entry and its pull on the y-scale (org series,
target, Oct-2023 marker, axis all stay); hidden on the England tab (the org's own line IS England
there). One switch — it gates ACTIVE_NAT in renderBig — so it carries into the expand modal and
the PNG/SVG export, and composes with the time-range window. Persists across switches; deep-link
hook `?england=off`.

## Open items
1. **11 June decommission verification — user to run on/after 11 Jun 2026.** The
   pre-deploy check confirmed the pipeline pulls the surviving Combined (ICB-based)
   source, not the decommissioned provider flat files. A copy-paste verification
   block (live discover + column-mapping + `gh run list`) was handed over; run it
   to close the residual. CI's fail-loud `normalise.py` + test gate protects the
   daily refresh meanwhile (a bad layout fails the run rather than shipping).
2. **NEXT TASK — data cleaning.** To be scoped in the planning session before any
   build. Not started.
3. **Deferred (v6).** Cancer sub-type breakdown control. The v6 dropdown narrows
   cancer to NHS's ten groups (+ group-aware route); the finer raw cancer-subtype
   level (e.g. 'Haematological - Lymphoma') is no longer pickable in the UI. The
   data STILL RETAINS the sub-type level (the `cancer` dim in the breakdown files),
   so a sub-type control can be added later if anyone misses it — a deferral, not a
   data loss.
4. **Parked.** (a) "Compare this trust" cross-link from the per-org page into the
   comparison view (planned, not built). (b) Overdispersion ↔ study-protocol
   alignment — a methods decision for the research side (multiplicative Winsorised
   φ vs additive random-effects, Winsorisation fraction, whether to adjust).
   (c) `python -m pipeline.run --synthetic` fails on an unmapped synthetic
   Cancer_Type 'Lower GI' (cancer_groups.group_for raises) — PRE-EXISTING, found
   2026-06-29. CI-independent (CI runs pytest + the real run only, never
   --synthetic), so it never affected deploys; the offline-dev synthetic path is
   just broken. Fix someday: map 'Lower GI' (and audit synthetic's labels against
   GROUP_OF) so the offline demo build works again.

## Known things to keep an eye on
- FDS28 funnel has very high φ (~80) — verified not degenerate (15 trusts beyond
  95%, 4 beyond 99.8%), so adjustment isn't masking all outliers. Re-check if NHS
  data shifts. See `DECISIONS.md`.
- ICB as a comparator scope is deliberately deferred (trust↔ICB is many-to-many);
  needs an attribution convention from the study protocol before building.

## Running locally
```bash
pip install -r requirements.txt
python -m pipeline.run --synthetic   # offline dev (separate synthetic store)
python -m pipeline.run               # real run against NHS England
python -m http.server 8099 --directory site   # then open localhost:8099
python -m pytest -q                  # tests (also run in CI before deploy)
```
