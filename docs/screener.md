# DGI Screener & Trading Skills

[← Back to README](../README.md)

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

**yfinance field fallbacks:** yfinance returns `None` for key fields (`dividendYield`, `payoutRatio`, `debtToEquity`) on many symbols (MO, PEP, JNJ, etc.). The screener uses automatic fallbacks:
- `dividendYield` → `trailingAnnualDividendYield` (already decimal, no conversion needed)
- `payoutRatio` → computed from `trailingAnnualDividendRate / trailingEps`
- `debtToEquity` → computed from `totalDebt / totalStockholderEquity`

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

The Buy Tracker is an AI-powered DCA timing agent that determines optimal
accumulation timing for DGI stocks. Unlike the Entry tag (pure technical timing
score), the Buy Tracker evaluates **5 dimensions** holistically and returns one
of six entry-timing states.

### Six-State Scale

The Buy Tracker uses a six-state ordered scale from most to least favorable for
**entry timing**. `UNFAVORABLE` and `AVOID` are not sell signals — they flag
poor timing for opening a *new* position.

| State | Entry Timing Meaning | Alert? | Sizing Guidance |
|-------|----------------------|--------|-----------------|
| `STRONG_BUY` | Exceptional confluence — all dimensions confirm + exceptional gate | Yes (high) | 2–3× normal DCA |
| `BUY` | Clear favorable window for accumulation | Yes (normal) | Normal DCA |
| `ACCUMULATE` | Acceptable but not compelling — lean positive | Yes (low) | ½ DCA or limit order |
| `WAIT` | Neutral — insufficient signal in either direction | No | Patient default |
| `UNFAVORABLE` | Conditions lean negative — poor timing for new entry | No | Avoid new buys now |
| `AVOID` | Actively bad setup — hard gate or deep headwinds | No | Do not enter |

### Tri-State Dimension Scoring (−1 / 0 / +1)

Each of the five dimensions scores **−1 (headwind)**, **0 (neutral)**, or
**+1 (tailwind)**. The signed total score ranges from **−5 to +5**.

| Dimension | Score +1 (Tailwind) | Score 0 (Neutral) | Score −1 (Headwind) |
|-----------|--------------------|--------------------|----------------------|
| **Value Entry / Pullback** | Pullback ≥5% from 52wH; or price ≤3% above SMA50; or price below SMA200 by ≤5% | No clear signal, or data missing | Price >5% above SMA50 AND >8% above SMA200 |
| **Trend** | Price ≥ SMA200 AND SMA50 ≥ SMA200 | Mixed or transitioning | Price >8% below SMA200 AND SMA50 < SMA200 |
| **Momentum** | RSI ≤50 AND (MACD Buy, Stoch Buy, or oscillator Sell/Neutral) | RSI 50–70 without confirmation | RSI >70 OR oscillator = STRONG_BUY |
| **Income & Fundamentals** | Yield ≥2% OR payout <60%; AND analyst ∈ {BUY, HOLD}; AND no cut | Some but not all conditions met | Dividend cut/suspended; OR analyst = STRONG_SELL with target >15% below price |
| **Calendar & Risk** | Earnings >14 days away; no calendar risk | Earnings 4–14 days away; data missing | Earnings ≤3 days away; or gap-down on volume |

### Activity Determination

| Score | Base State |
|-------|-----------|
| −5 to −3 | `AVOID` |
| −2 to −1 | `UNFAVORABLE` |
| 0 to +1 | `WAIT` |
| +2 to +3 | `ACCUMULATE` |
| +4 to +5 | `BUY` (→ `STRONG_BUY` only via exceptional gate) |

The score is displayed as a signed value: `"+3/5"`, `"-2/5"`, `"0/5"`.

### Hard Gates (Override Score)

Gates take precedence in this order: Hard AVOID → Hard WAIT → Exceptional STRONG_BUY → score-based.

**Hard AVOID** (force `AVOID` regardless of score):

- Dividend cut or suspended (`dividend_cut_or_suspended`)
- Triple bearish: oscillator = STRONG_SELL, MA = STRONG_SELL, price >10% below SMA200 (`triple_bearish_breakdown`)

**Hard WAIT** (cap to `WAIT` if score-based state would be ACCUMULATE or better):

- Earnings ≤ 2 days (`earnings_within_2_days`)
- RSI > 80 (`rsi_over_80`)
- Price >10% above SMA50 AND >15% above SMA200 (`price_extended_above_mas`)

### Exceptional STRONG_BUY Gate

