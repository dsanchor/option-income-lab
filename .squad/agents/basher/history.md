# Basher — History

## Project Context
- **Project:** options-agent
- **User:** dsanchor
- **Role:** test, regression, and reviewer-gate owner
- **Stack:** pytest, Node test runner, TypeScript contract checks, backend/frontend seam validation

## Core Context

- Basher validates production-shaped behavior, boundary conditions, persistence seams, and frontend/backend contract parity.
- Preferred test style: authoritative focused suites first, then broader confidence runs sized to the exact blast radius.
- Frontend verification may stay lightweight and source-contract based when the repo already favors typed/export contract tests over brittle component rendering.
- Durable rule: options economics uses the 100-share multiplier only for dollar-value calculations, never for counts or ratios.
- History before 2026-09 was condensed on 2026-09-09 into this core summary to keep the file usable.

## Recent Learnings

### 2026-09-09 — Dividends economics validation scope
- Added dedicated backend unit tests for dividends economics plus endpoint smoke coverage.
- Kept frontend validation intentionally lightweight with a contract test around `frontend/src/types/economics.ts` and the consuming views, matching existing repo testing patterns.

### 2026-09-08 — FIFO / net-accounting integration gate
- Cleared the FIFO integration gate after confirming stale failures were test-expectation drift rather than product defects.
- Reinforced the rule that reviewer verdicts must distinguish true implementation bugs from collateral stale tests.

### 2026-09-07 — Release-gate discipline
- Maintained the expectation that final release gates target zero unexpected skips/xfails inside the scoped authored suites and explicitly document any pre-existing unrelated failures.

### 2026-09-19 — Assigned option-to-stock correction linkage
- Approved the assigned-put/stock-BUY and assigned-call/stock-SELL linkage change after verifying the picker reuses the warning engine's active-link index, so deleted, voided, and superseded movements do not suppress candidates.
- Focused validation passed: 77 backend tests, 4 frontend contract tests, TypeScript type-check, and ESLint on the changed frontend files.
- Confirmed correction replacement semantics make the saved active stock link suppress the candidate and clear `OPTION_ASSIGNMENT_STOCK_MISSING`, while wrong-direction links do neither.

### 2026-09-19 — User-data backup release gate rejected
- Focused suites passed, but independent adversarial probes found release-blocking gaps hidden by the authored fixtures.
- A ZIP character-device entry was accepted; archive validation only rejects symlinks, not all non-regular file types.
- Import create inventory is journaled after each user-data write. An injected journal-save failure left `sec_XNAS_AAPL` orphaned while the API reported `ROLLED_BACK`, proving the durable-before-write/compensation contract is not met.
- Corporate-action replacement closure omitted the linked replacement group; import closure validation also does not enforce transfer/CA/reassignment group completeness.
- Recursive secret scanning is not fail-closed for malformed serialized JSON or opaque token/JWT-like values.
- Manifest/post-flight controls contain counts/statuses only; holdings, cost basis, realized results, dividends, option economics, and inactive-history controls are not computed or compared.
- Automatic backup lacks stale/missing `latest.json` recovery. Monthly retention is documented as requiring tags/reconciliation that runtime does not implement, and the Job does not pass the user-assigned identity client ID to `DefaultAzureCredential`.
- Frontend automatic-backup types/rendering expect strings, while the backend returns objects for `last_scheduled_success` and `latest_changed_archive`; a populated status can render an object as a React child.
- Full backend and full frontend lint remain red for unrelated pre-existing failures; changed-file lint, TypeScript, clean frontend build, compile, focused backup tests, and portfolio regressions passed.

### 2026-09-19 — User-data backup second release gate rejected
- Confirmed the ZIP special-file, durable journal/compensation, secret scan, round-trip controls, Blob recovery/retention, UAMI selection, frontend status rendering, and local/static infrastructure revisions.
- Focused backup/infra tests passed 45/45; portfolio/economics regressions passed 392/392; frontend contracts passed 16/16; TypeScript, changed-file ESLint, clean production build, Python compile, shell syntax/help/authenticated dry-run, and `git diff --check` passed.
- Independent malformed-graph probe still produced no validation errors for a transfer group containing two `TRANSFER_IN` legs and a `DIVIDEND_WITH_SCRIP` CA group missing its required `SHARE_ACQUISITION` leg. Import validation therefore still does not independently enforce transfer or CA group integrity.
- Independent scheduler probe showed a failed scheduled attempt overwrites `health.json` and erases the prior `last_scheduled_success`, defeating truthful status and the documented 26-hour stale-success alert.
- Backend revision author Danny is locked out from the next repair. Linus is assigned as the next backend revision author. Frontend and infrastructure revisions are locally ready, but the combined release remains unsafe to commit/deploy. Live Azure acceptance remains external and unverified.

