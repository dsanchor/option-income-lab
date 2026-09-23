# Livingston — Project History

## Core Context

- **Project:** options-agent
- **User:** dsanchor
- **Role:** backend persistence, aggregation, and integration owner
- **Focus areas:** portfolio ledger contracts, economics/report endpoints, FIFO/CMP correctness, and service seams that must stay pure-function testable
- **Durable pattern:** keep heavy business logic in pure Python modules first, then wire thin FastAPI/Cosmos adapters on top
- **Durable pattern:** ledger readers and writers must preserve complete financial field shapes (`gross`, `fees`, `net`, `withholding`) across manual, imported, and corporate-action flows
- **Durable pattern:** portfolio/economics APIs should avoid misleading blended totals when currencies or upstream contracts are not authoritative enough
- **History note:** pre-2026-09 detail was condensed on 2026-09-09 into this core summary to keep the file maintainable

## Recent Learnings

### 2026-09-09 — Dividends economics backend endpoints
- Added `backend/src/dividends_economics.py` as a pure aggregation module so dividend economics behavior can be unit-tested without Cosmos fakes.
- Added `GET /api/economics/dividends` and `GET /api/economics/overview` in `backend/web/app.py`.
- `yearly` and `cumulative` intentionally ignore `year` / `month` while still honoring `symbol` and `account_id`; the response declares that scope explicitly.

### 2026-09-08 — Manual ledger field normalization
- Normalized manual movement, transfer, correction, and corporate-action write paths so detail views always receive `gross`, `fees`, `net`, and `withholding` blocks.
- Reinforced the rule that round-trip-safe document shape matters as much as arithmetic correctness.

### 2026-09-08 — FIFO, cost-basis, and corporate-action semantics
- Confirmed/implemented the current contract that BUY cost uses net economics, zero-cost scrip shares enter holdings at zero cost, and grouped corporate actions must preserve event-level meaning.

## Learnings

### 2026-09-23 — Cross-account FIFO aggregation
- Consolidated symbol holdings must preserve FIFO independently per account; a sale or transfer-out can consume only that account's lots.
- Aggregate remaining shares and FIFO residual cost basis are sums of account residuals, and aggregate average cost is residual basis divided by aggregate remaining shares; never run FIFO over a pooled cross-account lot queue.
- Zero-cost scrip remains in the aggregate share denominator, while incomplete-cost holdings retain their explicit incomplete status.

### 2026-09-22 — Scrip FMV authority boundary
- Corporate-action create and group correction must share one fail-closed normalizer for `SHARE_ACQUISITION` FMV; client-provided `cost_basis_status` is advisory and must be replaced by the server-derived state.
- Blank authoritative EUR FMV is `INCOMPLETE`, explicit zero is `ZERO_COST`, and a positive finite value is `COMPLETE`; negative, non-finite, and malformed supplied amounts are validation errors rather than zero.
- For non-EUR share FMV, `gross.eur_amount` is the authority. A valid native amount without EUR conversion remains preserved but incomplete.

### 2026-09-20 — Legacy `_unassigned` backup round-trip (2026-09-20T16:32:21Z implementation)
- Backup dependency closure must treat `_unassigned` as a virtual account partition, not as a missing account document: exports must omit synthetic account creation and imports must accept the reference.
- The exemption belongs in the shared dependency validator used by validate, dry-run, apply preflight/recheck, and postflight; every other account ID remains subject to strict existence checks.
- **Implementation:** Added `LEGACY_ACCOUNT_SENTINEL` constant and `_is_supported_account_reference()` helper to `backend/src/backup/dependency_closure.py`; updated `close_dependencies()` and `validate_dependency_closure()` to accept `_unassigned` without requiring account document existence.
- **Validation:** 47 focused dependency closure tests passed; 64 broader backup/infrastructure tests passed; 3 unrelated deprecation warnings; diff hygiene clean.

