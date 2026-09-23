# Rusty — History

## Project Context
- **Project:** options-agent
- **User:** dsanchor
- **Role:** frontend, BFF, and integration owner
- **Stack:** Python, FastAPI/BFF, React, App Router, TypeScript

## Core Context

- Owns routed UX, BFF proxy wiring, and frontend contract alignment with backend decisions.
- Prefers shared layouts, typed transport contracts, and query-parameter carry-over instead of duplicated page scaffolding.
- Normalize and guard at the boundary: nullable or sparse backend data should render safely without inventing values.
- Keep user-facing labels and navigation semantics consistent across symbols, portfolio, and economics surfaces.
- History before 2026-09 was condensed on 2026-09-09 into this core summary to keep the file under control.

## Recent Learnings

### 2026-09-23 — Global Monitoring Agent member gates
- Store per-member gates under `scheduler.agents`; only an explicit boolean `false` disables a member, so missing, legacy, or malformed values preserve enabled behavior.
- Enforce the gate at both orchestration boundaries: the scheduler/full-analysis loops skip disabled members, while direct dashboard per-agent triggers return an explicit `409 disabled` response.
- Settings saves update Cosmos, YAML compatibility state, and the live scheduler config so gates take effect without restart; dashboard status exposes the same effective gate map.
- The main dashboard carries the effective backend gate on each agent table; disabled sections and their rows are visibly dimmed and expose `Deactivated globally` plus ARIA disabled/described-by semantics without changing symbol enrollment.
- At scheduler startup, omit `scheduler.agents` from YAML defaults before merging Cosmos settings, then replacement-normalize from the effective persisted snapshot; this prevents stale local false values from being re-seeded when Cosmos omits or malforms the block.
- In web-only mode, dashboard payload and status polling must resolve gates through the same scheduler-or-Cosmos-or-YAML authority so persisted gate changes alter the AutoRefresh signature without frontend-local state.

### 2026-09-22 — Dashboard trigger startup is transactional
- Treat both `threading.Thread(...)` construction and `thread.start()` as fallible setup: a run is not truthfully `triggered` until `start()` returns successfully.
- On startup failure, mark the exact run failed, release only its run-ID-owned trigger slot, and return an explicit retryable non-success response; the worker retains sole cleanup ownership after a successful start.

### 2026-09-22 — Dashboard per-agent trigger observability
- Agents HQ per-agent triggers bypass `TaskRegistry` intentionally, so their completion must be tracked separately rather than mutating the Settings `monitor_agents` task's `last_run`.
- Background runner exceptions must escape the runner helper into the trigger wrapper; otherwise the API reports a trigger while failures are silently discarded and no status timestamp can advance.
- `/api/dashboard/status` now merges scheduler-task timestamps with per-agent dashboard execution state, setting `last_run` only on successful completion and exposing failures explicitly.

### 2026-09-20 — Daily Azure backup cron
- The Container Apps Job uses `15 23 * * *`: once daily at 23:15 UTC, which is 00:15 Europe/Madrid in standard time and 01:15 during daylight-saving time.
- The application's local-date, due-time, and idempotency checks remain retry/manual-run safety guards; they are not an infrastructure polling mechanism.

### 2026-09-20 — Secret exceptions must be structural and value-specific
- Production option-position provenance can contain an opaque-looking identifier at projected path `$.source.activity_id`; exempt that exact path only from opaque-token heuristics.
- Do not exempt `activity_id` or `source_activity_id` globally, and do not exempt JWTs, explicit credential patterns, adjacent source fields, or the same names in settings/ledger/arbitrary structures.
- Apply the same scoped scanner policy during projection and archive validation so valid exports remain readable without opening a validation bypass.

### 2026-09-20 — Automatic backup configuration authority
- The Azure Container Apps Job environment is the only production runtime source for automatic-backup enabled/timezone/local-time/schedule-name values; `AutomaticBackupConfig.from_environment()` is the reader.
- `configure-backup.sh` owns the Azure cron and Job environment. General application YAML, API/BFF routes, and frontend Settings must not expose a second automatic configuration/status authority; `.env.example` is local documentation only.

### 2026-09-19 — Transfer validation must distinguish CA legs
- `SHARE_CONSOLIDATION` intentionally maps `CONSOLIDATION_OUT`/`CONSOLIDATION_IN` to `TRANSFER_OUT`/`TRANSFER_IN` while using `ca_group_id`, not ordinary transfer-group metadata.
- Dependency validation must exclude identified corporate-action legs from ordinary transfer-pair rules while still enforcing `_CA_REQUIRED_LEGS` and `_CA_LEG_TXN_TYPE`.

### 2026-09-19 — Automatic backup Azure infrastructure
- Provision the automatic user-data backup as a scheduled Container Apps Job using the immutable backend image; GitHub Actions only aligns its image and is never the scheduler.
- Keep Blob authorization on a dedicated user-assigned identity with `Storage Blob Data Contributor` scoped to the private backup container; Cosmos temporarily remains an existing Container Apps secret reference.
- Lifecycle deletion for daily archives must require `retentionClass=daily`, allowing application-managed monthly anchors to promote referenced objects and avoid unsafe age-only deletion.

### 2026-09-19 — Assigned-option stock linkage
- Extended the linkable-position backend contract with stock `BUY`/`SELL` contexts while preserving all option contexts and response fields.
- Assignment candidate eligibility should consume `assignment_stock_by_position_id` from `build_option_position_linkage()` and check the expected direction, keeping picker behavior aligned with warning semantics for active, deleted, voided, and superseded movements.
- Confirmed the existing correction path persists `ASSIGNMENT_STOCK` metadata for assigned-put buys and assigned-call sells without a persistence redesign.

### 2026-09-09 — Economics frontend tabs, overview, and dividends detail
- Implemented the canonical routed Economics structure: `/economics`, `/economics/options`, and `/economics/dividends`.
- Used a shared `layout.tsx` tab strip and a second dividends fetch for all-history comparison sections so YoY and snowball views stay truthful while the main page still respects scoped filters.
- Updated BFF routes and `frontend/src/types/economics.ts` to carry the new dividends report fields.

### 2026-09-08 — Sparse manual movement defensive guards
- Hardened detail/table components and widened types so legacy/manual movements that omit financial blocks do not crash the UI.

### 2026-09-06 — Portfolio routed frontend conventions
- Reinforced the pattern of thin BFF proxies, shared stat-card presentation, and account-name-only informational labels across portfolio surfaces.