### 2026-09-19 — User-data backup final gate rejected
- Confirmed malformed ordinary transfers are now rejected for duplicate direction, nonreciprocal peers, and invalid same-account relationships.
- Confirmed missing and transaction-mismatched CA legs are rejected from the production `_CA_REQUIRED_LEGS` and `_CA_LEG_TXN_TYPE` definitions.
- Confirmed the `UPLOADED` → `NO_CHANGE` → `FAILED` sequence preserves the prior `last_scheduled_success` and latest archive while recording the failed attempt and error truthfully.
- Found one release-blocking regression: valid production `SHARE_CONSOLIDATION` groups use `TRANSFER_OUT`/`TRANSFER_IN` legs with `ca_group_id`, not ordinary transfer group metadata. The revised validator applies `missing_transfer_group` to both legs, so a valid production backup cannot be imported.
- Validation passed: 53 focused backup/infra tests, 95 transfer/CA regressions, 16 frontend contracts, 6 infra tests, TypeScript, Python compile, shell syntax, and `git diff --check`.
- Linus is locked out from the next backend repair. Rusty is the next eligible author. Combined release remains unsafe to commit/deploy.

### 2026-09-19 — User-data backup release approved
- Approved Rusty's narrow dependency-closure repair after independently confirming production-shaped `SHARE_CONSOLIDATION` legs are accepted under `ca_group_id` without ordinary transfer metadata.
- Confirmed incomplete and transaction-mismatched consolidation groups remain blocked, valid ordinary transfer pairs remain accepted, and malformed direction, peer, and account relationships remain blocked.
- Focused backup/dependency tests passed 49/49; transfer/CA regressions passed 95/95 with 3 pre-existing warnings; independent probes, Python compile, and `git diff --check` passed.
- No dependency-closure regression found. The combined backup implementation is code-ready and safe to commit. Live Azure acceptance remains a separate deployment verification.

### 2026-09-20 — Backup config-source and economics average-line gate rejected
- Approved the economics chart change: the card and horizontal reference line consume one shared last-12, non-zero-month average, including negative, exact-zero, missing, and short datasets.
- Rejected the combined gate because `GET /api/backups/automatic` constructs `AutomaticBackupConfig` from the API container process environment, while `configure-backup.sh` writes the authoritative values only to the separate Container Apps Job. Direct Job environment changes can therefore leave the read-only frontend displaying defaults or stale API-app values as effective Job configuration.
- Validation passed: 58 focused backup/infrastructure tests, 104 economics/portfolio regressions, 20 frontend contracts, TypeScript, changed-file ESLint, clean production build, Python compile, shell syntax/help, and `git diff --check`.
- Rusty is strictly locked out from the next backup config-source revision. Danny is assigned as the different revision author. Combined changes are not safe to commit until status derives configuration from Job-produced durable status or otherwise reads the actual Job configuration without introducing a second authority.

### 2026-09-20 — Danny backup config-source revision rejected
- Confirmed the public automatic-backup API/BFF/card/types were removed, manual export/import still build and pass contracts, Job environment remains the runtime authority, and the approved economics monthly-average line is unchanged.
- Rejected because `AutomaticBackupService.get_status()` remains as unreachable automatic configuration/status presentation code and two scheduler assertions still exercise that dead surface. Repository search found no production caller, so the revision does not satisfy the explicit requirement to remove the misleading surface without dangling tests.
- Validation passed: 59 backup/infrastructure tests, 230 economics/portfolio regressions, 16 frontend contracts, TypeScript, changed-file ESLint, production build, Python compile, shell syntax/help, and `git diff --check`. Build retained one existing generated-CSS warning.
- Danny is locked out from the next revision. Livingston is the next eligible author. Changes are not safe to commit until the dead `get_status()` surface and its dedicated assertions are removed without changing Job runtime or Blob health/run records.

### 2026-09-20 — Livingston backup status-removal revision approved
- Approved the narrow removal of `AutomaticBackupService.get_status()` and its three dedicated scheduler-test references; repository search found no remaining implementation caller or automatic API/BFF/UI status/config surface.
- Confirmed environment-only automatic configuration, DST/due gating, scheduled upload/no-change/failure behavior, manual execution, latest recovery, Blob health/run persistence, archive/import paths, CLI wiring, and documentation remain intact.
- Validation passed: 58 backup/infrastructure tests, 11 frontend backup contracts, Python compile, shell syntax/help, YAML/config authority checks, documentation searches, and `git diff --check`.
- Final verdict: APPROVE. The combined backup configuration revision is safe to commit; live Azure acceptance remains a deployment check.

