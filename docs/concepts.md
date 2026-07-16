# Key Concepts

[← Back to README](../README.md)

## Activity vs Alert

**Sell-side agents (Covered Call, Cash Secured Put):**
A **activity** is recorded for EVERY symbol on EVERY run as an `activity` document in CosmosDB. Possible values: `SELL`, `WAIT`, or `HOLD`. The activity collection is the complete audit trail. An **alert** is the subset of activities where the action is `SELL` — stored as a separate `alert` document for efficient querying.

**Position monitors (Open Call Monitor, Open Put Monitor):**
A **activity** is recorded for EVERY position on EVERY run. Possible values: `WAIT`, `ROLL_UP`, `ROLL_DOWN`, `ROLL_OUT`, `ROLL_UP_AND_OUT`, `ROLL_DOWN_AND_OUT`, or `CLOSE`. An **alert** is any activity that is NOT `WAIT` — any roll or close action that requires attention. Positions are stored within the symbol's `symbol_config` document in CosmosDB.

## Open Position Monitors

The Open Call Monitor and Open Put Monitor watch **existing** short options positions for assignment risk. They differ from the sell-side agents in several ways:

| | Sell-Side Agents | Position Monitors |
|---|---|---|
| **Input** | Symbols with watchlist flag enabled in CosmosDB | Symbols with active positions in CosmosDB |
| **Activities** | SELL / WAIT | WAIT / ROLL_UP / ROLL_DOWN / ROLL_OUT / ROLL_UP_AND_OUT / ROLL_DOWN_AND_OUT / CLOSE |
| **Alerts** | SELL only | Any ROLL or CLOSE |
| **Focus** | "Should I open a new position?" | "Is my existing position safe?" |

Positions are managed via the web dashboard or API. Each position is stored within the symbol's `symbol_config` document in CosmosDB with type (call/put), strike, expiration, status, and notes. Position monitors only run for symbols with `status: "active"` positions.

**Two-phase pipeline:** Position monitors use a two-phase architecture. **Phase 1 (Assessment)** evaluates assignment risk and produces a structured handoff JSON if action is needed. **Phase 2 (Roll Management)** receives the handoff plus a filtered options chain (see below) and selects specific roll targets (strike/expiration) with full roll economics (buyback cost, new premium, net credit/debit).

**Category-aware delta targets:** Roll agents receive the symbol's DGI category (Aristocrat, Compounder, Rising Star, High Yield, Balanced) and use category-specific delta ranges when selecting roll targets. This ensures roll strikes align with the same risk profile used when the position was originally opened — e.g., a Rising Star CC rolls to delta 0.10–0.20 (protecting upside), while a High Yield CC rolls to 0.25–0.35 (maximizing premium). The category context is injected into the Phase 2 prompt alongside the roll candidates table.

### Options Chain Filter Pipeline

Before Phase 2 receives the options chain, a 4-stage filter pipeline narrows it to relevant contracts:

1. **Type filter** — Strips the irrelevant option side (puts when monitoring calls, calls when monitoring puts)
2. **Position filter** — ±15 strikes around the current position
3. **Delta filter** — Removes deep ITM/OTM contracts outside configured delta ranges
4. **Direction filter** — Narrows to strikes/expirations valid for the roll direction (e.g., only higher strikes for ROLL_UP)

After filtering, a pre-computed **candidates table** with roll economics (buyback cost, new premium, net credit) is generated and included in the Phase 2 prompt.

**Profit optimization (premium-first roll policy):** When market indicators show the position is deeply OTM with no risk catalysts, the monitor may recommend tightening the strike to collect additional premium (ROLL_DOWN for calls, ROLL_UP for puts). This requires 3 mandatory conditions (≥10 DTE, deeply OTM, net credit) plus at least 4 of 7 flexible conditions (super-majority gate) — conservative by design. The ultra-defensive roll threshold caps maximum debit at $1 ($100 per contract). Profit-optimization rolls are tagged with a `"profit_optimization"` risk flag to distinguish them from defensive rolls. Monitor agents prioritize premium collection when rolling, considering whether to tighten strikes more aggressively when conditions allow.

