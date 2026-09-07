# Danny — Reviewer Gate: PEP Security Identity Repair (targeted)

## Verdict: REJECTED (single test-file defect; product code is sound)

## Files inspected

- `backend/scripts/repair_pep_security_id.py` (Livingston)
- `backend/tests/test_repair_pep_security_id.py` (Basher)
- `.squad/decisions/inbox/danny-pep-security-id-repair-contract.md`

## Test run: 41/42 passed

`test_pep12_backup_created_before_first_write` (PEP-12a) fails.

## Independent determination: test defect, not product defect

Read the failing test directly. It defines a helper closure
`_backup_then_apply()` that calls `backup_repair(...)` then sets
`backup_completed[0] = True` before calling `apply_repair(...)` — but
**that helper is never invoked**. The test body instead calls
`apply_repair(...)` directly, so `backup_completed[0]` is never set to
`True` by any code path that actually runs. The injected
`_tracking_create` wrapper on `syms.create_item` therefore asserts
`backup_completed[0]` — permanently `False` — on the very first
`create_item` call regardless of whether the product code backs up
first. This is dead test scaffolding, not a real invariant check.

Verified independently against the actual implementation
(`run_apply`, `repair_pep_security_id.py` lines 780-842): Phase 2
(`build_backup(disc)` → `write_backup(backup, backup_dir)`) executes and
logs completion *before* Phase 3a's first `symbols_container.create_item(
new_target)` call — the only write in the entire apply pipeline that
precedes it is the backup itself. **Livingston's claim is confirmed
correct**: PEP-12a is a Basher test-file bug (an orphaned/never-called
closure), not a product regression.

## Broader safety review (product code, all confirmed sound)

- **Dry-run**: `--audit` path only calls `discover()`/report-building
  functions; no container mutation method is reachable from `run_audit`.
- **Backup completeness/ordering**: confirmed above; `build_backup`
  captures every doc from `_Discovery` (config, source, target-if-found,
  all ledger_txns, all import_session refs).
- **Checksum**: `write_backup` computes and stores `sha256`; `run_restore`
  rejects a tampered backup (verified passing: `test_pep13_tampered_
  backup_rejected_on_restore`).
- **Target MIC derivation**: `_derive_target_security_id` resolves
  `config_PEP.exchange` via the canonical `LEGACY_ALIAS_TO_MIC`
  (imported from `provider_symbols.py`, no duplicate table) — never
  hardcodes `"XNAS"`; aborts (`exit_code=2`) if unresolvable.
- **Currency evidence**: `_currency_verdict` returns `unanimous:<CUR>`
  only when every discovered ledger_txn's `gross.currency` agrees and
  differs from current `listing_currency`; any split returns
  `inconclusive` with `proposed=None` — never guessed.
- **Collision handling**: `_check_collision` fail-closes on any
  conflicting hard identifier (isin/cusip/sedol), ticker mismatch, or a
  malformed pre-existing target — verified passing
  (`test_pep8_conflicting_isin_aborts`, `test_pep9_malformed_target_
  aborts`).
- **CAS**: `_etag_replace` uses `MatchConditions.IfNotModified` for
  `config_PEP` and every `ledger_txn`/`import_session` patch; a conflict
  on one movement is recorded and does not block others (verified:
  `test_pep14_etag_conflict_on_one_movement_does_not_block_others`).
  **Minor advisory (non-blocking)**: the final source-delete call
  (`symbols_container.delete_item(...)`) does not pass an explicit
  `etag`/`match_condition` guard — it instead re-reads the doc fresh and
  re-confirms zero remaining references immediately beforehand, which is
  a materially narrower race window but not a true CAS guard on the
  delete itself. Recommend hardening in a future pass; does not block
  this gate.
- **Field preservation**: verified passing —
  `test_pep11_financial_fields_byte_equivalent`,
  `test_pep11_movement_ids_unchanged`,
  `test_pep11_account_id_unchanged`,
  `test_pep11_only_security_id_changed_on_movement`.
- **Import-session references**: discovered and patched via the same
  `resolution_map`/`enrolled_security_ids` scan defined in the contract;
  absence of any sessions does not abort (`test_no_import_sessions_
  does_not_abort`).
- **Holdings equivalence**: `_holdings_snapshot` aggregates
  `movement_count`/`net_qty` per `security_id`, compared before/after;
  an injected mismatch aborts with `VerificationError` (exit 3) *before*
  Phase 5's delete step — verified
  (`test_pep10_holdings_mismatch_aborts_before_delete`).
- **No source deletion with remaining refs/conflicts**: confirmed in
  source — CAS conflicts on any ledger_txn short-circuit before
  verification and the source is never deleted; the same is true if the
  post-write re-check finds any surviving reference. Verified:
  `test_pep16_source_not_deleted_if_references_remain`,
  `test_pep18_source_deleted_only_after_step5_passes`.
- **Idempotent restore**: `run_restore` re-reads live state before
  writing rather than blind-overwriting, and does not clobber a
  legitimate concurrent edit — verified
  (`test_pep19_restore_reverts_migration`,
  `test_pep19_restore_does_not_clobber_legitimate_concurrent_edit`).

## CLI/resource-name safety

`_build_cosmos` requires `COSMOSDB_ENDPOINT`/`COSMOSDB_KEY` env vars and
fails closed (`exit_code=2`) if unset — verified live (`--help` prints
cleanly; default `--audit` with no env vars set errors out without
attempting a connection). `--database`/`--symbols-container`/
`--portfolio-container` default to the same production names used
throughout the rest of the codebase (`option-income-lab`/`symbols`/
`portfolio`) — consistent with `migrate_legacy_symbol_config.py`'s
established convention, and all are explicitly overridable, so there is
no risk of an operator's real credentials being silently redirected to
an unintended database/container by a hidden default.

## Blocker

**`backend/tests/test_repair_pep_security_id.py::TestBackupCompleteness::
test_pep12_backup_created_before_first_write`** — dead/unreachable
assertion scaffolding (`_backup_then_apply` helper defined but never
called; `backup_completed` flag never set True on the executed path).
Must be rewritten to actually exercise the backup-before-write invariant,
e.g. by wrapping `write_backup`/`build_backup` (or the backup_dir file
write) itself to flip a shared flag, then asserting the flag is `True`
inside the `create_item` wrapper — proving the real call order rather
than relying on a helper that is never invoked.

## Assignment (reviewer lockout)

Basher is the original author of the defective test and is **locked out**
from fixing it in this cycle. Assign the PEP-12a rewrite to **Reuben**
(test-file-only scope: `backend/tests/test_repair_pep_security_id.py`,
this one test method only). Livingston's product code
(`backend/scripts/repair_pep_security_id.py`) requires no changes and is
approved as-is; do not modify it in the revision cycle.

Re-submit for a follow-up point gate once PEP-12a is corrected and all
42 tests pass.
