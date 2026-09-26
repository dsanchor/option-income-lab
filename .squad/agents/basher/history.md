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

### 2026-09-26 — Roll contract quantity final gate approved
- Danny's revision closed all three prior blockers: legacy implicit-one now
  requires unversioned historical writer evidence with matching UTC ID/open
  timestamps strictly before the schema-v2 boundary; successful rolls resolve
  before mutation and always persist canonical positive contracts/schema v2;
  Roll Scenarios rejects inactive, ambiguous, malformed, and quantity-invalid
  positions before provider or chain access.
- Independently verified alias precedence/conflicts, signed shorts, invalid
  numeric forms, legacy and partial-close roll persistence, exact identity,
  full-position economics, warnings/source, Decimal/midpoint/chain behavior,
  and no simulation/scenario mutation.
- Validation passed 421 backend tests, 6 focused frontend tests, 1,341/1,343
  full frontend contracts with the same two unrelated failures, and 49
  adversarial assertions/probes. Static checks, production build, and diff
  hygiene passed. Final verdict: APPROVE.

### 2026-09-25 — Dashboard Banner provider-contract final gate approved
- Saul's revision now consumes only the exact `TechnicalsCalculator` summary,
  moving-average, indicator-entry, and real history-timestamp contract.
  Unsupported aliases, casing normalization, alternate nesting, scalars, and
  lookalikes fail closed; authoritative conflicts are rejected, while valid
  fields coexisting with aliases remain single-counted.
- Independently confirmed the prior exact-SMA50/alias-envelope blocker now
  yields deterministic no-data with zero facts, counts, watermarks, or LLM
  calls. Exact technical and MA schemas, including direct real-calculator
  output, preserve one snapshot and the actual history watermark.
- Validation passed 168 focused backend tests, 20/23 full yfinance provider
  tests with the same 3 unrelated option-chain fixture failures, 10 frontend
  contracts, and 7 independent probes. Scoped Ruff, compile, TypeScript, and
  Saul diff hygiene passed. Final verdict: APPROVE.

### 2026-09-25 — Dashboard Banner strict MA allowlist final gate rejected
- Reuben's exact direct indicator allowlist correctly blocks generic
  period/length structures, deceptive/case variants, nested recognized copies,
  unknown oscillators, non-finite values, and unsupported default evidence.
- Rejected because the surrounding recommendation schema remains permissive:
  an exact `SMA50` combined with unsupported `name`/`score` recommendation
  aliases and case-normalized `Buy` count invoked the LLM, counted/published a
  market snapshot and watermark, and persisted commentary. The summary path
  accepts the same non-provider aliases.
- Validation passed 94 focused backend tests and 10 frontend contracts; the
  same 3 unrelated option-chain fixture failures remain. Seven independent
  evidence cases passed and two strict-schema alias cases failed. Ruff,
  TypeScript/ESLint, Python compilation, and diff hygiene passed.

### 2026-09-25 — Dashboard Banner semantic freshness final gate rejected
- Danny's revision closed provider-shaped empty/null/non-finite/old/future
  handling and replaced unbounded dashboard polling with a single-flight,
  abort-deadlined, generation-fenced AutoRefresh poller.
- Rejected because provider-default `NEUTRAL` technical and moving-average
  recommendations remain semantically eligible. A default-only payload invoked
  the LLM, persisted commentary, counted one market snapshot, and exposed a
  current watermark instead of deterministic no-data metadata.
- Validation passed 230 focused backend tests and 115 frontend tests; the same
  3 unrelated provider option-chain fixture failures remain. Independent
  AutoRefresh and timestamp probes passed; the default-only semantic probe
  failed. Type, scoped lint, compile, production build, and diff hygiene passed.

### 2026-09-25 — Dashboard Banner freshness rereview rejected
- Livingston's revision fixed explicit provider errors/malformed payloads,
  bounded provider timeout with no late persistence, actual source watermarks,
  counts/API/UI coverage, exact reload verification, monotonic generated time,
  old-event filtering, no-recent fallback, and legacy Last Run behavior.
- Rejected because provider-shaped payload wrappers containing no usable facts
  still satisfy `any(parsed.values())`; an independent probe counted the empty
  snapshot and persisted LLM commentary with a current `source_as_of`.
- Dashboard `AutoRefresh` is still unbounded: interval polling has neither an
  abort deadline nor an in-flight guard, so hung status requests overlap.
- Validation passed 166 focused backend tests and 115 focused frontend tests;
  the same 3 unrelated provider option-chain fixture failures remain. Type,
  scoped lint, compile, production build, and diff hygiene passed.

### 2026-09-24 — Dashboard Banner last-run gate approved
- Rejected successive revisions for false completion/global polling, unbounded
  retained `TaskRun` state, and a global `last_run` compatibility regression.
- Approved the final split semantics: all scheduler consumers retain
  latest-attempt `last_run`; explicit `last_success` drives Dashboard Banner
  presentation with persisted `generated_at` as restart fallback.
- Final validation passed 76 focused backend tests, 69 banner/Azure-order tests,
  5 focused frontend tests, concurrency/capacity probes, and type, lint,
  compile, import, and diff checks. The isolated `azure.core` stub failure is
  pre-existing and order-dependent.

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

### 2026-09-26 — Rights-removal integration gate rejected
- Rejected after 934 focused backend tests and 430 focused frontend tests passed,
  because independent adversarial probes exposed three fail-closed gaps.
- A legacy dividend carrying non-zero `source_derechos_amount` is recognized by
  the strict backup detector but `sanitize_legacy_movement()` removes the field
  and retains the dividend. It then contributes cash income/counts instead of
  remaining inert. The authored dividends test explicitly expects this legacy
  record to remain included.
- The six-column sales CSV path still accepts a zero-quantity, positive-proceeds
  sale and merely warns that it may be a rights transaction, allowing silent
  conversion into an ordinary stock SELL.
- Frontend compatibility filtering is not fail-closed: malformed non-numeric
  rights amounts and whitespace/lowercase legacy type/warning values are
  displayed because checks require finite numeric and exact-case matches.
- Exact search found 36 legacy identifier occurrences across 9 active source
  files and 170 across 21 test files. Runtime rejection/compatibility guards are
  justified, but multiple stale tests/comments still describe and assert the
  removed positive rights model rather than explicit rejection/inertness.
