# PEP repair currency-correction: corrected implementation landed

Source contract: `.squad/decisions/inbox/danny-pep-repair-currency-correction.md`

`backend/scripts/repair_pep_security_id.py` now implements the corrected
invariant only:

- Ledger `gross.currency` is diagnostic-only (`ledger_accounting_currency_note`)
  and can never influence `listing_currency`.
- New optional `--listing-currency CUR` CLI flag. Omitted → no currency
  change, existing source listing_currency preserved.
- If provided, `--apply` requires a live `YFinanceFetcher('PEP')` check
  (currency == financialCurrency == requested CUR, and provider exchange
  corroborates the target MIC via the existing `EXCHANGE_MAP` +
  `LEGACY_ALIAS_TO_MIC` composition) to pass BEFORE backup/mutation, else
  aborts exit 2. `--audit` reports the same check best-effort, never aborts.
- No second MIC/exchange mapping table was created — reused
  `src.dgi_screener.EXCHANGE_MAP` + `src.portfolio.provider_symbols.LEGACY_ALIAS_TO_MIC`.
- All other previously-approved safety behaviors (MIC derivation, collision
  detection, mandatory backup, checksum/CAS, holdings-equivalence,
  restore) are byte-for-byte unchanged.

Verification: full `backend/tests/test_repair_pep_security_id.py` suite is
51/51, including Reuben's independently-authored `TestCurrencyEvidence`
(rewritten) and new `TestProviderVerifiedListingCurrency` classes — no
back-and-forth needed on naming; the contract's literal field names
(`provider_currency_verdict`, `--listing-currency`) were followed exactly.

Not executed: production run, commit, push. Test file untouched by Linus.