**Roll types:**
- **ROLL_UP** — Higher strike, same expiration (gives more room above for calls)
- **ROLL_DOWN** — Lower strike, same expiration (gives more room below for puts)
- **ROLL_OUT** — Same strike, later expiration (more time value)
- **ROLL_UP_AND_OUT** / **ROLL_DOWN_AND_OUT** — Combined strike + expiration adjustment
- **CLOSE** — Buy back without re-selling (exit the position entirely)

## Position Snapshots & Time Series

Each position monitor run captures a **snapshot** of the position's key indicators, stored in CosmosDB for historical tracking (no TTL — kept indefinitely). Snapshots power both the chart visualization and the DPS scorer.

**Captured indicators per snapshot:**
- **Gap %** — distance from underlying price to strike (positive = OTM, negative = ITM)
- **RSI (14)** — daily relative strength index
- **MACD** — MACD line value (daily)
- **ADX** — average directional index (trend strength)
- **Midprice** — current contract mid-price (from options chain)
- **P&L %** — mark-to-market profit/loss: `(premium_received - midprice) / premium_received × 100`
- Underlying price, premium_received, timestamp

**Chart visualization:** The symbol detail page renders an interactive Chart.js time series with all indicators on dual y-axes (Gap%/P&L% on left, RSI/MACD/ADX/DPS on right). Features include tooltip with all values, weekend filtering (hidden by default), color-coded lines per indicator, and **per-attribute toggle checkboxes** to show/hide individual lines. Charts are also visible on closed positions (read-only mode, no DPS button).

## Deterministic Position Scorer (DPS)

The DPS provides **on-demand and automatic rule-based analysis** of open positions without using an LLM. It combines live options chain Greeks with historical snapshot trends to produce a deterministic HOLD/WATCH/ROLL recommendation.

**How it works:**
1. Fetches live options chain via yfinance for the position's strike/expiration
2. Extracts Greeks (delta, gamma, theta, IV) and contract mid-price
3. Reads historical snapshots for RSI, MACD, and ADX trend analysis (last 21 weekday-only snapshots, using linear regression + first-to-last delta)
4. Applies a scoring algorithm (0–100 scale, base 50) with contributions from:

| Factor | Range | Notes |
|--------|-------|-------|
| Delta | ±13 | OTM favorable, ATM penalized |
| RSI level + trend | ±20 | Direction interpretation inverted for puts vs calls |
| MACD trend | ±13 | Improving/worsening relative to position type |
| ADX | -13 to +8 | Direction-aware: rising ADX in unfavorable direction = bad |
| DTE | -10 to +8 | Contextual: depends on moneyness (short DTE + OTM = bonus) |
| Gamma | -8 | Penalty when high gamma + near ATM |
| IV level | -7 to +7 | High IV favorable for short options |
| **P&L** | **-8 to +10** | Mark-to-market: ≥80% profit → +10, <-20% loss → -8 |

**Combo modifiers (cross-factor interactions):**

| Combo | Range | Trigger |
|-------|-------|---------|
| P&L + DTE | ±5 | P&L ≥70% + DTE ≤7 → close opportunity; P&L <-20% + DTE ≤14 → time pressure |
| IV + DTE | +6 | High IV + short DTE + OTM → accelerated decay |
| MACD + RSI | ±9 | Both trends agreeing → confirmed signal |
| Delta + ADX | -5 | ATM + rising unfavorable ADX → compound risk |

**Informational factors (shown in breakdown but not scored):**
- **GAP %** — redundant with Delta; kept in chart for visual reference
- **Theta** — replaced by P&L which captures theta materialized as actual profit

**Trend calculation:**
- Uses last 21 non-None weekday snapshots (weekends filtered out)
- Linear regression slope normalized by value range
- Thresholds: `|rel_slope| > 0.08` or `|pct_change| > 3%` → trend detected
- Strength levels: weak / moderate / strong

**Decision thresholds:**
- Score ≥ 70 → **HOLD** (position is safe)
- Score 50–69 → **WATCH** (monitor closely)
- Score < 50 → **ROLL** (consider adjusting)

**Override rules:** Force ROLL when delta is extreme + ADX rising + MACD worsening. Allow HOLD when delta is high but RSI + MACD + ADX all improving.

**Automatic scoring:** DPS is computed automatically after each position monitor run (~4 times daily during market hours per the monitor cron `30 9-16/4 * * 1-5`) and the score is stored in the snapshot timeline. You can also trigger analysis on-demand with the "📊 DPS Analysis" button on the position panel.

