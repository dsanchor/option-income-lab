# 🧪 Option Income Lab

> *Where boring dividends get interesting*

## Philosophy

The core strategy is **DGI (Dividend Growth Investing)** — building a portfolio of quality dividend stocks that pay you more every year. It's proven. It's reliable. It's also... boring.

Option Income Lab makes DGI *interesting* by layering options strategies on top:

- 🔍 **DGI Screener** → Find the best dividend growth stocks from the S&P 500
- 💰 **Cash-Secured Puts** → Get paid to wait for stocks you want at prices you choose
- 📈 **Covered Calls** → Squeeze extra income from stocks you already own
- 🤖 **AI-Powered Monitoring** → Agents watch your positions 24/7, suggest rolls, flag risks
- 📊 **Economics Dashboard** → Track P&L, premiums, buyback costs, RoC%, and win rate across all positions
- 📅 **Events Calendar** → Earnings and ex-dividend dates with position exposure warnings
- 🎯 **Profit Target Gate** → Auto-roll positions at 70% profit to lock in gains
- 📡 **Momentum & Signal Filters** → SMA50/200 + RSI + ADX momentum analysis with actionable signal categories (Ideal Puts, Ideal Calls, Accumulate, No Puts, No Calls)
- 🛒 **Buy Tracker** → AI-powered DCA timing agent scoring 5 dimensions (value entry, trend, momentum, income, calendar)

The result: a DGI portfolio that generates income from **dividends AND option premiums** — with an AI copilot keeping watch while you sleep, and a full economics dashboard tracking your P&L.

---

DGI-focused income acceleration platform — uses **Cash Secured Puts (CSP)** to acquire top dividend growth stocks at a discount, and **Covered Calls (CC)** to generate additional income on held DGI positions. A built-in **DGI Screener** identifies the best dividend growth candidates from a configurable stock universe (default: S&P 500), ranking them by a composite quality score combining fundamental strength and technical timing. Options trading analysis is powered by Microsoft Agent Framework (Azure OpenAI or Google Gemini) with Yahoo Finance data fetching via **yfinance** — direct API access for fundamentals, technicals, dividends, analyst data, and full options chains (23+ expirations with computed Greeks). No browser, no scraping, no authentication required. All data — watchlists, positions, activities, reports, alerts, and DGI screener results — is stored in **Azure CosmosDB** (NoSQL) with a symbol-centric partition model.

## Architecture

Eight specialized agents handle options trading and stock screening:
- **Covered Call Agent**: Analyzes stocks for covered call writing opportunities
- **Cash Secured Put Agent**: Analyzes stocks for cash secured put opportunities
- **Open Call Monitor**: Monitors open covered call positions for assignment risk
- **Open Put Monitor**: Monitors open cash-secured put positions for assignment risk
- **Supervisor Agent (Quality Auditor)**: Validates trading decisions for data errors, blind spots, and unaddressed risks — acts as a quality gate ensuring the primary agents' work is accurate
- **Alpha Advisor Agent (Aggressive Perspective)**: Provides alternative, more aggressive viewpoints when technically justified — suggesting higher-premium strikes, shorter DTE, or bolder entries to complement the conservative primary agents
- **Report Agent**: Generates comprehensive per-symbol reports combining technical analysis, dividends, options chain, open position risk, and monitoring recommendations
- **DGI Screener**: Screens a configurable stock universe for top dividend growth investing candidates, ranking by composite quality score (70% fundamental + 30% technical timing) and selecting the Top 20
- **Buy Tracker Agent**: AI-powered DCA timing agent that evaluates 5 dimensions (value entry/pullback, trend, momentum, income/fundamentals, calendar/risk) and produces STRONG_BUY, BUY, or WAIT signals per symbol. Designed for patient accumulation timing — not momentum trading
- **Portfolio Enrichment**: Background process that enriches watchlist symbols with DGI quality scores, technical timing, momentum signals, and category classification. Results power the watchlist UI columns and signal filters

The first two agents (sell-side) decide whether to **open** new positions. The next two (position monitors) decide whether to **hold or adjust** existing positions. The Supervisor and Alpha Advisor run as Phase 3 in parallel — after the primary decision is written but before Telegram notifications, providing quality assurance and aggressive alternatives respectively. The report agent provides on-demand deep-dive analysis accessible from each symbol's detail page. Additionally, **per-symbol chat** is available directly from the symbol detail page, offering context-aware conversations with pre-loaded market data via the yfinance provider.

Both sell-side agents use the Microsoft Agent Framework (`agent-framework`) with Yahoo Finance (yfinance) as the data source. All market data — overview, technicals, forecast, dividends, and full options chains — is fetched via the `yfinance` Python library. No browser, no scraping, no authentication required. Data is pre-fetched deterministically and passed to the LLM for analysis. The LLM never makes HTTP requests directly.

