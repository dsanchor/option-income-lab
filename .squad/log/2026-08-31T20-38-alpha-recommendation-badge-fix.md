# Alpha Recommendation Badge Fix — Session Log
**Date:** 2026-08-31  
**Agent:** Linus  
**Task:** Fix frontend rendering of Alpha SELL + ALPHA badges  

---

## Problem
Dashboard incorrectly displayed primary WAIT activity when Alpha provided SELL recommendation, suppressing ALPHA badge visibility.

## Solution
Modified `RecentCell` in `DashboardAgentTables.tsx` to check `recommendationSource === "alpha"` and render hardcoded SELL + ALPHA badges instead of primary activity list.

## Changes
- **File:** `frontend/src/components/DashboardAgentTables.tsx`
- **Component:** `RecentCell`
- **Type Checking:** ✅ TypeScript noEmit passed
- **Lint:** ✅ ESLint targeted checks passed

## Impact
Users now see correct Alpha recommendations in dashboard with proper badge display.

## Related
- Backend: `backend/web/app.py` → `_build_dashboard_tables` (already sets `recommendation_source`)
- Tests: `backend/tests/test_dashboard_alpha_fallback.py` → All 19 tests passed
