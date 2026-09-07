### 2026-09-07T13:27:00+02:00: Danny — Design Review Decision
**Directive:** `.squad/decisions/inbox/copilot-directive-20260907-single-add-symbol-flow.md`
**Scope:** Single end-to-end Add Symbol operation, built on the existing Add Security flow.

---

## 1. Current-state findings (evidence)

**Canonical orchestration endpoint already exists and is mostly correct:**
`POST /api/symbols/add` (`backend/web/portfolio_routes.py:1098`, "R6 — Unified Add Symbol"):
- Select-existing (`{security_id}`) → `svc.get_security()` → 200.
- Create-new (`{create: {...}}`) → `svc.create_security()` (canonical `sec_{MIC}_{TICKER}` doc, 409 on ISIN/ticker collision) → 201.
- Both branches call `ensure_symbol_config(symbols_container, security_id, source="add_symbol")` (`backend/src/portfolio/symbol_config_sync.py:35`) — idempotent (point-read `config_{TICKER}` first; existing config is **never** mutated), and on first creation writes `watchlist.{covered_call,cash_secured_put,buy_tracker}=False` and `telegram_notifications_enabled=False` — i.e. all agents/alerts/notifications default OFF. This is correct and must be preserved as the **only** code path allowed to create a `symbol_config` document.
- Frontend `AddSymbolForm.tsx` + `SecurityCreateForm.tsx` already call this single endpoint via `addSymbol()` (`frontend/src/lib/portfolio-api.ts:379` → `POST /api/symbols/add`) for both search-select and create-new. This is the correct, single UI entry point and should remain unchanged in shape.

**What is missing from the canonical endpoint:** it does **not** run any warm-up. No enrichment, no forecast backfill.

**Two duplicate/legacy entry points still exist and must be eliminated:**

1. **`POST /api/symbols`** (`backend/web/app.py:956`, `api_create_symbol`) — a second, competing symbol-creation path that:
   - Writes the **same** `config_{TICKER}` document type directly via `cosmos.create_symbol()` (`backend/src/cosmos_db.py:143`), **bypassing SecurityMaster entirely** — no canonical `security_id`/MIC:TICKER doc is created or linked.
   - Defaults `telegram_notifications_enabled=True` and accepts `covered_call`/`cash_secured_put`/`buy_tracker` directly in the creation payload — **violates the all-disabled invariant** outright.
   - Is the **only place warm-up currently lives**: fire-and-forget background thread calling `enrich_symbol(symbol)` (bare ticker, no yf_symbol resolution) and a second background thread calling `backfill_symbol_forecasts(...)` (forecast/price history seed). Both use bare local ticker as the Yahoo fetch symbol — broken for any non-US security (the exact class of bug fixed in the Yahoo-symbol-resolution contract).
   - Exposed to the browser via `frontend/src/app/api/symbols/route.ts` `POST` handler (BFF mirror). The `GET` handler on the same file (list symbols) is unrelated and must be kept.

2. **`DgiScreenerView.tsx` `AddButton`** (`frontend/src/components/DgiScreenerView.tsx:248-283`) — the DGI screener's "CSP"/"TBuy" row action calls `POST /api/symbols` **directly**, with `exchange: entry.exchange || "NYSE"` (free-text, not a MIC) and `{cash_secured_put: true}` / `{buy_tracker: true}` **baked into the creation payload** — a second, independent violation of the all-disabled invariant, and a second bypass of SecurityMaster/canonical identity. On 409 it falls back to `PUT /api/symbols/{symbol}` (legitimate, keep).

No other backend caller of `cosmos.create_symbol()` exists (confirmed by direct code search — single call site at `app.py:979`). `update_watchlist()`/`PUT /api/symbols/{symbol}` (`app.py:1562`, `api_update_symbol`) is a distinct, legitimate, already-US-eligibility-guarded (§J.4.3) toggle endpoint for **explicit, subsequent, single-flag** changes on an existing config — this endpoint is unrelated to creation and must remain exactly as-is.

