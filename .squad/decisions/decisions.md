

---


### Context Injection for Agent Execution (2026-03-28T13:48)
**By:** dsanchor (via Copilot)  
**What:** Include last 2 decisions (configurable 0–5) for the symbol/position being analyzed. Signals embedded in decisions, not separate context.  
**Why:** Simplifies context injection model. Decisions are primary unit.  
**Impact:** Changes `src/context.py`, `config.yaml` defaults, agent runner context injection logic.  
**Status:** ✅ Implemented in Phase 2

---

## Backend / Infrastructure

### Switch from Azure CLI Credential to API Key Authentication
**Date:** 2025-07-XX  
**Decider:** Rusty (Backend Dev)  
**Status:** Implemented

Switched from `AzureCliCredential` to API key authentication for Azure OpenAI. Simpler user setup (env var only), better Docker compatibility, reduced dependencies. Updated `AzureOpenAIChatClient`, `config.yaml`, `requirements.txt`, and documentation.

**Files Changed:** `src/agent_runner.py`, `src/config.py`, `src/main.py`, `config.yaml`, `requirements.txt`, `README.md`  
**Commit:** c502632

---

### Dockerfile Architecture for Playwright MCP
**Date:** 2025-07  
**Agent:** Rusty  
**Status:** Implemented

Single-stage `python:3.12-slim` base with Node.js installed via NodeSource. Pre-caches Playwright browsers during build. ENTRYPOINT pattern allows natural flag appending (`--web-only`, `--port`).

**Volume mount contract:**
- `data/` — Watchlists, position files (read-write)
- `logs/` — JSONL decision/signal logs (read-write)
- `config.yaml` — Configuration (read-only)
- `~/.azure` — Azure CLI credentials (read-only)

---

### Render-time Signal Enrichment from Decisions
**Author:** Rusty  
**Date:** 2025-07  
**Status:** Implemented

Enrich signals at render time in `web/app.py` by matching each signal to the closest decision (same symbol key, ±2 hour window). Helper `_enrich_signal_from_decisions()` copies only missing fields. Keeps signal JSONL compact.

---

## Performance & Reliability

### Chat Context Preload Pattern
**Date:** 2025-07  
**Author:** Rusty (Agent Dev)  
**Status:** Implemented  

Per-symbol chat previously fetched CosmosDB config + last 5 activities + TradingView data on every message (~5-10s latency). Split into two endpoints:

1. `POST /api/symbols/{symbol}/chat/context` — Heavy data fetch, runs once on page load
2. Chat message endpoint — Accepts optional `context` field, uses cached context if provided

**Result:** Chat response time drops from ~8-12s to ~2-3s per message (after initial load).

**Key Choices:**
- POST (TradingView fetch is a side effect)
- Optional context field (backward compatible)
- Extracted helpers `_build_symbol_context()` and `_build_symbol_system_prompt()` to avoid duplication

---

## Data Management

### Runtime Telemetry Infrastructure
**Date:** 2025-07  
**Author:** Rusty (Agent Dev)  
**Status:** Implemented  

Added a second CosmosDB container (`telemetry`) to track runtime performance stats for agent executions and TradingView data fetching.

**Design:**
- Separate container with partition key `/metric_type` (operational data separate from business data)
- Best-effort initialization (system works without telemetry container)
- 30-day TTL on all telemetry docs (per-document, no manual cleanup)
- Fetcher stores stats in `self.last_fetch_stats`; caller (AgentRunner) handles write (decoupling)
- Telemetry writes post-execution in separate try/except (never masks real errors)
- Python-side aggregation for `get_telemetry_stats()`

**Impact:** Settings page shows runtime stats card; no changes to agent logic or activity/signal flow.

---

### Position Rollover Design

#### Roll Position — Atomic Single-Write
**Date:** 2025-07  
**Author:** Rusty (Agent Dev)  
**Status:** Implemented  

Rolling a position (close old, open new with same monitor-agent signal) happens with a single in-memory operation and single `replace_item` CosmosDB call. Avoids partial-write states.

**Traceability:**
- Old position → `rolled_to: <new_position_id>` + `closing_source: {snapshot}`
- New position → `rolled_from: <old_position_id>` + `source: {snapshot}`
- Both snapshots reference same `decision_id` from monitor signal

**No Watchlist/Cascade Side Effects:** Unlike "Open Position from Activity" (watch agents), roll endpoint does NOT disable watchlist flags or cascade-delete activities. Monitor agents track open positions — disabling would break monitoring.

---

#### Cascade runs after watchlist flag is persisted
**Date:** 2025-07  
**Author:** Rusty (Agent Dev)  
**Status:** Implemented  

When a user toggles off a watchlist agent (covered_call or cash_secured_put), cascade delete runs AFTER the watchlist document update is persisted (`replace_item`), not before.

**Reasoning:** If cascade fails mid-way, flag is already `False` — UI correctly shows agent as disabled. Orphaned activities/signals are harmless and would be cleaned up on subsequent toggle-off or manual cleanup.

---

### Render-time Alert Enrichment from Activities
**Date:** 2025-07  
**Author:** Rusty (Agent Dev)  
**Status:** Implemented  

Enrich alerts at render time in `web/app.py` by matching each alert to the closest activity (same symbol key, ±2 hour window). Helper `_enrich_alert_from_activities()` copies only missing fields. Keeps alert JSONL compact.


---

## Data Fetching & Backend Architecture

### Refactor TradingView Fetchers from Playwright to BeautifulSoup + Scanner API
**Date:** 2026-07-14  
**Author:** Rusty (Backend Dev)  
**Status:** Implemented  
**Impact:** Performance, reliability, resource usage  

All 5 TradingView data fetchers in `src/tv_data_fetcher.py` used Playwright (headless Chromium) to load full pages and extract innerText. This was heavyweight — every fetch launched browser tabs, waited for networkidle, and pulled raw unstructured text.

Test scripts (`test/test_fetcher.py`, `test/test_dividends_fetcher.py`, `test/test_technicals_fetcher.py`, `test/test_forecast_fetcher.py`) proved that 4 of 5 resources could be fetched via plain HTTP requests + BeautifulSoup, with a scanner API fallback.

**Decision:** Switch overview, technicals, forecast, and dividends to **requests + BeautifulSoup + TradingView scanner API**. Keep Playwright **only** for options chain (requires browser-level API interception).

**Key Changes:**
1. **4 fetchers refactored** — multi-strategy: HTML extraction → embedded JSON → scanner API
2. **Options chain unchanged** — still Playwright with response interception
3. **Lazy browser init** — Playwright only starts when options chain is needed
4. **Structured JSON output** — fetchers return `json.dumps()` with typed fields instead of raw page text
5. **Added `beautifulsoup4>=4.12.0`** to requirements.txt

**Trade-offs:**
- **Pro:** ~10x faster fetches (no browser startup), lower memory, structured data for LLM analysis
- **Pro:** Playwright failure modes (timeouts, consent banners, JS rendering) eliminated for 4/5 resources
- **Pro:** Lazy browser means Playwright isn't loaded at all when options chain isn't requested
- **Con:** Depends on TradingView's HTML structure / scanner API stability (same as test scripts)
- **Con:** Return format changed from plain text to JSON string — callers that parsed raw text may need adjustment (current callers just pass strings through, so no impact)

**Implications:**
- All callers (`agent_runner.py`, `web/app.py`, agent wrappers) are unchanged — they call `fetch_all()` which returns `dict[str, str]`
- LLM agents now receive structured JSON instead of raw page dumps — potentially better analysis quality
- If TradingView changes their scanner API or page structure, the 3-strategy fallback provides resilience

---

### Use Playwright Locators for Targeted Data Extraction
**Date:** 2025-01-XX  
**Author:** Rusty (Backend Dev)  
**Status:** Implemented  

The original `fetch_overview` method grabbed the entire `#tv-content` innerText, which returned excessive noise. We needed a more surgical approach to extract only the "Fundamentals and stats" section.

**Decision:** Rewrote `fetch_overview` to use Playwright's locator API:
1. Locate the H1 element containing "Fundamentals and stats"
2. Traverse to its parent container using `.locator('..')`
3. Extract only that container's inner text

**Implementation Details:**
- Uses `page.locator('h1:has-text("Fundamentals and stats")').locator('..')`
- Includes fallback to old `_fetch_page_text()` approach if locator fails
- Maintains retry wrapper compatibility
- Proper page lifecycle management with finally block

**Rationale:**
- Reduces noise in overview data by targeting specific DOM section
- More resilient than hardcoded CSS selectors (semantic text-based targeting)
- Graceful degradation ensures system doesn't break if page structure changes
- Pattern can be applied to other fetch methods if similar issues arise

**Impact:**
- Overview data should be cleaner and more focused on fundamental metrics
- Slightly more complex code, but better failure handling with fallback
- No breaking changes to return format or API contract

**Future Considerations:**
- Monitor success rate of locator approach vs. fallback usage
- Consider applying same pattern to `fetch_technicals`, `fetch_forecast`, etc. if they have similar noise issues
- If TradingView changes page structure, fallback ensures continuity

---|---|---|
| >30 days | Open normally | None |
| 15-30 days | Allow if exp ≥7d before earnings | `earnings_approaching` |
| 7-14 days | Allow if exp ≥5d before earnings (caution) | `earnings_soon` |
| <7 days | Block | `earnings_imminent` |
| 0-2 days after | Ideal | None |
| Unknown | Conservative DTE (<21d CC, <30d CSP) | `unknown_earnings` |

**Rationale:** The risk is the position being open during earnings, not earnings existing on the calendar. Pre-earnings IV inflation produces better premiums — sellers should capture this when expiration is safely before the event.

**Files Changed:**
- `src/tv_covered_call_instructions.py` — 6 sections updated
- `src/tv_cash_secured_put_instructions.py` — 6 sections updated

---

### Alert Pre-fill Pattern for Position Forms

**Date:** 2026-07  
**Author:** Rusty  
**Status:** Implemented  

Alert data for position pre-fill is embedded as JavaScript objects in the page (via `latest_sell_alerts` template variable) rather than fetched via API. The backend extracts the latest SELL alert per watchlist agent type from the already-fetched alerts list — no extra CosmosDB query needed.

**Rationale:**
- Instant UX: checking the checkbox fills the form with zero latency
- No new API endpoints to maintain
- Reuses the same alert field set as the existing `from-activity` endpoint's `source` dict
- The checkbox is purely a convenience — it pre-fills but doesn't create any link between the position and the alert

**Pattern:**
- `latest_sell_alerts` dict keyed by agent_type (`covered_call`, `cash_secured_put`)
- Frontend maps position type to agent type: `call → covered_call`, `put → cash_secured_put`
- Checkbox visibility gated by BOTH watchlist enabled AND alert exists with valid strike+expiration

---

### Alert Checkbox Attaches Source Metadata, Does Not Pre-fill Form

**Date:** 2026-07  
**Author:** Rusty  
**Status:** Implemented  

The alert checkbox on the Add Position form was pre-filling strike/expiration/notes from alert data. User wanted it to transparently attach alert source metadata instead, with no form field changes.

**Decision:**
- Checkbox sends `source_activity_id` in POST body to `/api/symbols/{symbol}/positions`
- Backend looks up the activity and builds the same `source` dict used by the from-activity route
- Source metadata is stored on the position document but does NOT affect form fields
- No side effects: no watchlist disable, no cascade-delete

**Files Changed:**
- `web/app.py` — `api_add_position` route accepts optional `source_activity_id`
- `web/templates/symbol_detail.html` — removed `applyAlertPrefill()`, updated label and submit logic

---

### Monitor Earnings Decision Matrix

**Author:** Linus (Quant Dev)  
**Date:** 2026-07-16  
**Status:** Implemented  
**Commit:** f587058

The analysis agents had a sophisticated Earnings Decision Matrix but the monitor agents had only 3-5 lines of vague earnings handling. A monitor recommended selling a call with expiration AFTER earnings without flagging it.

**Decision:** Port the Earnings Decision Matrix to both monitor instruction files, adapted for monitoring (HOLD/FLAG/ROLL/CLOSE) rather than opening (OPEN/AVOID/BLOCK).

**Key Design Choices:**
1. **Same tier structure** as analysis agents (>30d, 15-30d, 7-14d, <7d, just-passed, unknown) for consistency
2. **Expiration-vs-earnings is the primary axis**: "Does my position span earnings?" drives the action
3. **Urgency escalation**: FLAG → ROLL recommended → ROLL urgently → CLOSE as earnings get closer
4. **Override rule**: When earnings risk is urgent and position spans earnings, it overrides favorable Greeks
5. **Legacy compatibility**: `earnings_before_expiry` retained as alias for `earnings_within_dte`
6. **Put-specific additions**: earnings miss gap risk and downgrade clustering added to put monitor only

**Impact:**
- Both monitor agents now apply the same earnings risk rigor as analysis agents
- Risk flags are consistent across all 4 agent types (CC, CSP, call monitor, put monitor)
- JSON output examples updated to demonstrate correct flag usage

**Files Changed:**
- `src/agents/tv_open_call_instructions.py` — 10-tier earnings assessment, risk flags, HOLD/FLAG/ROLL/CLOSE actions
- `src/agents/tv_open_put_instructions.py` — 10-tier earnings assessment with put-specific gap risk and downgrade handling

---

### Client-side Markdown Rendering for Chat

**Date:** 2026-07-22  
**Author:** Rusty  
**Status:** Implemented  
**Commit:** 23c0817  

LLM chat responses contain markdown formatting (`**bold**`, `# headers`, `- lists`, tables) that was displayed as raw text.

**Decision:**
- Use `marked.js` via CDN (`https://cdn.jsdelivr.net/npm/marked/marked.min.js`) loaded in `base.html`
- Only assistant messages get markdown rendering; user messages remain plain text
- Assistant bubbles get `.markdown-body` class with dedicated CSS for tables, code blocks, lists, headers, blockquotes
- Graceful fallback: if `marked` fails to load, messages render as plain text

**Files Changed:**
- `web/templates/base.html` — added marked.js CDN script tag
- `web/templates/chat.html` — updated `addMessage()` for markdown rendering
- `web/templates/symbol_chat.html` — same update
- `web/static/style.css` — added `.markdown-body` and child element styles

---

### Risk & Moneyness columns on Symbol Detail positions table

**Date:** 2026-07-15  
**Author:** Rusty (Agent Dev)  
**Status:** Implemented  
**Commit:** e8d56c8

The dashboard already showed assignment_risk and moneyness for position monitors, but the symbol detail page's positions table lacked this data — requiring users to scroll through activities to find the risk status of each open position.

**Decision:** Enrich positions in the symbol detail route by scanning the already-fetched activities for the latest monitor activity per `position_id`. Attach `_assignment_risk` and `_moneyness` as transient fields (underscore prefix = not persisted). Reuse existing CSS classes from the dashboard.

**Impact:**
- **Files changed:** `web/app.py`, `web/templates/symbol_detail.html`, `web/static/style.css`
- **No new queries:** Reuses the activities already fetched for the page; zero additional CosmosDB calls.
- **Fallback:** Positions without monitor data show "—" gracefully.

---

### Settings must always be read from CosmosDB first

**Date:** 2026-07  
**Author:** Rusty  
**Status:** Implemented  

On deploy, user-configured settings (scheduler cron, timezone, telegram config) were being displayed incorrectly on the dashboard. The root cause: the dashboard route read settings from `config.yaml` (which resets to defaults with every new container image), while the Settings/Config page correctly read from CosmosDB.

**Decision:** All web routes that display or use user-configurable settings MUST read from CosmosDB first, falling back to `config.yaml` only if CosmosDB is unavailable.

**Pattern:**
```python
cosmos_settings = _load_settings_from_cosmos(cosmos)
config = cosmos_settings if cosmos_settings else _load_config()
```

**Exceptions:** Connection credentials (`azure.*`, `cosmosdb.*`) should still come from `config.yaml` / env vars since they are infrastructure config, not user settings.

**Changes Made:**
- `web/app.py` dashboard route: CosmosDB-first for scheduler cron/timezone
- `web/app.py` telegram test route: CosmosDB-first for telegram settings
- `web/app.py` settings save: now passes timezone to `scheduler.reschedule()`

**Notes:**
- The `merge_defaults()` logic in `cosmos_db.py` is correctly implemented (never overwrites existing keys)
- The startup banner in `run.py` still reads from `config.yaml` since CosmosDB isn't initialized yet — cosmetic only, acceptable


---

### Unified Mandatory Earnings Gate

**Date:** 2026-07-09
**Author:** Linus (Quant Dev)
**Status:** Implemented
**Impact:** Team-wide (changes agent output schema)

The LLM was ignoring the Earnings Decision Matrix (added in ccf299a) because it was buried at section 8/9 among many other analytical sections. A watcher agent recommended selling a call with expiration AFTER earnings, with no earnings flag. The matrix wasn't prominent enough for the LLM to consistently follow.

**Decision:** Created a **MANDATORY EARNINGS GATE** that runs as the FIRST analytical step (before technicals, volatility, fundamentals) in all 4 instruction files.

**Key Design Choices:**

1. **Gate-before-analysis architecture**: The earnings check is a pre-flight gate, not one section among many. If it returns BLOCKED, the agent must output WAIT/CLOSE immediately — it never reaches the technical analysis.

2. **Mandatory `earnings_analysis` output object**: Every JSON response now MUST include an `earnings_analysis` object with computed fields (next_earnings_date, days_to_earnings, expiration_to_earnings_gap, earnings_gate_result, earnings_risk_flag). This forces the LLM to explicitly reason about earnings timing before recommending. If it must fill in the fields, it can't skip the logic.

3. **HARD OVERRIDE RULE**: Explicit language stating no combination of bullish technicals, strong fundamentals, or favorable IV can override a BLOCK. This is a binary gate, not a weighted factor.

4. **Unified terminology across all 4 files**: Same thresholds, same flag names, same matrix structure. Only the actions differ (watchers: OPEN/WAIT; monitors: HOLD/ROLL/CLOSE).

**Implications for Team:**

- **Rusty (Infrastructure)**: The JSON output schema now includes `earnings_analysis` as a required field. If any downstream parsing depends on the schema, it should be updated to expect this new object. The agent_runner's JSON extraction should handle it transparently since it parses the full JSON block.

- **Schema change**: All agents now output `earnings_analysis` in every response. This is a non-breaking addition (new field, existing fields unchanged).

- **Testing**: Recommend testing with a symbol where earnings are 10-20 days away to verify the gate correctly blocks positions that span earnings.

**Files Changed:**
- `src/tv_covered_call_instructions.py`
- `src/tv_cash_secured_put_instructions.py`
- `src/tv_open_call_instructions.py`
- `src/tv_open_put_instructions.py`

**Commit:** b51ea1a

------|------------|-----------|
| Session creation overhead | Medium | ~50ms per session, negligible vs. 5–15s network I/O per symbol |
| IP-based blocking persists | Low | Monitor 403 rates; if problem persists, user deploys with proxy rotation (out of scope v1) |
| Exponential backoff increases runtime | Medium | Worst case 3 retries × 45s per symbol; for 50 symbols with 10% failure rate, adds ~11 min total (acceptable for 4-hour cron) |
| Randomization breaks determinism | Low | Already configurable; keep default true (high value) |

### Configuration Changes Summary