**Agent integration:** Monitor agents (call/put assessment) receive a supplementary `POSITION HEALTH METRICS` block with latest DPS score, trend direction, and P&L %. This is advisory context only — agents make independent decisions but can flag divergence.

**UI:** A "📊 DPS Analysis" button on each active position panel triggers the analysis. Results show the recommendation, score, risk zone, key drivers, and an expandable score breakdown table with per-factor point contributions.

### DPS Insights (LLM Narrative)

The **DPS Insights** feature provides a one-shot LLM-generated narrative explaining a position's DPS health over time. Accessible via a "🧠 DPS Insights" button next to the "📊 DPS Analysis" button on active position panels, it narrates the deterministic DPS score's trajectory and contributing factors without recomputing or overriding the score.

**How it works:**
1. Reads the position record and its full snapshot history from CosmosDB
2. Sends the historical DPS scores, trends, and key indicators to an LLM (default: `gpt-5.4-mini`)
3. The LLM produces a plain-text narrative interpreting what the DPS data shows — momentum, inflection points, risk drivers
4. **No live data fetch** — only persisted position and snapshot data are used, keeping the analysis grounded in what the system actually recorded

**Key principles:**
- **Narrates, never overrides:** The LLM explains the deterministic score's story; it does not recalculate or second-guess the DPS algorithm
- **Historical context only:** Uses the exact snapshots captured at monitor run time, not current market data
- **One-shot response:** No chat history or follow-up questions — single advisory output per click

**Configuration:** Model configurable via `dps_insights.model` in `config.yaml` (default: `gpt-5.4-mini`).

## Risk Rating (Sell-Side Agents)

Every sell-side agent output (Covered Call and Cash Secured Put) includes a **risk rating** on a 0–10 scale, quantifying how risky the recommended action is.

**Scoring:** 5 dimensions, each scored 0–2 (sum = 0–10):

| Dimension | Score |
|-----------|-------|
| **Volatility risk** | 0=normal/low IV, 1=elevated IV, 2=extreme IV |
| **Assignment risk** | 0=far OTM (delta <0.20), 1=moderate (0.20–0.40), 2=high (>0.40) |
| **Technical risk** | 0=aligned trend, 1=mixed signals, 2=counter-trend |
| **Calendar risk** | 0=no events, 1=ex-div or earnings nearby, 2=earnings within window |
| **Sentiment risk** | 0=bullish, 1=neutral, 2=bearish |

**Output:** The `risk_rating` (integer 0–10) and `risk_rating_breakdown` (object with 5 dimension scores) are included in every SELL activity. Displayed on alert cards and activity detail pages.

## Profit Target Gate (Monitor Agents)

Position monitors include an **automatic profit target gate** at 70% of max profit. When a position reaches ≥70% profit (mark-to-market P&L), the monitor agent is prompted to consider rolling to lock in gains and reset theta decay.

**How it works:**
1. The monitor calculates `(premium_received - current_midprice) / premium_received × 100`
2. If P&L ≥ 70%, the assessment prompt includes a profit-taking note
3. The agent evaluates whether to:
   - **ROLL_OUT** (same strike, extend expiration) — collect new premium while keeping the same risk profile
   - **ROLL + tighten strike** (closer to underlying) — more aggressive premium collection
   - **WAIT** — let it expire worthless for the full 100%

**Design rationale:**
- Theta decay is non-linear — most premium decays in the first 30 days
- Rolling at 70% profit captures most of the gain while freeing capital for the next cycle
- Rolling is NOT mandatory — the agent decides based on DTE, IV, and technical factors

**DPS integration:** The DPS scorer includes P&L as a scored factor (≥80% profit → +10 points, encouraging close consideration). The monitor agent receives both the profit gate prompt AND the DPS score, allowing it to weigh both perspectives.

## Events Calendar

The **Events Calendar** (`/symbols/calendar`) displays a monthly calendar view of earnings and ex-dividend dates for all tracked symbols. It helps you avoid selling options into earnings or time entries around ex-dividend dates.

**Features:**
- Monthly calendar grid with color-coded event badges
- **Earnings dates:** Purple (no exposure) or Orange (active position at risk)
- **Ex-dividend dates:** Red (call position at risk of early assignment) or Yellow (no call position)
- Hover tooltips show symbol details and position exposure
- Data cached in CosmosDB `calendar` container (partition key: `/symbol`)
- Daily sync from Yahoo Finance via scheduled cron job (default: `0 3 * * 1-5` — 3 AM weekdays)
- Manual refresh button for on-demand updates