- TypeScript, production build, removal-specific ESLint, new policy Ruff, Python
  compile, and diff/conflict checks passed. Full frontend remained at 1325/1327
  with the same two unrelated baseline failures; broader changed-file lint
  retained three existing DividendsView hook findings and legacy Ruff debt.

### 2026-09-20 — Blob tag RBAC and daily cron gate rejected
- Approved the daily `15 23 * * *` UTC schedule and its 00:15 Madrid standard-time / 01:15 daylight-saving-time documentation; the internal due/local-date/idempotency gate remains retry/manual safety only, and no stale backup `*/15` claim remains.
- Confirmed Blob diagnostics expose operation, fixed path category, status, error code, and request ID without object names or payloads. Immutable create, lease, archive verification, CAS pointers, and retention tagging remain intact.
- Rejected the production Blob provisioning change because an existing same-name custom role is accepted without validating or repairing its actions and scopes. The new role is also assignable at the whole resource group rather than the intended backup-container scope, so its least-privilege boundary is broader than required.
- Deployment documentation still says the identity receives only `Storage Blob Data Contributor`, omitting the required tag-write role.
- Validation passed: 70 focused backup/infrastructure tests, shell syntax/help/authenticated dry-run, Python compile, documentation/stale-cron searches, and `git diff --check`.
- Livingston is locked out from the next Blob RBAC revision. Danny, Linus, or Rusty are eligible independent revision authors. Verdict: REJECT; the combined dirty change is unsafe to commit. Rusty's cron artifact is independently approved.

### 2026-09-24 — Dashboard Banner third revision approved
- Approved Linus's restoration of global scheduler `last_run` attempt semantics and the banner-only successful-generation presentation boundary.
- Confirmed success, failure, and timeout metadata consistency; restart fallback; prior-success preservation; first-failure `Never` plus explicit error; immediate manual completion handling; bounded waiter-safe retained runs; and one dashboard refresh per banner generation signature change.
- Validation passed: 76 focused backend tests, 69 banner/Azure-order tests, 5 frontend contracts, capacity-16 retention across 40 runs, 24 concurrent waiters, status/manual-response probes, TypeScript, scoped ESLint, Python compilation, and diff checks.
- The isolated `test_banner_agent.py` collection failure is an unchanged order-dependent test stub that shadows installed `azure.core`, not a missing package or product defect.
- Final verdict: APPROVE. No source/test edits, commit, push, deployment, or production access.

### 2026-09-24 — Dashboard position-monitor identity review rejected
- Confirmed distinct persisted `position_id` values preserve separate same-contract,
  cross-account, paper/real, call/put, expiry/strike, activity, alert, and snapshot
  rows; React row keys are position-based and the complete Activities feed remains
  intact.
- Rejected because a legacy activity with no `position_id` but an explicit stale
  contract first misses the contract lookup and then falls through to the
  single-symbol fallback. After a roll, an old-contract activity is therefore
  assigned to the sole new active position, borrowing its recent activity, risk,
  and price instead of remaining blank.
- Validation passed: 46 focused backend tests, 10 frontend tests, TypeScript,
  scoped ESLint, Python compilation, scoped diff checks, and positive identity
  probes. The independent stale-contract probe reproduced the blocker.
- Final verdict: REJECT. No implementation/test edits, commit, push, deployment,
  or production access.

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

### 2026-09-23 — Global Monitoring Agent member-gate review rejected
- **REJECT.** Scheduled execution, full analysis, Settings Run Now, unified scheduler Run Now, and direct dashboard triggers all use the shared explicit-false gate. Disabled Buy Tracker is skipped while enabled members continue; master scheduling remains separate; direct disabled triggers return explicit HTTP 409 before allocating trigger slots or run records.
- Dashboard mapping correctly covers all five members, including active call/put positions. Disabled sections and rows are dimmed and carry visible `Deactivated globally` plus ARIA semantics. Settings types, controls, payload, persistence, and valid save/load round-trip cover all five values. AutoRefresh includes the global-gate map and changes signature once per state change.
- Release blocker: live Cosmos reload does not replace or clear `scheduler.agents` when the persisted value is missing or malformed. After Buy Tracker is explicitly disabled, reloading settings with no `agents` key or `agents: "invalid"` leaves the stale in-memory `{"buy_tracker": false}` gate active. This violates the required missing/invalid-default-enabled rule and can accidentally keep an agent disabled until restart or a later valid map.
- Validation: 39 focused backend tests and 5 frontend contracts passed; TypeScript, changed-file ESLint, Python compile, and `git diff --check` passed. All-five dashboard mapping and disabled-trigger no-allocation probes passed. Ruff's sole F821 is the unchanged pre-existing `_refresh_tasks_lock` finding in `backend/web/app.py:5728`; broader default Ruff output contains additional legacy style debt.
- Residual UX limitation: disabled dashboard sections still render active Run Analysis buttons, and `TriggerButton` labels every HTTP 409 as `Already running`, so a globally disabled click is misleading even though the backend correctly blocks it. This is secondary to the reload blocker.

### 2026-09-23 — Global Monitoring Agent final re-review rejected
- **REJECT.** Livingston's live-reload and disabled-control repairs pass: false→missing/malformed/invalid-member/true replacement works; a valid false survives unrelated updates; cron, master enabled, timezone, and unrelated scheduler keys remain intact; all five scheduled/full/direct gates behave correctly; disabled direct requests allocate neither slot nor run; dashboard controls are natively/ARIA disabled and disabled versus duplicate 409 states are distinct.
- Release blocker: startup is still not replacement-normalized. `setup()` feeds YAML `scheduler.agents` into `CosmosDBService.merge_defaults()`, so a missing persisted agents block is re-seeded from YAML. An independently reproduced stale YAML `buy_tracker: false` therefore survives a missing Cosmos block instead of defaulting to true.
- Release blocker: `/api/dashboard/status` emits the gate map only when an in-process scheduler exists. In supported web-only mode, changing Cosmos gates leaves `monitor_agent_enabled` as `{}`, so AutoRefresh cannot observe or refresh for the change.
- Quality blocker: authoritative test-file Ruff fails `I001` on `backend/tests/test_monitor_agent_global_settings.py`. The separate app F821 remains pre-existing and reproduces against `HEAD`.
- Validation: 61 focused backend tests and 6 frontend contracts passed; all-five independent scheduler/full/direct/dashboard probes passed; TypeScript, changed-file ESLint, Python compile, and diff check passed.

