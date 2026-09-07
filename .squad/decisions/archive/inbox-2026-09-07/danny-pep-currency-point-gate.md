# Danny — POINT Gate: PEP Currency Correction (Linus + Reuben)

## Verdict: APPROVED

Inspected the actual diff in `backend/scripts/repair_pep_security_id.py`
(Linus), `backend/tests/test_repair_pep_security_id.py` (Reuben), and
`.squad/decisions/inbox/danny-pep-repair-currency-correction.md`.

## Confirmed against every required point

- **Ledger `gross.currency` is diagnostic-only.** `_extract_gross_currencies`
  now feeds only `_ledger_accounting_currency_note` (renamed from the
  rejected `_currency_verdict`), explicitly documented as never an input to
  `listing_currency`, and verified inert in both directions
  (`test_pep3_ledger_gross_currency_never_determines_listing_currency`,
  `test_pep3b_..._differing_from_listing_currency_still_ignored`).
- **No currency change without an explicit arg.** `discover()` only calls
  `_verify_listing_currency_with_provider` when `listing_currency` is
  truthy; absent it, `proposed_listing_currency` stays `None` and
  `run_apply` falls back to `source_clean.get("listing_currency", "EUR")`
  unchanged — verified (`test_pep4_no_listing_currency_arg_...`,
  `test_pep5_no_movements_no_flag_preserves_listing_currency`).
- **Mandatory live triple-check.** `_verify_listing_currency_with_provider`
  requires `currency == financialCurrency == requested` **and**
  `_resolve_provider_mic(info)` (reusing the pre-existing
  `EXCHANGE_MAP` in `src/dgi_screener.py` → `LEGACY_ALIAS_TO_MIC` — no new
  or divergent mapping table) to corroborate the already-derived
  `target_mic`. Any single disagreement returns `"mismatch:..."`.
- **Fail-closed on unreachable/missing/mismatched, before backup/mutations,
  exit 2.** `run_apply` runs this gate immediately after `discover()`/
  collision-check and **before** `build_backup`/`write_backup` — confirmed
  by reading the source ordering directly. Verified: mismatch on currency,
  mismatch on exchange/MIC, raised exception, `None` response, and missing
  fields all abort with `exit_code=2` and **zero** `create_item`/
  `replace_item`/`delete_item` calls and **no backup file written**
  (`test_currency_field_mismatch_aborts_zero_mutations` explicitly asserts
  `not list(tmp_path.glob("*.json"))`). A resumed second `--apply` also
  re-verifies and aborts on mismatch with no further mutations
  (`test_verification_precedes_mutations_and_cannot_be_bypassed_on_resume`).
- **Audit remains read-only and reports facts.** `run_audit` calls the same
  `discover()` path but never raises on a provider failure; verified
  (`test_audit_mode_provider_unreachable_does_not_abort` — exit 0, verdict
  reports `"unreachable"`).
- **No unsafe MIC fallback / no duplicate mapping.** MIC derivation for the
  target is unchanged from the prior approved gate (`config_PEP.exchange`
  → `LEGACY_ALIAS_TO_MIC`); the new provider-side corroboration reuses the
  same `LEGACY_ALIAS_TO_MIC` plus the pre-existing (not newly invented)
  `EXCHANGE_MAP` from `src/dgi_screener.py` — confirmed this table already
  existed prior to this change, not authored for this script.
- **Movement bytes unchanged.** `test_pep3c_ledger_movements_remain_byte_
  identical_regardless_of_currency` and the pre-existing PEP-11 class both
  confirm `gross`/`fees`/`net`/`quantity`/`trade_date`/`fx`/`withholding`
  are untouched on any patched ledger_txn.
- **All previously approved repair safeguards unchanged** — backup/
  checksum, collision handling, CAS, holdings-equivalence gating,
  no-premature-deletion, idempotent restore, PEP-12a — none of this logic
  was touched by Linus's diff; confirmed by re-running the full suite.

## Test capability-gate review

`_CURRENCY_FLAG_AVAILABLE = _SCRIPT_AVAILABLE and _accepts_kwarg(apply_repair,
"listing_currency")` uses real `inspect.signature` introspection, not a
hardcoded/always-true bypass — since the corrected API now genuinely
exists, the gate does not skip; all 8
`TestProviderVerifiedListingCurrency` tests and the 5 rewritten
`TestCurrencyEvidence` tests executed for real (none reported as
`SKIPPED`). The fake-provider injection (`_apply_with_provider`) wires
through the real `yf_fetcher=` DI parameter Linus actually implemented
(confirmed via the same signature-introspection helper) rather than
monkeypatching around the verification function itself — only the data
source is faked, so the real `_verify_listing_currency_with_provider`
logic is genuinely exercised, not bypassed. No concealment found.

## Tests run

- `backend/tests/test_repair_pep_security_id.py` → **51/51 passed**.
- `backend/tests/test_migrate_legacy_symbol_config.py` → **22/22 passed**,
  no regressions.
- CLI `--help` prints the new `--listing-currency` flag with accurate
  help text; default `--audit` with no Cosmos env vars fails closed
  (exit 2) without attempting a connection, as before.

## Result

The corrected currency invariant is fully and correctly implemented and
tested. **The PEP security identity repair (design, MIC/collision/backup/
CAS/holdings-equivalence logic, and now the corrected currency
verification) is fully approved.** No production writes were made or
authorized in this gate.
