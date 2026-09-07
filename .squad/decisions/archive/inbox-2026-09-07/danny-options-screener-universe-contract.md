### 2026-09-07T13:37:00+02:00: Danny — Design Review Decision
**Directive:** `.squad/decisions/inbox/copilot-directive-20260907-options-screener-universe.md`
**Scope:** Options Screener universe predicate (`/api/screener/options` manual endpoint + the scheduled options-chain cache-refresh job). No conflict with the two other in-flight contracts (single-add-symbol, TradingView symbol) — this touches `options_screener.py`/`app.py`'s screener code paths and `main.py`'s scheduled job, none of which those two contracts modify.

---

## 1. Current-state findings (evidence)

**The universe leak is real and exists in two places, both keyed off an unfiltered `cosmos.list_symbols()`:**

1. **Manual/on-demand:** `backend/web/app.py::_build_screener_symbol_inputs(cosmos, cache, symbol_filter)` — `docs = cosmos.list_symbols()`, filtered only by the caller-supplied `symbols=` query param (`symbol_filter`), never by exchange, portfolio shares, or watchlist membership. Every `symbol_config` document in Cosmos enters `symbol_inputs`, gets checked against the chain cache, gets a warm-up scheduled if cold, and is counted into `evaluate_options_screener`'s `summary.total/ready/warming/error`.
2. **Scheduled:** `backend/src/main.py::_run_options_chain_fetch_async` — `symbols = self.cosmos.list_symbols()` → `symbol_names = [s["symbol"] for s in symbols]` → `cache.refresh_all(symbol_names)`. Same unfiltered universe; this job actively fetches/caches option chains (yfinance + TradingView) for non-US and non-eligible symbols today, wasting provider calls and populating cache entries the screener should never read.

**Reusable building blocks already exist and must not be duplicated:**
- `backend/src/us_exchange_eligibility.py::is_us_options_eligible(mic)` — the single MIC-eligibility predicate (`{"XNYS","XNAS"}`, fail-closed on falsy/unknown).
- `backend/web/app.py::_is_watchlist_member(config)` (§1.2 of `danny-unified-watchlist-contract.md`) — the single "explicit Watchlist membership" predicate: not-auto-enrolled (manually added) OR any `watchlist.{covered_call,cash_secured_put,buy_tracker}` toggle true OR `telegram_notifications_enabled` true. This is the exact, already-approved definition of "explicit Watchlist membership" the new directive refers to — reused verbatim, not redefined.
- `backend/web/app.py::_compute_symbols_overview` already demonstrates the correct N+1-avoiding pattern for "portfolio shares": one `HoldingsService.compute_holdings()` call for **all** tickers, then a per-symbol dict lookup (`holdings_by_ticker`) — no per-symbol Cosmos/ledger query. The Unified Watchlist visibility predicate there uses `shares_val != 0` (any nonzero, including negative, stays visible) — **this directive's screener predicate is intentionally stricter**: `shares_val > 0` only. This is a deliberate divergence between two different, already-independently-approved concerns (row visibility vs. options-screener eligibility), not an inconsistency to reconcile.
- The options-screener's own pre-existing `symbol_config.total_shares` field (read by `_build_share_availability_map` for free-lots/covered-call sizing display) is a **different, legacy, potentially-stale field** from the real computed holdings (`HoldingsService`). It is **out of scope** here — this contract does not touch share-availability display; it only defines which symbols are admitted to the screener universe at all, using the same holdings source as the Unified Watchlist for that determination.
- MIC resolution without an extra per-symbol read: `ensure_symbol_config` (`symbol_config_sync.py`) already writes `symbol_config.exchange = security_master.exchange_mic` at config-creation time for every symbol created through the canonical Add Security/Add Symbol flow (current and, once `danny-single-add-symbol-contract.md` lands, universally going forward). So `doc.get("exchange")` is already a real MIC for the entire canonical population, with **zero additional Cosmos reads** — reuse it directly rather than re-resolving via `security_master` per symbol (which would reintroduce an N+1). Any symbol_config predating security_master linkage (the population `danny-single-add-symbol-contract.md` is actively eliminating the creation source of) will have a non-MIC value in `exchange`; `is_us_options_eligible()` correctly fails closed on it — this is compliant with the directive's explicit "unknown MIC" exclusion requirement, not a bug to special-case.
- Frontend `OptionsScreenerView.tsx`/`screener/options/page.tsx` perform **no** independent eligibility/universe filtering — they render whatever `summary`/`calls`/`puts` the backend returns. **No frontend change is required**; the displayed counts self-correct once the backend universe shrinks.

## 2. Contract

### 2.1 One reusable predicate/source — new module, no duplication
Add `backend/src/options_screener_universe.py` (business-logic layer, importable by both `backend/web/app.py` and `backend/src/main.py` without circular imports — today neither imports the other):