### 2026-09-23 — Global Monitoring Agent third-revision final gate approved
- **APPROVE.** Startup now removes YAML `scheduler.agents` from Cosmos defaults and replacement-normalizes the effective persisted snapshot. With stale YAML false, missing or malformed persisted gates become all true, while explicit persisted false survives; live reload follows the same rules. Cron, master enabled, timezone, reload interval, and unrelated scheduler/default fields remain intact.
- All five members independently pass scheduled and full-analysis skipping, direct-trigger 409 gating before slot/run allocation, and dashboard section/row mapping without enrollment mutation. The unified Settings Run Now path invokes the same gated scheduler callback; master monitoring enablement remains separate. Settings expose, load, save, and live-apply all five booleans.
- Scheduler-backed and web-only dashboard rendering/status share the scheduler→Cosmos→YAML resolver. Persisted web-only gate changes alter the status signature, and AutoRefresh records the new signature before refreshing, avoiding a refresh loop. Disabled sections and controls are dimmed/native-disabled and expose accessible `Deactivated globally`.
- Validation: 74 focused backend tests and 6 frontend contracts passed; independent all-five scheduled/full/direct/dashboard probes passed; TypeScript, changed-file ESLint, Python compile, changed-test Ruff, and `git diff --check` passed. The sole app Ruff F821 at `_refresh_tasks_lock` reproduces unchanged against `HEAD`.
- Residual limitation: execution and dashboard run/slot state remain process-local, as previously documented; no blocker for the reviewed member-gate contract.

### 2026-09-24 — Dividend filter review
- **REJECT.** The shared authoritative-metadata predicate correctly gives `BUY` dividend share-acquisition legs dual Buy/Dividend membership and keeps ordinary, rights-issue, and metadata-free buys out of Dividend. Both claimed surfaces avoid the exact `txn_type=DIVIDEND` API filter and fetch account/security/date candidates before semantic filtering.
- Release blocker: `StockTransactionsTable` has no request-generation or abort guard. A slow multi-page Dividend load can finish after a later Buy, Sell, or time-filter load and overwrite its rows/count, leaving results inconsistent with the selected filters.
- Release blocker: Dividend batching is offset-based over backend ordering by `trade_date` only. Equal-date rows have no deterministic tie-breaker, so separate page requests can overlap or skip rows. Stock history does not dedupe; global Movements dedupes overlaps but can still terminate on raw count with a unique row missing. Complete pagination and no-duplicate/count guarantees are therefore not met.
- Validation: 284 focused movement-filter/search/time tests passed, as did TypeScript, changed-file ESLint, and `git diff --check`. The new tests primarily validate the pure predicate and source wiring, not asynchronous component pagination/race behavior.

### 2026-09-24 — Guided historical-rights migration release gate
- **REJECT.** Concurrent operators are not serialized by a case-journal ETag/CAS. An adversarial two-apply race reproduced a superseded source with both deterministic replacements deleted: one operator treated the other operator's exact documents as compensatable, while journals diverged through `FAILED_ROLLED_BACK` and `ATTENTION_REQUIRED`.
- Mixed outcome C cannot preserve a genuine cash dividend: its guided plan and builder emit only `SHARE_ACQUISITION` and `RIGHTS_SOLD`. Production holdings confirmed zero dividend income for the mixed preview.
- Discovery drops same-account/security alternatives beyond 62 days instead of retaining them with an advisory distance warning.
- Production targeting has no explicit endpoint argument; the factory silently selects `COSMOSDB_ENDPOINT`/`COSMOSDB_KEY` from process environment, so acknowledgement does not bind the endpoint and environment labels do not prevent test/production target confusion.
- Migrated and superseded ledger shapes are incompatible with strict backup projection. Representative replacements fail on migration provenance, replacement links, FMV/top-up, and basis fields; supersession/rollback provenance fields are likewise absent from the ledger allowlist.
- Saved-preview confirmation is not bound to the saved outer plan: outcome, operator, and rationale can disagree while the service preview remains valid. The service model also accepts cross-account/cross-security sources and replacements.
- Positive controls passed: one-case CLI surface, TTY/literal gates, canonical preview content, PREPARED-before-ledger ordering, deterministic IDs, pre-supersession compensation, partial-supersession attention state, resume/rollback happy paths, actual A/B/C holdings behavior, Python compile, Ruff, CLI help, and diff hygiene.
- Validation: 39 focused migration tests and 375 FIFO/corporate-action/correction/dividend/backup regressions passed (4 existing warnings). Independent probes reproduced every blocker; no Cosmos or production access was used.

### 2026-09-24 — Dividend/Buy movement filter final gate
- **APPROVE.** Abortable request generations and unmount invalidation guard every post-await rows, count, loading, and error commit in both movement consumers. Deterministic delayed-response probes confirmed an older Dividend request cannot overwrite a newer filter or commit after cancellation.
- `get_movements()` applies one total `(trade_date DESC, id DESC)` order before slicing for every account, security, type, option-position, and date-filter combination. Ledger IDs are mandatory strings and are generated from account-qualified deterministic import IDs or UUID movement IDs.
- Repeated adversarial pagination over 137 same-date rows with shuffled container iteration returned all 137 rows exactly once in identical order across five runs. Defensive batching stops only on a short raw page; overlap is ID-deduped without using reported or deduped counts as an early-stop condition, and displayed client-filter counts use the final unique membership set.
- Shared membership preserves dividend-derived `BUY` share acquisitions in both Buy and Dividend while excluding ordinary, rights-issue, and metadata-free buys from Dividend. Global Movements supports account/type/date/symbol combinations; Stock Transactions supports All/Buy/Sell/Dividend with all time filters.
- Validation passed: 137 focused backend tests, 325 focused frontend tests, TypeScript, changed-file ESLint, Python compile, changed-test Ruff, repeated equal-date/race/overlap probes, and `git diff --check`. The application file retains the same 187 pre-existing Ruff findings as `HEAD`; the reviewed change adds none.

