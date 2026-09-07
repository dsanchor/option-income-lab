# Danny — Design Review: Provider-Symbol Corrections for ENAG / MICCT / ULVR

## Verdict: APPROVED (design only — no implementation, no production writes)

## Source facts (read-only audit)

| Canonical | current `provider_symbols.yfinance` | Yahoo result | Live correct ticker | Company/exchange/currency (live) | Ledger refs |
|---|---|---|---|---|---|
| `XMAD:ENAG` | `ENAG.MC` | none | `ENG.MC` (must verify) | — | 28 |
| `XAMS:MICCT` | `MICCT.AS` | 404 | `MICC.AS` | Magnum Ice Cream Company N.V., AMS, EUR | 6 |
| `XAMS:ULVR` | `ULVR.AS` | 404 | `UNA.AS` (Unilever PLC, AMS, EUR) — `ULVR.L` also live on London | 38 |

No identity migration is requested or needed: all three canonical
`security_id`s (`XMAD:ENAG`, `XAMS:MICCT`, `XAMS:ULVR`) are correct and
must not change. This is purely a **provider-symbol correction** — the
existing `provider_symbols` override precedence in
`resolve_yfinance_symbol`/`resolve_tradingview_symbol`
(`security_master_doc["provider_symbols"][...]`, already the
highest-precedence lookup) is the exact mechanism designed for this, and
is reused verbatim, not duplicated.

## Why `ULVR.AS` and `MICCT.AS` fail, and why the override is safe

Yahoo Finance does not always mirror the local exchange ticker after a
listing name change/merger (e.g. Unilever's ticker on Euronext Amsterdam
is `UNA`, not `ULVR`; Magnum's spin-off ticker is `MICC`, not `MICCT`).
`suggest_yfinance_symbol(ticker, exchange_mic)` mechanically appends the
MIC suffix to the **local/canonical** ticker — it has no way to know about
a provider-side ticker divergence, and must not guess one; that is
precisely why `provider_symbols.yfinance` exists as an explicit,
per-security override (§ already documented in
`resolve_yfinance_symbol`'s own docstring, "e.g. Nestlé NESN → NESN.SW" as
a normal-case example, and per-security override for
divergent-ticker cases like this one). Per the user's explicit
instruction, `XAMS:ULVR` keeps its canonical identity and uses the
`UNA.AS` override unless evidence contradicts — evidence here (Magnum,
Unilever both live/AMS/EUR) directly supports it.

## TradingView overrides are also required (new correction, same mechanism)

`resolve_tradingview_symbol`'s non-override path constructs
`f"{MIC_TO_TRADINGVIEW_EXCHANGE[mic]}-{ticker.upper()}"` from the
**canonical local ticker**, exactly like the Yahoo suffix path — so
absent an override it would produce `BME-ENAG`, `EURONEXT-MICCT`,
`EURONEXT-ULVR`: all three wrong, for the identical reason (divergent
provider ticker vs. canonical ticker). Because the user has explicitly
asked whether TradingView overrides are required, and the divergence is
proven for all three, **yes — all three need
`provider_symbols.tradingview` overrides**, set to the corrected ticker
under the already-approved MIC→exchange-code table
(`MIC_TO_TRADINGVIEW_EXCHANGE`, unchanged, no new mapping):

- `XMAD:ENAG` → `BME-ENG` (pending §1 verification of `ENG.MC`)
- `XAMS:MICCT` → `EURONEXT-MICC`
- `XAMS:ULVR` → `EURONEXT-UNA`

Both overrides are set via the **same** `provider_symbols` map on each
security_master doc (`{"yfinance": "...", "tradingview": "..."}`), through
the existing `validate_provider_symbols()` helper — no new validation
logic, no new mapping table, no divergent code path from what
`provider_symbols.py` already defines.

## Required design: `backend/scripts/repair_provider_symbols_enag_micct_ulvr.py`

### 1. Live provider verification (mandatory before any write)

- For each of the 3 securities, call `YFinanceFetcher(candidate).get_ticker_data()`
  with the proposed corrected Yahoo symbol (`ENG.MC`, `MICC.AS`, `UNA.AS`)
  and verify:
  - A non-empty `info` result is returned (not `None`/404-equivalent).
  - `currency == "EUR"` (matches each security's existing
    `listing_currency` — no currency change is in scope here; this is a
    corroboration check, not a correction).
  - `exchange`/`fullExchangeName` resolves (via the same live-observed,
    explicitly-cited substring corroboration approach used in the AD
    repair design — reusing whatever hint table that work introduces
    rather than inventing a third one, if it has landed by the time this
    is implemented; otherwise document the raw observed fields verbatim in
    the audit report as the evidence trail) to the security's own
    canonical MIC (`XMAD` for ENAG, `XAMS` for MICCT and ULVR) — i.e. the
    corrected ticker must resolve to the **same exchange the security
    already canonically belongs to**, not merely "some live ticker."
  - For `ENG.MC` specifically: also verify the returned company
    name/`longName` is plausibly the same issuer already on file for
    `XMAD:ENAG` (fuzzy/substring check against existing `company_name`,
    logged either way) — this is the one candidate in the evidence set
    explicitly flagged "must verify," unlike MICCT/ULVR which already have
    confirmed live company identities.
- Any missing/mismatched/unreachable result for a given security **aborts
  that security's correction only** (independent per-security gate — a
  failure on ENAG must not block MICCT/ULVR from being corrected), exit
  code 2 if *all three* fail, otherwise partial success is reported
  per-security with a non-zero count of unresolved corrections.

