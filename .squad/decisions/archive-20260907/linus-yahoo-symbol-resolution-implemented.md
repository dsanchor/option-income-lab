# Implementation Note: Yahoo Symbol Resolution Wired Into Portfolio Enrichment

**Date:** 2026-09-07
**Author:** Linus
**Ref:** danny-yahoo-symbol-resolution-contract.md (APPROVED)

## What shipped

- `resolve_yfinance_symbol(ticker, exchange_mic, security_master_doc=None)`
  added to `backend/src/portfolio/provider_symbols.py` per contract
  precedence (override → MIC suffix table → fail-closed `None`).
- `portfolio_enrichment.run_portfolio_enrichment()` now fetches the
  companion `security_master` doc via `CosmosSecuritiesService` (using
  `sym_doc.get("security_id")` or a derived `{exchange}:{symbol}`), resolves
  the Yahoo symbol, and skips (counted in `errors`, structured warning
  logged) on unresolved MIC — never falls back to the bare ticker.
- `enrich_symbol()` and `dgi_screener.analyze_single_symbol()` both gained
  an optional `yf_symbol` parameter (default `symbol`) — zero behavior
  change for existing US-only callers.

## Deviation from literal contract text worth flagging to Danny

The contract's MIC table only covers true MIC codes (XNYS, XNAS, XMAD, …).
Code inspection during implementation found a second, older code path
(`POST /api/symbols`, the DGI-screener "add to watchlist" flow) that stores
`exchange` as a **free-text display name** — `"NASDAQ"`, `"NYSE"`, `"AMEX"`
— never a MIC code. `cosmos.list_symbols()` returns docs from *both* paths,
so the scheduled enrichment job processes both vocabularies through the
same field name.

Applying the contract's fail-closed rule literally to this field would
have broken enrichment for every legacy US watchlist symbol added via that
older flow (a functional regression with no existing test coverage to
catch it). I added a narrow, explicitly-documented
`_LEGACY_US_EXCHANGE_ALIASES = {"NYSE", "NASDAQ", "AMEX"}` safety net in
`resolve_yfinance_symbol` that treats these three labels as bare-ticker
equivalents (same behavior as XNYS/XNAS — suffix is always empty, so this
is not a second suffix table). This preserves the "wrong data is worse than
no data" principle for genuinely ambiguous/foreign MICs while not
regressing the working US case.

**Ask for Danny/Basher:** worth deciding whether the `/api/symbols` legacy
add flow should eventually be migrated to write real MIC codes (or be
retired in favor of the unified `/api/symbols/add` flow), which would let
this alias set be removed. Out of scope for this fix — flagging for
awareness only.

## Test note for Basher

`test_provider_symbols.py` now has focused coverage for
`resolve_yfinance_symbol` (override precedence, fallthrough, unknown/missing
MIC, XNYS/XNAS bare, legacy alias bare). The broader
`test_portfolio_enrichment.py` (§7 gap) and full regression sweep are still
yours per the contract's assignment.
