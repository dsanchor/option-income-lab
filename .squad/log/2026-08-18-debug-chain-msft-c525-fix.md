# Session Log: MSFT 2026-09-04 C525 Debug-Chain Fix

**Date**: 2026-08-18  
**Agent Lead**: Linus (Quant Dev)  
**Reviewer**: Basher (Tester/Reviewer)  
**Task**: Restore buyback-cost visibility for MSFT $525 call (17 DTE, 2026-09-04) in Debug > Agent Chain Pipeline View.

## Problem
Simulating a current MSFT call position (strike 525, expiration 2026-09-04) in Debug > Agent Chain Pipeline View reported:
- "Buyback cost unavailable because current contract is not in chain data"
- "No valid ROLL_OUT candidates"

The contract existed in merged chain (yfinance + TradingView) but was invisible to the debug endpoint.

## Root Cause
The delta filter (`filter_options_chain_by_delta`) correctly drops contracts with delta outside the standard band (calls: 0.15–0.90). The MSFT $525 call had a legitimate $3.20 ask but degenerate near-zero IV (yfinance quirk when bid/ask both zero, markets closed), resulting in ~0.0 delta. The debug endpoint derived current contract from the already-filtered chain, losing it before display.

**Pattern**: Same bug class fixed 2026-07-09 in production pipeline—never propagated to debug surface or formatter helper.

## Solution
1. **`format_roll_candidates_table()`**: Added optional `current_contract` parameter; when supplied, uses it for CURRENT POSITION row independently of candidate filtering. Backward compatible.
2. **Debug endpoint** (`web/app.py`): Captures current contract from raw chain (before filtering) using `get_contract()`, passes to formatter.
3. **Production call site** (`agent_runner.py`): Now passes pre-filter `current_contract` reference, ensuring CURRENT POSITION row populates.

## Verification
- **Unit tests** (27 new): `test_format_roll_candidates_table.py`, `test_debug_agent_chain_pipeline.py`, `test_options_chain_position_and_direction_filters.py`
- **Regression run**: 213 tests passed (118 pre-existing + 95 new/modified)
- **Pre-existing baseline**: 1 known yfinance mock-drift failure, unrelated

## Key Insight
Any pipeline consumer (dashboards, debug tools, agents) needing current-contract reference must capture it before delta/direction filtering, not after. Do not rely on in-chain lookup post-filtering—direction filters by design exclude the held strike+expiration.

## Files Touched
- `backend/src/options_chain_filters.py` — formatter parameter
- `backend/web/app.py` — debug endpoint
- `backend/src/agent_runner.py` — production call site
- `backend/tests/` — new test modules for integration and coverage

## Status
✅ Complete. Ready for production merge.
