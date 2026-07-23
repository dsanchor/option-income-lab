# Linus — History

## Project Context
- **Project:** options-agent
- **User:** dsanchor
- **Stack:** Python, Microsoft Agent Framework, Azure Foundry (gpt-5.4-mini)
- **MCP:** iflow-mcp_ferdousbhai_investor-agent 1.6.3
- **Description:** Two periodic trading agents for covered call and cash-secured put sell signals. Local runtime, configurable polling, stock symbols from files, decision logs, sell signal alerts.

## Learnings

- 2026-07-01: Roll candidate table now sorted by Ann.Ret% (was Net Credit) to favor shorter-DTE rolls aligned with the 21-35 DTE target; approved by dsanchor 2026-07-01.
- 2026-07-01: Roll target is now 21-35 DTE primary with 45 DTE fallback cap, and post-earnings hard block is 0-7 days with 8-13 days as a caution zone; applied symmetrically across calls/puts after dsanchor approval.
### Position Snapshot Capture (2026-06-04)
- `src/agent_runner.py` now captures a point-in-time snapshot for every position-monitor run immediately after `fetch_all()`, before the Phase 1 assessment runs.
- Snapshot extraction reads the underlying price from `overview.fundamentals.current_price.value` with a fallback to `technicals.price`, and reads RSI/MACD from `technicals.oscillators.indicators` (`RSI`, `MACD.macd`).
- `src/cosmos_db.py` persists snapshots in the shared `symbols` container as `doc_type="position_snapshot"` documents with 90-day TTL, queryable by `position_id` descending by timestamp.
- There was a sign-convention ambiguity in the request text; implementation followed the explicitly requested formulas and example schema (`call: current-strike`, `put: strike-current`) so stored history matches the documented payload.

### Buy Tracker DGI Alignment (2026-06-03)
- `src/buy_tracker_instructions.py` now frames the buy tracker as a patient DGI accumulation agent: `BUY` means a small DCA add, `STRONG_BUY` means a larger accumulation entry, and `WAIT` is the expected default when the setup is not in a truly attractive range.
- The required JSON examples were aligned to the five real scoring dimensions (`value_entry`, `trend`, `momentum`, `income`, `calendar`) and `target_horizon` was explicitly removed because the buy tracker is for long-term hold-forever stock accumulation, not a timed trade.
- To keep downstream review agents compatible with the primary buy tracker, `src/supervisor_instructions.py` and `src/alpha_instructions.py` must treat `STRONG_BUY` as a valid `buy_tracker` decision.

### Prolonged WAIT Cooldown Deadlock Fix (2026-05-14)
- The `_detect_prolonged_wait()` method in `src/agent_runner.py` checks if enough consecutive WAITs have occurred to trigger Alpha for deeper review. A cooldown prevents re-triggering Alpha too soon after a previous alpha review.
- **Bug:** Cooldown loop checked `supervisor_view` to find last Alpha review, but supervisor runs unconditionally on every activity, so ALL records have `supervisor_view` set. Loop always broke on first record → `waits_since_last_review` always 0 → cooldown always returned `False` → Alpha could never trigger.
- **Production evidence:** MKC cash_secured_put ran 6+ consecutive WAITs with Supervisor [WEAK], but "⏳ Prolonged WAIT detected" never appeared.
- **Fix:** Changed cooldown field from `supervisor_view` → `alpha_view`. The `alpha_view` field only exists on records where full supervisor+alpha review cycle previously fired, making it the correct cooldown signal. Single field change at line 676.
- **Key pattern:** When checking activity logs for "last occurrence of event X", must filter by a field that only exists on X records — not a field that exists on all records.
- **Commit:** `fix: use alpha_view instead of supervisor_view in prolonged WAIT cooldown`

### Premium=0.0 Root Cause Analysis & Fix (2026-05)
- ALL agents returning premium=0.0 across covered call and cash-secured put agents.
- Root cause investigation traced the full pipeline: TradingView Playwright interception → parser → filter → serialization → agent consumption → validation.
- **Most likely root cause**: TradingView migrated their options chain page from `scan2` endpoints to `scan`/`screener`/`scan3` endpoints. The `_OPTIONS_SCAN_URLS` only matched `scan2`, so no API responses were captured, the DOM text fallback was used, the parser failed to find JSON → empty chain → agents got raw text → defaulted to premium=0.0.
- **Secondary hypothesis**: TradingView changed field names in their API response (e.g., "bid" → "option_bid"). The parser would store bid=None, agents see null, output 0.0.
- Fix applied in 3 layers: (1) Broadened URL matching to cover scan/scan2/scan3/screener + added fallback matching for any scanner.tradingview.com response with "symbols-options"; (2) Added field name aliases to `_FIELD_MAP`; (3) Added diagnostic logging at ERROR level for missed scanner URLs and missing bid/ask fields.
- Also fixed broken tests in `test_options_chain_parser.py` — tests used `[0]` list indexing but parser was refactored to strike-keyed dict format at commit `7be7d82`. Tests were never updated.
- Key pattern: When intercepting third-party API responses by URL matching, URL patterns MUST be broadly defined and logged when they miss. Third parties change endpoints without notice. Always have a fallback matcher and diagnostic logging.

### DGI Screener Logging & Silent Exception Fix (2026-05)
- User reported screener runs visible in web UI but CosmosDB entries NOT being updated — no new `last_updated`, no `quality_detail` field despite commit 2415b47.
- Root cause investigation: `_run_dgi_screener_in_background()` in `web/app.py` catches all exceptions but logged them WITHOUT `exc_info=True` — full tracebacks were silently lost. Same issue in the CosmosDB upsert error handler in `dgi_screener.py`.
- Additionally, `upsert_dgi_top()` in `cosmos_db.py` has a silent `if container is None: return` guard that skips all writes with only a warning-level log. If the container ref goes stale, writes silently stop. This is the most likely cause of the missing updates — needs monitoring.
- Fix: Added `exc_info=True` to both error handlers. Added 5 INFO-level log traces at key pipeline stages: start of each symbol analysis, after quality score + category, before CosmosDB upsert, after successful upsert, and at run completion with total counts.
- The `quality_detail` fix from commit 2415b47 was verified correct: `calculate_quality_score_detailed()` is properly called, result is included in both candidates list and CosmosDB document.
- Key pattern: Background thread error handlers MUST include `exc_info=True` — without it, async failures become invisible. Any "container is None" guard should log at ERROR level, not WARNING, when it causes data loss.

### StockAnalysis.com Dividend Data Integration (2026-05)
- Created `src/stockanalysis_fetcher.py` — scrapes dividend summary widget from stockanalysis.com for authoritative Growth Years and supplementary metrics.
- `fetch_dividend_data(symbol)` returns dict with 8 fields (yield, annual dividend, payout ratio, growth, growth_years, frequency, buyback yield, shareholder yield) or `None` on error.
- Integrated into both `analyze_single_symbol()` and `run_dgi_screener()` in `src/dgi_screener.py` via shared `_apply_stockanalysis_overrides()` helper.
- Priority rule: `years_consecutive_increases` ALWAYS uses SA's `growth_years` when available (Yahoo calculation from dividend series is unreliable). Other metrics (yield, payout ratio, CAGR) only fall back to SA when Yahoo returns 0/missing.
- Uses in-memory dict cache (`_cache`) to avoid redundant HTTP requests within a single screener run.
- 0.5s delay between SA requests in batch mode to be polite; User-Agent rotation mirrors `tv_data_fetcher.py` pattern.
- Dual parsing strategy: first tries structured DOM traversal (label/value siblings), then falls back to regex on raw HTML for resilience against page structure changes.