### 2026-09-20 — Symbol Details holding P&L approved
- Approved the additive nullable Symbol Details EUR valuation/P&L contract and Stocks-first tab revision.
- Confirmed the backend reuses the cached Symbols Overview EUR price and the existing holdings snapshot/FIFO residual basis without provider calls or per-row queries; unavailable/non-finite valuation, closed holdings, and zero/non-positive denominators remain null rather than producing misleading values.
- Confirmed the holdings UI consumes backend P&L fields directly, formats signed positive/negative and neutral zero values, and exposes non-color ARIA gain/loss/unavailable semantics.
- Confirmed Stocks → Options → Action Plans ordering/default while explicit hashes, query strings, and browser hash/history synchronization remain intact.
- Validation passed: 276 backend regressions, 382 frontend contracts, 4 independent valuation probes, TypeScript, changed-file ESLint, production build, Python compile, and `git diff --check`.
- Final verdict: APPROVE. No blockers; safe to commit.

### 2026-09-20 — Production backup schema alignment rejected
- Confirmed the added security migration fields and ledger company/warning fields are persisted authoritative/reference data, `pricing_cache` is omitted from exported symbol configs, unknown fields remain fail-closed, and automatic schema diagnostics expose only section, hashed logical identity, issue, and sanitized field paths.
- Rejected because adding `activity_id` and `source_activity_id` to the global leaf-name identifier allowlist suppresses JWT/opaque-token detection at any nested path with either name. An independent probe showed `metadata.activity_id=<JWT>` and top-level `source_activity_id=<JWT>` both pass, exceeding the required narrow exception for position provenance `source.activity_id`.
- Validation passed: 59 focused backup/infrastructure tests, 110 migration/security/pricing persistence regressions (3 pre-existing warnings), Python compile, and `git diff --check`. Independent secret-scanner probes reproduced the scope defect.
- Livingston is locked out from the next repair. Rusty is assigned as the different revision author. Verdict: REJECT; unsafe to commit or deploy until the exception is path-scoped and regression tests prove token/JWT/credential detection remains active everywhere else.

### 2026-09-20 — Rusty production backup schema revision approved
- Approved the narrow secret-scanner repair: no global `activity_id` or `source_activity_id` exemption remains, and only opaque non-JWT provenance at the exact option-position path `$.source.activity_id` is permitted.
- Confirmed JWTs, bearer/account-key values, credential keys, adjacent source fields, settings, ledger warnings, and arbitrary structures remain rejected; projection and archive validation use the same section-scoped rule.
- Confirmed the legitimate security migration and ledger fields remain, `pricing_cache` remains stripped, diagnostics remain value-free, production-shaped export remains viable, and the separately approved Symbol Details P&L/tab changes are untouched.
- Validation passed: 69 focused backup/infrastructure tests, 218 migration/security/pricing persistence regressions (4 pre-existing warnings), 71 Symbol Details backend regressions (3 pre-existing warnings), 78 frontend contracts, independent secret-rule probes, Python compile, and `git diff --check`.
- Final verdict: APPROVE. Safe to commit. Production requires building and deploying a new image containing this revision before retrying the read-only export; the currently deployed image remains unfixed.

### 2026-09-20 — Blob tag RBAC and daily cron gate rejected
- Approved the daily `15 23 * * *` UTC schedule and its 00:15 Madrid standard-time / 01:15 daylight-saving-time documentation; the internal due/local-date/idempotency gate remains retry/manual safety only, and no stale backup `*/15` claim remains.
- Confirmed Blob diagnostics expose operation, fixed path category, status, error code, and request ID without object names or payloads. Immutable create, lease, archive verification, CAS pointers, and retention tagging remain intact.
- Rejected the production Blob provisioning change because an existing same-name custom role is accepted without validating or repairing its actions and scopes. The new role is also assignable at the whole resource group rather than the intended backup-container scope, so its least-privilege boundary is broader than required.
- Deployment documentation still says the identity receives only `Storage Blob Data Contributor`, omitting the required tag-write role.
- Validation passed: 70 focused backup/infrastructure tests, shell syntax/help/authenticated dry-run, Python compile, documentation/stale-cron searches, and `git diff --check`.
- Livingston is locked out from the next Blob RBAC revision. Danny, Linus, or Rusty are eligible independent revision authors. Verdict: REJECT; the combined dirty change is unsafe to commit. Rusty's cron artifact is independently approved.