**Storage backend:** Azure CosmosDB with five containers: `symbols` (watchlists, positions, activities, alerts, reports), `telemetry` (runtime performance stats with 30-day TTL), `settings` (application configuration persistence), `dgi_screener` (DGI screening results and daily snapshots), and `calendar` (cached earnings and ex-dividend dates from Yahoo Finance). Each symbol is a partition key in the symbols container containing four document types: `symbol_config` (watchlist flags + positions), `activity` (full audit trail), `alert` (actionable alerts), and `report` (generated symbol reports). The telemetry container tracks data fetch durations and agent run times, displayed on the Settings page. The settings container persists application configuration with partition key `/id`. The dgi_screener container stores current Top 20 entries and daily snapshots for historical tracking, partitioned by `/symbol`. The calendar container stores event data partitioned by `/symbol`. See the [Azure CosmosDB Setup](#azure-cosmosdb-setup) section for provisioning.

## How It Works

End-to-end flow for each scheduled run:

```
Scheduler (main.py)
  │
  ├─ Query CosmosDB for symbols with watchlist.covered_call = true
  │    for each symbol:
  │      1. Load per-symbol context (recent activities + alerts from CosmosDB)
  │      2. Pre-fetch market data via yfinance (overview, technicals, forecast, dividends, options chain)
  │      3. LLM analyzes pre-fetched data → structured JSON activity
  │      4. Write activity to CosmosDB; if SELL → also write alert document
  │      5. Phase 3 (Supervisor + Alpha): If alert or prolonged WAIT → quality audit + aggressive alternative (in parallel)
  │      6. Telegram notification includes supervisor/alpha one-liners (if MODERATE/STRONG)
  │
  ├─ Query CosmosDB for symbols with watchlist.cash_secured_put = true
  │    (same loop with supervisor + alpha phase, different agent instructions)
  │
  ├─ Query CosmosDB for symbols with active call positions
  │    for each position:
  │      1. Load position details from symbol_config
  │      2. Pre-fetch market data via yfinance
  │      3. Phase 1 (Assessment): LLM evaluates assignment risk → WAIT or handoff to Phase 2
  │      4. Phase 2 (Roll Management): Selects specific roll targets from filtered options chain, calculates economics
  │      5. Write activity to CosmosDB; if ROLL/CLOSE → also write alert
  │      6. Phase 3 (Supervisor + Alpha): If alert or prolonged WAIT → quality audit + aggressive alternative (in parallel)
  │      7. Telegram notification includes supervisor/alpha one-liners (if MODERATE/STRONG)
  │
  └─ Query CosmosDB for symbols with active put positions
       (same two-phase pipeline with supervisor + alpha phase, different agent instructions)
```

**Data gathering:** Python pre-fetches ALL market data deterministically via `YFinanceDataProvider` (`src/yfinance_data_provider.py`). Five data types are fetched per symbol — overview, technicals, forecast, dividends, and options chain — all through the `yfinance` Python library. No browser, no scraping, no authentication required. The provider includes built-in rate limiting (2 calls/sec) and a TTL cache (5 min) to avoid redundant fetches. Options chains include 23+ expirations with computed Greeks (delta, gamma, theta, vega) via Black-Scholes (py-vollib). The LLM never makes HTTP requests — it receives pre-fetched data as text and only performs analysis. See [Pre-fetch Architecture](#pre-fetch-architecture-yfinance) below.

**Per-symbol context injection:** Before each symbol is analyzed, the runner reads that symbol's recent activities from CosmosDB and injects them into the prompt. Each activity includes whether it triggered an alert (via the `is_alert` field). The LLM sees only context for the symbol it's currently analyzing — not a mix of all symbols. Context depth is configurable in `config.yaml` (`context.max_activity_entries`, default 2, range 0–5).

**Output:** Every symbol produces an activity (SELL, WAIT, or HOLD) written to CosmosDB as a `activity` document. Only SELL activitys also produce a `alert` document — the actionable alerts that the dashboard and downstream systems watch. Position monitors produce WAIT or ROLL activities, with ROLL/CLOSE activities creating alert documents. If Telegram notifications are enabled, a message is sent for each alert (see [Telegram Notifications](#telegram-notifications-optional)).

## Key Concepts

### Activity vs Alert

**Sell-side agents (Covered Call, Cash Secured Put):**
A **activity** is recorded for EVERY symbol on EVERY run as an `activity` document in CosmosDB. Possible values: `SELL`, `WAIT`, or `HOLD`. The activity collection is the complete audit trail. An **alert** is the subset of activities where the action is `SELL` — stored as a separate `alert` document for efficient querying.

**Position monitors (Open Call Monitor, Open Put Monitor):**
A **activity** is recorded for EVERY position on EVERY run. Possible values: `WAIT`, `ROLL_UP`, `ROLL_DOWN`, `ROLL_OUT`, `ROLL_UP_AND_OUT`, `ROLL_DOWN_AND_OUT`, or `CLOSE`. An **alert** is any activity that is NOT `WAIT` — any roll or close action that requires attention. Positions are stored within the symbol's `symbol_config` document in CosmosDB.

### Open Position Monitors

The Open Call Monitor and Open Put Monitor watch **existing** short options positions for assignment risk. They differ from the sell-side agents in several ways:

| | Sell-Side Agents | Position Monitors |
|---|---|---|
| **Input** | Symbols with watchlist flag enabled in CosmosDB | Symbols with active positions in CosmosDB |
| **Activities** | SELL / WAIT | WAIT / ROLL_UP / ROLL_DOWN / ROLL_OUT / ROLL_UP_AND_OUT / ROLL_DOWN_AND_OUT / CLOSE |
| **Alerts** | SELL only | Any ROLL or CLOSE |
| **Focus** | "Should I open a new position?" | "Is my existing position safe?" |

Positions are managed via the web dashboard or API. Each position is stored within the symbol's `symbol_config` document in CosmosDB with type (call/put), strike, expiration, status, and notes. Position monitors only run for symbols with `status: "active"` positions.

**Two-phase pipeline:** Position monitors use a two-phase architecture. **Phase 1 (Assessment)** evaluates assignment risk and produces a structured handoff JSON if action is needed. **Phase 2 (Roll Management)** receives the handoff plus a filtered options chain (see below) and selects specific roll targets (strike/expiration) with full roll economics (buyback cost, new premium, net credit/debit).

#### Options Chain Filter Pipeline

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

### Position Snapshots & Time Series

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

### Deterministic Position Scorer (DPS)

The DPS provides **on-demand and scheduled rule-based analysis** of open positions without using an LLM. It combines live options chain Greeks with historical snapshot trends to produce a deterministic HOLD/WATCH/ROLL recommendation.

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

**Daily DPS Cron:** A scheduled job (configurable, default `0 22 * * 1-5`) runs DPS for all active positions and stores the score in the snapshot timeline. Enable/disable and change the schedule from the Settings page.

**Agent integration:** Monitor agents (call/put assessment) receive a supplementary `POSITION HEALTH METRICS` block with latest DPS score, trend direction, and P&L %. This is advisory context only — agents make independent decisions but can flag divergence.

**UI:** A "📊 DPS Analysis" button on each active position panel triggers the analysis. Results show the recommendation, score, risk zone, key drivers, and an expandable score breakdown table with per-factor point contributions.

### Risk Rating (Sell-Side Agents)

Every sell-side agent output (Covered Call and Cash Secured Put) includes a **risk rating** on a 0–10 scale, quantifying how risky the recommended action is.

**Scoring:** 5 dimensions, each scored 0–2 (sum = 0–10):
- **Covered Call:** Volatility, Assignment, Technical, Calendar, Sentiment
- **Cash Secured Put:** Fundamental, Technical, Volatility, Calendar, Sentiment

**Interpretation:**
| Score | Level | Guidance |
|-------|-------|----------|
| 0–2 | Low | Strong setup, high conviction |
| 3–4 | Moderate | Acceptable with awareness |
| 5–6 | Elevated | Proceed with caution |
| 7–8 | High | Likely should WAIT |
| 9–10 | Very high | Definitely WAIT |

The rating appears in JSON output (`risk_rating` integer + `risk_rating_breakdown` object) and in the SUMMARY line (`Risk X/10`). Telegram sell alerts also include `Risk: X/10`.

### Profit Target Gate (Monitor Agents)

A **mandatory hard rule** embedded in both position monitor agents (Open Call Monitor and Open Put Monitor) that triggers profit-optimization rolls when favorable conditions are met. This ensures the system proactively locks in gains rather than passively waiting for expiration.

**Trigger conditions (ALL must be true):**
- **P&L ≥ 70%** — the position has captured at least 70% of maximum profit
- **DTE ≥ 10** — at least 10 days remain until expiration (enough time to roll effectively)

**Actions:**
- **Calls** → `ROLL_DOWN` or `ROLL_DOWN_AND_OUT` (tighten strike to collect new premium)
- **Puts** → `ROLL_UP` or `ROLL_UP_AND_OUT` (tighten strike to collect new premium)

The gate fires BEFORE other assessment logic (except earnings proximity). When triggered, the agent sets `close_for_profit_recommended: true` and adds `"profit_optimization"` to risk flags. The roll economics (buyback cost, new premium, net credit) are calculated from the filtered options chain.

### Events Calendar

The **Events Calendar** (`/symbols/calendar`) provides a monthly view of earnings dates and ex-dividend dates for all tracked symbols, with color coding to indicate whether open positions are exposed.

**Color coding:**
| Event Type | Position Active on Date | No Active Position |
|------------|------------------------|--------------------|
| Earnings | 🟣 Purple (warning) | 🟠 Orange (informational) |
| Ex-Dividend | 🔴 Red (warning) | 🟡 Yellow (informational) |

**Active position detection:** A position is considered "active on a date" if its status is `active` AND its expiration date is on or after the event date. This ensures only positions actually exposed to the event are flagged — not just any open position for that symbol.

**Data source:** Earnings and ex-dividend dates are fetched from Yahoo Finance (`yfinance`) and cached in CosmosDB (`calendar` container, partition key `/symbol`). A scheduled sync job runs Mon–Fri at 5am by default (configurable via Settings). A manual **Refresh** button on the calendar page triggers an on-demand sync.

**Navigation:** Accessible from the Symbols dropdown menu → "📅 Calendar" (alongside "📋 Configuration" for symbol management).

### Supervisor Agent (Quality Auditor)

The Supervisor Agent is a separate LLM instance that audits every actionable trading decision for data errors, blind spots, and unaddressed risks. It verifies the primary analyst's work so the human trader can proceed with confidence or revisit genuine issues. Unlike a contrarian, it does NOT argue the opposite position — it validates accuracy and flags only genuine findings.

**When it runs:**

| Trigger | Agent Types | Example |
|---------|------------|---------|
| Alert decisions (SELL, ROLL_*, CLOSE) | All agent types | A SELL alert always triggers a supervisor review |
| Prolonged WAIT (5+ consecutive) | All agent types | Symbol stuck in WAIT for 5+ cycles triggers supervisor (cooldown: 3 WAITs between reviews) |
| Normal WAIT | — | No supervisor (noise reduction) |

**Pipeline position:** The Supervisor runs as Phase 3a — after the primary decision is written to CosmosDB but before the Telegram notification is sent. It runs in parallel with the Alpha Advisor.

**How it works:**
1. A separate ChatAgent instance receives the primary agent's decision, market data, and recent context
2. It uses decision-specific playbooks to audit the decision quality
3. Output is a structured JSON with challenge strength, counter-arguments, net assessment, and a one-liner
4. The `supervisor_view` is stored as a field on the activity document in CosmosDB

**Output schema:**
```json
{
  "challenge_strength": "STRONG | MODERATE | WEAK",
  "counter_arguments": [
    {
      "point": "One-sentence audit finding or confirmation",
      "data_support": "Specific data backing this argument"
    }
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

### Alpha Advisor Agent (Aggressive Perspective)

The Alpha Advisor is a separate LLM instance that provides alternative, more aggressive viewpoints on trading decisions. It complements the conservative primary agents by suggesting higher-premium alternatives **only when technically justified** — it does NOT replace the conservative recommendation.

**When it runs:** Same triggers as the Supervisor (alerts, prolonged WAITs, on-demand). Runs in parallel with the Supervisor as Phase 3b.

**Philosophy:**
- **Not a contrarian:** It agrees with the trade direction but suggests bolder parameters
- **Data-driven only:** Every suggestion must cite specific technical/quantitative evidence
- **NONE is valid:** If the conservative choice is already excellent, it says so — not every trade has a better aggressive version
- **Risk transparency:** Every alternative clearly states the additional risk vs. the conservative choice

**Output schema:**
```json
{
  "opportunity_strength": "STRONG | MODERATE | NONE",
  "alternative": {
    "action": "What the aggressive alternative recommends",
    "rationale": "Technical/quantitative evidence supporting this",
    "additional_risk": "Extra risk vs. conservative choice",
    "premium_comparison": "Conservative: $X (Y%/mo) vs. Aggressive: $A (B%/mo)",
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

**What it suggests (examples):**
- **SELL:** Closer strike with higher delta (0.30 vs. 0.20) for 3x more premium, when support levels justify it
- **ROLL:** Shorter DTE for faster theta decay and capital efficiency
- **WAIT:** Early close + re-entry at a fresher strike for more premium
- **NOT_NOW:** Entry despite neutral technicals when IV rank is high enough to compensate

**Safety constraints:**
- Never suggests delta > 0.50 (stays in premium-selling territory)
- Never violates the 45 DTE maximum rule
- Never suggests entering before earnings if the primary agent rejected for that reason
- Only suggests aggressive alternatives when premium improvement is significant (>50% more) AND technically supported
- Always includes `premium_comparison` so the trader sees the exact trade-off

**Prolonged WAIT detection (shared with Supervisor):**
When a symbol or position has 5+ consecutive WAIT decisions (`PROLONGED_WAIT_THRESHOLD = 5`), both the Supervisor and Alpha Advisor are triggered. The Supervisor checks if continued waiting is losing opportunities; the Alpha Advisor checks if an aggressive entry or adjustment could work. A cooldown of 3 WAITs (`SUPERVISOR_COOLDOWN = 3`) prevents repeated reviews — after a review, at least 3 more WAITs must occur before triggering again.

**Web dashboard integration:**
- **Activity detail page**: Two collapsible panels — "Supervisor" (🛡️) with color-coded badges (🟢 WEAK, 🟡 MODERATE, 🔴 STRONG) and "Alpha Advisor" (🔍) with opportunity badges (🟢 NONE, 🔵 MODERATE, 🔵 STRONG). Supervisor panel always appears when a `supervisor_view` exists — WEAK panels auto-collapse on page load, MODERATE/STRONG start expanded. Alpha Advisor panels show trade details (strike, expiration, premium, delta, DTE) when alternatives are suggested.
- **Dashboard & symbol detail**: 🤔 indicator icon on WAIT activities that have MODERATE or STRONG supervisor opinions (STRONG gets a pulse animation)

### Position Lifecycle

**Open Position from Alert:**
When a sell-side agent (covered_call, cash_secured_put) generates a SELL alert, the activity detail page displays an "Open Position" button. Clicking it creates a position from the alert data (strike, expiration, type), storing a `source` snapshot of the original alert for full traceability. The watchlist flag is disabled for that symbol, and related activities/alerts are cascade-deleted.

**Roll Position from Alert:**
When a monitor agent (open_call_monitor, open_put_monitor) generates a ROLL alert, the activity detail page shows a "Roll Position" button. Clicking it atomically closes the old position and creates a new one. The old position is marked `status: "closed"` with a `closing_source` snapshot (the alert) and `rolled_to` pointing to the new position ID. The new position carries a `source` snapshot and `rolled_from` pointing to the old position ID, creating an auditable chain.

**Manual Roll:**
Active positions in the Symbol Detail page have a Roll button in the positions table. Clicking it opens an inline form to specify new strike, new expiration, and optional notes. The same `rolled_to`/`rolled_from` chain is created without alert snapshots.

**Position Actions:**
- **Close** — Marks position as closed (status: "closed") with the timestamp
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

### Pre-fetch Architecture (yfinance)

LLMs don't reliably make multi-step HTTP tool calls. When given fetching tools directly, they skip steps, fabricate data, and ignore sequencing instructions.

The solution: `YFinanceDataProvider` (`src/yfinance_data_provider.py`) fetches all market data via the `yfinance` Python library — a clean, zero-auth API wrapper over Yahoo Finance. No browser automation, no scraping, no HTML parsing. It gathers five data sets per symbol with built-in rate limiting (2 calls/sec) and a TTL cache (5 min default) to avoid redundant fetches when multiple agents or endpoints request the same symbol data:

| Data | Method | Content |
|------|--------|---------|
| Overview | `yfinance` Ticker.info | Market cap, P/E, EPS, dividend yield, sector, employees, company description |
| Technicals | `yfinance` price history + `pandas-ta` indicators | Oscillators (RSI, MACD, Stochastic), moving averages (EMA/SMA 10-200), summary recommendations |
| Forecast | `yfinance` analyst data | Analyst consensus, price targets (high/median/low), ratings distribution |
| Dividends | `yfinance` dividend history + info | Dividend yield, amount, ex-date, payment frequency, payout ratio, growth history |
| Options chain | `yfinance` options API + `py-vollib` Greeks | 23+ expirations (vs ~5 from old source), strikes, bids, asks, computed Greeks (delta, gamma, theta, vega via Black-Scholes), volume, OI |

The provider returns each data type as a JSON string, ready for injection into LLM prompts. The `fetch_all(symbol)` convenience method returns all 5 types in a single call.

**Key advantages over previous architecture:**
- No browser dependencies (no Playwright, no Chromium)
- No authentication required
- 23+ option expirations with full computed Greeks
- Built-in rate limiting and caching
- Single dependency (`yfinance`) instead of `requests` + `BeautifulSoup` + Playwright

The agent is created with **no tools** — it only analyzes the pre-fetched data included in its prompt. This is the key pattern: move deterministic multi-step workflows to the host language; let the LLM do what it's good at — analysis.

### Data Cache

The yfinance provider includes a built-in TTL cache that sits between consumers (chat, report, analysis endpoints) and Yahoo Finance, eliminating redundant fetches when multiple agents analyze the same symbol in a short time window.

**How it works:**
- Cache keys are per-symbol per-data-type: `(symbol, data_type)` where `data_type` is one of `overview`, `technicals`, `forecast`, `dividends`, or `options_chain`
- Each entry has a configurable TTL (default 5 minutes) — stale entries are evicted automatically
- Rate limiting (2 calls/sec) prevents Yahoo Finance throttling
- The cache is process-local (in-memory) — no external infrastructure required

**Consumers:** The cache is used by the `/chat` endpoint (Portfolio Chat and Quick Analysis), the Report Agent, and the per-symbol analysis runner. Any component that calls `YFinanceDataProvider` benefits from deduplication transparently.

### Options Chain Cache

A separate, centralized **Options Chain Cache** (`src/options_chain_cache.py`) provides the single source of truth for options chain data across the entire application. This addresses gaps in yfinance data (missing strikes) by merging with TradingView.

**Load procedure (on miss or hourly cron refresh):**
1. Fetch from **yfinance** — all expirations with computed Greeks (delta, gamma, theta, vega)
2. Fetch from **TradingView** — overlay: overwrites matching strikes, adds missing ones
3. Store merged result in cache with **30-minute TTL**

**Design rationale:**
- yfinance occasionally misses strikes that TradingView has (observed with VZ $48.5 strike)
- TradingView data fills gaps and corrects stale entries
- No market-open detection needed — cache always contains the best available merge

**Consumers:** All agents (CSP, CC, monitors), DPS analysis, web endpoints, and the options chain trigger endpoint read from this cache. On miss, the load procedure runs automatically before returning data.

### Per-symbol Context Filtering

Each symbol's analysis sees its last N activities (default 2, configurable 0–5). Each activity includes whether it triggered an alert via the `is_alert` field — there is no separate alert configuration. The context provider queries CosmosDB within the symbol's partition, returning only matching entries up to the configured limit. This prevents cross-contamination between symbols and keeps context focused.

Configurable in `config.yaml`:
```yaml
context:
  max_activity_entries: 2   # Recent activities to inject as agent context (0=none, max 5). Each activity includes its alert status.
  activity_ttl_days: 90
```

### CosmosDB Document Model

All data is stored in Azure CosmosDB across four containers:

**`symbols` container** (partition key: `/symbol`) — four document types:

| Document Type | Purpose | Growth |
|---|---|---|
| `symbol_config` | One per symbol — watchlist flags, positions, metadata | Static (updated, not appended) |
| `activity` | One per symbol per agent run — full analysis output | ~20/day per symbol |
| `alert` | One per actionable activity (SELL, ROLL, CLOSE) | ~1-5/week per symbol |
| `report` | On-demand symbol report — technical analysis, dividends, options, risk | ~1-2/week per symbol |

**`telemetry` container** (partition key: `/metric_type`) — runtime performance stats with 30-day TTL:

| Metric Type | Purpose | Fields |
|---|---|---|
| `data_fetch` | Market data fetch timing | resource, duration_seconds, response_size_chars |
| `agent_run` | End-to-end agent execution timing | agent_type, duration_seconds |

**`settings` container** (partition key: `/id`) — application configuration persistence:

| Document ID | Purpose | Persisted Sections |
|---|---|---|
| `app_config` | Application settings synchronized across all components | `context`, `scheduler`, `web`, `telegram` |

**`dgi_screener` container** (partition key: `/symbol`) — DGI screening results:

| Document Type | Purpose | Growth |
|---|---|---|
| `dgi_top` | Current top DGI entries — composite score, category, metrics | Static (replaced each run) |
| `dgi_snapshot` | Daily snapshots for historical tracking of screener results | ~1/day per symbol |

On first run, configuration from `config.yaml` is seeded into the `settings` container (except `ai`, `azure`, `gemini`, and `cosmosdb` sections which remain file-only). On subsequent runs, new keys from `config.yaml` are added to CosmosDB, but existing values are never overwritten, allowing the Settings UI to persist changes. The Settings UI reads and writes directly to CosmosDB, making configuration changes immediately available to all components (scheduler, telegram notifier, web UI) without restart. If CosmosDB is unavailable, `config.yaml` serves as the fallback.

Telemetry stats are displayed on the Settings page and auto-expire after 30 days.

Activities older than 90 days can be configured for TTL-based cleanup. Alerts are kept indefinitely for audit.

## Output

All activities and alerts are stored in Azure CosmosDB. The web dashboard provides a UI for browsing them, or query directly via the CosmosDB Data Explorer.

### Activity Documents (complete audit trail)

Every agent run creates an `activity` document per symbol in CosmosDB. Query by `doc_type = "activity"` and filter by `agent_type` or `symbol`.

### Alert Documents (actionable alerts only)

Actionable activities (SELL, ROLL, CLOSE) also create a `alert` document linked to the activity. Query by `doc_type = "alert"` for the dashboard's primary read path.

### Example Activity Object

Each activity document in CosmosDB:
```json
{
  "timestamp": "2026-03-27T00:00:00Z",
  "symbol": "MO",
  "exchange": "NYSE",
  "agent": "covered_call",
  "activity": "SELL",
  "strike": 60.0,
  "expiration": "2026-04-17",
  "iv": 32.5,
  "reason": "IV Rank elevated with strong technical support; selling 30-delta call",
  "confidence": "high",
  "risk_flags": [],
  "risk_rating": 3,
  "risk_rating_breakdown": {
    "volatility": 1,
    "assignment": 0,
    "technical": 1,
    "calendar": 0,
    "sentiment": 1
  }
}
```

For `SELL` activities, `strike`, `expiration`, premium, `risk_rating`, and `risk_rating_breakdown` fields are populated. A corresponding `alert` document is also created with the actionable subset of the activity data.

### Telegram Notifications

When a `SELL`, `ROLL`, or `CLOSE` alert is generated, a Telegram notification is sent if enabled (see [Configuration](#configuration)). The message includes the symbol, action, and key details (strike, expiration, risk flags). Sell alerts include the risk rating (`Risk: X/10`) and premium. Roll alerts include roll economics (buyback cost, new premium, net credit/debit) and assignment risk level. Close alerts show the buyback cost for the position exit. When a supervisor review produces a **MODERATE** or **STRONG** challenge, the supervisor one-liner is appended to the alert. When the Alpha Advisor finds a **MODERATE** or **STRONG** opportunity, its one-liner is also included. **WEAK/NONE** results are omitted from Telegram to reduce noise — they remain accessible in the web dashboard.

## Dual-Mode Chat Experience

The `/chat` page now offers two distinct modes for analysis:

### Portfolio Chat

Analyze tracked symbols using your CosmosDB watchlist and position data. The chat context includes:
- Recent activities and alerts for the selected symbol
- Open positions (strike, expiration, status)
- Historical decisions and risk flags

Perfect for in-depth analysis of symbols you're actively tracking.

**How to use:**
1. Visit `/chat`
2. Click "Portfolio Chat"
3. Select a tracked symbol or ask general questions about your portfolio
4. Get insights based on your historical data and positions

### Quick Analysis

Analyze any symbol (tracked or not) using live Yahoo Finance data, without saving to your database. Quick Analysis fetches:
- Real-time overview (market cap, P/E, dividend yield, etc.)
- Technical indicators (RSI, MACD, moving averages, etc.)
- Analyst forecasts (price targets, ratings)
- Options chain (if available)
- Dividend history

Perfect for researching new symbols before committing to tracking.

**How to use:**
1. Visit `/chat`
2. Click "Quick Analysis"
3. Enter a symbol and select its market (NASDAQ, NYSE, AMEX, OTC)
4. Click "Fetch & Analyze"
5. Chat about the symbol with live data context
6. Use "Change Mode" to switch back to Portfolio Chat or select a different symbol

**Configuration:** Quick Analysis is read-only — data is fetched but never saved to CosmosDB. Rate limiting is handled gracefully with clear error messages.

## Summarization Agent

An optional daily summary agent that sends a Telegram notification with a digest of your portfolio activities. Useful for staying informed without checking the dashboard daily.

### Features

- **Daily Summaries** — Automatically runs on a configurable schedule (default: 8 AM, America/New_York timezone)
- **Per-Symbol Activity Digest** — Summarizes the N most recent activities for each tracked symbol (configurable, default: 3)
- **Configurable Schedule** — Set the cron expression to match your timezone and preferences
- **Enable/Disable Toggle** — Turn on/off without restarting the application
- **Telegram Integration** — Requires Telegram notifications to be enabled; summaries are sent via Telegram

### Configuration

Configure the Summarization Agent in the **Settings** page (`/settings`):

1. **Enable/Disable** — Toggle the agent on/off
2. **Cron Expression** — Set the schedule (e.g., `0 8 * * *` for 8 AM daily)
3. **Activity Count** — Number of recent activities per symbol to include in the summary (1–5)
4. **Timezone** — Uses the global scheduler timezone from `config.yaml` (default: `America/New_York`)

Or configure in `config.yaml`:
```yaml
summary_agent:
  enabled: true
  cron: "0 8 * * *"        # 8 AM daily (America/New_York timezone)
  activity_count: 3         # Latest N activities per symbol
```

### How It Works

1. The summarization agent runs on the configured schedule
2. It queries CosmosDB for all tracked symbols with recent activities
3. For each symbol, it retrieves the N most recent activities and any related alerts
4. The configured LLM provider (Azure OpenAI or Google Gemini) generates a concise summary of recent decisions and trends
5. A Telegram message is sent with the summary (if Telegram is enabled)
6. The message includes per-symbol activity digests and portfolio-wide insights

### Requirements

- **Telegram Notifications** must be enabled (see `/settings`)
- **LLM credentials** configured for your chosen provider (`azure` or `gemini` — see [AI provider](#ai-provider-azure-or-gemini))
- Valid **CosmosDB** connection

If Telegram is disabled, the summary is still generated but not sent.

## Symbol Report

The "Generate Report" button on each symbol's detail page triggers a comprehensive, on-demand analysis report via the **Report Agent** (`src/report_agent.py`).

### What's Included

Each report covers:
- **Technical Analysis** — Current trend direction, price range, and key technical indicators
- **Earnings & Ex-Dividend** — Upcoming dates and their impact on options timing
- **Dividend Summary & Growth** — Yield, payment history, and growth trajectory
- **Options Chain** — Available calls then puts with strikes, premiums, and greeks
- **Open Position Risk Analysis** — Risk assessment for any active positions, including recent activity history
- **Monitoring Agent Recommendations** — Suggested actions based on current market conditions

### How It Works

1. User clicks "Generate Report" on the symbol detail page
2. The Report Agent uses the same `AgentRunner → Agent → OpenAIChatCompletionClient` pattern as other agents
3. Market data is loaded via the [Data Cache](#data-cache) for fast context assembly
4. The LLM generates a structured report from the system prompt (`src/tv_report_instructions.py`)
5. The report is stored in CosmosDB as a `doc_type="report"` document and displayed on a dedicated page (`/symbols/{symbol}/report`)

## DGI Screener

The DGI Screener identifies top dividend growth investing candidates from a configurable stock universe (default: S&P 500). It ranks stocks by a composite quality score combining fundamental strength (70%) and technical timing (30%), selecting the Top 20 for investment consideration via CSP or direct purchase.

### Categories

Each screened stock is classified into one of five categories:

| Category | Criteria | Badge Color |
|---|---|---|
| **Aristocrat** | 25+ years of consecutive dividend growth, 2%+ yield | 🟣 Purple |
| **Rising Star** | 15%+ dividend growth CAGR | 🟢 Green |
| **Compounder** | 10%+ dividend growth CAGR | 🔵 Blue |
| **High Yield** | 4%+ current dividend yield | 🟠 Orange |
| **Balanced** | Meets minimum filters but doesn't fit above categories | ⚪ Gray |

### Quality Score

The composite quality score is a weighted blend of fundamental and technical factors:

| Factor | Weight | Description |
|---|---|---|
| `dividend_yield` | 15% | Current annual dividend yield |
| `dividend_growth` | 18% | Dividend growth CAGR over available history |
| `payout_safety` | 10% | Payout ratio health (lower is safer) |
| `valuation` | 10% | P/E ratio attractiveness vs. sector |
| `financial_health` | 7% | Debt/equity ratio and balance sheet strength |
| `consistency` | 10% | Years of consecutive dividend growth |
| `technical_timing` | 30% | Technical indicator composite (RSI, moving averages, proximity to 52-week low) |

### Minimum Filters

Stocks must pass all filters before scoring:

| Filter | Default | Description |
|---|---|---|
| `min_yield` | 1.5% | Minimum dividend yield |
| `max_payout` | 75% | Maximum payout ratio |
| `max_pe` | 30 | Maximum P/E ratio |
| `max_de` | 2.0 | Maximum debt/equity ratio |
| `min_years` | 3 | Minimum consecutive years of dividend growth |
| `min_market_cap` | $10B | Minimum market capitalization |
| `min_growth` | 0% | Minimum dividend growth rate |

### Data Source

The DGI Screener uses **yfinance** as its primary data source — the same provider used by the trading agents. Stock fundamentals, dividend history, and technical indicators are sourced from Yahoo Finance via the `yfinance` Python package. Additionally, **stockanalysis.com** is scraped as a supplementary data source via `requests` + `BeautifulSoup` (`stockanalysis_fetcher.py`). The primary value-add is the authoritative **Growth Years** (consecutive years of dividend increases), which is always preferred over Yahoo's calculated value. Other dividend metrics (yield, payout ratio, dividend growth CAGR) are used as fallback when Yahoo Finance returns zero or missing data. An in-memory cache avoids redundant requests within the same screener run.

### Storage

DGI Screener results are stored in the CosmosDB `dgi_screener` container (partition key: `/symbol`) with two document types:

- **`dgi_top`** — Current top entries. Replaced on each screener run with the latest rankings, scores, categories, and metrics.
- **`dgi_snapshot`** — Daily snapshots preserving historical screener results for trend tracking (e.g., how long a stock has been in the top list).

### Scheduling

The DGI Screener runs on a configurable cron schedule (default: `0 6 * * 1-5` — 6 AM weekdays). It can be enabled or disabled via the Settings page toggle.

### Symbols Configuration

The stock universe is configured in `config.yaml` under `dgi_screener.symbols` as a comma-separated list. This can also be edited via the Settings page textarea.

```yaml
dgi_screener:
  enabled: true
  cron: "0 6 * * 1-5"
  top_n: 20
  symbols: "AAPL,MSFT,JNJ,PG,KO,PEP,ABBV,MCD,T,VZ,O,SCHD,..."
  filters:
    min_yield: 1.5
    max_payout: 75
    max_pe: 30
    max_de: 2.0
    min_years: 3
    min_market_cap: 10000000000
    min_growth: 0
  score_weights:
    dividend_yield: 0.15
    dividend_growth: 0.18
    payout_safety: 0.10
    valuation: 0.10
    financial_health: 0.07
    consistency: 0.10
    technical_timing: 0.30
  technical_indicators:
    rsi_period: 14
    sma_periods: [50, 200]
    week52_proximity_weight: 0.4
```

### Web UI

The DGI Screener has a dedicated page at `/dgi`, accessible from the navigation bar. The page displays a Top 20 table with the following columns:

| Column | Description |
|---|---|
| Rank | Position in the Top 20 |
| Symbol | Stock ticker |
| Category | Color-coded badge (Aristocrat, Rising Star, Compounder, High Yield, Balanced) |
| Score | Composite quality score (0-100) |
| Yield | Current dividend yield |
| Growth CAGR | Dividend growth compound annual growth rate |
| Years | Consecutive years of dividend growth |
| Days on List | Number of consecutive days the stock has appeared in the Top 20 |
| Timing | Technical timing score (0-100) |
| Entry | Entry tag based on timing (Strong Buy, Buy, Accumulate, Hold, Wait) |
| Price | Current stock price |

Each row has per-symbol actions:
- **Quick Analysis (▶)** — Triggers the CSP agent for immediate analysis of the stock
- **Add to Symbols (➕)** — Adds the stock to the watchlist with CSP enabled

### How It Works

The DGI Screener runs an 11-step pipeline:

1. **Load symbols** from `config.yaml` (or Settings override)
2. **Fetch yfinance data** — fundamentals, dividend history, technicals for each symbol
3. **Supplement with stockanalysis.com** — scrape authoritative Growth Years + fallback dividend metrics
4. **Calculate fundamental metrics** — yield, growth CAGR, payout ratio, P/E, D/E, years of growth
5. **Calculate technical metrics** — RSI, SMA crossovers, 52-week low proximity
6. **Apply minimum filters** — exclude stocks that fail any filter threshold
7. **Calculate quality scores** — weighted composite of all factors
8. **Select Top N** — rank by score, keep top N (configurable)
9. **Categorize** — assign category based on metrics (Aristocrat, Rising Star, etc.)
10. **Update days_on_list** — persist consecutive appearance count across runs
11. **Write to CosmosDB** — upsert `dgi_top` documents + append `dgi_snapshot` for the day

## Momentum Analysis

Each watchlist symbol gets a **Momentum** signal computed by the Portfolio Enrichment process. The signal combines trend direction (SMA50/SMA200) with trend strength (ADX) and exhaustion (RSI):

### Signals

| Signal | Condition | Options Implication |
|--------|-----------|---------------------|
| **Bullish** | SMA50 > SMA200, price > SMA50, ADX ≥ 20 | ✅ Sell Puts / ⚠️ Avoid Calls |
| **Bullish (overextended)** | Bullish + RSI > 70 | ⚠️ Possible reversal — cautious on puts |
| **Weakening** | SMA50 > SMA200 but price ≤ SMA50 | ✅ Sell Calls / ⚠️ Caution on puts |
| **Neutral** | ADX < 20 (no real trend) or mixed signals | Range-bound — premium decay favors sellers |
| **Bearish** | SMA50 < SMA200, price < SMA50, ADX ≥ 20 | ✅ Sell Calls / ❌ Avoid Puts |
| **Bearish (oversold)** | Bearish + RSI < 30 | Possible bounce — timing for puts |

### Technical Indicators Used

- **SMA50 / SMA200** — Moving average crossover for trend direction
- **ADX (14-period, Wilder's smoothing)** — Trend strength filter. ADX < 20 forces Neutral regardless of SMAs
- **RSI (14-period)** — Exhaustion modifier. RSI > 70 flags overextension, RSI < 30 flags oversold

### Signal Filters (Watchlist UI)

The watchlist provides predefined filter pills combining Entry (technical timing) + Momentum for actionable categories. RSI extremes (oversold/overextended) act as independent signals:

| Filter | Logic | Action |
|--------|-------|--------|
| **Ideal Puts** | (SB/Buy + Bullish/Neutral/Weakening) OR (any + Oversold) | Sell puts with confidence |
| **Ideal Calls** | (Hold/Wait + Weakening/Bearish/Neutral) OR (any + Overextended) | Sell covered calls |
| **Accumulate** | Accumulate + Bullish/Neutral | Small DCA add |
| **No Puts** | SB/Buy + Bearish (pure) | Falling knife — don't sell puts |
| **No Calls** | Wait + Bullish (pure) | Runaway — don't sell calls |

**RSI extreme handling:** Oversold (RSI < 30) signals probable bounce → routed to Ideal Puts regardless of Entry. Overextended (RSI > 70) signals probable pullback → routed to Ideal Calls regardless of Entry. No Puts and No Calls only match pure momentum (without RSI modifiers).

## Buy Tracker

The Buy Tracker is an AI-powered DCA timing agent that helps determine optimal accumulation timing for DGI stocks. Unlike the Entry tag (pure technical timing score), the Buy Tracker evaluates **5 dimensions** holistically:

### Scoring Dimensions (0 or 1 each)

| Dimension | Scores 1 if... |
|-----------|----------------|
| **Value Entry / Pullback** | Price pulled back ≥5% from high, near SMA50, RSI < 45, or yield above typical range |
| **Trend Not Broken** | Price > SMA200, or golden cross structure, or testing major support |
| **Momentum Not Extreme** | RSI 20–65, or oversold (< 30), or oscillators neutral/sell |
| **Income & Fundamentals** | Yield ≥ 2%, payout < 75%, analyst consensus not bearish, no imminent earnings |
| **Calendar & Risk Context** | No earnings within 7 days, ex-div approaching, beta ≤ 1.5, orderly price action |

### Activity Determination

| Score | Signal | Meaning |
|-------|--------|---------|
| 5/5 | `STRONG_BUY` | All dimensions confirm — high-conviction larger entry |
| 4/5 | `STRONG_BUY` | Near-perfect — strong entry |
| 3/5 | `BUY` | Good DCA setup — small add |
| 2/5 | `WAIT` | Mixed signals — wait |
| 1/5 | `WAIT` | Weak setup |
| 0/5 | `WAIT` | Bearish — stay away |

### WAIT Triggers (Override)

Any ONE of these forces WAIT regardless of score:
- Earnings within 2 days
- RSI > 80 (severely overbought)

### Entry Tag vs Buy Tracker

| Aspect | Entry Tag | Buy Tracker |
|--------|-----------|-------------|
| Method | Deterministic (tech timing score thresholds) | AI (LLM interprets 5 dimensions) |
| Inputs | RSI, SMA, pivot supports, volume | + dividend yield, earnings calendar, analyst consensus, payout ratio, Fear & Greed |
| Output | Strong Buy / Buy / Accumulate / Hold / Wait | STRONG_BUY / BUY / WAIT |
| When they diverge | Normal — Entry may say "Strong Buy" while Buy Tracker says "WAIT" (e.g., earnings tomorrow) |

## Project Structure

```
stock-options-manager/
├── config.yaml                           # Configuration (AI provider, CosmosDB, scheduling, context limits)
├── src/
│   ├── __init__.py
│   ├── main.py                           # Entry point — scheduler with immediate + periodic runs
│   ├── config.py                         # YAML config loader with env var substitution and validation
│   ├── llm.py                            # LLM provider factory (Azure OpenAI / Google Gemini)
│   ├── cosmos_db.py                      # CosmosDB service layer — all database operations
│   ├── context.py                        # Context injection adapter — formats CosmosDB data for prompts
│   ├── agent_runner.py                   # Core execution engine — yfinance pre-fetch + SkillsProvider integration
│   ├── yfinance_data_provider.py         # Yahoo Finance data provider (overview, technicals, forecast, dividends, options chain)
│   ├── options_chain_cache.py            # Centralized options chain cache (yfinance + TradingView merge, 30-min TTL)
│   ├── options_chain_filters.py          # Options chain filter pipeline + roll candidates table
│   ├── covered_call_agent.py             # Covered call wrapper
│   ├── covered_call_instructions.py      # Covered call system prompt
│   ├── cash_secured_put_agent.py         # Cash secured put wrapper
│   ├── cash_secured_put_instructions.py  # Cash secured put system prompt
│   ├── open_call_monitor_agent.py        # Open call position monitor wrapper
│   ├── open_call_assessment_instructions.py  # Open call Phase 1 (assessment)
│   ├── open_call_roll_instructions.py        # Open call Phase 2 (roll management)
│   ├── open_call_chat_instructions.py        # Chat instructions for open call
│   ├── open_put_monitor_agent.py         # Open put position monitor wrapper
│   ├── open_put_assessment_instructions.py   # Open put Phase 1 (assessment)
│   ├── open_put_roll_instructions.py         # Open put Phase 2 (roll management)
│   ├── open_put_chat_instructions.py         # Chat instructions for open put
│   ├── buy_tracker_agent.py              # Buy tracker wrapper
│   ├── buy_tracker_instructions.py       # Buy tracker system prompt
│   ├── supervisor_instructions.py        # Supervisor agent (quality auditor)
│   ├── alpha_instructions.py             # Alpha Advisor agent (aggressive perspective)
│   ├── report_instructions.py            # Report agent system prompt
│   ├── summary_instructions.py           # Summary agent system prompt
│   ├── technical_analysis_instructions.py # Technical analysis system prompt
│   ├── banner_instructions.py            # Banner instructions
│   ├── skills/                           # Native agent-framework Skills (SKILL.md format)
│   │   ├── earnings-gate-monitor/SKILL.md   # Earnings gate for open position monitors
│   │   ├── earnings-gate-sell/SKILL.md      # Earnings gate for sell-side watchers
│   │   ├── roll-economics/SKILL.md          # Premium-First Roll Policy (3-tier hierarchy)
│   │   ├── data-source/SKILL.md             # Yahoo Finance data format guide
│   │   ├── risk-flags/SKILL.md              # Risk flag taxonomy
│   │   └── activity-log/SKILL.md            # Previous activity log interpretation
│   ├── dgi_screener.py                   # DGI Screener pipeline
│   ├── dgi_metrics.py                    # DGI metric calculations (quality score, RSI, ADX, technical timing)
│   ├── portfolio_enrichment.py           # Watchlist enrichment (DGI scores, momentum, technicals → CosmosDB)
│   ├── yfinance_fetcher.py               # Yahoo Finance data fetcher for DGI Screener
│   ├── stockanalysis_fetcher.py          # StockAnalysis.com scraper
│   └── telegram_notifier.py             # Telegram notification service
├── scripts/
│   └── provision_cosmosdb.sh             # Azure CosmosDB provisioning via az CLI
├── web/
│   ├── __init__.py
│   ├── app.py                            # FastAPI web dashboard — all routes + CosmosDB queries
│   ├── templates/                        # Jinja2 HTML templates (Revolut-inspired dark theme)
│   │   ├── base.html                     # Base layout with nav
│   │   ├── dashboard.html                # Main dashboard — alert overview + activity feed
│   │   ├── alerts.html                   # Alert list for agent+symbol
│   │   ├── alert_detail.html             # Single alert + backing activities
│   │   ├── settings.html                 # Settings (cron expression, error stats)
│   │   ├── settings_config.html           # Settings config tab (scheduler toggles, Run Now buttons)
│   │   ├── symbols.html                   # Symbols watchlist (signal filters, momentum, put exposure)
│   │   ├── symbol_detail.html            # Symbol detail with positions, activities, notes
│   │   ├── symbol_report.html            # Per-symbol report display page
│   │   ├── symbol_chat.html              # Per-symbol chat page with context selection
│   │   ├── fetch_preview.html            # Raw data debug/preview page
│   │   ├── dgi_screener.html             # DGI Screener Top 20 page
│   │   ├── economics.html                # Economics P&L analytics dashboard
│   │   ├── calendar.html                 # Events Calendar (earnings & ex-dividend dates)
│   │   └── chat.html                     # Chat interface (dual-mode)
│   └── static/
│       ├── style.css                     # Revolut-inspired dark trading theme CSS
│       └── app.js                        # Client-side JS
├── run_web.py                            # Web dashboard entry point
├── requirements.txt
├── DESIGN.md                             # UI/UX design reference
└── README.md
```

## Web Dashboard

- **Dashboard** (`/`) — Alerts overview by agent type with rolling time-range counts (today, last 7 days, last 30 days), scheduler status, recent activity feed with alert indicators and clickable links, position summary. Activities can be filtered by **confidence level** (high/medium/low) and **agent type** for granular views. WAIT activities with MODERATE or STRONG supervisor opinions display a 🤔 indicator icon (STRONG gets a pulse animation).
- **Alert Details** (`/alerts/{agent}/{symbol}`) — All alerts for a specific symbol, newest first, with activity badges and risk flags.
- **Alert + Activities** (`/alerts/{agent}/{symbol}/{index}`) — Full alert JSON and backing activities from the same time window.
- **Symbol Detail** (`/symbols/{symbol}`) — Full detail page for a symbol: expandable positions with source traceability, editable notes field, Close/Roll/Delete actions, activities, alerts, and "Open Position from Alert" / "Roll Position from Alert" buttons on activity detail. Features a **play button** (▶) for running individual symbol analysis on demand. **Generate Report** and **Chat** buttons are aligned right; watchlist toggles are aligned left. Activities support confidence and agent-type filtering. WAIT activities with MODERATE or STRONG supervisor opinions display a 🤔 indicator icon. Activity detail includes collapsible "Supervisor" and "Alpha Advisor" panels with color-coded badges showing audit findings and aggressive alternatives.
- **Symbols Watchlist** (`/symbols`) — Central watchlist management page showing all tracked symbols with enrichment data. Columns: Symbol, Category, DGI Score, Tech Timing, Entry (technical timing tag), Momentum (directional signal), Price, Shares (inline editable), In Calls, Put Exposure ($committed if assigned). Features **signal filter pills** for actionable categories:
  - **All** — Show everything
  - **Ideal Puts** — (SB/Buy + Bullish/Neutral/Weakening) or (any + Oversold)
  - **Ideal Calls** — (Hold/Wait + Weakening/Bearish/Neutral) or (any + Overextended)
  - **Accumulate** — Accumulate + Bullish/Neutral (small DCA)
  - **⚠️ No Puts** — SB/Buy + Bearish pure (falling knife)
  - **⚠️ No Calls** — Wait + Bullish pure (runaway)
- **Symbol Report** (`/symbols/{symbol}/report`) — Dedicated report display page showing the latest generated report for a symbol (technical analysis, dividends, options chain, risk assessment, and recommendations).
- **Symbol Chat** (`/symbols/{symbol}/chat`) — Per-symbol chat page with a context selection screen before starting the conversation. Pre-loads market data via the yfinance provider for faster responses. Supports open call and open put analysis contexts.
- **Fetch Preview** (`/symbols/{symbol}/fetch-preview`) — Debug page showing raw market data for each resource (overview, technicals, forecast, options chain) with fetch timing and size.
- **Chat** (`/chat`) — Dual-mode chat experience powered by your configured LLM provider (Azure or Gemini):
  - **Portfolio Chat** — Analyze tracked symbols using CosmosDB data (watchlists, positions, recent activities). Click "Portfolio Chat" to ask questions about your tracked symbols.
  - **Quick Analysis** — Analyze any symbol (tracked or not) by fetching live Yahoo Finance data without saving to the database. Click "Quick Analysis", select a market (NASDAQ/NYSE/AMEX/OTC), and get instant analysis without committing to tracking.
  - Mode selector on the chat page lets you switch between modes at any time.
- **Settings** (`/settings`) — Scheduler config, Telegram notifications toggle & test button, Summarization Agent config (cron schedule & activity count), runtime stats (today/7d/30d telemetry), a Debug Data Fetch tool for testing data fetching per symbol, and an **Agent Chain Pipeline** debug view (`/api/debug/agent-chain/{symbol}`) for inspecting the full two-phase monitor pipeline per symbol. Each scheduled agent (Monitoring, Calendar Sync, Options Chain, DGI Screener, Summary, Portfolio Enrichment) has a **Run Now** button for manual triggering. Settings are persisted to CosmosDB and survive application restarts and deployments. Changes made in the Settings UI are immediately available to all components (scheduler, telegram notifier, summarization agent, etc.) without requiring a restart.
- **DGI Screener** (`/dgi`) — Top 20 dividend growth stock candidates ranked by composite quality score. Color-coded category badges, per-row Quick Analysis (▶) and Add to Symbols (➕) actions. Configurable stock universe and filter thresholds via Settings.
- **Economics** (`/economics`) — P&L analytics dashboard for options trading performance. Features:
  - **Summary cards** — Total premium collected, total buyback costs, net income, weighted average RoC% (annualized), and win rate
  - **Filters** — Year, months (multi-select dropdown with checkboxes), symbols (multi-select dropdown with checkboxes), type (call/put), status (closed/rolled). Defaults to current year on load.
  - **Monthly breakdown table** — Net income, premium, buyback, RoC%, and win rate per month
  - **By-symbol breakdown table** — Same metrics grouped by underlying symbol
  - **Charts** — Monthly Net Income stacked bar chart (calls vs puts breakdown) and Calls vs Puts donut chart
  - **Positions detail table** — All matching positions with per-share and dollar amounts, individual RoC%, days held, and status
  - **Weighted RoC%** — Calculated as `total_net_income / sum(strike × 100)` to avoid misleading simple averages. Annualized using average days to expiration.
  - **Contract multiplier** — Premium and buyback are stored per-share; Economics displays total dollar amounts (×100 for standard options contracts)
- **Events Calendar** (`/symbols/calendar`) — Monthly calendar view showing earnings and ex-dividend dates for all tracked symbols. Color-coded by event type and position exposure (purple/orange for earnings, red/yellow for ex-dividend). Data cached in CosmosDB with configurable daily sync from Yahoo Finance. Includes a manual Refresh button for on-demand updates.

---

## Running Locally

### Prerequisites

1. **Python 3.12+** (matches the Docker image)
2. **LLM provider** — choose one:
   - **Azure** (default): Azure AI Foundry project + API key (e.g. `gpt-5.1`, `gpt-5.4-mini`)
   - **Gemini**: [Google AI API key](https://aistudio.google.com/apikey) (e.g. `gemini-2.0-flash`, `gemini-2.5-pro`)
3. **Azure CosmosDB Account** — See [Azure CosmosDB Setup](#azure-cosmosdb-setup) below

### Setup

#### 1. Create Virtual Environment and Install Dependencies

```bash
python -m venv venv
source venv/bin/activate 
pip install -r requirements.txt
```

This installs:
- `agent-framework-core` + `agent-framework-openai` - Microsoft Agent Framework core SDK (Azure OpenAI and OpenAI-compatible APIs such as Gemini)
- `yfinance` - Yahoo Finance data provider (overview, technicals, forecast, dividends, options chains)
- `py-vollib` - Black-Scholes Greeks computation for options chain data
- `pandas-ta` - Technical analysis indicators (RSI, MACD, moving averages, etc.)
- `requests` - HTTP client for stockanalysis.com dividend scraping
- `beautifulsoup4` - HTML parsing for stockanalysis.com dividend data
- `numpy`, `pandas` - Numerical computation and data manipulation for DGI scoring pipeline
- `pyyaml`, `croniter`, `python-dotenv` - Configuration and scheduling

#### 2. Configure Environment Variables

Create a `.env` file in the project root (loaded automatically on startup) or export variables in your shell.

**CosmosDB** (required for all setups):

```bash
export COSMOSDB_ENDPOINT="https://your-account.documents.azure.com:443/"
export COSMOSDB_KEY="your-primary-key"
```

**Azure** (when `AI_PROVIDER` is unset or `azure`):

```bash
export AI_PROVIDER=azure
export AZURE_AI_PROJECT_ENDPOINT="https://your-project.services.ai.azure.com"
export MODEL_DEPLOYMENT="gpt-5.1"          # default model for all agent roles
export AZURE_OPENAI_API_KEY="your-api-key-here"
```

**Gemini** (when `AI_PROVIDER=gemini`):

```bash
export AI_PROVIDER=gemini
export GOOGLE_API_KEY="your-google-api-key"
export MODEL_DEPLOYMENT="gemini-2.0-flash"  # default model for all agent roles
```

Market data needs no API key — yfinance fetches overview, technicals, forecast, dividends, and options chains from Yahoo Finance.

#### 3. (Optional) Set Up Telegram Notifications

Receive alerts directly on Telegram. Skip this section if you don't need notifications.

**Create a Telegram bot:**
1. Open Telegram and message [@BotFather](https://t.me/BotFather)
2. Send `/newbot` and follow the prompts (choose a name, then a username)
3. Copy the bot token (format: `123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11`)

**Get your chat ID:**
1. Add the bot to a group or start a direct message with it
2. Send any message to the bot
3. Visit `https://api.telegram.org/bot<TOKEN>/getUpdates` (replace `<TOKEN>` with your bot token)
4. Look for `chat.id` in the JSON response — copy the ID (group IDs are negative)

**Set environment variables:**
```bash
export TELEGRAM_BOT_TOKEN="123456:ABC-DEF..."
export TELEGRAM_CHAT_ID="-1001234567890"  # Use negative for groups
```

**Enable in config.yaml** (see step 5) or toggle on the Settings page. Use the **Test** button to verify connectivity.

#### 4. Set Up Azure CosmosDB

See the [Azure CosmosDB Setup](#azure-cosmosdb-setup) section below for provisioning instructions.

#### 5. Configure Symbols

Symbols and positions are managed via the **web dashboard** or the CosmosDB API. Each symbol has:
- **Watchlist flags**: `covered_call` and `cash_secured_put` (true/false)
- **Positions**: Open call/put positions with strike, expiration, and status

The exchange prefix is stored for reference (e.g., `NYSE` + `MO`).

#### 6. Adjust Configuration (Optional)

Edit `config.yaml` to customize. Model names and per-role overrides live under `ai` and apply to **both** Azure and Gemini.

#### AI provider (Azure or Gemini)

| Setting | Purpose |
|---|---|
| `ai.provider` | `azure` or `gemini` (from `${AI_PROVIDER}`; empty = `azure`) |
| `ai.model_deployment` | Default model for all agents (from `${MODEL_DEPLOYMENT}`) |
| `ai.models` | Optional per-role overrides (`chat`, `symbol_chat`, `supervisor`, `monitor_assessment`, etc.) — each falls back to `model_deployment` |

Provider-specific credentials only:

| Provider | Config section | Env vars |
|---|---|---|
| Azure | `azure` | `AZURE_AI_PROJECT_ENDPOINT`, `AZURE_OPENAI_API_KEY` |
| Gemini | `gemini` | `GOOGLE_API_KEY` |

Example `config.yaml` (Gemini):

```yaml
ai:
  provider: "${AI_PROVIDER}"       # azure | gemini
  model_deployment: "${MODEL_DEPLOYMENT}"
  models:
    chat: "gemini-2.0-flash"
    symbol_chat: "gemini-2.0-flash"
    supervisor: "gemini-2.0-flash"

azure:
  project_endpoint: "${AZURE_AI_PROJECT_ENDPOINT}"
  api_key: "${AZURE_OPENAI_API_KEY}"

gemini:
  api_key: "${GOOGLE_API_KEY}"

cosmosdb:
  endpoint: "${COSMOSDB_ENDPOINT}"
  key: "${COSMOSDB_KEY}"
  database: "stock-options-manager"

context:
  max_activity_entries: 2               # Recent activities injected per symbol (0=none, max 5). Each includes alert status.
  activity_ttl_days: 90                 # Auto-cleanup old activities

scheduler:
  cron: "0 9-16/2 * * 1-5"               # Cron expression (e.g. every 2h, Mon-Fri 9am-4pm)

telegram:
  enabled: false                        # Toggle on/off (also controllable from Settings UI)
  bot_token: "${TELEGRAM_BOT_TOKEN}"    # Bot token from @BotFather
  chat_id: "${TELEGRAM_CHAT_ID}"        # Target chat/group/channel ID
```

### Running

#### Full app (web dashboard + scheduler)

```bash
python run.py
```

Opens the dashboard at http://localhost:8000 and starts the agent scheduler in a background thread. Press `Ctrl+C` to stop both.

#### Web dashboard only

```bash
python run.py --web-only
```

#### Scheduler only (no web UI)

```bash
python run.py --scheduler-only
```

#### Options

| Flag | Description |
|------|-------------|
| `--web-only` | Start only the web dashboard (no scheduler) |
| `--scheduler-only` | Start only the scheduler (no web) |
| `--port PORT` | Override the web server port (default: from `config.yaml` or 8000) |

The dashboard runs on `http://localhost:8000` by default (configurable in `config.yaml` under `web:`).

### Running with Docker

Build the image:

```bash
docker build -t option-income-lab .
```

Run with CosmosDB + your LLM provider credentials.

**Azure example:**

```bash
docker run -d --name option-income-lab \
  -p 8000:8000 \
  -e AI_PROVIDER=azure \
  -e AZURE_AI_PROJECT_ENDPOINT="https://your-project.services.ai.azure.com" \
  -e MODEL_DEPLOYMENT="gpt-5.1" \
  -e AZURE_OPENAI_API_KEY="your-api-key-here" \
  -e COSMOSDB_ENDPOINT="https://your-account.documents.azure.com:443/" \
  -e COSMOSDB_KEY="your-primary-key" \
  option-income-lab
```

**Gemini example:**

```bash
docker run -d --name option-income-lab \
  -p 8000:8000 \
  -e AI_PROVIDER=gemini \
  -e GOOGLE_API_KEY="your-google-api-key" \
  -e MODEL_DEPLOYMENT="gemini-2.0-flash" \
  -e COSMOSDB_ENDPOINT="https://your-account.documents.azure.com:443/" \
  -e COSMOSDB_KEY="your-primary-key" \
  option-income-lab
```

| Variable | Required when | Purpose |
|---|---|---|
| `COSMOSDB_ENDPOINT` | Always | CosmosDB account endpoint |
| `COSMOSDB_KEY` | Always | CosmosDB primary key |
| `AI_PROVIDER` | Optional | `azure` (default) or `gemini` |
| `MODEL_DEPLOYMENT` | Always | Default model name for all agent roles |
| `AZURE_AI_PROJECT_ENDPOINT` | Azure | Azure AI Foundry project endpoint |
| `AZURE_OPENAI_API_KEY` | Azure | Azure OpenAI API key |
| `GOOGLE_API_KEY` | Gemini | Google AI API key |

View logs:

```bash
docker logs -f option-income-lab
```

Pass flags (e.g. web-only mode):

```bash
docker run -d --name option-income-lab-web \
  -p 8000:8000 \
  -e AI_PROVIDER=azure \
  -e AZURE_AI_PROJECT_ENDPOINT="..." \
  -e MODEL_DEPLOYMENT="gpt-5.1" \
  -e AZURE_OPENAI_API_KEY="your-api-key-here" \
  -e COSMOSDB_ENDPOINT="..." \
  -e COSMOSDB_KEY="..." \
  option-income-lab --web-only
```

---

## Azure Deployment

### Prerequisites

- [Azure CLI](https://docs.microsoft.com/en-us/cli/azure/install-azure-cli) installed and logged in (`az login`)
- LLM credentials configured (Azure AI Foundry **or** Google Gemini API key)
- Container image built (e.g., via GitHub Actions)

### 1. Set Variables

```bash
# ── Resource names ───────────────────────────────────────────────────────────
RESOURCE_GROUP="${RESOURCE_GROUP:-rg-option-income-lab}"
LOCATION="${LOCATION:-eastus}"

# CosmosDB
COSMOSDB_ACCOUNT="${COSMOSDB_ACCOUNT:-cosmos-stock-options}"
DATABASE_NAME="${DATABASE_NAME:-stock-options-manager}"
CONTAINER_NAME="${CONTAINER_NAME:-symbols}"

# Container Apps
CONTAINER_ENV="${CONTAINER_ENV:-cae-option-income-lab}"
CONTAINER_APP="${CONTAINER_APP:-ca-option-income-lab}"
IMAGE="${IMAGE:-ghcr.io/dsanchor/option-income-lab:latest}"

# ── Credentials (fill these in) ─────────────────────────────────────────────
AI_PROVIDER="${AI_PROVIDER:-azure}"          # azure | gemini
MODEL_DEPLOYMENT="${MODEL_DEPLOYMENT:-gpt-5.1}"
AZURE_AI_PROJECT_ENDPOINT="${AZURE_AI_PROJECT_ENDPOINT:-your-project-endpoint}"
AZURE_OPENAI_API_KEY="${AZURE_OPENAI_API_KEY:-your-api-key-here}"
GOOGLE_API_KEY="${GOOGLE_API_KEY:-}"         # required when AI_PROVIDER=gemini
```

### 2. Create Resource Group

```bash
az group create \
  --name "$RESOURCE_GROUP" \
  --location "$LOCATION" \
  -o none
```

### 3. Provision CosmosDB

Serverless is recommended — pay-per-request with no minimum cost.

```bash
# Create CosmosDB account (serverless)
az cosmosdb create \
  --name "$COSMOSDB_ACCOUNT" \
  --resource-group "$RESOURCE_GROUP" \
  --kind GlobalDocumentDB \
  --capacity-mode Serverless \
  --default-consistency-level Session \
  --locations regionName="$LOCATION" failoverPriority=0 isZoneRedundant=false \
  -o none

# Create database
az cosmosdb sql database create \
  --account-name "$COSMOSDB_ACCOUNT" \
  --resource-group "$RESOURCE_GROUP" \
  --name "$DATABASE_NAME" \
  -o none

# Create container with partition key /symbol
az cosmosdb sql container create \
  --account-name "$COSMOSDB_ACCOUNT" \
  --resource-group "$RESOURCE_GROUP" \
  --database-name "$DATABASE_NAME" \
  --name "$CONTAINER_NAME" \
  --partition-key-path "/symbol" \
  --partition-key-version 2 \
  -o none

# Create telemetry container (partition key /metric_type, per-document TTL enabled)
az cosmosdb sql container create \
  --account-name "$COSMOSDB_ACCOUNT" \
  --resource-group "$RESOURCE_GROUP" \
  --database-name "$DATABASE_NAME" \
  --name "telemetry" \
  --partition-key-path "/metric_type" \
  --partition-key-version 2 \
  -o none

# Then update to enable TTL (30 days = 2592000 seconds)
az cosmosdb sql container update \
  --account-name "$COSMOSDB_ACCOUNT" \
  --resource-group "$RESOURCE_GROUP" \
  --database-name "$DATABASE_NAME" \
  --name "telemetry" \
  --ttl 2592000 \
  -o none

# Create settings container (partition key /id, configuration persistence)
az cosmosdb sql container create \
  --account-name "$COSMOSDB_ACCOUNT" \
  --resource-group "$RESOURCE_GROUP" \
  --database-name "$DATABASE_NAME" \
  --name "settings" \
  --partition-key-path "/id" \
  --partition-key-version 2 \
  -o none

# Create dgi_screener container (partition key /symbol, DGI screening results)
az cosmosdb sql container create \
  --account-name "$COSMOSDB_ACCOUNT" \
  --resource-group "$RESOURCE_GROUP" \
  --database-name "$DATABASE_NAME" \
  --name "dgi_screener" \
  --partition-key-path "/symbol" \
  --partition-key-version 2 \
  -o none

# Create calendar container (partition key /symbol, earnings & ex-dividend dates)
az cosmosdb sql container create \
  --account-name "$COSMOSDB_ACCOUNT" \
  --resource-group "$RESOURCE_GROUP" \
  --database-name "$DATABASE_NAME" \
  --name "calendar" \
  --partition-key-path "/symbol" \
  --partition-key-version 2 \
  -o none

# Apply custom indexing policy
az cosmosdb sql container update \
  --account-name "$COSMOSDB_ACCOUNT" \
  --resource-group "$RESOURCE_GROUP" \
  --database-name "$DATABASE_NAME" \
  --name "$CONTAINER_NAME" \
  --idx '{
    "indexingMode": "consistent",
    "automatic": true,
    "includedPaths": [
      {"path": "/symbol/?"},
      {"path": "/doc_type/?"},
      {"path": "/timestamp/?"},
      {"path": "/watchlist/covered_call/?"},
      {"path": "/watchlist/cash_secured_put/?"},
      {"path": "/agent_type/?"},
      {"path": "/activity/?"}
    ],
    "excludedPaths": [
      {"path": "/reason/*"},
      {"path": "/raw_response/*"},
      {"path": "/analysis_context/*"},
      {"path": "/*"}
    ]
  }' \
  -o none

# Retrieve endpoint and key
COSMOSDB_ENDPOINT=$(az cosmosdb show \
  --name "$COSMOSDB_ACCOUNT" \
  --resource-group "$RESOURCE_GROUP" \
  --query documentEndpoint \
  --output tsv)

COSMOSDB_KEY=$(az cosmosdb keys list \
  --name "$COSMOSDB_ACCOUNT" \
  --resource-group "$RESOURCE_GROUP" \
  --query primaryMasterKey \
  --output tsv)

echo "COSMOSDB_ENDPOINT=$COSMOSDB_ENDPOINT"
echo "COSMOSDB_KEY=$COSMOSDB_KEY"
```

> **Alternatively**, run `bash scripts/provision_cosmosdb.sh` which performs these same steps, or create the resources manually via the [Azure Portal](https://portal.azure.com) (CosmosDB → NoSQL → serverless capacity mode).

### 4. Deploy to Container Apps

```bash
# Create Container Apps environment
az containerapp env create \
  --name "$CONTAINER_ENV" \
  --resource-group "$RESOURCE_GROUP" \
  --location "$LOCATION" \
  -o none

# Deploy the container app
az containerapp create \
  --name "$CONTAINER_APP" \
  --resource-group "$RESOURCE_GROUP" \
  --environment "$CONTAINER_ENV" \
  --image "$IMAGE" \
  --target-port 8000 \
  --ingress external \
  --min-replicas 1 \
  --max-replicas 1 \
  --cpu 1 \
  --memory 2Gi \
  --env-vars \
    AI_PROVIDER="$AI_PROVIDER" \
    MODEL_DEPLOYMENT="$MODEL_DEPLOYMENT" \
    AZURE_AI_PROJECT_ENDPOINT="$AZURE_AI_PROJECT_ENDPOINT" \
    AZURE_OPENAI_API_KEY="$AZURE_OPENAI_API_KEY" \
    GOOGLE_API_KEY="$GOOGLE_API_KEY" \
    COSMOSDB_ENDPOINT="$COSMOSDB_ENDPOINT" \
    COSMOSDB_KEY="$COSMOSDB_KEY" \
  -o none
```

> **Note:** If your GHCR package is private, add `--registry-username <github-username> --registry-password <github-pat>` with a PAT that has `read:packages` scope.

```bash
# Verify — get the app URL
APP_URL=$(az containerapp show \
  --name "$CONTAINER_APP" \
  --resource-group "$RESOURCE_GROUP" \
  --query "properties.configuration.ingress.fqdn" -o tsv)

echo "Dashboard: https://$APP_URL"

# Check logs
az containerapp logs show \
  --name "$CONTAINER_APP" \
  --resource-group "$RESOURCE_GROUP" \
  --follow
```

> **Security Tip:** Secure your Container App by configuring authentication with Entra ID or other identity providers. This ensures only authorized users can access your application. For setup instructions, see [Azure Container Apps authentication with Entra ID](https://learn.microsoft.com/en-us/azure/container-apps/authentication-entra).

### 5. Update Deployment

After pushing new code (triggers the GitHub Actions workflow to build a new image):

```bash
az containerapp update \
  --name "$CONTAINER_APP" \
  --resource-group "$RESOURCE_GROUP" \
  --image "$IMAGE"
```

---

## Environment Variables

| Variable | Required when | Description |
|---|---|---|
| `COSMOSDB_ENDPOINT` | Always | CosmosDB account endpoint (e.g., `https://account.documents.azure.com:443/`) |
| `COSMOSDB_KEY` | Always | CosmosDB primary key |
| `AI_PROVIDER` | Optional | `azure` (default) or `gemini` |
| `MODEL_DEPLOYMENT` | Always | Default model for all agent roles (Azure deployment name or Gemini model ID) |
| `AZURE_AI_PROJECT_ENDPOINT` | Azure | Azure AI Foundry project endpoint |
| `AZURE_OPENAI_API_KEY` | Azure | Azure OpenAI API key |
| `GOOGLE_API_KEY` | Gemini | Google AI API key from [AI Studio](https://aistudio.google.com/apikey) |
| `TELEGRAM_BOT_TOKEN` | Optional | Telegram bot token (if notifications enabled) |
| `TELEGRAM_CHAT_ID` | Optional | Telegram chat ID (if notifications enabled) |

## Troubleshooting

### "Missing required config: azure.project_endpoint" (using Gemini)
Set `AI_PROVIDER=gemini` in `.env` (or `ai.provider: gemini` in `config.yaml`). Azure credentials are not required when using Gemini. Ensure `GOOGLE_API_KEY` is set.

### "Environment variable AZURE_AI_PROJECT_ENDPOINT not set"
You are using the Azure provider (`AI_PROVIDER` unset or `azure`). Export `AZURE_AI_PROJECT_ENDPOINT` and `AZURE_OPENAI_API_KEY`, or switch to Gemini with `AI_PROVIDER=gemini` and `GOOGLE_API_KEY`.

### "gemini API key not configured"
Set `GOOGLE_API_KEY` in `.env` when `AI_PROVIDER=gemini`.

### CosmosDB Connection Errors
- Verify `COSMOSDB_ENDPOINT` and `COSMOSDB_KEY` are set correctly
- Ensure the CosmosDB account, database (`stock-options-manager`), and containers (`symbols`, `telemetry`) exist
- Run `bash scripts/provision_cosmosdb.sh` to create missing resources

### Data Fetching Issues
- If market data fetching fails, check network connectivity and Yahoo Finance availability
- yfinance requires no authentication — if you get 429 errors, the built-in rate limiter should handle it

### LLM / Authentication Errors
- **Azure:** Ensure `AZURE_OPENAI_API_KEY` and `AZURE_AI_PROJECT_ENDPOINT` are set. Get the API key from the Azure Portal under your Azure OpenAI resource.
- **Gemini:** Ensure `GOOGLE_API_KEY` is set and `MODEL_DEPLOYMENT` uses a valid Gemini model ID (e.g. `gemini-2.0-flash`). Gemini uses Google's OpenAI-compatible API endpoint.

### Module Import Errors
Make sure you installed the correct SDK packages: `pip install agent-framework-core agent-framework-openai` (NOT `azure-ai-agents`)

## Development

### Agent Skills Architecture

Agent instructions use the **native agent-framework `SkillsProvider`** for shared knowledge blocks. Instead of duplicating common sections (earnings gates, roll economics, data format guides) across every instruction file, they are extracted into reusable `SKILL.md` files under `src/skills/`.

**How it works — Progressive Disclosure:**

1. **Advertise** — Skill names and descriptions are injected into the agent's system prompt (~100 tokens per skill)
2. **Load on demand** — The agent calls `load_skill` tool to retrieve full content only when needed
3. **Read resources** — Supplementary files available via `read_skill_resource` tool

```python
# In agent_runner.py
from agent_framework import Agent, SkillsProvider

skills_provider = SkillsProvider.from_paths(skill_paths="src/skills/earnings-gate-monitor")
agent = Agent(
    client=client,
    instructions="...",  # Only role-specific instructions
    context_providers=[skills_provider],  # Skills loaded on demand
)
```

**Available skills:**

| Skill | Description | Used by |
|-------|-------------|---------|
| `earnings-gate-monitor` | Earnings decision matrix for open positions | Assessment agents |
| `earnings-gate-sell` | Earnings decision matrix for new positions | Covered call / CSP watchers |
| `roll-economics` | Premium-First Roll Policy (3-tier hierarchy) | Roll management agents |
| `data-source` | Yahoo Finance data format guide | All agents |
| `risk-flags` | Risk flag taxonomy | Assessment + Roll agents |
| `activity-log` | Previous activity log interpretation | Assessment agents |

**Benefits:**
- **Reduced token cost** — Skills only loaded when the agent needs them (progressive disclosure)
- **No duplication** — Shared knowledge lives in one place
- **Cleaner instruction files** — Only role-specific logic, ~20% shorter
- **Standard format** — SKILL.md with YAML frontmatter follows the `agentskills.io` specification

### Instruction Files

Each agent has its own instruction file returning a system prompt string:
- `covered_call_instructions.py` — Covered call watcher
- `cash_secured_put_instructions.py` — Cash secured put watcher
- `open_call_assessment_instructions.py` — Open call Phase 1 (assessment)
- `open_call_roll_instructions.py` — Open call Phase 2 (roll management)
- `open_put_assessment_instructions.py` — Open put Phase 1 (assessment)
- `open_put_roll_instructions.py` — Open put Phase 2 (roll management)
- `buy_tracker_instructions.py` — Buy tracker (informational, no supervisor/alpha review)
- `supervisor_instructions.py` — Quality auditor (9 playbooks × 4 agent contexts)
- `alpha_instructions.py` — Alpha Advisor (aggressive perspective)

All instructions assume pre-fetched market data — the LLM receives data as text and performs analysis only (no tools, no HTTP access).

### SDK Information

This project uses the **Microsoft Agent Framework** (`agent-framework` package from https://github.com/microsoft/agent-framework).

Key components:
- `agent_framework.Agent` — Agent runner class with `context_providers` support for native Skills
- `agent_framework.SkillsProvider` — Discovers SKILL.md files and provides progressive disclosure via tools
- `agent_framework.openai.OpenAIChatCompletionClient` — Chat client for Azure OpenAI and OpenAI-compatible APIs
- `src/llm.py` — Provider factory (`azure` / `gemini`) shared by agents and web chat endpoints

Market data is fetched via `yfinance` Python library — overview, technicals, forecast, dividends, and options chains are all retrieved through Yahoo Finance's API. All fetching is driven from Python (`yfinance_data_provider.py`), not by the LLM. The LLM receives pre-fetched data as text and performs analysis only — no tools are given to the agent (except the skill-loading tools injected by SkillsProvider).

---

## Acknowledgments

This project was built with [GitHub Copilot](https://github.com/features/copilot) and [Squad](https://github.com/bradygaster/squad) by [@bradygaster](https://github.com/bradygaster) — an AI team orchestration framework that runs inside Copilot CLI. Squad coordinated multiple specialized agents to develop, test, and iterate on this codebase.
