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

### 2026-09-24 — Exact terminal-intent recovery
- A terminal recovery seal must bind the complete canonical journal application document, not merely a payload subset. Recovery compares that document recursively and permits differences only for explicitly enumerated top-level Cosmos repository fields (`_etag`, `_rid`, `_self`, `_attachments`, `_ts`).
- Build the terminal document once before sealing and persist that same sealed document on initial write, owner retry, or crash recovery. This preserves timestamps/history exactly and makes retries idempotent.
- Added, removed, or changed application fields—including nested fields—must reject recovery without clearing the seal; seal clearing follows only an exact post-persistence match.

### 2026-09-24 — Controlled historical-rights migration CLI
- Historical rights resolution is a one-case, operator-driven workflow: discovery is advisory, preview is zero-write, and apply/rollback require literal case/outcome/hash/actor/rationale confirmation.
- Keep discovery and financial preview construction pure and separate from the transactional service. The CLI now emits Livingston's canonical `RightsMigrationPreview`, while the service owns journaling, ETag/CAS, compensation, resume, and rollback.
- Candidate recommendations require the same account and canonical security plus structured evidence; date proximity, small quantities, and text matches remain advisory and ambiguous alternatives stay visible.
- A/B/C economics are explicit: A creates cash-dividend income plus a linked rights sale and no shares; B creates shares and no sale; C creates shares plus a leftover rights sale. Share FMV plus subscription top-up enters FIFO basis, and missing reliable FMV requires an explicit ZERO_COST affirmation.
- Production-capable commands require explicit environment/database/container/factory inputs, reject piped confirmation, and add a separate production acknowledgement. The factory has no implicit target or connection fallback.

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

### 2026-09-24 — Dashboard banner refresh and last-run continuity
- The Configuration `Dashboard Banner Agent` card was the only current UI combining the literal `Last Run` / `Never` fallback with the banner generator.
- Manual banner execution must enter through `TaskRegistry.trigger_task_now`; bypassing the registry leaves runtime `last_run` unset even when the banner document is persisted.
- Banner generation is persisted on `dashboard_banner.generated_at`. Settings falls back to that value after restart and updates from the single completed Run Now response; no global polling loop is needed.
- Dashboard auto-refresh signatures must include the persisted banner generation timestamp; activity and monitoring-agent timestamps do not change when only banner content changes.

### 2026-09-24 — Legacy dashboard monitor identity must be monotonic
- A legacy monitor record without `position_id` is matched by intersecting every explicit identity field it carries with active positions; a failed strike, expiration, account, option type, paper lane, contract ID, or instrument ID may never be discarded in favor of symbol-only matching.
- Legacy fallback is valid only when that complete predicate resolves exactly one active position. Zero or multiple matches remain unassigned while the record stays visible in the global Activities feed.
- An explicit `position_id` remains authoritative, including stale rolled/closed IDs: failed ID lookup never falls back to contract or symbol identity.

### 2026-09-25 — Manual monitor trigger identity presence is explicit
- Trigger payload identity uses key presence, not truthiness: an absent field enables the intended legacy path, while a present null, blank, whitespace-only, or malformed field fails with HTTP 400.
- Position constraints require an explicit valid `position_id`; endpoint route type remains an independent call/put constraint, and a supplied `option_type` must normalize and match rather than being replaced by the route default.
- Exact-ID selection validates every supplied constraint before launch. Mismatches remain conflicts, and ambiguous symbol-only legacy requests remain HTTP 409.
- Position-specific locking continues to permit different position IDs concurrently while rejecting a duplicate run for the same position.
- Monitor wrapper annotations now use explicit `str | None` and `dict[str, Any] | None`, resolving the four changed-code RUF013 findings without ignores.

### 2026-09-25 — Dashboard banner content freshness
- The banner fed yfinance's historical `exDividendDate` (`ex_dividend_date_recent`) and any past earnings/position dates directly to an LLM explicitly told to mention proximity. Regenerating the document advanced `generated_at` while allowing the same 20-day-old facts to be presented as current.
- Banner activities now consume all Cosmos continuation pages and are parsed, filtered to 24 hours, and sorted as UTC datetimes rather than relying on lexicographic timestamp order. Market reads bypass the shared process cache for each banner generation.
- Past earnings/ex-dividend dates and expired-but-active positions are excluded from current-event context. When neither a fresh market snapshot nor recent activity is available, the persisted banner explicitly says no recent eligible data.
- Banner documents now persist `source_as_of`, per-source watermarks/counts, a content hash, and a unique generation ID. A run fails unless Cosmos returns the exact generation ID/content it was asked to persist.
- `/api/dashboard` exposes the watermark and Agents HQ renders `Sources as of`; AutoRefresh continues to key on the unique microsecond `generated_at`.

### 2026-09-25 — Dashboard Banner Agent removed
- Removed the Dashboard Banner Agent implementation, instructions, scheduler registration, manual endpoint, settings/config/provider catalog entries, Cosmos read/write helpers, dashboard payload fields, UI component, refresh signature, and feature-specific tests.
- Restored `TaskRegistry` to its generic fire-and-forget contract by removing banner-only retained completion results and attempt/success/error metadata.
- Legacy persisted `banner_agent` settings remain harmless unknown data: startup and settings loading ignore them, while the application no longer reads, updates, exports, or presents them.
- Existing `dashboard_banner` Cosmos documents were not accessed or deleted; they are inert orphaned production data.
- Preserved the bounded generic AutoRefresh poller and all non-banner dashboard, monitoring, reporting, plan, pricing, and portfolio behavior.
- Removed the final active documentation reference from the architecture tree and retired banner-specific wording from Saul's active charter; remaining banner mentions are append-only historical `.squad` records, the superseding removal decision, and compatibility/removal regression fixtures.

### 2026-09-26 — Rights movements removed from frontend
- Removed rights sale selection, rights-issue corporate actions, correction controls, badges, details, warnings, and Economics/Dividends columns and copy.
- Added a shared fail-closed compatibility boundary that excludes legacy rights sales, rights corporate-action legs/events, rights-bearing dividends, and rights warning rows while preserving ordinary BUY/SELL and `Dividend · Buy`.
- Dividend and Economics totals are normalized to cash-only values; detailed dividend aggregates are rebuilt after excluding legacy rights-bearing positions.
- Import previews block commit when an older backend returns unsupported rows instead of silently committing hidden data.

### 2026-09-26 — Total Dividends card simplified
- Economics/Dividends now presents `Total Dividends` as one localized net-dividends-received value with a matching accessible label.
- Removed the nested breakdown structure; focused source-contract coverage prevents redundant subcards from returning.

### 2026-09-26 — Total Dividends card grouped metrics
- The Economics/Dividends summary keeps `total_net_eur` as the primary Total Dividends headline and groups canonical gross, combined withholding, and effective withholding beneath it in a semantic definition list.
- Dividend Count, Avg Monthly Net (last 12mo), and Portfolio Yield on Cost remain the only three sibling `StatCard` metrics, in their prior relative order.
- The primary and sibling regions stack on mobile/tablet and align as equal-height desktop columns without nested card components; missing/nonfinite grouped values render as unavailable.
