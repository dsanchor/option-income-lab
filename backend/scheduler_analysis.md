# Stock Options Manager - Scheduler Analysis

**Analyst:** Rusty (Agent Dev)  
**Date:** 2026-06-26  
**Scope:** Complete scheduler architecture, task inventory, and improvement recommendations

---

## Part A: How the Scheduler Works

### Entrypoints

**Primary Entrypoint:** `run.py`
- Default mode: Starts both web dashboard + scheduler (unified mode)
- `--web-only`: Web dashboard only
- `--scheduler-only`: Scheduler only
- `run_web.py`: Backwards-compatible alias for `--web-only`

**Scheduler Class:** `OptionsAgentScheduler` in `src/main.py`
- Initialized by `run.py` in a daemon thread when running unified mode
- Or directly via `python src/main.py` or `python run.py --scheduler-only`

### Scheduling Mechanism

**Technology:** `croniter` library for cron expression parsing
- Not APScheduler, not while-True with naive intervals
- Clean cron-based scheduling with proper timezone support

**Core Loop Architecture:**
1. **Setup phase** (lines 149-263): 
   - Loads config from `config.yaml` + CosmosDB settings overlay
   - Initializes CosmosDB, AgentRunner, ContextProvider
   - Parses 8 independent cron expressions (main + 7 sub-schedulers)
   - Each scheduler has: enabled flag, cron expression, next_run timestamp

2. **Main loop** (lines 916-1130):
   - 1-second polling interval (line 1122)
   - Each iteration checks: heartbeat, config reload, 8 independent schedulers
   - When `now >= next_run`, executes task and recalculates next_run
   - Individual try/except around each task — one failure doesn't kill the loop

3. **Concurrency model:**
   - Sequential execution within the scheduler (no parallelism)
   - Each agent task runs to completion before checking next task
   - No overlap protection — if a task runs long, next trigger might be delayed

4. **Error handling:**
   - Per-task try/except (lines 1053-1056, 1062-1065, etc.)
   - Outer loop recovery catch (lines 1123-1127) prevents full crash
   - Logs errors, sleeps 5s on loop exception, continues

### Control Flow

```
run.py
  └─> OptionsAgentScheduler.__init__()
      └─> scheduler.run()
          ├─> setup()
          │   ├─> Config() loads config.yaml + .env
          │   ├─> CosmosDBService() connects to Cosmos
          │   ├─> merge_defaults() overlays CosmosDB settings over config.yaml
          │   └─> AgentRunner() initializes Agent Framework + LLM
          └─> while self.running:
              ├─> heartbeat log every 10 min (line 920)
              ├─> _reload_config_from_cosmos() every 60s (line 926)
              ├─> check 8 schedulers: if now >= next_run → execute
              │   ├─> run_all_agents()              [main monitors]
              │   ├─> run_summary_agent_job()       [daily summary]
              │   ├─> run_plan_monitor_job()        [planned trade monitor]
              │   ├─> run_options_chain_fetch_job() [cache refresh]
              │   ├─> run_dgi_screener_job()        [DGI screener]
              │   ├─> run_banner_agent_job()        [dashboard banner]
              │   ├─> run_calendar_sync_job()       [earnings/ex-div dates]
              │   └─> run_portfolio_enrichment_job()[DGI enrichment]
              └─> sleep(1)
```

### Market Hours Gating

**File:** `src/market_hours.py`
- **Method:** Live options bid/ask probe on MSFT ATM call (nearest expiration)
- **Logic:** If bid > 0 or ask > 0 → market open, else closed
- **Cache:** 5-minute TTL to avoid excessive yfinance calls
- **NOT used in scheduler** — agents themselves may call it, but scheduler runs on cron regardless of market state

### Configuration Management

**Two-tier config:**
1. **`config.yaml`** — static defaults, committed to repo
2. **CosmosDB settings** — dynamic overrides, persisted in `settings` container

**Reload mechanism:**
- `_reload_config_from_cosmos()` runs every 60s (line 926)
- Compares new vs current cron for each scheduler
- Sets `_<task>_cron_changed` flag
- Main loop detects flag, reinitializes croniter, logs change (lines 931-1047)