**Position exposure logic:**
- Earnings with active position (any type) → **Orange** badge
- Ex-dividend with active call position → **Red** badge (early assignment risk when deep ITM)
- No position or non-call position → **Purple** (earnings) or **Yellow** (ex-dividend)

**Data source:** Yahoo Finance via `yfinance` library. Earnings dates from `earnings_dates`, ex-dividend dates from `dividends` history. Cached per-symbol in the `calendar` container.

## Pause Watchlist Until Earnings

To save LLM tokens, a symbol's **following agents** (Covered Call, Cash-Secured Put, Buy Tracker) can be suspended until its next earnings date. Near earnings the earnings gate returns `WAIT` anyway, so running them wastes tokens.

**How it works:**
- On the symbol detail page, **Pause until earnings** stores a `watchlist_pause` object on the `symbol_config` doc: `{ until: <next earnings date>, reason: "earnings", scope: [covered_call, cash_secured_put, buy_tracker], set_at }`. The button is disabled when no upcoming earnings date is stored (sync the calendar first).
- The `watchlist.*` toggle flags are **left unchanged** — the pause is a separate suspension layer that preserves user intent. While paused, the three toggles render shadowed/disabled with a `⏸ Paused until earnings · <date>` badge, and the symbol's rows appear shadowed on the main dashboard.
- Gating is enforced two ways: the watchlist queries (`get_covered_call_symbols` / `get_cash_secured_put_symbols` / `get_buy_tracker_symbols`) exclude symbols whose pause is still active (`watchlist_pause.until >= today`), and each following agent re-checks `is_watchlist_paused()` on the per-symbol manual path. Position monitors (Open Call / Open Put) are **not** affected.
- **Auto-resume:** the pause clears the day after earnings. This happens at query level (`until < today` symbols are no longer excluded) and via a daily **Watchlist Reactivation** scheduled job (default `0 6 * * 1-5`) that deletes expired `watchlist_pause` objects. The **Resume now** button (`DELETE /api/symbols/{symbol}/pause`) clears it manually at any time.



The Supervisor is a separate LLM instance that acts as a quality gate, reviewing primary agent decisions for data errors, blind spots, and unaddressed risks. It runs as **Phase 3a** (in parallel with Alpha Advisor) after the primary decision is written but before Telegram notifications.

**When it runs:**
- **Every alert** (SELL, ROLL, CLOSE) — always triggered
- **Prolonged WAITs** — when a symbol/position has 5+ consecutive WAIT decisions
- **On-demand** — via the "🛡️ Supervisor" button on activity detail pages

**Output schema:**
```json
{
  "challenge_strength": "WEAK | MODERATE | STRONG",
  "issues": [
    "Specific concern #1 with data citation",
    "Specific concern #2 with alternative interpretation"
  ],
  "net_assessment": "ORIGINAL_HOLDS | RECONSIDER",
  "one_liner": "Short summary for Telegram notification"
}
```

**Key behaviors:**
- **WEAK = success:** A WEAK finding means the original analysis is thorough and well-supported — this is the best outcome
- **Never manufactures objections:** Only flags genuine data misreads, ignored risks, logical gaps, or overlooked alternatives
- **Premium yield benchmarks:** Knows that CSP >1.5%/month is GOOD, CC >1%/month is GOOD — never flags good yields as "low"
- **Premium-expiration verification:** Rule #9 verifies that premium values match the correct expiration key in the options chain (cross-expiration mix-ups are a known error pattern)
- **Premium correction awareness:** When the programmatic premium validator corrects agent-reported values (`premium_corrected: true`), the supervisor automatically treats this as at minimum MODERATE — data corrections always warrant scrutiny
- **Non-blocking:** Supervisor failures never affect the primary decision flow

**Programmatic premium validation:** Before the Supervisor and Alpha Advisor run, the agent runner performs a programmatic cross-check (`_validate_premium_against_chain()`) comparing the primary agent's reported premium and delta against the actual options chain data. If mismatches are found, the values are corrected in-place and `premium_corrected: true` is set on the activity. This catches LLM hallucinations (e.g., agent reports premium from a different expiration) before they reach the quality gates.

