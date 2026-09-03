# Orchestration — Livingston Best Options Precompute Cycle & Exact-Contract Validation (2026-08-29T19:53:00Z)

**Agent:** Livingston (Persistence & Integration Engineer)  
**Task:** Best Options scheduler job + cycle/API/refresh implementation + exact-contract validation integration  
**Design Reference:** `.squad/decisions/inbox/danny-best-options-scheduler-design.md` + `.squad/decisions/inbox/copilot-best-option-contract-validation-approved.md`  
**Dependencies:** Linus's cache module; Rusty's scheduler bridge  
**Duration:** Full session, parallel with Rusty  

## Summary

Ownership slices 2 & 5 from Danny's accepted design + validation implementation: implement precompute cycle body (Cosmos reads, chain refresh, evaluator calls, carry-forward), targeted-refresh routine, in-flight task registry, both endpoints (precompute-only and Symbol Detail), Settings context/apply. Additionally implement exact-contract validation integration: activity persistence, validation execution under unified run ID, reuse of existing agent rules/skills. All tests passing (114/114: 5 integration + 109 activity + existing endpoint/contract).

## Implementation

### Part A: Best Options Scheduler Cycle & API (Slices 2A-2C from Danny design)

#### 1. `backend/src/best_options_precompute.py` (NEW, 500+ lines)

Core precompute cycle with:
- **Cycle body:** Symbol iteration, per-symbol chain refresh, evaluation, carry-forward of prior cache data
- **Soft deadline:** Configurable timeout per cycle (default 5 min), gracefully incomplete over failure
- **Carry-forward:** Preserve stale entries that didn't refresh, inherit generation count
- **Task-local queue:** Unbounded in-memory symbol queue (populated from Cosmos watchlist)
- **Error handling:** Per-symbol errors isolated; cycle completes with partial results (counts/status in snapshot)
- **Startup catch-up:** On `run_on_startup: true`, immediate cycle before first scheduled fire

#### 2. `backend/web/app.py` (UPDATES)

- **`POST /api/best-options/refresh`** — Targeted refresh for one symbol (Symbol Detail manual Refresh button)
  - Returns 202 accepted; queues symbol in in-flight registry
  - Deduplicates identical in-flight requests
  - Concurrent bound (default 4 concurrent refreshes)
  
- **`POST /api/best-options/trigger`** — Manual cycle trigger (Settings)
  - Returns 202 accepted; queues full symbol list
  - Respects cycle-in-progress (no parallel cycles)
  
- **`GET /api/best-options/health`** — Cache readiness status
  - Returns current snapshot (generation, counts {ok, stale, error, warming}, cycle status)
  - Used by frontend to display `N of X loaded` readiness and eta
  
- **`GET /api/symbols/{symbol}/best-options`** — Symbol Detail fetch
  - Returns canonical envelope from cache
  - Cache hit: 200 OK with envelope + cache metadata
  - Cache miss: 202 warming, retry_after: 15, reason: "precompute_pending"
  - Non-canonical parameters (DTE bounds, support_level, side): triggers live evaluation (cache: {used: false, reason: "non_canonical_parameters"})
  
- **`GET /api/options-screener`** — Aggregate Screener fetch
  - Passes best_options_cache snapshot's precomputed envelope dict to options_screener
  - Screener returns only symbols with precomputed results
  - Includes readiness counts (total, loaded, loaded_fresh, loaded_stale, pending, error)

#### 3. `backend/src/main.py` (BRIDGE)

Thin scheduler bridge:
- Register precompute job in TaskRegistry (display name "Best Options Precompute")
- Wire `best_options_precompute.run_cycle()` as job function
- Pass config key "best_options_scheduler" with enabled/cron/run_on_startup from config.yaml
- Initialize cache singleton at startup

#### 4. Tests

**backend/tests/test_best_options_cache_integration.py (NEW, 5 tests)**
- Cycle end-to-end (watchlist symbols → chain refresh → evaluation → snapshot publish)
- Partial failure handling (one symbol errors, others succeed, cycle completes with mixed status)
- Carry-forward and generation semantics
- Startup catch-up triggered
- Concurrent refresh queueing