**Reschedule API:**
- Web UI calls `scheduler.reschedule_<task>(new_cron)` (methods lines 95-147)
- Sets flag, config is updated on next loop iteration

### Crash Recovery

- Signal handlers for SIGINT/SIGTERM (lines 547-550, 754-755)
- Outer try/except in loop (lines 1123-1127)
- Sets `self.alive = False` on shutdown (line 1129)
- Web UI can check `scheduler.alive` for health status

---

## Part B: Complete Task Inventory

| Task Name | What It Does | Trigger Cadence | Market Hours Gating | Agent/Module Invoked | Config Key |
|-----------|-------------|-----------------|---------------------|---------------------|------------|
| **Monitor Agents** | Runs 5 main monitoring agents for all enabled symbols | `30 9-16/4 * * 1-5` (every 4h starting 9:30 AM, weekdays) | No (cron-based) | `covered_call`, `cash_secured_put`, `buy_tracker`, `open_call_monitor`, `open_put_monitor` | `scheduler.cron` |
| **Summary Agent** | Daily activity digest for all symbols | `0 8 * * *` (8 AM daily) | No | `runner.run_summary_agent()` | `summary_agent.cron` |
| **Plan Monitor** | Checks "planned" trades for entry conditions | `0 4,16 * * 1-5` (4 AM, 4 PM weekdays) | No | `runner.run_plan_monitor()` | `plan_monitor.cron` |
| **Options Chain Scheduler** | Refreshes options chain cache for all symbols | `0 * * * *` (hourly) | No | `options_chain_cache.refresh_all()` | `options_chain_scheduler.cron` |
| **DGI Screener** | Screens S&P 500 for dividend growth opportunities | `0 6 * * 1-5` (6 AM weekdays) | No | `run_dgi_screener()` | `dgi_screener.cron` |
| **Dashboard Banner** | Generates priority banner items for web UI | `0 5 * * *` (5 AM daily) | No | `run_banner_agent()` | `banner_agent.cron` |
| **Calendar Sync** | Fetches earnings + ex-dividend dates from yfinance | `0 5 * * 1-5` (5 AM weekdays) | No | `_run_calendar_sync_async()` | `calendar_sync.cron` |
| **Portfolio Enrichment** | DGI-style quality scoring for portfolio symbols | `0 9-17 * * 1-5` (hourly 9-17, weekdays) | No | `run_portfolio_enrichment()` | `portfolio_enrichment.cron` |
| **DPS Cron** (ORPHANED) | Daily DPS score calculation for active positions | `0 22 * * 1-5` (10 PM weekdays) | No | `run_dps_cron()` in `src/dps_cron.py` | `dps_scorer.cron` |

### Notes on Each Task

1. **Monitor Agents** (lines 265-298):
   - Sequentially runs 5 agents: covered call, cash-secured put, buy tracker, open call monitor, open put monitor
   - Each agent iterates over enabled symbols from CosmosDB
   - Symbol randomization enabled by default (`yfinance_randomize_symbols`)
   - Uses `_run_async()` helper to bridge sync scheduler → async agent code

2. **Summary Agent** (lines 300-322):
   - Configurable activity count (default: 3 recent activities per symbol)
   - Enabled/disabled via `summary_agent.enabled` flag
   - Runs via `AgentRunner.run_summary_agent()` (not a standalone agent file)

3. **Plan Monitor** (lines 324-378):
   - Queries all plans with `status='planned'`
   - For each plan, calls `runner.run_plan_monitor()` with plan + symbol doc
   - Checks entry conditions, generates alerts
   - Model configurable via `plan_monitor.model` (default: gpt-5.4-mini)

4. **Options Chain Scheduler** (lines 380-408):
   - Uses shared `OptionsChainCache` singleton
   - Refreshes all symbols in parallel (yfinance + TradingView merge logic)
   - Critical for option Greeks calculations in agents

