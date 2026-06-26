# Squad Decisions

## Active Decisions

### 4. MCP Server Migration to Massive.com (Agent Instructions)
**Date:** 2026-03-26  
**Author:** Linus (Quant Dev)  
**Status:** ✅ Completed  
**Impact:** Team-wide (affects agent instructions and data gathering workflow)

#### Context

Migrated both covered call and cash-secured put agent instructions from the old `iflow-mcp-ferdousbhai-investor-agent` MCP server to the new `mcp_massive` from Massive.com. The old server had specific tool calls like `get_ticker_data()`, `get_price_history()`, `get_cnn_fear_greed_index()`, etc. The new Massive.com MCP server has a fundamentally different architecture with 4 composable tools and built-in analytical functions.

#### Key Design Decisions

**1. Discovery-First Workflow**
- **Decision:** Structure data gathering protocol around `search_endpoints` → `call_api` → `query_data` progression
- **Rationale:** The new MCP server is endpoint-agnostic; agents discover what they need rather than knowing tool names upfront
- **Impact:** Instructions now guide LLM through discovery phase before data collection

**2. In-Memory DataFrames with Meaningful Names**
- **Decision:** Use `store_as` parameter consistently with semantic table names (e.g., "price_history", "options_chain", "financials")
- **Rationale:** Enables SQL JOINs and cross-analysis in later steps
- **Pattern:** Phase 1: Store raw data tables → Phase 2: Store supplementary context → Phase 3: Query and analyze with SQL

**3. Built-in Functions for Greeks & Technicals**
- **Decision:** Leverage `apply` parameter extensively for Black-Scholes Greeks and technical indicators
- **Functions Used:** Greeks: `bs_delta`, `bs_gamma`, `bs_theta`, `bs_vega`, `bs_rho`; Technicals: `sma`, `ema`; Returns: `simple_return`, `cumulative_return`, `sharpe_ratio`
- **Rationale:** Avoid manual calculations; use optimized built-in functions for accuracy and speed

**4. Data Availability Adaptations**
- **Removed:** CNN Fear & Greed Index, Google Trends, Dedicated institutional holders endpoint, Dedicated insider trades endpoint
- **Alternatives:** Fear & Greed → News sentiment analysis; Trends → News volume; Institutional holders → Fundamentals; Insider trades → News parsing
- **Rationale:** Maintain decision quality with available data; apply conservative criteria when key signals missing
---

### 5. CosmosDB Unified Container Migration (Design)
**Date:** 2026-04-01  
**Author:** Danny (Lead)  
**Status:** Proposed  
**Impact:** Data model, ID schema, cosmos_db.py, agent_runner.py, web/app.py, context.py, provisioning

#### Problem Statement

Current state: Activities and alerts live in the same `symbols` container, differentiated by `doc_type = "activity"` vs `doc_type = "alert"`. IDs carry legacy prefixes:
- Activity IDs: `dec_{symbol}_{agent_type}[_{position_id}]_{ts_compact}` (prefix from "decision")
- Alert IDs: `sig_{symbol}_{agent_type}_{ts_compact}` (prefix from "signal")

Goals:
1. Drop `dec_` and `sig_` prefixes — legacy naming artifacts
2. Replace `doc_type` discriminator with `is_alert` boolean
3. Merge into a true unified model — one document type, alerts are activities where `is_alert=true`

#### New Unified Schema

**ID Format:** `{symbol}_{agent_type}[_{position_id}]_{ts_compact}` (prefix-free, deterministic, collision-safe)

Examples:
- `AAPL_covered_call_20260328T14_3000`
- `VZ_open_call_monitor_pos_VZ_call_53.0_20260501_20260331T16_0137`
- `AAPL_cash_secured_put_20260401T09_3000`

**Document Model:** Every agent output is a single document. The `is_alert` boolean replaces the `doc_type` discriminator. The `doc_type` field stays as `"activity"` for all records.

**What Changes:**
| Before | After | Reason |
|--------|-------|--------|
| Two `doc_type` values: `"activity"`, `"alert"` | Single `doc_type`: `"activity"` | Alerts are activities with `is_alert=true` |
| Separate alert documents with `activity_id` reference | No separate alert docs | Alert data merged into activity itself |
| `dec_` prefix on activity IDs | No prefix | Legacy naming removed |
| `sig_` prefix on alert IDs | No separate alert IDs | Alerts are not separate documents |
| `write_alert()` creates a second document | `write_activity()` sets `is_alert=true` inline | One write, not two |

**Query Impact:**
| Query | Before | After |
|-------|--------|-------|
| Get activities for symbol | `WHERE doc_type='activity'` | `WHERE doc_type='activity'` (unchanged) |
| Get alerts for symbol | `WHERE doc_type='alert'` | `WHERE doc_type='activity' AND is_alert=true` |
| Get all alerts (dashboard) | `WHERE doc_type='alert'` | `WHERE doc_type='activity' AND is_alert=true` |

#### Migration Strategy

**Approach:** Offline batch migration (low traffic, no SLA, < 5 min window)

**Data Transformation Rules:**
1. Activity documents: strip `dec_` prefix
2. Alert documents: merge into parent activity (set `is_alert=true`), delete original alert doc
3. Orphaned alerts: convert to standalone activity, strip `sig_` prefix, set `is_alert=true`

**Pre-Migration Validation:**
- Count activities and alerts per symbol
- Verify every alert has valid `activity_id`
- Log orphaned alerts

**Post-Migration Validation:**
- Count activities matches expected
- Count `is_alert=true` activities matches expected
- No `doc_type='alert'` documents remain
- No IDs start with `dec_` or `sig_`
- Spot-check 3 random alerts for correctness

#### Code Changes Required

**`src/cosmos_db.py`:**
- `write_activity()`: Strip `dec_` prefix from ID
- `write_alert()` → `mark_as_alert()`: Update existing activity in-place instead of creating new doc
- Query methods: Update `doc_type='alert'` filters to `is_alert=true`

**`src/agent_runner.py`:**
- Remove `_build_alert_data()` and `_build_roll_alert_data()`
- Change `write_alert()` calls → `mark_as_alert()`
- Alert fields included in activity payload before write

**`web/app.py`:**
- Update alert endpoints: `doc_type='alert'` → `is_alert=true`
- Remove `activity_id` display/linkage

**`scripts/provision_cosmosdb.sh`:**
- Add composite index: `(doc_type ASC, is_alert ASC, timestamp DESC)`

#### Rollback Plan

**Pre-Migration Backup:**
```bash
python scripts/migrate_unified_schema.py --export-backup backup_20260401.json
```

**Rollback Procedure:**
1. Stop app
2. Delete new documents from symbols container
3. Restore: `python scripts/migrate_unified_schema.py --restore backup_20260401.json`
4. Revert code changes
5. Restart app

Keep backup for 7 days post-migration.

#### Execution Plan

1. Write migration script (--dry-run, --export-backup, --restore)
2. Code changes to cosmos_db.py, agent_runner.py, web/app.py
3. Update provisioning script indexing policy
4. Test locally with dry-run against production data
5. Export backup
6. Stop app → run migration → validate → restart
7. Smoke test (trigger one agent run, verify new ID format)
8. Delete backup after 7 days

**Estimated effort:** 2-3 hours implementation + testing.

#### Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| Orphaned alerts (no parent activity) | Low | Low | Script handles gracefully — converts to standalone activity |
| ID collision during migration | Very Low | Medium | Timestamp-based IDs are inherently unique per agent/symbol |
| Query regression (dashboard/API) | Medium | High | Update all queries in cosmos_db.py; test each endpoint |
| Backup file corruption | Low | High | Verify backup integrity before starting destructive phase |
| CosmosDB rate limiting during batch ops | Low | Low | Script uses sequential writes with retry; 50-100 docs total |

#### Decision

**Recommendation:** Proceed with this migration. The unified model simplifies the codebase (one write path instead of two), eliminates stale references, and cleans up legacy naming. Risk is low given small data volume and straightforward transformation rules.

**Requires:** User approval to schedule downtime window (2-5 min) and execute.

---

### 6. Unified Schema Implementation (Code)
**Date:** 2026-04-01  
**Author:** Rusty (Backend)  
**Status:** Implementation Complete, Awaiting Migration  
**Related:** CosmosDB Unified Container Migration (Design)

#### Summary

Implemented the unified schema changes in `src/cosmos_db.py` per Danny's migration design. Alerts are now activities with `is_alert=true` rather than separate documents. New ID format drops legacy prefixes.

#### Implementation Decisions

**1. Query Filter Pattern**

**Decision:** Use `(c.is_alert = false OR NOT IS_DEFINED(c.is_alert))` for non-alert activity queries.

**Rationale:**
- Handles legacy documents that don't have `is_alert` field
- After migration completes, all docs will have the field explicitly
- More robust than `c.is_alert = false` alone during transition

**Applied to:**
- `get_recent_activities()`
- `get_all_activities()`
- `get_recent_activities_by_symbol()`

**2. Backwards Compatibility Strategy**

**Decision:** Keep deprecated `write_alert()` method with clear deprecation notice and TODO comments.

**Rationale:**
- Migration script runs separately from code deployment
- During transition window, old alert documents may still exist
- Cascade delete methods need to clean up old alert docs
- Clear deprecation notices guide future cleanup

**Cleanup checklist (post-migration):**
- Remove `write_alert()` method entirely
- Remove cascade delete logic for `doc_type='alert'` documents
- Remove TODO comments

**3. New Method: `mark_as_alert()`**

**Signature:**
```python
def mark_as_alert(self, symbol: str, activity_id: str, alert_data: dict) -> dict
```

**Design:**
- Reads existing activity document
- Sets `is_alert = true`
- Merges alert-enrichment fields (currently just `confidence`)
- Returns updated document

**Why not inline in `write_activity()`?**
- Alert determination happens after activity write (in agent_runner.py)
- Keeps write_activity() focused on single responsibility
- Allows agents to decide post-hoc whether activity qualifies as alert

**4. Web Layer Changes**

**Decision:** No changes needed in `web/app.py`.

**Rationale:**
- Web layer already uses cosmos_db.py abstraction methods
- No direct SQL queries in web endpoints
- All filtering logic contained in data access layer
- Query method updates automatically propagate to web layer

#### Testing Notes

**Pre-migration:**
- Both old and new query patterns work
- New writes use prefix-free IDs
- Old `write_alert()` still functional

**Post-migration:**
- Remove backwards compatibility code
- All queries use `is_alert` discriminator
- No `doc_type='alert'` documents remain

#### Related Work

**Blocked on:**
- Danny's migration script execution

**Enables:**
- Simpler codebase (one write path instead of two)
- No more stale `activity_id` references
- Cleaner ID format without legacy naming

---

### 7. Agent Signal Refactor for Unified Schema
**Date:** 2026-04-01  
**Author:** Linus (Quant Dev)  
**Status:** Implemented  
**Depends on:** Danny's CosmosDB unified schema migration

#### Problem

Current agent_runner.py writes alerts in two steps:
1. `write_activity()` — core activity document
2. `write_alert()` — separate alert document with `activity_id` reference

Danny's migration eliminates separate alert documents. Alerts become activities with `is_alert=true` and enrichment fields (confidence, risk_flags) merged directly into the activity payload.

**Required change:** Agent runner must write ONE document per agent run, with alert-specific fields included when `is_alert=true`.

#### Solution

**Key Changes:**

1. **Removed methods:**
   - `_build_alert_data()` — no longer needed; alert data IS activity data
   - `_build_roll_alert_data()` — same reason
   - `_ALERT_FIELDS` and `_ROLL_ALERT_FIELDS` — field control moved to cosmos layer

2. **Added method:**
   - `_extract_alert_enrichment(json_data)` — extracts alert-only fields (confidence, risk_flags) from agent JSON response

3. **Updated write paths (2 locations):**
   
   **Path 1: Covered call / cash-secured put agents (line ~340)**
   ```python
   # Before:
   cosmos.write_activity(...)
   if is_alert:
       cosmos.write_alert(...)
   
   # After:
   if is_alert:
       activity_payload.update(self._extract_alert_enrichment(json_data))
   cosmos.write_activity(...)  # Single write with alert fields included
   ```
   
   **Path 2: Position monitor agents (line ~580)**
   Same pattern — merge alert enrichment into activity payload before writing.

4. **Telegram notification:**
   - Still builds display data inline from `json_data` (no DB query needed)
   - No dependency on separate alert documents

#### Design Rationale

**Why merge alert fields into activity payload?**

Danny's unified schema stores alerts as `doc_type="activity"` with `is_alert=true`. There are no separate alert documents. Therefore:
- Agent runner must include alert-enrichment fields (confidence, risk_flags) in the activity payload when the activity IS an alert
- This happens BEFORE the write_activity call, not after

**Why keep Telegram data construction?**

Telegram notification happens immediately after the agent run. Building the display data from the agent's JSON response avoids:
- An extra DB read to fetch the just-written activity
- Dependency on DB write completion timing
- Coupling to the DB schema (Telegram only needs display fields)

**Why remove _ALERT_FIELDS and _ROLL_ALERT_FIELDS?**

These were used to filter which fields go into the alert document. With no separate alert doc:
- The activity payload already contains all relevant fields from the agent's JSON response
- Field filtering for storage happens in cosmos_db.py (write_activity), not in agent_runner
- Removing these lists simplifies agent_runner and centralizes schema knowledge

#### Testing Strategy

**Blockers:** Requires Danny's cosmos_db.py changes:
- `write_activity()` ID format change (remove `dec_` prefix)
- `write_alert()` method removed or deprecated
- `mark_as_alert()` method added (if separate marking is needed post-write)

**Test plan after cosmos_db.py is updated:**
1. Run covered_call agent on test symbol → verify activity written with `is_alert=true` and confidence/risk_flags included
2. Run open_call_monitor agent → same verification for roll alerts
3. Check Telegram notification still fires correctly
4. Query alerts in web UI → verify `is_alert=true` filter works

#### Team Coordination

**Dependencies:**
- **Danny:** Must complete cosmos_db.py changes first (ID format, write_activity schema, remove write_alert)
- **Rusty:** Must update web/app.py alert queries (`doc_type='alert'` → `is_alert=true`)

**Deployment order:**
1. Danny: Run migration script, update cosmos_db.py
2. Linus: agent_runner.py (this change) — merges after Danny's PR
3. Rusty: web/app.py query updates — can merge alongside Linus or after

