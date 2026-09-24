# Danny — History

## Project Context
- **Project:** options-agent
- **User:** dsanchor
- **Role:** design review, product contract, and final-gate owner
- **Stack:** Python, React, portfolio/economics architecture, documentation-first review workflow

## Core Context

- Danny captures user directives as durable contracts before implementation whenever ambiguity could create churn.
- Favors explicit route structures, crisp field semantics, and event-grain accounting over convenient but misleading UI shortcuts.
- Repeated project themes: portfolio ledger design, economics information architecture, symbol/screener rules, and reviewer gate criteria.
- History before 2026-09 was condensed on 2026-09-09 into this core summary for maintainability.

## Recent Learnings

### 2026-09-24 — Bounded retained scheduler results
- `TaskRegistry.trigger_task_now()` remains allocation-free and
  fire-and-forget by default; only callers that need a terminal result opt into
  retained `TaskRun` state.
- Waiter claims, publication, timeout release, late retrieval, and cleanup
  share one lock. Completed unclaimed results are capped at 64 without evicting
  active or claimed runs.
- The lifecycle repair was retained, but the first revision's global
  success-only `last_run` change was rejected. Final scheduler compatibility
  keeps `last_run` as latest attempt and exposes `last_success` separately.

### 2026-09-09 — Economics Unified Dashboards Design
- Established the three-view Economics information architecture: `/economics`, `/economics/options`, `/economics/dividends`.
- Rejected a blended total KPI in v1 because options economics is not yet FX-normalized while dividends are authoritative in EUR.

### 2026-09-09 — Fiscal Reports design
- Designed a future `/portfolio/fiscal-reports` feature for the Investments menu.
- Chose dividend reporting at composite `ca_group_id` event grain when present, with withholding taxonomy that distinguishes source-only, destination-only, both, and neither while treating absent and explicit-zero withholding as the same visible “no withholding” outcome.
- Deferred CSV export to a later backend-generated phase.

### 2026-09-08 — Share-consolidation and portfolio contract review
- Reinforced the pattern that reviewer feedback should resolve ambiguity at the model/contract layer before more UI or backend code is added.

### 2026-09-19 — Assigned option stock-link picker contract
- Found a read/write contract mismatch: stock `BUY`/`SELL` corrections support
  `ASSIGNMENT_STOCK`, but the shared linkable-position endpoint and frontend picker
  accept only option transaction types.
- Decided to extend the existing picker endpoint: `BUY` lists assigned puts missing a
  linked stock buy; `SELL` lists assigned calls missing a linked stock sell.
- Assignment eligibility must reuse the linkage service's active movement index so the
  picker and `OPTION_ASSIGNMENT_STOCK_MISSING` warning cannot drift.
- Stock dropdown selection should synchronize position ID, `ASSIGNMENT_STOCK`, and the
  candidate option type; no auto-matching or auto-creation.

### 2026-09-19 — User-data backup and restore contract
- Chose a logical, versioned ZIP archive rather than a raw Cosmos dump.
- Full backup covers accounts, Security Master, curated symbol/watchlist settings,
  embedded manual/paper option positions, the complete ledger/audit graph, action
  plans, and non-secret functional settings.
- Stock holdings are derived and must be rebuilt from `ledger_txn`; embedded option
  positions remain authoritative because their lifecycle and notes are not fully
  reconstructible from optionally linked movements.
- Mixed documents must be projected field-by-field: generated enrichment/pricing
  data and action-plan agent notes are excluded by default.
- Import is dry-run first, dependency-closed and idempotent. Ledger conflicts block
  rather than overwrite. Cosmos rollback is compensating, so full replace is deferred
  until maintenance mode, journaling, ETag guards and post-flight verification exist.

### 2026-09-19 — Backup implementation contract review
- Focused review fixed the first release at manual selective export, validate,
  zero-write dry-run, and create-only/skip-identical import; update-existing and
  replacement remain deferred.
- Option positions have one archive authority in a dedicated section rather than
  being duplicated inside projected symbol configs.
- Import apply must re-upload and re-plan against current destination state, create a
  durable journal before writes, use create-if-absent/CAS, and report failed
  compensation as `PARTIAL_REQUIRES_ATTENTION`.
- Automatic backup is a separate Azure Container Apps Job triggered every 15 minutes
  UTC and gated to 00:15 Europe/Madrid by local date; it never joins `TaskRegistry`.
- Blob publication requires a renewable lease, immutable conditional create,
  downloaded-byte verification, and CAS-protected `latest.json`; unchanged scheduled
  content records `NO_CHANGE` without a ZIP.
- Storage uses a dedicated managed identity and container-scoped Storage Blob Data
  Contributor role. The idempotent setup authority is
  `backend/scripts/configure-backup.sh`.

### 2026-09-19 — Backup backend rejection revision
- Under original-author lockout, independently revised the backend backup
  implementation and closed Basher findings 1–6 except Azure lifecycle setup.
- Durable create journals must persist the complete physical-write inventory
  before user data changes. PREPARED entries are treated as ambiguous after a
  crash, probed by canonical hash, compensated, and verified absent before a
  run may report ROLLED_BACK.
- Archive closure must traverse relationship graphs bidirectionally and include
  CA replacement-group edges, not merely direct movement references.
- Deterministic restore controls are most reliable when they reuse production
  FIFO holdings and dividend economics code, augmented with exact audit, link,
  option, FX, and withholding controls; import must recompute them from actual
  destination reads.
- `latest.json` is recoverable state, not authority: write the verified
  append-only run record before pointer publication, then reconstruct stale or
  missing pointers from run records plus immutable verified blobs.
