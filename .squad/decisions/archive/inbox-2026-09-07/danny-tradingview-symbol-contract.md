### 2026-09-07T13:33:00+02:00: Danny — Design Review Decision
**Directive:** `.squad/decisions/inbox/copilot-directive-20260907-tradingview-symbol-by-mic.md`
**Scope:** Centralize TradingView symbol resolution by canonical Security MIC. Coordinated with the active single-Add-Symbol work (`danny-single-add-symbol-contract.md`, in progress under Linus in `portfolio_routes.py`).

---

## 1. Current-state findings (evidence)

- `frontend/src/components/TradingViewSymbolInfo.tsx` and `RtChart.tsx` both build `tvSymbol = exchange ? \`${exchange}:${symbol}\` : symbol` **client-side**, from a raw `exchange` prop.
- That prop is `d.exchange` (`frontend/src/app/symbols/[symbol]/page.tsx:150-151`), which comes straight from the `/api/symbols/{symbol}/detail` response's top-level `exchange` field (`backend/web/app.py:1429`, `clean.get("exchange", "")`) — i.e. the **raw `symbol_config.exchange` string**, not a resolved provider code. For symbols created via the canonical Security flow, `ensure_symbol_config` sets this field to the security's `exchange_mic` verbatim (e.g. `"XMAD"`), so today's widgets are literally requesting TradingView symbol `"XMAD:ACS"` — wrong, since TradingView has no `XMAD` exchange code (it wants `"BME-ACS"` / `BME:ACS`). **This is a real, currently-broken plugin/link for every non-US security**, confirmed by inspection (no MIC→TradingView mapping exists anywhere in the repo today).
- A **separate, unrelated** TradingView integration already exists in `backend/src/tv_options_chain_fetcher.py:272-274` (Playwright options-chain scraper): it accepts a symbol in **hyphen format** (`"NASDAQ-AAPL"`), converts to colon format for the widget/page URL (`symbol.replace("-", ":")`), and builds `https://www.tradingview.com/symbols/{tv_symbol}/options-chain/`. Its only current caller (`options_chain_cache.py:909`) always passes a bare US ticker with no exchange prefix (this path is US-options-only per Amendment J), so the hyphen-format capability is present but effectively dormant. This confirms **`"{EXCHANGE}-{TICKER}"` (hyphen) is already this codebase's established TradingView identifier convention** — the new resolver should produce exactly this format, and the existing `.replace("-", ":")` one-liner is the correct (and only necessary) widget-format transform, not a second mapping table.
- `backend/src/portfolio/provider_symbols.py` already has the exact reusable pattern for this problem: `MIC_TO_YFINANCE_SUFFIX` (mapping table) + `suggest_yfinance_symbol()` + `resolve_yfinance_symbol()` (override → MIC mapping → legacy-US-alias fallback → fail-closed `None`), plus a generic `provider_symbols` override map on `security_master` validated by `validate_provider_symbols()` (`_PROVIDER_KEY_RE`/`_PROVIDER_VALUE_RE` already accept any lowercase provider key and any value containing `A-Za-z0-9._^-`, **hyphens included** — a `"tradingview": "NASDAQ-MSFT"` override needs zero validation changes).
- `_compute_symbol_detail()` (`backend/web/app.py:1130`) is the single computation point for both the `portfolio_only` path (security-only, no config yet — `security_doc.get("exchange_mic")` is already directly available) and the main path (config exists — `security` field already carries `exchange_mic` from the linked `security_master`, confirmed at API-response-shape level in `frontend/src/types/symbol-detail.ts:106`). This is the one place resolution needs to be added.
- Linus's in-progress single-add-symbol warm-up code (`portfolio_routes.py::_start_symbol_warmup`) already establishes the precedent of resolving a provider symbol from `security_master.exchange_mic` via `resolve_yfinance_symbol` at exactly this kind of call site — the new TradingView resolver is additive to the same file/module and touches neither that function nor its call site, so there is no collision risk with Linus's active edit.

## 2. Contract

### 2.1 Centralized mapping — single table, backend only
Add to `backend/src/portfolio/provider_symbols.py` (same file as `MIC_TO_YFINANCE_SUFFIX`, not a new file, not duplicated in TypeScript):

```python
MIC_TO_TRADINGVIEW_EXCHANGE: Dict[str, str] = {
    "XMAD": "BME",
    "XAMS": "EURONEXT",
    "XLON": "LSE",
    "XSWX": "SIX",
    "XNYS": "NYSE",
    "XNAS": "NASDAQ",
}
```
Only the six MICs explicitly given by the user are added now. **`XPAR`/`XETR`/`XBRU`/`XLIS` are deliberately left out**, even though they already exist in `MIC_TO_YFINANCE_SUFFIX` — a security on one of those exchanges still gets correct Yahoo enrichment (unaffected, different table) but no TradingView widget/link until each code is verified against TradingView's actual exchange listing (`EURONEXT` vs a country-specific code for Paris/Brussels/Lisbon differs by product; guessing risks silently wrong links). Follow-up: verify real codes via TradingView's public symbol search before adding — out of scope for this contract, tracked as a follow-up item, not blocking.