### 2026-09-20 — Production Blob tag authorization incident
- `Storage Blob Data Contributor` permits Blob read/write/delete/lease operations but does not include the distinct Blob index-tag `tags/write` data action.
- Azure SDK `upload_blob(..., tags=...)` sends tags with the immutable `PutBlob`; production therefore returned `403 AuthorizationPermissionMismatch` even though lock creation, lease, reads, listing, and untagged writes succeeded.
- Preserve tagged retention without broad roles by assigning a custom tag-write-only data role at the exact container scope alongside Blob Data Contributor.
- Sanitized Blob failures should identify the storage operation, path category, HTTP status, Azure error code, and request ID while omitting payloads and full object names.

### 2026-09-20 — Production backup schema alignment
- The first production backup image failed because strict backup projections lagged known persisted shapes: security migration provenance, imported ledger `company_name`/`warnings`, and the runtime `pricing_cache` field.
- Option-position provenance `source.activity_id` was also an unsafe opaque-token false positive; identifier exemptions must be narrow and key-specific rather than weakening recursive secret scanning.
- Production validation should reuse Container Apps Job secret references through a read-only execution override and report only section, hashed logical identity, issue category, field names, counts, and archive size.

### 2026-09-20 — Symbol Details holding unrealized P&L
- Added nullable `current_value_eur`, `unrealized_pnl_eur`, and `unrealized_pnl_pct` to the Symbol Details `portfolio` response.
- Symbol Details and Symbols Overview now share one valuation helper backed by cached `pricing_cache.price_eur`; no quote or FX provider calls were added.
- Unrealized P&L uses the FIFO residual `remaining_cost_basis_eur`; zero-cost holdings retain numeric absolute P&L while percentage remains null, and closed/unpriced holdings remain null-valued.

### 2026-09-20 — Automatic-backup single-source cleanup
- A removed public status surface must also be removed from internal services and tests; leaving an unreachable presenter creates a misleading second authority even without production callers.
- Scheduler tests should assert durable Blob health/run/latest records and actual scheduled/manual outcomes directly, rather than reconstructing those records through a presentation method.

### 2026-09-22 — Dashboard run-state concurrency
- Dashboard execution state must be keyed by a server run UUID, not only by agent: same-agent runs for different symbols can finish out of order without cross-contaminating symbol/error state.
- All run creation, completion, snapshotting, and retention happen under one `threading.Lock`; completion is guarded by run ID and running state.
- Per-agent status deterministically reflects the newest-started retained attempt, while `last_run` is separate sticky successful-completion state and survives later running/failed attempts.
- Retain a bounded newest set of completed process-local runs while exempting active runs; clients poll their returned run ID and never equate accepted enqueue with success.

### 2026-09-19 — Frontend automatic-backup status contract revision
- `last_scheduled_success` is a run-record object and `latest_changed_archive` is an archive-pointer object; frontend status types must preserve those shapes rather than coercing them to strings.
- Backup status UI now formats selected scalar fields defensively, exposes failed last-attempt detail, and uses explicit placeholders for absent or partial state without ever rendering raw objects.
- Response-shaped tests should execute the production formatters against populated, empty, partial, and failure payloads, not merely search component source for field names.

### 2026-09-19 — Implementación de backup lógico y restore create-only
- El ZIP v1 usa un conjunto fijo de nueve entradas, JSON canónico, checksums ordenados y validación previa de traversal, duplicados, colisiones de mayúsculas, symlinks, profundidad, expansión, tamaños y secretos recursivos.
- Las posiciones de opciones se exportan como autoridad separada, pero durante restore se materializan dentro del `symbol_config` creado; añadir posiciones a un config ya existente queda bloqueado porque create-only no permite `replace`.
- El import persiste el journal antes del primer write de usuario, usa `create_item`, verifica el estado final y compensa en orden inverso; cualquier compensación incompleta termina en `PARTIAL_REQUIRES_ATTENTION`.
- La publicación Blob usa `DefaultAzureCredential` mediante import lazy, lease renovable, objeto inmutable, descarga/verificación antes de `latest`, CAS y gate por fecha local/DST.
- No existe guard operator/admin en FastAPI; por seguridad se omitió `POST /api/backups/automatic/run` y se dejó el run manual en el entrypoint del Container Apps Job.