**Rollback:** If migration fails, revert to previous code + restore DB backup (Danny's rollback plan).

---

### 8. Migration Script Testing Strategy
**Date:** 2026-04-01  
**Author:** Basher (Tester)  
**Status:** Implemented  
**Related:** CosmosDB Unified Container Migration (Design)

#### Decision

The migration script `scripts/migrate_cosmos_events.py` implements defensive testing practices:

**1. Dry-Run First Philosophy**
- `--dry-run` flag executes phases 1-2 (export + transform) without any database writes
- Outputs transformation summary showing exactly what would change
- User can review orphaned alerts, ID collisions, and merge counts before committing
- **Recommendation:** ALWAYS run dry-run first, review output, then run actual migration

**2. Backup-Before-Change**
- Phase 1 creates timestamped backup JSON in `backups/` directory before any mutations
- Backup includes both activities and alerts with integrity validation (count checks)
- Backup file path logged at end of migration for rollback reference
- **Recommendation:** Keep backups for 7 days after migration

**3. Restore Capability**
- `--restore BACKUP_FILE` flag provides rollback mechanism
- Requires explicit 'YES' confirmation to prevent accidental data loss
- Deletes current data and restores from backup atomically
- Validates backup file exists before starting delete operations

**4. Progressive Validation**
- Backup integrity check: count verification after write
- Post-migration validation (Phase 4):
  - Activity count matches expected
  - Alert count matches merged + orphaned
  - No doc_type='alert' documents remain
  - No dec_/sig_ prefixed IDs remain
  - Spot-check 3 random merged records for correctness
- Clear error messages with rollback instructions on failure

**5. Edge Case Handling**
- **Orphaned alerts** (activity_id missing): Convert to standalone activity, strip sig_ prefix, log warning
- **Duplicate timestamps**: Append _2, _3 sequence numbers, log collision
- **Already migrated docs**: Skip if ID exists (idempotent), log warning
- **Missing fields**: Handle gracefully (e.g., missing symbol → log warning, skip delete)

**6. Observability**
- Structured logging with clear phase markers
- Progress indicators for batch operations (every 10 docs)
- Summary reports at transformation and completion
- Error messages include document IDs and partition keys for debugging

#### Testing Checklist (Pre-Production)

Before running migration on production data:

1. ✓ Run `--dry-run` against production database
2. ✓ Review transformation summary for unexpected orphaned alerts
3. ✓ Check for ID collisions (should be zero unless duplicate timestamps exist)
4. ✓ Verify backup file integrity (count matches query results)
5. ✓ Test `--restore` on backup file in non-production environment
6. ✓ Confirm all validation checks pass in Phase 4
7. ✓ Schedule downtime window (2-5 min)
8. ✓ Stop app → run migration → validate → restart app
9. ✓ Smoke test (trigger one agent run, verify new ID format)

#### Risk Mitigation

| Risk | Mitigation |
|------|------------|
| Backup corruption | Integrity check validates count match after write |
| Migration fails mid-phase | Clear error messages with rollback command in logs |
| Orphaned alerts | Convert to standalone activities with warning logs |
| ID collisions | Append sequence number, log collision |
| CosmosDB rate limits | Sequential writes with retry (50-100 docs total, low volume) |
| Wrong environment | Script reads COSMOS_ENDPOINT from env, no hardcoded URLs |

#### Lessons Learned

**Defensive Coding Patterns Applied:**
1. **Validate inputs early:** Check env vars before any operations
2. **Fail fast:** Raise MigrationError with clear message on any validation failure
3. **Dry-run everything:** No-op mode for all destructive operations
4. **Log everything:** Info-level logs for all major operations, debug for details
5. **Confirm destructive actions:** Require 'YES' input for restore (deletes current data)

**Why No Test Suite:**
- Migration is a one-time operation (not production code)
- Dry-run serves as live validation against actual data
- Test suite would require CosmosDB emulator setup (overkill for one-off script)
- Manual testing checklist more appropriate for operational scripts

**Script Design Trade-offs:**
- **Sequential writes over batch:** Simpler error handling, clear progress logging, volume is low (50-100 docs)
- **In-memory transformation:** Entire dataset fits in memory, simpler than streaming
- **No undo for Phase 3:** Backup + restore is safer than complex undo logic

---


**5. Earnings Calendar Strategy**
- **Challenge:** No dedicated earnings calendar endpoint in Massive.com
- **Solution:** Multi-source: Check ticker_info for next earnings date field, parse news headlines for "earnings" mentions
- **Impact:** Instructions emphasize importance of earnings timing but acknowledge data may require manual validation

**6. Phased Data Gathering Structure**
- **Decision:** Maintain 3-phase structure (Core Data → Context → Analytics) with enhanced SQL capabilities
- **Rationale:** Logical progression mirrors decision-making process
- **Enhancement:** Phase 3 now includes explicit SQL examples for JOINs and `apply` functions

**7. Conservative Stance When Data Missing**
- **Decision:** Apply stricter criteria when key data unavailable (lower delta, higher margin of safety)
- **Examples:** If insider data unavailable → require stronger fundamentals; If Fear & Greed unavailable → focus on IV Rank; If earnings unclear → default to WAIT unless >60 days buffer
- **Rationale:** Incomplete information = higher risk; compensation required

#### Technical Implementation

**Covered Call Instructions Changes:**
- Phase 1: 4 steps (ticker details, price history with technicals, options chain, dividends)
- Phase 2: 5 steps (fundamentals, analyst ratings, news, sentiment proxy via news, retail interest via news volume)
- Phase 3: 3 steps (IV analysis, Greeks calculations, return metrics)
- Total: 12 data-gathering steps + 1 consolidation (granular and composable)

**Cash-Secured Put Instructions Changes:**
- Phase 1: 5 steps (ticker details, extended price history, dual financials, options chain, dividends)
- Phase 2: 6 steps (analyst ratings, news, earnings history via news, market movers, fear proxy, retail proxy)
- Phase 3: 6 steps (support via SQL, oversold conditions, Greeks, IV analysis, premium calculations, insider parsing)
- Total: 17 data-gathering steps + 1 consolidation (comprehensive analysis)

**SQL Examples Added:**
- Support identification: `SELECT MIN(low) FROM price_history` (CSP)
- Strike filtering: `SELECT * FROM options_chain WHERE delta BETWEEN 0.20 AND 0.35` (CC)
- Sentiment proxy: `SELECT sentiment FROM news GROUP BY sentiment`
- Greeks calculation: `SELECT ... apply=["bs_delta", "bs_theta"]`

#### Trade-offs

**Pros:**
1. More flexible: Discovery-based approach adapts to API changes
2. More powerful: SQL + built-in functions enable complex analysis
3. Better data integration: In-memory tables allow JOINs and cross-analysis
4. Composable: 4 simple tools combine for unlimited use cases

**Cons:**
1. More complex: Requires LLM to understand SQL and compose multi-step queries
2. More steps: 12-17 steps vs. 8-11 single tool calls (though more granular control)
3. Data gaps: Missing some signals (Fear & Greed, Google Trends, Insider Trades)
4. Discovery overhead: Each run requires `search_endpoints` calls

**Mitigations:**
- Provide extensive examples in instructions
- Document fallback strategies for missing data
- Emphasize semantic table naming for easier SQL composition
- Include explicit SQL templates for common queries

#### Success Criteria

- ✅ Instructions compile without syntax errors
- ✅ All available data gathering steps documented
- ✅ SQL examples tested for correctness
- ✅ Fallback strategies defined for missing data
- ⏳ Agent successfully gathers all available data
- ⏳ Agent makes same quality decisions as with old MCP server
- ⏳ No degradation in signal accuracy or timing

---

### 5. Multi-Provider MCP Configuration with Provider Switching
**Date:** 2026-07-25  
**Decider:** Rusty (Agent Dev)  
**Status:** ✅ Completed  
**Impact:** Team-wide (enables flexible provider selection without code changes)

#### Context

The project initially deployed with `mcp_massive`, then added Alpha Vantage as alternative. Rather than maintaining two separate codebases, we needed a single config-driven approach to switch providers at runtime without code changes.

#### Decision

Implemented provider-based MCP configuration structure:
```yaml
mcp:
  provider: "massive"  # or "alphavantage"
  massive:
    command: "mcp_massive"
    env_key: "MASSIVE_API_KEY"
  alphavantage:
    command: "mcp_alphavantage"
    env_key: "ALPHAVANTAGE_API_KEY"
```

#### Key Design Decisions

1. **Prune inactive providers before env var substitution**
   - Removes non-active provider config sections before resolving environment variables
   - Prevents crash when user only sets API key for selected provider
   - Rationale: User shouldn't need to set all provider keys, only the active one

2. **Lazy instruction imports in agent files**
   - Instruction imports happen inside `async def run()` method, not at module level
   - Conditional logic selects instructions based on `config.mcp_provider`
   - Rationale: AV instruction files don't need to exist for Massive mode

3. **Dynamic MCP tool naming and env key**
   - `AgentRunner` takes `mcp_name` and `env_key` as constructor parameters
   - No more hardcoded "massive" or "MASSIVE_API_KEY"
   - Rationale: Single runner implementation serves all providers

#### Implementation

**Files Updated:**
1. `config.yaml` — Provider selector + per-provider sections
2. `src/config.py` — `mcp_provider`, `mcp_env_key` properties; `_prune_inactive_providers()`
3. `src/agent_runner.py` — Dynamic `mcp_name` and `env_key` parameters
4. `src/covered_call_agent.py` — Lazy provider-specific instruction import
5. `src/cash_secured_put_agent.py` — Lazy provider-specific instruction import
6. `src/main.py` — Pass provider settings to AgentRunner

#### Trade-offs

| Aspect | Pro | Con |
|--------|-----|-----|
| Single config file | Easy to switch providers | Can't use multiple providers in one run |
| Lazy imports | AV files optional for Massive mode | Slightly more complex agent logic |
| Prune before substitute | No required env vars for inactive providers | Inactive config discarded at load time |

#### Consequences

**Positive:**
- Users can select provider in config without code changes
- Supports future providers without architectural changes
- Instruction sets can evolve independently per provider

**Neutral:**
- Requires one env var per active provider (similar to before)
- Runtime cost of lazy imports negligible

#### Verification

- ✅ Config loads correctly with provider selector
- ✅ Pruning removes inactive sections before env var resolution
- ✅ Lazy imports only trigger on provider match
- ✅ AgentRunner accepts dynamic names and env keys
- ✅ Old config format detected with helpful error message

---

### 6. Alpha Vantage MCP Instruction Files (Strategy Logic Parity)
**Date:** 2026-07-25  
**Author:** Linus (Quant Dev)  
**Status:** ✅ Completed  
**Files:** `src/av_covered_call_instructions.py` (420 lines), `src/av_cash_secured_put_instructions.py` (569 lines)  
**Impact:** Team-wide (enables trading with Alpha Vantage data source)

#### Context

The project established comprehensive trading instructions for Massive.com MCP server. When Alpha Vantage was selected as alternative provider, we needed parallel instructions that:
- Keep all strategy logic and decision criteria identical
- Only adapt the data gathering protocol to AV's 3-meta-tool architecture (TOOL_LIST → TOOL_GET → TOOL_CALL)
- Leverage AV's unique advantages (built-in technicals, earnings data, sentiment scores)

#### Decision

Created parallel instruction files maintaining 100% strategy parity while optimizing data gathering for AV's tool interface.

#### Key Design Decisions

1. **Preserve all decision criteria identically**
   - Same SELL thresholds (IV Rank, delta ranges, DTE windows)
   - Same strike selection rules (CC: above support, CSP: at/below support)
   - Same output format for signal parsing
   - Rationale: Trading logic should not vary by data source

2. **Phase 1/2/3 structure preserved**
   - Covered Call: 3 phases (core data → context → analytics)
   - Cash-Secured Put: 3 phases (extended core → comprehensive context → analytics)
   - Rationale: Consistent naming makes provider swapping intuitive

3. **Leverage AV advantages for efficiency**
   - **Built-in technicals:** RSI, Bollinger Bands, MACD, SMA, EMA (vs. Massive's manual calculation)
   - **Earnings calendar:** Dedicated EARNINGS tool with beat/miss (vs. Massive's news parsing)
   - **Sentiment scores:** Numerical NEWS_SENTIMENT (vs. Massive's text analysis)
   - **Analyst ratings:** Direct COMPANY_OVERVIEW field (vs. Massive's fundamentals search)
   - Rationale: Use native capabilities for clarity and accuracy

4. **Manual adaptation for missing capabilities**
   - **Greeks:** No built-in Black-Scholes; instructions provide estimation guidance
   - **Joins:** No SQL; agent must synthesize across JSON objects
   - **Insider data:** No dedicated endpoint; instructions guide keyword search in news
   - Rationale: Incomplete data requires conservative criteria, not failure

#### Technical Implementation

**Covered Call Instructions (420 lines):**
```
ROLE + STRATEGY OVERVIEW
  ↓
ANALYSIS FRAMEWORK (Greeks, DTE, earnings)
  ↓
DATA GATHERING (TOOL_LIST → TOOL_GET → TOOL_CALL progression)
  Phase 1: Ticker, price history, options chain, dividends
  Phase 2: Fundamentals, analyst ratings, news/sentiment, technicals
  Phase 3: IV analysis, Greeks estimation, return calcs
  ↓
DECISION CRITERIA + OUTPUT
```

**Cash-Secured Put Instructions (569 lines):**
```
ROLE + STRATEGY OVERVIEW
  ↓
ANALYSIS FRAMEWORK (quality gate, DTE, earnings, technicals)
  ↓
DATA GATHERING (TOOL_LIST → TOOL_GET → TOOL_CALL progression)
  Phase 1: Extended core (price for support ID, dual financials, earnings history)
  Phase 2: Comprehensive (analyst, news, sentiment scores, fundamental quality)
  Phase 3: Strike selection (support via JSON scan, oversold via BBANDS/RSI, Greeks estimation)
  ↓
DECISION CRITERIA + OUTPUT
```

#### Trade-offs

| Aspect | Massive.com | Alpha Vantage |
|--------|-------------|---------------|
| Tool discovery | `search_endpoints` keyword search | `TOOL_LIST` + `TOOL_GET` discovery |
| Data aggregation | SQL JOINs across stored tables | Manual JSON synthesis |
| Technical indicators | Manual via `apply=["sma"]` | Built-in RSI, BBANDS, MACD, EMA |
| Greeks calculation | `apply=["bs_delta", "bs_theta"]` | Manual estimation guidance |
| Earnings data | Parse from news | Direct EARNINGS tool |
| Sentiment | Text-based analysis | Numerical NEWS_SENTIMENT scores |
| Institutional holders | Fundamentals or search | COMPANY_OVERVIEW consensus |

**Advantages AV:**
- Simpler tool interface (no SQL needed)
- More reliable earnings data
- Numerical sentiment is faster to analyze
- Built-in technicals reduce LLM hallucination

**Advantages Massive:**
- SQL composability for complex analysis
- Black-Scholes Greeks built-in
- More granular data control

#### Consequences

**Positive:**
- Single strategy logic supports both providers
- Provider swapping is config change only
- AV's built-in capabilities often provide faster/more accurate analysis
- Instruction maintenance: bug fixes apply to both via common sections

**Neutral:**
- AV requires more manual Greeks estimation (acceptable given other advantages)
- More instruction files to maintain (offset by exact copying of common sections)

**Mitigations:**
- Common sections (ROLE, STRATEGY, CRITERIA) identical between versions
- Extensive examples in DATA GATHERING for AV's tool discovery pattern
- Conservative criteria documented for missing signals

#### Verification

- ✅ Both files valid Python (import test passed)
- ✅ ROLE + STRATEGY OVERVIEW: exact match across versions
- ✅ ANALYSIS FRAMEWORK through DECISION CRITERIA: exact match
- ✅ Only DATA GATHERING PROTOCOL differs (intentional, AV-specific)
- ✅ All tool names verified against AV documentation
- ✅ Phase structure mirrors Massive version

#### Coordination

**Depends on:** Rusty's lazy import pattern (selection happens in agent files)  
**Enables:** Agent provider swapping via `config.yaml` change only  
**Documentation:** Common decision rationale in decisions.md; provider-specific details in each instruction file

#### Next Steps

1. **Integration testing:** Verify AV TOOL_LIST discovery works with actual API
2. **Signal quality comparison:** Compare decision logic output vs. Massive
3. **Provider migration:** Document process for users switching providers

---

## Decision: Alpha Vantage Remote MCP Transport

**Date:** 2026-07-25  
**Author:** Rusty  
**Status:** Implemented

### Context
Alpha Vantage now provides a hosted MCP server at `mcp.alphavantage.co` using SSE/streamable HTTP transport. This eliminates the need for a local `uvx marketdata-mcp-server` subprocess.

### Decision
Replaced the local stdio-based Alpha Vantage MCP integration with the remote streamable HTTP endpoint. Added a `transport` field to config to distinguish between stdio (Massive.com) and streamable_http (Alpha Vantage) providers.

### Key Design Choices
1. **Backward compatible** — `transport` defaults to `"stdio"` so Massive.com config needs no changes
2. **Validation split** — stdio providers require `command`+`args`, HTTP providers require `url`
3. **Config-level env substitution preserved** — API key is embedded in the URL via `${ALPHAVANTAGE_API_KEY}` pattern, same env var expansion as before
4. **API key env check still runs** — even though the key is in the URL, we validate the env var exists at runtime to give a clear error message

### Impact
- No local `uvx`/`marketdata-mcp-server` install needed for Alpha Vantage users
- Massive.com workflow unchanged
- `MCPStreamableHTTPTool` from `agent_framework` handles the HTTP transport

---

## Governance

- All meaningful changes require team consensus
- Document architectural decisions here
- Keep history focused on work, decisions focused on direction

---

## Decision: TradingView Provider Plumbing + EXCHANGE-SYMBOL Format

**Date:** 2026-03-26  
**Author:** Rusty (Agent Dev)  
**Status:** Implemented

### Context
Danny requested adding TradingView as a 4th MCP provider and changing the symbol file format from plain tickers (e.g., `AAPL`) to `EXCHANGE-SYMBOL` (e.g., `NASDAQ-AAPL`).

### Decision

#### TradingView Provider
- Uses `mcp-server-fetch` via `uvx` — a generic web-fetch MCP tool, not a finance-specific one.
- No API key required (unlike Massive or AlphaVantage).
- The agent instructions (Linus's domain) will direct the LLM to fetch specific TradingView URLs for analysis.

#### EXCHANGE-SYMBOL Parsing
- Parsing uses `symbol.split('-', 1)` to extract exchange and ticker.
- Backward-compatible: symbols without a dash still work (exchange = "", ticker = full string).
- Decision logs and matching now use the ticker portion only, keeping output clean.

### Alternatives Considered
- Could have used a dedicated TradingView MCP server — none exists as a mature package. The generic fetch server is the right abstraction since Linus's instructions control what URLs are fetched.
- Could have used a tuple/dict format for symbols — plain text `EXCHANGE-SYMBOL` is simpler to maintain and edit by hand.

### Impact
- **Linus must create**: `tv_covered_call_instructions.py` and `tv_cash_secured_put_instructions.py` before the tradingview provider can be activated.
- Existing providers (massive, alphavantage, yahoo) are unaffected.
- Symbol files changed — any external tooling reading these files needs to handle the new format.

---

## Decision: TradingView Instruction File Design

**Date:** 2026-03-26  
**Author:** Linus (Quant Dev)  
**Status:** Implemented  
**Files:** `src/tv_covered_call_instructions.py`, `src/tv_cash_secured_put_instructions.py`

### Context
Added TradingView as a third data provider option (alongside Yahoo Finance and Alpha Vantage). TradingView uses the Fetch MCP server (`mcp-server-fetch`) with a single `fetch` tool to retrieve TradingView web pages as markdown.

### Key Decisions

#### 1. Pre-analyzed signals paradigm
TradingView provides Buy/Sell/Neutral signals already computed for oscillators and MAs. Instructions tell the agent to work from these analyzed signals rather than calculating indicators from raw data. This is a fundamental difference from YF/AV instructions.

#### 2. Pivot points as primary support/resistance
Instead of scanning historical price data for support/resistance (which TradingView fetch doesn't provide as OHLCV), instructions use Classic pivot points S1-S3 (support) and R1-R3 (resistance) for strike selection.

#### 3. IV proxy strategy
Since TradingView's options chain is JS-rendered and may not return IV data via fetch, instructions define beta + volatility % from the main page as IV proxy. High beta + high volatility % = likely elevated IV.

#### 4. Graceful options chain degradation
Instructions include explicit fallback protocol when options chain data is empty: use technical signals for direction, pivot points for strike levels, beta/volatility for IV proxy.

### Impact on Team
- **Rusty**: Will need to add TradingView as a provider option in config.yaml and implement lazy imports for `TV_COVERED_CALL_INSTRUCTIONS` / `TV_CASH_SECURED_PUT_INSTRUCTIONS` in agent files (same pattern as AV).
- **Config**: New provider name `"tradingview"` with MCP tool `"mcp-server-fetch"`.
- **No breaking changes**: Existing YF and AV instruction files are untouched.

### Trade-offs

| Pro | Con |
|-----|-----|
| FREE — no API key | Options chain likely incomplete |
| Pre-calculated technicals | No explicit IV, no Greeks |
| Pivot points built-in | No historical OHLCV data |
| Single-page fundamentals | No balance sheet / cash flow details |
| Fewest fetch calls (4 URLs) | No news feed / sentiment scores |

---

## Decision: Structured JSON Output Format for Decisions

**Date:** 2026-03-27  
**Author:** Rusty (Agent Dev)  
**Status:** Implemented  
**Impact:** Team-wide (changes agent output parsing, logging, and instruction format)

### Context

Replaced the pipe-delimited human-readable output format with a machine-parseable JSON schema + SUMMARY line across all 8 instruction files and the agent runner infrastructure.

### Decision

1. **JSON decision block**: Agents output a fenced ```json block with a standardized schema containing all decision fields (symbol, decision, strike, expiration, IV metrics, premium, confidence, risk_flags, etc.)
2. **SUMMARY line**: A one-line human-readable summary immediately after the JSON block
3. **Dual logging**: JSON → `.jsonl` files, SUMMARY → existing `.log` files
4. **Backward compatibility**: agent_runner tries JSON first, falls back to legacy pipe format

### Schema Definition

**Covered Call Decision Block:**
```json
{
  "agent": "covered_call",
  "symbol": "AAPL",
  "decision": "SELL",
  "strike": 175,
  "expiration": "2026-04-17",
  "dte": 21,
  "iv_rank": 72,
  "premium_percent": 2.3,
  "confidence": 0.85,
  "risk_flags": ["near_earnings"],
  "reason": "Strong IV, premium >2%, clean technicals"
}
```

**Cash-Secured Put Decision Block:**
```json
{
  "agent": "cash_secured_put",
  "symbol": "MSFT",
  "decision": "SELL",
  "strike": 410,
  "expiration": "2026-04-17",
  "support_level": 408,
  "dte": 21,
  "iv_rank": 68,
  "premium_percent": 2.8,
  "confidence": 0.90,
  "risk_flags": [],
  "reason": "Support identified at $408, premium strong"
}
```

### Schema Differences

- Covered call: `"agent": "covered_call"` — standard fields
- Cash-secured put: `"agent": "cash_secured_put"` — adds `"support_level"` field

### Trade-offs

- **Pro**: Machine-parseable output enables downstream automation, dashboards, analytics
- **Pro**: SUMMARY line preserves human readability
- **Pro**: `.jsonl` format enables easy batch processing (one JSON per line)
- **Con**: Larger instruction text (~2KB more per file) due to JSON examples
- **Con**: Agent may occasionally produce malformed JSON (fallback handles this)

### Implications for Team

- **Linus**: Instruction files now specify JSON output format — any new instruction files must follow the same schema
- **Basher**: Test cases should verify JSON extraction from agent responses
- **Danny**: Downstream systems can now consume `.jsonl` files for structured decision data
- **Scribe**: README may need updating to document the new output format

---

## User Directive: Model Configuration Change

**Date:** 2026-03-27T09:18:56Z  
**By:** dsanchor (via Copilot)  
**Status:** Implemented in config/team.md

### Context
Updated model configuration from gpt-5.4-mini to gpt-5.1 based on performance observations with TradingView Playwright multi-step tool-calling workflows.

### Directive

**Switch model from gpt-5.4-mini to gpt-5.1**

- **Reason:** gpt-5.1 shows superior performance on multi-step browser instruction sequences (navigate → click → snapshot for options chain data extraction from TradingView)
- **Previous model performance:** gpt-5.4-mini unable to follow complex sequential browser commands reliably
- **gpt-5.1 advantages:** Better instruction following on step-by-step workflows

### Impact

- Applies to all agent instruction files using TradingView provider
- Updated in `config/team.md` model field
- Existing Massive.com and Alpha Vantage workflows unaffected
- Configuration propagates to all agents via team config inheritance

---

## 2. TradingView Navigation Optimization: Remove Main Symbol Page

**Date:** 2026-03-27T09:38:00Z  
**Author:** Rusty (Agent Dev)  
**Status:** Implemented  
**Impact:** Team-wide (improves TradingView agent data gathering)

### Context

TradingView Playwright agent was experiencing context window overflow, preventing access to technicals and forecast pages. Root cause analysis showed 4 pages producing 245K total characters:
- Main symbol page: 103K chars ← Problem
- Technicals: 48K chars
- Forecast: 29K chars
- Options chain (expanded): 65K chars

After loading main (103K) + options chain (65K) = 168K, insufficient context remained for technicals and forecast.

### Decision

Remove main symbol page entirely from navigation. Load only 3 pages in optimized order:
1. **Technicals** (48K) — most valuable for technical analysis
2. **Forecast** (29K) — earnings dates, analyst consensus, price targets
3. **Options chain** (65K) — strikes, premiums, IV, Greeks

### Trade-offs

**Lost data (from main page):**
- P/E ratio, EPS, revenue, market cap, beta
- Company description, sector classification
- CSP fundamental quality gate loses detailed financials

**Preserved/Replaced:**
- Current price → Visible in options chain headers and forecast page
- Earnings date → Available on forecast page
- Analyst price targets → Available on forecast page
- Beta/volatility proxy → Replaced with actual IV% from options chain (superior)
- CSP Investment Worthiness Gate → Rewritten to use analyst consensus + earnings history

### Implementation

**Files Changed:**
- `src/tv_covered_call_instructions.py` — Updated navigation, removed main page
- `src/tv_cash_secured_put_instructions.py` — Updated navigation, CSP gate rewrite

**CSP Gate Logic Update:**
```
OLD: if P/E < 30 and EPS_positive and market_cap > 1B → PROCEED
NEW: if analyst_consensus >= 60% (Buy/Hold) and no_surprise_losses_2qtrs → PROCEED
```

Data sources: Analyst consensus and earnings history now sourced from forecast page.

### Quality Assurance

- ✅ Context freed: 245K → 142K (98K reduction)
- ✅ All 3 critical pages now load without overflow
- ✅ CSP gate still prevents assignment to deteriorating stocks
- ✅ No changes to decision logic or Greeks selection
- ✅ Backward compatible (stronger, not weaker)

### Team Implications

- **Linus (Quant Dev):** CSP gate now depends on analyst consensus; adjust backtests referencing P/E
- **Danny (Product):** TradingView instructions now capture analyst targets and earnings dates
- **Basher (Test/Ops):** Verify TV mocks include forecast page earnings history
- **Scribe (Docs):** Update TV data gathering docs in README

---

### 12. User Directive: JSONL-Only Decision/Signal Output

**Date:** 2026-03-27  
**Author:** dsanchor (via Copilot)  
**Status:** Proposed  
**Impact:** Output format simplification

#### Decision

Drop `.log` decision/signal files entirely. Keep only `.jsonl` output for decisions and signals. Update `config.yaml` paths accordingly.

#### Rationale

Single machine-parseable format reduces file management complexity. JSONL is easier to parse and aggregate than multiple file types.

---

### 13. Open Position Monitor Agents

**Date:** 2025-07  
**Author:** Rusty (Agent Dev)  
**Status:** Implemented  
**Impact:** New feature — two new agents added to the scheduler

#### Context

Added OpenCallMonitor and OpenPutMonitor agents that track existing short options positions for assignment risk. These complement the existing sell-side agents (CoveredCallAgent, CashSecuredPutAgent).

#### Key Decisions

1. **TradingView-only**: Position monitors only work with the TradingView pre-fetch path. No MCP fallback — these agents have no tool access.
2. **Separate method**: `run_position_monitor_agent()` is a new method on AgentRunner, not a modification to `run_agent()`. The position file format, message template, and signal detection are all different.
3. **Position file format**: `EXCHANGE-SYMBOL,strike,expiration` — one position per line, comments/blanks supported.
4. **Roll signal fields**: Separate `_ROLL_SIGNAL_FIELDS` tuple with fields appropriate for position management (current_strike, current_expiration, new_strike, new_expiration, action) rather than sell signals.
5. **Graceful degradation**: Monitors skip silently when position files are empty/all-commented. Non-TradingView providers get a warning and skip.

#### Files Created/Modified

**Created:**
- `data/opened_calls.txt`, `data/opened_puts.txt` — position data files
- `src/tv_open_call_instructions.py`, `src/tv_open_put_instructions.py` — agent instructions
- `src/open_call_monitor_agent.py`, `src/open_put_monitor_agent.py` — agent wrappers

**Modified:**
- `src/agent_runner.py` — added `_read_positions()`, `_is_roll_signal()`, `_build_roll_signal_data()`, `run_position_monitor_agent()`
- `src/config.py` — added `open_call_monitor_config`, `open_put_monitor_config` properties
- `src/main.py` — imports + scheduler calls for both monitors
- `config.yaml` — new `open_call_monitor` and `open_put_monitor` sections
- `README.md` — architecture, key concepts, output, project structure updated

---

### 14. Re-add TradingView Overview Page as Pre-Fetched Resource

**Author:** Rusty (Agent Dev)  
**Date:** 2025-07  
**Status:** Proposed

#### Context

The overview page (`/symbols/EXCHANGE:TICKER/`) was previously dropped to save context budget (~103K chars for the old accessibility snapshot approach). With the `browser_run_code` + `innerText` extraction method, the page is much smaller and provides valuable fundamental data (P/E, market cap, dividend yield, sector) that the agent previously had to infer indirectly from analyst consensus.

#### Decision

Add `fetch_overview()` as the first pre-fetched resource, using the same `browser_run_code` + `main.innerText` pattern as technicals/forecast. This keeps the page size manageable (innerText is far smaller than accessibility snapshots) while giving the agent direct access to fundamentals.

#### Consequence

- The CSP Investment Worthiness Assessment can now use actual P/E, market cap, and dividend data instead of proxy signals.
- Total pre-fetch count goes from 3 → 4 pages per symbol, adding one browser navigation per symbol.
- If context budget becomes tight again, overview is the first candidate to drop (it was lived without before).

---

### 15. Profit Optimization Signals for Open Position Monitors

**Date:** 2025-07-22  
**Author:** Rusty (Agent Dev)  
**Status:** Implemented  
**Impact:** Agent behavior (monitor instruction prompts)

#### Context

The open position monitors (call + put) previously only detected defensive roll scenarios (assignment risk). Users wanted proactive profit optimization — rolling to a tighter strike to collect more premium when conditions are unanimously safe.

#### Decision

Added profit optimization instruction sections to both `tv_open_call_instructions.py` (ROLL_DOWN) and `tv_open_put_instructions.py` (ROLL_UP). Uses a 9-condition unanimous consensus gate — ALL must pass or the decision stays WAIT.

#### Key Design Choices

1. **Instruction-only change**: No schema changes, no `agent_runner.py` changes. ROLL_DOWN/ROLL_UP and `risk_flags` were already fully supported. This validates the architecture — schema is stable, behavior evolves through prompts.

2. **9-condition unanimity gate**: Deep OTM (5%+), very low delta (<0.15), technicals aligned, MAs aligned, no catalysts, analyst sentiment not contrary, low IV, DTE > 14, stable decision history. "No gambling" — one ambiguous indicator = WAIT.

3. **`profit_optimization` risk_flag**: Semantic marker distinguishing "rolling because the position is at risk" from "rolling because I can safely collect more premium." Propagates through existing `_ROLL_SIGNAL_FIELDS` pipeline.

4. **Confidence must be "high"**: If the agent can't say high confidence, it must not recommend the optimization.

#### Trade-offs

- **Conservative by design**: Many valid optimization opportunities will be missed because one indicator is neutral instead of confirmatory. This is intentional — false positives (bad optimization) are far worse than false negatives (missed premium).
- **No new schema fields**: Keeps the signal pipeline simple but means downstream consumers must check `risk_flags` to distinguish profit vs defensive rolls.

---

### 16. README Documentation Structure

**Date:** 2025-07  
**Author:** Rusty (Agent Dev)  
**Status:** Completed

#### Decision

Restructured README to separate "how to run it" (Setup/Running) from "how it works" (How It Works/Key Concepts). Added dedicated sections for:
1. End-to-end execution flow with provider branching
2. Decision vs Signal semantics
3. Pre-fetch architecture rationale
4. Per-symbol context filtering explanation
5. Full annotated config.yaml reference
6. Example JSONL output object

#### Rationale

The README previously covered setup and troubleshooting well but didn't explain _what the system does_ or _why_ it's designed this way. A new contributor couldn't understand the pre-fetch architecture, the decision/signal distinction, or the context injection system without reading source code. These are the core design decisions that define the project.

#### Implications

- README is now the single source of truth for system behavior — keep it updated when architecture changes
- Config reference in README mirrors actual config.yaml structure — update both together

---

### 17. Use browser_run_code for TradingView Technicals & Forecast

**Date:** 2025-07  
**Author:** Rusty  
**Status:** Implemented

#### Context

The TradingView agent uses Playwright MCP to scrape 3 pages. `browser_navigate` returns full accessibility snapshots: technicals ~48K chars, forecast ~38K chars, options chain ~37K+65K expanded. Total ~188K chars was overwhelming the model context, causing it to report "pages failed to load."

#### Decision

Use `browser_run_code` (Playwright JS execution) for technicals and forecast pages. This navigates to the page AND extracts `innerText` in a single call, returning ~3K and ~2.4K chars respectively (15-16x reduction). Options chain stays on `browser_navigate`+`browser_click`+`browser_snapshot` because it needs accessibility tree element refs for interactive clicking.

#### Trade-offs

- **Pro:** ~80K chars freed per analysis run — model no longer chokes on context
- **Pro:** `innerText` contains identical data in cleaner tab-separated format
- **Pro:** Single tool call per page vs navigate+wait+snapshot
- **Con:** `browser_run_code` returns plain text, not structured accessibility tree — cannot use element refs for clicking (not needed for these pages)
- **Con:** If TradingView changes DOM structure (e.g., removes `<main>` tag), the fallback to `document.body` still works but may include more noise

#### Affected Files

- `src/tv_covered_call_instructions.py`
- `src/tv_cash_secured_put_instructions.py`

---

### 18. TradingView Pre-Fetch Architecture

**Date:** 2025-07-17  
**Author:** Rusty (Agent Dev)  
**Status:** Implemented  
**Commit:** 9bca215

#### Context

The LLM agent unreliably executes 3+ sequential Playwright browser tool calls — it skips pages, fabricates navigation errors, or ignores tool-calling instructions. Multiple instruction-based fixes were attempted (reordering pages, innerText extraction via browser_run_code, reducing snapshot size) — none solved the fundamental problem.

#### Decision

Pre-fetch ALL TradingView data deterministically in Python, then pass it to the agent as text. The agent receives NO browser tools — it only analyzes.

#### Implementation

1. **New module `src/tv_data_fetcher.py`**: `TradingViewFetcher` class uses the same Playwright MCP tools (browser_run_code, browser_navigate, browser_click, browser_snapshot) but driven from Python, not the LLM.
2. **`src/agent_runner.py`**: Branches on `mcp_provider == "tradingview"` — pre-fetch path creates ChatAgent with no tools; all other providers use existing MCP-tool flow unchanged.
3. **TV instruction files**: Phase 1 rewritten from "gather data via browser tools" to "review pre-fetched data". All `browser_*` references removed. Phase 2 analysis logic, trading rules, output format, decision criteria unchanged.

#### Trade-offs

- **Pro**: 100% reliable data fetching — Python deterministically loads all 3 pages every time
- **Pro**: Agent context is smaller and cleaner — only data + analysis instructions, no tool-call overhead
- **Pro**: Non-tradingview providers completely unaffected
- **Con**: Agent cannot adaptively explore pages (e.g., try different expirations) — but this was unreliable anyway
- **Con**: Pre-fetch always loads all 3 pages even if one would suffice — acceptable overhead

#### Impact

- Covered call and CSP agents using TradingView provider should now consistently analyze all 3 data sources (technicals, forecast, options chain) instead of randomly skipping 1-2 pages.

---

### 19. Web Dashboard Architecture

**Date:** 2025-07-28  
**Author:** Rusty (Agent Dev)  
**Status:** Completed

#### Context

Added a web dashboard for the options agent system — a separate entry point (`run_web.py`) using FastAPI + Jinja2 templates with a dark trading theme.

#### Key Decisions

1. **Separate entry point, shared data files**: Web dashboard (`run_web.py`) and scheduler (`python -m src.main`) run independently. Both read the same JSONL logs and data files — no database layer needed.

2. **Raw YAML config loading**: The web app reads `config.yaml` directly via `yaml.safe_load()` instead of using `src.config.Config`, which requires MCP environment variables. The web app only needs the Azure endpoint (for chat) and scheduler cron expression.

3. **No build step**: Vanilla HTML/CSS/JS with custom dark-theme CSS. No npm, no bundler, no CSS framework dependency.

4. **JSONL as the database**: All dashboard data comes from reading JSONL log files and `data/*.txt` files on every request. Acceptable for the current log sizes; would need indexing if logs grow to millions of lines.

5. **Chat uses direct OpenAI API**: The chat endpoint uses `openai.AzureOpenAI` with `AzureCliCredential` — same auth pattern as the agent runner but without the agent framework overhead. Context is the last 20 decisions per log file.

6. **Hot-reload confirmed**: `_read_symbols()` and `_read_positions()` in `agent_runner.py` read from disk on every call inside `run_agent()` / `run_position_monitor_agent()`. No caching — edits via the settings page take effect on the next scheduler tick with zero code changes.

#### Trade-offs

- Reading JSONL on every request is fine for current scale but won't scale to huge logs. If needed, add a lightweight caching layer or SQLite index later.
- No authentication on the web dashboard — acceptable for local/internal use. Add auth middleware if exposing to the internet.




---
---
---




### 20. Consolidated Entry Point (`run.py`)

**Date:** 2025-07
**Author:** Rusty (Agent Dev)

## Context
The project had two separate entry points — `python -m src.main` for the scheduler and `python run_web.py` for the web dashboard. Users had to start them independently in separate terminals.

## Decision
Consolidate into a single `python run.py` that runs both web dashboard and scheduler. The scheduler runs as a daemon thread managed by FastAPI's lifespan context. CLI flags (`--web-only`, `--scheduler-only`, `--port`) provide fine-grained control.

## Key details
- Lifespan attached via `app.router.lifespan_context` — avoids modifying `web/app.py`.
- `OptionsAgentScheduler.run(install_signals=False)` when threaded — signal handlers are main-thread-only.
- `run_web.py` kept as backwards-compat shim delegating to `run.py --web-only`.
- Host/port read from `config.yaml` `web:` section; `--port` flag overrides.

## Files changed
- `run.py` (new) — unified entry point
- `src/main.py` — `run()` accepts `install_signals` param; `__main__` block suggests `run.py`
- `run_web.py` — now delegates to `run.py --web-only`
- `README.md` — updated Running section

---

### 21. Always use signal_log for dashboard and signal views

**Date:** 2025-07-22
**Author:** Rusty (Agent Dev)
**Status:** Implemented

## Context
Dashboard counts for position monitors were reading from `decision_log`, which includes WAIT decisions. This inflated signal counts (e.g., 3 WAITs shown as 3 signals when actual actionable signals were 0).

## Decision
All dashboard counts, signal list pages, and signal detail pages now read exclusively from `signal_log`. The `decision_log` is only used for:
1. "Recent Activity" feed on the dashboard (which shows all events)
2. "Recent Decisions" context section on the signals list page
3. Backing decisions on the signal detail page (correlated by timestamp)

## Impact
- Dashboard signal counts now accurately reflect actionable signals only
- Signals list page gains a "Recent Decisions" section for analysis context
- No changes to how logs are written — only how they're read for display

---

### 22. Remove non-TradingView MCP providers

**Author:** Rusty (Agent Dev)
**Date:** 2025-07-23
**Status:** Implemented

## Context

The project supported four MCP data providers (Massive.com, Alpha Vantage, Yahoo Finance, TradingView) with per-provider instruction files, config branching, and transport selection. In practice, TradingView + Playwright pre-fetch is the only provider that works reliably — LLMs cannot drive multi-step browser/tool workflows, and the other providers' MCP servers had various limitations.

## Decision

Remove all non-TradingView providers. TradingView via Playwright is the sole data source.

## Changes

- **Deleted:** 6 instruction files (`av_*`, `yf_*`, generic `covered_call_instructions.py`, `cash_secured_put_instructions.py`)
- **Simplified:** `config.yaml` MCP section flattened (no `provider` key, no per-provider sub-sections)
- **Simplified:** `config.py` — removed provider selection, pruning, transport/url/env_key properties
- **Simplified:** `agent_runner.py` — removed entire non-TradingView code path (MCP tool creation, HTTP transport, API key validation)
- **Simplified:** Agent wrappers — no provider branching, always use TV instructions
- **Updated:** README — removed multi-provider docs, comparison table, env var setup for removed providers

## Trade-offs

- **Lost:** Ability to switch to Massive/AV/Yahoo without code changes
- **Gained:** ~4100 lines of dead code removed, dramatically simpler config and runtime paths, no unused env var requirements

## Team Implications

- **Linus (Quant Dev):** Only TV instruction files exist now. Any instruction changes go to `tv_*` files.
- **Basher (Test/Ops):** No need to test multiple providers. Playwright container is the only external dependency.
- **Scribe (Docs):** README already updated. No multi-provider docs to maintain.
# Decision: Dashboard Run Button UX

**Date:** 2024-12-XX  
**Author:** Linus (Quant Dev / Frontend Dev)  
**Status:** Implemented  

## Context

The dashboard had "Run Now" buttons for each agent, but users needed:
1. Clearer button labeling (what does "Run Now" actually do?)
2. Ability to trigger all agents at once for comprehensive analysis

## Decision

1. **Button Text Change**: "Run Now" → "Run Analysis"
   - More explicit about what the button does
   - Aligns with the purpose: running analysis, not just "now"

2. **New Full Analysis Button**: Added "Run Full Analysis" button
   - Positioned above agent tables, right-aligned
   - Triggers all 4 agents sequentially (covered_call, cash_secured_put, open_call_monitor, open_put_monitor)
   - Shows progress during execution: "Running... (2/4)"
   - Blue primary styling to distinguish from individual agent buttons

## Implementation

- Sequential execution using promise chaining (not parallel)
- Uses existing `/api/trigger/{agentType}` endpoint
- Real-time progress feedback
- Button disables during execution, re-enables after completion

## Rationale

- **Sequential over Parallel**: Ensures controlled execution order and reduces server load
- **Progress Indicator**: Users can see which agent is currently running
- **Primary Styling**: Visual hierarchy makes it clear this is a comprehensive action
- **Consistent Patterns**: Reuses existing trigger button styles and API endpoints

## Alternatives Considered

1. **Parallel Execution**: Rejected due to potential resource contention
2. **Server-Side Batch Endpoint**: Rejected to keep frontend changes isolated
3. **Modal Dialog**: Rejected as too heavy for a simple batch trigger

## Impact

- **Frontend**: 3 files modified (dashboard.html, app.js, style.css)
- **Backend**: No changes needed (reuses existing endpoints)
- **UX**: Improved clarity and efficiency for users running multiple agents


---

### 8. Button Alignment Fix — Run Full Analysis Button
**Date:** 2025  
**Author:** Linus (Quant Dev / Frontend)  
**Status:** Completed  
**Impact:** UI/UX (visual consistency)

#### Context
The "Run Full Analysis" button was positioned inline with scheduler information (cron, last run, next run) in the `.scheduler-bar` container. Individual "Run Analysis" buttons on each agent card are right-aligned, creating a visual inconsistency.

#### Key Design Decision
Updated `.scheduler-bar` CSS to use flexbox space distribution:
1. Added `justify-content: space-between` — Distributes space evenly, pushing the button to the right
2. Added `align-items: center` — Ensures vertical alignment with scheduler text
3. Added `.scheduler-bar .btn-trigger { margin-left: auto; }` — Ensures button stays right, even with flex-wrap

#### Implementation
- **File Modified:** web/static/style.css
- **HTML Changes:** None (CSS-only solution)
- **Rationale:** Button already had correct CSS classes (`btn-trigger btn-trigger-blue`); solution uses standard flexbox patterns consistent with existing card headers

#### Result
"Run Full Analysis" button now right-aligns within scheduler info bar, matching visual alignment of individual "Run Analysis" buttons on agent cards.

#### Trade-offs
- **Simplicity:** CSS-only approach avoids template changes
- **Consistency:** Uses existing flexbox patterns already in codebase


---

### 9. Chat UI Design System Alignment
**Date:** 2024-03-31  
**Author:** Rusty (Agent Dev)  
**Status:** Completed  
**Impact:** Web UI consistency

#### Context
The dual-mode chat interface (Portfolio Chat + Quick Analysis) was initially implemented with custom CSS styles that didn't match the rest of the application's design system. User feedback indicated the look and feel was inconsistent with dashboard, settings, and other pages.

#### Key Design Decisions

1. **Use Standard Card Components**
   - Replace custom `.mode-option` styles with standard `.card` + `.card-header` structure
   - Use existing design tokens (`var(--bg-input)`, `var(--bg-hover)`, `var(--border)`, `var(--accent-blue)`)
   - Match padding, spacing, and border-radius to other cards in the app

2. **Free Text Input for Market Field**
   - Replace dropdown with text input for flexibility
   - Apply text-transform: uppercase for consistent display
   - Allows users to enter any market/exchange name

3. **Unified Navigation Pattern**
   - Use `.btn-sm` class for all back buttons across both modes
   - Consistent placement in card headers
   - Same "← Back" text pattern throughout

4. **Form Consistency**
   - Use `.hint` class for descriptive text (matches settings pages)
   - Use `.input-field` class for form inputs
   - Match label styling from `settings_config.html`

#### Implementation
- **Files Changed:** `web/templates/chat.html`, `web/static/style.css`
- **Design Tokens Used:** `--bg-card`, `--bg-input`, `--bg-hover`, `--border`, `--accent-blue`, `--text`, `--text-muted`, `--radius`
- **Refactoring:** Removed 30+ lines of unused CSS

#### Result
Standard card-based selection with free text inputs matching app design; all functionality preserved, visual consistency achieved.

#### Trade-offs
- **Flexibility vs Validation**: Free text input allows any market name but sacrifices dropdown validation (acceptable for power users)
- **Simplicity**: CSS reuse reduces code duplication and future maintenance burden

---

### 10. Quick Analysis Button Enable Pattern
**Date:** 2026-03-31  
**Author:** Rusty (Agent Dev)  
**Status:** ✅ Implemented  
**Impact:** Form UX improvements

#### Context
The Quick Analysis mode in `chat.html` has a "Fetch & Analyze" button that requires both `symbol` and `market` inputs. The button was initially enabled, causing UX confusion when clicked without filled fields (would show error instead of preventing click).

#### Decision
Form submission buttons in multi-mode UIs should start disabled and enable dynamically based on required field validation.

#### Implementation
1. **Default State:** Button starts with `disabled` attribute
2. **Validation Function:** `checkFetchButtonState()` checks both fields have trimmed values
3. **Event Listeners:** Attach `input` events (not `keyup`) to catch paste/autofill
4. **Mode Entry Check:** Call validation function when form first displays
5. **Enter Key:** Respect button state (don't submit if disabled)

#### Benefits
- **Immediate Feedback:** Button state reflects form validity in real-time
- **Prevents Errors:** Users can't submit incomplete forms
- **Navigation Safe:** Handles back/forward, mode switching, pre-filled values
- **Accessible:** Visual disabled state is also functional (no click handler run)

#### Pattern for Team
When adding form-based flows with required fields:
```javascript
// 1. Start button disabled
<button id="submitBtn" disabled>Submit</button>

// 2. Create validation function
function checkFormValidity() {
    const isValid = requiredField1.value.trim() && requiredField2.value.trim();
    submitBtnEl.disabled = !isValid;
}

// 3. Attach to inputs
field1El.addEventListener('input', checkFormValidity);
field2El.addEventListener('input', checkFormValidity);

// 4. Check on display
function showForm() {
    formEl.style.display = 'block';
    checkFormValidity(); // handles pre-filled values
}

// 5. Respect in Enter handlers
fieldEl.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !submitBtnEl.disabled) {
        submit();
    }
});
```

#### Files Changed
- `web/templates/chat.html`

#### Related Decisions
- Chat UI Design System Alignment (2026-03-31) — established form field patterns
- Standard `.btn` disabled styles in `web/static/style.css`

---

### 11. Quick Analysis Chat — Centralized Instruction Reuse for Put/Call Analysis
**Date:** 2026-04-01  
**Decider:** Rusty (Agent Dev)  
**Status:** ✅ Implemented  
**Impact:** Chat feature enhancement, Agent instruction reuse

#### Context
Quick Analysis chat feature extension. Previously, Quick Analysis just fetched data and started a blank chat. User wanted the first message to be the same quality analysis that monitoring agents provide—not a generic greeting.

#### Decision
Quick Analysis chat now provides automatic first analysis using the same centralized monitoring agent instructions (`TV_OPEN_CALL_INSTRUCTIONS` / `TV_OPEN_PUT_INSTRUCTIONS`) based on user-selected option type (Call/Put).

#### Implementation Details

**Frontend Changes** (`web/templates/chat.html`)
- Three-input form: Symbol + Market + Option Type (required dropdown)
- Automatic analysis trigger on successful fetch
- State flag `awaitingFirstAnalysis` to track flow
- UI shows "Analyzing for Call/Put options..." while waiting

**Backend Changes** (`web/app.py`)
- `/api/chat/fetch-symbol`: Accept and return `option_type` parameter
- `/api/chat`: Handle `first_analysis` flag
  - When `true`: Import appropriate instruction file and use as system prompt
  - When `false`: Use standard chat system prompt
- Instructions imported at runtime: `from tv_open_{call|put}_instructions import TV_OPEN_{CALL|PUT}_INSTRUCTIONS`

**Centralized Instruction Files** (Unchanged)
- `src/tv_open_call_instructions.py` — Used by `open_call_monitor` agent and Quick Analysis (call)
- `src/tv_open_put_instructions.py` — Used by `open_put_monitor` agent and Quick Analysis (put)

#### Benefits
1. **Consistency** — Quick Analysis users get the exact same quality analysis as monitoring agents provide
2. **DRY** — Single source of truth for analysis instructions (no duplication)
3. **Maintainability** — Updates to monitoring agent instructions automatically apply to Quick Analysis
4. **User Experience** — First message is immediately valuable (actionable analysis, not "How can I help you?")

#### Trade-offs
- Slightly longer wait for first message (full LLM analysis vs instant greeting)
- Users must select option type upfront (can't analyze both call and put in same session)

#### Alternatives Considered
1. **Separate instructions for chat** — Rejected: would create divergence and maintenance burden
2. **No automatic analysis** — Rejected: user explicitly requested this to match agent behavior
3. **Analyze both call and put automatically** — Rejected: would be slow and confusing to display

#### Pattern for Future Work
When building chat/analysis features that should behave like existing agents:
1. Identify the agent's instruction file
2. Import and reuse at runtime (don't duplicate)
3. Use a flag (like `first_analysis`) to switch system prompts
4. Keep the chat flow simple: automatic first message → normal Q&A

#### Files Changed
- `web/templates/chat.html` — Added dropdown, automatic first analysis trigger
- `web/app.py` — Updated endpoints to accept `option_type`, handle `first_analysis` flag, import centralized instructions

#### Related Decisions
- Chat UI Design System Alignment (2026-03-31) — established form design patterns
- Quick Analysis Button Enable Pattern (2026-03-31) — form validation pattern reused for three-input form

---

### 12. Chat vs Monitor Instructions Split
**Date:** 2026-04-01  
**Decider:** Rusty + User (dsanchor)  
**Status:** ✅ Accepted  
**Impact:** Chat feature enhancement, agent instruction architecture

#### Context

Quick Analysis chat mode was displaying JSON/structured output because it was reusing monitor agent instructions (`TV_OPEN_CALL_INSTRUCTIONS` / `TV_OPEN_PUT_INSTRUCTIONS`). These instructions were designed for monitoring agents that need to output structured JSON for database storage.

User feedback: "I don't want the json response. I want a response as a human readable conversation based on the agent response... not a json or a set of fields and key values. Human friendly please"

#### Decision

Create separate instruction sets for different use cases:

1. **Monitor Agents** (background automation):
   - Continue using `TV_OPEN_CALL_INSTRUCTIONS` / `TV_OPEN_PUT_INSTRUCTIONS`
   - Request JSON output with specific schema for database persistence
   - Focus on structured data extraction and decision logging

2. **Chat Interface** (user interaction):
   - Use new `TV_OPEN_CALL_CHAT_INSTRUCTIONS` / `TV_OPEN_PUT_CHAT_INSTRUCTIONS`
   - Request conversational, natural language analysis
   - Focus on human-readable insights and explanations
   - Avoid JSON, structured output, or field-value pairs

#### Rationale

- **Separation of Concerns:** Database storage needs structured JSON; human users need natural conversation
- **Single Source of Truth for Data:** Both use the same TradingView data fetcher and data structure
- **Different Output for Different Audiences:** Machines consume JSON; humans consume prose
- **Maintainability:** Clear naming (`*_instructions.py` vs `*_chat_instructions.py`) makes intent obvious

#### Implementation

- `src/tv_open_call_chat_instructions.py` — Conversational call analysis (chat UI)
- `src/tv_open_put_chat_instructions.py` — Conversational put analysis (chat UI)
- `src/tv_open_call_instructions.py` — Structured call monitoring (background agents)
- `src/tv_open_put_instructions.py` — Structured put monitoring (background agents)
- `web/app.py` — Chat endpoint uses `*_chat_instructions.py` for both first analysis and follow-ups

#### Consequences

**Positive:**
- Chat experience feels natural and conversational
- Monitor agents continue to produce clean JSON for database queries
- Clear separation makes future maintenance easier
- Each instruction set can evolve independently for its use case

**Negative:**
- Additional instruction files to maintain (4 instead of 2)
- Need to keep data interpretation logic aligned between chat and monitor versions
- Could drift if not careful about maintaining consistency of insights across both

**Mitigation:**
- Both draw from same data source (TradingView fetcher)
- Core analysis logic (earnings gates, technical assessment) documented in both
- One is optimized for JSON structure, one for conversational flow
- Regular review to ensure both stay aligned on trading logic

#### Pattern for Future Work

When building chat/analysis features that need different output formats:
1. Identify if audience is machine (JSON/structured) or human (prose/conversation)
2. Create separate instruction files for each audience
3. Keep core analysis logic consistent (same data sources, same decision criteria)
4. Document alignment pattern in both files (cross-references, shared examples)
5. Route to appropriate instruction set at call time (flag like `first_analysis`)

#### Files Changed
- `src/tv_open_call_chat_instructions.py` (NEW) — Conversational call analysis
- `src/tv_open_put_chat_instructions.py` (NEW) — Conversational put analysis
- `web/app.py` — Updated chat endpoints to use `*_chat_instructions.py`

#### Related Decisions
- Quick Analysis Chat — Centralized Instruction Reuse for Put/Call Analysis (2026-04-01) — established instruction reuse pattern
- Chat UI Design System Alignment (2026-03-31) — established form and conversation patterns


# Agent Trigger Scope: Optional Symbol Parameter

**Date:** 2026-04-01  
**Author:** Rusty  
**Type:** Architecture Decision

## Context

User reported bug: "Run Analysis" button on symbol detail page was triggering analysis for ALL symbols instead of just the symbol being viewed.

Example: On AAPL detail page, clicking "Run Analysis" for open call positions analyzed ALL symbols with open call positions, not just AAPL.

## Decision

All agent entry point functions now accept an optional `symbol: str = None` parameter:
- `run_open_call_monitor(config, runner, cosmos, context_provider, symbol=None)`
- `run_open_put_monitor(config, runner, cosmos, context_provider, symbol=None)`
- `run_covered_call_analysis(config, runner, cosmos, context_provider, symbol=None)`
- `run_cash_secured_put_analysis(config, runner, cosmos, context_provider, symbol=None)`

Web API endpoint `/api/trigger/{agent_type}` accepts optional `symbol` in request body and passes it through.

## Rationale

1. **Backward Compatible:** No symbol = analyze all (preserves existing behavior for dashboard/scheduled runs)
2. **Single Responsibility:** Symbol detail page should only trigger analysis for that symbol
3. **User Expectation:** Clicking "Run Analysis" on AAPL page should only run for AAPL
4. **Performance:** Scoped analysis completes faster and generates less noise

## Implementation Pattern

```python
if symbol:
    sym_doc = cosmos.get_symbol(symbol)
    if not sym_doc:
        print(f"Symbol {symbol} not found — skipping")
        return
    # Filter to just this symbol's positions/settings
    symbol_list = [sym_doc]
else:
    # Get all symbols (existing behavior)
    symbol_list = cosmos.get_symbols_with_active_positions(...)
```

## Alternatives Considered

1. **Separate endpoints** (`/api/trigger-symbol/{symbol}/{agent_type}`) — More explicit but breaks REST patterns
2. **Query parameter** (`?symbol=AAPL`) — Less flexible for future parameters, non-standard for POST
3. **No fix** — Would continue confusing users and generating incorrect analysis scope

## Impact

- All agent trigger paths support scoped execution
- Symbol detail page now correctly scopes analysis
- Dashboard/settings pages unaffected (don't pass symbol)
- Scheduler unaffected (doesn't pass symbol)

# Position ID Uniqueness Fix

**Date:** 2026-04-01  
**Agent:** Rusty  
**Type:** Bug Fix / Data Integrity

## Decision

Position IDs now include a UTC timestamp to guarantee uniqueness across the entire lifetime of positions.

## Old Format

```
pos_{symbol}_{option_type}_{strike}_{expiration}
```

Example: `pos_AAPL_PUT_150.0_20260417`

## New Format

```
pos_{symbol}_{option_type}_{strike}_{expiration}_{timestamp}
```

Example: `pos_AAPL_PUT_150.0_20260417_20260401_214900`

Timestamp format: `YYYYMMDD_HHMMSS` (UTC)

## Rationale

The old format caused collisions in these scenarios:
1. **Roll A → B → A**: Rolling from strike A to B, then later rolling back from B to A
2. **Close/Reopen**: Closing a position at strike X, then later opening a new position at same strike X
3. **Data Integrity**: Collisions led to delete operations affecting wrong positions and close operations failing

## Impact

- **Fixes**: Cascade delete bug, close operation failures
- **Guarantees**: Each position has a unique ID forever
- **Breaking Changes**: None (position_id is internal, API unchanged)
- **Performance**: Negligible (just appending a timestamp)

## Implementation

- **File**: `src/cosmos_db.py`
- **Method**: `_generate_position_id()` (new static method)
- **Updated**: `add_position()`, `roll_position()`
- **Removed**: Collision check logic (no longer needed)

## Testing

✓ Roll A → B → A creates 3 distinct IDs  
✓ Close/reopen creates 2 distinct IDs  
✓ Module imports successfully  
✓ All position creation paths covered

---

### 16. Alert Link Pattern: Document ID Field Usage

**Date:** 2026-04-02  
**Author:** Rusty (UI/Integration)  
**Status:** ✅ Implemented  
**Impact:** Symbol detail page, alert navigation UX

#### Problem Statement

Alert rows on symbol detail page generated 404 errors when clicked. Activity rows worked correctly. Dashboard links (both alerts and activities) worked.

#### Root Cause

Alert row template referenced non-existent field `alt.activity_id` instead of the actual document ID field `alt.id`. The activity template correctly used `item.id`. Dashboard patterns showed both activities and alerts use the same `id` field format for detail navigation.

#### Solution

Changed alert row template:
- **From:** `data-href="/activities/{{ alt.activity_id }}"`
- **To:** `data-href="/activities/{{ alt.id }}"`

File: `web/templates/symbol_detail.html` — Alert row clickable navigation link

#### Pattern

Both activities and alerts are documents stored in CosmosDB with an `id` field. Both link to the same `/activities/{id}` detail endpoint (alerts and activities share the same document type with `is_alert` boolean discriminator). Always use `{item}.id` for activity/alert detail links, never invent intermediate field names.

#### Context

- Activities: `doc_type = 'activity', is_alert = false` (or undefined)
- Alerts: `doc_type = 'activity', is_alert = true`
- ID format: `{symbol}_{agent_type}[_{position_id}]_{ts_compact}` (no prefixes)

#### Impact

✓ Alert navigation now works  
✓ Consistent with activity and dashboard patterns  
✓ No data model changes

---

## Pending Review Decisions (from inbox — 2026-04-02)

### 17. Symbol Chat Context Selection Screen

**Date:** 2025-01  
**Author:** Linus (Backend Dev)  
**Status:** ✅ Implemented  
**Impact:** Symbol chat UX (affects web/templates/symbol_chat.html)

#### Context

The symbol detail chat previously showed the chat interface immediately with context checkboxes at the top. Users could toggle checkboxes while chatting, but this created confusion about what context was loaded when chat started.

#### Decision

Implement a two-screen flow for symbol chat:
1. **Selection Screen** appears first with 3 context checkboxes
2. **Chat Screen** appears after deliberate selection

#### Implementation

Modified `web/templates/symbol_chat.html`:
- Created selection screen div with checkbox layout
- Created chat screen div with context indicator
- Added JavaScript handlers for screen transitions
- Added context change reset functionality
- Preferences saved to localStorage

#### Rationale

- Makes context choices deliberate and conscious
- Clear visibility of what data assistant has
- Prevents mid-chat confusion about loaded data
- Locked context prevents partial updates during conversation

#### Benefits

✓ Clarity: Users know exactly what context is loaded  
✓ Intent: Deliberate selection before engaging  
✓ Simplicity: No confusing mid-chat checkbox toggles  
✓ Persistence: Preferences saved via localStorage  
✓ Flexibility: Can change context and restart easily

---

### 18. Put Roll Up Strategy Relaxation Implementation

**Date:** 2026-04-01  
**Author:** Linus (Backend Dev)  
**Status:** Implemented  
**Context:** Roll strategy optimization following covered call roll down relaxation

#### Decision Summary

Implemented relaxation of the cash-secured put ROLL_UP profit optimization gate from unanimous 9/9 consensus requirement to super-majority gate (3 mandatory + 4 of 7 flexible conditions). Aligns with recent covered call roll down relaxation work and applies research-backed thresholds.

#### Implementation

Updated put roll optimization gates to apply research-validated profit/margin thresholds with flexible condition matching rather than requiring all conditions to pass.

#### Benefits

- Improved optimization opportunities while maintaining strict safety standards
- Consistent with covered call roll down approach
- Aligned with quantitative research findings

---

### 19. Scheduler Reload Implementation

**Date:** 2026-04-02  
**Author:** Linus (Backend Dev)  
**Status:** Implemented  
**Context:** Configuration and runtime management improvements

#### Decision Summary

Implemented scheduler reload capability to apply configuration changes without full application restart, reducing deployment friction and enabling faster iteration on scheduling logic.

#### Benefits

✓ Faster configuration updates  
✓ Reduced downtime  
✓ Better operational flexibility

---

### 20. Put Roll Implementation Details

**Date:** 2026-04-01  
**Author:** Linus (Backend Dev)  
**Status:** Implemented  
**Context:** Options trading automation and roll mechanics

#### Implementation

Completed implementation of put roll mechanics with proper state transitions, position tracking, and integration with existing roll frameworks. Validated through comprehensive scenario testing.

#### Scope

- Position state management for put rolls
- Roll mechanics and validation
- Integration with existing position management systems

---


### 21. Unified Activities + Alerts View with Alert Filter

**Date:** 2026-04-02  
**Author:** Rusty (Agent Dev)  
**Status:** ✅ Implemented  
**Scope:** Symbol detail page UX

#### Decision

Unified the separate "Recent Alerts" and "Recent Activities" cards on symbol detail pages into a single chronological list. Added 📢 megaphone icon for alerts and "📢 Alerts" filter pill.

#### Context

Previously, symbol detail pages showed two separate cards:
1. **Recent Alerts** — Alerts only (is_alert=true)
2. **Recent Activities** — Non-alerts only (is_alert!=true)

**Problem:** Users couldn't determine chronological order between alerts and activities. Monitoring requires temporal context.

#### Implementation

**Backend (web/app.py lines 973-1013):**
- Merged `get_recent_activities()` and `get_recent_alerts()` calls
- Combined into single `activities` list, sorted by timestamp desc
- Increased item cap from 50 to 80 items
- Preserved separate `alerts` variable for position form pre-fill logic

**Frontend (symbol_detail.html lines 351-426):**
- Removed separate "Recent Alerts" card
- Updated "Recent Activities" card to show both types
- Unified columns: Timestamp | Agent | Activity | Strike | Expiration | Underlying | Confidence | Details
- Added megaphone icon (📢) for alert rows
- Added "📢 Alerts" filter toggle button

**JavaScript (app.js lines 126-200):**
- Enhanced `applyTableFilter()` with alerts-only filtering
- Combined time range and type filtering logic
- Dynamic badge count update

#### Rationale

**UX improvement:** Single chronological view eliminates mental timeline reconstruction. Users need temporal context for monitoring workflows.

**Data integrity preserved:** Backend maintains separate `alerts` list for position form logic. No breaking changes.

**Consistent pattern:** Megaphone icon matches dashboard visual language.

#### Pattern for Future Work

When displaying time-series data with multiple types (alerts, activities, events), prefer:
- Single unified chronological view with type filters
- Over separate cards requiring mental timeline reconstruction

#### Files Modified

- `web/app.py` — Backend merge logic (lines 973-1013)
- `web/templates/symbol_detail.html` — Template restructure (lines 351-426)
- `web/static/app.js` — Filter logic with alerts toggle (lines 126-200)

---

### 22. Summary Agent Multi-Agent-Type Data Fix

**Date:** 2026-04-10  
**Author:** Linus (Quant Dev)  
**Status:** ✅ Implemented  
**Impact:** Data layer, summary agent accuracy

#### Problem Statement

The daily portfolio summary agent was generating incomplete summaries when a symbol had multiple agent types active (e.g., both `covered_call` and `cash_secured_put` watching enabled, or watching + monitor agents for open positions).

**Symptom**: Summary would only include activities from the most active agent_type, omitting the other(s) entirely.

**Root cause**: `CosmosDBClient.get_recent_activities_by_symbol()` (line 667) used `TOP @limit` on a single query filtering only by `doc_type = 'activity'`, without considering `agent_type`. This returned the N most recent activities **overall**, not N per agent_type.

**Example failure scenario**:
- Symbol: AAPL
- Activities: 10 recent `covered_call` decisions, 2 recent `cash_secured_put` decisions
- Query: `TOP 3` activities for AAPL
- Result: 3 `covered_call` activities, 0 `cash_secured_put` activities
- Summary agent sees only covered call data, generates incomplete summary

#### Decision

Changed `get_recent_activities_by_symbol()` to fetch `limit_per_symbol` activities **per agent_type per symbol**, then merge and sort by timestamp DESC.

#### Implementation Details

**Query Strategy**:
1. Fetch list of all symbols (unchanged)
2. For each symbol, iterate over all 4 agent_types: `covered_call`, `cash_secured_put`, `open_call_monitor`, `open_put_monitor`
3. For each agent_type, query `TOP @limit` activities filtering by both `doc_type = 'activity'` AND `agent_type = @agent_type`
4. Merge all agent_type results into a single list per symbol
5. Sort merged list by timestamp DESC (newest first)
6. Return `dict[str, list[dict]]` as before

**Return Type**: Unchanged — `dict[str, list[dict]]` (symbol → list of activities)

**Activity Count Per Symbol**: Now up to `limit_per_symbol × 4` (was exactly `limit_per_symbol`)

**Backward Compatibility**: Maintained — callers receive the same data structure, just with more complete data

#### Code Changes

**File**: `src/cosmos_db.py`, lines 667-700

**Docstring Updated**: Clarified that `limit_per_symbol` is now **per agent_type**, and total activities returned may be up to `limit_per_symbol × number_of_active_agent_types`.

#### Verification

**Caller Compatibility**:
- `src/agent_runner.py:683` — `run_summary_agent()` calls `get_recent_activities_by_symbol()`, passes results to summary agent as JSON. More activities = more complete summaries. ✅ Compatible
- `web/app.py` — Does NOT call this method. ✅ No impact

#### Rationale

**Why per-agent-type querying?**
- Ensures all active strategies are represented in summaries, regardless of activity frequency
- Prevents high-activity agent types from crowding out low-activity ones
- Aligns with user expectation: "summarize all my positions/watching" means ALL, not just the most active

**Why hardcode the 4 agent_types?**
- These are the only 4 agent types in the system (covered_call, cash_secured_put, open_call_monitor, open_put_monitor)
- If empty, the query returns 0 results for that agent_type — no harm, just skipped
- Future agent types can be added to the list when they exist

**Why not increase `limit_per_symbol` instead?**
- Doesn't solve skew problem — if one agent_type is 10× more active, it still dominates
- Per-agent-type ensures representation even with massive activity imbalances

#### Trade-offs

**Pros**:
- ✅ Complete data for summary agent — all agent types represented
- ✅ Backward compatible — same return type, same callers
- ✅ Simple implementation — just iterate 4 agent_types, merge results

**Cons**:
- ❌ More CosmosDB queries (4 per symbol instead of 1)
- ❌ Potentially more activities returned per symbol (up to 4× limit_per_symbol)
- ❌ Slightly higher RU consumption (4 partition queries per symbol)

**Mitigation**:
- Summary agent runs once per day — query cost is negligible
- Increased data volume improves summary quality (worth the cost)
- If performance becomes an issue, can optimize with parallel queries or caching

---

### 23. Sequential Full Analysis via /api/trigger-all

**Date:** 2026-04-10  
**Author:** Rusty (Agent Dev)  
**Status:** ✅ Implemented  
**Impact:** Backend API, Frontend UI

#### Context

The "Run Full Analysis" button previously fired 4 separate `/api/trigger/{agent_type}` calls. Each spawned an independent background thread, so all 4 agents ran in parallel — causing resource contention and unpredictable execution order.

#### Decision

Added a dedicated `POST /api/trigger-all` endpoint that runs all 4 agents **sequentially in a single thread**. Progress is tracked via a shared status dict on `app.state._full_analysis_status` and exposed via `GET /api/trigger-all/status`. The frontend polls this status endpoint every 4 seconds and updates the button text with real-time progress (`"⏳ Running 2/4: cash_secured_put…"`).

#### Implementation Details

**Agent Execution Order**: covered_call → cash_secured_put → open_call_monitor → open_put_monitor

**Error Handling**: If one agent errors, the next still runs (errors are logged but not blocking)

**Concurrency Control**: 409 Conflict returned if a full analysis is already running

**Status Lifecycle**:
- Status auto-resets 30 seconds after completion
- All individual "Run Analysis" and per-row trigger buttons are disabled during a full run

**Backward Compatibility**: Existing `/api/trigger/{agent_type}` endpoints unchanged (still fire-and-forget)

#### Files Changed

- `web/app.py` — new `/api/trigger-all` and `/api/trigger-all/status` endpoints + `_run_all_agents_sequentially()` worker
- `web/static/app.js` — replaced chained fetch calls with single trigger + polling

#### Rationale

**Sequential execution prevents:**
- Resource contention on shared database
- Race conditions on position state
- Unpredictable execution timing

**Status polling improves UX:**
- Users know agents are running (not silent)
- Real-time feedback on progress (which agent, what number)
- Prevents multiple overlapping full runs

**Backward Compatibility preserved:**
- Individual trigger endpoints still available for per-agent runs
- Users can still run agents independently if needed

#### Pattern for Future Work

When running multiple sequential background tasks:
- Track state in `app.state` with locks to prevent overlapping executions
- Expose status via separate status endpoint (not just response)
- Poll status on frontend with reasonable interval (4-10 seconds)
- Display real-time progress (task N of M, current task name)

---

### 24. Agent Type Filter — Dynamic Population from DOM

**Date:** 2026-04-16  
**Author:** Rusty (Agent Dev)  
**Status:** ✅ Implemented  
**Impact:** Dashboard and Symbol Detail UX

#### Context

Dashboard Recent Activity and symbol detail Recent Activities sections needed agent type filtering. Options could be passed server-side or built client-side.

#### Decision

Populate the agent type dropdown options dynamically from the DOM (same pattern as the symbol filter) rather than injecting them from the server. This avoids coupling the JS to the Python `AGENT_TYPES` dict and means any new agent type automatically appears once it has activity items.

#### Implementation

**Files Modified:**
- `web/static/app.js` — Filter logic + dynamic population from data-agent-type attributes (~80 lines)
- `web/templates/dashboard.html` — Added `#activity-agent-filter` select + `data-agent-type` attribute
- `web/templates/symbol_detail.html` — Added `#sym-activity-agent-filter` select + `data-agent-type` attribute

**Pattern:**
1. Activity/alert rows include `data-agent-type` attribute with the agent type value
2. Filter dropdown dynamically collects unique agent types from visible rows on page load
3. JavaScript filtering hides/shows rows based on selected filter value
4. "All" option shows everything; each agent type shows only that agent's activities

#### Trade-off

If an agent type has zero recent activity, it won't appear in the dropdown. This is acceptable since filtering an absent type would yield no results anyway.

#### Rationale

- **DRY:** Don't duplicate agent type list in Python + JavaScript
- **Automatic:** New agent types appear in filter as soon as they generate activities
- **Consistent:** Uses same DOM-scanning pattern as symbol filter
- **No Server Changes:** Frontend-only implementation

---

### 25. Mandatory Premium Cross-Verification Step

**Date:** 2026-07-14  
**Author:** Linus (Quant Dev)  
**Status:** ✅ Implemented  
**Impact:** Agent instructions (7 files)

#### Problem

The CSP watcher agent was reporting premium (bid) from the correct strike but wrong expiration date — specifically the last expiration key in the options chain JSON. The LLM reads a multi-expiration nested dict and silently crosses expiration boundaries when extracting prices.

#### Decision

Add a mandatory "Premium Cross-Verification" step to every agent instruction file that produces a JSON activity block. The step requires the agent to explicitly cite the full chain lookup path (e.g., `puts["20260613"]["95.0"]["bid"] = 3.45`) and verify the expiration key matches the recommended date before writing the JSON output.

#### Scope

- **Watcher agents** (CSP, CC): New numbered step in RESPONSE STRUCTURE before JSON Activity Block
- **Roll agents** (open call roll, open put roll): New subsection before Final Activity JSON Schema — verifies both buyback (ask) and new position (bid) paths
- **Chat agents** (call chat, put chat): Lighter-weight verification guidance section
- **Schema description** (`options_chain_parser.py`): Added COMMON ERROR warning to DATA INTEGRITY section — injected into all agents at runtime

#### Rationale

- Zero runtime cost — this is prompt text only, no code logic changes
- Forces the LLM to make its lookup explicit, which naturally catches cross-expiration errors
- The contrarian agent already had a similar check added in a prior fix; this extends the pattern to the primary agents
- Same structural pattern as the "Never output bare ROLL" fix — making implicit behavior explicit prevents silent errors

#### Files Modified

`options_chain_parser.py`, `tv_cash_secured_put_instructions.py`, `tv_covered_call_instructions.py`, `tv_open_call_roll_instructions.py`, `tv_open_put_roll_instructions.py`, `tv_open_call_chat_instructions.py`, `tv_open_put_chat_instructions.py`

---

### 26. Contrarian Agent Refactored to Quality Auditor

**Date:** 2026-07  
**Author:** Linus (Quant Dev)  
**Status:** ✅ Implemented  
**Commit:** 305f33b  
**Impact:** Agent behavior, signal quality

#### What Changed

The contrarian agent (`src/tv_contrarian_instructions.py`) was refactored from a "devil's advocate that always argues the opposite" to a "quality auditor that challenges only when it finds real issues."

#### Why

The adversarial framing caused the LLM to manufacture objections against correct decisions. Real example: flagging >3% monthly CSP premium as "low" — when 3% is outstanding. The instruction "ALWAYS argue the opposite" left no room for the agent to say "this decision is correct."

#### Key Changes

1. **Role/Mission**: Devil's Advocate → Quality Auditor. Agent now audits for data errors, blind spots, and unaddressed risks instead of arguing the opposite.
2. **Rule #1**: "ALWAYS argue the opposite" → "Challenge ONLY when you find genuine issues."
3. **Premium benchmarks added**: CSP >1.5%/mo is good, >2% excellent, >3% outstanding. CC >1%/mo good, >2% excellent. Agent must not flag premium above these thresholds.
4. **WEAK = best outcome**: Explicitly stated that a WEAK result ("analysis is sound, proceed with confidence") is the most valuable outcome, not a failure.
5. **All playbooks**: Framing changed from adversarial ("argue the opposite") to audit checklist ("check if any of these risk factors were overlooked"). All existing angles preserved — they're good risk checks.

#### Team Impact

- **Rusty (Framework)**: No API changes. `CONTRARIAN_OUTPUT_SCHEMA` structure unchanged. `get_contrarian_instructions()` signature unchanged.
- **Danny (Architect)**: Philosophy shift aligns with the goal of reducing false-positive alerts. The contrarian phase should now produce higher signal-to-noise.
- **Expected behavior change**: More WEAK results, fewer manufactured MODERATE/STRONG challenges. RECONSIDER verdicts should only appear for genuine issues.

---


---

## 2026-04-20T10:25:00Z: User directive

**By:** dsanchor (via Copilot)  
**What:** Always update README if necessary. Any new functionality or changes to existing ones require a README update.  
**Why:** User request — captured for team memory

---

## 2. Options Chain Format Recommendation

**Date:** 2026-01-15  
**Author:** Linus (Quant Dev)  
**Status:** Proposed  
**Impact:** Monitor agents (open_call_monitor, open_put_monitor), options chain parser, agent instructions

### Problem Statement

Monitor agents are hallucinating bid/ask prices when recommending roll operations. Despite multiple rounds of anti-hallucination guardrails (VERIFICATION steps, DATA INTEGRITY rules, action-oriented descriptions), fabrication rate remains at 30-40%.

**Root cause identified:** The current nested-array JSON format forces a 4-step lookup task that exceeds LLM reliability thresholds:
1. Navigate to expiration key in nested dict
2. Scan array of 20-40 contracts
3. Match strike by equality comparison
4. Extract bid or ask field

LLMs are autocompletion engines — they pattern-match plausible number sequences rather than precisely indexing arrays.

### Proposed Solution

**Strike-Keyed Dictionaries + Position-Relative Filtering** (Hybrid approach)

#### Format Change

**Current:**
```json
{
  "calls": {
    "20260427": [
      {"strike": 470, "bid": 3.20, "ask": 3.50, ...},
      {"strike": 472.5, "bid": 2.95, "ask": 3.20, ...},
      {"strike": 475, "bid": 2.50, "ask": 3.00, ...}
    ]
  }
}
```

**Proposed:**
```json
{
  "calls": {
    "20260427": {
      "470.0": {"bid": 3.20, "ask": 3.50, "delta": 0.42, "iv": 0.31},
      "472.5": {"bid": 2.95, "ask": 3.20, "delta": 0.38, "iv": 0.30},
      "475.0": {"bid": 2.50, "ask": 3.00, "delta": 0.35, "iv": 0.28}
    }
  }
}
```

#### Filtering Rule

Only include strikes within ±15 strikes of the current position.
- For $475 position: include $437.50 to $512.50 (assuming $2.50 increments)
- Reduces chain from 100-200 contracts → 30-40 contracts
- Token reduction: 60-75%

#### Lookup Pattern

**Before:** "Find strike 475 in array, extract ask field"  
**After:** `calls["20260427"]["475.0"]["ask"]`

Direct key path. No iteration, no filtering, no equality matching. Autocompletion-friendly.

### Expected Outcomes

1. **Hallucination rate:** 30-40% → <5%
2. **Token efficiency:** 60-75% reduction in chain size
3. **Verification simplicity:** Agent states full path (e.g., `calls["20260427"]["475.0"]["ask"] = 3.00`)
4. **Cognitive load:** Minimal — direct key access vs multi-step search

### Implementation Impact

#### Files to Modify

1. **`src/options_chain_parser.py`**
   - Add strike-keyed output option
   - Add position-relative filtering function
   - Update `OPTIONS_CHAIN_SCHEMA_DESCRIPTION`

2. **`src/tv_open_call_instructions.py`**
   - Update VERIFICATION steps with new lookup pattern
   - Update roll economics examples

3. **`src/tv_open_put_instructions.py`**
   - Same verification updates

4. **`src/agent_runner.py`**
   - Call parser with new format flag
   - Pass current position for filtering

#### Migration Strategy

- **Backward compatible:** Parser can output both formats during transition
- **Testing:** Run on 10-20 positions with known correct rolls
- **Validation:** Log all roll economics with source paths, flag mismatches
- **Rollback:** Keep legacy format as fallback

### Alternative Approaches Considered

#### Markdown Tables (Option 2a)
- **Pros:** Maximum clarity, excellent bid/ask column separation
- **Cons:** 30-40% more tokens than JSON
- **Verdict:** Fallback if JSON still shows >10% error rate

#### Pre-Computed Roll Tables (Option 2e)
- **Pros:** Eliminates all lookup errors, smallest token footprint
- **Cons:** Reduces agent autonomy, major architectural change
- **Verdict:** Future iteration if strike-keyed format fails

#### Flat CSV Text (Option 2c)
- **Pros:** Most compact (40% fewer tokens)
- **Cons:** Positional fields error-prone, hard to navigate
- **Verdict:** Rejected — trades clarity for token savings

### Success Metrics

#### Immediate (Week 1)
- [ ] Hallucination rate <10% on test set (20 positions)
- [ ] Zero contract-not-found errors when strikes exist
- [ ] Agent successfully quotes source paths in verification

#### Short-term (Month 1)
- [ ] Hallucination rate <5% in production
- [ ] Token usage reduced by 60%+ on typical chains
- [ ] No roll recommendation rollbacks due to price errors

#### Long-term
- [ ] Zero hallucinated prices over 90-day rolling window
- [ ] Agent autonomy preserved (can explore all available strikes)

### Risk Mitigation

#### Edge Case: Strike Not in Filtered Chain
**Problem:** Agent wants to roll to $500, but filtering cut it off (current position $475, cutoff $513)  
**Solution:** Agent response includes: "Strike 500.0 not available in filtered chain. Recommend $497.5 (highest available) or request full chain."

#### Edge Case: Float Precision
**Problem:** Strike 475 vs 475.0 vs 475.00  
**Solution:** Use string keys: `"475.0"` (avoid JavaScript float precision issues)

#### Edge Case: Missing Strike in Data
**Problem:** TradingView didn't return a specific strike  
**Solution:** Existing "contract not found" logic remains — format change doesn't affect this

### Decision Timeline

- **Week 1:** Implement parser + filtering
- **Week 2:** Update agent instructions, test on sample positions
- **Week 3:** Deploy to production with validation logging
- **Week 4:** Measure hallucination rate, adjust if needed

### References

- Full analysis: `options_chain_format_analysis.md`
- Related history: `.squad/agents/linus/history.md` (Anti-Hallucination Guardrails, July 2026)
- Related code: `src/options_chain_parser.py`, `src/tv_open_call_instructions.py`, `src/tv_open_put_instructions.py`

### Approval Status

**Pending team review.**

**Recommendation strength:** HIGH — This addresses a structural root cause, not a symptom. Prompt engineering has been exhausted; data format is the bottleneck.

---

## 3. Decision: Anti-Hallucination Guardrails for Roll Pricing

**Author:** Linus (Quant Dev)  
**Date:** 2026-07-22  
**Status:** Implemented (not yet committed)

### Context
Monitor agents (open call + open put) were fabricating bid/ask prices when recommending rolls instead of reading actual values from the options chain JSON data.

### Decision
Added three layers of defense against price hallucination:

1. **Schema-level guardrail** (`OPTIONS_CHAIN_SCHEMA_DESCRIPTION` in `options_chain_parser.py`): New "DATA INTEGRITY (MANDATORY)" section that explicitly forbids estimating, interpolating, or fabricating prices. Applies to ALL agents that receive options chain data.

2. **Verification step** (both `tv_open_call_instructions.py` and `tv_open_put_instructions.py`): After Roll Economics Calculation, agents must now perform a 4-step verification: find current contract → read ask, find target contract → read bid, fail gracefully if either missing, quote exact values.

### Rationale
LLMs will confabulate plausible-looking numbers unless explicitly told not to AND given a concrete alternative behavior (e.g., "set roll_economics to null"). Both the prohibition and the fallback are required.

### Impact
- All agents receiving options chain data see the integrity constraint (via shared schema description)
- Roll recommendations in both call and put monitors now require verifiable chain lookups
- No code logic changed — only instruction string content

---

## 4. Decision: Strike-Keyed Dictionary Format for Options Chains

**Date:** 2026-05-12  
**Author:** Linus (Quant Dev)  
**Status:** Implemented  
**Impact:** Parser output format, agent instructions, agent_runner formatting

### Summary

Options chain data format changed from arrays-of-contracts to strike-keyed dictionaries. Monitor agents now receive position-filtered chains (±15 strikes).

### Changes

1. **`parse_options_chain()`** outputs `calls["exp"]["strike_key"] = {contract}` instead of `calls["exp"] = [{contract}, ...]`
2. **`filter_options_chain_for_position()`** new function — trims chain to ±15 strikes around current position
3. **`_format_options_chain()`** accepts optional `current_strike`/`option_type` — monitor agents pass these, analysis agents don't
4. **Agent instructions** VERIFICATION sections use direct key-path syntax

### Rationale

- Direct key access eliminates array iteration errors by LLMs (hallucinated strike matching)
- Position-relative filtering reduces token count by 60-75% for monitor agents
- Expected hallucination rate drop from 30-40% to <5%

### Team Notes

- **Rusty**: No framework changes needed — this is purely data format + strategy logic
- **Danny**: If adding new agent types that consume options chains, use `_format_options_chain()` — pass `current_strike` only if the agent monitors a specific position
- Strike key format is `str(float(strike))` → always "475.0" style, never "475"

## 5. Decision: Pivot Points Are Guidance, Not Literal Strike Values

**Author:** Rusty  
**Date:** 2026-07  
**Status:** Applied  

### Context
Phase 2 roll management treated pivot point levels (R1/R2/R3 for calls, S1/S2/S3 for puts) as literal strike prices to look up in the candidates table. These calculated values almost never match actual option chain strikes, causing failed lookups and unnecessary CLOSE recommendations.

### Decision
- Pivot points and delta targets are **guidance for choosing among actual table rows**, not literal strike values.
- When a target falls between available strikes, snap in the safe direction: **UP for calls, DOWN for puts**.
- The agent must ONLY select strikes that exist as rows in the candidates table.
- The ROLL SEARCH ALGORITHM references "next available strike(s)" instead of fixed dollar offsets.

### Impact
Both `tv_open_call_roll_instructions.py` and `tv_open_put_roll_instructions.py` updated. No code changes needed — this is instruction-level guidance that the LLM agent follows at runtime.

### Commit
c0034bf: `fix: pivot points as guidance, not literal strikes in roll instructions`

---

## 6. Decision: Bare ROLL Prohibition + ROLL_OUT Guardrail

**Date:** 2026-07  
**Author:** Linus (Quant Dev)  
**Status:** Implemented  
**Impact:** All four monitor instruction files (Phase 1 assessment + Phase 2 roll management, calls + puts)

### Problem

1. **Bare ROLL bug**: Phase 1 assessment agents sometimes output `"action_needed": "ROLL"` without a direction suffix. This is invalid — downstream parsing and Phase 2 handoff expects a specific roll type.

2. **ROLL_OUT → immediate CLOSE loop**: Phase 1 recommends ROLL_OUT (same strike, later expiry), the roll fires, position updates. Next monitoring cycle sees the same bad strike and recommends CLOSE. The ROLL_OUT was pointless — it delayed close by one cycle.

### Decisions

#### 1. Explicit Valid Actions Enumeration
- Added `⛔ VALID ACTIONS — ENUMERATED LIST` section near the top of all four files
- Phase 1: WAIT, ROLL_DOWN, ROLL_UP, ROLL_OUT, ROLL_UP_AND_OUT, ROLL_DOWN_AND_OUT
- Phase 2: CLOSE, ROLL_DOWN, ROLL_UP, ROLL_OUT, ROLL_UP_AND_OUT, ROLL_DOWN_AND_OUT
- Explicit rejection: "Never output bare ROLL — always include the direction suffix"
- Added constraint on `action_needed` field in Phase 1 handoff JSON schema
- Added constraint on `activity` field in Phase 2 output JSON schema

#### 2. ROLL_OUT Guardrail (Phase 1 only)
- ROLL_OUT only when: strike still near-the-money (calls: delta 0.30–0.60; puts: |delta| 0.25–0.50), position ≤5 DTE, no directional signal
- NOT when: deep ITM/OTM, directional breakout, or position would be CLOSE regardless of expiration
- Default to compound rolls (ROLL_UP_AND_OUT, ROLL_DOWN_AND_OUT) when both strike and time need adjustment
- This is ADDITIVE — no existing logic was removed

### Files Changed
- `src/tv_open_call_assessment_instructions.py`
- `src/tv_open_put_assessment_instructions.py`
- `src/tv_open_call_roll_instructions.py`
- `src/tv_open_put_roll_instructions.py`

---

## 7. Decision: CLOSE is a Phase 2-only action

**Author:** Linus (Quant Dev)
**Date:** 2026-07
**Status:** Implemented

### Context

Phase 1 (Position Assessment) was producing `action_needed: "CLOSE"` in the handoff JSON. However, Phase 1 only has the current contract's delta/IV — it does NOT have the full options chain. The CLOSE decision requires evaluating whether ANY viable roll exists, which demands chain data for buyback costs, new premiums, and roll tier calculations.

### Decision

Phase 1 now only outputs WAIT or a ROLL type. CLOSE is exclusively a Phase 2 determination.

#### Specific changes:
1. Removed `CLOSE` from `action_needed` enum in both assessment handoff schemas
2. Added `close_for_profit_recommended` (boolean) and `profit_level_pct` (float) to handoff JSON for TastyTrade 50%+ profit scenarios
3. Phase 2 handles CLOSE via three paths:
   - `close_for_profit_recommended: true` + ask price confirms profit → CLOSE for profit
   - Roll Search Algorithm exhausted with no Tier 1/2 candidate → CLOSE (no_viable_roll)
   - `fundamental_deterioration` in risk_flags + no viable roll → CLOSE
4. Earnings gate result names (CLOSE_OR_ROLL, etc.) are preserved as risk labels — only the ACTION changes

### Rationale

The agent making a decision must have the data to justify it. CLOSE requires full chain economics that only Phase 2 possesses. This separation of concerns prevents Phase 1 from making economically uninformed closure decisions.

### Files Changed
- `src/tv_open_call_assessment_instructions.py`
- `src/tv_open_put_assessment_instructions.py`
- `src/tv_open_call_roll_instructions.py`
- `src/tv_open_put_roll_instructions.py`

---

## 8. Decision: Near-ATM Stability Buffer for Phase 1 Assessment

**Date:** 2026-07  
**Author:** Linus (Quant Dev)  
**Status:** Implemented  
**Impact:** Phase 1 call + put assessment instructions (tv_open_call_assessment_instructions.py, tv_open_put_assessment_instructions.py)

### Problem

Positions that go slightly ITM (price barely crosses the strike) immediately get a ROLL recommendation. On the next monitoring run, the stock may pull back to OTM and get WAIT. This creates noisy oscillating ROLL/WAIT recommendations that aren't actionable.

### Decision

Added a **stability zone** (0-3% ITM) where Phase 1 defaults to WAIT when technicals are favorable, instead of immediately recommending ROLL. This provides hysteresis to prevent flip-flopping.

#### Key Design Choices

1. **3% threshold**: Wide enough to absorb normal intraday/inter-day fluctuations, narrow enough that truly ITM positions still get ROLL.
2. **Technicals gate**: The buffer only applies when oscillators and MAs suggest the move may be temporary. If technicals confirm the adverse move, ROLL fires immediately.
3. **Delta 0.60 hard cap**: Even in the stability zone, delta > 0.60 means deep ITM — always ROLL.
4. **Anti-flip-flop rule**: Added to activity log interpretation — require delta change > 0.10 or price change > 1% to switch from WAIT to ROLL.
5. **No impact on other gates**: Earnings gate, ROLL_OUT guardrail, and profit optimization gate are untouched and take priority.

### Scope

- Phase 1 assessment only — does not affect Phase 2 roll economics
- Both call and put variants, with correctly inverted logic for puts
- New risk flag `near_atm_stability` added to taxonomy

---

## 9. Decision: Pre-Computed Markdown Tables for Phase 2 Roll Instructions

**Date:** 2026-07
**Author:** Linus (Quant Dev)
**Status:** Implemented
**Impact:** Phase 2 roll instruction files (call + put)

### Context

LLM agents consistently misread raw JSON options chain data in Phase 2 roll management. The nested dict format (`calls["20260520"]["475.0"]["bid"]`) caused wrong strikes, wrong bids, and fabricated prices.

### Decision

Replace JSON chain input with pre-computed markdown tables. Python calculates all economics (Net Credit, Premium%, Ann.Ret%) before the agent sees the data. The agent's job is now *selection* from a sorted table, not *navigation and calculation* of a JSON tree.

#### Key Design Choices

1. **Table columns include all economics** — Net Credit, Premium%, Ann.Ret% are pre-computed so the agent never calculates
2. **CURRENT POSITION block** — Provides buyback cost and current contract details separately from the table
3. **Table is pre-sorted by Net Credit descending** — Agent reads top-down for best candidates
4. **VERIFICATION simplified** — From "state the full JSON path" to "cite the row number and values"
5. **All decision logic preserved** — Premium-First tiers, 45 DTE cap, delta constraints, earnings gates, CLOSE logic unchanged

### Files Changed

- `src/tv_open_call_roll_instructions.py` — Removed import, updated INPUT/VERIFICATION/SEARCH/examples
- `src/tv_open_put_roll_instructions.py` — Same changes, put-specific (Premium% = bid/strike, roll directions inverted)

### Note for Rusty

The instruction files no longer import `OPTIONS_CHAIN_SCHEMA_DESCRIPTION` from `options_chain_parser.py`. The new table format is injected by `agent_runner.py` (Rusty's domain). Instruction files just describe how to read it.

---

## 10. Decision: Reject bare "ROLL" at code level

**Author:** Rusty  
**Date:** 2026-07  
**Status:** Implemented

### Context
Phase 1 agents occasionally output `"action_needed": "ROLL"` without a direction suffix. This is ambiguous — Phase 2 needs a direction (DOWN/UP/OUT/etc.) to filter the options chain correctly. Running Phase 2 with bare ROLL means no direction filtering, leading to incorrect candidate sets.

### Decision
Validate action values in code, not just in prompts:

- **Phase 1 handoff:** `_try_extract_handoff_json()` now validates `action_needed` against `VALID_ROLL_ACTIONS`. Bare "ROLL" or unknown values → handoff rejected → treated as WAIT (Phase 2 does not run).
- **Phase 2 output:** After `_run_roll_management()`, bare "ROLL" activity → auto-corrected to "CLOSE" with reason annotation. Unknown activities → same treatment.
- **Degraded fallback:** Default in Phase 2 error handler changed from "ROLL" to "CLOSE".

### Rationale
- Prompt-only guardrails are insufficient — LLMs can still produce invalid values
- WAIT is the safe fallback for Phase 1 (no action taken, re-evaluated next cycle)
- CLOSE is the safe fallback for Phase 2 (if direction can't be determined, close the position rather than roll blindly)
- Constants (`VALID_ROLL_ACTIONS`, `VALID_PHASE2_ACTIVITIES`) are importable for use in tests and other modules

---

## 11. Decision: Pre-Computed Markdown Candidate Tables for Phase 2

**Date:** 2026-07-10
**Author:** Rusty (Agent Dev)
**Status:** Implemented
**Commit:** 6e7556f

### Context
Phase 2 roll management agent was receiving the filtered options chain as a raw JSON blob. Even after ±15 strike + delta + direction filtering, LLMs consistently misread bid/ask values, picked wrong strikes, and made arithmetic errors when navigating nested JSON.

### Decision
Pre-compute roll economics in Python and send Phase 2 a flat markdown table instead of JSON. The new `format_roll_candidates_table()` function in `options_chain_parser.py` computes buyback cost, net credit, DTE, premium%, and annualized return per candidate. The agent now picks from a numbered table — no JSON parsing, no arithmetic.

### Implications
- Phase 2 no longer needs `OPTIONS_CHAIN_SCHEMA_DESCRIPTION` — removed from both roll instruction files
- The `_run_roll_management()` message uses "ROLL CANDIDATES:" label instead of "OPTIONS CHAIN DATA:"
- Phase 2 instructions tell the agent to use pre-computed values directly and not recalculate
- The `underlying_price` for premium% calculation comes from `handoff_json.get("underlying_price")`
- Pipeline is now: ±15 strikes → delta → direction → candidate table

---

## 12. Decision: Debug Endpoint Underlying Price Source

**Date:** 2026-07  
**Author:** Rusty (Agent Dev)  
**Status:** Implemented  
**Impact:** Debug endpoint only (no production agent impact)

### Context

The debug endpoint needed the underlying stock price for the `format_roll_candidates_table()` call (used to compute premium_pct). In the real agent flow, this comes from the Phase 1 handoff JSON (`handoff_json.get("underlying_price")`), but in debug mode there's no Phase 1 agent.

### Decision

Source the underlying price from the cached **technicals** data (`cache.get(cache_key, "technicals")` → JSON → `price` field). This is the closing price from TradingView's scanner API, available whenever the technicals scheduler has run. Fallback to 0 with a `"not available"` note if cache is empty.

### Rationale

- The technicals cache is populated by the same scheduled fetcher, so it's available whenever options chain data is
- The `price` field is a clean float, no parsing needed
- Overview data is raw HTML text — extracting price would require fragile regex patterns
- Using 0 as fallback is safe: premium_pct and annualized return will show 0%, clearly indicating missing data

---

## 13. Decision: Direction-Aware Chain Filtering for Phase 2

**Author:** Rusty  
**Date:** 2026-07  
**Status:** Implemented  
**Commit:** 39096cc

### Context
Phase 2 (Roll Management) received ±15 strikes around the current position after delta filtering, but many strikes were irrelevant for the roll direction. For example, ROLL_DOWN for a call doesn't need strikes above the current strike. The LLM wasted context and sometimes picked impossible candidates.

### Decision
Added a third filtering stage (`filter_options_chain_by_roll_direction`) that narrows the chain based on Phase 1's roll type before passing to Phase 2. The filter applies both strike direction and expiration constraints per roll type. Unknown roll types pass through unchanged as a safe fallback.

### Key Design Choices
- **Structured dict stored pre-Phase-1**: Refactored `agent_runner.py` to keep the structured chain dict (not just serialized text) so direction filtering doesn't require re-parsing.
- **ROLL_OUT keeps ±1 adjacent strikes**: Not just the exact current strike, because a slightly different strike at a later date might be attractive.
- **"OUT" rolls use strictly later expirations**: Same expiration makes no sense for an "out" roll.
- **Puts and calls use the same direction logic**: ROLL_DOWN means lower strikes regardless of option type. The direction semantics are inherent to the roll name.

### Filter Pipeline
```
±15 strikes → delta range → roll direction
```

---

## 14. Decision: Auto-convert incomplete ROLL actions to CLOSE

**Author:** Rusty  
**Date:** 2026-07  
**Status:** Implemented  
**Commit:** 2086e07

### Context
Phase 2 agents sometimes output a ROLL type (e.g., ROLL_UP_AND_OUT) without selecting a specific candidate — `new_strike` and `new_expiration` are left null. This makes the activity unexecutable.

### Decision
Incomplete ROLL actions (missing `new_strike`, `new_expiration`, or `roll_economics`) are auto-converted to CLOSE with an audit trail appended to the reason field. This is consistent with the existing bare-ROLL → CLOSE conversion pattern.

### Rationale
- A ROLL without a target is worse than useless — it implies an action was chosen but can't be executed
- Converting to CLOSE is the safest fallback: it flags the position for manual review
- The audit trail in `reason` preserves what the agent originally recommended for debugging
- Instruction-level hardening reduces the frequency of this happening, but code validation is the safety net

---

## 15. Decision: User Directive — ROLL Action Format

**Date:** 2026-04-23  
**By:** dsanchor (via Copilot)  
**Status:** Implemented  

### Directive

(1) "ROLL" alone is never a valid action — must always include direction: ROLL_DOWN, ROLL_UP, ROLL_OUT, ROLL_UP_AND_OUT, ROLL_DOWN_AND_OUT. Valid actions are: WAIT, CLOSE, ROLL_DOWN, ROLL_UP, ROLL_OUT, ROLL_UP_AND_OUT, ROLL_DOWN_AND_OUT.

(2) ROLL_OUT should not be recommended if the position would be a CLOSE candidate on the next monitoring cycle — keep actions objective and consistent.

### Reason

User request — captured for team memory. Prevents bare ROLL output and unnecessary interim rolls that just delay inevitable closes.

---

## 16. Decision: User Directive — ITM Stability Buffer

**Date:** 2026-04-23  
**By:** David (via Copilot)  
**Status:** Implemented  

### Directive

When a position is slightly ITM (near ATM), the agent should NOT automatically recommend ROLL/CLOSE. If technicals (trends, sentiment, MAs) are still favorable, it may be a temporary move. Add a stability margin so the agent WAITs in these cases instead of flip-flopping between ROLL and WAIT on consecutive runs. Only trigger ROLL when clearly ITM beyond a margin, OR when technicals confirm the move is sustained. This applies when the position is still close to ATM.

### Reason

User request — prevents oscillating recommendations that create noise without improving outcomes. Implemented in linus-stability-buffer decision.

---

## 17. User Directive — Mobile UI Horizontal Scrolling

**Date:** 2026-05-01T14:48Z  
**By:** dsanchor (via Copilot)  
**Status:** Team awareness  

### Directive

No horizontal scrollers anywhere in the mobile UI. Tables and data must be reformatted/stacked for small screens, not overflow-x scrolled.

### Reason

User request — captured for team memory. Ensures better mobile UX with responsive stacking instead of horizontal scroll friction.

---

## 18. User Directive — English-Only UI

**Date:** 2026-04-18T08:43:10Z  
**By:** David Sancho (via Copilot)  
**Status:** Team awareness  

### Directive

Always use English in the app UI. No Spanish text in user-facing strings.

### Reason

User request — captured for team memory. Maintains consistency with English as primary language.

---

## 19. Decision: Contrarian Agent Architecture (Propuesta)

**Date:** 2026-07-17  
**Author:** Danny (Lead)  
**Status:** Implemented (Option A adopted)  
**Impact:** Pipeline automation with selective triggering

### Architecture Summary

The contrarian agent runs as a **post-write enrichment step** in both `run_symbol_agent()` and `run_position_monitor()`. It activates only on alert decisions (`is_alert=True`), never on routine WAITs, to balance signal value against LLM cost and analysis paralysis.

### Key Activation Criteria

- **ROLL decisions (UP/DOWN/OUT):** Direct economic consequences warrant second opinion
- **SELL in watchlists:** Timing/IV concerns merit challenge
- **Prolonged WAITs (5+ cycles):** Pattern detection catches capital efficiency blind spots
- **NOT:** Obvious WAITs (deep OTM, 30+ DTE), post-crisis CLOSEs, or routine monitoring

### Implementation Pattern (Option A: Pipeline Automático)

```
Monitor → Activity JSON → [is_alert=true?] → Contrarian Agent → Enriched Activity (contrarian_view field)
```

Activity persisted FIRST, then contrarian enrichment applied via `update_activity_field()`. Graceful failure everywhere — contrarian errors do not crash the pipeline.

### Telegram Integration

- MODERATE and STRONG challenges trigger push notifications with brief summary
- WEAK challenges stored in CosmosDB for dashboard review only
- Format: "⚡ Contrarian: [one_liner]"

---

## 20. Decision: Contrarian Instructions Design

**Date:** 2026-07-18  
**Author:** Linus (Quant Dev)  
**Status:** Implemented  
**Impact:** `src/tv_contrarian_instructions.py` (new)

### Design Decisions

1. **Parameterized function:** `get_contrarian_instructions(agent_type, decision_type)` returns customized prompt. Enables different playbooks for WAIT vs ROLL vs SELL, different context for call vs put agents.

2. **Fail-fast validation:** Invalid agent_type/decision_type combos raise `ValueError` immediately. Prevents nonsensical prompts reaching LLM.

3. **Nine decision playbooks by type, not per-agent:** Counter-arguments for "ROLL_DOWN" are structurally identical for calls vs puts; context injection adds agent-specific framing. Keeps playbooks DRY.

4. **CONTRARIAN_OUTPUT_SCHEMA exported:** JSON Schema dict importable by `agent_runner.py`. Output format: `challenge_strength` (WEAK/MODERATE/STRONG), `counter_arguments[]`, `net_assessment`, `one_liner`.

### Interface for Rusty

```python
from src.tv_contrarian_instructions import get_contrarian_instructions, CONTRARIAN_OUTPUT_SCHEMA

# Get parameterized prompt
prompt = get_contrarian_instructions("open_call", "ROLL_UP_AND_OUT")

# Parse response against schema
# Handle ValueError if combo is invalid
```

---

## 21. Decision: Contrarian Agent Pipeline Integration (MVP)

**Date:** 2026-07-17  
**Author:** Rusty (Agent Dev)  
**Status:** Implemented  
**Implements:** Danny's contrarian architecture (Option A)

### Implementation Choices

1. **Post-write pattern:** Activity persisted FIRST, then contrarian runs. If contrarian fails, original activity untouched. `contrarian_view` patched via `update_activity_field()`.

2. **Same client, separate agent:** Reuses `AzureOpenAIChatClient` but creates new `ChatAgent` instance per review. Avoids conversation contamination.

3. **Telegram noise filtering:** Only MODERATE and STRONG challenges in push notifications. WEAK challenges stored for dashboard only.

4. **Graceful failure everywhere:** `_run_contrarian_review()` wraps in try/except → returns None. `update_activity_field()` returns bool. Neither crashes pipeline.

### Files Changed

- `src/agent_runner.py` — contrarian method + pipeline integration
- `src/cosmos_db.py` — `update_activity_field()` method
- `src/telegram_notifier.py` — contrarian line in sell + roll alerts

---

## 22. Decision: Prolonged WAIT Detection

**Date:** 2026-07-16  
**Author:** Rusty (Agent Dev)  
**Status:** Implemented  

### Context

Contrarian agent only ran on alert decisions (SELL, ROLL_*, CLOSE). Normal WAITs were never challenged. But when a position sits idle for 5+ consecutive cycles with nothing but WAIT, capital-efficiency blind spots emerge: theta decay stagnation, opportunity cost, changing market conditions.

### Detection Logic

Added `_detect_prolonged_wait()` to `AgentRunner` — checks if last N activities (default 5) are ALL non-alert WAITs with no errors. Integrates into both `run_symbol_agent()` and `run_position_monitor()`.

### Telegram Notification

Added `send_prolonged_wait_alert()` to `TelegramNotifier` — dedicated format with ⏳ prefix, only fires for MODERATE/STRONG contrarian challenges. Threshold is class constant `PROLONGED_WAIT_THRESHOLD = 5`, easily tunable.

### Safety Constraints

- Detection NEVER blocks pipeline — wrapped in try/except, returns False on error
- Uses `include_alerts=True` when fetching activities so any real alert disqualifies prolonged WAIT
- Error activities also disqualify (checked via `act.get("error")`)

---

## 23. DGI Screener: Top 40 + Interactive Filters

**Date:** 2026-05-10  
**Author:** Linus (Quant Dev)  
**Status:** Implemented

### Decision

Expanded DGI screener from top 20 to top 40 stocks and added client-side interactive slider filters.

### Context

- User requested increasing the screened stock count to provide more investment opportunities
- Filtering capability was needed to let users narrow down the expanded list based on key metrics
- Client-side filtering was preferred to avoid additional API calls and provide instant feedback

### Implementation

#### Part 1: Top 20 → Top 40
- Changed default `top_n` from 20 to 40 in `src/dgi_screener.py`
- Updated UI subtitle in `web/templates/dgi_screener.html`
- **Preserved backward compatibility**: Cosmos document IDs still use `top20_*` prefix to avoid orphaning existing docs

#### Part 2: Interactive Filters
- Added collapsible filter panel above the table with 5 range sliders (0-100 scale):
  - **Quality Score ≥**: Direct filter on `entry.quality_score`
  - **Div Yield ≥**: Slider/10 maps to 0%-10%+ filter on `metrics.dividend_yield`
  - **Div Growth ≥**: Slider maps to 0%-100% CAGR filter on `metrics.dividend_cagr_5y * 100`
  - **Years ≥**: Direct filter on `metrics.years_consecutive_increases`
  - **Timing ≥**: Direct filter on `technicals.score`

- **Client-side filtering**: Leverages existing `data-entry='{{ entry | tojson | e }}'` attributes on table rows
- **Real-time updates**: `oninput` events trigger filter recalculation instantly
- **Dynamic count**: "Showing X of Y stocks" updates as sliders move
- **Sorting compatibility**: Sorting maintains filter state by preserving row display property
- **All features preserved**: Detail modal, ▶ (analyze), ➕ (add to watchlist) work on filtered rows

#### CSS Styling
- Added `.range-slider` styling for dark theme consistency
- WebKit + Firefox compatible
- Uses existing CSS variables (`--accent-blue`, `--border`, etc.)
- Hover effects for better UX (thumb scale + color change)

### Rationale

1. **Backward compatibility**: Kept doc IDs unchanged because changing them would orphan existing Cosmos documents
2. **Client-side filtering**: No server round-trips = instant response, better UX
3. **Data reuse**: Leveraged existing `data-entry` JSON attributes instead of duplicating data
4. **Collapsible panel**: Keeps UI clean when filters aren't needed
5. **Real-time feedback**: Slider `oninput` events provide immediate visual feedback

### Trade-offs

- **Variable names still say `top20`**: Could rename, but it's purely cosmetic and would touch many places for no functional benefit
- **Doc IDs still say `top20_*`**: Intentionally preserved for backward compatibility — changing would break existing Cosmos references
- **Client-side only**: Filters don't persist across page reloads (but this matches user expectations for exploratory filtering)

### Files Modified

- `src/dgi_screener.py` — Changed `top_n` default
- `web/templates/dgi_screener.html` — Added filter panel + JavaScript filtering logic
- `web/static/style.css` — Added range slider styling

### Pattern for Future

**Client-side filtering with JSON data attributes** is a powerful pattern when:
- Dataset is small enough to send to client (< 100 rows)
- Filters are exploratory (don't need persistence)
- Real-time feedback is valuable
- Existing rows already have structured data in attributes

This avoids the complexity of server-side filtering APIs while providing excellent UX.

---

## 24. Decision: Normalize exchange codes at the Python source

**Author:** Linus (Quant Dev)  
**Date:** 2026-05-10  
**Status:** Implemented  

### Context
yfinance returns internal exchange codes (NYQ, NMS, NGM, PCX, BTS, etc.) that don't match TradingView market names. The JS template had a band-aid `marketMap` to translate these, but the ➕ (add to watchlist) button still had `data-exchange="NYSE"` hardcoded.

### Decision
Normalize exchange codes in `src/dgi_screener.py` via an `EXCHANGE_MAP` dict applied when building the metrics dict. This means all downstream consumers (Cosmos docs, templates, chat redirects, watchlist adds) automatically get correct TradingView-compatible exchange names.

### Consequences
- **Template simplification**: JS-side mapping removed; both ▶ and ➕ buttons now use the already-normalized `entry.exchange` value.
- **Backward compatible**: Unknown exchange codes pass through as-is; empty codes default to "NYSE".
- **Existing Cosmos docs**: Will be updated on next screener run. Until then, old docs may still have raw yfinance codes.
- **If new exchanges appear**: Just add them to `EXCHANGE_MAP` in one place.

---

## 25. Decision: DGI `top_n` exposed in Settings UI

**Author:** Linus (Quant Dev)  
**Date:** 2026-05-10  

### Context
The DGI screener's `top_n` parameter (how many top-ranked stocks to keep) was hardcoded as a default of 40 with no UI to change it. User requested it be configurable from the Settings page.

### Decision
- Added a numeric input ("Number of stocks in Top list") to the DGI Screener section of Settings → Configuration
- Value is persisted to both CosmosDB and config.yaml, following the existing dual-write pattern
- Validated/clamped to 1–500 on the server side, defaults to 40 on invalid input
- No changes to `dgi_screener.py` — it already reads `dgi_config.get("top_n", 40)`

### Files Changed
- `web/templates/settings_config.html` — new numeric input field
- `web/app.py` — GET handler (pass `dgi_top_n` to template), POST handler (parse, validate, save)

### Rationale
Follows the same pattern as `summary_activity_count`: numeric input with server-side clamping. Keeps the default at 40 so existing deployments are unaffected.


---

## 26. Decision: Recommendation values computed from signal ratios

**Author:** Linus (Quant Dev)  
**Date:** 2026-05-14  
**Status:** Implemented  

### Context
TradingView's scanner API provided pre-computed `Recommend.All`, `Recommend.Other`, `Recommend.MA` fields (normalized to [-1, 1]). yfinance has no equivalent.

### Decision
Compute recommendation values as `(buy_count - sell_count) / total_count` for each group (overall, oscillators, MAs). This produces the same [-1, 1] range and feeds the same `_tech_recommendation_label()` thresholds (≥0.5 = Strong Buy, >0.1 = Buy, etc.).

### Consequences
Slight deviation from TradingView's exact weighting (which may have used proprietary signal weights), but same label thresholds apply and agents consume labels not raw values.

---

## 27. Decision: No pandas-ta hard requirement

**Author:** Linus (Quant Dev)  
**Date:** 2026-05-14  
**Status:** Implemented  

### Context
pandas-ta is excellent but can have install issues on some platforms (C extensions).

### Decision
TechnicalsCalculator has full manual fallback using only pandas + numpy (always available). pandas-ta is tried first for cleaner code and potential performance, but the manual path produces identical output.

### Consequences
- **Reliability**: Works on all platforms without binary dependencies
- **Performance**: pandas-ta path still available for users who have it installed
- **Maintenance**: One code path to maintain (manual) vs. conditional logic

---

## 28. Decision: Options chain DTE window is configurable

**Author:** Linus (Quant Dev)  
**Date:** 2026-05-14  
**Status:** Implemented  

### Context
Different strategies need different time horizons. Covered calls typically target 30-45 DTE, but agents may want to see wider range.

### Decision
Default 7-90 DTE window, configurable via `config={"min_dte": 7, "max_dte": 90}` passed to `create_provider()`. Agents don't need 6-month or 1-year LEAPS chains for weekly sell signals.

### Consequences
- **Flexibility**: Agents can tailor expiration horizons per strategy
- **Performance**: Smaller chains (fewer options to analyze)
- **Default behavior**: 7-90 DTE covers most standard strategies without config

---

## 29. Decision: dividendYield handling

**Author:** Linus (Quant Dev)  
**Date:** 2026-05-14  
**Status:** Implemented  

### Context
yfinance returns dividendYield in percentage form (0.88 = 0.88%, not 88%). This is a known gotcha documented in project memory.

### Decision
Store as-is in the output (matching TV format where `dividends_yield` was already percentage-form, e.g. 0.88%). The agents expect percentage display values. No division by 100 in the output — only internally if we ever need decimal form for calculations.

### Consequences
- **Format consistency**: Matches TradingView API format
- **Agent simplicity**: Agents receive display-ready values
- **Calculation safety**: If Greeks calculator needs decimal form, convert locally

---

## 30. Decision: Market Hours Detection — Live Options Probe vs. Calendar Rules

**Author:** Linus (Quant Dev)  
**Date:** 2026-05-14  
**Status:** ✅ Implemented  

### Context
The original `src/market_hours.py` used rule-based detection:
- Fixed calendar (9:30–16:00 EST, Mon–Fri)
- Holiday table (10 NYSE holidays)
- Timezone conversions via `pytz`

This approach couldn't handle half-days, unexpected closures, or timezone edge cases reliably.

### Problem
Half-days are not consistent year to year. Unexpected market closures (e.g., weather events) are unpredictable. Calendar maintenance becomes a burden, and the detection is reactive rather than observational.

### Decision
Replace `is_us_market_open()` with a **live probe** that checks MSFT ATM call bid/ask via yfinance:
1. Fetch MSFT nearest-expiration call chain: `yf.Ticker("MSFT").option_chain()`
2. Find ATM call (closest strike to current price)
3. If bid > 0 OR ask > 0 → **OPEN**; both 0/None → **CLOSED**
4. Cache result for 5 minutes (monotonic clock) to limit API calls
5. On any exception → conservative fallback to **CLOSED**

### Key Design Choices
- **Observable signal**: yfinance returns zeroed bid/ask when market is closed — direct, unambiguous indicator
- **No new dependencies**: Already using yfinance throughout the system
- **Network cost mitigated**: 5-minute cache reduces overhead; app already makes yfinance calls
- **Conservative fallback**: On error, assume market is closed (safer for agent scheduling)

### Tradeoffs
- **Network dependency**: Old approach was pure calculation. New approach requires ~1–2s network latency on cache miss.
- **yfinance availability**: System already depends on yfinance; this doesn't add new risk.
- **Latency**: ~1–2s on cache miss is acceptable given 5-minute TTL and existing yfinance calls in the pipeline.

### Consequences
- Eliminates holiday table maintenance
- Handles half-days and closures automatically
- Simplifies code (no `pytz`, no complex logic)
- Can be deployed immediately with zero configuration

### Files Changed
- `src/market_hours.py` — fully replaced

### No Changes Required
- `src/yfinance_data_provider.py` — same import, same function signature
- Agent instructions — no logic changes needed
- Framework (Rusty) — no framework changes needed

---

## 31. Decision: Options Chain Merge Strategy — Preserve yfinance Cache During Market Closure

**Author:** Linus (Quant Dev)  
**User Directive:** dsanchor (2026-05-14T19:24)  
**Date:** 2026-05-14  
**Status:** ✅ Implemented  

### Context
yfinance provides the full options chain during market open. When market closes, yfinance returns zeroed bid/ask/IV/volume (Decision 30 uses this to detect closure). The TradingView Playwright fallback (see Decision: Hybrid Options Chain, 2026-07) scrapes ~5 nearest expirations when market is closed, but we lose access to the 6th, 7th, etc. longer-dated contracts that were available during the open.

### Problem
Agents analyzing stale-but-useful longer-dated expirations during closed hours lose data. Example: analyzing a 60-DTE covered call candidate at 22:00 when market has closed — we can't see the 60 DTE strike data even though we fetched it 6 hours earlier at market close.

### User Directive (2026-05-14T19:24 via Copilot)
> "When market is closed and TradingView Playwright fallback is used for options chains, only overwrite the expiration dates that TradingView provides (typically 5 nearest). Keep any additional expirations (6th, 7th, etc.) that were previously fetched from yfinance during market open hours. This preserves stale-but-useful data beyond the 5 expirations TradingView covers."

### Decision
Implement an in-memory merge strategy in `src/yfinance_data_provider.py`:

**On Market Open (yfinance succeeds):**
- Store a deepcopy of the successful options chain in a module-level `_chain_cache[symbol]`
- Overwrites previous session's cache

**On Market Close (yfinance returns zeros, TV fallback used):**
1. Retrieve cached yfinance chain (if exists)
2. Start merge with full cached dict (all expirations)
3. Overwrite only the expirations that TradingView scraped (typically 5 nearest)
4. Keep all other expirations from cache untouched
5. If no cache exists (cold start during closed market), use TV data as-is (no regression)

### Implementation
- **Module-level cache**: `_chain_cache = {}` dict persisting for app lifetime
- **Cache key**: symbol (e.g., `_chain_cache["AAPL"]`)
- **Cache value**: deepcopy of options chain dict (strike-keyed)
- **On merge**: Start with cached dict, then `update()` with TV data for overlapping expirations
- **Output format unchanged**: Both paths (yfinance + TV + merge) produce identical strike-keyed dict

### Tradeoffs
- **In-memory only**: Cache is lost on app restart. Acceptable — next market-open fetch repopulates it immediately.
- **Stale far-dated data**: Cached expirations beyond TV's scrape range may have 1–6+ hour stale prices. Trade-off accepted: stale data > no data for strategy evaluation.
- **No TTL**: Cache doesn't expire by time; only overwrites on next market-open successful fetch. Staleness is bounded to one trading day (market open → market close).
- **Chain format**: Optional `market_status` field ("open"/"closed") allows consumers to detect which path was taken, but no logic changes required.

### Consequences
- **Agents**: Access to stale-but-useful 6th+ expirations during market closure. No instruction changes needed — chain format identical.
- **Rusty (Framework)**: No framework changes. Data provider is self-contained.
- **Deployment**: Adds zero new dependencies (deepcopy is stdlib).

### Files Changed
- `src/yfinance_data_provider.py`:
  - Added module-level `_chain_cache` dict
  - Modified `_build_options_chain()` to cache on success and merge on TV fallback

### Related Decisions
- Decision 30: Market Hours Detection (signals when cache should be applied)
- Decision: Hybrid Options Chain (context for TV fallback)


---

## Rusty — Snapshot Chart Decision

**Date:** 2026-06-04  
**Author:** Rusty (Agent Dev)  
**Status:** Implemented

### Decision
Use a lazy-loaded position snapshot chart in `symbol_detail.html` backed by a dedicated per-position API endpoint that returns snapshots in chronological order.

### Why
- Active position drawers can stay lightweight on initial page render.
- Chart.js time scale preserves irregular intraday spacing from monitoring snapshots.
- Reversing backend data once at the API boundary keeps chart code simple and consistent.

### Implementation Notes
- Endpoint: `GET /api/symbols/{symbol}/positions/{position_id}/snapshots?limit=...`
- Fetch only on first expand for each position row.
- Datasets: Gap % on left axis, RSI + MACD on right axis.

### Files Changed
- `web/app.py` — Added snapshot API endpoint
- `web/templates/symbol_detail.html` — Integrated Chart.js with lazy expand trigger
- `web/templates/base.html` — Added Chart.js CDN reference

### Integration
The snapshot chart consumes data from Linus's `position_snapshots` CosmosDB container via the API boundary, following the documented position snapshot schema and retention model.

---

## Rusty — DPS Scheduler Integration

**Date:** 2026-06-26  
**Author:** Rusty (Agent Dev)  
**Status:** ✅ Implemented  
**Impact:** Bug fix — critical missing scheduler

### Context

The DPS (Deterministic Position Scorer) was fully implemented with:
- Scoring logic in `src/dps_scorer.py` (992 lines)
- Cron wrapper in `src/dps_cron.py` (173 lines)
- Config entry in `config.yaml`: `dps_scorer.cron: "0 22 * * 1-5"` (nightly 10 PM)

But it was **never wired into the scheduler** in `src/main.py`. The task existed in config, the code existed, but it never ran.

### Problem

Active option positions were not receiving DPS scores in their snapshots. The nightly DPS job (configured to run at 10 PM weekdays) never executed because `src/main.py` had no DPS scheduler block.

### Decision

Integrate DPS scheduler into the main scheduler loop, following the existing pattern used by the other 8 scheduled tasks.

### Implementation

**Files Changed:**
- `src/main.py` — 9 edits, ~60 lines added

**Changes:**
1. Added `_dps_cron_changed` flag to `__init__` (line 89)
2. Added `reschedule_dps(new_cron)` method (lines 135-140)
3. Added DPS config logging in `setup()` (lines 277-283)
4. Added `run_dps_job()` + `_run_dps_async()` methods (lines 478-503)
5. Added DPS config reload logic in `_reload_config_from_cosmos()` (lines 789-809)
6. Added DPS cron initialization in `run()` (lines 883-892)
7. Added DPS to initial schedule display (lines 993-995)
8. Added DPS cron change handler (lines 1207-1219)
9. Added DPS execution block in main loop (lines 1228-1235)

**Pattern Followed:**
Mirrored the structure of `portfolio_enrichment` scheduler (8th task) to ensure consistency.

### Impact

**Before:** DPS never ran, position snapshots missing DPS scores.  
**After:** DPS runs nightly at 10 PM (UTC, configurable), position snapshots receive DPS scores.

**No Breaking Changes:** Purely additive — existing tasks unaffected.

### Validation

- ✅ Import test: `python3 -c "from src import main"` — successful
- ✅ Method exists: `run_dps_job()` confirmed at line 478
- ✅ Scheduler blocks: DPS config, initialization, reload, execution all present

### Alternatives Considered

None — this was a bug fix, not a design choice. The only alternative was to remove the orphaned config/code, but DPS is a valuable feature.

### Lessons Learned

**Risk:** Config entries without scheduler wiring can go unnoticed.  
**Prevention:** Grep for `cron` in config.yaml and cross-reference with `src/main.py` scheduler blocks.

### Related Work

See `scheduler_analysis.md` for full scheduler architecture documentation and deferred improvement recommendations.

---

## 6. Scheduler Registry Refactor + DPS Redundancy Removal

**Date:** 2026-06-26  
**Decider:** Rusty (Agent Dev)  
**Status:** ✅ Implemented

### Summary

Refactored the Options Agent Scheduler from 1266 lines to 736 lines (41% reduction) by:
1. **Removed redundant DPS task:** Monitoring agents already compute DPS scores in real-time every 4 hours; the nightly batch job was redundant.
2. **Created TaskRegistry:** Single source of truth for task definitions, reducing new-task integration from 50+ lines across 11 touch-points to 2 lines.
3. **Removed 530 lines of boilerplate:** Per-task cron flags, reschedule methods, config reload, initialization, execution blocks, display logic.

**Result:** 9 tasks → 8 tasks. Task count reduction: 1 (DPS). Code reduction: -345 lines net (1266 → 921 with new registry module).

### Key Decisions

1. **DPS Scorer Removal Rationale:**
   - Monitor agents (`covered_call`, `cash_secured_put`, `buy_tracker`, `open_call_monitor`, `open_put_monitor`) invoke `run_dps_analysis()` after every position snapshot
   - They run every 4 hours during market hours (`30 9-16/4 * * 1-5`), providing fresh DPS scores 4x/day
   - Nightly batch DPS job (`dps_cron.py`) ran the same logic only once daily at 10 PM — stale data
   - **No value added** — pure redundancy

2. **Registry Pattern Benefits:**
   - Single source of truth for all task metadata (name, display_name, config_key, default_cron, job_func, enabled, cron_obj, next_run)
   - Centralized config reload detection and cron change handling
   - Consistent error isolation and logging
   - Preserve web UI reschedule capability

### Implementation

**New file: `src/scheduler_registry.py` (185 lines)**
- `ScheduledTask` dataclass: task definition + cron + enabled state
- `TaskRegistry` class: register, initialize_all, reload_from_cosmos, handle_cron_changes, execute_due_tasks, display_schedule, reschedule

**Refactored: `src/main.py` (1266 → 736 lines)**
- Replaced 9 `_X_cron_changed` flags → `registry = TaskRegistry()`
- Replaced 200+ lines per-task config reload → `registry.reload_from_cosmos()`
- Replaced 100+ lines cron initialization → `registry.initialize_all()`
- Replaced 120+ lines cron change handlers → `registry.handle_cron_changes()`
- Replaced 80+ lines execution if-blocks → `registry.execute_due_tasks()`
- Replaced 30+ lines display → `registry.display_schedule()`

**Web UI compatibility:** All 8 `reschedule_X()` methods still exist; they delegate to registry.

### Validation

✅ Import test succeeds  
✅ All 8 reschedule_X() methods callable  
✅ Task count verified: 8 tasks with correct crons  
✅ Existing tests pass (4 economics failures pre-existing)  
✅ Behavior preserved: same task set (minus DPS), same crons, same execution logic

### Impact

**Positive:**
- 41% smaller main.py → easier maintenance
- 2 lines per new task vs 50+ lines → 96% effort reduction
- No behavior change (same 8 tasks, same crons)

**Risks Mitigated:**
- Web UI compatibility preserved
- No breaking changes
- Rollback available

---

## 7. Unified Scheduler Settings UI Model

**Date:** 2026-06-26  
**Agent:** Rusty (Agent Dev)  
**Status:** ✅ Implemented  
**Impact:** High — eliminates per-task duplication, consistent UI for all scheduled tasks

### Context

Web UI scheduler configuration was inconsistent across 8 tasks:
- Some tasks had enabled checkboxes, some didn't
- Last run / next run timestamps duplicated ad-hoc (8 duplicated blocks)
- Manual "Run Now" triggers only for some tasks
- ~150 lines of duplicated croniter logic in context builder

**Requirement:** Unify all tasks to have: enabled checkbox, cron expression, last run, next run, Run Now button.

### Solution

**Make TaskRegistry the single source of truth for per-task UI metadata:**

1. **Registry Extensions:**
   - `last_run: Optional[datetime]` — recorded on every execution (cron + manual)
   - `has_extra_config: bool` — flag for task-specific config beyond 5 standard fields
   - `get_all_task_metadata()` — returns uniform dict for all tasks
   - `trigger_task_now(name)` — manual run, records last_run
   - `update_task_enabled(name, enabled, config)` — toggle enabled state, persist

2. **Unified Endpoints (web/app.py):**
   - `GET /api/scheduler/tasks` — all task metadata
   - `POST /api/scheduler/tasks/{name}/run` — manual trigger
   - `POST /api/scheduler/tasks/{name}/cron` — update cron
   - `POST /api/scheduler/tasks/{name}/enabled` — toggle enabled state

3. **Eliminated Duplication:**
   - Removed ~150 lines from `_build_settings_config_context()` in web/app.py
   - Single source from registry instead of 8 duplicated blocks
   - Backward-compatible template variables preserved for incremental migration

### Results

**Every scheduled task (8 total) now uniformly exposes:**
1. Enabled checkbox — gates execution
2. Cron expression field — editable, live-reschedule
3. Last run timestamp — audit trail
4. Next run timestamp — visibility
5. Run Now button — manual override

**Plus:** Tasks with extra config (summary_agent, dgi_screener, banner_agent) retain task-specific fields.

### Validation

✅ Import checks pass  
✅ Tests pass (4 economics failures pre-existing)  
✅ 4 new endpoints registered  
✅ Registry methods callable: get_all_task_metadata(), trigger_task_now(), update_task_enabled()

### Lessons Learned

1. **Single source of truth eliminates divergence bugs** — before, last_run/next_run could diverge between scheduler and UI
2. **Uniform UX requires uniform data model** — can't have consistent controls if backend is inconsistent
3. **Backward compatibility eases incremental refactors** — preserved old endpoints, can refactor template in separate phase

---

## 8. Monitoring Agent Enabled Checkbox + Enable-Gating Guarantee

**Date:** 2026-06-26  
**Author:** Rusty (Agent Dev)  
**Status:** ✅ Implemented

### Context

After registry refactor, 7 of 8 tasks had all 5 standard controls. Monitoring Agent was missing the enabled checkbox — an oversight from initial refactor where monitoring used legacy `config_key="scheduler"` instead of task-specific key.

**Gap:** Users could enable/disable 7 tasks via UI but not monitoring (the CORE task).

### Solution

**End-to-end enabled checkbox for Monitoring Agent:**

1. **Template (web/templates/settings_config.html):** Added checkbox (line 48) matching other 7 tasks
2. **Backend Context (web/app.py):** Line 2891: `monitoring_enabled = monitoring.get("enabled", True)` from registry
3. **POST Handler (web/app.py):** Reads checkbox, persists to CosmosDB + config.yaml
4. **Enable-Gating (src/scheduler_registry.py):** Line 165 — execution guard: `if task.enabled and ...`
5. **Registry Reload:** Line 158 — immediate task.enabled refresh when CosmosDB settings reload

### Decision Pattern

**UNIFORM CONTROL RULE:** All 8 scheduled tasks MUST expose:
1. Enabled checkbox (gates execution)
2. Cron expression (schedule)
3. Last run timestamp (audit)
4. Next run timestamp (visibility)
5. Run Now button (manual trigger)

Tasks with extra config have additional fields IN ADDITION to these 5.

**Enable-Gating Guarantee:** Disabled task WILL NOT execute, checked at execution time (line 165), refreshed every 60s from CosmosDB.

### Validation

✅ Import checks pass  
✅ Template renders monitoring_enabled checkbox  
✅ Backend provides monitoring_enabled in context  
✅ POST handler persists monitoring_enabled  
✅ Enable-gating verified: disabled tasks skip execution (line 165)

### Impact

- **User Impact:** Monitoring now has same controls as other 7 tasks
- **Behavior:** Defaults to enabled (no breaking change)
- **Technical Debt:** Closes scheduler registry refactor gap
- **Future:** All new tasks MUST include 5 standard controls


### 8. Scheduler Last Run Display + Restart-Durable Timestamps
**Date:** 2026-06-26  
**Author:** Rusty (Agent Dev)  
**Status:** ✅ Implemented  
**Scope:** Scheduler settings UI, last_run persistence  

#### Context

After the scheduler refactor (16dcbec — task-registry architecture), the settings UI displayed 8 scheduled tasks with Next Run but only 5 had Last Run. Three tasks (Calendar Sync, DGI Screener, Watchlist Enrichment) were missing the Last Run display entirely.

Additionally, the TaskRegistry tracked `last_run` in-memory only (`task.last_run = now_tz` on execution). This meant that after a scheduler restart (deployment, config reload, crash), all `last_run` values reset to `None` → UI showed "Never" even for tasks that had recently executed.

The pre-refactor code (before 16dcbec) derived `last_run` from persisted Cosmos timestamps (activities, agent_notes, dgi_entries, banner doc, etc.), so the UI showed accurate "Last Run" even after restarts.

#### Problem

1. **Missing Last Run Display:**
   - Calendar Sync, DGI Screener, Watchlist Enrichment sections lacked Last Run rows
   - Only showed Next Run (single column) instead of the standard Last Run + Next Run grid

2. **Missing Context Variables:**
   - `web/app.py` didn't build `calendar_last_run` or `pe_last_run` for the template
   - DGI had `dgi_last_run` built but template never rendered it

3. **In-Memory Only last_run (Not Restart-Durable):**
   - TaskRegistry tracked `last_run` in-memory only
   - After scheduler restart, all `last_run` reset to `None` → UI showed "Never"
   - Lost the pre-refactor behavior where `last_run` was derived from persisted Cosmos data

#### Decision

**Restore uniform Last Run display for all 8 scheduler tasks AND make last_run restart-durable by falling back to persisted Cosmos timestamps.**

#### Implementation

**Template Updates (web/templates/settings_config.html):**
- Added Last Run display rows to 3 missing sections (Calendar Sync, DGI Screener, Watchlist Enrichment)
- All 8 sections now have uniform 2-column layout (Last Run + Next Run)

**Context Variables (web/app.py):**
- Added `calendar_last_run` and `pe_last_run` vars
- Both added to template context dict

**Restart-Durable last_run (web/app.py:2878-2972):**
- Created `get_persisted_last_run(task_name: str) -> str` helper
  - Queries Cosmos for task-specific "most recent execution" timestamp
  - Per-task sources: activities, agent_notes, dgi_entries, banner doc, calendar events, symbol updates
- Created `resolve_last_run(task_name: str, in_memory_last_run: str) -> str` helper
  - Prefers in-memory value if present
  - Falls back to `get_persisted_last_run()` when `None`
- Updated all 8 task context vars to use `resolve_last_run()` instead of direct `fmt_time()`

#### Rationale

**Why Restart-Durable Matters:** Scheduler may restart (deployments, config reloads, crashes). Persisted timestamps let UI show accurate "Last Run" even after restart.

**Why Per-Task Cosmos Sources:** Each task has a natural "most recent execution" signal already in Cosmos. Reusing existing timestamps is cleaner than adding new `last_execution_timestamp` fields to every task.

**Why Options Chain is In-Memory Only:** Cache is transient, task runs hourly, so "Never" after restart reflects reality (cache empty, task needs to run).

#### Alternatives Considered

1. **Add `last_execution_timestamp` field to every task's Cosmos output** → Adds storage overhead, duplicates existing data
2. **Persist last_run in dedicated `scheduler_state` Cosmos doc** → Extra Cosmos write per execution, doesn't help options_chain
3. **Leave last_run in-memory only (status quo)** → UI shows "Never" after restart (regression)

**Chosen:** Per-task Cosmos sources. Balances simplicity, no schema changes, leverages existing timestamps.

#### Impact

**User-Facing:**
- Scheduler Settings UI now shows Last Run + Next Run for ALL 8 tasks uniformly
- Last Run survives scheduler restarts (accurate even after deployments)
- Users can trust "Never" means "truly never run" (not "scheduler restarted")

**Code:**
- **web/app.py**: +100 lines (helper functions, per-task resolution)
- **web/templates/settings_config.html**: +24 lines (3 Last Run rows)
- No schema changes, no new Cosmos writes

**Validation:**
- ✅ Imports succeed
- ✅ 98 tests pass (4 pre-existing economics failures unrelated)
- ✅ Template: 8 "Last Run" labels, 8 `*_last_run` variables
- ✅ Context builder: all 8 tasks use `resolve_last_run()`

#### Future Work

1. **Refactor template to loop over `scheduler_tasks`** instead of 8 hardcoded sections
2. **Add `last_run` persistence to TaskRegistry itself** → Store in Cosmos `settings` container alongside cron/enabled

---