```python
def compute_options_screener_universe(
    symbol_configs: list[dict],
    portfolio_shares_by_ticker: dict[str, Decimal],
) -> set[str]:
    """Return the set of tickers eligible for the Options Screener universe.

    A symbol is included iff:
      1. is_us_options_eligible(doc-resolved MIC) is True, AND
      2. portfolio_shares_by_ticker.get(ticker, 0) > 0
         OR is_watchlist_member(doc) is True

    MIC is resolved from doc["exchange"] only (no security_master lookup —
    avoids N+1; see §1 for why this is safe/correct for the canonical
    population and directive-compliant fail-closed for legacy stragglers).
    Unknown/missing MIC -> not eligible (fail-closed), matching
    is_us_options_eligible's own contract.
    Shares comparison is strict > 0 (Decimal); zero and negative shares
    (data anomaly) never grant universe membership on their own -- only
    explicit Watchlist membership does for a non-held/zero/negative symbol.
    """
```
Internally composes, **without reimplementing**:
- `is_us_options_eligible` from `us_exchange_eligibility.py` (imported, unchanged).
- `is_watchlist_member` — relocated (pure-function extraction, behavior-preserving) from `backend/web/app.py::_is_watchlist_member` into a new shared module `backend/src/portfolio/watchlist_membership.py`, so `main.py` (non-web layer) can use the exact same predicate `app.py` uses. `app.py`'s existing `_is_watchlist_member` becomes a thin import of the relocated function; every existing internal call site in `app.py` (`_compute_symbols_overview`) is unaffected.

### 2.2 Enforcement at both call sites — filter before any per-symbol work
- **Manual:** `_build_screener_symbol_inputs` computes `portfolio_shares_by_ticker` via one `HoldingsService.compute_holdings()` call (same pattern as `_compute_symbols_overview` — reuse, don't reinvent), calls `compute_options_screener_universe(docs, portfolio_shares_by_ticker)`, and filters `docs` down to that set **immediately after** `cosmos.list_symbols()` and **before** the existing `symbol_filter` narrowing and before the per-doc chain/cache loop. Excluded symbols therefore never reach the cache-hydration check, never get a warm-up scheduled, and never enter `symbol_inputs` — so `evaluate_options_screener`'s `summary.total/ready/warming/error` counts are correct by construction with **zero changes to `options_screener.py` itself**.
- **Scheduled:** `_run_options_chain_fetch_async` computes `portfolio_shares_by_ticker` the same way (via `self.cosmos.portfolio_container` → `HoldingsService`, mirroring the web-layer pattern but instantiated from the scheduler's own long-lived `cosmos` handle) and filters `symbols` down to `compute_options_screener_universe(...)` **before** building `symbol_names` for `cache.refresh_all()`. This stops the scheduled job from ever fetching/caching chains for ineligible symbols, closing the provider-call waste identified in §1.

### 2.3 Deduplication
Not needed beyond what already exists: `symbol_config` documents are one-per-ticker by Cosmos partition key (`config_{ticker}`), so `cosmos.list_symbols()` is already duplicate-free; `compute_options_screener_universe` returns a `set[str]`, and both callers filter their existing list against it (no new dedup pass required).

### 2.4 Cache invalidation
None required, by construction. Because ineligible symbols are filtered out **before** either the scheduled `cache.refresh_all()` or the manual `cache.get_or_hydrate()` is ever invoked for them, their entries (if any pre-existing cache rows remain from before this fix, or from an unrelated single-symbol page view) are simply never read or written by screener code paths again. Any stale leftover rows are inert w.r.t. the screener and age out via the cache's existing unrelated staleness/TTL handling — no explicit eviction step is needed to satisfy this directive.

### 2.5 Counts
`summary.total`, `summary.ready`, `warming`, `error` inside `evaluate_options_screener` (`options_screener.py`) require **no code change** — they already sum only over the `symbol_inputs` list they're given, which now (per §2.2) already reflects the correct universe before it reaches them.

### 2.6 N+1 avoidance (explicit)
Per request/run: **one** `list_symbols()` call, **one** `HoldingsService.compute_holdings()` call, **zero** additional per-symbol Cosmos or security_master reads for MIC (reused from `doc["exchange"]`, §1). Identical shape to the already-approved `_compute_symbols_overview` batching pattern — no new query pattern introduced.

### 2.7 Frontend
**No change required.** `OptionsScreenerView.tsx` and `screener/options/page.tsx` perform no independent universe/eligibility logic; the counts and rows they render are already sourced entirely from the backend response and will self-correct once §2.2 lands.

## 3. Assignments

- **Linus** — Backend: extract `is_watchlist_member` into `backend/src/portfolio/watchlist_membership.py` (thin re-export from `app.py`, no call-site behavior change); add `backend/src/options_screener_universe.py::compute_options_screener_universe`; wire it into `_build_screener_symbol_inputs` (`app.py`) and `_run_options_chain_fetch_async` (`main.py`), both via the single-batch holdings pattern in §2.2.
- **Basher** — Tests:
  - `backend/src/options_screener_universe.py`: unit tests for all four quadrants (US+shares>0, US+watchlist-only, US+neither→excluded, non-US regardless of shares/watchlist→excluded, unknown/missing MIC→excluded, negative shares without watchlist membership→excluded, negative shares with watchlist membership→included).
  - Extend `test_options_screener_endpoint.py`: assert a non-US symbol and a zero/negative-share non-watchlist symbol never appear in `/api/screener/options` rows or counts, and are absent from `summary.error`/`warming` too (i.e., truly uncounted, not silently errored).
  - New/extended scheduler test for `_run_options_chain_fetch_async`: assert `cache.refresh_all()` is invoked with only the eligible symbol subset (mock `HoldingsService`/`cosmos.list_symbols()` fixtures spanning the same quadrants).
  - No frontend test changes required (§2.7).

## 4. Verdict

**APPROVED** design — proceed to implementation per §2.1–2.7 and §3 assignments. No code written by Danny.
