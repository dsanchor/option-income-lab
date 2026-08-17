# Basher — History

## Project Context
- **Project:** options-agent
- **User:** dsanchor
- **Role:** Test, regression, and reviewer-gate owner
- **Stack:** Python, pytest, TypeScript/React, CosmosDB, Microsoft Agent Framework

## Core Context

- Built deployment and migration validation for CosmosDB, including idempotent
  provisioning, dry-run/backup/restore workflows, schema transformation checks,
  orphan handling, and progressive integrity validation.
- Established anti-403, scheduler, alert, activity-chat, DPS Insights, roll
  table, watchlist, and position-financial regression suites.
- Review standard: test production-shaped data, malformed and boundary inputs,
  persistence atomicity, frontend/backend contract parity, and current-state
  integration rather than stale concurrent snapshots.
- Option economics use the 100-share contract multiplier only for dollar
  values; ratios, per-share values, counts, filters, and ordering stay unscaled.
- Provider fetch tests no longer enforce retired DTE windows; expiration and
  roll-candidate limits are separate concerns.

## Recent Learnings

### 2026-08-17 — Buy Tracker Normalization Contract
- Added parameterized coverage for all score mappings, exceptional-gate inputs
  and boundaries, hard-WAIT overrides, raw-evidence precedence, malformed
  breakdown/evidence, canonical flags, coherent output, and non-mutation.
- Runner tests prove normalized WAIT is non-alert, BUY/STRONG_BUY are alerts,
  and one normalized object reaches enrichment, evaluation, persistence, and
  notification.
- Final provider-proxy contract approved. Buy Tracker validation reported 271
  focused tests passing.

### 2026-08-17 — Open Call Zero-Quote Safety
- Executable ask must be numeric, finite, and greater than zero; strings,
  booleans, zero, negatives, NaN, and infinity are invalid.
- Roll tables and snapshot P&L use executable ask, not midpoint. Missing or
  invalid buyback economics remain null and cannot pass profit-target rules.
- Production-shaped MSFT coverage verifies WAIT degradation, no profit-only
  Phase 2 or alerts, safe prose, repeated cycles, and valid positive-ask CLOSE.
- Final validation reported 297 focused, 76 integration, and 717 backend tests
  excluding unchanged provider tests; reviewer contract approved.

### 2026-08-08 — Watchlist and Position Financial Review
- Approved deterministic suitability categories: All, Ideal Puts, Ideal Calls,
  No Puts, and No Calls. Classification is based on normalized Entry + Momentum
  semantics, not tracking flags or option-chain delta filters.
- Verified symbol creation, shares editing, forecast backfill isolation, and
  strict financial input validation with persistence/status preservation.
- Frontend validation used focused ESLint, TypeScript, and a runtime
  classification matrix because no dedicated frontend test runner exists.

## Durable Testing Patterns
- Use hermetic mocks for Cosmos and provider boundaries.
- Assert invalid inputs cause no writes.
- Preserve exact upstream HTTP status codes through BFF/backend layers.
- Include repeated-cycle tests for scheduler and alert state.
- Treat existing unrelated provider failures as baseline, not regressions.