### 2026-09-24 — Historical rights migration final adversarial re-review
- **REJECT.** Lease/fencing remains vulnerable to a check-then-write race. A stale apply holder passed `assert_operation`, a replacement holder claimed the expired case head, and the stale holder then performed the actual replacement ledger create before losing the journal CAS. There is no lease renewal, `assert_operation` does not reject an expired lease, and repository mutation methods do not receive or atomically enforce the fencing token.
- Compensation is not fenced per mutation. `_compensate_before_supersession` checks ownership once before its delete loop; a synchronized takeover then allowed the stale compensator to delete the replacement while the new holder was resuming, leaving the new holder failed and the durable case stuck `APPLYING`.
- Strict validation is incomplete. A directly reconstructed canonical `RightsMigrationPreview` accepted `quantity="-7"`, `gross.eur_amount="NaN"`, and `net.eur_amount="Infinity"`. The backup journal projector also accepted an unknown nested field inside `preview`, so journal schema evolution does not fail closed.
- Unbounded discovery is quadratic and uncapped: 400 same-account/security rights rows generated 159,600 retained alternatives. Neither bounded nor unbounded mode caps inventory, cases, alternatives, memory, or work, so a large export can exhaust operator memory; alternatives remained advisory and never auto-linked.
- Claimed fixes otherwise held in reviewed paths: mixed C emits at most one cash-dividend leg with exact dates and no duplicate migration invariant income; explicit endpoint/database/portfolio/journal target identity is preview-hashed and revalidated; no endpoint/default write target comes from environment; one-case/TTY/literal confirmation gates remain; outer journal and ledger unknown fields fail closed; secrets are scanned; migration journal/ledger closure is bidirectional.
- Validation: 478 focused migration, backup, FIFO, corporate-action, correction, dividend, and holdings tests passed with 4 existing warnings. Authored migration Ruff, Python compile, CLI help, and `git diff --check` passed. Whole changed-file Ruff reported six findings that reproduce on the corresponding `HEAD` files.

### 2026-09-24 — Historical rights migration third adversarial gate
- **REJECT.** Ledger mutations are now genuinely account-partition transactional batches: the lease ETag replace and one create/replace/delete share `/account_id`, with two operations per batch. Stale ledger writers and stale compensation therefore lose atomically after takeover. The journal remains a separate-container gap, however: apply releases the account lease before appending `VERIFIED`. A deterministic takeover in `release_operation` left `new-owner` holding the higher fence while the stale operation successfully appended `VERIFIED`. Verification reads and journal transitions are not transactionally or freshly bound to the account lease.
- Canonical numeric validation still accepts forbidden values. Direct preview reconstruction accepted replacement `quantity=None`, `quantity=""`, and `quantity="-0"` (persisted as `-0.000000`), plus `operator_plan.source_withholding_eur=None`. This contradicts the claimed rejection of null/blank/negative-zero financial leaves and allows hashes to bind representations with ambiguous persisted meaning.
- Backup schema v1 is not recursively exact. `verification_results.invariants.future_metric` exported successfully because invariant maps are unrestricted. Conversely, a legitimate production-shaped source in `preview.operator_plan.before_documents` with `withholding={"source": null, "destination": null}` was rejected as `invalid_nested_schema`; the nested allowlist also omits production withholding fields such as derived `rate_pct`. Strictness therefore both misses unknown nested data and falsely rejects valid ledger shapes.
- Discovery scaling held: 10,000 synthetic rows produced a deterministic unique 5,000-case hard-capped page in 0.433 seconds, at most 11 comparisons per case, with explicit truncation; a 5,001-case request failed closed.
- Validation passed: 70 authoritative migration tests; 405 explicit FIFO/holdings/correction/CA/dividend/backup regressions with 4 existing warnings; 129 affected frontend movement tests; Python compilation; migration Ruff; CLI help; TypeScript; and `git diff --check`. A broader keyword run encountered the environment's missing async pytest plugin, so the explicit production-relevant file set was used instead. No Cosmos or production access was performed.

### 2026-09-24 — Reconstructed historical-rights artifact gate
- **REJECT.** The reconstructed module is NUL-free and imports/compiles, and all 109 focused migration/backup tests pass, but two substantive safety gaps remain.
- `CosmosRightsMigrationRepository.append_terminal_revision()` checks the portfolio lease and then independently writes the journal container. A deterministic takeover inserted between those calls produced durable `VERIFIED` for stale fence 1 while `winner` held fence 2. The concurrency tests mask this cross-container gap by wrapping the in-memory terminal transition and lease acquisition in one `mutation_lock`, which Cosmos cannot share across containers.
- Recursive financial validation omits production withholding leaves named `amount_eur` (and `rate_pct`). A reconstructed canonical preview with replacement `withholding.source.amount_eur="NaN"` was accepted and assigned a fresh document/preview hash, so strict numeric validation and backup fail-closed claims do not hold.
- Validation: 109 focused tests passed; 8 scoped files were NUL-free; four migration modules compiled; CLI help passed. `git diff --check` failed only on unrelated `.squad/routing.md` trailing whitespace. Ruff's normal cache retained NUL-corrupt package metadata; `--no-cache` completed and reported nine migration-file findings.

### 2026-09-24 — Livingston historical-rights revision re-review
- **REJECT.** The lease CAS seal now closes the stale terminal-write race: takeover before the seal invalidates its ETag, and takeover after the seal is blocked. Recursive financial validation also rejects the reviewed nested NaN/Infinity/exponent/negative-zero/precision/overflow cases while preserving `country` and `rate_source` strings.
- A crash or journal outage after `_seal_terminal_commit()` but before the terminal journal CAS permanently strands the case once the lease expires. An injected terminal journal failure left `terminal_commit` on the portfolio lease and journal state `APPLYING`; both a higher-fence acquirer and an owner retry were rejected as `pending terminal journal commit`, while the expired original owner could no longer seal or persist the terminal revision. `acquire_operation()` only clears a seal when the bound terminal journal revision already exists, so there is no takeover, resume, or recovery path for an unreflected seal.
- Validation: 105 focused migration/CLI/backup tests passed; four migration modules compiled; CLI help passed; 8 artifact files were NUL-free; artifact-specific whitespace check passed. Whole-tree `git diff --check` remains red only for unrelated `.squad/routing.md` trailing whitespace. No production access occurred.

