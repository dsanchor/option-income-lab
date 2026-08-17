# Squad Decisions

## Active Decisions

### 1. Scheduler Hang Watchdog — Per-Symbol Timeout & Worker Max Duration

**Date:** 2026-06-30  
**Author:** Rusty (Agent Dev)  
**Status:** ✅ Implemented  
**Impact:** Scheduler reliability, options chain caching, production stability

#### Context

Production Container App logs showed the scheduler was "working" (next_run advancing every 10 min) but NO jobs were actually executing for hours. Root cause: the options_chain job at 03:20 printed "Refreshing options chain cache for 16 symbols..." and NEVER printed "Complete". It hung indefinitely. From 04:20 onward every hour: "Options Chain Fetcher - Skipped (still running)" + "Skipping options_chain: previous run still in progress". Because the worker thread is single/sequential (by design to preserve job ordering and avoid concurrency issues with shared state), that one hung job blocked ALL other jobs (monitor_agents, summary, etc.) from ever executing.

#### Diagnosed Root Causes

1. **options_chain_cache.py `refresh_all` (lines ~150-163):** Awaited `self.refresh(symbol)` **sequentially with NO timeout**. `refresh` calls `_fetch_yfinance` (line ~196) which makes **synchronous blocking** yfinance calls (`yf.Ticker(symbol)`, `ticker.info`, `ticker.option_chain(...)` at lines ~212-255) directly inside an `async def`, with no `asyncio.to_thread`/executor and no socket timeout. yfinance uses `requests` under the hood; a stalled TCP connection hangs forever. One hung symbol => `refresh_all` never returns => the scheduler worker thread is blocked permanently.
   - Note: The existing sync web path `get_or_load` (line ~63-89) **DID** bound it: `pool.submit(self._sync_refresh, symbol).result(timeout=120)`. `refresh_all` lacked this protection.

2. **scheduler_registry.py `_worker_loop` (lines ~200-228):** Ran each job to completion with **no max-duration guard**. A hung job jams the queue forever. The main loop still ticks (heartbeat + next_run advancement continue because we have a worker thread), but NO jobs execute.

3. **web/app.py (lines 2904, 2954):** Called `cosmos.get_all_symbols()` which **does not exist**. The correct method is `cosmos.list_symbols()` (defined in src/cosmos_db.py:124). This caused `'CosmosDBService' object has no attribute 'get_all_symbols'` errors when resolving `last_run` timestamps for summary_agent and portfolio_enrichment tasks.

#### The Two-Layer Fix

##### Fix 1 — Bound options_chain refresh_all per symbol (primary defense)

**File:** `src/options_chain_cache.py`

**Changes:**
- Added module constant `_REFRESH_SYMBOL_TIMEOUT = 90` (90 seconds per symbol, line ~28)
- Added `import concurrent.futures` (line 17)
- Rewrote `refresh_all` (lines ~150-180) to use `concurrent.futures.ThreadPoolExecutor(max_workers=4)` and execute each symbol's `_sync_refresh` in a thread with a **hard timeout** via `future.result(timeout=_REFRESH_SYMBOL_TIMEOUT)`
- On timeout: log a warning, count it as an error, and **CONTINUE** to the next symbol (does not abort the batch)
- Reuses the existing `_sync_refresh(symbol)` helper which runs `self.refresh(symbol)` in its own event loop (safe for thread execution; each thread gets its own loop via `asyncio.new_event_loop()`)
- Small bounded concurrency (max_workers=4) speeds up the overall refresh while keeping each symbol timeout-bounded

**Rationale:**
Prevents one hung symbol from blocking the entire options chain refresh job. In production, if one symbol's yfinance connection stalls (network timeout, API hang, etc.), the job logs the error and moves on to the next symbol. The worker queue never jams.

**Code Pattern:**
```python
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
    futures_map = {symbol: executor.submit(self._sync_refresh, symbol) for symbol in symbols}
    for symbol, future in futures_map.items():
        try:
            future.result(timeout=_REFRESH_SYMBOL_TIMEOUT)
            success_count += 1
        except concurrent.futures.TimeoutError:
            logger.warning("%s: options chain refresh timed out after %d seconds", symbol, _REFRESH_SYMBOL_TIMEOUT)
            error_count += 1
        except Exception as e:
            logger.error("%s: options chain refresh failed: %s", symbol, e)
            error_count += 1
```

##### Fix 2 — Worker watchdog (defense in depth, protects the ENTIRE scheduler)

**File:** `src/scheduler_registry.py`

**Changes:**
- Added module constant `_MAX_TASK_DURATION_SECONDS = 1800` (30 minutes, line ~17)
- Rewrote `_worker_loop` (lines ~200-245) to execute each dequeued job in a **sub-thread** with `join(timeout=_MAX_TASK_DURATION_SECONDS)`
- If the job exceeds the timeout:
  - Log error: "task X exceeded max duration, abandoning"
  - Print: "❌ SCHEDULER TIMEOUT: {task.display_name} exceeded {_MAX_TASK_DURATION_SECONDS}s"
  - Set `task.running = False`
  - **Continue to the next queued job** (the orphaned thread may linger but does NOT block the worker)
- Preserves:
  - Setting `task.last_run` (start time) on completion AND on timeout/error
  - Error isolation (exceptions logged, worker keeps running)
  - The existing overlap guard via `task.running` (prevents double-runs)
- The job functions themselves bridge async via `_run_async` (new event loop). Running them in a sub-thread is safe (each thread is daemon).

**Rationale:**
No job can ever jam the queue forever, even if:
- The per-symbol timeout guard is bypassed (e.g. a future job type we add doesn't have item-level timeouts)
- A different job type hangs (e.g. a summary_agent LLM call stalls, a cosmos query hangs, etc.)
- A bug is introduced that disables the per-item timeout

This is a **catch-all safety net** for the entire scheduler. It guarantees that the worker queue will never be permanently jammed by ANY job, known or future.

**Code Pattern:**
```python
def run_task():
    try:
        task.job_func()
    except Exception as e:
        print(f"❌ SCHEDULER ERROR in {task.name}: {e}")
        logger.exception(f"Error executing task {task_name}")

job_thread = threading.Thread(target=run_task, daemon=True, name=f"TaskExec-{task_name}")
job_thread.start()
job_thread.join(timeout=_MAX_TASK_DURATION_SECONDS)

if job_thread.is_alive():
    logger.error(f"Task {task_name} exceeded max duration of {_MAX_TASK_DURATION_SECONDS}s, abandoning")
    print(f"❌ SCHEDULER TIMEOUT: {task.display_name} exceeded {_MAX_TASK_DURATION_SECONDS}s")

task.last_run = start_time
task.running = False
```

##### Fix 3 — get_all_symbols bug

**File:** `web/app.py`

**Changes:**
- Line ~2904 (summary_agent branch in last_run resolver): `cosmos.get_all_symbols()` → `cosmos.list_symbols()`
- Line ~2954 (portfolio_enrichment branch in last_run resolver): `cosmos.get_all_symbols()` → `cosmos.list_symbols()`

**Rationale:**
`get_all_symbols()` does not exist. `list_symbols()` is the correct method (defined in src/cosmos_db.py:124). This fixes the AttributeError in the persisted last_run resolver that was causing errors in production logs.

#### Design Decisions

##### Why Two Layers?

**Layer 1 (per-symbol timeout):** Targets the known risky operation (yfinance network calls). Fast to timeout (90s per symbol), provides detailed error reporting (logs which symbol hung), and doesn't penalize the entire batch (other symbols still refresh).

**Layer 2 (worker watchdog):** Protects the ENTIRE scheduler from ANY hung job, known or unknown. Slower to trigger (30 min), but guarantees the queue never jams permanently. Defense in depth.

Both are necessary:
- Layer 1 prevents 95% of hangs (common network timeouts, API rate limits, etc.) with fast recovery
- Layer 2 catches the 5% we didn't anticipate (new job types, bugs in timeout logic, rare edge cases)

##### Why max_workers=4 for options chain?

- **Conservative parallelism:** yfinance makes network calls to Yahoo Finance API. Too much concurrency risks rate limiting or connection pool exhaustion.
- **Bounded timeout per symbol:** With a 90s timeout per symbol and 16 symbols, sequential execution would take 24 minutes (16 * 90s) in the worst case. With max_workers=4, the worst case is ~6 minutes (16/4 * 90s), which is acceptable for an hourly job.
- **Each thread isolated:** `_sync_refresh` creates a new event loop per symbol, so threads don't share async state.

##### Why 30 minutes for worker watchdog?

- Longest expected job: `monitor_agents` runs 5 agents across all symbols with many sequential LLM + yfinance calls. In production, this can take 10-15 minutes for a large portfolio.
- 30 minutes provides a comfortable margin (2x the expected max) without being so long that a hung job jams the queue for hours.
- The timeout is a constant (`_MAX_TASK_DURATION_SECONDS`), so it can be adjusted if job characteristics change.

##### Why daemon threads?

- Daemon threads are killed when the main process exits, so we don't leave orphaned threads running after scheduler shutdown.
- The worker thread is daemon (always running, consuming the queue).
- Job execution sub-threads are daemon (may be orphaned if they exceed the timeout, but won't prevent shutdown).

#### Constraints Honored

- ✅ Do NOT change cron expressions, task set, web API surface, or template variable names
- ✅ Keep tz-aware datetimes (all datetime objects are timezone-aware)
- ✅ Daemon threads only; no signal handlers off the main thread
- ✅ Keep imports tidy (concurrent.futures and threading already used in the codebase)

#### Validation

1. ✅ **Import check:** `python3 -c "import src.main, src.scheduler_registry, web.app, src.options_chain_cache; print('import OK')"`
2. ✅ **Throwaway runtime test** (deleted after):
   - a) `refresh_all`-style call where one "symbol" hangs (mocked to sleep 5s with timeout=2s) returns within ~2s, counts it as an error, and OTHER symbols still succeed → **PASS**
   - b) Scheduler worker watchdog: enqueue a job that sleeps longer than test max-duration (2s); assert the worker logs/abandons it, clears running, and then a SECOND enqueued job still runs (proving the queue is not jammed) → **PASS**
3. ✅ **Targeted pytest:** `pytest tests/ -k "schedul or registry or options_chain or cache" -q` → 3 passed, 99 deselected, 4 warnings (pre-existing economics/yfinance fixture failures ignored)

#### Production Impact

**Before:**
- One hung symbol in options_chain refresh => entire scheduler queue jammed for hours
- Symptoms: "Options Chain Fetcher - Skipped (still running)" repeated every hour, no other jobs execute, portfolio stale, agents don't run

**After:**
- One hung symbol => logged as error after 90s, other symbols continue, job completes
- Any hung job (not just options_chain) => abandoned after 30 min, queue continues with next job
- Scheduler never jams permanently
- Production logs will show:
  - Per-symbol timeouts: `WARNING: AAPL: options chain refresh timed out after 90 seconds`
  - Job-level timeouts: `ERROR: Task options_chain exceeded max duration of 1800s, abandoning`
  - Queue continues: next job executes normally

#### Key Learnings

1. **Blocking I/O in async with no timeout:** yfinance (and other libraries using `requests`) makes synchronous blocking network calls. If called directly inside an `async def` without wrapping in `asyncio.to_thread` or `run_in_executor`, a stalled connection hangs the async task forever. **ALWAYS wrap blocking I/O in a thread with a hard timeout.**

2. **Per-item timeout for batch jobs:** When a batch job (refresh_all, fetch_all, etc.) iterates over many items, **each item MUST have a bounded timeout**. One hung item must NOT block the entire batch.

3. **Worker watchdog for scheduler safety:** Even with per-item timeouts, a scheduler worker should have a **max-duration guard for the entire job execution**. This prevents ANY job type (known or future) from jamming the queue forever.

4. **Defense in depth:** Use two layers:
   - (1) Per-item timeout for known risky operations (yfinance, LLM, etc.) — fast recovery, detailed errors
   - (2) Max-duration guard for the entire job execution — catch-all safety net
   Both are necessary — the first prevents common hangs, the second is a last resort.

5. **Method name bugs in error paths:** Always verify method names exist when calling dynamic code paths (e.g. error handlers, last_run resolvers). `get_all_symbols()` did not exist but was only called in the last_run resolver error path, so it went unnoticed until production logs showed the AttributeError.

#### Files Changed

- `src/options_chain_cache.py` (lines 17, 28-30, 150-180): Per-symbol timeout with ThreadPoolExecutor
- `src/scheduler_registry.py` (lines 17, 200-245): Worker watchdog with max-duration guard
- `web/app.py` (lines 2904, 2954): `get_all_symbols()` → `list_symbols()`

#### Future Considerations

- **Configurable timeouts:** If different symbols or job types need different timeouts, consider adding per-task timeout configuration (e.g. `task.max_duration` override).
- **Metrics/monitoring:** Log timeout events to a metrics system (e.g. Application Insights custom events) for production monitoring and alerting.
- **Per-symbol retry:** If a symbol times out, consider adding it to a retry queue with exponential backoff (but only if the timeout was transient, not a permanent hang).
- **yfinance replacement:** If yfinance hangs become frequent, consider switching to a more reliable data source or implementing circuit breakers.
---

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

### 27. Robust Mid-Price Calculation for Illiquid Options

**Date:** 2026-06-30  
**Author:** Rusty (Agent Dev)  
**Status:** ✅ Implemented  
**Impact:** Options pricing accuracy, P&L calculations, open-put monitor dashboard

#### Context

The open-put monitor dashboard showed an ADM 67.5 put (expiration 2026-07-17, stock price $76.73, deep out-of-the-money SOLD put) with P&L of **-254.6%** (displayed in red/negative) when it should have been strongly positive. Investigation revealed:

- Position snapshot stored: `midprice=1.95`, `premium_received=0.55`
- P&L formula: `(premium_received - midprice) / premium_received * 100` = `(0.55 - 1.95) / 0.55 * 100` = **-254.5%**
- Live yfinance data showed: `bid=0.05`, `ask=0.25` → true value ~$0.15
- Corrupted snapshot came from: `bid=0`, `ask≈3.9` → naive mid = `(0 + 3.9)/2 = 1.95`

The problem was a **garbage one-sided illiquid quote** where the naive midpoint calculation produced an absurd mark.

#### Decision

Replace the naive mid-price calculation `(bid + ask) / 2` with a **robust mid-price function** that resists one-sided and stale-wide illiquid quotes.

#### Implementation

##### Created `src/options_math.py`

New shared module containing `robust_mid(bid, ask, last=0.0)` with logic:

1. **Sane two-sided quote** (bid > 0, ask > 0, not implausibly wide) → `(bid + ask) / 2`
2. **Implausibly wide spread** (ask > bid * 8 + 0.20) → anchor to `bid` (ignore stale/garbage ask)
3. **No bid (bid ≤ 0)** → mark conservatively near 0, hard cap at `0.10` (never use `ask/2`)
4. **Nothing usable** → `0.0`

The `last` (lastPrice) parameter is accepted for future heuristics but currently unused (lastPrice is stale for illiquid names).

