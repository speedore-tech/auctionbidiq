# BidIQ — Auction Bid Intelligence System

Turns historical auction sold-data into a single, defensible **all-in max bid** per lot: fair value,
best-buy / target / walk-away / hard-ceiling hammer prices, all-in landed cost, empirical win
probability, equity vs retail, and risk flags. Every number traces to real sold comps or to a
visible, editable assumption. Built from `AuctionBidIntelligence_Spec.md`.

## Quick start

```bash
pip install -r requirements.txt
```
```bash
python scripts/seed_demo.py
```
```bash
python -m uvicorn bidiq.api.main:app --port 8000
```

Open http://localhost:8000. The seed script creates a demo tenant (login in the script's
docstring) plus **synthetic** comps (`source_file = DEMO-synthetic`, sale ids starting with `D`).
Remove them once real data is loaded: `python scripts/seed_demo.py --purge`.

Load your real result sheets (shared pool by default):

```bash
python scripts/ingest_folder.py path/to/result_pdfs/
```

Tests: `python -m pytest`. Local Docker/Postgres: `docker compose up --build` (set `BIDIQ_DB_PASSWORD`).

**Production (VPS):** see [deploy/DEPLOY_STEPS.sh](deploy/DEPLOY_STEPS.sh) — Postgres + app behind Traefik on the shared `n8n_default` network, same pattern as insurancechekr. Public sign-up is off (`BIDIQ_ALLOW_REGISTRATION=false`); create the first login with `scripts/create_admin.py`, then manage everyone else on the **Users** page (admins: own workspace; platform owner in `BIDIQ_SUPERADMIN_EMAILS`: can also create separate partner workspaces).

## How the spec maps to the code

| Spec | Where |
|---|---|
| §4 data model (class-aware, multi-tenant) | `bidiq/models.py` — `SoldComp`, `TargetLot`, `Sale`, `RetailFMV`, `Outcome`; `tenant_id NULL` = shared pool |
| §5.1 result PDFs (coordinate bucketing, idempotent) | `bidiq/ingest/results_pdf.py`, `bidiq/ingest/__init__.py` |
| §5.2 catalogs, §5.3 mileage / reefer hours (ODOM≠ECM, scratch, non-runner) | `bidiq/ingest/tm_formats.py` (real T&M PDF layouts, validated on the Pekin 10/15/2026 set), `bidiq/ingest/sheets.py` (CSV) |
| §5.4 automation | `n8n/ingest_results_workflow.json`, `scripts/ingest_folder.py` |
| §6 normalization (model groups, MD-box super-group, engine families + desirability) | `bidiq/normalize.py` |
| §7 pricing: A cohort percentiles, B OLS, C retail-minus, blend + confidence | `bidiq/pricing/engine.py` |
| §8 recommender: thresholds, all-in, blind-buy reserve, win prob, multi-lot | `bidiq/recommender.py` |
| §9 retail FMV / equity (manual, date-stamped, staleness) | `bidiq/fmv.py`, Retail FMV page |
| §10 CDL/GVWR, VIN check digit + decode, warranty, reefer | `bidiq/risk.py` |
| §11 bid sheet (print + CSV), live card, what-if, comp drill-down, deposits | `web/app.js` |
| §12 outcomes + walk-forward backtest | `bidiq/services.py` (`backtest`), Outcomes page |
| §16 per-tenant settings | `bidiq/config.py` (`DEFAULT_SETTINGS`), Settings page |

### Accuracy on real data
Walk-forward backtest on 10 Taylor & Martin sales (Jul–Oct 2026, 3,671 lots, each priced only from earlier sales):
median error **24%** (High-confidence lots **20%**), overall bias +3%. Individual sales swing: Burgettstown 10/8 sold
31% under the model, Pekin 8/6 sold 7% over. Re-run it any time on the Outcomes page.

### Pricing in one paragraph
For a target spec the engine (1) fits a family OLS on log price `ln(price) ≈ b0 + b_age·age + b_usage·usage [+ b_eng·engine weight]` (percentage depreciation, recent sales weighted more)
(usage = miles, or same-metric reefer hours), (2) builds a cohort of same-model comps, auto-widening
year/usage bands until N ≥ `min_cohort_n` (tractors also match engine family; only MD-box and 53' reefer
groups may widen across models), (3) **adjusts each comp to the target's age/usage** with the regression
slopes so mileage always moves the number, (4) adds retail × auction discount when FMV exists, and
(5) blends by data density. Non-op and branded units are excluded from stats. Units newer than any comp
are flagged and lean on retail-minus. Confidence (High/Med/Low) is reported separately and never moves the price.

## API
`/api/auth/{register,login,me}`, `/api/settings`, `/api/ingest/{results,catalog,mileage,log}`, `/api/comps`,
`/api/sales`, `/api/predict`, `/api/recommend`, `/api/whatif`, `/api/targets[/{id}[/recommendation]]`,
`/api/bidsheet[.csv]`, `/api/multilot/plan`, `/api/risk/{vin,cdl}`, `/api/fmv[/import]`, `/api/outcomes`,
`/api/backtest`. Interactive docs at `/docs`.

## Known gaps / decisions to confirm
- **Parser validated only on generated PDFs.** The x-ranges follow the spec; run your 9 real result files
  through `scripts/ingest_folder.py` and check the ingest log for "unparsed lines".
- **Pre-sale documents:** T&M catalogs, mileage sheets and reefer-hour sheets are parsed from their real layouts and linked by sale date + location, in any upload order. Other auction houses' layouts would need their own parser.
- **Frontend is dependency-free JS** (no React build step); the live bid card is a phone-friendly web page, not React Native.
- **Tenant isolation is enforced in the app layer**, not Postgres row-level security. No pgvector similar-comp search yet.
- **Assumed defaults** (all editable in Settings): transport $1.75/mi driveaway, $2.75/mi haul, $150 title/doc, blind-buy reserves (DPF/DEF $3.5k, transmission $4k, liftgate $2.5k, reefer unit $5k, tires/brakes $1.5k), auction discount 55/62/70% of retail.
- **Warranty Class 8 mileage cap is unset** — the spec only gives the 370k cap for Class 2–7.
- Open §16 items left as-is: outcome opt-in (`share_with_pool`) is stored but doesn't feed the shared model yet; no upcoming-sale watchlist alerts.