**New `config.yaml` section (Phase 1–4):**
```yaml
tradingview:
  request_delay_min: 1.0         # Existing
  request_delay_max: 3.0         # Existing
  
  # NEW: Anti-403 settings
  warmup_enabled: false          # Phase 4: Visit homepage before fetching
  max_403_retries: 3             # Phase 2: Retry attempts on 403
  retry_delays: [5, 15, 45]      # Phase 2: Exponential backoff (seconds)
  randomize_symbols: true        # Phase 3: Shuffle symbol order
```

**Defaults:** Conservative (warmup off, randomize on)

### Implementation Checklist

- [ ] Phase 1: Core Session Isolation (Rusty)
  - [ ] Refactor 4 agent wrappers — move `async with create_fetcher()` inside symbol loop
  - [ ] Remove `self.has_403` from fetcher
  - [ ] Update `fetch_all()` to return `{"error_403": "message"}` in result dict
  - [ ] Update `agent_runner.py` to check `data.get("error_403")`
  
- [ ] Phase 2: Graduated Recovery (Rusty)
  - [ ] Replace `_check_403()` with `_fetch_with_403_recovery()` implementing exponential backoff + session refresh
  - [ ] Update all `self._session.get()` calls to use new recovery method
  - [ ] Add config: `max_403_retries`, `retry_delays`
  
- [ ] Phase 3: Symbol Randomization (Rusty)
  - [ ] Add `random.shuffle(cc_symbols)` in all 4 agent wrappers after `cosmos.get_*_symbols()`
  - [ ] Add config: `randomize_symbols` (default: true)
  
- [ ] Phase 4: Optional Warm-Up (Rusty)
  - [ ] Add `_warmup()` method to `TradingViewFetcher`
  - [ ] Call `await self._warmup()` at start of `fetch_all()` if enabled
  - [ ] Add config: `warmup_enabled` (default: false)
  
- [ ] Testing (Basher)
  - [ ] Verify each symbol gets fresh session
  - [ ] Simulate 403 (firewall block); verify retry + recovery
  - [ ] Verify symbol order randomized when enabled
  - [ ] Monitor 403 rates before/after deployment

### Alternatives Considered (and Rejected)

| Alternative | Why Rejected |
|-------------|-------------|
| Proxy rotation | Requires external infrastructure. If IP-based blocking persists, user can deploy to multiple VMs later. |
| Playwright for all resources | 10x slower (~15s vs ~1s per resource); would increase runtime from ~5min to ~40min for 50 symbols. Only justified for options chain (requires browser API interception). |
| Per-resource session rotation | Too granular (5 resources × 50 symbols = 250 sessions likely triggers rate-limiting). Per-symbol isolation is sweet spot. |
| TradingView API keys | TradingView doesn't offer public API for retail users. |

### Approval & Next Steps

**Decision:** Pending user (dsanchor) review and approval.

**If Approved:**
1. Rusty implements Phase 1–2 (core changes)
2. Basher validates with real TradingView access
3. Monitor 403 rates for 1 week post-deployment
4. Deploy Phase 3–4 if additional improvement needed

**Alignment with Team Proposals:**

Danny's "Per-Symbol Session Isolation" proposal prioritizes simpler per-symbol lifecycle (always fresh session per symbol). Linus's "TV Fetcher 403-Resilience Analysis" proposes hybrid with request-count thresholds.

**Resolution:** Both converge on core strategy. Danny's simpler approach preferred for MVP implementation; Linus's threshold optimization can be Phase 5 enhancement if needed.

### Related Files

- `.squad/decisions/inbox/danny-anti403-strategy.md` (detailed Danny proposal)
- `.squad/decisions/inbox/linus-anti403-implementation.md` (detailed Linus proposal)
- `.squad/orchestration-log/2026-04-06T14-00-danny-antibot.md` (Danny task log)
- `.squad/orchestration-log/2026-04-06T14-00-linus-antibot.md` (Linus task log)
- `.squad/log/2026-04-06T14-00-anti403-strategy.md` (Session summary)

---

## Symbol Position Report — LLM Endpoint Pattern

**Date:** 2026-07  
**Author:** Rusty (Agent Dev)  
**Status:** Implemented

### Decision

Added a new LLM report generation endpoint (`POST /api/symbols/{symbol}/report`) that follows the same Azure OpenAI calling pattern as `symbol_chat_api` but with:
- Higher `max_completion_tokens` (4096 vs 2048) for comprehensive reports
- Per-agent-type activity loading (last 3 each) instead of mixed last-3-overall
- Cache-only TradingView data (no `force_refresh`) for speed
- Structured Spanish-language system prompt with 7 mandatory sections

### Rationale

- Reports need more token budget than chat replies since they cover all sections
- Per-agent activity breakdown gives better context than the chat's mixed approach
- Using cache avoids 30-60s TradingView fetches during interactive report generation
- Spanish output matches the user's communication language

### Impact

- `web/app.py`: New endpoint between fetch-preview page route and fetch-preview API
- `web/templates/symbol_detail.html`: Report button + modal overlay + markdown renderer
- No changes to existing endpoints or data models

### Related Files

- `.squad/orchestration-log/2026-04-18T0827-rusty-report.md` (Agent routing log)
- `.squad/log/2026-04-18T0827-symbol-report.md` (Session summary)


---

## Centralized Options Chain Schema Description

**Date:** 2026-07-25  
**Author:** Linus (Quant Dev)  
**Status:** Implemented

### Decision

All agent prompts, chat contexts, and reports that include options chain data now prepend a standardized schema description (`OPTIONS_CHAIN_SCHEMA_DESCRIPTION` from `src/options_chain_parser.py`). This ensures every LLM receiving options chain data knows the JSON structure and field semantics.

### Rationale

Agents were receiving raw JSON without context on what fields mean — especially critical fields like `iv` (decimal, not percentage), `delta` (sign convention), and `theta` (negative = decay). This led to potential misinterpretation in analysis.

### Impact

- The constant is defined once in `options_chain_parser.py` and imported everywhere
- Any new injection point for options chain data should import and prepend `OPTIONS_CHAIN_SCHEMA_DESCRIPTION`
- No logic changes — only documentation added to prompts

---

## Filter TradingView Scanner Responses by totalCount

**Date:** 2026-07-25  
**Author:** Linus (Quant Dev)  
**Status:** Implemented  
**Impact:** Data quality for options chain fetching

### Context

The `fetch_options_chain` method intercepts TradingView scanner API responses matching `_OPTIONS_SCAN_URLS`. One of the intercepted responses contains `totalCount: 1` — it's metadata/noise, not actual option chain data. This polluted the captured data sent downstream.

### Decision

Filter responses at capture time inside `_on_response`: parse the JSON body and discard any response where `totalCount <= 1`. Responses that fail JSON parsing are kept (safe default).

### Rationale