### 2026-09-24 — Danny second revision final rights-migration gate
- **REJECT.** Durable recovery now closes the prior seal-to-journal deadlock for both apply and rollback, but terminal-journal tamper detection is not exact.
- `_terminal_revision_matches()` at `backend/src/portfolio/rights_migration.py:1218-1232` checks only that every sealed payload key has the expected value. It does not reject additional fields in the durable terminal journal revision. `recover_terminal_revision()` treats that subset match as authoritative at `:1170-1178`, clears the seal, and returns the altered revision.
- A deterministic crash-after-journal-CAS probe added `unexpected_terminal_data="tampered"` to the durable `VERIFIED` journal document. Recovery accepted it and removed `terminal_commit`, permanently reconciling a journal revision that was not the exact sealed intent.
- Validation: 122 focused migration/CLI/backup tests passed; four migration modules compiled/imported; top-level and apply CLI help passed; 8 artifact files were NUL-free; artifact-specific whitespace checks passed. No production access occurred.

### 2026-09-24 — Rusty exact terminal-intent recovery final gate
- **APPROVE.** The durable seal now binds the complete canonical terminal journal application document and hash. Reconciliation removes only the five enumerated top-level Cosmos-managed fields, then requires recursive equality; injected, missing, changed, and nested-tampered application data reject without clearing the seal.
- Initial terminal persistence, exact owner retry, and crash recovery reuse the sealed document, preserving timestamp, history, payload, apply/rollback identity, target/hash binding, and operation/fence intent. Seal clearing occurs only after verified persistence; repeated recovery is idempotent.
- Previously approved fencing, transactional ledger mutation, stale-writer/compensator rejection, bounded discovery, strict financial validation, confirmation, compensation, and backup round-trip behavior remain covered. Production code uses no shared-process lock and documentation makes no cross-container atomicity claim.
- Validation: 126 focused migration, CLI, backup, and archive-format tests passed plus 4 independent exact-tamper probes. Four modules compiled/imported; top-level and apply CLI help passed; 8 artifacts were NUL-free; artifact-specific diff/whitespace checks passed. No production access occurred.

### 2026-09-24 — Historical-rights migration removal review
- **REJECT.** Product-scope searches found zero migration references and zero migration-named files. Shared backup/archive/collector/dependency/export/import/model/schema paths are byte-identical to `HEAD`; 111 targeted backup/import/archive/dependency/portfolio tests and the complete 75-test backup suite passed. Fourteen affected modules compiled and eight imported.
- The unrelated deterministic movement pagination diff remains in `cosmos_portfolio.py`; duplicate-position work remains present; ten non-shared Dashboard Banner paths are unchanged from `5dcee274`. No broad reset was observed.
- Release blocker: repository scope still contains 524 `.pyc` files across seven `__pycache__` directories, plus four pytest/Ruff cache directories. The explicit no-generated-cache-artifacts requirement is therefore unmet.
- Residual hygiene: `git diff --check` still reports the two pre-existing trailing-space lines in `.squad/routing.md`; no migration product residual caused that result. No production access, commit, push, or deployment occurred.

### 2026-09-24 — Dashboard banner Last Run review
- **REJECT.** The manual banner endpoint now correctly queues `banner_agent` through `TaskRegistry`, persisted `dashboard_banner.generated_at` supplies restart continuity, settings/dashboard reads bypass fetch caching, and AutoRefresh includes the persisted banner revision.
- Failed banner generation is still reported as a successful completion. `_run_banner_agent_async()` catches and suppresses generation exceptions, while `TaskRegistry` also catches task exceptions and unconditionally advances `task.last_run` after error or timeout. Settings prefers that runtime timestamp over the last persisted successful `generated_at`, and the frontend interprets any changed timestamp as `✅ Completed`.
- The frontend refresh behavior was also applied to every scheduler Run Now control, not just the banner: all ten task keys now poll settings every two seconds for up to five minutes. This changes other agent controls and can add up to 150 requests per accepted run, contrary to the scoped requirement.
- Validation passed: 17 focused backend tests, 4 frontend contract tests, TypeScript, changed-file ESLint, focused Python compilation, and focused `git diff --check`. Independent probes confirmed both swallowed banner failures and failed registry jobs receiving a new `last_run`. `azure.core` 1.39.0 and `azure.cosmos` 4.16.1 import successfully; both `pytest` and `python3 -m pytest` pass the focused suite, so the noted missing-dependency failure is not reproducible and is unrelated environment state, not this diff.

### 2026-09-24 — Dashboard Banner last-run revision re-review
- **REJECT.** Banner generation failures now propagate, failed attempts retain the prior successful `last_run`, successful manual runs complete through `TaskRegistry`, Settings consumes the single completion response, Agents HQ observes persisted `generated_at` on its existing bounded cadence, and restart fallback/`Never` semantics are correct.
- Release blocker: the new completion tracking is applied to every `TaskRegistry.trigger_task_now()` call, but only the banner endpoint calls `wait_for_run()`, which is the sole cleanup path. Best Options startup/manual runs and the generic scheduler Run Now route therefore retain every completed `TaskRun` in `_runs` for the process lifetime. An independent 25-run non-banner probe left all 25 completed records resident. This changes other scheduler controls and creates unbounded memory growth, violating the scoped/unchanged-registry requirement.
- Validation passed: 42 focused backend tests, 11 frontend contracts, TypeScript, scoped ESLint, Python compilation, and scoped `git diff --check`. The initial `python` command failure was independently reassessed as an environment alias issue; `python3` completed all backend validation.

