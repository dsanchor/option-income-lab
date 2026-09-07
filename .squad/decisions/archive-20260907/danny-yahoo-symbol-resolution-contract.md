# Design Review: Yahoo Symbol Resolution for Portfolio Enrichment

**Date:** 2026-09-07
**Reviewer:** Danny (Lead)
**Trigger:** Portfolio enrichment fails for every non-US security (ABE, ACS, ULVR, BAYGN, NESN, DGE, …) because local tickers are sent to Yahoo without exchange suffixes.

## Root cause (confirmed by code inspection)

- `portfolio_enrichment.run_portfolio_enrichment()` reads `sym_doc.get("symbol", "")` — the **bare local ticker only** — and passes it straight to `dgi_screener.analyze_single_symbol(symbol)` → `YFinanceFetcher.get_ticker_data(symbol)` → `yf.Ticker(symbol)`. The symbol's MIC (stored as `symbol_config.exchange`, e.g. `XMAD`, `XLON`) is **never consulted**.
- The fix is **not** a missing suffix table. `backend/src/portfolio/provider_symbols.py` already implements exactly what's needed: `MIC_TO_YFINANCE_SUFFIX` (XMAD→.MC, XLON→.L, XETR→.DE, XSWX→.SW, XPAR→.PA, XAMS→.AS, XBRU→.BR, XLIS→.LS, XNYS/XNAS→none) and `suggest_yfinance_symbol(ticker, exchange_mic)`, plus a per-security `provider_symbols` override map on `security_master` docs (for names Yahoo maps unpredictably, e.g. Nestlé `NESN` → `NESN.SW`, Beiersdorf-style tickers, dual-listed ADR-vs-local ambiguity). None of this is wired into the enrichment path today.

## Implementation contract (decision)

1. **Single resolution point.** Add one function, `resolve_yfinance_symbol(security_id_or_symbol_doc, security_master_doc=None) -> str | None`, in `backend/src/portfolio/provider_symbols.py` (same module that owns the suffix table — do not fork logic into `portfolio_enrichment.py` or `dgi_screener.py`). Precedence, highest wins:
   a. `security_master.provider_symbols["yfinance"]` explicit override, if present and non-empty.
   b. `suggest_yfinance_symbol(ticker, exchange_mic)` — existing suffix table.
   c. Unknown MIC → `None` (fail closed; do NOT guess or fall back to bare ticker for non-US MICs — a bare local ticker silently resolves to the wrong US-listed security on Yahoo often enough that "no data" is safer than "wrong data").
2. **Caller change.** `portfolio_enrichment.enrich_symbol()` must accept/derive both the local ticker (for storage keys, logging, Cosmos doc identity — unchanged) and the resolved Yahoo symbol (for the fetch call only). `run_portfolio_enrichment()` must fetch the companion `security_master` doc (by `security_id = f"{doc['exchange']}:{doc['symbol']}"`, falling back to `doc.get("security_id")` if already present) to obtain `exchange_mic` and `provider_symbols`. `analyze_single_symbol()` gains an optional `yf_symbol` parameter (defaults to `symbol` for backward compatibility with the existing US-only DGI screener universe, which never had this problem and must not regress).
3. **XNYS/XNAS unaffected.** Empty suffix, bare ticker — zero behavior change for the existing US screener universe (verified: 431 backend tests currently green must stay green).
4. **Unknown/missing MIC behavior.** If `exchange` is missing on `symbol_config` (legacy docs predating security_master rollout) or the MIC isn't in the suffix table, `resolve_yfinance_symbol` returns `None`. `enrich_symbol` must then log a single structured warning (`"Portfolio enrichment: %s — no Yahoo symbol mapping for MIC=%s"`) and return `None` (skip, counted in `errors`), exactly like today's "no market data" path — no new error taxonomy needed, no partial/garbage enrichment written to Cosmos.
5. **Logging.** One INFO line per symbol when a suffix or override is applied (ticker → resolved symbol), so on-call can audit routing without a debugger. No change to existing per-symbol success/error log lines beyond adding the resolved symbol to the message.
6. **Persistence.** No schema change required — `security_master.exchange_mic` and `.provider_symbols` already exist (Amendment J / provider-symbol-import contract). No migration.
7. **Test boundaries.**
   - `test_provider_symbols.py`: add cases for the new `resolve_yfinance_symbol` (override precedence over suffix table; unknown MIC → None; missing `provider_symbols` key → falls through to suffix).
   - New `test_portfolio_enrichment.py` (currently absent — gap Basher should close): `enrich_symbol`/`run_portfolio_enrichment` with a fake Cosmos returning XMAD/XLON/XETR/XSWX fixtures asserts the yfinance fetcher is invoked with the suffixed symbol, not the bare ticker; unknown-MIC fixture asserts skip+warning, not a crash.
   - `test_yfinance_data_provider.py` / `dgi_screener` tests: no change needed — they operate below the resolution layer and already receive whatever symbol string is passed in.
   - Do not add network-dependent tests; mock `YFinanceFetcher`/`yf.Ticker` as existing tests do.

## Scope guardrails

- No changes to `yfinance_fetcher.py` itself — it correctly fetches whatever symbol string it's given; the bug is entirely upstream in what string gets constructed.
- No changes to the US-options-eligibility gate (`us_exchange_eligibility.py`) — unrelated concern (that gates the options agents, not DGI enrichment).
- No new Cosmos containers/fields.

## Verdict: APPROVED FOR IMPLEMENTATION

## Assignments

- **Linus:** Implement `resolve_yfinance_symbol()` in `provider_symbols.py`; update `portfolio_enrichment.py` (fetch security_master, resolve, pass `yf_symbol` through) and `dgi_screener.analyze_single_symbol()` (accept optional `yf_symbol` param, default to `symbol`). Keep the diff surgical — no refactor of unrelated enrichment logic.
- **Basher:** Add/extend unit tests per §7 above (provider_symbols precedence cases + new `test_portfolio_enrichment.py` covering XMAD/XLON/XETR/XSWX suffix routing and unknown-MIC skip behavior). Run full backend suite to confirm zero regressions before sign-off.