- Filtering at the callback level prevents garbage from ever entering `captured_responses`
- `totalCount > 1` is a reliable discriminator: real option chain data always has multiple rows
- Non-JSON responses are allowed through as a safe fallback (shouldn't happen for these endpoints, but defensive)

### Files Modified

- `src/tv_data_fetcher.py` — `_on_response` callback in `fetch_options_chain`

# Decision: Enforce Hard 45 DTE Cap for New Positions

**Date:** 2026-07  
**Author:** Linus (Quant Dev)  
**Status:** ✅ Implemented  
**Impact:** Agent instructions (CC + CSP), user-facing recommendations

## Problem

User reported seeing expiration recommendations with DTE > 45 days too frequently. Root cause: instructions said "Optimal: 30-45 DTE" but "Avoid: >60 DTE", creating a 46-60 day gap the LLM treated as permitted. Additionally, the earnings gate post-earnings path could push expirations beyond 45 DTE.

## Decision

45 DTE is now a **hard maximum** for all new covered call and cash-secured put positions. If no expiration ≤45 DTE passes all criteria (earnings gate, Greeks, premium), the agent outputs WAIT instead of extending to a longer-dated expiration.

## Changes Made

Both `src/tv_covered_call_instructions.py` and `src/tv_cash_secured_put_instructions.py`:
1. Replaced "Avoid >60 DTE" with "⛔ HARD MAXIMUM: 45 DTE"
2. Added DTE ≤ 45 guard to earnings gate post-earnings allowance row
3. Added 45 DTE cap to DTE Selection Priority and KEY PRINCIPLE sections
4. Added "No Eligible Expiration ≤ 45 DTE" as explicit WAIT trigger
5. Added "Never exceed 45 DTE" to Theta descriptions

## Follow-up Needed

- Alpha Vantage and Massive.com instruction files likely have the same ">60 DTE" gap — should be audited and patched.

---

# Decision: Risk fields in Telegram notifications

**Date:** 2026-07
**Author:** Rusty
**Status:** Implemented

## Context
Telegram sell alerts already displayed `risk_rating` but it was never passed in the alert_data dict from agent_runner. Roll alerts had no risk info at all.

## Decision
- Sell alert_data now includes `risk_rating` (from agent JSON output)
- Roll alert_data now includes `assignment_risk` (from agent JSON output)
- Roll alert formatting shows "Assignment Risk: {value}" when present
- Summary agent instructions now guide inclusion of risk levels for notable cases

## Convention
When adding new fields to Telegram notifications, ensure both sides are wired: the alert_data dict in agent_runner.py AND the formatter in telegram_notifier.py. Fields should be optional (guarded with `if X is not None`) for backward compatibility.

---

# Decision: Always filter options chains by delta before passing to agents

**Date:** 2026-04-22  
**Author:** dsanchor (User)  
**Status:** Implemented  
**Initiated by:** Copilot directive 2026-04-22T05:01:55Z

## Context
Agents receive full options chains which include deep ITM/OTM contracts with extreme or missing deltas. These contracts are noise — agents rarely recommend them and they bloat the context window. User directive to standardize filtering across all data flows.

## Decision
Apply a delta filter in `_format_options_chain()` after position filtering but before JSON serialization:
- **Calls:** keep delta 0.15–0.90
- **Puts:** keep delta -0.60 to -0.15
- **Missing delta:** excluded

Default ranges are configurable via function parameters if future agents need different windows.

## Impact
- Reduces token usage in agent prompts (fewer contracts serialized)
- Agents focus on the most actionable strike range
- No behavioral change for agents that were already ignoring extreme-delta contracts

---

# Decision: Dashboard tables show activity timeline badges instead of count columns

**Date:** 2026-04-22  
**Author:** Rusty (Agent Dev)  
**Status:** Implemented  
**Commit:** be9c812

## Context
The dashboard agent tables previously showed Today/7d/30d alert count columns which were low-signal — a count of "3" doesn't tell you *what* happened. The activity feed below already had rich colored badges showing actionable results.

## Decision
- Replaced 3 count columns with a single "Recent" column containing up to 3 activity badges (oldest→newest) per row.
- Badges use the same `badge-{{ activity | lower }}` CSS classes as the activity feed for consistency.
- Each badge links to its activity detail page and has a timestamp tooltip.
- Removed the top summary cards for Alerts Today/7d/30d (kept Symbols Watched + Open Positions).
- `grand_totals` dict is preserved in the backend for any future use but no longer rendered.

## Trade-offs
- Loses exact numeric counts per time range (can still be seen in the activity feed with filters).
- Gains at-a-glance pattern recognition: seeing `[WAIT] › [WAIT] › [SELL]` is immediately actionable.

## Files Modified
- web/templates/dashboard.html
- web/app.py
- web/static/style.css

---

# Decision: Monitor Agent Split — Position Monitor + Roll Management

**Date:** 2026-04-22
**Author:** Danny (Lead)
**Status:** Recommended — awaiting user approval
**Impact:** Agent instructions, agent_runner.py, runner execution flow, instruction files

## Proposal (from dsanchor)

Split each monitor agent (open_call_monitor, open_put_monitor) into two sequential agents:
1. **Position Monitor** — Decides WAIT vs action (CLOSE/ROLL). Ends at WAIT.
2. **Roll Management** — When action ≠ WAIT, takes over to determine specific strike, expiration, premium using the option chain.

Goal: Simplify each agent's context so the roll agent focuses on reading the chain correctly, fixing credit miscalculations.

## Assessment

### Root Cause of Credit Miscalculation

The current monitor instructions are **589 lines (call) / 603 lines (put)**. A single agent must:

1. Run a 25-row earnings decision matrix (lines 62–100)
2. Execute 8 analysis dimensions (moneyness, DTE, delta/gamma, volume, ex-div, earnings, technicals, IV — lines 174–258)
3. Apply profit optimization gates (3 mandatory + 4-of-7 flexible conditions — lines 294–323)
4. **Then**, for ROLL decisions, correctly look up specific contracts by JSON key path (e.g., `calls["20260427"]["475.0"]["ask"]`)
5. Calculate roll economics arithmetic (buyback_cost - new_premium) per the premium-first policy (lines 331–393)
6. If initial candidate fails, run the roll search algorithm across multiple strikes/expirations (lines 369–378)

The options chain schema description (`OPTIONS_CHAIN_SCHEMA_DESCRIPTION` in `options_chain_parser.py`, 75 lines) is prepended to the chain JSON in the user message. The roll economics verification rules are buried at line ~340 of 589 total instruction lines.

**Diagnosis:** The model's attention is diluted across ~600 lines of instruction covering 8+ analysis dimensions. By the time it gets to the chain lookup (the most precise, arithmetic-heavy task), it has already consumed significant context processing the earnings matrix, fundamentals, technicals, and WAIT/ROLL decision logic. The chain reading task requires exact key-path navigation and bid/ask arithmetic — precisely the type of task that degrades under context pressure.

### Instruction Content Breakdown (Open Call Monitor)

| Section | Lines | Agent 1 needs? | Agent 2 needs? |
|---------|-------|----------------|----------------|
| Role + Strategy + Data Source | ~60 | ✅ | Partial (role only) |
| Earnings Gate (25-row matrix) | ~100 | ✅ | ❌ (receives result) |
| Earnings override + roll target rules | ~60 | ✅ | ✅ (roll target rules only) |
| Position context | ~10 | ✅ | ✅ (receives from Agent 1) |
| Analysis framework (8 dimensions) | ~100 | ✅ | ❌ |
| WAIT/ROLL criteria + triggers | ~30 | ✅ | ❌ |
| Roll types | ~15 | Partial | ✅ |
| Profit optimization gate | ~40 | ✅ | ❌ (Agent 1 decides) |
| Premium-first roll policy + search algo | ~70 | ❌ | ✅ |
| Output format + examples | ~130 | ~60 (WAIT only) | ~100 (ROLL + economics) |

**Agent 1 estimated size: ~350-400 lines** (everything except roll economics/search, reduced output examples)
**Agent 2 estimated size: ~200-250 lines** (roll types, premium-first policy, search algo, chain schema, output format)

### Data Flow Analysis

Current flow (`agent_runner.py` lines 433–528):
```
fetch_all() → [overview + technicals + forecast + options_chain] → single agent → activity JSON
```

Options chain is filtered by `filter_options_chain_for_position()` (±15 strikes) and `filter_options_chain_by_delta()` before injection.

Proposed flow:
```
fetch_all() → [overview + technicals + forecast + minimal chain*] → Agent 1 → WAIT? done
                                                                               → ROLL? → [chain + Agent 1 output + pivot points] → Agent 2 → activity JSON
```

*Agent 1 still needs the current contract's delta/IV for its analysis (single contract lookup). Could pass just the current expiration's row rather than the full filtered chain.

### Token Economics

~70-80% of monitor runs result in WAIT. For those runs:
- **Current:** Full chain processed, all 600 lines consumed, full response generated → wasted chain tokens
- **Proposed:** Agent 1 runs with minimal chain context, produces WAIT → done. Agent 2 never invoked.

For the ~20-30% that result in ROLL:
- **Current:** 1 API call
- **Proposed:** 2 API calls, but Agent 2's context is ~60% smaller instruction-wise

Net: likely token savings overall, with slightly higher latency for ROLL cases (~5-10s extra).

### Technical Handoff Design

Agent 1 output (when action ≠ WAIT) becomes Agent 2 input:

```json
{
  "action_needed": "ROLL_UP_AND_OUT",
  "symbol": "MO",
  "exchange": "NYSE",
  "current_strike": 72,
  "current_expiration": "2026-04-24",
  "underlying_price": 73.80,
  "moneyness": "ITM",
  "delta": 0.62,
  "assignment_risk": "critical",
  "earnings_analysis": { ... },
  "risk_flags": ["approaching_itm", "earnings_soon"],
  "reason": "Stock broke through $72 strike with bullish momentum...",
  "confidence": "high",
  "pivot_points": { ... }
}
```

Agent 2 receives this + the full filtered options chain + roll-specific instructions. Produces the final activity JSON (same schema as today, with `roll_economics` populated).

### Runner Impact (`agent_runner.py`)

`run_position_monitor()` (lines 433–665) becomes a 2-phase method:

```python
async def run_position_monitor(self, ...):
    # Phase 1: Position assessment
    phase1_result = await self._run_position_assessment(...)
    
    if phase1_result["activity"] in self._NON_ALERT_ACTIVITIES:
        # WAIT — persist and return
        cosmos.write_activity(...)
        return
    
    # Phase 2: Roll management
    final_result = await self._run_roll_management(
        phase1_output=phase1_result,
        options_chain=chain_data,
        ...
    )
    cosmos.write_activity(...)
```

Activity/alert persistence model is unchanged — the final JSON (whether from Agent 1 for WAIT or Agent 2 for ROLL) follows the same schema and gets written the same way.

### Pros

1. **Directly addresses the credit problem**: Agent 2 has ~200-250 lines focused purely on chain reading, roll economics, and search. No earnings matrix, no 8-dimension analysis, no fundamental checks competing for attention.
2. **Token savings on majority case**: ~70-80% of runs are WAIT → Agent 2 never invoked → chain tokens saved.
3. **Easier debugging**: Credit wrong? It's Agent 2. Decision wrong? It's Agent 1. Clean separation.
4. **Independent instruction tuning**: Can refine roll economics instructions without touching the position assessment logic.
5. **Extensible**: Agent 2 could eventually handle more complex multi-leg strategies without bloating the monitor.

### Cons / Risks

1. **Latency for ROLL cases**: Extra API call adds ~5-10s for non-WAIT decisions.
2. **Handoff contract maintenance**: Two instruction files per strategy (call/put) × 2 agents = 4 new files. Handoff schema must stay in sync.
3. **Agent 1 still needs some chain data**: Current delta/IV requires at least the current contract. Solution: pass only the current expiration+strike row to Agent 1, not the full chain.
4. **Agent 2 may need some technicals**: Roll candidate selection references pivot points (R1/R2/R3 for calls, S1/S2/S3 for puts). Solution: extract pivot points from technicals and pass to Agent 2 (small payload).
5. **Profit optimization complexity**: The ROLL_DOWN/ROLL_UP optimization gate (3+4 conditions) straddles both agents — Agent 1 runs the gate conditions, Agent 2 does the economics. Need clean ownership split.

### Gotchas

1. **JSON schema consistency**: Agent 2 must produce the exact same output schema as the current unified format. No schema migration needed if done right.
2. **Error handling**: If Agent 2 fails/errors, need to persist Agent 1's output with a "roll_economics_unavailable" flag rather than losing the entire run.
3. **Profit optimization gate**: Currently lives entirely in the monitor instructions. Under the split, the gate decision (ROLL_DOWN/ROLL_UP for premium capture) should stay in Agent 1, with Agent 2 only executing the economics.

## Recommendation

**✅ SUPPORT the split.** The instruction complexity is the most likely cause of credit miscalculation, and the split directly addresses it by isolating the chain-reading task into a focused ~200-line agent. The 70-80% WAIT case saves tokens, and the 2-agent overhead for ROLL cases is acceptable.

### Suggested Implementation Approach

1. **New instruction files**: `tv_open_call_assessment_instructions.py` + `tv_open_call_roll_instructions.py` (same for put)
2. **Agent 1 gets**: Overview, technicals, forecast, current contract delta/IV only, previous context
3. **Agent 2 gets**: Full filtered chain, Agent 1's decision output, pivot points subset, roll-specific instructions
4. **Handoff format**: Define a strict intermediate JSON schema (above)
5. **Runner refactor**: Split `run_position_monitor()` into `_run_position_assessment()` + `_run_roll_management()` with conditional chaining
6. **Fallback**: If Agent 2 fails, persist Agent 1's output with `roll_economics: null` + `"roll_agent_error"` flag

### Team Assignment (proposed)

- **Linus**: Write the split instruction files (4 files: call_assessment, call_roll, put_assessment, put_roll)
- **Rusty**: Refactor `agent_runner.py` for 2-phase execution, define handoff schema
- **Basher**: Test end-to-end with known positions, verify credit calculations match chain data
- **Danny**: Review instruction split for coverage gaps, approve handoff schema

---

# Decision: Monitor Instruction Split — Implementation Details

**Date:** 2026-07-22
**Author:** Linus (Quant Dev)
**Status:** Implemented — pending Rusty's runner integration
**Relates to:** danny-monitor-split.md

## What Was Done

Created 4 new instruction files per Danny's architecture decision, splitting each monitor agent into Assessment (Agent 1) + Roll Management (Agent 2).

## Design Decisions

### 1. Function-based exports (not module-level constants)
The new files use `get_open_call_assessment_instructions()` functions instead of `TV_OPEN_CALL_INSTRUCTIONS` constants. This allows future parameterization if needed (e.g., passing position-specific context into the prompt template).

### 2. Roll instructions import OPTIONS_CHAIN_SCHEMA_DESCRIPTION
Agent 2 files do `from src.options_chain_parser import OPTIONS_CHAIN_SCHEMA_DESCRIPTION` and inject it via f-string. This keeps the chain schema DRY — single source of truth in options_chain_parser.py.

### 3. Handoff schema includes roll_target_rules
Added a `roll_target_rules` field to the handoff JSON so Agent 2 can respect earnings-driven expiration constraints without needing the full earnings gate logic. Agent 1 pre-computes which expirations are blocked.

### 4. Profit optimization gate stays in Agent 1
Agent 1 evaluates the 3+4 gate conditions and reports `profit_optimization_gate: "passed"/"failed"/null`. Agent 2 trusts this and only handles the economics (finding the right strike, calculating net credit).

## Team Impact

- **Rusty**: Needs to wire up `agent_runner.py` to use the new instruction functions. The 2-phase flow: call `get_open_call_assessment_instructions()` for Agent 1, parse its output, and if non-WAIT, call `get_open_call_roll_instructions()` for Agent 2 with the handoff JSON + chain.
- **Basher**: Can test each agent independently — Agent 1 with mock position data, Agent 2 with mock handoff + chain.
- **Danny**: Review the handoff schema for completeness before Rusty integrates.

## Files

| File | Lines | Role |
|------|-------|------|
| `src/tv_open_call_assessment_instructions.py` | 463 | Call position assessment (Agent 1) |
| `src/tv_open_call_roll_instructions.py` | 298 | Call roll management (Agent 2) |
| `src/tv_open_put_assessment_instructions.py` | 462 | Put position assessment (Agent 1) |
| `src/tv_open_put_roll_instructions.py` | 300 | Put roll management (Agent 2) |

---

# Decision: Roll Cost Sign Convention + Profit Optimization Gate Split

**Author:** Linus (Quant Dev)
**Date:** 2026-07
**Status:** Implemented
**Files:** tv_open_call_roll_instructions.py, tv_open_put_roll_instructions.py, tv_open_call_assessment_instructions.py, tv_open_put_assessment_instructions.py

## Context
Rubber duck review of the monitor agent split found 3 issues in the instruction files.

## Decisions

### 1. estimated_roll_cost = new_premium - buyback_cost (always)
Examples in both roll files showed negative roll cost alongside positive net credit — contradictory. Fixed all examples so `estimated_roll_cost` equals the net credit/debit math. Positive = credit, negative = debit, consistent with the rules text.

### 2. Profit optimization gate: "eligible" (not "passed")
Agent 1 (assessment) was checking conditions it cannot evaluate — specifically "no earnings/ex-div before new expiration" — because Agent 2 (roll management) selects the expiration. Changed:
- Gate result from "passed" → "eligible" (Agent 1's checks passed, but Agent 2 must validate)
- Removed 2 candidate-dependent flexible conditions from assessment; now 5 stock-level conditions, need 3 of 5
- Added `profit_optimization_constraints` to handoff JSON so Agent 2 gets earnings/ex-div dates
- Added PROFIT OPTIMIZATION VALIDATION section to both roll files

### 3. Mandatory JSON output in roll agents
Added explicit warning: roll agents MUST always produce a JSON activity block. If no viable roll, output CLOSE with `roll_tier: "no_viable_roll"`.

## Team Impact
- **Rusty**: agent_runner.py should handle "eligible" in addition to "passed" if it inspects `profit_optimization_gate`. The null JSON issue (Finding 3) needs a framework-level guard in agent_runner.py too — instructions alone aren't sufficient.
- **Danny**: No direct frontend impact, but the `profit_optimization_gate` value in decision logs will now show "eligible" instead of "passed" for profit optimization rolls.

---

# Decision: Runner 2-Phase Execution — Implementation Details

**Date:** 2026-07-22
**Author:** Rusty (Agent Dev)
**Status:** Implemented
**Implements:** danny-monitor-split.md

## What was done

Refactored `agent_runner.py` and both monitor wrappers to support the 2-phase Position Assessment → Roll Management execution model.

## Key design decisions

### 1. Backward-compatible opt-in via optional params
`run_position_monitor()` gained `assessment_instructions` and `roll_instructions` as optional kwargs. When both are `None`, the original single-agent path runs unchanged. This means the refactor is safe to merge even before Linus's instruction files land.

### 2. Handoff detection via `action_needed` key
Phase 1's output format diverges from the standard activity format:
- **WAIT path:** `{ "activity": "WAIT", ... }` → standard `_try_extract_json()` picks it up
- **Action path:** `{ "action_needed": "ROLL_UP_AND_OUT", ... }` → new `_try_extract_handoff_json()` picks it up

Using a distinct key (`action_needed` vs `activity`) avoids ambiguity and makes the detection reliable.

### 3. Phase 2 error resilience
If Phase 2 (roll management) fails for any reason, the runner persists Phase 1's handoff as a degraded activity with `roll_economics: null` and `"roll_agent_error"` appended to `risk_flags`. The run never crashes — the user sees the assessment result even if roll economics are unavailable.

### 4. Try/except import for parallel development
Monitor wrappers import Linus's instruction functions inside a try/except ImportError block. If the files don't exist yet, `assessment_instructions` and `roll_instructions` stay `None`, and the runner falls back to single-agent mode.

## Files changed
- `src/agent_runner.py` — new methods: `_try_extract_handoff_json`, `_run_position_assessment`, `_run_roll_management`; refactored `run_position_monitor`
- `src/open_call_monitor_agent.py` — imports + passes assessment/roll instructions
- `src/open_put_monitor_agent.py` — same pattern for puts

## Dependencies
- **Linus:** 4 instruction files must be committed for 2-phase mode to activate:
  - `src/tv_open_call_assessment_instructions.py`
  - `src/tv_open_call_roll_instructions.py`
  - `src/tv_open_put_assessment_instructions.py`
  - `src/tv_open_put_roll_instructions.py`
- Until those exist, the runner operates in single-agent fallback mode.

---

# Decision: Remove Legacy Single-Agent Fallback from Position Monitors

**Date:** 2026-04-23
**Author:** Rusty (Agent Dev)
**Status:** Implemented
**Commit:** 7f04db7

## Context
The 2-phase position monitor flow (Phase 1: assessment, Phase 2: roll management) was introduced with a try/except ImportError fallback so the code could merge before Linus committed the instruction files. Both call and put instruction files are now committed and stable.

## Decision
Remove all legacy single-agent fallback paths:
- `open_call_monitor_agent.py` / `open_put_monitor_agent.py`: Direct imports instead of try/except; drop `instructions=` parameter from `run_position_monitor` calls.
- `agent_runner.py`: Drop `instructions` parameter from `run_position_monitor` signature; remove `two_phase` boolean check; delete the ~50-line single-agent `else` branch; hard-code `two_phase: True` in telemetry.

## Rationale
- Dead code: the single-agent path was unreachable since the instruction files are committed.
- Simpler control flow: one execution path instead of two branching conditionally.
- Prevents accidental regression to the less capable single-agent mode.
- Net deletion of ~80 lines.

## Impact
- No runtime behavior change — the 2-phase path was already the only path executed.
- Any future instruction file changes must keep the assessment/roll module pattern.

---

# DGI Screener Feature Decisions

## User Directive: Scope Simplification

**Date:** 2026-05-10T14:49  
**From:** David Sancho (via Copilot)  
**Decision:** Remove the CSP Recommender agent entirely from the DGI Screener feature. Instead, add a manual "Quick Analysis" button that triggers the existing quick analysis feature, and an "Add to Watchlist + CSP Watch" button. No new LLM agent needed — reuse existing infrastructure.  
**Rationale:** User preference for control; existing quick analysis is sufficient; eliminate need for a separate daily LLM agent.

---

## Decision: Scope Simplification — CSP Recommender Removed

**Date:** 2026-05-10  
**Author:** Danny (Lead)  
**Status:** Approved by David (User)  
**Impact:** Reduces complexity, accelerates delivery, improves user control

### Context
Initial DGI Screener v2 proposal (2026-05-10) included a daily LLM agent (CSP Recommender) to generate automatic Cash-Secured Put recommendations for each Top 20 symbol. User feedback reversed this decision.

### Changes
**Eliminated:**
- LLM agent: `dgi_csp_recommender.py`
- Agent instructions: `dgi_csp_recommender_instructions.py`
- Cron job: daily recommendation generation
- CosmosDB doc_type: `csp_recommendation`
- YAML config block: `dgi_csp_recommender`

**Added: Two manual buttons in DGI Screener dashboard**
1. "Quick Analysis" → `POST /api/chat` (mode: "quick-analysis") — reuses existing feature
2. "Add to Watchlist + CSP Watch" → `POST /api/dgi/add-to-watchlist` — integrates with existing CSP agent

### Benefits
- LLM cost reduced 80–90% (manual triggers vs. daily automation)
- Complexity: 1 agent + reused endpoints vs. 2 new agents
- Delivery: 50% faster (1–2 days vs. 3–4 days)
- Code reuse: maximized (no new agent code, reuses `/api/chat` + existing CSP infrastructure)
- User control: manual analysis before decisions

### Implementation Phases
1. **Phase 1 (3–4 days):** DGI Screener MVP (yfinance_fetcher, dgi_metrics, dgi_screener, CosmosDB, scheduler)
2. **Phase 2 (2–3 days):** Web dashboard (/dgi-screener, API endpoints, manual buttons)
3. **Phase 3 (1–2 days):** Error handling, logging, edge cases

---

## Decision: Technical Indicators for DGI Screener Entry Timing

**Date:** 2026-05-10  
**Author:** Danny (Lead)  
**Status:** Incorporated in DGI Screener proposal v2  
**Impact:** dgi_metrics.py, yfinance_fetcher.py, config.yaml quality_score calculation

### Context
User feedback: "Don't give me 20 good DGI stocks — I know which those are. Give me 20 with good entry points now."  
Screener evolves from purely fundamental-quality to hybrid: **70% fundamental quality + 30% technical entry timing**.

### Quality Score Rebalancing
- **Fundamental (70%):** Yield (15%), Growth (18%), Payout (10%), Valuation (10%), Health (7%), Consistency (10%)
- **Technical Timing (30%):** RSI (30%), SMA position (25%), 52-week high distance (25%), Bollinger Bands (20%)

### Technical Indicators (Programmatic, no LLM cost)
| Indicator | Implementation | Favorable Signal |
|-----------|---------------|------------------|
| RSI(14) | `calculate_rsi()` in dgi_metrics.py | < 40 (oversold) |
| SMA(50/200) | `calculate_sma()` | Price at or below SMA(200) |
| 52w High Distance | Direct calculation | > 15% pullback |
| Bollinger Bands(20,2) | `calculate_bollinger_bands()` | Lower quartile of bands |

### Data Source
- **Primary:** yfinance — `ticker.history(period="1y")` provides OHLCV daily data
- **Backup:** Existing TradingView integration
- `yfinance_fetcher.py` returns history + info + dividends

### Configuration
All parameters configurable in config.yaml:
- RSI/SMA/BB periods
- Quality score weights (fundamental vs. technical)
- Component sub-weights

### Rationale
- Best DGI stocks are stable and well-known; differentiation is timing entry
- Technical indicators are deterministic; no LLM cost
- 30% technical weight sufficient to influence ranking without sacrificing fundamental quality
- yfinance provides free, adequate OHLCV for these calculations

---

## Decision: Post-Agent Premium Validation

**Date:** 2026-07-13  
**Author:** Rusty (Agent Dev)  
**Status:** Implemented  
**Impact:** agent_runner.py, options chain data integrity

### Context
LLM agents occasionally hallucinate premiums when reading options chain JSON — mismatching bid values from wrong expiration dates. Example: CSP agent reported $1.55 for strike $45 exp 2026-06-18, but $1.55 bid belonged to 2026-12-18; actual bid was $0.15. Instructions alone cannot prevent this error.

### Solution
Added programmatic post-agent validation step (`_validate_premium_against_chain`) in `AgentRunner` that cross-checks every reported premium against actual parsed options chain data **after** agent JSON output but **before** persistence.

### Validation Scope
- **Watchlist (SELL signals):** premium (bid) at reported strike + expiration
- **Monitor (ROLL signals):** new_premium (bid of new contract) and buyback_cost (ask of current contract)
- **Delta:** corrected if chain shows different value

### Behavior
- Mismatches > $0.02: auto-corrected with WARNING log
- `premium_corrected: True` flag set for traceability
- premium_pct and net_credit recalculated on correction
- Defensive: wrapped in try/except, never crashes pipeline
- Logs: DEBUG on validation pass, WARNING on correction

### Files Changed
- `src/agent_runner.py` — methods: `_validate_premium_against_chain()`, `_validate_single_premium()`, `_validate_buyback_cost()` plus integration into `run_symbol_agent()` and `run_position_monitor()`

---

## Decision: DGI Screener Bug Fixes

**Date:** 2026-07  
**Author:** Rusty (Agent Dev)  
**Status:** Implemented

### Context
Basher's code review identified 5 critical and 3 moderate bugs in DGI Screener pipeline. All were interface contract mismatches between dgi_screener.py (caller) and dgi_metrics.py / yfinance_fetcher.py (callees), plus missing scheduler integration and config/dependencies.

### Fixes Implemented

1. **`days_on_list` counter starts at 1** 
   - First day on list = day 1, not 0
   - More intuitive for user dashboard display

2. **Technical indicator config passed as kwargs**
   - rsi_period, bb_period, bb_std from config.yaml passed as optional kwargs
   - Only if present in config; otherwise function defaults apply
   - Keeps call site forward-compatible for future parameters

3. **DGI scheduler mirrors options_chain pattern**
   - Follows same init → reschedule → trigger → display flow
   - Consistency across all scheduler job types

### Team Impact
- **Danny:** config.yaml now has `dgi_screener` section for filters/weights updates
- **Linus:** `calculate_quality_score` hardcodes weights internally — config weights NOT passed through; requires signature update if dynamic weights needed
- **Basher:** All critical/moderate findings resolved; ready for re-review

---

# DGI Screener Display & Data Fixes

**Date:** 2026-05-10
**Author:** Linus (Quant Dev)
**Status:** ✅ Implemented

## Changes

### 1. Dividend Yield Display — removed `* 100`
- `dividend_yield` from yfinance `info["dividendYield"]` is already in percentage form (e.g., 1.18 = 1.18%)
- Template was multiplying by 100 again → showing 118% instead of 1.18%
- `dividend_cagr_5y` from `calculate_dividend_cagr()` returns a ratio (0-1) → kept `* 100`

**Note:** The scoring functions in `dgi_metrics.py` (`_dividend_yield_score`, `categorize_stock`, `passes_minimum_filters`) treat `dividend_yield` as a ratio (thresholds like 0.015, 0.02). If yfinance now returns percentage values, these thresholds may need recalibration. Recommend verifying scoring accuracy after the next screener run.

### 2. Detail Modal on Row Click
- Added modal overlay reusing existing `.modal-*` CSS classes from `style.css`
- Clicking a row (not action buttons) shows full entry data grouped into: Overview, Fundamentals, Technical Timing (with nested sub-scores)
- Escape key, clicking outside, or × button closes the modal
- Entry data passed via `data-entry` JSON attribute on each `<tr>`

### 3. `years_consecutive_increases` Partial-Year Bug — FIXED
- **Root cause:** `ticker.dividends` includes the current (incomplete) year. If only 2 of 4 quarterly payments have been made, the current year total is lower than the previous year, breaking the consecutive streak at position 0.
- **Fix:** Both `calculate_years_consecutive_increases()` and `calculate_dividend_cagr()` now drop the current year from annual totals before comparing.
- This explains why most top-20 stocks showed 0 years despite being established dividend growers.

---

# Decision: DGI Screener Documentation Structure

**Author:** Rusty (Agent Dev)
**Date:** 2026-07
**Status:** Implemented

## Decision
The DGI Screener section in README.md is structured as a self-contained reference with subsections for categories, quality score, filters, data source, storage, scheduling, config, web UI, and pipeline steps. This mirrors the depth of existing sections (e.g., Symbol Report) while being independently readable.

## Key Choices
1. **DGI section placed after Symbol Report, before Project Structure** — follows the pattern of feature sections before structural/setup sections
2. **yfinance explicitly called out as independent of TradingView** — prevents confusion about data source overlap
3. **Config.yaml example included inline** — enables copy-paste setup without cross-referencing
4. **Provision script uses `DGI_CONTAINER` variable** — consistent with `TELEMETRY_CONTAINER` and `SETTINGS_CONTAINER` patterns

## Impact
- README accurately reflects 8 agents, 4 containers, and the DGI investment strategy
- Provision script creates all 4 containers in a single run
- New contributors can understand DGI Screener without reading source code

---

# Decision: DGI Screener ▶ Button → Chat Quick Analysis

**Date:** 2026-05-10  
**Agent:** Linus  
**Status:** Implemented  

## Decision

The DGI screener's ▶ (analyze) button now redirects to the Chat page's Quick Analysis mode instead of triggering the CSP analysis API in the background.

## Rationale

1. **Better UX**: Interactive chat analysis is more valuable than a background API call with no feedback
2. **Centralization**: The chat page already has all the Quick Analysis infrastructure — reuse rather than duplicate
3. **Flexibility**: Users can interact with the analysis, ask follow-ups, adjust parameters

## Implementation Details

**DGI Screener** (`web/templates/dgi_screener.html`):
- Button redirects to `/chat?mode=quick-analysis&symbol=XXX&market=YYY&option_type=put`
- Added Yahoo Finance exchange code mapping (NYQ→NYSE, NMS→NASDAQ, etc.)
- Market/exchange extracted from row's `data-entry` JSON (entry.exchange)

**Chat Page** (`web/templates/chat.html`):
- Detects URL params on load
- Pre-fills form and auto-starts `fetchAndAnalyze()` if params present
- User lands on chat page with analysis already running

## Technical Notes

- **Exchange Mapping Required**: Yahoo Finance returns codes (NYQ, NMS) but TradingView expects full names (NYSE, NASDAQ)
- **Option Type Default**: Always pre-selects "put" for DGI screener (cash-secured put strategy)
- **No Breaking Changes**: ➕ (add to watchlist) button and detail modal remain functional

## Pattern

**Redirect-to-Form-with-Auto-Submit**: When multiple features need similar functionality, redirect to the canonical implementation with URL params rather than duplicating logic. The receiving page owns the flow; the sender just bootstraps with context.

## Files Modified

- `web/templates/dgi_screener.html` — button click handler
- `web/templates/chat.html` — URL param detection & auto-start

## Future Considerations

- Could extend this pattern to other screeners/tables (e.g., watchlist, positions)
- Could add more URL params (e.g., `auto_submit=true` flag instead of implicit behavior)
- Exchange mapping could be centralized if needed elsewhere (currently inline in DGI screener JS)
# Decision: Chat View Output Format - Section-Based Cards vs Markdown Tables

**Date**: 2026-05-11  
**Author**: Linus (Quant Dev)  
**Status**: Implemented  
**Context**: Chat UI rendering optimization  

## Problem

Decision Summary tables in chat agent responses (covered calls, cash-secured puts) rendered poorly in chat bubbles:
- Wide 2-column markdown tables (`| Factor | Assessment |`) with long multi-line content using `<br>` tags
- Chat bubbles have constrained width, making wide tables cramped and hard to read
- `<br>` tags in markdown table cells are a hack that doesn't flow naturally
- Poor responsive behavior at different screen widths

## Decision

**Replace markdown table format with section-based card format** for Decision Summaries in chat agent prompts.

### Old Format (Markdown Table)
```markdown
## 📊 Decision Summary

| Factor | Assessment |
|--------|------------|
| **Overall Recommendation** | Cautiously Favorable |
| **Key Reasons AGAINST** | • Risk 1<br>• Risk 2<br>• Risk 3 |
| **Suggested Strikes** | $435: Reasoning<br>$440: Alternative |
```

### New Format (Section-Based Cards)
```markdown
## 📊 Decision Summary

**🎯 Overall Recommendation:** Cautiously Favorable for selling covered calls

---

**⚠️ Key Reasons AGAINST Selling:**
- Risk 1 — specific detail
- Risk 2 — specific detail
- Risk 3 if applicable

**💰 Suggested Strike Prices:**
- **$435 strike** (0.20 delta, OTM): Reasoning here
- **$440 strike** (0.15 delta, further OTM): Alternative
```

## Rationale

1. **Responsive**: Section-based format adapts to any chat bubble width without horizontal scrolling
2. **Readability**: Bullet lists are more natural than `<br>`-separated items in table cells
3. **Visual Hierarchy**: Emoji prefixes + markdown headers create clear visual sections
4. **Maintainability**: Easier for AI to generate — no need to manage table alignment or worry about `<br>` placement
5. **Content Preservation**: All the same information, just better formatted for the chat UI

## Implementation

- Updated prompt templates in `src/tv_open_call_chat_instructions.py` and `src/tv_open_put_chat_instructions.py`
- Changed both the format specification (template section) AND the example response
- Preserved strategy-specific differences (PUT uses "Assignment Readiness", CALL uses "Profit Target")
- Added CSS improvements to `web/static/style.css` for tables that remain (options chain in reports):
  - Horizontal scrolling support
  - Better dark theme colors
  - Alternating row backgrounds
  - Improved `<br>` tag spacing in cells
- Added formatting note to `src/tv_report_instructions.py` to avoid `<br>` in new tables

## Scope

**What Changed**:
- Chat agent output format for Decision Summaries (CALL and PUT)
- CSS for all tables in chat bubbles

**What Did NOT Change**:
- Strategy assessment logic or gate criteria
- Content guidelines (what to include in each section)
- Report agent tables (options chain, etc.) — those keep table format but with improved CSS

## Team Impact

- **Frontend/UI**: Chat views will render decision summaries more cleanly
- **AI Prompts**: Future prompt changes should use this section-based format for chat outputs
- **Reports**: Report agent continues to use compact markdown tables (appropriate for that context)

## Pattern for Future Reference

**Use section-based card format when**:
- Output appears in constrained-width UI (chat bubbles, mobile)
- Content has multi-line items within categories
- Visual scanning is important

**Use markdown tables when**:
- Displaying tabular data (options chains, dividend history)
- Fixed-width layout is available (reports, full-page views)
- Keep columns narrow; avoid `<br>` in cells (use multiple rows instead)

---

## DGI Enhancements (2026-05-11)

### Radar "Ideal Minimum" Thresholds Use Above-Filter Values
**Date:** 2026-05-11  
**Author:** Linus (Quant Dev)  
**Status:** ✅ Implemented

DEFAULT_FILTERS in dgi_metrics.py sit at the exact zero-point of each scoring function (all map to sub-score 0). Plotting them on the radar would show a collapsed dot at center — useless visually.

**Decision:** The "Ideal Minimum" red line uses moderately higher raw values representing a "decent DGI holding" floor rather than bare elimination cutoff:
- Yield 2.5% (score 22), Growth 5% CAGR (score 33), Payout ≤60% (score 30), PE ≤25 (score 25), D/E ≤1.5 + ROE ≥10% (score 29), Years ≥8 (score 23), Tech 40

**Implications:**
- If DEFAULT_FILTERS change, `_compute_minimum_thresholds()` may need updating — they're currently independent.
- The threshold is computed fresh each call (trivial cost) rather than cached.
- Legend is now displayed on the radar chart to distinguish the two datasets.

**Files Changed:** `src/dgi_metrics.py`, `web/templates/dgi_analysis.html`

---

### Beasts Filter Preset
**Author:** Linus (Quant Dev)  
**Date:** 2026-05-11  
**Status:** ✅ Implemented

User requested a "Beasts" preset filter button on the DGI screener page to quickly surface elite dividend stocks.

**Decision:** Added a `🐂 Beasts` button in `web/templates/dgi_screener.html` that sets filter thresholds: Quality Score ≥ 80, Dividend Yield ≥ 2.5%, Dividend Growth ≥ 10%, Years ≥ 10, Timing ≥ 90. The button reuses the existing `applyFilters()` function by programmatically setting slider values — no duplicate filter logic.

**Rationale:**
- Reusing sliders + `applyFilters()` keeps one source of truth for filtering logic.
- Users can see and adjust the preset values after clicking since sliders visually update.
- "Over X" interpreted as ≥ X for practical UI purposes.

**Files Changed:** `web/templates/dgi_screener.html`

---

### DGI Single-Symbol Analysis Page
**Date:** 2026-05-11  
**Author:** Linus (Quant Dev)  
**Status:** ✅ Implemented  
**Impact:** Frontend, API, scoring logic

Added a read-only DGI analysis page (`/dgi/analyze/{symbol}`) that lets users score any ticker — not just symbols in the S&P 500 screener universe.

**Key Design Choices:**
1. **New `analyze_single_symbol()` function** in `dgi_screener.py` rather than reusing `run_dgi_screener()` — avoids CosmosDB writes, config dependencies, and batch overhead. Single-symbol path fetches one ticker and returns immediately.
2. **New `calculate_quality_score_detailed()`** in `dgi_metrics.py` instead of modifying existing `calculate_quality_score()` — the detailed variant returns all sub-scores, weights, and health breakdowns. Original function untouched for backward compatibility.
3. **Executor thread for blocking I/O** — `yfinance` is synchronous, so the endpoint uses `run_in_executor()` to avoid blocking the FastAPI event loop.
4. **No CosmosDB dependency** — the analysis endpoint doesn't require CosmosDB at all. It fetches live data from yfinance and computes scores on the fly.

**Files Changed:** `src/dgi_metrics.py`, `src/dgi_screener.py`, `web/app.py`, `web/templates/dgi_screener.html`, `web/templates/dgi_analysis.html`

---

### DGI Score History Tracking
**Date:** 2026-05-11  
**Author:** Linus (Quant Dev)  
**Status:** ✅ Implemented  
**Impact:** Data model (CosmosDB `dgi_top20` documents), Frontend (detail modal)

When stocks persist in the DGI screener top_n across multiple runs, their quality_score may change as market conditions evolve.

**Decision:** Added a `score_history` array field to each `dgi_top20` document in CosmosDB:
- Format: `[{"date": "YYYY-MM-DD", "score": float}, ...]`
- Capped at 90 entries (matches the daily snapshot TTL)
- Deduplication: same day + same score → skip; same day + different score → replace last entry
- New stocks initialize with a single entry

**Implications for Team:**
- **Rusty (Framework):** No changes needed — the field is just another document property flowing through existing upsert/read paths.
- **Danny (Lead):** The 90-entry cap keeps document size bounded. If we later want longer history, consider moving to the snapshot collection instead.
- **Frontend:** Chart.js CDN added to `dgi_screener.html` (not base.html) — only loaded on the DGI page. If other pages need charts, consider moving to base.html.

**Files Changed:** `src/dgi_metrics.py`, `web/app.py`

---

### StockAnalysis.com as Supplementary Dividend Data Source
**Date:** 2026-05-11  
**Author:** Linus (Quant Dev)  
**Status:** ✅ Implemented  
**Impact:** DGI screener pipeline (both single-symbol and batch modes)

Yahoo Finance's dividend payment series produces unreliable `years_consecutive_increases` counts. StockAnalysis.com publishes an authoritative "Growth Years" metric on each stock's dividend page that matches established dividend aristocrat/champion databases.

**Decision:**
- **Growth Years**: ALWAYS prefer stockanalysis.com over Yahoo-calculated value. Fall back to Yahoo only when SA returns `None`.
- **Other metrics** (yield, payout ratio, CAGR): use SA as fallback only when Yahoo returns 0 or missing. Yahoo remains the primary source for these.
- `dgi_metrics.py` is NOT modified — the override happens at the integration level in `dgi_screener.py`.

**Risks:**
- Web scraping is fragile — SA may change page structure, block requests, or rate-limit.
- Mitigations: dual parsing strategy (DOM + regex fallback), `None` return on any error, in-memory cache, polite delays, User-Agent rotation.
- If SA is completely unreachable, the screener falls back silently to Yahoo-only behavior with no disruption.

**Files Changed:** `src/dgi_screener.py`

---

### Dual Threshold Lines on Radar Chart
**Author:** Linus (Quant Dev)  
**Date:** 2026-05-11  
**Status:** ✅ Implemented

The radar chart previously showed a single red "Ideal Minimum" threshold line. Now displays two uniform threshold lines:
- **Mínimo (65)** — red dashed line, uniform 65 on all axes.
- **Ideal (80)** — green dashed line, uniform 80 on all axes.

Uniform approach works because weights sum to 1.0: setting all sub-scores to N yields a weighted total of N.

**API Change:** `calculate_quality_score_detailed()` now returns `"ideal_thresholds"` in addition to `"minimum_thresholds"`.

**Files Changed:** `src/dgi_metrics.py`, `web/templates/dgi_analysis.html`

---

### yfinance dividendYield Normalization
**Author:** Linus (Quant Dev)  
**Date:** 2026-07-25  
**Status:** ✅ Implemented

MSFT was displaying ~88% dividend yield in DGI screener modal (actual yield ~0.8%).

**Root Cause:** yfinance `dividendYield` returns values in percentage form (0.88 = 0.88%, 2.42 = 2.42%). The normalization `if raw_yield > 1: raw_yield /= 100` only caught yields above 1%, leaving sub-1% stocks (MSFT 0.88, AAPL 0.37) in percentage form while downstream (scoring, categorization, filters) expected decimal.

**Downstream Effects:**
- Filter modal: `0.88 * 100 = 88.00%`
- Scoring: `0.88 / 0.05 * 100 = 1760`, clamped to 100 — always max score
- Categorization: `0.88 >= 0.04` → wrongly classified as "High Yield"

**Decision:** Always divide `dividendYield` by 100 (unconditional). Verified against 6 stocks: yfinance `dividendYield` is consistently percentage-form across all yield ranges.

**Files Changed:** `src/dgi_metrics.py`

---

### DGI Screener Payout Ratio Display Fix
**Author:** Linus (Quant Dev)  
**Date:** 2026-05-13  
**Status:** ✅ Implemented

The DGI screener detail modal displayed raw decimal values for `payout_ratio` ("0.3", "0.333") instead of formatted percentages ("30%", "33.3%").

**Investigation:**
1. yfinance returns `payoutRatio` as decimal (0.333 = 33.3%) — tested with ZTS, MSFT, JNJ, AAPL, KO
2. CosmosDB stores correctly as decimal (internal convention: 0-1 range for ratios)
3. Scoring functions expect decimal (`_payout_safety_score`, `max_payout: 0.75`)
4. Analysis page formats correctly (`dgi_analysis.html` uses `* 100`)
5. Bug location: `dgi_screener.html` detail modal uses generic `buildSection()` which applies `fmtVal()` to all fields — doesn't know about percentage vs. dollar vs. ratio fields

**Root Cause:** Formatting logic applied in wrong order. `fmtVal()` converted values to strings before field-specific numeric checks could run, causing conditionals to fail.

**Fix:** Reordered formatting logic in `web/templates/dgi_screener.html` (lines 422-447):
- **Field-specific formatting FIRST** (percentage, market cap, price, ratio) while values are still numeric
- **Generic fallback** to `fmtVal()` for other fields

**Formatted Fields:** payout_ratio, dividend_yield, dividend_cagr_5y, roe, debt_to_equity, market_cap, current_price

**Files Changed:** `web/templates/dgi_screener.html`

---

### Premium=0.0 — Root Cause & Diagnostic Fix
**Author:** Linus (Quant Dev)  
**Date:** 2026-05-11  
**Status:** 🔧 Applied — needs runtime verification

All trading agents returning premium=0.0. Options chain data confirmed present by user. Systemic across all agent types.

**Two Most Likely Causes:**
1. **TradingView endpoint migration:** `_OPTIONS_SCAN_URLS` only matched `scan2` endpoints. If TradingView migrated to `scan`, `screener`, or `scan3`, no API responses would be intercepted. DOM fallback fails → parser fails → agents get raw text → output premium=0.0.
2. **TradingView field name change:** If field names changed (e.g., "bid" → "option_bid"), parser stores bid=None, agents see null, output premium=0.0.

**Changes:**
- Broadened URL matching to cover scan/scan2/scan3/screener + added fallback for any scanner.tradingview.com response with "symbols-options"
- Added field name aliases to `_FIELD_MAP`
- Added diagnostic logging at ERROR level for missed scanner URLs and missing bid/ask fields
- Fixed broken tests in `test_options_chain_parser.py` (tests used `[0]` indexing but parser was refactored to strike-keyed dict format at commit 7be7d82)

**Key Pattern:** When intercepting third-party API responses by URL matching, URL patterns MUST be broadly defined and logged when they miss. Third parties change endpoints without notice. Always have fallback matcher and diagnostic logging.

**Files Changed:** `src/tv_data_fetcher.py`, `test_options_chain_parser.py`

---

### DGI Screener Observability Standards
**Author:** Linus (Quant Dev)  
**Date:** 2026-05-11  
**Status:** ✅ Implemented

User triggered a DGI screener run from web UI. Run appeared active but CosmosDB was never updated. Root cause: background thread error handlers lacked `exc_info=True`, making failures invisible.

**Decision:**
1. **All background thread error handlers must include `exc_info=True`** — without full tracebacks, async failures are undebuggable.
2. **All CosmosDB write operations must have INFO-level logging before AND after** — "Saving N entries..." / "✅ All N entries saved".
3. **The `container is None` guard in `cosmos_db.py` should be audited** — it silently skips writes with only a WARNING. For DGI screener writes, this is data loss and should log at ERROR level.

**Key Pattern:** Background-threaded operations need explicit error context. CosmosDB silent-skip patterns should log at ERROR when they cause data loss, not WARNING.

**Applies to:** All background-threaded operations (DGI screener, future scheduled tasks). Rusty should review other `cosmos_db.py` methods for similar silent-skip patterns.

**Files Changed:** `src/dgi_screener.py`, `src/cosmos_db.py`

---

### Per-Function Model Override System
**Author:** Rusty (Backend Dev)  
**Date:** 2026-07  
**Status:** ✅ Implemented

All agents used a single `model_deployment` config value. Different agents have different complexity/cost profiles and benefit from different models.

**Decision:**
- Added `azure.models` section to `config.yaml` with per-role keys
- `Config.model_for(role)` returns override or falls back to `model_deployment`
- `AgentRunner` lazily caches one `AzureOpenAIChatClient` per unique deployment name
- All overrides optional — backward compatible with existing configs

**Role Keys:** `monitor_assessment`, `monitor_roll`, `supervisor`, `alpha`, `analysis`, `summary`, `report`, `chat`, `symbol_chat`

**Backward Compatibility:** No breaking changes to existing env vars or configs.

**Files Changed:** `config.yaml`, `src/config.py`, `src/agent_runner.py`

---
# yfinance Feasibility Deep-Dive: Replacing All Data Sources

**Date:** 2026-05-14  
**Author:** Linus (Quant Dev)  
**Status:** Analysis Complete  
**Impact:** All agents, DGI screener, options chain pipeline, TV data fetcher, StockAnalysis fetcher

---

## Executive Summary

**Verdict: yfinance can replace TradingView and StockAnalysis.com for ~90% of data needs.** The biggest win is options chains (23+ expiration dates vs. TradingView's ~5) and elimination of all scraping fragility (Playwright, anti-bot detection, User-Agent rotation, 403 recovery). The main gap is Greeks — yfinance provides IV but not delta/gamma/theta/vega/rho. These must be computed via Black-Scholes, which is straightforward. Pre-computed oscillator signals (TradingView's "Buy/Sell/Neutral" recommendations) are also lost, but we already compute RSI/SMA/Bollinger from OHLCV in `dgi_metrics.py`, so extending that is natural.

---

## 1. Current Data Sources Inventory

### 1.1 TradingView (`tv_data_fetcher.py`) — 5 Resource Types

| Resource | Method | Anti-bot Risk | Fragility |
|----------|--------|--------------|-----------|
| `overview` | BS4 scrape + scanner API POST | High (403s, CAPTCHAs) | URL/field changes break it |
| `technicals` | BS4 scrape + scanner API POST | High | Field name migrations |
| `forecast` | BS4 scrape + scanner API POST | High | Same |
| `dividends` | BS4 scrape + scanner API POST | High | Same |
| `options_chain` | Playwright browser interception | Very High (headless detection) | URL pattern changes (scan/scan2/scan3/screener) — already broke once |

### 1.2 StockAnalysis.com (`stockanalysis_fetcher.py`)
- Scrapes dividend growth years, yield, payout ratio, CAGR
- User-Agent rotation, dual parsing (DOM + regex fallback)
- Used to override Yahoo's unreliable `years_consecutive_increases`

### 1.3 Existing yfinance (`yfinance_fetcher.py` + `dgi_metrics.py`)
- Already fetches `ticker.info`, `ticker.dividends`, `ticker.history(period="1y")`
- Already computes RSI, SMA, Bollinger Bands from OHLCV
- Already used as primary data source for DGI screener

---

## 2. Comprehensive Feasibility Matrix

### 2.1 Overview / Fundamentals (TradingView `overview`)

| Data Field | TV Scanner Field | yfinance Equivalent | Coverage | Notes |
|------------|-----------------|-------------------|----------|-------|
| Market Cap | `market_cap_basic` | `ticker.info['marketCap']` | ✅ Full | Same source (Yahoo) |
| P/E Ratio (TTM) | `price_earnings_ttm` | `ticker.info['trailingPE']` | ✅ Full | Also `forwardPE` available |
| EPS (TTM) | `earnings_per_share_basic_ttm` | `ticker.info['trailingEps']` | ✅ Full | Also `epsForward`, `epsCurrentYear` |
| Dividend Yield | `dividends_yield` | `ticker.info['dividendYield']` | ✅ Full | Returns decimal (0.0036) |
| Revenue (FY) | `total_revenue_fy` | `ticker.info['totalRevenue']` | ✅ Full | |
| Net Income | `net_income` | `ticker.info['netIncomeToCommon']` | ✅ Full | |
| Beta (1Y) | `beta_1_year` | `ticker.info['beta']` | ✅ Full | |
| Shares Outstanding | `total_shares_outstanding` | `ticker.info['sharesOutstanding']` | ✅ Full | |
| Float Shares | `float_shares_outstanding_current` | `ticker.info['floatShares']` | ✅ Full | |
| Employees | `number_of_employees` | `ticker.info['fullTimeEmployees']` | ✅ Full | |
| Sector | `sector` | `ticker.info['sector']` | ✅ Full | |
| Industry | `industry` | `ticker.info['industry']` | ✅ Full | |
| Revenue (Last Quarter) | `revenue_fq` | `ticker.quarterly_income_stmt` | ✅ Full | Need to extract from financials DataFrame |
| EPS (Last Quarter) | `earnings_per_share_fq` | `ticker.info['trailingEps']` | ⚠️ Partial | Quarterly EPS needs `earnings_history` |
| EPS Forecast (Next Q) | `earnings_per_share_forecast_next_fq` | `ticker.info['epsForward']` | ⚠️ Partial | Forward EPS is annual, not quarterly |
| Revenue Forecast (Next Q) | `revenue_forecast_next_fq` | `ticker.revenue_estimate` | ✅ Full | DataFrame with current/next quarter |
| Next Earnings Date | `earnings_release_next_date_fq` | `ticker.info['earningsTimestampStart']` | ✅ Full | Also `earningsTimestampEnd` |
| Analyst Rating | `recommendation_mark` | `ticker.info['recommendationMean']` | ✅ Full | 1-5 scale (same semantics) |
| All-Time High | `all_time_high` | `ticker.info['allTimeHigh']` | ✅ Full | |
| All-Time Low | `all_time_low` | `ticker.info['allTimeLow']` | ✅ Full | |
| Currency | `fundamental_currency_code` | `ticker.info['currency']` | ✅ Full | |
| Website | `web_site_url` | `ticker.info['website']` | ✅ Full | |

**Coverage: 21/21 fields ✅ (2 partial but usable)**

### 2.2 Dividends (TradingView `dividends`)

| Data Field | TV Scanner Field | yfinance Equivalent | Coverage | Notes |
|------------|-----------------|-------------------|----------|-------|
| DPS (FY) | `dps_common_stock_prim_issue_fy` | `ticker.info['dividendRate']` | ✅ Full | Annual dividend rate |
| DPS (FQ) | `dps_common_stock_prim_issue_fq` | `ticker.info['lastDividendValue']` | ✅ Full | |
| Dividend Yield | `dividends_yield` | `ticker.info['dividendYield']` | ✅ Full | |
| Payout Ratio (TTM) | `dividend_payout_ratio_ttm` | `ticker.info['payoutRatio']` | ✅ Full | |
| DPS Growth YoY | `dps_common_stock_prim_issue_yoy_growth_fy` | Computed from `ticker.dividends` | ✅ Full | Already computed in `dgi_metrics.py` |
| Consecutive Years Paying | `continuous_dividend_payout` | Not directly available | ⚠️ Compute | Can compute from `ticker.dividends` series |
| Consecutive Years Growing | `continuous_dividend_growth` | Not directly available | ⚠️ Compute | Already computed in `dgi_metrics.py` (`calculate_years_consecutive_increases`) |
| Ex-Dividend Date | `ex_dividend_date_recent` | `ticker.info['exDividendDate']` | ✅ Full | Epoch timestamp |
| EPS (TTM) | `earnings_per_share_basic_ttm` | `ticker.info['trailingEps']` | ✅ Full | |
| P/E Ratio | `price_earnings_ttm` | `ticker.info['trailingPE']` | ✅ Full | |
| Market Cap | `market_cap_basic` | `ticker.info['marketCap']` | ✅ Full | |

**Coverage: 11/11 fields ✅**

**StockAnalysis.com replacement:** The primary value from SA was `growth_years` (consecutive dividend increase years). yfinance's `ticker.dividends` provides the full dividend history, and we already compute this in `dgi_metrics.py` via `calculate_years_consecutive_increases()`. However, note the existing caveat in `dgi_screener.py`: SA's `growth_years` was preferred because Yahoo's dividend series can be unreliable. **Risk: We lose the SA cross-check.** Mitigation: validate our computed value against known Dividend Aristocrat lists periodically.

### 2.3 Technicals (TradingView `technicals`)

| Data Field | TV Scanner Field | yfinance Equivalent | Coverage | Notes |
|------------|-----------------|-------------------|----------|-------|
| Overall Recommendation | `Recommend.All` | ❌ Not available | ❌ Gap | Must compute our own composite signal |
| Oscillator Recommendation | `Recommend.Other` | ❌ Not available | ❌ Gap | Must compute |
| MA Recommendation | `Recommend.MA` | ❌ Not available | ❌ Gap | Must compute |
| RSI (14) | `RSI` | Computed from OHLCV | ✅ Full | Already in `dgi_metrics.py` |
| Stochastic %K | `Stoch.K` | Computed from OHLCV | ✅ Full | Standard formula from H/L/C |
| CCI (20) | `CCI20` | Computed from OHLCV | ✅ Full | (typical_price - SMA) / (0.015 × mean_deviation) |
| ADX (14) | `ADX` | Computed from OHLCV | ✅ Full | Requires +DI/-DI computation |
| Awesome Oscillator | `AO` | Computed from OHLCV | ✅ Full | SMA(5, median) - SMA(34, median) |
| Momentum (10) | `Mom` | Computed from OHLCV | ✅ Full | close - close[10] |
| MACD (12,26) | `MACD.macd` | Computed from OHLCV | ✅ Full | EMA(12) - EMA(26) |
| Williams %R | `W.R` | Computed from OHLCV | ✅ Full | Standard formula |
| Bull Bear Power | `BBPower` | Computed from OHLCV | ✅ Full | close - EMA(13) |
| Ultimate Oscillator | `UO` | Computed from OHLCV | ✅ Full | Weighted multi-period |
| SMA 10/20/30/50/100/200 | `SMA10..SMA200` | Computed from OHLCV | ✅ Full | Already have `calculate_sma` |
| EMA 10/20/30/50/100/200 | `EMA10..EMA200` | Computed from OHLCV | ✅ Full | Standard EMA formula |
| Ichimoku Base Line | `Ichimoku.BLine` | Computed from OHLCV | ✅ Full | (highest_high_26 + lowest_low_26) / 2 |
| VWMA (20) | `VWMA` | Computed from OHLCV + Volume | ✅ Full | Volume-weighted MA |
| Hull MA (9) | `HullMA9` | Computed from OHLCV | ✅ Full | WMA(2×WMA(n/2) - WMA(n), √n) |
| ATR | Not in scanner cols | Computed from OHLCV | ✅ Full | Already familiar pattern |

**Coverage: All indicator VALUES computable ✅. Pre-computed signals ("Buy/Sell/Neutral") lost ❌ — must reimplement signal logic.**

**Key insight:** The signal interpretation logic (`_oscillator_signal`, `_ma_signal`) already exists in `tv_data_fetcher.py`. We just need to compute the raw indicator values from OHLCV and feed them through the same signal functions. The entire `_build_technicals_dict` pipeline can be preserved — only the data source changes.

**Recommended approach:** Create a `technicals_calculator.py` module that:
1. Takes OHLCV DataFrame (from `ticker.history()`)
2. Computes all oscillator and MA values
3. Returns a dict matching the same keys as TradingView scanner (`RSI`, `Stoch.K`, `MACD.macd`, etc.)
4. Existing `_oscillator_signal` and `_ma_signal` functions work unchanged

**Library option:** `ta` (Technical Analysis library for Python) or `pandas-ta` can compute all these indicators in one line each. Would eliminate need to hand-code each formula.

### 2.4 Forecast / Analyst Data (TradingView `forecast`)

| Data Field | TV Scanner Field | yfinance Equivalent | Coverage | Notes |
|------------|-----------------|-------------------|----------|-------|
| Avg Price Target | `price_target_average` | `ticker.info['targetMeanPrice']` or `ticker.analyst_price_targets['mean']` | ✅ Full | |
| High Price Target | `price_target_high` | `ticker.info['targetHighPrice']` or `ticker.analyst_price_targets['high']` | ✅ Full | |
| Low Price Target | `price_target_low` | `ticker.info['targetLowPrice']` or `ticker.analyst_price_targets['low']` | ✅ Full | |
| Median Price Target | `price_target_median` | `ticker.info['targetMedianPrice']` or `ticker.analyst_price_targets['median']` | ✅ Full | |
| Overall Rating (1-5) | `recommendation_mark` | `ticker.info['recommendationMean']` | ✅ Full | Same 1-5 scale |
| Total Analysts | `recommendation_total` | `ticker.info['numberOfAnalystOpinions']` | ✅ Full | |
| Buy/Hold/Sell breakdown | `recommendation_buy/hold/sell` | `ticker.recommendations_summary` | ✅ Full | DataFrame with strongBuy/buy/hold/sell/strongSell |
| Technical Recommendation | `Recommend.All` | ❌ Not available | ❌ Gap | Same gap as technicals |

**Coverage: 7/8 fields ✅. Technical recommendation is the same gap from technicals section.**

**Bonus data from yfinance not available in TradingView:**
- `ticker.upgrades_downgrades` — individual analyst firm upgrades/downgrades with dates, price targets
- `ticker.earnings_estimate` — consensus EPS estimates
- `ticker.revenue_estimate` — consensus revenue estimates  
- `ticker.growth_estimates` — growth estimate percentages
- `ticker.eps_trend` — EPS trend over time
- `ticker.eps_revisions` — EPS revision history

### 2.5 Options Chain (TradingView Playwright interception)

| Data Field | TV Chain | yfinance `option_chain()` | Coverage | Notes |
|------------|----------|--------------------------|----------|-------|
| Strike | ✅ `strike` | ✅ `strike` | ✅ Full | |
| Bid | ✅ `bid` | ✅ `bid` | ✅ Full | |
| Ask | ✅ `ask` | ✅ `ask` | ✅ Full | |
| Mid (theo price) | ✅ `theoPrice` | ❌ Not provided | ⚠️ Compute | `(bid + ask) / 2` or Black-Scholes |
| IV | ✅ `iv` | ✅ `impliedVolatility` | ✅ Full | |
| **Delta** | ✅ `delta` | ❌ Not provided | ❌ Gap | Must compute via Black-Scholes |
| **Gamma** | ✅ `gamma` | ❌ Not provided | ❌ Gap | Must compute via Black-Scholes |
| **Theta** | ✅ `theta` | ❌ Not provided | ❌ Gap | Must compute via Black-Scholes |
| **Vega** | ✅ `vega` | ❌ Not provided | ❌ Gap | Must compute via Black-Scholes |
| **Rho** | ✅ `rho` | ❌ Not provided | ❌ Gap | Must compute via Black-Scholes |
| Bid IV | ✅ `bid_iv` | ❌ Not provided | ❌ Gap | Minor — main IV sufficient |
| Ask IV | ✅ `ask_iv` | ❌ Not provided | ❌ Gap | Minor — main IV sufficient |
| Last Price | ❌ | ✅ `lastPrice` | ✅ Bonus | |
| Volume | ❌ | ✅ `volume` | ✅ Bonus | Useful for liquidity filtering |
| Open Interest | ❌ | ✅ `openInterest` | ✅ Bonus | Critical for liquidity assessment |
| In The Money | ❌ | ✅ `inTheMoney` | ✅ Bonus | |
| Last Trade Date | ❌ | ✅ `lastTradeDate` | ✅ Bonus | Staleness detection |
| Contract Symbol | ✅ `opra_symbol` | ✅ `contractSymbol` | ✅ Full | |
| Currency | ✅ `currency` | ✅ `currency` | ✅ Full | |
| Contract Size | ❌ | ✅ `contractSize` | ✅ Bonus | |

**yfinance option chain columns (verified):**
```
contractSymbol, lastTradeDate, strike, lastPrice, bid, ask, change, 
percentChange, volume, openInterest, impliedVolatility, inTheMoney, 
contractSize, currency
```

**Expiration dates: AAPL has 23 expirations via yfinance vs. ~5 from TradingView.** This is the biggest win — agents can evaluate far more expiration choices for optimal DTE targeting.

### Greeks Computation (Addressing the Gap)

The 5 Greeks (delta, gamma, theta, vega, rho) must be computed. This is standard and well-solved:

```python
# Using py_vollib or scipy
from py_vollib.black_scholes.greeks import analytical as greeks
from py_vollib.black_scholes import black_scholes

# Inputs available from yfinance:
# S = current stock price (ticker.info['currentPrice'])
# K = strike price (from option_chain)
# T = time to expiry (computed from expiration date)
# r = risk-free rate (e.g., 10Y Treasury yield via yfinance or fixed)
# sigma = implied volatility (from option_chain 'impliedVolatility')

delta = greeks.delta('c', S, K, T, r, sigma)  # 'c' for call, 'p' for put
gamma = greeks.gamma('c', S, K, T, r, sigma)
theta = greeks.theta('c', S, K, T, r, sigma)
vega  = greeks.vega('c', S, K, T, r, sigma)
rho   = greeks.rho('c', S, K, T, r, sigma)
```

**Libraries:** `py_vollib` (fast, C-optimized) or `mibian` or pure scipy `norm.cdf` implementation. We already referenced Black-Scholes in the MCP migration (Decision #4: `apply` parameter with `bs_delta`, `bs_gamma`, etc.). This is the same math, just computed locally.

**Risk-free rate:** Can use `yf.Ticker('^TNX').info['regularMarketPrice'] / 100` for 10Y Treasury yield, or hardcode a reasonable default (e.g., 4.5%).

**Effort:** ~50 lines of Python for all 5 Greeks. Well-tested libraries available.

---

## 3. Data NOT Available from yfinance (True Gaps)

| Data | Impact | Mitigation |
|------|--------|-----------|
| Pre-computed Buy/Sell/Neutral signals | Medium — agents currently reference these | Recompute from raw indicators (logic already in `tv_data_fetcher.py`) |
| Bid IV / Ask IV (separate from main IV) | Low — agents use main IV | Use single IV; not decision-critical |
| Dividend growth years (SA cross-check) | Medium — SA was more reliable than computed | Already compute in `dgi_metrics.py`; validate periodically |
| TradingView "theo price" (mid) | Low | Compute as `(bid + ask) / 2` |

**None of these are blocking.** All have straightforward mitigations.

---

## 4. WINS — What Gets Better

### 4.1 Options Chain: Massive Improvement
- **23+ expiration dates** vs. ~5 from TradingView
- Agents can properly target 30-45 DTE sweet spot with granular choices
- **Volume and Open Interest** data — enables liquidity filtering (avoid illiquid contracts)
- **Last Trade Date** — detect stale quotes
- No Playwright, no browser interception, no URL pattern matching, no `scan/scan2/scan3/screener` endpoint migrations

### 4.2 Elimination of Scraping Fragility
- **No Playwright dependency** — removes ~50MB of Chromium downloads, async browser management, headless detection bypass
- **No anti-bot detection** — no User-Agent rotation, no 403 recovery with exponential backoff, no warmup pages, no cookie harvesting
- **No HTML parsing fragility** — no dual-strategy (DOM + regex) scraping, no field name alias maps
- **No TradingView API endpoint migrations** — the `scan/scan2/scan3/screener` migration that caused premium=0.0 across all agents would never happen

### 4.3 Single Dependency
- Replace 3 external dependencies (TradingView + StockAnalysis.com + existing yfinance) with 1
- Remove `playwright`, `requests`, `beautifulsoup4` as scraping deps (keep `requests` for other uses)
- Simpler CI/CD, faster container builds (no Playwright install)

### 4.4 Bonus Data
- **Analyst upgrades/downgrades** with specific firms, dates, and price target changes
- **EPS estimates, revenue estimates, growth estimates** — richer consensus data
- **Insider transactions and institutional holders** — was removed in MCP migration (Decision #4)
- **Earnings history** — actual vs. estimated EPS for surprise analysis
- **SEC filings** — link to primary sources
- **WebSocket streaming** — potential for real-time price updates

### 4.5 Rate Limiting Already Solved
- `yfinance_fetcher.py` already has `_rate_limit()` with configurable RPM and exponential backoff retries
- Extends naturally to options chain fetching

---

## 5. RISKS

### 5.1 Yahoo Finance API Stability
- yfinance is unofficial — Yahoo can change/block the API at any time
- **Mitigation:** yfinance has 18k+ GitHub stars, massive community, actively maintained. Any breakage gets fixed within days. This is the same risk TradingView has (and TV already broke on us).

### 5.2 Greeks Computation Accuracy
- Our computed Greeks vs. TradingView's may differ slightly due to model assumptions (dividend yield handling, American vs. European options)
- **Mitigation:** Use `py_vollib` which handles American options. Differences will be within acceptable trading tolerances. Our agents already tolerate approximate values.

### 5.3 Rate Limiting
- Yahoo may throttle aggressive option chain fetching (fetching all 23 expirations = 23 API calls per symbol)
- **Mitigation:** Already have rate limiting in `yfinance_fetcher.py`. Can batch with 1-second delays. For 10 symbols × 23 expirations = ~230 calls, at 1/sec = ~4 minutes. Acceptable for our polling cadence.

### 5.4 Dividend Growth Years Reliability
- Losing StockAnalysis.com cross-check means relying solely on Yahoo's dividend series
- **Mitigation:** `calculate_years_consecutive_increases()` is well-tested. Can add a static Dividend Aristocrat list as a secondary validation.

### 5.5 Data Timing
- yfinance data may have slight delays vs. TradingView (which intercepts real-time scanner data)
- **Mitigation:** Our agents run on multi-hour polling cycles. 15-minute delay is irrelevant for options strategy decisions.

---

## 6. Implementation Roadmap (Suggested)

### Phase 1: Options Chain Migration (Highest Impact)
1. Extend `yfinance_fetcher.py` with `get_options_chain(symbol)` method
2. Fetch ALL expiration dates via `ticker.options`
3. Fetch chain for each date via `ticker.option_chain(date)`
4. Add Greeks computation (Black-Scholes from IV + stock price + strike + DTE)
5. Output in same format as `options_chain_parser.py` (strike-keyed dicts per expiration)
6. **Eliminates:** `tv_data_fetcher.py` Playwright section, `options_chain_parser.py`

### Phase 2: Fundamentals + Dividends + Forecast
1. Map all `ticker.info` fields to current overview/dividend/forecast structures
2. Add `ticker.recommendations_summary` for analyst breakdown
3. Add `ticker.analyst_price_targets` for price targets
4. **Eliminates:** `tv_data_fetcher.py` BS4 sections, `stockanalysis_fetcher.py`

### Phase 3: Technicals
1. Create `technicals_calculator.py` with all oscillator/MA computations
2. Use `ticker.history(period="1y")` for OHLCV
3. Reuse signal logic from `tv_data_fetcher.py` (`_oscillator_signal`, `_ma_signal`)
4. Consider `pandas-ta` library to avoid hand-coding 15+ indicators
5. **Eliminates:** TradingView scanner API dependency for technicals

### Phase 4: Agent Instructions Update
1. Update all `tv_*_instructions.py` files — remove TradingView-specific data gathering steps
2. Simplify agent prompts — data arrives pre-fetched, no discovery protocol needed
3. Update `OPTIONS_CHAIN_SCHEMA_DESCRIPTION` for new column names

### Phase 5: Cleanup
1. Remove `tv_data_fetcher.py`, `tv_cache.py`, `stockanalysis_fetcher.py`, `options_chain_parser.py`
2. Remove Playwright from `requirements.txt`
3. Update container builds (remove Chromium/Playwright install steps)

---

## 7. yfinance API Quick Reference

```python
import yfinance as yf
t = yf.Ticker("AAPL")

# Fundamentals (single dict, ~150 keys)
t.info                     # marketCap, trailingPE, dividendYield, sector, etc.

# Price History
t.history(period="1y")     # OHLCV DataFrame (Open, High, Low, Close, Volume)
t.history(period="2y")     # Longer history for 200-day SMA

# Dividends
t.dividends                # Full dividend history (Series indexed by date)
t.info['exDividendDate']   # Next ex-div date (epoch)
t.info['dividendRate']     # Annual dividend per share
t.info['payoutRatio']      # Payout ratio (decimal)

# Options
t.options                  # Tuple of all expiration date strings ("2026-05-15", ...)
t.option_chain("2026-06-18")  # OptionChain(calls=DataFrame, puts=DataFrame)
# Columns: contractSymbol, lastTradeDate, strike, lastPrice, bid, ask,
#           change, percentChange, volume, openInterest, impliedVolatility,
#           inTheMoney, contractSize, currency

# Analyst Data
t.analyst_price_targets    # Dict: current, high, low, mean, median
t.recommendations          # Same as recommendations_summary
t.recommendations_summary  # DataFrame: period, strongBuy, buy, hold, sell, strongSell
t.upgrades_downgrades      # DataFrame: Firm, ToGrade, FromGrade, Action, priceTarget changes

# Earnings
t.info['earningsTimestampStart']  # Next earnings date
t.earnings_estimate        # Consensus EPS estimates
t.revenue_estimate         # Consensus revenue estimates
t.earnings_history         # Actual vs. estimated EPS
t.eps_trend                # EPS trend
t.growth_estimates         # Growth estimate percentages

# Financials
t.income_stmt              # Annual income statement
t.quarterly_income_stmt    # Quarterly income statement
t.balance_sheet            # Annual balance sheet
t.cashflow                 # Annual cash flow
```

---

## 8. Final Verdict

| Dimension | Score | Detail |
|-----------|-------|--------|
| Data Coverage | 9/10 | Only missing pre-computed signals and raw Greeks (both computable) |
| Options Chain | 10/10 | Massive upgrade: 23 expirations, volume, OI, last trade date |
| Reliability | 9/10 | No scraping = no breakage. yfinance community fixes Yahoo changes fast |
| Simplicity | 10/10 | One library, no Playwright, no anti-bot, no HTML parsing |
| Risk | Low | Main risk is Yahoo API changes, same risk class as current TradingView |
| Effort | Medium | ~2-3 days for full migration. Greeks + technicals computation is the bulk |

**Recommendation: Proceed with migration.** The benefits dramatically outweigh the costs. Options chain improvement alone justifies the change — agents currently can't properly evaluate the 30-45 DTE sweet spot with only 5 expiration dates.

---

# Architecture Transition Plan: Full yfinance Migration

**Date:** 2026-05-14  
**Author:** Danny (Lead)  
**Status:** Approved — Ready for Implementation  
**Based on:** Linus's feasibility analysis (`linus-yfinance-feasibility.md`)  
**Directive:** User (dsanchor) — NO fallback, NO keeping old code. Clean cut.

---

## Executive Summary

This plan replaces **all** TradingView and StockAnalysis.com data sources with yfinance. It eliminates Playwright, BeautifulSoup scraping, anti-bot detection, and User-Agent rotation. The system gains 23+ option expiration dates (vs ~5), volume/OI liquidity data, and a single reliable data dependency. Estimated effort: **4-5 days** across 5 phases.

---

## 1. File Impact Analysis

### 1.1 Files to DELETE (7 files — 4,227 lines removed)

| File | Lines | Reason |
|------|-------|--------|
| `src/tv_data_fetcher.py` | 1,611 | Entire TradingView scraping layer (BS4 + Playwright). Replaced by `yfinance_data_provider.py` |
| `src/tv_cache.py` | 125 | TradingView-specific cache with anti-stampede locks. yfinance is fast enough for direct calls; replaced by simpler cache in provider |
| `src/stockanalysis_fetcher.py` | 192 | SA scraping for dividend growth years. Replaced by `dgi_metrics.py` computation from `ticker.dividends` |
| `src/options_chain_parser.py` | 684 | Parses raw TradingView scanner JSON (field alias maps, OPRA symbol extraction). yfinance returns clean DataFrames — new builder in provider |
| `TRADINGVIEW_ANTI_BOT.md` | ~200 | No longer relevant |
| `src/tv_open_call_instructions.py` | 589 | Legacy single-phase instructions (superseded by assessment+roll split). Confirm unused before deleting |
| `src/tv_open_put_instructions.py` | 603 | Same — legacy single-phase instructions |

### 1.2 Files to CREATE (3 new files)

| File | Purpose | Est. Lines |
|------|---------|-----------|
| `src/yfinance_data_provider.py` | **Unified data provider** — fetches all 5 resource types (overview, technicals, forecast, dividends, options chain) via yfinance. Includes Greeks computation (Black-Scholes via `py_vollib`) and technicals computation. Returns structured dicts matching the format agents expect. | ~500 |
| `src/greeks_calculator.py` | **Black-Scholes Greeks module** — computes delta, gamma, theta, vega, rho from IV + stock price + strike + DTE + risk-free rate. Uses `py_vollib`. Separated for testability and reuse. | ~80 |
| `src/technicals_calculator.py` | **Technical indicators module** — computes all oscillators (RSI, Stochastic, CCI, ADX, MACD, Momentum, Williams %R, etc.) and MAs (SMA/EMA 10-200, Ichimoku, VWMA, Hull MA) from OHLCV. Includes signal interpretation (Buy/Sell/Neutral). Uses `pandas-ta` where available, falls back to manual. | ~250 |

### 1.3 Files to MODIFY (Heavy — 16 files)

| File | Lines | What Changes | Why |
|------|-------|-------------|-----|
| `src/agent_runner.py` | 2,338 | **HEAVY.** Replace `fetcher.fetch_all()` calls with `YFinanceDataProvider.fetch_all()`. Remove all `tv_403` error handling (no more 403s). Remove `_get_tv_cache()` import. Update `run_symbol_agent()` and `run_position_monitor()` data injection. Change `=== PRE-FETCHED TRADINGVIEW DATA ===` header to `=== PRE-FETCHED MARKET DATA ===`. Remove `write_telemetry("tv_fetch", ...)` blocks. Keep `_format_options_chain()` and `_format_current_contract_chain()` but update to work with new structured data (no raw text parsing). Keep `_validate_premium_against_chain()` — works on parsed dict, minimal changes. | Core data flow |
| `src/covered_call_agent.py` | 67 | Replace `from .tv_data_fetcher import create_fetcher` with `from .yfinance_data_provider import create_provider`. Replace `async with create_fetcher(config) as fetcher` with `provider = create_provider(config)`. Remove `tradingview_randomize_symbols` reference (randomization stays, config key changes to `data_provider.randomize_symbols`). | Fetcher swap |
| `src/cash_secured_put_agent.py` | 67 | Same changes as `covered_call_agent.py` | Fetcher swap |
| `src/open_call_monitor_agent.py` | 80 | Same pattern. Replace `create_fetcher` → `create_provider` | Fetcher swap |
| `src/open_put_monitor_agent.py` | 80 | Same pattern | Fetcher swap |
| `src/main.py` | ~550 | Replace `_run_options_chain_fetch_async()` — use `YFinanceDataProvider` instead of `create_fetcher`. Remove `from .tv_cache import get_tv_cache`. Remove `from .tv_data_fetcher import create_fetcher`. Update options chain scheduler to use new provider. | Scheduler |
| `src/config.py` | ~180 | Remove all `tradingview_*` properties (6 properties). Add `yfinance_*` properties: `requests_per_minute`, `max_retries`, `randomize_symbols`. Remove `options_chain_scheduler` TradingView-specific config if no longer needed. | Config cleanup |
| `src/dgi_screener.py` | ~350 | Remove `_apply_stockanalysis_overrides()` — SA is being deleted. Remove `EXCHANGE_MAP` / `_normalize_exchange()` if no longer needed (exchange mapping moves to provider). | SA removal |
| `src/dgi_metrics.py` | ~400 | **Extend** with additional technical indicators if `technicals_calculator.py` reuses/wraps these. Existing RSI/SMA/Bollinger stay. May become a thin wrapper that delegates to `technicals_calculator.py` for consistency. | Indicator consolidation |
| `src/yfinance_fetcher.py` | 134 | **Keep and extend.** Add `get_options_chain(symbol)`, `get_overview(symbol)`, `get_technicals(symbol)`, `get_forecast(symbol)`, `get_dividends(symbol)` methods. This becomes the low-level yfinance wrapper; `yfinance_data_provider.py` orchestrates the higher-level formatting. | Extended API |
| `src/cosmos_db.py` | ~1000 | Minor. Remove `get_tv_health_status()` and `set_tv_health_status()` methods. Update `write_telemetry()` — change telemetry type from `"tv_fetch"` to `"data_fetch"`. | Cleanup |
| `web/app.py` | ~2600 | **MODERATE.** Replace all `from src.tv_data_fetcher import create_fetcher` / `from src.tv_cache import get_tv_cache` imports. Update `/api/fetch-preview`, `/api/cache/stats`, `/api/cache/clear`, `/debug` endpoints to use new provider. Rename "TradingView" references in UI text to "Market Data". Remove Playwright-specific fetch preview logic. | Web layer |
| `config.yaml` | 82 | Remove entire `tradingview:` section (lines 47-55). Rename `options_chain_scheduler` if desired. Add `yfinance:` section with `requests_per_minute: 60`, `max_retries: 3`. | Config |
| `requirements.txt` | 17 | Remove `playwright>=1.40.0`. Remove `beautifulsoup4>=4.12.0` (only used by TV/SA fetchers). Add `py-vollib>=1.0.0` (Greeks). Optionally add `pandas-ta>=0.3.0` (technicals — evaluate if worth the dependency). Keep `requests>=2.31.0` (used elsewhere). | Dependencies |
| `Dockerfile` | 24 | Remove `RUN playwright install chromium --with-deps` (saves ~50MB+ in image size). Remove Playwright-related comments. | Build |
| `README.md` | varies | Remove all TradingView setup instructions. Remove anti-bot section. Update data source description. Remove Playwright dependency mention. | Docs |

### 1.4 Instruction Files to MODIFY (12 files — ~6,500 lines total)

All instruction files need updates. The changes follow a consistent pattern:

| File | Lines | Key Changes |
|------|-------|------------|
| `tv_covered_call_instructions.py` | 785 | (1) Rename file → `covered_call_instructions.py`. (2) Replace "pre-fetched from TradingView" → "pre-fetched market data". (3) Remove "You do NOT have any browser tools" (irrelevant). (4) Update Phase 1 data sections — no more "OVERVIEW PAGE", "TECHNICALS PAGE" with TV-specific field descriptions. Use generic section names. (5) Remove pivot point references from technicals (we don't compute those — they're TradingView-specific). (6) Add new fields: volume, openInterest, lastTradeDate in options chain description. (7) Update `OPTIONS_CHAIN_SCHEMA_DESCRIPTION` references for new fields. (8) Remove bid_iv/ask_iv references (not available). (9) Remove OPRA symbol references — yfinance uses `contractSymbol`. |
| `tv_cash_secured_put_instructions.py` | 1,058 | Same pattern as covered call. Additionally: remove CSP-specific pivot point strike selection guidance (S1-S3). Replace with SMA/support level based guidance. |
| `tv_open_call_assessment_instructions.py` | 536 | Rename → `open_call_assessment_instructions.py`. Update data source references. |
| `tv_open_call_roll_instructions.py` | 370 | Rename → `open_call_roll_instructions.py`. Update references. |
| `tv_open_put_assessment_instructions.py` | 534 | Rename → `open_put_assessment_instructions.py`. Update references. |
| `tv_open_put_roll_instructions.py` | 373 | Rename → `open_put_roll_instructions.py`. Update references. |
| `tv_open_call_chat_instructions.py` | 335 | Rename → `open_call_chat_instructions.py`. |
| `tv_open_put_chat_instructions.py` | 382 | Rename → `open_put_chat_instructions.py`. |
| `tv_supervisor_instructions.py` | 545 | Rename → `supervisor_instructions.py`. Remove TradingView-specific quality checks. |
| `tv_alpha_instructions.py` | 491 | Rename → `alpha_instructions.py`. |
| `tv_report_instructions.py` | 96 | Rename → `report_instructions.py`. |
| `tv_summary_instructions.py` | 172 | Rename → `summary_instructions.py`. |

**Simplification opportunity:** With yfinance, agent instructions get SIMPLER:
- Remove all "if section shows [ERROR: ...]" fallback logic (no more 403s)
- Remove "pivot points" references entirely (we don't compute those)
- Remove warnings about "TradingView API field name changes"
- Remove dual-parsing guidance ("tab-separated table data" descriptions)
- Add: volume/OI liquidity filtering guidance ("avoid contracts with OI < 100 or volume < 10")
- Add: `lastTradeDate` staleness check ("skip contracts not traded in 3+ days")

### 1.5 Files UNCHANGED

| File | Reason |
|------|--------|
| `src/context.py` | Reads from CosmosDB, no data source dependency |
| `src/cosmos_db.py` | Minor changes only (removing TV health methods) |
| `src/telegram_notifier.py` | Notification layer, data-source agnostic |
| `src/report_agent.py` | Uses instructions + runner, no direct data fetch |
| `src/__init__.py` | Package init |

---

## 2. Architecture Decisions

### Decision 1: New `yfinance_data_provider.py` (not extending `yfinance_fetcher.py`)

**Decision:** Create a NEW `yfinance_data_provider.py` as the high-level orchestrator. Keep `yfinance_fetcher.py` as the low-level yfinance wrapper.

**Rationale:**
- `yfinance_fetcher.py` is a clean, single-responsibility wrapper (rate limiting + retries). It should stay that way.
- The new provider handles: (a) orchestrating multiple data types, (b) formatting data for agent consumption, (c) options chain enrichment with Greeks, (d) technicals computation, (e) caching.
- Clean separation: `yfinance_fetcher.py` knows yfinance. `yfinance_data_provider.py` knows our agents' data contract.

**Interface:**
```python
class YFinanceDataProvider:
    def __init__(self, fetcher: YFinanceFetcher, config: Config):
        self.fetcher = fetcher
        self.greeks = GreeksCalculator()
        self.technicals = TechnicalsCalculator()

    async def fetch_all(self, symbol: str) -> dict:
        """Returns {"overview": str, "technicals": str, "forecast": str,
                     "dividends": str, "options_chain": str}
        Same keys as old tv_data_fetcher — agents receive identical structure."""

def create_provider(config: Config) -> YFinanceDataProvider:
    """Factory function — drop-in replacement for create_fetcher()."""
```

### Decision 2: Options Chain Format — KEEP existing structure, ADD new fields

**Decision:** Keep the existing `options_chain_parser.py` output format (expiration-keyed → strike-keyed dicts) but generate it directly from yfinance DataFrames instead of parsing raw scanner JSON.

**Rationale:**
- All agent instructions reference this format. Changing it means rewriting 6,500+ lines of instructions.
- The format is actually good: hierarchical, easy to look up contracts by expiration + strike.
- Simply build the same dict structure from `ticker.option_chain()` DataFrames.

**Changes to schema:**
```python
# KEEP these fields (computed where needed):
"strike", "bid", "ask", "mid",  # mid = (bid+ask)/2
"iv", "delta", "gamma", "theta", "vega", "rho",
"currency", "expiration", "option_type",

# ADD new fields (yfinance bonus):
"volume", "openInterest", "lastPrice", "lastTradeDate",
"inTheMoney", "contractSymbol",

# REMOVE these fields (not available/needed):
"opra_symbol",  # replaced by contractSymbol
"bid_iv", "ask_iv",  # not available from yfinance
```

**Critical:** The `OPTIONS_CHAIN_SCHEMA_DESCRIPTION` in `options_chain_parser.py` moves to `yfinance_data_provider.py` (updated version). All consumers import from there.

### Decision 3: Greeks — Separate `greeks_calculator.py` module

**Decision:** Dedicated module, not inline.

**Rationale:**
- Greeks computation is pure math — no I/O, no state, easily testable
- Reused by both options chain building AND potential future analytics
- Clear dependency: `py_vollib` is isolated here
- ~80 lines, well-defined interface

**Interface:**
```python
class GreeksCalculator:
    def __init__(self, risk_free_rate: float = None):
        """If rate is None, fetch from ^TNX via yfinance on first call."""

    def compute(self, flag: str, S: float, K: float, T: float,
                sigma: float) -> dict:
        """Returns {"delta": ..., "gamma": ..., "theta": ...,
                    "vega": ..., "rho": ...}"""
```

**Risk-free rate strategy:** Fetch `^TNX` (10Y Treasury) once per run, cache for session. Fallback to 4.5% if unavailable.

### Decision 4: Technicals — `pandas-ta` + dedicated module

**Decision:** Create `technicals_calculator.py` using `pandas-ta` for indicator computation. Consolidate with `dgi_metrics.py` existing indicators.

**Rationale:**
- `dgi_metrics.py` already computes RSI, SMA, Bollinger — but only 3 indicators
- Agents need 15+ indicators (Stochastic, CCI, ADX, MACD, Williams %R, etc.)
- Hand-coding all 15 is error-prone and ~400 lines. `pandas-ta` does them in 1 line each.
- `pandas-ta` is lightweight, well-maintained, and we already depend on pandas/numpy.

**Migration path for `dgi_metrics.py`:**
- Keep `dgi_metrics.py` for DGI-specific scoring logic (quality scores, categorization, filters)
- Move raw indicator computation to `technicals_calculator.py`
- `dgi_metrics.py` calls `technicals_calculator.py` for RSI/SMA/Bollinger instead of computing them inline
- This avoids duplicate indicator code

**Signal interpretation:** Port `_oscillator_signal()` and `_ma_signal()` from `tv_data_fetcher.py` into `technicals_calculator.py`. These convert raw values to Buy/Sell/Neutral. Then build composite `Recommend.All` / `Recommend.Other` / `Recommend.MA` equivalents.

**Pivot points:** DROP. Pivot points (Classic, Fibonacci, Camarilla, Woodie, DM) are TradingView-specific pre-computed data. They are easily computed from daily OHLCV, BUT the question is: **do agents actually need them?**

Analysis: Covered call instructions say "use R1-R3 as strike targets". CSP instructions say "use S1-S3 as strike targets". These are useful but can be replaced with simpler guidance: "set strike near resistance levels (above current SMA-50/200)" for calls, "set strike near support levels (below SMA-50/200)" for puts. **Decision: Omit pivot points initially. Add in Phase 5 if agents struggle with strike selection.**

### Decision 5: Caching — Simplify

**Decision:** Replace `tv_cache.py` (async locks, per-resource TTLs) with a simpler TTL cache in `YFinanceDataProvider`.

**Rationale:**
- `tv_cache.py` was designed around TradingView's slow, fragile scraping (15-second Playwright fetches, hourly pre-warm scheduler)
- yfinance calls complete in 1-3 seconds per symbol — caching is less critical
- The options chain scheduler in `main.py` can still pre-warm if desired, but writes to a simpler dict cache
- No need for per-resource TTLs or async locks

**Implementation:** Simple `{symbol: {data: dict, timestamp: float}}` dict with configurable TTL. The options chain scheduler populates it; agent runs check freshness before re-fetching.

### Decision 6: Agent Instruction File Renaming

**Decision:** Rename all `tv_*_instructions.py` → drop the `tv_` prefix.

**Rationale:**
- "TV" stood for TradingView. That's gone.
- The instructions describe the agent's strategy logic, not the data source.
- Cleaner imports: `from .covered_call_instructions import COVERED_CALL_INSTRUCTIONS`

**git mv** all 14 files in a single commit (rename-only) to preserve git history.

---

## 3. Migration Phases

### Phase 1: Foundation — New Modules (Parallelizable, 1 day)

**Owner: Linus (Quant Dev)**

Create the three new modules with full test coverage:

1. **`src/greeks_calculator.py`** (~80 lines)
   - Black-Scholes Greeks via `py_vollib`
   - Risk-free rate fetcher (^TNX with fallback)
   - Unit tests: known option → known Greeks (compare to reference values)

2. **`src/technicals_calculator.py`** (~250 lines)
   - All oscillator computations from OHLCV
   - All MA computations (SMA/EMA 10-200, Ichimoku, VWMA, Hull MA)
   - Signal interpretation (Buy/Sell/Neutral)
   - Composite recommendation scores
   - Port `_oscillator_signal()` / `_ma_signal()` from `tv_data_fetcher.py`
   - Unit tests: known OHLCV → known indicator values

3. **`src/yfinance_data_provider.py`** (~500 lines)
   - `fetch_all(symbol)` → returns dict with 5 keys matching old format
   - Options chain builder: DataFrame → structured dict with Greeks
   - Overview/Forecast/Dividends formatters
   - Technicals formatter using `TechnicalsCalculator`
   - Simple TTL cache
   - Integration test: fetch real data for AAPL, verify all 5 sections populated

4. **Update `requirements.txt`**
   - Add `py-vollib>=1.0.0`
   - Add `pandas-ta>=0.3.0`
   - Do NOT remove playwright/bs4 yet (old code still runs)

**Testing strategy:** New modules are self-contained. Test with real yfinance calls (AAPL, MSFT) to verify data shapes. Mock yfinance for unit tests of formatting/computation.

**Can run in parallel with:** Nothing depends on this yet. Linus works independently.

### Phase 2: Core Pipeline Swap (Sequential, 1.5 days)

**Owner: Rusty (Agent Dev)**  
**Depends on:** Phase 1 complete

Replace the data pipeline in agent execution:

1. **`src/agent_runner.py`** — The big one
   - Replace `from .tv_cache import get_tv_cache` → remove
   - Replace `fetcher.fetch_all()` → `provider.fetch_all()`
   - Remove all `tv_403` error handling (15+ lines per method, two methods)
   - Change message template: `=== PRE-FETCHED TRADINGVIEW DATA ===` → `=== PRE-FETCHED MARKET DATA ===`
   - Section headers: `--- OVERVIEW PAGE ---` → `--- OVERVIEW ---` etc.
   - Update `_format_options_chain()` — no longer parses raw text, receives structured dict
   - Update `_format_current_contract_chain()` — same
   - Keep `_validate_premium_against_chain()` — works on parsed dict already
   - Update telemetry: `"tv_fetch"` → `"data_fetch"`
   - Remove `fetch_stats` tracking (no more per-resource timing from TV)

2. **4 agent wrappers** (covered_call, cash_secured_put, open_call_monitor, open_put_monitor)
   - Replace `from .tv_data_fetcher import create_fetcher` → `from .yfinance_data_provider import create_provider`
   - Replace `async with create_fetcher(config) as fetcher:` → `provider = create_provider(config)` (no async context manager needed)
   - Replace `tradingview_randomize_symbols` → `config.randomize_symbols`

3. **`src/main.py`**
   - Update `_run_options_chain_fetch_async()` to use `YFinanceDataProvider`
   - Remove `from .tv_data_fetcher import create_fetcher`
   - Remove `from .tv_cache import get_tv_cache`

4. **`src/config.py`**
   - Remove 6 `tradingview_*` properties
   - Add `yfinance_requests_per_minute`, `yfinance_max_retries`, `randomize_symbols`

5. **`config.yaml`**
   - Remove `tradingview:` section
   - Add `yfinance:` section

6. **`web/app.py`**
   - Replace all `tv_data_fetcher` / `tv_cache` imports
   - Update fetch preview endpoint
   - Update cache management endpoints
   - Update debug page

**Testing strategy:**
- Run a single symbol analysis (e.g., AAPL covered call) end-to-end
- Verify agent receives all 5 data sections
- Verify premium validation still catches mismatches
- Verify options chain format is parseable by agents
- Compare agent output quality to a baseline from TradingView era

### Phase 3: Instruction Files Update (Parallelizable, 1 day)

**Owner: Linus (Quant Dev) — he owns instruction content**  
**Depends on:** Phase 2 complete (need to know exact data format)

1. **Rename all 14 `tv_*` instruction files** (single git mv commit):
   ```
   tv_covered_call_instructions.py → covered_call_instructions.py
   tv_cash_secured_put_instructions.py → cash_secured_put_instructions.py
   tv_open_call_assessment_instructions.py → open_call_assessment_instructions.py
   tv_open_call_roll_instructions.py → open_call_roll_instructions.py
   tv_open_call_chat_instructions.py → open_call_chat_instructions.py
   tv_open_put_assessment_instructions.py → open_put_assessment_instructions.py
   tv_open_put_roll_instructions.py → open_put_roll_instructions.py
   tv_open_put_chat_instructions.py → open_put_chat_instructions.py
   tv_supervisor_instructions.py → supervisor_instructions.py
   tv_alpha_instructions.py → alpha_instructions.py
   tv_report_instructions.py → report_instructions.py
   tv_summary_instructions.py → summary_instructions.py
   ```

2. **Update all imports** in files that reference old names (agent_runner.py, agent wrappers, web/app.py, main.py)

3. **Content updates** (consistent across all instruction files):
   - "pre-fetched from TradingView" → "pre-fetched"
   - Remove browser tool disclaimers
   - Remove TV-specific field format descriptions
   - Remove pivot point references (Covered Call: R1-R3 for strikes; CSP: S1-S3 for strikes)
   - Replace with: "use SMA-50/200 and recent support/resistance levels for strike selection"
   - Add liquidity guidance: "Prefer contracts with openInterest ≥ 100 and volume ≥ 10"
   - Add staleness check: "Skip contracts with lastTradeDate > 3 days ago"
   - Update OPTIONS_CHAIN_SCHEMA_DESCRIPTION: add volume, openInterest, lastTradeDate, contractSymbol; remove opra_symbol, bid_iv, ask_iv
   - Remove "[ERROR: ...]" fallback logic
   - Remove "TradingView API field name change" warnings

**Testing strategy:** Run all 4 agent types on 2-3 symbols. Review agent output for coherence. Verify agents correctly use volume/OI data in their analysis.

### Phase 4: Cleanup & Deletion (Sequential, 0.5 days)

**Owner: Rusty (Agent Dev)**  
**Depends on:** Phases 2 and 3 complete and verified

1. **Delete files:**
   - `src/tv_data_fetcher.py` (1,611 lines)
   - `src/tv_cache.py` (125 lines)
   - `src/stockanalysis_fetcher.py` (192 lines)
   - `src/options_chain_parser.py` (684 lines)
   - `src/tv_open_call_instructions.py` (589 lines — legacy, verify unused)
   - `src/tv_open_put_instructions.py` (603 lines — legacy, verify unused)
   - `TRADINGVIEW_ANTI_BOT.md`

2. **Update `requirements.txt`:**
   - Remove `playwright>=1.40.0`
   - Remove `beautifulsoup4>=4.12.0`

3. **Update `Dockerfile`:**
   - Remove `RUN playwright install chromium --with-deps`
   - Update comment: remove "Playwright for TradingView data fetching"

4. **Update `src/cosmos_db.py`:**
   - Remove `get_tv_health_status()` / `set_tv_health_status()` methods

5. **Update `src/dgi_screener.py`:**
   - Remove `_apply_stockanalysis_overrides()` function
   - Remove `from .stockanalysis_fetcher import fetch_dividend_data`
   - Review `EXCHANGE_MAP` — keep if still needed for CosmosDB symbol format

6. **Update `README.md`:**
   - Remove TradingView setup instructions
   - Remove Playwright/Chromium dependency notes
   - Remove anti-bot troubleshooting section
   - Update data source description to yfinance
   - Update architecture diagram if present

7. **Consolidate `dgi_metrics.py`:**
   - Replace inline RSI/SMA/Bollinger with calls to `technicals_calculator.py`
   - Avoid duplicate indicator implementations

**Testing strategy:** Full regression — run all 4 agent types + DGI screener. Verify Docker build succeeds without Playwright. Verify image size reduction.

### Phase 5: Optimization & Hardening (Parallelizable, 1 day)

**Owner: Linus + Rusty**  
**Depends on:** Phase 4 complete

1. **Options chain fetching optimization:**
   - Don't fetch all 23 expirations — filter to relevant DTE range (7-90 days) before fetching chains
   - Implement batch caching: pre-warm cache for all symbols at scheduled intervals
   - Rate limit tuning: find optimal RPM for Yahoo's current limits

2. **Agent output quality validation:**
   - Run both agents on 10+ symbols, compare output to TradingView-era baselines
   - Check: Are agents selecting reasonable strikes? Are premiums accurate? Are Greeks in expected ranges?
   - Verify delta filtering still works (0.15-0.40 range for OTM)

3. **Pivot points (conditional):**
   - If agents struggle with strike selection without pivot points, add Classic pivot computation to `technicals_calculator.py`
   - Simple formula: P = (H+L+C)/3, R1 = 2P-L, S1 = 2P-H, etc.

4. **Monitoring:**
   - Add yfinance-specific telemetry (fetch time, symbols processed, rate limit hits)
   - Add health check for yfinance availability (try fetch ^GSPC on startup)

---

## 4. Risk Assessment

### 4.1 What Could Break

| Risk | Severity | Mitigation |
|------|----------|-----------|
| **Options chain format mismatch** — agents can't parse new chain structure | HIGH | Keep exact same dict structure. Phase 2 testing catches this immediately. |
| **Greeks accuracy divergence** — our computed Greeks differ from TradingView's | MEDIUM | Use `py_vollib` (handles American options). Differences within trading tolerance. Validate against known values in Phase 1 tests. |
| **Missing pivot points** — agents give worse strike selection | MEDIUM | Replace with SMA-based guidance. Monitor output quality in Phase 5. Add pivot computation if needed. |
| **Rate limiting** — Yahoo throttles aggressive fetching | LOW | Already have rate limiter in `yfinance_fetcher.py`. Start conservative (30 RPM), tune up. |
| **Import chain breaks** — renaming 14 instruction files creates import errors | LOW | Do renames in a single commit. Use `grep -r` to find ALL imports before committing. |
| **DGI screener loses SA cross-check** — dividend growth years less accurate | LOW | `calculate_years_consecutive_increases()` is well-tested. Validate against Dividend Aristocrat list. |
| **Agent prompt drift** — instructions reference removed features | MEDIUM | Systematic search-and-replace across all instruction files. Review each one individually. |

### 4.2 What Needs Careful Testing

1. **Options chain end-to-end:** Fetch AAPL chain → compute Greeks → build structured dict → inject into agent → verify agent can read bid/ask/delta correctly
2. **Premium validation:** Ensure `_validate_premium_against_chain()` works with new dict keys (no field name changes if we're careful)
3. **Monitor agents (2-phase):** Phase 1 assessment receives current contract data → Phase 2 roll receives filtered chain. Both flows must work.
4. **DGI screener:** Run with SA overrides removed. Compare top-20 results to a known-good baseline.
5. **Docker build:** Build image, verify no Playwright traces, verify image size reduction.

### 4.3 Rollback Plan

**There is no rollback.** User directive: NO fallback, no keeping old code.

**Mitigation for this constraint:**
- Each phase has its own verification gate. Do NOT proceed to next phase until current phase is verified.
- Phase 1 (new modules) can be built and tested completely independently — zero risk to existing system.
- Phase 2 (pipeline swap) is the critical cut. Do it on a branch, test thoroughly, merge only when all agents produce acceptable output.
- Phase 4 (deletion) is the point of no return. Only execute after Phase 2+3 are verified on the branch.
- Git history preserves all deleted files if emergency recovery is needed (even if deleted code isn't "kept").

---

## 5. Impact on Agent Instructions

### 5.1 Sections Referencing TradingView-Specific Data

**Covered Call Instructions (`tv_covered_call_instructions.py`):**
- Line ~5: "Data is pre-fetched from TradingView via Playwright"
- Lines ~18-21: "All market data has been pre-fetched from TradingView... You do NOT have any browser tools."
- Lines ~22-24: "Pre-calculated technicals — TradingView provides RSI, MACD... with Buy/Sell/Neutral signals already computed"
- Lines ~25: "Pivot points — Classic, Fibonacci, Camarilla, Woodie, DM"
- Lines ~30-70: Phase 1 data section descriptions (5 sections with TV-specific formatting)
- Lines ~45-55: Technicals section describes "Tab-separated table data" (TV scanner format)
- Options chain: references "OPRA:MSFT260427C475.0" (OPRA format specific to TV)

**CSP Instructions (`tv_cash_secured_put_instructions.py`):**
- Same header/data source blocks as covered call
- Additional: "S1-S3 pivot points as strike price targets" (TV-specific guidance)
- Oversold detection references TV pre-computed values

**Monitor Instructions (assessment + roll for both call/put):**
- All reference TradingView as data source
- All describe the 5-section data format with TV-specific details
- Roll instructions reference TV chain format for buyback cost lookup

**Supervisor/Alpha Instructions:**
- Reference TradingView data quality checks
- May reference specific TV field names

### 5.2 Simplifications Possible

1. **Remove error handling prose:** ~15-20 lines per instruction file about "[ERROR: ...]" fallback and "if data unavailable" scenarios. yfinance either works or raises an exception — no partial 403 failures.

2. **Remove browser tool disclaimers:** "You do NOT have any browser tools" appears in every instruction file. With yfinance, there's no browser in the architecture at all.

3. **Remove TV scanner format descriptions:** The lengthy descriptions of "Tab-separated table data: Name\tValue\tAction" format for technicals can be replaced with simple "JSON dict with indicator names as keys and values/signals as nested dicts."

4. **Add liquidity intelligence:** New instructions can include: "Filter out options with openInterest < 100 or no recent trades (lastTradeDate > 3 days ago). These are illiquid and may have unreliable pricing." This was impossible with TradingView which didn't provide OI/volume.

5. **Simplify strike selection:** Instead of "use R1/R2/R3 pivot points", guide agents with: "Select strikes near or above the 50-day SMA for calls (below for puts). Use the delta range 0.15-0.35 as primary selection criteria."

6. **Add staleness detection:** "If a contract's lastTradeDate is more than 3 trading days ago, flag it as potentially stale and prefer contracts with recent activity."

---

## 6. Dependency Graph

```
Phase 1 (Foundation)        ← Independent, no prerequisites
    │
    ▼
Phase 2 (Pipeline Swap)     ← Requires Phase 1 modules
    │
    ├──▶ Phase 3 (Instructions)  ← Can start after Phase 2 data format is finalized
    │
    ▼
Phase 4 (Cleanup)            ← Requires Phase 2 + Phase 3 verified
    │
    ▼
Phase 5 (Optimization)       ← Requires Phase 4, but parts can overlap with Phase 3
```

**Parallelism opportunities:**
- Phase 1: Linus builds all 3 modules independently
- Phase 3: Can overlap with Phase 2 testing (instruction content is mostly independent of implementation)
- Phase 5: Optimization can start as soon as Phase 2 is verified (doesn't need file deletions)

---

## 7. Config Changes Summary

### Remove from `config.yaml`:
```yaml
# DELETE this entire section:
tradingview:
  request_delay_min: 1.0
  request_delay_max: 3.0
  warmup_enabled: false
  max_403_retries: 3
  retry_delays: [5, 15, 45]
  randomize_symbols: true
```

### Add to `config.yaml`:
```yaml
yfinance:
  requests_per_minute: 60    # Rate limit for yfinance API calls
  max_retries: 3             # Retry attempts per symbol on failure
  randomize_symbols: true    # Shuffle symbol order each run
  cache_ttl: 300             # Data cache TTL in seconds (5 min default)
```

### Update `options_chain_scheduler`:
```yaml
options_chain_scheduler:
  enabled: true
  cron: "0 * * * *"     # Keep hourly — now uses yfinance (faster)
  max_expirations: 8    # Limit expirations to fetch (nearest N by DTE)
```

---

## 8. New Dependencies Summary

| Add | Version | Purpose | Size Impact |
|-----|---------|---------|-------------|
| `py-vollib` | >=1.0.0 | Black-Scholes Greeks (delta, gamma, theta, vega, rho) | ~2MB |
| `pandas-ta` | >=0.3.0 | Technical indicators (15+ oscillators/MAs) | ~5MB |

| Remove | Purpose | Size Impact |
|--------|---------|-------------|
| `playwright` | >=1.40.0 | TradingView browser automation | **-200MB+** (including Chromium) |
| `beautifulsoup4` | >=4.12.0 | HTML parsing for TV/SA | -2MB |

**Net image size change: ~195MB reduction.** Build time improvement: ~30-60 seconds (no Chromium download).

---

## 9. Work Assignment

| Phase | Owner | Duration | Blocker |
|-------|-------|----------|---------|
| Phase 1: Foundation modules | **Linus** | 1 day | None |
| Phase 2: Pipeline swap | **Rusty** | 1.5 days | Phase 1 |
| Phase 3: Instructions update | **Linus** | 1 day | Phase 2 format finalized |
| Phase 4: Cleanup & deletion | **Rusty** | 0.5 days | Phase 2+3 verified |
| Phase 5: Optimization | **Linus + Rusty** | 1 day | Phase 4 |

**Total: 4-5 days** (with parallelism between Phase 3 and Phase 2 testing)

---

## 10. Definition of Done

- [ ] All 4 agent types produce valid JSON output for at least 5 symbols each
- [ ] Greeks values are within 5% of reference values for known test cases
- [ ] Options chain includes volume, openInterest, and lastTradeDate
- [ ] No TradingView/Playwright/BeautifulSoup references remain in codebase
- [ ] Docker image builds without Playwright, image size reduced by >100MB
- [ ] DGI screener produces top-40 rankings without SA fallback
- [ ] All instruction files renamed (no `tv_` prefix)
- [ ] `config.yaml` has no `tradingview:` section
- [ ] `requirements.txt` has no `playwright` or `beautifulsoup4`
- [ ] README updated with yfinance data source description

---

## Phase 2 Completed — Implementation Decisions

### Rusty Phase 2 — Pipeline Swap: TradingView → yfinance
**Status:** ✅ Completed (2026-07)  
**Agent:** Rusty  
**Scope:** Replace ALL TradingView data fetching with yfinance provider across entire application

#### Decisions Made

1. **Clean cut — no fallback**
   - Removed all TradingView import paths and error handling (403, Playwright) from active code
   - Old `tv_data_fetcher.py` and `tv_cache.py` remain in repo but are unreferenced

2. **Exchange prefix removed from fetch calls**
   - Old: `f"{exchange}-{symbol}"` or `f"{exchange}:{symbol}"` → New: plain `symbol`
   - Exchange field still stored in CosmosDB, used for display/context only

3. **`parse_options_chain()` replaced with `json.loads()`**
   - yfinance provider returns pre-structured JSON matching same schema
   - HTML parser no longer needed; filter functions still work unchanged

4. **Provider singleton pattern**
   - web/app.py: `app.state.yf_provider` initialized at startup
   - Agent wrappers: one `create_provider()` call per batch, shared across symbols
   - Built-in in-memory cache (keyed by symbol, TTL from config)

5. **API backward compatibility**
   - `preferences` dict key `"tradingview"` kept to avoid breaking frontend
   - Now controls yfinance market data inclusion

6. **`has_data_error` / `data_error` flag removed**
   - TV-specific 403 tracking deleted entirely
   - yfinance raises exceptions on failure (no partial data with error flags)

#### Implementation Details
- Files changed: 13 (391+/451-)
- config.yaml: tradingview → yfinance section
- Affected modules: agent_runner, covered_call_agent, cash_secured_put_agent, open_call_monitor_agent, open_put_monitor_agent, main.py, web/app.py
- Templates cleaned of TV/Playwright references

---

### Linus Phase 2 — Options Filters Extraction + README
**Status:** ✅ Completed (2026-07)  
**Agent:** Linus  
**Scope:** Parallel Phase 2 track — Standalone filter module + yfinance documentation

#### Decision 1: Options Chain Filters as Standalone Module

**Created:** `src/options_chain_filters.py` (16.8 KB)

- Extracted 5-function filter pipeline from `options_chain_parser.py`
- Functions: `filter_options_chain_by_type`, `filter_options_chain_for_position`, `filter_options_chain_by_delta`, `filter_options_chain_by_roll_direction`, `format_roll_candidates_table`
- Works on unified dict format (YYYYMMDD→strike→contract) from both TV parser and yfinance provider
- Zero dependency on `options_chain_parser.py` — fully self-contained

**Rationale:**
- Filter functions work on provider-agnostic structured format
- Keeping in `options_chain_parser.py` couples unnecessarily to TV parser
- Standalone enables clean imports without pulling TV-specific code

**Migration Note:**
- Existing imports from `options_chain_parser` still work (backward compat)
- New code should import from `options_chain_filters`

#### Decision 2: README — yfinance as Single Data Source

**Updated:** README.md — Comprehensive yfinance migration documentation

**Changes:**
- Opening: yfinance direct API, no browser/scraping/auth
- Architecture: Yahoo Finance as data source
- Pre-fetch section: Renamed to "Pre-fetch Architecture (yfinance)"
- Cache section: Renamed to "Data Cache", describes built-in TTL + rate limiting
- Setup: Removed `playwright install chromium`, added `py-vollib`, `pandas-ta`
- Docker: Removed Playwright/Chromium pre-install
- Troubleshooting: Replaced "Playwright / Chromium Issues" with "Data Fetching Issues"
- Removed all TradingView/Playwright/BS4 references

**Impact:**
- Documentation, onboarding, Docker setup fully aligned with yfinance architecture
- Eliminates confusion about scraping capabilities and requirements

---

## Phase 2 Summary

Both Rusty and Linus tracks completed in parallel:
- **Rusty:** 13 files modified, full pipeline swap to yfinance, clean cut, singleton provider, backward API compat
- **Linus:** Filters module extracted (provider-agnostic), README fully refreshed, yfinance as canonical data source
- **Result:** Application is yfinance-native, no TradingView/Playwright/scraping in active codebase
- **Tests:** 127 passed, 22 failed (old TV tests slated for Phase 4 deletion)

---

## Phase 3: Instruction File Renames

**Date:** 2026-05-14  
**Agent:** Linus  

### Decision: Keep Variable Names As-Is

**Decision:** Do NOT rename internal constants (e.g., `TV_SUMMARY_INSTRUCTIONS`, `TV_REPORT_INSTRUCTIONS`, `TV_CASH_SECURED_PUT_INSTRUCTIONS`).

**Rationale:**
- These are string constants exported from instruction files (the actual prompt text)
- Renaming would require updates across 20+ import sites and dependent modules
- Low semantic value: the variable name doesn't affect runtime behavior
- Marginal clarity gain vs. high refactor cost (low ROI)

**Alternative considered:**
- Rename all constants to drop `TV_` prefix (e.g., `SUMMARY_INSTRUCTIONS`)
- Rejected: Would ripple through agent_runner.py lazy-load patterns, agent modules, web/app.py
- Overkill for string constants

**Execution:**
✅ Completed Phase 3 with minimal scope creep:
- File renames: 14 files via `git mv` (history preserved)
- Import updates: 10 sites across 6 modules
- Tests: 96 passed, no regressions
- Commit: 2709b75

**Future Consideration:**
If/when codebase matures and these constants become "legacy" (e.g., migration to structured prompt templates), revisit full variable rename as part of broader cleanup. For now: acceptable naming debt.

---

## Phase 4: Cleanup (Delete Obsolete Files, Strip Chromium)

**Date:** 2026-05-14  
**Agent:** Rusty  

### Decision: Stubbed `_apply_stockanalysis_overrides` instead of deleting

**Context:** stockanalysis_fetcher.py was deleted as part of Phase 4 cleanup. The function `_apply_stockanalysis_overrides()` in `dgi_screener.py` is called from 2 locations.

**Decision:** Converted the function to a no-op stub rather than removing it and its call sites. This keeps the code path intact if a future data enrichment source is added, and avoids modifying the scoring pipeline logic unnecessarily.

**Alternatives considered:**
1. Delete function + remove all call sites → higher risk of breaking scoring pipeline flow
2. Keep function with try/except ImportError → messy, hides real errors

**Status:** Implemented in commit db7b4ff.

**Phase 4 Summary:**
- Deleted 8 obsolete files (-3,807 lines)
- Removed playwright+bs4 from requirements.txt
- Stripped Chromium from Dockerfile (~400MB savings)
- Stubbed stockanalysis as no-op
- All 96 tests pass, 0 failures
- Commit: db7b4ff

---

## Phase 5: Instruction File Content Update + Provider-Agnostic Terminology

**Date:** 2026-05-14  
**Agents:** Linus (Quant Dev) + Rusty (Agent Dev)  

### Decision: Instruction File Content Updated to Yahoo Finance (Linus)

**Context:** After Phase 3 renamed the 14 `tv_*` instruction files and Phase 4 removed the TradingView/Playwright code, the instruction file *content* still referenced TradingView as the data source. This creates confusion for LLM agents.

**Decision:** Updated all 9 instruction files that contained TradingView/Playwright references (43 replacements total):

| Pattern | Replacement |
|---------|-------------|
| "TradingView" (data source) | "Yahoo Finance" |
| "via Playwright" | "via yfinance" |
| "browser tools" | "data fetching tools" |
| "TradingView provides RSI..." | "computed via pandas-ta" |
| "TradingView Pre-Fetched Data" | "Pre-Fetched Data" |
| "TradingView provides pre-analyzed" | "The provider includes pre-analyzed" |
| "pre-fetched TradingView data" | "pre-fetched market data" |
| "Analyze the TradingView data" | "Analyze the market data" |

**Files unchanged (no TradingView references):**
- `open_call_roll_instructions.py`
- `open_put_roll_instructions.py`
- `alpha_instructions.py`
- `supervisor_instructions.py`
- `summary_instructions.py`

**Preserved:**
- All trading logic, thresholds, DTE rules, delta targets
- Variable names (`TV_CASH_SECURED_PUT_INSTRUCTIONS`, etc.)
- Tool-call prohibition language

**Verification:** 96 tests pass, zero remaining TradingView/Playwright/browser references.

### Decision: Provider-Agnostic Plumbing Terminology (Rusty)

**Problem:** Remaining references to "TradingView", "Playwright", "BeautifulSoup", "stockanalysis" in plumbing files created ambiguity after Phase 4 deletion.

**Decision:** Remove all stale references from plumbing and adopt provider-agnostic terminology:
- Replace "TradingView" with "market data" or "data provider"
- Replace "Playwright" with "yfinance"
- Delete unused functions entirely (`_apply_stockanalysis_overrides`) rather than stubs
- Rename `preferences["tradingview"]` → `preferences["market_data"]`

**Rationale:**
1. **Dead code removal > stubs:** `_apply_stockanalysis_overrides()` no-op was stubbed in Phase 4. Full deletion signals intent, eliminates maintenance burden.
2. **Provider-agnostic naming:** If yfinance is ever replaced, code using "market data" requires no changes. Reduces future migration effort.
3. **Separation of concerns:** Instructions (Linus) keep "TradingView"; plumbing (Rusty) uses neutral terminology. Clear boundary prevents conflicts.
4. **Call sites cleaned up:** Both call sites to `_apply_stockanalysis_overrides()` (lines 113, 266 in dgi_screener.py) removed, not left as orphans.

**Affected Components:**
- `cosmos_db.py`: Health status terminology updated
- `dgi_screener.py`: Removed stockanalysis function + call sites
- `main.py`: Scheduler message updated
- `options_chain_filters.py`: Module docstring cleaned
- `report_agent.py`: Context description updated
- `web/app.py`: Preference key renamed for consistency

**Trade-offs:**
- `preferences["market_data"]` replaces `preferences["tradingview"]` (internal only, not exposed to API)
- Instruction files still use "TradingView" terminology for backward compat, frontend stays compatible

**Outcomes:**
- ✅ All 96 tests pass
- ✅ No dead code remains
- ✅ Plumbing terminology now provider-agnostic
- ✅ Clear separation maintained with instruction files

---

### Decision: Fix prolonged WAIT cooldown deadlock (Linus)

**Author:** Linus (Quant Dev)  
**Date:** 2025-07-15  
**Status:** Implemented  
**Commit:** fix: use alpha_view instead of supervisor_view in prolonged WAIT cooldown

**Problem:** The `_detect_prolonged_wait()` method in `src/agent_runner.py` (lines 672-681) checked `supervisor_view` to find the last Alpha review for cooldown. However, supervisor runs unconditionally on every activity, so all records have `supervisor_view` set. The loop always broke on first record, `waits_since_last_review` was always 0, and cooldown always returned `False` — preventing Alpha from ever triggering on prolonged WAITs. Production evidence: MKC cash_secured_put ran 6+ consecutive WAITs with Supervisor [WEAK], but no Alpha trigger.

**Decision:** Changed cooldown field from `supervisor_view` → `alpha_view`. The `alpha_view` field only exists on records where full supervisor+alpha review cycle has previously fired, making it the correct cooldown signal.

**Scope:** Single field change in `_detect_prolonged_wait()` (line 676). Surgical fix affecting only cooldown gating.

**Risk:** Low.

---

### Decision: Alpha Advisor indicator icon (Linus)

**Author:** Linus (Quant Dev)  
**Date:** 2026-07  
**Status:** Implemented  
**Commit:** feat: add alpha advisor indicator icon to activity lists

**Problem:** The Alpha Advisor's alternative perspective was invisible in activity lists. Users had to click into details to discover that Alpha had reviewed an activity. Other indicators (📢 alerts, 🤔 supervisor challenges) were visible at a glance.

**Decision:** Add 🧠 brain emoji indicator for activities with `alpha_view` field in `dashboard.html` and `symbol_detail.html` activity lists. Follow the existing pattern: conditional check → span with class + title tooltip → emoji.

**Implementation:**
- `web/templates/dashboard.html` (~line 187): Added indicator before activity badge
- `web/templates/symbol_detail.html` (~line 439): Same indicator pattern with `d.` prefix
- `web/static/style.css`: Added `.alpha-indicator` class with blue/purple tint

**Indicator Set Now Complete:**
- 📢 `.alert-indicator` (alerts)
- ⚠️ `.data-error-indicator` (data errors)
- 🤔 `.review-indicator` (supervisor challenges)
- 🧠 `.alpha-indicator` (alpha advisor) ← NEW

**Convention Established:**
New activity indicators should follow the same pattern: conditional check → span with class + title tooltip → emoji. CSS goes in `style.css` near the other indicator classes.

**Risk:** Low. Follows existing patterns, no logic changes, purely presentational.

### Decision: Roll DTE Target and Post-Earnings Window Update (Linus)

**Author:** Linus (Quant Dev)  
**Date:** 2026-07-01  
**Status:** Implemented  
**Scope:** Symmetric application to covered call and cash-secured put roll/assessment instructions

**Decision**

Covered call and cash-secured put roll instructions now target **21-35 DTE as the primary roll window**. The existing DTE ≤ 45 candidate cap remains as the hard outer fallback and should only be used when the 21-35 DTE window cannot provide adequate premium to finance the buyback.

Post-earnings expiration handling now uses **0-7 day hard block** after earnings. Expirations 8-13 days after earnings are a **caution zone** and may be selected only when technicals have stabilized and roll economics are compelling. Expirations at least 8 days after earnings are otherwise acceptable, while pre-earnings expirations at least 3 days before earnings remain preferred.

**Rationale**

The shorter 21-35 DTE primary target accelerates theta capture and tightens risk control while preserving the 45 DTE cap for cases where roll economics require more time. Narrowing the hard post-earnings block to 0-7 days avoids the highest post-earnings chaos/IV-crush period, while the 8-13 day caution zone keeps flexibility for compelling, stabilized setups.

**Files Modified**
- `src/open_call_roll_instructions.py`
- `src/open_put_roll_instructions.py`
- `src/open_call_assessment_instructions.py`
- `src/open_put_assessment_instructions.py`

**Validation** (Linus)
- ✅ Python compile check passed
- ✅ Stale-string grep clean
- ✅ No test assertions affected

---

### Portfolio Chat Symbol Data Toggle

**Date:** 2026-07-15  
**Agent:** Rusty  
**Status:** Implemented

## Decision

Add an optional `include_symbol_data` flag to Portfolio Chat requests. The flag defaults to `false` for backward compatibility; when true, the backend appends a single `=== SYMBOL DATA ===` section with persisted per-symbol enrichment data.

## Rationale

Portfolio Chat already builds context from selected agents and recent activities. Enrichment data can be high-value but larger, so it should be opt-in from the configuration screen rather than always included.

## Contract

- Frontend sends `include_symbol_data` from the Portfolio Chat config checkbox.
- Backend reads only existing `symbol_config.enrichment` from CosmosDB via `cosmos.list_symbols()`; no live yfinance fetches are performed.
- Symbols are collected only if they actually appear in selected agent context, de-duplicated across agents, then sorted alphabetically for one consolidated section.
- Missing or empty enrichment emits `No enrichment data available.` for that symbol.
# Decision: README Modularization

**Date:** 2026-07-15  
**Status:** APPROVED  
**Author:** Danny (Lead)

## Context

The project's README.md had grown to ~1720 lines, mixing high-level overview with detailed implementation documentation. This created several problems:
- New contributors struggled to understand "what this project does" vs "how it works in detail"
- Onboarding took longer — users had to scroll through the entire file to find setup instructions
- Maintenance was difficult — feature updates required editing a single monolithic file
- The README couldn't serve as both a marketing page and a technical reference

## Decision

Split the README into a lightweight index (~135 lines) and a structured `docs/` directory containing 11 topic-specific files. The README now serves as an **index + quick start**, while detailed content lives in dedicated documentation files.

### New Structure

- **README.md** (~135 lines)
  - Title, philosophy, architecture overview (1 paragraph)
  - Documentation table (links to all docs/*.md)
  - Feature highlights (2-3 lines each with links)
  - Quick Start (minimal commands)
  - Acknowledgments

- **docs/** directory (11 files, 1691 lines total)
  - `concepts.md` — Core system concepts (Activity vs Alert, DPS, Supervisor, Alpha Advisor, Position Lifecycle)
  - `architecture.md` — System design, agent pipeline, data flow, pre-fetch architecture, CosmosDB model, project structure
  - `chat.md` — Dual-mode chat experience
  - `screener.md` — DGI Screener, Momentum Analysis, Buy Tracker, Category-Based Strategy Skills
  - `agents.md` — Summarization Agent, Symbol Report, Action Plans
  - `output.md` — Activity & alert documents, example JSON, Telegram notifications
  - `web-dashboard.md` — Dashboard UI, pages, features
  - `local-setup.md` — Local setup, prerequisites, Python venv, Docker
  - `deployment.md` — Azure deployment, CosmosDB provisioning, environment variables
  - `troubleshooting.md` — Common errors and fixes
  - `development.md` — Skills architecture, instruction files, SDK information

### Principles

1. **README as index** — The README is a lightweight landing page with links to detailed docs
2. **VERBATIM content migration** — All content moved without summarization (code blocks, tables, JSON examples, CLI snippets preserved exactly)
3. **Breadcrumbs** — Every `docs/*.md` file has `[← Back to README](../README.md)` under the H1 title
4. **Maintenance discipline** — When features change, update BOTH the relevant `docs/*.md` file AND the corresponding highlight in the README's Features section
5. **Single source of truth** — Detail lives in `docs/`, the README links to it (no duplication)

## Rationale

- **Faster onboarding** — New users can read the README in <2 minutes and understand what the project does
- **Better navigation** — Topic-specific files make it easier to find specific information
- **Easier maintenance** — Changes to a feature only require editing one focused doc file
- **Improved discoverability** — Documentation table acts as a clear table of contents
- **Preserved completeness** — All original content is still accessible, just reorganized

## Alternatives Considered

1. **Keep monolithic README** — Rejected: continued growth would make it increasingly unwieldy
2. **Use wiki or external docs site** — Rejected: docs should live in the repo for version control and contributor accessibility
3. **Summarize content in docs files** — Rejected: verbatim migration preserves all details without information loss

## Implementation

Completed 2026-07-15. All headings from the original README verified present in the new structure. No content lost. Code fences balanced across all files.

## Open Questions

None. Structure is complete and verified.