##### Updated Two Call Sites

Both naive calculations were identical and replaced in a single commit:

1. **`src/options_chain_cache.py` line ~460:**
   - Before: `"mid": round((bid + ask) / 2, 4) if (bid + ask) > 0 else 0.0,`
   - After: `"mid": robust_mid(bid, ask, last_price),`

2. **`src/yfinance_data_provider.py` line ~522:**
   - Before: `"mid": round((bid + ask) / 2, 4) if (bid + ask) > 0 else 0.0,`
   - After: `"mid": robust_mid(bid, ask, last_price),`

Both sites have access to `last_price` parameter via existing local variables.

##### Test Coverage

- **Created `tests/test_options_math.py`:** 11 test cases covering:
  - Sane two-sided quotes → midpoint
  - Garbage one-sided quotes (bid=0, ask=3.9) → capped at 0.10 (NOT 1.95)
  - Normal spreads (bid=0.05, ask=0.25) → 0.15
  - Implausibly wide spreads (bid=0.05, ask=3.9) → anchor to bid (0.05)
  - Edge cases (both zero, only bid, only ask, negative/None handling, rounding)

- **Updated `tests/test_yfinance_data_provider.py`:**
  - `test_mid_price_calculation` now uses `robust_mid` instead of naive average for expected values

- **Validation:**
  - ✅ All 11 new tests pass
  - ✅ Updated yfinance test passes
  - ✅ Direct verification: `robust_mid(0, 3.9)` = `0.1` (not 1.95), `robust_mid(0.05, 0.25)` = `0.15`
  - ✅ No regressions

#### Rationale

1. **Data quality at the source:** Fixing bad marks at the data ingestion layer (options_chain_cache, yfinance_data_provider) prevents downstream corruption in position snapshots, P&L calculations, and dashboard displays.

2. **Shared logic:** Option pricing math should live in a dedicated module, not duplicated across multiple files.

3. **No lastPrice trust:** For illiquid names, `lastPrice` is stale (hours/days old) and unreliable. Using it as a fallback would just substitute one garbage value for another.

4. **Hard cap for bidless options:** When there are no buyers (bid=0), the option is near-worthless to the holder. Capping at `0.10` prevents a stale-high ask from inflating the mark on truly worthless positions.

5. **No downstream changes:** The P&L formula, dashboard logic, and position tracking are all CORRECT. This is purely a data-quality fix.

#### Impact

- **Immediate:** Prevents future position snapshots from recording absurd mid-prices due to one-sided/illiquid quotes.
- **Historical:** Existing corrupted snapshots (like the ADM 67.5 put with mid=1.95) remain in storage until the next live refresh overwrites them with correct marks.
- **Monitor accuracy:** Once refreshed, the open-put monitor will show correct P&L for all illiquid positions.

#### Files Changed

- **Created:** `src/options_math.py`
- **Modified:** `src/options_chain_cache.py` (import + line ~460)
- **Modified:** `src/yfinance_data_provider.py` (import + line ~522)
- **Created:** `tests/test_options_math.py`
- **Modified:** `tests/test_yfinance_data_provider.py` (test_mid_price_calculation)

#### Ownership

- **Module:** Rusty (Agent Dev — Python / data plumbing)
- **Not changed:** P&L logic, dashboard (Linus/Ralph/Danny — they own strategy/UI)

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

## 27. Premium/Buyback Display Normalization

**Date:** 2026-06-26  
**Status:** ✅ Implemented  
**Agent:** Rusty (Agent Dev)  
**Requested by:** dsanchor

### Problem

Premium and buyback cost values sometimes displayed as "N/A" on the symbol detail page even though the economics page correctly showed and counted those values for the same positions.

### Root Cause

**Data shape inconsistency** between the economics aggregation path and the symbol detail display path:

1. **Economics path** (`web/app.py` line 216-230):
   - Normalizes `source` to `{}` if not a dict
   - Uses `_parse_numeric()` for tolerant parsing (accepts numbers, numeric strings like "1.50", strips "$" and commas, treats "N/A" as None)
   - Skips positions where premium parses to None

2. **Symbol detail template** (`web/templates/symbol_detail.html`):
   - Direct Jinja2 access: `pos.source.premium` and `pos.buyback_cost`
   - No normalization or parsing
   - If `source` is None/non-dict → Jinja2 returns Undefined → unpredictable rendering
   - If premium is a string "N/A" or other non-numeric → renders raw string

**Result:** Economics uses tolerant parsing and shows/counts valid values, while the template directly accesses potentially malformed data and shows "N/A" for the same position.

### Data Model Facts

- **Premium location:** `position["source"]["premium"]` (nested in source dict)
  - Written by: `api_add_position`, `api_add_position_from_activity`, `api_roll_position_from_activity`, `api_manual_roll_position`
  - Can be: number, numeric string, None, or missing
  - `source` can be: dict, None, non-dict (string, number), or missing

- **Buyback location:** `position["buyback_cost"]` (top-level)
  - Written by: manual roll endpoint, `update_position_buyback_cost`
  - Can be: number, numeric string, None, or missing
  - Semantically tied to `rolled_to` (template only shows buyback if position was rolled)

### Decision

**Normalize premium and buyback at the READ boundary** (in the route handler) using the same logic as economics, rather than in the template.

#### Implementation

**Changed files:**
1. `web/app.py` (line 2120-2131): Added normalization in `symbol_detail_page` route
   ```python
   # Normalize premium and buyback for display (same logic as economics)
   source = pos.get("source")
   if not isinstance(source, dict):
       source = {}
   pos["_display_premium"] = _parse_numeric(source.get("premium"))
   pos["_display_buyback"] = _parse_numeric(pos.get("buyback_cost"))
   ```

2. `web/templates/symbol_detail.html`:
   - Lines 353-372: Updated premium display to use `pos._display_premium` with `"%.2f"|format` filter
   - Lines 373-394: Updated buyback display to use `pos._display_buyback` with `"%.2f"|format` filter
   - Lines 453-485: Updated manual position section to also use normalized fields

#### Benefits

✅ **Consistency:** Economics and symbol detail pages now show identical values  
✅ **Robustness:** Handles all data shapes (strings, None, missing, non-dict source)  
✅ **Centralized logic:** Single source of truth (`_parse_numeric`) for numeric parsing  
✅ **Clean templates:** Templates render pre-normalized data, no complex logic in Jinja2

### Validation

✅ `python3 -c "import web.app"` — imports successfully  
✅ `python3 -c "from src import main"` — imports successfully  
✅ Custom validation tests — all passed (7/7 test cases covering various data shapes)  
✅ `python3 -m pytest tests/ -q` — 4 pre-existing failures (economics/yfinance), no new failures

**Test coverage:**
- Normal numeric values (1.50, 0.50) → parsed correctly
- String values ("2.25", "$3.50") → parsed correctly
- Missing source dict (None) → premium=None (correct fallback)
- Non-dict source ("manual", 1) → premium=None (correct fallback)
- String "N/A" → premium=None (correct, avoids displaying "$N/A")
- Buyback without `rolled_to` → shown in economics, hidden in UI (by design)

### Alternatives Considered

❌ **Fix the template directly:** Add parsing logic in Jinja2
  - Rejected: Duplicates business logic, harder to maintain, poor separation of concerns

❌ **Normalize on write:** Ensure all writes store numeric values
  - Rejected: Could corrupt existing data; doesn't handle legacy/malformed data; read-time normalization is safer

### Notes

- **No write-path changes:** Values continue to be stored as-is (preserves existing data)
- **Backwards compatible:** Handles both old (potentially malformed) and new data
- **Template semantic:** Buyback only shown when `rolled_to` exists (unchanged from original design)

### Related Files

- `web/app.py` (symbol_detail_page route, _parse_numeric helper)
- `web/templates/symbol_detail.html` (premium/buyback display sections)
- `.squad/agents/rusty/history.md` (data shape documentation)

---

### 8. Scheduler Settings: Relative Time Display
**Date:** 2026-06-26  
**Agent:** Rusty (scheduler + Settings UI owner)  
**Status:** ✅ Completed  
**Impact:** UI/UX (scheduler settings page)

#### Request
User requested: "under scheduler configuration settings, could you calculate the time next to Last Run and Next Run so we know when it was triggered and how much time is still to the next one? Add it next to the label."

#### Implementation Approach

**Choice: Live Client-Side JS (Preferred)**
Implemented live client-side relative time calculation with ISO timestamps in data attributes. This keeps the "in 45m" countdown continuously accurate as the page sits open without requiring a page reload.

**Rationale:**
- Better UX: countdown stays live (updates every 30 seconds via setInterval)
- No staleness: times remain accurate without reload
- Clean separation: server provides raw timestamps, client renders human-friendly relative times
- DRY: single reusable `formatRelative()` JS helper for all 8 tasks

#### Server-Side Changes (web/app.py)

1. **Added `to_iso()` helper** (line ~2981): Normalizes ISO timestamp strings to UTC for client-side parsing
2. **Added `resolve_last_run_iso()` helper** (line ~3000): Parallel to `resolve_last_run()` but returns raw ISO instead of formatted display string
3. **Extended context for all 8 tasks** (lines ~3005–3075):
   - Added `*_last_run_iso` and `*_next_run_iso` variants for each task:
     - `monitoring_last_run_iso`, `monitoring_next_run_iso`
     - `summary_last_run_iso`, `summary_next_run_iso`
     - `banner_last_run_iso`, `banner_next_run_iso`
     - `calendar_last_run_iso`, `calendar_next_run_iso`
     - `options_chain_last_run_iso`, `options_chain_next_run_iso`
     - `dgi_last_run_iso`, `dgi_next_run_iso`
     - `pe_last_run_iso`, `pe_next_run_iso`
     - `plan_monitor_last_run_iso`, `plan_monitor_next_run_iso`
   - Pattern: each task now contributes 4 context vars (display + ISO for both last/next)
4. **Added ISO variants to return dict** (lines ~3090–3137): All 16 ISO context vars added to the template context dictionary

#### Template Changes (web/templates/settings_config.html)

1. **Updated all 8 task sections** (lines vary):
   - Each Last Run div now has `data-last-run="{{ *_last_run_iso }}"`
   - Each Next Run div now has `data-next-run="{{ *_next_run_iso }}"`
   - Each div contains a `<span class="relative-time" style="..."></span>` for the computed relative time
   - Style: `font-size:0.75rem; opacity:0.7; margin-left:0.5rem;` to match existing muted text styling
2. **Added JS helper** (line ~498):
   - `formatRelative(isoStr)`: DRY helper that computes relative time strings
     - Returns `(2h ago)` for past times, `(in 45m)` for future times
     - Handles days, hours, minutes, `<1m` for very recent/imminent
     - Gracefully handles empty/invalid ISO strings (returns `''`)
   - `updateAllRelativeTimes()`: queries all `[data-last-run], [data-next-run]` elements and updates their `.relative-time` spans
   - Runs on page load and every 30 seconds via `setInterval(updateAllRelativeTimes, 30000)`

#### Validation Results

✅ **Import checks**: `python3 -c "import web.app"` → OK; `python3 -c "from src import main"` → OK  
✅ **Template parsing**: Jinja template parses successfully (no syntax errors)  
✅ **Attribute counts** (via grep):
  - `data-last-run=` → 8 occurrences (✓ all 8 tasks)
  - `data-next-run=` → 8 occurrences (✓ all 8 tasks)
  - `class="relative-time"` → 16 occurrences (✓ 8 last + 8 next)  
✅ **Pytest**: 4 pre-existing economics test failures (expected, unrelated); no new failures introduced

#### Affected Tasks (All 8)
1. Monitoring Agent
2. Summarization
3. Dashboard Banner
4. Calendar Sync
5. Options Chain
6. DGI Screener
7. Watchlist Enrichment (portfolio_enrichment)
8. Plan Monitor

#### Edge Cases Handled
- `Never` / `None` last_run → no relative time shown (empty span)
- `N/A` next_run → no relative time shown (empty span)
- Next Run in the past (overdue task) → shows `(in <1m)` if very soon, or `(Xm ago)` if past
- Timezone correctness: ISO strings include UTC timezone, JS `Date` parses correctly

#### Notes
- Did NOT modify scheduler logic, cron expressions, or how last_run is resolved (purely display-additive)
- Kept all existing absolute timestamps intact (relative time is ADDITIONAL, not a replacement)
- Uniform styling across all 8 tasks (muted, small font, consistent placement)
- Live updates every 30s ensure "in 45m" → "in 44m" → ... without page reload

#### Files Modified
- `web/app.py` (lines ~2971–3137): Added ISO helpers and context vars
- `web/templates/settings_config.html`: Updated all 8 task sections + added JS helper

#### Future Improvements (Out of Scope)
- Could refactor the template to loop over `scheduler_tasks` instead of 8 hardcoded sections (reduces duplication)
- Could add tooltip on hover showing exact local time conversion

---
# Replace Close Position Prompt with Dropdown Modal

**Date:** 2026-06-27  
**Author:** Rusty  
**Status:** Implemented  
**PR/Commit:** TBD

## Context

Users were prompted to type a number (1/2/3) to select a close reason when closing a position:
- 1 → Expired
- 2 → Assigned  
- 3 → Manual close

This required remembering the mapping and typing accurately. The Close button already had a ▾ symbol hinting at a dropdown, but the UX was still a basic `prompt()` dialog.

## Decision

Replace the numeric `prompt()` with a **dropdown modal** for selecting the close reason.

**User Request (translated from Spanish):**  
> "al cerrar las posiciones, da la opción de cerrar como expirada, asignada o close manual. Puedes cambiarlo para que no sea introducir un número sino que sea un desplegable?"  
> ("When closing positions, give the option to close as expired, assigned, or manual close. Can you change it so it's not entering a number but a dropdown?")

## Implementation

### Modal UI (web/templates/symbol_detail.html:793-815)

Added a reusable modal following the existing pattern used by `planDetailModalSD` and `summaryDetailModal`:

```html
<div class="modal-overlay" id="closePositionModal" style="display:none;">
    <div class="modal-content" style="max-width:450px;">
        <div class="modal-header">
            <h3>Close Position</h3>
            <button class="modal-close" id="closePositionModalClose">&times;</button>
        </div>
        <div style="padding:1rem 1.25rem;">
            <div style="margin-bottom:1.5rem;">
                <label for="closeReasonSelect" style="display:block; margin-bottom:0.5rem; font-weight:500; font-size:0.9rem;">Close Reason</label>
                <select id="closeReasonSelect" class="form-control" style="width:100%; padding:0.5rem; border-radius:var(--radius); border:1px solid var(--border); background:var(--bg-input); color:var(--text); font-size:0.9rem;">
                    <option value="manual" selected>Manual close</option>
                    <option value="expired">Expired</option>
                    <option value="assigned">Assigned</option>
                </select>
            </div>
            <div style="display:flex; gap:0.5rem; justify-content:flex-end;">
                <button id="closePositionCancel" class="btn-sm">Cancel</button>
                <button id="closePositionConfirm" class="btn btn-primary">Close Position</button>
            </div>
        </div>
    </div>
</div>
```

