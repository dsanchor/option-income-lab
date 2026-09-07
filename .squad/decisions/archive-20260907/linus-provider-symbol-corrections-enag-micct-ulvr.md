# Provider-symbol corrections for ENAG/MICCT/ULVR — implementation landed

Source contract: `.squad/decisions/inbox/danny-provider-symbol-corrections-enag-micct-ulvr.md`

New artifact: `backend/scripts/repair_provider_symbols_enag_micct_ulvr.py`
(canonical implementation, matches the contract's authorized path and
Reuben's independently-authored test suite) plus
`backend/scripts/repair_provider_symbols.py` (thin re-export shim, no
logic of its own, satisfying the shorter filename this session's task
instruction requested).

Scope: `security_master.provider_symbols.{yfinance,tradingview}` only, for
exactly three securities (`XMAD:ENAG` → `ENG.MC`/`BME-ENG`,
`XAMS:MICCT` → `MICC.AS`/`EURONEXT-MICC`, `XAMS:ULVR` → `UNA.AS`/
`EURONEXT-UNA`). No identity/security_id change. Zero writes to config or
ledger/portfolio documents under any circumstance.

Mechanism reused verbatim (per contract, nothing new invented):
`validate_provider_symbols()`, the existing `provider_symbols` override
precedence already built into `resolve_yfinance_symbol`/
`resolve_tradingview_symbol`, and `enrich_symbol()` from
`portfolio_enrichment.py` for the post-correction warm-up rerun.

Each of the three corrections is independently gated behind a live
`YFinanceFetcher` verification (currency corroboration + MIC corroboration
+ ENAG-only company-name corroboration) — a failure on one never blocks
the other two. Audit-default; `--apply` performs mandatory backup before
any write, CAS-merges provider_symbols (preserving unrelated existing
provider keys), then synchronously reruns enrichment per corrected
security (non-fatal if enrichment itself fails — reported via exit_code=3
without rolling back the durable provider_symbols fix). `--restore`
reverts only the 3 security_master docs.

Verification: `backend/tests/test_repair_provider_symbols.py` (Reuben,
independent authorship) 31/31; `test_provider_symbols.py` 72/72;
`test_repair_pep_security_id.py` 51/51 — no regressions. No production
execution, no commit/push.