### 2026-09-24 — TaskRegistry lifecycle final gate
- **REJECT.** Fire-and-forget triggers are allocation-free; retained results are bounded, lock-safe, waiter-safe, late-readable until capacity eviction, and preserve active/waited records. Banner failures propagate without advancing its prior success, successful manual runs update Settings from one response, Agents HQ observes persisted `generated_at`, and restart/`Never` behavior is correct.
- Release blocker: the registry revision changes `last_run` from “execution attempt start, including failure/timeout” to “successful completion only” for every registered task. The unified scheduler API and all Settings task cards expose this shared field, while the new `last_attempt`/`last_error` fields are not rendered. An independent unrelated-task failure probe therefore left the public Last Run stale, unlike `HEAD`. This violates the required compatibility of unrelated scheduler controls/startup tasks; the semantic change must be banner-scoped or separately migrated.
- Validation passed: 64 focused backend tests, 11 frontend contracts, 40-run/16-capacity and 24-waiter stress probes, TypeScript, scoped ESLint, Python compilation, and scoped diff checks. The standalone banner-agent suite hit its pre-existing incomplete `azure` test stub and was not used as a product failure.

### 2026-09-24 — Historical-rights run-guided review
- **REJECT.** The deterministic ordering, inclusive date matching, per-case prompts, service-journal crash recovery, atomic file replacement, target/filter/bundle binding, legacy commands, and focused regression suites are substantially present, but four safety gaps remain.
- Guided state has no semantic transition validation. A canonically rehashed state with `status="applied"` and `phase="unstarted"` plus no outcome, preview, or artifact loads successfully; `run_guided()` then skips the case without review or confirmation. The public canonical checksum therefore does not prevent a changed state from causing case omission.
- Explicit blank `--account` or `--symbol` values are silently discarded and become an unfiltered full-bundle run instead of failing closed.
- Credential redaction compares exact leaf names only. Independent discovery probes persisted `COSMOSDB_KEY`, `api_key`, and `access_token` values into the discovery bundle, which can then be duplicated into guided preview artifacts.
- Failed cases are counted as `completed` and not `pending`, so the summary can claim completion while unresolved failures remain.
- Validation: 115 focused CLI/service tests and 139 comprehensive CLI/service/backup/archive tests passed. Four migration modules compiled/imported; root and `run-guided` help, seven-file NUL scan, and scoped diff checks passed. Independent probes reproduced state-forged omission, blank-filter broadening, credential persistence, impossible state acceptance, and failed/completed counter drift. Whole-tree diff remains red only for unrelated pre-existing `.squad/routing.md` trailing whitespace. No production access occurred.

### 2026-09-24 — Final pending-diff integration gate
- **APPROVE.** The combined Dashboard monitor identity and movement pagination/filter diff has no high-confidence functional blocker.
- Backend pagination applies deterministic descending `trade_date`/`id` ordering before offset slicing. Frontend batch loading uses abortable latest-request ownership, short-page exhaustion, ID deduplication, complete client-side Dividend membership, and coherent symbol/account/type/date pagination behavior.
- Duplicate-contract positions remain isolated by `position_id`; ambiguous, stale, or conflicting legacy activity is not borrowed by another position. Historical-rights migration references are absent outside append-only `.squad` history.
- Validation passed: 200 focused backend tests, 133 focused frontend Node tests, TypeScript, changed-file ESLint, new-test Ruff, Python compilation, product diff whitespace checks, and the production frontend build. The broader frontend suite passed 1,338/1,340 tests; both failures are in untouched source-contract areas.
- Non-blocking residuals: two trailing-whitespace lines in `.squad/routing.md`; baseline Ruff findings in the two modified legacy Python modules; one existing generated-CSS warning during the successful build; two unrelated frontend source-contract failures (`economicsParity` PP-6 and `movementDetailDefensiveGuards` optional-chaining expectation).
- No commit, push, deployment, or production access occurred.

### 2026-09-25 — Manual position-agent execution review rejected
- **REJECT.** The row-to-run identity path correctly carries and reloads `position_id`, isolates reversed same-symbol/account/paper positions, scopes trigger locks by position, preserves scheduled all-position iteration, and persists authoritative identity.
- Release blocker: explicit blank identity constraints are silently discarded. Independent endpoint probes returned HTTP 200 and launched the run for `account_id=""`, `contract_id=""`, `expiration=""`, and `option_type=""` with an explicit `position_id`; the accepted invariant requires every supplied identity constraint to agree or fail closed.
- Quality blocker: the changed monitor wrappers add four Ruff `RUF013` violations for implicit optional `position_id` and `position_constraints` annotations.
- Validation otherwise passed: 79 backend tests, 50 frontend Node tests, two adversarial probes, TypeScript, changed-file ESLint, Python compilation, new-file Ruff, diff whitespace, and production frontend build. No commit, push, deployment, or production access occurred.
### 2026-09-25 — Manual position execution re-review remains rejected
- Rusty's presence-model revision fixed the behavioral blockers: 176 focused
  backend tests, 45 frontend tests, and 141 independent adversarial assertions
  passed, including no-launch invalid requests and exact reverse-order
  same-contract/account/paper-lane selection.
- Final lint gate still fails: the revision adds four `RUF013` findings in
  `backend/web/app.py` for the new `position_id`/`position_constraints`
  parameters, increasing that file from 8 baseline findings to 12. The new
  focused test also reports Ruff `I001`.
- Verdict remains REJECT; no source/tests were modified.

### 2026-09-25 — Manual position execution final lint gate approved
- **APPROVE.** Danny's annotation-only revision removed all four new
  `RUF013` findings, restoring `backend/web/app.py` to its exact baseline of
  8, without Ruff suppressions or behavioral changes.
- The focused test file passes full scoped Ruff and `I001`; 76/76 focused
  manual-position tests passed, including invalid blank/null constraints,
  exact same-symbol selection, position locks, and persistence.
- Python compilation and diff whitespace checks passed. Residuals are 8
  pre-existing app `RUF013` findings and 3 pre-existing/deprecation warnings.
- No source/tests were modified; no commit, push, deployment, or production
  access occurred.

### 2026-09-25 — Dashboard Banner recommendation eligibility final gate
- **REJECT.** Exact default-only summary/MA `NEUTRAL` now correctly produces
  zero facts/count/watermark, skips the LLM, and persists deterministic
  no-data.
- Release blocker: `_has_finite_indicator()` treats any finite pseudo-indicator
  with a positive `period`/`length` as recognized evidence. A full-flow
  `provider_default={value:0,period:20}` probe made an unsupported MA `BUY`
  invoke the LLM, count a market snapshot, publish a watermark, and persist
  generated commentary.
