# Rusty — History

## Project Context
- **Project:** options-agent
- **User:** dsanchor
- **Role:** Backend, runner, scheduler, persistence, and frontend integration owner
- **Stack:** Python, Microsoft Agent Framework, CosmosDB, yfinance/TradingView,
  FastAPI/BFF, React

## Core Context

- Built the CosmosDB service layer, scheduler/task registry, dashboard APIs,
  symbol/position workflows, settings persistence, chat endpoints, and agent
  runner integration.
- Data-provider architecture prefetches and normalizes overview, technical,
  forecast, dividend, and options-chain data before agent execution.
- Scheduler work uses non-blocking queued jobs, overlap guards, per-symbol and
  worker timeouts, dynamic configuration, and persisted task state.
- Unified activity/alert records use `is_alert`; all downstream consumers should
  share one normalized activity object.
- Settings use CosmosDB as authoritative when configured, with ETag
  read-merge-replace, conflict retry, read-back verification, and scheduler
  reload only after durable success. YAML is authoritative only without Cosmos.
- UI/backend work includes symbol watchlists, pause-until-earnings, financial
  editing, roll tables, options-chain caching, provider/model settings, and
  portfolio chat context.

## Durable Implementation Patterns

- Normalize at read/input boundaries; malformed, non-finite, or unverified data
  remains unavailable.
- Reassert protected fields after dict spreading to avoid caller overwrite.
- Use lazy initialization/imports for expensive or provider-specific resources.
- Keep position monitors active when following-agent watchlists are paused.
- Preserve source intent: automated from-activity values and manual values have
  distinct contracts.
- Long-running scheduler jobs must not block heartbeat or next-run advancement.
- When Cosmos is configured, persistence failure is an error, never silent YAML
  fallback.

## Recent Learnings

### 2026-08-17 — Buy Tracker Canonical Normalization
- Adapt raw provider output into a fixed ephemeral evidence object. Only the
  five binary score dimensions are accepted; score is always recomputed.
- Apply exceptional promotion and hard-WAIT predicates before alerting,
  evaluation, persistence, summaries, tracing, and notification.
- Exact canonical risk flags are conservative fallbacks only when raw evidence
  is unavailable. Raw safe evidence overrides stale flags; prose cannot create
  positive evidence.
- Provider prompt examples and evidence paths are shared and production-shaped.

### 2026-08-17 — OpenCallMonitor Zero-Quote Safety
- Added a shared positive-finite executable-ask contract for short-call P&L,
  buyback, roll tables, candidate tables, and DPS economics.
- Invalid asks yield null economics, skip profit-only Phase 2, and persist
  deterministic non-alert WAIT without prolonged-WAIT notifications.
- Independent risk rationale remains enforceable; valid positive asks preserve
  CLOSE and ROLL behavior.

### 2026-08-10 — AI Provider Cosmos Persistence
- Settings mutations read the authoritative document, merge only intended
  fields, conditionally replace by ETag, retry conflicts, and verify read-back.
- Configured Cosmos unavailability returns failure; unrelated document fields
  are preserved and live scheduler state updates only from verified data.

### 2026-08-08 — Watchlist and Position Financial Integration
- Symbol creation and inline shares editing validate normalized inputs and keep
  forecast backfill failure isolated from durable creation.
- Position premium and buyback updates use distinct routes and strict numeric
  validation.
- Suitability categories are owned by deterministic Entry + Momentum semantics,
  independent of watchlist flags and option-chain filters.

## Validation Practice
- Run targeted pytest suites, Python compilation, focused frontend lint/type
  checks, and scoped diffs.
- Preserve unrelated baseline provider failures in reports.
- Verify runner ordering and object identity at downstream boundaries.
