# Danny — POINT Gate: PEP-12a revision (Reuben)

## Verdict: APPROVED

Prior blocker (dead/unreachable `_backup_then_apply` closure in PEP-12a)
is resolved. Confirmed lockout was respected: `backend/scripts/
repair_pep_security_id.py` mtime (15:21:45) predates the test file's
revision (15:28:35) and its content hash is unchanged from the prior
gate — **product code untouched**, as required. Only Reuben's test-file
revision was inspected.

## What the rewrite actually proves

`test_pep12_backup_created_before_first_write` now:

1. Drives the **real** `apply_repair` → `run_apply` path (no product
   code touched) against fake containers whose fixture is deliberately
   shaped so the happy path exercises **all three mutation kinds**
   (`create_item` for the new target, `replace_item` for `config_PEP` +
   both ledger_txns, `delete_item` for the source) across **both**
   containers (symbols + portfolio) — the test explicitly asserts
   `mutation_kinds_seen == {"create_item","replace_item","delete_item"}`
   and `containers_seen == {"symbols","portfolio"}` before trusting the
   ordering check, so the invariant can't pass on a degenerate/empty
   fixture.
2. Instruments the container mutation methods and monkeypatches the
   real `write_backup` (not a stub) to additionally call the script's
   own `read_backup` immediately after writing and assert the re-read
   `sha256` matches the in-memory `RepairBackup.sha256`, plus asserts the
   file exists and is non-empty — i.e. it proves a **complete,
   checksum-valid** backup file exists on disk before trusting the
   ordering, not merely that a function was called.
3. Builds a single ordered event trace across both instrumentation
   points and asserts `backup_indices[0] < first_mutation_idx` — a real
   ordering proof, not a dead flag.
4. Asserts `report.exit_code == 0` first, so the ordering assertion can
   only be reached once the fixture has genuinely driven a full,
   successful apply (a failed/aborted run wouldn't validate anything
   meaningful).

This directly closes the gap identified in the prior gate and is not
brittle — it is keyed on real call order and a real checksum re-read
rather than source-text pattern matching.

## Tests run

- `backend/tests/test_repair_pep_security_id.py` → **42/42 passed**.
- `backend/tests/test_migrate_legacy_symbol_config.py` → **22/22
  passed**, no regressions from the unrelated PEP work.

## Result

Both artifacts (Livingston's script, previously approved and unchanged;
Reuben's corrected test) are sound. **PEP security identity repair
contract is fully approved, design and implementation.** No production
writes were made or authorized in this gate.
