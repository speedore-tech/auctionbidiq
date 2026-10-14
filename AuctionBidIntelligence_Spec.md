# Auction Bid Intelligence System — Build Spec

**Working name:** BidIQ (placeholder)
**Owner:** Kashif — Speedore LLC / Sohi Transportation
**Purpose:** Turn historical auction sold‑data into a single, defensible **"all‑in max bid"** for any lot a buyer is considering, with a win‑probability curve, equity check, and risk flags — so bidding is a number, not a gut call.

---

## 1. Problem & Goal

At live truck/trailer auctions (Taylor & Martin and similar), the hard question is always the same: **"What's the most I should pay for this exact unit, all‑in, and still make money?"** Today that answer is assembled by hand — pulling comps from PDF result sheets, adjusting for miles/engine/condition, adding buyer's premium and transport, and checking it against retail. This system automates that end‑to‑end.

**Primary output for every lot:**
- **Fair value** (what it typically sells for) and **best‑buy** (a steal)
- **Target / Walk‑away / Hard‑ceiling** hammer prices
- **All‑in landed cost** at each (premium + transport + fees)
- **Win probability** at any bid level (empirical)
- **Equity vs. retail FMV** (your margin)
- **Risk & eligibility flags** (CDL, title, warranty, blind‑buy reserve)

**Design principle:** every number must be **traceable to real sold comps** or an explicit, visible assumption. No black boxes — the user must be able to see *why* a bid was recommended before trusting it with real money on an absolute (no‑return) sale.

---

## 2. Users & Core Use Cases

| User | Use case |
|---|---|
| Buyer (you) | Pre‑sale: build a bid sheet for a catalog of target lots |
| Buyer | Live: glance at target/walk/ceiling + win‑prob while bidding |
| Buyer | Blind buy: value a far‑away lot from photos + specs only |
| Analyst | Backtest: "what would the model have bid vs. what it actually sold for?" |
| Ops | Post‑sale: log won/lost + actual condition to improve the model |

**Key non‑goals (v1):** no automated bidding/execution, no live price feed integration, no financing module. Human stays in the loop on every bid.

---

## 3. System Architecture

```
┌─────────────┐   ┌──────────────┐   ┌─────────────────┐   ┌──────────────┐
│  INGESTION  │──▶│ NORMALIZE &  │──▶│  PRICING /       │──▶│ BID          │
│ PDF results │   │ ENRICH       │   │  PREDICTION      │   │ RECOMMENDER  │
│ catalogs    │   │ (comp store) │   │  ENGINE          │   │              │
│ mileage/hrs │   └──────────────┘   └─────────────────┘   └──────┬───────┘
└─────────────┘          │                    ▲                   │
                         ▼                    │                   ▼
                  ┌──────────────┐   ┌─────────────────┐   ┌──────────────┐
                  │ RETAIL FMV   │   │ RISK & ELIGIBIL.│   │ OUTPUT:      │
                  │ (web scrape) │   │ CDL/VIN/title/  │   │ bid sheet,   │
                  │              │   │ warranty        │   │ dashboard,   │
                  └──────────────┘   └─────────────────┘   │ what‑if      │
                                                           └──────────────┘
```

Each block below is independently buildable and testable.

---

## 4. Data Model (the comp record)

The atomic unit is one **sold lot**. Store every historical sale, not just targets.

```
SoldComp {
  sale_id            # e.g. 2026055
  sale_date
  location           # e.g. "SOUTH SIOUX CITY"
  lot_no
  lot_type           # M = operable, P = non-op/parts
  year
  make               # FRTL, INTER, VOLVO, KENW, PETE...
  model_raw          # "M2 106 BUSINESS CLAS"
  model_group        # normalized: "FRTL_M2_106" (see §6)
  body_class         # CONV / TRUCK / straight-truck / tractor / trailer
  engine_raw         # "CUMMINS ISB 6.7 LIT"
  engine_family      # normalized: CUMMINS_B67, DETROIT_DD8, NAVISTAR_A26...
  transmission       # ALLISON_AUTO, MANUAL_10SPD...
  miles              # int, nullable
  hours              # for reefers/APUs (nullable)
  branded_title      # bool
  non_operable       # bool
  sold_price         # hammer price (pre-premium)
  # --- enriched (computed) ---
  age_years          # sale_year - model_year
  miles_per_year
  lifetime_mph       # miles / engine_hours  (reefer/idle proxy, if hours present)
  source_file        # provenance
}
```

Also store **catalog lots** (future/target units) in a parallel `TargetLot` table — same schema plus body length, GVWR, VIN, APU/sleeper, door/liftgate, condition notes, and photos. Result files **lack** body length, GVWR, and VIN — those come from catalogs or manual entry.

---

## 5. Ingestion

