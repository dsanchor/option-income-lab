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