**Audit playbooks (9 decision types):**

| Decision | Playbook Focus |
|----------|---------------|
| WAIT | Capital efficiency, theta stagnation, opportunity cost, directional risk |
| ROLL_UP | Overbought reversion, buyback cost vs. credit, time decay advantage |
| ROLL_DOWN | Support bounce, minimal premium delta, oversold signals |
| ROLL_UP_AND_OUT | Overbought reversion, extending obligation risk, close-and-reenter |
| ROLL_DOWN_AND_OUT | Support bounce, double penalty (lower strike + longer exposure) |
| ROLL_OUT | Strike viability, theta already captured, event risk |
| CLOSE | Remaining theta, premium recapture, technical reversal (exception: risk management triggers → WEAK) |
| SELL | IV rank reality check, earnings proximity, technical headwinds, premium adequacy with benchmarks |
| NOT_NOW | Support/resistance alignment, elevated IV, opportunity cost accumulation |

**Ex-dividend awareness (CSP SELL only):** For cash-secured put SELL decisions, the Supervisor includes informational entry-timing awareness when an ex-dividend date falls within the trade window (now → expiration). The note surfaces the ex-div date and typical price drop effect (underlying typically drops by approximately the dividend amount on the ex-date, moving modestly toward the short put strike). This is **non-blocking, informational context only** — it does not raise `challenge_strength` or block the decision by itself, as options already price known dividends via put-call parity. Deep-ITM positions (delta < -0.70) with ex-div within ~10 days receive a brief note on rare early-assignment possibility. Call-side agents are unchanged; their ex-div ITM early-assignment handling remains in place.

## Alpha Advisor Agent (Parameter Relaxation)

The Alpha Advisor is a separate LLM instance that identifies the **single blocking parameter** behind a WAIT decision and offers the best possible trade relaxing only that constraint. It does NOT replace the conservative recommendation — it provides a data-driven alternative when a trade was "almost good enough."

**When it runs:** Same triggers as the Supervisor (alerts, prolonged WAITs, on-demand). Runs in parallel with the Supervisor as Phase 3b.

**Philosophy:**
- **Diagnostic first:** Identifies exactly which parameter blocked the trade (premium, IV, delta, technical, DTE)
- **Minimal relaxation:** Only bends ONE constraint — all other rules remain enforced
- **NONE is valid:** If no safe relaxation exists, it says so — not every WAIT has a viable alternative
- **Trade-off transparency:** Every alternative clearly states what is being sacrificed

**Relaxable parameters:**
- `premium_below_category_minimum` — Premium below the category skill threshold
- `iv_below_category_threshold` — IV Rank below category requirement
- `delta_outside_category_range` — Delta outside the category's preferred range
- `technical_borderline` — RSI/momentum slightly outside ideal
- `dte_below_ideal` — DTE acceptable but shorter than preferred

**Hard gates (never relaxed):** Earnings within window, DTE > 45, fundamental quality failures, free-fall conditions (RSI < 25 + negative momentum), delta > 0.50.

**Exclusion of held contracts:** When proposing alternative strikes for open positions, the Alpha Advisor automatically excludes the exact contract currently held (matching strike + expiration) from its recommendations, ensuring alternatives are always genuinely different. For roll scenarios, it surfaces the current position's buyback cost as a reference point when evaluating roll economics.

**Output schema:**
```json
{
  "opportunity_strength": "STRONG | MODERATE | NONE",
  "relaxed_parameter": "premium_below_category_minimum | iv_below_category_threshold | delta_outside_category_range | technical_borderline | dte_below_ideal | none",
  "alternative": {
    "action": "What the relaxed alternative recommends",
    "rationale": "Technical/quantitative evidence supporting this",
    "parameter_detail": "Category min 0.8%, best available 0.65% (19% gap)",
    "trade_off": "Lower premium yield trades X for Y",
    "premium_comparison": "Category target: $X (Y%/mo) vs. Relaxed: $A (B%/mo)",
    "strike": 55.0,
    "expiration": "2026-07-18",
    "premium": 2.10,
    "delta": -0.28,
    "dte": 39
  },
  "one_liner": "Short summary for Telegram notification"
}
```