### 2. Backup (mandatory, before any write)

- Full document body (including `_etag`) of all 3 `security_master` docs,
  SHA-256 checksum, `generated_at` timestamp — same convention as the
  PEP/AD repairs. Ledger and config docs are **not modified** by this
  repair (see §3) but should still be read and their reference counts
  recorded in the audit report for verification purposes (28/6/38 expected
  per the source facts).

### 3. Exact scope of mutation — provider_symbols only

- Only `security_master.provider_symbols.yfinance` (and `.tradingview`)
  are updated, via `validate_provider_symbols()` (reused, not
  reimplemented) merged into the existing `provider_symbols` map (existing
  unrelated provider keys, if any, must be preserved — this is a merge,
  not a replace).
- `security_id`, `exchange_mic`, `listing_currency`, `isin`/`cusip`/
  `sedol`, `aliases`, `asset_class`, `broker_ids`, `created_at`, and every
  other field are byte-identical before/after.
- `config_*` documents and all `ledger_txn` docs for these three tickers
  are **not touched** — canonical IDs are unchanged, so nothing downstream
  references a stale identity. This repair is strictly narrower than the
  PEP/AD identity repairs (no repointing, no deletion, no collision
  surface) — confirm and assert zero writes to the portfolio container.

### 4. Enrichment rerun + persistence verification (in scope, per request)

- After each security's provider_symbols correction succeeds, the script
  must call the existing `enrich_symbol(ticker, yf_symbol=<corrected>)`
  (from `src/portfolio_enrichment.py`, reused verbatim — no reimplemented
  DGI analysis) and persist via the existing
  `cosmos.update_symbol_enrichment(symbol, enrichment)` path, exactly
  mirroring what the hourly scheduled job already does — this repair
  simply triggers it once, synchronously, instead of waiting for the next
  cron cycle, using the now-corrected override.
- Post-write verification: re-read each `config_<TICKER>` document and
  confirm `enrichment.last_updated` is newer than the repair's start
  timestamp and `enrichment.quality_score`/`category` are populated
  (non-default) — this is the "warm-up/enrichment persistence
  verification" the user asked for. A security whose provider_symbols
  fix succeeded but whose subsequent `enrich_symbol` call fails (e.g.
  transient DGI/network error) must be reported as
  `provider_symbol_corrected=True, enrichment_verified=False` — not
  silently swallowed, but also not treated as a fatal repair failure
  (the provider_symbols fix itself is the durable, valuable change; the
  next scheduled hourly run will retry enrichment automatically now that
  the override is in place).

### 5. Idempotency / dry-run / exit codes

- `--audit` (default): read-only, performs the same live checks, reports
  proposed corrections and their verification verdicts, writes nothing.
- `--apply`: performs the writes per §1-§4, independently gated per
  security.
- Re-running `--apply` after a prior full or partial success must be a
  safe no-op for already-corrected securities (idempotent: if
  `provider_symbols.yfinance` already equals the proposed value, skip the
  write but still re-run the enrichment-verification check).
- Exit codes: 0 (all requested corrections applied/already-correct or
  audit ran cleanly), 2 (backup failure, or **all** per-security provider
  verifications failed — nothing written), 3 (a provider_symbols write
  succeeded but a post-write verification — either the persisted
  `provider_symbols` value itself, or, informationally, enrichment — did
  not confirm; see §4 for enrichment's non-fatal treatment specifically).
- No restore-of-financial-state is needed since ledger/config are never
  touched; `--restore` still restores the 3 `security_master` docs from
  backup for symmetry/reversibility of the provider_symbols change itself.

## Tests (independent authorship required)

Cover: dry-run zero writes; per-security independent success/failure (one
mismatched provider does not block the other two); `provider_symbols`
merge preserves pre-existing unrelated override keys; `validate_
provider_symbols()` reuse (reject an oversized/malformed proposed value
rather than writing it raw); byte-identical preservation of all other
security_master fields; zero writes to config/ledger/portfolio container
under any circumstance; enrichment rerun invoked only after a successful
provider_symbols write; enrichment failure reported non-fatally without
rolling back the provider_symbols correction; idempotent re-run; restore
reverts security_master only.

## Assignment

- **Implementation**: Linus — most recently worked the live-provider
  verification mechanism (PEP currency correction) this design directly
  reuses in spirit; no lockout applies since this is a new, distinct
  artifact.
- **Tests**: Reuben — independent of Linus, no prior authorship on this
  artifact.

## Authorized paths

- `backend/scripts/repair_provider_symbols_enag_micct_ulvr.py` (new,
  Linus)
- `backend/tests/test_repair_provider_symbols_enag_micct_ulvr.py` (new,
  Reuben)
- No changes authorized to `provider_symbols.py`, `portfolio_
  enrichment.py`, `dgi_screener.py`, or any other shared module — this
  repair only **consumes** those existing contracts (specifically
  `validate_provider_symbols`, `resolve_yfinance_symbol`/`resolve_
  tradingview_symbol`'s existing override precedence, and `enrich_symbol`).

No production execution is authorized under this review.