### 5.1 Auction result PDFs (primary)
T&M "PRICE LISTING BY TYPE/LOT#" sheets are **fixed‑width, column‑positioned**. Parse with **pdfplumber coordinate bucketing**, not naive text slicing (sub‑fields like engine/sleeper/transmission share the description band and bleed with slicing).

Column x‑ranges (validated on T&M 2026xxx format):
```
type   x < 30         miles    x 600–690  (comma number, no cents)
lot    30–60          branded  'Y' at x 660–700
year   60–90          non_op   'Y' at x ≥ 700
make   90–120         price    token with cents (x > 700)
class  120–160
model  160–291
engine 291–430
desc   430–525   (sleeper/body — blank on box trucks)
trans  525–625
```
Header line gives `SALE: <id> <date> <location>`. **Idempotent**: re‑ingesting a file replaces its rows (keyed on sale_id + lot_no).

### 5.2 Catalog PDFs
Catalogs carry what result files don't: **body length, GVWR, APU, sleeper size, engine‑brake, matched groups.** Parse to `TargetLot`. These feed the recommender for upcoming sales.

### 5.3 Mileage / reefer‑hours sheets
Separate sheets map lot → odometer (in thousands) / ECM miles, or reefer **Total vs Engine vs Electric hours.** Join on lot_no. **Flag ODOM vs ECM mismatch** (tamper signal) and record which hours metric is shown (Total engine hours ≠ engine‑only hours — a known trap).

### 5.4 Automation
Run ingestion as an **n8n workflow** (fits existing stack): watch an inbox/folder for new result PDFs → parse → upsert to Postgres → recompute cohort stats. Manual upload path for ad‑hoc files.

---

## 6. Normalization & Enrichment

The single biggest accuracy lever. Garbage grouping = garbage comps.

- **model_group**: regex‑map raw strings to canonical groups. Critical rules learned:
  - `"M2 106"` and `"M2 106 BUSINESS CLAS"` → **same group** (Business Class is the family name; 106 is the model). Do **not** split them.
  - `M2 106` (106″ BBC, non‑CDL class) vs `M2 112` (112″ BBC, heavier, often CDL) → **different groups.**
  - International **MV607** and **4300/DuraStar** = medium‑duty box trucks, **direct alternatives to M2 106** — tag as a shared "medium‑duty box" super‑group for cross‑shopping, but keep model identity.
- **engine_family**: collapse "CUMMINS ISB 6.7 LIT", "CUMMINS 6.7 LITER", "CUMMINS B6.7" → `CUMMINS_B67`. Keep a **serviceability/desirability weight** per family (e.g., Cummins B6.7 > Detroit DD5/DD8 for resale & nationwide parts — a real price driver).
- **age_years, miles_per_year, lifetime_mph** computed.
- **Exclusions:** `non_operable` and `branded_title` units are **excluded from pricing stats by default** (kept, flagged, shown separately). They are a different market.

---

## 7. Pricing / Prediction Engine

Produce, for any target spec, a **price distribution** — not a single number.

### 7.1 Method A — Cohort percentiles (always available)
Filter comps to a cohort: same model_group, year ± band, miles ± band. Compute **p25 (best‑buy), p50 (fair/going‑rate), p75, max (ceiling)**, plus **N** (comp count). Auto‑widen bands until N ≥ min_threshold (e.g., 5); record how wide it had to go (feeds confidence).

### 7.2 Method B — Regression (when N sufficient)
Family‑level OLS:
```
sold_price ≈ base − (β_age · age) − (β_miles · miles) [+ engine dummies + flags]
```
Gives a point estimate **and** residual std → **prediction interval**. Example coefficients already observed in data (LT625 family): ≈ **$2,585 per 100k miles**, ≈ **$5,461 per year**. Recompute per family on each ingest. This is what catches the "416k‑mi and 1M‑mi both priced $20k" error — mileage must move the number.

### 7.3 Method C — Retail‑minus fallback (sparse/new)
When there's **no comp** (e.g., a 2023 M2 at auction — newer than any historical sale), anchor to **retail FMV × auction‑discount factor** (observed ~55–70% of retail for clean runners). Flag these predictions as **low‑confidence / extrapolated**.

