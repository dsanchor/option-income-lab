# Reuben — currency test revision (Basher locked out)

**Scope:** `backend/tests/test_repair_pep_security_id.py` only, per
`danny-pep-repair-currency-correction.md`. `backend/scripts/repair_pep_security_id.py`
(Linus-owned) was not modified.

## What changed

- Replaced the rejected `TestCurrencyEvidence` (5 tests encoding "unanimous
  ledger gross.currency implies listing_currency") with a corrected class of
  the same name asserting the amendment's invariant: ledger `gross.currency`
  never determines `listing_currency`, in either direction, with or without
  matching values, and movements stay byte-identical (`pep3`/`pep3b`/`pep3c`);
  absent `--listing-currency`, `listing_currency` is preserved unchanged in
  both the with-movements and no-movements cases, and the report must not
  claim a correction occurred (`pep4`/`pep5`).
- Added a new `TestProviderVerifiedListingCurrency` class (8 tests) covering
  the corrected §3a-3d contract: matching provider triple writes the
  requested currency; mismatch in `currency`/`financialCurrency`/`exchange`
  aborts exit 2 with zero backup/mutations; provider exception/`None`/missing
  fields all fail closed; `--audit`(`dry_run=True`) with an unreachable
  provider does not abort and reports `provider_currency_verdict` containing
  "unreachable"; verification re-runs and can still abort on a resumed
  `--apply` even after a prior successful run. All provider interaction is
  faked (`_FakeProvider` mimicking `YFinanceFetcher.get_ticker_data`) — no
  network.
- Added a capability gate (`_CURRENCY_FLAG_AVAILABLE` / `_currency_skip`,
  via `inspect.signature`) so the new class would have skipped gracefully
  had `apply_repair` not yet accepted `listing_currency` — mid-revision,
  Linus's concurrent work landed the corrected mechanism (`yf_fetcher`/
  `listing_currency` kwargs, `provider_currency_verdict` field), so all 13
  currency tests now run and pass rather than skip.

## Validation

- `pytest tests/test_repair_pep_security_id.py -k Currency`: 13/13 passed.
- `pytest tests/test_repair_pep_security_id.py`: 51/51 passed (was 42 before
  this revision's additions; no regressions, including
  `TestBackupCompleteness::test_pep12_backup_created_before_first_write`
  fixed in the prior revision).
- `pytest tests/test_migrate_legacy_symbol_config.py`: 22/22 passed.
- `git status` confirms only the test file was touched by Reuben; the
  product script's diff is Linus's own pre-existing/concurrent work.
- No `--apply` run against production; all fixtures are in-memory fakes.