Score +5 with all dimensions confirming. Requires also: pullback 8–20% from
52wH, price within SMA50 band, RSI 25–45, MACD+Stoch Buy confirmed, dividend
current, payout ≤75%, analyst upside ≥5%, earnings >7 days away. If any check
fails, score +5 remains `BUY`.

### Missing Data Behavior

- Missing data for a dimension → score `0` (neutral).
- If ≥ 3 dimensions score 0 due to missing evidence → state capped at `WAIT`,
  risk flag `insufficient_data` added.

### Entry Tag vs Buy Tracker

| Aspect | Entry Tag | Buy Tracker |
|--------|-----------|-------------|
| Method | Deterministic (technical timing score thresholds) | AI (LLM interprets 5 dimensions; normalizer enforces deterministic gates) |
| Inputs | RSI, SMA, pivot supports, volume | + dividend yield, earnings calendar, analyst consensus, payout ratio |
| Output | Strong Buy / Buy / Accumulate / Hold / Wait | STRONG_BUY / BUY / ACCUMULATE / WAIT / UNFAVORABLE / AVOID |
| AVOID/UNFAVORABLE | Not applicable | Entry-timing signals only — never sell recommendations |


## Category-Based Strategy Skills

Sell-side agents (Covered Call and Cash-Secured Put) apply **category-specific parameter skills** that adapt trading thresholds to each stock's DGI category. The category is read from the symbol's enrichment data and the matching skill is loaded automatically alongside the base skills (earnings gate, data source, risk flags).

**All base rules remain enforced** — earnings gate, DTE ≤ 45 hard cap, fundamental checks, and WAIT triggers. Category skills ONLY adjust delta ranges, premium minimums, IV requirements, and market state guidance.

### Parameter Summary

**Covered Call skills** (`backend/src/skills/cc-{category}/`):

| Category | Delta Range | Min Premium (30-45 DTE) | IV Requirement | Key Behavior |
|---|---|---|---|---|
| **Aristocrat** | 0.20–0.30 | ≥ 0.5% | None (low IV is structural) | Total return = premium + dividend; uptrend is normal |
| **Compounder** | 0.15–0.25 | ≥ 0.6% | IV Rank ≥ 30 | Protect growth upside; WAIT on strong momentum |
| **Rising Star** | 0.10–0.20 | ≥ 0.8% | IV Rank ≥ 40 | Very selective; WAIT during breakouts |
| **High Yield** | 0.25–0.35 | ≥ 0.8% | IV Rank ≥ 30 | Aggressive income; critical ex-div awareness |
| **Balanced** | 0.20–0.30 | ≥ 0.8% | IV Rank ≥ 35 | Standard defaults |

**Cash-Secured Put skills** (`backend/src/skills/csp-{category}/`):

| Category | Delta Range | Min Premium (30-45 DTE) | IV Requirement | Key Behavior |
|---|---|---|---|---|
| **Aristocrat** | -0.25 to -0.35 | ≥ 0.8% | None (low IV is structural) | Assignment = owning a top-tier stock at discount |
| **Compounder** | -0.20 to -0.30 | ≥ 1.0% | IV Rank ≥ 30 | Buy-the-dip entries; pullbacks in uptrend |
| **Rising Star** | -0.15 to -0.25 | ≥ 1.2% | IV Rank ≥ 40 | Conservative; only at strong support + oversold |
| **High Yield** | -0.25 to -0.35 | ≥ 1.0% | IV Rank ≥ 25 | Extra fundamental scrutiny (payout, debt) |
| **Balanced** | -0.20 to -0.30 | ≥ 1.2% | IV Rank ≥ 35 | Standard defaults |

### How It Works

1. **Portfolio Enrichment** computes the symbol's DGI category (Aristocrat, Compounder, etc.)
2. When the agent runs, `covered_call_agent.py` / `cash_secured_put_agent.py` reads `enrichment.category` from the symbol document
3. `agent_runner.py` resolves the matching skill (e.g., `cc-aristocrat` for a Covered Call on an Aristocrat stock)
4. The skill is loaded alongside `earnings-gate-sell`, `data-source`, and `risk-flags`
5. The agent's prompt includes the category label — the agent loads the skill and applies its adjusted thresholds
6. The agent evaluates market state (RSI, trend, IV) in real-time with fresh data, then applies category-specific guidance
7. **Roll alignment:** When an open position needs rolling, the monitor agents pass the symbol's category to the roll management phase, which receives category-specific delta targets (e.g., Rising Star CC → 0.10–0.20) ensuring roll strikes match the original entry risk profile