The `strike`, `expiration`, `premium`, `delta`, and `dte` fields are optional — included when the Alpha Advisor suggests a specific alternative contract (MODERATE/STRONG), omitted for NONE results. Values must come from the actual options chain, never invented.

**Safety constraints:**
- Never suggests delta > 0.50 (stays in premium-selling territory)
- Never violates the 45 DTE maximum rule
- Never suggests entering before earnings if the primary agent rejected for that reason
- Relaxation must still produce a mathematically sound trade (positive expected value)

**Prolonged WAIT detection (shared with Supervisor):**
When a symbol or position has 5+ consecutive WAIT decisions (`PROLONGED_WAIT_THRESHOLD = 5`), both the Supervisor and Alpha Advisor are triggered. The Supervisor checks if continued waiting is losing opportunities; the Alpha Advisor checks if relaxing a single parameter could unlock a viable entry. A cooldown of 3 WAITs (`SUPERVISOR_COOLDOWN = 3`) prevents repeated reviews — after a review, at least 3 more WAITs must occur before triggering again.

**Web dashboard integration:**
- **Activity detail page**: Two collapsible panels — "Supervisor" (🛡️) with color-coded badges (🟢 WEAK, 🟡 MODERATE, 🔴 STRONG) and "Alpha Advisor" (🔍) with opportunity badges (🟢 NONE, 🔵 MODERATE, 🔵 STRONG). Supervisor panel always appears when a `supervisor_view` exists — WEAK panels auto-collapse on page load, MODERATE/STRONG start expanded. Alpha Advisor panels show the relaxed parameter as an orange badge and trade details (strike, expiration, premium, delta, DTE) when alternatives are suggested.
- **Dashboard & symbol detail**: 🤔 indicator icon on WAIT activities that have MODERATE or STRONG supervisor opinions (STRONG gets a pulse animation)

## Position Lifecycle

**Open Position from Alert:**
When a sell-side agent (covered_call, cash_secured_put) generates a SELL alert, the activity detail page displays an "Open Position" button. Clicking it creates a position from the alert data (strike, expiration, type), storing a `source` snapshot of the original alert for full traceability. The watchlist flag is disabled for that symbol, and related activities/alerts are cascade-deleted.

**Roll Position from Alert:**
When a monitor agent (open_call_monitor, open_put_monitor) generates a ROLL alert, the activity detail page shows a "Roll Position" button. Clicking it atomically closes the old position and creates a new one. The old position is marked `status: "closed"` with a `closing_source` snapshot (the alert) and `rolled_to` pointing to the new position ID. The new position carries a `source` snapshot and `rolled_from` pointing to the old position ID, creating an auditable chain.

**Manual Roll:**
Active positions in the Symbol Detail page have a Roll button in the positions table. Clicking it opens an inline form to specify new strike, new expiration, and optional notes. The same `rolled_to`/`rolled_from` chain is created without alert snapshots.

**Position Actions:**
- **Close** — Marks position as closed (status: "closed") with the timestamp. Supports an optional per-share `buyback_cost` field when closing manually (input shown only for manual close reason; omitted for assigned/expired closes).
- **Roll** — Atomically closes current position (status: "rolled") and opens a new one, maintaining traceability chain. Supports optional buyback cost (per-share cost to close the old position) and new premium (per-share premium on the new position).
- **Delete** — Permanently removes the position and cascade-deletes all linked activities/alerts

**Position Financial Fields:**
- **Premium** — Per-share premium received when opening the position. Editable inline on the symbol detail page. Stored in `source.premium` (from alert) or top-level when manually set.
- **Buyback Cost** — Per-share cost to buy back a rolled position. Only shown for positions with `status: "rolled"`. Editable inline.
- **Status values:** `active` (open position), `closed` (expired/closed manually), `rolled` (closed via roll to a new position)

**Position Model Example:**
```json
{
  "position_id": "pos_MO_call_60.0_20250620",
  "type": "call",
  "strike": 60.0,
  "expiration": "2025-06-20",
  "opened_at": "2025-03-15T10:00:00Z",
  "status": "active",
  "buyback_cost": null,
  "notes": "",
  "source": {
    "activity_id": "dec_...",
    "agent_type": "covered_call",
    "timestamp": "2025-03-15T10:00:00Z",
    "premium": 1.25
  },
  "rolled_from": "pos_MO_call_55.0_20250520"
}
```