5. **DGI Screener** (lines 410-432):
   - 100% programmatic (no LLM), uses yfinance for data
   - Configurable S&P 500 symbol list (config line 86)
   - Scores symbols on dividend yield, growth, payout safety, valuation, etc.
   - Top N results stored in CosmosDB

6. **Dashboard Banner** (lines 459-480):
   - LLM-based synthesis of priority items across portfolio
   - Categories: earnings_proximity, ex_div_proximity, trend_change, actionable_alert, risk_warning
   - Max items configurable (default: 10)
   - Model: `config.model_for('banner')` (default: gpt-5.4-mini)

7. **Calendar Sync** (lines 482-545):
   - Fetches earnings timestamp + ex-dividend date from yfinance `.info`
   - Stores in CosmosDB `calendar_events` container
   - Flags events for symbols with active positions

8. **Portfolio Enrichment** (lines 434-457):
   - Reuses DGI screener's `analyze_single_symbol()` for portfolio symbols
   - Stores `enrichment` field on each symbol doc (quality score, category, entry tag, technicals)
   - Runs hourly during market hours to keep enrichment fresh

9. **DPS Cron** (ORPHANED):
   - **Problem:** Defined in `src/dps_cron.py` but NOT scheduled in `src/main.py`
   - Config entry exists: `dps_scorer.cron: "0 22 * * 1-5"` (line 62-63 in config.yaml)
   - Never imported or called by the scheduler
   - Purpose: Compute Deterministic Position Score for active positions, store in snapshots

---

## Part C: Improvements Applied + Recommendations

### Issues Identified

1. **DPS Cron is orphaned** — config exists, code exists, never scheduled
2. **Excessive boilerplate** — 8 separate `_<task>_cron_changed` flags + reschedule methods
3. **No task registry** — tasks hardcoded in 8 separate if-blocks in main loop
4. **Duplication** — cron reload logic repeated 8 times (lines 931-1047)
5. **No shared error metrics** — each task logs independently, no aggregated stats
6. **Config reload inefficiency** — reloads full settings every 60s even if nothing changed

### Improvements Applied

#### 1. Add Missing DPS Scheduler (CRITICAL FIX)

**Problem:** DPS cron job never runs despite being configured.

**Fix:** Add DPS scheduler to main loop.

**Files Changed:**
- `src/main.py`

**Changes:**
1. Add `_dps_cron_changed` flag to `__init__` (line 86)
2. Add `reschedule_dps()` method (after line 127)
3. Add DPS cron initialization in `setup()` (after line 263)
4. Add DPS enabled check in config reload (after line 740)
5. Add DPS cron change handler in main loop (after line 1047)
6. Add DPS execution block in main loop (after line 1120)

**Impact:** DPS scoring now runs nightly at 10 PM as intended.

#### 2. Centralized Task Registry Pattern (REFACTOR)

**Problem:** 8 separate if-blocks, flags, and reschedule methods.

**Solution:** Introduce `TaskRegistry` to unify task management.

**Implementation:**
- Create `src/scheduler_tasks.py` with `ScheduledTask` dataclass + `TaskRegistry`
- Each task defines: name, cron_expr, enabled, job_func, config_key
- Registry handles: cron parsing, next_run calculation, config reload, execution

**Benefits:**
- New tasks require 1 line of registration vs 50+ lines of boilerplate
- Centralized error metrics (success/failure counts per task)
- Dynamic enable/disable without code changes

**Trade-off:** Adds abstraction layer — deferred to recommendation (see below)

#### 3. Config Reload Optimization

**Problem:** Full settings reload every 60s even if unchanged.

**Solution:** Add version/timestamp check in CosmosDB settings doc.

**Deferred to recommendation** — requires CosmosDB schema change.

---

### Recommendations (NOT Applied)

These improvements are architecturally sound but risky without testing. Documented for review:

#### A. Task Registry Abstraction (MEDIUM RISK)

**File:** Create `src/scheduler_tasks.py`

