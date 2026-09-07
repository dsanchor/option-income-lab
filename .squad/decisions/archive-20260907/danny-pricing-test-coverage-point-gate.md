# POINT Gate — Symbol Pricing Test-Coverage Revision (SP-4/SP-5)

**Reviewer:** Danny (Lead)
**Verdict: APPROVED**

## Scope
Re-gate of the single narrow rejection from `danny-pricing-viewselector-screener-final-gate.md`
(test-coverage gap in `backend/tests/test_symbol_pricing.py` for SP-4 EUR identity and SP-5 CHF
conversion). Product code was not part of that rejection and remains previously approved.

## Verified
- **Diff scope**: only `backend/tests/test_symbol_pricing.py` was touched. Confirmed
  `backend/src/symbol_pricing.py` and all other previously-approved product files are unmodified
  since the prior gate (`git status` shows no changes beyond the test file relevant to this
  artifact; other unrelated concurrent changes present in the tree — e.g. `GlobalChatView.tsx`,
  `PortfolioMovementsTable.tsx`, `economicsParity.test.mjs` — are out of scope for this point gate
  and not evaluated here).
- **`test_sp1_gbp_cache_entry_shape_via_build`**: now has a real `run_symbol_pricing()` fallback
  (mirrors the SP-25 pattern) when `build_pricing_cache_entry` is absent — no longer skipped.
- **`test_sp4_eur_identity_no_ecb_call`**: real end-to-end run for an XETR/EUR symbol.
  `get_fx_rate` is replaced with a `MagicMock(side_effect=AssertionError(...))` — the test fails
  loudly if the EUR-identity path ever calls the ECB lookup, which is a strong, non-vacuous
  guarantee (not just "assert not called" after the fact — it fails inside the run itself).
  Asserts `price_eur == price_major`, `fx_rate == "1.000000000"`, `fx_pair == "EUR/EUR"`,
  `price_currency == "EUR"` against the actual written cache. This exercises the real
  production code path and would fail today if the identity shortcut were ever broken.
- **`test_sp5_chf_conversion`**: real end-to-end run for an XSWX/CHF symbol with
  `get_fx_rate` mocked to return `"0.940000000"`. Asserts `get_fx_rate` **was** called (correctly
  distinguishing CHF from the EUR-identity shortcut), `price_eur == price_major × rate` (not the
  reciprocal — the test explicitly documents and would catch a `price_major / rate ≈ 128.19`
  regression vs. the correct `≈113.27`), `fx_pair == "CHF/EUR"` (not the reciprocal
  `"EUR/CHF"`), and `fx_rate` stores the direct rate unchanged (not the reciprocal). This is
  meaningful directional/regression coverage, not a source-string check.
- No assertions were weakened, removed, or made vacuous relative to the rejected version — the
  `if build_pricing_cache_entry is not None: ... else: <real run_symbol_pricing path>` structure
  preserves the original "bonus" helper-based check as a fast path while adding genuine coverage
  on the primary (currently active) code path, consistent with the pattern already used in
  SP-1/SP-2/SP-21/SP-11/SP-25.

## Test run
`pytest backend/tests/test_symbol_pricing.py backend/tests/test_options_screener_dropdown.py`:
**62 passed, 0 skipped, 0 failed** (37 pricing + 25 dropdown) — matches Basher's reported numbers
exactly, independently reproduced.

## Conclusion
The rejected gap is closed with real, non-vacuous, production-code-exercising coverage for both
EUR identity and CHF conversion (including reciprocal/direction regression guards). Combined with
the previously-approved product code (pricing scheduler/cache/FX/GBp handling, screener dropdown
`screener_eligible` fix, view selector), this batch is now **fully APPROVED** end-to-end.

## Ownership
No lockout triggered — Basher's revision was accepted as-is; no further reassignment needed.