- Monthly retention must preserve anchors beyond shorter run-record retention;
  reconciliation therefore merges valid existing anchors with recent runs,
  keeps twelve, deletes expired anchors, and retags only currently referenced
  immutable blobs as monthly.

### 2026-09-20 — Automatic backup configuration-source revision
- A separate API Container App cannot truthfully expose Container Apps Job
  environment values without Azure control-plane access.
- Removed the automatic-backup API/UI status surface rather than duplicating
  configuration or presenting API-process defaults as effective Job state.
- Automatic backup configuration and monitoring now live exclusively in the
  Job environment plus Container Apps/Blob/Portal/CLI operational surfaces;
  manual export/import Settings remain unchanged.

### 2026-09-20 — Blob tag-write custom-role revision
- Azure custom-role `AssignableScopes` cannot be narrowed to a Blob container;
  the resource group is the narrowest valid definition scope, while the actual
  assignment remains at the exact container.
- A deployment-derived UUIDv5 plus UUID-bearing display name prevents unrelated
  same-name roles from being silently accepted.
- Safe reruns normalize and compare the existing role's single permission
  block and assignable scope, fail closed on drift, never overwrite an
  administrator-managed definition, and verify the assignment by exact role ID
  and exact container scope.

### 2026-09-22 — Dashboard trigger slot concurrency revision
- In-flight trigger slots use the dashboard run ID as an immutable ownership
  token; completion removes a slot only when that run still owns the current
  agent/symbol record, so an expired worker cannot release its replacement.
- Trigger slots and run-status data now live in one eagerly initialized process
  state object, with an atomic fallback initializer for isolated TestClient
  state, eliminating split lazy-registry/lock initialization.

### 2026-09-24 — Dividend filter pagination revision
- Offset pagination is safe only when the authoritative service applies a total
  order. Movement pages now sort descending by `trade_date` and stable unique
  movement `id` before slicing, including equal-date boundaries.
- Multi-page Dividend/search retrieval must stop on a short-page exhaustion
  signal rather than raw or deduplicated count; count-based stopping can miss a
  unique later row when pages overlap.
- Shared abortable request generations let both global Movements and symbol
  history reject stale rows, counts, loading, errors, and pagination state,
  including after component unmount. Stable-ID dedupe remains defense-in-depth,
  never the authoritative pagination mechanism.

### 2026-09-24 — Historical rights migration independent revision
- A write-capable migration case has one durable journal head guarded by
  revision/state ETag CAS, an expiring operation lease, and a fencing token
  checked before every ledger mutation. Concurrent apply/resume/rollback losers
  cannot enter writes or compensate another operator's documents.
- Saved previews bind outcome, actor, rationale, account, canonical security,
  exact source IDs, warning resolutions, and an explicit canonical
  endpoint/database/container target identity. Apply requires literal parity
  and revalidates source identity from storage.
- Mixed rights outcome C may preserve exactly one genuine cash-dividend leg
  alongside acquired shares and leftover rights sold; source removal plus one
  cash leg prevents duplicate dividend income.
- Rights discovery retains distant same-account/security alternatives within a
  configurable horizon (or unbounded review) and marks distance/text-only
  evidence weak rather than auto-linking or discarding it.
- Strict user backup now has an authoritative rights-migration case section and
  fail-closed ledger fields, with bidirectional journal/ledger dependency
  closure and exact round-trip preservation.

### 2026-09-24 — Historical rights terminal recovery revision
- A terminal lease seal is now a complete durable commit intent: it binds the
  apply-versus-rollback terminal state, expected journal head, operation, lease,
  fence, full terminal payload, and canonical payload/verification hashes.
- Lease release and expiry retain an unreflected seal. Resume can materialize
  only that validated intent, reconcile an already-written terminal revision,
  and repeat safely without re-running writes or guessing state.
- Pending intent still blocks takeover, while completed recovery clears the
  seal so an expired lease can advance to a higher fence; stale owners remain
  unable to write or clear the winner's lease.

### 2026-09-24 — Dashboard banner run lifecycle revision
- Manual scheduler triggers remain fire-and-forget by default and allocate no
  completion record. Callers that need a result opt in with
  `retain_result=True`.
- Retained terminal results are capacity-bounded, while active runs and runs
  with attached waiters are never pruned. Waiter accounting makes completion,
  timeout, concurrent retrieval, and cleanup deterministic.
- The banner endpoint is the sole opt-in caller and awaits its owned run, so
  failure still propagates without advancing `last_run`, while successful
  completion returns the persisted-run timestamp.

### 2026-09-24 — Guided rights migration trust revision
- Local guided state and public checksums are navigation/corruption aids only;
  they can neither authorize a write nor silently retire a no-write case.
- Every restarted non-VERIFIED write path requires a fresh exact live-TTY
  confirmation immediately before prepare/apply/resume. A legacy persisted
  `confirmed` phase has no authority; only a target-bound `VERIFIED` service
  journal proves completion and enables prompt-free reconciliation.
- Local skip/reject/needs-evidence records are displayed and exactly
  re-acknowledged on every resume, including forged terminal or previewed
  forms. This makes tampering visible without claiming local authenticity.
- Guided error diagnostics now redact credential/key/token/password/secret
  variants and known environment values through nested causes and repr-only
  exceptions while preserving useful exception types and context.

### 2026-09-24 — Dashboard option-type identity revision
- Monitor identity treats `type`, `current_option_type`, `option_type`, and
  `right` as one strict option-type alias set at both the document top level
  and nested `source`.
- Explicit option type accepts only trimmed, case-insensitive `call` or `put`.
  Null, blank, malformed, unknown, or conflicting aliases fail closed.
- Exact `position_id` assignment still requires every explicit option-type
  constraint to agree; mismatch never degrades to contract or symbol fallback.