### 2.2 Resolver — mirrors `resolve_yfinance_symbol` precedence exactly
```python
def resolve_tradingview_symbol(
    ticker: str,
    exchange_mic: Optional[str],
    security_master_doc: Optional[dict] = None,
) -> Optional[str]:
```
Precedence (highest wins), identical shape to the Yahoo resolver so both are auditable side-by-side:
1. `security_master_doc["provider_symbols"]["tradingview"]` — explicit per-security override (escape hatch for the day a code turns out wrong, or a security needs a specific listing venue). No validation changes needed (`_PROVIDER_VALUE_RE` already permits hyphens).
2. `MIC_TO_TRADINGVIEW_EXCHANGE.get(exchange_mic.upper())` → `f"{code}-{ticker.upper()}"`.
3. `exchange_mic` in `_LEGACY_US_EXCHANGE_ALIASES` (`{"NYSE", "NASDAQ", "AMEX"}`, already defined, reused verbatim — no new alias set) → `f"{alias}-{ticker.upper()}"`. This is what makes pre-unification legacy configs (free-text `exchange` field, no linked `security_master`) resolve correctly without a third table: their alias text already *is* the TradingView exchange code for the US tickers they represent.
4. Unknown/missing MIC → `None` (fail-closed — never guess, never fall back to the bare ticker or raw MIC text as the exchange code).

### 2.3 Backward compatibility ("securities created through the prior Security experience")
Resolution keys off `exchange_mic` sourced from the linked `security_master` document wherever one exists (both the `portfolio_only` and main branches of `_compute_symbol_detail` already have this value available today — no new lookup required). This means **any** security created through the canonical Add Security/Add Symbol flow (past or present) resolves correctly automatically, because `exchange_mic` is always the real MIC regardless of when the security was created. Only symbol_configs that pre-date `security_master` entirely (no linked security at all — the same legacy population addressed by the single-add-symbol contract's removal of the old free-text `POST /api/symbols`) rely on step 3 (legacy alias fallback) instead of step 2.

### 2.4 Where resolution happens: backend response, not a frontend helper
Resolution is computed **once**, backend-side, in `_compute_symbol_detail()`, using the already-resolved `exchange_mic` in scope in both branches. Add a new field to the detail response:
```
"tradingview_symbol": string | null   // e.g. "BME-ACS", null if unresolved
```
mirrored in `frontend/src/types/symbol-detail.ts` (`SymbolDetail.tradingview_symbol: string | null`). **No mapping table is ever shipped to the frontend.** `TradingViewSymbolInfo.tsx` and `RtChart.tsx` are changed from `(symbol, exchange)` props to a single `tvSymbol: string | null` prop; internally each still performs the mechanical, already-proven `tvSymbol?.replace("-", ":")` transform for the widget script config (this is a format transform of an already-fully-resolved string, not exchange-code knowledge — keeping it inside the two widget components avoids adding a third file just to hold one `.replace()` call). `symbols/[symbol]/page.tsx` passes `tvSymbol={d.tradingview_symbol}` instead of `symbol={d.symbol} exchange={d.exchange}`.

### 2.5 Fail-closed rendering
When `tradingview_symbol` is `null` (unmapped/unverified MIC, e.g. a Paris/Brussels/Lisbon security today), both widget components must render nothing (omit the widget/link entirely) rather than fall back to a guessed or raw-MIC symbol — consistent with the existing fail-closed pattern from the Yahoo and US-options-eligibility contracts. This is a narrow, additive prop-level change; it does not touch the unrelated `d.exchange`/`us_options_eligible` gating already in place for the Options section.

### 2.6 URL/escaping
`tradingview_symbol` values only ever contain `[A-Z0-9-]` (uppercase mapped exchange code + hyphen + uppercase ticker via `_PROVIDER_VALUE_RE`-compatible characters) — safe to embed directly in a `https://www.tradingview.com/symbols/{tradingview_symbol}/` external link with no additional percent-encoding, and safe to `JSON.stringify` into the existing widget script configs unchanged (already how both components serialize `symbol` today). No escaping helper is needed; do not add one speculatively.

### 2.7 No duplication guardrail
Exactly one mapping table (`MIC_TO_TRADINGVIEW_EXCHANGE`) and one resolver function (`resolve_tradingview_symbol`) may exist in the codebase. `tv_options_chain_fetcher.py`'s hyphen→colon transform is reused as-is inside the two widget components (already proven, one line, not exchange-mapping logic) and must **not** be reimplemented a second time.

## 3. Assignments

- **Linus** — Backend: add `MIC_TO_TRADINGVIEW_EXCHANGE` + `resolve_tradingview_symbol()` to `provider_symbols.py` (additive function, no edits to `resolve_yfinance_symbol` or the in-progress `_start_symbol_warmup`); wire `tradingview_symbol` into `_compute_symbol_detail()`'s both branches (`app.py`).
- **Rusty** — Frontend: change `TradingViewSymbolInfo.tsx`/`RtChart.tsx` to a single `tvSymbol: string | null` prop with fail-closed no-render on `null`; update `symbols/[symbol]/page.tsx` call sites; add `tradingview_symbol` to `SymbolDetail` type.
- **Basher** — Tests: extend `backend/tests/test_provider_symbols.py` with a `TestResolveTradingviewSymbol` class (override precedence; all 6 MIC mappings; legacy US alias fallback for XNYS/XNAS-alias-only configs; unmapped MIC → `None`; missing MIC → `None`); backend detail-endpoint test asserting `tradingview_symbol` appears correctly for both a security-master-linked symbol and a legacy pre-unification config; frontend test asserting both widget components render nothing when `tvSymbol` is `null` and pass the correct colon-converted string to the script config otherwise.

## 4. Verdict

**APPROVED** design — proceed to implementation per §2.1–2.7 and §3 assignments. No code written by Danny. XPAR/XETR/XBRU/XLIS TradingView codes remain a tracked, non-blocking follow-up pending verification.