- Validation: 73 focused backend tests passed with the same 3 pre-existing
  provider fixture failures; 10 frontend contracts passed; independent probes
  were 1 passed/1 failed. Banner Ruff, TypeScript, changed-file ESLint, Python
  compilation, and diff whitespace passed. No source/tests were modified.

### 2026-09-25 — Dashboard Banner Agent removal review
- **REJECT.** Active source/config/docs cleanup is otherwise complete: exactly
  the banner scheduler registration was removed, seven feature-exclusive
  tracked files are deleted, and only three compatibility/removal tests plus
  append-only `.squad` history retain identifiers.
- Release blocker: `backend/src/scheduler_registry.py` still imports the
  banner-only `dataclasses.field`; scoped Ruff reports `F401`.
- Release blocker: eleven feature-exclusive compiled artifacts remain under
  backend `__pycache__` directories for the deleted agent, instructions, and
  focused tests. Pytest/Ruff cache indexes also retain removed identifiers.
- Validation passed 206 focused backend tests, 12 focused frontend tests,
  TypeScript, scoped ESLint, Python compilation, production build, diff
  whitespace, registration comparison, and exact active-source searches.
  Broader suites reported 4,263 backend passes with 20 unrelated yfinance
  event-loop failures and 1,335 frontend passes with two unrelated movement
  contract failures. No implementation/tests/docs were modified.

### 2026-09-25 — Dashboard Banner Agent removal final review
- **REJECT.** Danny correctly removed the unused `dataclasses.field` import;
  scoped F401 is clean, and scheduler-registry Ruff improved from the known
  25-finding HEAD baseline to 15 pre-existing findings.
- Banner-specific bytecode count is zero, active product references are zero,
  and only three allowed compatibility/removal test references remain.
- Final blocker: `backend/.pytest_cache/v/cache/lastfailed` and `nodeids`
  retain 85 matching lines / 88 occurrences of removed banner identifiers.
  They predated this review and were not reviewer-created, so they were not
  deleted.
- Seven tracked deletions remain exactly the intended feature-exclusive files;
  diff whitespace passes. No tests were rerun for the import/cache-only cleanup,
  and no source/tests/docs, commit, push, deployment, or production change was
  performed by the reviewer.

### 2026-09-25 — Dashboard Banner removal post-cache final review
- **REJECT.** Livingston cleared the two stale pytest indexes; pytest-cache
  identifiers are now zero.
- Final blockers remain in generated artifacts: two Ruff cache files retain
  four identifier occurrences, and four compatibility-test `.pyc` files
  retain four occurrences. Banner-named `.pyc` files remain zero.
- Active runtime/config/API/UI/docs references remain zero; only the exact
  three compatibility/removal test files and append-only `.squad` records
  retain identifiers. Five broader production `banner` matches are unrelated
  Best Options/Economics terminology.
- The deletion set remains exactly seven intended tracked feature files with
  zero unexpected deletions. Current status is 27 modified, 7 deleted, and 1
  untracked; `git diff --check` passes and the known import/Ruff baseline is
  unchanged. No tests, imports, or linters were run, and no source/tests/docs,
  commit, push, deployment, or production change was performed.

### 2026-09-25 — Dashboard Banner removal final approval
- **APPROVE.** Generated cache identifiers are zero; only four identifier lines
  remain across the three intentional compatibility/removal tests.
- Active runtime, configuration, API, UI, and documentation references are
  zero. The seven tracked deletions are exactly the intended feature files,
  with no unexpected deletion, secret, or generated artifact.
- `git diff --check` passes and the one untracked file is the expected frontend
  removal contract. No tests, imports, linters, builds, deployment, production
  access, or production-data operation was performed during the final
  read-only review.

### 2026-09-25 — Simulate a Roll end-to-end review
- **REJECT.** One strict exact-contract blocker remains. The simulator parses
  target strikes with `Decimal`, but converts them to `float` before the shared
  contract lookup. Targets `100.00000000000000000001` and
  `99.99999999999999999999` both collapsed to `100.0`, matched the existing
  current contract, bypassed `same_contract`, and returned successful
  `Estimated even` simulations instead of `target_contract_not_found` or
  `same_contract`.
- The reviewed path otherwise preserves exact `position_id`, active CALL/PUT
  gating, full positive-integer quantity, multiplier 100 behind the existing
  US-options guard, robust two-sided midpoint-only pricing, quote provenance,
  explicit UI states, informational/no-commission disclosure, and no
  persistence mutation.
- Validation: 89 focused backend tests and 107 focused frontend/regression
  tests passed; 38 independent formula, quote, quantity, identity, date,
  provenance, and mutation probes passed before the two precision probes
  exposed the blocker. TypeScript, scoped ESLint, Python compilation,
  production build, and diff whitespace passed. Ruff reported 435 findings
  across the two changed legacy Python modules versus 437 on `HEAD`, with the
  new test clean; the changed endpoint region still contains one `BLE001`
  finding. The build retained one pre-existing malformed generated CSS utility
  warning. No implementation/tests, commit, push, deployment, production
  access, or production data were modified.

### 2026-09-26 — Simulate a Roll strike-precision final review
- **APPROVE.** Saul's revision preserves canonical base-10 `Decimal` strike
  identity from raw JSON parsing through same-contract comparison, exact chain
  lookup, response serialization, frontend text state, and raw BFF forwarding.
- Independently confirmed `100`/`100.0`/`100.000` equality and rejection,
  distinct exact lookup/response identity for 20-place strikes on either side
  of 100, exact misses without collapse, and fail-closed booleans, null, signs,
  whitespace, exponent, nonfinite, overflow, and excess-precision requests.
- Midpoint-only formula, 100-share and full-position scaling, any-expiry policy,
  quote integrity/provenance, exact active position and CALL/PUT identity, UI
  states/disclaimer, and no-mutation behavior remain correct.
- Validation passed 288 backend tests, 165 focused frontend tests, and 64
  independent probes. The full frontend suite passed 1,340 with the same two
  unrelated source-contract failures. TypeScript, scoped ESLint, Python
  compilation, production build, and diff hygiene passed. Scoped Ruff improved
  from 441 findings on `HEAD` to 439; the new test and changed endpoint region
  are clean. Residuals are the two unrelated frontend failures, legacy baseline
  Ruff findings, and one existing generated-CSS warning. No source/tests,
  commit, push, deployment, or production access was performed.