### 7.4 Blend & confidence
Weight A/B/C by data density. Emit a **Price Confidence score** (High/Med/Low) driven by **N**, band width, and method. Confidence is shown separately from the price — never bake "thin data" into the point estimate by demoting good trucks (a prior mistake: don't let blank‑data fields or low N drag the *quality* score).

---

## 8. Bid Recommender (the core deliverable)

Given a `TargetLot` + its predicted distribution + user settings:

### 8.1 Hammer thresholds
```
best_buy   = p25                 (buy-well line)
target     = p25…p50 (user aggressiveness slider)
walk_away  = p75  (or fair + buffer)
hard_ceiling = min(cohort_max, user_budget_cap)   # never exceed proven market top
```

### 8.2 Convert hammer → all‑in landed
```
all_in = hammer × (1 + buyers_premium) + transport + title/doc fees
```
- **buyers_premium**: configurable per sale/lot (T&M **5% standard**, up to **10%** on some lots — store per sale).
- **transport**: `distance(origin→home) × rate` (driveaway vs haul). Origin from sale location; home configurable (DFW).
- Show the user **all‑in**, not just hammer — premium + freight is real money.

### 8.3 Blind‑buy risk reserve
When no in‑person inspection, **subtract a condition reserve** from the ceiling for uncovered risks (DPF/DEF, liftgate, transmission — i.e. everything the engine‑only warranty doesn't cover). Make it an itemized, visible deduction, not a fudge factor.

### 8.4 Win probability (empirical)
```
P(win at bid B) = share of cohort comps that sold ≤ B
```
Render as a curve: "bid $X → ~N% chance." Lets the user trade price vs. certainty explicitly ("I want ≥80% odds → bid $Y").

### 8.5 Multi‑lot / "don't accidentally win both"
When bidding several similar lots in one sale, allocate a **total budget** and sequence rules: max N wins, de‑escalate ceiling after a win, skip remaining if budget spent. Prevents the "bid on 4, win 4, cash problem" scenario.

---

## 9. Retail FMV / Equity Module

> **v1 = manual refresh** (locked decision §16). Buyer periodically enters/imports retail comps; each set is date‑stamped and flagged stale after N days. Scraper/API is a later drop‑in behind the same interface.

- Record **open‑market retail** for the exact config in the buyer's region (sourced from TruckPaper, Commercial Truck Trader, Comvoy, Rush/dealer networks). **Exclude** auction & wholesale sources when the goal is *retail* FMV.
- Compute tiers: **Dealer retail / Private‑party / Wholesale.**
- **Equity = retail FMV − all‑in landed.** Flag margin % and dollar spread on every lot. (This is the "am I buying at half of Dallas retail?" check.)
- Mileage is the dominant retail driver — bucket comps by mileage band, discount asking prices ~10% to transaction level.

---

## 10. Risk & Eligibility Module

- **CDL / GVWR classifier:** rear‑axle rating → likely class; definitive from the **door‑sticker GVWR‑LBS** line. Rule: rear ≤ 19,000 → likely 26,000 GVWR non‑CDL; 21,000+ → CDL. Always require GVWR confirmation for borderline (19,000 rear).
- **VIN tools:** check‑digit validation (well‑formed / not mistyped‑cloned), decode year/plant/origin and **cross‑check against door sticker**; checklist links to **NICB VINCheck** (stolen/total‑loss) and **NMVTIS** (title brands). Store results per lot.
- **Title/brand & non‑op flags** surfaced prominently (absolute sales have **no arbitration** — a branded/non‑runner surprise is unrecoverable).
- **Warranty eligibility** (T&M Total Trust / National Truck Protection): engine‑only, 60 days/20k, $750 deductible, $10k cap, **7‑yr/Class 2–8**, mileage caps (Class 2–7 expire 370k). Auto‑check if a target qualifies; flag "engine covered, DPF/trans/liftgate NOT."
- **Reefer logic:** swing vs lift‑gate door, 53ft, **hours ≤ threshold**, Total‑vs‑Engine hours caveat; secondary‑market TK/Carrier unit cost reference.

---

## 11. Output / UX

1. **Pre‑sale Bid Sheet** (per catalog): one row per target lot — specs, miles, GVWR/CDL, VIN/sticker checklist blanks, **target / walk / ceiling (hammer + all‑in)**, win‑prob, equity vs retail, risk flags. Printable for the floor.
2. **Live Bid Card:** big numbers — target, walk, ceiling, current‑bid input → live win‑prob + "STOP" indicator when a bid crosses ceiling.
3. **What‑if panel:** sliders for miles, premium, transport, aggressiveness → numbers recompute live.
4. **Comp drill‑down:** every recommendation expands to the actual comps behind it (traceability).
5. **Deposit/logistics reminders:** deposit amount & wire deadline, payment window (e.g., $5k deposit, wire by 3pm day‑before for first‑time buyers; full pay in 24h).

---

## 12. Data Flywheel (learning loop)

After each sale, log: **did we bid? win/lose? at what price? actual condition on arrival? repair costs? resale/lease outcome?** Feed back to:
- Recalibrate auction‑discount factor and condition reserves (predicted vs actual).
- Tune per‑engine desirability weights.
- Backtest the recommender ("model's ceiling vs. actual hammer") each cycle.

This is what turns a static calculator into a system that gets sharper every auction.

---

## 13. Suggested Tech Stack (fits existing infra)

- **Ingestion/parse:** Python (pdfplumber, pandas, numpy), orchestrated via **n8n**.
- **Store:** **PostgreSQL** (+ **pgvector** optional — embed each lot's spec vector to do "find most‑similar comps" semantic lookup when exact cohorts are thin).
- **Modeling:** scikit‑learn / statsmodels (OLS + quantile regression); start simple, keep interpretable.
- **API:** **FastAPI** — endpoints: `/ingest`, `/predict`, `/recommend`, `/whatif`, `/comps`.
- **Frontend:** React (web) / React Native (floor use on phone).
- **Deploy:** Docker.
- Could live as a **module inside LoadLedger** (shared trucking back‑office context) or standalone.

---

## 14. Phased Roadmap

> **Build the data model wide from day one** (class‑aware: box trucks, tractors, reefers) and **multi‑tenant from day one** (auth + per‑tenant config + shared/private comp split). Phase the *features* below, not the schema — retrofitting either is expensive.

**Phase 1 — MVP (comp calculator):** ingest result PDFs → cohort percentiles → target/walk/ceiling + all‑in + win‑prob, from a manual spec entry. Start with **box trucks** as the first class exercised through the class‑aware engine. (You already have the parser and the 9‑file dataset — this is weeks, not months.)

**Phase 2 — Recommender + equity:** regression layer, retail FMV scrape, blind‑buy reserve, bid sheet export.

**Phase 3 — Risk/eligibility + catalog ingest:** CDL/VIN/warranty modules, catalog parsing, upcoming‑sale watchlist/alerts matching your criteria.

**Phase 4 — Flywheel + live card:** outcome logging, backtest, live bidding UI, multi‑lot budget allocator.

---

## 15. Edge Cases & Assumptions

- Result files have **no body length, GVWR, or VIN** — never infer these from a result row; require catalog/manual entry.
- **Non‑op & branded** excluded from stats (different market) but retained & flagged.
- **"Total engine hours" ≠ engine‑only hours** on reefers — don't compare across metrics.
- **New‑than‑any‑comp** units (e.g., 2023 at auction) must fall back to retail‑minus and be marked low‑confidence.
- Buyer's premium **varies per sale/lot** — never hardcode.
- ODOM vs ECM mismatch → tamper flag, lower confidence.
- All sales assumed **absolute / as‑is / no arbitration** unless a sale's terms say otherwise — risk flags are not optional.

---

## 16. Decisions (locked)

| # | Decision | Build implication |
|---|---|---|
| 1 | **Full scope** — box trucks, sleeper tractors (LT625/Cascadia), **and reefer trailers** from day one | Schema must be class‑aware now (`body_class`, `hours`, reefer fields). Don't paint into a box‑truck‑only corner. Phase the *features* narrow, but design the *data model* wide. |
| 2 | **Multi‑tenant** — sell to other buyers later | Add auth, per‑tenant config (home region, budget, premium defaults), row‑level data isolation, and a shared vs. private comp store. Build this in from v1 — retrofitting multi‑tenancy is expensive. |
| 3 | **Retail FMV = periodic manual refresh** | No fragile scrapers in v1. Provide a simple FMV‑comps entry screen + CSV import; stamp each FMV set with a date and show staleness. Leave a clean interface so a paid API can slot in later. |

**Derived requirements from these choices:**
- **Class‑aware pricing cohorts:** reefers price on *hours* (Total vs Engine vs Electric) and door type (swing/lift) + length, not miles; tractors price on miles + sleeper size + engine (exclude Navistar A26 vs Cummins X15 — they price differently). The engine must branch cohort logic by `body_class`.
- **Per‑tenant settings object:** home/region (transport origin math), default buyer's‑premium, budget caps, aggressiveness, risk‑reserve defaults.
- **Comp store split:** a **shared** historical comp pool (everyone benefits from more sold data) + **private** target lists, bids, and outcomes per tenant. Decide early whether a tenant's logged outcomes feed the shared model (opt‑in).
- **FMV module:** manual `RetailFMV {region, model_group, year_band, miles_band, tier, price, as_of_date, source}` table; equity check reads the freshest matching rows and warns if stale (> N days).

### Still to pin down (not blockers for Phase 1)
- Whether tenants' outcome logs feed the shared model (opt‑in data‑sharing) or stay fully private.
- How much manual spec entry vs. full catalog auto‑parse for the first release.
- Pricing/packaging if sold to other buyers (per‑seat, per‑sale, flat).

---

*Built from the manual methodology worked out across the M2 / MV607 / 4300 / LT625 / reefer bidding sessions — this spec just encodes what's already been done by hand into something repeatable.*