---

## 2. Contract

### 2.1 Canonical endpoint (unchanged identity, extended behavior)
`POST /api/symbols/add` is the **sole** create-or-select-and-configure operation. No other endpoint may create a `symbol_config` or `security_master` document. No other UI surface may present an "add symbol" experience that does not route through this endpoint (directly, or via `addSymbol()`).

### 2.2 Transaction boundary
- **Hard boundary:** SecurityMaster create-or-select. Must succeed or the request fails (existing 400/404/409/503 handling is correct, unchanged).
- **Soft boundary:** `ensure_symbol_config`. Failure is logged and surfaced as `config_warning` in a still-200/201 response (existing behavior, unchanged) — a security may legitimately exist with a temporarily-failed config write; the caller can retry `add_symbol` idempotently.
- **Best-effort, non-blocking boundary:** warm-up (§2.4). Must never affect the HTTP status/body beyond the one new observability field below, must never raise into the request handler, and must be attempted **at most once per new config** (see §2.3).

### 2.3 Idempotency
- Selecting an existing security (`{security_id}`) is naturally idempotent (read-only lookup).
- Creating a security is idempotent w.r.t. collisions: repeat `{create:{...}}` calls for the same ticker/ISIN return 409 with `existing_security` (unchanged).
- `ensure_symbol_config` is idempotent: a second `add_symbol` call for the same `security_id` returns `config_existed=True`, `config_created=False`, and **must not** re-trigger warm-up. Warm-up fires **only** when `config_created is True` (i.e., exactly once, on the auto-enrollment transition from "no config" → "config exists"). This prevents duplicate enrichment/forecast-backfill runs every time a user re-adds an already-watched symbol.

### 2.4 Warm-up (relocated, not reinvented)
On `config_created=True` only, `add_symbol` must, after building the HTTP response body but before returning it, schedule (fire-and-forget background thread, matching the existing pattern in `app.py:979-1043`, **not** `await`ed inline):
1. Resolve the Yahoo provider symbol via `resolve_yfinance_symbol(security)` (`backend/src/portfolio/provider_symbols.py`) using the just-created/-selected `security_master` document. If it returns `None` (fail-closed unknown MIC), **skip enrichment** entirely and log at `info` level — this is not an error.
2. If resolved: background thread calling `enrich_symbol(ticker, yf_symbol=resolved)` (`backend/src/portfolio_enrichment.py:36`) → `update_symbol_enrichment` + `record_enrichment_snapshot`, exactly as `app.py:979-996` does today, but replace the current bare `except Exception: pass` with a logged `logger.warning(..., exc_info=True)` — silent swallowing is not acceptable in the canonical path.
3. Background thread calling `backfill_symbol_forecasts(cosmos, yf_provider, ticker, sessions=DEFAULT_BACKFILL_SESSIONS)` (`backend/src/forecast_cron.py:417`), reusing the existing async-task/event-loop dispatch logic verbatim from `app.py:998-1043`.
4. Add a new response field `warmup_started: bool` (true iff step 1 resolved a yf_symbol and both background threads were scheduled; false if skipped due to unresolved MIC or `config_created=False`). This gives Basher a synchronous, mockable signal for tests without needing to await background threads.

### 2.5 Canonical identity & provider symbols
- `security_id` (`MIC:TICKER`, e.g. `XNYS:AAPL`) is the **only** canonical identity accepted by `add_symbol` for selection, and the only identity persisted. Free-text exchange strings (`"NYSE"`, `"NASDAQ"`) are never valid input to this endpoint's `create` branch beyond what `SecurityCreateForm`/`create_security()` already normalizes via the legacy-alias table from the Yahoo-symbol-resolution contract — no new alias surface is introduced here.
- Yahoo/provider symbol resolution (`resolve_yfinance_symbol`) is used **only** for the warm-up fetch; it is never persisted as or confused with the canonical `security_id`.