### 2026-09-26 — Roll simulation midpoint/cache fix review
- **REJECT.** The production cache JSON string and ordinary provider wrapper
  now decode correctly, and exact Decimal lookup, quote integrity, stale/carried
  warnings, frontend error headings, position identity, and no-mutation
  behavior remain correct.
- Three normalization blockers remain: empty or malformed `calls`/`puts`
  payloads are misreported as `current_contract_not_found`; generic Mapping
  payloads are rejected; and outer wrapper status is discarded, allowing an
  explicit error-status wrapper with a valid inner chain to be priced.
- Validation: 279 backend tests and 5 focused frontend tests passed; the full
  frontend suite remained 1,340/1,342 with two unrelated failures. Independent
  probes passed 31/36 and exposed the five failing shapes covered by the three
  blockers. TypeScript, scoped ESLint, Python compilation, production build,
  and diff hygiene passed. Ruff retained legacy findings plus one changed-test
  import-order finding. No implementation/tests, commit, push, deployment, or
  production access was performed.

### 2026-09-26 — Roll simulation chain normalization final review
- **APPROVE.** Livingston cleared all three prior blockers: generic nested
  Mapping inputs normalize without mutation, structural empty/malformed
  required sides fail as `chain_unavailable`, and explicit wrapper status is
  validated before embedded payload extraction.
- Revalidated serialized JSON/bytes/dict/Mapping inputs, accepted and rejected
  statuses, retained-state warnings, matching one-sided chains, exact
  current/target errors, two-sided midpoint integrity, Decimal strike/expiry/
  type identity, formula/scaling, frontend visibility, and no mutation.
- Validation passed 925 comprehensive backend roll/chain tests, 140 focused
  backend tests, 5 frontend contracts, and 24 independent probes. TypeScript,
  scoped ESLint, Python compilation, critical scoped Ruff, clean production
  build, and diff hygiene passed.
- Residuals are one existing generated-CSS warning, 440 broad legacy Ruff
  findings, and one unrelated pre-existing `web/app.py:6024` F821 when the
  whole legacy endpoint module is included. Blockers: zero. No source/tests,
  commit, push, deployment, or production access was performed.

### 2026-09-26 — Roll contract quantity review
- **REJECT.** Quantity parsing, explicit remaining-count precedence, exact
  position isolation, scaling, API/UI provenance, and new manual/paper writes
  are correct, but three release blockers remain.
- The supposed historical fallback has no schema/version or time boundary, so
  current corrupt quantity-less positions are silently treated as one
  contract. Legacy rolls also create another quantity-less active position
  instead of persisting the resolved one-contract count.
- Roll Scenarios lacks the simulator's active-position gate and returned
  successful scenarios for a closed position.
- Validation passed 343 focused backend tests, 6 focused frontend contracts,
  1,341/1,343 full frontend contracts, type/lint/compile/build/diff checks, and
  25 adversarial probes with 7 unsafe outcomes. The two frontend failures,
  deprecation/subprocess warnings, and generated-CSS warning are unrelated.
  No implementation/tests, commit, push, deployment, or production access was
  performed.

### 2026-09-26 — Rights-removal revision final review
- **REJECT.** Backend inertness, strict sales quantities, backup boundaries,
  ordinary trades/cash dividends, and Dividend · Buy passed independent
  probes, but the frontend shared normalizer only trims and uppercases marker
  values. It allowed accented and punctuation/whitespace-equivalent rights
  markers (`dérêchos`, `rights.sold`, `rights sold`, `rights-sold`).
- The Total Dividends card still falls back from canonical `total_net_eur` to
  legacy `cash_net`, and orphan `ACCIONES_ZERO_QUANTITY` /
  `INVALID_SALES_TYPE` warning types and four label maps remain without a
  backend runtime producer.
- Full validation: backend 4428 passed, 5 skipped, 12 failed, 16 errors
  (unrelated best-options/yfinance fixture failures); frontend 1335/1337 with
  the same two unrelated failures. TypeScript, scoped ESLint/Ruff, compile,
  production build, and diff hygiene passed. Frontend adversarial rights
  probes passed 5/9 and failed the four equivalent-marker cases.

### 2026-09-26 — Rights-removal final gate approved
- **APPROVE.** Saul closed the normalization, canonical Total Dividends, and
  orphan-warning blockers. NFKD/accent/case/punctuation/separator/whitespace
  and nested variants now fail closed without rejecting ordinary descriptions,
  company names, trades, cash dividends, or Dividend · Buy.
- Confirmed one Total Dividends card sourced only from finite canonical
  `total_net_eur`, zero obsolete warning references, no rights creation/import/
  correction/corporate-action/backup acceptance, inert legacy records, strict
  positive finite sales quantity, and preserved filters/pagination/FIFO.
- Validation passed 973 focused backend tests, 1339/1341 full frontend
  contracts with the same two unrelated failures, 328/329 focused frontend
  contracts with the unrelated paper-action failure, and 81 independent
  adversarial probes. TypeScript, scoped ESLint/Ruff, compile, production
  build, conflict/diff checks passed; existing lint debt remains unrelated.

### 2026-09-26 — Dividends summary card layout approved
- **APPROVE.** The seven pre-change displayed values are preserved: canonical
  Total Dividends plus six secondary metrics. Gross, withholding, and effective
  withholding now form a flat semantic definition list in the primary card;
  count, last-12-month average, and portfolio YoC remain the only three sibling
  cards in their original order.
- Confirmed finite canonical fields, correct unavailable/currency/percentage
  rendering, no `cash_net` fallback, no new frontend recomputation, no nested
  cards, responsive stretched grids, complete filter-driven refresh, and no
  rights semantics.
- Validation passed 23 focused frontend tests, an independent structural probe,
  TypeScript, changed-test ESLint, production build, and diff hygiene. The
  component's three ESLint errors reproduce on HEAD, and the generated-CSS
  build warning is existing. Blockers: zero. No source/tests, commit, push,
  deployment, or production access was performed.