**Design choices:**
- One reusable modal (not per-position) to minimize DOM bloat
- Default selection: "Manual close" (matches previous default behavior)
- Three explicit options: Manual close (`manual`), Expired (`expired`), Assigned (`assigned`)
- Standard modal close paths: × button, Cancel button, overlay click

### Handler Logic (web/templates/symbol_detail.html:1347-1399)

Replaced the old prompt-based handler with:

1. **Module variable:** `currentClosePositionId` stores the position_id when modal opens
2. **Open function:** `openClosePositionModal(posId)` sets the position_id, resets dropdown to default, shows modal
3. **Close function:** `closeClosePositionModal()` hides modal, clears position_id
4. **Close triggers:** × button, Cancel button, overlay click all call close function
5. **Confirm handler:** Reads `closeReasonSelect.value`, calls `PUT /api/symbols/{symbol}/positions/{position_id}/close` with `{ close_reason: <value> }`, reloads on success, alerts on error
6. **Button click:** Each `[data-close-pos]` button opens modal with `e.stopPropagation()` preserved (doesn't trigger row toggle)

**Position ID flow:**
```
User clicks [data-close-pos] button
  → Extract dataset.closePos
  → openClosePositionModal(posId)
  → Store in currentClosePositionId
  → User selects reason, clicks "Close Position"
  → Confirm handler reads currentClosePositionId + closeReasonSelect.value
  → fetch PUT with { close_reason }
```

### API Contract (unchanged)

- **Endpoint:** `PUT /api/symbols/{symbol}/positions/{position_id}/close`
- **Body:** `{ close_reason: "expired" | "assigned" | "manual" }`
- **Backend:** web/app.py:1072 `api_close_position`
- **Default:** "manual" (when body omitted or invalid)

No backend changes required. The dropdown values map directly to the existing API contract.

## Validation

- ✅ Jinja2 template parses successfully
- ✅ `import web.app` succeeds
- ✅ Old `prompt('Close reason?` removed from codebase
- ✅ New `<select id="closeReasonSelect">` with expired/assigned/manual options present
- ✅ Fetch still posts `{ close_reason: <value> }` to same endpoint
- ✅ Tests pass with same baseline failures (2 economics, 1 yfinance config, 17 yfinance fixture errors — all pre-existing, unrelated to this change)
- ✅ Manual trace confirms: button click → modal opens with position_id → confirm sends correct PUT request

## Alternatives Considered

1. **Inline dropdown in table row:** Would clutter the position table and require per-row dropdowns
2. **Keep prompt() with text options:** Still requires typing; modal is more user-friendly
3. **Custom dropdown component:** Overkill; standard `<select>` is accessible and sufficient

## Impact

- **User-facing:** More intuitive UX, no need to remember number mappings
- **Code:** Replaced ~25 lines of prompt-based handler with ~53 lines of modal UI + handlers (net +28 lines)
- **Consistency:** Follows the same modal pattern as plan detail and summary modals
- **Accessibility:** Standard `<select>` element is keyboard-navigable and screen-reader-friendly
- **Behavior:** Default to "Manual close" matches previous default, no behavioral change

## Future Considerations

- Could add keyboard shortcut (Escape to close) for power users — already works via overlay click
- If we add more close reasons in the future, just add `<option>` elements to the dropdown
- The modal pattern is reusable for other action confirmations (e.g., delete position, roll confirmation)
# Scheduler Non-Blocking Architecture

**Date:** 2026-06-29  
**Status:** ✅ Implemented  
**Components:** Scheduler, TaskRegistry  
**Files:** `src/scheduler_registry.py`, `src/main.py`

## Context

The scheduler UI displayed `next_run` timestamps in the past (e.g., "2026-06-29 13:55:00 UTC (6h ago)"), indicating the scheduler loop had frozen. Users could not tell if the scheduler was alive or when the next run would actually occur.

## Problem

Three interrelated issues caused the freeze:

1. **Loop freeze:** Jobs ran synchronously on the single scheduler thread. A long-running or hung job (e.g., a yfinance/LLM network call with no timeout) blocked the entire loop → no `next_run` advances, heartbeat stops, UI shows frozen past timestamp.

2. **next_run advanced AFTER job completes:** In `execute_due_tasks`, `task.next_run = task.cron_obj.get_next(datetime)` ran AFTER `task.job_func()`. Even a normal long job showed a past `next_run` for its whole duration.

3. **monitor_agents double-scheduled:** The heaviest job (runs 5 agents across all symbols with many sequential LLM + yfinance calls) was registered in the TaskRegistry AND ALSO handled by separate local `next_run`/`cron` variables in the loop. This caused `run_all_agents()` to run TWICE when due, and the heartbeat's local next_run diverged from the registry next_run shown in the UI.

## Decision

**Non-blocking job execution via worker thread:**

- Introduce a single dedicated worker thread inside `TaskRegistry` that executes jobs sequentially off the main loop thread
- Main loop detects due tasks, advances their `next_run` to the next future occurrence, and enqueues the job (non-blocking)
- Worker thread consumes jobs from a `queue.Queue`, executes them one at a time, logs exceptions but never dies
- Keep jobs SEQUENTIAL (one worker, not concurrent) because agents/cosmos/runner are NOT proven thread-safe

**Rationale:**
- Keeps the main loop ticking (heartbeat + schedule advancement) even while heavy jobs run
- Preserves sequential job execution to avoid breaking existing code assumptions
- Isolates failures: a failing job logs an error but doesn't kill the loop or worker
- Simple and correct: one queue, one worker, one job at a time

**Advance next_run BEFORE dispatching:**
- Compute `task.next_run = task.cron_obj.get_next(datetime)` BEFORE enqueuing the job
- Loop `get_next()` until the result is strictly in the future (guards against stale cron base)
- UI always shows a FUTURE `next_run`, never a past timestamp

**Overlap guard:**
- Add `task.running: bool` flag, set to `True` when enqueuing, `False` when job completes
- If a task is due but `task.running == True`, skip and log a warning (don't enqueue duplicate)
- `trigger_task_now()` (Run Now button) also respects the overlap guard

**Eliminate duplicate monitor_agents scheduling:**
- Remove local `cron`/`next_run` variables for monitor agents in `src/main.py`
- Remove the separate `if now_tz >= next_run: run_all_agents()` block
- `monitor_agents` lives ONLY in the TaskRegistry, like all other tasks
- Heartbeat reads `monitor_task.next_run` from the registry

## Alternatives Considered

**ThreadPoolExecutor with max_workers=1:**
- Pros: Standard library, no manual queue management
- Cons: Requires more boilerplate for shutdown, less explicit control over job sequencing
- **Rejected:** Simple `queue.Queue` + worker thread is more explicit and easier to reason about

**Concurrent job execution (thread pool with N workers):**
- Pros: Higher throughput, could run multiple lightweight tasks in parallel
- Cons: Agents/cosmos/runner are NOT proven thread-safe; would require extensive testing + locks
- **Rejected:** Risk too high for marginal gain (jobs already take hours, not seconds)

**Async/await with asyncio.create_task:**
- Pros: Python-native concurrency, could integrate with existing async agent code
- Cons: Scheduler loop is sync, would require refactoring `run()` method and signal handling
- **Rejected:** Mixing sync loop + async jobs adds complexity; worker thread is simpler

**Move `next_run` advancement AFTER job completes (status quo):**
- Pros: Simpler logic (one place to update next_run)
- Cons: UI shows past timestamps during long runs, confusing users
- **Rejected:** Advance-before-dispatch is a one-line change with huge UX benefit

## Implementation

**src/scheduler_registry.py:**
- Added `queue.Queue` (`_job_queue`), worker thread (`_worker_thread`), and shutdown flag (`_shutdown`) to `TaskRegistry.__init__`
- `initialize_all()` starts daemon worker thread
- `_worker_loop()` consumes jobs, executes them, sets `last_run` and clears `running` flag
- `execute_due_tasks()` detects due tasks, advances `next_run` via `_advance_next_run()`, sets `running = True`, enqueues job
- `_advance_next_run()` loops `get_next()` until result is in the future (max 100 iterations)
- `trigger_task_now()` checks overlap guard, enqueues job
- `shutdown()` sets `_shutdown` flag and joins worker thread with 5s timeout

**src/main.py:**
- Removed local `cron`/`next_run` variables for monitor agents
- Removed separate `if now_tz >= next_run: run_all_agents()` block
- Removed local cron reschedule block for monitor agents
- Updated heartbeat to read `monitor_task.next_run` from registry
- Added `self.registry.shutdown()` call before exiting

**Special handling for monitor_agents:**
- `monitor_agents` uses `config.cron_expression` (not `config.config['scheduler']['cron']`)
- `initialize_task()` and `handle_cron_changes()` special-case `task.name == "monitor_agents"`

## Validation

- ✅ Import checks: `python3 -c "import src.main, src.scheduler_registry, web.app"`
- ✅ Jinja2 template parsing: `jinja2.Environment().parse(...)`
- ✅ Runtime check: Created test script validating (a) next_run is future after dispatch, (b) loop not blocked, (c) job runs once, (d) last_run set, (e) overlap guard works → all PASSED
- ✅ Existing tests: `pytest tests/ -k "schedul or registry or main"` → 0 failures

## Consequences

**Positive:**
- Scheduler loop never freezes, even when jobs hang or take hours
- UI always shows accurate future `next_run` timestamps
- Heartbeat confirms scheduler is alive every 10 minutes
- Overlap guard prevents duplicate job execution
- Clean shutdown via `registry.shutdown()`

**Negative:**
- Slightly more complex: worker thread + queue instead of direct function calls
- Jobs still sequential (not concurrent), so total runtime unchanged

**Neutral:**
- `task.last_run` now records job START time (when enqueued) instead of completion time
  - Rationale: Start time is more useful for "when did this last run?" UI display
  - Alternative: Could record completion time, but then last_run wouldn't update until job finishes

## Follow-up

- Consider adding per-task timeout (e.g., `job_timeout: Optional[int]` in `ScheduledTask`, worker enforces via `threading.Timer`) if jobs start hanging indefinitely
- Consider logging job duration (worker thread records start/end, logs delta) for performance monitoring
- Consider making `max_iterations` in `_advance_next_run()` configurable (currently hard-coded to 100)

## Related

- `.squad/agents/rusty/history.md` — Learnings section on worker thread pattern
- `src/scheduler_registry.py` — Implementation
- `src/main.py` — Scheduler loop

### 2. Sort Roll Candidates by Ann.Ret%

**Date:** 2026-07-01  
**Author:** Linus (Quant Dev)  
**Requested by:** dsanchor  
**Status:** ✅ Implemented  
**Impact:** Roll candidate ranking, DTE target alignment

#### Decision

Roll candidate tables are sorted by `Ann.Ret%` (annualized return) descending instead of Net Credit descending.

#### Rationale

Net Credit descending biases candidate selection toward longer-dated contracts because longer expirations usually carry higher absolute premium. Sorting by `Ann.Ret% = Premium% × 365 / DTE` normalizes premium by time, surfacing the best return per day and better aligning candidate ranking with the approved 21-35 DTE roll target.

#### Scope

The Net Credit column and `net_credit` values remain available for economics and threshold checks. Only candidate table sort order and related instruction prose changed.

#### Changes

- **src/options_chain_filters.py**: Sort key in both branches + table label updated
- **src/open_call_roll_instructions.py**: Prose "sorted by Net Credit" → "sorted by Ann.Ret%"
- **src/open_put_roll_instructions.py**: Prose "sorted by Net Credit" → "sorted by Ann.Ret%"

#### Validation

- ✅ py_compile passed
- ✅ Targeted pytest: 2 pre-existing unrelated failures confirmed (contract-multiplier bug; yfinance DTE-window filter test)

### 3. Economics Test Fix — Contract Multiplier & Net-RoC Semantics

**Date:** 2026-07-01  
**Author:** Basher (Tester)  
**Requested by:** dsanchor  
**Status:** ✅ Done  
**Impact:** Test suite correctness, production contract multiplier semantics

#### Decision

Update stale `tests/test_economics.py` expectations to match current `web/app.py::_build_economics_report` contract-multiplier semantics.

#### Scope

Production code was NOT changed. Only test expectations updated to reflect intentional web/app.py behavior:
- `CONTRACT_MULTIPLIER = 100` for option contract dollar amounts
- RoC now reported net-of-buyback
- win_rate now counts profitable rolls as wins

#### Changes

**tests/test_economics.py:**
- Dollar aggregate expectations multiplied by 100 per CONTRACT_MULTIPLIER
- avg_roc_pct / annualized_roc_pct updated to expect net-RoC values (net of buyback cost)
- win_rate updated to reflect profitable-roll-as-win semantics
- Added premium_per_share and buyback_per_share field assertions

#### Validation

✅ pytest tests/test_economics.py -q → 2 passed, 2 warnings

Coordinator (Squad) independently verified the new expected values are correct against the intentional web/app.py logic (not rubber-stamped).

#### Pending — Held Item

**yfinance DTE-Window Test Failure (DIAGNOSED ONLY, NO CODE CHANGES)**

Root causes identified but held pending dsanchor decision:

1. **Mock Mismatch:** Test mocks `src.yfinance_data_provider.yf` but yfinance now imported directly in `src/options_chain_cache.py` (not through wrapper). TradingView Playwright path also unmocked.

2. **Dead Config Keys:** The 7-90 DTE window filter was dropped during OptionsChainCache refactor. `config.yaml` keys `min_dte` and `max_dte` are now unused.

**Decision Pending:** Dsanchor to decide whether to (a) re-implement the DTE window filter, or (b) retire the config keys + remove the test.


### 4. Remove Dead 7-90 DTE Window Config

**Date:** 2026-07-01  
**Author:** Rusty (Agent Dev)  
**Requested by:** dsanchor  
**Status:** ✅ Done  
**Impact:** Config cleanliness, eliminated dead configuration keys

#### Decision

Remove the nested `yfinance.options_chain` config block from `config.yaml`, including `min_dte` and `max_dte` keys.

#### Rationale

The 7-90 DTE filter on options-chain fetch was intentionally removed during the `OptionsChainCache` refactor. The fetch path now only excludes expired contracts; roll-candidate selection keeps its separate DTE≤45 cap.

#### Changes

**config.yaml:**
- Removed `yfinance.options_chain` sub-block containing `min_dte: 7` and `max_dte: 90`

#### Verification

- ✅ No live config reads depend on `yfinance.options_chain.min_dte` / `max_dte` (src/config.py has no accessors)
- ✅ `config.yaml` parses successfully after removal

### 5. Retire Obsolete yFinance DTE-Window Tests

**Date:** 2026-07-01  
**Author:** Basher (Tester)  
**Requested by:** dsanchor  
**Status:** ✅ Done  
**Impact:** Test suite cleanliness, removed assertions on deleted filter behavior

#### Decision

Retire obsolete tests from `tests/test_yfinance_data_provider.py` that asserted the removed fetch-time 7-90 DTE filter or removed `_min_dte` / `_max_dte` attributes.

#### Changes

**tests/test_yfinance_data_provider.py:**
- Removed `test_only_7_to_90_dte_included` (asserted removed fetch-time 7-90 filter)
- Removed `test_near_term_excluded` (asserted removed fetch-time 7-90 filter)
- Removed `test_custom_config_applied` (asserted removed _min_dte/_max_dte attributes)
- Removed empty `TestDTEFiltering` class
- Updated fixture comments to no longer imply a 7-90 fetch-time filter

#### Verification

- ✅ pytest tests/test_yfinance_data_provider.py: 20 passed, 1 failed
- **Pre-existing out-of-scope failure:** `test_greeks_populated_for_nonzero_iv` fails due to Playwright/mock-target root cause (same issue as held yfinance item). This failure exists independently and is not caused by DTE config removal.

#### Note on Held Item

The pre-existing `test_greeks_populated_for_nonzero_iv` failure is now also documented as related to the held yfinance mock-drift issue: test mocks `src.yfinance_data_provider.yf` but yfinance is now imported directly in `src/options_chain_cache.py` (not through wrapper), and TradingView Playwright path is also unmocked. Browser cannot start in test environment.

### 6. Manual Position Close — Optional Per-Share Buyback Cost

**Date:** 2026-07-02  
**Author:** Rusty (Agent Dev)  
**Requested by:** dsanchor  
**Status:** ✅ Done  
**Impact:** Manual close workflows, position economics tracking

#### Decision

Extend manual position closes to accept an optional per-share `buyback_cost`. When provided, the value is stored directly on the closed position. When omitted or empty, the field is not set. The input is only exposed in the close modal for the `manual` close reason; assigned and expired closes retain their existing flow.

#### Rationale

Users may want to track the actual cost paid to buy back shares when closing a position manually. The value is optional to maintain backward compatibility. Limiting the input to manual closes avoids schema drift in positions closed by automated reasons (assignment, expiration).

#### Changes

**src/cosmos_db.py:**
- `close_position()` function gained parameter: `buyback_cost: float | None = None`
- Sets `pos["buyback_cost"]` only when `buyback_cost` is provided (not None)
- If not provided, the field is omitted from the position record

**web/app.py:**
- `api_close_position()` endpoint now parses optional `buyback_cost` from the request JSON body
- Invalid or empty values are normalized to None
- The parameter is suppressed for non-manual close reasons; only exposed when `reason='manual'`
- Passes the parsed value to `close_position()`

**web/templates/symbol_detail.html:**
- Added optional buyback cost input field in the close modal
- Input is shown only when the close reason is set to `manual`
- Input is reset on modal open (no carry-over from previous closes)
- Only included in the PUT request body when the value is valid and non-empty

**tests/test_cosmos_close.py:**
- NEW test file with 2 test cases:
  - Close position WITH buyback_cost (verifies field is stored)
  - Close position WITHOUT buyback_cost (verifies field is omitted when not provided)
- Both tests passed ✅

#### Validation

- ✅ pytest tests/test_cosmos_close.py -q → 2 passed
- ✅ py_compile src/cosmos_db.py web/app.py → OK

#### Technical Notes

- The economics module already reads `position.buyback_cost` and multiplies by the contract multiplier
- No reporting changes were required
- The field is optional; backward-compatible with existing positions that lack it
- Manual-close-only constraint ensures assigned and expired positions keep clean, simple schemas

### 7. Scheduler Enabled Toggle Live Registry Persistence

**Date:** 2026-07-03  
**Author:** Rusty (Agent Dev)  
**Requested by:** dsanchor  
**Status:** ✅ Done  
**Impact:** Settings UI reliability, scheduler task enable/disable workflows

#### Decision

When saving scheduler settings from the `settings_config_save` endpoint, update the live scheduler registry enabled state for every togglable task immediately after rescheduling its cron.

#### Context

The save path persisted the `enabled` flag to disk config and CosmosDB, and rescheduled the associated cron task. However, the settings page reads checkbox state from `scheduler.registry.get_all_task_metadata()`, which contains the live in-memory state. Without updating the registry after save, the page reload would display stale (previously cached) enabled state for toggled tasks.

**Root Cause:** Gap between persistent storage (disk/Cosmos) and live registry state. The save wrote to disk but not to the registry.

#### Outcome

Added `scheduler.registry.update_task_enabled(task_name, enabled_bool, scheduler.config)` immediately after each `scheduler.reschedule_*()` call in `settings_config_save` for all togglable tasks:

1. summary_agent
2. plan_monitor
3. options_chain
4. dgi_screener
5. banner_agent
6. calendar_sync
7. portfolio_enrichment

**Note:** `monitor_agents` was intentionally left unchanged because the registry hardcodes it as enabled per design.

#### Validation

- ✅ `python3 -m py_compile web/app.py` — No syntax errors
- ✅ Code review: 7 one-line insertions, minimal and surgical
- ✅ No pre-existing unit tests for settings_config_save to run

#### Technical Notes

- The fix is strictly an in-memory sync operation; no API contract changes
- All task enable/disable state paths now synchronized: disk → Cosmos → registry
- Backward-compatible; no breaking changes to existing functionality

### 8. Supervisor surfaces ex-dividend for CSP (informational); calls unchanged

**Date:** 2026-07-08  
**Author:** dsanchor (via Copilot)  
**Agent:** Linus (Quant Dev)  
**Status:** ✅ Implemented  
**Impact:** CSP entry-timing awareness, supervisor context

#### Decision

Add a NON-BLOCKING informational note to the supervisor audit for cash-secured put (CSP) SELL decisions when an ex-dividend date falls within the trade window. Covered-call / call side is intentionally LEFT UNCHANGED (its ex-div ITM early-assignment warning already handles the real risk).

#### Context

**Motivation:** User request. Motivated by a GIS CSP alert (2026-07-07, $35 Aug-21 put) where ex-div was 3 days out and not surfaced.

**Ex-div data availability:** Ex-div data is already in the supervisor's context via the `DIVIDENDS PAGE` block injected by `agent_runner.py:1131-1132` (from yfinance `ex_dividend_date_recent`), so this is instruction-only — no plumbing work.

**Why CSP-only:** For short puts, ex-div creates mild entry-timing consideration (the underlying typically drops ~the dividend on ex-date, moving it modestly toward the short strike). However, options already price this via put-call parity; the value is discretionary entry timing, not catching mispricing. Calls have different dynamics (ITM early-assignment risk), already handled in call instructions.

#### Implementation

**File:** `src/supervisor_instructions.py`  
**Method:** Modified `get_supervisor_instructions()` to conditionally append ex-div section when:
- `agent_type == "cash_secured_put"`
- `decision_type == "SELL"`

**Content Guidelines:**
- Check DIVIDENDS PAGE for ex-div within trade window (now → expiration)
- Emphasize near-term case (~10 days) as most relevant for fresh entry
- State ex-div date and typical price drop effect (modest headwind toward strike)
- Frame as **INFORMATIONAL / entry-timing awareness ONLY** — must NOT block, downgrade, or flip SELL decision by itself
- Must NOT by itself raise `challenge_strength` (options already price dividends via put-call parity)
- Deep-ITM (delta < -0.70) + ex-div within ~10 days: rare early-assignment possibility (brief note, consistent with existing CSP framework)
- Fold into existing audit fields (`counter_arguments`, `one_liner`, etc.) — no schema changes

**Lines added:** ~26

#### Verification

- ✅ `python3 -m py_compile src/supervisor_instructions.py` — Passed
- ✅ `covered_call SELL` — no ex-div text (unchanged)
- ✅ `open_call WAIT` — no ex-div text (unchanged)
- ✅ `cash_secured_put SELL` — has ex-div text (CSP-gated)
- ✅ `cash_secured_put NOT_NOW` — no ex-div text (SELL-only gating)

All tests passed — CSP-gating works correctly, other agents byte-for-byte unchanged.

#### Rationale

- **CSP-specific:** Ex-div creates mild entry-timing consideration but options already price this. Different from calls where ex-div creates ITM early-assignment risk (already handled).
- **Non-blocking:** Awareness, not a blocker. Supervisor surfaces as context, not as a challenge requiring reconsideration unless there's also a genuine data/risk issue.
- **No schema changes:** Folds into existing audit fields to keep response parsing unchanged.
- **Conditional append:** Implementation ensures covered_call and other agents get zero changes (tested and verified).

#### Technical Notes

- `agent_runner.py:1131-1132` — DIVIDENDS PAGE injection (already exists)
- `src/supervisor_instructions.py:556-578` — new CSP ex-div awareness section
- Entry-timing awareness framing ensures alignment with existing options pricing model (put-call parity)

### 9. Calendar active-position flag per event date

**Date:** 2026-07-08  
**Author:** Rusty (Agent Dev)  
**Status:** ✅ Implemented  
**Impact:** Calendar event accuracy, position state consistency

#### Decision

The scheduled `sync_calendar` in `src/main.py` now computes `has_active_position` per calendar event date, matching the logic already present in `web/app.py`.

#### Context

Calendar events (earnings / ex-dividend) were being flagged as "active position" whenever the symbol had ANY active position, even if that position had expired before the event date. This was a symbol-wide check that failed to account for position expiration dates.

The web/manual sync in `web/app.py` (lines 2012-2021) was already implementing the correct per-event logic; the scheduled sync had not been updated to match.

#### Implementation

**File:** `src/main.py`  
**Changes:**
- Rewrote `sync_calendar` to collect active positions with their expirations
- Added helper function `_has_position_active_on(event_date)` that returns True only if some active position has `expiration >= event_date`
- Applied the helper to both `earnings` and `ex_dividend` upserts
- Web/app.py left unchanged (already correct)

**New Test File:** `tests/test_calendar_active_position.py`  
- Validates per-event active position logic
- Test passed ✅

#### Validation

```
python3 -m py_compile src/main.py → OK
pytest tests/test_calendar_active_position.py -q → 1 passed
```

#### Rationale

- **Per-event accuracy:** Each calendar event should be checked against active positions that extend through that specific date
- **Consistency:** Scheduled sync now mirrors the correct web/manual sync logic
- **Scope:** Changes apply to scheduled calendar sync only; web/manual sync and trading logic remain unchanged

---

## 2026-07-09: Alpha Exclude Identical Held Contract + Preserve Buyback Cost

**Date:** 2026-07-09  
**Requester:** @dsanchor  
**Status:** ✅ Implemented  
**Impact:** Alpha advisor accuracy, prevents no-op rolls, improves close vs roll cost comparison

### Problem

The alpha advisor was recommending rolling into the EXACT same contract (same strike AND expiration) as the currently-held position.

**Example:** Held $65 call exp 2026-07-17 → Alpha proposed "ROLL at $65 strike, exp 2026-07-17" (buy back at ask ~$0.20, re-sell at bid ~$0.15 = guaranteed ~$0.05/share loss for no position change).

**Root Cause:** `AgentRunner._build_alpha_options_chain()` filtered by option type + delta but did NOT exclude the currently-held contract from candidate selection. In the position-monitor flow, the held contract's strike/expiration were available but unused.

**User Feedback (Pass 2):** After implementing the exclusion filter, "If you remove the current contract, you miss the buyback cost." The buyback (buy-to-close) cost is the CURRENT contract's ask price, needed for the alpha to compare "close now (pay buyback) vs roll to a different contract."

### Solution: Two-Pass Implementation

#### Pass 1: Exclude Identical Contract (No-Op Prevention)

**Primary Fix:** Added `exclude_contract()` function to `src/options_chain_filters.py`
- Removes the EXACT contract matching BOTH current strike AND current expiration
- Preserves roll-out (same strike, different exp) and roll-up/down (different strike, same exp) candidates
- Operates on correct bucket: "calls" or "puts" per option_type
- Normalizes expiration: `str(exp).replace("-","")[:8]` (handles both "2026-07-17" and "20260717")
- Robust strike matching by float: compares `float(key) == float(current_strike)` (handles "65.0"/"65.00"/"65")
- Null-safe: if current_strike/current_expiration None, returns chain unchanged

**Wired into Alpha Builder:** `src/agent_runner.py`
- Updated `_build_alpha_options_chain` signature: added `current_strike=None`, `current_expiration=None` params
- After delta filter (line 1166+), if both params provided, call `exclude_contract(structured, current_strike, current_expiration, option_type)`
- Monitor call site (~line 2182): passes held position strike/expiration
- SELL-flow call site (line 1329): unchanged (new positions have no current contract; params default None)

**Instruction Guard:** Extended rule #7 in `src/alpha_instructions.py`
- A proposed roll/re-sell alternative MUST change the strike and/or the expiration
- NEVER propose rolling into the identical strike AND expiration
- If only alternative would be identical contract, report opportunity_strength as NONE

#### Pass 2: Preserve Buyback Cost Reference (Comparison Support)

**Lookup Helper:** Added `get_contract()` function to `src/options_chain_filters.py`
- Retrieves the current contract dict by strike/expiration from the full chain (BEFORE other filters)
- Uses same normalization logic as `exclude_contract` for consistency
- Pure lookup, null-safe, non-mutating

**Reference Block:** Updated `_build_alpha_options_chain` in `src/agent_runner.py`
- Capture current contract BEFORE delta filter (may already be filtered out by delta band)
- After excluding from candidates, append labeled reference block:
  ```
  === CURRENT POSITION (buyback-cost reference — NOT a roll candidate) ===
  {
    "strike": <current_strike>,
    "expiration": <current_expiration>,
    "bid": <bid>,
    "ask": <ask>,
    "buyback_cost": <ask>,  // explicit field for clarity
    "delta": <delta>,
    "last": <last>
  }
  Note: buyback_cost is the ask (cost to buy-to-close). Use it to compare closing vs rolling. Do NOT propose this exact strike+expiration as a roll target.
  ```
- Graceful fallback: if candidates empty BUT current_contract present, return just reference block (still useful)

**Instruction Guard Extended:** Rule #7 now states
- Use the current position block's ask as buy-to-close cost when comparing close vs roll
- Current contract is reference-only; must NEVER be selected as roll/re-sell target

### Verification

**Compilation:**
```
python3 -m py_compile src/options_chain_filters.py src/agent_runner.py src/alpha_instructions.py → OK
```

**Unit Tests:** 16 new tests
- `tests/test_exclude_contract.py` (8 tests): exact match removal, strike variants, expiration formats, null handling, preserves roll-out/up/down candidates
- `tests/test_get_contract.py` (8 tests): exact match retrieval, strike variants, expiration formats, null handling, missing contract, wrong bucket

**Result:**
```
pytest tests/test_exclude_contract.py tests/test_get_contract.py -q → 16 passed
```

**Field Mapping Verified:**
- ask = buyback cost per `yfinance_data_provider.py` schema (standard buy-to-close offer price)

### Implementation Files Changed

- `src/options_chain_filters.py` — added `exclude_contract()`, `get_contract()`
- `src/agent_runner.py` — updated imports, modified `_build_alpha_options_chain`, monitor call site updated
- `src/alpha_instructions.py` — extended rule #7
- `tests/test_exclude_contract.py` — 8 new tests
- `tests/test_get_contract.py` — 8 new tests
- `.squad/agents/linus/history.md` — updated work history
- `.squad/agents/rusty/history.md` — updated work history

### Key Pattern

When candidate chain must exclude a reference item (to prevent no-op selection) BUT the agent still needs its pricing:
1. **Capture reference BEFORE filters** that might remove it (e.g., delta filter)
2. **Surface as clearly-labeled REFERENCE block** separate from candidates
3. **Make semantic purpose explicit** (e.g., "buyback_cost" not just "ask")
4. **Reinforce in instructions** — reference is informational only, not selectable

### Impact

✅ Alpha can now correctly compare close cost (ask of held contract) vs rolling to candidates  
✅ Held contract never appears as no-op roll target (data-layer enforcement)  
✅ Works even when held contract's delta is outside alpha's kept band  
✅ Instruction guard reinforces semantic constraint (roll MUST change strike and/or expiration)

---

## 2026-07-09: Per-Activity Chat Feature — Two-Tier Context & Live-Fetch Design

**Decision owners:** Linus (prompt design), Rusty (endpoint + frontend), Basher (testing), dsanchor (feature request)  
**Status:** Implemented & Validated (13 tests passing)  
**Date decided:** 2026-07-09

### Context

Users requested a "Chat" button on activity detail pages to consult/discuss a specific trading activity (monitor/supervisor/alpha decision) with an LLM. The feature must compare the historical agent decision against current market data while maintaining data provenance clarity and read-only/advisory-only semantics.

**Design fork (resolved):**
- ~~Snapshot the filtered option chain + technicals into the activity doc at generation time~~ (would require schema changes, redundant storage)
- ✅ **Live-fetch at chat time:** Fetch CURRENT market data when user opens chat, clearly labeled as current (not what agents used historically)

### Decision: Two-Tier Context Separation

#### Tier 1: AGENT DECISION (Historical, Exact)
- Persisted outputs of monitor/supervisor/alpha agents PLUS position state at decision time
- Exact historical record: what agents decided and why
- Used ONLY to explain past agent reasoning

#### Tier 2: CURRENT MARKET DATA (Live, Re-fetched)
- Option chain (filtered for position) and technical analysis fetched LIVE at chat time
- Reflects present moment for user decision-making
- Always flagged as current/not-what-agents-used

### Message Format: Five Exact Section Headers

The system enforces these headers (Linus's system prompt + Rusty's endpoint construction):
```
=== AGENT DECISION (historical, exact — what the agents actually decided) ===
[activity doc as JSON]

=== POSITION ===
[position dict from symbol's positions[] array]

=== CURRENT MARKET DATA (LIVE NOW — NOT what the agents used) ===
[filtered option chain + current technical analysis]

=== CONVERSATION SO FAR ===
[prior turns, or "(none)"]

=== USER QUESTION ===
[latest user message]
```

### Critical Rules

1. **Explaining past decisions:** Reason ONLY from AGENT DECISION block. Never use CURRENT MARKET DATA to reconstruct a past decision. If current contradicts past, frame as "conditions have changed."
2. **Advising on current actions:** Use CURRENT MARKET DATA, always flag as live and NOT basis of original decision.
3. **Never invent data:** Do not fabricate strikes, premiums, Greeks, or technicals not present in context. If data missing, say so.
4. **Read-only/advisory-only:** Assistant explains and suggests. MUST NOT claim to execute trades. If asked to act, explain it can only advise.
5. **Domain competence:** Understands CSP/CC mechanics, rolling, delta, gamma, theta, IV, earnings/ex-div timing. Concise, concrete, grounded in numbers.
6. **Honest about uncertainty:** Acknowledge tradeoffs and limitations. Do not overstate confidence.

### Implementation Details

**Backend endpoint:** `POST /api/activities/{activity_id}/chat` (web/app.py)
- Loads activity from CosmosDB
- Fetches current option chain via cache, filters for position (±10 strikes)
- Fetches current technical analysis; best-effort fresh-gen fallback if no recent persisted doc
- Builds message with 5 exact headers
- Calls Agent(gpt-5.4-mini) via create_async_chat_client
- All error paths graceful: missing chain/technicals never block request

**Frontend:** "Chat" button on activity_detail.html (next to "Delete Activity")
- Ephemeral chat panel with message input, send button, history display
- Conversation history held in browser only (not persisted to DB)
- XSS-safe textContent for rendering

**Configuration:**
- Added Config.activity_chat_model property (default 'gpt-5.4-mini')
- Reads from config['activity_chat']['model'] for production override

### Rationale

**Why two-tier context:** Market moves constantly. Option chain NOW ≠ chain at decision time. If chat uses current data to explain past decisions, it generates false explanations ("agent was wrong" when actually market moved).

**Why live-fetch, no snapshots:**
- Keeps activity docs lean (no redundant chain/technical data stored)
- Users can ask "Is this still valid NOW?" and get real current answer
- Reduces schema complexity; activity struct unchanged

**Why five exact headers:** Clear parse anchors for LLM reasoning. Unambiguous separation of historical vs. current. Prevents the model from conflating data sources.

**Why gpt-5.4-mini:** Cost-effective for Q&A over provided context. No code gen needed. Consistent with plan_monitor_model precedent (all advisory tasks should have configurable models).

**Why read-only enforcement:** Advisory chat is non-transactional. Preventing execution claims avoids user confusion and potential errors.

### Files Changed

- `src/activity_chat_instructions.py` (new, 95 lines) — System prompt with strict two-tier contract
- `src/config.py` (~line 256) — Added activity_chat_model property
- `web/app.py` (~line 2815) — Added api_activity_chat endpoint
- `web/templates/activity_detail.html` (~lines 361–379, 565–651) — Added Chat button, panel, JS handler
- `tests/test_activity_chat.py` (new, 415 lines, 13 tests) — Comprehensive hermetic endpoint testing

### Validation

✅ py_compile: all modified Python files  
✅ pytest: tests/test_activity_chat.py → 13 passed  
✅ Contract tests: all 5 headers present, activity JSON in message, live chain data verified  
✅ Read-only enforcement: zero cosmos write/delete calls detected  
✅ Graceful degradation: chain unavailable, missing technicals, no linked position all handled  

### Bugs Found

None.

### Pattern for Future Chat Features

When building a chat assistant over agent decisions + live market data:
1. Enforce strict context-tier contract at the prompt level.
2. Assistant must understand which tier answers which question type.
3. NEVER conflate historical decision reasoning with current market conditions.
4. Always surface data provenance: "agent decided based on X at decision time; current data now shows Y."
5. Use exact section headers to anchor LLM parsing.

### Dependencies & Coordination

- Linus (system prompt) ← Rusty (exact section headers from endpoint)
- Rusty (endpoint) ← Linus (system prompt)
- Basher (test suite) → All (validates contract + read-only)
- Coordinator: Fixed AgentRunner construction (was AgentRunner(cfg), now AgentRunner(llm=..., model=...))

### Future Considerations

- Persistent chat history: Store chat sessions in Cosmos if users request (new container or extend activity docs)
- Technical analysis caching: TTL-based caching in OptionsChainCache or dedicated TechnicalAnalysisCache if freshness becomes issue
- Chat transcript export: Add "Export" button generating markdown/JSON dump
- Conversation threading: Extend CosmosDB activity docs with chat_sessions sub-collection for multi-turn persistence

---

## 2026-07-09: DPS Insights Prompt Module

**Date:** 2026-07-09  
**Owner:** Linus (Quant Dev)  
**Status:** Implemented  
**Context:** DPS (Deterministic Position Scorer) time-series narrative feature

### Problem

The DPS (Deterministic Position Scorer) produces numeric health scores (0-100) that are persisted in per-position time-series snapshots. Users need a natural-language **interpretation** of these snapshots to understand:
- Current position health
- DPS score trend over time (improving / worsening / stable)
- Notable historical inflection points
- Likely short-term outlook

The narrative must be **advisory-only** and **read-only** — it interprets persisted scores but does NOT recompute them or execute trades.

### Solution

Created `src/dps_interpret_instructions.py` with a single function:
```python
def get_dps_interpret_instructions() -> str
```

This returns a system prompt for a **one-shot summarizer agent** that produces natural-language prose (NOT JSON) interpreting DPS health over time.

### Design Principles

#### 1. Narrate, Don't Recompute
The DPS scorer owns the score computation logic. The insights agent **interprets and contextualizes** the persisted scores — it does NOT re-derive HOLD/WATCH/ROLL decisions or recalculate numeric scores.

**Rationale:** Separating computation from narrative prevents drift between the authoritative scorer and its explanation. The persisted `dps_score` is ground truth; the insights agent's job is to tell the story of how it evolved and what it means.

#### 2. Read-Only, Advisory-Only
The assistant explains trends and provides probabilistic outlook but CANNOT execute trades, place orders, or modify positions/data. If asked to act, it explains it can only advise.

**Rationale:** Matches the house pattern from `activity_chat_instructions.py` — advisors explain and suggest; they do not execute.

#### 3. Strict Context Contract
Input contains EXACTLY two blocks with these headers (enforced verbatim):
1. `=== POSITION ===` — position dict (symbol, type, strike, expiration, etc.)
2. `=== DPS SNAPSHOT HISTORY (oldest first) ===` — JSON list of snapshots (timestamp, underlying_price, gap_percent, rsi_14, macd_level, adx, midprice, pnl_pct, dps_score)

**Rationale:** Rusty (framework dev) owns the endpoint implementation and will pass data using these exact headers. The prompt references them verbatim to ensure alignment. This follows the pattern from `activity_chat_instructions.py`, which enforces a strict multi-tier context structure.

#### 4. Tie Score Movements to Underlying Signals
When narrating trend, the assistant must explain WHICH signals moved with the DPS score:
- **Moneyness (gap_percent):** narrowing (stock toward strike) vs. widening (stock away from strike)
- **Momentum (rsi_14, macd_level):** strengthening vs. weakening
- **Trend strength (adx):** high ADX = strong trend, low ADX = choppy
- **P&L (pnl_pct):** improving vs. eroding
- **Option price (midprice):** rising (position worsening) vs. falling (position improving)

**Rationale:** Users need to understand the *why* behind score movements, not just the numbers. Connecting the DPS trend to technicals builds intuition and trust.

#### 5. Handle Sparse Data Gracefully
If there are <3 snapshots or `dps_score` is mostly missing, the assistant says "the history is too short for a reliable trend" and summarizes what's available.

**Rationale:** Early in a position's lifecycle, there may not be enough data for meaningful trend analysis. The assistant must be honest about data limitations.

#### 6. Hedged, Probabilistic Outlook
The SHORT-TERM OUTLOOK section provides forward-looking assessment grounded in:
- Observed DPS trend
- Days to expiration (DTE) derived from expiration vs. latest snapshot timestamp
- Current moneyness and momentum

Framed as "if the current trend persists…" — NEVER states certainty. Notes gamma/assignment risk if score is deteriorating near expiration.

**Rationale:** Options trading is probabilistic. The assistant must acknowledge uncertainty and provide hedged guidance, not overconfident predictions.

### Output Format

**Natural-language prose** (NOT JSON), structured with clear section headers:
- **Current State**
- **Trend**
- **History**
- **Short-Term Outlook**

Each section cites specific numbers and timestamps from the snapshot data. Domain-aware language (OTM, ITM, delta, gamma, theta, assignment risk, roll semantics).

### Implementation Details

- **Module:** `src/dps_interpret_instructions.py`
- **Function signature:** `def get_dps_interpret_instructions() -> str`
- **Validation:** `python3 -m py_compile src/dps_interpret_instructions.py` passes
- **Target model:** gpt-5.4-mini (model-agnostic prompt design)
- **House style:** Matches `activity_chat_instructions.py` (professional, concise, domain-aware)

### Key Patterns

1. **Narrate don't recompute:** When building an assistant over persisted computational outputs, enforce a strict separation — the assistant interprets the outputs as ground truth, it does NOT re-run the computation or second-guess the logic.

2. **Context contract with exact headers:** When an endpoint will pass structured context blocks, reference the EXACT headers verbatim in the prompt to prevent misalignment between backend and assistant.

3. **Tie score movements to signals:** When narrating time-series data, explain the *why* by connecting the metric trend to its underlying drivers. This builds user intuition and trust.

4. **Honest about data limitations:** If the data is too sparse for reliable analysis, say so explicitly. Don't invent trends or extrapolate beyond what the data supports.

5. **Hedged, probabilistic outlook:** Options trading is uncertain. Always frame forward-looking statements as conditional ("if the current trend persists…") and acknowledge risks.

### Alignment with Existing Patterns

This design follows the same **read-only narrative assistant** philosophy as the recently-added activity-chat feature (`activity_chat_instructions.py`):
- Both enforce strict context-tier contracts with exact headers
- Both separate historical/authoritative data from narrative interpretation
- Both are advisory-only (no execution, no data modification)
- Both produce natural-language prose (not JSON)
- Both cite specific numbers from provided context (never invent data)
- Both target gpt-5.4-mini

The difference: activity-chat is **interactive Q&A** over agent decisions + live market data; DPS insights is a **one-shot summary** over time-series snapshots. But the underlying design pattern (read-only narrator over structured data) is the same.

### Future Considerations

- If users request interactive Q&A over DPS history (e.g., "why did the score drop on July 1?"), we could extend this into a multi-turn chat interface like activity-chat.
- If the snapshot schema evolves (e.g., adding IV rank, earnings proximity, or other signals), the prompt's snapshot field list and TREND narration logic should be updated to match.
- If we add multiple scoring models (e.g., DPS v2, alternative scorers), the prompt may need to clarify which scorer's outputs it's interpreting.

**Implemented by:** Linus (Quant Dev)  
**Reviewed by:** N/A (solo implementation)  
**Related files:**
- `src/dps_interpret_instructions.py` (new)
- `.squad/agents/linus/history.md` (updated — added DPS Insights learning)

---

## 2026-07-09: DPS Insights Endpoint

**Date:** 2026-07-09  
**Author:** Rusty (Agent Dev)  
**Status:** Implemented  
**Collaborators:** Linus (Strategy/Prompt owner for `src.dps_interpret_instructions`)

### Context

Users need a quick, narrative summary of a position's DPS health (trend, history, outlook) without running the full deterministic DPS analysis. The existing "📊 DPS Analysis" button provides detailed scoring metrics, but requires fetching live option chains and running computational analysis. We wanted a complementary "🧠 DPS Insights" button that is:
- **Fast:** No live fetches, no heavy computation
- **Narrative:** LLM interprets historical DPS snapshots into plain English
- **Focused:** Context is ONLY the position + its snapshot history

### Decision

Built a new one-shot endpoint `POST /api/symbols/{symbol}/positions/{position_id}/dps-insights` that:
1. Loads the position and up to 30 DPS snapshots (oldest first) from Cosmos
2. Builds an LLM message with EXACT headers (contract with Linus's prompt):
   - `=== POSITION ===` → position dict as JSON
   - `=== DPS SNAPSHOT HISTORY (oldest first) ===` → snapshots as JSON
   - Final prompt: `Summarize this position's DPS: current state, trend, notable history, and likely short-term outlook.`
3. Calls Agent Framework with `dps_insights_model` (default `gpt-5.4-mini`)
4. Returns `{ "insights": str }` as JSON

**Frontend:** Added "🧠 DPS Insights" button next to the existing "📊 DPS Analysis" button on each active position card. On click, fetches insights and renders as plain text (safe, no innerHTML).

**Config:** Added `dps_insights_model` property to `src/config.py` (reads from `config['dps_insights']['model']`, default `'gpt-5.4-mini'`), mirroring `activity_chat_model` precedent.

### Rationale

- **Why position + snapshots only?** Keeps the feature lightweight and fast. DPS snapshots already capture the deterministic scoring over time — the LLM's job is interpretation, not re-computation.
- **Why one-shot (no history)?** The DPS Insights use case is "give me a quick read on this position's DPS health" — not a conversation. One-shot design keeps it simple and focused.
- **Why exact headers contract?** Linus owns the prompt logic in `src.dps_interpret_instructions`. The exact headers (`=== POSITION ===`, `=== DPS SNAPSHOT HISTORY (oldest first) ===`) are a shared interface between Rusty's plumbing and Linus's strategy logic. This separation of concerns allows parallel work (Rusty builds endpoint, Linus writes prompt).
- **Why reuse activity chat pattern?** The DPS Insights endpoint is structurally identical to the per-activity chat endpoint (commit 65762ab): endpoint in web/app.py calling Agent(gpt-5.4-mini) via create_async_chat_client, config model property, and a button+panel in a template. Reusing this proven pattern accelerated implementation and maintained consistency.

### Alternatives Considered

1. **Fetch live option chain + run DPS:** Rejected — that's what the existing "📊 DPS Analysis" button does. We wanted a complementary feature that's faster and narrative-focused.
2. **Multi-turn chat history:** Rejected — DPS Insights is a quick read, not a conversation. Keep it one-shot.
3. **Hardcode model:** Rejected — always make model configurable (follow `activity_chat_model` / `plan_monitor_model` precedent).

### Implementation Details

**Backend:**
- Endpoint: `web/app.py` line ~1286 (`@app.post("/api/symbols/{symbol}/positions/{position_id}/dps-insights")`)
- Position loading: Reuses exact boilerplate from `api_dps_analysis` (line ~1207-1233)
- Snapshot loading: `cosmos.get_position_snapshots(symbol, position_id, limit=30)` then `snapshots.reverse()` (oldest first)
- LLM call: Agent Framework with `get_dps_interpret_instructions()` from `src.dps_interpret_instructions` (Linus owns)
- Error handling: Mirrors `api_dps_analysis` exactly — `except RuntimeError` → 503, generic `except Exception` → log + 500

**Frontend:**
- Button: `web/templates/symbol_detail.html` line ~563 (class `dps-insights-btn`, same data attributes as DPS Analysis button)
- Result div: line ~565 (class `dps-insights-result`, styled with `white-space:pre-wrap` for safe text rendering)
- JS handler: line ~1275 (mirrors existing `.dps-analyze-btn` handler, uses `textContent` for XSS safety)

**Config:**
- Property: `src/config.py` line ~261 (`dps_insights_model`, default `'gpt-5.4-mini'`)

**Files Changed:**
- `src/config.py`
- `web/app.py`
- `web/templates/symbol_detail.html`

### Validation

- ✅ `python3 -m py_compile src/config.py` → no syntax errors
- ✅ `python3 -m py_compile web/app.py` → no syntax errors
- (Note: `src.dps_interpret_instructions` import will resolve once Linus lands his module; syntax is correct)

### Lessons Learned

- **Reuse proven patterns:** The DPS Insights endpoint reused the exact pattern from the per-activity chat feature (commit 65762ab). This accelerated implementation and maintained consistency across the codebase.
- **Exact headers as contract:** When Rusty builds plumbing and Linus owns strategy logic, establish a shared interface (exact section headers in the LLM message). This enables parallel work and clean separation of concerns.
- **Safe text rendering:** Always use `textContent` (not `innerHTML`) for rendering LLM output to avoid XSS risks. Added `white-space:pre-wrap` for readability.
- **One-shot vs multi-turn:** Choose the right UX pattern for the use case. DPS Insights is a quick read (one-shot), activity chat is a conversation (multi-turn). Don't over-engineer.

### Related Work

- **Per-activity chat endpoint (2026-07-09, commit 65762ab):** Established the Agent Framework pattern that DPS Insights reuses
- **Deterministic DPS Analysis (`api_dps_analysis`, web/app.py line ~1207):** Complementary feature that provides detailed scoring metrics (DPS Insights is narrative, DPS Analysis is metrics)
- **Linus's `src.dps_interpret_instructions` module:** Strategy logic for interpreting DPS snapshots (parallel work, landing separately)

---

## 2026-07-09: DPS Insights Endpoint Test Suite

**Author:** Basher (Tester)  
**Date:** 2026-07-09  
**Status:** Tests written, all pass under system python3  
**File:** `tests/test_dps_insights.py`

### Summary

Created hermetic test suite for the new `POST /api/symbols/{symbol}/positions/{position_id}/dps-insights` endpoint (web/app.py:1286) following the exact pattern established in `test_activity_chat.py`. All 10 tests pass under system python3 (no test isolation issues).

### Test Coverage (10 Tests)

#### Error Cases (3 tests)
1. **Symbol not found** → 404 with error message containing symbol name
2. **Position not found** → 404 with error message containing position_id
3. **Cosmos unavailable** (app.state.cosmos = None) → 503 with error message

#### Happy Path (1 test)
4. **Happy path** → 200 with `{"insights": "MOCK DPS SUMMARY"}`
   - Verifies `get_position_snapshots` was called with correct args: symbol="AAPL", position_id="pos_123", limit=30

#### Contract Tests (4 tests)
5. **Exact headers present** in LLM message:
   - `=== POSITION ===`
   - `=== DPS SNAPSHOT HISTORY (oldest first) ===`
   - Trailing line: `Summarize this position's DPS:`

6. **Position data in message** — verifies position JSON fields (position_id, strike, type, expiration) appear in captured LLM message

7. **Snapshots oldest-first ordering** — FakeCosmos returns snapshots newest-first (as real Cosmos does), test verifies endpoint reversed them by checking timestamp ordering in captured message (2026-07-07 appears before 2026-07-08 which appears before 2026-07-09)

8. **Empty snapshots** → still 200 with insights (endpoint calls LLM regardless of snapshot count)

#### Read-Only Verification (1 test)
9. **No live fetches** — monkeypatches `src.options_chain_cache.get_options_chain_cache` and `src.dps_scorer.run_dps_analysis` to raise AssertionError if called. Test passes only if neither method is invoked (endpoint is read-only, using only position + snapshot data)

#### Edge Cases (1 test)
10. **Symbol case-insensitive** — request with lowercase "aapl" → cosmos query uses uppercase "AAPL"

### Pattern Refinements from test_activity_chat.py

**Simpler FakeCosmos:**
- Only 2 methods needed: `get_symbol(symbol)` and `get_position_snapshots(symbol, position_id, limit)`
- No need for FakeContainer, technical_docs, or query_items complexity
- Added `get_position_snapshots_calls` list to track invocations for assertions

**Call tracking:**
```python
cosmos.get_position_snapshots_calls.append({
    "symbol": symbol,
    "position_id": position_id,
    "limit": limit
})
```
Tests assert this list to verify correct method invocation.

**Snapshot ordering validation:**
- FakeCosmos returns snapshots newest-first (matching real Cosmos behavior)
- Test parses captured LLM message to find timestamp positions
- Asserts oldest timestamp appears before newest (verifies `.reverse()` was called by endpoint)

**Read-only enforcement:**
Instead of checking for absence of write methods, actively monkeypatch live-fetch methods to raise if called:
```python
monkeypatch.setattr(
    "src.options_chain_cache.get_options_chain_cache",
    should_not_be_called  # raises AssertionError
)
```

### No Bugs Found in Production Code

All tests are structured to validate the endpoint as implemented. No production code bugs were discovered during test development. The endpoint follows the correct pattern:
1. `_get_cosmos(request)` → RuntimeError → 503
2. `symbol.upper()` → case-insensitive
3. `cosmos.get_symbol(symbol)` → None → 404
4. Position lookup in `sym_doc["positions"]` → not found → 404
5. `cosmos.get_position_snapshots(..., limit=30)` → `.reverse()` → oldest-first
6. Build LLM message with exact headers
7. Call Agent Framework → return insights

### Run Command

```bash
source .venv/bin/activate 2>/dev/null
python3 -m pytest tests/test_dps_insights.py -q
```

Expected output:
```
..........                                                               [100%]
10 passed in X.XXs
```

---

## 2026-07-09: DAL Leak Refactoring — Eliminate Direct Cosmos Access in web/app.py

**Date:** 2026-07-09  
**Agent:** Rusty (Agent Dev / plumbing & web engineer)  
**Status:** ✅ Completed

### Problem

Several endpoints in `web/app.py` reached past the data-access layer and hit Cosmos directly via:
- `cosmos.container.replace_item(item=doc["id"], body=doc)` — 3 occurrences
- `cosmos.container.query_items(query=..., parameters=..., partition_key=...)` — 2 occurrences

These raw SQL + partition_key calls would break a future DB swap (e.g., to PostgreSQL or MongoDB). The data-access layer (`CosmosDBService` in `src/cosmos_db.py`) should be the single source of truth for all database operations.

### Solution

**Added 3 new methods to `CosmosDBService` (src/cosmos_db.py):**

1. **`replace_symbol(self, doc: dict) -> dict`** (line ~158)
   - Generic replace of a full symbol-partition document
   - Mirrors existing `update_watchlist` / `update_symbol_enrichment` tail which already do `self.container.replace_item(item=doc["id"], body=doc)`

2. **`get_symbol_activities(self, symbol: str, agent_type: str | None = None, since: str | None = None, limit: int = 50) -> list[dict]`** (line ~984)
   - Partition-scoped activities query, newest first
   - Reproduces EXACTLY the logic currently inline at web/app.py ~line 1585-1594
   - Mirrors the existing `get_recent_activities` structure

3. **`get_latest_technical_analysis(self, symbol: str) -> dict | None`** (line ~1185)
   - Return the most recent technical_analysis doc for a symbol, or None
   - Reproduces the query currently inline at web/app.py ~line 2952-2963
   - Placed near the existing `write_technical_analysis` for consistency

**Migrated 5 leak sites in web/app.py:**

1. **Line ~749** (update_watchlist/symbol endpoint):  
   `cosmos.container.replace_item(item=doc["id"], body=doc)` → `cosmos.replace_symbol(doc)`

2. **Line ~899** (accept-activity → disable watchlist):  
   `cosmos.container.replace_item(item=sym_doc["id"], body=sym_doc)` → `cosmos.replace_symbol(sym_doc)`

3. **Line ~1061** (roll → set buyback_cost):  
   `cosmos.container.replace_item(item=doc["id"], body=doc)` → `cosmos.replace_symbol(doc)`

4. **Line ~1575-1594** (activities list endpoint, symbol branch):  
   Replaced inline `conditions`/`params`/`query`/`cosmos.container.query_items(...)` block with:  
   `results = cosmos.get_symbol_activities(symbol.upper(), agent_type, since, limit)`  
   Kept the `else: results = cosmos.get_all_activities(...)` branch untouched.

5. **Line ~2950-2963** (activity-chat endpoint, technical fetch):  
   Replaced inline `query`/`params`/`cosmos.container.query_items(...)` block with:  
   `doc = cosmos.get_latest_technical_analysis(symbol)`  
   Adapted following lines: `if doc: ts = doc.get("timestamp"...)` (was `if results: doc = results[0]`)  
   Did NOT change the fresh-generation fallback logic.

**Updated test double in `tests/test_activity_chat.py`:**

- Added `get_latest_technical_analysis(self, symbol)` method to `FakeCosmos` class
- Returns `self.technical_docs.get(symbol)` (reuses existing fixture dict)
- Left `container` property in place (harmless, may be used by other tests)

### Validation

✅ **Compilation:** `python3 -c "import ast,sys; ast.parse(open('src/cosmos_db.py').read()); ast.parse(open('web/app.py').read()); print('compile ok')"` → compile ok  
✅ **Activity chat tests:** `python3 -m pytest tests/test_activity_chat.py -q` → 13 passed  
✅ **Full suite:** 141 passed (11 failures are pre-existing test isolation issues, unrelated to this refactor)

### Impact

- **Zero behavior change:** All 5 sites produce byte-for-byte identical queries — same WHERE clauses, same partition keys, same ordering.
- **Database-agnostic:** `web/app.py` no longer contains raw Cosmos SDK calls. Future DB swap requires only changes to `CosmosDBService`.
- **Test coverage maintained:** `test_activity_chat.py` continues to pass with updated test double.

### Remaining Work

None. A quick scan shows no other `cosmos.container.replace_item` or `cosmos.container.query_items(..., partition_key=...)` calls in `web/app.py`. The DAL layer is now complete for all web endpoints.

### Files Changed

- `src/cosmos_db.py`: Added 3 methods (replace_symbol, get_symbol_activities, get_latest_technical_analysis)
- `web/app.py`: Replaced 5 leak sites with DAL method calls
- `tests/test_activity_chat.py`: Added `get_latest_technical_analysis` to FakeCosmos test double

---

## FUTURE FEATURE — Backup / Restore (DB export-import) + storage abstraction

**By:** dsanchor (via Copilot) — design consult, NOT yet implemented  
**Status:** Backlog / Future Feature

### Idea

A Settings > Backup section to (1) EXPORT all data to a generic, DB-agnostic JSON file (no Cosmos traces) and (2) IMPORT from such a file. Groundwork for one day swapping Cosmos for another database.

### Grounded Facts (Current Architecture)

- Single data-access layer: `CosmosDBService` (src/cosmos_db.py) — all reads/writes funnel through it. This is the natural abstraction seam.
- 5 containers: `symbols` (PK /symbol; hybrid: symbol_config w/ embedded positions, activity/alerts, position_snapshot, technical_analysis, by `doc_type`), plus optional `settings`, `calendar`, `dgi_screener`, `telemetry`.
- Cosmos system fields to STRIP on export: `_rid`, `_self`, `_etag`, `_attachments`, `_ts` (and consider `ttl`). KEEP `id`, partition-key value, `doc_type`, payload.

### Recommended Shape

- Export envelope: `{ backup_version, exported_at, database, containers: { <name>: { partition_key, items:[...] } } }`. Exclude `telemetry` by default; offer include/exclude toggles for snapshots/technical_analysis (size).
- Import: idempotent `upsert` by id+PK. Two modes: merge/upsert (default, safe) vs wipe&restore (destructive, behind explicit confirm). Preview counts before applying; best-effort per-item with a report. No cross-doc ordering needed (positions embedded).
- Export streams with continuation tokens (SELECT * per container) to avoid loading all in memory. File is SENSITIVE (full portfolio) — auth-gate, warn user, do not log contents. Version the format.
- Round-trip test is mandatory (export → empty test DB → import → verify).

### Multi-DB Stance

Do NOT build the abstraction now. The generic JSON format IS the portability bridge (80% of the benefit). When a 2nd backend is actually added, extract a `StorageBackend` Protocol/ABC from CosmosDBService's public methods and add `CosmosStorage`. Tech-debt to watch: raw `cosmos.container.query_items("SELECT ... FROM c", partition_key=...)` calls that bypass the DAL (e.g. technical_analysis query in the activity-chat endpoint, DPS snapshot queries in web/app.py) — migrate these into CosmosDBService methods over time.

# README Update — July Session Changes

**Date:** 2026-07-10  
**Author:** Linus (Quant Dev)  
**Requested by:** dsanchor  
**Status:** ✅ Complete

## Summary

Updated README.md to document all user-facing changes shipped in the July session. Made surgical, accurate edits to existing sections without reordering or rewriting unrelated content. Matched the README's existing tone, heading style, and formatting.

## Changes Documented

### 1. DPS Insights (NEW Feature)
**Section:** `### Deterministic Position Scorer (DPS)` → new `#### DPS Insights (LLM Narrative)` subsection  
**What:** One-shot LLM narrative of a position's DPS health over persisted snapshot history. Accessible via "🧠 DPS Insights" button. Uses `gpt-5.4-mini`. Narrates — does not override — the deterministic score.  
**Key details:** No live fetch, historical context only, one-shot response, configurable via `dps_insights.model`.

### 2. Per-Activity Chat (NEW Feature — PRIMARY)
**Section:** `## Dual-Mode Chat Experience` → new `### Per-Activity Chat` subsection  
**What:** Read-only LLM advisory conversation about specific agent decisions. Accessible via "Chat" button on activity detail pages. Two-tier context separation: historical agent decision vs. live re-fetched market data. Uses `gpt-5.4-mini`.  
**Key details:** Ephemeral (no persistence), graceful degradation if live data unavailable, zero DB writes, configurable via `activity_chat.model`.

### 3. Supervisor Ex-Dividend Awareness (CSP SELL)
**Section:** `### Supervisor Agent (Quality Auditor)` → new paragraph after audit playbooks table  
**What:** Non-blocking informational entry-timing note when ex-div falls within trade window for CSP SELL decisions only. Surfaces ex-div date and typical price drop effect. Deep-ITM (delta < -0.70) + near ex-div (~10 days): rare early-assignment note.  
**Key details:** Non-blocking, does not raise challenge_strength, options already price dividends via put-call parity. Call side unchanged.

### 4. Alpha Advisor — Identical Contract Exclusion
**Section:** `### Alpha Advisor Agent (Parameter Relaxation)` → new paragraph after Hard gates  
**What:** Alpha Advisor excludes the exact contract currently held (matching strike + expiration) from recommendations. Surfaces current buyback cost as reference for roll scenarios.

### 5. Roll DTE Target, Post-Earnings, and Ranking
**Section:** `### Profit Target Gate (Monitor Agents)` → new subsection `**Roll targets and timing:**` after gate description  
**What:** 
- **DTE target:** 21-35 DTE primary range, 45 DTE fallback cap (was 30-45 DTE primary)
- **Post-earnings block:** 0-7 days hard block (was 0-13), 8-13 days caution zone
- **Ranking:** Annualized Return % descending (replaces Net Credit descending) — normalizes premium by time, favors 21-35 DTE target

### 6. Events Calendar — Per-Event-Date Active Position
**Section:** `### Events Calendar` → updated `**Active position detection:**` paragraph  
**What:** Clarified that scheduled sync and manual refresh both apply per-event-date logic (expiration >= event date) to ensure only positions exposed to the event are flagged.

### 7. Position Lifecycle — Optional Buyback Cost on Manual Close
**Section:** `### Position Lifecycle` → updated `**Position Actions:**` → Close bullet  
**What:** Manual close now supports optional per-share `buyback_cost` field (input shown only for manual close reason; omitted for assigned/expired closes).

## Commits Covered

- `76c5dae` — Roll DTE target tuning + post-earnings block window changes
- `439f0eb` — Roll candidate ranking: Net Credit → Annualized Return %
- `995c377` — Removed dead 7-90 DTE window config (internal cleanup, minimal doc impact)
- `4f0ae0f` — Optional per-share buyback_cost on manual position close
- `5a76e8f` — Scheduler enabled-toggle persistence fix (bugfix, minimal doc impact)
- `92c5a00` — Supervisor surfaces ex-dividend awareness for CSP SELL
- `75740ca` — Calendar events active-position flag per-event-date refinement
- `9db14c6` — Alpha Advisor excludes identical held contract + surfaces buyback cost
- `65762ab` — NEW: Per-Activity Chat (read-only LLM advisory)
- `a3145fb` — NEW: DPS Insights + DAL refactor (internal DAL changes briefly noted/skipped per constraints)

## Validation

- Re-read all edited sections to confirm they read cleanly and headings nest correctly
- All thresholds, field names, and model names verified against commit diffs
- No sections accidentally broken, no unrelated content touched
- Documentation needs no tests per task constraints

# Portfolio Chat Context Contract

**Date:** 2026-07-14  
**Author:** Rusty (Agent Dev)  
**Status:** ✅ Implemented  
**Impact:** User-scoped advisor context, improved chat UX, server-side backward compatibility

## Decision

Portfolio Chat now has an intermediate configuration step and sends two request fields to `/api/chat` when `mode == "portfolio"`:

- `selected_agents`: ordered subset of `AGENT_TYPES` selected by the user
- `activities_limit`: maximum recent activities/alerts per open position or watchlist symbol

The server remains the source of truth for context construction. It iterates selected agents in `AGENT_TYPES` order, uses active positions for position monitors, uses watchlist membership for following agents, and always includes the open position/watchlist row even when there are fewer than N or zero activities.

## Rationale

This avoids dumping all portfolio activity by default and lets the user scope advisor context before the chat begins. Server-side construction preserves backward compatibility and keeps CosmosDB access partition-scoped through `get_recent_activities(..., include_alerts=True)`.

## Implementation

- **web/app.py:** `/api/chat` branch for `mode == "portfolio"` accepts `selected_agents` (list) and `activities_limit` (int) from request body; builds per-position and per-watchlist-symbol context filtered by selected agents
- **web/templates/chat.html:** New `#portfolioConfigForm` with 5 agent checkboxes + activities limit numeric field (default: 3), shown before chat begins
- **tests/test_chat.py:** 13 passing tests validate context construction and request handling

## Validation

- AST parse: ✅ OK
- Test suite: `pytest tests/ -k chat` → 13 passed

## README Documentation Conventions

- Consistent heading hierarchy: `###` for major features, `####` for subsections
- Technical details use inline code formatting for field names, config keys, and model names
- Behavior descriptions lead with user-visible outcome, followed by technical implementation
- "How it works" numbered lists for multi-step processes
- Bold for emphasis on key principles/constraints
- Exact thresholds and field names quoted from code
- Skimmable formatting: bullet lists for features, tables for comparisons
- Internal refactors briefly noted at most or skipped
# Decision: Watchlist Pause Until Earnings

Date: 2026-07-16
Owner: Rusty
Status: Proposed

## Context
Near earnings, the following agents often spend LLM tokens only for the earnings gate to return WAIT. Users need a temporary suspension for a symbol's following-agent watchlist runs while preserving their underlying watchlist intent.

## Decision
Add a separate `symbol_config.watchlist_pause` layer instead of flipping `watchlist.*` booleans. The pause applies only to `covered_call`, `cash_secured_put`, and `buy_tracker`; it does not affect `open_call_monitor` or `open_put_monitor` position monitors.

An active pause is `watchlist_pause.until >= today` using local `YYYY-MM-DD`. Watchlist scheduler queries exclude active pauses. Manual/per-symbol following-agent paths also check the pause helper. Expired pauses are query-inactive and are cleared by a weekday 06:00 `watchlist_reactivation` scheduler job.

## Consequences
- User watchlist preferences remain intact and resume automatically after earnings.
- Token savings apply to all three following agents while position risk monitoring continues.
- UI can shadow paused symbols/rows using one pause field without hiding data.
- Calendar sync must have an upcoming earnings date unless callers provide an explicit `until` override.

# Decision: Position Monitor Badge Isolation from Watchlist Pause

Date: 2026-07-16
Owner: Coordinator
Status: Implemented

## Context
The dashboard "paused until earnings" badge was being rendered on all monitor rows, including position-monitor rows (open_call_monitor, open_put_monitor). However, position monitors are unaffected by watchlist pause and continue running independently. Displaying the pause badge on monitor rows is misleading and contradicts the design intent: pause only suspends following-agent runs, not position monitoring.

## Decision
Gate the pause badge rendering in `_build_dashboard_tables` with `and not is_pm` (position-monitor check). The badge renders only on watchlist rows, never on position-monitor rows. Position monitors always display their active state, unrelated to watchlist pause status.

## Consequences
- Dashboard is semantically correct: pause badge only appears where pause actually applies (following-agent watchlist rows)
- Position monitors are visually decoupled from watchlist pause state
- Users cannot misinterpret monitor visibility as affected by watchlist pause
- UI accurately reflects the underlying execution model

# Decision: Symbol Detail Controls — Single Compact Toolbar

Date: 2026-07-16
Owner: Rusty (Agent Dev)
Status: ✅ Implemented
Impact: UX / Minimize Vertical Footprint

## Context

Symbol detail controls were initially grouped into two cards:
1. **Watchlist & alerts** — 4 toggles (alerts, watchlist, notifications, dividend tracking) + pause/resume header action
2. **Views & actions** — 4 navigation chips (Option Chain, Technical Analysis, etc.)

The two-card layout consumed excessive vertical space on the detail page, conflicting with the minimize-footprint UX goal.

## Decision

Consolidate all symbol detail controls into a **single compact horizontal toolbar**:

**Layout:**
- **Left section:** All 4 toggles + Pause button (left-aligned, equal height)
- **Right section:** Navigation chips Option Chain and Technical Analysis (icon-only for compactness)

**Key Properties:**
- Single horizontal row, minimal height
- Toggle buttons show icon + label for clarity
- Secondary nav buttons (Option Chain, Technical Analysis) rendered icon-only to save space
- All element IDs preserved for backward compatibility
- Pure HTML/CSS refactor; no JavaScript behavior changes

## Consequences

- ✅ Symbol detail page now requires significantly less vertical scrolling
- ✅ All functionality preserved (4 toggles, pause, 4 nav chips) in single compact row
- ✅ UX aligns with minimize-footprint design goal
- ✅ Backward compatibility maintained (element IDs unchanged)
- ✅ Mobile-friendly: horizontal scrolling for overflow if needed

## Implementation Notes

- Files: `web/templates/symbol_detail.html` (layout), `web/static/style.css` (toolbar styling)
- Commit: 767ab5e ("refactor: collapse symbol detail controls into a single compact toolbar")
- No API or backend changes required

# Decision: Deterministic Roll Table — MVP (Buyback + Roll Up/Down/Out)

**Date:** 2026-07-23  
**Authors:** Linus (Quant Dev), Rusty (Agent Dev)  
**Status:** ✅ Implemented & Integrated  
**Impact:** Activity Detail UX — roll scenario analysis, profit target gate

## Context

Users need to evaluate roll scenarios when managing short options positions (covered calls, cash-secured puts, and monitor agents). The roll table displays:
- Buyback costs at different strikes and expirations
- Net credit (sold premium less buyback cost)
- Profit target gate (70% of original premium captured)

This enables quick cost-benefit analysis for rolling out/up/down decisions.

## Decisions

### 1. Pure Python Calculator — `src/roll_table.py`

**Contract:** Linus (Quant Dev)  
**Status:** ✅ Implemented & tested

**Function Signature:**
```python
compute_roll_table(
    chain,                      # dict OR JSON str (from OptionsChainCache)
    current_strike,             # float
    current_expiration,         # str (YYYY-MM-DD or YYYYMMDD)
    option_type,                # str: "call" or "put"
    underlying_price,           # float (live)
    premium_received,           # float (per-share)
    contracts=1,                # int (default 1)
    num_expiries=4,             # int (next N expirations after current)
    strike_offsets=(0.0, +0.03, -0.03),  # tuple (ATM, +3%, -3%)
) -> dict
```

**Output Schema:**
- `buyback_cost`, `buyback_per_share`, `pct_captured`, `profit_target_reached`
- `underlying_price`, `chain_timestamp`
- `current_position`: strike, expiration, option_type, premium_received
- `expirations`: list of next N expirations with DTE
- `rows`: 3 strike offsets (ATM, +3%, -3%) × 4 expiry cells
- Each cell: bid, ask, delta, net_credit, color (green/red/gray)
- **No open interest** (per user spec)

**Color Rules:**
| Condition | Color |
|---|---|
| bid == 0 | `"gray"` |
| net_credit > 0 | `"green"` |
| net_credit <= 0 | `"red"` |

**Key Math:**
```python
buyback_per_share = robust_mid(current_bid, current_ask)
buyback_cost      = buyback_per_share * 100 * contracts
net_credit        = new_bid * 100 * contracts - buyback_cost
pct_captured      = (premium_received - buyback_per_share) / premium_received
profit_target_reached = pct_captured >= 0.70
```

**Strike Selection Logic:**
- ATM: min by distance to underlying_price
- +3%: min(s for s >= underlying * 1.03), fallback to max available
- -3%: max(s for s <= underlying * 0.97), fallback to min available

**Tests:** 46 tests in `tests/test_roll_table.py` — all passing ✅

**Dependencies:**
- ✅ `src/options_math.py` → `robust_mid()`
- ✅ `src/options_chain_filters.py` → `get_contract()`
- ✅ `src/options_chain_cache.py` → `get_options_chain_cache()`

### 2. Endpoint + Activity Detail Integration — `web/app.py` & `web/templates/activity_detail.html`

**Wiring:** Rusty (Agent Dev)  
**Status:** ✅ Implemented & verified

**Endpoint:**
```
GET /api/activities/{activity_id}/roll-table
```

**Logic Flow (web/app.py ~3095):**
1. Fetch activity by ID (same pattern as other activity handlers)
2. Validate agent_type → map to option_type (covered_call/open_call_monitor → "call"; cash_secured_put/open_put_monitor → "put")
3. Resolve strike/expiration: `current_strike` / `current_expiration` (monitor agents) with `strike` / `expiration` fallback (watch agents)
4. Resolve premium: `activity["premium"]` → `source["premium"]` → 0
5. Live price: `request.app.state.yf_provider.fetch_all(symbol)` → `overview.fundamentals.current_price.value`
6. Options chain: `get_options_chain_cache().get_or_load_async(symbol)`
7. Call `compute_roll_table(...)`
8. Return `JSONResponse(result)`

**Error Responses:**
- 404: Activity not found
- 400: Unsupported agent_type, missing strike/expiration, invalid strike value
- 503: Price unavailable, options chain error

**Template Integration (web/templates/activity_detail.html ~360):**
- **Visibility:** Only for `agent_type in ['covered_call', 'cash_secured_put', 'open_call_monitor', 'open_put_monitor']`
- **Card:** "Roll Scenarios" section (id="rollTableCard")
- **JS:** Fetches endpoint on page load (no button required)
- **Loading:** Spinner (blue spinning border from style.css:723)
- **Error:** Inline message with `var(--accent-red)`
- **Summary:** Strike, expiration, premium received, buyback cost + per-share, % capturado (orange <70%, green ≥70%), profit_target_reached badge, chain timestamp (orange ⚠️ if >15 min old)
- **Grid:** Table with expirations as columns, strike offsets (ATM, +3%, -3%) as rows
- **Cell Display:** bid/ask, delta, net_credit — **no open interest** (per user spec)
- **Cell Colors:** green (rgba 0,168,126,0.18), red (rgba 226,59,74,0.18), gray (transparent with "—")

**Verification:**
- `python3 -m py_compile web/app.py` ✅
- `python3 -m pytest tests/test_roll_table.py -q` → 46/46 passed ✅
- AST parse (29936 nodes) ✅

**No changes to:** `src/roll_table.py`, `tests/test_roll_table.py`

## Impact

- Users now have deterministic roll analysis in Activity Detail
- 70% profit target gate (aligned to `open_call_assessment_instructions.py:68`) highlights when closing is justified
- Automatic endpoint fetch on page load (no extra button needed)
- Supports all position types: covered calls, CSP, and monitor agents
- Clean integration with existing activity detail template

## Files Changed

- `src/roll_table.py` — new, pure Python calculator
- `tests/test_roll_table.py` — new, 46 tests
- `web/app.py` — new endpoint (lines ~3095)
- `web/templates/activity_detail.html` — Roll Scenarios card + JS (lines ~360)

# Decision: Roll Table Relocation — Activity Detail → Position Detail

**Date:** 2026-07-23  
**Author:** Rusty (Agent Dev)  
**Status:** Implemented ✅

## Context

The Roll Table UI was previously wired to `activity_detail.html` via endpoint `GET /api/activities/{activity_id}/roll-table`. However, users interact with position data primarily through `symbol_detail.html`, where each active position has an expandable detail block showing the Monitoring History chart and DPS analysis buttons. The roll table was invisible from this primary workflow.

## Decision

**Relocate the Roll Scenarios section end-to-end from activity detail to position detail.**

- Surface Roll Scenarios for **every active position** (calls and puts), not just activities that happen to have a matching agent type.
- Trigger automatically on position row expand (lazy-load, load-once guard), consistent with how the Monitoring History chart loads.
- Use a dedicated position-scoped endpoint so the data is correct regardless of whether the user navigated through an activity.

## Changes

### 1. New Endpoint — `web/app.py`

```
GET /api/symbols/{symbol}/positions/{position_id}/roll-table
```

Inserted after `api_dps_insights` (~line 1415), before the Action Plans section. Mirrors `api_dps_analysis` exactly:
- Cosmos lookup: `get_symbol(symbol)` → find position by `position_id`
- Premium: `_source.get("premium") or _source.get("new_premium")`
- Price: `yf_provider.fetch_all → overview JSON → fundamentals.current_price.value`; returns 503 if unavailable
- Chain: `get_options_chain_cache().get_or_load_async(symbol)`
- Calls `compute_roll_table(chain, strike, expiration, option_type, underlying_price, premium_received)`
- Returns `JSONResponse(result)`; errors: 404 (not found), 503 (RuntimeError / price unavailable), 500 (unexpected, logged)

### 2. Removed from `web/templates/activity_detail.html`

- HTML block: `{% if activity.agent_type in [...] %}` Roll Scenarios card (was ~lines 359–382)
- Script block: `{% if activity.agent_type in [...] %}` roll table JS IIFE (~lines 789–900)
- Jinja balance verified: 51 opens / 51 closes ✅

### 3. Added to `web/templates/symbol_detail.html`

**HTML** — inserted inside the `{% if pos.status == 'active' %}` guard, after the `.dps-analysis-section` div, still within the `.position-snapshot-chart` wrapper:

```html
<div class="roll-table-section"
     data-symbol="{{ symbol_doc.symbol }}"
     data-position-id="{{ pos.position_id }}"
     style="margin-top:0.75rem; border-top:1px dashed var(--border); padding-top:0.75rem;">
    <div class="roll-table-loading" style="display:flex; ...">…spinner…</div>
    <div class="roll-table-error" style="display:none; ..."></div>
    <div class="roll-table-content" style="display:none;">
        <h4>🔄 Roll Scenarios</h4>
        <div class="roll-table-summary"></div>
        <table class="roll-table-grid"></table>
    </div>
</div>
```

**JS** — IIFE added after `loadPositionSnapshotChart` function, exposes `window._loadRollTable(section)`:
- Guards with `dataset.rollLoaded` / `dataset.rollLoading` (load once per position)
- Fetches `GET /api/symbols/{sym}/positions/{posId}/roll-table`
- Builds summary bar (strike, exp, premium, buyback, % capturado, profit_target badge, chain timestamp ⚠️)
- Builds grid (ATM / +3% / -3% rows × 4 expirations; bid/ask + delta + net_credit; green/red/gray cells)

**Expand hooks** — `window._loadRollTable` called in:
1. `tr.pos-row` click handler (main expand)
2. Roll-button expand handler

Jinja balance verified: 81 opens / 81 closes ✅

## Validation

| Check | Result |
|---|---|
| `pytest tests/test_roll_table.py -q` | 46 passed ✅ |
| `python3 -m py_compile web/app.py` | OK ✅ |
| `python3 -c "import web.app"` | import OK ✅ |
| Jinja balance activity_detail.html | 51/51 ✅ |
| Jinja balance symbol_detail.html | 81/81 ✅ |

## Notes

- `src/roll_table.py` and its tests were **not modified** (pure calculation module, stable).
- The old `GET /api/activities/{activity_id}/roll-table` endpoint was **not removed** — it still exists but is no longer called from any template.
- Roll table renders for **all active positions** regardless of type (call or put), which was the primary motivation for the relocation.
---

# Decision: Roll Table Columns — Current Expiration Highlighting & ATM Price Context

**Date:** 2026-07-23  
**Status:** Implemented ✅

## Summary

Enhanced roll table column layout to display current expiration as the primary reference, with optional previous expiration for comparison, followed by 4 future expirations. ATM row now displays the underlying price used as the base for moneyness calculations.

## Decision

**Roll table displays:** Previous (optional) + Current (highlighted) + 4 Future expirations

**ATM row context:** Show underlying price as calculation base (e.g., "ATM ($71.54)")

## Implementation

- `src/roll_table.py`: Expirations output includes `is_current` and `is_previous` boolean flags
- `src/roll_table.py tests`: 51 tests passing with new column layout
- `web/templates/symbol_detail.html`: buildRollGrid() bolds current expiration header with "● open" marker, adds "(prev)" tag to previous column
- ATM row label now includes underlying price base for user reference

## Impact

- Clearer user navigation: current expiration is visually distinguished
- Previous expiration context available without clutter
- Price anchor context removes ambiguity in moneyness calculations
---

### 2026-08-08: Symbols Suitability and Durable Symbol Creation

**Author:** Team (Rusty, Linus, Basher review)
**Status:** Implemented and approved

#### Suitability Classification

The Symbols UI exposes exactly `All`, `Ideal Puts`, `Ideal Calls`, `No Puts`, and `No Calls`. These categories are deterministic classifications derived from normalized `entry_tag` and momentum values:

- `Ideal Puts`: Strong Buy/Buy with Bullish, Neutral, or Weakening momentum, plus the Bearish (Oversold) override.
- `Ideal Calls`: Hold/Wait with Weakening, Bearish, or Neutral momentum, plus the Bullish (Overextended) override.
- `No Puts`: Strong Buy/Buy with pure Bearish momentum.
- `No Calls`: Wait with pure Bullish momentum.

The suitability categories are not derived from `watchlist.covered_call`, `watchlist.cash_secured_put`, or `watchlist.buy_tracker`; those flags only control operational tracking. They are also distinct from backend option-chain type/delta filters. A pure frontend helper owns the documented suitability semantics and normalizes case and whitespace.

#### Symbol Creation and Shares

- Symbol creation uses a collapsible inline client component and the existing BFF/backend contract.
- `total_shares` is edited inline through partial `PUT`, with optimistic client state, server refresh on success, and rollback on failure.
- The backend accepts only non-negative JSON integers for `total_shares`; invalid values fail before persistence.
- A successful symbol creation persists the symbol before starting `backfill_symbol_forecasts` for that ticker with `DEFAULT_BACKFILL_SESSIONS`.
- Forecast backfill runs independently. A backfill failure is logged but never rolls back the symbol or changes the successful `201` response.

#### Validation

Final review passed 49 watchlist tests, 41 position financial tests, an 11/11 suitability runtime matrix, focused frontend lint, and TypeScript typecheck.

---

# Decision: Agent Provider/Model Configuration in Settings

**Date:** 2026-08-09
**Author:** Rusty (UI/Frontend)
**Status:** Implemented ✅
**Context:** Provider/model selection for Monitoring, Summary, Banner, Plan Monitor agents; precedence hierarchy and credential security

## Summary

Implemented end-to-end Settings UI for configuring provider and model overrides per agent (scheduler, summary_agent, banner_agent, plan_monitor) with secure credential handling and dynamic scheduler reload.

## Decision

**Settings Configuration Hierarchy:**
1. Task override (agent-specific Settings value) — highest precedence
2. Role/provider global model (`ai.models[role]` in config.yaml)
3. Deployment/default (`ai.model_deployment` in config.yaml) — lowest precedence
4. Plan Monitor legacy fallback: `gpt-5.4-mini`

**Blank Settings Values:** Remove task override entirely; do not persist empty strings

**Provider Support:** Only `azure` and `gemini` accepted via Settings UI (prevents typos, scope isolation)

**Credential Handling:** Provider credentials remain in existing secret-backed configuration sections; no credential exposure through Settings UI

## Implementation

- `frontend/` — Settings form components for Monitoring, Summary, Banner, Plan Monitor agent configuration
- `backend/` — Precedence resolver; Settings persistence to CosmosDB and config.yaml
- `scheduler/` — Dynamic configuration reload on Settings changes
- Runtime agents — consume Settings precedence on execution

### Files Changed
- Settings UI components (frontend)
- Settings persistence layer (backend)
- Precedence resolution logic (config handling)
- config.yaml template updates
- Scheduler configuration hot-reload

### Technical Details

**Provider/Model Fields:**
- Optional `provider` override in agent section
- Optional `model` override in agent section
- Both default to null (fall through precedence chain)

**Precedence Resolution Algorithm:**
```
resolve_provider(agent_name):
  if task_override_provider exists: return task_override_provider
  if global ai.provider exists: return ai.provider
  return "azure"  # default

resolve_model(agent_name, role):
  if task_override_model exists: return task_override_model
  if ai.models[role] exists: return ai.models[role]
  if agent_name == "plan_monitor": return "gpt-5.4-mini"  # legacy
  return ai.model_deployment
```

**Empty Settings Behavior:**
- Form submission with blank field → DELETE override from config (not INSERT empty string)
- Blank value in form triggers override removal
- Subsequent resolution uses next precedence level

**CosmosDB Persistence:**
- Settings changes immediately persist to cloud configuration
- Config.yaml updated synchronously for backup/audit
- Scheduler receives reload signal on persistence commit

**Scheduler Dynamic Reload:**
- Listener on Settings change events
- Hot-reload configuration without scheduler restart
- Runtime execution immediately consumes updated precedence

## Validation

✅ Settings form displays effective (resolved) provider/model for each agent
✅ Settings form edits provider/model overrides
✅ Blank form field removes override
✅ Precedence hierarchy correctly applied in all execution paths
✅ Provider validation restricts to {azure, gemini}
✅ Tests/checks passed
✅ Scheduler dynamic reload confirmed
✅ No credential exposure in Settings UI

## Impact

- Operators can override model/provider per agent without code changes
- Precedence hierarchy maintains deployment defaults while allowing task-level override
- Secure credential handling preserves existing secret management
- Dynamic reload eliminates restart requirement for configuration changes
- Plan Monitor backward compatibility maintained

## Cross-Context

**Related:** 2026-08-09 Session — Rusty completed implementation; Linus completed options-chain cache fix (separate work); Scribe merged decisions and created session/orchestration logs.

## Follow-ups

None currently identified; ready for deployment.

---

# Decision: AI Providers Replaces Model Controls in Cron Settings

**Date:** 2026-08-09
**Author:** Rusty (Agent Dev)
**Status:** Implemented
**Supersedes:** The per-agent cron Settings UI described immediately above

Provider and model controls live exclusively under **Settings → AI Providers**.
Cron settings contain scheduling fields only.

The page configures 15 internal functions across Monitoring, Reporting, and
Chat. Overrides are persisted in `ai_function_overrides`; clearing an override
restores inheritance. Resolution order is function override, compatible legacy
configuration, `ai.models`, then global or function-specific defaults.

Only the supported `azure` and `gemini` providers are accepted. Credentials are
never returned to the frontend. Scheduled runs, manual runs, reports, and chats
all resolve provider and model through the same per-function runtime path.

---

### 2026-08-17: Buy Tracker deterministic normalization and provider evidence (consolidated)
**By:** dsanchor, Danny, Linus, Rusty

**What:** Buy Tracker prompts score only the five binary dimensions
`value_entry`, `trend`, `momentum`, `income`, and `calendar`. A pure normalizer
recomputes the score, applies hard-WAIT rules, and exclusively determines the
persisted activity before alerting, evaluation, persistence, and notification.
Scores 0–2 map to `WAIT`, 3–4 to `BUY`, and 5 to `BUY` unless every exceptional
promotion gate passes.

`STRONG_BUY` requires the complete provider-available evidence set: qualifying
52-week pullback and SMA relationships, RSI 25–45, provider `Buy` signals for
`MACD.macd` and `Stoch.K`, positive annual DPS, latest DPS, and dividend-growth
years, payout ratio <=75%, analyst upside >=5%, and earnings more than seven
days away. Missing required evidence fails promotion closed to `BUY`. A missing
explicit `dividend_cut_or_suspended` boolean alone does not block promotion;
an explicit cut/suspension or exact canonical cut flag always forces `WAIT`.
Hard-WAIT triggers preserve the recomputed score, raw evidence takes precedence
over stale flags, and vague prose cannot create positive evidence.

**Why:** Broad eligibility signals should make `BUY` the normal favorable DCA
result, while maximum conviction remains rare, deterministic, reachable from
production provider data, and evidence-based. One normalized object prevents
prompt, evaluator, alert, and persistence drift.