### 2.6 Default-off invariant (unchanged, now singly-enforced)
`covered_call`, `cash_secured_put`, `buy_tracker`, `telegram_notifications_enabled` are `False` for every config created via `ensure_symbol_config`, with **zero exceptions and zero caller-supplied overrides at creation time**. Any subsequent toggle (e.g., DGI screener's "add with CSP enabled" UX) **must** be a separate, explicit `PUT /api/symbols/{symbol}` call issued *after* `add_symbol` succeeds — never merged into the creation payload. This is what closes both violations found in §1.

### 2.7 Duplicate-entry-point removal (explicit assignments)
- **Remove** `POST /api/symbols` (`app.py:956-1044`) and its `create_symbol`/related warm-up code entirely once §2.4 is live in `add_symbol`. `GET /api/symbols` (list) is untouched. Remove the `POST` handler from `frontend/src/app/api/symbols/route.ts`; keep `GET`.
- **Rewire** `DgiScreenerView.tsx`'s `AddButton` to: (a) resolve/derive `exchange_mic` for the US-only DGI universe deterministically (screener entries are inherently XNYS/XNAS-eligible per Amendment J — no free-text `"NYSE"` fallback), (b) call `addSymbol({create:{...}})` (or `{security_id}` if a prior search found the security) exactly like `AddSymbolForm`, then (c) issue the existing `PUT /api/symbols/{symbol}` toggle for the single requested flag (`cash_secured_put` or `buy_tracker`) as a separate follow-up call, preserving the current 409→PUT fallback UX pattern for already-existing symbols.
- `cosmos_db.create_symbol()` may be deleted once its only caller (`app.py:979`) is removed; `get_symbol`/`update_watchlist`/`replace_symbol` remain (used by `PUT /api/symbols/{symbol}`, list, and elsewhere).

### 2.8 Async/sync response contract
`add_symbol` remains fully synchronous end-to-end for the SecurityMaster + symbol_config write, returning 200 (select) / 201 (create) immediately. Warm-up is asynchronous/fire-and-forget by design (matches existing production behavior) — the response is not held open for enrichment or forecast backfill to complete. `warmup_started` (§2.4.4) is the only new field.

### 2.9 Failure / partial-success semantics (summary)
| Stage | Failure behavior |
|---|---|
| Security create/select | Hard fail: 400/404/409/503, no config, no warm-up |
| ensure_symbol_config | Soft fail: 200/201 with `config_warning`, no warm-up (nothing to warm up) |
| yf_symbol resolution (fail-closed) | Not a failure: `warmup_started=false`, logged info, response otherwise normal |
| enrich_symbol / backfill_symbol_forecasts | Best-effort: logged warning on exception, never affects response (already returned) |

---

## 3. Assignments

- **Linus** — Backend: relocate/adapt warm-up trigger (§2.4) into `add_symbol`; remove `POST /api/symbols` and `cosmos_db.create_symbol()` (§2.7); add `warmup_started` field.
- **Rusty** — Frontend: rewire `DgiScreenerView.tsx` `AddButton` to the canonical `addSymbol()` + follow-up `PUT` pattern (§2.7); remove the `POST` handler from `frontend/src/app/api/symbols/route.ts`.
- **Basher** — Tests:
  - Backend: warm-up fires exactly once on `config_created=True`, never on `config_existed=True`; `warmup_started` reflects yf_symbol resolution outcome (mock `resolve_yfinance_symbol` → `None` case); logged (not swallowed) warning on enrichment/backfill exception; regression test asserting `POST /api/symbols` no longer exists (404/405) and `GET /api/symbols` still works; `cosmos_db.create_symbol` removed without breaking `get_symbol`/`update_watchlist`/`PUT /api/symbols/{symbol}`.
  - Frontend: `DgiScreenerView` AddButton test asserting it calls `addSymbol()` (never raw `POST /api/symbols`) then `PUT` toggle; idempotency test (second `add_symbol` call for same security → no duplicate warm-up).

## 4. Verdict

**APPROVED** design — proceed to implementation per §2.1–2.9 and §3 assignments. No code written by Danny.
