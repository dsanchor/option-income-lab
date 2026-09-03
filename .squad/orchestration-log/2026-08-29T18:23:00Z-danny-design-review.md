# Orchestration — Danny Design Review: Best Options Scheduled Precompute (2026-08-29T18:23:00Z)

**Agent:** Danny (Lead, Design Review)  
**Task:** BEFORE Design Review — Best Options precompute/shared cache architecture  
**Review Type:** Revalidation ceremony against HEAD before implementation proceeds  
**Status:** ✅ Design CONFIRMED — no conflicts found, implementation may proceed  

## Summary

Conducted formal design review of Danny's "Best Options scheduled precompute + shared in-memory result cache" architecture. Revalidated all design assumptions (scheduler registry, reschedule pattern, options_screener.py structure, frontend contract, new files) against committed HEAD (e3a20a2). No conflicts detected. Implementation authorized to proceed in parallel across four ownership slices (Linus, Livingston, Rusty, Basher).

## Revalidation Findings

### Scheduler Foundation (CONFIRMED)

- 10 tasks registered via `registry.register()` in `main.py:680–752` ✅
- Single `_worker_loop` with `queue.Queue` (scheduler_registry.py:53,203) ✅
- `_MAX_TASK_DURATION_SECONDS = 1800` (scheduler_registry.py:18) — sufficient for 5-min soft deadline ✅
- All existing cron comments follow container-TZ semantics (e.g., "8 AM daily (UTC)") ✅

### Scheduler Usage Pattern (CONFIRMED)

- `price_forecast` reschedule pattern established (app.py:4595): `scheduler.registry.reschedule("price_forecast", pf_cron, scheduler.config)` ✅
- Design correctly identifies this as the pattern to follow (not legacy `reschedule_*` wrappers) ✅
- No per-task timezone capability exists (design correctly rules this out) ✅

### Options Screener Seam (CONFIRMED)

- `evaluate_best_options` imported at line 105 ✅
- Called at line 265 in main evaluation path ✅
- `_normalize_symbol` helper at line 133 ✅
- `_evaluate_symbol` at line 247 ✅
- **No existing `precomputed` parameter** — surgical change is clean ✅
- Response shape well-defined, no hidden side effects ✅

### Frontend Contract (CONFIRMED)

- `BestOptionsView.tsx:248` requests `?side=both` only ✅
- `OptionsScreenerView.tsx:267-273` uses `partialStatus`/`warming`/`cold` (to be retired per design §11b) ✅
- No frontend assumptions about cache lifecycle (ready to consume new API contract) ✅

### File Creation (CONFIRMED CLEAN)

- `best_options_cache.py` does NOT exist yet ✅
- `best_options_precompute.py` does NOT exist yet ✅
- No conflicting branch work in these files ✅

### Existing Tests (IDENTIFIED)

Tests that will be affected by design changes (design §12 correctly identified these):
- `test_best_options_endpoint.py` — canonical path detection, cache miss behavior
- `test_best_options_frontend_contract.py` — response shape with cache metadata
- `test_options_screener_cache_concurrency.py` — precomputed dict passing

All pre-existing tests confirmed runnable with existing code; design changes are backwardly compatible or explicitly manage breaking changes.

### No Conflicting Changes

Only dirty file in repo: `.squad/agents/danny/history.md` (unrelated to this design)  
No in-flight branches touching scheduler, cache, or screener paths  

## Design Certification

✅ **Scheduler timezone semantics:** Inherited container-TZ correctly; no per-task override introduced  
✅ **Cron expression verified:** `5 10-23 * * 1-5` produces 14 fires per weekday at 10:05–23:05  
✅ **Canonical envelope design:** One `side="both"` result per symbol, byte-for-byte identical for both consumers  
✅ **Operational semantics:** Carry-forward, soft deadline, readiness counts all sound  
✅ **Cache immutability:** Discipline-enforced (no runtime guards), but sufficient given module scope  
✅ **Concurrency model:** Per-symbol OS locks correctly decouple scheduler thread from event loop  
✅ **Backward compatibility:** All changes are additive or clearly flagged as breaking  

## Ownership Slices Approved

| Slice | Agent | Files | Scope |
|-------|-------|-------|-------|
| 1 | Linus | `best_options_cache.py`, `options_screener.py` updates, tests | Pure cache + screener surgical update |
| 2 | Livingston | `best_options_precompute.py`, `app.py` endpoints, Settings, tests | Cycle/API/refresh + validation integration |
| 3 | Rusty | `main.py` bridge, `config.yaml`, frontend components, types, Settings | Scheduler registration + UI |
| 4 | Basher | (All implementations) | Independent adversarial review |

## Binding User Directives

| ID | Directive | Status |
|----|-----------|--------|
| D1 | Precompute Best Options per symbol on scheduler; Symbol Detail + Screener consume same cached result; Settings config; default `5 10-23 * * 1-5` | ✅ Incorporated |
| D2 | Manual Refresh button on Symbol Detail only; recalculates that symbol's shared entry; **no** refresh on Screener | ✅ Incorporated |
| D3 | Screener never computes missing entries; shows `N of X loaded`; warning to wait for next cycle | ✅ Incorporated |

## Critical Design Decisions Documented

1. **Canonical envelope is strictly `side="both"`** — both Symbol Detail and Screener consume identical bytes; verified by explicit test (§12)
2. **Per-symbol OS locks** — scheduler thread and event loop separately hold per-symbol threading.Lock; async calls offload to executor; prevents deadlock  
3. **Soft deadline with carry-forward** — cycle times out after 5 min, preserves stale entries, completes with partial results
4. **Screener never computes on request** — missing entry downgrades to error, no fallback to live evaluation  
5. **Readiness explicit** — frontend polls `/api/best-options/health` for `N of X loaded` display, not implicit cache state

## Next Steps

- Linus proceeds with `best_options_cache.py` + screener update (no blocking dependencies)  
- Livingston begins `best_options_precompute.py` after Linus, pulls cache module  
- Rusty runs parallel to Livingston, pulls Livingston's API contracts when finalized  
- Basher reviews all implementations independently after each batch completes  

**Status:** ✅ All checks passed. Design approved for immediate implementation.