### 2026-09-22 — Dashboard trigger run-state gate rejected
- Focused dashboard-trigger, scheduler Run Now, force-alpha scoping, and registry tests passed 29/29.
- Settings Run Now now correctly returns enqueue rejection as HTTP 409 and preserves `run_trigger="manual", force_alpha=False`; successful and first-run failed dashboard cases also pass.
- Rejected because `_dashboard_agent_runs` is an unlocked, one-record-per-agent map while the trigger contract explicitly permits concurrent runs for different symbols of the same agent. A deterministic probe ran AAPL and MSFT concurrently: MSFT succeeded, then AAPL failed, and final status reported `symbol="MSFT"` with `error="AAPL failed"`. Completion order can therefore overwrite/misattribute another run and transiently report success while work is still active.
- The status endpoint iterates the same dict while background threads can add keys, risking `dictionary changed size during iteration`; concurrent first writes can also lose an entry.
- The frontend polling signature ignores `agent_statuses`, and `TriggerButton` reports `Triggered` immediately without following completion. Running/failure transitions that preserve `last_run` are therefore invisible to polling after a prior success, so the user-facing “failure looked triggered” problem remains.
- Required revision: synchronize state, identify runs (or key state by agent+symbol), make completion updates conditional on the originating run, define aggregate per-agent semantics for concurrent runs, include execution state in polling/UI behavior, and add ordered-concurrency plus success→running→failure retention tests.
- Final verdict: REJECT. Rusty is locked out from the next revision by reviewer protocol.

### 2026-09-20 — Danny Blob tag-role revision rejected
- Confirmed the deterministic UUIDv5 role ID/name, exact sole `blobs/tags/write` DataAction, empty Actions/NotActions/NotDataActions, fail-closed role-definition comparison, exact-ID role creation/assignment, prerequisite documentation, daily cron, sanitized diagnostics, and Blob guarantees.
- Rejected because the role still uses resource-group `AssignableScopes` while current Microsoft RBAC documentation permits resource-instance assignable scopes; the storage account is at minimum a narrower valid parent boundary, so the documentation's claim that the resource group is narrowest is false.
- An adversarial assignment probe supplied both the exact container assignment and an inherited resource-group assignment for the same principal and deterministic role. The validator returned success, so broader effective access is not failed closed and the script does not establish an exact-only assignment.
- Validation passed: 73 focused backup/infrastructure tests, shell syntax/help/authenticated dry-run, Python compile, docs/YAML/stale-cron searches, and `git diff --check`.
- Livingston and Danny are locked out from the next Blob RBAC revision. Linus or Rusty are eligible; Linus is preferred. Verdict: REJECT; unsafe to commit. The daily cron and Blob diagnostic/runtime portions remain approved.

### 2026-09-22 — Livingston dashboard trigger run-state follow-up rejected
- The exact ordered adversarial scenario now keeps AAPL's late failure and MSFT's earlier success correctly scoped by run ID; the deterministic per-agent aggregate remains the newer-started MSFT success.
- Run-state locking, safe snapshots, conditional run-ID completion, last-success retention, bounded completed-run retention with active runs preserved, API shape, 409 behavior, frontend exact-run polling, cleanup hooks, and AutoRefresh aggregate consumption were otherwise confirmed.
- Rejected because the in-flight slot registry is not stale-completion safe: after a timed-out slot is reclaimed, the original worker's unconditional key-only release deletes the replacement run's slot, allowing a third duplicate run while the replacement is active.
- The in-flight registry/lock first-use initialization is also unsynchronized. A deterministic two-thread first-access probe accepted both claims for the same agent/symbol under separate locally created locks.
- Validation passed: 40 focused backend tests, 5 frontend contract tests, TypeScript, changed-file ESLint, `git diff --check`, ordered concurrency, last-success, retention-with-active, and snapshot probes. The authored suites do not cover the two failing in-flight ownership races.
- Final verdict: REJECT. Livingston is locked out for the next dashboard-trigger revision.