```python
from dataclasses import dataclass
from typing import Callable, Optional
from datetime import datetime
from croniter import croniter

@dataclass
class ScheduledTask:
    name: str
    config_key: str
    default_cron: str
    job_func: Callable
    enabled: bool = True
    cron_expr: Optional[str] = None
    next_run: Optional[datetime] = None
    cron_obj: Optional[croniter] = None
    success_count: int = 0
    error_count: int = 0

class TaskRegistry:
    def __init__(self):
        self.tasks: dict[str, ScheduledTask] = {}
    
    def register(self, name: str, config_key: str, default_cron: str, job_func: Callable) -> None:
        self.tasks[name] = ScheduledTask(name, config_key, default_cron, job_func)
    
    def initialize_all(self, config, now_tz) -> None:
        for task in self.tasks.values():
            # Load from config, parse cron, set next_run
            ...
    
    def reload_from_config(self, config) -> list[str]:
        # Returns list of changed task names
        ...
    
    def execute_due_tasks(self, now_tz) -> dict[str, bool]:
        # Returns {task_name: success_bool}
        ...
```

**Migration:**
- Replace 8 `_<task>_cron_changed` flags with `registry.reload_from_config()`
- Replace 8 if-blocks with `registry.execute_due_tasks(now_tz)`
- Remove 8 `reschedule_<task>()` methods, use `registry.reschedule(name, cron)`

**Risk:** Large refactor, hard to A/B test, behavior changes if abstraction has bugs.

**Confidence:** 70% — needs thorough testing.

---

#### B. Config Reload Version Check (LOW RISK)

**Problem:** Reloads settings every 60s even if unchanged.

**Solution:**
1. Add `version` or `updated_at` timestamp to CosmosDB settings doc
2. In `_reload_config_from_cosmos()`, compare version before parsing
3. Skip reload if version unchanged

**Files:**
- `src/cosmos_db.py` — add version field to settings write
- `src/main.py` — add version check in reload method

**Risk:** Requires CosmosDB migration (add version field to existing settings).

**Confidence:** 90% — simple optimization, low risk if version field is optional.

---

#### C. Shared Error Aggregator (LOW RISK)

**Problem:** No centralized error metrics.

**Solution:**
- Add `task_stats` dict to scheduler: `{task_name: {"success": N, "errors": N, "last_run": ts}}`
- Update after each task execution
- Expose via web API endpoint `/api/scheduler/stats`

**Risk:** Minimal — purely additive.

**Confidence:** 95% — safe to apply.

---

#### D. Remove Hardcoded Symbol Lists from Config (COSMETIC)

**Problem:** DGI screener config has 500+ symbol tickers hardcoded (line 86).

**Solution:**
- Move to separate `dgi_symbols.txt` or fetch from external API
- Or use yfinance's S&P 500 component list dynamically

**Risk:** None — purely cosmetic cleanup.

**Confidence:** 100% — safe refactor.

---

## Summary of Changes

### Applied
1. ✅ **DPS Scheduler Integration** — critical bug fix, DPS now runs as configured

### Deferred (Documented for Review)
2. ⏸️ **Task Registry Abstraction** — reduces boilerplate, needs testing
3. ⏸️ **Config Reload Optimization** — minor perf improvement, requires schema change
4. ⏸️ **Error Aggregator** — helpful for observability, safe to apply
5. ⏸️ **Symbol List Cleanup** — cosmetic, low priority

---

## Key File Paths

- **Main Scheduler:** `src/main.py` (1153 lines)
- **Config Loader:** `src/config.py` (265 lines)
- **Orphaned DPS Cron:** `src/dps_cron.py` (173 lines)
- **Market Hours Probe:** `src/market_hours.py` (99 lines)
- **Entrypoint:** `run.py` (146 lines)
- **Config File:** `config.yaml` (107 lines)

---

## Architecture Patterns Observed

**Strengths:**
- Clean separation: config → setup → loop
- Per-task error isolation prevents cascading failures
- Cron-based scheduling (industry standard)
- Dynamic config reload without restart

**Weaknesses:**
- High boilerplate for new tasks (50+ lines per task)
- No task dependency management
- No concurrent task execution (sequential only)
- Orphaned code (DPS cron)

**Overall:** Solid foundation, needs DRY refactor and missing DPS fix.

---

**End of Analysis**
