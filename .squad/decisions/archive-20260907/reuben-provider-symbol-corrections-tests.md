# Reuben — independent tests for provider-symbol corrections (ENAG/MICCT/ULVR)

**Contract:** `.squad/decisions/inbox/danny-provider-symbol-corrections-enag-micct-ulvr.md`
**Script under test (read-only, Linus-owned):** `backend/scripts/repair_provider_symbols_enag_micct_ulvr.py`
**New artifact (mine):** `backend/tests/test_repair_provider_symbols.py`

## What I did
Wrote an independent, hermetic pytest suite (33 tests, no lockout applicable —
new artifact, clean division of labor per the contract's own assignment
section). No product code was modified.

Coverage: PSC-0 (ungated — proves the already-shipped
`resolve_yfinance_symbol`/`resolve_tradingview_symbol`/`validate_provider_symbols`
override mechanism this repair reuses actually yields the exact corrected
symbols and rejects malformed overrides), plus 15 gated classes exercising
the real script end to end via fake Cosmos containers and a fake
yfinance-style provider: audit/dry-run zero-writes; per-security
independent verification (currency/exchange-MIC/company-name corroboration,
provider unreachable/error/missing-fields all fail closed); all-3-fail →
exit_code 2; checksum-valid backup written before the first mutation;
provider_symbols merge preserves unrelated keys, all other security_master
fields byte-identical; config_*/ledger_txn docs never written; synchronous
enrichment invoked with canonical ticker + corrected yf_symbol only after a
successful write, enrichment persistence has non-empty
technicals/quality/entry/momentum, enrichment failure is non-fatal (no
rollback) but surfaces as exit_code 3; idempotent re-run performs zero
additional writes; `restore_repair` reverts exactly the 3 security_master
docs and never touches config/ledger/portfolio; TradingView overrides are
exactly `BME-ENG` / `EURONEXT-MICC` / `EURONEXT-UNA`.

## Result
`pytest tests/test_repair_provider_symbols.py -v` → **33 passed**, 0 failed,
0 skipped (script now exists and is stable; all gated tests execute for
real, not just skip).

Full backend suite: 3746 passed, 20 failed, 5 skipped. All 20 failures are
in `tests/test_yfinance_data_provider.py` and are **pre-existing and
unrelated** — confirmed by running that file in isolation (only 2 of the 20
fail standalone; the rest are a pre-existing test-isolation/ordering issue
in that file, reproducible with or without my new test file present, and
that file has zero uncommitted changes). No regression introduced by this
change.

## Defect found (reported, NOT fixed — script is read-only for this task)
`verify_provider_symbol()` (line ~370 of
`repair_provider_symbols_enag_micct_ulvr.py`) does:

```python
fetcher = YFinanceFetcher(spec["expected_yfinance"])
```

when no `yf_fetcher` is injected. This passes the ticker string (e.g.
`"ENG.MC"`) positionally into `YFinanceFetcher.__init__(requests_per_minute:
int = 60, max_retries: int = 3)`, binding it to `requests_per_minute`. It
will raise `TypeError` on `60.0 / requests_per_minute` inside the
constructor — i.e. the script crashes on **every real (CLI, non-test)
invocation** since none of them inject a fetcher. All of my tests inject
`yf_fetcher=` explicitly and are unaffected by this bug, so it does not
surface in the test suite; it must be fixed before this script is safe to
run against production Cosmos. Recommend: `YFinanceFetcher()` (no
positional ticker arg) with the ticker passed to
`.get_ticker_data(spec["expected_yfinance"])` instead — Linus's call.

## Scope / hygiene
Only `backend/tests/test_repair_provider_symbols.py` was created/modified
by me. No commit/push performed. Other untracked files observed in
`git status` (`repair_ad_xams_security_id.py`,
`test_repair_ad_xams_security_id.py`, `SymbolConfigurationCard.tsx`, etc.)
are concurrent work by other agents/background processes, not touched by
me.