### 2026-09-22 — Dashboard third revision and Scrip Dividend FMV review
- **DASHBOARD: REJECT.** The exact stale-owner and simultaneous-first-acquisition race probes now pass. One process-state object is initialized under a dedicated lock; trigger and run locks have no observed inversion, exact run-ID completion, AAPL/MSFT isolation, last-success retention, bounded retention, 409 behavior, and frontend exact-run polling remain intact.
- Independent trigger-start failure injection found a blocker: if `threading.Thread.start()` raises after the slot and run record are created, the endpoint returns 500 while leaving the `(agent, symbol)` slot occupied and the run permanently `running`. Thread construction has the same unguarded gap. Cleanup currently covers `_start_dashboard_run()` failure and worker completion only.
- Dashboard validation passed: the two exact rejected race probes, 38 broader focused backend tests, 5 frontend contracts, TypeScript, changed-file ESLint, and `git diff --check`.
- **SCRIP: REJECT.** Positive EUR FMV `5.3` survives create, stored Cosmos shape, active readback, group correction, and correction readback as `COMPLETE`; holdings tests place €5.30 in FIFO residual basis without dividend income. Explicit zero remains `ZERO_COST`; blank remains `INCOMPLETE`; foreign gross without EUR authority remains non-complete; no cash-top-up leg is synthesized.
- Release blocker: backend create and correction do not reject invalid FMV. Negative EUR FMV is accepted and persisted; malformed text is silently coerced to zero and can become `ZERO_COST`; `NaN`/`Infinity` escape as `decimal.InvalidOperation` rather than a controlled validation rejection. Create/correction share the defect. A stale `COMPLETE` status with non-EUR gross and missing EUR amount is also canonicalized to `ZERO_COST` rather than `INCOMPLETE`.
- Scrip validation passed: 129 focused backend tests, 75 frontend contracts, 115 zero-cost/FIFO regressions, TypeScript, and changed-file ESLint. No unrelated full-suite failures were used for either verdict.

### 2026-09-22 — Dashboard transactional-start and Scrip FMV final re-review
- **DASHBOARD: APPROVE.** Thread construction and `start()` failures now complete the exact run as failed, release only its owner-token slot, return HTTP 503 `failed_to_start`, and permit an immediate successful retry. Worker cleanup remains owner-scoped; stale owners cannot release replacement slots; simultaneous first acquisition admits exactly one owner; duplicate requests remain 409; no tested failure returned a false `triggered` response or left a run stuck.
- Dashboard evidence: 44/44 focused backend tests, including all construction/start/retry/stale-owner/first-acquire/duplicate probes; 5/5 frontend dashboard contracts within the 85-test frontend run.
- **SCRIP: APPROVE.** Shared backend normalization is used by create and group correction. Missing/blank is `INCOMPLETE`, explicit zero is `ZERO_COST`, positive finite EUR FMV is `COMPLETE`, submitted status is ignored, and negative/non-finite/malformed values produce controlled HTTP 400 responses. Non-EUR native amount/currency survive without EUR authority while status remains `INCOMPLETE`.
- Scrip evidence: 203/203 corporate-action, correction, holdings, zero-cost/import, and FIFO tests. Positive €5.30 persists through correction/readback and enters FIFO basis only; it does not inflate dividend income or synthesize/inflate cash top-up.
- Cross-checks passed: 85/85 frontend contracts, TypeScript, changed-file ESLint with four existing warning-only findings, Python compile, and `git diff --check`. Only unrelated existing FastAPI/py_vollib/pandas deprecation warnings remain.
- Residual limitation: dashboard execution state and locking are process-local, so cross-process coordination/persistence still depends on single-process deployment assumptions; no blocker for the reviewed contract.

### 2026-09-23 — Multi-account holdings and movement-label review
- **HOLDINGS: APPROVE.** FIFO lots are isolated by `account_id`; aggregate shares and residual basis equal the sum of independently computed account holdings, including zero-cost scrip dilution and zero contribution from a fully sold account. A remaining incomplete account component makes the consolidated average null while preserving account-filtered behavior. Independent cross-account transfer and security-identity probes preserved carried cost without double counting.
- **LABELS: APPROVE.** One shared helper derives `Dividend · Buy` solely from `txn_type`, `ca_leg_type`, and `ca_event_type`; all six requested movement surfaces consume it. Ordinary buys, rights acquisitions, cash dividends, and missing CA metadata retain the intended labels, while filters, badge classes, and accounting remain keyed to stored transaction types.
- Validation: 109/109 holdings/FIFO/symbol-detail backend tests, 12/12 label contracts, related movement/filter contracts apart from two unrelated existing source-contract failures, TypeScript, clean changed-file ESLint except the unchanged `StockTransactionsTable` effect finding reproduced against `HEAD`, and `git diff --check`.
- Residual limitations: transfer preservation is independently probed but lacks a new committed cross-account regression test. Existing unrelated `economicsParity` paper-action and `movementDetailDefensiveGuards` sparse-field source assertions remain red.