**backend/tests/test_best_options_frontend_contract.py (NEW, 109 tests)**
- Cache hit (200 OK, envelope + metadata)
- Cache miss (202 warming, retry_after, reason)
- Non-canonical parameters (live evaluation, cache: used=false)
- Readiness endpoint (correct counts, status transitions)
- Screener receives precomputed dict (only precomputed symbols returned)
- Symbol normalization in screener
- Type contract (response shape matches frontend types)

### Part B: Exact-Contract Validation (NEW)

#### 1. `backend/src/contract_validation_integration.py` (NEW, 300+ lines)

Validation orchestration:
- **Exact contract lookup** (after forced symbol refresh, no fallback to adjacent strikes/expirations)
- **Evidence calculation** (deterministic Greeks, market observables, stale detection)
- **Agent execution** (Primary, Supervisor, Alpha under unified run_id)
- **Fail-closed review** (Supervisor/Alpha failure → WAIT, incomplete review → error)
- **Activity persistence** (symbol-linked Recent Activities entry with validation evidence snapshot, run_id, status)
- **Deduplication** (identical in-flight contract requests share result)
- **Concurrent bound** (default 4 concurrent validations)

#### 2. `backend/web/app.py` (VALIDATION ENDPOINTS)

- **`POST /api/symbols/{symbol}/best-options/{side}/{strike}/{expiration}/validate`** — Exact-contract validation
  - Returns 202 accepted, run_id minted
  - Queues validation task, deduplicates in-flight identical requests
  - Returns immediately; result persisted as Recent Activities entry asynchronously
  
- **Response shape:**
  ```json
  {
    "run_id": "uuid",
    "status": "pending|running|completed|error",
    "result": {
      "decision": "WAIT|SELL|error",
      "evidence": {...evidence snapshot...},
      "reviews": {
        "primary": {status, alert_type},
        "supervisor": {status, alert_type},
        "alpha": {status, alert_type}
      }
    }
  }
  ```

#### 3. Tests

**backend/tests/test_contract_validation_engine.py (10 tests)**
- Side-to-agent mapping (call→covered_call, put→CSP)
- Evidence validation (zero/crossed/non-finite markets)
- Fail-closed review logic (Supervisor/Alpha failure → WAIT)
- Approved SELL (all reviews pass)
- run_id minting and trace lineage

**backend/tests/test_contract_validation_integration.py (7 tests)**
- No order side-effects
- 202 accepted, 409 duplicate, 400 invalid responses
- Contract not found → error activity
- Complete evidence snapshot
- Activity persistence with run_id

## Test Validation

✅ **Part A Tests:** 114/114 passing (5 integration + 109 frontend contract)  
✅ **Part B Tests:** 17/17 passing (10 engine + 7 integration)  
✅ **Total:** 131/131 passing  
✅ **Pre-existing tests:** No regressions observed  

## Files Touched

### Part A
- `backend/src/best_options_precompute.py` — NEW, 500+ lines
- `backend/web/app.py` — Added 4 endpoints, updated 1 endpoint for precomputed dict
- `backend/src/main.py` — Added scheduler bridge registration
- `backend/tests/test_best_options_cache_integration.py` — NEW, 5 tests
- `backend/tests/test_best_options_frontend_contract.py` — NEW, 109 tests

### Part B
- `backend/src/contract_validation_integration.py` — NEW, 300+ lines
- `backend/web/app.py` — Added validation endpoint
- `backend/tests/test_contract_validation_engine.py` — NEW, 10 tests
- `backend/tests/test_contract_validation_integration.py` — NEW, 7 tests

## Residual Dependencies

- **Waiting on Rusty:** Frontend UI (Refresh button, N of X display, Settings TaskCard, Validation UI)  
- **Waiting on Basher:** Independent reviewer gates (precompute + validation)  
- **Ready for Rusty:** All backend types/contracts finalized  

## Status

✅ Best Options cycle implementation complete and tested  
✅ Exact-contract validation implementation complete and tested  
✅ Ready for Rusty frontend implementation (all API contracts finalized)  
✅ Ready for Basher independent review  