### 2026-09-19 — Backup diario condicionado en Azure Blob
- El runtime productivo combina API y scheduler en una única Container App y depende de una sola réplica; para backups durables se eligió un Container Apps Job separado, con lease Blob y estado idempotente.
- Container Apps Jobs evalúa cron en UTC. Para respetar `00:15` en una zona IANA (`Europe/Madrid` por defecto) con DST, el Job se activa periódicamente y un gate durable ejecuta una sola vez por fecha local, con catch-up el mismo día.
- El cambio se detecta por hash del dataset lógico canónico antes de ZIP/cifrado; timestamps de export, run IDs, orden, metadata ZIP y nonce quedan fuera. Comparar ZIPs produciría falsos cambios.
- Los ZIP cambiados son objetos inmutables; `latest` se actualiza por ETag/CAS solo tras verificar el upload y puede reconstruirse desde catálogo/blobs.
- Defaults acordados: 35 días de copias cambiadas, 12 anchors mensuales, soft delete/versioning 14 días, staging 1 día y WORM solo por obligación regulatoria.

### 2026-09-19 — Revisión del contrato de backup/import de usuario
- Aprobado con refinamientos el enfoque de backup lógico: holdings bursátiles se reconstruyen desde el ledger completo, mientras que `symbol_config.positions` debe exportarse porque conserva lifecycle, notas, paper state e IDs no derivables.
- Detectada configuración manual persistida que debe clasificarse expresamente en la allowlist: `app-config.calendar_sync` y `app-config.agent_trace.enabled_types`.
- Un archivo de backup no debe representar posiciones dos veces (`symbol-configs` y `option-positions`); debe existir una sola autoridad por registro dentro del ZIP.
- El import seguro por defecto es dry-run + create-only/skip-identical, sin upsert. El writer actual del ledger no es apto para restore porque su auto-reparación puede purgar tombstones/cadenas VOIDED o SUPERSEDED.
- Cualquier apply sobre un destino vivo requiere quiescencia de schedulers/escritores, journal durable, CAS por documento y rollback compensatorio verificable; ETags por sí solos no impiden inserciones concurrentes entre fases.

### 2026-09-11 — Paper positions simplified to a position-only toggle
- Reverted movement-side `is_paper` plumbing from manual creation/correction/duplicate detection so paper status lives only on the symbol position document, per direct user direction.
- Added a dedicated toggle endpoint in `backend/web/app.py:3300` (`PATCH /api/symbols/{symbol}/positions/{position_id}/paper`) backed by `backend/src/cosmos_db.py:703`, instead of faking/linking paper movements.
- Added `coverage_status = "paper"` in `backend/src/portfolio/option_linkage_service.py:407` and suppressed linkage warnings for paper positions so they disappear from the unlinked-warning bucket without affecting real-economics totals.

### 2026-09-11 — Paper positions + economics movement drilldown
- Implemented paper-position persistence and movement parity across `backend/web/app.py`, `backend/src/cosmos_db.py`, `backend/web/portfolio_routes.py`, and `backend/src/portfolio/cosmos_portfolio.py`.
- Added `option_position_id` filtering to the movements API and wired the economics drilldown UI in `frontend/src/lib/portfolio-api.ts` and `frontend/src/components/EconomicsView.tsx`.
- Propagated `is_paper` through linkage/report layers in `backend/src/portfolio/option_linkage_service.py`, `backend/web/app.py`, `frontend/src/types/economics.ts`, and `frontend/src/types/portfolio.ts`.
- Added paper-aware UX in `frontend/src/components/OptionLinkageBadges.tsx`, `MovementDetailDialog.tsx`, `AddPositionForm.tsx`, `AddMovementDialog.tsx`, and `EconomicsOverviewView.tsx`.
- No intentional spec deviations in backend contract scope; frontend implementation detail choices are recorded in `.squad/decisions/inbox/livingston-paper-positions-impl.md`.