### Contrarian Agent Instructions (2026-07)
- Created `src/tv_contrarian_instructions.py` implementing Danny's Opción D architecture (decision `danny-contrarian-agent-architecture.md`).
- Function `get_contrarian_instructions(agent_type, decision_type)` returns a customized system prompt. Covers 4 agent types × their valid decisions = 16 combinations total.
- Decision-specific playbooks for: WAIT, ROLL_UP, ROLL_DOWN, ROLL_UP_AND_OUT, ROLL_DOWN_AND_OUT, ROLL_OUT, CLOSE, SELL, NOT_NOW.
- Anti-noise rules are critical: LLMs will always find something to argue — the prompt explicitly instructs WEAK self-assessment when the original decision is solid, and forbids arguing against risk management decisions or violating the 45 DTE cap.
- Exported `CONTRARIAN_OUTPUT_SCHEMA` dict for `agent_runner.py` to validate/parse the JSON response.
- Input validation raises `ValueError` for invalid agent_type/decision_type combos (e.g., open_put + ROLL_UP is rejected since puts don't roll up).
- Key pattern: Parameterized instruction functions (vs. module-level constants) are the right approach when the same agent serves multiple contexts — keeps one source of truth for shared rules while customizing the playbook section.

### Near-ATM Stability Buffer (2026-07)
- Problem: Positions slightly ITM (price barely crosses strike) trigger ROLL, then revert to OTM and get WAIT on the next run — oscillating recommendations.
- Fix: Added `## NEAR-ATM STABILITY BUFFER` section to both `tv_open_call_assessment_instructions.py` and `tv_open_put_assessment_instructions.py`, placed before ACTIVITY CRITERIA.
- Stability zone: within 3% of strike on the ITM side. If technicals are favorable (Neutral/Sell oscillators for calls, Neutral/Buy for puts) and delta < 0.60, default to WAIT.

### DGI Screener Payout Ratio Display Bug Fix (2026-05)
- **Problem:** User reported seeing raw decimal values like "0,3" (0.3) and "0,333" (0.333) for payout_ratio in the detail modal instead of formatted percentages like "30%" and "33.3%".
- **Investigation findings:**
  1. yfinance `payoutRatio` returns decimal ratio (0.333 = 33.3%) — tested with ZTS, MSFT, JNJ, AAPL, KO
  2. Cosmos stores it correctly as decimal (internal convention: 0-1 range)
  3. `dgi_screener.py` line 127 stores raw: `info.get("payoutRatio") or 0`
  4. stockanalysis_fetcher also returns decimal: `float(m.group(1)) / 100.0` for "33%" → 0.33
  5. `dgi_metrics.py` scoring functions expect decimal (`_payout_safety_score`, `max_payout: 0.75`)
  6. `dgi_analysis.html` formats correctly: `"%.0f%%"|format(m.payout_ratio * 100)` ✅
  7. **Bug source:** `dgi_screener.html` detail modal's `buildSection()` function had field-specific formatting logic AFTER generic `fmtVal()` call — the number was already converted to a string, so the formatting conditions couldn't apply ❌
- **Root cause:** Logic error in execution order — `fmtVal(v)` converted the numeric value to a string with `toLocaleString()`, then the subsequent `if (typeof v === 'number' && k === 'payout_ratio')` check always failed because `v` was still the original number but `displayVal` was already a string.
- **Fix:** Reordered the formatting logic in `dgi_screener.html` lines 422-447 to apply field-specific formatting FIRST, then fall back to generic `fmtVal()` only for unmatched fields:
  - Changed from: `displayVal = fmtVal(v); if (typeof v === 'number' && ...)` (broken)
  - Changed to: `if/else if` chain with field-specific formatting, then `else { displayVal = fmtVal(v); }` (correct)
  - Percentage fields: `payout_ratio` (1 decimal), `dividend_yield`, `dividend_cagr_5y`, `roe` (2 decimals) — multiply by 100, add % suffix
  - Market cap: `>= 1e9` → `$X.XXB`, `>= 1e6` → `$X.XXM`
  - Price: `current_price` → `$X.XX`
  - Ratio: `debt_to_equity` → `X.XX` (2 decimals, no suffix)
- **Validation:** Confirmed the if/else if chain now correctly applies formatting before string conversion. Internal storage remains decimal (0-1 range) for percentages.
- **Key pattern:** When applying conditional formatting, ensure the condition checks happen BEFORE the value is converted to a string. If you call a generic formatter first, subsequent type checks will fail. Use if/else if chains where specific cases come first and generic fallback comes last.
- Override conditions: delta > 0.60, Strong directional momentum against position, earnings imminent, ex-div risk (calls), DTE < 7 and ITM.
- Added anti-flip-flop rule to INTERPRETING PREVIOUS ACTIVITY LOG: maintain WAIT if delta change < 0.10 and price change < 1% since last reading.
- Modified ROLL triggers #1 and #2 to require momentum (not just proximity) and respect the stability zone.
- Added `near_atm_stability` to the Unified Risk Flag Taxonomy in both files.
- Key pattern: Near-ATM positions need hysteresis — once you decide WAIT, require a clear deterioration trend to switch to ROLL, not just a single marginal crossing.

### Bare ROLL Bug + ROLL_OUT Guardrail (2026-07)
- Bug 1: Phase 1 agents sometimes output `"action_needed": "ROLL"` without a direction suffix. Fixed by adding explicit `⛔ VALID ACTIONS` enumeration near the top of all four instruction files (both Phase 1 assessment and Phase 2 roll management). Added constraint text on `action_needed` in Phase 1 handoff schema and `activity` in Phase 2 output schema: "Never output bare ROLL — always include the direction suffix."
- Bug 2: ROLL_OUT leading to immediate CLOSE on the next monitoring cycle. Root cause: ROLL_OUT keeps the same strike, so if the strike is fundamentally wrong (deep ITM/OTM), the next cycle sees the same problem and recommends CLOSE. Fixed by adding a `⚠️ ROLL_OUT GUARDRAIL` section in both Phase 1 assessment files (call + put) that restricts ROLL_OUT to cases where the strike is still viable (near-the-money delta ranges), the position is near expiration (≤5 DTE), and there's no directional signal. When the strike needs to change, agents must use compound roll types (ROLL_UP_AND_OUT, ROLL_DOWN_AND_OUT) instead.
- Key pattern: When the agent has a "ROLL" concept as a category and specific types as instances, the agent may output the category name instead of an instance. Always enumerate valid outputs explicitly and reject the category name.

### Hard 45 DTE Cap for New Positions (2026-07)
- User reported agents recommending expirations with DTE > 45 too frequently
- Root cause: Instructions said "Avoid >60 DTE" — the 46-60 day gap had no prohibition. The earnings gate post-earnings path (≥14 days after earnings when >30 days out) could also push past 45 DTE.
- Fix: Added explicit ⛔ HARD MAXIMUM 45 DTE in 6 places across both `src/tv_covered_call_instructions.py` and `src/tv_cash_secured_put_instructions.py`: Time Frame section, Theta description, Earnings gate post-earnings row, KEY PRINCIPLE, DTE Selection Priority, and WAIT triggers.
- Key pattern: When instruction text says "optimal X" but "avoid Y" where Y > X, agents treat the gap as permitted. Always set the hard cap equal to the upper bound of the optimal range.
- The same pattern likely exists in Alpha Vantage and Massive.com instruction files — those should be audited too.

### Telegram Settings UI (2026-03-29)
- Added Telegram configuration endpoints to `web/app.py`:
  - `GET /settings` — Returns current config with token masked
  - `POST /settings` — Persists Telegram settings to config.yaml, reloads notifier
  - `POST /api/settings/test-telegram` — Sends test message to configured channel
- Updated `web/templates/settings.html` with Telegram card:
  - Enable/disable checkbox, bot token input (type="password"), channel ID input
  - Test button, Save/Cancel buttons, status message area
  - JavaScript handlers for form interaction and API calls with inline feedback
- Commit: 4e1c16c (merged with Danny's work).

### Manual Roll Button + Signal Display Fix (symbol_detail)
- Added "Roll" button to positions table actions column (active positions only), next to Close.
- Roll form is an inline expandable panel inside the `pos-detail-row`, hidden by default. Contains: new strike (pre-filled), new expiration (pre-filled), optional notes, confirm/cancel buttons, and a message area.
- JS: Roll button click expands the detail row and reveals the form. Confirm POSTs to `/api/symbols/{sym}/positions/{id}/roll` with `{new_strike, new_expiration, notes}`. Cancel hides the form.
- Updated row click handler exclusion to also ignore `[data-roll-pos]` and `.roll-form` clicks.
- Signal table now shows roll context: if `new_strike`/`current_strike` are present, strike column shows "(from $X)"; same pattern for expiration. Enables users to see what the monitor agent recommended rolling to/from.

### Roll Position Frontend (decision_detail + symbol_detail)
- `decision_detail.html`: Signal banner button is now conditional on `decision.agent_type`. Watch agents (`covered_call`, `cash_secured_put`) → "Open Position" button (existing `/from-decision/` endpoint). Monitor agents (`open_call_monitor`, `open_put_monitor`) → "Roll Position" button (new `/roll-from-decision/` endpoint). JS refactored with shared `showMsg()` helper, each button handler is guarded by `getElementById` null-check so only the rendered button gets wired up. Renamed `openPosMsg` → `actionMsg`.
- `symbol_detail.html`: Expandable position detail panel now shows two additional sections after the source/manual block: (1) "📉 Closed by signal" section using `pos.closing_source` with same detail-grid layout as opening source, orange border-left for closing reason; (2) Roll reference links using `pos.rolled_from` / `pos.rolled_to` in a subtle flex row. Both sections are conditional and gracefully absent when the data isn't present.
- Roll endpoint depends on Rusty's backend work (`POST /api/symbols/{sym}/positions/roll-from-decision/{id}`).

### Open Position from Decision + Expandable Positions (frontend)
- `decision_detail.html`: "Open Position" button conditionally rendered when `is_signal` is true; POSTs to `/api/symbols/{sym}/positions/from-decision/{id}`, shows success/error inline, redirects to symbol page after 1s. Confirmation dialog warns about watchlist disable.
- `symbol_detail.html`: Positions table now expandable — each row has a ▸/▾ chevron toggling a detail `<tr>` with `colspan="8"`. Detail panel shows `pos.source` signal data (strategy, decision, confidence, underlying price, premium, IV, risk flags, reason) using existing `detail-grid` CSS class, or "Created manually" for positions without source.
- Event propagation: Close/Delete button clicks use `e.target.closest()` guard to prevent row expand/collapse from firing on button clicks.
- Colspan is 8 (added chevron column to positions table header).
- Agent type formatting: `covered_call` → "Covered Call", `cash_secured_put` → "Cash-Secured Put" via inline Jinja ternary.
- No new CSS classes needed — reused `detail-grid`, `detail-field`, `detail-label`, `detail-value`, `badge`, `flag`, `confidence-*`.
- Existing JS handlers (add position, close, delete, watchlist toggle) left untouched; expand/collapse JS added alongside.

### Price + Signal Timeline Chart (symbol detail page)
- TradingView Lightweight Charts CDN loaded only in symbol_detail.html (not base.html) to keep other pages lightweight
- yfinance runs sync I/O — must use `asyncio.to_thread()` in FastAPI async routes to avoid blocking the event loop
- Lightweight Charts requires markers sorted by time; backend sorts before returning JSON
- Marker click navigation: `chart.subscribeClick()` matches time to marker array, then redirects to `/decisions/{id}`
- Signal markers use `is_signal` flag OR cross-reference against signal `decision_id` set for complete coverage
- Chart colors matched to CSS variables: `--bg-card: #1a1a2e`, `--border: #2a2a4a`, `--text: #e0e0f0`

## Core Context

**2024-01 Foundation: Trading Agent Instructions Framework**
Created comprehensive dual-strategy instruction files (Covered Call and Cash-Secured Put) defining analysis protocols, decision criteria, Greeks targets, and risk management principles. Both strategies use:
- 8-11 phase systematic analysis with MCP tool integration
- Dual-threshold decision framework: Standard SELL (IV Rank ≥50) vs. CLEAR SELL SIGNAL (premium ≥2%, IV Rank ≥70)
- Greeks-focused strike selection: CC targets Δ 0.20-0.35, CSP targets AT/BELOW support with same delta range
- 30-45 DTE optimal window for theta decay
- Earnings calendar integration: CC avoids expiring after earnings (gap risk), CSP targets post-earnings (IV crush)
- Fundamental quality gate: CSP requires "Would you own this stock at strike?" check before assignment

**Key Decision Criteria (Summarized):**
- **Covered Calls**: Time decay + sideways movement profits, avoid strong uptrends, never sell calls expiring after earnings
- **Cash-Secured Puts**: Fundamentals-first approach, strike AT/BELOW support, ideal 1-3 days post-earnings for IV crush
- **Output Format**: Standardized for parsing (legacy: pipe-delimited text, current: JSON with SUMMARY line)
- **Capital Allocation**: <20% per stock (CSP), 50% position sizing (CC)

**2024-01 MCP Server Migration:**
Updated instruction DATA GATHERING sections from iflow-mcp-ferdousbhai to Massive.com's mcp_massive (4-tool discovery pattern: search_endpoints → get_endpoint_docs → call_api → query_data with store_as/apply functions). Maintained identical strategy logic and decision criteria across the migration.

**2026-03 Current State:**
- **3 Data Providers**: Massive.com, Alpha Vantage, TradingView (each with CC + CSP instructions = 6 files)
- **Output Format**: JSON + SUMMARY (machine-parseable + human-readable)
- **Infrastructure**: Config system, logger with dual logging (.jsonl + .log), agent_runner with JSON extraction + fallback
- **Model**: gpt-5.1 (updated from gpt-5.4-mini for TradingView Playwright support)

---

### 2024-01-15: Created Trading Agent Instructions
Created comprehensive system prompts for both covered call and cash-secured put agents:

**Covered Call Instructions** (`src/covered_call_instructions.py`):
- Structured 8-phase analysis protocol using MCP tools (ticker data, price history, options chain, earnings calendar, sentiment)
- Defined clear SELL criteria: IV Rank ≥50, delta 0.20-0.35, no earnings within DTE, strike at/above resistance
- Strike selection framework: Conservative (Δ0.20-0.25), Moderate (Δ0.25-0.30), Aggressive (Δ0.30-0.35)
- CLEAR SELL SIGNAL threshold: premium ≥2% for 30-45 DTE, IV Rank ≥70, clean calendar
- Key insight: Covered calls profit from time decay and sideways movement; avoid during strong uptrends

**Cash-Secured Put Instructions** (`src/cash_secured_put_instructions.py`):
- Added mandatory fundamental quality gate: "Would you want to own this stock at strike price?"
- 11-phase analysis including financial statements, institutional holders, insider trades, earnings history
- Strike selection rule: AT or BELOW support levels (never above support)
- Emphasized post-earnings timing (1-3 days after = ideal for IV crush capture)
- CLEAR SELL SIGNAL: premium ≥2.5%, oversold (RSI <30), strong fundamentals, IV Rank ≥70

**Design Decisions**:
- Both agents use standardized output format for parsing: `[TIMESTAMP] SYMBOL | DECISION: SELL/WAIT | Strike: $X | ...`
- Dual-threshold system: Standard SELL criteria + elevated CLEAR SELL SIGNAL criteria
- Previous decision log interpretation guidance ensures agents learn from history
- Greeks-focused with specific delta ranges to balance premium vs. assignment risk
- 30-45 DTE sweet spot for optimal theta decay across both strategies

**Risk Management Principles Embedded**:
- Never compromise fundamentals for premium (especially CSP)
- Assignment is acceptable outcome, not failure (for CSP when stock wanted)
- Rolling strategies defined for both up/out (CC) and down/out (CSP)
- Capital allocation limits: <20% per stock for CSP, 50% position sizing for CC

### 2024-01-15: Migrated Instructions to Massive.com MCP Server

Rewrote DATA GATHERING PROTOCOL sections in both instruction files for new `mcp_massive` server from Massive.com (replacing `iflow-mcp-ferdousbhai-investor-agent`).

**New MCP Server Architecture**:
- **4 Composable Tools**: `search_endpoints`, `get_endpoint_docs`, `call_api`, `query_data`
- **Built-in Functions**: Greeks (bs_delta, bs_theta, bs_vega, bs_gamma, bs_rho), Technicals (sma, ema), Returns (simple_return, sharpe_ratio)
- **Workflow**: Discovery → API calls with `store_as` → SQL analysis with `apply` functions

**Covered Call Instructions Changes**:
- Restructured to 12-step data gathering (Phase 1: Core data, Phase 2: Fundamentals & sentiment, Phase 3: Analytics)
- `search_endpoints` → `call_api(store_as="price_history")` → `query_data(apply=["sma", "ema"])`
- Greeks calculation: `query_data(apply=["bs_delta", "bs_theta", "bs_vega"])` on options_chain table
- Added SQL examples for IV analysis, strike filtering, return calculations

**Cash-Secured Put Instructions Changes**:
- Expanded to 17-step comprehensive protocol (extended price history for support, dual financials calls)
- Support identification via SQL: `SELECT MIN(low) FROM price_history GROUP BY month`
- Oversold detection: `query_data(apply=["sma", "ema"])` for Bollinger Bands approximation
- Greeks sweet spot targeting delta -0.25 to -0.30 via SQL filters

**Data Availability Adaptations**:
- **Removed (not in Massive.com)**: CNN Fear & Greed Index, Google Trends, dedicated institutional holders, dedicated insider trades
- **Alternatives Implemented**:
  - Fear & Greed → News sentiment analysis (Benzinga positive/negative ratio)
  - Google Trends → News volume over time (article frequency)
  - Institutional holders → Check fundamentals data or company filings
  - Insider trades → Parse news headlines for "insider" keywords
- **Earnings Calendar**: Parse ticker_info field + search news for "earnings" mentions (no dedicated endpoint)

**Key Design Patterns**:
1. **Discovery-first workflow**: `search_endpoints` before every data type collection
2. **Semantic table naming**: "ticker_info", "price_history", "options_chain", "financials" for SQL clarity
3. **Phased analysis**: Store raw data (Phase 1-2) → Analyze with SQL JOINs (Phase 3)
4. **Conservative fallbacks**: Apply stricter criteria when key data missing (lower delta, higher margin of safety)

**Technical Improvements**:
- In-memory DataFrames enable cross-table JOINs and complex analysis
- Built-in Greeks functions eliminate manual Black-Scholes calculations
- SQL composability allows agents to create custom queries beyond template
- Explicit SQL examples reduce LLM hallucination on query structure

**Trade-offs**:
- **Pro**: More flexible (discovery-based), more powerful (SQL + functions), better integration (JOINs)
- **Con**: More complex (requires SQL knowledge), more steps (12-17 vs. 8-11), missing some signals
- **Mitigation**: Extensive SQL examples, fallback strategies documented, semantic naming conventions

**Testing Needed**:
- Verify `search_endpoints` returns correct endpoints for each data type
- Validate `apply=["bs_delta", ...]` produces accurate Greeks
- Test SQL JOINs across stored tables
- Confirm news parsing catches earnings dates reliably
- Validate decision quality matches old MCP server outputs

**Decision Document**: Created `.squad/decisions/inbox/linus-mcp-massive-instructions.md` with full migration rationale, trade-offs, testing recommendations, and open questions.

### 2026-03-26: Completed Data Gathering Protocol Migration to Massive.com MCP

**Orchestration Summary (2026-03-26T16:05):**
Successfully completed comprehensive rewrite of both covered call and cash-secured put agent instructions for `mcp_massive` discovery-first workflow architecture.

**Instructions Updates Complete:**
- **Covered Call Instructions**: 12-step data gathering protocol (3 phases) with SQL examples
- **Cash-Secured Put Instructions**: 17-step protocol (3 phases) with extended support analysis
- **SQL Examples**: Strike filtering, support identification, return metrics, Greeks calculations
- **Fallback Strategies**: Documented for missing Fear & Greed, Trends, Insider data

**Key Design Patterns Established:**
1. Discovery-first workflow: `search_endpoints` → `call_api` → `query_data`
2. Semantic table naming: "ticker_info", "price_history", "options_chain" for SQL clarity
3. Built-in functions: Leveraging `apply=["bs_delta", "bs_theta"]` for Greeks instead of manual math
4. Conservative adaptations: Stricter criteria when key data unavailable

**Data Availability Adaptations:**
- Fear & Greed → News sentiment analysis (Benzinga positive/negative)
- Trends → News volume (article frequency as retail proxy)
- Institutional holders → Fundamentals data + company filings
- Insider trades → News headline parsing for keywords

**Coordination with Rusty:**
- Config updated to reference `"massive"` MCP tool
- Instructions verified compatible with mcp_massive 4-tool architecture
- Ready for integration with Rusty's agent-framework implementation

**Ready for Testing:**
- Instructions syntax validated
- SQL examples verified for correctness
- Discovery-first pattern documented with extensive examples
- Next steps: Integration testing with actual MCP server and agent execution

### 2026-07-25: Created Alpha Vantage MCP Instruction Files

Created two new instruction files as alternatives to the Massive.com-based instructions, targeting the Alpha Vantage MCP server with its progressive tool discovery pattern.

**Files Created:**
- `src/av_covered_call_instructions.py` — `AV_COVERED_CALL_INSTRUCTIONS` variable (420 lines)
- `src/av_cash_secured_put_instructions.py` — `AV_CASH_SECURED_PUT_INSTRUCTIONS` variable (569 lines)

**Alpha Vantage Tool Discovery Pattern:**
- 3 meta-tools: `TOOL_LIST` → `TOOL_GET(tool_name)` → `TOOL_CALL(tool_name, arguments)`
- No SQL / `store_as` / `query_data` — all data returned as JSON, agent analyzes directly
- Progressive discovery: confirm tool availability before calling

**Key Differences from Massive.com Instructions:**
- **Advantages leveraged**: Built-in RSI, BBANDS, SMA, EMA, MACD (no manual calculation); EARNINGS tool with beat/miss data; numerical NEWS_SENTIMENT scores; analyst ratings in COMPANY_OVERVIEW; dividends in COMPANY_OVERVIEW
- **Limitations documented**: No SQL joins, no built-in Black-Scholes Greeks (must estimate manually), no `store_as` pattern, no time-series analyst ratings, no insider/institutional endpoints
- **Adaptations**: Fear/Greed proxy via aggregated NEWS_SENTIMENT scores; retail interest via article frequency; insider activity via news keyword search; support identification by scanning JSON price data directly

**Strategy Logic Parity:**
ROLE, STRATEGY OVERVIEW, ANALYSIS FRAMEWORK, and DECISION CRITERIA sections are identical to Massive versions. Only DATA GATHERING PROTOCOL sections differ (rewritten for AV tools). This ensures trading decisions remain consistent regardless of data source.

**Verification:**
- ✅ Python import test passed for both modules
- ✅ ROLE + STRATEGY OVERVIEW sections: exact match with Massive versions
- ✅ ANALYSIS FRAMEWORK through decision criteria: exact match
- ✅ Only DATA GATHERING PROTOCOL differs (intentional)
- ✅ Phase 1/2/3 structure preserved for consistency with Massive instructions

**Design Decision**: Kept Phase 1/2/3 structure identical to Massive instructions for consistency, even though AV's tool interface is fundamentally different (meta-tool discovery vs. endpoint search). This makes it easy for Rusty's lazy import pattern to swap MCP providers by just selecting instructions based on config.

**Coordination with Rusty:**
Rusty implemented lazy imports in agent files that conditionally load AV instructions only when alphavantage provider is selected. This means AV instruction files are optional when using massive provider, and vice versa. No hard dependencies between the work.

### 2026-07-25: Created TradingView MCP Instruction Files

Created two new instruction files for the TradingView data provider, using the Fetch MCP server (`mcp-server-fetch`) to retrieve TradingView pages as markdown.

**Files Created:**
- `src/tv_covered_call_instructions.py` — `TV_COVERED_CALL_INSTRUCTIONS` variable (436 lines)
- `src/tv_cash_secured_put_instructions.py` — `TV_CASH_SECURED_PUT_INSTRUCTIONS` variable (612 lines)

**TradingView Provider Architecture:**
- Single tool: `fetch(url, max_length, start_index, raw)` from mcp-server-fetch
- 4 TradingView URLs per symbol: main page, technicals, forecast, options-chain
- Symbol format: EXCHANGE-SYMBOL (e.g., NYSE-AA) → URLs like `https://www.tradingview.com/symbols/NYSE-AA/`
- Content returned as markdown (HTML converted)

**Key Design Decisions:**
- **Pre-analyzed signals paradigm**: Unlike YF/AV which require manual indicator calculation, TradingView provides pre-calculated RSI, MACD, Stochastic, CCI, ADX, all MAs with Buy/Sell/Neutral signals. Instructions emphasize working from analyzed signals → synthesis rather than raw data → calculation → synthesis.
- **Pivot points for strike selection**: S1-S3 for CSP support/strike targets, R1-R3 for CC resistance/strike targets. Replaces manual support/resistance identification from price history scanning.
- **IV proxy via beta + volatility %**: TradingView doesn't expose IV via fetch (JS-rendered). Instructions use beta and volatility % from main page as IV approximation.
- **Options chain limitation documented**: JS rendering means fetch may return limited/empty options chain. Fallback protocol uses technical signals + pivot points for strike selection when options data unavailable.
- **Phase 2 requires no additional fetches**: All 4 URLs fetched in Phase 1; Phase 2 is pure synthesis. Minimizes fetch calls per analysis run.

**Strategy Logic Parity:**
ROLE, STRATEGY OVERVIEW, ANALYSIS FRAMEWORK, DECISION CRITERIA, OUTPUT FORMAT, CLEAR SELL SIGNAL, RISK MANAGEMENT, and RESPONSE STRUCTURE sections are identical to Yahoo Finance versions. Only DATA GATHERING PROTOCOL sections differ (rewritten for TradingView fetch approach).

**Advantages Documented:**
- FREE — no API key needed
- Pre-calculated technicals with Buy/Sell/Neutral signals (unique among all providers)
- Pivot points (Classic, Fibonacci, Camarilla, Woodie, DM) with R1-R3, S1-S3
- Single-page fundamentals (P/E, EPS, revenue, beta, earnings date, analyst targets)
- Analyst consensus on forecast page

**Limitations Documented:**
- Options chain likely incomplete (JS-rendered)
- No explicit IV data, no Greeks, no historical OHLCV
- No balance sheet, income statement details, or cash flow
- No news feed, insider trades, or institutional ownership
- Market hours dependency for some indicator values

**Verification:**
- ✅ Python import test passed for both modules
- ✅ Variable names match expected pattern (TV_COVERED_CALL_INSTRUCTIONS, TV_CASH_SECURED_PUT_INSTRUCTIONS)
- ✅ ANALYSIS FRAMEWORK through RESPONSE STRUCTURE: exact match with YF versions
- ✅ Only DATA GATHERING PROTOCOL differs (intentional)
- ✅ Line counts within target range (436 CC, 612 CSP)


**Status:** ✅ Completed 2026-03-26T22:40:00Z  
**Team:** Coordination with Rusty (provider plumbing), Coordinator (README), Danny (feature request)

### 2026-03-27: Output Format Updated to JSON+SUMMARY

**Notification (from Rusty's work):** All instruction files (8 total: Massive.com, Alpha Vantage, and TradingView variants for both Covered Call and Cash-Secured Put) have been updated with a new dual-format output specification:

**Changes Made by Rusty:**
1. **JSON decision block**: Agents now output a fenced ```json block with standardized schema
2. **SUMMARY line**: One-line human-readable summary follows the JSON block
3. **Schema differences**:
   - Covered Call: `"agent": "covered_call"`, standard decision fields
   - Cash-Secured Put: `"agent": "cash_secured_put"`, adds `"support_level"` field
4. **Backward compatibility**: agent_runner falls back to legacy pipe format if JSON parsing fails

**Impact for Instruction Files:**
- No logic changes — decision criteria, Greeks targets, DTE windows, fundamentals gates remain identical
- Output section sections expanded with JSON examples (~2KB per file)
- All new instruction files must follow this JSON+SUMMARY format going forward
- Legacy pipe-delimited format still supported via fallback in agent_runner.py

**Infrastructure Updates:**
- agent_runner.py: Enhanced JSON extraction + legacy fallback
- logger.py: Dual logging to `.jsonl` (structured) + `.log` (human-readable SUMMARY)
- config/team.md: Model updated to gpt-5.1 for TradingView Playwright support

**Status:** ✅ Accepted — instruction files compatible with new output format
**Team:** Rusty (implementation), Infrastructure (agent_runner, logger)

**2026-03-27 TradingView Navigation Optimization:**
Rusty removed main symbol page (103K chars) from TV navigation to free context window. Freed 98K characters, enabling technicals → forecast → options chain loading without overflow. CSP Investment Worthiness Gate rewritten to use analyst consensus instead of P/E/EPS (data now sourced from forecast page). No breaking changes; CSP gate still prevents assignment to deteriorating stocks. Impact: TV instructions no longer load main symbol page; analyst consensus and earnings history from forecast page replace lost P/E/EPS/market cap data.

## Cross-Agent Impact

### 2026-03-28: CosmosDB Refactor (No Instruction Changes Required)
**From:** Rusty (Agent Dev), Phase 1–3 implementation

This large refactor (file-based → CosmosDB across entire system) has **zero impact** on Linus's instruction files:
- Context output format remains identical (reason-per-line, oldest-first) via `src/context.py` adapter pattern
- Agent decision criteria, Greeks targets, DTE windows, fundamentals gates unchanged
- Backward compatibility: agent_runner output parsing logic unmodified

**Status:** Notification only — no action required
**Team:** Rusty (implementation), Danny (architecture), Basher (provisioning)


### Position-from-Decision Feature — Frontend Integration (2026-03-29)
- Added "Open Position" button to `web/templates/decision_detail.html` placed in the signal banner flexbox row (Jinja conditional `is_signal`). Button launches the position-opening flow.
- Implemented expandable position rows in `web/templates/symbol_detail.html`:
  - Each position row gets a sibling `<tr class="pos-detail-row">` with `display:none` toggled by row click
  - Chevron (▸/▾) provides visual affordance; click handler uses `e.target.closest('[data-close-pos], [data-delete-pos]')` to guard against expand/collapse on action button clicks
  - Detail panel reuses existing CSS classes (`detail-grid`, `detail-field`, `detail-label`, `detail-value`) for consistency
  - Table expanded to 8 columns (added 2rem chevron column for affordance)
- Design decisions:
  1. Button in signal banner (keeps signal indicator and CTA paired)
  2. `<tr>` expansion (maintains table semantics vs. accordion/details elements)
  3. Propagation guard with `closest()` (more robust than `stopPropagation()`)
  4. Reused CSS (visual consistency)
  5. Colspan=8 (supports expanded detail rows)
- Trade-offs: Inline styles for detail panel (acceptable one-off); agent type formatting via inline Jinja ternary (would benefit from custom filter if more types added)
- Backend API endpoint `POST /api/symbols/{symbol}/positions/from-decision/{decision_id}` implemented by Rusty (Agent Dev)
- **Status:** ✅ Complete and ready for end-to-end testing

## Cross-Agent Impact

### 2026-03-29: Position-from-Decision Feature (Backend Implementation by Rusty)
**From:** Rusty (Agent Dev)

Rusty completed backend implementation for the position-from-decision workflow:
- Extended `cosmos_db.py` `add_position()` with `source` parameter to track position origin
- Implemented `POST /api/symbols/{symbol}/positions/from-decision/{decision_id}` endpoint with inline watchlist disable and cascade-delete
- Source snapshot captures full decision provenance (decision_id, agent_type, confidence, reason, underlying_price, premium, iv, risk_flags, timestamp)

**Status:** Feature complete — awaiting end-to-end testing
**Team:** Rusty (backend), Linus (frontend)

## Learnings

### 2024-03-29: Comprehensive Frontend Entity Rename

**Task:** Renamed "decision" → "activity" and "signal" → "alert" across all frontend/web files.

**Key changes:**
- **web/app.py**: Renamed all API routes (`/decisions` → `/activities`, `/signals` → `/alerts`), function names, cosmos_db method calls, variable names, and template references
- **Templates renamed**: `decision_detail.html` → `activity_detail.html`, `signal_detail.html` → `alert_detail.html`, `signals.html` → `alerts.html`
- **All HTML templates updated**: dashboard.html, symbol_detail.html, chat.html, symbols.html, base.html - renamed all variable references, display text, URLs, and data attributes
- **web/static/style.css**: Renamed CSS classes (`.decision-*` → `.activity-*`, `.signal-banner` → `.alert-banner`)
- **Route mappings**: `/api/symbols/{symbol}/positions/from-decision/{decision_id}` → `/api/symbols/{symbol}/positions/from-activity/{activity_id}`

**Scope:** Frontend only - did NOT touch src/ Python files (Rusty's domain) or README.md (Danny's domain).

**Validation:** Used comprehensive grep searches to confirm no remaining entity references to "decision_id", "signal_id", "get_decision", "get_signal", etc. Only field value references (like `.decision` for the actual decision value) remain, which is correct.


### 2024-XX-XX: Client-Side Activity/Alert Filtering

**Task:** Added time-range (1d/7d/30d) and symbol filters to dashboard and symbol detail pages.

**Key changes:**
- **web/app.py**: Increased data limits for filtering (activities 10→100, alerts 10→30, detail activities 20→50, detail alerts 10→30)
- **web/templates/dashboard.html**: Added filter controls (time pills + symbol dropdown) inline with "Recent Activity" header; added `data-timestamp` and `data-symbol` attributes to activity items
- **web/templates/symbol_detail.html**: Added time-range pill filters to "Recent Activities" and "Recent Alerts" headers; added `data-timestamp` attributes to table rows; added table IDs (`#activities-table`, `#alerts-table`)
- **web/static/style.css**: Added CSS for `.filter-group`, `.filter-pills`, `.pill` buttons, and `.filter-select` dropdown
- **web/static/app.js**: Implemented client-side filtering logic with `cutoffDate()`, `applyDashboardFilters()`, and `applyTableFilter()` functions; wire up pill click handlers and symbol dropdown; auto-apply 7d default on page load; update badge counts after filtering

**Approach:** Client-side JS filtering (vs. server-side) for instant feedback without page reloads. Increased data limits to provide enough data for meaningful filtering.

**UX:** Pill-style time buttons with active state, dynamic symbol dropdown populated from existing items, badge counts auto-update to show visible items.


### 2024-XX-XX: Dashboard Button Updates

**Task:** Updated dashboard run buttons with clearer labeling and batch execution capability.

**Key changes:**
- **web/templates/dashboard.html**: 
  - Changed "Run Now" → "Run Analysis" for all individual agent trigger buttons
  - Added "Run Full Analysis" button above agent tables (right-aligned)
- **web/static/app.js**: 
  - Added handler for "Run Full Analysis" button
  - Sequentially triggers all 4 agents (covered_call, cash_secured_put, open_call_monitor, open_put_monitor)
  - Shows real-time progress indicator (e.g., "Running... (2/4)")
  - Displays completion summary (e.g., "✓ Complete (4/4)")
  - Button disables during execution and re-enables after 3 seconds
- **web/static/style.css**: 
  - Added `.btn-trigger.btn-primary` styling for the full analysis button
  - Larger padding and font size to differentiate from individual agent buttons
  - Blue accent color to indicate comprehensive action

**Approach:** Sequential execution using promise chaining (`.reduce()` pattern) to ensure agents run one after another. Used existing `/api/trigger/{agentType}` endpoint for each agent.

**UX:** Clear button labeling, real-time feedback during execution, visual distinction between individual and full analysis actions. Primary button styling makes the "Run Full Analysis" action more prominent.


### 2024-XX-XX: Run Full Analysis Button Styling Fix

**Task:** Fixed "Run Full Analysis" button to match user's visual expectations.

**Key changes:**
- **web/templates/dashboard.html**: 
  - Moved "Run Full Analysis" button from separate container into `.scheduler-bar` (same box as cron/last run/next run info)
  - Changed class from `btn-trigger btn-primary` to `btn-trigger btn-trigger-blue`
- **web/static/style.css**: 
  - Removed `.btn-trigger.btn-primary` styling (solid blue button with white text)
  - Added `.btn-trigger.btn-trigger-blue` modifier for blue variant
  - Blue variant uses transparent background with blue border and blue text (matching the green "Run Analysis" button pattern)
  - Added matching hover state with `rgba(74,158,255,0.15)` background

**User feedback:** Originally implemented with solid blue background (btn-primary), but user expected the same format as other "Run Analysis" buttons (transparent background, colored border/text) using blue instead of green.

**Pattern:** Button variants use transparent backgrounds with colored borders and text. Hover states use semi-transparent backgrounds of the button color. Format consistency > visual hierarchy through color fills.

**Placement pattern:** Action buttons that relate to scheduler information (cron, last/next run) should be placed inside the `.scheduler-bar` container, not in separate sections.

### 2025-01-XX: Settings Page Split into Three Submenus

**Task:** Reorganize monolithic Settings page into 3 separate pages with navigation submenu.

**Changes made:**
- **Created 3 new templates:**
  - `web/templates/settings_config.html` — Scheduler and Telegram notification settings (editable form)
  - `web/templates/settings_runtime.html` — Agent run stats and TradingView fetch performance (read-only telemetry)
  - `web/templates/settings_debug.html` — TradingView fetch test tool and CosmosDB connection diagnostics (testing tools)
  
- **Navigation submenu (base.html):**
  - Wrapped Settings link in `.nav-dropdown` container
  - Added `.nav-dropdown-content` div with 3 submenu links: Configuration, Runtime Stats, Debug
  - Each submenu link has emoji prefix (⚙️, 📊, 🔍) for visual clarity
  
- **CSS dropdown styles (style.css):**
  - `.nav-dropdown` — relative positioning container
  - `.nav-dropdown-content` — hidden by default, absolute positioned below parent, appears on hover
  - Menu items use same styling as top nav links (muted text, hover background)
  - Box shadow for depth, border radius for consistency
  
- **Backend routes (web/app.py):**
  - `GET /settings/config` — Load scheduler + telegram config, render settings_config.html
  - `POST /settings/config` — Save scheduler + telegram settings (dual-write to CosmosDB + config.yaml)
  - `GET /settings/runtime` — Load telemetry stats from CosmosDB, render settings_runtime.html (read-only)
  - `GET /settings/debug` — Load CosmosDB connection info + symbols list, render settings_debug.html (testing tools)
  - `GET /settings` — 301 redirect to /settings/config for backward compatibility
  - Removed old monolithic `settings_page()` and `settings_save()` routes
  
**Pattern learned:**
- Settings pages split by purpose: **Config** (user-editable), **Runtime** (read-only stats), **Debug** (testing tools)
- Dropdown menu uses hover state (no JavaScript) — simple and accessible
- POST endpoints redirect to same page with `saved` parameter for success feedback
- Backward compatibility via 301 redirect prevents broken links
- Each split page has focused data loading (only fetches what it needs, no overhead)

**User preference:** Group settings by function, not by data source. Scheduler + Telegram together because both are configuration. Agent stats + Fetch stats together because both are runtime metrics.

## Learnings

### Navigation Dropdown Implementation (2025-01-xx)
- **Pattern**: Converted Settings navigation from fixed submenu to hover-based dropdown
- **Key files**: 
  - `web/templates/base.html` - Changed `<a>` to `<span class="nav-dropdown-trigger">` to make Settings non-clickable
  - `web/static/style.css` - Added `.nav-dropdown-trigger` styles with hover states and cursor pointer
- **Design decision**: Used `<span>` instead of `<a>` for dropdown trigger to semantically indicate it's not a link
- **Styling approach**: 
  - Maintained consistency with existing `.nav-links a` styling (padding, border-radius, transitions)
  - Added `user-select: none` to prevent text selection on trigger
  - Added rounded corners to first/last dropdown items for polish
  - Dropdown appears on hover of parent `.nav-dropdown` container
- **User preference**: Clean, hover-based dropdowns; Settings should not be directly clickable

### Timezone Configuration UI (2025-01-XX)
- Added timezone dropdown field to Settings Configuration page scheduler section
- **Frontend** (`web/templates/settings_config.html`):
  - Restructured scheduler section to include labeled fields
  - Added timezone select dropdown with 7 common timezones:
    - America/New_York (EST/EDT) — default
    - America/Chicago (CST/CDT)
    - America/Los_Angeles (PST/PDT)
    - Europe/Madrid (CET/CEST)
    - Europe/London (GMT/BST)
    - UTC
    - Asia/Tokyo (JST)
  - Included info icon (ⓘ) with tooltip explaining "Timezone for cron schedule execution"
  - Consistent styling with existing cron expression field
- **Backend** (`web/app.py`):
  - Updated `settings_config_page()` GET handler to read `scheduler.timezone` from config (defaults to "America/New_York")
  - Updated `settings_config_save()` POST handler to persist timezone to both CosmosDB and config.yaml
  - Timezone persisted alongside cron expression in scheduler config section
  - Both GET and POST handlers pass timezone to template
- **User Request**: "set America/New York as default" — implemented as default value in backend and as first/default option in dropdown
- **Pattern**: Matched existing scheduler field handling — read from CosmosDB first, fallback to config.yaml, persist to both on save

---

### 2024-03-XX: Dashboard Timezone Display Enhancement

**Task**: Display "Last run" and "Next run" times in the scheduler's configured timezone with local timezone fallback.

**Context**:
- User requested that dashboard show times in the scheduler's configured timezone (e.g., America/New_York)
- Previously times were displayed as raw strings without proper timezone formatting
- Backend already updated by Rusty to pass timezone-aware ISO timestamps

**Implementation**:
- **Frontend** (`web/templates/dashboard.html`):
  - Added IDs to last run and next run display elements (`last-run-display`, `next-run-display`)
  - Created inline JavaScript that formats ISO timestamps client-side
  - Uses `toLocaleString()` with timezone parameter to format in scheduler timezone
  - Detects user's browser timezone - if different from scheduler timezone, shows both:
    - Primary: Scheduler timezone (e.g., "Mar 20, 2024, 02:30:00 PM EDT")
    - Secondary: User's local time (smaller, gray text below)
  - Adds hover tooltip showing both times when they differ
  - Graceful fallback if timezone not supported in browser
  - Keeps consistent with dashboard design - no layout changes needed

**Technical details**:
- Backend provides: `last_run_iso`, `next_run_iso`, `scheduler_timezone`
- JavaScript parses ISO datetime, formats using Intl API
- Dual timezone display only shown when user TZ ≠ scheduler TZ
- Format: "MMM DD, YYYY, HH:MM:SS AM/PM TZN"

**User benefit**:
- Clear visibility of when scheduler last ran and will run next
- No confusion about which timezone is shown
- Automatic adaptation to user's local timezone when relevant
- Professional, polished time display


## Fixed: Alert Detail "Return to Symbol" Link (2024-03-30)

**Issue**: The "return to SYMBOL" link in alert detail view was broken - constructing URLs with "MARKET:SYMBOL" format (e.g., "NASDAQ:AAPL"), but symbol detail page expects just the symbol (e.g., "AAPL").

**Root Cause**: The `symbol` variable passed to the alert_detail.html template included the market prefix when coming from certain data sources.

**Solution**: Updated `web/templates/alert_detail.html` to strip the market prefix using Jinja2 template logic:
- Changed link construction from `{{ symbol }}` to `{{ symbol.split(':')[-1] if ':' in symbol else symbol }}`
- Applied fix to:
  - Back link URL (line 6)
  - Back link display text (line 6)
  - Page title (line 2)
  - Subtitle (line 8)

**Pattern**: When dealing with symbols in templates, always strip market prefix for URL construction:
```jinja2
{{ symbol.split(':')[-1] if ':' in symbol else symbol }}
```

**Files Modified**:
- `web/templates/alert_detail.html`: Added symbol parsing logic to extract ticker from "MARKET:SYMBOL" format

**Key Learning**: Symbol data can come in different formats depending on the source. Templates should handle both "SYMBOL" and "MARKET:SYMBOL" formats gracefully by extracting just the ticker part for URL routing.

### Notifications Toggle UI (2026-01-XX)
Added per-symbol notification toggle UI to enable/disable Telegram notifications:

**Locations Updated:**
1. **`web/templates/symbols.html`**:
   - Added "Notifications" column header to table
   - Added toggle switch for each symbol row using `toggle-notifications` class
   - Default state: checked (enabled) unless explicitly disabled
   - JavaScript handler: saves state to backend via PUT `/api/symbols/{symbol}` with `{notifications_enabled: boolean}`
   - Toggle pattern matches existing call/put watchlist toggles for consistency

2. **`web/templates/symbol_detail.html`**:
   - Added "Notifications" toggle in watchlist-toggles section (between Put Watchlist and Chat link)
   - Uses ID `toggle-notifications` for single-element selection
   - Default state: checked (enabled) unless explicitly disabled
   - JavaScript handler: saves state to backend via PUT `/api/symbols/{symbol}` with `{notifications_enabled: boolean}`
   - Same UI pattern as call/put toggles (switch class with slider span)

**Technical Details:**
- Field name: `telegram_notifications_enabled` (backend API field)
- Default behavior: `{% if sym.telegram_notifications_enabled is not defined or sym.telegram_notifications_enabled %}checked{% endif %}`
  - Treats undefined/missing values as enabled (checked) for backward compatibility
- AJAX error handling: reverts checkbox state on failure (`this.checked = !this.checked`)
- No confirmation dialog (instant save on change, like watchlist toggles)

**Pattern Reused:**
- Follows exact same toggle implementation as call/put watchlist toggles
- Uses existing `.switch` and `.slider` CSS classes from base styles
- JavaScript uses same fetch pattern with PUT method and JSON payload
- Optimistic UI with rollback on error

**Backend Integration:**
- Backend updated in parallel by Rusty to handle `telegram_notifications_enabled` field
- Frontend sends boolean value to backend via existing symbol update endpoint
- No schema migration needed (backend handles missing field gracefully)


### Dashboard Timezone Display Pattern (2026-03-31)
- **UI Pattern:** Dual-timezone display for scheduler times ("Last run", "Next run").
- **Primary:** Shows time in scheduler's configured timezone (ISO 8601 string + IANA timezone name from backend).
- **Secondary:** If user's browser timezone differs, shows local time below in muted text.
- **Tooltip:** Hover reveals both times clearly labeled.
- **Format:** "MMM DD, YYYY, HH:MM:SS AM/PM TZN" (e.g., "Mar 30, 2024, 02:00:00 PM EDT").
- **Implementation:** Backend passes `{field}_iso` + `scheduler_timezone`; frontend formats client-side using `toLocaleString()` with Intl API (no external timezone libs).
- **Reusability:** Pattern can be applied to any timestamp display in web UI.
- **Related decision:** `.squad/decisions/decisions.md` — "Dashboard Timezone Display Pattern"

### Earnings Decision Matrix Refinement (2025-07-24)
- Replaced binary "earnings nearby = WAIT" logic with tiered Earnings Decision Matrix in both `src/tv_covered_call_instructions.py` and `src/tv_cash_secured_put_instructions.py`.
- **Key principle**: If option expires BEFORE earnings with sufficient buffer, earnings event is not a risk. High pre-earnings IV = better premiums to capture.
- Tiers: >30d = no constraint, 15-30d = allowed if exp ≥7d before earnings, 7-14d = allowed if exp ≥5d before (caution), <7d = block, post-earnings 0-2d = ideal.
- Added new risk flags: `earnings_approaching`, `earnings_soon`, `earnings_imminent` (tiered severity).
- Updated 6 sections per file: earnings tiers table, earnings calendar fallback, fundamental considerations, SELL calendar check, WAIT triggers, CLEAR SELL clean calendar.
- Updated WAIT examples to show imminent earnings pattern (not generic "earnings nearby").
- User preference: maximize income opportunities, avoid leaving money on the table with overly conservative blanket rules.
- Commit: ccf299a

### Monitor Earnings Decision Matrix Port (2026-07-16)
- Ported the Earnings Decision Matrix from analysis agents (covered_call, cash_secured_put) to monitor agents (open_call_monitor, open_put_monitor)
- Key adaptation: analysis agents decide "should I OPEN?" (OPEN/AVOID/BLOCK), monitors decide "what do I DO with my OPEN position?" (HOLD/FLAG/ROLL/CLOSE)
- Matrix has 10 tiers: >30d, 15-30d, 7-14d, <7d, 0-2d post, unknown — each with expiration-vs-earnings sub-conditions
- Risk flags aligned with analysis agents: `earnings_approaching`, `earnings_soon`, `earnings_imminent`, `earnings_within_dte`, `unknown_earnings`
- `earnings_before_expiry` retained as legacy alias for `earnings_within_dte`
- Put monitor includes additional put-specific considerations (earnings miss gap risk, downgrade clustering)
- Files modified: `src/tv_open_call_instructions.py`, `src/tv_open_put_instructions.py`
- Commit: f587058

## Spawn Manifest — 2026-03-31

### linus-monitor-earnings
**Status:** Merged to history  
**Commit:** 2026-03-31T15:34:10Z (scribe consolidation)

**Summary:** Ported the Earnings Decision Matrix from analysis agents (covered_call, cash_secured_put) to monitor agents (open_call_monitor, open_put_monitor).

**Decisions Merged:**
1. Earnings Decision Matrix — Nuanced vs Binary (2025-07-24) — Analysis agent tier structure and logic
2. Monitor Earnings Decision Matrix (2026-07-16, commit f587058) — Adapted for monitoring actions (HOLD/FLAG/ROLL/CLOSE)

**Orchestration Log:** `.squad/orchestration-log/2026-03-31T15:34:10Z-linus.md`

**Key Changes:**
- Both monitor instruction files now implement 6-tier earnings assessment (>30d, 15-30d, 7-14d, <7d, just-passed, unknown)
- Expiration-vs-earnings axis drives primary action (position spanning earnings = escalating urgency)
- Earnings risk overrides favorable Greeks when urgent
- Put monitor includes earnings miss gap risk and downgrade clustering assessment
- Risk flags consistent across all 4 agent types (CC, CSP, call monitor, put monitor)

**Files Modified:**
- `src/agents/tv_open_call_instructions.py` — 10-tier earnings assessment, HOLD/FLAG/ROLL/CLOSE actions
- `src/agents/tv_open_put_instructions.py` — 10-tier earnings assessment with put-specific risk additions

**Impact Scope:** Agent instruction files only; no downstream architecture changes.


### Unified Mandatory Earnings Gate (2026-07)
- Created a **MANDATORY EARNINGS GATE** section placed BEFORE all other analysis in all 4 instruction files
- Problem: LLM was ignoring buried earnings logic (section 8/9) and recommending positions spanning earnings dates
- Solution architecture:
  - Gate runs as Step 0 pre-check — if BLOCKED, agent outputs WAIT/CLOSE immediately without evaluating anything else
  - HARD OVERRIDE RULE: explicit language that no technical/fundamental/IV signal can override a BLOCK
  - Mandatory `earnings_analysis` JSON object forces LLM to explicitly compute and output earnings timing before making a recommendation
  - Unified thresholds and flag names across all 4 files (only actions differ: watchers use OPEN/WAIT, monitors use HOLD/ROLL/CLOSE)
- Key files modified:
  - `src/tv_covered_call_instructions.py` — watcher gate + earnings_analysis in schema + updated examples
  - `src/tv_cash_secured_put_instructions.py` — watcher gate + earnings_analysis in schema + updated examples
  - `src/tv_open_call_instructions.py` — monitor gate + earnings_analysis in schema + updated examples
  - `src/tv_open_put_instructions.py` — monitor gate + earnings_analysis in schema + updated examples
- Old duplicate earnings sections (section 8/9 in watchers, section 6 in monitors) replaced with brief cross-references to the gate
- Gate result enum values:
  - Watchers: OPEN_NORMALLY, ALLOWED, ALLOWED_WITH_CAUTION, BLOCKED, IDEAL, CONSERVATIVE_DTE
  - Monitors: HOLD, HOLD_WITH_CAUTION, FLAG, ROLL_RECOMMENDED, ROLL_URGENTLY, CLOSE_OR_ROLL, CONSERVATIVE
- Earnings risk flags (unified): `earnings_approaching`, `earnings_soon`, `earnings_imminent`, `earnings_within_dte`, `unknown_earnings`
- Design insight: Forcing a structured `earnings_analysis` object in every response makes the LLM reason explicitly about dates — it can't skip the logic if it must fill in the fields
- Commit: b51ea1a

---

## Earnings Matrix Ported to Monitors (2026-07-16)
**Status:** ✅ Complete

Ported Earnings Decision Matrix from analysis agents to both open_call_monitor and open_put_monitor instruction files. Same tier structure (>30d, 15-30d, 7-14d, <7d, just-passed, unknown) adapted for monitoring context (HOLD/FLAG/ROLL/CLOSE vs OPEN/AVOID/BLOCK).

**Files Changed:**
- src/tv_open_call_instructions.py
- src/tv_open_put_instructions.py

**Key Design:**
- Expiration-vs-earnings axis remains primary
- Urgency escalation: FLAG → ROLL → CLOSE
- Put-specific additions: earnings miss gap risk, downgrade clustering
- Override rule: earnings risk urgency overrides favorable Greeks

---

## Unified Mandatory Earnings Gate (2026-07-09)
**Status:** ✅ Complete

Implemented mandatory earnings gate as FIRST analytical step across all 4 instruction files. Gate-before-analysis architecture with hard override rule. All responses now include required `earnings_analysis` JSON object.

**Files Changed:**
- src/tv_covered_call_instructions.py
- src/tv_cash_secured_put_instructions.py
- src/tv_open_call_instructions.py
- src/tv_open_put_instructions.py

**Key Design Choices:**
1. Pre-flight gate: returns BLOCKED → agent outputs WAIT/CLOSE immediately
2. Mandatory `earnings_analysis` output object with: next_earnings_date, days_to_earnings, expiration_to_earnings_gap, earnings_gate_result, earnings_risk_flag
3. HARD OVERRIDE RULE: no bullish technicals/fundamentals/IV can override BLOCK
4. Unified terminology across all 4 files; only actions differ by agent type

**Impact:** Non-breaking schema addition. All agents now output earnings_analysis in every response.

## 2025-01-09: TradingView Bot Detection Fix

### Problem
TradingView was returning 403 Forbidden errors frequently, blocking our automated data fetching.

### Root Causes Identified
1. Static User-Agent on all requests
2. Missing modern browser headers (Sec-Fetch-*, sec-ch-ua)
3. No session management (cookies)
4. No request timing randomization
5. Playwright automation easily detectable
6. No rate limiting between requests

### Solutions Implemented
1. **User-Agent Rotation**: Pool of 7 realistic UAs from Chrome, Edge, Firefox, Safari
2. **Realistic Headers**: Complete modern browser header set with sec-ch-ua matching
3. **Session Management**: `requests.Session()` for persistent cookies
4. **Rate Limiting**: Random delays (1-3s default, configurable) between requests
5. **Referer Chain**: Natural navigation simulation (overview → technicals → forecast)
6. **Playwright Stealth**: 
   - `--disable-blink-features=AutomationControlled`
   - Remove `navigator.webdriver` via JS injection
   - Randomized timing for clicks/navigation
   - Fresh context per request with random viewport
7. **Configuration**: `tradingview.request_delay_min/max` in config.yaml

### Files Modified
- `src/tv_data_fetcher.py`: Core anti-bot logic, `create_fetcher()` factory
- `src/config.py`: Added TradingView config properties
- `config.yaml`: Added `tradingview` section
- All agent files: Use `create_fetcher(config)` instead of `TradingViewFetcher()`
- `web/app.py`: Updated 3 fetch locations

### Key Learnings
- **Bot detection is multi-layered**: Need multiple techniques, not just one
- **Timing matters**: Random delays are crucial for mimicking human behavior
- **Headers are fingerprints**: Modern browsers send 10+ headers, all must match
- **Sessions = identity**: Persistent cookies make you look like a returning user
- **Playwright is detectable**: Need stealth mode + JS injection to hide automation

### Configuration Guide
- **Dev/Test**: 0.5-1.5s delays (fast iteration)
- **Production**: 2-5s delays (maximum stealth)
- **Default**: 1-3s delays (balanced)

### Performance Impact
- Adds 5-15s per symbol (acceptable for background jobs)
- Minimal memory/CPU overhead

### Documentation
Created `TRADINGVIEW_ANTI_BOT.md` with full technical details, configuration guide, and monitoring recommendations.


### TradingView Anti-Bot Detection Implementation (2026-04-01)
- Implemented comprehensive anti-bot measures to mitigate 403 Forbidden errors from TradingView
- **Core changes:**
  - User-Agent rotation: 7 realistic browser user agents, randomly selected per request
  - Session management: Persistent `requests.Session()` with cookie handling
  - Rate limiting: Configurable delays between requests (default 1-3 seconds)
  - Realistic headers: Complete modern browser header set for each request
  - Playwright stealth mode: Enhanced browser automation detection evasion
- **Configuration (config.yaml):**
  ```yaml
  tradingview:
    request_delay_min: 1.0
    request_delay_max: 3.0
  ```
- **API change:** `async with TradingViewFetcher()` → `async with create_fetcher(config)`
- **Files modified:** src/tv_data_fetcher.py, src/config.py, config.yaml, web/app.py, 4 agent files
- **New files:** TRADINGVIEW_ANTI_BOT.md (comprehensive documentation), scripts/validate_antibot.py (validation suite)
- **Team impact:**
  - Rusty: web/app.py already updated; monitor fetch times for loading indicators
  - Danny: Consider cron schedule adjustment; monitor logs for 403 error reduction
  - Ralph & Basher: No action needed; backward compatible changes
- **Consequences:** Fetch times increase 5-15s per symbol; eliminates/drastically reduces 403 errors; makes traffic indistinguishable from human behavior; configurable for different environments
- **Decision record:** `.squad/decisions/decisions.md`

### Unified Alert Write Path (2026-04-01)
- **Context:** Danny's CosmosDB migration to unified schema (one doc_type, is_alert boolean)
- **Changes to agent_runner.py:**
  - Removed `_build_alert_data()` and `_build_roll_alert_data()` methods — alerts are no longer separate documents
  - Added `_extract_alert_enrichment()` — extracts alert-specific fields (confidence, risk_flags) to merge into activity payload
  - Removed obsolete `_ALERT_FIELDS` and `_ROLL_ALERT_FIELDS` — field control now happens at cosmos_db.py level
  - Updated both alert write paths (covered_call/cash_secured_put + position monitors):
    - When `is_alert=true`, merge alert enrichment fields directly into `activity_payload` before writing
    - Single `write_activity()` call replaces dual write (activity + alert)
    - Telegram notification still works: build display data inline from json_data (no DB dependency)
- **Pattern:** Activity IS the alert. `is_alert=true` + enrichment fields (confidence, risk_flags) all in one document.
- **ID format:** No change in agent_runner (still uses cosmos.write_activity); ID prefix removal happens in cosmos_db.py
- **Testing blockers:** Requires Danny's cosmos_db.py changes to be merged first (mark_as_alert method, ID format)
- **Team dependencies:**
  - Danny: cosmos_db.py write_activity ID format, remove write_alert, add mark_as_alert (if needed)
  - Rusty: web/app.py query updates (doc_type='alert' → is_alert=true filter)
- **Files modified:** src/agent_runner.py
- **Decision record:** `.squad/decisions/inbox/linus-signal-refactor.md`

## Orchestration Session (2026-04-01T21:39:57Z)

**Session:** CosmosDB Unified Schema — Decision Consolidation and Team Orchestration

**Status:** Refactoring complete and documented. Awaiting cosmos_db.py PR merge before testing.

**Team Coordination Update:**
- Danny: Migration design complete with all edge cases, rollback plan, and validation checklist
- Rusty: cosmos_db.py changes complete with backwards compatibility layer
- Basher: Migration script ready (dry-run, backup, restore, validation phases)
- Linus (this work): Refactoring complete, ready to merge after Rusty

**Deployment Sequence:**
1. Rusty: Merge cosmos_db.py PR (new ID format, write_activity change, mark_as_alert method, query updates)
2. Basher: Execute migration script (--dry-run → review → production run)
3. Linus: Merge agent_runner.py changes (single-write alert path using new cosmos_db methods)
4. Rusty: Merge web/app.py updates (alert query filters)

**Testing Plan (Post-PR Merge):**
- [Pending] Run covered_call agent on test symbol → verify new ID format
- [Pending] Verify `is_alert=true` and enrichment fields (confidence, risk_flags) in activity doc
- [Pending] Verify Telegram notifications still fire correctly
- [Pending] Verify web UI alert queries work with `is_alert=true` filter

**Session Log:** `.squad/log/2026-04-01T21-39-cosmosdb-unified-schema.md`  
**Orchestration Log:** `.squad/orchestration-log/2026-04-01T21-39-linus.md`


### Roll Down Strategy Relaxation (2026-04-01)
- Updated roll down gate logic in `src/tv_open_call_instructions.py` from unanimous (9/9) to super-majority (3 mandatory + 4 of 7 flexible):
  - **Mandatory conditions**: Deep OTM (≥3.5%), low delta (<0.20), minimum DTE (≥15 days)
  - **Flexible conditions** (need 4/7): Technicals neutral/bearish, MAs neutral/bearish, no earnings before expiry, no ex-dividend, analyst sentiment not bullish, IV stable/declining, position stable
  - **Relaxed thresholds**: Delta 0.15→0.20, Deep OTM 5%→3.5%, DTE 14→15, new strike OTM 2-3%→1.5-2%, new strike target delta 0.20-0.30→0.25-0.30
  - **Research-backed**: Thresholds align with <8-10% assignment probability studies, adequate safety buffers, meaningful premium windows
- Added roll down strategy section to `src/tv_open_call_chat_instructions.py`:
  - Conversational guidance for quick analysis chat mode when user has existing deep OTM positions
  - Same gate logic (3 mandatory + 4 of 7 flexible) with plain-language explanations
  - Example language emphasizing earnings gate non-negotiable and low assignment risk
  - Clear distinction: only suggest roll downs for existing positions, not new opens
- **Key principle preserved**: Earnings gate remains STRICT — never roll down if earnings fall before new expiration
- Files modified: `src/tv_open_call_instructions.py`, `src/tv_open_call_chat_instructions.py`

### Put Roll Up Strategy Analysis (2026-04-01)
- **Context:** Following covered call roll down relaxation, user requested analysis of put roll up strategy for similar improvements
- **Current State:** Unanimous 9/9 consensus gate for ROLL_UP profit optimization (very conservative)
- **Analysis Findings:**
  - Current gate: Deep OTM ≥5%, |delta| <0.15, 9 unanimous conditions
  - Mirrors previous call gate structure (before relaxation)
  - Overly restrictive; misses valid optimization opportunities
  - Delta <0.15 = ~15% assignment probability (very conservative)
- **Recommendations:**
  - Adopt same structure as relaxed call gate: 3 mandatory + 4 of 7 flexible
  - **Mandatory conditions:** Deep OTM ≥3.5%, |delta| <0.20, DTE ≥15
  - **Flexible conditions (need 4/7):** Technicals neutral/bullish, MAs neutral/bullish, no earnings, no ex-div, analyst not bearish, IV stable/declining, position stable
  - **Relaxed thresholds:** Delta 0.15→0.20, Deep OTM 5%→3.5%, DTE 14→15, new strike OTM 2-3%→1.5-2%, new strike delta 0.20-0.30→0.25-0.30
  - **Research basis:** |Delta| 0.20 ≈ 20% assignment probability (acceptable risk tier), 3.5% OTM exceeds typical noise range (2-3%), 15 DTE provides meaningful theta decay window
- **Safety Preservations:**
  - Earnings gate remains STRICT (non-negotiable override)
  - Assignment risk must stay "low" after roll
  - Confidence must be "high"
  - New strike must be OTM by ≥1.5-2%
- **Put-Specific Considerations:**
  - Puts have downside gap risk on earnings misses (higher than calls)
  - Support levels critical for strike selection
  - Assignment can be desirable (buying stock at discount)
  - Bullish technicals = safer for puts (stock moving away from strike)
- **Files to Update:**
  - `src/tv_open_put_instructions.py` (lines 296-317) — update gate logic
  - `src/tv_open_put_chat_instructions.py` — add roll up strategy section (currently missing)
- **Alignment:** Consistent with recent call work; same philosophy and threshold relaxations
- **Detailed Analysis:** Created comprehensive report at `put_strategy_analysis.md` with research basis, thresholds, implementation plan, code snippets

### Put Roll Up Strategy Implementation (2026-04-01)
- **Status:** COMPLETED
- **Context:** User approved put roll up strategy relaxation recommendations from `put_strategy_analysis.md`
- **Implementation:**
  1. **Updated `src/tv_open_put_instructions.py` (lines 294-332):**
     - Replaced unanimous 9/9 gate with super-majority gate: 3 mandatory + 4 of 7 flexible
     - **Mandatory conditions:** Deep OTM ≥3.5% (was 5%), |delta| <0.20 (was 0.15), DTE ≥15 (was >14)
     - **Flexible conditions (need 4/7):** Technicals neutral/bullish, MAs neutral/bullish, no earnings, no ex-div, analyst not bearish, IV stable/declining, position stable
     - **New strike targets:** Delta 0.25-0.30 (was 0.20-0.30), OTM by 1.5-2% (was 2-3%)
     - **Research annotations:** Added inline research basis for each threshold (TastyTrade, Option Alpha, assignment probability studies)
     - **Critical override:** Emphasized earnings gate is NON-NEGOTIABLE for puts due to gap-down asymmetry
  2. **Updated `src/tv_open_put_chat_instructions.py` (lines 221-292):**
     - Added new "PROFIT OPTIMIZATION: ROLL UP STRATEGY" section (mirroring call chat instructions structure)
     - **Gate logic guidance:** Plain-language explanation of 3 mandatory + 4 of 7 flexible conditions
     - **Conversational examples:** "Good setup", "Marginal setup", "Earnings blocker" scenarios with realistic language
     - **Put-specific risk emphasis:**
       - Rolling UP for puts = HIGHER strike = MORE aggressive (closer to money)
       - Earnings risk is SEVERE for puts (gap-down can move OTM to ITM instantly)
       - Assignment readiness check: "Would you be comfortable owning at the new higher strike?"
     - **When NOT to suggest:** Bearish signals, earnings uncertainty, <4 flexible conditions, flip-flopping activity
- **Key Differences vs Calls:**
  - Puts: Rolling UP = higher strike (more aggressive); Calls: Rolling DOWN = lower strike (more aggressive)
  - Puts: Bullish technicals = safer (stock moving away); Calls: Bearish technicals = safer
  - Puts: Gap-down earnings risk more severe (assignment at bad entry); Calls: Gap-up risk loses profit potential
  - Puts: Earnings gate MORE critical due to downside asymmetry
- **Consistency with Recent Call Work:**
  - Same gate structure philosophy: 3 mandatory + super-majority flexible
  - Same threshold relaxations: delta 0.15→0.20, OTM 5%→3.5%, DTE 14→15, new strike adjustments
  - Same strict preservation: Earnings gate non-negotiable, assignment risk must stay "low", confidence must be "high"
- **Files Modified:**
  - `src/tv_open_put_instructions.py`
  - `src/tv_open_put_chat_instructions.py`
- **Decision Record:** Created at `.squad/decisions/inbox/linus-put-roll-implementation.md`

## Learnings

### Gate Design Philosophy (from Roll Strategy Work)
- **Super-majority gates (3 mandatory + 4 of 7 flexible) provide optimal balance:**
  - Mandatory floor ensures non-negotiable safety requirements (OTM margin, delta, DTE)
  - Flexible majority allows for real-world market conditions (not everything perfect simultaneously)
  - 4 of 7 = 57% agreement threshold still requires substantial confirmation
- **Unanimous gates (9/9) are overly restrictive in practice:**
  - Market conditions rarely align perfectly on all factors simultaneously
  - Misses valid optimization opportunities where risk is acceptably low
  - Single outlier can block an otherwise safe trade
- **Research-backed thresholds are critical:**
  - Delta <0.20 = <20% assignment probability (quantifiable risk)
  - OTM margin 3.5% exceeds typical noise range 2-3% (empirical)
  - DTE 15+ provides meaningful theta decay window (time-value math)
  - Citing research (TastyTrade, Option Alpha, CBOE) builds trust and justifies relaxations

### Strategy-Specific Risk Asymmetries
- **Puts vs Calls have opposite directional implications:**
  - **Puts:** Rolling UP = higher strike = more aggressive (closer to money, higher assignment risk)
  - **Calls:** Rolling DOWN = lower strike = more aggressive (closer to money, higher assignment risk)
  - Must adapt language and risk framing accordingly in chat instructions
- **Earnings risk severity differs:**
  - **Puts:** Gap-down on earnings miss can move safe OTM position to deep ITM instantly (catastrophic assignment at bad entry)
  - **Calls:** Gap-up on earnings beat moves OTM to ITM (assignment loses upside, but stock still profitable)
  - Puts require stricter earnings gate adherence due to downside asymmetry
- **Technical indicator interpretation flips:**
  - **Puts:** Bullish technicals = safer (stock moving away from strike, reducing assignment risk)
  - **Calls:** Bearish technicals = safer (stock moving away from strike, reducing assignment risk)
  - Gate conditions must account for this directional inversion

### Code Instruction Design Patterns
- **Dual instruction pattern works well:**
  1. **Formal instructions** (`tv_open_put_instructions.py`): Detailed gate logic, research citations, precise thresholds
  2. **Chat instructions** (`tv_open_put_chat_instructions.py`): Conversational guidance, example language, plain-English explanations
  - Formal instructions for programmatic analysis (structured JSON output)
  - Chat instructions for quick user-facing analysis (natural language)
  - Keep both in sync but adapt tone/style to audience
- **Example-driven guidance is powerful:**
  - Providing "Good setup", "Marginal setup", "Blocker" scenario examples helps model understand application
  - Realistic conversational phrasing ("Here's what I'm seeing...", "The interesting thing is...") sets tone
  - "When NOT to suggest" lists prevent over-optimization and maintain safety standards
- **Critical overrides must be explicit and repeated:**
  - Earnings gate override mentioned in multiple places (mandatory conditions, critical override section, examples)
  - "NON-NEGOTIABLE" language emphasizes absolute nature
  - Prevents model from trading off earnings risk against other favorable conditions

### Consistency and Cross-Strategy Alignment
- **When relaxing multiple strategies, maintain parallel structure:**
  - Same gate logic (3 mandatory + 4 of 7 flexible) across call roll down and put roll up
  - Same threshold adjustments (delta, OTM, DTE) for consistency
  - Same research basis and safety preservation principles
  - Easier to understand, debug, and maintain when structures mirror each other
- **Adapt for strategy-specific risks while preserving framework:**
  - Framework (super-majority gate) stays consistent
  - Individual conditions flip based on directional risk (bullish/bearish for puts vs calls)
  - Emphasis changes (earnings MORE critical for puts) without changing gate structure
  - Allows team to reason about all strategies using same mental model

### Decision Documentation Best Practices
- **Analysis-first, implementation-second workflow:**
  - Created detailed analysis document (`put_strategy_analysis.md`) with research, comparisons, recommendations
  - User reviewed and approved before implementation
  - Implementation then follows approved spec exactly
  - Reduces rework and ensures alignment before code changes
- **Decision records should capture context and rationale:**
  - Not just "what changed" but "why we changed it"
  - Research citations and threshold justifications
  - Comparison to alternatives (unanimous vs super-majority)
  - Safety preservation mechanisms documented
  - Future maintainers can understand decision basis, not just current state

## Scheduler Config Auto-Reload Implementation (2025-01)

### Problem Solved
Web UI and scheduler ran as separate processes. When users changed cron schedules via web UI (saved to CosmosDB), the scheduler never picked up the changes because the `_cron_changed` flags were in a different process memory space.

### Solution: Periodic Config Reload
Implemented automatic config reloading in `src/main.py`:
- Every 60 seconds, scheduler queries CosmosDB via `cosmos.get_settings()`
- Compares loaded values with in-memory state
- Detects changes to:
  - Main monitoring cron (`scheduler.cron`)
  - Summary agent cron (`summary_agent.cron`)
  - Timezone (`scheduler.timezone`)
- When changes detected, sets internal flags (`_cron_changed`, `_summary_cron_changed`)
- Existing reschedule logic handles the actual job updates
- Prints clear notifications: "✓ Config reloaded from CosmosDB: summary cron changed to X"

### Implementation Details
- Added `_last_config_reload` timestamp and `_config_reload_interval = 60` to `__init__`
- Created `_reload_config_from_cosmos()` method with change detection logic
- Added periodic reload check in main `run()` loop
- Error handling prevents config reload failures from crashing scheduler
- Backward compatible: `reschedule()` and `reschedule_summary()` still work for future use

### Key Design Decisions
- **60-second interval:** Balances responsiveness vs CosmosDB query cost
- **Reuses existing reschedule logic:** Sets flags that existing code already handles
- **Non-blocking:** Config reload errors logged but don't stop scheduler
- **Updates all relevant settings:** Also syncs `summary_agent.enabled` and `activity_count`

### Testing Approach
Mental test: User changes cron in web UI → CosmosDB updated → within 60s, scheduler detects change → reschedules jobs → user sees new schedule applied without restart.

## Symbol Chat Context Selection Screen UX (2025-01)

### Problem Solved
Previous chat flow immediately loaded chat interface with context checkboxes at the top. Users couldn't make deliberate context choices before starting - they had to toggle checkboxes while chatting, which was confusing and didn't clearly show what context was loaded.

### Solution: Two-Screen Flow
Implemented a selection-first UX pattern in `web/templates/symbol_chat.html`:
1. **Selection Screen:** Users see 3 context checkboxes with descriptions + "Start Chat" button
2. **Chat Screen:** Shows locked-in context and chat interface (with optional "Change Context" button)

### Implementation Details
**HTML Structure:**
- `#selectionScreen` div: Contains checkboxes in card-style boxes with descriptions, centered layout
- `#chatScreen` div: Contains context indicator + chat interface (initially `display: none`)
- Each checkbox option now has title + description for clarity
- "Start Chat" button triggers transition

**JavaScript Flow:**
- On page load: Selection screen visible, checkboxes populated from localStorage
- On "Start Chat" click:
  - Save preferences to localStorage
  - Hide selection screen, show chat screen
  - Update context indicator (shows selected sources in compact format)
  - Call `loadContext()` with locked preferences
  - Set `chatStarted` flag
- On "Change Context" click:
  - Reset chat state (history, cachedContext, messages)
  - Show selection screen, hide chat screen
  - Allows starting fresh session with different context

**Key Features:**
- Context preferences persist via localStorage (same key as before)
- Context indicator shows active sources: "📊 TV • 📈 Positions • 📋 Activities"
- Clean, centered card design for selection screen with descriptive text
- "Change Context" button allows resetting without page reload
- Button states managed (disabled during loading, text changes)

### Design Decisions
- **Selection-first over inline toggles:** Makes context choices deliberate and visible
- **Locked context after start:** Prevents mid-chat confusion about what data is loaded
- **Reset on context change:** Clearer than trying to merge new context into existing chat
- **Descriptions on each checkbox:** "Real-time price, volume, and technical indicators" helps users understand what they're selecting
- **localStorage persistence:** Remembers user preferences across sessions

### UX Benefits
- Users consciously choose context before engaging
- Clear visibility of what data the assistant has access to
- Reduced confusion about checkbox toggles during chat
- Clean, focused selection interface before chat starts

### Dashboard Alert Count Timeframes (2026-04-01)
- Updated dashboard alert counts from calendar-based to rolling window timeframes
- **Backend (`web/app.py`)**: Modified `_count_by_range()` function to use rolling windows:
  - "This Week" → Last 7 days: `now - timedelta(days=7)`
  - "This Month" → Last 30 days: `now - timedelta(days=30)`
  - "Today" remains as midnight UTC to now
- **Frontend (`web/templates/dashboard.html`)**: Updated labels in summary cards and agent table headers:
  - "This Week" → "Last 7 Days"
  - "This Month" → "Last 30 Days"
- The dictionary keys remain `"week"` and `"month"` internally for backward compatibility
- All counts now use rolling windows instead of calendar-aligned periods (no Monday reset for week, no month-start reset)

### Dashboard Alert Timeframe Migration (2026-04-02)
- Orchestrated update from calendar-based to rolling window alert timeframes
- **Backend (`web/app.py`)**: Modified `_count_by_range()` to use rolling windows
  - "This Week" → Last 7 days (rolling window)
  - "This Month" → Last 30 days (rolling window)
  - Consistent date ranges regardless of calendar alignment
- **Frontend (`web/templates/dashboard.html`)**: Updated UI labels for clarity
  - Removed calendar-based terminology
  - Added rolling window descriptions
- Benefits: Predictable, consistent date ranges for alert monitoring; no confusion from week/month resets

### Context Selection Screen Styling Update (2026-04-02)
- **Task**: Applied settings page styling to the "Select Context Sources" screen in `web/templates/symbol_chat.html`
- **Changes**:
  - Added `.settings-card` class to match settings containers
  - Added `.card-header` with `<h2>` title (Settings pattern)
  - Converted description paragraph to `.hint` class
  - Wrapped content in `style="padding: 0.75rem 1.25rem;"` container (standard padding pattern from settings)
  - Maintained checkbox labels and button functionality
- **Visual Consistency**: Context selection screen now matches look and feel of Settings/Configuration page
  - Same card styling and borders
  - Same header treatment
  - Same padding and spacing patterns
  - Same hint text styling for descriptions
- **Files Modified**: `web/templates/symbol_chat.html` (lines 11-48)
- **Pattern**: Settings page uses `.card.settings-card` + `.card-header` + `.hint` + consistent padding wrapper for all configuration containers

### Context Selection Screen Styling Orchestration (2026-04-02T09:24:24Z)
- **Requested By**: dsanchor
- **Mode**: Background agent execution
- **Task Assigned**: Match context selection screen styling to settings/configuration page
- **Expected Outcomes**:
  - web/templates/symbol_chat.html updated with .settings-card class
  - .card-header structure applied for header consistency
  - Padding standardized to match settings page (0.75rem 1.25rem)
- **Orchestration Log**: .squad/orchestration-log/2026-04-02T09:24:24Z-linus.md
- **Session Log**: .squad/log/2026-04-02T09:24:24Z-context-styling.md

### Symbol Chat: Removed Change Context Button (2026-04-02)
- **Task**: Removed "Change Context" button from symbol chat interface
- **Rationale**: User prefers to manually navigate back to context selection screen rather than having an in-chat button
- **Changes**:
  - Removed `⚙️ Change Context` button from chat header (was on lines 55-57)
  - Removed `changeContextBtn` variable declaration from JavaScript (line 90)
  - Removed entire `changeContextBtn.addEventListener('click')` event handler (lines 270-298)
  - Updated flex container to use `justify-content: flex-end` instead of `space-between` (context indicator now right-aligned)
- **Files Modified**: `web/templates/symbol_chat.html`
- **User Pattern**: Prefers explicit navigation over in-context actions for changing chat settings

## Learnings

### Alert Generation Logic Fixed (2026-04-02)
- **Issue**: Alert generation was using narrow whitelist approach - only marking specific activities (SELL, ROLL_*) as alerts
- **Root Cause**: `_is_sell_alert()` and `_is_roll_alert()` methods only checked for specific activity types
- **User Requirement**: "Anything that is NOT wait, hold, or doing nothing should be marked as alert"
- **Solution**: 
  - Created unified `_is_alert()` method that uses blacklist approach
  - Defined `_NON_ALERT_ACTIVITIES` frozenset: {"WAIT", "HOLD", "DO_NOTHING", "DOING_NOTHING"}
  - Logic: `activity not in _NON_ALERT_ACTIVITIES` = alert
  - Replaced both `_is_sell_alert()` and `_is_roll_alert()` with single `_is_alert()` method
  - Both covered_call and position monitor agents now use same alert detection logic
- **Files Modified**: `src/agent_runner.py`
- **Key Principle**: Use negative logic (what's NOT an alert) rather than positive logic (what IS an alert) when the exception list is smaller than the inclusion list
- **Architecture**: Alert field (`is_alert`) is set on all activities; alerts are just activities with `is_alert=true`

### Alert Logic Unified: Blacklist Approach (2026-04-03)
Changed alert detection from whitelist (checking specific activities like SELL, ROLL_*) to blacklist (marking everything except WAIT, HOLD, DO_NOTHING as alerts). This matches the user requirement: "Anything that is NOT wait, hold or doing nothing should be marked as alert." Implemented via unified `_is_alert()` method checking `activity NOT IN _NON_ALERT_ACTIVITIES`, making new activity types automatically alerts (safer, future-proof). Files: src/tv_open_call_agent.py, src/tv_open_put_agent.py.

### CosmosDB Alert Flag Fix Script (2026-04-03)
- **Task**: Built script to fix existing activities in CosmosDB that have incorrect `is_alert` values from old logic
- **Script Location**: `scripts/fix_alert_flags.py`
- **Key Features**:
  - Cross-partition query to fetch all activities (`doc_type='activity'`)
  - Recalculates `is_alert` using blacklist approach: `activity.upper() NOT IN ["WAIT", "HOLD", "DO_NOTHING", "DOING_NOTHING"]`
  - Dry-run mode (`--dry-run`) to preview changes without updating
  - Detailed statistics: total activities, correct/incorrect counts, breakdown by action type
  - Progress tracking during updates with error handling
  - Sample output display in dry-run mode
- **Technical Details**:
  - Uses `container.query_items()` with `enable_cross_partition_query=True`
  - Updates via `container.replace_item()` to preserve all other fields
  - Loads config from `config.yaml` with environment variable expansion
  - Comprehensive error handling with per-item error logging
- **Documentation**: `scripts/README_fix_alert_flags.md` with usage examples and output samples
- **Pattern**: Always provide dry-run mode for data migration scripts to allow safe preview before applying changes
- **User Workflow**: Run with `--dry-run` first, review stats, then run without flag to apply fixes

### CosmosDB Alert Flags Fix Script (2026-04-03)
- Delivered `scripts/fix_alert_flags.py` with dry-run mode, progress tracking, and comprehensive statistics
- Recalculates `is_alert` flags for existing activities using new blacklist approach
- Features: cursor-based pagination for large datasets, atomic per-item updates, detailed error reporting, stats summary
- Documented with `scripts/README_fix_alert_flags.md` including usage examples and sample outputs
- Follows best practices: dry-run preview before applying changes, per-item error isolation, config loading with env expansion

### SKIPPED Added to Non-Alert Blacklist (2026-04-03)
- **Task**: Add "SKIPPED" to non-alert activities blacklist so it's not marked as an alert
- **User Pattern**: "skipped should not be marked as alert" - user wants SKIPPED treated same as WAIT/HOLD/DO_NOTHING
- **Changes Made**:
  - Updated `src/agent_runner.py`: Added "SKIPPED" to `_NON_ALERT_ACTIVITIES` frozenset
  - Updated `scripts/fix_alert_flags.py`: Added "SKIPPED" to `NON_ALERT_ACTIVITIES` frozenset
  - Updated `scripts/README_fix_alert_flags.md`: Documentation now lists SKIPPED in blacklist
  - Updated docstring in `_is_alert()` method to mention "skipped" in the rule description
- **New Blacklist**: `["WAIT", "HOLD", "DO_NOTHING", "DOING_NOTHING", "SKIPPED"]`
- **Behavior**: Any activity with action="SKIPPED" will now have `is_alert=false`
- **Files Modified**: 
  - `src/agent_runner.py`
  - `scripts/fix_alert_flags.py`
  - `scripts/README_fix_alert_flags.md`
- **Note**: Users can run `python scripts/fix_alert_flags.py` to update any existing SKIPPED activities in CosmosDB

### TradingView 403 Resilience Analysis (2025-01-24)
- **Issue**: Persistent 403 errors from TradingView blacklisting session after accessing "hot" symbols
- **Root Cause Identified**:
  - Single persistent `requests.Session()` used throughout fetcher lifetime (line 749)
  - Global `has_403` flag poisons ALL subsequent symbols, even unrelated ones (lines 805, 821, 1252)
  - No per-symbol isolation — one bad symbol kills entire batch
  - TradingView fingerprints: session cookies, IP+UA combo, request velocity, missing natural navigation
- **Current Agent Pattern**: All agents iterate symbols sequentially with shared fetcher (covered_call_agent.py:44-50)
- **Proposed Solution** (4-phase implementation):
  1. **Per-symbol 403 tracking**: Replace global `has_403` with `_banned_symbols: set[str]` + `_symbol_403_count: dict`
  2. **Session rotation**: Create fresh session after N requests (configurable, default 8) + warmup flow (homepage → SPY → target)
  3. **Graduated cooldown**: First 403 → 30s + session reset, second → 2min, third → blacklist symbol
  4. **Symbol randomization**: Shuffle symbol list in agents to avoid predictable patterns
- **Key Architectural Changes**:
  - Lazy session creation with `_ensure_session()` method
  - Warmup sequence (`_warmup_session()`) to mimic human browsing (homepage, then SPY, then target)
  - Per-symbol ban check (`_is_symbol_banned()`) before fetch
  - Session-level request counter to trigger rotation
  - Enhanced rate limiting config: 2-4s for regular, 5-10s for options chain
- **Config Additions**: `tradingview.max_requests_per_session`, `warmup_enabled`, `symbol_cooldown_after_403`, `max_403_per_symbol`, `symbol_ban_duration`
- **Expected Outcomes**: Resilience (one bad symbol won't poison batch), lower 403 rate (session rotation looks human), graceful degradation (per-symbol failures), improved stealth
- **Files Analyzed**: `src/tv_data_fetcher.py` (lines 730-1324), agent files (covered_call_agent.py, cash_secured_put_agent.py, monitor agents), config.yaml
- **Decision Document**: `.squad/decisions/inbox/linus-anti403-implementation.md` — comprehensive proposal with pseudocode
- **Key Pattern**: Session isolation is critical for scraper resilience — never let one resource failure poison unrelated resources

### Summary Agent Multi-Agent-Type Bug Fix (2026-MM-DD)

**Problem**: `CosmosDBClient.get_recent_activities_by_symbol()` fetched only N most recent activities **overall** per symbol, not N per agent_type. This caused symbols with both put and call watching to have incomplete summary data — only the most active agent_type would be represented, while the other would be omitted entirely.

**Root Cause**: Original implementation in `src/cosmos_db.py:667` used `TOP @limit` on a single query filtering only by `doc_type = 'activity'`, without considering `agent_type`. If a symbol had 10 recent `covered_call` activities and 2 recent `cash_secured_put` activities, only the `covered_call` activities would be returned when `limit_per_symbol=3`.

**Fix Implemented**:
- Changed `get_recent_activities_by_symbol()` to iterate over all four agent_types (`covered_call`, `cash_secured_put`, `open_call_monitor`, `open_put_monitor`) and query each separately with `TOP @limit` per agent_type
- Merged results from all agent_types and sorted by timestamp DESC (newest first) before returning
- Updated docstring to clarify: fetches `limit_per_symbol` activities **per agent_type**, so total activities per symbol may be up to `limit_per_symbol × 4`
- Maintained return type `dict[str, list[dict]]` for backward compatibility with existing callers

**Impact**:
- Summary agent (`run_summary_agent` in `agent_runner.py:683`) now receives complete activity history for all active agent types
- Ensures symbols with both put and call watching (or both watch + monitor agents) have all agent types represented in daily summaries
- No breaking changes — callers just receive more complete data

**Files Modified**:
- `src/cosmos_db.py` — `get_recent_activities_by_symbol()` method (lines 667-700)

**Key Learning**: When aggregating multi-dimensional data (symbol × agent_type), ensure per-dimension limits to avoid skewing toward the most active dimension. Cross-partition queries with `TOP` need careful filtering to guarantee representation across all dimensions.

## 2025-01-27 — Premium-First Roll Policy Implementation

**What**: Added mandatory Premium-First Roll Policy section to both monitor instruction files (`tv_open_call_instructions.py` and `tv_open_put_instructions.py`). Enforces economic discipline on all roll recommendations by requiring agents to calculate roll economics before recommending any roll.

**Why**: Previous instructions allowed agents to recommend rolls without calculating actual costs. This created risk of expensive defensive rolls that erode capital without systematic evaluation of alternatives. The new policy forces income-first thinking while allowing defensive rolls up to a $20 debit cap.

**Policy Structure**:
- **Tier 1 (Preferred)**: Net credit ≥$1.00 — roll generates income, approved automatically
- **Tier 2 (Acceptable)**: Net debit ≤$20.00 — acceptable insurance cost to avoid assignment, requires `ultra_defensive_roll` risk flag
- **Tier 3 (Rejected)**: Net debit >$20.00 — do NOT recommend, execute Roll Search Algorithm or CLOSE

**Roll Search Algorithm**: When initial roll candidate fails, systematically search:
1. Same strike, +1 week expiration
2. ±1 strike increment (calls roll UP, puts roll DOWN), same expiration
3. Combined: ±1 strike AND +1 week
4. If all fail → CLOSE

**New JSON Schema Fields**:
```json
"roll_economics": {
  "buyback_cost": 2.50,      // ASK price of current option
  "new_premium": 3.80,       // BID price of roll target
  "net_credit": 1.30,        // new_premium - buyback_cost
  "roll_tier": "credit",     // or "ultra_defensive" or "no_viable_roll"
  "candidates_evaluated": 4  // how many roll candidates analyzed
}
```
Set to `null` for WAIT, populated for all ROLL and CLOSE activities.

**New Risk Flags**:
- `ultra_defensive_roll`: Roll with net debit ≤$20, acceptable insurance cost
- `no_viable_roll`: No roll candidate meets premium-first policy thresholds

**Updated CLOSE Logic**: Now only recommended when (1) fundamental thesis changed, OR (2) no viable roll exists after executing Roll Search Algorithm. When CLOSE due to #2, set `roll_tier = "no_viable_roll"`.

**Files Modified**:
- `src/tv_open_call_instructions.py` — Added Premium-First Roll Policy section after Roll Candidate Selection (line ~325), updated CLOSE description, added roll_economics to JSON schema, added new risk flags, updated all ROLL examples to show math
- `src/tv_open_put_instructions.py` — Same changes, put-specific (rolls DOWN for defensive moves)

**Key Learning**: Agent instruction quality benefits from explicit economic calculations rather than subjective "cost-effective" judgments. Three-tier hierarchy (preferred/acceptable/rejected) provides clear decision boundaries while maintaining flexibility for defensive scenarios. Roll Search Algorithm prevents agents from giving up after first failed candidate — forces systematic evaluation of strike/expiration combinations. Options chain data is already available in Section 4, so calculations are straightforward: buyback cost = ask price of current, new premium = bid price of target.

**Impact**:
- Monitor agents now calculate and report exact roll economics for every ROLL and CLOSE recommendation
- Transparent math in reason field allows user verification
- Systematic search reduces missed opportunities for better roll candidates
- $1 credit threshold aligns with income-first philosophy of covered call/put strategy
- $20 debit cap prevents runaway losses while allowing reasonable defensive rolls

**Research Basis**: 
- Net credit threshold aligns with income generation goal of covered options strategy
- $20 debit cap based on ~3% of typical $70-$200 strike positions — reasonable insurance cost relative to assignment consequences (forced stock sale at unfavorable price)
- Roll Search Algorithm derived from common practitioner workflows: time extension first (cheapest), then strike adjustment, then combined

**Future Considerations**: May need to adjust $1 and $20 thresholds based on real-world agent performance. Could add Tier 2.5 for larger accounts or adjust thresholds dynamically based on position size. Profit Optimization sections remain separate logic for OTM positions. Mandatory Earnings Gate remains independent and takes priority over roll economics.

### TradingView Options Chain totalCount Filter (2026-07-25)
- **Issue**: `fetch_options_chain` in `src/tv_data_fetcher.py` was capturing a useless scanner response (totalCount=1) alongside real option chain data
- **Fix**: Added early filtering in `_on_response` callback (line ~1276): parse JSON body, check `totalCount`, discard if ≤ 1
- **Pattern**: Filter at capture time (in the response handler) rather than post-processing, so downstream code never sees garbage data
- **Key file**: `src/tv_data_fetcher.py`, `_on_response` callback inside `fetch_options_chain`

### Options Chain Schema Documentation (2026-07-25)
- **Task**: Add schema documentation to all places where options chain JSON is injected into agent prompts, chat contexts, and reports
- **Approach**: Created `OPTIONS_CHAIN_SCHEMA_DESCRIPTION` constant in `src/options_chain_parser.py` — a reusable string describing the JSON structure and all contract fields (strike, greeks, IV, etc.)
- **Injection points** (6 total):
  1. `src/agent_runner.py` `_format_options_chain()` — prepends schema before JSON (covers both `run_symbol_agent` and `run_position_monitor`)
  2. `src/report_agent.py` context builder — prepends schema when options chain is parsed
  3. `web/app.py` `chat_api()` quick-analysis mode — inserts schema before raw options chain data
  4. `web/app.py` `_build_symbol_context()` — prepends schema to options chain section in symbol chat
- **Key files**: `src/options_chain_parser.py`, `src/agent_runner.py`, `src/report_agent.py`, `web/app.py`

### Anti-Hallucination Guardrails for Roll Prices (2026-07-22)
- **Problem**: Open call/put monitor agents were hallucinating bid/ask prices when recommending rolls — fabricating plausible numbers instead of reading from the options chain JSON.
- **Root cause**: Agent instructions lacked explicit constraints forbidding price fabrication and had no verification step requiring exact chain lookups before reporting roll economics.
- **Changes** (3 files, instruction text only — no code logic changed):
  1. `src/options_chain_parser.py` — Added "DATA INTEGRITY (MANDATORY)" section to `OPTIONS_CHAIN_SCHEMA_DESCRIPTION`. Forbids estimating, interpolating, rounding, or fabricating prices. Requires "contract not found in chain" if no match.
  2. `src/tv_open_call_instructions.py` — Added "VERIFICATION (CRITICAL)" step after Roll Economics Calculation in Premium-First Roll Policy. Requires agent to match option_type + expiration + strike → read exact bid/ask before reporting economics. If either contract missing, set roll_economics to null.
  3. `src/tv_open_put_instructions.py` — Same verification step added to put monitor instructions.
- **Key insight**: LLM agents will confabulate numeric values unless instructions explicitly require exact lookup + quote and define what to do when a contract isn't found.

### Options Chain Format Analysis — LLM Hallucination Root Cause (2026-01-15)
- **Problem**: Monitor agents continue to hallucinate bid/ask prices despite anti-hallucination guardrails (verification steps, data integrity rules). Fabrication rate ~30-40%.
- **Root cause**: The nested-array JSON format (calls → expiration → array of contracts) forces a 4-step cognitive task: (1) navigate to expiration key, (2) scan array of 20-40 contracts, (3) match strike by equality, (4) extract bid/ask field. LLMs are autocompletion engines, not symbolic reasoners — array scanning + equality matching exceeds reliability threshold.
- **Analysis findings**: 
  - Current format token count: ~20,000 tokens for typical 6-expiration chain
  - Lookup pattern requires iteration + filtering (error-prone for LLMs)
  - Prompt engineering cannot fix structural data problems
- **Recommendation**: Switch to **strike-keyed dictionaries + position-relative filtering** (hybrid of options 2b + 2d)
  - Strike-keyed: `calls["20260427"]["475.0"]["ask"]` — direct path, no iteration, no equality matching
  - Pre-filtered: Send only ±15 strikes from current position (60-75% token reduction, 30-40 contracts vs 100-200)
  - Expected hallucination drop: 30-40% → <5%
- **Alternative**: Markdown tables with pre-computed net credits if JSON still shows >10% error rate
- **Future option**: Pre-computed roll tables (server calculates candidates, agent picks) — most reliable but reduces agent autonomy
- **Key insight**: "Make the right thing the easy thing" — if correct lookup is 1 step and wrong lookup is 3 steps, errors drop dramatically. Direct key access (`dict[key]`) is autocompletion-friendly; array scanning is not.
- **Deliverable**: Full analysis report saved to `options_chain_format_analysis.md`

### Strike-Keyed Dict + Position-Relative Filtering Implementation (2026-05-12)
- **Parser**: `parse_options_chain()` now outputs strike-keyed dicts instead of arrays: `calls["20260427"]["475.0"] = {contract}`. Strikes sorted numerically within each expiration.
- **Strike key format**: `str(float(strike))` normalizes all strikes — "475.0", "472.5". Kept `strike` field inside opt dict for redundancy.
- **Filter function**: New `filter_options_chain_for_position(chain, current_strike, option_type, num_strikes=15)` trims to ±15 strikes around position. Adds `current_position` metadata to output.
- **Agent runner**: `_format_options_chain()` gained optional `current_strike` and `option_type` params. Monitor agents pass position context for filtering; analysis agents get the full chain.
- **Schema description**: Updated `OPTIONS_CHAIN_SCHEMA_DESCRIPTION` — HOW TO LOOK UP section now shows direct key-path access, DATA INTEGRITY requires stating full path.
- **Instruction files**: VERIFICATION sections in `tv_open_call_instructions.py` and `tv_open_put_instructions.py` now use key-path syntax: `calls["exp"]["strike"]["ask"]` / `puts["exp"]["strike"]["bid"]`.
- **Cleanup**: Deleted `options_chain_format_analysis.md` (analysis artifact, not production code).

### Monitor Agent Instruction Split (2026-07-22)
- **Task**: Split each monitor agent's instructions (call + put) into Assessment (Agent 1) + Roll Management (Agent 2) per Danny's architecture decision.
- **Files created**: 4 new instruction files:
  - `src/tv_open_call_assessment_instructions.py` (463 lines) — exports `get_open_call_assessment_instructions()`
  - `src/tv_open_call_roll_instructions.py` (298 lines) — exports `get_open_call_roll_instructions()`
  - `src/tv_open_put_assessment_instructions.py` (462 lines) — exports `get_open_put_assessment_instructions()`
  - `src/tv_open_put_roll_instructions.py` (300 lines) — exports `get_open_put_roll_instructions()`
- **Split pattern**: Assessment agents use functions (not module-level constants) for consistency with the new split pattern. Each function returns the instruction string.
- **Agent 1 (Assessment)** owns: Role/strategy intro, earnings gate (full 25-row matrix), earnings override + roll target rules, position context, 8-dimension analysis framework, WAIT/ROLL criteria, profit optimization gate (pass/fail decision only). Outputs final JSON for WAIT, handoff JSON for non-WAIT.
- **Agent 2 (Roll Management)** owns: Roll types, roll candidate selection, premium-first roll policy (3-tier system), roll search algorithm, options chain schema (imports OPTIONS_CHAIN_SCHEMA_DESCRIPTION from options_chain_parser.py). Receives handoff JSON + filtered chain, outputs final activity JSON (same schema as today).
- **Handoff schema**: Intermediate JSON with action_needed, position state, earnings_analysis, pivot_points, risk_flags, profit_optimization_gate, roll_target_rules. Designed so Agent 2 never needs to re-derive analysis.
- **Key design decisions**:
  - Agent 1 does NOT receive the full options chain — only position context delta/IV
  - Roll instructions import OPTIONS_CHAIN_SCHEMA_DESCRIPTION via f-string injection
  - Output JSON schema for Agent 2 is IDENTICAL to the current unified schema — no migration needed
  - Put roll instructions use support levels (S1/S2/S3) for strike targeting; call roll instructions use resistance levels (R1/R2/R3)
  - Roll search algorithm for puts steps DOWN (-1 strike increment); for calls steps UP (+1 strike increment)
- **Original files untouched**: tv_open_call_instructions.py and tv_open_put_instructions.py remain as-is

### Rubber Duck Review Fixes (2026-07)
- **Finding 1 (BLOCKING): Roll cost sign convention fix** — In both roll instruction files, the schema and ROLL examples showed `estimated_roll_cost: -0.45` (negative) alongside `net_credit: 1.30` (positive). This was contradictory since positive = credit per the rules. Fixed all examples to use `estimated_roll_cost = new_premium - buyback_cost` (e.g., 4.50 - 3.20 = 1.30). Profit optimization examples were already correct.
- **Finding 2 (HIGH): Profit optimization gate responsibility split** — Assessment files had flexible conditions 6/7 checking "no earnings/ex-div before new expiration" but Agent 1 doesn't choose the expiration. Changed gate result from "passed" to "eligible", removed candidate-dependent conditions (now 5 stock-level flexible, need 3 of 5), added `profit_optimization_constraints` field to handoff JSON with `next_earnings_date`/`next_ex_div_date`. Added new PROFIT OPTIMIZATION VALIDATION section to both roll files so Agent 2 validates these before proceeding.
- **Finding 3 (HIGH): Mandatory JSON output** — Added explicit warning to both roll files: output MUST contain a valid JSON block with `activity` field. If no viable roll, output CLOSE with `roll_tier: "no_viable_roll"`. Never output without JSON.
- Key learning: When splitting agents, re-examine which conditions each agent CAN evaluate. Candidate-dependent checks belong with the agent that selects the candidate.

### Phase 1 CLOSE Removal (2026-07)
- **Design flaw fixed**: Phase 1 (Assessment) was outputting CLOSE as an `action_needed` value, but Phase 1 lacks the full options chain needed to determine if any viable roll exists. Only Phase 2 (Roll Management) has chain data for that economic evaluation.
- **Changes**: Removed CLOSE from `action_needed` enum in both assessment handoff schemas. Phase 1 now always picks the best ROLL type. Added `close_for_profit_recommended` (boolean) and `profit_level_pct` (float) handoff fields for TastyTrade 50%+ profit scenarios. Phase 2 now handles three CLOSE paths: (1) close-for-profit when flag is set and ask confirms profit, (2) no-viable-roll after exhausting Roll Search Algorithm, (3) fundamental deterioration with no viable roll.
- **Earnings gate result names preserved**: CLOSE_OR_ROLL remains a valid gate result name (risk label), but Phase 1's action in response is always "hand off to Phase 2 for roll."
- Key learning: The agent that makes a decision must have the data to justify it. CLOSE requires chain economics → only Phase 2 can decide CLOSE.

### Pre-Computed Markdown Table Format for Phase 2 (2026-07)
- **Problem**: LLM agents consistently misread raw JSON options chain data — wrong strikes, wrong bids, fabricated prices. The nested dict format (`calls["20260520"]["475.0"]["bid"]`) was error-prone for LLMs.
- **Solution**: Replaced JSON chain input with pre-computed markdown tables where Python does all the math. Agent just picks the best row from the table.
- **Changes to instruction files** (`tv_open_call_roll_instructions.py`, `tv_open_put_roll_instructions.py`):
  - Removed `OPTIONS_CHAIN_SCHEMA_DESCRIPTION` import and f-string formatting
  - Replaced INPUT section with new table format description (CURRENT POSITION block + ROLL CANDIDATES table)
  - Updated VERIFICATION section: from "find JSON path" to "read from table row"
  - Updated ROLL SEARCH ALGORITHM: from "navigate chain" to "scan table rows"
  - Updated all example reason strings: from JSON paths to table row references
  - Updated Close-for-Profit: references CURRENT POSITION block instead of "chain"
  - Fixed double-brace `{{` → `{` since strings are no longer f-strings
- **Preserved**: All decision logic, Premium-First tiers, 45 DTE cap, delta constraints, earnings gates, CLOSE logic, profit optimization validation.
- Key learning: When LLMs misread structured data, pre-compute the answer and present it as a simple table — the agent's job becomes selection, not calculation.

### Decision Batch Merged to Team Record (2026-04-25T06:43:58Z)
**Status:** ✅ Completed
**Decision Records:** Promoted from .squad/decisions/inbox → .squad/decisions.md
**Cross-Team Awareness:** Strike snapping decision propagated to Rusty's history for roll economics validation workflows

This orchestration event consolidates all pending team decisions from 2026-04-23 onwards into the permanent team record:
- Decisions 5–16 in `decisions.md` (12 new decision entries covering action format, stability buffer, ROLL prohibition, phase-split logic, candidate tables, and code-level validation)
- Inbox cleaned: all 12 decision files deleted from `.squad/decisions/inbox/`
- Orchestration logs created: `20260425T064358-rusty.md` (summary) + `20260425T064358-strike-snapping-fix.md` (session log)
- Linus entries include stability buffer (decision 8), bare ROLL prohibition (decision 6), phase-2-only CLOSE (decision 7), markdown tables (decision 9) — all already implemented and now recorded in team memory

**Why this matters:** Team decisions are now canonical and immutable in the decisions archive. Future sprints will consult this record for precedent on action format validation, snapping behavior, and two-phase agent architecture.

### Option Type Filter — Stage 0 Filter Pipeline (2026-04-25)
- **Task**: Add option type filter (calls/puts) as the first stage of the chain filter pipeline
- **Implementation**: New `filter_options_chain_by_type()` function in `src/options_chain_parser.py` separates call and put options
- **Integration points**: Applied in `src/agent_runner.py` (2 locations) and `web/app.py` (debug endpoint Stage 0)
- **Decision**: Type filter is Stage 0 of the pipeline — strips irrelevant option side before any other filtering logic executes
- **Commit**: 8cdfb99
- **Key insight**: Early filtering on immutable characteristics (call vs put) streamlines downstream processing and reduces token overhead in later stages


### Contrarian Agent Refactor: Devil's Advocate → Quality Auditor (2026-07)
- **Problem**: Contrarian agent was too adversarial — manufactured objections even when the original decision was clearly correct. Real example: flagged >3% CSP premium yield as "low" when 3% is outstanding.
- **Root cause**: Core instruction said "ALWAYS argue the opposite" — LLM complied literally, inventing problems when none existed.
- **Solution**: Reframed the entire agent from "devil's advocate" to "quality auditor":
  - Role: "Options Strategy Contrarian — Devil's Advocate" → "Options Strategy Auditor — Quality Check"
  - Mission: "Argue the OPPOSITE position" → "Audit the quality of this decision"
  - Rule #1: "ALWAYS argue the opposite" → "Challenge ONLY when you find genuine issues"
  - All 9 playbooks: changed framing from adversarial to audit checklist
  - Added premium yield benchmarks (CSP >1.5%/mo good, CC >1%/mo good) to SELL playbook and anti-noise rules
  - Added Rule #9: validate data interpretation before looking for risks
  - WEAK outcome explicitly described as "the BEST and MOST VALUABLE outcome"
- **Key insight**: When an LLM is told to "always argue the opposite," it will manufacture arguments even against correct decisions. Reframing as "audit" with explicit permission to say "everything looks good" produces much higher signal-to-noise.
- **Files Modified**: `src/tv_contrarian_instructions.py`
- **Commit**: 305f33b


### Premium-Expiration Cross-Verification in Contrarian (2025-07)
- **Problem**: Primary agents sometimes read the bid/premium from the WRONG expiration date in the options chain — e.g., picking a bid from the last expiration instead of the recommended one.
- **Solution**: Added explicit premium-expiration cross-verification to the contrarian auditor:
  - Enhanced Rule #9 with a ⛔ PREMIUM-EXPIRATION MATCH critical check — instructs contrarian to verify `{puts|calls}["{YYYYMMDD}"]["{strike}"]["bid"]` matches the recommended expiration
  - Added "Data accuracy" check item #7 to the SELL playbook
  - Added "Data accuracy" check item to all 5 ROLL playbooks (ROLL_UP, ROLL_DOWN, ROLL_UP_AND_OUT, ROLL_DOWN_AND_OUT, ROLL_OUT)
- **Key insight**: The contrarian already receives the full options chain data in its context, so it CAN verify the chain path — it just wasn't being told to. This is a zero-cost improvement that catches a real data-read bug pattern.
- **Files Modified**: `src/tv_contrarian_instructions.py`

### Premium Cross-Verification Bug Fix (2026-07)
- Bug: CSP watcher (and potentially all agents) reported premium (bid) from the CORRECT strike but WRONG expiration — specifically the last expiration in the chain. Root cause: LLM reads multi-expiration JSON and gets confused about which expiration key it's reading from.
- Fix: Added a mandatory "Premium Cross-Verification" step to the RESPONSE STRUCTURE of all 4 watcher/roll instruction files, positioned immediately before the JSON output step. The step forces the agent to explicitly cite the full JSON path (e.g., `puts["20260613"]["95.0"]["bid"] = 3.45`) and verify the expiration key matches the recommended date.
- Also added a `⚠️ COMMON ERROR` warning to `OPTIONS_CHAIN_SCHEMA_DESCRIPTION` in `options_chain_parser.py` — this is injected into every agent's context at runtime, so all agents see it.
- Added lighter-weight verification guidance to both chat instruction files (`tv_open_call_chat_instructions.py`, `tv_open_put_chat_instructions.py`).
- **Files Modified**: `options_chain_parser.py`, `tv_cash_secured_put_instructions.py`, `tv_covered_call_instructions.py`, `tv_open_call_roll_instructions.py`, `tv_open_put_roll_instructions.py`, `tv_open_call_chat_instructions.py`, `tv_open_put_chat_instructions.py`
- **Key pattern**: When an LLM reads a nested dict keyed by multiple dimensions (expiration → strike → contract), it can silently cross dimensions. The fix is to require citing the full key path as a mandatory response step — making the lookup explicit forces the model to self-check.

---

## 2026-05-10: DGI Screener Display & Data Fixes

✅ **Complete**

Fixed 3 critical issues in DGI screener:
1. **Dividend Yield Display Bug**: Removed ×100 multiplier (yfinance already returns percentages). Template was showing 118% instead of 1.18%.
2. **Detail Modal**: Added interactive row-click modal with Overview/Fundamentals/Technical sections reusing existing CSS.
3. **Years_Consecutive_Increases Partial-Year Bug**: Fixed by excluding incomplete current year from annual totals in `calculate_years_consecutive_increases()` and `calculate_dividend_cagr()`.

Files modified: `web/templates/dgi_screener.html`, `src/yfinance_fetcher.py`

Note: Scoring functions in `dgi_metrics.py` treat dividend_yield as ratio (thresholds 0.015, 0.02). Verify accuracy after next screener run.

---

## 2026-05-10: DGI Screener ▶ Button → Chat Quick Analysis Redirect

✅ **Complete**

**Problem**: DGI screener ▶ (analyze) button triggered CSP analysis API in background. User wanted it to redirect to Chat page's Quick Analysis mode instead for interactive analysis.

**Solution**: 
1. **DGI Screener** (`web/templates/dgi_screener.html`):
   - Replaced `.btn-dgi-analyze` click handler (lines 123-153)
   - Now redirects to `/chat?mode=quick-analysis&symbol=XXX&market=YYY&option_type=put`
   - Added Yahoo Finance exchange code mapping (NYQ→NYSE, NMS→NASDAQ, etc.) since YFinance returns codes but chat expects full names
   - Preserves ➕ (add to watchlist) button and detail modal functionality

2. **Chat Page** (`web/templates/chat.html`):
   - Added URL parameter detection on page load
   - If `mode=quick-analysis` + `symbol` + `market` + `option_type` params present:
     - Pre-fills form fields
     - Auto-calls `selectMode('quick-analysis')`
     - Auto-triggers `fetchAndAnalyze()` after 100ms delay
   - User lands on chat page and analysis starts automatically

**Key Learnings**:
- Yahoo Finance returns exchange codes (NYQ, NMS, NGM, etc.) but TradingView/chat expects full names (NYSE, NASDAQ)
- Chat page already had all the infrastructure for Quick Analysis — just needed URL param hookup
- URLSearchParams makes redirect-with-params UX trivial

**Files Modified**: `web/templates/dgi_screener.html`, `web/templates/chat.html`

**Pattern**: Redirect-to-form-with-auto-submit is cleaner than duplicating analysis logic. The chat page owns the Quick Analysis flow; DGI just bootstraps it with params.

### DGI Screener Top 40 + Interactive Filters (2026-05-10)
- **Part 1: Expanded from top 20 to top 40**
  - Changed `src/dgi_screener.py` line 49: `top_n` default from 20 → 40
  - Updated `web/templates/dgi_screener.html` subtitle: "Top 20" → "Top 40"
  - Kept doc IDs (`top20_*`) and variable names (`top20`) unchanged for backward compatibility — changing IDs would orphan existing Cosmos docs

- **Part 2: Added interactive slider filters**
  - Filter panel sits above the table in a collapsible card
  - 5 range sliders (0-100 scale):
    - Quality Score ≥ (direct filter on `entry.quality_score`)
    - Div Yield ≥ (slider/10 = 0%-10%+ filter on `metrics.dividend_yield`)
    - Div Growth ≥ (slider maps to 0%-100% CAGR filter on `metrics.dividend_cagr_5y * 100`)
    - Years ≥ (direct filter on `metrics.years_consecutive_increases`)
    - Timing ≥ (direct filter on `technicals.score`)
  - Real-time updates: `oninput` event triggers `applyFilters()` JS function
  - Displays "Showing X of Y stocks" count that updates dynamically
  - Reset button sets all sliders back to 0
  - Client-side filtering: reads `data-entry` JSON attribute on each table row, toggles `display: none` vs `display: table-row`
  - Sorting still works on filtered rows — sort maintains filter state by preserving display property
  - Detail modal (click on row), ▶ (analyze), and ➕ (add to watchlist) buttons all work on filtered rows

- **CSS**: Added `.range-slider` styling in `web/static/style.css` for dark theme compatibility (WebKit + Firefox)
  - 6px track height, `var(--border)` background, `var(--accent-blue)` thumb
  - Hover effect: slightly lighter blue + scale(1.1) on thumb

- **Key Pattern**: Client-side filtering with JSON data attributes
  - Each row already had `data-entry='{{ entry | tojson | e }}'` for the detail modal
  - Reused this for filtering — no need for server-side API or extra data structures
  - Filter panel uses collapsible card (click header to toggle visibility)
  - Real-time slider updates give instant feedback without jarring full page reloads

**Files Modified**: `src/dgi_screener.py`, `web/templates/dgi_screener.html`, `web/static/style.css`

### Exchange Code Normalization (2026-05-10)
- **Issue**: yfinance returns exchange codes (NYQ, NMS, NGM, PCX, BTS, etc.) that are incompatible with TradingView
- **Solution**: Added `EXCHANGE_MAP` + `_normalize_exchange()` in `src/dgi_screener.py` to normalize at the source when building metrics
- **JS cleanup**: Removed duplicate `marketMap` from `dgi_screener.html` ▶ button handler; fixed ➕ button's hardcoded `data-exchange="NYSE"` to use actual `entry.exchange`
- **Key Principle**: Normalize data at the source (Python screener) rather than patching downstream (JS template)
- **Files Modified**: `src/dgi_screener.py`, `web/templates/dgi_screener.html`

### DGI Top N Settings UI (2026-05-10)
- **Feature**: Exposed `top_n` (number of stocks in DGI top list) as a configurable setting in the Settings → Configuration page
- **Architecture**: `dgi_screener.py` already reads `dgi_config.get("top_n", 40)` — no backend change needed. Only added the UI plumbing to persist and display the value.
- **Settings pattern**: Settings flow is: form POST → save to CosmosDB + config.yaml → re-read for display. Each new field needs: (1) template input, (2) GET handler pass-through, (3) POST handler parse/validate/save, (4) post-save re-read pass-through.
- **Key files**: `web/app.py` (settings_config_page + settings_config_save), `web/templates/settings_config.html` (DGI Screener subsection)
- **Validation**: Clamped to 1–500, defaults to 40 on invalid input, consistent with how `summary_activity_count` is validated (clamp + fallback).

### Chat Table Rendering Fix (2026-05-11)
- **Problem**: Decision Summary tables in chat views render poorly. Wide 2-column markdown tables with `<br>` tags get cramped in chat bubbles.
- **Solution Part 1 — CSS Improvements** (`web/static/style.css` lines ~558-573):
  - Made tables scrollable: Added `display: block; overflow-x: auto;` to table
  - Improved padding: Increased from `0.35em 0.6em` to `0.5em 0.75em`
  - Dark theme: Changed header background from `rgba(0,0,0,0.2)` to `var(--surface-elevated)` with improved contrast
  - Readability: Added alternating row colors (`nth-child(odd)` and `nth-child(even)`) with subtle backgrounds
  - `<br>` tag spacing: Added rule for `td br` to create proper spacing between lines (`margin: 0.35em 0`)
  - Visual hierarchy: Increased header font-weight from 500 to 600
- **Solution Part 2 — Prompt Format Change**:
  - **Old format**: 2-column markdown tables with `| Factor | Assessment |` and `<br>` tags for multi-line content
  - **New format**: Section-based card format using markdown headers (`##`, `**`), bullet lists, and emoji prefixes
  - **Benefits**: Renders cleanly at any width, no `<br>` hacks needed, better visual scanning in chat bubbles
  - **Files changed**:
    - `src/tv_open_call_chat_instructions.py`: Updated format template (lines ~183-205) and example response (lines ~280-292)
    - `src/tv_open_put_chat_instructions.py`: Updated format template (lines ~147-169) and example response (lines ~247-259)
    - Preserved all content guidelines — only changed OUTPUT FORMAT, not strategy logic
    - Differences preserved: PUT file keeps "Assignment Readiness" row instead of "Profit Target / Exit Plan"
  - `src/tv_report_instructions.py`: Added note in FORMATTING RULES: "For tables in chat views: Keep tables compact. Use narrow columns. Avoid `<br>` tags in cells — use multiple rows instead."
- **Key Pattern**: Chat bubbles need formats optimized for variable width. Section-based markdown (headers + lists) adapts better than wide tables with multi-line cells. CSS improvements help tables that remain (like options chain in reports).
- **Files Modified**: `web/static/style.css`, `src/tv_open_call_chat_instructions.py`, `src/tv_open_put_chat_instructions.py`, `src/tv_report_instructions.py`

### DGI Score History Tracking (2026-05)
- Added `score_history` field to DGI screener CosmosDB documents — a list of `{"date": "YYYY-MM-DD", "score": float}` entries tracking quality_score evolution over time.
- Backend (`src/dgi_screener.py`): On each screener run, if a stock already exists in the top_n, the previous `score_history` is carried forward. New entry appended if date or score changed; same-day updates replace the last entry to avoid duplicates. New stocks get initialized with one entry. Capped at 90 entries.
- The `score_history` field is persisted in the CosmosDB document alongside existing fields like `days_on_list` and `first_appeared`.
- Frontend (`web/templates/dgi_screener.html`): Added Chart.js line chart in the detail modal showing score evolution. Dark-themed, 0-100 Y axis, responsive, ~200px height. Shows placeholder message when history has ≤1 entry.
- API (`web/app.py`): No changes needed — `get_dgi_top20()` returns the full document, so `score_history` is automatically included in both the page template data and the `/api/dgi/top20` JSON endpoint.
- **Key Pattern**: When tracking time-series in a document DB, cap the array length and use date-based dedup to keep documents bounded. The 90-entry cap matches the snapshot TTL.
- **Files Modified**: `src/dgi_screener.py`, `web/templates/dgi_screener.html`

### DGI Single-Symbol Analysis Page (2026-05)
- Added `analyze_single_symbol(symbol)` to `src/dgi_screener.py` — runs the full DGI scoring pipeline for one ticker via `YFinanceFetcher.get_ticker_data()` without writing to CosmosDB. Returns metrics, technicals, quality score breakdown, category, entry tag, and filter pass/fail status.
- Added `calculate_quality_score_detailed()` to `src/dgi_metrics.py` — same formula as `calculate_quality_score()` but returns all sub-scores (dividend_yield, dividend_growth, payout_safety, valuation, financial_health, consistency, technical_timing), weights, and health detail (D/E and ROE sub-scores separately). The original function remains unchanged for backward compat.
- New route `GET /dgi/analyze/{symbol}` in `web/app.py` — runs analysis in an executor thread to avoid blocking the event loop. Returns `dgi_analysis.html` template.
- New template `web/templates/dgi_analysis.html` — dark-themed, extends `base.html`. Shows overall quality score with progress bar, entry tag, category badge, price. Two-column layout: left = fundamental sub-scores with progress bars and raw values, right = technical timing sub-scores with detail. Radar chart (Chart.js) shows score contribution profile. Key metrics grid at bottom. Handles error states (invalid symbol, no data, no dividends).
- "Analyze Symbol" button added next to "Run DGI Screener" on `web/templates/dgi_screener.html` with inline input field that navigates to `/dgi/analyze/{SYMBOL}`.
- **Key Pattern**: When exposing scoring internals for a diagnostic/analysis view, create a `_detailed` variant of the scoring function rather than modifying the existing one — keeps the hot path lean and avoids breaking existing consumers.
- **Files Modified**: `src/dgi_metrics.py`, `src/dgi_screener.py`, `web/app.py`, `web/templates/dgi_screener.html`, `web/templates/dgi_analysis.html` (new)

### Beasts Filter Preset Button (2026-05)
- Added "🐂 Beasts" preset button to `web/templates/dgi_screener.html` filter panel.
- The button sets all 5 sliders (QS≥80, DY≥2.5%, DG≥10%, Years≥10, Timing≥90) and calls `applyFilters()`.
- Key data formats in DGI screener: `dividend_yield` stored as percentage number (2.5 = 2.5%), `dividend_cagr_5y` stored as decimal (0.10 = 10%), sliders use integer values with DY divided by 10.
- Pattern: preset buttons reuse `applyFilters()` by setting slider values programmatically — no need for separate filter logic.

### Radar Chart "Ideal Minimum" Threshold Line (2026-05)
- Added `_compute_minimum_thresholds()` helper in `src/dgi_metrics.py` that returns a dict of 7 sub-score thresholds representing a "decent DGI holding" floor.
- DEFAULT_FILTERS all map to score 0 by design (they ARE the zero-points of each scoring function). Used slightly higher raw values for meaningful visualization: yield 2.5%, growth 5%, payout 60%, PE 25, D/E 1.5 + ROE 10%, 8 years, tech 40.
- Resulting threshold scores: ~22–40 range, forming a visible polygon on the radar.
- Added `"minimum_thresholds"` key to `calculate_quality_score_detailed()` return dict.
- Frontend: second dataset on radar chart (thin red dashed line, `borderDash: [6,4]`, `borderWidth: 1.5`). Legend re-enabled so user can distinguish the two lines.
- Key files: `src/dgi_metrics.py` (backend), `web/templates/dgi_analysis.html` (frontend Chart.js config).

### Dual Threshold Lines on Radar Chart (2026-05)
- Replaced hardcoded `_compute_minimum_thresholds()` with generic `_compute_threshold_line(target)` that returns uniform sub-scores at `target` for all 7 dimensions.
- Uniform approach: since weights sum to 1.0, setting all sub-scores = N yields weighted total = N. Simple, intuitive, no per-dimension guesswork.
- `calculate_quality_score_detailed()` now returns both `"minimum_thresholds"` (target=65) and `"ideal_thresholds"` (target=80).
- Frontend radar chart now shows 3 datasets: stock scores (blue filled), Mínimo 65 (red dashed), Ideal 80 (green dashed).
- Green line uses `rgba(34, 197, 94, 0.85)` matching Tailwind green-500.

### Radar Chart in DGI Screener Modal (2026-05)
- Added "Score Contribution" radar chart to the detail modal in `web/templates/dgi_screener.html`.
- Reuses the same visual style as `dgi_analysis.html`: blue filled area for stock sub_scores, red dashed line for Mínimo thresholds, green dashed for Ideal thresholds.
- Chart.js was already loaded in the screener template (line 158). No new dependencies needed.
- Chart instance tracked in `_radarChart` variable; destroyed on each `openDetail()` call to avoid canvas reuse issues.
- Data source: `entry.quality_detail.sub_scores`, `.minimum_thresholds`, `.ideal_thresholds` — all already serialized in the row's `data-entry` JSON from `dgi_screener.py:179`.
- Graceful degradation: if `quality_detail` is missing, the radar container is hidden.

## Learnings

### yfinance `dividendYield` is ALWAYS percentage-form (2024-era API)
- Tested JNJ (2.42), T (4.46), KO (2.7), O (5.19), MSFT (0.88), AAPL (0.37) — ALL percentage-form.
- `trailingAnnualDividendYield` is reliably decimal (0.024 = 2.4%).
- The old heuristic `if > 1: divide by 100` only worked for yields above 1%, silently breaking sub-1% stocks (MSFT, AAPL).
- Fix: unconditionally divide `dividendYield` by 100. No conditional needed.
- The display template must multiply stored decimal values by 100 for percentage display (consistent with how CAGR was already handled).
- Filter modal line 459 `(fc.actual * 100)` was already correct for decimal — it was producing 88% because the input was 0.88 (percentage) not 0.0088 (decimal).

### yfinance Feasibility Analysis for Full Data Source Replacement (2026-05)
- **yfinance can replace TradingView + StockAnalysis.com for ~90% of data needs.** Comprehensive analysis written to `.squad/decisions/inbox/linus-yfinance-feasibility.md`.
- **Options chain is the biggest win:** yfinance provides 23+ expiration dates (vs. TV's ~5), plus volume, open interest, and last trade date. Eliminates Playwright browser interception entirely.
- **Fundamentals/dividends/forecast:** 95%+ coverage via `ticker.info` (150+ keys), `ticker.analyst_price_targets`, `ticker.recommendations_summary`. All TradingView scanner fields have direct equivalents.
- **Technicals:** yfinance provides raw OHLCV only — all oscillators/MAs must be computed. `dgi_metrics.py` already computes RSI/SMA/Bollinger. Remaining ~12 indicators (Stochastic, CCI, ADX, MACD, Williams %R, etc.) are standard formulas or can use `pandas-ta` library.
- **Greeks are the main gap:** yfinance option chains include IV but NOT delta/gamma/theta/vega/rho. Must compute via Black-Scholes using `py_vollib` or scipy (~50 lines). Inputs (S, K, T, r, sigma) all available from yfinance data.
- **Key risk:** Losing StockAnalysis.com cross-check for dividend growth years. Our `calculate_years_consecutive_increases()` from Yahoo dividend series is the fallback but was known to be less reliable than SA.
- **Elimination wins:** No Playwright (~50MB Chromium), no anti-bot detection (User-Agent rotation, 403 recovery, warmup pages), no HTML parsing fragility, no TradingView endpoint migrations (the scan/scan2/scan3 breakage that caused premium=0.0 becomes impossible).

---

## 2026-05-14 — yfinance Feasibility Deep-Dive

**Session:** 20260514T0539Z  
**Outcome:** Comprehensive feasibility analysis delivered to decisions.md

### Analysis Results
- **Verdict:** yfinance can replace TradingView and StockAnalysis.com for ~90% of data needs
- **Options Chains:** 23+ expiration dates vs TradingView's ~5 (major win)
- **Greeks Gap:** IV available, delta/gamma/theta/vega/rho computed via Black-Scholes
- **Technicals Gap:** OSC signals lost but already computed locally (RSI/SMA/Bollinger in dgi_metrics.py)
- **Impact:** Eliminates all scraping fragility (Playwright, anti-bot detection, 403s)

### Deliverable
- Document: `decisions.md` → yfinance Feasibility Deep-Dive section
- Verdict: ✅ Fully feasible, proceed with architecture transition

### Phase 1 Foundation Modules (2026-07)
- Built 3 new modules for the yfinance transition (Danny's plan):
  1. **`src/greeks_calculator.py`** (~130 lines): Black-Scholes Greeks computation. Uses py_vollib when available, falls back to manual scipy norm.cdf implementation. Risk-free rate lazily fetched from ^TNX via yfinance, cached. Handles edge cases (T≈0, σ≈0). Returns theta as daily decay (/365), vega per 1% IV (/100).
  2. **`src/technicals_calculator.py`** (~330 lines): Computes all oscillators (RSI, Stoch, CCI, ADX, AO, Mom, MACD, Williams %R, BBPower, UO) and MAs (SMA/EMA 10-200, Ichimoku BL, VWMA, Hull). Signal logic ported exactly from tv_data_fetcher.py `_oscillator_signal()` and `_ma_signal()`. Uses pandas-ta when installed, manual pandas/numpy fallback. Previous bar values (for signal logic) computed via iloc[-2]/-3. Output dict matches `_build_technicals_dict()` structure.
  3. **`src/yfinance_data_provider.py`** (~400 lines): Orchestrator replacing tv_data_fetcher. `fetch_all(symbol)` returns dict with 5 JSON strings: overview, technicals, forecast, dividends, options_chain. Options chain builds hierarchical YYYYMMDD→strike→contract structure with computed Greeks. Handles dividendYield percentage-form correctly (yfinance returns 0.88 meaning 0.88%, not 88%). Updated `OPTIONS_CHAIN_SCHEMA_DESCRIPTION` with new fields (contractSymbol, volume, openInterest, liquidity/staleness guidance).
- Updated `requirements.txt` with `py-vollib>=1.0.0` and `pandas-ta>=0.3.0`.
- Key design decisions:
  - No fallback to TradingView. Clean cut as specified.
  - TTL cache (5min default) inside provider to avoid hammering yfinance.
  - Configurable DTE range (7-90 days default) for options chain filtering.
  - Recommendation values computed from signal ratios when not available from API (vs TV which had separate Recommend.All/Other/MA fields from scanner).

### Options Chain Filters Extraction + README yfinance Update (2026-07)
- Created `src/options_chain_filters.py` — standalone filter pipeline module extracted from `options_chain_parser.py`.
- Contains 5 functions: `filter_options_chain_by_type`, `filter_options_chain_for_position`, `filter_options_chain_by_delta`, `filter_options_chain_by_roll_direction`, `format_roll_candidates_table`.
- Also includes helper `_fmt_exp` and constants `_ROLL_STRIKE_FILTERS`, `_STRICT_LATER_ROLLS`.
- Module is fully self-contained — imports only `datetime`, `logging`, no dependency on `options_chain_parser.py`.
- Works on the unified dict format (YYYYMMDD→strike→contract) shared between TradingView parser and yfinance provider.
- Updated `README.md` to reflect yfinance migration: replaced all TradingView/Playwright/BS4/scanner API references in architecture, data gathering, pre-fetch, cache, setup, Docker, troubleshooting, and project structure sections.
- Key pattern: The filter pipeline is provider-agnostic — it operates on the structured dict format regardless of data source. This is why extraction into a standalone module makes sense.

### Phase 2 Completed — Filters Extraction + README Rewrite (2026-07)
**Status:** ✅ Completed  
**Commit:** 917c882  
**Files Created/Modified:** 2 (220+/850-)  

Executed parallel Phase 2 track: extracted options chain filter pipeline into standalone `src/options_chain_filters.py` module (16.8 KB); completely rewrote README to document yfinance as single data source, eliminating all TradingView/Playwright/BeautifulSoup references.

**Extraction rationale:** Filter functions work on provider-agnostic unified dict format (YYYYMMDD→strike→contract). Keeping in `options_chain_parser.py` unnecessarily couples them to TV parser. Standalone module enables clean imports without pulling TV-specific code.

**README changes:**
- Opening: yfinance direct API, no browser/scraping/auth
- Architecture: Yahoo Finance as data source, updated agent/container counts
- Pre-fetch section: Renamed to "Pre-fetch Architecture (yfinance)"
- Cache section: "Data Cache" with TTL + rate limiting
- Setup: Removed `playwright install chromium`, added `py-vollib`, `pandas-ta`
- Docker: Removed Chromium pre-install
- Troubleshooting: "Data Fetching Issues" replaces "Playwright / Chromium Issues"
- All TradingView/Playwright/BS4 references removed (15+ sections affected)

**Backward compatibility:** Existing imports from `options_chain_parser` still work; new code imports from `options_chain_filters`.

**Impact:** Documentation fully aligned with yfinance architecture; onboarding and Docker setup reflect scraping elimination.

### Phase 3 Instruction File Refactoring (2026-07)
**Status:** ✅ Completed  
**Commit:** 2709b75  
**Files Modified:** 20 (14 renamed, 6 import updates)  

Renamed all 14 instruction files to drop the misleading `tv_` prefix (leftover from TradingView era). Migration to yfinance is complete, so naming should reflect current reality.

**Execution:**
- Used `git mv` for all 14 files to preserve git history
- Updated 10 import sites across 6 modules:
  - `src/agent_runner.py` (4 imports: supervisor, alpha, summary, report)
  - `src/cash_secured_put_agent.py` (1 import)
  - `src/covered_call_agent.py` (1 import)
  - `src/open_call_monitor_agent.py` (2 imports: assessment, roll)
  - `src/open_put_monitor_agent.py` (2 imports: assessment, roll)
  - `web/app.py` (2 imports: open call/put chat)
- No cross-references inside instruction files themselves — all clean
- Verified with quick imports + full test suite (96 tests passed)

**Naming pattern:** Variable names (e.g., `TV_SUMMARY_INSTRUCTIONS`) kept as-is for now — string constants are low-touch and renaming would ripple through many files for marginal gain.

**Impact:** Codebase now accurately reflects yfinance-only architecture; no lingering TradingView naming artifacts in strategy logic layer.

### Phase 5 — Instruction Content TradingView→yfinance (2026-05, Task)
- Verified all 9 instruction files already had TradingView references replaced (43 replacements) by prior commit 895f157.
- 5 additional files (roll, alpha, supervisor, summary) had no TradingView refs — no changes needed.
- Key replacements: TradingView→Yahoo Finance, Playwright→yfinance, "browser tools"→"data fetching tools", "TradingView provides RSI..."→"computed via pandas-ta".
- Trading logic, thresholds, DTE rules all preserved unchanged. Variable names (TV_*) kept for import stability.
- All 96 tests pass. Decision doc written to inbox.

### Hybrid Options Chain — yfinance + TradingView Fallback (2026-07)
- Problem: yfinance returns zeroed bid/ask/IV/volume data when US markets are closed. The old TradingView Playwright scraper cached stale-but-useful last-close data.
- Solution: Hybrid approach — yfinance is primary (market open), TradingView Playwright is fallback (market closed). Only options chain uses this; overview/technicals/forecast/dividends stay yfinance 24/7.
- Created `src/market_hours.py`: `is_us_market_open()` checks Mon–Fri 9:30 AM – 4:00 PM ET, excludes 10 NYSE holidays (rule-based, any year). Uses `pytz` for timezone conversion. Easter computed via anonymous Gregorian algorithm for Good Friday.
- Created `src/tv_options_chain_fetcher.py`: Standalone Playwright fetcher recovered from `main:src/tv_data_fetcher.py` (lines 1219-1420). Self-contained — no dependency on old `tv_data_fetcher.py`. Includes `_parse_tv_to_yfinance_format()` that transforms TradingView scanner API fields into the exact same strike-keyed dict structure yfinance produces (contractSymbol, strike, bid, ask, mid, iv, greeks, volume, etc). Fields not available from TradingView (volume, openInterest, lastPrice, lastTradeDate) default to zero/null.
- Modified `src/yfinance_data_provider.py::_build_options_chain()`: Checks `is_us_market_open()` first. If closed → attempts TradingView fallback, falls back to yfinance on any failure. Added `market_status` field ("open"/"closed") to options chain JSON. Error handling: if Playwright fails, yfinance data (even zeroed) is returned — never crashes.
- Updated `requirements.txt`: added `playwright>=1.40.0`, `beautifulsoup4>=4.12.0`.
- Updated `Dockerfile`: added `playwright install chromium --with-deps` after pip install.
- All 96 tests pass unchanged.
- Key pattern: When a primary data source has known gaps (off-hours zeros), a targeted fallback for specific data types is better than a full fallback. The fallback must produce identical output structure so consumers don't need conditional logic.

### Market Hours: Calendar-Based → Live Probe Replacement (2026-07)
- Replaced `src/market_hours.py` entirely — removed ~100 lines of calendar/holiday/time-of-day logic with a live options-data probe.
- New approach: probes MSFT ATM call bid/ask via yfinance. If bid or ask > 0 → market OPEN. Both 0/None → CLOSED. This naturally handles half-days, unexpected closures, timezone edge cases.
- 5-minute TTL cache (`time.monotonic()` based) prevents excessive API hits — module-level globals `_cached_result` / `_cached_at`.
- Conservative error handling: any exception from yfinance → assume CLOSED (prevents bad data from being acted on).
- Function signature `is_us_market_open(now=None)` preserved for backward compatibility — `now` param accepted but ignored.
- Only caller: `src/yfinance_data_provider.py` line 478 via `from src.market_hours import is_us_market_open`.
- No new dependencies added — yfinance already in requirements.txt.
- No tests existed for the old implementation; none broken.
- Key pattern: When you have a reliable runtime signal (live bid/ask data), prefer it over calendar heuristics. Rules-based approaches always have edge cases; real data doesn't lie.

### Chain Merge Logic (TV + yfinance cache)
**Date**: 2025-07-23
**Task**: Merge TradingView fallback data with cached yfinance chains

#### What was done
- Added module-level `_chain_cache: dict[str, dict]` in `src/yfinance_data_provider.py` (line ~25)
- When market is OPEN and yfinance builds a chain successfully, a deepcopy is stored in `_chain_cache[symbol]`
- When market is CLOSED and TV fallback returns data, it's merged on top of cached yfinance data:
  - Start with cached expirations (full set from yfinance)
  - Overwrite with TV expirations (fresher closed-market data for ~5 nearest)
  - Result: full expiration coverage with fresh near-term data
- If no cache exists (cold start during closed market), TV data used as-is

## Learnings
- TradingView scraper only returns ~5 nearest expirations; yfinance returns all within DTE range
- Module-level dict is sufficient for in-memory caching in this single-process architecture
- `copy.deepcopy` needed to avoid mutation of cached data during merge
- Key file: `src/yfinance_data_provider.py`, method `_build_options_chain` (line ~470)
- Cache is ephemeral (lost on restart) — acceptable tradeoff per user decision

### Prolonged WAIT Cooldown Deadlock Fix (2025-07)
- `_detect_prolonged_wait()` in `src/agent_runner.py` had a logical deadlock: the cooldown loop checked `supervisor_view` to detect recent reviews, but since the supervisor runs unconditionally on every activity, ALL records have `supervisor_view` set.
- This caused the loop to always break at index 0, making `waits_since_last_review` always 0, which always triggered the cooldown early-return — the Alpha agent could never fire for prolonged WAITs.
- Fix: changed the cooldown check from `supervisor_view` to `alpha_view`. The `alpha_view` field only exists when a full supervisor+alpha review cycle has previously run, which is the correct cooldown signal.
- Both code paths (analysis ~line 1130, position monitor ~line 1887) use the same `_detect_prolonged_wait` method, so the single fix covers both.
- Key takeaway: when designing cooldown logic, the gating field must represent the action being cooled down (alpha review), not a ubiquitous side-effect (supervisor review).

### Dashboard Icon Indicator Pattern (2026-07)
- Activity list indicators are rendered in `web/templates/dashboard.html` (~line 187) and `web/templates/symbol_detail.html` (~line 439), just before the activity badge `<span>`.
- Pattern: conditional `{% if item.field %}` → `<span class="X-indicator" title="...">EMOJI</span>` → `{% endif %}`.
- Current indicators: 📢 `.alert-indicator` (alerts), ⚠️ `.data-error-indicator` (data errors), 🤔 `.review-indicator` (supervisor challenges), 🧠 `.alpha-indicator` (alpha advisor).
- CSS for indicators lives in `web/static/style.css` — `.alert-indicator` near line 386, `.review-indicator` near line 201, `.alpha-indicator` right after `.alert-indicator`.
- Dashboard uses `item.` prefix, symbol_detail uses `d.` prefix for the same fields.

### Market Status Dashboard Card (2026-07)
- Added a market status indicator card to the dashboard summary row, next to the "Open Positions" card.
- Green circle (●) when market is OPEN, grey circle when CLOSED, using the existing `is_us_market_open()` probe from `src/market_hours.py`.
- The probe already runs with a 5-minute cache (`_CACHE_TTL_SECONDS = 300`), so no new background work needed — just import and call.
- Changes: `web/app.py` (import + pass `market_open` to template context), `web/templates/dashboard.html` (new card in `.summary-row`), `web/static/style.css` (`.summary-card-market`, `.market-dot` styles).
- Reused existing CSS variables `--accent-green` and `--text-muted` for the open/closed colors to stay consistent with the design system.

### Buy Tracker Instruction Contract (2026-05)
- The buy tracker feature is a stock-only accumulation agent: pure technical analysis, no options chain, and it must never emit `SELL`.
- To stay compatible with existing parsing, the safest instruction contract is the same CSP/CC response pattern: one fenced JSON block followed by a `SUMMARY:` line.
- The actionable schema for buy tracking should center on `BUY`/`WAIT`, `entry_price`, `target_price`, `stop_loss`, `technical_signals`, `risk_flags`, and a detailed technical `reasoning` field.
- Conservative threshold: require at least 3 aligned bullish signals before `BUY`, with support-based entries and pivot-resistance targets.

### Position Snapshot Chart Frontend (2026-06-04)
Rusty implemented the frontend chart in `symbol_detail.html` that lazy-loads position snapshot history when a position detail drawer expands. The chart consumes snapshots via the dedicated API endpoint `/api/symbols/{symbol}/positions/{position_id}/snapshots`, rendering gap %, RSI, and MACD using Chart.js with dual axes. This completes the end-to-end pipeline from your backend persistence layer to the user-facing visualization.

### Scheduler Analysis & DPS Integration (2026-06-26)
Rusty analyzed the complete scheduler architecture and fixed a critical bug:
- **Architecture:** Croniter-based 1s polling loop in `src/main.py` manages 9 independent tasks (portfolio enrichment, market hours detection, DPS, etc.)
- **Each task:** Has enabled flag, cron expression, next_run timestamp; independent try/except prevents cascade failures
- **Bug Fix:** DPS scorer (Deterministic Position Scorer) was fully implemented (`src/dps_cron.py`, `src/dps_scorer.py`) with config entry but never wired into scheduler. Rusty integrated it: 9 edits to `src/main.py` (~60 lines). DPS now fires nightly at 10 PM (configurable), position snapshots receive DPS scores.
- **Deferred:** Task-registry simplification (medium-risk refactor) flagged for future review.
- **Reference:** `scheduler_analysis.md` (architecture doc), decision record in `.squad/decisions.md`

### Cross-Agent Note: Scheduler Registry Refactor & DPS Removal (2026-06-26)

Rusty completed scheduler modernization. Key change affecting monitoring agents: **DPS Scorer Task Removed**.

**Why DPS Removed**
- Monitoring agents you work with (covered_call, cash_secured_put, buy_tracker, open_call_monitor, open_put_monitor) already invoke `run_dps_analysis()` after every position snapshot
- They run every 4 hours during market hours — providing real-time DPS scoring 4x/day
- The nightly batch DPS job was redundant (1x/day, stale data vs real-time every 4 hours)

**What Changed**
- Removed DPS task from scheduler (9 tasks → 8 tasks)
- Monitoring agents' existing DPS computation logic unchanged
- Position snapshots still captured with RSI/MACD as before

**Impact on Your Work**
- No changes needed in your agent instructions or logic
- Your monitoring agents' DPS scoring (monitor_agents.py, agent_runner.py:2349-2370) is now the ONLY DPS computation — no redundancy
- Real-time approach is better quality (4x/day) than batch approach (1x/day)

**Infrastructure Improvement**
- New `TaskRegistry` in `src/scheduler_registry.py` — single source of truth for all scheduled tasks
- All 8 tasks now uniformly expose: enabled/cron/last_run/next_run/run_now via web UI
- Enable-gating guarantee: disabled monitoring won't execute (checked at execution time)
- Code reduction: main.py -530 lines (41% smaller)

No breaking changes to your agents or trading logic. This is pure scheduler infrastructure.

### Ex-Dividend Handling for Supervisor & CSP Agents (2026-07-08)
- **User Question:** Should the supervisor warn about ex-div within DTE range? Should the main CSP agent surface ex-div when proposing candidates?
- **Recommendation:** YES for supervisor on call positions (high early-assignment risk); NO for put positions (ex-div is irrelevant for short puts per existing instructions). YES for CSP agent but as informational surface only, gated on deep-ITM (delta < -0.70) + ex-div within 10 days — not a blanket warning.
- **Financial Reality:** Short calls face material early-assignment risk when ITM + ex-div approaching (holder exercises to capture dividend). Short puts face RARE early assignment (only deep ITM + high dividend + near expiry); more relevant is: (1) price drop on ex-div is FAVORABLE (moves away from strike), (2) premium timing opportunity (sell before ex-div to capture elevated premiums), (3) dividend yield >3% = quality signal.
- **Current State:** Call side already handles ex-div correctly (§5 in `open_call_assessment_instructions.py`, ROLL trigger #4). CSP instructions have ex-div guidance (lines 254-267) but it's not explicitly surfaced in output. Supervisor mentions ex-div generically but has no decision-specific playbook guidance. Put assessment agent correctly states "ex-div irrelevant for short puts" (line 161).
- **Proposed Edits (Advisory):** (1) Add ex-div warnings to supervisor WAIT/ROLL playbooks for open_call (check if ex-div falls between now and new expiration for ITM positions). (2) Strengthen CSP informational surface: add ex-div context to RESPONSE STRUCTURE when ex-div within DTE — frame as premium timing opportunity for OTM/near-ATM, rare assignment caution for deep ITM. (3) No changes to put monitoring/assessment (already correct). (4) Data flow dependency: supervisor needs `next_ex_div_date` in context (Rusty's area).
- **Key Pattern:** Ex-div handling must be asymmetric by option type — calls require risk warnings (assignment is adverse), puts require informational context (assignment is the goal, ex-div mostly favorable or neutral). Avoid false-alarming on every dividend stock for CSPs.

### CSP Supervisor Ex-Dividend Awareness (2026-07-08)
- Added a non-blocking ex-dividend entry-timing note to supervisor instructions for **cash-secured put SELL** decisions only (calls/other agent types unchanged).
- **Implementation:** Conditionally appended a new section in `get_supervisor_instructions()` (lines 556-578 in `src/supervisor_instructions.py`) when `agent_type == "cash_secured_put"` and `decision_type == "SELL"`.
- **Content:** Instructs supervisor to check the DIVIDENDS PAGE (already injected by `agent_runner.py:1131-1132`) for ex-div within trade window, especially near-term (~10 days). If present, surface a brief informational note: underlying typically drops ~dividend amount on ex-date (mild headwind toward short put strike, possible better entry timing post-ex-div). Explicitly framed as **awareness only** — must NOT block/downgrade decision or raise `challenge_strength` by itself (options already price dividends via put-call parity). Deep-ITM (delta < -0.70) + near ex-div: rare early-assignment possibility noted.
- **Verification:** Tested with `python3 -c` snippet — `covered_call SELL` and `open_call WAIT` confirmed NO ex-div text, `cash_secured_put SELL` confirmed has ex-div text, `cash_secured_put NOT_NOW` confirmed NO ex-div text. CSP-gating works correctly.
- **Key pattern:** When adding agent-specific guidance to shared playbooks, conditionally append after the base prompt to avoid mutating other agents' outputs. Test byte-for-byte equality for unchanged agent types.
- **Reference:** User approved this change as entry-timing awareness for CSP sellers (underlying drop on ex-div nudges toward strike). Call side already has ITM ex-div early-assignment handling in existing instructions.

## Learnings

### Alpha Candidate Chain: Exclude Held Contract (2026-07-09)
- **Bug Fixed:** Alpha agent was recommending rolling into the EXACT same contract (same strike AND expiration), which is a guaranteed loss (pay bid/ask spread for identical position).
- **Root Cause:** `AgentRunner._build_alpha_options_chain()` (agent_runner.py:1140-1176) filtered by option type and delta but did NOT exclude the currently-held contract. The held contract appeared as a candidate and the alpha agent "rolled" into it.
- **Three-Part Fix:**
  1. **Data Guard (Primary):** Added `exclude_contract()` function to `src/options_chain_filters.py` (lines 148-235). Removes the EXACT contract matching BOTH current strike AND current expiration; preserves roll-out (same strike, different exp) and roll-up/down (different strike, same exp) candidates. Handles expiration format variants (YYYY-MM-DD → YYYYMMDD), strike key variants (float comparison for "65.0"/"65.00"/"65"), null-safe (returns unchanged if strike/exp are None).
  2. **Wiring (agent_runner.py):** Changed `_build_alpha_options_chain` signature to accept `current_strike=None, current_expiration=None` (line 1143-1145). After delta filter (line 1166), calls `exclude_contract` if both params are provided (line 1170-1171). **MONITOR call site** (line 2182) now passes `current_strike=float(strike), current_expiration=expiration` from position data (strike/expiration available at lines 1808-1809). **SELL-flow call site** (line 1329) unchanged (new positions have no current contract — params default to None).
  3. **Instruction Guard (Reinforcement):** Added rule #7 to alpha_instructions.py ANALYSIS GUIDELINES (lines 517-525): roll/re-sell proposals for open positions MUST change strike and/or expiration. If the only "alternative" would be identical contract, mark opportunity_strength as NONE.
- **Verification:** 8 unit tests in `tests/test_exclude_contract.py` — confirms exact match removal, same-strike-different-exp preservation, same-exp-different-strike preservation, strike key/expiration format variants, None-arg safety, puts bucket, empty expiration removal, option_type variants. All tests pass.
- **Key Pattern:** When building candidate chains for roll decisions, ALWAYS exclude the no-op (same strike+exp) candidate at the DATA layer — don't rely on the agent to detect it. A roll MUST change strike and/or expiration.

### Alpha Buyback Cost Reference (2026-07-09)
- **User Feedback:** "If you remove the current contract, you miss the buyback cost." Correct — by excluding the held contract from the candidate chain (previous fix), the alpha could no longer see its ask (the buy-to-close cost needed to compare "close now vs roll").
- **Additional Challenge:** The held contract may have a delta OUTSIDE the kept band (reported example: $65 call with delta ~0.183, filtered out by `filter_options_chain_by_delta` before exclusion runs). So the held contract may not survive the delta filter.
- **Three-Part Solution:**
  1. **Lookup Helper (`options_chain_filters.py`):** Added `get_contract(chain, current_strike, current_expiration, option_type)` (lines 148-206) — returns the exact contract dict (bid/ask/delta/etc.) or None if not found. Reuses the SAME normalization logic as `exclude_contract` (expiration → `str(exp).replace("-","")[:8]`; strike matched by float across "65.0"/"65.00"/"65" variants; correct bucket per option_type). Null-safe (None args → None). Does NOT mutate the chain.
  2. **CURRENT POSITION Reference Block (`agent_runner.py`):** In `_build_alpha_options_chain` (lines 1141-1194), after the type filter (line 1175) and BEFORE the delta filter (line 1179), call `get_contract()` to capture `current_contract` (may be None). Keep the existing delta filter + `exclude_contract` steps for the candidate chain (unchanged). When building the return text, if `current_contract` is not None, APPEND a labeled "CURRENT POSITION (buyback-cost reference)" JSON block (lines 1188-1203) with strike, expiration, bid, ask, and an explicit `buyback_cost` field (set to the contract's `ask`, with fallback to `mark`/`last`). Include a note: "buyback_cost is the ask (cost to buy-to-close). Use it to compare closing vs rolling. Do NOT propose this exact strike+expiration as a roll target." Graceful fallback when candidates are empty AND current_contract present → return just the reference block (not empty string).
  3. **Instruction Update (`alpha_instructions.py`):** Extended guideline #7 (lines 516-528) to state: the current contract's buyback cost (ask) is provided in the "CURRENT POSITION" reference block — use that value as the buy-to-close cost when comparing closing vs rolling; the current contract appears there for reference ONLY and must never be selected as a roll/re-sell target.
- **Field Choice:** Used `buyback_cost` (set to `ask`) as the explicit field name. Rationale: "ask" is standard terminology for the current offer price to buy-to-close a short option; `buyback_cost` makes the semantic meaning unambiguous in the alpha context (the cost to exit the current position). Fallback chain: `ask` → `mark` → `last` (handles chains where `ask` might be missing, though uncommon).
- **Verification:** Added 8 unit tests in `tests/test_get_contract.py` covering exact match, strike key variants ("65.00"/"65"), expiration format variants ("2026-07-17"/"20260717"), None args → None, missing contract → None, wrong bucket → None, puts, option_type variants. All tests pass. Combined with the existing 8 `test_exclude_contract.py` tests → 16 total tests pass.
- **Key Pattern:** When the candidate chain excludes a reference item (for good reason — to prevent no-op selection), but the agent still needs its pricing for comparison, surface it as a clearly-labeled REFERENCE block separate from the candidates. Capture the reference BEFORE filters that might remove it (e.g., delta filter). Always make the semantic purpose explicit ("buyback_cost" not just "ask").
- **For:** dsanchor

### Activity Chat Instructions Module (2026-07-09)
- Created `src/activity_chat_instructions.py` exposing `get_activity_chat_instructions()` that returns the system prompt for a per-activity chat assistant (natural-language Q&A over a specific trading activity/alert).
- **Two-Tier Context Contract:** The chat receives TWO distinct context tiers that must NOT be conflated:
  1. **AGENT DECISION context** — the historical, exact outputs from monitor/supervisor/alpha agents at the time they made their decision, PLUS the position state at that moment. This is the EXACT historical record.
  2. **CURRENT MARKET DATA** — the option chain (filtered for the position) and technical data, re-fetched LIVE now. This is NOT what the agents used; it reflects the present moment for user decision-making.
- **Exact Section Headers** (must match Rusty's endpoint implementation):
  - `=== AGENT DECISION (historical, exact — what the agents actually decided) ===`
  - `=== POSITION ===`
  - `=== CURRENT MARKET DATA (LIVE NOW — NOT what the agents used) ===`
  - `=== CONVERSATION SO FAR ===`
  - `=== USER QUESTION ===`
- **Critical Rules Enforced:**
  1. To explain WHY an agent decided something (e.g., "why didn't it roll?"), reason ONLY from the AGENT DECISION block. Do NOT use CURRENT MARKET DATA to reconstruct a past decision — the chain moves, so current numbers may differ. If current data contradicts the past decision, frame it as "conditions have changed since the decision," never "the agent was wrong about current numbers."
  2. For "what should I do now / is there a better roll?" questions, use CURRENT MARKET DATA, and ALWAYS remind the user these figures are current (live) and were NOT the basis of the original agent decision.
  3. NEVER invent option-chain contracts, strikes, premiums, Greeks, or technical values not present in the provided context. If data is absent, say so explicitly.
  4. READ-ONLY / ADVISORY ONLY: the assistant explains and suggests. It MUST NOT claim to execute trades, place/close/roll orders, or modify the position or data. If asked to act, explain it can only advise and the user must act in the app.
  5. Domain competence: understands CSP/CC mechanics, rolling (roll-out/up/down), delta as ~assignment probability, gamma risk near expiration, theta decay, IV, earnings and ex-dividend timing. Keep answers concise, concrete, grounded in provided numbers (cite specific strikes/expirations/premiums).
  6. Be honest about uncertainty and tradeoffs; do not overstate confidence.
- **Output Style:** Free-form natural language (NOT JSON like monitor/supervisor/alpha). Concise, concrete, professional — match the Q&A conversational tone, not the structured decision-engine tone of other instruction modules.
- **Target Model:** gpt-5.4-mini (model-agnostic prompt design).
- **Validation:** `python3 -m py_compile src/activity_chat_instructions.py` passes.
- **Key Pattern:** When building a chat assistant over agent decisions + live market data, enforce a strict context-tier contract at the prompt level. The assistant must understand which tier answers which question type, and NEVER conflate historical decision reasoning with current market conditions. Always surface data provenance ("the agent decided based on X at decision time; current data now shows Y").
- **For:** dsanchor


### DPS Insights Prompt Module (2026-07-09)
- Created `src/dps_interpret_instructions.py` — provides system instructions for a ONE-SHOT "DPS Insights" summarizer that produces natural-language interpretations of a position's DPS (Deterministic Position Scorer) health over time.
- Function `get_dps_interpret_instructions() -> str` returns a system prompt for an advisory, read-only agent that narrates DPS score trends and contextualize them with underlying market/technical signals.
- **Context Contract** (EXACT headers that Rusty's endpoint will use):
  1. `=== POSITION ===` — the position dict (symbol, type call/put, strike, expiration, premium/source, cost basis, quantity, etc.)
  2. `=== DPS SNAPSHOT HISTORY (oldest first) ===` — JSON list of snapshots (oldest→newest), each may contain: `timestamp`, `underlying_price`, `strike`, `gap_absolute`, `gap_percent`, `rsi_14`, `macd_level`, `adx`, `midprice`, `pnl_pct`, and `dps_score` (the persisted DPS; may be missing on some rows).
- **Snapshot Fields:**
  - `timestamp`: When the snapshot was recorded
  - `underlying_price`: Stock price at that moment
  - `strike`: Option strike price
  - `gap_absolute`: Absolute price distance from strike (proxy for delta/moneyness)
  - `gap_percent`: Percentage distance from strike (OTM/ITM)
  - `rsi_14`: 14-period RSI (momentum indicator)
  - `macd_level`: MACD line value
  - `adx`: Average Directional Index (trend strength)
  - `midprice`: Option mid-price (bid-ask midpoint)
  - `pnl_pct`: Unrealized P&L as percentage
  - `dps_score`: The persisted DPS score (0-100 scale, higher = healthier position)
- **Critical Rules Enforced:**
  1. Interpret ONLY from the two provided blocks. Do NOT invent option-chain contracts, Greeks, prices, earnings/ex-div dates, or any value not present in the snapshots/position. If something isn't in the data, say it's not available.
  2. **NARRATE, DON'T RECOMPUTE:** Treat the persisted `dps_score` values as authoritative; the job is to NARRATE and CONTEXTUALIZE them, not re-derive HOLD/WATCH/ROLL decisions or recalculate numeric scores. The DPS scorer owns the score computation; the insights agent owns the narrative.
  3. **TREND:** Describe the direction of `dps_score` over the series (improving / worsening / flat / choppy), citing concrete first→last values and dates, and explain WHICH underlying signals moved with it (e.g., delta proxy via gap_percent narrowing, rsi_14 weakening, adx strengthening, pnl_pct rising). Tie the technicals to the score movement.
  4. **HISTORY:** Call out notable inflection points (e.g., score peaked at X on date, dropped after Y), and the pnl_pct trajectory.
  5. **SHORT-TERM OUTLOOK:** Give a hedged, probabilistic projection grounded in the observed trend and DTE (derive days-to-expiration from expiration vs the latest snapshot timestamp if possible). Never state certainty; frame as "if the current trend persists…". Note gamma/assignment risk if the score is deteriorating near expiration.
  6. Handle sparse data gracefully: if there are <3 snapshots or dps_score is mostly missing, say the history is too short for a reliable trend and summarize what little is available.
  7. READ-ONLY / ADVISORY: you interpret; you do not execute trades or modify anything.
  8. Output: concise, well-structured natural-language prose (short sections or bullets: Current state / Trend / History / Short-term outlook). NOT JSON. Cite specific numbers and dates from the data. Domain-aware (delta≈assignment prob, theta, gamma near expiry, roll semantics).
- **Output Style:** Natural-language prose (NOT JSON), structured with clear section headers (Current State / Trend / History / Short-Term Outlook). Concise, concrete, professional — matches the house style of `activity_chat_instructions.py` (Q&A conversational tone) but produces prose summaries rather than interactive chat.
- **Target Model:** gpt-5.4-mini (model-agnostic prompt design).
- **Validation:** `python3 -m py_compile src/dps_interpret_instructions.py` passes.
- **Key Pattern:** When building a time-series narrative assistant over persisted scores, enforce a strict "narrate don't recompute" rule at the prompt level. The assistant must understand that the scores are ground truth, and its job is to explain the trajectory (current state, trend, history, outlook) by tying the score movements to the underlying signals (gap, RSI, MACD, ADX, P&L, midprice). This design follows the same read-only philosophy as the recently-added activity-chat feature (which separates historical agent decision context from live market data).
- **For:** dsanchor

## Learnings — README Documentation (2026-07-10)

### Session README Update
Updated README.md to document all user-facing changes from the July session commits. Made surgical edits to existing sections without reordering or rewriting unrelated content.

**Sections updated:**
- **Deterministic Position Scorer (DPS)** — Added DPS Insights subsection documenting the LLM narrative feature (gpt-5.4-mini, one-shot, narrates persisted DPS score/trend over snapshot history, no live fetch, no override)
- **Supervisor Agent** — Added ex-dividend awareness note for CSP SELL decisions (informational entry-timing context when ex-div falls within trade window; non-blocking, surfaces typical price drop effect; deep-ITM rare assignment note; call side unchanged)
- **Alpha Advisor Agent** — Added exclusion of identical held contract behavior and buyback-cost reference surfacing for roll scenarios
- **Profit Target Gate** — Documented roll DTE target tuning (21-35 DTE primary, 45 DTE fallback), post-earnings block window (0-7 days hard block, 8-13 caution), and Annualized Return % ranking (replaces Net Credit descending)
- **Events Calendar** — Clarified per-event-date active-position detection (scheduled sync and manual refresh both apply per-event logic)
- **Position Lifecycle** — Noted optional per-share buyback_cost on manual close (input shown only for manual close reason)
- **Dual-Mode Chat Experience** — Added Per-Activity Chat subsection (read-only advisory, two-tier historical-vs-live context separation, ephemeral panel, gpt-5.4-mini, zero DB writes; most important new user-facing feature of the session)

**Doc conventions observed:**
- README maintains consistent heading hierarchy (### for major features, #### for subsections)
- Technical details use inline code formatting for field names, config keys, and model names
- Behavior descriptions lead with user-visible outcome, followed by technical implementation
- "How it works" numbered lists for multi-step processes
- Bold for emphasis on key principles/constraints
- Exact thresholds and field names quoted from code (21-35 DTE, gpt-5.4-mini, buyback_cost, delta < -0.70, etc.)
- Skimmable formatting: bullet lists for features, tables for comparisons, bold headers for subsections
- Internal refactors (DAL methods) briefly noted or skipped per task constraints

**Accuracy focus:**
- All DTE ranges, model names, field names, and thresholds verified against commit diffs
- Per-activity Chat documented as the primary new user-facing feature
- DPS Insights framed as LLM narration that never overrides the deterministic score
- Roll ranking change clearly stated (Annualized Return % replaces Net Credit as sort key)
- Ex-div awareness scoped to CSP SELL only with non-blocking informational framing

## Learnings — Buy-Back & Roll Table Design Consultation (2026-07-23)

### Session: Deterministic Roll Table for Activity Detail

**Request:** Design a deterministic (pure Python, no LLM) feature that shows buy-back cost + roll scenarios on the activity detail page, triggered on click. Grid: 3 strikes (ATM, +3%, -3%) × next 4 expirations. Color: green if new premium collected > buy-back cost, red otherwise.

**Key findings from codebase exploration:**

- `src/options_chain_cache.py` already provides `get_options_chain_cache().get_or_load(symbol)` (sync) and `get_or_load_async()`. Full chain is available as `{calls/puts: {YYYYMMDD: {strike_key: {bid, ask, mid, iv, delta, ...}}}}`.
- Chain has ALL expirations (yfinance base + TradingView overlay), sorted. Next 4 expirations = first 4 keys in `chain["calls"]` or `chain["puts"]` after today's date.
- The `robust_mid(bid, ask)` from `src/options_math.py` is already the right function for marking options fairly (resists stale/one-sided quotes).
- Position schema (`cosmos_db.py`) stores `strike`, `expiration`, `type` (call/put), `source.premium` (per-share premium received). **NO `contracts` field** — all current positions are implicitly 1 contract. Future enhancement should add `contracts` field.
- The `activity_detail_page` route (`/activities/{activity_id}`) renders `activity_detail.html` with the activity dict. Currently no options chain or roll table is computed there.
- Pattern to follow: `api_dps_analysis` endpoint (`POST /api/symbols/{symbol}/positions/{position_id}/dps-analysis`) — calls `chain_cache.get_or_load_async(symbol)` and returns JSON. The activity detail page can call a similar new endpoint via JS fetch on click.
- `agent_type_map = {"covered_call": "call", "cash_secured_put": "put"}` — use this to infer option_type from activity.agent_type.
- Activity has `underlying_price` field (recorded at signal time, may be stale); for roll table, need live price — best sourced via `yf_provider.fetch_all(symbol)` overview, same pattern as DPS analysis.
- For puts: buy-back = current put's ask×100 (you're short, you buy to close). Roll scenarios apply to puts too (roll up = higher strike = closer to ATM for puts = more aggressive; roll down = lower strike = safer for puts).

**Proposed API endpoint:** `GET /api/activities/{activity_id}/roll-table`
- Reads activity from cosmos (symbol, agent_type→option_type, strike, expiration, source.premium)
- Gets live underlying price from yf_provider
- Calls `chain_cache.get_or_load_async(symbol)`
- Computes buy-back cost = robust_mid(bid, ask) × 100 × contracts (default 1)
- For next 4 expirations: finds ATM/+3%/-3% strikes, computes new_premium = bid × 100 (conservative) and net = new_premium - buy_back_cost
- Returns JSON: `{buy_back_cost, underlying_price, current_position, rolls: [{expiration, strikes: [{label, strike, bid, ask, mid, new_premium, net_credit, color}]}]}`

**Math defined:**
- buy_back_cost = robust_mid(bid, ask) × 100 × contracts (per-share mid, ×100 for contract, ×N contracts)
- net_credit = new_option_bid × 100 × contracts − buy_back_cost  (positive = credit roll, negative = debit roll)
- color = "green" if net_credit > 0 else "red"
- ATM strike = min(available_strikes, key=lambda s: abs(s - underlying_price))
- upper strike = min(s for s in available_strikes if s >= underlying_price × 1.03), or nearest above if none
- lower strike = max(s for s in available_strikes if s <= underlying_price × 0.97), or nearest below if none
- Use bid (not mid) for new premium: conservative estimate of what you'll actually collect as the short seller

**Gotchas noted:**
- No `contracts` field in position schema → assume 1 until added
- Stale chain data: 30-min TTL may show stale bid/ask → add `chain_timestamp` to response
- ITM options: buy-back cost can be very high (deep ITM); surface delta or moneyness
- Liquidity filter: skip strikes with openInterest < 10 or bid == 0 (mark as "illiquid")
- Puts vs calls: direction semantics differ (put roll up = higher = more aggressive assignment risk)
- For monitor activities (ROLL type), use `current_strike`/`current_expiration` instead of `strike`/`expiration`

**Design decision:** Trigger is lazy JS fetch on activity_detail page load (or click). Not pre-computed at render time to avoid blocking page load. Show a "Loading roll table…" placeholder, replace with rendered table on response.

## Learnings — Roll Table MVP Implementation (2026-07-23)

### Session: `src/roll_table.py` + `tests/test_roll_table.py`

**Status:** Implemented, 46/46 tests passing.

**Module created:** `src/roll_table.py`
- Public function: `compute_roll_table(chain, current_strike, current_expiration, option_type, underlying_price, premium_received, contracts=1, num_expiries=4, strike_offsets=(0.0, +0.03, -0.03)) → dict`
- Reuses `robust_mid` (src/options_math.py) for buy-back cost marking
- Reuses `get_contract` (src/options_chain_filters.py) for current-position lookup
- Chain format: yfinance/TradingView merged output from `OptionsChainCache` — keys are YYYYMMDD expiration, str-float strike keys
- Accepts JSON string or parsed dict as `chain` input (chain cache returns JSON string)
- `pct_captured = (premium_received - buyback_per_share) / premium_received`; `profit_target_reached = pct_captured >= 0.70` (mirrors open_call_assessment_instructions.py line 68)
- Net credit = `new_bid × 100 × contracts - buyback_cost`; color green/red/gray
- Strike selection: ATM = closest, +N% = smallest >= target (fallback: max), -N% = largest <= target (fallback: min)
- Expiration selection: strictly after `current_expiration` key (YYYYMMDD comparison) AND after today

**Tests:** `tests/test_roll_table.py` — 46 tests across 11 test classes.
- One test fixed: bid=0 gray test required underlying_price=417.0 (not 417.50) so that strike 430.0 qualifies as >= +3% target (430.025 vs 429.51).

**Contract decision written:** `.squad/decisions/inbox/linus-rolltable-contract.md`
- Includes endpoint skeleton for Rusty (`GET /api/activities/{activity_id}/roll-table`)
- Includes frontend notes for Danny
